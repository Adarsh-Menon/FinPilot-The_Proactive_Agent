"""Forecast backtest: how accurate is each forecasting method on synthetic people whose future is known?

Rolling-origin evaluation: for each synthetic persona, cut the history at several dates, forecast the next 7 and 30
days using only what was known at the cut, and compare with what actually happened. Scheduled income and bills are
known to every method, so the comparison isolates everyday spending, the part each method has to forecast.

Methods:
    trailing_mean   the original forecaster: average daily spending, with a range made by resampling past days
    hybrid          everyday spending from the frozen time-series foundation model (Chronos-Bolt), simulated futures

Metrics, per method and horizon:
    end_error       mean absolute error of the balance at the horizon end, in whole currency units
    spend_wape      |forecast spending - actual spending| / actual spending, as a percentage (lower is better)
    coverage_80     share of cases where the actual end balance fell inside the 10th-90th percentile range (target 80%)
    pinball         mean pinball loss at the 10th, 50th and 90th percentiles of the end balance (lower is better)
    dip_recall      of the cases where the balance really dipped below the reserve, the share the method warned about
    dip_precision   of the cases the method warned about (chance above 50%), the share that really dipped

Run: python -m backend.app.backtest [--model amazon/chronos-bolt-small] [--run-id ID]
Results go to evals/results/forecast/<run id>.json, shown on Admin > Forecast accuracy.
"""
import argparse
import json
import math
import random
import statistics
import subprocess
from datetime import datetime
from pathlib import Path

from .config import ROOT
from .hybrid import QUANTILES, FoundationModel, simulate

DAYS, HORIZONS, CUTOFFS, HISTORY = 240, (7, 30), (120, 135, 150, 165, 180, 195), 90
RESULTS = ROOT / "evals" / "results" / "forecast"
PERSONAS = {
    "steady": "Salary on the 1st, rent on the 2nd, steady weekday spending with busier weekends",
    "lumpy": "Steady spending plus occasional large purchases (electronics, travel)",
    "rising": "Everyday spending creeping up about 1% a week",
    "festive": "A festive-season month with spending about 60% higher",
    "frugal_noisy": "Low spending that varies a lot from day to day",
    "emi_heavy": "Rent plus several EMIs, little room left each month",
}


def persona(name, seed):
    """One synthetic person's daily spending and scheduled flows over DAYS days.

    Args:
        name: a key of PERSONAS.
        seed: random seed (each persona gets its own).

    Returns:
        Dict with spend (daily everyday spending, minor units), scheduled (known money in/out per day), opening (balance on
        day 0) and reserve.
    """
    rng = random.Random(seed)
    base = {"frugal_noisy": 45_000, "emi_heavy": 90_000}.get(name, 120_000)
    spend, scheduled = [], []
    for d in range(DAYS):
        weekend = d % 7 in (5, 6)
        level = base * (1.6 if weekend else 1.0)
        if name == "rising":
            level *= 1.01 ** (d / 7)
        if name == "festive" and 150 <= d < 180:
            level *= 1.6
        noise = rng.lognormvariate(0, 0.9 if name == "frugal_noisy" else 0.35)
        amount = level * noise
        if name == "lumpy" and rng.random() < 0.04:
            amount += rng.choice([300_000, 650_000, 1_500_000])
        spend.append(round(amount))
        day_of_month = d % 30
        flow = 0
        if day_of_month == 0:
            flow += {"emi_heavy": 7_000_000}.get(name, 8_500_000)
        if day_of_month == 1:
            flow -= {"frugal_noisy": 1_500_000}.get(name, 2_800_000)
        if name == "emi_heavy" and day_of_month in (4, 9, 14):
            flow -= 950_000
        scheduled.append(flow)
    return {"spend": spend, "scheduled": scheduled, "opening": 5_000_000, "reserve": 1_000_000}


def empirical_quantiles(history):
    """The nine deciles of past daily spending, used as every future day's range (the baseline's band)."""
    ordered = sorted(history)
    return [ordered[min(int(q * len(ordered)), len(ordered) - 1)] for q in QUANTILES]


def pinball(actual, quantiles):
    """Mean pinball loss of the 10th, 50th and 90th percentiles against the actual value."""
    loss = 0.0
    for level, value in ((0.1, quantiles[0]), (0.5, quantiles[4]), (0.9, quantiles[8])):
        loss += max(level * (actual - value), (level - 1) * (actual - value))
    return loss / 3


