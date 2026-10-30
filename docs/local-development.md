# Local development

This is a local, single-process development build. Use synthetic data while the remaining production privacy, ingestion sandbox, retention and financial-quality gates are unfinished. [Build status](build-status.md) describes the exact implemented scope.

## Quick start and preview

Follow the [root README](../README.md) to install the pinned Python dependencies and the npm lockfile. To enable the hybrid time-series foundation model (`amazon/chronos-bolt-small` on CPU) instead of the trailing-mean baseline, also install `requirements-forecast.txt`:

```powershell
uv pip install --python .venv/Scripts/python.exe -r requirements-forecast.txt --extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match
```

Start the backend on port 8010 and Vite on port 5173. The Vite proxy keeps `/api`, `/ops` and `/health` on the frontend origin. API documentation is at [localhost:8010/docs](http://127.0.0.1:8010/docs); modifying requests require an allowed `Origin` header and an authenticated session's `X-CSRF-Token`.

If the Windows Store `python` or `py` alias fails, use the actual installed interpreter with `uv venv --python <path-to-python.exe>`. Run the app with `.venv/Scripts/python.exe`, not the Store alias. In the build session, Python 3.11.0 and Node 24.14.0 were used. If port 8010 is occupied, select a free backend port and update the targets in [vite.config.js](../frontend/vite.config.js). The server binds only to loopback.

SQLite tables initialize automatically on startup. Owner data is encrypted with a master key kept at `~/.finpilot/master.key` (override with `MASTER_KEY_FILE`); it is created on first run, must be backed up separately, and without it the database cannot be read. Older plaintext databases are encrypted automatically on first start. The ignored `runtime/finpilot.db` contains development accounts and financial records. Never commit it or use this mode to claim PostgreSQL row-level security. Source edits require restarting the backend, or adding `--reload` for development. Vite refreshes frontend changes.

## Working flow

1. Create an account using a username and a password of 12-128 characters. No email provider is connected. Sign-in attempts have a local five-minute rate limit.
2. Choose **Explore an example** in a fresh workspace to create synthetic context/history and calculate a forecast. The demo uses deterministic calculations (plus the frozen Chronos-Bolt foundation model if installed); missing categories use the LLM gateway only when configured and policy permits.
3. For manual setup, use **Your context** to supply currency, timezone, history coverage and a balance as of the cutoff. A missing balance produces net cash flow only. Reserve and coverage are explicit inputs.
4. Enter every relevant future payday/bill as a dated signed flow. Historical recurring expenses are excluded from the variable-spend estimate and need their future dates listed explicitly.
5. Add signed manual transactions or import CSV. Inspect preview, then confirm. Reconfirming the same import is idempotent; overlapping files require an explicit acknowledgement. Legitimate identical rows within a file are preserved.
6. Create a 7–90 day forecast. When `chronos-forecasting` is installed and `FORECAST_MODEL` is set, the hybrid forecaster predicts daily spending quantiles and simulates 1,000 correlated paths to draw a 10th–90th percentile shaded range and reserve/zero dip probabilities; otherwise it uses the trailing-mean baseline. Simulate a dated purchase to see its effect. This never writes a real transaction or issues a definitive affordability verdict.
7. Open **Agent activity** to follow each stage in order: ingestion, the categorizer (with its model, prompt version and outcome counts), then the analyzer, forecaster (`hybrid` or `deterministic`) and numeric checks with what each one read, produced and how long it took.
8. Click any category in **Transactions** to fix it: set your own label, or describe the purchase in a note for the categorizer. **Retry with AI** re-sends every uncategorized row, such as rows added while AI was off. Both need a new forecast afterwards.
9. Open the chat bubble at the bottom-right to talk with Fina (by text or voice). Ask about your forecast or a purchase ("can I afford a 2,000 dinner on Friday?"), tell her about income or bills ("my salary is 85k on the 1st") or categories ("Crossword is books"); changes appear as cards you confirm. Every reply is checked by the deterministic coordinator supervisor (`backend/app/supervisor.py`) before display. Answers are explanations, never a yes or no; each is labeled "checked against your numbers" (model answer that passed server checks, risk review and supervisor verification) or "worked out from your forecast". Model answers usually take 10–30 seconds; 20 questions per hour.
10. When Overview shows **Fina has a few questions**, answer them: she asks for missing cash, reserve and history confirmation, and suggests your next regular income and bills from your history. Edit or skip anything; only what you confirm is saved.
11. Open **Goals** to add savings goals (or tell Fina "save 50k for a trip by March"). Each shows progress, the monthly amount it needs and your pace from the latest forecast; tick it when done, use **Ask Fina** for a short note, and **Ways to get there sooner** for subscriptions to pause and non-essential spending to trim, with how much each helps the goal.
12. When a what-if dips under your reserve or below zero, use **What could make this work?** (or ask Fina "what should I do?"). Options are calculated first; Fina explains the best ones and the risk reviewer approves them. Admins can pause suggestions.
13. Open **Outlook** to see spending that is creeping up, what it would mean for your goals and reserve if it continued, and what would help. **Explain with Fina** adds a reviewed explanation. It needs at least 60 days of history.
14. On **Goals**, a goal that falls behind shows routes back (catch up, a new date, or a smaller milestone) with the reasoning for each, and every goal shows when it lands once all your goals are funded in date order. **Had an emergency?** (or telling Fina "I had to use 50,000 for an emergency") previews taking that money from your goals fairly with new dates for each; nothing changes until you apply it.
15. In **Outlook**, add loans, credit cards and rent under **Debts and fixed payments**. The debt agent shows payoff dates, how much of your income goes to fixed payments, what paying a little extra on the costliest debt saves, and goals you can add.
16. Open **Invest** to see whether investing fits your situation yet (emergency fund, costly debt, money left after goals), a cautious SIP amount when it does, broad options explained with their risks, and recent market headlines for context. It never names specific stocks or funds.
17. **Referral** shows your FinaPoints, vouchers matched to your spending, your invite code and achievement tokens for milestones such as your first and third goal.
18. **Preferences** lets you opt in to sharing de-identified category labels or coordinator questions to help improve evaluation goldens (earning FinaPoints once per toggle). Everything is off by default; turning a choice off or clicking **Delete shared examples** removes your contributed items immediately.

Supported currencies in this initial implementation are INR, AED, USD, EUR and GBP, all with two minor-unit decimal places. One consolidated cash account is modeled. A transfer must be classified by the owner; internal transfers do not count as expenses or income. Refund matching, automatic recurrence discovery during forecasting, transaction editing and durable clarification interrupts remain future work.

## CSV format

```csv
date,description,amount_minor,currency,kind,category,recurring
2026-10-17,Cafe lunch,-25000,INR,expense,food,false
2026-10-18,Salary,8500000,INR,income,income,true
```

Required columns: `date`, `description`, `amount_minor`, `currency`, `kind`. Optional: `category`, `recurring`. Dates are ISO dates; amounts are signed integer minor units, not decimal currency amounts. Allowed kinds: expense, income, transfer, refund, borrowing, repayment. Expenses/repayments are negative; income/refunds/borrowing are positive. Category may be omitted for LLM categorization; ambiguous descriptions or unavailable AI remain uncategorized. Limits: 500,000 characters and 1,000 rows; HTTP bodies are capped at 1 MB. Only CSV text parsing is implemented; PDF, Excel and OCR are not accepted. The parser is bounded but not yet an isolated worker.

## Operations account

Create an operations-only account from a trusted local terminal:

```powershell
.venv/Scripts/python.exe -m backend.manage create-operator --username local_ops
```

The command prompts for a password without echoing it and refuses to promote an existing customer. Sign in through the same UI. The operations dashboard shows static prompt versions/checksums, source evaluation status, content-free timing records and policy revisions. It cannot access customer endpoints. Policy changes are audited and use optimistic revisions.

## Admin portal

Open `/admin` (for example http://localhost:5173/admin) and sign in with an operations account to reach the tabbed portal. It has its own session, so you can stay signed in to the customer app in another tab ([iteration 7](../specs/iteration_7.md), [iteration 18](../specs/iteration_18.md), [iteration 19](../specs/iteration_19.md)):
- **Overview:** policy controls (`RUNNING`, `ADVICE_PAUSED`, `AI_PAUSED`), categorization outcomes, prompt versions and checksums.
- **Alerts & health:** uptime, DB check/size, AI mode, 5xx errors, p95 latency, model call failure rate, spend vs cap, Telegram test button, and alert acknowledge/resolve.
- **Users & activity:** pseudonymous IDs (`u-xxxxxx`), counts and a live request table.
- **Abuse & limits:** 7-day limit hits by pseudonym and one-click suspend/unsuspend.
- **AI costs:** today/month spend, daily cap and auto-pause toggle, 30-day spend chart, and spend by model using `MODEL_PRICES` from `.env`.
- **Feedback:** per-agent thumbs-up/down counts and reasons, plus de-identified opt-in contributions (`GET /ops/v1/contributions`) with export and reject actions.
- **Logs:** recent API requests and policy changes.
- **Forecast accuracy:** active forecaster model status (`amazon/chronos-bolt-small` or trailing average), synthetic backtest comparisons (balance MAE, 80% range coverage, spend WAPE, pinball loss, per-persona breakdown), content-free realized accuracy (`ForecastAccuracy`), and **Run backtest** (`POST /ops/v1/forecast-accuracy/run`).
- **Evaluations:** DeepEval suite runs (`coordinator`, `categorizer`, `forecaster`, `risk`, `goals_coach`, `system`), per-case inspection, and background evaluation runner (`POST /ops/v1/evals/run`).
- **Audit log:** every operator action and automatic system pause (`admin_audit`).
- **Grafana** and **LangSmith:** embedded Grafana dashboard (`GRAFANA_URL`) and recent sanitized LangSmith runs.

Operators never see usernames, amounts, descriptions, notes or chat text.

## Hybrid forecaster and backtest ([iteration 19](../specs/iteration_19.md))

Set `FORECAST_MODEL=amazon/chronos-bolt-small` in `.env` (the default) and install `requirements-forecast.txt`. When a customer has an opening balance and at least 14 days of history, `backend/app/hybrid.py` runs `amazon/chronos-bolt-small` on CPU to predict nine daily quantiles (`10%..90%`) for everyday non-recurring spending, then simulates 1,000 correlated paths (`RHO = 0.5`) with exact scheduled income and bills. Set `FORECAST_MODEL=` (empty) to force the trailing-mean baseline.

Run the rolling-origin synthetic backtest from the repository root:

```powershell
.venv/Scripts/python.exe -m backend.app.backtest --model amazon/chronos-bolt-small --run-id 20261028-calibrated
```

Results are saved to `evals/results/forecast/<run-id>.json` and displayed on **Admin > Forecast accuracy**.

## PostgreSQL and Redis

Compose provides PostgreSQL 17 and Redis 7, exposed on loopback only. Set unique `POSTGRES_PASSWORD`, `APP_DB_PASSWORD` and `OPS_DB_PASSWORD` in an ignored `.env`, then run:

```powershell
docker compose up -d
```

The initialization script creates `finpilot_app` and `finpilot_ops` roles on a fresh database volume. Use an owner connection temporarily for schema initialization, then a restricted role for the server:

```powershell
# Substitute a URL-encoded local owner password privately; do not commit it.
$env:DATABASE_URL = 'postgresql+psycopg://postgres:<owner-password>@localhost:5432/finpilot'
.venv/Scripts/python.exe -m backend.manage init-db
Remove-Item Env:DATABASE_URL
```

Set `.env` `DATABASE_URL` to the `finpilot_app` connection and `OPS_DATABASE_URL` to the `finpilot_ops` connection before starting the API. The owner role is for migration only. The API checks for non-bypass/non-owner application privileges, enforced RLS on private tables, and absence of private-table privileges on the operations connection. Run `create-operator` using the app connection after migration. Never run the API as the owner or superuser.

[The initial RLS migration](../backend/migrations/001_tenant_rls.sql) scopes `profiles`, `transactions`, `imports`, `runs`, `goals`, `tenant_keys`, `rewards`, `debts` and `preferences` with transaction-local tenant context. Authentication tables are service-owned and inaccessible to the operations connection. Redis is provisioned but not yet used; cache/checkpoint integration is still pending. Changing environment passwords does not rotate roles in an already-initialized volume; perform an explicit credential rotation instead.

## Tests

The regular backend suite uses temporary SQLite files (`FORECAST_MODEL` is cleared in `tests/backend/conftest.py` so unit tests run fast and test the hybrid path via a deterministic stand-in model in `tests/backend/test_hybrid.py`). PostgreSQL tests require a separate database **named `finpilot_test`** and permission to create the two restricted test roles. They create synthetic records and must never target a real application database. Test-only role passwords are fixed disposable fixture values.

```powershell
$env:FINPILOT_TEST_POSTGRES_ADMIN_URL = 'postgresql+psycopg://postgres:<test-password>@127.0.0.1:<test-port>/finpilot_test'
.venv/Scripts/python.exe -m pytest -q
Remove-Item Env:FINPILOT_TEST_POSTGRES_ADMIN_URL
```

## Mesh / OpenRouter categorization and model routing

Set `INFERENCE_PROVIDER` (`mesh` or `openrouter`) and the corresponding `*_API_KEY`, `*_MODEL` (the shared model), and `*_RISK_MODEL` in the ignored `.env`, then restart the backend. No LLM IDs are hardcoded in Python. The initial policy is `AI_PAUSED`; an operations account can select `RUNNING` (or `ADVICE_PAUSED`, which permits categorization but not advice). See [framework and model decisions](framework_decision.md) for the current model selections (`deepseek/deepseek-v4-flash` for shared agents, `qwen/qwen3.5-122b-a10b` for risk review).

Manual entries and CSV previews generate categories for unlabeled rows in batches of 25 after input/currency validation. Explicit owner categories and matching merchant rules (`owner_rule`) bypass inference. Only the category, its source/status/reason and verified prompt/model provenance are added; amounts, kind and currency remain unchanged.

### Dynamic category names

Category names are open text, generated by the LLM or entered by the owner (`Category` in `schemas.py`: 1–64 characters after trimming, normalized to lowercase). Up to 100 category names from the current owner's ledger and import are supplied as optional hints. `uncategorized` remains the server's unresolved marker.

### Fixing and retrying categories

`POST /api/v1/transactions/{id}/category` takes `expected_version` and either `category` (the owner's own label, which also spreads to matching unlabelled rows from the same merchant) or `note` (a short description sent to the categorizer as `owner_note`). `POST /api/v1/transactions/recategorize` retries unresolved rows and reuses saved notes. Each customer has **20 AI categorization requests per day** (notes and retries); setting a category manually is never limited.

### Learning from your corrections ([iteration 6](../specs/iteration_6.md), [iteration 17](../specs/iteration_17.md))

Each categorizer request includes up to 30 `owner_examples` from the same owner, and exact merchant rules (`merchant_key`) apply the owner's most specific earlier label before any model call. Examples never cross tenants and are minimized like descriptions.

## Observability

Telemetry is content-free by construction: metric labels and trace fields come from fixed allowlists in [observability.py](../backend/app/observability.py). Amounts, descriptions, per-person categories, usernames and record IDs are never labels or exported trace fields.

### Prometheus metrics

The API exposes `GET /metrics` (loopback only, unauthenticated, like `/health`). Series use route templates such as `/api/v1/runs/{run_id}`, never raw paths.

| Metric | Labels | Meaning |
| --- | --- | --- |
| `finpilot_http_requests_total`, `finpilot_http_request_duration_seconds` | method, route, status | API traffic and latency; boundary rejections use `route="unmatched"` |
| `finpilot_agent_node_duration_seconds` | node, status | LangGraph node latency (analysis, projection, checks_node) |
| `finpilot_llm_requests_total`, `finpilot_llm_request_duration_seconds`, `finpilot_llm_tokens_total` | agent, model, outcome/type | Model calls, latency and token usage |
| `finpilot_supervisor_blocks_total` | route, rule | Replies blocked by the coordinator supervisor (`backend/app/supervisor.py`) |
| `finpilot_categorizations_total` | source, status | Owner/merchant_rule/model/unresolved category outcomes |
| `finpilot_transactions_ingested_total` | channel | Manual and CSV rows written |
| `finpilot_auth_attempts_total` | action, outcome | Sign-in/registration success, rejection, throttling |
| `finpilot_policy_mode`, `finpilot_ai_configured`, `finpilot_tracing_enabled` | mode | Current kill-switch and configuration state |

### Prometheus and Grafana

Set `GRAFANA_ADMIN_PASSWORD` in `.env`, start the API, then:

```powershell
docker compose -f compose.observability.yaml up -d
```

Grafana opens at [localhost:3000](http://127.0.0.1:3000) (user `admin`) with the provisioned **FinPilot overview** dashboard as home. Prometheus is at [localhost:9090](http://127.0.0.1:9090); its target page should show `finpilot-api` as UP.

### LangSmith tracing

Off by default, and the off state also overrides an ambient `LANGSMITH_TRACING` environment variable. To enable, set in `.env`:

```text
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<your key>
LANGSMITH_PROJECT=finpilot-local
```

Traces show the forecast graph (`forecast` → `analysis` → `projection` → `checks_node`) and each model call with latency, model, prompt version, batch size, outcome and token usage. The LangSmith client's `hide_inputs`/`hide_outputs` hooks reduce every run to that allowlist, so node state (profiles, transactions, balances) is never exported.

## Phone alerts (Telegram)

1. In Telegram, open @BotFather, send `/newbot` and copy the bot token.
2. Send `/start` to your new bot from your phone.
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and copy `message.chat.id`.
4. Put both in `.env` as `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`, then restart the API.
5. In the admin portal, open **Alerts & health** and press **Send test alert**.

Without these settings alerts are still recorded and listed in the admin portal. Set a daily AI budget under **AI costs**; at the cap the API can pause AI by itself.
