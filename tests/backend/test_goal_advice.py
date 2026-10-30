import json
from datetime import date, timedelta

import httpx
from fastapi.testclient import TestClient

from backend.app.advisor import goal_levers, subscriptions
from backend.app.config import Settings
from backend.app.goals import DAYS_PER_MONTH, progress
from backend.app.main import create_app
from backend.app.storage import Policy
from conftest import ORIGIN, register

PROFILE = {"currency": "INR", "timezone": "Asia/Kolkata", "as_of": "2026-10-18", "coverage_start": "2026-07-21",
    "coverage_confirmed": True, "opening_balance_minor": 5_000_000, "reserve_minor": 1_000_000,
    "scheduled_flows": [{"date": "2026-11-01", "label": "Salary", "amount_minor": 6_000_000}]}
START = date(2026, 7, 21)
TXS = ([{"date": (START + timedelta(days=d)).isoformat(), "description": "Swiggy order", "amount_minor": -40_000,
         "currency": "INR", "kind": "expense", "category": "food delivery"} for d in range(90)]
    + [{"date": day, "description": "NETFLIX.COM", "amount_minor": -64_900, "currency": "INR", "kind": "expense",
        "category": "entertainment"} for day in ("2026-08-10", "2026-09-10", "2026-10-10")]
    + [{"date": day, "description": "Flat rent", "amount_minor": -2_500_000, "currency": "INR", "kind": "expense",
        "category": "rent", "recurring": True} for day in ("2026-08-03", "2026-09-03", "2026-10-03")]
    + [{"date": (START + timedelta(days=d)).isoformat(), "description": "DMart", "amount_minor": -150_000, "currency": "INR",
        "kind": "expense", "category": "groceries"} for d in range(0, 90, 7)])
GOAL = {"name": "Trip to Goa", "target_minor": 6_000_000, "target_date": "2027-02-28", "saved_minor": 1_000_000, "status": "active"}


def test_subscriptions_and_goal_options_are_exact():
    assert subscriptions(TXS, PROFILE["as_of"]) == [{"name": "Netflix Com", "monthly": 64_900}]  # not rent, groceries or Swiggy
    run = {"forecast": {"timeline": [{"cumulative_flow_minor": 0}] * 29 + [{"cumulative_flow_minor": 300_000}]}}
    prog = progress(GOAL, PROFILE["as_of"], run)
    options = {o["ref"]: o for o in goal_levers(GOAL, prog, PROFILE, TXS)}
    assert [o for o in options] == ["s1", "c1"] and options["c1"]["facts"]["category"] == "food delivery"
    sub = options["s1"]["facts"]
    pace, need, remaining = prog["pace_per_month_minor"], prog["needed_per_month_minor"], prog["remaining_minor"]
    assert sub["monthly_cost"] == 64_900 and sub["share_of_need_percent"] == round(64_900 * 100 / need)
    assert sub["yearly_saving"] == 64_900 * 12
    assert sub["months_sooner"] == round(remaining / pace - remaining / (pace + 64_900)) >= 1 and "reach_date" in sub
    fast = {"forecast": {"timeline": [{"cumulative_flow_minor": 0}] * 29 + [{"cumulative_flow_minor": 3_000_000}]}}
    quick = {o["ref"]: o for o in goal_levers(GOAL, progress(GOAL, PROFILE["as_of"], fast), PROFILE, TXS)}
    assert "reach_date" not in quick["s1"]["facts"] and "months_sooner" not in quick["s1"]["facts"]  # under a month: no date
    no_pace = {o["ref"]: o for o in goal_levers(GOAL, progress(GOAL, PROFILE["as_of"], None), PROFILE, TXS)}
    assert no_pace["s1"]["facts"]["reach_date"] == (date(2026, 10, 18) + timedelta(days=round(remaining / 64_900 * DAYS_PER_MONTH))).isoformat()
    cut = options["c1"]["facts"]
    monthly = round(40_000 * 90 / 90 * DAYS_PER_MONTH)
    assert cut["monthly_spend"] == monthly and cut["saving_per_month"] == round(monthly * 0.25)
    assert cut["months_sooner"] == round(remaining / pace - remaining / (pace + cut["saving_per_month"]))
    assert goal_levers({**GOAL, "saved_minor": 6_000_000}, progress({**GOAL, "saved_minor": 6_000_000}, PROFILE["as_of"], run), PROFILE, TXS) == []


