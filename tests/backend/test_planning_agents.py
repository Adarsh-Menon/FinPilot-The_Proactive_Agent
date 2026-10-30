import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import invest
from backend.app.debts import analyse, payoff
from backend.app.plans import emergency_plan, recovery, waterfall
from conftest import ORIGIN, import_history, register, setup_profile

AS_OF = "2026-10-18"


def amortize(balance, rate, payment):
    months, interest, r = 0, 0, rate / 100 / 12
    while balance > 0:
        charge = balance * r
        interest += charge
        balance = balance + charge - payment
        months += 1
    return months, interest


def test_payoff_matches_month_by_month_amortization():
    months, interest = payoff(10_000_000, 18, 500_000)
    m2, i2 = amortize(10_000_000, 18, 500_000)
    assert months == m2 and abs(interest - i2) < 600_000   # closed form vs whole-month loop: same months, close interest
    assert payoff(1_000_000, 24, 20_000) == (None, None)    # payment below the monthly interest never clears it
    assert payoff(1_200_000, 0, 100_000) == (12, 0)


def test_debt_agent_detects_obligations_and_offers_paying_the_costliest_first():
    profile = {"as_of": AS_OF, "scheduled_flows": [{"date": "2026-11-01", "label": "Salary", "amount_minor": 10_000_000}]}
    ledger = [{"date": d, "description": "HDFC car loan EMI", "amount_minor": -1_500_000, "kind": "repayment", "category": "loan"}
        for d in ("2026-08-05", "2026-09-05", "2026-10-05")]
    ledger += [{"date": d, "description": "Flat rent", "amount_minor": -2_500_000, "kind": "expense", "category": "rent"}
        for d in ("2026-09-03", "2026-10-03")]
    debts = [{"id": "d", "name": "Credit card", "kind": "credit_card", "balance_minor": 8_000_000, "rate_percent": 36, "monthly_minor": 500_000},
        {"id": "e", "name": "Education loan", "kind": "loan", "balance_minor": 40_000_000, "rate_percent": 9, "monthly_minor": 900_000}]
    view = analyse(debts, profile, ledger)
    assert {d["name"] for d in view["detected"]} == {"Hdfc Car Loan Emi", "Flat Rent"}
    assert view["obligations_minor"] == 500_000 + 900_000 + 1_500_000 + 2_500_000 and view["ratio_percent"] == 54
    assert view["pressure"] == "high" and view["costliest"] == "Credit card"
    option = view["options"][0]["facts"]
    assert option["debt_name"] == "Credit card" and option["months_sooner"] > 0 and option["interest_saved"] > 0
    assert [g["name"] for g in view["suggested_goals"]] == ["Pay off Credit card", "Pay off Education loan"]


def goal(i, saved, target, date, status="active"):
    return {"id": f"g{i}", "name": f"Goal {i}", "saved_minor": saved, "target_minor": target, "target_date": date, "status": status}


def test_waterfall_and_emergency_reroute_re_estimate_every_goal():
    goals = [goal(1, 2_000_000, 5_000_000, "2026-12-31"), goal(2, 6_000_000, 10_000_000, "2027-06-30")]
    plan = waterfall(goals, 1_000_000, AS_OF)
    assert plan["g1"]["reach_date"] < plan["g2"]["reach_date"]          # the earlier goal is funded first
    reroute = emergency_plan(goals, 4_000_000, 1_000_000, AS_OF)
    taken = {g["id"]: g["taken"] for g in reroute["goals"]}
    assert taken == {"g1": 1_000_000, "g2": 3_000_000} and reroute["from_cash"] == 0   # in proportion to what each holds
    assert all(g["reach_after"] > g["reach_before"] for g in reroute["goals"])
    big = emergency_plan(goals, 10_000_000, 1_000_000, AS_OF)
    assert big["from_goals"] == 8_000_000 and big["from_cash"] == 2_000_000 and all(g["saved_after"] == 0 for g in big["goals"])


def test_recovery_routes_only_for_goals_that_fell_behind():
    g = goal(1, 1_000_000, 10_000_000, "2027-04-18")
    behind = {"status": "stretch", "pace_per_month_minor": 500_000, "needed_per_month_minor": 1_500_000, "remaining_minor": 9_000_000, "days_left": 182}
    cut = [{"kind": "cancel_subscription", "summary": "Pause Netflix", "facts": {"monthly_cost": 64_900}}]
    routes = {r["kind"]: r for r in recovery(g, behind, AS_OF, cut)}
    assert routes["catch_up"]["facts"]["gap_per_month"] == 1_000_000 and routes["catch_up"]["facts"]["covered_by_cuts"] == 64_900
    assert routes["new_date"]["facts"]["reach_date"] > "2027-04-18" and routes["smaller_target"]["facts"]["milestone"] < 10_000_000
    assert all(r["why"] for r in routes.values())
    assert recovery(g, {**behind, "status": "on_track"}, AS_OF, cut) == []
    shrinking = {**behind, "status": "off_track", "pace_per_month_minor": -1_500_000}   # ₹649 of cuts can't outrun a ₹15,000 monthly fall
    routes = {r["kind"]: r for r in recovery(g, shrinking, AS_OF, cut)}
    assert set(routes) == {"catch_up", "fix_shortfall"} and routes["fix_shortfall"]["facts"]["still_short"] == 1_500_000 - 64_900
    slow = {r["kind"]: r for r in recovery(g, {**behind, "pace_per_month_minor": 50_000}, AS_OF, [])}
    assert slow["new_date"]["facts"]["far"] and "years away" in slow["new_date"]["why"]


