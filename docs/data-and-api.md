# Data, memory, and API contracts

Status: target data/API contracts. The implemented subset through [iteration 19](../specs/iteration_19.md) is described in [build status](build-status.md) and [local development](local-development.md); this table includes both implemented and remaining target endpoints.

## Storage rules

Use UUID primary identifiers and `tenant_id` on every private row. Tenant IDs are derived from authenticated membership, never trusted from a model or request body. Use composite `(tenant_id, id)` constraints and composite foreign keys to prevent cross-tenant references. UUIDs do not replace authorization. PostgreSQL row-level security complements explicit service-layer filtering; details are in [security and operations](security-and-operations.md).

Money uses signed integer minor units plus ISO currency and an explicit transaction kind. Persist date separately from timestamps when a statement has no time; store actual timestamps in UTC and evaluate calendar periods in the user's timezone. Versions monotonically advance when source data changes. Writes and downstream invalidation/outbox events commit in the same database transaction.

## Logical schema

| Entity | Important fields and invariants |
| --- | --- |
| Tenant / membership | Owner subject, role, locale, timezone, currency; admin role confers no membership in customer tenants. |
| Account / Profile | Currency, timezone, opening balance, balance as-of date, reserve, coverage start/confirmed, dated scheduled flows (`profiles` table, encrypted per customer). |
| Import / source file | Owner, content hash, MIME, encrypted rows, parser version, confirmed status (`imports` table). |
| Transaction | Owner, signed amount, currency, date, sanitized description, kind, category, source/status/reason, idempotency key and payload hash (`transactions` table, encrypted per customer). |
| Category decision | Transaction/version, category source (`owner` / `merchant_rule` / `model` / `unresolved`), status, reason, AI attempt count, history of prior decisions. |
| Clarification | Question type, allowed fields, related record, graph interrupt, status, answer author/time, version; accept each answer once. |
| Debt | Detected or owner-edited recurring obligation (`debts` table, encrypted per customer): amount still owed and annual interest rate (`PATCH /api/v1/debts/{id}`). |
| Analysis fact | Query/rule version, 30-day matched period comparisons, values, evidence references, uncategorized count. |
| Forecast (`runs`) | Snapshot version, opening balance basis, horizon, `method` (`hybrid` or `trailing_mean`), `model` (`chronos-bolt-small` or `None`), `rule_version` (`hybrid-tsfm-v1` or `trailing-mean-v1`), `uncertainty_method` (`simulated-quantiles` or `not_calibrated`), daily timeline (`cash_minor`, and for hybrid `cash_low_minor` / `cash_high_minor`), `below_reserve_probability`, `below_zero_probability`, `spend_q`, `spend_total_q`, limitations (`runs` table, encrypted per customer). |
| Forecast accuracy (`forecast_accuracy`) | Content-free realized accuracy scored once a customer's `as_of` date passes an earlier forecast's end date: hashed `run_ref`, `method`, `model`, `horizon_days`, `spend_wape`, `inside_band`, `created_at` ([iteration 19](../specs/iteration_19.md)). |
| Scenario | Baseline forecast ID, immutable structured input, dated adjustments, output deltas, and shifted 10th–90th percentile band + reserve/zero probabilities for hybrid runs; never writes actual transactions. |
| Recommendation | Action, evidence/forecast/scenario IDs, expected effect, assumptions, review verdict, approved policy/prompt versions. |
| Goal | Owner-accepted definition (`goals` table, encrypted per customer), target, due date, progress state, recovery routes, emergency re-plan preview/apply. |
| Reward ledger (`rewards`, `referral_codes`) | Signed FinaPoints change, reason, goal savings proof, referral code, achievement tokens; never negative ([iteration 15](../specs/iteration_15.md), [iteration 17](../specs/iteration_17.md)). |
| Preference & contribution (`preferences`, `contributions`) | Tenant-scoped opt-in toggles (`share_categories`, `share_questions`, off by default) and de-identified shared examples (`u-xxxxxx`, PII redacted, amounts rounded to ₹10) for evaluation goldens; turning off deletes shared rows immediately ([iteration 19](../specs/iteration_19.md)). |
| Operational & alert tables (`metrics`, `activity`, `request_logs`, `feedback`, `alerts`, `ai_spend`, `ops_settings`, `suspensions`, `admin_audit`, `policies`) | Content-free timings, pseudonymous activity (`u-xxxxxx`), per-agent feedback ratings/reasons, Telegram/portal alerts, daily token spend and budget cap, account suspensions by pseudonym, and operator/system audit trail ([iteration 15](../specs/iteration_15.md), [iteration 18](../specs/iteration_18.md)). |

Import idempotency uses tenant/account plus file hash and normalized row identity. Re-uploading the same file does not duplicate rows. Across overlapping statements, prefer bank reference IDs; otherwise mark possible duplicates for review. Two genuine purchases of the same amount/merchant/date must remain distinguishable, so do not globally dedupe on those fields alone. Manual writes use client idempotency keys with payload hashes; reusing a key for different content is rejected.

## Three memory tiers

The terms describe lifecycle and authority, not three required databases. Proposed retention defaults below must be validated against product needs and deployment policy.