def backtest(model=None, progress=print, rho=None):
    """Run every persona, cut-off and horizon for both methods.

    Args:
        model: a FoundationModel (or any callable series, horizon -> per-day quantiles); None skips the hybrid method.
        progress: called with a short status line per persona.

    Returns:
        Dict with summary (per method and horizon), personas (per persona, method and horizon) and cases.
    """
    cases = []
    for index, name in enumerate(PERSONAS):
        p = persona(name, seed=1000 + index)
        for cut in CUTOFFS:
            history = p["spend"][cut - HISTORY:cut]
            balance_at_cut = p["opening"] + sum(p["scheduled"][:cut]) - sum(p["spend"][:cut])
            quantiles = {"trailing_mean": [empirical_quantiles(history)] * max(HORIZONS)}
            if model is not None:
                q = model(p["spend"][max(0, cut - 512):cut], max(HORIZONS))
                if q:
                    quantiles["hybrid"] = q
            for method, spend_q in quantiles.items():
                for h in HORIZONS:
                    scheduled = p["scheduled"][cut:cut + h]
                    actual_spend = sum(p["spend"][cut:cut + h])
                    path, low = balance_at_cut, balance_at_cut
                    for d in range(h):
                        path += scheduled[d] - p["spend"][cut + d]
                        low = min(low, path)
                    bands = simulate(balance_at_cut, scheduled, spend_q[:h], p["reserve"], seed=cut * 31 + h, rho=rho)
                    end_q = bands["balance_q"][-1]
                    if method == "trailing_mean":
                        point = balance_at_cut + sum(scheduled) - round(statistics.fmean(history)) * h
                        forecast_spend = round(statistics.fmean(history)) * h
                    else:
                        point = end_q[4]
                        forecast_spend = round(sum(sum(q) / len(q) for q in spend_q[:h]))
                    cases.append({"persona": name, "cutoff": cut, "horizon": h, "method": method,
                        "end_error": abs(point - path) / 100, "spend_wape": abs(forecast_spend - actual_spend) * 100 / max(actual_spend, 1),
                        "inside_80": end_q[0] <= path <= end_q[8], "pinball": pinball(path, end_q) / 100,
                        "dipped": low < p["reserve"], "warned": (bands["below_reserve_probability"] or 0) > 0.5})
        progress(f"{name}: done")

    def summarise(rows):
        dipped = [r for r in rows if r["dipped"]]
        warned = [r for r in rows if r["warned"]]
        return {"cases": len(rows), "end_error": round(statistics.fmean(r["end_error"] for r in rows), 1),
            "spend_wape": round(statistics.median(r["spend_wape"] for r in rows), 1),
            "coverage_80": round(sum(r["inside_80"] for r in rows) * 100 / len(rows), 1),
            "pinball": round(statistics.fmean(r["pinball"] for r in rows), 1),
            "dip_recall": round(sum(r["warned"] for r in dipped) * 100 / len(dipped), 1) if dipped else None,
            "dip_precision": round(sum(r["dipped"] for r in warned) * 100 / len(warned), 1) if warned else None}

    methods = sorted({c["method"] for c in cases})
    summary = [{"method": m, "horizon": h, **summarise([c for c in cases if c["method"] == m and c["horizon"] == h])}
        for m in methods for h in HORIZONS]
    personas = [{"persona": n, "method": m, "horizon": h, **summarise([c for c in cases if c["persona"] == n and c["method"] == m
        and c["horizon"] == h])} for n in PERSONAS for m in methods for h in HORIZONS]
    return {"summary": summary, "personas": personas, "cases": cases}


def main():
    """Command-line entry: run the backtest and write evals/results/forecast/<run id>.json."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="amazon/chronos-bolt-small", help="Chronos-Bolt model id; empty for baseline only")
    parser.add_argument("--run-id", default=datetime.now().strftime("%Y%m%d-%H%M%S"))
    args = parser.parse_args()
    started = datetime.now().isoformat(timespec="seconds")
    model = FoundationModel(args.model) if args.model else None
    result = backtest(model, progress=lambda line: print(line, flush=True))
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        commit = None
    meta = {"run_id": args.run_id, "started_at": started, "finished_at": datetime.now().isoformat(timespec="seconds"),
        "model": args.model or None, "model_error": model.error if model else None, "personas": PERSONAS,
        "cutoffs": list(CUTOFFS), "horizons": list(HORIZONS), "history_days": HISTORY, "git_commit": commit}
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{args.run_id}.json").write_text(json.dumps({"meta": meta, **result}, indent=1), encoding="utf-8")
    for row in result["summary"]:
        print(f"{row['method']:>14} {row['horizon']:>2}d  error ₹{row['end_error']:,.0f}  spend {row['spend_wape']}%  "
            f"coverage {row['coverage_80']}%  pinball {row['pinball']:,.0f}", flush=True)


if __name__ == "__main__":
    main()
