import json
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, event, text
from sqlalchemy import inspect as inspect_schema
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, object_session, sessionmaker
from sqlalchemy.pool import StaticPool
from . import crypto

PENDING = "pending"  # set when a plain value changes; replaced by ciphertext just before the row is written


def uid():
    """A new random UUID string, used as a primary key.

    Returns:
        UUID4 string.
    """
    return str(uuid4())


def now():
    """Current UTC time without timezone info (how timestamps are stored).

    Returns:
        A naive datetime.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for every table."""
    pass


class Sealed:
    """A Python value stored only as ciphertext in the row's `sealed` column.

    Reading decrypts with the owner's data key (and caches); writing marks the row so it is re-encrypted on save.

    Used by: Profile.data, Transaction.data, Import.rows, Run.result, Goal.data, Reward.data and Debt.data.
    """

    def __set_name__(self, owner, name):
        """Remember the attribute name for the per-object plaintext cache."""
        self.cache = f"_plain_{name}"

    def __get__(self, obj, owner=None):
        """Decrypt and return the value (cached after the first read).

        Returns:
            The plain Python value; raises when the row has no session or is not yet saved.
        """
        if obj is None:
            return self
        if self.cache not in obj.__dict__:
            session = object_session(obj)
            if session is None or obj.sealed in (None, PENDING):
                raise RuntimeError("Sealed value is not available")
            plain = crypto.keyring().open(session.connection(), obj.tenant_id, obj.__tablename__, obj.sealed)
            obj.__dict__[self.cache] = json.loads(plain)
        return obj.__dict__[self.cache]

    def __set__(self, obj, value):
        """Set a new value; the ciphertext is written when the row is flushed."""
        obj.__dict__[self.cache] = value
        obj.sealed = PENDING  # marks the row changed; ciphertext is written in before_insert/before_update


class TenantKey(Base):
    """An owner's data key, stored only wrapped by the master key.

    Fields:
        tenant_id: owner id.
        wrapped_key: the data key encrypted by the master key.
        master_key_id: which master key wrapped it.
        created_at: when it was created.
    """
    __tablename__ = "tenant_keys"
    tenant_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    wrapped_key: Mapped[str] = mapped_column(Text)
    master_key_id: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: now())


class User(Base):
    """A customer or operator account.

    Fields:
        id: internal id.
        username: unique sign-in name (operators never see customers' usernames).
        password_hash: Argon2 hash.
        role: 'customer' or 'operator'.
        version: increases on every data change, so stale forecasts and edits are detected.

    Used by: Sign-in, sessions and every customer request.
    """
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String(40), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="customer")
    version: Mapped[int] = mapped_column(Integer, default=0)


class LoginSession(Base):
    """A signed-in browser session.

    Fields:
        token_hash: SHA-256 of the session cookie (the cookie itself is never stored).
        user_id: account id.
        csrf: CSRF token the browser must send on changes.
        expires: expiry time.
    """
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    csrf: Mapped[str] = mapped_column(String(64))
    expires: Mapped[datetime] = mapped_column(DateTime)


class Profile(Base):
    """The owner's saved context (Your context page), encrypted.

    Fields:
        tenant_id: owner (tenant) id; rows are isolated per owner by row-level security in PostgreSQL.
        data: currency, timezone, as_of, coverage, cash, reserve and scheduled flows.
    """
    __tablename__ = "profiles"
    tenant_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    sealed: Mapped[str] = mapped_column(Text)
    data = Sealed()


class Transaction(Base):
    """One ledger transaction, encrypted.

    Fields:
        tenant_id: owner (tenant) id; rows are isolated per owner by row-level security in PostgreSQL.
        id: transaction id.
        idempotency_key: stops a retried entry being saved twice.
        payload_hash: detects a reused key with different content.
        data: date, description, amount_minor, currency, kind, category and categorization details.

    Used by: Transactions page, forecasts, categorizer, debts and FinaPoints proof.
    """
    __tablename__ = "transactions"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key"),)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    idempotency_key: Mapped[str] = mapped_column(String(100))
    payload_hash: Mapped[str] = mapped_column(String(64))
    sealed: Mapped[str] = mapped_column(Text)
    data = Sealed()


