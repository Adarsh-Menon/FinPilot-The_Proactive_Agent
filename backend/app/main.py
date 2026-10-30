import base64
import csv
import hashlib
import io
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from threading import Lock

from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
import httpx
from openai import OpenAIError
from pwdlib import PasswordHash
from sqlalchemy import select, update, delete, func, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .config import ROOT, Settings
from . import crypto
from .storage import Activity, AdminAudit, ForecastAccuracy, GoldenContribution, Preference, AiSpend, Alert, OpsSetting, Suspension, Debt, Feedback, Goal, ReferralCode, Reward, User, LoginSession, Profile, Transaction, Import, Run, Policy, PolicyAudit, Metric, database, initialize, set_tenant, now
from .schemas import AssistantInput, ChatMessage, VoiceInput, Credentials, DebtInput, DebtPatch, FeedbackInput, Reroute, Redeem, Registration, GoalInput, GoalPatch, ProfileUpdate, TransactionInput, ManualInput, CategoryUpdate, Recategorize, CSVInput, ConfirmImport, ForecastInput, ScenarioInput, PolicyInput, BudgetInput, SuspendInput, EvalRunInput, PreferencesInput
from .finance import AI_DAILY_LIMIT, daily_spending, set_predictor, ai_attempts, categorize, categorize_many, graph, merchant_key, same_merchant, simulate
from .prompts import PromptCatalog
from .mesh import MeshGateway, minimized_description
from .hybrid import FoundationModel
from . import supervisor
from .alerts import Alerter, Background
from .assistant import build_facts, calculated_answer, check_answer, show as show_fact
from .coordinator import EMERGENCY, REPLIES, flow_draft, goal_draft, goal_route, offline_route, rule_route, validate as validate_route
from .advisor import calculated_items, for_model, goal_levers, levers, render_items
from .advisor import money as money_text
from .behaviour import scan as scan_patterns
from .behaviour import advice_items, for_model as pattern_view, outlook, pattern_text, render_narrative, warning_text
from . import rewards as fina_points
from . import debts as debt_agent, invest as invest_agent, plans
from .goals import calculated_note, coach_facts, progress as goal_progress, render_note, show as goal_show
from .investigator import build_inputs, fallback_questions, merge_questions
from .observability import FEEDBACK, FINA_VERIFIED, REQUEST_LOG, actor_ref, log_request, priced, AI_CONFIGURED, ASSISTANT_ANSWERS, AUTH_ATTEMPTS, CATEGORIZATIONS, HTTP_LATENCY, HTTP_REQUESTS, INGESTED, POLICY_MODE, Telemetry

passwords = PasswordHash.recommended()
DUMMY_HASH = passwords.hash("unavailable-account-dummy-password")


def digest(value):
    """SHA-256 hex of a text, used to store session tokens and file hashes instead of the values.

    Args:
        value: text to hash.

    Returns:
        64-character hex string.
    """
    return hashlib.sha256(value.encode()).hexdigest()


def payload_hash(data):
    """Stable hash of a transaction, so a reused idempotency key with different content is caught.

    Args:
        data: transaction dict.

    Returns:
        Hex hash.
    """
    return digest(json.dumps(data, sort_keys=True, separators=(",", ":")))


