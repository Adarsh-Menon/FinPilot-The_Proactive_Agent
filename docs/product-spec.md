# Product specification

Baseline date: 2026-10-17. Status: proposed interpretation of [iteration 0](../specs/iteration_0.md).

## Outcome and boundaries

Help a person understand their cash trajectory, assess a decision before spending, and take a specific achievable action. The agent can warn, explain, and remind; it cannot actually stop purchases without a future explicitly authorized financial integration. It must communicate uncertainty and missing data rather than promise perfect advice.

The full vision remains in scope across phases. P0 below means the first complete hackathon demonstration; P1 extends ingestion and engagement; P2 adds features requiring more provider, funding, or policy work. This sequencing is a proposal, not a deletion of the founder's requirements.

## Requirements and acceptance criteria

| ID | Capability | Phase | Acceptance evidence |
| --- | --- | --- | --- |
| FP-01 | Mesh-only AI | P0 | Every inference call goes through the server Mesh adapter; network tests reject any other inference host; missing credentials produce an explicit unavailable state. |
| FP-02 | Coordinator and distinct agents | P0 | Separate node contracts, prompts/tools, and observable transitions for categorization, investigation, analysis, forecasting, advising, risk, and goals. |
| FP-03 | Transaction ingestion | P0/P1 | CSV and manual entry first; XLSX/XLS, text PDFs, and receipts later. Preview, mapping, validation, confirmation, UUIDs, provenance, and duplicate handling demonstrated. |
| FP-04 | Categorization with HITL | P0 | Description-sensitive classification; ambiguous expenses, transfers, and income prompt the owner; corrections persist and trigger recomputation. |
| FP-05 | Investigator | P0 | Collect current balances, debts/obligations, savings, goals/dates, currency, timezone, and preferences progressively; unknown values remain unknown. |
| FP-06 | Behavioral analysis | P0 | Compare equivalent periods; compute grounded trends and anomalies. Ask neutral subscription-usage questions without prematurely publishing advisory conclusions. |
| FP-07 | Forecast | P0 | Daily 30/60/90-day cash trajectory with assumptions, obligations, data coverage, and an uncertainty treatment tested against held-out data. |
| FP-08 | What-if simulation | P0 | A dated purchase or spending change produces baseline/scenario balances, minimum balance, shortfall dates, and goal impact using the same data snapshot. |
| FP-09 | Specific advice and audit | P0 | Every published recommendation references valid owned facts and forecast/scenario IDs, quantified impact where supported, and risk-review outcome. |
| FP-10 | Risk review and statements | P0 | Deterministic budget checks plus an independent review can approve, revise, or block advice; cash-flow and partial net-worth statements identify missing inputs. |
| FP-11 | Goal tracking | P0 | User accepts a feasible goal with baseline, metric, target, and due date; progress responds to data corrections and month transitions. |
| FP-12 | Fina coins and referrals | P1/P2 | Idempotent coin ledger and referral attribution; simulated redemption explicitly labeled; funded cash redemption deferred pending decisions. |
| FP-13 | Scheduled proactive delivery | P0/P1 | A persistent scheduler sends a consented personalized digest through one real channel; later email, Telegram, WhatsApp coverage. Restart/retry and opt-out tested. |
| FP-14 | Customer interface | P0/P1 | Welcome/auth, imports, clarification queue, chat, forecast/scenario view, agent activity with evidence, dated goals, and later rewards/referrals. |
| FP-15 | Multi-tenant privacy | P0 | Tenant A cannot access tenant B's records, files, streams, caches, checkpoints, exports, or tool results; admin lacks financial-content access. |
| FP-16 | Memory and caching | P0 | Expiring short-term context, confirmed long-term profile, durable financial records; cached answers invalidate on relevant changes. |
| FP-17 | Admin observability | P0 | Operational charts, anonymous run/node status, prompt versions, evaluations and kill switch; no prompts with user values or financial payloads. |
| FP-18 | Guardrails and tool security | P0 | PII minimization, instruction/data separation, tool allowlists, arithmetic/evidence checks, and adversarial tests. |
| FP-19 | Golden data and measurement | P0 | Versioned synthetic fixtures, frozen holdout, measured categorization/forecast results, run provenance and documented limitations. |
| FP-20 | Investment/property/IPO/business research | P2 | Fresh attributable research with jurisdiction, date, assumptions, suitability inputs and risk review; no guaranteed gains or automated transactions. |
| FP-21 | Spec-driven delivery | P0 onward | Immutable numbered spec revisions and maintained architecture, decisions, README, and actual results. |

## Customer journey

1. Sign in and choose currency, timezone, and optional notification consent. A user initially owns one tenant; future shared households require an explicit membership model.
2. Upload a file or add transactions. Preview date/amount mappings and ambiguities; confirm import. Never guess ambiguous date locale or debit/credit signs silently.
3. Answer concise grouped questions about unclear transactions and financial context. The user may skip optional questions; missing required balances block absolute affordability claims.
4. Watch agent statuses and inspect their own sanitized input summaries, structured outputs and supporting records. Display concise decision explanations, not private model chain-of-thought.
5. Receive a trajectory, a risk-reviewed action, and an explanation such as a dated goal shortfall. Neutral internal anomaly findings can be inspected as evidence after review.
6. Ask a purchase question. If amount/date or funding source is missing, clarify before computing. A scenario is hypothetical until the user explicitly records an actual transaction.
7. Accept/edit goals and channel preferences; receive limited, timely reminders. Goal progress and reward eligibility come from validated records, not a completion checkbox alone.

## Behavioral policies to resolve carefully

- Proposed interpretation of "less than or equal to 5 days": usage on at most five days in the last 30 days. Ask the user to confirm the window. Consider value, annual contracts, penalties and essential use before recommending cancellation; never infer usage from billing.
- A 20% food-spend reduction is an example, not a universal target. Use comparable historical periods and user constraints; avoid rewarding missed essentials.
- Proposed coin rule: five coins per uniquely verified goal period. At the suggested rate of 5,000 coins = INR 150, five coins equal INR 0.15 and reaching redemption takes 1,000 such awards. Economics and incentives need a deliberate revision before live redemption.
- A personal transaction ledger supports an income/expense statement. A complete balance sheet requires assets and liabilities, and business profit requires appropriate accounting inputs. Do not label all credits as profit.
- "No wrong information" becomes enforceable checks plus honest uncertainty, abstention, corrections, and measured error rates; it is not an achievable absolute LLM guarantee.

## Example demonstration

Use a synthetic persona with known salary dates, rent, food delivery, subscriptions, an opening balance, and a savings goal. Import their history, clarify an ambiguous transfer, confirm low subscription usage, show an upcoming shortfall, simulate a trip, and accept a smaller spending goal. Display the numerical evidence behind the action, a blocked unsuitable alternative, an automatically scheduled digest, and an admin view containing only operational data. All demonstration amounts must come from the fixture, not hand-written UI messages.
