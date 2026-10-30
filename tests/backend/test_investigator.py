import json

import httpx
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.storage import Policy
from conftest import ORIGIN, register

PROFILE = {"currency": "INR", "timezone": "Asia/Kolkata", "as_of": "2026-10-18", "coverage_start": "2026-08-01",
    "coverage_confirmed": False, "opening_balance_minor": None, "reserve_minor": None, "scheduled_flows": []}
CSV = "\n".join(["date,description,amount_minor,currency,kind,category,recurring",
    "2026-08-01,ACME PAYROLL AUG alice@example.com,8500000,INR,income,salary,false",
    "2026-09-01,ACME PAYROLL SEP alice@example.com,8500000,INR,income,salary,false",
    "2026-10-01,ACME PAYROLL OCT alice@example.com,8500000,INR,income,salary,false",
    "2026-08-03,Flat rent,-2500000,INR,expense,rent,true",
    "2026-09-03,Flat rent,-2500000,INR,expense,rent,true",
    "2026-10-03,Flat rent,-2500000,INR,expense,rent,true"]
    + [f"2026-10-{d:02d},Cafe lunch,-30000,INR,expense,food,false" for d in range(5, 18)])


def ready(client):
    register(client)
    assert client.put("/api/v1/profile", json={"profile": PROFILE, "expected_version": 0}).status_code == 200
    preview = client.post("/api/v1/imports", json={"csv_text": CSV}).json()
    client.post(f"/api/v1/imports/{preview['id']}/confirm", json={"expected_version": preview["version"]})


def test_fallback_questions_cover_gaps_and_monthly_patterns(client):
    ready(client)
    result = client.post("/api/v1/investigator/questions").json()
    assert result["source"] == "calculation"
    kinds = [q["kind"] for q in result["questions"]]
    assert kinds[0] == "available_cash" and "next_income" not in kinds  # salary pattern answers it
    flows = {q["proposal"]["amount_minor"]: q["proposal"] for q in result["questions"] if q["kind"] == "upcoming_flow"}
    assert set(flows) == {8500000, -2500000}  # cafe lunches are everyday spending, not a scheduled flow
    assert all("2026-10-18" < f["date"] <= "2027-01-16" for f in flows.values())
    assert {"reserve", "coverage"} <= set(kinds) and len(kinds) <= 6
    assert not any(any(c.isdigit() for c in q["question"]) for q in result["questions"])


def test_saved_flow_is_not_proposed_again(client):
    ready(client)
    salary = next(q for q in client.post("/api/v1/investigator/questions").json()["questions"]
        if q["kind"] == "upcoming_flow" and q["proposal"]["amount_minor"] > 0)
    profile = {**PROFILE, "scheduled_flows": [salary["proposal"]]}
    version = client.get("/api/v1/profile").json()["version"]
    assert client.put("/api/v1/profile", json={"profile": profile, "expected_version": version}).status_code == 200
    again = client.post("/api/v1/investigator/questions").json()["questions"]
    assert not any(q["kind"] == "upcoming_flow" and q["proposal"]["amount_minor"] > 0 for q in again)


def model_app(tmp_path, questions, seen):
    def handler(request):
        body = json.loads(request.content)
        context = json.loads(body["messages"][1]["content"])
        seen.append(context)
        reply = {"agent": "investigator", "prompt_version": context["prompt_version"], "snapshot_ref": context["snapshot_ref"],
            "status": "needs_clarification", "payload": {"questions": questions(context["untrusted_owner_context"])},
            "evidence_refs": [], "questions": [], "limitations": []}
        return httpx.Response(200, json={"id": "mock", "object": "chat.completion", "created": 0, "model": "test-default",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(reply)}}]})
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'inv.db'}", environment="test",
        mesh_api_key="test-key", mesh_model="test-default"), mesh_transport=httpx.MockTransport(handler))
    return app


def salary_ref(context):
    return next(g["group_ref"] for g in context["history_groups"] if g["kind"] == "income")


def test_only_grounded_model_proposals_are_kept(tmp_path):
    seen = []
    def questions(ctx):
        ref = salary_ref(ctx)
        good = {"label": "Salary", "amount_minor": 8500000, "date": "2026-11-01"}
        return [
            {"ref": "available_cash", "kind": "available_cash", "question": "How much cash do you have today?", "proposal": None},
            {"ref": ref, "kind": "upcoming_flow", "question": "Is your next salary due soon?", "proposal": good},
            {"ref": ref, "kind": "upcoming_flow", "question": "Duplicate ref", "proposal": good},
            {"ref": "reserve", "kind": "reserve", "question": "Keep 5000 aside?", "proposal": None},                       # digits
            {"ref": "g99", "kind": "upcoming_flow", "question": "Unknown group?", "proposal": good},                         # invented ref
            {"ref": "coverage", "kind": "coverage", "question": "Is your history complete?", "proposal": None},
        ]
    app = model_app(tmp_path, questions, seen)
    with TestClient(app, headers=ORIGIN) as client:
        ready(client)
        with app.state.sessions() as db:
            db.get(Policy, 1).mode = "RUNNING"
            db.commit()
        result = client.post("/api/v1/investigator/questions").json()
        assert result["source"] == "ai"
        context = seen[-1]["untrusted_owner_context"]
        rent = next(g["group_ref"] for g in context["history_groups"] if "rent" in g["description"].lower())
        # Fixed gap wording, the grounded model flow, and the rent pattern the model skipped.
        assert [q["ref"] for q in result["questions"]] == ["available_cash", salary_ref(context), rent, "reserve", "coverage"]
        assert result["questions"][1]["question"] == "Is your next salary due soon?"
        assert result["questions"][0]["question"].startswith("How much cash do you have available right now?")
        context = seen[-1]["untrusted_owner_context"]
        assert "alice@example.com" not in json.dumps(context)
        assert {g["missing"] for g in context["profile_gaps"]} == {"available_cash", "next_income", "reserve", "coverage"}


def test_invented_amounts_or_dates_fall_back_to_calculation(tmp_path):
    def questions(ctx):
        ref = salary_ref(ctx)
        return [{"ref": ref, "kind": "upcoming_flow", "question": "Next salary?",
                 "proposal": {"label": "Salary", "amount_minor": 9999999, "date": "2026-11-01"}},
                {"ref": ref, "kind": "upcoming_flow", "question": "Next salary?",
                 "proposal": {"label": "Salary", "amount_minor": 8500000, "date": "2027-06-01"}}]
    app = model_app(tmp_path, questions, [])
    with TestClient(app, headers=ORIGIN) as client:
        ready(client)
        with app.state.sessions() as db:
            db.get(Policy, 1).mode = "RUNNING"
            db.commit()
        result = client.post("/api/v1/investigator/questions").json()
        assert result["source"] == "calculation" and result["questions"][0]["kind"] == "available_cash"
