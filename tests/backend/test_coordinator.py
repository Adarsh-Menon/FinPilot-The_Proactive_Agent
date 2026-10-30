import json
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.coordinator import amounts_in, rule_route, validate
from backend.app.main import create_app
from backend.app.storage import Policy
from conftest import ORIGIN, import_history, register, setup_profile

AS_OF, END = date(2026, 10, 18), date(2026, 11, 17)


@pytest.mark.parametrize("text,expected", [("my salary is 85k", {8_500_000}), ("a ₹2,000 dinner", {200_000}),
    ("2 lakh bonus", {20_000_000}), ("Rs. 1,250.50 bill on the 1st", {125_050}), ("pay on the 5th", set())])
def test_amounts_in_reads_what_the_owner_wrote(text, expected):
    assert amounts_in(text) == expected


@pytest.mark.parametrize("text,route", [("Why can't I afford this?", "explain"), ("why is my balance going down", "explain"),
    ("should I invest in mutual funds?", "invest"), ("how do I clear my credit card", "debts"), ("I want to save for a trip", "goal"),
    ("what should I do about food delivery", "advice"), ("what's missing?", "setup"),
    ("my salary is 85k on the 1st", None), ("hey Fina, how are you?", "greeting"), ("Crossword is books", None), ("can I afford a 2000 dinner", None)])
def test_rules_route_clear_messages_without_a_model(text, route):
    assert rule_route(text) == route


def test_validation_only_keeps_details_the_owner_stated():
    msg = "my salary is 85k on the 1st"
    flow = lambda amount, day: {"intent": "upcoming_flow", "flow": {"label": "Salary", "amount_minor": amount, "date": day},
        "purchase": None, "profile_value": None, "category": None}
    assert validate(flow(8_500_000, "2026-11-01"), msg, AS_OF, END, [])[0] == "upcoming_flow"
    assert validate(flow(85_000, "2026-11-01"), msg, AS_OF, END, [])[1]["amount_minor"] == 8_500_000  # model used rupees
    assert validate(flow(9_000_000, "2026-11-01"), msg, AS_OF, END, []) == ("setup", None)   # invented amount
    assert validate(flow(8_500_000, "2027-03-01"), msg, AS_OF, END, []) == ("setup", None)   # outside the window
    buy = {"intent": "purchase", "purchase": {"amount_minor": 200_000, "date": None}, "flow": None, "profile_value": None, "category": None}
    assert validate(buy, "can I afford a 2000 dinner", AS_OF, END, []) == ("purchase", {"amount_minor": 200_000, "date": None})
    assert validate(buy, "can I afford a dinner", AS_OF, END, [])[0] == "explain"
    cat = {"intent": "category", "category": {"merchant": "Crossword", "category": "Books"}, "purchase": None, "flow": None, "profile_value": None}
    descriptions = [("a", "Crossword bookstore"), ("b", "Uber ride"), ("c", "CROSSWORD Phoenix")]
    assert validate(cat, "Crossword is books", AS_OF, END, descriptions) == ("category", {"merchant": "Crossword", "category": "books", "ids": ["a", "c"]})
    assert validate(cat, "Sapna is books", AS_OF, END, descriptions) == ("unsupported", None)  # merchant not in message


def routed_app(tmp_path, payload):
    def handler(request):
        body = json.loads(request.content)
        ctx = json.loads(body["messages"][1]["content"])
        reply = {"agent": "coordinator", "prompt_version": ctx["prompt_version"], "snapshot_ref": ctx["snapshot_ref"],
            "status": "completed", "payload": {"purchase": None, "flow": None, "profile_value": None, "category": None, "goal": None, **payload},
            "evidence_refs": [], "questions": [], "limitations": []}
        return httpx.Response(200, json={"id": "m", "object": "chat.completion", "created": 0, "model": "test-default",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(reply)}}]})
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'coord.db'}", environment="test",
        mesh_api_key="test-key", mesh_model="test-default"), mesh_transport=httpx.MockTransport(handler))
    return app


def chat(client, text, run=None):
    return client.post("/api/v1/assistant/message", json={"question": text, "baseline_id": run and run["id"]}).json()


def prepared(app, client):
    register(client); setup_profile(client); import_history(client)
    with app.state.sessions() as db:
        db.get(Policy, 1).mode = "RUNNING"
        db.commit()
    return client.post("/api/v1/forecasts", json={"horizon_days": 30}).json()


def test_stated_income_becomes_a_proposal_not_a_change(tmp_path):
    app = routed_app(tmp_path, {"intent": "upcoming_flow", "flow": {"label": "Salary", "amount_minor": 8_500_000, "date": "2026-11-01"}})
    with TestClient(app, headers=ORIGIN) as client:
        run = prepared(app, client)
        before = client.get("/api/v1/profile").json()
        reply = chat(client, "my salary is 85k on the 1st", run)
        assert reply["route"] == "upcoming_flow"
        assert reply["action"] == {"type": "confirm_flow", "flow": {"label": "Salary", "amount_minor": 8_500_000, "date": "2026-11-01"}}
        assert client.get("/api/v1/profile").json() == before


def test_purchase_question_runs_the_what_if(tmp_path):
    app = routed_app(tmp_path, {"intent": "purchase", "purchase": {"amount_minor": 2_000, "date": None}})
    with TestClient(app, headers=ORIGIN) as client:
        run = prepared(app, client)
        reply = chat(client, "can I afford a 20 rupee snack?", run)
        assert reply["route"] == "purchase" and reply["action"] == {"type": "scenario", "amount_minor": 2_000, "date": "2026-10-19"}
        assert reply["answer"].startswith("I've assumed you'd pay tomorrow") and "If you pay ₹20 on 19 Oct 2026" in reply["answer"]
        stale = chat(client, "can I afford a 20 rupee snack?", None)
        assert stale["action"] == {"type": "go_overview"}


def test_category_statement_finds_matching_transactions(tmp_path):
    app = routed_app(tmp_path, {"intent": "category", "category": {"merchant": "Cafe meal", "category": "Eating out"}})
    with TestClient(app, headers=ORIGIN) as client:
        run = prepared(app, client)
        reply = chat(client, "Cafe meal is eating out", run)
        assert reply["action"]["type"] == "confirm_category" and reply["action"]["category"] == "eating out"
        assert len(reply["action"]["ids"]) == 60


def test_invented_amount_is_never_proposed(tmp_path):
    app = routed_app(tmp_path, {"intent": "upcoming_flow", "flow": {"label": "Salary", "amount_minor": 9_999_900, "date": "2026-11-01"}})
    with TestClient(app, headers=ORIGIN) as client:
        run = prepared(app, client)
        reply = chat(client, "my salary is 85k on the 1st", run)
        assert reply["route"] == "setup" and reply["action"] == {"type": "open_questions"}


def test_rules_and_offline_routing_work_without_ai(client):
    register(client); setup_profile(client); import_history(client)
    run = client.post("/api/v1/forecasts", json={"horizon_days": 30}).json()
    assert chat(client, "why is my balance going down?", run)["route"] == "explain"
    assert chat(client, "my salary is 85k on the 1st", run)["action"] == {"type": "open_questions"}
    assert chat(client, "should I buy crypto?", run)["route"] == "unsupported"
    assert chat(client, "hello there", run)["route"] == "greeting"
    assert chat(client, "tell me something", run)["route"] == "help"
