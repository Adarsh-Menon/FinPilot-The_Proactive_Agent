from fastapi.testclient import TestClient
from sqlalchemy import select
from backend.app.storage import User, Metric, Transaction, Profile, Policy
from backend.app.main import passwords
from conftest import register, setup_profile, import_history, ORIGIN


def test_auth_session_csrf_and_logout(client):
    assert client.get("/api/v1/profile").status_code == 401
    register(client)
    assert "HttpOnly" in client.cookies.jar._cookies["testserver.local"]["/"]["finpilot_session"]._rest
    profile = client.get("/api/v1/profile")
    assert profile.status_code == 200
    assert client.post("/api/v1/auth/logout", headers={"X-CSRF-Token":"wrong"}).status_code == 403
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/profile").status_code == 401


def test_origin_and_password_not_echoed(client):
    res = client.post("/api/v1/auth/login", headers={"Origin":"https://evil.example"}, json={"username":"test", "password":"secret-password-123"})
    assert res.status_code == 403
    bad = client.post("/api/v1/auth/register", json={"username":"test", "password":"PRIVATE"})
    assert bad.status_code == 422
    assert "PRIVATE" not in bad.text


def test_forecast_exact_values_and_scenario_no_ledger_mutation(client):
    register(client); setup_profile(client); import_history(client)
    result = client.post("/api/v1/forecasts", json={"horizon_days":30})
    assert result.status_code == 200, result.text
    run = result.json()
    assert run["forecast"]["daily_variable_minor"] == 100
    assert run["forecast"]["timeline"][-1]["cash_minor"] == 14000
    assert run["analysis"]["comparisons"][0]["percent_change"] == 0
    before = client.get("/api/v1/transactions").json()
    scenario = client.post("/api/v1/scenarios", json={"baseline_id":run["id"], "amount_minor":12000, "currency":"INR", "date":"2026-10-21"}).json()
    assert scenario["ending_delta_minor"] == -12000
    assert scenario["timeline"][-1]["cash_minor"] == 2000
    assert scenario["first_reserve_breach"] == "2026-11-08"
    assert client.get("/api/v1/transactions").json() == before
    assert run["ai_used"] is False


def test_forecast_steps_explain_each_calculation(client):
    register(client); setup_profile(client); import_history(client)
    run = client.post("/api/v1/forecasts", json={"horizon_days":30}).json()
    assert [s["node"] for s in run["steps"]] == ["analyzer", "forecaster", "numeric_checks"]
    assert all(s["details"] and s["duration_ms"] >= 0 for s in run["steps"])
    assert "Grouped spending into 1 category; 60 transactions are uncategorized." in run["steps"][0]["details"]
    assert "Average day-to-day spending is INR 1.00, from 60 days of non-recurring expenses." in run["steps"][1]["details"]
    assert run["checks"]["first_reserve_breach"] is None
    assert "Balance stays above your reserve." in run["steps"][2]["details"]


def test_missing_balance_never_fabricates_cash_or_affordability(client):
    register(client); setup_profile(client, balance=None); import_history(client)
    run = client.post("/api/v1/forecasts", json={}).json()
    assert run["forecast"]["basis"] == "net_cash_flow"
    assert all(r["cash_minor"] is None for r in run["forecast"]["timeline"])
    assert run["checks"]["affordability"] == "not_assessed"


def test_reimport_is_idempotent_and_identical_rows_are_preserved(client):
    register(client); setup_profile(client)
    text="date,description,amount_minor,currency,kind\n2026-10-18,Cafe,-100,INR,expense\n2026-10-18,Cafe,-100,INR,expense"
    p=client.post("/api/v1/imports",json={"csv_text":text}).json()
    url=f"/api/v1/imports/{p['id']}/confirm"
    assert client.post(url,json={"expected_version":p["version"]}).json()["imported"]==2
    assert client.post(url,json={"expected_version":p["version"]}).json()["already_confirmed"] is True
    assert client.post("/api/v1/imports",json={"csv_text":text}).json()["id"]==p["id"]
    assert client.get("/api/v1/transactions").json()["total"]==2


def test_overlap_requires_explicit_acknowledgement(client):
    register(client); setup_profile(client); import_history(client)
    p=client.post("/api/v1/imports",json={"csv_text":"date,description,amount_minor,currency,kind\n2026-08-20,Cafe meal,-100,INR,expense"}).json()
    url=f"/api/v1/imports/{p['id']}/confirm"
    assert client.post(url,json={"expected_version":p["version"]}).status_code==409
    assert client.post(url,json={"expected_version":p["version"],"acknowledge_possible_duplicates":True}).json()["imported"]==1


