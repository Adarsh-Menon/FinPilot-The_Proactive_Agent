"""Hybrid forecasting: a frozen time-series foundation model for everyday spending, exact maths for everything known.

The owner's scheduled income and bills are known and stay exact. Only the uncertain part, day-to-day spending, is
forecast by a pretrained time-series foundation model (Amazon Chronos-Bolt, weights frozen, no fine-tuning) as nine
quantiles per day. Possible futures are simulated from those quantiles to give a likely balance range and the chance
of dipping below the reserve or zero. A language model never produces these numbers; Fina only explains them.

The model is optional: without `chronos-forecasting` installed, or if it fails, the forecaster falls back to the
trailing-mean baseline, and says so in the forecast's limitations.
"""
import math
import random
import sys
import threading

QUANTILES = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
PATHS = 1000


class FoundationModel:
    """A lazily loaded, frozen time-series foundation model on CPU.

    Used by: create_app() (installed as the forecaster's spending predictor) and the forecast backtest.
    """

    def __init__(self, model_id="amazon/chronos-bolt-small"):
        """Remember which model to load on first use.

        Args:
            model_id: Hugging Face id of a Chronos-Bolt model (tiny, mini, small or base).
        """
        self.model_id, self._pipe, self._lock, self.error = model_id, None, threading.Lock(), None

    def _pipeline(self):
        """Load the model once (cached weights are loaded offline first; downloaded on first use); None when unavailable."""
        with self._lock:
            if self._pipe is None and self.error is None:
                try:
                    if any(".venv" in p for p in sys.path):
                        sys.path[:] = [p for p in sys.path if "Python311\\Lib\\site-packages" not in p and "Roaming\\Python" not in p]
                    import torch
                    from chronos import BaseChronosPipeline
                    try:
                        self._pipe = BaseChronosPipeline.from_pretrained(
                            self.model_id, device_map="cpu", torch_dtype=torch.float32, local_files_only=True
                        )
                    except Exception:
                        self._pipe = BaseChronosPipeline.from_pretrained(
                            self.model_id, device_map="cpu", torch_dtype=torch.float32
                        )
                except Exception as exc:          # not installed, offline, or a bad model id: use the baseline
                    self.error = f"{type(exc).__name__}: {exc}"[:200]
            return self._pipe

    @property
    def name(self):
        """Short model name for forecasts and reports, e.g. 'chronos-bolt-small'."""
        return self.model_id.split("/")[-1]

    def __call__(self, series, horizon):
        """Forecast daily spending.

        Args:
            series: daily spending in minor units, oldest first (at least 14 days).
            horizon: days to forecast.

        Returns:
            List of `horizon` lists, each the nine quantiles (10%..90%) of that day's spending in minor units (never
            negative), or None when the model is unavailable.
        """
        pipe = self._pipeline()
        if pipe is None:
            return None
        import torch
        with torch.inference_mode():
            quantiles, _ = pipe.predict_quantiles(torch.tensor([[float(x) for x in series]]), prediction_length=horizon,
                quantile_levels=QUANTILES)
        days = []
        for row in quantiles[0].tolist():
            values = sorted(max(0.0, v) for v in row)          # monotone and non-negative
            days.append([round(v) for v in values])
        return days


def _draw(q, u):
    """One day's spending drawn from its nine quantiles by inverse-CDF interpolation.

    Args:
        q: nine quantiles (10%..90%) in minor units.
        u: a uniform random number in (0, 1).

    Returns:
        Spending in minor units; the tails below 10% and above 90% extend linearly to 0 and to 90% + (90% - 50%).
    """
    if u <= 0.1:
        return q[0] * u / 0.1
    if u >= 0.9:
        top = q[8] + (q[8] - q[4])
        return q[8] + (top - q[8]) * (u - 0.9) / 0.1
    i = int((u - 0.1) // 0.1)
    lo, hi = q[i], q[min(i + 1, 8)]
    return lo + (hi - lo) * ((u - 0.1) - 0.1 * i) / 0.1


RHO = 0.5              # how strongly days move together within one simulated future; chosen by the backtest
_INV_SQRT2 = 1.0 / math.sqrt(2.0)


def _uniform(rng, shared, rho):
    """A day's random position (0-1) that partly follows the whole future's shared level.

    Real spending runs in streaks: a busy week tends to stay busy. Mixing a per-future shock into every day keeps the
    sum of many days as uncertain as it really is, instead of good and bad days cancelling out.
    """
    z = rho * shared + math.sqrt(1 - rho * rho) * rng.gauss(0, 1)
    return min(max(0.5 * (1 + math.erf(z * _INV_SQRT2)), 1e-6), 1 - 1e-6)


def simulate(opening, scheduled, spend_q, reserve=None, seed=7, purchase=None, paths=PATHS, rho=None):
    """Simulate possible futures and summarise the balance as bands and chances.

    Args:
        opening: balance today in minor units.
        scheduled: known money in (+) and out (-) for each forecast day, minor units.
        spend_q: per-day spending quantiles from FoundationModel (same length as scheduled).
        reserve: the owner's reserve in minor units, or None.
        seed: random seed, so the same forecast always gives the same bands.
        purchase: optional (day index, amount minor units) for a what-if.
        paths: number of simulated futures.
        rho: day-to-day correlation within a future (default RHO).

    Returns:
        Dict with balance_q (per day, nine quantiles), spend_total_q (nine quantiles of total spending over the
        horizon), below_reserve_probability and below_zero_probability (chance of dipping under at any point).
    """
    rng = random.Random(seed)
    rho = RHO if rho is None else rho
    scale = math.sqrt(1 - rho * rho)
    inv_sqrt2 = _INV_SQRT2
    gauss = rng.gauss
    erf = math.erf
    days = len(scheduled)
    net_scheduled = [scheduled[d] - (purchase[1] if purchase and d == purchase[0] else 0) for d in range(days)]
    columns = [[0] * paths for _ in range(days)]
    totals, under_reserve, under_zero = [], 0, 0
    for p in range(paths):
        balance, spent, low = opening, 0.0, opening
        shared_term = rho * gauss(0, 1)
        for d in range(days):
            z = shared_term + scale * gauss(0, 1)
            u = 0.5 * (1.0 + erf(z * inv_sqrt2))
            if u < 1e-6:
                u = 1e-6
            elif u > 1.0 - 1e-6:
                u = 1.0 - 1e-6
            q = spend_q[d]
            if u <= 0.1:
                spend = q[0] * u / 0.1
            elif u >= 0.9:
                top = q[8] + (q[8] - q[4])
                spend = q[8] + (top - q[8]) * (u - 0.9) / 0.1
            else:
                i = int((u - 0.1) // 0.1)
                lo = q[i]
                hi = q[i + 1 if i < 8 else 8]
                spend = lo + (hi - lo) * ((u - 0.1) - 0.1 * i) / 0.1
            spent += spend
            balance += net_scheduled[d] - spend
            columns[d][p] = balance
            if balance < low:
                low = balance
        totals.append(spent)
        under_reserve += reserve is not None and low < reserve
        under_zero += low < 0
    pick = lambda values: [round(sorted(values)[min(int(q * len(values)), len(values) - 1)]) for q in QUANTILES]
    return {"balance_q": [pick(c) for c in columns], "spend_total_q": pick(totals),
        "below_reserve_probability": round(under_reserve / paths, 3) if reserve is not None else None,
        "below_zero_probability": round(under_zero / paths, 3)}
