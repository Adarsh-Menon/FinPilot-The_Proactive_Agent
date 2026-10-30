import json
import time

import httpx
from fastapi.testclient import TestClient

from backend.app.alerts import Alerter
from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.observability import actor_ref
from backend.app.storage import Alert, Policy, database
from conftest import ORIGIN, register, setup_profile
from test_admin import operator


def telegram(sent):
    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})
    return httpx.MockTransport(handler)


def wait_for(check, seconds=3):
    end = time.time() + seconds
    while time.time() < end and not check():
        time.sleep(0.05)
    return check()


def test_repeated_alerts_push_once_and_count_up(tmp_path):
    engine, sessions = database(f"sqlite:///{tmp_path / 'alerts.db'}")
    Alert.metadata.create_all(engine)
    sent = []
    alerter = Alerter(sessions, "bot-token", "42", telegram(sent))
    first = alerter.raise_alert("5xx:GET /x", "critical", "Server error on GET /x", "Unhandled ZeroDivisionError")
    second = alerter.raise_alert("5xx:GET /x", "critical", "Server error on GET /x", "Unhandled ZeroDivisionError")
    assert first == second and wait_for(lambda: len(sent) == 1)
    time.sleep(0.2)
    assert len(sent) == 1 and sent[0]["chat_id"] == "42" and "Server error on GET /x" in sent[0]["text"]
    with sessions() as db:
        alert = db.get(Alert, first)
        assert alert.count == 2 and alert.delivered
    assert not Alerter(sessions).configured                     # no token: recorded for the portal only


def test_server_errors_alert_and_operators_resolve_them_with_an_audit_trail(tmp_path):
    sent = []
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'ops.db'}", environment="test", telegram_bot_token="t",
        telegram_chat_id="1"), alert_transport=telegram(sent))
    app.add_api_route("/api/v1/boom", lambda: 1 / 0)
    with TestClient(app, headers=ORIGIN, raise_server_exceptions=False) as client:
        assert client.get("/api/v1/boom").status_code == 500
        assert wait_for(lambda: len(sent) == 1) and "Server error on GET /api/v1/boom" in sent[0]["text"]
        assert "ZeroDivisionError" in sent[0]["text"]
        ops = operator(app)
        alerts = ops.get("/ops/v1/alerts").json()["alerts"]
        assert len(alerts) == 1 and alerts[0]["status"] == "open" and alerts[0]["delivered"]
        assert ops.post(f"/ops/v1/alerts/{alerts[0]['id']}/acknowledge").json()["status"] == "acknowledged"
        assert ops.post(f"/ops/v1/alerts/{alerts[0]['id']}/resolve").json()["status"] == "resolved"
        assert ops.get("/ops/v1/alerts").json()["alerts"] == []
        assert ops.post("/ops/v1/alerts/test").json() == {"delivered": True}
        health = ops.get("/ops/v1/health").json()
        assert health["database"]["ok"] and health["telegram"] and health["errors_hour"] >= 1 and health["alerts_open"] == 0
        actions = [e["action"] for e in ops.get("/ops/v1/audit").json()["entries"]]
        assert actions == ["alert_test", "alert_resolve", "alert_acknowledge"]
        assert all(e["operator"] == "ops_admin" for e in ops.get("/ops/v1/audit").json()["entries"])


def test_ai_budget_alerts_and_pauses_ai(tmp_path):
    sent = []
    def model(request):
        body = json.loads(request.content)
        return httpx.Response(200, json={"id": "m", "object": "chat.completion", "created": 0, "model": body["model"],
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "save 60k"}}],
            "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 0, "total_tokens": 1_000_000}})
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'budget.db'}", environment="test", mesh_api_key="k",
        mesh_model="m", mesh_speech_model="speech", model_prices={"speech": [0.5, 1.0]}, telegram_bot_token="t", telegram_chat_id="1"),
        mesh_transport=httpx.MockTransport(model), alert_transport=telegram(sent))
    from test_voice import wav
    with TestClient(app, headers=ORIGIN) as client:
        with app.state.sessions() as db:
            db.get(Policy, 1).mode = "RUNNING"
            db.commit()
        ops = operator(app)
        assert ops.put("/ops/v1/budget", json={"daily_budget_usd": 1.0, "auto_pause": True}).status_code == 200
        register(client)
        client.post("/api/v1/voice/transcribe", json={"audio_base64": wav()})          # $0.50: half the budget
        app.state.background.drain()
        assert ops.get("/ops/v1/costs").json()["today_usd"] == 0.5 and not sent
        client.post("/api/v1/voice/transcribe", json={"audio_base64": wav()})          # $1.00: cap reached
        app.state.background.drain()
        assert wait_for(lambda: any("Daily AI budget reached" in m["text"] for m in sent))
        assert "AI is now paused" in sent[-1]["text"]
        with app.state.sessions() as db:
            assert db.get(Policy, 1).mode == "AI_PAUSED"
        assert client.post("/api/v1/voice/transcribe", json={"audio_base64": wav()}).status_code == 503
        audit = ops.get("/ops/v1/audit").json()["entries"]
        assert audit[0]["operator"] == "system" and audit[0]["action"] == "ai_auto_pause" and audit[1]["action"] == "budget_change"


def test_limit_hits_show_in_abuse_view_and_suspension_blocks_the_account(app, client):
    register(client)
    setup_profile(client)
    ref = actor_ref(_user_id(app, "customer_a"))
    for _ in range(3):
        client.post("/api/v1/auth/logout")
        assert client.post("/api/v1/auth/login", json={"username": "customer_a", "password": "wrong-password-123"}).status_code == 401
    login = client.post("/api/v1/auth/login", json={"username": "customer_a", "password": "synthetic-password-123"}).json()
    client.headers["X-CSRF-Token"] = login["csrf_token"]
    ops = operator(app)
    app.state.background.drain()
    rows = {a["actor"]: a for a in ops.get("/ops/v1/abuse").json()["accounts"]}
    assert rows[ref]["limits"] == {"failed_sign_in": 3} and not rows[ref]["suspended"]
    assert "customer_a" not in json.dumps(rows)                                  # pseudonyms only
    assert ops.post("/ops/v1/suspensions", json={"actor": ref, "reason": "Scripted abuse of AI"}).status_code == 201
    assert client.get("/api/v1/profile").status_code == 403                       # live sessions stop working
    client.post("/api/v1/auth/logout")
    blocked = client.post("/api/v1/auth/login", json={"username": "customer_a", "password": "synthetic-password-123"})
    assert blocked.status_code == 403 and "suspended" in blocked.json()["detail"]
    assert {a["actor"]: a for a in ops.get("/ops/v1/abuse").json()["accounts"]}[ref]["suspended_by"] == "ops_admin"
    assert ops.delete(f"/ops/v1/suspensions/{ref}").status_code == 204
    assert client.post("/api/v1/auth/login", json={"username": "customer_a", "password": "synthetic-password-123"}).status_code == 200
    assert [e["action"] for e in ops.get("/ops/v1/audit").json()["entries"]][:2] == ["unsuspend", "suspend"]


def _user_id(app, username):
    from backend.app.storage import User
    from sqlalchemy import select
    with app.state.sessions() as db:
        return db.scalar(select(User.id).where(User.username == username))
