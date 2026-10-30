# Iteration 19: Hybrid forecaster with Chronos-Bolt, forecast backtests, DeepEval harness, coordinator supervisor and opt-in contribution sharing

Date: 2026-10-28. Author: FinPilot engineering. Request from the user: build and verify a hybrid
forecaster where scheduled flows remain exact and uncertain everyday spending comes from a frozen
time-series foundation model, evaluate it against the trailing-mean baseline with rolling backtests
and realized accuracy, add a multi-agent evaluation harness with a deterministic coordinator
supervisor, and let owners opt in to sharing de-identified examples.

## 1. Hybrid forecaster (`backend/app/hybrid.py`, `backend/app/finance.py`, `backend/app/assistant.py`)

Financial arithmetic and known obligations stay exact in code; language models never compute
numbers. Only the uncertain component—day-to-day non-recurring spending—is predicted by a
pretrained, frozen time-series foundation model on CPU:

- **Foundation model wrapper (`FoundationModel` in `backend/app/hybrid.py`):** lazily loads
  `amazon/chronos-bolt-small` (`chronos-forecasting==2.3.2`, `torch==2.14.1` in optional
  `requirements-forecast.txt`, configured via `FORECAST_MODEL`) on first forecast call under a
  thread lock. Given up to 512 days of historical daily non-recurring spending (requiring at least
  14 days of history and a known opening balance), it predicts nine daily spending quantiles
  (`0.1, 0.2, ..., 0.9`) over the 7–90 day horizon, clamped non-negative and sorted monotonically.
- **Correlated path simulation (`simulate()` in `backend/app/hybrid.py`):** draws `PATHS = 1000`
  possible futures from the daily spending quantiles using inverse-CDF linear interpolation (with
  linear tails to `0` below the 10th percentile and `q[8] + (q[8] - q[4])` above the 90th) and a
  Gaussian copula factor (`RHO = 0.5`, chosen by backtest calibration) so multi-day spending
  streaks do not unrealistically cancel out. Known `scheduled_flows` (salary, rent, bills) and any
  what-if purchase (`purchase=(day_index, amount_minor)`) are applied exactly to every simulated
  path.
