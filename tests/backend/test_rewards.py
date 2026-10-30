from datetime import date, timedelta

from fastapi.testclient import TestClient

from backend.app.rewards import allocate_proof, balance, earned, savings_deposits
from conftest import ORIGIN, import_history, register, setup_profile

GOAL = {"name": "boys trip", "target_minor": 5_000_000, "target_date": "2026-12-30", "saved_minor": 500_000}


def rewards(client):
    return client.get("/api/v1/rewards").json()


TODAY = date.today()


def move(client, amount_minor, description="Transfer to savings account", day=TODAY.isoformat()):
    """Import one transfer: negative moves money into savings, positive takes it back."""
    csv = f"date,description,amount_minor,currency,kind\n{day},{description},{amount_minor},INR,transfer"
    p = client.post("/api/v1/imports", json={"csv_text": csv}).json()
    client.post(f"/api/v1/imports/{p['id']}/confirm", json={"expected_version": p["version"]})


def test_savings_proof_uses_transfers_into_savings_made_after_the_goal():
    tx = lambda d, desc, a, k="transfer": {"date": d, "description": desc, "amount_minor": a, "kind": k, "category": None}
    ledger = [tx("2026-01-05", "To savings account", -2_000_000), tx("2026-03-01", "HDFC FD booking", -1_000_000),
        tx("2026-04-01", "Monthly SIP", -500_000), tx("2026-04-02", "From savings account", 300_000),
        tx("2026-02-01", "Salary savings bonus", 9_000_000, "income"), tx("2026-02-02", "To Rahul", -700_000)]
    assert savings_deposits(ledger) == [["2026-01-05", 2_000_000], ["2026-03-01", 1_000_000], ["2026-04-01", 200_000]]
    goal = lambda i, created, target: {"id": i, "status": "achieved", "saved_minor": target, "target_minor": target, "created": created}
    goals = [goal("a", "2026-02-15", 1_000_000), goal("b", "2026-02-20", 500_000)]
    assert allocate_proof(goals, ledger, set()) == {"a": 1_000_000, "b": 200_000}   # January money predates both goals
    assert allocate_proof(goals, ledger, {"b"}) == {"b": 500_000, "a": 700_000}     # a rewarded goal keeps first claim


def test_points_never_go_negative_and_reversals_no_longer_count():
    legacy = [{"kind": "goal", "points": 1000, "ref": "g"}, {"kind": "achievement", "points": 250, "ref": "first_goal"},
        {"kind": "redeem", "points": -1000, "ref": "food_30"}, {"kind": "reversal", "points": -1000, "ref": "g"}]
    assert balance(legacy) == 0 and earned(legacy) == 250                    # unproven goal points and reversals ignored


def test_goal_points_need_savings_made_after_the_goal_and_are_never_taken_back(client):
    register(client); setup_profile(client)
    gid = client.post("/api/v1/goals", json=GOAL).json()["id"]
    assert client.patch(f"/api/v1/goals/{gid}", json={"status": "achieved"}).json()["points_change"] == 0  # not fully saved
    unproven = client.patch(f"/api/v1/goals/{gid}", json={"saved_minor": 5_000_000}).json()
    assert unproven["points_change"] == 0 and unproven["proof"]["found_minor"] == 0 and not unproven["proof"]["verified"]
    created = date.fromisoformat(unproven["proof"]["since"])       # the goal's date in the owner's timezone, not the test clock
    move(client, -3_000_000, day=(created - timedelta(days=1)).isoformat())                    # before the goal: doesn't count
    move(client, -3_000_000, day=created.isoformat())
    assert client.get("/api/v1/goals").json()["goals"][0]["proof"]["found_minor"] == 3_000_000           # partly proven: no points
    move(client, -2_000_000, "Monthly SIP mutual fund", (created + timedelta(days=1)).isoformat())
    goals = client.get("/api/v1/goals").json()                                                           # proof arrived: settled on load
    assert goals["goals"][0]["proof"]["verified"] and [u["kind"] for u in goals["unlocked"]] == ["goal", "achievement"]
    assert client.get("/api/v1/goals").json()["unlocked"] == []                                          # celebrated once
    assert rewards(client)["balance"] == 1250                                                              # + First Summit bonus
    assert client.patch(f"/api/v1/goals/{gid}", json={"status": "active"}).json()["points_change"] == 0      # no punishment
    assert client.patch(f"/api/v1/goals/{gid}", json={"status": "achieved"}).json()["points_change"] == 0    # no double award
    move(client, 1_000_000, "From savings account", (created + timedelta(days=2)).isoformat())          # money taken back out
    data = rewards(client)
    assert data["balance"] == 1250 and data["earned_total"] == 1250 and all(h["points"] > 0 for h in data["history"])
    assert data["worth_minor"] == 3750                                                                       # 1,000 = ₹30


