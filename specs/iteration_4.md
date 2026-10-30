# Iteration 4: content-free observability

Date: 2026-10-26. Author: Claude Code. Request: the user asked whether observability
was recorded in a spec iteration and, since it was not, to record it as iteration 4.
Evidence: the original brief ([iteration 0](iteration_0.md)) asks for an admin view of
metrics, overall latency and LangSmith charts, with "observability only" and no access
to user data.

## Previous state and change

Iteration 3 gave operations a table of content-free timing records stored in the
operations database, policy revisions and a static prompt inventory. There were no
time-series metrics, dashboards, alerts or traces. Observability code was later added
without an iteration record. This iteration records it and its live verification.

Affected requirements: FP-17 (admin observability), partially; FP-18 (telemetry
privacy), the telemetry part only. Neither is complete; see remaining work below.

- **Prometheus metrics.** `GET /metrics` exposes HTTP traffic and latency by route
  template, LangGraph node latency, model call counts/latency/tokens by agent and
  model, category outcomes, ingestion counts, sign-in outcomes, and policy, AI and
  tracing state. Labels come from fixed allowlists in
  [observability.py](../backend/app/observability.py); raw paths, amounts,
  descriptions, per-person categories, usernames and record IDs are never labels.
- **Grafana and alerts.** [compose.observability.yaml](../compose.observability.yaml)
  runs Prometheus and Grafana on loopback. The provisioned **FinPilot overview**
  dashboard covers overview stats, API, agent graph, model inference, and ingestion and
  categorization. [Alert rules](../observability/prometheus/alerts.yml) cover API down,
  server error ratio, model call failures and sign-in failure spikes.
- **LangSmith traces.** Opt-in tracing covers the forecast graph and each model call.
  The client's `hide_inputs`/`hide_outputs` hooks reduce every run to an allowlist
  (agent, model, prompt version, batch size, horizon, transaction count, outcome, token
  usage). Tracing is off by default, and the off state overrides an ambient
  `LANGSMITH_TRACING` variable. Provider errors are raised outside the span.

## Decisions and assumptions

Accepted from the brief: admin sees metrics, latency and LangSmith-backed views, never
customer data. Engineering assumptions awaiting confirmation:

- Grafana is the charting surface for now; the in-app operations dashboard does not
  draw charts yet.
- `/metrics` is unauthenticated but bound to loopback, like `/health`. A deployed build
  needs authentication or network isolation.
- Prometheus keeps 15 days of data. No Alertmanager receiver is configured, so alerts
  fire only in the Prometheus UI.
- LangSmith is opt-in per environment. Exported traces are allowlisted, not redacted
  after the fact.

## Acceptance and evidence

- `tests/backend/test_observability.py`: the sanitizer keeps only allowlisted fields;
  `/metrics` uses route templates and contains no seeded private values; LangSmith
  export keeps agent structure without financial content; tracing stays off by default
  even with ambient environment variables set.
- Full backend suite on 2026-10-26: 53 passed, 3 PostgreSQL tests skipped without a
  configured test database.
- Live `/metrics` on 2026-10-26: 45 `finpilot_` series served by the local API, with
  AI configured, tracing enabled and policy `AI_PAUSED`.
- Live LangSmith export on 2026-10-26 with synthetic transactions: one categorizer run
  and one risk run reached the `finpilot-local` project. Inputs held only agent, model,
  prompt version and batch size; outputs held only outcome and token usage. None of the
  synthetic descriptions appeared in the exported runs.
- Grafana and Prometheus were not started in this session (Docker was not running), so
  the dashboard and alert rules remain unverified live.

## Data, security and evaluation impact

No schema or data migration. No prompt version change. Telemetry adds no new storage of
customer data; Prometheus and Grafana volumes hold only allowlisted series. LangSmith
is an external processor when enabled; the allowlist limits what leaves the process,
but enabling it in a real environment still needs a processor review. Exception
messages from deterministic graph nodes are still recorded by LangSmith on failed runs.

## Remaining work

From FP-17 and [security and operations](../docs/security-and-operations.md): in-app
charts, anonymous run tokens showing agent order, cache hit rate, queue age,
blocked-advice counts, evaluation scores, small-cohort suppression, Alertmanager
receivers and live Grafana verification. Golden synthetic evaluations remain NOT RUN.
