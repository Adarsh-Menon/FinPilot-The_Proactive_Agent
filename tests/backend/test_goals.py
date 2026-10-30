import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.goals import progress
from backend.app.main import create_app
from backend.app.storage import Policy
from conftest import ORIGIN, import_history, register, setup_profile

GOAL = {"name": "Trip to Goa", "target_minor": 10000, "target_date": "2026-12-18", "saved_minor": 2000}


def test_progress_status_follows_the_forecast_pace():
    run = {"forecast": {"timeline": [{"cumulative_flow_minor": 0}] * 29 + [{"cumulative_flow_minor": 4000}]}}
    goal = {**GOAL, "status": "active"}
    on = progress(goal, "2026-10-18", run)
    assert on["remaining_minor"] == 8000 and on["percent_saved"] == 20 and on["months_left"] == 2
    assert on["pace_per_month_minor"] == 4059 and on["needed_per_month_minor"] == 3992 and on["status"] == "on_track"
    assert progress({**goal, "target_minor": 20000}, "2026-10-18", run)["status"] == "stretch"
    shrinking = {"forecast": {"timeline": [{"cumulative_flow_minor": -500}] * 30}}
    assert progress(goal, "2026-10-18", shrinking)["status"] == "off_track"
    assert progress(goal, "2026-10-18", None)["status"] == "needs_forecast"
    assert progress({**goal, "status": "achieved"}, "2026-10-18", run)["status"] == "reached"
    assert progress(goal, "2027-01-01", run)["status"] == "past_due"


def test_goal_lifecycle_is_private_to_its_owner(client, app):
    register(client); setup_profile(client); import_history(client)
    created = client.post("/api/v1/goals", json=GOAL)
    assert created.status_code == 201 and created.json()["progress"]["status"] == "needs_forecast"
    goal_id = created.json()["id"]
    assert client.post("/api/v1/goals", json={**GOAL, "target_date": "2026-10-01"}).status_code == 422
    assert client.post("/api/v1/goals", json={**GOAL, "target_minor": 0}).status_code == 422
    client.post("/api/v1/forecasts", json={"horizon_days": 30})
    listed = client.get("/api/v1/goals").json()
    assert listed["has_forecast"] and listed["goals"][0]["progress"]["status"] == "on_track"
    updated = client.patch(f"/api/v1/goals/{goal_id}", json={"saved_minor": 10000})
    assert updated.json()["progress"]["status"] == "reached"
    assert client.patch(f"/api/v1/goals/{goal_id}", json={"status": "achieved"}).json()["status"] == "achieved"
    with TestClient(app, headers=ORIGIN) as other:
        register(other, "customer_b"); setup_profile(other)
        assert other.get("/api/v1/goals").json()["goals"] == []
        assert other.patch(f"/api/v1/goals/{goal_id}", json={"saved_minor": 1}).status_code == 404
        assert other.delete(f"/api/v1/goals/{goal_id}").status_code == 404
    assert client.delete(f"/api/v1/goals/{goal_id}").status_code == 204
    assert client.get("/api/v1/goals").json()["goals"] == []


def test_calculated_coaching_note_without_ai(client):
    register(client); setup_profile(client); import_history(client)
    client.post("/api/v1/forecasts", json={"horizon_days": 30})
    goal = client.post("/api/v1/goals", json=GOAL).json()
    note = client.post(f"/api/v1/goals/{goal['id']}/coach").json()
    assert note["source"] == "calculation"
    assert note["note"].startswith("You're 20% of the way to Trip to Goa, with ₹80 to go by 18 Dec 2026. That's about ₹40 a month.")
    assert "on track" in note["note"]


