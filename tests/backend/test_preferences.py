"""Preferences: opt-in sharing of de-identified examples for evaluation goldens."""
import json

from conftest import register, setup_profile
from test_admin import operator


def add(client, description, key):
    body = {"idempotency_key": key, "transaction": {"date": "2026-10-10", "description": description, "amount_minor": -45_678,
        "currency": "INR", "kind": "expense"}}
    return client.post("/api/v1/transactions", json=body).json()["id"]


def label(client, tx_id, category):
    version = client.get("/api/v1/profile").json()["version"]
    return client.post(f"/api/v1/transactions/{tx_id}/category", json={"expected_version": version, "category": category})


def test_sharing_is_off_by_default_and_shares_only_after_opting_in(app, client):
    register(client); setup_profile(client)
    assert client.get("/api/v1/preferences").json() == {"share_categories": False, "share_questions": False,
        "contributed": {"categorizer": 0, "coordinator": 0}}
    first = add(client, "UPI/DR/412345678901/SWIGGY/YESB/swiggy@ybl/Payment", "pref-test-0001")
    label(client, first, "food delivery")
    client.post("/api/v1/assistant/message", json={"question": "hi fina"})
    assert client.get("/api/v1/preferences").json()["contributed"] == {"categorizer": 0, "coordinator": 0}   # nothing shared yet

    on = client.put("/api/v1/preferences", json={"share_categories": True, "share_questions": True}).json()
    assert on["share_categories"] and on["contributed"]["categorizer"] == 1          # earlier confirmed labels are shared too
    label(client, add(client, "BESCOM ELECTRICITY BILL call 9876543210", "pref-test-0002"), "utilities")
    client.post("/api/v1/assistant/message", json={"question": "6004 Goa", "page": "goals"})
    assert client.get("/api/v1/preferences").json()["contributed"] == {"categorizer": 2, "coordinator": 1}

    ops = operator(app)
    shared = ops.get("/ops/v1/contributions").json()
    text = json.dumps(shared)
    assert "customer_a" not in text and "swiggy@ybl" not in text and "412345678901" not in text and "9876543210" not in text
    assert {i["actor"] for i in shared["items"]} == {shared["items"][0]["actor"]} and shared["items"][0]["actor"].startswith("u-")
    draft = ops.get("/ops/v1/contributions/export", params={"kind": "categorizer"}).json()
    assert draft["agent"] == "categorizer" and {c["accept"][0] for c in draft["cases"]} == {"food delivery", "utilities"}
    assert all(c["amount_minor"] == -46_000 for c in draft["cases"])                  # rounded to ₹10
    coord = ops.get("/ops/v1/contributions/export", params={"kind": "coordinator"}).json()["cases"][0]
    assert coord["routes"] == ["goal"] and coord["page"] == "goals" and "needs-review" in coord["tags"]
    reject = next(i for i in shared["items"] if i["kind"] == "coordinator")
    assert ops.delete(f"/ops/v1/contributions/{reject['id']}").status_code == 204
    assert [e["action"] for e in ops.get("/ops/v1/audit").json()["entries"]][:3] == ["contribution_reject", "contribution_export",
        "contribution_export"]

    off = client.put("/api/v1/preferences", json={"share_categories": False, "share_questions": True}).json()
    assert off["contributed"]["categorizer"] == 0                                     # turning a choice off deletes it
    gone = client.delete("/api/v1/preferences/contributions").json()
    assert gone == {"share_categories": False, "share_questions": False, "contributed": {"categorizer": 0, "coordinator": 0}}
    assert ops.get("/ops/v1/contributions").json()["items"] == []