class Import(Base):
    """A CSV import preview awaiting confirmation, encrypted.

    Fields:
        tenant_id: owner (tenant) id; rows are isolated per owner by row-level security in PostgreSQL.
        id: import id.
        content_hash: same file twice returns the same preview.
        rows: validated, categorized rows.
        confirmed: whether the rows were added to the ledger.

    Used by: The Import transactions dialog.
    """
    __tablename__ = "imports"
    __table_args__ = (UniqueConstraint("tenant_id", "content_hash"),)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    content_hash: Mapped[str] = mapped_column(String(64))
    sealed: Mapped[str] = mapped_column(Text)
    rows = Sealed()
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)


class Run(Base):
    """A saved forecast run, encrypted.

    Fields:
        tenant_id: owner (tenant) id; rows are isolated per owner by row-level security in PostgreSQL.
        id: run id.
        source_version: the owner's data version it was calculated from.
        result: analysis, forecast, checks and steps.
        created_at: when it ran.

    Used by: Overview chart, Fina's explanations, goals pace and Agent activity.
    """
    __tablename__ = "runs"
    tenant_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    source_version: Mapped[int] = mapped_column(Integer)
    sealed: Mapped[str] = mapped_column(Text)
    result = Sealed()
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Policy(Base):
    """The global AI policy: RUNNING, ADVICE_PAUSED or AI_PAUSED.

    Fields:
        id: always 1.
        mode: current mode.
        revision: increases on each change (optimistic locking).

    Used by: Every model call checks it. Set on Admin > Overview, or paused automatically by the AI budget.
    """
    __tablename__ = "policy"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    mode: Mapped[str] = mapped_column(String(30), default="AI_PAUSED")
    revision: Mapped[int] = mapped_column(Integer, default=1)


class PolicyAudit(Base):
    """History of AI policy changes.

    Fields:
        mode: new mode.
        revision: new revision.
        reason: why it changed.
        created_at: when.

    Used by: Admin > Logs (policy changes).
    """
    __tablename__ = "policy_audit"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    mode: Mapped[str] = mapped_column(String(30))
    revision: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Goal(Base):
    """A savings goal, encrypted; progress is calculated, never stored.

    Fields:
        tenant_id: owner (tenant) id; rows are isolated per owner by row-level security in PostgreSQL.
        id: goal id.
        data: name, target_minor, target_date, saved_minor, status.
        created_at: when the goal was set (savings proof counts transfers from this day).

    Used by: Goals page, Fina, the advisor, Outlook and FinaPoints.
    """
    __tablename__ = "goals"
    tenant_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    sealed: Mapped[str] = mapped_column(Text)
    data = Sealed()
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Reward(Base):
    """One FinaPoints ledger entry, encrypted; append-only with a per-owner idempotency key.

    Fields:
        tenant_id: owner (tenant) id; rows are isolated per owner by row-level security in PostgreSQL.
        idem_key: unique per owner, so retries never award twice.
        data: kind (goal, referral, welcome, achievement, redeem), points, label, ref.
        created_at: when.

    Used by: The Referral page (balance, history, vouchers, achievements).
    """
    __tablename__ = "rewards"
    __table_args__ = (UniqueConstraint("tenant_id", "idem_key"),)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    idem_key: Mapped[str] = mapped_column(String(120))
    sealed: Mapped[str] = mapped_column(Text)
    data = Sealed()
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Debt(Base):
    """A tracked debt or fixed payment, encrypted.

    Fields:
        tenant_id: owner (tenant) id; rows are isolated per owner by row-level security in PostgreSQL.
        id: debt id.
        data: name, kind, balance_minor, rate_percent, monthly_minor.

    Used by: Outlook > Debts and fixed payments; the Debt Slayer achievement.
    """
    __tablename__ = "debts"
    tenant_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    sealed: Mapped[str] = mapped_column(Text)
    data = Sealed()
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ReferralCode(Base):
    """An owner's referral code.

    Fields:
        code: the code friends enter.
        tenant_id: owner id.

    Used by: The Referral page ("Your referral code") and sign-up.
    """
    __tablename__ = "referral_codes"
    code: Mapped[str] = mapped_column(String(12), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)


