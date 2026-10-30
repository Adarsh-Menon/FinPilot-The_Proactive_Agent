"""Hybrid forecaster: scheduled money stays exact; everyday spending comes from a (stand-in) foundation model."""
from datetime import date, timedelta

import pytest

from backend.app import finance, hybrid
from backend.app.assistant import build_facts

PROFILE = {"currency": "INR", "timezone": "Asia/Kolkata", "as_of": "2026-10-18", "coverage_start": "2026-08-20",
    "coverage_confirmed": True, "opening_balance_minor": 5_000_000, "reserve_minor": 1_000_000,
    "scheduled_flows": [{"date": "2026-11-01", "label": "Salary", "amount_minor": 8_500_000},
        {"date": "2026-11-02", "label": "Rent", "amount_minor": -2_800_000}]}
LEDGER = [{"id": f"t{d}", "date": (date(2026, 8, 20) + timedelta(days=d)).isoformat(), "description": "Cafe", "amount_minor": -160_000,
    "kind": "expense", "category": "food", "recurring": False} for d in range(60)]


class StandIn:
    """A fixed spending forecast: ₹1,000-₹2,200 a day, median ₹1,600."""
    name = "stand-in"

    def __call__(self, series, horizon):
        return [[100_000, 120_000, 135_000, 150_000, 160_000, 170_000, 185_000, 200_000, 220_000]] * horizon


@pytest.fixture
def stand_in():
    finance.set_predictor(StandIn())
    yield
    finance.set_predictor(None)


def run():
    return finance.graph.invoke({"profile": PROFILE, "transactions": LEDGER, "horizon": 30})


def test_hybrid_forecast_has_a_range_and_chances(stand_in):
    result = run()
    f = result["forecast"]
    assert f["method"] == "hybrid" and f["model"] == "stand-in" and f["uncertainty_method"] == "simulated-quantiles"
    for row in f["timeline"]:
        assert row["cash_low_minor"] <= row["cash_minor"] <= row["cash_high_minor"]
    nov1 = next(r for r in f["timeline"] if r["date"] == "2026-11-01")
    assert nov1["scheduled_minor"] == 8_500_000                                 # known money stays exact
    assert 0 <= f["below_reserve_probability"] <= 1 and 0 <= f["below_zero_probability"] <= f["below_reserve_probability"]
    assert result["checks"]["below_reserve_probability"] == f["below_reserve_probability"]
    assert run()["forecast"]["timeline"] == f["timeline"]                       # same inputs, same bands
    facts = build_facts({**result}, PROFILE)["facts"]
    assert facts["balance_at_horizon_end_likely_low"]["value"] <= facts["balance_at_horizon_end_likely_high"]["value"]
    assert "chance_below_reserve_percent" in facts


def test_what_if_shifts_the_band_and_raises_the_chance(stand_in):
    f = run()
    plain = finance.simulate(f, 1, "2026-10-25")
    big = finance.simulate(f, 3_000_000, "2026-10-25")
    assert big["timeline"][-1]["cash_minor"] < plain["timeline"][-1]["cash_minor"]
    assert big["below_reserve_probability"] >= plain["below_reserve_probability"]


def test_without_a_model_the_baseline_is_used():
    f = run()["forecast"]
    assert f["method"] == "trailing_mean" and "cash_low_minor" not in f["timeline"][0]


def test_model_failure_falls_back_with_a_note():
    class Broken:
        name = "broken"
        def __call__(self, series, horizon):
            raise RuntimeError("model crashed")
    finance.set_predictor(Broken())
    try:
        f = run()["forecast"]
    finally:
        finance.set_predictor(None)
    assert f["method"] == "trailing_mean" and "RuntimeError" in f["limitations"][0]


def test_simulation_respects_its_quantiles():
    # Each day spends 0 or about 1,000 with roughly even odds, so ten days end near -5,000 and almost never at zero.
    bands = hybrid.simulate(0, [0] * 10, [[0, 0, 0, 0, 1000, 1000, 1000, 1000, 1000]] * 10, reserve=-5000)
    low, mid, high = bands["balance_q"][-1][0], bands["balance_q"][-1][4], bands["balance_q"][-1][8]
    assert low <= mid <= high and -6500 <= mid <= -3500 and bands["below_zero_probability"] > 0.95
