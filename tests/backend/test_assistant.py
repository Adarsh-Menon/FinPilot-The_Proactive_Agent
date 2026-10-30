import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.storage import Policy
from conftest import ORIGIN, import_history, register, setup_profile

SCENARIO = {"amount_minor": 12000, "date": "2026-10-21"}
GOOD = ("After paying {purchase_amount} on {purchase_date}, your balance falls below your {reserve} reserve on "
        "{first_reserve_breach_with_purchase}. This is not a verdict.")
RENDERED = "After paying ₹120 on 21 Oct 2026, your balance falls below your ₹30 reserve on 8 Nov 2026. This is not a verdict."


def ready(client):
    register(client); setup_profile(client); import_history(client)
    return client.post("/api/v1/forecasts", json={"horizon_days": 30}).json()


def ask(client, run, question="Why can't I afford this?", scenario=SCENARIO):
    return client.post("/api/v1/assistant/explain", json={"baseline_id": run["id"], "question": question,
        "scenario": scenario, "history": [{"role": "owner", "text": "hi"}]})


def completion(model, reply):
    return httpx.Response(200, json={"id": "mock", "object": "chat.completion", "created": 0, "model": model,
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(reply)}}]})


def model_app(tmp_path, summary=GOOD, seen=None, verdict="approve"):
    def handler(request):
        body = json.loads(request.content)
        facts = json.loads(body["messages"][1]["content"])
        if seen is not None:
            seen.append(body)
        envelope = {"prompt_version": facts["prompt_version"], "snapshot_ref": facts["snapshot_ref"],
            "status": "completed", "evidence_refs": [], "questions": [], "limitations": []}
        if body["model"] == "test-risk":
            return completion("test-risk", {**envelope, "agent": "risk", "payload": {"verdict": verdict, "checks": [
                {"rule": "numbers_attributed", "result": "pass" if verdict == "approve" else "fail", "reason": "synthetic"}],
                "reason": "synthetic"}})
        return completion("test-default", {**envelope, "agent": "forecaster", "payload": {"basis": "cash_balance",
            "summary": summary, "facts_used": [], "assumptions": [], "coverage_flags": []}})
    return create_app(Settings(database_url=f"sqlite:///{tmp_path / 'chat.db'}", environment="test",
        mesh_api_key="test-key", mesh_model="test-default", mesh_risk_model="test-risk"),
        mesh_transport=httpx.MockTransport(handler))


def running(app):
    with app.state.sessions() as db:
        db.get(Policy, 1).mode = "RUNNING"
        db.commit()


def test_calculated_explanation_when_ai_is_unavailable(client):
    run = ready(client)
    reply = ask(client, run).json()
    assert reply["source"] == "calculation"
    assert reply["answer"].startswith("I can see why this one feels tight.")
    assert ("If you pay ₹120 on 21 Oct 2026, you'd have ₹47 left that day and ₹20 by 17 Nov 2026, "
            "instead of ₹140 without it.") in reply["answer"]
    assert "dips under the ₹30 you wanted to keep aside, starting 8 Nov 2026" in reply["answer"]
    assert "I won't give you a yes or no" in reply["answer"]
    trend = ask(client, run, "Why is my balance going down?", None).json()["answer"]
    assert "You're starting at ₹100 and heading to ₹140 by 17 Nov 2026." in trend
    dinner = ask(client, run, "Can I afford a dinner out?", None).json()["answer"]
    assert dinner.startswith("Happy to look at that with you.") and "What if panel" in dinner
    levers = ask(client, run, "What would change this picture?", SCENARIO).json()["answer"]
    assert "add your next paydays and bills in Your context" in levers


def test_stale_foreign_and_out_of_range_requests_are_refused(client, app):
    run = ready(client)
    assert ask(client, run, scenario={"amount_minor": 100, "date": "2026-12-30"}).status_code == 422
    with TestClient(app, headers=ORIGIN) as other:
        register(other, "customer_b")
        assert ask(other, run).status_code == 404
    client.post("/api/v1/transactions", json={"idempotency_key": "stale-chat-1", "transaction": {"date": "2026-10-18",
        "description": "Cafe", "amount_minor": -100, "currency": "INR", "kind": "expense"}})
    assert ask(client, run).status_code == 409


def test_placeholders_are_filled_by_the_server_after_risk_review(tmp_path):
    seen = []
    app = model_app(tmp_path, seen=seen)
    with TestClient(app, headers=ORIGIN) as client:
        run = ready(client)
        running(app)
        reply = ask(client, run).json()
        assert reply["source"] == "ai" and reply["answer"] == RENDERED and reply["prompt_version"] == "v4"
        assert reply["risk_reviewed"] is True
        assert [b["model"] for b in seen] == ["test-default", "test-risk"]
        assert json.loads(seen[1]["messages"][1]["content"])["proposed_explanation"] == RENDERED
        facts = json.loads(seen[0]["messages"][1]["content"])
        assert facts["engine_facts"]["facts"]["balance_after_purchase_on_purchase_date"]["value"] == 47
        assert "before" not in facts["engine_facts"]["facts"]["balance_after_purchase_on_purchase_date"]["meaning"]
        assert facts["untrusted_owner_question"] == "Why can't I afford this?"
        assert "Cafe meal" not in json.dumps(seen)


@pytest.mark.parametrize("summary", [
    "Keep 5000 aside before buying this.",                         # typed digits
    "Your savings of {savings_balance} cover it.",                 # unknown placeholder
    "You can't afford this purchase on {purchase_date}.",          # verdict
    "We guarantee {reserve} stays untouched.",                     # guarantee
])
def test_unsupported_model_answers_fall_back_to_calculation(tmp_path, summary):
    app = model_app(tmp_path, summary)
    with TestClient(app, headers=ORIGIN) as client:
        run = ready(client)
        running(app)
        reply = ask(client, run).json()
        assert reply["source"] == "calculation" and "If you pay ₹120" in reply["answer"]


def test_risk_reviewer_block_falls_back_to_calculation(tmp_path):
    app = model_app(tmp_path, verdict="block")
    with TestClient(app, headers=ORIGIN) as client:
        run = ready(client)
        running(app)
        reply = ask(client, run).json()
        assert reply["source"] == "calculation" and reply["answer"] != RENDERED


def test_hedged_affordability_wording_is_allowed(tmp_path):
    hedged = "FinPilot can't tell you whether you can afford it, but your balance drops below {reserve} on {first_reserve_breach_with_purchase}."
    app = model_app(tmp_path, hedged)
    with TestClient(app, headers=ORIGIN) as client:
        run = ready(client)
        running(app)
        assert ask(client, run).json()["source"] == "ai"


def test_questions_are_capped_per_customer_per_hour(client):
    run = ready(client)
    for _ in range(20):
        assert ask(client, run).status_code == 200
    assert ask(client, run).status_code == 429