def create_app(settings=None, *, mesh_transport=None, telemetry=None, alert_transport=None):
    """Build the FinPilot API: database, encryption, model gateway, alerts, middleware and every route.

    Args:
        settings: configuration (defaults to Settings() from .env).
        mesh_transport: HTTP transport for model calls (tests).
        telemetry: Telemetry instance (tests).
        alert_transport: HTTP transport for Telegram (tests).

    Returns:
        The FastAPI application.

    Used by: uvicorn (`backend.app.main:app`), tests and the evaluation harness.
    """
    settings = settings or Settings()
    settings.check()
    keys = crypto.configure(crypto.LocalMasterKey(settings.master_key_file))
    engine, sessions = database(settings.database_url)
    ops_engine, ops_sessions = database(settings.ops_database_url or settings.database_url)
    catalog = PromptCatalog(ROOT / "prompts")
    lock = Lock()
    attempts = defaultdict(deque)
    questions = defaultdict(deque)
    voice_clips = defaultdict(deque)
    started_at = time.time()
    model_failures, auth_failures, limit_hits = defaultdict(deque), deque(), defaultdict(deque)
    spend_lock = Lock()
    last_model_success = {}

    def inference_policy():
        # Fresh operations transaction observes pause changes during an inference call.
        """Read the current AI policy in a fresh operations transaction, so a pause applies mid-request.

        Returns:
            'RUNNING', 'ADVICE_PAUSED' or 'AI_PAUSED' ('AI_PAUSED' if it cannot be read).

        Used by: The model gateway before every call.
        """
        try:
            with ops_sessions() as db:
                policy = db.get(Policy, 1)
                return policy.mode if policy else "AI_PAUSED"
        except SQLAlchemyError:
            return "AI_PAUSED"

    telemetry = telemetry or Telemetry(settings.langsmith_api_key, settings.langsmith_project,
        settings.langsmith_tracing, endpoint=settings.langsmith_endpoint)
    key, model, risk_model = settings.inference()
    gateway = MeshGateway(key, model, catalog, inference_policy, mesh_transport, risk_model=risk_model,
        telemetry=telemetry, provider=settings.inference_provider, speech_model=settings.speech_model())
    alerter = Alerter(sessions, settings.telegram_bot_token, settings.telegram_chat_id, alert_transport)
    background = Background()

    def recent(queue, seconds):
        """Record now in a time window and count the events inside it.

        Args:
            queue: deque of timestamps.
            seconds: window length.

        Returns:
            Number of events in the window.

        Used by: Failure, sign-in and abuse alert thresholds.
        """
        stamp = time.monotonic()
        queue.append(stamp)
        while queue and queue[0] < stamp - seconds:
            queue.popleft()
        return len(queue)

    def on_model_call(agent, model, outcome, tokens):
        """After each model call: add its cost to today's spend, alert on repeated failures, enforce the daily
           budget.

        Args:
            agent: agent name.
            model: model id.
            outcome: 'completed', 'incomplete' or 'error'.
            tokens: token counts {input_tokens, output_tokens}.

        Used by: The gateway's on_call hook, run on the background worker. Seen on Admin > AI costs and as phone
        alerts.
        """
        cost = priced(model, tokens.get("input_tokens"), tokens.get("output_tokens"), settings.model_prices) or 0.0
        day = now().date().isoformat()
        with spend_lock, sessions() as db:
            row = db.get(AiSpend, (day, model)) or AiSpend(day=day, model=model, calls=0, failures=0, cost_usd=0.0)
            row.calls, row.failures, row.cost_usd = row.calls + 1, row.failures + (outcome != "completed"), row.cost_usd + cost
            db.merge(row)
            db.commit()
            spent = db.scalar(select(func.sum(AiSpend.cost_usd)).where(AiSpend.day == day)) or 0.0
            budget = db.get(OpsSetting, 1)
            budget = (budget.daily_budget_usd, budget.auto_pause) if budget else (None, False)
        if outcome == "completed":
            last_model_success[agent] = now()
        elif (n := recent(model_failures[agent], 600)) >= 3:
            alerter.raise_alert(f"llm:{agent}", "warning", f"AI calls failing: {agent}",
                f"{n} failed or incomplete calls in the last 10 minutes (model {model}).")
        limit_usd, auto_pause = budget
        if limit_usd:
            share = spent / limit_usd
            if share >= 1:
                paused = auto_pause and pause_ai("Daily AI budget reached")
                alerter.raise_alert(f"budget100:{day}", "critical", "Daily AI budget reached",
                    f"${spent:.2f} of ${limit_usd:.2f} spent today." + (" AI is now paused." if paused else ""))
            elif share >= 0.8:
                alerter.raise_alert(f"budget80:{day}", "warning", "AI spend at 80% of today's budget",
                    f"${spent:.2f} of ${limit_usd:.2f} spent today.")

    def pause_ai(reason):
        """Pause AI from the system side when the daily budget is reached, recording it in the audit logs.

        Args:
            reason: why (shown in the audit log).

        Returns:
            True when this call changed the mode.

        Used by: on_model_call(). Seen as a system entry in Admin > Audit log.
        """
        with ops_sessions() as db:
            policy = db.get(Policy, 1)
            if policy is None or policy.mode == "AI_PAUSED":
                return False
            db.execute(update(Policy).where(Policy.id == 1, Policy.revision == policy.revision)
                .values(mode="AI_PAUSED", revision=policy.revision + 1))
            db.add(PolicyAudit(mode="AI_PAUSED", revision=policy.revision + 1, reason=reason))
            db.add(AdminAudit(operator="system", action="ai_auto_pause", target="policy", detail=reason))
            db.commit()
        return True

    gateway.on_call = lambda *call: background.submit(on_model_call, *call)
    forecaster = FoundationModel(settings.forecast_model) if settings.forecast_model else None
    set_predictor(forecaster)

    def limit_hit(user, which):
        """Record that a customer hit a usage limit (in the background) for the abuse view and alerts.

        Args:
            user: the customer.
            which: limit name: failed_sign_in, ai_categorize_daily, chat_hourly or voice_hourly.

        Used by: Rate limits and failed sign-ins. Seen on Admin > Abuse & limits.
        """
        background.submit(record_limit, actor_ref(user.id), which)

    def record_limit(ref, which):
        """Write a limit hit to the activity log and alert when one account hits five in an hour.

        Args:
            ref: customer pseudonym.
            which: limit name.
        """
        with sessions() as db:
            db.add(Activity(actor_ref=ref, action="limit_hit", outcome=which, duration_ms=0))
            db.commit()
        if (n := recent(limit_hits[ref], 3600)) >= 5:
            alerter.raise_alert(f"abuse:{ref}", "warning", f"Possible abuse by {ref}",
                f"{n} usage limits hit in the last hour, latest: {which.replace('_', ' ')}.")

    @asynccontextmanager
    async def lifespan(app):
        """Startup checks and shutdown cleanup.

        On SQLite it creates tables; on PostgreSQL it refuses to start unless row-level security is on and the app
        role cannot bypass it, and the operations role cannot read private tables. It also verifies the prompt
        release.

        Args:
            app: the FastAPI app.
        """
        if engine.dialect.name == "sqlite":
            initialize(engine)
        if engine.dialect.name == "postgresql":
            if not settings.ops_database_url:
                raise ValueError("PostgreSQL requires a separate OPS_DATABASE_URL")
            with engine.connect() as connection:
                role = connection.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user")).one()
                if any(role):
                    raise ValueError("The application role must not bypass row security")
                tables = connection.execute(text("SELECT relrowsecurity, relforcerowsecurity, pg_get_userbyid(relowner)=current_user AS owns FROM pg_class WHERE oid IN ('profiles'::regclass, 'transactions'::regclass, 'imports'::regclass, 'runs'::regclass, 'goals'::regclass)")).all()
                if len(tables) != 5 or any(not t[0] or not t[1] or t[2] for t in tables):
                    raise ValueError("Tenant row security must be enabled and owned by a separate migration role")
            with ops_engine.connect() as connection:
                for table in ("users", "sessions", "profiles", "transactions", "imports", "runs"):
                    if connection.scalar(text("SELECT has_table_privilege(current_user, :table, 'SELECT,INSERT,UPDATE,DELETE')"), {"table": table}):
                        raise ValueError("Operations database role has private table access")
        catalog.selected()
        if forecaster is not None and settings.environment != "test":
            background.submit(forecaster._pipeline)
        try:
            yield
        finally:
            gateway.close()
            telemetry.close()
            engine.dispose()
            ops_engine.dispose()

    app = FastAPI(title="FinPilot", version="0.1.0", lifespan=lifespan)
    app.state.sessions = sessions
    app.state.background = background
    app.state.engine = engine
    app.state.catalog = catalog

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        # Never echo passwords or entire financial payloads in validation errors.
        """Validation errors without echoing submitted values (passwords and financial data stay out of responses).

        Args:
            request: the request.
            exc: the validation error.

        Returns:
            422 with field locations and messages only.
        """
        return JSONResponse(status_code=422, content={"detail": [{"loc": e["loc"], "msg": e["msg"]} for e in exc.errors()]})

    @app.exception_handler(SQLAlchemyError)
    async def unavailable(request, exc):
        """Database errors as a generic 503, never leaking query details.

        Args:
            request: the request.
            exc: the database error.

        Returns:
            503 JSON response.
        """
        return JSONResponse(status_code=503, content={"detail": "Database operation unavailable; refresh and retry"})

    @app.middleware("http")
    async def boundaries(request, call_next):
        """Request boundaries: 1 MB size cap, trusted origins for changes, and no-store security headers.

        Args:
            request: the request.
            call_next: next handler.

        Returns:
            The response, or 413/403 when rejected.
        """
        if request.headers.get("content-length", "0").isdigit() and int(request.headers.get("content-length", "0")) > 1_000_000:
            return JSONResponse(status_code=413, content={"detail": "Request too large"})
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if request.headers.get("origin") not in settings.allowed_origins.split(","):
                return JSONResponse(status_code=403, content={"detail": "Untrusted request origin"})
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > 1_000_000:
                    return JSONResponse(status_code=413, content={"detail": "Request too large"})
                chunks.append(chunk)
            request._body = b"".join(chunks)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    # Registered last, so it is outermost and also counts boundary rejections.
    @app.middleware("http")
    async def observe(request, call_next):
        # Route templates only (never raw paths), so record IDs stay out of metric labels.
        """Metrics and the request log for every request (route templates only), and an alert on unhandled errors.

        Args:
            request: the request.
            call_next: next handler.

        Returns:
            The response.

        Used by: Prometheus/Grafana, Admin > Logs and the "Server error" phone alert.
        """
        started, status, error = time.perf_counter(), 500, None
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        except Exception as exc:
            error = type(exc).__name__  # Exception type only; messages can contain private data.
            raise
        finally:
            route = request.scope.get("route")
            label = route.path if route is not None else "unmatched"
            HTTP_REQUESTS.labels(request.method, label, str(status)).inc()
            HTTP_LATENCY.labels(request.method, label).observe(time.perf_counter() - started)
            if label not in {"/metrics", "/health", "/ops/v1/logs"}:  # skip scrapes and the log's own polling
                log_request(request.method, label, status, time.perf_counter() - started, error)
            if status >= 500 and error:
                background.submit(alerter.raise_alert, f"5xx:{request.method} {label}"[:80], "critical",
                    f"Server error on {request.method} {label}", f"Unhandled {error}; the request failed with {status}.")

    def db_session():
        """FastAPI dependency: a database session committed on success and rolled back on error.

        Returns:
            Yields the session.
        """
        with sessions() as db:
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    # Customers and operators hold separate cookies, so one browser can be signed in as both
    # and neither session can reach the other side's routes.
    COOKIES = {"customer": "finpilot_session", "operator": "finpilot_ops_session"}

    def authenticate(request, db, role):
        """Check the session cookie, role, CSRF token on changes and suspension, then scope the database to the
           user.

        Args:
            request: the request.
            db: database session for this request (committed on success, rolled back on error).
            role: 'customer' or 'operator'.

        Returns:
            The User; raises 401 when signed out, 403 for a bad CSRF token or a suspended account.
        """
        token = request.cookies.get(COOKIES[role], "")
        session = db.get(LoginSession, digest(token)) if token else None
        if not session or session.expires <= now():
            raise HTTPException(401, "Sign in to continue")
        user = db.get(User, session.user_id)
        if user is None or user.role != role:
            raise HTTPException(401, "Sign in to continue")
        if request.method in {"POST", "PATCH", "PUT", "DELETE"} and not secrets.compare_digest(request.headers.get("x-csrf-token", ""), session.csrf):
            raise HTTPException(403, "Missing or invalid CSRF token")
        if role == "customer" and db.get(Suspension, actor_ref(user.id)) is not None:
            raise HTTPException(403, "This account is suspended. Contact support if you think this is a mistake.")
        set_tenant(db, user.id)
        request.state.login_session = session
        return user

    def customer_actor(request: Request, db=Depends(db_session)):
        """Dependency: the signed-in customer.

        Args:
            request: the request.
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            The User.
        """
        return authenticate(request, db, "customer")

    def operator(request: Request, db=Depends(db_session)):
        """Dependency: the signed-in operator for admin routes.

        Args:
            request: the request.
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            The User.
        """
        return authenticate(request, db, "operator")

    def customer(user=Depends(customer_actor), db=Depends(db_session)):
        """Dependency: the signed-in customer, locked for update so concurrent edits are serialized.

        Args:
            user: from customer_actor.
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            The User.
        """
        return db.scalar(select(User).where(User.id == user.id).with_for_update().execution_options(populate_existing=True))

    def throttle(request):
        # Local, single-process guard; distributed deployment remains explicitly unsupported.
        """Limit sign-in attempts per IP address (local, single process).

        Args:
            request: the request.

        Used by: Sign-in. Raises 429 when there are too many attempts.
        """
        key = request.client.host if request.client else "unknown"
        stamp = time.monotonic()
        with lock:
            for stale in [k for k, values in attempts.items() if not values or values[-1] < stamp - 300]:
                del attempts[stale]
            queue = attempts[key]
            while queue and queue[0] < stamp - 300:
                queue.popleft()
            if len(queue) >= 20:
                raise HTTPException(429, "Too many sign-in attempts; retry in five minutes")
            queue.append(stamp)

    def activity(db, user, action, started, outcome="ok", agent=None, model=None, count=None):
        # Fixed vocabulary only; never usernames, amounts, descriptions, notes or questions.
        """Record a content-free activity row (pseudonym, fixed action, outcome, timing).

        Args:
            db: database session for this request (committed on success, rolled back on error).
            user: the customer.
            action: fixed action name.
            started: perf_counter at the start.
            outcome: short outcome.
            agent: agent involved.
            model: model used.
            count: items involved.

        Used by: Almost every customer route. Seen on Admin > Users & activity.
        """
        db.add(Activity(actor_ref=actor_ref(user.id), action=action, agent=agent, model=model, outcome=outcome[:40],
            duration_ms=int((time.perf_counter() - started) * 1000), item_count=count))

    def ai_model():
        """The live shared model id, or None when AI is off.

        Returns:
            Model id or None.

        Used by: activity() rows.
        """
        return gateway.model if gateway.inspect_configuration()["live_inference_enabled"] else None

    def question_quota(user):
        # Per-customer cap on chat questions to bound model cost; single-process like throttle().
        """Allow at most 20 Fina questions an hour per customer.

        Args:
            user: the customer.

        Used by: Fina chat and the investigator. Raises 429 and records a limit hit.
        """
        stamp = time.monotonic()
        with lock:
            queue = questions[user.id]
            while queue and queue[0] < stamp - 3600:
                queue.popleft()
            if len(queue) >= 20:
                limit_hit(user, "chat_hourly")
                raise HTTPException(429, "You've asked 20 questions this hour. Please try again later.")
            queue.append(stamp)

    def begin_session(user, request, response, db):
        """Start a signed-in session: random token cookie, CSRF token, old session removed.

        Args:
            user: the account.
            request: the request.
            response: the response to set the cookie on.
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            {username, role, csrf_token}.

        Used by: Register and sign-in.
        """
        cookie = COOKIES[user.role]
        old = request.cookies.get(cookie)
        if old:
            db.execute(delete(LoginSession).where(LoginSession.token_hash == digest(old)))
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        db.add(LoginSession(token_hash=digest(token), user_id=user.id, csrf=csrf, expires=now()+timedelta(hours=settings.session_hours)))
        response.set_cookie(cookie, token, httponly=True, secure=settings.cookie_secure, samesite="strict", max_age=settings.session_hours*3600)
        return {"username": user.username, "role": user.role, "csrf_token": csrf}

    def advance(db, user, expected=None):
        """Bump the owner's data version, refusing stale edits.

        Args:
            db: database session for this request (committed on success, rolled back on error).
            user: the customer.
            expected: version the edit was based on (optional).

        Used by: Every change to transactions, profile or categories. Raises 409 when data changed meanwhile.
        """
        version = user.version if expected is None else expected
        result = db.execute(update(User).where(User.id == user.id, User.version == version).values(version=version+1))
        if result.rowcount != 1:
            raise HTTPException(409, "Data changed; refresh and retry")
        db.flush()
        db.refresh(user)

    def policy_read(db):
        """The AI policy row.

        Args:
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            The Policy; 503 when missing.
        """
        policy = db.get(Policy, 1)
        if policy is None:
            raise HTTPException(503, "Policy unavailable")
        return policy

    @app.get("/health")
    def health():
        """Liveness and feature flags (GET /health).

        Returns:
            Status, version, mode, ai_enabled, tracing_enabled and voice_enabled.

        Used by: Monitoring, the frontend's voice mode check and Telegram/health tiles.
        """
        return {"status": "ok", "version": "0.1.0", "mode": "local_development",
            "ai_enabled": gateway.inspect_configuration()["live_inference_enabled"],
            "tracing_enabled": telemetry.enabled, "voice_enabled": gateway.speech_ready()}

    @app.get("/metrics", include_in_schema=False)
    def metrics():
        # Content-free aggregates for a loopback Prometheus scraper; no tenant data or identifiers.
        """Prometheus metrics (GET /metrics): content-free aggregates.

        Returns:
            Prometheus text format.

        Used by: The local Prometheus scraper and Grafana.
        """
        mode = inference_policy()
        for name in ("RUNNING", "ADVICE_PAUSED", "AI_PAUSED"):
            POLICY_MODE.labels(name).set(int(name == mode))
        AI_CONFIGURED.set(int(gateway.inspect_configuration()["configured"]))
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/api/v1/auth/register", status_code=201)
    def register(body: Registration, request: Request, response: Response, db=Depends(db_session)):
        """Create a customer account, optionally with a referral code (POST /api/v1/auth/register).

        Args:
            body: Registration.
            request: the request.
            response: response for the session cookie.
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            {username, role, csrf_token}; both people get FinaPoints when a code is valid.

        Used by: The Create account page.
        """
        try:
            throttle(request)
        except HTTPException:
            AUTH_ATTEMPTS.labels("register", "throttled").inc()
            raise
        user = User(username=body.username.lower(), password_hash=passwords.hash(body.password))
        db.add(user)
        try:
            db.flush()
        except IntegrityError:
            AUTH_ATTEMPTS.labels("register", "rejected").inc()
            raise HTTPException(409, "Username unavailable")
        keys.data_key(db.connection(), user.id, create=True)  # every customer's data key exists from sign-up
        db.add(ReferralCode(code=fina_points.new_code(), tenant_id=user.id))
        referrer = db.get(ReferralCode, body.referral_code.upper()) if body.referral_code else None
        if referrer is not None:
            # Write each ledger entry under its owner's tenant so row-level security accepts it.
            set_tenant(db, referrer.tenant_id)
            keys.data_key(db.connection(), referrer.tenant_id)
            db.add(Reward(tenant_id=referrer.tenant_id, idem_key=f"referral:{user.id}", data={"kind": "referral",
                "points": fina_points.REFERRAL_POINTS, "label": "A friend joined with your code", "ref": None}))
            db.flush()
            set_tenant(db, user.id)
            db.add(Reward(tenant_id=user.id, idem_key="welcome", data={"kind": "welcome", "points": fina_points.WELCOME_POINTS,
                "label": "Joined with a referral code", "ref": None}))
            activity(db, user, "referral", time.perf_counter(), "joined with code")
        AUTH_ATTEMPTS.labels("register", "success").inc()
        activity(db, user, "register", time.perf_counter())
        return begin_session(user, request, response, db)

    def sign_in(body, request, response, db, role):
        """Shared sign-in: throttle, verify the password, check role and suspension, start a session.

        Args:
            body: Credentials.
            request: the request.
            response: the response.
            db: database session for this request (committed on success, rolled back on error).
            role: expected role.

        Returns:
            {username, role, csrf_token}.

        Used by: Both sign-in routes. Failed attempts feed the abuse view and the sign-in spike alert.
        """
        try:
            throttle(request)
        except HTTPException:
            AUTH_ATTEMPTS.labels("login", "throttled").inc()
            if (n := recent(auth_failures, 300)) > 30:
                background.submit(alerter.raise_alert, "auth-spike", "warning", "Unusual number of failed sign-ins", f"{n} failed or throttled sign-ins in 5 minutes.")
            raise
        user = db.scalar(select(User).where(User.username == body.username.lower()))
        valid = passwords.verify(body.password, user.password_hash if user else DUMMY_HASH)
        if not user or not valid:
            AUTH_ATTEMPTS.labels("login", "rejected").inc()
            if user is not None and user.role == "customer":
                limit_hit(user, "failed_sign_in")
            if (n := recent(auth_failures, 300)) > 30:
                background.submit(alerter.raise_alert, "auth-spike", "warning", "Unusual number of failed sign-ins", f"{n} failed sign-ins in 5 minutes.")
            raise HTTPException(401, "Invalid username or password")
        if user.role == "customer" and db.get(Suspension, actor_ref(user.id)) is not None:
            raise HTTPException(403, "This account is suspended. Contact support if you think this is a mistake.")
        if user.role != role:
            AUTH_ATTEMPTS.labels("login", "rejected").inc()
            raise HTTPException(403, "This is an admin account. Sign in at /admin." if role == "customer"
                else "This is a customer account. Sign in on the main page.")
        AUTH_ATTEMPTS.labels("login", "success").inc()
        if user.role == "customer":
            activity(db, user, "sign_in", time.perf_counter())
        return begin_session(user, request, response, db)

    @app.post("/api/v1/auth/login")
    def login(body: Credentials, request: Request, response: Response, db=Depends(db_session)):
        """Customer sign-in (POST /api/v1/auth/login).

        Args:
            body: Credentials.
            request: the request.
            response: the response.
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            Session details.

        Used by: The main sign-in page.
        """
        return sign_in(body, request, response, db, "customer")

    @app.post("/ops/v1/auth/login")
    def ops_login(body: Credentials, request: Request, response: Response, db=Depends(db_session)):
        """Operator sign-in (POST /ops/v1/auth/login).

        Args:
            body: Credentials.
            request: the request.
            response: the response.
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            Session details.

        Used by: The /admin sign-in page.
        """
        return sign_in(body, request, response, db, "operator")

    def describe(request, user):
        """Session details for the browser.

        Args:
            request: the request.
            user: the account.

        Returns:
            {username, role, csrf_token}.
        """
        return {"username": user.username, "role": user.role, "csrf_token": request.state.login_session.csrf}

    def end_session(request, response, db, role):
        """Delete the session and its cookie.

        Args:
            request: the request.
            response: the response.
            db: database session for this request (committed on success, rolled back on error).
            role: which cookie to clear.
        """
        db.delete(request.state.login_session)
        response.delete_cookie(COOKIES[role], secure=settings.cookie_secure, httponly=True, samesite="strict")

    @app.get("/api/v1/auth/session")
    def session(request: Request, user=Depends(customer_actor)):
        """The current customer session (GET /api/v1/auth/session).

        Args:
            request: the request.
            user: the signed-in customer, from the session cookie (row-level security is set to them).

        Returns:
            Session details or 401.

        Used by: App start (stay signed in after reload).
        """
        return describe(request, user)

    @app.get("/ops/v1/auth/session")
    def ops_session(request: Request, user=Depends(operator)):
        """The current operator session (GET /ops/v1/auth/session).

        Args:
            request: the request.
            user: the signed-in operator (admin portal session).

        Returns:
            Session details or 401.

        Used by: Admin portal start.
        """
        return describe(request, user)

    @app.post("/api/v1/auth/logout", status_code=204)
    def logout(request: Request, response: Response, user=Depends(customer_actor), db=Depends(db_session)):
        """Customer sign-out (POST /api/v1/auth/logout).

        Args:
            request: the request.
            response: the response.
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            204.

        Used by: The sign-out button.
        """
        end_session(request, response, db, "customer")

    @app.post("/ops/v1/auth/logout", status_code=204)
    def ops_logout(request: Request, response: Response, user=Depends(operator), db=Depends(db_session)):
        """Operator sign-out (POST /ops/v1/auth/logout).

        Args:
            request: the request.
            response: the response.
            user: the signed-in operator (admin portal session).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            204.

        Used by: Admin "Sign out".
        """
        end_session(request, response, db, "operator")

    @app.get("/api/v1/profile")
    def get_profile(user=Depends(customer), db=Depends(db_session)):
        """The owner's saved context (GET /api/v1/profile).

        Args:
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            {version, profile} (profile None when not saved).

        Used by: Your context and the app's start-up.
        """
        profile = db.get(Profile, user.id)
        return {"version": user.version, "profile": profile.data if profile else None}

    @app.put("/api/v1/profile")
    def save_profile(body: ProfileUpdate, user=Depends(customer), db=Depends(db_session)):
        """Save the owner's context (PUT /api/v1/profile).

        Args:
            body: ProfileUpdate with the version it was edited from.
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            {version, profile}.

        Used by: Save context, Fina's confirm cards and investigator answers.
        """
        currencies = [t.data["currency"] for t in ledger(db, user)]
        if any(c != body.profile.currency for c in currencies):
            raise HTTPException(409, "Currency cannot change while transactions use another currency")
        started = time.perf_counter()
        advance(db, user, body.expected_version)
        activity(db, user, "profile_update", started)
        profile = db.get(Profile, user.id)
        if profile:
            profile.data = body.profile.model_dump(mode="json")
        else:
            db.add(Profile(tenant_id=user.id, data=body.profile.model_dump(mode="json")))
        return {"version": user.version, "profile": body.profile.model_dump(mode="json")}

    @app.get("/api/v1/transactions")
    def transactions(offset: int = 0, user=Depends(customer), db=Depends(db_session)):
        """List the owner's transactions (GET /api/v1/transactions).

        Args:
            offset: first row to return.
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            {items, total, version, ai_daily_limit, ai_used_today}.

        Used by: The Transactions page and the Overview chart.
        """
        if not 0 <= offset <= 100000:
            raise HTTPException(422, "Invalid offset")
        rows = db.scalars(select(Transaction).where(Transaction.tenant_id == user.id).order_by(Transaction.id).offset(offset).limit(1000)).all()
        total = db.scalar(select(func.count()).select_from(Transaction).where(Transaction.tenant_id == user.id))
        return {"items": [{**t.data, "id": t.id} for t in rows], "total": total, "version": user.version,
            "ai_daily_limit": AI_DAILY_LIMIT, "ai_used_today": ai_used_today(db, user)}

    def require_currency(db, user, rows):
        """Require a saved profile and transactions in the profile's currency.

        Args:
            db: database session for this request (committed on success, rolled back on error).
            user: the customer.
            rows: transactions to check.
        """
        profile = db.get(Profile, user.id)
        if not profile:
            raise HTTPException(409, "Save your financial context first")
        if any(t["currency"] != profile.data["currency"] for t in rows):
            raise HTTPException(422, "Transaction currency must match your financial context")

    def ledger(db, user):
        """All of the owner's transactions.

        Args:
            db: database session for this request (committed on success, rolled back on error).
            user: the customer.

        Returns:
            Transaction rows.
        """
        return db.scalars(select(Transaction).where(Transaction.tenant_id == user.id).order_by(Transaction.id)).all()

    def existing_categories(db, user):
        # Tenant-local naming hints, never an allowed-category list.
        """The owner's category names, as naming hints for the categorizer.

        Args:
            db: database session for this request (committed on success, rolled back on error).
            user: the customer.

        Returns:
            Up to 100 sorted names.
        """
        labels = {t.data.get("category") for t in ledger(db, user)} - {None, "uncategorized"}
        return sorted(labels)[:100]

    def owner_examples(db, user):
        """The owner's corrections and notes first, then other labels; one per description.

        Args:
            db: database session for this request (committed on success, rolled back on error).
            user: the customer.

        Returns:
            Up to 30 {description, category, kind, owner_note} examples.

        Used by: The categorizer's few-shot examples and owner merchant rules.
        """
        rows = [t.data for t in ledger(db, user) if t.data.get("category_source") == "owner" or t.data.get("category_note")]
        taught = sorted(rows, key=lambda r: not (r.get("category_history") or r.get("category_note")))
        examples, seen = [], set()
        for row in taught:
            key = " ".join(row["description"].lower().split())
            if key in seen or row["category"] == "uncategorized":
                continue
            seen.add(key)
            examples.append({"description": row["description"], "category": row["category"], "kind": row["kind"],
                **({"owner_note": row["category_note"]} if row.get("category_note") else {})})
            if len(examples) >= 30:
                break
        return examples

    def hints(db, user):
        """Categorizer hints for the owner: category names and labelled examples.

        Args:
            db: database session for this request (committed on success, rolled back on error).
            user: the customer.

        Returns:
            Keyword arguments for categorize_many().
        """
        return {"existing_categories": existing_categories(db, user), "owner_examples": owner_examples(db, user)}

    @app.post("/api/v1/transactions", status_code=201)
    def add_transaction(body: ManualInput, user=Depends(customer), db=Depends(db_session)):
        """Add one transaction by hand (POST /api/v1/transactions); categorized on the way in.

        Args:
            body: ManualInput with an idempotency key.
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            {id, version, duplicate}.

        Used by: Add transaction on Transactions, and test data for evaluations.
        """
        started = time.perf_counter()
        data = body.transaction.model_dump(mode="json")
        h = payload_hash(data)
        old = db.scalar(select(Transaction).where(Transaction.tenant_id == user.id, Transaction.idempotency_key == body.idempotency_key))
        if old:
            if old.payload_hash != h:
                raise HTTPException(409, "Idempotency key already used for different content")
            return {"id": old.id, "version": user.version, "duplicate": True}
        require_currency(db, user, [data])
        categorized = categorize(data, gateway, **hints(db, user))
        advance(db, user)
        row = Transaction(tenant_id=user.id, idempotency_key=body.idempotency_key, payload_hash=h, data=categorized)
        db.add(row)
        db.flush()
        INGESTED.labels("manual").inc()
        activity(db, user, "manual_entry", started, categorized.get("category_status") or categorized["category_source"],
            "categorizer", ai_model(), 1)
        return {"id": row.id, "version": user.version, "duplicate": False}

    def relabel(row, update):
        # Replace every category field and keep a short history of earlier labels.
        """Replace a transaction's category fields, keeping a short history of earlier labels.

        Args:
            row: the Transaction.
            update: new category fields.
        """
        old = row.data
        history = (old.get("category_history") or [])[-9:] + [
            {"category": old["category"], "source": old.get("category_source"), "status": old.get("category_status")}]
        row.data = {**{k: v for k, v in old.items() if not k.startswith("category")},
            "category_ai_attempts": ai_attempts(old), **update, "category_history": history}

    def ai_used_today(db, user):
        """Owner-requested AI categorizations today (notes and retries), from the activity log.

        Args:
            db: database session for this request (committed on success, rolled back on error).
            user: the customer.

        Returns:
            Count.

        Used by: The 20-a-day limit and "AI requests left today".
        """
        midnight = now().replace(hour=0, minute=0, second=0, microsecond=0)
        return db.scalar(select(func.count()).select_from(Activity).where(Activity.actor_ref == actor_ref(user.id),
            Activity.action.in_(("category_note", "category_retry")), Activity.created_at >= midnight)) or 0

    def require_ai_quota(db, user):
        """Refuse a 21st AI categorization request today.

        Args:
            db: database session for this request (committed on success, rolled back on error).
            user: the customer.

        Used by: Raises 429 and records a limit hit.
        """
        if ai_used_today(db, user) >= AI_DAILY_LIMIT:
            limit_hit(user, "ai_categorize_daily")
            raise HTTPException(429, f"You've used today's {AI_DAILY_LIMIT} AI categorization requests. "
                "Set the category yourself, or ask again tomorrow.")

    def spread_label(db, user, row):
        """Give the owner's label to every other transaction from the same merchant they have not labelled.

        Args:
            db: database session for this request (committed on success, rolled back on error).
            user: the customer.
            row: the transaction just labelled.

        Returns:
            How many others changed.

        Used by: Category fixes. Seen as "Also updated N similar transactions".
        """
        label = row.data["category"]
        key = merchant_key(row.data["description"], row.data.get("category_note"))
        if key is None or label == "uncategorized":
            return 0
        changed = 0
        for other in ledger(db, user):
            data = other.data
            if (other.id == row.id or data.get("category_source") == "owner" or data["kind"] != row.data["kind"]
                    or data["category"] == label or not same_merchant(key, data["description"])):
                continue
            relabel(other, {"category": label, "category_source": "merchant_rule", "category_status": "classified",
                "category_reason": f"Matches your label for “{' '.join(key)}”."})
            changed += 1
        return changed

    def require_ai():
        """Refuse AI categorization while AI is off, with a customer-friendly 409 (the policy itself stays hidden)."""
        if not gateway.inspect_configuration()["live_inference_enabled"]:
            # Customer-facing: the policy state itself is an operations detail.
            raise HTTPException(409, "AI categorization is unavailable right now. You can set the category yourself.")

    def model_proposals(rows, db, user, notes=None):
        """Run the categorizer on existing rows, optionally with the owner's notes.

        Args:
            rows: Transaction rows.
            db: database session for this request (committed on success, rolled back on error).
            user: the customer.
            notes: one note per row, or None.

        Returns:
            Category field updates, one per row.
        """
        pending = [{**{k: v for k, v in r.data.items() if not k.startswith("category")},
            "category_ai_attempts": ai_attempts(r.data)} for r in rows]
        for tx, note in zip(pending, notes or [None] * len(rows), strict=True):
            if note:
                tx["owner_note"] = note
        return [{k: v for k, v in result.items() if k.startswith("category")}
                for result in categorize_many(pending, gateway, **hints(db, user))]

    @app.post("/api/v1/transactions/{tx_id}/category")
    def update_category(tx_id: str, body: CategoryUpdate, user=Depends(customer), db=Depends(db_session)):
        """Fix a transaction's category with the owner's label or an AI note (POST
           /api/v1/transactions/{id}/category).

        Args:
            tx_id: transaction id.
            body: CategoryUpdate.
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            The updated row with version and also_updated.

        Used by: The Fix this category dialog.
        """
        started = time.perf_counter()
        row = db.get(Transaction, (user.id, tx_id))
        if row is None:
            raise HTTPException(404, "Transaction not found")
        if body.category is not None:
            advance(db, user, body.expected_version)
            relabel(row, {"category": body.category, "category_source": "owner"})
            activity(db, user, "category_set", started, "owner label")
            if sharing(db, user).share_categories:
                share_label(db, user, row.data)
        else:
            require_ai()
            require_ai_quota(db, user)
            advance(db, user, body.expected_version)
            relabel(row, {**model_proposals([row], db, user, [body.note])[0], "category_note": body.note})
            activity(db, user, "category_note", started, row.data.get("category_status", "unresolved"), "categorizer", ai_model(), 1)
        also = spread_label(db, user, row)
        return {**row.data, "id": row.id, "version": user.version, "also_updated": also}

    @app.post("/api/v1/transactions/recategorize")
    def recategorize(body: Recategorize, user=Depends(customer), db=Depends(db_session)):
        """Retry the categorizer on uncategorized rows (POST /api/v1/transactions/recategorize).

        Args:
            body: Recategorize.
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            {attempted, categorized, version}.

        Used by: "Retry with AI" on Transactions and Agent activity.
        """
        started = time.perf_counter()
        require_ai()
        rows = [t for t in ledger(db, user) if t.data.get("category_source") == "unresolved"][:200]
        if not rows:
            return {"attempted": 0, "categorized": 0, "version": user.version}
        require_ai_quota(db, user)
        advance(db, user, body.expected_version)
        notes = [r.data.get("category_note") for r in rows]
        for row, proposal in zip(rows, model_proposals(rows, db, user, notes), strict=True):
            relabel(row, {**proposal, **({"category_note": row.data["category_note"]} if row.data.get("category_note") else {})})
        categorized = sum(r.data["category_source"] == "model" for r in rows)
        activity(db, user, "category_retry", started, f"{categorized} categorized", "categorizer", ai_model(), len(rows))
        return {"attempted": len(rows), "categorized": categorized, "version": user.version}

    @app.post("/api/v1/imports", status_code=201)
    def preview(body: CSVInput, user=Depends(customer), db=Depends(db_session)):
        """Validate and categorize a CSV, without saving it (POST /api/v1/imports).

        Args:
            body: CSVInput.
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            {id, rows, confirmed, version}.

        Used by: The Import transactions dialog.
        """
        started = time.perf_counter()
        text = body.csv_text.removeprefix("\ufeff")
        h = digest(text)
        old = db.scalar(select(Import).where(Import.tenant_id == user.id, Import.content_hash == h))
        if old:
            return {"id": old.id, "rows": old.rows, "confirmed": old.confirmed, "version": user.version}
        reader = csv.DictReader(io.StringIO(text))
        required = ["date", "description", "amount_minor", "currency", "kind"]
        if not reader.fieldnames or len(reader.fieldnames) != len(set(reader.fieldnames)) or not set(required).issubset(reader.fieldnames) or set(reader.fieldnames) - set(required + ["category", "recurring"]):
            raise HTTPException(422, "CSV columns: date,description,amount_minor,currency,kind; optional category,recurring")
        rows, errors = [], []
        try:
            for index, row in enumerate(reader, 2):
                if index > 1001:
                    raise HTTPException(422, "At most 1,000 rows per import")
                try:
                    if None in row or any(v is None for v in row.values()):
                        raise ValueError("Column count mismatch")
                    row["amount_minor"] = int(row["amount_minor"])
                    if "recurring" in row:
                        if row["recurring"] not in {"true", "false", ""}:
                            raise ValueError("recurring must be true or false")
                        row["recurring"] = row["recurring"] == "true"
                    if not row.get("category"):
                        row.pop("category", None)
                    rows.append(TransactionInput.model_validate(row).model_dump(mode="json"))
                except (ValueError, TypeError):
                    errors.append({"row": index, "message": "Invalid date, amount, category, kind, currency or column count"})
        except csv.Error:
            raise HTTPException(422, "Malformed CSV")
        if errors or not rows:
            raise HTTPException(422, errors or "CSV has no transactions")
        require_currency(db, user, rows)
        rows = categorize_many(rows, gateway, **hints(db, user))
        record = Import(tenant_id=user.id, content_hash=h, rows=rows)
        db.add(record)
        activity(db, user, "csv_preview", started, f"{sum(r['category_source'] == 'model' for r in rows)} by AI",
            "categorizer", ai_model(), len(rows))
        db.flush()
        return {"id": record.id, "rows": rows, "confirmed": False, "version": user.version}

    @app.post("/api/v1/imports/{import_id}/confirm")
    def confirm(import_id: str, body: ConfirmImport, user=Depends(customer), db=Depends(db_session)):
        """Add a previewed import to the ledger (POST /api/v1/imports/{id}/confirm).

        Args:
            import_id: preview id.
            body: ConfirmImport.
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            {imported, version} or a duplicates warning.

        Used by: Confirm import.
        """
        started = time.perf_counter()
        record = db.get(Import, (user.id, import_id))
        if record is None:
            raise HTTPException(404, "Import not found")
        if record.confirmed:
            return {"imported": 0, "already_confirmed": True, "version": user.version}
        require_currency(db, user, record.rows)
        def signature(row):
            """The fields that identify a duplicate transaction.

            Args:
                row: transaction dict.

            Returns:
                (date, description, amount_minor, currency, kind).
            """
            return (row["date"], row["description"], row["amount_minor"], row["currency"], row["kind"])
        existing = {signature(t.data) for t in ledger(db, user)}
        duplicates = sum(signature(row) in existing for row in record.rows)
        if duplicates and not body.acknowledge_possible_duplicates:
            raise HTTPException(409, f"{duplicates} possible overlapping rows. Review and acknowledge before importing.")
        advance(db, user, body.expected_version)
        for index, row in enumerate(record.rows):
            db.add(Transaction(tenant_id=user.id, idempotency_key=f"import_{record.id}_{index}", payload_hash=payload_hash(row), data=row))
        record.confirmed = True
        INGESTED.labels("csv").inc(len(record.rows))
        activity(db, user, "csv_import", started, "imported", count=len(record.rows))
        return {"imported": len(record.rows), "already_confirmed": False, "version": user.version}

    def score_past_forecasts(db, user, profile, transactions):
        """Score the owner's earlier forecasts whose period their history now covers (content-free, once per run).

        A forecast is scored when the balance date has moved past its last day: its everyday-spending forecast is compared
        with the spending that actually happened, and only the method, horizon, percentage error and whether the actual
        total fell inside the forecast range are stored.

        Args:
            db: database session for this request.
            user: the customer.
            profile: their current profile data.
            transactions: their ledger rows.

        Used by: create_forecast(). Seen on Admin > Forecast accuracy (Real accuracy).
        """
        as_of = profile["as_of"]
        run_ids = db.scalars(select(Run.id).where(Run.tenant_id == user.id, Run.source_version < user.version)
            .order_by(Run.created_at.desc()).limit(20)).all()
        if not run_ids:
            return
        refs = {rid: digest(f"forecast-run:{rid}")[:32] for rid in run_ids}
        scored = set(db.scalars(select(ForecastAccuracy.run_ref).where(ForecastAccuracy.run_ref.in_(refs.values()))))
        unscored = [rid for rid in run_ids if refs[rid] not in scored]
        if not unscored:
            return
        runs = db.scalars(select(Run).where(Run.tenant_id == user.id, Run.id.in_(unscored))).all()
        for run in runs:
            f = (run.result or {}).get("forecast") or {}
            timeline = f.get("timeline") or []
            if not timeline or timeline[-1]["date"] > as_of:
                continue
            ref = digest(f"forecast-run:{run.id}")[:32]
            if db.get(ForecastAccuracy, ref) is not None:
                continue
            first, last = date.fromisoformat(timeline[0]["date"]), date.fromisoformat(timeline[-1]["date"])
            actual = sum(daily_spending(transactions, first, last))
            if actual <= 0:
                continue
            if f.get("spend_total_q"):
                q = f["spend_total_q"]
                forecast_total = round(sum(sum(day) / len(day) for day in f["spend_q"]))
                inside = q[0] <= actual <= q[8]
            else:
                forecast_total, inside = f["daily_variable_minor"] * len(timeline), None
            db.add(ForecastAccuracy(run_ref=ref, method=f.get("method", "trailing_mean"), model=f.get("model"),
                horizon_days=len(timeline), spend_wape=round(abs(forecast_total - actual) * 100 / actual, 1), inside_band=inside))

    @app.post("/api/v1/forecasts")
    def create_forecast(body: ForecastInput, user=Depends(customer), db=Depends(db_session)):
        """Run the forecast pipeline and save the run (POST /api/v1/forecasts).

        Args:
            body: ForecastInput.
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            The run: analysis, forecast, checks, steps, id.

        Used by: Calculate forecast on Overview.
        """
        started = time.perf_counter()
        policy_read(db)  # Deterministic calculations remain available in pause modes.
        profile = db.get(Profile, user.id)
        if not profile:
            raise HTTPException(409, "Save financial context first")
        txs = db.scalars(select(Transaction).where(Transaction.tenant_id == user.id)).all()
        if not txs:
            raise HTTPException(409, "Add transactions first")
        rows = [{**t.data, "id": t.id} for t in txs]
        score_past_forecasts(db, user, profile.data, rows)
        with telemetry.tracing("forecast"):
            result = graph.invoke({"profile": profile.data, "transactions": rows, "horizon": body.horizon_days},
                config={"run_name": "forecast"})
        output = {k: result[k] for k in ("analysis", "forecast", "checks", "steps")}
        release = catalog.selected()
        output["prompt_release"] = release["release_version"]
        output["prompt_release_sha256"] = release["release_sha256"]
        output["ai_used"] = False
        run = Run(tenant_id=user.id, source_version=user.version, result=output)
        db.add(run)
        db.add(Metric(operation="forecast", duration_ms=int((time.perf_counter()-started)*1000), status="completed"))
        activity(db, user, "forecast", started, f"{body.horizon_days} days", "calculation")
        db.flush()
        return {**output, "id": run.id, "source_version": run.source_version}

    @app.get("/api/v1/runs/{run_id}")
    def get_run(run_id: str, user=Depends(customer), db=Depends(db_session)):
        """A saved forecast run and whether it is stale (GET /api/v1/runs/{id}).

        Args:
            run_id: run id.
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            The run with a stale flag.

        Used by: Overview and Agent activity.
        """
        run = db.get(Run, (user.id, run_id))
        if run is None:
            raise HTTPException(404, "Run not found")
        return {**run.result, "id": run.id, "source_version": run.source_version, "stale": run.source_version != user.version}

    @app.post("/api/v1/scenarios")
    def scenario(body: ScenarioInput, user=Depends(customer), db=Depends(db_session)):
        """A what-if purchase against a run (POST /api/v1/scenarios).

        Args:
            body: ScenarioInput.
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            The simulated timeline and checks.

        Used by: What if? on Overview.
        """
        started = time.perf_counter()
        baseline = db.get(Run, (user.id, body.baseline_id))
        if baseline is None:
            raise HTTPException(404, "Baseline not found")
        if baseline.source_version != user.version:
            raise HTTPException(409, "Baseline is stale; create a new forecast")
        forecast = baseline.result["forecast"]
        if body.currency != forecast["currency"] or not forecast["timeline"][0]["date"] <= body.date.isoformat() <= forecast["timeline"][-1]["date"]:
            raise HTTPException(422, "Use the baseline currency and a date within its horizon")
        result = simulate(baseline.result, body.amount_minor, body.date.isoformat())
        activity(db, user, "what_if", started, "reserve breach" if result["first_reserve_breach"] else "ok", "calculation")
        return {**result, "baseline_id": baseline.id, "source_version": baseline.source_version}

    @app.post("/api/v1/investigator/questions")
    def investigator_questions(user=Depends(customer), db=Depends(db_session)):
        """Fina's questions about missing context (POST /api/v1/investigator/questions).

        Args:
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            {questions, source, version}.

        Used by: "Ask Fina what's missing" on Your context.
        """
        started = time.perf_counter()
        profile = db.get(Profile, user.id)
        if not profile:
            raise HTTPException(409, "Save your financial context first")
        question_quota(user)
        txs = [t.data for t in db.scalars(select(Transaction).where(Transaction.tenant_id == user.id))]
        inputs = build_inputs(profile.data, txs)
        source, model = "calculation", None
        questions = fallback_questions(inputs)
        if inputs["profile_gaps"] or inputs["history_groups"]:
            try:
                proposed, provenance = gateway.investigate(inputs)
                checked = merge_questions(proposed, inputs)
                if checked is not None:
                    questions, source, model = checked, "ai", provenance["model"]
            except (OpenAIError, httpx.HTTPError, ValueError):
                pass  # Fall back to deterministic questions; provider details never reach the owner.
        activity(db, user, "investigator", started, f"{len(questions)} questions ({source})",
            "investigator" if source == "ai" else "calculation", model, len(questions))
        return {"questions": questions, "source": source, "version": user.version}

    def fresh_baseline(db, user, baseline_id):
        """A forecast run that is still current.

        Args:
            db: database session for this request (committed on success, rolled back on error).
            user: the customer.
            baseline_id: run id.

        Returns:
            The Run; 404 when missing, 409 when out of date.
        """
        baseline = db.get(Run, (user.id, baseline_id))
        if baseline is None:
            raise HTTPException(404, "Forecast not found")
        if baseline.source_version != user.version:
            raise HTTPException(409, "Your forecast is out of date. Refresh it on Overview, then ask again.")
        return baseline

    @app.post("/api/v1/assistant/explain")
    def explain(body: AssistantInput, user=Depends(customer), db=Depends(db_session)):
        """Answer a question about the forecast (POST /api/v1/assistant/explain, older endpoint).

        Args:
            body: AssistantInput.
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            {answer, source}.

        Used by: Earlier versions of the Overview chat.
        """
        baseline = fresh_baseline(db, user, body.baseline_id)
        scenario = body.scenario and {"amount_minor": body.scenario.amount_minor, "date": body.scenario.date.isoformat()}
        question_quota(user)
        answer = explain_answer(db, user, baseline, scenario, body.question, [t.model_dump() for t in body.history])
        return {k: v for k, v in answer.items() if not k.startswith("_")}

    def explain_answer(db, user, baseline, scenario, question, history):
        """Fina's grounded, risk-reviewed answer about a forecast, or the calculated one.

        Args:
            db: database session for this request (committed on success, rolled back on error).
            user: the customer.
            baseline: the run.
            question: the question.
            history: recent turns.
            scenario: purchase being discussed.

        Returns:
            {answer, source} where source is 'ai' or 'calculation'.

        Used by: Fina's explain and purchase answers.
        """
        started = time.perf_counter()
        timeline = baseline.result["forecast"]["timeline"]
        if scenario and not timeline[0]["date"] <= scenario["date"] <= timeline[-1]["date"]:
            raise HTTPException(422, "Use a purchase date within the forecast horizon")
        with db.no_autoflush:
            facts = build_facts(baseline.result, db.get(Profile, user.id).data, scenario)
        evidence = sorted(supervisor.rupees_in("\n".join(show_fact(k, f["value"], facts["currency"])
            for k, f in facts["facts"].items() if f["value"] is not None)))
        reply = {"source": "calculation", "questions": [], "limitations": facts["limitations"], "_evidence": evidence}
        try:
            output, provenance = gateway.explain(facts, question, history)
            text, problem = check_answer(output.payload.summary, facts)
            if problem is None:
                approved, _failed = gateway.review_explanation(facts, question, text)
                problem = None if approved else "risk"
            if problem is None:
                ASSISTANT_ANSWERS.labels("ai", "shown").inc()
                activity(db, user, "fina_question", started, "ai answer shown", "forecaster+risk",
                    f"{provenance['model']} + {gateway.risk_model}")
                return {**reply, "source": "ai", "answer": text, "questions": output.questions,
                    "model": provenance["model"], "prompt_version": provenance["prompt_version"], "risk_reviewed": True,
                    "_fallback": calculated_answer(facts, question)}
            ASSISTANT_ANSWERS.labels("ai", f"rejected_{problem}").inc()
            outcome = f"fallback: rejected {problem}"
        except (OpenAIError, httpx.HTTPError, ValueError):
            # Never expose provider errors or model text that failed validation.
            ASSISTANT_ANSWERS.labels("calculation", "ai_unavailable").inc()
            outcome = "fallback: ai unavailable"
        activity(db, user, "fina_question", started, outcome, "calculation")
        return {**reply, "answer": calculated_answer(facts, question)}

    def latest_fresh_run(db, user):
        """The newest forecast run that matches the owner's current data.

        Args:
            db: database session for this request (committed on success, rolled back on error).
            user: the customer.

        Returns:
            The run result or None.
        """
        run = db.scalar(select(Run).where(Run.tenant_id == user.id, Run.source_version == user.version)
            .order_by(Run.created_at.desc()).limit(1))
        return run.result if run else None

    def goal_view(goal, as_of, run):
        """A goal for the API with its calculated progress.

        Args:
            goal: Goal row.
            as_of: balance date.
            run: latest run.

        Returns:
            Goal dict with id and progress.
        """
        return {**goal.data, "id": goal.id, "progress": goal_progress(goal.data, as_of, run)}

    def owned_goal(db, user, goal_id):
        """One of the owner's goals.

        Args:
            db: database session for this request (committed on success, rolled back on error).
            user: the customer.
            goal_id: goal id.

        Returns:
            The Goal; 404 when not theirs.
        """
        goal = db.get(Goal, (user.id, goal_id))
        if goal is None:
            raise HTTPException(404, "Goal not found")
        return goal

    def profile_as_of(db, user):
        """The owner's balance date.

        Args:
            db: database session for this request (committed on success, rolled back on error).
            user: the customer.

        Returns:
            ISO date; 409 without a profile.
        """
        profile = db.get(Profile, user.id)
        if not profile:
            raise HTTPException(409, "Save your financial context first")
        return profile.data["as_of"]

    @app.get("/api/v1/goals")
    def list_goals(user=Depends(customer), db=Depends(db_session)):
        """All goals with progress, plan dates and savings proof; settles any points now proven (GET /api/v1/goals).

        Args:
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            {goals, has_forecast, unlocked}.

        Used by: The Goals page; unlocked items open the celebration pop-up.
        """
        as_of, run = profile_as_of(db, user), latest_fresh_run(db, user)
        unlocked, proofs = settle_goals(db, user)   # transfers imported since the tick can now prove a goal
        unlocked += award_achievements(db, user)
        goals = db.scalars(select(Goal).where(Goal.tenant_id == user.id).order_by(Goal.created_at)).all()
        views = [goal_view(g, as_of, run) for g in goals]
        pace = views[0]["progress"]["pace_per_month_minor"] if views else None
        plan = plans.waterfall(views, pace, as_of)
        return {"goals": [{**v, "plan": plan.get(v["id"]), "proof": proofs.get(v["id"])} for v in views],
            "has_forecast": run is not None, "unlocked": unlocked}

    @app.post("/api/v1/goals", status_code=201)
    def create_goal(body: GoalInput, user=Depends(customer), db=Depends(db_session)):
        """Add a goal (POST /api/v1/goals).

        Args:
            body: GoalInput.
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            The goal with progress and any unlocked achievements.

        Used by: Add a goal, Fina's and voice confirm cards.
        """
        started = time.perf_counter()
        as_of = profile_as_of(db, user)
        if body.target_date.isoformat() <= as_of:
            raise HTTPException(422, "Pick a target date after your balance date")
        if db.scalar(select(func.count()).select_from(Goal).where(Goal.tenant_id == user.id)) >= 20:
            raise HTTPException(409, "You can track up to 20 goals")
        goal = Goal(tenant_id=user.id, data={**body.model_dump(mode="json"), "status": "active"})
        db.add(goal)
        db.flush()
        activity(db, user, "goal_create", started)
        return {**goal_view(goal, as_of, latest_fresh_run(db, user)), "unlocked": award_achievements(db, user)}

    @app.patch("/api/v1/goals/{goal_id}")
    def update_goal(goal_id: str, body: GoalPatch, user=Depends(customer), db=Depends(db_session)):
        """Update a goal and settle its points (PATCH /api/v1/goals/{id}).

        Args:
            goal_id: goal id.
            body: GoalPatch.
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            The goal with points_change, proof and unlocked.

        Used by: Goal cards (amount set aside, tick, edits).
        """
        started = time.perf_counter()
        goal, as_of = owned_goal(db, user, goal_id), profile_as_of(db, user)
        changes = body.model_dump(mode="json", exclude_none=True)
        if "target_date" in changes and changes["target_date"] <= as_of:
            raise HTTPException(422, "Pick a target date after your balance date")
        goal.data = {**goal.data, **changes}
        activity(db, user, "goal_update", started, ",".join(sorted(changes)) or "none")
        unlocked, proofs = settle_goals(db, user)
        unlocked += award_achievements(db, user)
        change = sum(u["bonus"] for u in unlocked if u["id"] == f"goal:{goal.id}")
        return {**goal_view(goal, as_of, latest_fresh_run(db, user)), "points_change": change,
            "proof": proofs.get(goal.id), "unlocked": unlocked}

    @app.delete("/api/v1/goals/{goal_id}", status_code=204)
    def delete_goal(goal_id: str, user=Depends(customer), db=Depends(db_session)):
        """Remove a goal (DELETE /api/v1/goals/{id}).

        Args:
            goal_id: goal id.
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            204.

        Used by: The bin icon on a goal card.
        """
        started = time.perf_counter()
        db.delete(owned_goal(db, user, goal_id))
        activity(db, user, "goal_delete", started)

    @app.post("/api/v1/goals/{goal_id}/coach")
    def coach_goal(goal_id: str, user=Depends(customer), db=Depends(db_session)):
        """Fina's note on one goal (POST /api/v1/goals/{id}/coach).

        Args:
            goal_id: goal id.
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            {note, source}.

        Used by: "Ask Fina" on a goal card.
        """
        started = time.perf_counter()
        goal, as_of = owned_goal(db, user, goal_id), profile_as_of(db, user)
        question_quota(user)
        prog = goal_progress(goal.data, as_of, latest_fresh_run(db, user))
        currency = db.get(Profile, user.id).data["currency"]
        try:
            note, provenance = gateway.coach_goal(coach_facts(goal.data, prog))
            text = render_note(note, coach_facts(goal.data, prog), currency)
            if text:
                activity(db, user, "goal_coach", started, prog["status"], "goals", provenance["model"])
                return {"note": text, "source": "ai", "progress": prog}
        except (OpenAIError, httpx.HTTPError, ValueError):
            pass
        activity(db, user, "goal_coach", started, prog["status"], "calculation")
        return {"note": calculated_note(goal.data, prog, currency), "source": "calculation", "progress": prog}

    def advice_for(db, user, baseline, scenario, question):
        """Advisor: calculated options, worded by the model only if checks and the risk reviewer pass.

        Args:
            db: database session for this request (committed on success, rolled back on error).
            user: the customer.
            run: forecast run.
            question: the question.
            scenario: purchase, if any.

        Returns:
            {items, source} or a pause notice.

        Used by: Advice routes and Fina's advice answers.
        """
        started = time.perf_counter()
        if inference_policy() != "RUNNING":
            activity(db, user, "advice", started, "paused")
            return {"paused": True, "items": [], "source": "policy"}
        with db.no_autoflush:
            profile = db.get(Profile, user.id).data
            as_of, run = profile["as_of"], baseline.result
            goals = [goal_view(g, as_of, run) for g in db.scalars(select(Goal).where(Goal.tenant_id == user.id))
                if g.data.get("status") == "active"]
        options = levers(run, profile, scenario, goals)
        if not options:
            activity(db, user, "advice", started, "no options")
            return {"paused": False, "items": [], "source": "calculation"}
        currency = profile["currency"]
        facts = build_facts(run, profile, scenario)
        situation = {"purchase_considered": scenario is not None,
            "below_reserve": bool(facts["facts"].get("first_reserve_breach_with_purchase" if scenario else "first_reserve_breach_without_purchase")),
            "below_zero": bool(facts["facts"].get("first_below_zero_date_with_purchase" if scenario else "first_below_zero_date_without_purchase"))}
        shown_options = for_model(options, currency)
        try:
            proposed, provenance = gateway.advise(shown_options, question, situation)
            items = render_items(proposed, options, currency)
            if items:
                text = "\n".join(f"{i['title']}: {i['text']}" for i in items)
                approved, _failed = gateway.review_explanation(facts, question, text, "advice", shown_options["options"])
                if approved:
                    activity(db, user, "advice", started, f"{len(items)} shown", "advisor+risk",
                        f"{provenance['model']} + {gateway.risk_model}", len(items))
                    return {"paused": False, "items": items, "source": "ai", "risk_reviewed": True}
                outcome = "fallback: rejected risk"
            else:
                outcome = "fallback: rejected grounding"
        except (OpenAIError, httpx.HTTPError, ValueError):
            outcome = "fallback: ai unavailable"
        items = calculated_items(options, currency)
        activity(db, user, "advice", started, outcome, "calculation", count=len(items))
        return {"paused": False, "items": items, "source": "calculation"}

    @app.post("/api/v1/advisor/advice")
    def advice(body: AssistantInput, user=Depends(customer), db=Depends(db_session)):
        """Options for the current forecast (POST /api/v1/advisor/advice).

        Args:
            body: AssistantInput (forecast run id, question, optional purchase and history).
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            {items, source}.

        Used by: "Show me options" on Overview.
        """
        baseline = fresh_baseline(db, user, body.baseline_id)
        scenario = body.scenario and {"amount_minor": body.scenario.amount_minor, "date": body.scenario.date.isoformat()}
        timeline = baseline.result["forecast"]["timeline"]
        if scenario and not timeline[0]["date"] <= scenario["date"] <= timeline[-1]["date"]:
            raise HTTPException(422, "Use a purchase date within the forecast horizon")
        question_quota(user)
        return advice_for(db, user, baseline, scenario, body.question)

    @app.post("/api/v1/goals/{goal_id}/advice")
    def goal_advice(goal_id: str, user=Depends(customer), db=Depends(db_session)):
        """Ways to reach one goal sooner (POST /api/v1/goals/{id}/advice).

        Args:
            goal_id: goal id.
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            {items, source} or paused.

        Used by: "Ways to get there sooner" on a goal card.
        """
        started = time.perf_counter()
        goal, as_of = owned_goal(db, user, goal_id), profile_as_of(db, user)
        question_quota(user)
        if inference_policy() != "RUNNING":
            activity(db, user, "goal_advice", started, "paused")
            return {"paused": True, "items": [], "source": "policy"}
        profile = db.get(Profile, user.id).data
        prog = goal_progress(goal.data, as_of, latest_fresh_run(db, user))
        options = goal_levers(goal.data, prog, profile, [t.data for t in ledger(db, user)])
        if not options:
            activity(db, user, "goal_advice", started, "no options")
            return {"paused": False, "items": [], "source": "calculation", "status": prog["status"]}
        currency = profile["currency"]
        shown = for_model(options, currency)
        question = f"How can I reach my goal {goal.data['name']} sooner?"
        try:
            proposed, provenance = gateway.advise(shown, question, {"goal": goal.data["name"], "goal_status": prog["status"]})
            items = render_items(proposed, options, currency)
            if items:
                text = "\n".join(f"{i['title']}: {i['text']}" for i in items)
                approved, _failed = gateway.review_explanation({"goal": goal.data["name"], "status": prog["status"]},
                    question, text, "advice", shown["options"])
                if approved:
                    activity(db, user, "goal_advice", started, f"{len(items)} shown", "advisor+risk",
                        f"{provenance['model']} + {gateway.risk_model}", len(items))
                    return {"paused": False, "items": items, "source": "ai", "risk_reviewed": True, "status": prog["status"]}
                outcome = "fallback: rejected risk"
            else:
                outcome = "fallback: rejected grounding"
        except (OpenAIError, httpx.HTTPError, ValueError):
            outcome = "fallback: ai unavailable"
        items = calculated_items(options, currency)
        activity(db, user, "goal_advice", started, outcome, "calculation", count=len(items))
        return {"paused": False, "items": items, "source": "calculation", "status": prog["status"]}

    def outlook_for(db, user):
        """The owner's profile and Outlook view.

        Args:
            db: database session for this request (committed on success, rolled back on error).
            user: the customer.

        Returns:
            (profile, outlook view).
        """
        profile = db.get(Profile, user.id)
        if not profile:
            raise HTTPException(409, "Save your financial context first")
        run = latest_fresh_run(db, user)
        goals = [goal_view(g, profile.data["as_of"], run) for g in db.scalars(select(Goal).where(Goal.tenant_id == user.id))
            if g.data.get("status") == "active"]
        return profile.data, outlook(profile.data, [t.data for t in ledger(db, user)], goals, run)

    @app.get("/api/v1/outlook")
    def get_outlook(user=Depends(customer), db=Depends(db_session)):
        """Patterns, where they lead if they continue, debts and advice (GET /api/v1/outlook).

        Args:
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            The Outlook view with advice and debts.

        Used by: The Outlook page.
        """
        started = time.perf_counter()
        profile, view = outlook_for(db, user)
        currency = profile["currency"]
        activity(db, user, "outlook", started, f"{len(view['patterns'])} patterns, {len(view['warnings'])} warnings", "calculation")
        debt_view = debt_agent.analyse(owner_debts(db, user), profile, [t.data for t in ledger(db, user)])
        view["debts"] = {**debt_view, "advice": calculated_items(debt_view["options"], currency)}
        return {**view, "pattern_texts": [pattern_text(p, currency) for p in view["patterns"]],
            "new_texts": [f"{n['category'].capitalize()}: {money_text(n['recent_month'], currency)} in the last 30 days, with nothing "
                "the month before. New spending can be one-off, so I'll watch it rather than project it." for n in view.get("new_spending", [])],
            "warnings": [{"ref": w["ref"], "kind": w["kind"], "title": w["summary"], "text": warning_text(w, currency),
                "advice": advice_items(w["advice"], currency)} for w in view["warnings"]]}

    @app.post("/api/v1/outlook/explain")
    def explain_outlook(user=Depends(customer), db=Depends(db_session)):
        """The behavioural pattern agent explains the warnings (POST /api/v1/outlook/explain).

        Args:
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            {items, source}.

        Used by: "Explain with Fina" on the Outlook.
        """
        started = time.perf_counter()
        question_quota(user)
        profile, view = outlook_for(db, user)
        if not view["warnings"]:
            return {"items": [], "source": "calculation"}
        if inference_policy() != "RUNNING":
            return {"items": [], "source": "policy", "paused": True}
        currency, shown = profile["currency"], pattern_view(view)
        try:
            proposed, provenance = gateway.describe_patterns(shown)
            items = render_narrative(proposed, view, currency)
            if items:
                text = "\n".join(f"{i['title']}: {i['text']}" for i in items)
                approved, _failed = gateway.review_explanation(shown, "What happens if my spending keeps rising?", text,
                    "explanation", shown["warnings"])
                if approved:
                    activity(db, user, "outlook_explain", started, f"{len(items)} shown", "patterns+risk",
                        f"{provenance['model']} + {gateway.risk_model}", len(items))
                    return {"items": items, "source": "ai", "risk_reviewed": True}
                outcome = "fallback: rejected risk"
            else:
                outcome = "fallback: rejected grounding"
        except (OpenAIError, httpx.HTTPError, ValueError):
            outcome = "fallback: ai unavailable"
        activity(db, user, "outlook_explain", started, outcome, "calculation")
        return {"items": [{"ref": w["ref"], "title": w["summary"], "text": warning_text(w, currency)} for w in view["warnings"]],
            "source": "calculation"}

    def points_ledger(db, user):
        """The owner's FinaPoints ledger.

        Args:
            db: database session for this request (committed on success, rolled back on error).
            user: the customer.

        Returns:
            (Reward rows, entry dicts with key and date).
        """
        rows = db.scalars(select(Reward).where(Reward.tenant_id == user.id).order_by(Reward.created_at)).all()
        return rows, [{**r.data, "key": r.idem_key, "date": r.created_at.date().isoformat()} for r in rows]

    def goal_records(db, user):
        """The owner's goals and their records with creation dates.

        Args:
            db: database session for this request (committed on success, rolled back on error).
            user: the customer.

        Returns:
            (Goal rows, record dicts with id and created). "created" is the owner's local date (their profile timezone),
            the same calendar as their transactions, so a transfer from the day before never counts as proof.
        """
        goals = db.scalars(select(Goal).where(Goal.tenant_id == user.id).order_by(Goal.created_at)).all()
        profile = db.get(Profile, user.id)
        try:
            zone = ZoneInfo(profile.data["timezone"]) if profile else timezone.utc
        except (KeyError, ZoneInfoNotFoundError):
            zone = timezone.utc
        local = lambda moment: moment.replace(tzinfo=timezone.utc).astimezone(zone).date().isoformat()
        return goals, [{**g.data, "id": g.id, "created": local(g.created_at)} for g in goals]

    def settle_goals(db, user):
        """Award points to every goal its savings transfers now prove.

        Args:
            db: database session for this request (committed on success, rolled back on error).
            user: the customer.

        Returns:
            (celebrations for the pop-up, proof status by goal id).

        Used by: Goals and Referral loads, goal updates.
        """
        _, entries = points_ledger(db, user)
        goals, records = goal_records(db, user)
        found = fina_points.allocate_proof(records, [t.data for t in ledger(db, user)], fina_points.proven_goals(entries))
        unlocked, proofs = [], {}
        for goal, record in zip(goals, records):
            if record["status"] == "achieved":
                proofs[goal.id] = fina_points.proof_status(record, found.get(goal.id, 0))
            entry = fina_points.goal_entry(goal.id, record, entries, found.get(goal.id, 0))
            if entry is None:
                continue
            key = entry.pop("key")
            if db.scalar(select(Reward).where(Reward.tenant_id == user.id, Reward.idem_key == key)) is None:
                db.add(Reward(tenant_id=user.id, idem_key=key, data=entry))
                db.flush()
                activity(db, user, "points_award", time.perf_counter(), entry["kind"])
                unlocked.append(goal_celebration(goal))
        return unlocked, proofs

    def goal_celebration(goal):
        """The pop-up item for a completed goal.

        Args:
            goal: the Goal.

        Returns:
            {id, kind 'goal', title, about, bonus, tier}.
        """
        return {"id": f"goal:{goal.id}", "kind": "goal", "title": f"{goal.data['name']} complete",
            "about": "Saved in full, and your savings transfers prove it.", "bonus": fina_points.GOAL_POINTS, "tier": "gold"}

    def award_achievements(db, user):
        """Award each newly earned achievement once, with its bonus.

        Args:
            db: database session for this request (committed on success, rolled back on error).
            user: the customer.

        Returns:
            Pop-up items for achievements unlocked just now.

        Used by: Goals, rewards, debts, invest and redeem responses.
        """
        profile = db.get(Profile, user.id)
        _, entries = points_ledger(db, user)
        if profile:
            _, run_now, ready = invest_view(db, user)
            extra = {"goals_active": len(active_goals(db, user, profile.data["as_of"], run_now)),
                "debts_cleared": sum(d["balance_minor"] == 0 for d in owner_debts(db, user)), "investor_ready": int(ready["ready"])}
        else:
            extra = {}
        unlocked = []
        for a in fina_points.achievements(entries, extra):
            key = f"achievement:{a['id']}"
            if a["new"] and db.scalar(select(Reward).where(Reward.tenant_id == user.id, Reward.idem_key == key)) is None:
                db.add(Reward(tenant_id=user.id, idem_key=key, data={"kind": "achievement", "points": a["bonus"],
                    "label": f"Achievement: {a['title']}", "ref": a["id"]}))
                db.flush()
                activity(db, user, "achievement", time.perf_counter(), a["id"])
                unlocked.append({"kind": "achievement", **{k: a[k] for k in ("id", "title", "about", "bonus", "tier")}})
        return unlocked

    def referral_code(db, user):
        """The owner's referral code, created on first use.

        Args:
            db: database session for this request (committed on success, rolled back on error).
            user: the customer.

        Returns:
            The code.
        """
        code = db.scalar(select(ReferralCode).where(ReferralCode.tenant_id == user.id))
        if code is None:  # customers who joined before referrals existed
            code = ReferralCode(code=fina_points.new_code(), tenant_id=user.id)
            db.add(code)
            db.flush()
        return code.code

    @app.get("/api/v1/rewards")
    def get_rewards(user=Depends(customer), db=Depends(db_session)):
        """FinaPoints balance, history, vouchers, referral code and achievements (GET /api/v1/rewards).

        Args:
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            The rewards view plus unlocked items.

        Used by: The Referral page.
        """
        profile = db.get(Profile, user.id)
        # Goals are re-checked against the ledger and achievements awarded before computing the balance.
        unlocked = settle_goals(db, user)[0] if profile else []
        unlocked += award_achievements(db, user)
        _, entries = points_ledger(db, user)
        if profile:
            _, run_now, ready = invest_view(db, user)
            extra = {"goals_active": len(active_goals(db, user, profile.data["as_of"], run_now)),
                "debts_cleared": sum(d["balance_minor"] == 0 for d in owner_debts(db, user)), "investor_ready": int(ready["ready"])}
        else:
            extra = {}
        badges = fina_points.achievements(entries, extra)
        points = fina_points.balance(entries)
        txs = [t.data for t in ledger(db, user)]
        as_of = profile.data["as_of"] if profile else date.today().isoformat()
        rising = (scan_patterns(txs, profile.data) or {}).get("rising") if profile else None
        order, reasons = fina_points.recommend(txs, as_of, rising)
        vouchers = sorted(fina_points.CATALOG, key=lambda v: (order.index(v["type"]) if v["type"] in order else 99, v["points"]))
        code = referral_code(db, user)
        joined = sum(e["kind"] == "referral" for e in entries)
        return {"balance": points, "worth_minor": points * fina_points.RUPEES_PER_1000 * 100 // 1000,
            "earned_total": fina_points.earned(entries),
            "history": list(reversed([{k: e[k] for k in ("kind", "points", "label", "date")} for e in fina_points.counted(entries)]))[:50],
            "vouchers": [{**v, "recommended": v["type"] in reasons, "reason": reasons.get(v["type"]), "affordable": points >= v["points"]}
                for v in vouchers],
            "redeemed": [{"title": e["label"], "code": e["voucher_code"], "date": e["date"], "points": -e["points"]}
                for e in reversed(entries) if e["kind"] == "redeem"],
            "referral": {"code": code, "friends_joined": joined},
            "achievements": [{k: a[k] for k in ("id", "title", "about", "target", "progress", "earned", "bonus", "tier")} for a in badges],
            "rules": {"goal": fina_points.GOAL_POINTS, "referral": fina_points.REFERRAL_POINTS, "welcome": fina_points.WELCOME_POINTS,
                "points": 1000, "rupees": 30}, "unlocked": unlocked}

    @app.post("/api/v1/rewards/redeem")
    def redeem(body: Redeem, user=Depends(customer), db=Depends(db_session)):
        """Exchange points for a demo voucher (POST /api/v1/rewards/redeem).

        Args:
            body: Redeem.
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            {code, title, duplicate, unlocked}.

        Used by: Voucher buttons on the Referral page.
        """
        started = time.perf_counter()
        voucher = next((v for v in fina_points.CATALOG if v["id"] == body.voucher_id), None)
        if voucher is None:
            raise HTTPException(404, "Voucher not found")
        key = f"redeem:{body.idempotency_key}"
        existing = db.scalar(select(Reward).where(Reward.tenant_id == user.id, Reward.idem_key == key))
        if existing is not None:
            return {"code": existing.data["voucher_code"], "title": existing.data["label"], "duplicate": True}
        _, entries = points_ledger(db, user)
        if fina_points.balance(entries) < voucher["points"]:
            raise HTTPException(409, f"You need {voucher['points'] - fina_points.balance(entries)} more FinaPoints for this voucher")
        code = "DEMO-" + fina_points.new_code()
        db.add(Reward(tenant_id=user.id, idem_key=key, data={"kind": "redeem", "points": -voucher["points"], "label": voucher["title"],
            "ref": voucher["id"], "voucher_code": code}))
        db.flush()
        activity(db, user, "redeem", started, voucher["type"])
        return {"code": code, "title": voucher["title"], "duplicate": False, "unlocked": award_achievements(db, user)}

    def active_goals(db, user, as_of, run):
        """Active goals with progress.

        Args:
            db: database session for this request (committed on success, rolled back on error).
            user: the customer.
            as_of: balance date.
            run: latest run.

        Returns:
            Goal dicts.
        """
        return [goal_view(g, as_of, run) for g in db.scalars(select(Goal).where(Goal.tenant_id == user.id).order_by(Goal.created_at))
            if g.data.get("status") == "active"]

    def owner_debts(db, user):
        """The owner's tracked debts.

        Args:
            db: database session for this request (committed on success, rolled back on error).
            user: the customer.

        Returns:
            Debt dicts with ids.
        """
        return [{**d.data, "id": d.id} for d in db.scalars(select(Debt).where(Debt.tenant_id == user.id).order_by(Debt.created_at))]

    def explain_options(db, user, options, question, situation, currency, action):
        """Advisor wording plus risk review for any agent's options; calculated wording otherwise.

        Args:
            db: database session for this request (committed on success, rolled back on error).
            user: the customer.
            options: calculated options.
            question: framing question.
            situation: summary facts.
            action: activity name.

        Returns:
            {items, source}.

        Used by: Explain buttons on Invest and Debts.
        """
        started = time.perf_counter()
        if inference_policy() != "RUNNING":
            return {"items": [], "source": "policy", "paused": True}
        if not options:
            return {"items": [], "source": "calculation"}
        shown = for_model(options, currency)
        try:
            proposed, provenance = gateway.advise(shown, question, situation)
            items = render_items(proposed, options, currency)
            if items:
                text = "\n".join(f"{i['title']}: {i['text']}" for i in items)
                approved, _ = gateway.review_explanation(situation, question, text, "advice", shown["options"])
                if approved:
                    activity(db, user, action, started, f"{len(items)} shown", "advisor+risk",
                        f"{provenance['model']} + {gateway.risk_model}", len(items))
                    return {"items": items, "source": "ai", "risk_reviewed": True}
        except (OpenAIError, httpx.HTTPError, ValueError):
            pass
        activity(db, user, action, started, "calculated", "calculation")
        return {"items": calculated_items(options, currency), "source": "calculation"}

    # Debt agent ---------------------------------------------------------------------------
    @app.get("/api/v1/debts")
    def get_debts(user=Depends(customer), db=Depends(db_session)):
        """The debt agent's view (GET /api/v1/debts).

        Args:
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            Debts, detected obligations, pressure, advice and goals.

        Used by: Outlook > Debts and fixed payments.
        """
        profile = db.get(Profile, user.id)
        if not profile:
            raise HTTPException(409, "Save your financial context first")
        view = debt_agent.analyse(owner_debts(db, user), profile.data, [t.data for t in ledger(db, user)])
        return {**view, "advice": calculated_items(view["options"], profile.data["currency"])}

    @app.post("/api/v1/debts", status_code=201)
    def add_debt(body: DebtInput, user=Depends(customer), db=Depends(db_session)):
        """Track a debt found in history (POST /api/v1/debts).

        Args:
            body: DebtInput.
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            The debt plus unlocked items.

        Used by: Entering owed and rate on a detected row.
        """
        if db.scalar(select(func.count()).select_from(Debt).where(Debt.tenant_id == user.id)) >= 20:
            raise HTTPException(409, "You can track up to 20 debts")
        debt = Debt(tenant_id=user.id, data=body.model_dump())
        db.add(debt)
        db.flush()
        activity(db, user, "debt_add", time.perf_counter(), body.kind)
        return {**debt.data, "id": debt.id, "unlocked": award_achievements(db, user) if db.get(Profile, user.id) else []}

    @app.patch("/api/v1/debts/{debt_id}")
    def update_debt(debt_id: str, body: DebtPatch, user=Depends(customer), db=Depends(db_session)):
        """Change only the amount owed and yearly rate (PATCH /api/v1/debts/{id}).

        Args:
            debt_id: debt id.
            body: DebtPatch.
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            The debt plus unlocked items.

        Used by: Inline fields on Outlook > Debts; clearing to zero unlocks Debt Slayer.
        """
        debt = db.get(Debt, (user.id, debt_id))
        if debt is None:
            raise HTTPException(404, "Debt not found")
        debt.data = {**debt.data, **body.model_dump(exclude_none=True)}
        db.flush()
        activity(db, user, "debt_update", time.perf_counter(), debt.data["kind"])
        return {**debt.data, "id": debt.id, "unlocked": award_achievements(db, user) if db.get(Profile, user.id) else []}

    @app.delete("/api/v1/debts/{debt_id}", status_code=204)
    def delete_debt(debt_id: str, user=Depends(customer), db=Depends(db_session)):
        """Stop tracking a debt (DELETE /api/v1/debts/{id}).

        Args:
            debt_id: debt id.
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            204.

        Used by: Debt rows.
        """
        debt = db.get(Debt, (user.id, debt_id))
        if debt is None:
            raise HTTPException(404, "Debt not found")
        db.delete(debt)

    # Goal recovery and emergency reroute ----------------------------------------------------
    @app.get("/api/v1/goals/{goal_id}/recovery")
    def goal_recovery(goal_id: str, user=Depends(customer), db=Depends(db_session)):
        """Routes back on track for one goal (GET /api/v1/goals/{id}/recovery).

        Args:
            goal_id: goal id.
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            {status, routes}.

        Used by: The recovery box on a goal card that fell behind.
        """
        goal, as_of = owned_goal(db, user, goal_id), profile_as_of(db, user)
        profile = db.get(Profile, user.id).data
        prog = goal_progress(goal.data, as_of, latest_fresh_run(db, user))
        options = goal_levers(goal.data, prog, profile, [t.data for t in ledger(db, user)])
        return {"status": prog["status"], "routes": plans.recovery(goal.data, prog, as_of, options)}

    def reroute_plan(db, user, amount):
        """The emergency re-plan for an amount.

        Args:
            db: database session for this request (committed on success, rolled back on error).
            user: the customer.
            amount: minor units.

        Returns:
            (plan, as_of, goal rows).
        """
        as_of, run = profile_as_of(db, user), latest_fresh_run(db, user)
        goals = active_goals(db, user, as_of, run)
        if not goals:
            raise HTTPException(409, "Add a goal first")
        pace = goals[0]["progress"]["pace_per_month_minor"]
        return plans.emergency_plan(goals, amount, pace, as_of, run), as_of

    @app.post("/api/v1/goals/reroute")
    def preview_reroute(body: Reroute, user=Depends(customer), db=Depends(db_session)):
        """What an emergency withdrawal does to every goal, before anything changes (POST /api/v1/goals/reroute).

        Args:
            body: Reroute.
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            The plan.

        Used by: "Had an emergency?" on Goals.
        """
        plan, _ = reroute_plan(db, user, body.amount_minor)
        return plan

    @app.post("/api/v1/goals/reroute/apply")
    def apply_reroute(body: Reroute, user=Depends(customer), db=Depends(db_session)):
        """Apply an emergency re-plan (POST /api/v1/goals/reroute/apply).

        Args:
            body: Reroute.
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            {applied, plan}.

        Used by: "Apply" in the re-plan dialog.
        """
        started = time.perf_counter()
        plan, as_of = reroute_plan(db, user, body.amount_minor)
        for row in plan["goals"]:
            goal = owned_goal(db, user, row["id"])
            goal.data = {**goal.data, "saved_minor": row["saved_after"]}
        if body.create_rebuild_goal and plan["rebuild_goal"]["target_date"] > as_of:
            db.add(Goal(tenant_id=user.id, data={**plan["rebuild_goal"], "saved_minor": 0, "status": "active"}))
        activity(db, user, "emergency_reroute", started, f"{len(plan['goals'])} goals", "calculation")
        return plan

    # Investment agent -----------------------------------------------------------------------
    def invest_view(db, user):
        """Inputs and the readiness result for the investment agent.

        Args:
            db: database session for this request (committed on success, rolled back on error).
            user: the customer.

        Returns:
            (profile, run, readiness view).
        """
        profile = db.get(Profile, user.id)
        if not profile:
            raise HTTPException(409, "Save your financial context first")
        run = latest_fresh_run(db, user)
        goals = active_goals(db, user, profile.data["as_of"], run)
        from .goals import pace_from
        view = invest_agent.readiness(profile.data, [t.data for t in ledger(db, user)], goals, owner_debts(db, user), pace_from(run))
        return profile.data, run, view

    @app.get("/api/v1/invest")
    def get_invest(user=Depends(customer), db=Depends(db_session)):
        """Readiness checks, SIP amount, education and headlines (GET /api/v1/invest).

        Args:
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            The Invest view plus unlocked items.

        Used by: The Invest page.
        """
        profile, run, view = invest_view(db, user)
        options = invest_agent.options_for(view, profile)
        suggested = []
        if view["ready"]:
            suggested.append({"name": "Start investing", "target_minor": view["sip_minor"] * 12,
                "target_date": (date.fromisoformat(profile["as_of"]) + timedelta(days=365)).isoformat()})
        ef = next(c for c in view["checks"] if c["id"] == "emergency_fund")["facts"]
        if not view["checks"][0]["passed"] and ef["essentials_per_month"]:
            suggested.append({"name": "Emergency fund", "target_minor": ef["essentials_per_month"] * invest_agent.EMERGENCY_MONTHS,
                "target_date": (date.fromisoformat(profile["as_of"]) + timedelta(days=183)).isoformat()})
        return {**view, "has_forecast": run is not None, "advice": calculated_items(options, profile["currency"]),
            "suggested_goals": suggested, "news": invest_agent.headlines(settings.news_feeds), "unlocked": award_achievements(db, user)}

    @app.post("/api/v1/invest/explain")
    def explain_invest(user=Depends(customer), db=Depends(db_session)):
        """Fina explains the readiness result (POST /api/v1/invest/explain).

        Args:
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            {items, source}.

        Used by: "Explain with Fina" on Invest.
        """
        question_quota(user)
        profile, _, view = invest_view(db, user)
        options = invest_agent.options_for(view, profile)
        return explain_options(db, user, options, "Can I start investing?", {"investment_status": view["status"]},
            profile["currency"], "invest_explain")

    @app.post("/api/v1/voice/transcribe")
    def transcribe_voice(body: VoiceInput, user=Depends(customer), db=Depends(db_session)):
        """Speech to text for the mic buttons (POST /api/v1/voice/transcribe); audio is never stored.

        Args:
            body: VoiceInput.
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            {text}.

        Used by: Mic buttons when a server speech model is configured.
        """
        started = time.perf_counter()
        stamp = time.monotonic()
        with lock:
            queue = voice_clips[user.id]
            while queue and queue[0] < stamp - 3600:
                queue.popleft()
            if len(queue) >= 40:
                limit_hit(user, "voice_hourly")
                raise HTTPException(429, "That's a lot of voice for one hour. Please type for now, or try again later.")
            queue.append(stamp)
        try:
            audio = base64.b64decode(body.audio_base64, validate=True)
        except ValueError:
            raise HTTPException(422, "That recording could not be read")
        if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
            raise HTTPException(422, "Voice must be a WAV recording")
        try:
            text = gateway.transcribe(body.audio_base64)
        except ValueError:
            raise HTTPException(503, "Voice transcription is off right now. Type your message instead.")
        except OpenAIError:
            raise HTTPException(502, "I couldn't make that out. Try again, or type instead.")
        activity(db, user, "voice_transcribe", started, "ok" if text else "empty", "transcriber", gateway.speech_model)
        return {"text": text}

    @app.post("/api/v1/assistant/message")
    def chat_message(body: ChatMessage, user=Depends(customer), db=Depends(db_session)):
        """Fina's chat: the coordinator routes the message to an agent, then verifies the reply before it is shown
        (POST /api/v1/assistant/message).

        Args:
            body: ChatMessage.
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            {route, source, answer, action, verified}. A reply that fails a check is replaced by the agent's calculated
            answer when there is one, otherwise by a safe message, with no confirm card.

        Used by: The Fina chat and the Goals / Your context voice cards. Blocked replies show on Admin > Users & activity
        as "Fina reply blocked", and in Grafana as finpilot_fina_replies_verified_total.
        """
        started = time.perf_counter()
        reply = answer_message(body, user, db)
        evidence, fallback = reply.pop("_evidence", None), reply.pop("_fallback", None)
        problems = supervisor.verify(body.question, reply, evidence)
        FINA_VERIFIED.labels(reply.get("route", "unknown"), "passed" if not problems else problems[0]).inc()
        if not problems:
            if sharing(db, user).share_questions:
                share_question(db, user, body, reply)
            return {**reply, "verified": True}
        activity(db, user, "fina_blocked", started, ",".join(problems), "coordinator")
        if fallback and not supervisor.verify(body.question, {**reply, "answer": fallback, "action": None}, evidence):
            return {**reply, "answer": fallback, "source": "calculation", "action": None, "verified": True, "replaced": problems}
        return {**reply, "answer": supervisor.SAFE_REPLY, "source": "calculation", "action": None, "verified": False,
            "replaced": problems}

    def answer_message(body, user, db):
        """The coordinator's routing step: decide what the message means and let the right agent answer it.

        Args:
            body: ChatMessage.
            user: the signed-in customer.
            db: database session for this request.

        Returns:
            The agent's reply {route, source, answer, action}, plus private _evidence (amounts the agent was given) and
            _fallback (a calculated answer) for the supervisor check in chat_message().
        """
        started = time.perf_counter()
        profile = db.get(Profile, user.id)
        if not profile:
            raise HTTPException(409, "Save your financial context first")
        question_quota(user)
        history = [t.model_dump() for t in body.history]
        baseline = db.get(Run, (user.id, body.baseline_id)) if body.baseline_id else None
        fresh = baseline is not None and baseline.source_version == user.version
        as_of = date.fromisoformat(profile.data["as_of"])
        horizon_end = date.fromisoformat(baseline.result["forecast"]["timeline"][-1]["date"]) if fresh \
            else as_of + timedelta(days=90)
        run_now = baseline.result if fresh else latest_fresh_run(db, user)
        active = [{**g.data, "id": g.id} for g in db.scalars(select(Goal).where(Goal.tenant_id == user.id))
            if g.data.get("status") == "active"]
        goal_hit = goal_route(body.question, active) if active else \
            (("emergency", {"amount_minor": None}) if EMERGENCY.search(body.question) else None)
        intent, details, model = (goal_hit if goal_hit else (rule_route(body.question), None)) + (None,)
        if intent is None:
            try:
                payload, provenance = gateway.coordinate(body.question, history, as_of.isoformat(), horizon_end.isoformat())
                descriptions = ([(t.id, t.data["description"]) for t in
                    db.scalars(select(Transaction).where(Transaction.tenant_id == user.id))]
                    if payload.get("intent") == "category" else [])
                intent, details = validate_route(payload, body.question, as_of, horizon_end, descriptions)
                model = provenance["model"]
            except (OpenAIError, httpx.HTTPError, ValueError):
                intent = offline_route(body.question)
        activity(db, user, "fina_message", started, intent, "coordinator" if model else "rules", model)
        reply = {"route": intent, "source": "calculation", "action": None}
        currency = profile.data["currency"]
        if body.page == "context" and not (intent in {"upcoming_flow", "profile_value"} and details):
            # On Your context a short phrase ("freelance fee") is still something to add: prefill what was said.
            kind, draft = flow_draft(body.question, as_of)
            if kind == "profile_value":
                return {**reply, "route": kind, "answer": "Got it. Check the amount and save it; nothing changes until you do.",
                    "action": {"type": "confirm_value", **draft}}
            if draft["label"] or draft["amount_minor"]:
                missing = [w for w, k in (("the amount", "amount_minor"), ("the date", "date")) if not draft[k]]
                way = "coming in" if draft["direction"] == "in" else "going out"
                ask = f"Fill in {' and '.join(missing)}, then check the rest. " if missing else "Check the details. "
                return {**reply, "route": kind, "answer": f"I'll add “{draft['label'] or 'this'}” as money {way}. {ask}"
                    "Nothing changes until you confirm.", "action": {"type": "confirm_flow", "flow": draft}}
        if body.page == "goals" and intent not in {"goal_status", "goal_saving", "goal_saving_ask", "emergency"} \
                and not (intent == "goal" and details):
            # On the Goals card a half-heard sentence ("6004 Goa") is still a goal: offer what was caught to check.
            draft = goal_draft(body.question, as_of)
            if draft["name"] or draft["target_minor"]:
                missing = [w for w, k in (("a name", "name"), ("the amount", "target_minor"), ("a date", "target_date")) if not draft[k]]
                return {**reply, "route": "goal", "answer": "Here's the goal I caught. " + (f"Add {' and '.join(missing)}, then check "
                    "the rest" if missing else "Check the details") + ". Voice sometimes hears “for” as “4”, so look at the amount "
                    "too. Nothing is saved until you add it.", "action": {"type": "confirm_goal", "goal": draft}}
        if intent == "goal_status":
            shown = [(g, goal_progress(g, profile.data["as_of"], run_now)) for g in details["goals"][:3]]
            evidence = supervisor.rupees_in(" ".join(goal_show(k, f["value"], currency)
                for g, prog in shown for k, f in coach_facts(g, prog)["facts"].items()))
            return {**reply, "answer": " ".join(calculated_note(g, prog, currency) for g, prog in shown),
                "action": {"type": "go_goals"}, "_evidence": sorted(evidence)}
        if intent == "goal_saving":
            g = details["goal"]
            new_saved = g["saved_minor"] + details["add_minor"]
            return {**reply, "answer": f"Lovely, every bit counts. Should I add this to {g['name']}? It would go from "
                f"{money_text(g['saved_minor'], currency)} to {money_text(new_saved, currency)} of {money_text(g['target_minor'], currency)}.",
                "action": {"type": "confirm_goal_saving", "goal_id": g["id"], "name": g["name"], "add_minor": details["add_minor"],
                    "saved_minor": g["saved_minor"], "target_minor": g["target_minor"]}}
        if intent == "goal_saving_ask":
            names = ", ".join(g["name"] for g in details["goals"][:4])
            return {**reply, "answer": f"Nice work! How much did you set aside, and for which goal ({names})? For example: "
                f"\"I saved 2,000 for {details['goals'][0]['name']}\"."}
        if intent in {"explain", "purchase"}:
            if not fresh:
                return {**reply, "answer": "I'd love to look at that with you. Create a forecast on Overview first "
                    "(or refresh it if you've changed things), then ask me again.", "action": {"type": "go_overview"}}
            scenario = body.scenario and {"amount_minor": body.scenario.amount_minor, "date": body.scenario.date.isoformat()}
            note = ""
            if intent == "purchase":
                first_day = baseline.result["forecast"]["timeline"][0]["date"]
                scenario = {"amount_minor": details["amount_minor"], "date": details["date"] or first_day}
                if not details["date"]:
                    note = "I've assumed you'd pay tomorrow; tell me the day or use What if to change it. "
            answer = explain_answer(db, user, baseline, scenario, body.question, history)
            return {**answer, "route": intent, "answer": note + answer["answer"],
                "action": {"type": "scenario", **scenario} if intent == "purchase" else None}
        if intent == "upcoming_flow":
            kind = "income" if details["amount_minor"] > 0 else "bill"
            return {**reply, "answer": f"Got it. Should I add this {kind} to your plan? Check the details first; "
                "nothing changes until you confirm.", "action": {"type": "confirm_flow", "flow": details}}
        if intent == "profile_value":
            what = "available cash" if details["field"] == "available_cash" else "reserve"
            return {**reply, "answer": f"Thanks. Should I save this as your {what}? You can adjust it before confirming.",
                "action": {"type": "confirm_value", **details}}
        if intent == "category":
            if not details["ids"]:
                return {**reply, "answer": f"I couldn't find any transactions mentioning \"{details['merchant']}\". "
                    "Check the spelling, or fix the category from the Transactions page."}
            count = len(details["ids"])
            return {**reply, "answer": f"I found {count} transaction{'s' if count != 1 else ''} matching "
                f"\"{details['merchant']}\". Should I label {'them' if count != 1 else 'it'} \"{details['category']}\"? "
                "I'll remember it for similar ones.", "action": {"type": "confirm_category", **details}}
        if intent == "setup":
            return {**reply, "answer": REPLIES["setup"], "action": {"type": "open_questions"}}
        if intent == "outlook":
            return {**reply, "answer": REPLIES["outlook"], "action": {"type": "go_outlook"}}
        if intent == "debts":
            return {**reply, "answer": REPLIES["debts"], "action": {"type": "go_outlook"}}
        if intent == "invest":
            return {**reply, "answer": REPLIES["invest"], "action": {"type": "go_invest"}}
        if intent == "emergency" and not active:
            return {**reply, "answer": "I'm sorry you had to deal with that; this is exactly what savings are for. You have no "
                "active goals, so there's nothing to re-plan. When you're ready, a goal to rebuild your emergency fund will "
                "help you get back on your feet.", "action": {"type": "go_goals"}}
        if intent == "emergency":
            if not details["amount_minor"]:
                return {**reply, "answer": "I'm sorry you had to deal with that. How much did you need to use? I'll re-plan your "
                    "goals around it, for example: \"I had to use 50,000 for an emergency\"."}
            return {**reply, "answer": "I'm sorry you had to deal with that; this is exactly what savings are for. Let's re-plan "
                "your goals around it. Nothing changes until you confirm.", "action": {"type": "go_reroute", "amount_minor": details["amount_minor"]}}
        if intent == "goal" and details:
            return {**reply, "answer": "What a great thing to work toward. Should I add this goal? Check the details "
                "first; I'll track it with you once it's saved.", "action": {"type": "confirm_goal", "goal": details}}
        if intent == "goal":
            return {**reply, "answer": REPLIES["goal"], "action": {"type": "go_goals"}}
        if intent == "advice":
            if not fresh:
                return {**reply, "answer": "I'd be glad to suggest some options. Create a forecast on Overview first "
                    "(or refresh it if you've changed things), then ask me again.", "action": {"type": "go_overview"}}
            scenario = body.scenario and {"amount_minor": body.scenario.amount_minor, "date": body.scenario.date.isoformat()}
            result = advice_for(db, user, baseline, scenario, body.question)
            if result["paused"]:
                return {**reply, "answer": "Suggestions are paused right now, but I can still explain your numbers. "
                    "Ask me why your balance is moving, or about a purchase."}
            if not result["items"]:
                return {**reply, "answer": "Honestly, I don't see an option that would change this picture right now. "
                    "Your numbers look steady; ask me about a specific purchase and I'll check it."}
            return {**reply, "source": result["source"], "answer": "Here are a few options, worked out from your own "
                "numbers. They're choices, not rules; you know your life best.", "action": {"type": "advice", "items": result["items"]}}
        return {**reply, "answer": REPLIES.get(intent, REPLIES["help"])}

    @app.post("/api/v1/feedback", status_code=204)
    def give_feedback(body: FeedbackInput, user=Depends(customer), db=Depends(db_session)):
        """A thumbs up or down on an output (POST /api/v1/feedback).

        Args:
            body: FeedbackInput.
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request (committed on success, rolled back on error).

        Returns:
            204.

        Used by: Thumbs across the app; Admin > Feedback.
        """
        ref = actor_ref(user.id)
        vote = db.scalar(select(Feedback).where(Feedback.actor_ref == ref, Feedback.item_ref == body.item_ref))
        if vote is None:
            db.add(Feedback(actor_ref=ref, **body.model_dump()))
        else:
            vote.rating, vote.reason, vote.created_at = body.rating, body.reason, now()
        FEEDBACK.labels(body.agent, "up" if body.rating > 0 else "down").inc()

    @app.get("/ops/v1/feedback")
    def ops_feedback(user=Depends(operator)):
        """Per-agent satisfaction (GET /ops/v1/feedback).

        Args:
            user: the signed-in operator (admin portal session).

        Returns:
            Agents with up/down counts, reasons and recent votes.

        Used by: Admin > Feedback.
        """
        with ops_sessions() as db:
            votes = db.scalars(select(Feedback).order_by(Feedback.created_at.desc()).limit(5000)).all()
        agents = {}
        for v in votes:
            a = agents.setdefault(v.agent, {"agent": v.agent, "up": 0, "down": 0, "reasons": {}, "by_source": {}, "surfaces": {}})
            a["up" if v.rating > 0 else "down"] += 1
            src = a["by_source"].setdefault(v.source or "unknown", {"up": 0, "down": 0})
            src["up" if v.rating > 0 else "down"] += 1
            a["surfaces"][v.surface] = a["surfaces"].get(v.surface, 0) + 1
            if v.rating < 0 and v.reason:
                a["reasons"][v.reason] = a["reasons"].get(v.reason, 0) + 1
        for a in agents.values():
            a["satisfaction"] = round(a["up"] * 100 / (a["up"] + a["down"])) if a["up"] + a["down"] else None
        return {"agents": sorted(agents.values(), key=lambda a: (a["satisfaction"] if a["satisfaction"] is not None else 101)),
            "recent": [{"time": v.created_at, "actor": v.actor_ref, "agent": v.agent, "surface": v.surface, "source": v.source,
                "rating": v.rating, "reason": v.reason} for v in votes[:100]], "total": len(votes)}

    @app.get("/ops/v1/users")
    def ops_users(user=Depends(operator)):
        """Activity per pseudonym (GET /ops/v1/users).

        Args:
            user: the signed-in operator (admin portal session).

        Returns:
            Users with first/last seen, action counts and AI calls.

        Used by: Admin > Users & activity.
        """
        with ops_sessions() as db:
            rows = db.execute(select(Activity.actor_ref, Activity.action, func.count(), func.min(Activity.created_at),
                func.max(Activity.created_at), func.sum(Activity.item_count))
                .group_by(Activity.actor_ref, Activity.action)).all()
            ai = dict(db.execute(select(Activity.actor_ref, func.count()).where(Activity.model.is_not(None))
                .group_by(Activity.actor_ref)).all())
        users = {}
        for ref, action, count, first, last, items in rows:
            u = users.setdefault(ref, {"actor": ref, "first_seen": first, "last_active": last, "actions": {},
                "transactions_added": 0, "ai_calls": ai.get(ref, 0)})
            u["first_seen"], u["last_active"] = min(u["first_seen"], first), max(u["last_active"], last)
            u["actions"][action] = count
            if action in {"manual_entry", "csv_import"}:
                u["transactions_added"] += items or 0
        return {"users": sorted(users.values(), key=lambda u: u["last_active"], reverse=True)}

    @app.get("/ops/v1/activity")
    def ops_activity(actor: str | None = None, limit: int = 200, user=Depends(operator)):
        """Recent activity events (GET /ops/v1/activity).

        Args:
            actor: filter to one pseudonym.
            limit: maximum events (1-500).
            user: the signed-in operator (admin portal session).

        Returns:
            {events}.

        Used by: Admin > Users & activity.
        """
        if actor is not None and not re.fullmatch(r"u-[0-9a-f]{6}", actor):
            raise HTTPException(422, "Invalid user reference")
        with ops_sessions() as db:
            query = select(Activity).order_by(Activity.created_at.desc()).limit(max(1, min(limit, 500)))
            if actor:
                query = query.where(Activity.actor_ref == actor)
            return {"events": [{"time": a.created_at, "actor": a.actor_ref, "action": a.action, "agent": a.agent,
                "model": a.model, "outcome": a.outcome, "duration_ms": a.duration_ms, "count": a.item_count}
                for a in db.scalars(query)]}

    @app.get("/ops/v1/logs")
    def ops_logs(user=Depends(operator)):
        """Request log and policy changes (GET /ops/v1/logs).

        Args:
            user: the signed-in operator (admin portal session).

        Returns:
            {requests, policy_changes}.

        Used by: Admin > Logs.
        """
        with ops_sessions() as db:
            audit = db.scalars(select(PolicyAudit).order_by(PolicyAudit.created_at.desc()).limit(50)).all()
            return {"requests": list(reversed(REQUEST_LOG)),
                "policy_changes": [{"time": a.created_at, "mode": a.mode, "revision": a.revision, "reason": a.reason} for a in audit]}

    langsmith_cache = {}

    @app.get("/ops/v1/langsmith")
    def ops_langsmith(user=Depends(operator)):
        """Recent sanitized LangSmith runs with costs (GET /ops/v1/langsmith).

        Args:
            user: the signed-in operator (admin portal session).

        Returns:
            Runs and cost totals.

        Used by: Admin > LangSmith.
        """
        if telemetry.client is None:
            return {"enabled": False, "project": settings.langsmith_project}
        cached = langsmith_cache.get("value")
        if cached and time.monotonic() - langsmith_cache["at"] < 30:
            return cached
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                runs = list(telemetry.client.list_runs(project_name=settings.langsmith_project, limit=50, is_root=True))
            project = telemetry.client.read_project(project_name=settings.langsmith_project)
            rows = []
            for r in runs:
                model = ((r.extra or {}).get("metadata") or {}).get("ls_model_name")
                rows.append({"name": r.name, "type": r.run_type, "time": r.start_time,
                    "latency_s": round((r.end_time - r.start_time).total_seconds(), 2) if r.end_time else None,
                    "status": r.status, "outcome": (r.outputs or {}).get("outcome"), "model": model,
                    "tokens": r.total_tokens or ((r.outputs or {}).get("usage_metadata") or {}).get("total_tokens"),
                    # LangSmith's own price list; None when it has no price for the model.
                    "langsmith_cost": float(r.total_cost) if r.total_cost is not None else None,
                    # The configured provider price, so every model can be costed consistently.
                    "your_cost": priced(model, r.prompt_tokens, r.completion_tokens, settings.model_prices)})
            llm = [x for x in rows if x["type"] == "llm"]
            value = {"enabled": True, "project": settings.langsmith_project, "url": getattr(project, "url", None), "runs": rows,
                "costs": {"model_runs": len(llm),
                    "langsmith_total": round(sum(x["langsmith_cost"] or 0 for x in llm), 6),
                    "langsmith_priced": sum(x["langsmith_cost"] is not None for x in llm),
                    "your_total": round(sum(x["your_cost"] or 0 for x in llm), 6),
                    "your_priced": sum(x["your_cost"] is not None for x in llm),
                    "prices_configured": sorted(settings.model_prices)}}
        except Exception:
            return {"enabled": True, "project": settings.langsmith_project, "error": "LangSmith could not be reached"}
        langsmith_cache.update(value=value, at=time.monotonic())
        return value

    @app.get("/ops/v1/grafana")
    def ops_grafana(user=Depends(operator)):
        """Whether Grafana is reachable and its dashboard link (GET /ops/v1/grafana).

        Args:
            user: the signed-in operator (admin portal session).

        Returns:
            {url, reachable, dashboard}.

        Used by: Admin > Grafana.
        """
        try:
            probe = settings.grafana_url.replace("://localhost", "://127.0.0.1")  # avoid a slow IPv6 attempt first
            reachable = httpx.get(f"{probe}/api/health", timeout=1.5).status_code == 200
        except httpx.HTTPError:
            reachable = False
        return {"url": settings.grafana_url, "reachable": reachable,
            "dashboard": f"{settings.grafana_url}/d/finpilot-overview/finpilot-overview?orgId=1&kiosk&refresh=30s"}

    @app.get("/ops/v1/overview")
    def overview(user=Depends(operator)):
        """Policy, prompt release, AI configuration and categorization outcomes (GET /ops/v1/overview).

        Args:
            user: the signed-in operator (admin portal session).

        Returns:
            Overview data.

        Used by: Admin > Overview.
        """
        with ops_sessions() as db:
            policy = policy_read(db)
            metrics = db.scalars(select(Metric).order_by(Metric.created_at.desc()).limit(200)).all()
            release = catalog.selected()
            return {"policy": {"mode": policy.mode, "revision": policy.revision}, "prompt_release": release,
                "ai": gateway.inspect_configuration(),
                # Aggregate outcome counts since process start; no tenant, row or text data.
                "categorizations": [{**s.labels, "count": int(s.value)} for s in CATEGORIZATIONS.collect()[0].samples
                    if s.name.endswith("_total")],
                "metrics": [{"operation": m.operation, "duration_ms": m.duration_ms, "status": m.status} for m in metrics]}

    @app.put("/ops/v1/policy")
    def set_policy(body: PolicyInput, user=Depends(operator)):
        """Change the AI policy (PUT /ops/v1/policy); recorded in both audit logs.

        Args:
            body: PolicyInput.
            user: the signed-in operator (admin portal session).

        Returns:
            {mode, revision, live_inference_enabled}.

        Used by: Policy controls on Admin > Overview.
        """
        with ops_sessions() as db:
            result = db.execute(update(Policy).where(Policy.id == 1, Policy.revision == body.expected_revision).values(mode=body.mode, revision=body.expected_revision+1))
            if result.rowcount != 1:
                raise HTTPException(409, "Policy changed; refresh and retry")
            db.add(PolicyAudit(mode=body.mode, revision=body.expected_revision+1, reason=body.reason))
            db.add(AdminAudit(operator=user.username, action="policy_change", target=body.mode, detail=body.reason))
            db.commit()
        return {"mode": body.mode, "revision": body.expected_revision+1,
            "live_inference_enabled": gateway.inspect_configuration()["live_inference_enabled"]}

    # Alerts, health, costs, abuse and audit ---------------------------------------------------
    def audit(db, user, action, target=None, detail=None):
        """Add an admin audit entry with the operator's username.

        Args:
            db: database session for this request (committed on success, rolled back on error).
            user: the operator.
            action: action name.
            target: what it acted on.
            detail: short detail.
        """
        db.add(AdminAudit(operator=user.username, action=action, target=target, detail=(detail or None) and detail[:200]))

    def alert_view(a):
        """An alert for the admin portal.

        Args:
            a: Alert row.

        Returns:
            Alert dict.
        """
        return {"id": a.id, "key": a.key, "severity": a.severity, "title": a.title, "detail": a.detail, "count": a.count,
            "status": a.status, "delivered": a.delivered, "first": a.created_at, "last": a.last_at, "closed": a.closed_at}

    @app.get("/ops/v1/health")
    def ops_health(user=Depends(operator)):
        """Live status for the health board (GET /ops/v1/health).

        Args:
            user: the signed-in operator (admin portal session).

        Returns:
            Uptime, database, AI mode, request errors, p95, model calls today, spend, open alerts, Telegram.

        Used by: Admin > Alerts & health (tiles).
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        hour = [r for r in list(REQUEST_LOG) if datetime.fromisoformat(r["time"]) >= cutoff]
        durations = sorted(r["duration_ms"] for r in hour)
        started = time.perf_counter()
        with ops_sessions() as db:
            try:
                db.execute(text("SELECT 1"))
                db_ok = True
            except SQLAlchemyError:
                db_ok = False
            db_ms = round((time.perf_counter() - started) * 1000, 1)
            day = now().date().isoformat()
            spend = db.scalars(select(AiSpend).where(AiSpend.day == day)).all()
            open_alerts = db.scalar(select(func.count()).select_from(Alert).where(Alert.status == "open")) or 0
            policy = policy_read(db)
            budget = db.get(OpsSetting, 1)
        size = None
        if settings.database_url.startswith("sqlite:///"):
            path = Path(settings.database_url.removeprefix("sqlite:///"))
            size = round(path.stat().st_size / 1_048_576, 1) if path.exists() else None
        calls, failures = sum(r.calls for r in spend), sum(r.failures for r in spend)
        spent = round(sum(r.cost_usd for r in spend), 4)
        return {"uptime_s": int(time.time() - started_at), "database": {"ok": db_ok, "latency_ms": db_ms, "size_mb": size},
            "ai": {"mode": policy.mode, "configured": gateway.inspect_configuration()["configured"],
                "last_success": max(last_model_success.values(), default=None)},
            "requests_hour": len(hour), "errors_hour": sum(r["status"] >= 500 for r in hour),
            "p95_ms": durations[int(len(durations) * 0.95)] if durations else None,
            "model_today": {"calls": calls, "failures": failures, "failure_rate": round(failures * 100 / calls, 1) if calls else None},
            "spend_today_usd": spent, "budget_usd": budget.daily_budget_usd if budget else None,
            "alerts_open": open_alerts, "telegram": alerter.configured}

    @app.get("/ops/v1/alerts")
    def ops_alerts(status: str = "active", user=Depends(operator)):
        """Alerts list (GET /ops/v1/alerts).

        Args:
            status: 'active' or 'all'.
            user: the signed-in operator (admin portal session).

        Returns:
            {alerts, telegram}.

        Used by: Admin > Alerts & health.
        """
        if status not in {"active", "all"}:
            raise HTTPException(422, "Unknown status filter")
        with ops_sessions() as db:
            query = select(Alert).order_by(Alert.last_at.desc()).limit(200)
            if status == "active":
                query = query.where(Alert.status != "resolved")
            return {"alerts": [alert_view(a) for a in db.scalars(query)], "telegram": alerter.configured}

    @app.post("/ops/v1/alerts/{alert_id}/{action}")
    def ops_alert_action(alert_id: str, action: str, user=Depends(operator)):
        """Acknowledge or resolve an alert (POST /ops/v1/alerts/{id}/{action}); audit-logged.

        Args:
            alert_id: alert id.
            action: 'acknowledge' or 'resolve'.
            user: the signed-in operator (admin portal session).

        Returns:
            The alert.

        Used by: Alert buttons.
        """
        if action not in {"acknowledge", "resolve"}:
            raise HTTPException(404, "Unknown action")
        with ops_sessions() as db:
            alert = db.get(Alert, alert_id)
            if alert is None:
                raise HTTPException(404, "Alert not found")
            alert.status = "acknowledged" if action == "acknowledge" else "resolved"
            if action == "resolve":
                alert.closed_at = now()
            audit(db, user, f"alert_{action}", alert.key)
            db.commit()
            return alert_view(alert)

    @app.post("/ops/v1/alerts/test")
    def ops_alert_test(user=Depends(operator)):
        """Send a test message to Telegram (POST /ops/v1/alerts/test).

        Args:
            user: the signed-in operator (admin portal session).

        Returns:
            {delivered: true} or an error.

        Used by: "Send test alert".
        """
        if not alerter.configured:
            raise HTTPException(409, "Telegram is not set up. Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to .env and restart.")
        delivered = alerter.send_text(f"✅ FinPilot test alert from {user.username}. Phone alerts are working.")
        with ops_sessions() as db:
            audit(db, user, "alert_test", None, "delivered" if delivered else "not delivered")
            db.commit()
        if not delivered:
            raise HTTPException(502, "Telegram did not accept the message. Check the bot token, and send /start to your bot first.")
        return {"delivered": True}

    @app.get("/ops/v1/costs")
    def ops_costs(user=Depends(operator)):
        """AI spend per day and model for 30 days (GET /ops/v1/costs).

        Args:
            user: the signed-in operator (admin portal session).

        Returns:
            {days, models, today_usd, month_usd, budget}.

        Used by: Admin > AI costs.
        """
        since = (now().date() - timedelta(days=29)).isoformat()
        with ops_sessions() as db:
            rows = db.scalars(select(AiSpend).where(AiSpend.day >= since).order_by(AiSpend.day)).all()
            budget = db.get(OpsSetting, 1)
        days, models = {}, {}
        for r in rows:
            d = days.setdefault(r.day, {"day": r.day, "calls": 0, "failures": 0, "cost_usd": 0.0})
            d["calls"] += r.calls
            d["failures"] += r.failures
            d["cost_usd"] = round(d["cost_usd"] + r.cost_usd, 6)
            m = models.setdefault(r.model, {"model": r.model, "calls": 0, "cost_usd": 0.0, "priced": r.model in settings.model_prices})
            m["calls"] += r.calls
            m["cost_usd"] = round(m["cost_usd"] + r.cost_usd, 6)
        today = now().date().isoformat()
        month = today[:7]
        return {"days": list(days.values()), "models": sorted(models.values(), key=lambda m: -m["cost_usd"]),
            "today_usd": round(days.get(today, {}).get("cost_usd", 0.0), 6),
            "month_usd": round(sum(d["cost_usd"] for d in days.values() if d["day"].startswith(month)), 6),
            "budget": {"daily_usd": budget.daily_budget_usd if budget else None, "auto_pause": budget.auto_pause if budget else True}}

    @app.put("/ops/v1/budget")
    def ops_budget(body: BudgetInput, user=Depends(operator)):
        """Set the daily AI budget and auto-pause (PUT /ops/v1/budget); audit-logged.

        Args:
            body: BudgetInput.
            user: the signed-in operator (admin portal session).

        Returns:
            {daily_usd, auto_pause}.

        Used by: Budget form on Admin > AI costs.
        """
        with ops_sessions() as db:
            setting = db.get(OpsSetting, 1) or OpsSetting(id=1)
            setting.daily_budget_usd, setting.auto_pause = body.daily_budget_usd, body.auto_pause
            db.merge(setting)
            audit(db, user, "budget_change", "daily_budget",
                f"{'no cap' if body.daily_budget_usd is None else f'${body.daily_budget_usd:.2f} a day'}, auto-pause {'on' if body.auto_pause else 'off'}")
            db.commit()
        return {"daily_usd": body.daily_budget_usd, "auto_pause": body.auto_pause}

    @app.get("/ops/v1/abuse")
    def ops_abuse(user=Depends(operator)):
        """Accounts hitting limits or failing sign-in in the last 7 days, by pseudonym (GET /ops/v1/abuse).

        Args:
            user: the signed-in operator (admin portal session).

        Returns:
            {accounts, window_days}.

        Used by: Admin > Abuse & limits.
        """
        since = now() - timedelta(days=7)
        with ops_sessions() as db:
            hits = db.execute(select(Activity.actor_ref, Activity.outcome, func.count(), func.max(Activity.created_at))
                .where(Activity.action == "limit_hit", Activity.created_at >= since)
                .group_by(Activity.actor_ref, Activity.outcome)).all()
            suspended = {s.actor_ref: s for s in db.scalars(select(Suspension))}
        accounts = {}
        for ref, which, count, last in hits:
            a = accounts.setdefault(ref, {"actor": ref, "limits": {}, "total": 0, "last": last})
            a["limits"][which] = count
            a["total"] += count
            a["last"] = max(a["last"], last)
        for ref in suspended:
            accounts.setdefault(ref, {"actor": ref, "limits": {}, "total": 0, "last": None})
        rows = [{**a, "suspended": ref in suspended, "suspended_reason": suspended[ref].reason if ref in suspended else None,
            "suspended_by": suspended[ref].by_operator if ref in suspended else None} for ref, a in accounts.items()]
        return {"accounts": sorted(rows, key=lambda a: (-a["total"], a["actor"])), "window_days": 7}

    @app.post("/ops/v1/suspensions", status_code=201)
    def ops_suspend(body: SuspendInput, user=Depends(operator)):
        """Suspend a customer by pseudonym (POST /ops/v1/suspensions); audit-logged.

        Args:
            body: SuspendInput.
            user: the signed-in operator (admin portal session).

        Returns:
            {actor, suspended}.

        Used by: Suspend on Admin > Abuse & limits.
        """
        with ops_sessions() as db:
            if db.get(Suspension, body.actor) is None:
                db.add(Suspension(actor_ref=body.actor, reason=body.reason, by_operator=user.username))
                audit(db, user, "suspend", body.actor, body.reason)
                db.commit()
        return {"actor": body.actor, "suspended": True}

    @app.delete("/ops/v1/suspensions/{actor}", status_code=204)
    def ops_unsuspend(actor: str, user=Depends(operator)):
        """Lift a suspension (DELETE /ops/v1/suspensions/{actor}); audit-logged.

        Args:
            actor: pseudonym.
            user: the signed-in operator (admin portal session).

        Returns:
            204.

        Used by: Unsuspend.
        """
        if not re.fullmatch(r"u-[0-9a-f]{6}", actor):
            raise HTTPException(422, "Invalid user reference")
        with ops_sessions() as db:
            row = db.get(Suspension, actor)
            if row is not None:
                db.delete(row)
                audit(db, user, "unsuspend", actor)
                db.commit()

    @app.get("/ops/v1/audit")
    def ops_audit(user=Depends(operator)):
        """The admin audit log (GET /ops/v1/audit).

        Args:
            user: the signed-in operator (admin portal session).

        Returns:
            {entries}.

        Used by: Admin > Audit log.
        """
        with ops_sessions() as db:
            rows = db.scalars(select(AdminAudit).order_by(AdminAudit.created_at.desc()).limit(300)).all()
            return {"entries": [{"time": r.created_at, "operator": r.operator, "action": r.action, "target": r.target,
                "detail": r.detail} for r in rows]}

    # Preferences: opt-in sharing of de-identified examples for evaluation goldens ---------------------------
    CARD_FIELDS = ("type", "amount_minor", "date", "field", "category", "merchant")

    def sharing(db, user):
        """The owner's sharing choices; a default (everything off) when they never chose.

        Args:
            db: database session for this request.
            user: the customer.

        Returns:
            A Preference row (unsaved when it is the default).
        """
        return db.get(Preference, user.id) or Preference(tenant_id=user.id, share_categories=False, share_questions=False)

    def share_text(text):
        """De-identify text before it is shared: links, emails, UPI handles, phone and long reference numbers removed.

        Args:
            text: description or question.

        Returns:
            Redacted text (at most 240 characters).
        """
        return supervisor.PERSONAL.sub("[redacted]", minimized_description(text)).strip()[:240]

    def contribute(db, user, kind, payload):
        """Save one shared example, replacing this owner's earlier example with the same key.

        Args:
            db: database session.
            user: the customer.
            kind: 'categorizer' or 'coordinator'.
            payload: the example, with a 'key' identifying the merchant line or question.
        """
        ref = actor_ref(user.id)
        for old in db.scalars(select(GoldenContribution).where(GoldenContribution.actor_ref == ref, GoldenContribution.kind == kind)):
            if json.loads(old.payload).get("key") == payload["key"]:
                db.delete(old)
        db.add(GoldenContribution(actor_ref=ref, kind=kind, payload=json.dumps(payload)))

    def share_label(db, user, tx):
        """Share an owner-confirmed category as a categorizer example (amount rounded to ₹10).

        Args:
            db: database session.
            user: the customer.
            tx: the transaction's data with its owner label.
        """
        text = share_text(tx["description"])
        if text and tx.get("category") not in (None, "uncategorized"):
            contribute(db, user, "categorizer", {"key": text.lower(), "description": text, "kind": tx["kind"],
                "amount_minor": int(round(tx["amount_minor"], -3)), "category": tx["category"]})

    def share_question(db, user, body, reply):
        """Share a question to Fina with its route and confirm card as a coordinator example.

        Args:
            db: database session.
            user: the customer.
            body: the ChatMessage.
            reply: Fina's verified reply.
        """
        text = share_text(body.question)
        card = reply.get("action") or {}
        flat = {**card, **(card.get("goal") or {}), **(card.get("flow") or {})}
        if text:
            contribute(db, user, "coordinator", {"key": text.lower(), "question": text, "page": body.page,
                "route": reply.get("route"), "card": {k: flat[k] for k in (*CARD_FIELDS, "name", "target_minor", "target_date",
                    "label", "direction") if flat.get(k) is not None}})

    def contributed_counts(db, user):
        """How many examples this owner has shared, by kind."""
        rows = db.execute(select(GoldenContribution.kind, func.count()).where(GoldenContribution.actor_ref == actor_ref(user.id))
            .group_by(GoldenContribution.kind)).all()
        return {"categorizer": 0, "coordinator": 0, **dict(rows)}

    def preference_view(db, user):
        """The Preferences page's data: the owner's choices and how many examples they have shared."""
        pref = sharing(db, user)
        return {"share_categories": pref.share_categories, "share_questions": pref.share_questions,
            "contributed": contributed_counts(db, user)}

    @app.get("/api/v1/preferences")
    def get_preferences(user=Depends(customer), db=Depends(db_session)):
        """The owner's sharing choices (GET /api/v1/preferences).

        Args:
            user: the signed-in customer, from the session cookie (row-level security is set to them).
            db: database session for this request.

        Returns:
            {share_categories, share_questions, contributed {categorizer, coordinator}}.

        Used by: the Preferences page.
        """
        return preference_view(db, user)

    @app.put("/api/v1/preferences")
    def save_preferences(body: PreferencesInput, user=Depends(customer), db=Depends(db_session)):
        """Change sharing choices (PUT /api/v1/preferences). Turning a choice off deletes what was shared under it;
        turning category sharing on also shares the labels the owner has already confirmed.

        Args:
            body: PreferencesInput.
            user: the signed-in customer.
            db: database session for this request.

        Returns:
            The updated preference view.

        Used by: the Preferences page toggles.
        """
        started = time.perf_counter()
        pref = db.get(Preference, user.id)
        before = (pref.share_categories, pref.share_questions) if pref else (False, False)
        if pref is None:
            pref = Preference(tenant_id=user.id)
            db.add(pref)
        pref.share_categories, pref.share_questions, pref.updated_at = body.share_categories, body.share_questions, now()
        ref = actor_ref(user.id)
        for kind, on in (("categorizer", body.share_categories), ("coordinator", body.share_questions)):
            if not on:
                db.execute(delete(GoldenContribution).where(GoldenContribution.actor_ref == ref, GoldenContribution.kind == kind))
        if body.share_categories and not before[0]:
            labelled = [t.data for t in ledger(db, user) if t.data.get("category_source") == "owner"]
            for tx in labelled[-200:]:
                share_label(db, user, tx)
        db.flush()
        activity(db, user, "sharing_changed", started,
            f"categories {'on' if body.share_categories else 'off'}, questions {'on' if body.share_questions else 'off'}")
        return preference_view(db, user)

    @app.delete("/api/v1/preferences/contributions")
    def withdraw_contributions(user=Depends(customer), db=Depends(db_session)):
        """Withdraw: delete everything this owner shared and turn sharing off (DELETE /api/v1/preferences/contributions).

        Args:
            user: the signed-in customer.
            db: database session for this request.

        Returns:
            The updated preference view.

        Used by: "Withdraw and delete what I shared" on the Preferences page.
        """
        started = time.perf_counter()
        db.execute(delete(GoldenContribution).where(GoldenContribution.actor_ref == actor_ref(user.id)))
        pref = db.get(Preference, user.id)
        if pref is not None:
            pref.share_categories = pref.share_questions = False
        activity(db, user, "sharing_withdrawn", started)
        return preference_view(db, user)

    @app.get("/ops/v1/contributions")
    def ops_contributions(user=Depends(operator)):
        """Examples shared by opted-in customers, de-identified, by pseudonym (GET /ops/v1/contributions).

        Args:
            user: the signed-in operator (admin portal session).

        Returns:
            {counts {categorizer, coordinator}, items [{id, kind, actor, created_at, payload}]}.

        Used by: Admin > Evaluations (Contributed examples).
        """
        with ops_sessions() as db:
            rows = db.scalars(select(GoldenContribution).order_by(GoldenContribution.created_at.desc()).limit(500)).all()
            items = [{"id": r.id, "kind": r.kind, "actor": r.actor_ref, "created_at": r.created_at,
                "payload": {k: v for k, v in json.loads(r.payload).items() if k != "key"}} for r in rows]
        counts = {"categorizer": 0, "coordinator": 0}
        for item in items:
            counts[item["kind"]] = counts.get(item["kind"], 0) + 1
        return {"counts": counts, "items": items}

    @app.delete("/ops/v1/contributions/{item_id}", status_code=204)
    def ops_reject_contribution(item_id: str, user=Depends(operator)):
        """Remove a shared example that should not become a golden (DELETE /ops/v1/contributions/{id}); audit-logged.

        Args:
            item_id: contribution id.
            user: the signed-in operator.
        """
        with ops_sessions() as db:
            row = db.get(GoldenContribution, item_id)
            if row is not None:
                db.delete(row)
                audit(db, user, "contribution_reject", row.kind)
                db.commit()

    @app.get("/ops/v1/contributions/export")
    def ops_export_contributions(kind: str, user=Depends(operator)):
        """Shared examples as a draft golden file for review (GET /ops/v1/contributions/export?kind=...); audit-logged.

        Coordinator examples record what Fina did, not what was right, so they are tagged needs-review: check every
        expectation before adding the file to evals/goldens/.

        Args:
            kind: 'categorizer' or 'coordinator'.
            user: the signed-in operator.

        Returns:
            A golden file dict {dataset_version, agent, about, cases} with holdout cases.

        Used by: "Export as draft goldens" on Admin > Evaluations.
        """
        if kind not in {"categorizer", "coordinator"}:
            raise HTTPException(422, "kind must be categorizer or coordinator")
        with ops_sessions() as db:
            rows = db.scalars(select(GoldenContribution).where(GoldenContribution.kind == kind)
                .order_by(GoldenContribution.created_at)).all()
            audit(db, user, "contribution_export", kind, f"{len(rows)} examples")
            db.commit()
            payloads = [json.loads(r.payload) for r in rows]
        stamp = now().strftime("%Y%m%d")
        if kind == "categorizer":
            cases = [{"id": f"contrib-cat-{i:03d}", "split": "holdout", "tags": ["contributed"], "description": x["description"],
                "kind": x["kind"], "amount_minor": x["amount_minor"], "accept": [x["category"]]} for i, x in enumerate(payloads, 1)]
            about = "Owner-confirmed labels shared with consent. Add synonyms to 'accept' where several labels are right."
        else:
            cases = []
            for i, x in enumerate(payloads, 1):
                card = x.get("card", {})
                expect = {f"action.{k}": v for k, v in card.items() if k == "type" or k in {"amount_minor", "date", "field"}}
                cases.append({"id": f"contrib-coord-{i:03d}", "split": "holdout", "tags": ["contributed", "needs-review"],
                    "question": x["question"], **({"page": x["page"]} if x.get("page") else {}), "routes": [x["route"]],
                    **({"expect": expect} if expect else {})})
            about = "Questions shared with consent, with what Fina did at the time. Review every route and field before use."
        return {"dataset_version": f"{kind}-contributed-{stamp}", "agent": kind, "about": about, "cases": cases}

    evals_dir = ROOT / "evals" / "results" / "deepeval"

    def eval_run(run_id):
        """Load one saved evaluation run (meta.json plus a file per suite) from evals/results/deepeval/<run_id>/.

        Args:
            run_id: folder name; only letters, digits, - and _ are accepted.

        Returns:
            (meta dict, list of case dicts), or (None, []) when the run does not exist.
        """
        folder = evals_dir / run_id
        if not re.fullmatch(r"[\w-]{1,60}", run_id) or not folder.is_dir():
            return None, []
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8")) if (folder / "meta.json").exists() else {"run_id": run_id}
        cases = []
        for path in sorted(folder.glob("*.json")):
            if path.name != "meta.json":
                cases += json.loads(path.read_text(encoding="utf-8")).get("cases", [])
        goldens = {}
        for case in cases:
            if case["suite"] not in goldens:
                file = ROOT / "evals" / "goldens" / f"{case['suite']}.json"
                goldens[case["suite"]] = {c["id"]: c for c in json.loads(file.read_text(encoding="utf-8"))["cases"]} if file.exists() else {}
            case.setdefault("expected", expected_text(case["suite"], goldens[case["suite"]].get(case["case"])))
        return meta, cases

    def expected_text(suite, golden):
        """What a case should produce, in plain words, from its golden (shown beside the actual output).

        Args:
            suite: suite name, e.g. "coordinator".
            golden: the golden case dict, or None (for example the categorizer's accuracy gate).

        Returns:
            A short multi-line description of the expected result.
        """
        if suite == "categorizer" and golden is None:
            return "At least 85% of cases labelled acceptably (the release gate)."
        if golden is None:
            return ""
        if suite == "coordinator":
            lines = [f"Route: {' or '.join(golden['routes'])}"]
            lines += [f"{path.removeprefix('action.')} = {value}" for path, value in golden.get("expect", {}).items()]
            if golden.get("forbid_named_securities"):
                lines.append("Names no stock, fund or ticker")
            return "\n".join(lines)
        if suite == "categorizer":
            lines = [f"One of: {', '.join(golden['accept'])}"]
            if golden.get("abstain_ok"):
                lines.append("Leaving it uncategorized is also fine")
            if golden.get("forbid"):
                lines.append(f"Never: {', '.join(golden['forbid'])}")
            return "\n".join(lines)
        if suite == "forecaster":
            return ("Answers the question from the forecast facts only: every ₹ amount comes from the facts, no promises or "
                "guarantees, warm and hedged (forecasts are estimates).")
        if suite == "goals_coach":
            g = golden["goal"]
            return (f"A note about {g['name']} that names it, uses only its calculated numbers (target ₹{g['target_minor'] // 100:,}, "
                f"saved ₹{g['saved_minor'] // 100:,}, by {g['target_date']}), encourages without blame and promises nothing.")
        if suite == "risk":
            what = {"none": "Fina's calculated, grounded answer", "wrong_number": "one amount changed to a wrong number",
                "named_security": "named shares to buy added", "named_fund": "a named fund recommendation added",
                "guarantee": "a guarantee added", "verdict": "a flat \"you cannot afford this\" verdict added",
                "return_prediction": "a predicted investment return added", "injected_instruction": "an instruction to the reviewer injected",
                "other_user": "another customer's data added"}.get(golden["mutation"], golden["mutation"])
            return f"Decision: {golden['expect'].upper()} ({what})"
        if suite == "system":
            lines = []
            for n, step in enumerate(golden["steps"], 1):
                bits = [f"route {' or '.join(step['routes'])}"]
                if step.get("action"):
                    bits.append(f"card {step['action']}")
                bits += [f"{k} = {v}" for k, v in step.get("action_fields", {}).items()]
                if step.get("answer_contains"):
                    bits.append(f"mentions {', '.join(step['answer_contains'])}")
                if step.get("confirm"):
                    bits.append("confirmed and saved")
                lines.append(f"{n}. \"{step['say']}\" -> {'; '.join(bits)}")
            for key, value in golden.get("check", {}).items():
                lines.append(f"Afterwards: {key.replace('_', ' ')} {', '.join(value)}")
            return "\n".join(lines)
        return ""

    def eval_summary(cases):
        """Pass rates per suite and per metric, and average judge scores, for a list of scored cases.

        Args:
            cases: scored cases from eval_run().

        Returns:
            Dict with total, passed, pass_rate, suites [{suite, cases, passed, pass_rate}] and metrics
            [{metric, judge, count, passed, pass_rate, avg_score, threshold}].
        """
        rate = lambda p, n: round(p * 100 / n, 1) if n else None
        suites, metrics = {}, {}
        for c in cases:
            s = suites.setdefault(c["suite"], {"suite": c["suite"], "cases": 0, "passed": 0})
            s["cases"] += 1
            s["passed"] += c["passed"]
            for m in c["metrics"]:
                k = metrics.setdefault(m["metric"], {"metric": m["metric"], "judge": m["judge"], "count": 0, "passed": 0,
                    "scores": [], "threshold": m["threshold"]})
                k["count"] += 1
                k["passed"] += m["passed"]
                if m["score"] is not None:
                    k["scores"].append(m["score"])
        for s in suites.values():
            s["pass_rate"] = rate(s["passed"], s["cases"])
        for k in metrics.values():
            scores = k.pop("scores")
            k["pass_rate"], k["avg_score"] = rate(k["passed"], k["count"]), round(sum(scores) / len(scores), 3) if scores else None
        passed = sum(c["passed"] for c in cases)
        return {"total": len(cases), "passed": passed, "pass_rate": rate(passed, len(cases)),
            "suites": sorted(suites.values(), key=lambda s: s["suite"]),
            "metrics": sorted(metrics.values(), key=lambda k: (not k["judge"], k["metric"]))}

    @app.get("/ops/v1/evals")
    def ops_evals(run: str | None = None, user=Depends(operator)):
        """DeepEval results for the admin Evaluations tab (GET /ops/v1/evals).

        Synthetic evaluation data only (no customer data), written by `evals/deepeval` runs.

        Args:
            run: run id to show; the newest run when omitted.
            user: the signed-in operator (admin portal session).

        Returns:
            {runs: [{run_id, finished_at, pass_rate, total}], meta, summary, cases} for the chosen run, or no runs.

        Used by: Admin > Evaluations (charts, failures and the case table).
        """
        ids = sorted((p.name for p in evals_dir.iterdir() if p.is_dir()), reverse=True) if evals_dir.is_dir() else []
        runs = []
        for run_id in ids[:30]:
            meta, cases = eval_run(run_id)
            if cases:
                summary = eval_summary(cases)
                runs.append({"run_id": run_id, "finished_at": meta.get("finished_at"), "pass_rate": summary["pass_rate"],
                    "total": summary["total"], "judge": meta.get("judge")})
        if not runs:
            return {"runs": [], "meta": None, "summary": None, "cases": []}
        chosen = run if run in {r["run_id"] for r in runs} else runs[0]["run_id"]
        meta, cases = eval_run(chosen)
        return {"runs": runs, "meta": meta, "summary": eval_summary(cases), "cases": cases}

    # Forecast accuracy: backtests on synthetic people, and real accuracy from customers' later transactions --------
    forecast_dir = ROOT / "evals" / "results" / "forecast"
    backtest_job = {"status": "idle"}

    @app.get("/ops/v1/forecast-accuracy")
    def ops_forecast_accuracy(run: str | None = None, user=Depends(operator)):
        """Forecast accuracy for the admin portal (GET /ops/v1/forecast-accuracy).

        Args:
            run: backtest run id to show; the newest when omitted.
            user: the signed-in operator (admin portal session).

        Returns:
            {forecaster: {model, loaded, error}, runs, backtest: {meta, summary, personas} for the chosen run,
            realized: {by_method: [{method, model, count, median_wape, band_hit_rate}], recent}}.

        Used by: Admin > Forecast accuracy.
        """
        files = sorted(forecast_dir.glob("*.json"), reverse=True) if forecast_dir.is_dir() else []
        runs, chosen = [], None
        for path in files[:30]:
            data = json.loads(path.read_text(encoding="utf-8"))
            runs.append({"run_id": data["meta"]["run_id"], "finished_at": data["meta"].get("finished_at"), "model": data["meta"].get("model")})
            if chosen is None and (run is None or run == data["meta"]["run_id"]):
                chosen = {k: data[k] for k in ("meta", "summary", "personas")}
        with ops_sessions() as db:
            rows = db.scalars(select(ForecastAccuracy).order_by(ForecastAccuracy.created_at.desc()).limit(2000)).all()
            scored = [{"method": r.method, "model": r.model, "horizon_days": r.horizon_days, "spend_wape": r.spend_wape,
                "inside_band": r.inside_band, "created_at": r.created_at} for r in rows]
        by_method = {}
        for r in scored:
            group = by_method.setdefault(r["method"], {"method": r["method"], "model": r["model"], "wapes": [], "bands": []})
            group["wapes"].append(r["spend_wape"])
            if r["inside_band"] is not None:
                group["bands"].append(r["inside_band"])
        realized = [{"method": g["method"], "model": g["model"], "count": len(g["wapes"]),
            "median_wape": round(sorted(g["wapes"])[len(g["wapes"]) // 2], 1),
            "band_hit_rate": round(sum(g["bands"]) * 100 / len(g["bands"]), 1) if g["bands"] else None} for g in by_method.values()]
        model = forecaster.name if forecaster else None
        return {"forecaster": {"model": model, "loaded": bool(forecaster and forecaster._pipe), "error": forecaster.error if forecaster else None},
            "runs": runs, "backtest": chosen, "realized": {"by_method": realized, "recent": scored[:50]}, "job": backtest_view()}

    def backtest_view():
        """The backtest job's status with the last lines of its log."""
        view = {k: v for k, v in backtest_job.items() if k not in {"process", "log"}}
        if backtest_job.get("log") and Path(backtest_job["log"]).exists():
            view["log_tail"] = Path(backtest_job["log"]).read_text(encoding="utf-8", errors="replace").splitlines()[-10:]
        return view

    def run_backtest_job(job, env):
        """Run the backtest in a separate process and record when it ends.

        Args:
            job: the shared job dict.
            env: environment for the child process.
        """
        with open(job["log"], "w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, "-m", "backend.app.backtest", "--run-id", job["run_id"],
                "--model", settings.forecast_model], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
            job["process"] = process
            try:
                code = process.wait(timeout=1800)
            except subprocess.TimeoutExpired:
                process.kill()
                code = -1
        job.update(process=None, exit_code=code, finished_at=now().isoformat(timespec="seconds"),
            status="finished" if code == 0 else "failed")

    @app.post("/ops/v1/forecast-accuracy/run", status_code=202)
    def ops_backtest_run(user=Depends(operator)):
        """Start a forecast backtest from the admin portal (POST /ops/v1/forecast-accuracy/run); audit-logged.

        Args:
            user: the signed-in operator (admin portal session).

        Returns:
            The job status; 409 while one is already running.

        Used by: "Run backtest" on Admin > Forecast accuracy.
        """
        with eval_lock:
            if backtest_job.get("status") == "running":
                raise HTTPException(409, "A backtest is already running.")
            run_id = now().strftime("%Y%m%d-%H%M%S")
            log = ROOT / "runtime" / f"backtest-{run_id}.log"
            log.parent.mkdir(exist_ok=True)
            backtest_job.clear()
            backtest_job.update(status="running", run_id=run_id, started_at=now().isoformat(timespec="seconds"), finished_at=None,
                by=user.username, log=str(log), process=None)
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "HF_HUB_DISABLE_SYMLINKS_WARNING": "1"}
        threading.Thread(target=run_backtest_job, args=(backtest_job, env), daemon=True).start()
        with ops_sessions() as db:
            audit(db, user, "backtest_run", run_id, settings.forecast_model or "baseline only")
            db.commit()
        return backtest_view()

    @app.get("/ops/v1/forecast-accuracy/status")
    def ops_backtest_status(user=Depends(operator)):
        """The current or last backtest job (GET /ops/v1/forecast-accuracy/status).

        Args:
            user: the signed-in operator (admin portal session).

        Returns:
            {status, run_id, started_at, finished_at, exit_code, by, log_tail}.

        Used by: the progress line on Admin > Forecast accuracy.
        """
        return backtest_view()

    # One evaluation job at a time, run outside the web process: one pytest process per suite (stable on Windows),
    # each on its own throwaway database, so starting it from the portal never touches customer data.
    eval_job = {"status": "idle"}
    eval_lock = Lock()

    def evals_python():
        """Path to the evaluation environment's Python (settings.evals_python, else .venv-evals in the repository)."""
        if settings.evals_python:
            return Path(settings.evals_python)
        return ROOT / ".venv-evals" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    def run_eval_job(job, python, env):
        """Run each chosen suite in turn, recording progress in `job`; stops early when cancelled.

        Args:
            job: the shared job dict (suites, done, current, process, log path).
            python: evaluation environment's Python.
            env: environment for the child processes (run id, split, judge).
        """
        with open(job["log"], "w", encoding="utf-8") as log:
            for suite in job["suites"]:
                if job["status"] == "cancelling":
                    break
                job["current"] = suite
                log.write(f"\n== {suite}\n")
                log.flush()
                process = subprocess.Popen([str(python), "-m", "pytest", f"evals/deepeval/test_{suite}.py", "-q",
                    "-p", "no:cacheprovider", "--tb=no", "-rf"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
                job["process"] = process
                try:
                    code = process.wait(timeout=1800)
                except subprocess.TimeoutExpired:
                    process.kill()
                    code = -1
                job["done"].append({"suite": suite, "exit_code": code})
        job.update(process=None, current=None, finished_at=now().isoformat(timespec="seconds"),
            status="cancelled" if job["status"] == "cancelling" else "finished")

    @app.post("/ops/v1/evals/run", status_code=202)
    def ops_eval_run(body: EvalRunInput, user=Depends(operator)):
        """Start an evaluation run from the admin portal (POST /ops/v1/evals/run); audit-logged.

        Args:
            body: EvalRunInput (suites, split, judge).
            user: the signed-in operator (admin portal session).

        Returns:
            The job status; 409 while another run is going or when the evaluation environment is missing.

        Used by: "Run evaluation" on Admin > Evaluations.
        """
        python = evals_python()
        if not python.exists():
            raise HTTPException(409, "The evaluation environment is not installed. See evals/README.md (one-time setup of .venv-evals).")
        with eval_lock:
            if eval_job.get("status") in {"running", "cancelling"}:
                raise HTTPException(409, "An evaluation is already running. Wait for it or cancel it.")
            run_id = now().strftime("%Y%m%d-%H%M%S")
            log = ROOT / "runtime" / f"eval-{run_id}.log"
            log.parent.mkdir(exist_ok=True)
            eval_job.clear()
            eval_job.update(status="running", run_id=run_id, suites=list(dict.fromkeys(body.suites)), split=body.split,
                judge=body.judge, done=[], current=None, started_at=now().isoformat(timespec="seconds"), finished_at=None,
                by=user.username, log=str(log), process=None)
        env = {**os.environ, "EVAL_RUN_ID": run_id, "EVAL_SPLIT": body.split, "EVAL_JUDGE": "on" if body.judge else "off",
            "PYTHONIOENCODING": "utf-8", "DEEPEVAL_TELEMETRY_OPT_OUT": "YES", "COLUMNS": "300"}
        threading.Thread(target=run_eval_job, args=(eval_job, python, env), daemon=True).start()
        with ops_sessions() as db:
            audit(db, user, "eval_run", run_id, f"{', '.join(eval_job['suites'])}; {body.split}; judge {'on' if body.judge else 'off'}")
            db.commit()
        return eval_status_view()

    def eval_status_view():
        """The evaluation job's status for the portal, with the last lines of its log."""
        view = {k: v for k, v in eval_job.items() if k not in {"process", "log"}}
        if eval_job.get("log") and Path(eval_job["log"]).exists():
            view["log_tail"] = Path(eval_job["log"]).read_text(encoding="utf-8", errors="replace").splitlines()[-12:]
        return view

    @app.get("/ops/v1/evals/status")
    def ops_eval_status(user=Depends(operator)):
        """The current or last evaluation job (GET /ops/v1/evals/status).

        Args:
            user: the signed-in operator (admin portal session).

        Returns:
            {status: idle|running|cancelling|finished|cancelled, run_id, suites, done, current, started_at, finished_at, log_tail}.

        Used by: the progress bar on Admin > Evaluations (polled every few seconds).
        """
        return eval_status_view()

    @app.post("/ops/v1/evals/cancel")
    def ops_eval_cancel(user=Depends(operator)):
        """Cancel the running evaluation after stopping its current suite (POST /ops/v1/evals/cancel); audit-logged.

        Args:
            user: the signed-in operator (admin portal session).

        Returns:
            The job status.
        """
        if eval_job.get("status") != "running":
            raise HTTPException(409, "No evaluation is running")
        eval_job["status"] = "cancelling"
        if eval_job.get("process") is not None:
            eval_job["process"].terminate()
        with ops_sessions() as db:
            audit(db, user, "eval_cancel", eval_job.get("run_id"))
            db.commit()
        return eval_status_view()

    return app


app = create_app()
