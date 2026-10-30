"""Content-free telemetry: Prometheus metrics and opt-in sanitized LangSmith traces.

Metric labels and trace fields come from fixed allowlists. Amounts, descriptions,
categories, usernames and record IDs never become a label or leave the process in a trace.
"""
import hashlib
import time
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from langsmith import Client, trace, tracing_context
from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter("finpilot_http_requests_total", "HTTP requests by route template", ["method", "route", "status"])
HTTP_LATENCY = Histogram("finpilot_http_request_duration_seconds", "HTTP latency by route template", ["method", "route"])
NODE_LATENCY = Histogram("finpilot_agent_node_duration_seconds", "Agent graph node latency", ["node", "status"],
    buckets=(.001, .0025, .005, .01, .025, .05, .1, .25, .5, 1, 2.5))
LLM_REQUESTS = Counter("finpilot_llm_requests_total", "Mesh inference calls", ["agent", "model", "outcome"])
LLM_LATENCY = Histogram("finpilot_llm_request_duration_seconds", "Mesh inference latency", ["agent", "model"],
    buckets=(.25, .5, 1, 2, 4, 8, 15, 30))
LLM_TOKENS = Counter("finpilot_llm_tokens_total", "Mesh token usage", ["agent", "model", "type"])
ASSISTANT_ANSWERS = Counter("finpilot_assistant_answers_total", "Chat explanations by source and outcome", ["source", "outcome"])
FINA_VERIFIED = Counter("finpilot_fina_replies_verified_total", "Fina replies checked by the coordinator before they are shown, by route and outcome", ["route", "outcome"])
FEEDBACK = Counter("finpilot_feedback_total", "Thumbs up and down per agent", ["agent", "rating"])
CATEGORIZATIONS = Counter("finpilot_categorizations_total", "Transaction category outcomes", ["source", "status"])
AUTH_ATTEMPTS = Counter("finpilot_auth_attempts_total", "Sign-in and registration attempts", ["action", "outcome"])
INGESTED = Counter("finpilot_transactions_ingested_total", "Transactions written to ledgers", ["channel"])
POLICY_MODE = Gauge("finpilot_policy_mode", "Current policy mode (1 = active)", ["mode"])
AI_CONFIGURED = Gauge("finpilot_ai_configured", "1 when Mesh credentials and the shared model are set")
TRACING_ENABLED = Gauge("finpilot_tracing_enabled", "1 when sanitized LangSmith tracing is enabled")

# Recent HTTP requests for the operations log: route templates, status and timing only.
REQUEST_LOG = deque(maxlen=500)


def actor_ref(user_id):
    """Stable pseudonym for a customer, shown to operations instead of a username.

    Args:
        user_id: the customer's internal id.

    Returns:
        "u-" plus 6 hex characters, e.g. "u-0a2eaf".

    Used by: Activity logging, the abuse view and suspensions. Seen everywhere in the admin portal instead of
    usernames.
    """
    return "u-" + hashlib.sha256(f"finpilot-actor:{user_id}".encode()).hexdigest()[:6]


def priced(model, prompt_tokens, completion_tokens, prices):
    """USD cost of a model call from its token counts and the configured price.

    Args:
        model: model id.
        prompt_tokens: input tokens.
        completion_tokens: output tokens.
        prices: settings.model_prices: model -> [input, output] USD per million tokens.

    Returns:
        Cost in USD, or None when the model has no configured price.

    Used by: Daily AI spend (Admin > AI costs) and the LangSmith cost column (Admin > LangSmith).
    """
    price = prices.get(model or "")
    if not price or prompt_tokens is None or completion_tokens is None:
        return None
    return round((prompt_tokens * price[0] + completion_tokens * price[1]) / 1_000_000, 8)


def log_request(method, route, status, seconds, error=None):
    """Append one request to the in-memory request log (method, route, status, time, error type).

    Args:
        method: HTTP method.
        route: route template, never the raw URL.
        status: HTTP status code.
        seconds: duration in seconds.
        error: exception type name, or None.

    Used by: The request middleware. Seen on Admin > Logs and in the health board's error and response-time tiles.
    """
    REQUEST_LOG.append({"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "method": method,
        "route": route, "status": status, "duration_ms": round(seconds * 1000, 1), "error": error})


TRACE_FIELDS = {"agent", "model", "prompt_version", "batch_size", "horizon_days", "transaction_count", "outcome"}
TOKEN_FIELDS = {"input_tokens", "output_tokens", "total_tokens"}


def sanitize(data):
    """Allowlist applied to every LangSmith trace's inputs and outputs, so no customer content is traced.

    Args:
        data: a run's inputs or outputs.

    Returns:
        Only allowlisted fields (agent, model, versions, counts, token usage).

    Used by: Telemetry.span() and the LangSmith client.
    """
    data = data if isinstance(data, dict) else {}
    safe = {k: v for k, v in data.items() if k in TRACE_FIELDS and isinstance(v, (str, int, float, bool))}
    usage = data.get("usage_metadata")
    if isinstance(usage, dict):
        safe["usage_metadata"] = {k: v for k, v in usage.items() if k in TOKEN_FIELDS and isinstance(v, int)}
    return safe


