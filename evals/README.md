# Evaluation protocol and results

## Running the agent evaluations (DeepEval)

Goldens live in `evals/goldens/` (one JSON file per agent plus `system.json` for end-to-end scenarios), each with
`dev` and `holdout` splits. The suites in `evals/deepeval/` run every case through the real API on a throwaway
database with live models from `.env`; code checks are the gate, and an LLM judge (never the model under test)
adds faithfulness, relevance and tone scores. Design: [docs/evals-design.md](../docs/evals-design.md).

```powershell
# one-time: a separate environment, so the app's pinned packages stay untouched
uv venv .venv-evals --python <path-to-python311>
uv pip install --python .venv-evals/Scripts/python.exe -r requirements-evals.txt --override evals/eval-overrides.txt

# run (dev split by default; EVAL_SPLIT=holdout or all; EVAL_JUDGE=off for code checks only)
.venv-evals/Scripts/python.exe -m pytest evals/deepeval -q
$env:EVAL_SPLIT="all"; $env:EVAL_JUDGE="off"; .venv-evals/Scripts/python.exe -m pytest evals/deepeval/test_coordinator.py -q
```

`EVAL_JUDGE_MODEL` picks the judge (default: the risk model). The goldens are also checked offline, without any
model call, by `tests/backend/test_goldens.py` in the normal test suite. Results are written to
`evals/results/deepeval/<run_id>/` and shown on **Admin > Evaluations** (`/ops/v1/evals`).

## Running the forecast backtest (`backend.app.backtest`)

The rolling-origin forecast backtest compares the hybrid forecaster (`amazon/chronos-bolt-small` daily spending
quantiles + 1,000 correlated simulated futures with `RHO = 0.5`) against the `trailing_mean` baseline across
6 synthetic personas (`steady`, `lumpy`, `rising`, `festive`, `frugal_noisy`, `emi_heavy`), 6 cutoffs (`120..195`),
and 2 horizons (`7d`, `30d` — 36 cases per horizon):

```powershell
# optional: install the CPU time-series foundation model dependencies
uv pip install --python .venv/Scripts/python.exe -r requirements-forecast.txt --extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match

# run the rolling backtest (writes evals/results/forecast/<run-id>.json)
.venv/Scripts/python.exe -m backend.app.backtest --model amazon/chronos-bolt-small --run-id 20261028-calibrated
```

Results appear on **Admin > Forecast accuracy** (`/ops/v1/forecast-accuracy`) alongside content-free realized
accuracy (`ForecastAccuracy`) scored as customers' balance dates move past earlier forecasts.

## Golden dataset design

Version the generator, seed, schema, taxonomy and expected outputs. Use independent hand-reviewed examples alongside generated examples so the evaluator does not simply reproduce the implementation's assumptions. Keep train/development data separate from a frozen holdout by persona and merchant family, and avoid near-duplicate leakage.

Initial proposed suite:

- Categorization: at least 500 labeled transactions across at least 12 categories, including multilingual/abbreviated descriptions, ambiguous merchants, unknowns, transfers, refunds, reimbursements, salary, loan proceeds and repayments. Each label records direction/kind as well as spending category and acceptable clarification behavior. (`evals/goldens/categorizer.json` currently provides 38 curated dev/holdout cases.)
- Forecasting: synthetic personas with daily observations across stable income, lumpy purchases, rising spending, festive-season spikes, frugal noisy spending and heavy EMI obligations (`backend/app/backtest.py`, 6 personas × 240 days; target 12+ personas for final submission). Hold out future dates. Do not claim that synthetic performance establishes real-world accuracy.
- Analysis: at least 50 manually reviewed cases with known anomaly/no-anomaly labels, period denominators and materiality, including new spending with a zero baseline and incomplete months.
- Simulation and statements: at least 40 fixed numerical cases with independently calculated expected balances, reserve breaches, transfer handling, refund adjustments and statement incompleteness.
- Advice: at least 100 evidence bundles with required/forbidden claims, user constraints and risk outcomes. Include low-usage but essential subscriptions, cancellation fees, negative cash and missing opening balances.
- Security/operations: two customer tenants and an operations admin; canary PII, malicious uploaded instructions, forged IDs, stale caches, interrupted runs, retries, concurrent reward claims and kill-switch races.

Each fixture should contain `case_id`, `dataset_version`, split, seed/source, synthetic tenant/persona, inputs, expected structured outcomes, label rationale and reviewer status. The generator must never contact real users or use live bank data. Golden labels cannot be generated solely by the same model being evaluated.

## Metrics and current results