def test_investment_readiness_never_names_securities():
    profile = {"as_of": AS_OF, "opening_balance_minor": 30_000_000, "scheduled_flows": []}
    ledger = [{"date": "2026-10-03", "description": "Flat rent", "amount_minor": -2_500_000, "kind": "expense", "category": "rent"}]
    costly = [{"name": "Credit card", "kind": "credit_card", "balance_minor": 5_000_000, "rate_percent": 36, "monthly_minor": 300_000}]
    blocked = invest.readiness(profile, ledger, [], costly, 4_000_000)
    assert not blocked["ready"] and blocked["first_fix"] == "costly_debt" and blocked["sip_minor"] is None
    ready = invest.readiness(profile, ledger, [], [], 4_000_000)
    assert ready["ready"] and ready["sip_minor"] == 2_000_000             # half the surplus, in ₹500 steps
    assert [o["kind"] for o in invest.options_for(ready, profile)] == ["start_sip"]
    names = " ".join(e["name"] for e in ready["education"]).lower()
    assert "fund" in names and not any(word in names for word in ("hdfc", "sbi", "nippon", "reliance", "tata", "buy"))


def test_headlines_parse_rss_and_skip_unsafe_links(monkeypatch):
    rss = b"""<rss><channel><title>Markets</title><item><title>Nifty ends higher</title><link>https://example.com/a</link></item>
        <item><title>Unsafe</title><link>javascript:alert(1)</link></item>
        <item><title>Defence stocks to buy, see up to 66% upside</title><link>https://example.com/b</link></item></channel></rss>"""
    monkeypatch.setattr(invest.httpx, "get", lambda *a, **k: httpx.Response(200, content=rss))
    invest._news_cache.clear()
    items = invest.headlines(["https://feed.example/rss", "http://insecure.example/rss"])
    assert items == [{"title": "Nifty ends higher", "link": "https://example.com/a", "source": "Markets", "published": ""}]


def test_endpoints_debts_reroute_and_invest(client, app):
    register(client); setup_profile(client); import_history(client)
    client.post("/api/v1/forecasts", json={"horizon_days": 30})
    no_goals = client.post("/api/v1/assistant/message", json={"question": "I had to use 10000 for an emergency"}).json()
    assert no_goals["route"] == "emergency" and no_goals["action"] == {"type": "go_goals"}   # nothing to re-plan yet
    debt = client.post("/api/v1/debts", json={"name": "Credit card", "kind": "credit_card", "balance_minor": 500_000, "rate_percent": 36, "monthly_minor": 50_000}).json()
    assert client.get("/api/v1/debts").json()["debts"][0]["name"] == "Credit card"
    assert client.patch(f"/api/v1/debts/{debt['id']}", json={"name": "Other", "monthly_minor": 1}).status_code == 422   # owed and rate only
    cleared = client.patch(f"/api/v1/debts/{debt['id']}", json={"balance_minor": 0, "rate_percent": 30}).json()
    assert cleared["balance_minor"] == 0 and cleared["rate_percent"] == 30 and cleared["name"] == "Credit card"
    assert [u["id"] for u in cleared["unlocked"]] == ["debt_free"]
    client.patch(f"/api/v1/debts/{debt['id']}", json={"balance_minor": 500_000})
    assert "debts" in client.get("/api/v1/outlook").json()
    g1 = client.post("/api/v1/goals", json={"name": "Trip", "target_minor": 50_000, "target_date": "2027-01-31", "saved_minor": 20_000}).json()["id"]
    preview = client.post("/api/v1/goals/reroute", json={"amount_minor": 10_000}).json()
    assert preview["goals"][0]["saved_after"] == 10_000
    assert client.get("/api/v1/goals").json()["goals"][0]["saved_minor"] == 20_000           # preview changes nothing
    client.post("/api/v1/goals/reroute/apply", json={"amount_minor": 10_000, "create_rebuild_goal": True})
    goals = client.get("/api/v1/goals").json()["goals"]
    assert goals[0]["saved_minor"] == 10_000 and goals[1]["name"] == "Rebuild emergency fund" and "plan" in goals[0]
    view = client.get("/api/v1/invest").json()
    assert view["first_fix"] in {"emergency_fund", "costly_debt", "surplus"} and view["news"] == []
    with TestClient(app, headers=ORIGIN) as other:
        register(other, "customer_b")
        assert other.delete(f"/api/v1/debts/{debt['id']}").status_code == 404
    chat = client.post("/api/v1/assistant/message", json={"question": "I had to use 10000 for an emergency"}).json()
    assert chat["action"] == {"type": "go_reroute", "amount_minor": 1_000_000}