def timed_node(name, fn):
    """Wrap a forecast pipeline step so its duration is measured and shown.

    Args:
        name: step name, e.g. 'forecaster'.
        fn: the step function taking and returning pipeline state.

    Returns:
        The wrapped step function.

    Used by: The LangGraph pipeline in finance.py. Seen as step times on Agent activity.
    """
    def node(state):
        """Run the wrapped step, record its latency metric and stamp duration_ms on its step entry.

        Args:
            state: the pipeline state dict.

        Returns:
            The step's result state.
        """
        started, status = time.perf_counter(), "error"
        try:
            result = fn(state)
            status = "ok"
            # Owner-visible step timing; the step list never leaves the process in traces.
            if result.get("steps"):
                result["steps"][-1]["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
            return result
        finally:
            NODE_LATENCY.labels(name, status).observe(time.perf_counter() - started)
    return node


def record_categories(rows):
    """Count categorization outcomes (owner, model, rule, unresolved) for metrics.

    Args:
        rows: categorized transaction rows.

    Used by: categorize_many(). Seen on Admin > Overview (categorization outcomes) and Grafana.
    """
    for row in rows:
        source = row.get("category_source", "unresolved")
        CATEGORIZATIONS.labels(source, row.get("category_status", "owner" if source == "owner" else "unresolved")).inc()


def record_llm_call(agent, model, outcome, seconds, usage):
    """Record a model call's count, latency and token usage as metrics.

    Args:
        agent: agent name.
        model: model id.
        outcome: 'completed', 'incomplete' or 'error'.
        seconds: duration.
        usage: the provider's usage object.

    Returns:
        Token counts {input_tokens, output_tokens, total_tokens} for tracing and spend.

    Used by: MeshGateway._complete() on every model call. Seen in Grafana and Admin > AI costs.
    """
    LLM_REQUESTS.labels(agent, model, outcome).inc()
    LLM_LATENCY.labels(agent, model).observe(seconds)
    tokens = {"input_tokens": getattr(usage, "prompt_tokens", None), "output_tokens": getattr(usage, "completion_tokens", None)}
    tokens = {k: v for k, v in tokens.items() if isinstance(v, int)}
    for kind, count in tokens.items():
        LLM_TOKENS.labels(agent, model, kind.removesuffix("_tokens")).inc(count)
    if tokens:
        tokens["total_tokens"] = sum(tokens.values())
    return tokens


class Telemetry:
    """Optional LangSmith tracing; off unless enabled with a key, and always sanitized.

    Used by: create_app() and the model gateway. Seen on Admin > LangSmith.
    """

    def __init__(self, api_key="", project="finpilot-local", enabled=False, *, endpoint="", client_options=None):
        """Create the LangSmith client when tracing is enabled.

        Args:
            api_key: LangSmith key.
            project: LangSmith project name.
            enabled: turn tracing on.
            endpoint: custom LangSmith endpoint (optional).
            client_options: extra client settings (tests).
        """
        self.project = project
        self.client = None
        if enabled and api_key:
            self.client = Client(api_key=api_key, api_url=endpoint or None, hide_inputs=sanitize,
                hide_outputs=sanitize, omit_traced_runtime_info=True, **(client_options or {}))
        TRACING_ENABLED.set(int(self.client is not None))

    @property
    def enabled(self):
        """True when tracing is on.

        Returns:
            bool.

        Used by: /health (tracing_enabled).
        """
        return self.client is not None

    def tracing(self, operation):
        """A tracing context for one operation, or a disabled one when tracing is off.

        Args:
            operation: name such as 'forecast'.

        Returns:
            A context manager.

        Used by: The forecast endpoint.
        """
        if self.client is None:
            return tracing_context(enabled=False)
        return tracing_context(enabled=True, client=self.client, project_name=self.project,
            metadata={"operation": operation}, tags=["finpilot"])

    @contextmanager
    def span(self, name, inputs, *, run_type="chain", metadata=None):
        """A sanitized trace span around one step or model call.

        Args:
            name: span name (agent or step).
            inputs: inputs, sanitized before sending.
            run_type: 'chain' or 'llm'.
            metadata: extra metadata (model, provider).

        Returns:
            Yields the run (or None when tracing is off).

        Used by: MeshGateway._complete().
        """
        if self.client is None:
            yield None
            return
        with tracing_context(enabled=True, client=self.client, project_name=self.project), \
                trace(name, run_type=run_type, inputs=sanitize(inputs), metadata=metadata,
                    client=self.client, project_name=self.project) as run:
            yield run

    def close(self):
        """Flush pending traces before shutdown."""
        if self.client is not None:
            self.client.flush()
