"""The coordinator's final check on every reply, and how chat uses it."""
from backend.app.supervisor import SAFE_REPLY, verify
from conftest import import_history, register, setup_profile


def reply(answer, route="explain", action=None):
    return {"route": route, "answer": answer, "action": action}


def test_safe_and_grounded_replies_pass():
    assert verify("why is my balance going down?", reply("Your balance would be about ₹58,672 by 17 Nov."), [58672, 1611]) == []
    card = {"type": "confirm_goal", "goal": {"name": "Goa", "target_minor": 600400, "target_date": None}}
    assert verify("6004 Goa", reply("Here's the goal I caught.", "goal", card)) == []


def test_each_rule_blocks_what_it_should():
    assert verify("x", reply("Buy HDFC Bank shares this week.", "invest")) == ["named_security"]
    assert "guarantee" in verify("x", reply("I guarantee you will be fine."))                       # also a verdict
    assert verify("x", reply("You cannot afford this phone.")) == ["verdict"]
    assert verify("x", reply("Whether you can afford it depends on rent.")) == []                     # hedged is fine
    assert verify("x", reply("Ask rahul@okaxis for the money.")) == ["personal_data"]
    assert verify("x", reply("Call 9876543210.")) == ["personal_data"]
    card = {"type": "scenario", "amount_minor": 900000, "date": "2026-10-25"}
    assert verify("can I buy a 5,000 phone?", reply("Simulated.", "purchase", card)) == ["card_amount_not_said"]
    assert verify("why?", reply("You'll have ₹99,999 left."), [58672]) == ["ungrounded_amount"]
    assert verify("x", reply("", "nonsense")) == ["unknown_route", "empty_or_too_long"]


def test_chat_replies_are_marked_verified(client):
    register(client); setup_profile(client); import_history(client)
    answer = client.post("/api/v1/assistant/message", json={"question": "hi fina"}).json()
    assert answer["verified"] is True and answer["answer"] != SAFE_REPLY
    draft = client.post("/api/v1/assistant/message", json={"question": "6004 Goa", "page": "goals"}).json()
    assert draft["verified"] is True and draft["action"]["goal"]["target_minor"] == 600400


def test_upcoming_payments_follow_the_owners_words():
    from backend.app.coordinator import validate
    from datetime import date
    payload = {"intent": "upcoming_flow", "flow": {"label": "Rent", "amount_minor": 2800000, "date": "2026-11-02"}}
    _, flow = validate(payload, "I have to pay 28000 rent on 2026-11-02", date(2026, 10, 18), date(2026, 11, 17), [])
    assert flow["amount_minor"] == -2800000                                   # the model's + sign is corrected
    wrong = {"type": "confirm_flow", "flow": {"label": "Rent", "amount_minor": 2800000, "date": "2026-11-02"}}
    assert verify("I have to pay 28000 rent", reply("Add this?", "upcoming_flow", wrong)) == ["card_direction_contradicts"]
