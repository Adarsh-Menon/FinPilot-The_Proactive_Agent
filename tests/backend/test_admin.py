from fastapi.testclient import TestClient
from pwdlib import PasswordHash

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.observability import actor_ref
from backend.app.storage import User
from conftest import ORIGIN, import_history, register, setup_profile

OPS_ROUTES = ["/ops/v1/users", "/ops/v1/activity", "/ops/v1/logs", "/ops/v1/langsmith", "/ops/v1/grafana"]


def operator(app):
    with app.state.sessions() as db:
        db.add(User(username="ops_admin", password_hash=PasswordHash.recommended().hash("synthetic-password-123"), role="operator"))
        db.commit()
    ops = TestClient(app, headers=ORIGIN)
    login = ops.post("/ops/v1/auth/login", json={"username": "ops_admin", "password": "synthetic-password-123"}).json()
    ops.headers["X-CSRF-Token"] = login["csrf_token"]
    return ops


def test_admin_sees_pseudonymous_activity_and_logs_without_content(tmp_path):
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'admin.db'}", environment="test",
        mesh_api_key="", mesh_model="", grafana_url="http://127.0.0.1:9"))
    with TestClient(app, headers=ORIGIN) as client:
        me = register(client, "private_person")
        setup_profile(client); import_history(client)
        client.post("/api/v1/transactions", json={"idempotency_key": "admin-test-1", "transaction": {"date": "2026-10-18",
            "description": "Secret clinic visit", "amount_minor": -98765, "currency": "INR", "kind": "expense"}})
        run = client.post("/api/v1/forecasts", json={"horizon_days": 30}).json()
        client.post("/api/v1/assistant/explain", json={"baseline_id": run["id"], "question": "Why can't I afford my secret trip?",
            "scenario": {"amount_minor": 4321, "date": "2026-10-21"}})
        for route in OPS_ROUTES:
            assert client.get(route).status_code == 401

        with operator(app) as ops:
            responses = {route: ops.get(route) for route in OPS_ROUTES}
            assert all(r.status_code == 200 for r in responses.values())
            dump = "".join(r.text for r in responses.values())
            for private in ["private_person", "Secret clinic", "98765", "987.65", "secret trip", "Cafe meal", run["id"]]:
                assert private not in dump

            with app.state.sessions() as db:
                user_id = db.query(User).filter_by(username="private_person").one().id
            ref = actor_ref(user_id)
            users = responses["/ops/v1/users"].json()["users"]
            assert [u["actor"] for u in users] == [ref]
            assert users[0]["transactions_added"] == 61
            assert {"register", "csv_preview", "csv_import", "manual_entry", "forecast", "fina_question"} <= set(users[0]["actions"])

            events = ops.get(f"/ops/v1/activity?actor={ref}").json()["events"]
            chat = next(e for e in events if e["action"] == "fina_question")
            assert chat["outcome"] == "fallback: ai unavailable" and chat["agent"] == "calculation"
            assert ops.get("/ops/v1/activity?actor=private_person").status_code == 422

            logs = responses["/ops/v1/logs"].json()["requests"]
            assert any(l["route"] == "/api/v1/runs/{run_id}" or l["route"] == "/api/v1/forecasts" for l in logs)
            assert all(set(l) == {"time", "method", "route", "status", "duration_ms", "error"} for l in logs)
            assert responses["/ops/v1/langsmith"].json()["enabled"] is False
            assert responses["/ops/v1/grafana"].json()["reachable"] is False