class Activity(Base):
    """Content-free record of a customer action for operations: no names, amounts or text.

    Fields:
        actor_ref: pseudonym (u-xxxxxx).
        action: fixed vocabulary, e.g. 'csv_import', 'limit_hit'.
        agent: agent involved, if any.
        model: model used, if any.
        outcome: short fixed outcome.
        duration_ms: how long it took.
        item_count: how many items (rows, transactions).

    Used by: Admin > Users & activity, Abuse & limits, and the 20 AI requests a day limit.
    """
    __tablename__ = "activity"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    actor_ref: Mapped[str] = mapped_column(String(12), index=True)
    action: Mapped[str] = mapped_column(String(30))
    agent: Mapped[str | None] = mapped_column(String(40), nullable=True)
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    outcome: Mapped[str] = mapped_column(String(40))
    duration_ms: Mapped[int] = mapped_column(Integer)
    item_count: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Feedback(Base):
    """A thumbs up or down on one agent output: fixed fields only, never text the owner typed.

    Fields:
        actor_ref: pseudonym.
        item_ref: which output was rated.
        agent: agent rated.
        surface: where in the app.
        source: 'ai' or 'calculation'.
        rating: 1 or -1.
        reason: fixed reason for a thumbs down.

    Used by: Thumbs on chat, advice and notes; Admin > Feedback.
    """
    __tablename__ = "feedback"
    __table_args__ = (UniqueConstraint("actor_ref", "item_ref"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    actor_ref: Mapped[str] = mapped_column(String(12), index=True)
    item_ref: Mapped[str] = mapped_column(String(64))
    agent: Mapped[str] = mapped_column(String(30))
    surface: Mapped[str] = mapped_column(String(30))
    source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    rating: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(String(30), nullable=True)


class ForecastAccuracy(Base):
    """How far off a past forecast turned out, once the owner's later transactions covered its period.

    Content-free: no amounts, dates or identity, only the method, the horizon and percentage errors.

    Fields:
        run_ref: hash of the forecast run, so each run is scored once.
        method: 'hybrid' or 'trailing_mean'.
        model: the foundation model's name for hybrid runs, else None.
        horizon_days: forecast length.
        spend_wape: |forecast everyday spending - actual| / actual, in percent.
        inside_band: whether actual spending fell inside the forecast's 10th-90th percentile range (hybrid only).
        created_at: when it was scored.

    Used by: Admin > Forecast accuracy ("Real accuracy").
    """
    __tablename__ = "forecast_accuracy"
    run_ref: Mapped[str] = mapped_column(String(32), primary_key=True)
    method: Mapped[str] = mapped_column(String(20))
    model: Mapped[str | None] = mapped_column(String(60), nullable=True)
    horizon_days: Mapped[int] = mapped_column(Integer)
    spend_wape: Mapped[float] = mapped_column()
    inside_band: Mapped[bool | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class Preference(Base):
    """An owner's choices about sharing data to improve Fina; everything is off until they opt in.

    Fields:
        tenant_id: owner id (row-level security in PostgreSQL).
        share_categories: share confirmed category labels as categorizer examples.
        share_questions: share questions to Fina and how they were routed as coordinator examples.
        updated_at: when the choice last changed.

    Used by: the Preferences page (GET/PUT /api/v1/preferences).
    """
    __tablename__ = "preferences"
    tenant_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    share_categories: Mapped[bool] = mapped_column(default=False)
    share_questions: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class GoldenContribution(Base):
    """One de-identified example an owner chose to share for building evaluation goldens.

    Stored apart from the owner's encrypted data, under a pseudonym only so it can be deleted when they withdraw;
    links, emails, UPI handles, phone numbers and long reference numbers are removed before it is saved.

    Fields:
        actor_ref: pseudonym of the owner who shared it (u-xxxxxx).
        kind: 'categorizer' or 'coordinator'.
        payload: JSON text: a merchant line with its category, or a question with its route and card.
        created_at: when it was shared.

    Used by: Admin > Evaluations (Contributed examples, export as draft goldens).
    """
    __tablename__ = "golden_contributions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    actor_ref: Mapped[str] = mapped_column(String(12), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    payload: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)


class Alert(Base):
    """An operations alert: what broke and how often, never customer data. Repeats update one open row.

    Fields:
        key: dedupe key.
        severity: critical/warning/info.
        title: headline.
        detail: content-free detail.
        count: how many times it happened.
        status: open/acknowledged/resolved.
        delivered: whether Telegram accepted it.
        sent_at: last push time (15-minute quiet period).
        created_at / last_at / closed_at: first seen, last seen, resolved.

    Used by: Admin > Alerts & health and phone alerts.
    """
    __tablename__ = "alerts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    key: Mapped[str] = mapped_column(String(80), index=True)
    severity: Mapped[str] = mapped_column(String(10))
    title: Mapped[str] = mapped_column(String(120))
    detail: Mapped[str] = mapped_column(String(300))
    count: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(15), default="open")
    delivered: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    last_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AiSpend(Base):
    """Model calls and their cost per UTC day and model, from configured prices.

    Fields:
        day: UTC date.
        model: model id.
        calls: number of calls.
        failures: failed or incomplete calls.
        cost_usd: estimated cost.

    Used by: Admin > AI costs, the health board and the daily budget.
    """
    __tablename__ = "ai_spend"
    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    model: Mapped[str] = mapped_column(String(160), primary_key=True)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(default=0.0)


class OpsSetting(Base):
    """Operations settings: the daily AI budget.

    Fields:
        daily_budget_usd: cap per day, or None for no cap.
        auto_pause: pause AI automatically at the cap.

    Used by: Admin > AI costs.
    """
    __tablename__ = "ops_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    daily_budget_usd: Mapped[float | None] = mapped_column(nullable=True)
    auto_pause: Mapped[bool] = mapped_column(default=True)


class Suspension(Base):
    """A customer blocked by an operator, by pseudonym; they cannot sign in or call the API.

    Fields:
        actor_ref: pseudonym of the suspended customer.
        reason: why (in the audit log too).
        by_operator: operator username.
        created_at: when.

    Used by: Admin > Abuse & limits (Suspend / Unsuspend); checked on every customer request.
    """
    __tablename__ = "suspensions"
    actor_ref: Mapped[str] = mapped_column(String(12), primary_key=True)
    reason: Mapped[str] = mapped_column(String(200))
    by_operator: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class AdminAudit(Base):
    """Who in operations did what, and when; "system" for automatic actions such as a budget pause.

    Fields:
        operator: operator username or 'system'.
        action: e.g. 'policy_change', 'suspend'.
        target: what it acted on.
        detail: short detail.
        created_at: when.

    Used by: Admin > Audit log.
    """
    __tablename__ = "admin_audit"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    operator: Mapped[str] = mapped_column(String(40))      # operator username (staff), or "system"
    action: Mapped[str] = mapped_column(String(40))
    target: Mapped[str | None] = mapped_column(String(80), nullable=True)
    detail: Mapped[str | None] = mapped_column(String(200), nullable=True)


class Metric(Base):
    """Duration and status of an operation such as a forecast, for operations.

    Fields:
        operation: operation name.
        duration_ms: how long.
        status: outcome.
        created_at: when.

    Used by: Admin > Overview.
    """
    __tablename__ = "metrics"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    operation: Mapped[str] = mapped_column(String(30))
    duration_ms: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


SEALED = {Profile: "data", Transaction: "data", Import: "rows", Run: "result", Goal: "data", Reward: "data", Debt: "data"}


def _seal(mapper, connection, target):
    """Encrypt a sealed record's value just before it is written.

    Args:
        mapper: SQLAlchemy mapper (unused).
        connection: database connection.
        target: the row being saved.
    """
    if target.sealed in (None, PENDING):
        value = target.__dict__["_plain_" + SEALED[type(target)]]
        target.sealed = crypto.keyring().seal(connection, target.tenant_id, target.__tablename__,
            json.dumps(value, separators=(",", ":")).encode())


for _model in SEALED:
    event.listen(_model, "before_insert", _seal)
    event.listen(_model, "before_update", _seal)


def seal_legacy(engine):
    """One-time migration: encrypt plaintext columns from before encryption, verify, then drop them.

    Args:
        engine: database engine.

    Used by: initialize() at startup; runs once on an old database.
    """
    tables = set(inspect_schema(engine).get_table_names())
    migrated = False
    with engine.begin() as conn:
        for model, field in SEALED.items():
            table = model.__tablename__
            columns = {c["name"] for c in inspect_schema(conn).get_columns(table)} if table in tables else set()
            if field not in columns:
                continue
            if "sealed" not in columns:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN sealed TEXT"))
            keys = ["tenant_id"] + (["id"] if "id" in columns else [])
            for row in conn.execute(text(f"SELECT {', '.join(keys)}, {field} FROM {table}")).mappings().all():
                plain = row[field] if isinstance(row[field], str) else json.dumps(row[field])
                ring = crypto.keyring()
                sealed = ring.seal(conn, row["tenant_id"], table, plain.encode())
                if json.loads(ring.open(conn, row["tenant_id"], table, sealed)) != json.loads(plain):
                    raise RuntimeError(f"Encryption check failed for {table}")
                where = " AND ".join(f"{k} = :{k}" for k in keys)
                conn.execute(text(f"UPDATE {table} SET sealed = :sealed WHERE {where}"), {"sealed": sealed, **{k: row[k] for k in keys}})
            conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {field}"))
            migrated = True
    if migrated and engine.dialect.name == "sqlite":
        # Dropped plaintext stays in free pages until the file is rebuilt.
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.exec_driver_sql("VACUUM")


def database(url):
    """Create an engine and session factory; on SQLite, turns on foreign keys and secure delete.

    Args:
        url: database URL.

    Returns:
        (engine, sessionmaker).

    Used by: create_app() and manage.py.
    """
    kwargs = {"pool_pre_ping": True, "hide_parameters": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url:
            kwargs["poolclass"] = StaticPool
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def foreign_keys(connection, _):
            """SQLite connection setup: enforce foreign keys and overwrite deleted content.

            Args:
                connection: the new SQLite connection.
                _: connection record (unused).
            """
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA secure_delete=ON")  # overwrite deleted content instead of leaving it in free pages
    return engine, sessionmaker(engine, expire_on_commit=False)


def set_tenant(db, tenant):
    """Tell PostgreSQL which owner is asking, so row-level security returns only their rows.

    Args:
        db: database session.
        tenant: owner id.

    Used by: Every authenticated customer request.
    """
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT set_config('app.tenant_id', :tenant, true)"), {"tenant": tenant})


def initialize(engine):
    # PostgreSQL is bootstrapped using the explicit migration command, never DDL as the app role.
    """Create tables, run the encryption migration and seed the AI policy (AI_PAUSED).

    Args:
        engine: database engine.

    Used by: App startup on SQLite and `manage.py init-db`.
    """
    Base.metadata.create_all(engine)
    seal_legacy(engine)
    with sessionmaker(engine)() as db:
        if db.get(Policy, 1) is None:
            db.add(Policy(id=1, mode="AI_PAUSED", revision=1))
            db.commit()
