"""Goals: deterministic progress and pace, plus Fina's coaching note.

Progress, pace and status are calculated here from the owner's own goal inputs and the
latest fresh forecast. The goals agent only writes a short note in which every amount,
date and count is a {placeholder} filled by the server, so it cannot invent numbers.
"""
import re
from datetime import date
from .assistant import HEDGED, SYMBOLS

DAYS_PER_MONTH = 30.44
PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
PROMISE = re.compile(r"\bguarantee|\bdefinitely\b|\bwill (?:surely|certainly)\b|\bcoins?\b|\breward", re.I)
MEANINGS = {
    "goal_target": "The goal's target amount",
    "goal_saved": "Amount the owner has set aside for this goal so far",
    "goal_remaining": "Amount still needed to reach the target",
    "goal_target_date": "Date the owner wants to reach the goal",
    "months_left": "Whole months left until the target date",
    "percent_saved": "Percent of the target already set aside",
    "needed_per_month": "Amount to set aside each month from now to reach the target on time",
    "pace_per_month": "How much the latest forecast says the balance grows each month (negative means it shrinks)",
}
MONEY = {"goal_target", "goal_saved", "goal_remaining", "needed_per_month", "pace_per_month"}


def money(minor, currency):
    """Format minor units as whole currency, e.g. 2500000 -> "₹25,000".

    Args:
        minor: amount in minor units (may be negative).
        currency: ISO currency code.

    Returns:
        Formatted string with the currency symbol.

    Used by: show() and Fina's goal notes.
    """
    sign = "-" if minor < 0 else ""
    return f"{sign}{SYMBOLS.get(currency, currency + ' ')}{abs(minor) / 100:,.0f}"


def pace_from(run):
    """Monthly balance change implied by a forecast run.

    Args:
        run: a forecast run result (forecast.timeline), or None.

    Returns:
        Minor units per month (negative when the balance shrinks), or None without a run.

    Used by: progress(), the waterfall and the investment readiness surplus. Seen as "/month pace" on goal cards.
    """
    if not run:
        return None
    f = run["forecast"]
    last, days = f["timeline"][-1], len(f["timeline"])
    change = last["cumulative_flow_minor"]
    return round(change / days * DAYS_PER_MONTH)


def progress(goal, as_of, run):
    """A goal's calculated progress against the latest forecast.

    Args:
        goal: the goal (name, target_minor, saved_minor, target_date, status).
        as_of: balance date, ISO string.
        run: latest fresh forecast run, or None.

    Returns:
        Dict with percent_saved, remaining_minor, days_left, months_left, needed_per_month_minor,
        pace_per_month_minor and status ('on_track'|'stretch'|'off_track'|'needs_forecast'|'reached'|'past_due').

    Used by: Every goal endpoint, Fina's goal answers and achievements. Seen as the progress bar, pill and facts on
    goal cards.
    """
    as_of = date.fromisoformat(as_of)
    target_date = date.fromisoformat(goal["target_date"])
    remaining = max(goal["target_minor"] - goal["saved_minor"], 0)
    days_left = (target_date - as_of).days
    months_left = max(days_left / DAYS_PER_MONTH, 0)
    needed = round(remaining / months_left) if remaining and months_left >= 0.5 else (remaining or 0)
    pace = pace_from(run)
    if goal["status"] == "achieved" or remaining == 0:
        status = "reached"
    elif days_left <= 0:
        status = "past_due"
    elif pace is None:
        status = "needs_forecast"
    elif pace >= needed:
        status = "on_track"
    elif pace > 0:
        status = "stretch"
    else:
        status = "off_track"
    return {"percent_saved": min(100, round(goal["saved_minor"] * 100 / goal["target_minor"])),
        "remaining_minor": remaining, "days_left": days_left, "months_left": int(months_left),
        "needed_per_month_minor": needed, "pace_per_month_minor": pace, "status": status}