| Tier | Contents | Backing / proposed lifetime | Authority |
| --- | --- | --- | --- |
| Short term | Recent conversation context, working facts, cached responses | Redis: chat context 24 hours; financial response cache at most 15 minutes. PostgreSQL checkpoints: active run, interrupted run up to 30 days. | Temporary and versioned; expired interrupts must restart safely. |
| Long term | Confirmed goals, preferences, merchant corrections, usage answers, reviewed summaries | PostgreSQL until edited/deleted, with freshness dates and periodic reconfirmation. | Confirmed facts override summaries and inferred preferences. |
| Durable records ("permanent") | Normalized transactions, accepted goals, recommendation evidence, reward ledger | PostgreSQL/private storage with explicit deletion and retention policy. Raw files proposed 30 days after successful extraction. | Financial source of truth; durable does not mean undeletable. |

Account deletion covers files, rows, checkpoints, caches, search indexes and destination credentials. Backups age out on a documented schedule; a restore must reapply deletion tombstones before serving traffic. Audit integrity is append-only during ordinary use, not an excuse to retain private content forever.

## Retrieval and cache

Use parameterized typed SQL tools for amounts, periods, trends and goals. User/model input cannot supply arbitrary SQL. A transaction sum must query the relevant complete ledger, never a top-k document sample.

Start with exact structured-intent caching. Suggested key: `tenant + principal_scope + intent_hash + ledger_version + profile_version + goal_version + usage_version + prompt/model_version + policy_version + date_bucket + currency + timezone`. A question with a changed purchase amount/date is a different intent. Recheck current kill-switch state even on a cache hit. Do not cache across users. Invalidate after imports, corrections, goal/usage/profile changes, prompt/policy changes, local date rollover and deletion.

Semantic answer caching is deferred: "Can I spend 5,000?" and "Can I spend 50,000?" must never collide. A future similarity layer may retrieve candidates but must require identical canonical parameters and evidence versions. Optional document RAG uses tenant-filtered chunks and sources; embedding and reranking models must also route through the configured gateway.

## REST surface (`/api/v1` and `/ops/v1`)

Prefix customer endpoints with `/api/v1`. Authentication establishes tenant scope before every lookup; foreign resources return a consistent non-disclosing response. All state changes require authorization and browser CSRF protection when cookie-authenticated.

| Endpoint | Contract |
| --- | --- |
| `POST /api/v1/imports` / `POST /api/v1/imports/{id}/confirm` | Bounded CSV preview with categorization and duplicate check; confirm with expected version. |
| `POST /api/v1/transactions` / `POST /api/v1/transactions/{id}/category` / `POST /api/v1/transactions/recategorize` | Manual entry, owner category or note-based AI recategorization, and bulk AI retry. |
| `GET /api/v1/profile` / `PUT /api/v1/profile` | Read/update confirmed financial context and scheduled flows with optimistic concurrency. |
| `POST /api/v1/forecasts` / `GET /api/v1/runs/{id}` / `POST /api/v1/scenarios` | Run hybrid (`Chronos-Bolt` + 1,000 simulated futures) or baseline forecast, score past completed forecasts (`ForecastAccuracy`), and simulate dated what-if purchases. |
| `POST /api/v1/assistant/message` / `POST /api/v1/assistant/explain` / `POST /api/v1/assistant/transcribe` | Coordinator-routed chat and voice with confirm cards, risk-reviewed explanations, and final deterministic `supervisor.py` verification. |
| `POST /api/v1/investigator/questions` | Missing context questions and recurring income/bill candidates. |
| `POST /api/v1/advisor/advice` / `POST /api/v1/goals/{id}/advice` | Grounded, risk-reviewed options for a tight forecast/purchase or to reach a goal sooner. |
| `GET /api/v1/goals` / `POST /api/v1/goals` / `PATCH /api/v1/goals/{id}` / `POST /api/v1/goals/emergency` | Goals with calculated progress, recovery routes, goal-coach notes, and fair emergency re-planning. |
| `GET /api/v1/outlook` / `PATCH /api/v1/debts/{id}` / `GET /api/v1/invest` | Behavioural spending warnings, debt payoff plans, and investment readiness with market headlines. |
| `GET /api/v1/rewards` / `POST /api/v1/rewards/redeem` / `POST /api/v1/feedback` | Proven FinaPoints, referral code, achievement tokens, and per-agent thumbs-up/down feedback. |
| `GET /api/v1/preferences` / `PUT /api/v1/preferences` / `DELETE /api/v1/preferences/contributions` | Opt-in de-identified category/question contribution sharing and immediate deletion ([iteration 19](../specs/iteration_19.md)). |

Admin endpoints under `/ops/v1` expose `overview`, `policy`, `health`, `alerts`, `users`, `activity`, `abuse` (with suspend/unsuspend), `costs` (with budget cap and auto-pause), `feedback`, `contributions` (with export and reject), `logs`, `forecast-accuracy` (synthetic backtests and realized accuracy, plus `POST /ops/v1/forecast-accuracy/run`), `evals` (DeepEval results and `POST /ops/v1/evals/run`), `audit`, `grafana` and `langsmith`. Admin endpoints never expose customer PII or delegate to customer endpoints with an impersonated user.
