from fastapi.testclient import TestClient
from pwdlib import PasswordHash

from backend.app.storage import User
from conftest import ORIGIN, register


def vote(client, item, agent="advisor", rating=1, reason=None, source="ai", surface="advice"):
    return client.post("/api/v1/feedback", json={"item_ref": item, "agent": agent, "surface": surface, "source": source,
        "rating": rating, "reason": reason})


def test_votes_are_one_per_output_and_summarised_for_admins(client, app):
    register(client)
    assert vote(client, "answer-0001", rating=1).status_code == 204
    assert vote(client, "answer-0001", rating=-1, reason="wrong_numbers").status_code == 204   # changed mind: same row
    assert vote(client, "answer-0002", rating=1).status_code == 204
    assert vote(client, "answer-0003", agent="forecaster", rating=-1, reason="confusing", source="calculation", surface="chat").status_code == 204
    assert vote(client, "answer-0004", reason="my bank details are 1234").status_code == 422     # only fixed reasons
    assert vote(client, "answer-0005", agent="someone").status_code == 422
    assert client.get("/ops/v1/feedback").status_code == 401
    with app.state.sessions() as db:
        db.add(User(username="fb_ops", password_hash=PasswordHash.recommended().hash("synthetic-password-123"), role="operator"))
        db.commit()
    with TestClient(app, headers=ORIGIN) as ops:
        ops.headers["X-CSRF-Token"] = ops.post("/ops/v1/auth/login", json={"username": "fb_ops", "password": "synthetic-password-123"}).json()["csrf_token"]
        summary = ops.get("/ops/v1/feedback").json()
        assert ops.post("/api/v1/feedback", json={}).status_code == 401      # operators cannot vote as customers
    assert summary["total"] == 3
    advisor = next(a for a in summary["agents"] if a["agent"] == "advisor")
    assert (advisor["up"], advisor["down"], advisor["satisfaction"]) == (1, 1, 50)
    assert advisor["reasons"] == {"wrong_numbers": 1} and advisor["by_source"] == {"ai": {"up": 1, "down": 1}}
    assert summary["agents"][0]["agent"] == "forecaster"                       # lowest satisfaction first
    assert all(set(r) == {"time", "actor", "agent", "surface", "source", "rating", "reason"} for r in summary["recent"])
    assert "customer_a" not in str(summary)
