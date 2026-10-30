import json
from datetime import date, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.behaviour import outlook, patterns
from backend.app.config import Settings
from backend.app.goals import DAYS_PER_MONTH
from backend.app.main import create_app
from backend.app.storage import Policy
from conftest import ORIGIN, register

AS_OF = date(2026, 10, 18)
PROFILE = {"currency": "INR", "timezone": "Asia/Kolkata", "as_of": AS_OF.isoformat(), "coverage_start": "2026-07-21",
    "coverage_confirmed": True, "opening_balance_minor": 5_000_000, "reserve_minor": 1_000_000,
    "scheduled_flows": [{"date": "2026-11-01", "label": "Salary", "amount_minor": 8_000_000}]}


def ledger():
    rows = []
    for d in range(90):
        day = AS_OF - timedelta(days=d)
        recent = d < 30
        rows.append({"date": day.isoformat(), "description": "Swiggy order", "amount_minor": -(60_000 if recent else 30_000),
            "currency": "INR", "kind": "expense", "category": "food delivery"})
        if d % 7 == 0:
            rows.append({"date": day.isoformat(), "description": "DMart", "amount_minor": -(400_000 if recent else 100_000),
                "currency": "INR", "kind": "expense", "category": "groceries"})   # rising, but essential
        if d % 10 == 0:
            rows.append({"date": day.isoformat(), "description": "Amazon", "amount_minor": -200_000, "currency": "INR",
                "kind": "expense", "category": "shopping"})                        # flat
    return rows


def test_patterns_find_rising_non_essentials_only():
    found = patterns(ledger(), PROFILE)
    assert [p["category"] for p in found] == ["food delivery"]
    p = found[0]
    assert (p["recent_month"], p["prior_month"], p["extra_per_month"], p["increase_percent"]) == (1_800_000, 900_000, 900_000, 100)
    assert patterns(ledger(), {**PROFILE, "coverage_start": "2026-09-01"}) is None   # under 60 days of history


def test_new_spending_and_window_end_at_latest_transaction():
    from backend.app.behaviour import scan
    rows = ledger() + [{"date": "2026-10-10", "description": "Goa flights", "amount_minor": -1_404_000, "currency": "INR",
        "kind": "expense", "category": "travel"}]
    later = {**PROFILE, "as_of": "2026-10-27"}            # balance date after the last import
    result = scan(rows, later)
    assert result["window"]["recent"] == ["2026-09-19", "2026-10-18"]   # ends at the latest expense, not the balance date
    assert result["new"] == [{"category": "travel", "recent_month": 1_404_000, "recent_count": 1}]
    assert [p["category"] for p in result["rising"]] == ["food delivery"]
    assert {c["category"] for c in result["checked"]} >= {"food delivery", "shopping", "travel"}


def goal(name, status, pace, needed, remaining, days_left, target="2027-04-30"):
    return {"name": name, "target_minor": remaining + 100_000, "target_date": target, "saved_minor": 100_000, "status": "active",
        "progress": {"status": status, "pace_per_month_minor": pace, "needed_per_month_minor": needed,
            "remaining_minor": remaining, "days_left": days_left, "months_left": int(days_left / DAYS_PER_MONTH), "percent_saved": 1}}


def test_if_this_continues_projection_and_advice():
    run = {"forecast": {"reserve_minor": 1_000_000, "timeline": [{"date": (AS_OF + timedelta(days=i + 1)).isoformat(),
        "cash_minor": 1_400_000 - i * 10_000} for i in range(30)]}, "checks": {"first_reserve_breach": None}}
    trip = goal("Trip", "on_track", 5_000_000, 2_000_000, 10_000_000, 183)
    calm = goal("Fund", "on_track", 30_000_000, 100_000, 1_000_000, 183)
    view = outlook(PROFILE, ledger(), [trip, calm], run)
    goal_warnings = [w for w in view["warnings"] if w["kind"] == "goal"]
    assert [w["facts"]["goal_name"] for w in goal_warnings] == ["Trip"]   # the comfortable goal gets no warning
    months = 183 / DAYS_PER_MONTH
    drag = round(900_000 * (months + 1) / 2)
    f = goal_warnings[0]["facts"]
    assert f["pace_if_continues"] == 5_000_000 - drag and f["status_now"] == "on track" and f["status_if"] == "a stretch"
    assert goal_warnings[0]["advice"][0]["kind"] == "back_to_usual"
    assert goal_warnings[0]["advice"][0]["facts"]["saving_per_month"] == 900_000
    reserve = next(w for w in view["warnings"] if w["kind"] == "reserve")
    daily = 900_000 / DAYS_PER_MONTH
    expected = next(r["date"] for n, r in enumerate(run["forecast"]["timeline"]) if r["cash_minor"] - round(daily * (n + 1)) < 1_000_000)
    assert reserve["facts"]["breach_date_if"] == expected


