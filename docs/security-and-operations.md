# Security, privacy, and operations

Status: target security requirements. The implemented controls and verified limits through [iteration 19](../specs/iteration_19.md) are listed in [build status](build-status.md); requirements below are not blanket completion claims.

## Tenant and admin isolation

Use authenticated server sessions with secure HTTP-only cookies, CSRF protection, session rotation/revocation and rate limiting. Prefer a maintained authentication library/provider; if passwords are hosted locally, use a vetted adaptive password hash and tested recovery flow. Never offer public admin self-registration. Operations roles are separately provisioned, with stronger authentication for policy changes.

Every customer query and object access requires verified tenant membership. PostgreSQL RLS policies use transaction-local tenant context (`app.current_tenant`) set by trusted middleware across `profiles`, `transactions`, `imports`, `runs`, `goals`, `tenant_keys`, `rewards`, `debts` and `preferences` ([backend/migrations/001_tenant_rls.sql](../backend/migrations/001_tenant_rls.sql)); connection pools must not leak session state. Application roles must not be superusers or have `BYPASSRLS`, and must not own financial tables. Apply `FORCE ROW LEVEL SECURITY` where appropriate and test worker/checkpointer paths too. PostgreSQL documents owner and privileged-role bypass behavior in [row security policies](https://www.postgresql.org/docs/current/ddl-rowsecurity.html).

The operations database role (`finpilot_ops`) can read only content-free operational projections (`metrics`, `activity`, `request_logs`, `feedback`, `alerts`, `ai_spend`, `ops_settings`, `suspensions`, `admin_audit`, `forecast_accuracy`, `contributions`, `policies`) and manage operational tables (`policies`, `alerts`, `ops_settings`, `suspensions`, `admin_audit`, `contributions`). It has no financial-schema, private-file, checkpoint, tenant-cache or user-stream permission. No administrator impersonation or raw trace viewer. Account suspension (`suspensions` table) operates by pseudonymous actor (`u-xxxxxx`) so operators never see usernames.

This protects against other customers and application administrators. It does not cryptographically hide data from a privileged database/cloud operator or a compromised backend that must decrypt data to process it. Do not market the server architecture as zero knowledge.

## Model gateway boundary and privacy

One server adapter constructs the OpenAI-compatible client with the fixed HTTPS origin and API key. Reject arbitrary endpoint configuration, credential-in-URL patterns and redirects to unapproved origins. Route every LLM/speech feature through it: agent reasoning, model-based classification, speech transcription and model judges. The hybrid forecaster's time-series foundation model (`amazon/chronos-bolt-small` via `backend/app/hybrid.py`) runs locally on CPU with frozen weights and sends no data over the network during inference. Deterministic parsers and arithmetic are ordinary code and do not need AI calls.

Before an inference request, remove account numbers, card numbers, addresses, contact details and unnecessary personal names; replace identifiers with tenant-local tokens. Preserve only the minimum description, merchant/category context and numeric facts required for the task.

Opt-in contribution sharing (`preferences` and `contributions` tables, [iteration 19](../specs/iteration_19.md)) is disabled by default (`share_categories=False`, `share_questions=False`). When an owner explicitly opts in, contributed category labels and coordinator questions are scrubbed of UPI handles, reference numbers, phone numbers, emails and card digits, amounts are rounded to ₹10, and the owner is recorded only as pseudonymous `u-xxxxxx`. Turning a toggle off or deleting contributions immediately deletes the owner's contributed rows.

## Guardrails, supervisor verification, and tool authorization

Treat CSV cells, receipt text, chat, voice transcripts and RSS headlines as untrusted data. They cannot change system instructions, tenant identity, allowlists or policy. Separate instructions from evidence, validate structured outputs, cap sizes, and ignore embedded requests to reveal secrets or invoke tools.

Publication gates check schema, owned/existing evidence, placeholder rendering (`{fact_key}`), currency/units, source freshness, uncertainty labeling, and risk-model review. In addition, every outgoing reply from `POST /api/v1/assistant/message` passes the deterministic coordinator supervisor (`backend/app/supervisor.py`, [iteration 19](../specs/iteration_19.md)), which blocks:
- Unknown routes or empty/overlong answers (`unknown_route`, `empty_or_too_long`)
- Named securities or tickers (`named_security`)
- Financial guarantees or promises (`guarantee`)
- Unhedged affordability verdicts (`verdict`)
- Personal data such as emails, UPI handles or phone numbers (`personal_data`)
- Confirm-card amounts not present in the owner's message (`card_amount_not_said`) or `confirm_flow` signs that contradict the owner's words (`card_direction_contradicts`)
- Rupee amounts in grounded explanations that are neither in the server's facts nor in the owner's question (`ungrounded_amount`)

Failed replies are replaced with a deterministic calculation or `SAFE_REPLY`, counted in `finpilot_supervisor_blocks_total`, and logged as `fina_blocked` in admin activity.

## Where sandboxing is needed

**Yes: isolate untrusted file parsing and any future external tool execution.** CSV/XLSX/PDF inputs can exhaust memory or exploit parsers even without AI. Run parsing in a non-root restricted worker/container with read-only application files, a per-job temporary directory, no network by default, CPU/memory/time/page/row/decompression limits, and no host mounts or secrets.

Fixed financial functions and the local CPU Chronos-Bolt tensor pipeline do not execute model-generated code.

## Observability, alerts, and prompt versions

Customer activity shows their own inputs/evidence, output summaries, durations and statuses. The admin view receives aggregate latency p50/p95, run/error counts, token usage, daily AI spend against the budget cap, blocked-reply counts, prompt versions, synthetic evaluation scores (`Admin > Evaluations`), forecast backtest and realized accuracy (`Admin > Forecast accuracy`), and audit-logged operator actions (`Admin > Audit log`). Exclude amounts, merchant names, filenames, categories tied to a person, chat, goals and raw exception payloads.

The API raises content-free alerts (`alerts` table, [iteration 18](../specs/iteration_18.md)) on unhandled 5xx errors, repeated model call failures, sign-in failure spikes, per-account rate-limit abuse, and 80%/100% daily AI budget thresholds, pushing them to Telegram when `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set (deduplicated per 15-minute window).

Prompts are stored under versioned source paths such as [prompts/advisor/v6.md](../prompts/advisor/v6.md), with assembly and release rules in the [prompt catalog](../prompts/README.md) and [prompt source versioning](prompt-versioning.md).

## Kill switch, AI budget, and release gates

Durable policy modes: `RUNNING`, `ADVICE_PAUSED`, and `AI_PAUSED`. In addition, the daily AI spend cap (`ops_settings` and `ai_spend` tables, [iteration 18](../specs/iteration_18.md)) can automatically pause AI (`AI_PAUSED`) and write a `system` entry to `admin_audit` when daily token spend reaches the configured cap. Read-only ledger access, exports, deterministic calculations and the local CPU hybrid forecaster remain available when AI is paused.

## Scheduler and proactive delivery

Choose APScheduler 3.x for the first implementation and pin a tested version later. Run one dedicated scheduler with a persistent job store, never one scheduler per FastAPI worker. Write notification intents through a transactional outbox with a unique tenant/channel/period/action key.

## Encryption of owner data (implemented, iteration 12)

Owner records (`profiles`, `transactions`, `imports`, `runs`, `goals`, `debts`) are sealed with a per-customer AES-256-GCM data key, bound to their table and customer. Data keys are stored only wrapped by a master key that never enters the database: a local key file in development (`MASTER_KEY_FILE`, default `~/.finpilot/master.key`), and a key management service in production through the `MasterKey` interface. See [iteration 12](../specs/iteration_12.md).
