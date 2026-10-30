import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app.config import ROOT, Settings
from backend.app.finance import categorize_many
from backend.app.main import create_app
from backend.app.mesh import MeshGateway, NumericContext
from backend.app.prompts import PromptCatalog, ROLES
from backend.app.schemas import TransactionInput
from backend.app.storage import Policy
from conftest import ORIGIN, register, setup_profile


def transaction(description="Cafe equipment", **overrides):
    return {"date": "2026-10-18", "description": description, "amount_minor": -100,
            "currency": "INR", "kind": "expense", **overrides}


def completion(content, finish="stop"):
    return httpx.Response(200, json={"id": "mock", "object": "chat.completion", "created": 0,
        "model": "test-default", "choices": [{"index": 0, "finish_reason": finish,
        "message": {"role": "assistant", "content": content}}]})


def response_for(request, category="workspace equipment"):
    body = json.loads(request.content)
    facts = json.loads(body["messages"][1]["content"])["untrusted_transaction_facts"]
    refs = [tx["transaction_ref"] for tx in facts["transactions"]]
    return {"agent": "categorizer", "prompt_version": facts["prompt_version"],
        "snapshot_ref": facts["snapshot_ref"],
        "status": "completed" if category else "needs_clarification",
        "payload": {"category_schema_version": "dynamic-categories-v1", "decisions": [
            {"transaction_ref": ref, "category_id": category, "reason": "Equipment purchase"}
            for ref in refs]}, "evidence_refs": refs, "questions": [], "limitations": []}


def gateway(handler, policy=lambda: "RUNNING", risk_model="test-risk"):
    return MeshGateway("test-key", "test-default", PromptCatalog(ROOT / "prompts"),
        policy, httpx.MockTransport(handler), risk_model=risk_model)


def test_batches_owner_override_provenance_and_minimized_input():
    requests = []
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["model"] == "test-default"
        assert body["response_format"]["type"] == "json_schema"
        facts = json.loads(body["messages"][1]["content"])["untrusted_transaction_facts"]
        assert "categories" not in facts
        assert "food" in facts["existing_categories"]
        assert "owner-secret" not in str(body)
        assert "alice@example.com" not in str(body)
        assert "123456789012" not in str(body)
        return completion(json.dumps(response_for(request)))
    mesh = gateway(handler)
    try:
        rows = [transaction("owner-secret", category="food")] + [
            transaction(f"Cafe equipment store {chr(97 + i % 26)}{chr(97 + i // 26)} alice@example.com 123456789012") for i in range(26)]
        result = categorize_many(rows, mesh)
        assert len(requests) == 2
        assert result[0]["category_source"] == "owner"
        assert all(row["category"] == "workspace equipment" for row in result[1:])
        assert result[1]["category_provenance"]["prompt_version"] == "v6"
        assert result[1]["category_provenance"]["model"] == "test-default"
        assert result[1]["amount_minor"] == -100
    finally:
        mesh.close()


def test_repeated_merchant_lines_are_categorized_once():
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return completion(json.dumps(response_for(request)))
    mesh = gateway(handler)
    try:
        rows = [transaction(f"UPI/DR/{400000000000 + i}/SWIGGY/YESB/swiggy@ybl/Payment") for i in range(30)]
        result = categorize_many(rows, mesh)
        sent = json.loads(requests[0]["messages"][1]["content"])["untrusted_transaction_facts"]["transactions"]
        assert len(requests) == 1 and len(sent) == 1 and len(result) == 30
        assert all(r["category"] == result[0]["category"] and r["category_source"] == "model" for r in result)
    finally:
        mesh.close()


@pytest.mark.parametrize("failure", ["invalid_category", "duplicate", "missing", "foreign_ref",
    "snapshot", "extra", "malformed", "truncated", "http_error"])
