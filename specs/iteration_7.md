# Iteration 7: admin portal with activity, logs, Grafana and LangSmith

Date: 2026-10-26. Author: Claude Code. Request: the user asked for an admin portal to see
users and their requests, logs, and the Grafana and LangSmith dashboards in one place.

## Previous state and change

The operations page showed policy controls, categorization counts, prompt versions and a
list of forecast timings. Operators could not see per-user activity, request logs or
dashboards without leaving the app.

Affected requirement: FP-17 (admin observability), partially.

**Separate admin entry.** Follow-up request: view the admin and customer sides
separately. The portal now lives at `/admin` with its own sign-in. Customers and
operators get separate session cookies (`finpilot_session`, `finpilot_ops_session`)
with separate CSRF tokens, so one browser can be signed in to both at once. Each sign-in
endpoint (`/api/v1/auth/login`, `/ops/v1/auth/login`) admits only its own role and tells
the other role where to go; each side's routes accept only its own cookie, so
cross-role requests now get 401 instead of 403.

The operations page is now a tabbed portal:

- **Overview**: the previous content (policy, categorization outcomes, prompt inventory).
- **Users & activity**: one row per user under a stable pseudonymous ID (`u-` plus six
  hex characters of a salted SHA-256 of the user ID), with first seen, last active, and
  counts of transactions added, forecasts, what-ifs, Fina questions and AI calls. A
  request table, refreshed every 10 seconds and filterable by user, shows each action
  with agent, model, outcome, item count and duration.
- **Logs**: the last 500 API requests since the server started (time, method, route
  template, status, duration, exception type for failures), refreshed every 5 seconds
  with filters, plus the policy-change audit trail. Health checks, metric scrapes and
  the log's own polling are not logged.
- **Grafana**: the provisioned FinPilot overview dashboard in a frame. Grafana now
  allows embedding; its own sign-in is still required. When Grafana is not running the
  tab explains how to start it.
- **LangSmith**: the 50 most recent root runs (name, type, model, outcome, status,
  tokens, latency) fetched through the LangSmith API with a link to the project.
  LangSmith sends `X-Frame-Options: SAMEORIGIN`, so it cannot be framed.

**Cost (follow-up, 2026-10-27).** The LangSmith tab shows each model call's cost twice:
LangSmith's own figure, and the cost at the provider's prices from `MODEL_PRICES` (USD per
million input and output tokens per model) applied to the run's token counts, with totals
for the latest 50 runs. Both are shown because LangSmith prices runs from its own list,
which differed from the provider's published price for the shared model (about $0.15 and
$0.60 per million input and output tokens against $0.05 and $0.45) and had no price for
the risk model. Against real runs: 34 model calls, LangSmith $0.0156 for the 25 it could
price, $0.0389 for all 34 at configured prices; risk reviews made up most of the cost.

## Decisions

The user chose "activity, no content" over showing usernames or full content, keeping
the brief's rule that operations sees observability only. The new `activity` table
stores a fixed action vocabulary, agent, model, a short outcome, duration and an item
count; never usernames, amounts, descriptions, notes, questions or answers. The
operations database role gets `SELECT` on it and the app role `INSERT`; operations still
has no access to customer tables, so user rows come only from activity records.
Pseudonyms are stable but not secret from someone with database access.

## Acceptance and evidence

- Test: a customer named `private_person` imports, adds "Secret clinic visit" for 987.65,
  forecasts and asks Fina about a "secret trip"; no ops endpoint response contains the
  username, description, amount, question, transaction text or run ID. Customers get 401
  on every ops route; invalid user references get 422; activity and counts match.
- Test: one client holds both cookies, each session works only on its own side,
  each CSRF token only for its own session, and logging out of admin leaves the
  customer signed in. Browser: `/admin` shows the operator sign-in, refuses a customer
  account, and a customer tab in the same browser stays signed in.
- Backend suite on 2026-10-26: 70 passed, 3 PostgreSQL tests skipped. Frontend build
  passed.
- Headless browser on a throwaway database: an operator saw the customer's pseudonymous
  row and six requests, the live request log, the Grafana not-running state and real
  sanitized LangSmith runs, with no private text on the page and no page errors.
- Not verified: Grafana embedding with Grafana running (Docker was not running), and the
  PostgreSQL grants for the new table (PostgreSQL tests were skipped).

## Data, security and evaluation impact

New `activity` table, created automatically for SQLite and by `init-db` for PostgreSQL;
existing databases gain it on next startup in SQLite mode. The request log is in memory
and resets on restart. LangSmith runs shown to operators are the already-sanitized
exports. Earlier test runs, before the test suite ignored the local `.env`, exported a
few mocked categorizer runs to the developer's LangSmith project; they contain only
allowlisted fields.
