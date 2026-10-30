# Context Summary — 2026-10-28 (Iteration 19)

See also: [2026-10-28_context_iteration_19.md](2026-10-28_context_iteration_19.md) and [specs/iteration_19.md](specs/iteration_19.md).

## 1. Forecaster & Hybrid Forecaster Code Audit

The hybrid forecaster is implemented end-to-end across the backend, evaluation harness, and React frontend:

- **Time-series foundation model (`backend/app/hybrid.py`):**
  - `FoundationModel(model_id="amazon/chronos-bolt-small")` lazily loads a frozen Amazon Chronos-Bolt pipeline on CPU (`torch.float32`, via optional `requirements-forecast.txt`: `chronos-forecasting==2.3.2`, `torch==2.14.1`).
  - Predicts 9 daily spending quantiles (`0.1..0.9`) for everyday non-recurring expenses over the 7–90 day horizon from up to 512 days of history (requiring at least 14 days of history and a known opening balance).
  - `simulate()` runs `PATHS = 1000` correlated Monte Carlo paths (`RHO = 0.5`, Gaussian copula factor + inverse-CDF interpolation) combining exact `scheduled_flows` (and optional what-if purchase overlays) with sampled daily spending.
  - Returns daily balance quantiles (`balance_q`), horizon total spending quantiles (`spend_total_q`), `below_reserve_probability`, and `below_zero_probability`.
- **LangGraph forecast pipeline (`backend/app/finance.py`):**
  - `forecast(state)` runs `PREDICTOR` when configured (`FORECAST_MODEL=amazon/chronos-bolt-small` in `backend/app/config.py`, wired in `create_app()` in `backend/app/main.py`).
  - Produces `method="hybrid"`, `rule_version="hybrid-tsfm-v1"`, `uncertainty_method="simulated-quantiles"`, and timeline rows with `cash_minor` (median), `cash_low_minor` (10th percentile), and `cash_high_minor` (90th percentile).
  - Falls back cleanly to `method="trailing_mean"` (`rule_version="trailing-mean-v1"`, `uncertainty_method="not_calibrated"`) if `chronos-forecasting` is not installed, `FORECAST_MODEL` is empty, opening balance is missing, history is < 14 days, or the model errors (noting the fallback reason in `limitations`).
  - `check(state)` includes `below_reserve_probability` and `below_zero_probability` in numeric checks and step details.
  - `simulate(baseline, amount, purchase_date)` re-runs `hybrid.simulate` with the same seed (`sum(series) % 100_003`) so what-if purchases shift the 10th–90th percentile band and update breach probabilities.
- **Forecaster explanation agent (`backend/app/assistant.py`, `prompts/forecaster/v4.md`):**
  - `build_facts()` exposes five hybrid facts when available: `balance_at_horizon_end_likely_low`, `balance_at_horizon_end_likely_high`, `chance_below_reserve_percent`, `chance_below_zero_percent`, and `chance_below_reserve_with_purchase_percent`.
  - `calculated_answer()` includes the 10th–90th percentile ending range and reserve-dip probability whenever the forecast is banded.
  - `MEANINGS["balance_at_horizon_end_without_purchase"]` is clarified as `"Expected balance on the last forecast day (the middle of the likely range)"`.
- **Rolling-origin backtest & realized accuracy (`backend/app/backtest.py`, `backend/app/storage.py`, `backend/app/main.py`):**
  - `python -m backend.app.backtest` evaluates `hybrid` vs `trailing_mean` across 6 personas (`steady`, `lumpy`, `rising`, `festive`, `frugal_noisy`, `emi_heavy`) × 6 cutoffs (`120..195`) × 2 horizons (`7d`, `30d`).
  - Saved run `evals/results/forecast/20261028-calibrated.json`:
    - **7d (36 cases):** `hybrid` MAE ₹2,611.9 vs `trailing_mean` ₹2,644.0; median spend WAPE 13.4% vs 21.9%; 80% coverage 77.8% vs 83.3%; pinball ₹943.5 vs ₹892.3; dip recall/precision 100%/100%.
    - **30d (36 cases):** `hybrid` MAE ₹7,597.3 vs `trailing_mean` ₹7,081.5; median spend WAPE 12.2% vs 12.3%; 80% coverage 83.3% vs 88.9%; pinball ₹2,590.4 vs ₹2,368.4; dip recall/precision 100%/100%.
  - `score_past_forecasts()` scores completed past forecasts against actual subsequent transactions into the content-free `forecast_accuracy` table.
  - **Overview & Admin UI (`frontend/src/App.jsx`):** shaded 10th–90th percentile band on Overview chart; **Admin > Forecast accuracy** tab (`/ops/v1/forecast-accuracy`) with grouped metric bars, persona table, realized accuracy, and background backtest runner.

## 2. Other Iteration 19 Additions Verified in Code

- **Coordinator deterministic supervisor (`backend/app/supervisor.py`, `tests/backend/test_supervisor.py`):**
  - Every reply from `POST /api/v1/assistant/message` passes `supervisor.verify()` before display, blocking `unknown_route`, `empty_or_too_long`, `named_security`, `guarantee`, `verdict`, `personal_data`, `card_amount_not_said`, `card_direction_contradicts`, and `ungrounded_amount`.
  - Coordinator also preserves fuller deterministic goal names (`"Goa trip"` over `"Trip"`) and enforces outflow sign on payment phrases (`"pay 28000 rent"` -> `-2800000`).
- **DeepEval evaluation harness (`evals/deepeval/`, `evals/goldens/`):**
  - Six suites (`coordinator`, `categorizer`, `forecaster`, `risk`, `goals_coach`, `system`) with `dev`/`holdout` splits, custom deterministic `Check` metrics, `GatewayJudge`, and **Admin > Evaluations** (`/ops/v1/evals`).
- **Opt-in contribution sharing (`Preference`, `Contribution`, `tests/backend/test_preferences.py`):**
  - Off by default (`GET/PUT /api/v1/preferences`); opting in shares de-identified category labels and routed questions (`GET /ops/v1/contributions`, `/ops/v1/contributions/export`), awards FinaPoints once per toggle, and deletes shared items immediately when turned off.

## 3. Verification & Documentation Updates Completed

- **Backend test suite:** `177 passed, 3 skipped` (`pytest tests/backend -q`).
- **Specs updated:** created [specs/iteration_19.md](specs/iteration_19.md) and indexed it in [specs/README.md](specs/README.md).
- **Living docs cross-checked and synchronized:**
  - [docs/build-status.md](docs/build-status.md)
  - [docs/architecture.md](docs/architecture.md)
  - [docs/framework_decision.md](docs/framework_decision.md)
  - [docs/local-development.md](docs/local-development.md)
  - [docs/data-and-api.md](docs/data-and-api.md)
  - [docs/security-and-operations.md](docs/security-and-operations.md)
  - [evals/README.md](evals/README.md)
  - [README.md](README.md)