- **Outputs and fallback (`forecast()` and `simulate()` in `backend/app/finance.py`):**
  - When the foundation model runs (`method="hybrid"`, `rule_version="hybrid-tsfm-v1"`,
    `uncertainty_method="simulated-quantiles"`), each timeline day carries `cash_minor` (50th
    percentile), `cash_low_minor` (10th percentile) and `cash_high_minor` (90th percentile), and
    the forecast and numeric checks include `below_reserve_probability`, `below_zero_probability`
    and `spend_total_q`.
  - What-if scenarios (`POST /api/v1/scenarios` and Fina's purchase simulations) re-run the 1,000
    paths with the same deterministic seed (`sum(series) % 100_003`) so the purchase shifts the
    likely balance band and updates reserve/zero breach probabilities.
  - When `chronos-forecasting` is not installed, `FORECAST_MODEL` is empty, history is under 14
    days, opening balance is unknown, or the model errors, the forecaster falls back cleanly to
    `method="trailing_mean"` (`rule_version="trailing-mean-v1"`,
    `uncertainty_method="not_calibrated"`) and records why in `limitations`.
- **Overview chart and Fina (`frontend/src/App.jsx`, `backend/app/assistant.py`):**
  - The Overview chart draws the shaded 10th–90th percentile band and a caption with the 80% likely
    ending range and reserve-dip probability.
  - `build_facts()` exposes five new grounded placeholders when present:
    `balance_at_horizon_end_likely_low`, `balance_at_horizon_end_likely_high`,
    `chance_below_reserve_percent`, `chance_below_zero_percent` and
    `chance_below_reserve_with_purchase_percent`.
  - `calculated_answer()` appends the 10th–90th percentile ending range and reserve-dip chance when
    available.

## 2. Forecast backtesting and realized accuracy (`backend/app/backtest.py`, `ForecastAccuracy`)

- **Rolling-origin synthetic backtest (`python -m backend.app.backtest`):** evaluates `hybrid` vs
  `trailing_mean` across 6 personas (`steady`, `lumpy`, `rising`, `festive`, `frugal_noisy`,
  `emi_heavy`), 6 cutoffs (`day 120, 135, 150, 165, 180, 195` using 90 days of history for the
  baseline and up to 512 days for Chronos-Bolt), and 2 horizons (`7` and `30` days; 36 cases per
  horizon).
- **Calibrated backtest results (`evals/results/forecast/20261028-calibrated.json`, commit `61c67de6`):**

| Method | Horizon | Cases | End Balance MAE (₹) | Median Spend WAPE (%) | 80% Range Coverage (%) | Pinball Loss (₹) | Reserve Dip Recall (%) | Reserve Dip Precision (%) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `hybrid` (`chronos-bolt-small`) | 7d | 36 | 2,611.9 | 13.4% | 77.8% | 943.5 | 100.0% | 100.0% |
| `trailing_mean` | 7d | 36 | 2,644.0 | 21.9% | 83.3% | 892.3 | 100.0% | 100.0% |
| `hybrid` (`chronos-bolt-small`) | 30d | 36 | 7,597.3 | 12.2% | 83.3% | 2,590.4 | 100.0% | 100.0% |
| `trailing_mean` | 30d | 36 | 7,081.5 | 12.3% | 88.9% | 2,368.4 | 100.0% | 100.0% |

  - By persona, `hybrid` improves endpoint MAE and spend WAPE on `steady` (7d MAE ₹1,316 vs ₹1,494;
    30d MAE ₹5,222 vs ₹5,282), `rising` at 30 days (MAE ₹2,551 vs ₹4,075; WAPE 2.8% vs 7.0%), and
    `frugal_noisy` (7d MAE ₹2,918 vs ₹3,853; 30d MAE ₹6,545 vs ₹9,463), while `trailing_mean` has
    lower 30-day error on abrupt regime jumps (`festive`, `lumpy`). Paired bootstrap 95% confidence
    intervals across the 36 cases straddle zero on pooled MAE and pinball loss; empirical 80%
    interval coverage with `RHO = 0.5` is 77.8% (7d) and 83.3% (30d) against the 80% target.
- **Content-free realized accuracy (`ForecastAccuracy` in `backend/app/storage.py`):** when an
  owner creates a forecast after their `as_of` date has moved past an earlier forecast's end date,
  `score_past_forecasts()` compares the earlier forecast's everyday spending against actual
  non-recurring expenses over that period and stores only a hashed `run_ref`, `method`, `model`,
  `horizon_days`, `spend_wape` and `inside_band` (no amounts, dates or identity).
- **Admin > Forecast accuracy (`OpsForecast` in `frontend/src/App.jsx`, `/ops/v1/forecast-accuracy`):**
  shows model status, grouped bar comparisons for balance error, range reliability (with 80% target
  marker), spending WAPE and pinball loss, per-persona breakdown, realized accuracy table, and an
  audit-logged **Run backtest** button (`POST /ops/v1/forecast-accuracy/run`).

## 3. DeepEval evaluation harness and Coordinator supervisor (`evals/deepeval/`, `backend/app/supervisor.py`)

- **DeepEval harness (`evals/deepeval/`, `evals/goldens/*.json`):** six suites (`coordinator`,
  `categorizer`, `forecaster`, `risk`, `goals_coach`, `system`) with `dev` and `holdout` splits
  tested offline in `tests/backend/test_goldens.py` and live via `.venv-evals` (`requirements-evals.txt`).
  Results are written to `evals/results/deepeval/<run_id>/` and browsed or triggered from
  **Admin > Evaluations** (`GET /ops/v1/evals`, `POST /ops/v1/evals/run`, `POST /ops/v1/evals/cancel`).
- **Coordinator as deterministic supervisor (`backend/app/supervisor.py`):** every reply from
  `POST /api/v1/assistant/message` is checked by `supervisor.verify()` before being returned to the
  owner:
  - Blocks unknown routes, empty/overlong text, named securities (`NAMED_SECURITY`), guarantees
    (`GUARANTEE`), unhedged affordability verdicts (`VERDICT`), and leaked phone numbers/emails/UPI
    handles (`PERSONAL`).
  - Verifies that any confirm-card amount (`CARD_AMOUNTS`) was actually stated in the owner's
    message (`card_amount_not_said`), that `confirm_flow` signs match the direction in the owner's
    words (`card_direction_contradicts`), and that every rupee figure in grounded explanations
    appears in the server-computed facts or the owner's question (`ungrounded_amount`).
  - Failed replies are replaced by a deterministic fallback or `SAFE_REPLY`, counted in Prometheus
    (`finpilot_supervisor_blocks_total`), and logged as `fina_blocked` in admin activity.
- **Fixes from the baseline evaluation (`evals/results/deepeval-2026-10-27.md`, verified in
  `20261027-235621-supervisor`):**
  - Coordinator keeps the fuller deterministic goal name when the model shortens it (e.g. `"Goa trip"`
    over `"Trip"`) and corrects `upcoming_flow` signs from the owner's words (`"pay 28000 rent"` ->
    `-2800000`).
  - `MEANINGS["balance_at_horizon_end_without_purchase"]` now describes the expected balance on the
    last forecast day (middle of the likely range) without confusing the faithfulness judge when no
    purchase is present.