def test_invalid_or_failed_output_never_sets_a_category(failure):
    def handler(request):
        data = response_for(request)
        decisions = data["payload"]["decisions"]
        if failure == "invalid_category": decisions[0]["category_id"] = "x" * 65
        if failure == "duplicate": decisions[1]["transaction_ref"] = decisions[0]["transaction_ref"]
        if failure == "missing": decisions.pop()
        if failure == "foreign_ref": decisions[0]["transaction_ref"] = "other_tenant"
        if failure == "snapshot": data["snapshot_ref"] = "old"
        if failure == "extra": decisions[0]["amount_minor"] = 0
        if failure == "http_error": return httpx.Response(503, json={"error": "private"})
        return completion("not json" if failure == "malformed" else json.dumps(data),
                          "length" if failure == "truncated" else "stop")
    mesh = gateway(handler)
    try:
        rows = categorize_many([transaction("Cafe one"), transaction("Cafe two")], mesh)
        assert all(row["category"] == "uncategorized" for row in rows)
        assert all(row["category_status"] == "unavailable" for row in rows)
    finally:
        mesh.close()


def test_ambiguous_model_decision_abstains():
    mesh = gateway(lambda request: completion(json.dumps(response_for(request, None))))
    try:
        row = categorize_many([transaction("XYZ123")], mesh)[0]
        assert row["category_status"] == "needs_clarification"
        assert row["category_source"] == "unresolved"
    finally:
        mesh.close()


def test_pause_blocks_dispatch_and_discards_late_result():
    mode = "AI_PAUSED"
    requests = []
    def handler(request):
        nonlocal mode
        requests.append(request)
        mode = "AI_PAUSED"
        return completion(json.dumps(response_for(request)))
    mesh = gateway(handler, lambda: mode)
    try:
        assert mesh.categorize([transaction()])[0]["category_status"] == "paused"
        assert not requests
        mode = "ADVICE_PAUSED"
        assert mesh.categorize([transaction()])[0]["category_status"] == "paused"
        assert len(requests) == 1
    finally:
        mesh.close()


def test_all_roles_share_one_model_except_risk_and_risk_has_no_fallback():
    requests = []
    def handler(request):
        requests.append(json.loads(request.content)["model"])
        return completion("{}")
    mesh = gateway(handler)
    try:
        for role in ROLES:
            mesh.evaluate(role, NumericContext(currency="INR", horizon_days=30, daily_variable_minor=100))
            assert requests[-1] == ("test-risk" if role == "risk" else "test-default")
        mesh.risk_model = ""
        with pytest.raises(ValueError, match="MESH_RISK_MODEL"):
            mesh.evaluate("risk", NumericContext(currency="INR", horizon_days=30, daily_variable_minor=100))
        assert len(requests) == len(ROLES)
    finally:
        mesh.close()


def test_manual_and_csv_use_mesh_only_after_input_validation_and_are_idempotent(tmp_path):
    requests = []
    def handler(request):
        requests.append(request)
        return completion(json.dumps(response_for(request)))
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'mesh.db'}", environment="test",
        mesh_api_key="test-key", mesh_model="test-default", mesh_risk_model="test-risk"),
        mesh_transport=httpx.MockTransport(handler))
    with TestClient(app, headers=ORIGIN) as client:
        register(client)
        setup_profile(client)
        with app.state.sessions() as db:
            db.get(Policy, 1).mode = "RUNNING"
            db.commit()
        body = {"idempotency_key": "mesh-test-123", "transaction": transaction()}
        assert client.post("/api/v1/transactions", json=body).status_code == 201
        assert client.post("/api/v1/transactions", json=body).json()["duplicate"]
        assert len(requests) == 1
        csv = "date,description,amount_minor,currency,kind\n2026-10-18,Cafe equipment,-100,INR,expense"
        preview = client.post("/api/v1/imports", json={"csv_text": csv})
        assert preview.status_code == 201
        assert preview.json()["rows"][0]["category"] == "workspace equipment"
        assert client.post("/api/v1/imports", json={"csv_text": csv}).status_code == 201
        assert len(requests) == 2
        assert client.post("/api/v1/imports", json={"csv_text": csv + "\ninvalid"}).status_code == 422
        assert client.post("/api/v1/imports", json={"csv_text": csv.replace("INR", "USD")}).status_code == 422
        assert len(requests) == 2
        stored = client.get("/api/v1/transactions").json()["items"][0]
        assert stored["category"] == "workspace equipment"
        assert stored["category_source"] == "model"
        latest_facts = json.loads(json.loads(requests[-1].content)["messages"][1]["content"])["untrusted_transaction_facts"]
        assert latest_facts["existing_categories"] == ["workspace equipment"]
        forecast = client.post("/api/v1/forecasts", json={}).json()
        assert forecast["analysis"]["by_category"] == {"workspace equipment": 100}

        # A second account must not inherit the first account's category names.
        register(client, "customer_b")
        setup_profile(client)
        assert client.post("/api/v1/transactions", json=body).status_code == 201
        other_facts = json.loads(json.loads(requests[-1].content)["messages"][1]["content"])["untrusted_transaction_facts"]
        assert other_facts["existing_categories"] == []