def coach_app(tmp_path, note):
    def handler(request):
        body = json.loads(request.content)
        ctx = json.loads(body["messages"][1]["content"])
        reply = {"agent": "goals", "prompt_version": ctx["prompt_version"], "snapshot_ref": ctx["snapshot_ref"], "status": "completed",
            "payload": {"note": note, "facts_used": []}, "evidence_refs": [], "questions": [], "limitations": []}
        return httpx.Response(200, json={"id": "m", "object": "chat.completion", "created": 0, "model": "test-default",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(reply)}}]})
    return create_app(Settings(database_url=f"sqlite:///{tmp_path / 'goals.db'}", environment="test",
        mesh_api_key="test-key", mesh_model="test-default"), mesh_transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("note,source", [
    ("You're {percent_saved} there, with {goal_remaining} left by {goal_target_date}. Lovely steady progress.", "ai"),
    ("Save 5000 more and you're done.", "calculation"),                         # typed digits
    ("Hit {goal_target} and earn bonus coins!", "calculation"),                 # rewards are not active
    ("Your {secret_balance} will cover it.", "calculation"),                    # unknown placeholder
])
def test_model_note_is_shown_only_when_grounded(tmp_path, note, source):
    app = coach_app(tmp_path, note)
    with TestClient(app, headers=ORIGIN) as client:
        register(client); setup_profile(client); import_history(client)
        with app.state.sessions() as db:
            db.get(Policy, 1).mode = "RUNNING"
            db.commit()
        goal = client.post("/api/v1/goals", json=GOAL).json()
        reply = client.post(f"/api/v1/goals/{goal['id']}/coach").json()
        assert reply["source"] == source
        if source == "ai":
            assert reply["note"] == "You're 20% there, with ₹80 left by 18 Dec 2026. Lovely steady progress."


def test_chat_goal_statement_becomes_a_confirm_card(tmp_path):
    def handler(request):
        body = json.loads(request.content)
        ctx = json.loads(body["messages"][1]["content"])
        reply = {"agent": "coordinator", "prompt_version": ctx["prompt_version"], "snapshot_ref": ctx["snapshot_ref"], "status": "completed",
            "payload": {"intent": "goal", "purchase": None, "flow": None, "profile_value": None, "category": None,
                "goal": {"name": "Trip", "target_minor": 50000, "date": "2027-03-31"}},
            "evidence_refs": [], "questions": [], "limitations": []}
        return httpx.Response(200, json={"id": "m", "object": "chat.completion", "created": 0, "model": "test-default",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(reply)}}]})
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'goalchat.db'}", environment="test",
        mesh_api_key="test-key", mesh_model="test-default"), mesh_transport=httpx.MockTransport(handler))
    with TestClient(app, headers=ORIGIN) as client:
        register(client); setup_profile(client)
        with app.state.sessions() as db:
            db.get(Policy, 1).mode = "RUNNING"
            db.commit()
        reply = client.post("/api/v1/assistant/message", json={"question": "I want to save 50k for a trip by March"}).json()
        assert reply["action"] == {"type": "confirm_goal", "goal": {"name": "Trip", "target_minor": 5_000_000, "target_date": "2027-03-31"}}
        assert client.get("/api/v1/goals").json()["goals"] == []   # nothing saved until confirmed
        no_amount = client.post("/api/v1/assistant/message", json={"question": "I want to start saving for goals"}).json()
        assert no_amount["route"] == "goal" and no_amount["action"] == {"type": "go_goals"}


def test_goals_page_turns_a_half_heard_sentence_into_a_goal_draft(client):
    register(client); setup_profile(client)
    ask = lambda q, page="goals": client.post("/api/v1/assistant/message", json={"question": q, "page": page}).json()
    misheard = ask("6004 Goa")                                   # "6,000 for Goa" as the browser heard it
    assert misheard["route"] == "goal" and misheard["action"]["type"] == "confirm_goal"
    assert misheard["action"]["goal"] == {"name": "Goa", "target_minor": 600400, "target_date": None}
    assert "a date" in misheard["answer"] and "“4”" in misheard["answer"]
    full = ask("save 60,000 for a Goa trip by March")["action"]["goal"]
    assert full == {"name": "Goa trip", "target_minor": 6000000, "target_date": "2027-03-31"}
    assert ask("6004 Goa", None)["route"] != "goal"                # only the Goals card reads it as a goal


def test_context_page_prefills_what_was_said(client):
    register(client); setup_profile(client)
    ask = lambda q: client.post("/api/v1/assistant/message", json={"question": q, "page": "context"}).json()
    short = ask("freelance fee")
    assert short["action"] == {"type": "confirm_flow", "flow": {"label": "Freelance fee", "amount_minor": None, "date": None, "direction": "in"}}
    assert "the amount and the date" in short["answer"]
    rent = ask("rent 28000 on the 2nd")["action"]["flow"]
    assert rent == {"label": "Rent", "amount_minor": -2800000, "date": "2026-11-02", "direction": "out"}
    assert ask("I have 45,000 in my account")["action"] == {"type": "confirm_value", "field": "available_cash", "amount_minor": 4500000}