def coach_facts(goal, prog):
    """The facts Fina's goal coach may use, each with a plain meaning, for the model prompt.

    Args:
        goal: the goal.
        prog: its progress from progress().

    Returns:
        Dict with goal_name, status and facts {key: {value, meaning}}.

    Used by: POST /api/v1/goals/{id}/coach (the goals coach model call).
    """
    values = {"goal_target": goal["target_minor"], "goal_saved": goal["saved_minor"], "goal_remaining": prog["remaining_minor"],
        "goal_target_date": goal["target_date"], "months_left": prog["months_left"], "percent_saved": prog["percent_saved"],
        "needed_per_month": prog["needed_per_month_minor"], "pace_per_month": prog["pace_per_month_minor"]}
    return {"goal_name": goal["name"], "status": prog["status"],
        "facts": {k: {"value": v, "meaning": MEANINGS[k]} for k, v in values.items() if v is not None}}


def show(key, value, currency):
    """Display one goal fact: money as currency, the target date as "31 Mar 2027", percent with %.

    Args:
        key: fact name from coach_facts().
        value: its value.
        currency: ISO currency code.

    Returns:
        Display string.

    Used by: render_note() and calculated_note().
    """
    if key in MONEY:
        return money(value, currency)
    if key == "goal_target_date":
        d = date.fromisoformat(value)
        return f"{d.day} {d.strftime('%b %Y')}"
    return f"{value}%" if key == "percent_saved" else str(value)


def render_note(note, facts, currency):
    """Fill the coach's {placeholders} with real values, rejecting anything unsafe.

    Args:
        note: the model's note with {placeholders}.
        facts: the dict from coach_facts().
        currency: ISO currency code.

    Returns:
        The final text, or None when the note has unknown placeholders, stray digits, promises or rewards.

    Used by: POST /api/v1/goals/{id}/coach. Seen as Fina's note under a goal after "Ask Fina".
    """
    known = facts["facts"]
    if any(k not in known for k in PLACEHOLDER.findall(note)) or re.search(r"\d", PLACEHOLDER.sub("", note)):
        return None
    text = PLACEHOLDER.sub(lambda m: show(m.group(1), known[m.group(1)]["value"], currency), note)
    return None if PROMISE.search(HEDGED.sub("", text)) else text


def calculated_note(goal, prog, currency):
    """Fina's goal note written by code, used when the model is off or its note is rejected.

    Args:
        goal: the goal.
        prog: its progress.
        currency: ISO currency code.

    Returns:
        A short plain-language note for the goal's status.

    Used by: The goal coach fallback and Fina's goal-status chat answers. Seen on goal cards and in chat.
    """
    v = {k: show(k, x["value"], currency) for k, x in coach_facts(goal, prog)["facts"].items()}
    name, status = goal["name"], prog["status"]
    if status == "reached":
        return f"You did it: {name} is fully funded at {v['goal_target']}. That took real consistency, well done."
    if status == "past_due":
        return (f"The date for {name} has passed with {v['goal_remaining']} still to go. No judgment; plans change. "
            "Pick a new date and I'll recalculate what each month needs.")
    lead = f"You're {v['percent_saved']} of the way to {name}, with {v['goal_remaining']} to go by {v['goal_target_date']}."
    need = f"That's about {v['needed_per_month']} a month."
    if status == "needs_forecast":
        return f"{lead} {need} Create a forecast on Overview and I'll tell you how that compares with your current pace."
    pace = f"Your latest forecast has your balance changing by about {v['pace_per_month']} a month."
    if status == "on_track":
        return f"{lead} {need} {pace} That covers it, so you're on track. Keep setting it aside as you go."
    if status == "stretch":
        return (f"{lead} {need} {pace} That's less than the goal needs, so this one is a stretch. A later date or a "
            "smaller first milestone would make it comfortable.")
    return (f"{lead} {need} {pace} Right now your balance isn't growing, so the goal can't be funded from the "
        "forecast yet. Adding upcoming income in Your context will give a truer picture.")