@pytest.mark.parametrize("label", ["", "   ", "x" * 65, "bad\x00label", "<script>", 123])
def test_category_text_validation_rejects_invalid_owner_and_model_values(label):
    with pytest.raises(ValueError):
        TransactionInput.model_validate(transaction(category=label))
    mesh = gateway(lambda request: completion(json.dumps(response_for(request, label))))
    try:
        assert mesh.categorize([transaction()])[0]["category_status"] == "unavailable"
    finally:
        mesh.close()


def test_owner_defined_categories_work_in_manual_and_csv_without_ai(client):
    register(client)
    setup_profile(client)
    body = {"idempotency_key": "custom-category-123",
            "transaction": transaction(category="  Creative   Learning  ")}
    assert client.post("/api/v1/transactions", json=body).status_code == 201
    row = client.get("/api/v1/transactions").json()["items"][0]
    assert row["category"] == "creative learning"
    assert row["category_source"] == "owner"
    csv = "date,description,amount_minor,currency,kind,category\n2026-10-18,Donation,-100,INR,expense,Community Projects"
    preview = client.post("/api/v1/imports", json={"csv_text": csv})
    assert preview.status_code == 201
    assert preview.json()["rows"][0]["category"] == "community projects"
    assert preview.json()["rows"][0]["category_source"] == "owner"


def test_model_category_names_are_normalized_for_grouping():
    mesh = gateway(lambda request: completion(json.dumps(response_for(request, "  Pet   Care  "))))
    try:
        assert mesh.categorize([transaction()])[0]["category"] == "pet care"
    finally:
        mesh.close()


def test_owner_can_relabel_and_note_requires_ai(client):
    register(client)
    setup_profile(client)
    body = {"idempotency_key": "relabel-test-123", "transaction": transaction("Crossword bookstore")}
    assert client.post("/api/v1/transactions", json=body).status_code == 201
    row = client.get("/api/v1/transactions").json()["items"][0]
    assert row["category_status"] == "not_configured"
    version = client.get("/api/v1/profile").json()["version"]
    url = f"/api/v1/transactions/{row['id']}/category"
    assert client.post(url, json={"expected_version": version, "note": "books"}).status_code == 409
    assert client.post(url, json={"expected_version": version}).status_code == 422
    assert client.post(url, json={"expected_version": version, "category": "x", "note": "y"}).status_code == 422
    assert client.post(url, json={"expected_version": version - 1, "category": "Books"}).status_code == 409
    updated = client.post(url, json={"expected_version": version, "category": "  Books "}).json()
    assert updated["category"] == "books" and updated["category_source"] == "owner"
    assert "category_status" not in updated
    assert updated["category_history"] == [{"category": "uncategorized", "source": "unresolved", "status": "not_configured"}]
    assert updated["version"] == version + 1
    assert client.post("/api/v1/transactions/missing-id/category",
        json={"expected_version": updated["version"], "category": "books"}).status_code == 404