def ready(client, app, mode):
    register(client)
    client.put("/api/v1/profile", json={"profile": PROFILE, "expected_version": 0})
    csv = "date,description,amount_minor,currency,kind,category,recurring\n" + "\n".join(
        f"{t['date']},{t['description']},{t['amount_minor']},INR,expense,{t['category']},{'true' if t.get('recurring') else 'false'}" for t in TXS)
    p = client.post("/api/v1/imports", json={"csv_text": csv}).json()
    client.post(f"/api/v1/imports/{p['id']}/confirm", json={"expected_version": p["version"]})
    client.post("/api/v1/forecasts", json={"horizon_days": 30})
    with app.state.sessions() as db:
        db.get(Policy, 1).mode = mode
        db.commit()
    goal = {k: v for k, v in GOAL.items() if k != "status"}
    return client.post("/api/v1/goals", json=goal).json()["id"]


def test_paused_and_calculated_goal_advice(client, app):
    goal_id = ready(client, app, "AI_PAUSED")
    assert client.post(f"/api/v1/goals/{goal_id}/advice").json()["paused"] is True
    with app.state.sessions() as db:
        db.get(Policy, 1).mode = "RUNNING"
        db.commit()
    reply = client.post(f"/api/v1/goals/{goal_id}/advice").json()
    assert reply["source"] == "calculation" and [i["ref"] for i in reply["items"]] == ["s1", "c1"]
    assert reply["items"][0]["text"].startswith("Pausing Netflix Com would free up ₹649 a month, or ₹7,788 a year")
    assert "Trip to Goa needs each month" in reply["items"][0]["text"]


def test_model_goal_advice_is_rendered_after_risk_approval(tmp_path):
    seen = []
    def handler(request):
        body = json.loads(request.content)
        ctx = json.loads(body["messages"][1]["content"])
        seen.append((body["model"], ctx))
        env = {"prompt_version": ctx["prompt_version"], "snapshot_ref": ctx["snapshot_ref"], "status": "completed",
            "evidence_refs": [], "questions": [], "limitations": []}
        if body["model"] == "test-risk":
            reply = {**env, "agent": "risk", "payload": {"verdict": "approve", "checks": [{"rule": "options_match", "result": "pass", "reason": None}], "reason": "ok"}}
        else:
            reply = {**env, "agent": "advisor", "payload": {"items": [{"ref": "s1", "title": "Pause a subscription",
                "text": "If you pause {s1_subscription}, that's {s1_monthly_cost} a month toward {s1_goal_name}, about {s1_share_of_need_percent} of what it needs."}]}}
        return httpx.Response(200, json={"id": "m", "object": "chat.completion", "created": 0, "model": body["model"],
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(reply)}}]})
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'ga.db'}", environment="test", mesh_api_key="k",
        mesh_model="test-default", mesh_risk_model="test-risk"), mesh_transport=httpx.MockTransport(handler))
    with TestClient(app, headers=ORIGIN) as client:
        goal_id = ready(client, app, "RUNNING")
        reply = client.post(f"/api/v1/goals/{goal_id}/advice").json()
        assert reply["source"] == "ai" and reply["risk_reviewed"] is True
        assert reply["items"][0]["text"].startswith("If you pause Netflix Com, that's ₹649 a month toward Trip to Goa, about ")
        advisor_ctx = next(c for m, c in seen if m == "test-default" and "options" in c)
        assert advisor_ctx["situation"]["goal"] == "Trip to Goa"
        assert "Swiggy" not in json.dumps(advisor_ctx) and "NETFLIX.COM" not in json.dumps(advisor_ctx)
