"""The coordinator as supervisor: a final check on every reply Fina is about to show, whichever agent produced it.

The coordinator routes each message to an agent; this module verifies what comes back before the owner sees it.
All checks are deterministic (no extra model call, no added latency or cost). Model-written text has already been
grounded with placeholders and reviewed by the risk model; this layer covers every route, including code-written
notes, confirm cards and drafts. A failed check replaces the reply with a safe fallback in main.py.
"""
import re

from .assistant import HEDGED, VERDICT
from .coordinator import INTENTS, amounts_in, flow_direction

# Routes beyond the model's intents that code paths produce.
ROUTES = INTENTS | {"goal_status", "goal_saving", "goal_saving_ask", "help"}
NAMED_SECURITY = re.compile(r"\b(?:hdfc|sbi|icici|axis|kotak|nippon|tata|reliance|infosys|tcs|adani|parag parikh|mirae|"
    r"quant|motilal|uti|aditya birla|dsp|franklin)\b[^.]{0,40}\b(?:fund|share|shares|stock|scheme|etf|bluechip|flexi ?cap)\b"
    r"|\b(?:nse|bse)\s*:\s*[a-z]+", re.I)
GUARANTEE = re.compile(r"\b(?:i |we )?guarantee[sd]?\b|\bwill definitely\b|\bdefinitely will\b|\b100% (?:safe|sure)\b|\brisk-free\b", re.I)
PERSONAL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|[\w.+-]+@(?:ok\w+|ybl|ibl|axl|paytm|upi|apl|hdfcbank|icici|sbi)\b|"
    r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)", re.I)
RUPEES = re.compile(r"₹\s?(\d[\d,]*)")
# Where each confirm card keeps its amount; that amount must be one the owner actually said.
CARD_AMOUNTS = {"confirm_goal": ("goal", "target_minor"), "confirm_flow": ("flow", "amount_minor"),
    "confirm_value": ("amount_minor",), "confirm_goal_saving": ("add_minor",), "scenario": ("amount_minor",),
    "go_reroute": ("amount_minor",)}
SAFE_REPLY = ("I couldn't double-check that answer, so I've left it out rather than risk getting it wrong. "
    "Could you ask again, or say it a little differently?")


def rupees_in(text):
    """Whole-rupee amounts written in a text, e.g. {58672} for "₹58,672".

    Args:
        text: reply text.

    Returns:
        Set of ints.
    """
    return {int(m.replace(",", "")) for m in RUPEES.findall(text or "")}


def card_amount(action):
    """The amount on a confirm card, in minor units, or None when the card has none.

    Args:
        action: the reply's action dict.

    Returns:
        int or None.
    """
    path = CARD_AMOUNTS.get((action or {}).get("type"))
    value = action
    for key in path or ():
        value = value.get(key) if isinstance(value, dict) else None
    return value if path else None


def verify(question, reply, evidence=None):
    """Check one reply before it is shown.

    Args:
        question: what the owner typed or said.
        reply: the agent's reply dict (route, answer, action).
        evidence: whole-rupee amounts the agent was given (from its facts), or None when the route has no facts;
            when given, every ₹ in the answer must be one of them or one the owner said.

    Returns:
        List of failed rule names; empty when the reply may be shown. Rules: unknown_route, empty_or_too_long,
        named_security, guarantee, verdict, personal_data, card_amount_not_said, card_direction_contradicts,
        ungrounded_amount.

    Used by: POST /api/v1/assistant/message (Fina chat and the voice cards). Blocked replies are counted per agent
    and rule, and logged as "fina_blocked" activity in the admin portal.
    """
    answer = reply.get("answer") or ""
    problems = []
    if reply.get("route") not in ROUTES:
        problems.append("unknown_route")
    if not answer.strip() or len(answer) > 2500:
        problems.append("empty_or_too_long")
    if NAMED_SECURITY.search(answer):
        problems.append("named_security")
    if GUARANTEE.search(answer):
        problems.append("guarantee")
    if VERDICT.search(HEDGED.sub("", answer)):
        problems.append("verdict")
    if PERSONAL.search(answer):
        problems.append("personal_data")
    said = amounts_in(question)
    amount = card_amount(reply.get("action"))
    if amount is not None and abs(amount) not in said:
        problems.append("card_amount_not_said")
    if (reply.get("action") or {}).get("type") == "confirm_flow" and amount:
        direction = flow_direction(question)
        if direction and (amount > 0) != (direction == "in"):
            problems.append("card_direction_contradicts")
    if evidence is not None:
        allowed = set(evidence) | {a // 100 for a in said}
        if rupees_in(answer) - allowed:
            problems.append("ungrounded_amount")
    return problems