| Area | Measurement | Proposed gate / interpretation | Result |
| --- | --- | --- | --- |
| Categorization | Accuracy and macro-F1 across categories; confusion matrix; per-category support | Macro-F1 >= 0.85 on frozen holdout; include all cases and report clarification separately | 38-case golden suite PASSED (`evals/goldens/categorizer.json`, accuracy >= 0.85; [deepeval-2026-10-27.md](results/deepeval-2026-10-27.md)); 500-case holdout NOT RUN |
| Coordinator & supervisor | Intent routing, exact amount/date/direction extraction, deterministic `supervisor.py` reply verification | Intent macro-F1 >= 0.92; zero ungrounded amounts or unstated card amounts | PASSED (34/34 in `20261027-baseline` and `20261027-235621-supervisor`; [iteration 19](../specs/iteration_19.md)) |
| Forecaster explanations | Grounded `{fact_key}` placeholders, no guarantees or verdicts, judge faithfulness/relevancy/tone | Zero ungrounded numbers; faithfulness >= 0.9 after judge calibration | Code gate PASSED (8/8 grounded, 0 guarantees in `20261027-baseline` & `20261027-235621-supervisor`); judge calibration pending |
| Forecast point & spend error | 7/30-day endpoint MAE in currency and everyday-spending median WAPE | Report against `trailing_mean` baseline across all personas | RUN (`20261028-calibrated`, 36 cases/horizon): 7d MAE ₹2,611.9 (`hybrid`) vs ₹2,644.0 (`trailing_mean`), WAPE 13.4% vs 21.9%; 30d MAE ₹7,597.3 vs ₹7,081.5, WAPE 12.2% vs 12.3% |
| Forecast ranges | Nominal (80%) versus observed 10th–90th percentile interval coverage and pinball loss | Coverage near 80% target (`RHO = 0.5`) | RUN (`20261028-calibrated`): 80% coverage 77.8% (7d) and 83.3% (30d) for `hybrid` vs 83.3% (7d) and 88.9% (30d) for `trailing_mean` |
| Forecast shortfalls | Precision/recall of reserve dips (`below_reserve_probability > 0.5`) | Report across personas | RUN (`20261028-calibrated`): 100.0% recall and 100.0% precision on reserve dips for both `hybrid` and `trailing_mean` |
| Risk reviewer | Approve/block precision and recall on unsafe outputs | Unsafe recall >= 0.98; safe approval >= 0.9 | 11/12 in `20261027-baseline` (9/9 unsafe mutations blocked; [deepeval-2026-10-27.md](results/deepeval-2026-10-27.md)) |
| Advice grounding | Valid evidence ownership/existence, verified numerical claims, constraint satisfaction | >= 98% fully grounded cases and zero critical unsupported financial claims across >= 100 cases | PASSED on synthetic data (2026-10-26, 100 cases, release v16): 100% grounded, 0 critical; see below |
| Tenant/privacy isolation | Unauthorized read/write/stream/cache/tool attempts; telemetry canary leakage | Zero observed leaks across the defined suite | Engineering suite passed (177 backend tests, plus PostgreSQL RLS suite); full adversarial holdout NOT RUN |
| Performance/cost | p50/p95 by node and full run, tokens, verified/estimated cost | Report hardware, model, concurrency and cache state before setting an SLA | Tracked live in Prometheus `/metrics` and `Admin > AI costs` |

## Forecast backtest protocol

At each cutoff, provide only transactions and profile facts known by that date. Fit recurrence and spending estimates on past data; forecast the subsequent horizon, then compare against held-out synthetic actuals. Repeat rolling cutoffs and report each horizon and persona separately, plus pooled results. Never let future salary dates, user corrections, or scenario outcomes leak into the fitting window.

Use MAE rather than MAPE for balances near zero or negative. For WAPE, define the cash-flow target and denominator explicitly; report undefined when the denominator is zero. Compare forecasts with the same opening balance, account coverage, currencies and cutoff. Improvements need uncertainty estimates or paired per-case results; do not cherry-pick only stable personas.

Latest recorded backtest: [evals/results/forecast/20261028-calibrated.json](results/forecast/20261028-calibrated.json) ([iteration 19](../specs/iteration_19.md)).

## Advice evaluation

Hard checks recompute all stated savings/shortfalls and verify evidence against the exact run snapshot. Human-reviewed rubric checks whether the advice is relevant, feasible, specific, appropriately cautious and faithful to the user. An optional LLM judge must run through the configured gateway, use a versioned rubric, and be reported separately from deterministic and human scores. It must not become the sole source of truth for numeric accuracy or its own model's quality.

Critical failures include cross-tenant evidence, invented balances/returns, unsupported definitive affordability, bypassed publication policy, or a prohibited financial/tool action. A high average cannot hide one such failure. Distinguish synthetic release evaluations from production feedback; admin sees aggregate scores, never private evaluation payloads.

## Advisor evaluation (implemented)

```powershell
.venv/Scripts/python evals/advisor_eval.py --cases 100 --seed 7
```

Runs synthetic personas through the deterministic options and the live advisor and risk
reviewer, audits every answer that would be shown (amounts and dates against the cited
option, no essential cuts, no verdicts or guarantees, remaining reserve breaches stated)
and writes `evals/results/advisor-<date>-seed<seed>-n<cases>.json`. It needs model
credentials and spends credits; it is not part of CI. Latest:
[advisor-2026-10-26-seed7-n100.json](results/advisor-2026-10-26-seed7-n100.json), gate
passed. Limits: one synthetic generator, a single run, no human gold answers, and no
judgment of usefulness or tone.