def test_cross_tenant_objects_and_operator_cannot_read_customer_data(client,app):
    register(client); setup_profile(client); p=import_history(client)
    run=client.post("/api/v1/forecasts",json={}).json()
    with TestClient(app,headers=ORIGIN) as other:
        register(other,"customer_b")
        assert other.get("/api/v1/transactions").json()["items"]==[]
        assert other.get(f"/api/v1/runs/{run['id']}").status_code==404
        assert other.post(f"/api/v1/imports/{p['id']}/confirm",json={"expected_version":0}).status_code==404
        assert other.post("/api/v1/scenarios",json={"baseline_id":run["id"],"amount_minor":100,"currency":"INR","date":"2026-10-21"}).status_code==404
        assert other.get("/ops/v1/overview").status_code==401
    with app.state.sessions() as db:
        db.add(User(username="operator",password_hash=passwords.hash("synthetic-password-123"),role="operator"));db.commit()
    with TestClient(app,headers=ORIGIN) as ops:
        login=ops.post("/ops/v1/auth/login",json={"username":"operator","password":"synthetic-password-123"}).json()
        ops.headers["X-CSRF-Token"]=login["csrf_token"]
        assert ops.get("/api/v1/transactions").status_code==401
        assert ops.get(f"/api/v1/runs/{run['id']}").status_code==401
        overview=ops.get("/ops/v1/overview")
        assert overview.status_code==200
        for private in ["customer_a","Cafe meal",run["id"]]:
            assert private not in overview.text
        counts=overview.json()["categorizations"]
        assert any(c["source"]=="unresolved" and c["status"]=="not_configured" and c["count"]>=60 for c in counts)
        assert all(set(c)=={"source","status","count"} for c in counts)
        revision=overview.json()["policy"]["revision"]
        update={"expected_revision":revision,"mode":"ADVICE_PAUSED","reason":"Synthetic policy test"}
        assert ops.put("/ops/v1/policy",json=update).status_code==200
        assert ops.put("/ops/v1/policy",json=update).status_code==409


def test_manual_idempotency_conflict_and_stale_forecast(client):
    register(client); setup_profile(client); import_history(client)
    run=client.post("/api/v1/forecasts",json={}).json()
    body={"idempotency_key":"synthetic-unique-123","transaction":{"date":"2026-10-18","description":"Cafe","amount_minor":-100,"currency":"INR","kind":"expense"}}
    first=client.post("/api/v1/transactions",json=body)
    assert first.status_code==201
    assert client.post("/api/v1/transactions",json=body).json()["id"]==first.json()["id"]
    body["transaction"]["amount_minor"]=-200
    assert client.post("/api/v1/transactions",json=body).status_code==409
    assert client.get(f"/api/v1/runs/{run['id']}").json()["stale"] is True
    assert client.post("/api/v1/scenarios",json={"baseline_id":run["id"],"amount_minor":100,"currency":"INR","date":"2026-10-21"}).status_code==409


def test_currency_signs_and_csv_errors(client):
    register(client); setup_profile(client)
    tx={"date":"2026-10-18","description":"Cafe","amount_minor":100,"currency":"INR","kind":"expense"}
    assert client.post("/api/v1/transactions",json={"idempotency_key":"test-123456","transaction":tx}).status_code==422
    tx.update(amount_minor=-100,currency="USD")
    assert client.post("/api/v1/transactions",json={"idempotency_key":"test-123456","transaction":tx}).status_code==422
    bad="date,description,amount_minor,currency,kind\nnot-date,private description,wrong,INR,expense"
    res=client.post("/api/v1/imports",json={"csv_text":bad})
    assert res.status_code==422
    assert "private description" not in res.text
    assert client.get("/api/v1/transactions").json()["total"]==0


def test_policy_fail_closed_and_paused_calculations_available(client,app):
    register(client); setup_profile(client); import_history(client)
    assert client.post("/api/v1/forecasts",json={}).status_code==200  # AI_PAUSED still permits math.
    with app.state.sessions() as db:
        db.delete(db.get(Policy,1));db.commit()
    assert client.post("/api/v1/forecasts",json={}).status_code==503


def test_profile_optimistic_version_and_no_public_role_assignment(client):
    register(client); profile=setup_profile(client)
    assert client.put("/api/v1/profile",json={"profile":profile,"expected_version":0}).status_code==409
    assert client.post("/api/v1/auth/register",json={"username":"rogue","password":"synthetic-password-123","role":"operator"}).status_code==422


def test_request_size_cap(client):
    res=client.post("/api/v1/imports",content='x'*1_000_001)
    assert res.status_code==413