def csv():
    return "date,description,amount_minor,currency,kind,category,recurring\n" + "\n".join(
        f"{t['date']},{t['description']},{t['amount_minor']},INR,expense,{t['category']},false" for t in ledger())


def ready(client, app=None, mode=None):
    register(client)
    client.put("/api/v1/profile", json={"profile": PROFILE, "expected_version": 0})
    p = client.post("/api/v1/imports", json={"csv_text": csv()}).json()
    client.post(f"/api/v1/imports/{p['id']}/confirm", json={"expected_version": p["version"]})
    client.post("/api/v1/forecasts", json={"horizon_days": 30})
    client.post("/api/v1/goals", json={"name": "House deposit", "target_minor": 30_000_000, "target_date": "2027-06-30"})
    if app is not None and mode:
        with app.state.sessions() as db:
            db.get(Policy, 1).mode = mode
            db.commit()


def test_outlook_endpoint_is_private_and_uses_calculated_text(client, app):
    ready(client)
    view = client.get("/api/v1/outlook").json()
    assert view["ready"] and view["pattern_texts"][0].startswith("Food delivery: ₹18,000 in the last 30 days, up from ₹9,000")
    warning = next(w for w in view["warnings"] if w["kind"] == "goal")
    assert warning["text"].startswith("If this keeps up, House deposit goes from")
    assert warning["advice"][0]["text"].startswith("Going back to about ₹9,000 a month on food delivery")
    with TestClient(app, headers=ORIGIN) as other:
        register(other, "customer_b")
        other.put("/api/v1/profile", json={"profile": PROFILE, "expected_version": 0})
        assert other.get("/api/v1/outlook").json()["patterns"] == []


def pattern_app(tmp_path, items, verdict="approve"):
    def handler(request):
        body = json.loads(request.content)
        ctx = json.loads(body["messages"][1]["content"])
        env = {"prompt_version": ctx["prompt_version"], "snapshot_ref": ctx["snapshot_ref"], "status": "completed",
            "evidence_refs": [], "questions": [], "limitations": []}
        if body["model"] == "test-risk":
            reply = {**env, "agent": "risk", "payload": {"verdict": verdict, "checks": [{"rule": "numbers_attributed",
                "result": "pass" if verdict == "approve" else "fail", "reason": None}], "reason": "r"}}
        else:
            reply = {**env, "agent": "analyzer", "payload": {"items": items(ctx)}}
        return httpx.Response(200, json={"id": "m", "object": "chat.completion", "created": 0, "model": body["model"],
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(reply)}}]})
    return create_app(Settings(database_url=f"sqlite:///{tmp_path / 'outlook.db'}", environment="test", mesh_api_key="k",
        mesh_model="test-default", mesh_risk_model="test-risk"), mesh_transport=httpx.MockTransport(handler))


def goal_ref(ctx):
    return next(w["ref"] for w in ctx["warnings"] if w["kind"] == "goal")


@pytest.mark.parametrize("text,source", [
    ("{p1_category} rose to {p1_recent_month}. If it continues, {REF_goal_name} becomes {REF_status_if}.", "ai"),
    ("{p1_category} rose a lot. Keep an eye on it.", "calculation"),                         # no consequence
    ("Food delivery doubled to 18000.", "calculation"),                                     # typed digits
])
def test_pattern_explanations_must_be_grounded(tmp_path, text, source):
    app = pattern_app(tmp_path, lambda ctx: [{"ref": goal_ref(ctx), "title": "A goal at risk", "text": text.replace("REF", goal_ref(ctx))}])
    with TestClient(app, headers=ORIGIN) as client:
        ready(client, app, "RUNNING")
        reply = client.post("/api/v1/outlook/explain").json()
        assert reply["source"] == source
        if source == "ai":
            assert reply["items"][0]["text"] == "food delivery rose to ₹18,000. If it continues, House deposit becomes a stretch." \
                or reply["items"][0]["text"].startswith("food delivery rose to ₹18,000. If it continues, House deposit becomes ")


def test_chat_routes_trend_questions_to_the_outlook(client):
    register(client)
    client.put("/api/v1/profile", json={"profile": PROFILE, "expected_version": 0})
    reply = client.post("/api/v1/assistant/message", json={"question": "What happens if this continues?"}).json()
    assert reply["route"] == "outlook" and reply["action"] == {"type": "go_outlook"}