def test_referral_credits_both_people_once(client, app):
    register(client)
    code = rewards(client)["referral"]["code"]
    with TestClient(app, headers=ORIGIN) as friend:
        r = friend.post("/api/v1/auth/register", json={"username": "friend_one", "password": "synthetic-password-123", "referral_code": code})
        friend.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        assert rewards(friend)["balance"] == 250
    with TestClient(app, headers=ORIGIN) as stranger:
        r = stranger.post("/api/v1/auth/register", json={"username": "stranger", "password": "synthetic-password-123", "referral_code": "NOPE1234"})
        stranger.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        assert r.status_code == 201 and rewards(stranger)["balance"] == 0
    data = rewards(client)
    assert data["balance"] == 600 and data["referral"]["friends_joined"] == 1                    # + Better Together bonus


def test_redeem_needs_points_and_is_idempotent(client):
    register(client); setup_profile(client)
    blocked = client.post("/api/v1/rewards/redeem", json={"voucher_id": "food_30", "idempotency_key": "redeem-test-1"})
    assert blocked.status_code == 409 and "1000 more" in blocked.json()["detail"]
    gid = client.post("/api/v1/goals", json={**GOAL, "saved_minor": 5_000_000}).json()["id"]
    move(client, -5_000_000)
    client.patch(f"/api/v1/goals/{gid}", json={"status": "achieved"})
    first = client.post("/api/v1/rewards/redeem", json={"voucher_id": "food_30", "idempotency_key": "redeem-test-2"}).json()
    again = client.post("/api/v1/rewards/redeem", json={"voucher_id": "food_30", "idempotency_key": "redeem-test-2"}).json()
    assert first["code"].startswith("DEMO-") and again == {**{k: v for k, v in first.items() if k != "unlocked"}, "duplicate": True}
    assert [u["id"] for u in first["unlocked"]] == ["first_voucher"]
    data = rewards(client)
    assert data["balance"] == 250 and len(data["redeemed"]) == 1 and data["redeemed"][0]["code"] == first["code"]


def test_vouchers_follow_the_owners_spending(client):
    register(client); setup_profile(client)
    lines = ["date,description,amount_minor,currency,kind,category"]
    lines += [f"{date(2026, 10, 18) - timedelta(days=d)},Swiggy order,-50000,INR,expense,food delivery" for d in range(20)]
    lines += [f"{date(2026, 10, 18) - timedelta(days=d)},Uber,-10000,INR,expense,transport" for d in range(5)]
    p = client.post("/api/v1/imports", json={"csv_text": "\n".join(lines)}).json()
    client.post(f"/api/v1/imports/{p['id']}/confirm", json={"expected_version": p["version"]})
    vouchers = rewards(client)["vouchers"]
    assert [v["type"] for v in vouchers[:3]] == ["food", "food", "transport"]
    assert vouchers[0]["reason"] == {"category": "food delivery", "spent_minor": 1_000_000, "rising_percent": None}
    assert not any(v["recommended"] for v in vouchers if v["type"] in {"shopping", "entertainment"})


def test_fina_keeps_track_of_goals_in_chat(client):
    register(client); setup_profile(client); import_history(client)
    gid = client.post("/api/v1/goals", json=GOAL).json()["id"]
    chat = lambda q: client.post("/api/v1/assistant/message", json={"question": q}).json()
    status = chat("i have saved boys trip in goals")
    assert status["route"] == "goal_status" and "boys trip" in status["answer"] and "₹45,000 to go" in status["answer"]
    saving = chat("I saved 2000 for boys trip")
    assert saving["action"] == {"type": "confirm_goal_saving", "goal_id": gid, "name": "boys trip", "add_minor": 200_000,
        "saved_minor": 500_000, "target_minor": 5_000_000}
    assert "₹5,000 to ₹7,000 of ₹50,000" in saving["answer"]
    ask = chat("i have saved")
    assert ask["route"] == "goal_saving_ask" and "boys trip" in ask["answer"]


def test_achievements_unlock_once_with_progress(client):
    register(client); setup_profile(client)
    badges = {a["id"]: a for a in rewards(client)["achievements"]}
    assert not badges["first_goal"]["earned"] and badges["three_goals"]["progress"] == 0
    move(client, -10_000_000)                                                   # proof for two goals, not three
    for i in range(3):
        gid = client.post("/api/v1/goals", json={**GOAL, "name": f"goal {i}", "saved_minor": 5_000_000}).json()["id"]
        client.patch(f"/api/v1/goals/{gid}", json={"status": "achieved"})
    data = rewards(client)
    badges = {a["id"]: a for a in data["achievements"]}
    assert badges["first_goal"]["earned"] and badges["three_goals"]["progress"] == 2   # one transfer proves one goal
    move(client, -5_000_000, "RD instalment")
    data = rewards(client)
    badges = {a["id"]: a for a in data["achievements"]}
    assert badges["three_goals"]["earned"] and badges["five_goals"]["progress"] == 3
    assert [u["id"] for u in data["unlocked"]] == ["goal:" + gid, "three_goals"]
    assert data["balance"] == 3 * 1000 + 250 + 500
    assert rewards(client)["balance"] == data["balance"]                                          # bonuses awarded once