def test_recategorize_retries_unresolved_rows_and_note_reaches_model(tmp_path):
    requests = []
    def handler(request):
        facts = json.loads(json.loads(request.content)["messages"][1]["content"])["untrusted_transaction_facts"]
        requests.append(facts)
        note = facts["transactions"][0].get("owner_note")
        return completion(json.dumps(response_for(request, "home services" if note else "books")))
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'retry.db'}", environment="test",
        mesh_api_key="test-key", mesh_model="test-default"), mesh_transport=httpx.MockTransport(handler))
    with TestClient(app, headers=ORIGIN) as client:
        register(client)
        setup_profile(client)
        for i, text in enumerate(["Crossword bookstore", "Urban Company"]):
            body = {"idempotency_key": f"retry-test-{i}00", "transaction": transaction(text)}
            assert client.post("/api/v1/transactions", json=body).status_code == 201
        assert not requests  # AI_PAUSED by default
        version = client.get("/api/v1/profile").json()["version"]
        assert client.post("/api/v1/transactions/recategorize", json={"expected_version": version}).status_code == 409
        with app.state.sessions() as db:
            db.get(Policy, 1).mode = "RUNNING"
            db.commit()
        run = client.post("/api/v1/forecasts", json={}).json()
        result = client.post("/api/v1/transactions/recategorize", json={"expected_version": version}).json()
        assert result == {"attempted": 2, "categorized": 2, "version": version + 1}
        assert client.get(f"/api/v1/runs/{run['id']}").json()["stale"] is True
        rows = client.get("/api/v1/transactions").json()["items"]
        assert {r["category"] for r in rows} == {"books"}
        assert all(r["category_history"][0]["status"] == "paused" for r in rows)
        assert client.post("/api/v1/transactions/recategorize",
            json={"expected_version": result["version"]}).json()["attempted"] == 0

        target = next(r for r in rows if r["description"] == "Urban Company")
        updated = client.post(f"/api/v1/transactions/{target['id']}/category",
            json={"expected_version": result["version"], "note": "Home deep cleaning"}).json()
        assert requests[-1]["transactions"][0]["owner_note"] == "Home deep cleaning"
        assert requests[-1]["transactions"][0]["description"] == "Urban Company"
        assert updated["category"] == "home services" and updated["category_source"] == "model"
        assert updated["category_note"] == "Home deep cleaning"
        assert [h["category"] for h in updated["category_history"]] == ["uncategorized", "books"]


def test_ai_categorization_is_capped_at_twenty_requests_a_day(tmp_path):
    requests = []
    def handler(request):
        requests.append(request)
        return completion(json.dumps(response_for(request, None)))  # model always abstains
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'limit.db'}", environment="test",
        mesh_api_key="test-key", mesh_model="test-default"), mesh_transport=httpx.MockTransport(handler))
    with TestClient(app, headers=ORIGIN) as client:
        register(client)
        setup_profile(client)
        body = {"idempotency_key": "limit-test-100", "transaction": transaction("UPI 8273645")}
        assert client.post("/api/v1/transactions", json=body).status_code == 201
        listing = client.get("/api/v1/transactions").json()
        row = listing["items"][0]
        assert listing["ai_daily_limit"] == 20 and listing["ai_used_today"] == 0
        with app.state.sessions() as db:
            db.get(Policy, 1).mode = "RUNNING"
            db.commit()
        version = client.get("/api/v1/profile").json()["version"]
        url = f"/api/v1/transactions/{row['id']}/category"
        version = client.post("/api/v1/transactions/recategorize", json={"expected_version": version}).json()["version"]
        for _ in range(19):                                      # no per-transaction limit any more
            version = client.post(url, json={"expected_version": version, "note": "household help"}).json()["version"]
        assert len(requests) == 20 and client.get("/api/v1/transactions").json()["ai_used_today"] == 20
        blocked = client.post(url, json={"expected_version": version, "note": "household help"})
        assert blocked.status_code == 429 and "today's 20" in blocked.json()["detail"]
        assert client.post("/api/v1/transactions/recategorize", json={"expected_version": version}).status_code == 429
        assert len(requests) == 20
        owner = client.post(url, json={"expected_version": version, "category": "household help"}).json()
        assert owner["category_source"] == "owner"                                   # setting it yourself is never limited


