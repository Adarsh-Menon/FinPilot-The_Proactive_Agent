import json
from datetime import date, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.advisor import levers
from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.storage import Policy
from conftest import ORIGIN, register

PROFILE = {"currency": "INR", "timezone": "Asia/Kolkata", "as_of": "2026-10-18", "coverage_start": "2026-08-20",
    "coverage_confirmed": True, "opening_balance_minor": 100000, "reserve_minor": 80000,
    "scheduled_flows": [{"date": "2026-11-01", "label": "Salary", "amount_minor": 50000}]}
CSV = "\n".join(["date,description,amount_minor,currency,kind,category,recurring"]
    + [f"{date(2026, 8, 20) + timedelta(days=d)},Swiggy,-500,INR,expense,food delivery,false" for d in range(60)]
    + [f"{date(2026, 8, 20) + timedelta(days=d)},DMart,-1000,INR,expense,groceries,false" for d in range(0, 60, 7)]
    + ["2026-09-05,Flat rent,-40000,INR,expense,rent,true", "2026-10-05,Flat rent,-40000,INR,expense,rent,true"])
SCENARIO = {"amount_minor": 30000, "date": "2026-10-25"}


def ready(client, app=None, mode=None):
    register(client)
    client.put("/api/v1/profile", json={"profile": PROFILE, "expected_version": 0})
    p = client.post("/api/v1/imports", json={"csv_text": CSV}).json()
    client.post(f"/api/v1/imports/{p['id']}/confirm", json={"expected_version": p["version"]})
    if app is not None and mode:
        with app.state.sessions() as db:
            db.get(Policy, 1).mode = mode
            db.commit()
    return client.post("/api/v1/forecasts", json={"horizon_days": 30}).json()


def test_options_are_exact_and_never_cut_essentials(client):
    run = ready(client)
    options = {o["ref"]: o for o in levers(run, PROFILE, SCENARIO, [])}
    assert [options["c1"]["facts"]["category"]] == ["food delivery"] and "c2" not in options  # rent, groceries excluded
    monthly = round(30000 / 60 * 30.44)
    assert options["c1"]["facts"]["monthly_spend"] == monthly and options["c1"]["facts"]["saving_per_month"] == round(monthly * 0.25)
    room = min(r["cash_minor"] - 80000 for r in run["forecast"]["timeline"] if r["date"] >= SCENARIO["date"])
    assert options["sm"]["facts"] == {"max_amount": room, "purchase_amount": 30000}
    assert options["mv"]["facts"]["pay_on"] == "2026-11-01"
    assert "pl" not in options  # income is in the plan
    no_income = levers(run, {**PROFILE, "scheduled_flows": []}, None, [])
    assert any(o["kind"] == "complete_plan" for o in no_income)


def advice(client, run, scenario=SCENARIO, question="What could make this work?"):
    return client.post("/api/v1/advisor/advice", json={"baseline_id": run["id"], "question": question, "scenario": scenario}).json()


def test_paused_policy_serves_no_advice(client):
    run = ready(client)  # default policy is AI_PAUSED
    assert advice(client, run) == {"paused": True, "items": [], "source": "policy"}


def test_calculated_options_when_model_is_unavailable(client, app):
    run = ready(client, app, "RUNNING")
    reply = advice(client, run)
    assert reply["source"] == "calculation" and [i["ref"] for i in reply["items"]] == ["c1", "mv", "sm"]
    assert reply["items"][0]["text"].startswith("You spend about ₹")
    assert not any("rent" in i["text"].lower() or "grocer" in i["text"].lower() for i in reply["items"])


def advisor_app(tmp_path, items, verdict="approve", seen=None):
    def handler(request):
        body = json.loads(request.content)
        ctx = json.loads(body["messages"][1]["content"])
        if seen is not None:
            seen.append((body["model"], ctx))
        envelope = {"prompt_version": ctx["prompt_version"], "snapshot_ref": ctx["snapshot_ref"], "status": "completed",
            "evidence_refs": [], "questions": [], "limitations": []}
        if body["model"] == "test-risk":
            reply = {**envelope, "agent": "risk", "payload": {"verdict": verdict, "checks": [{"rule": "options_match",
                "result": "pass" if verdict == "approve" else "fail", "reason": "synthetic"}], "reason": "synthetic"}}
        else:
            reply = {**envelope, "agent": "advisor", "payload": {"items": items}}
        return httpx.Response(200, json={"id": "m", "object": "chat.completion", "created": 0, "model": body["model"],
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(reply)}}]})
    return create_app(Settings(database_url=f"sqlite:///{tmp_path / 'advice.db'}", environment="test",
        mesh_api_key="test-key", mesh_model="test-default", mesh_risk_model="test-risk"), mesh_transport=httpx.MockTransport(handler))


GOOD = [{"ref": "sm", "title": "A smaller amount", "text": "Spending up to {sm_max_amount} instead of {sm_purchase_amount} keeps your reserve safe."}]


def test_grounded_and_approved_advice_is_shown(tmp_path):
    seen = []
    app = advisor_app(tmp_path, GOOD, seen=seen)
    with TestClient(app, headers=ORIGIN) as client:
        run = ready(client, app, "RUNNING")
        reply = advice(client, run)
        assert reply["source"] == "ai" and reply["risk_reviewed"] is True
        room = min(r["cash_minor"] - 80000 for r in run["forecast"]["timeline"] if r["date"] >= SCENARIO["date"])
        assert reply["items"][0]["text"] == f"Spending up to ₹{room / 100:,.0f} instead of ₹300 keeps your reserve safe."
        assert [m for m, _ in seen] == ["test-default", "test-risk"]
        review = seen[1][1]
        assert review["review_kind"] == "advice" and {o["ref"] for o in review["options"]} >= {"c1", "sm"}
        assert "Swiggy" not in json.dumps(seen)  # descriptions never reach the advisor


@pytest.mark.parametrize("items,verdict", [
    (GOOD, "block"),                                                                                           # risk blocks
    ([{"ref": "zz", "title": "Invented", "text": "Do something else."}], "approve"),                            # unknown option
    ([{"ref": "sm", "title": "Smaller", "text": "Spend only 5000 instead."}], "approve"),                      # typed digits
    ([{"ref": "sm", "title": "Smaller", "text": "Save {c1_saving_per_month} by buying less."}], "approve"),     # other option's fact
    ([{"ref": "sm", "title": "Smaller", "text": "Then you can afford {sm_max_amount}."}], "approve"),           # verdict
    ([{"ref": "c1", "title": "Less delivery", "text": "Spending less lifts your lowest balance to {c1_new_lowest_balance}."}], "approve"),  # hides the breach
])
def test_unsupported_advice_falls_back_to_calculated_options(tmp_path, items, verdict):
    app = advisor_app(tmp_path, items, verdict)
    with TestClient(app, headers=ORIGIN) as client:
        run = ready(client, app, "RUNNING")
        reply = advice(client, run)
        assert reply["source"] == "calculation" and reply["items"][0]["ref"] == "c1"


def test_chat_advice_route_returns_options(tmp_path):
    app = advisor_app(tmp_path, GOOD)
    with TestClient(app, headers=ORIGIN) as client:
        run = ready(client, app, "RUNNING")
        reply = client.post("/api/v1/assistant/message", json={"question": "What should I do about this purchase?",
            "baseline_id": run["id"], "scenario": SCENARIO}).json()
        assert reply["route"] == "advice" and reply["action"]["type"] == "advice"
        assert reply["action"]["items"][0]["ref"] == "sm"