## 4. Opt-in contribution sharing (`Preference`, `Contribution`)

- **Owner preferences (`GET/PUT /api/v1/preferences`, `DELETE /api/v1/preferences/contributions`):**
  everything is off by default (`share_categories=False`, `share_questions=False`). When an owner
  opts in on the **Preferences** page, confirmed category labels and routed chat questions are
  de-identified (UPI handles, reference numbers, phone numbers, emails and card digits redacted;
  amounts rounded to ₹10; actor stored only as pseudonymous `u-xxxxxx`) and saved in
  `contributions`. Opting in for the first time awards FinaPoints; turning a toggle off or deleting
  contributions immediately removes the owner's shared rows.
- **Admin review and export (`GET /ops/v1/contributions`, `GET /ops/v1/contributions/export`,
  `DELETE /ops/v1/contributions/{id}`):** operators can inspect de-identified items, reject rows,
  or export candidate golden cases (`needs-review`) for `categorizer` and `coordinator` with audit
  logging.

## 5. Data, security and acceptance evidence

- **Schema and PostgreSQL isolation (`backend/app/storage.py`, `backend/migrations/001_tenant_rls.sql`):**
  - `preferences` is tenant-owned and protected by PostgreSQL row-level security (`tenant_isolation`).
  - `forecast_accuracy` and `contributions` hold only de-identified or aggregate records;
    `finpilot_app` can read/write `contributions` and read/insert `forecast_accuracy`, while
    `finpilot_ops` can read `forecast_accuracy` and read/delete `contributions` without access to
    any customer table.
- **Verification (`2026-10-28`):**
  - `pytest tests/backend -q`: **177 passed, 3 skipped** (PostgreSQL tests skipped without a live
    PostgreSQL URL), covering hybrid forecast bands and probabilities, what-if shifts, missing/broken
    model fallback, realized forecast scoring, supervisor blocking rules, and opt-in preference
    sharing/deletion (`tests/backend/test_hybrid.py`, `tests/backend/test_supervisor.py`,
    `tests/backend/test_preferences.py`, `tests/backend/test_admin.py`).
  - Backtest run `20261028-calibrated` completed across all 6 personas × 6 cutoffs × 2 horizons
    with `amazon/chronos-bolt-small` on CPU.