def test_customer_and_admin_can_be_signed_in_together_in_one_browser(app):
    with TestClient(app, headers=ORIGIN) as browser:
        customer = register(browser, "both_customer")
        with app.state.sessions() as db:
            db.add(User(username="both_ops", password_hash=PasswordHash.recommended().hash("synthetic-password-123"), role="operator"))
            db.commit()
        # Each door admits only its own role.
        wrong = browser.post("/api/v1/auth/login", json={"username": "both_ops", "password": "synthetic-password-123"})
        assert wrong.status_code == 403 and "/admin" in wrong.json()["detail"]
        assert browser.post("/ops/v1/auth/login", json={"username": "both_customer", "password": "synthetic-password-123"}).status_code == 403
        ops = browser.post("/ops/v1/auth/login", json={"username": "both_ops", "password": "synthetic-password-123"}).json()
        assert {"finpilot_session", "finpilot_ops_session"} <= set(browser.cookies.keys())
        assert browser.get("/api/v1/auth/session").json()["username"] == "both_customer"
        assert browser.get("/ops/v1/auth/session").json()["username"] == "both_ops"
        assert browser.get("/api/v1/profile").status_code == 200
        assert browser.get("/ops/v1/overview").status_code == 200
        # Each side's CSRF token only works for its own session.
        assert browser.post("/ops/v1/auth/logout", headers={"X-CSRF-Token": customer["csrf_token"]}).status_code == 403
        assert browser.post("/ops/v1/auth/logout", headers={"X-CSRF-Token": ops["csrf_token"]}).status_code == 204
        assert browser.get("/ops/v1/overview").status_code == 401
        assert browser.get("/api/v1/profile").status_code == 200


def test_evaluations_are_admin_only_and_safe_to_select(app, client):
    assert client.get("/ops/v1/evals").status_code == 401
    ops = operator(app)
    data = ops.get("/ops/v1/evals").json()
    assert set(data) == {"runs", "meta", "summary", "cases"}
    escaped = ops.get("/ops/v1/evals", params={"run": "../../backend"}).json()       # never reads outside the results folder
    assert escaped["meta"] == data["meta"]


def test_evaluation_runs_need_the_eval_environment_and_fixed_choices(tmp_path):
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'e.db'}", environment="test", mesh_api_key="", mesh_model="",
        evals_python=str(tmp_path / "missing" / "python.exe")))
    with TestClient(app, headers=ORIGIN):
        ops = operator(app)
        assert ops.post("/ops/v1/evals/run", json={"suites": ["coordinator; rm -rf /"]}).status_code == 422    # fixed names only
        missing = ops.post("/ops/v1/evals/run", json={"suites": ["coordinator"], "split": "dev", "judge": False})
        assert missing.status_code == 409 and "not installed" in missing.json()["detail"]
        assert ops.get("/ops/v1/evals/status").json()["status"] == "idle"
        assert ops.post("/ops/v1/evals/cancel").status_code == 409


def test_forecast_accuracy_is_admin_only_and_scores_past_forecasts(app, client):
    assert client.get("/ops/v1/forecast-accuracy").status_code == 401
    register(client); setup_profile(client); import_history(client)
    client.post("/api/v1/forecasts", json={"horizon_days": 7})
    profile = client.get("/api/v1/profile").json()
    moved = {**profile["profile"], "as_of": "2026-10-28", "scheduled_flows": []}        # the history now covers that week
    client.put("/api/v1/profile", json={"profile": moved, "expected_version": profile["version"]})
    rows = ["date,description,amount_minor,currency,kind"] + [f"2026-10-{d},Cafe meal,-120,INR,expense" for d in range(19, 29)]
    preview = client.post("/api/v1/imports", json={"csv_text": "\n".join(rows)}).json()
    client.post(f"/api/v1/imports/{preview['id']}/confirm", json={"expected_version": preview["version"]})
    client.post("/api/v1/forecasts", json={"horizon_days": 7})                           # scores the earlier run
    data = operator(app).get("/ops/v1/forecast-accuracy").json()
    realized = data["realized"]["by_method"]
    assert realized and realized[0]["method"] == "trailing_mean" and realized[0]["count"] == 1
    assert abs(realized[0]["median_wape"] - 16.7) < 0.1                                  # forecast ₹1/day vs actual ₹1.20/day
    assert "customer_a" not in str(data)