def test_one_label_updates_every_transaction_from_that_merchant(client):
    register(client)
    setup_profile(client)
    texts = ["UPI/DR/412345678901/Zomato Ltd/HDFC/zomato@hdfcbank/Food", "zomato", "UPI-ZOMATO-ZOMATO@HDFC-512345678901",
        "SWIGGY INSTAMART", "Zomato gift card"]
    for i, text in enumerate(texts):
        assert client.post("/api/v1/transactions", json={"idempotency_key": f"spread-test-{i}00", "transaction": transaction(text)}).status_code == 201
    refund = {**transaction("ZOMATO REFUND"), "amount_minor": 100, "kind": "refund"}
    client.post("/api/v1/transactions", json={"idempotency_key": "spread-test-900", "transaction": refund})
    rows = {r["description"]: r for r in client.get("/api/v1/transactions").json()["items"]}
    version = client.get("/api/v1/profile").json()["version"]
    version = client.post(f"/api/v1/transactions/{rows['Zomato gift card']['id']}/category",
        json={"expected_version": version, "category": "gifts"}).json()["version"]
    first = client.post(f"/api/v1/transactions/{rows['zomato']['id']}/category", json={"expected_version": version, "category": "food delivery"}).json()
    assert first["also_updated"] == 2
    rows = {r["description"]: r for r in client.get("/api/v1/transactions").json()["items"]}
    assert rows[texts[0]]["category"] == rows[texts[2]]["category"] == "food delivery"
    assert rows[texts[0]]["category_source"] == "merchant_rule" and "zomato" in rows[texts[0]]["category_reason"]
    assert rows["Zomato gift card"]["category"] == "gifts"                 # the owner's own label is never overwritten
    assert rows["ZOMATO REFUND"]["category"] != "food delivery"             # refunds are a different kind
    assert rows["SWIGGY INSTAMART"]["category"] != "food delivery"
    later = client.post("/api/v1/transactions", json={"idempotency_key": "spread-test-990", "transaction": transaction("POS ZOMATO 88123")}).json()
    new = next(r for r in client.get("/api/v1/transactions").json()["items"] if r["id"] == later["id"])
    assert new["category"] == "food delivery" and new["category_source"] == "merchant_rule"   # new rows follow it too


def test_categorizer_learns_from_owner_corrections_within_one_tenant(tmp_path):
    facts_seen = []
    def handler(request):
        facts = json.loads(json.loads(request.content)["messages"][1]["content"])["untrusted_transaction_facts"]
        facts_seen.append(facts)
        return completion(json.dumps(response_for(request, "books" if facts["owner_examples"] else None)))
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'learn.db'}", environment="test",
        mesh_api_key="test-key", mesh_model="test-default"), mesh_transport=httpx.MockTransport(handler))
    with TestClient(app, headers=ORIGIN) as client:
        register(client)
        setup_profile(client)
        with app.state.sessions() as db:
            db.get(Policy, 1).mode = "RUNNING"
            db.commit()
        first = {"idempotency_key": "learn-test-100", "transaction": transaction("Crossword bookstore")}
        assert client.post("/api/v1/transactions", json=first).status_code == 201
        assert facts_seen[-1]["owner_examples"] == []
        row = client.get("/api/v1/transactions").json()["items"][0]
        assert row["category"] == "uncategorized"
        version = client.get("/api/v1/profile").json()["version"]
        client.post(f"/api/v1/transactions/{row['id']}/category", json={"expected_version": version, "category": "Books"})

        calls = len(facts_seen)
        same = {"idempotency_key": "learn-test-150", "transaction": transaction("Crossword bookstore Phoenix mall")}
        assert client.post("/api/v1/transactions", json=same).status_code == 201
        followed = next(r for r in client.get("/api/v1/transactions").json()["items"] if "Phoenix" in r["description"])
        assert followed["category"] == "books" and followed["category_source"] == "merchant_rule" and len(facts_seen) == calls

        second = {"idempotency_key": "learn-test-200", "transaction": transaction("Sapna Book House")}
        assert client.post("/api/v1/transactions", json=second).status_code == 201
        assert facts_seen[-1]["owner_examples"] == [{"description": "Crossword bookstore", "category": "books"}]
        learned = next(r for r in client.get("/api/v1/transactions").json()["items"] if "Sapna" in r["description"])
        assert learned["category"] == "books" and learned["category_provenance"]["owner_examples"] == 1

        # Another customer never receives this owner's examples.
        register(client, "customer_b")
        setup_profile(client)
        other = {"idempotency_key": "learn-test-300", "transaction": transaction("Crossword bookstore")}
        assert client.post("/api/v1/transactions", json=other).status_code == 201
        assert facts_seen[-1]["owner_examples"] == []
