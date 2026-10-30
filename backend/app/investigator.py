"""Investigator: find missing forecast facts and propose confirmable answers.

The model only proposes. Every upcoming-flow proposal must copy an amount from the
owner's own repeated transactions and fall inside the forecast window; the owner then
confirms or edits each answer, and only confirmed answers are saved through the profile.
Without AI, deterministic questions and monthly-pattern suggestions are used instead.
"""
import re
from collections import defaultdict
from datetime import date, timedelta
from statistics import median
from .mesh import minimized_description

HORIZON_DAYS = 90
MONTHS = r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b"
SKIP_KINDS = {"transfer", "refund"}
GAP_TEXT = {
    "available_cash": "How much cash do you have available right now? Without it I can only show money coming in and "
        "going out, not your actual balance.",
    "next_income": "When is your next payday, and how much do you expect? Right now your forecast has no income in it, "
        "so it can only go down.",
    "reserve": "How much would you like to always keep aside as a safety cushion? I'll warn you when a plan dips below it.",
    "coverage": "Do the transactions you've added cover everything you spent in this period? If so, I can compare "
        "your recent months fairly.",
}


def normalize(description):
    """A description reduced to lower-case words, without month names, digits or symbols.

    Args:
        description: transaction description.

    Returns:
        Text such as "hdfc car loan emi".

    Used by: Grouping repeating payments here and in the debt agent.
    """
    text = re.sub(MONTHS, " ", description.lower())
    text = re.sub(r"[^a-z ]+", " ", text)
    return " ".join(text.split())


def build_inputs(profile, transactions):
    """What the investigator sees: repeating payments from history and the gaps in the owner's context.

    Args:
        profile: owner context.
        transactions: ledger rows.

    Returns:
        Dict with as_of, horizon_end, profile_gaps, history_groups (minimized) and already_scheduled.

    Used by: POST /api/v1/investigator/questions ("Ask Fina what's missing").
    """
    as_of = date.fromisoformat(profile["as_of"])
    horizon_end = as_of + timedelta(days=HORIZON_DAYS)
    start = as_of - timedelta(days=120)
    groups = defaultdict(list)
    for tx in transactions:
        when = date.fromisoformat(tx["date"])
        if start < when <= as_of and tx["kind"] not in SKIP_KINDS and normalize(tx["description"]):
            groups[(normalize(tx["description"]), tx["kind"])].append(tx)
    history = []
    for (key, kind), rows in groups.items():
        rows.sort(key=lambda t: t["date"])
        if len(rows) < 2 and not any(t.get("recurring") for t in rows):
            continue
        history.append({"description": minimized_description(rows[-1]["description"]), "kind": kind,
            "category": rows[-1].get("category"), "recurring": any(t.get("recurring") for t in rows),
            "occurrences": [{"date": t["date"], "amount_minor": t["amount_minor"]} for t in rows[-6:]],
            "_total": sum(abs(t["amount_minor"]) for t in rows)})
    history.sort(key=lambda g: -g["_total"])
    history = history[:40]
    for i, group in enumerate(history, 1):
        group["group_ref"] = f"g{i}"
        del group["_total"]
    upcoming = [f for f in profile["scheduled_flows"] if f["date"] > profile["as_of"]]
    gaps = []
    if profile["opening_balance_minor"] is None:
        gaps.append("available_cash")
    if not any(f["amount_minor"] > 0 for f in upcoming):
        gaps.append("next_income")
    if profile["reserve_minor"] is None:
        gaps.append("reserve")
    if not profile["coverage_confirmed"]:
        gaps.append("coverage")
    return {"as_of": profile["as_of"], "horizon_end": horizon_end.isoformat(),
        "profile_gaps": [{"gap_ref": g, "missing": g} for g in gaps], "history_groups": history,
        "already_scheduled": [{"date": f["date"], "label": f["label"], "amount_minor": f["amount_minor"]} for f in upcoming]}


def covered(proposal, scheduled):
    """True when a proposed flow is already scheduled (within 5 days and 10% of the amount).

    Args:
        proposal: proposed flow.
        scheduled: the owner's scheduled flows.

    Returns:
        bool.
    """
    when = date.fromisoformat(proposal["date"])
    return any(abs((date.fromisoformat(f["date"]) - when).days) <= 5 and (f["amount_minor"] > 0) == (proposal["amount_minor"] > 0)
        and abs(f["amount_minor"] - proposal["amount_minor"]) <= abs(proposal["amount_minor"]) * 0.1 for f in scheduled)


def label_for(description):
    """A readable label from a description, e.g. "Hdfc Car Loan Emi".

    Args:
        description: description.

    Returns:
        Title-case label.
    """
    words = [w for w in normalize(description).split() if w != "redacted"][:4]
    return " ".join(words).title()[:80] or "Regular payment"


def pattern_flows(inputs):
    """Monthly income and recurring bills found in history, as code-made proposals.

    Args:
        inputs: from build_inputs().

    Returns:
        List of proposed upcoming flows.
    """
    as_of, end = date.fromisoformat(inputs["as_of"]), date.fromisoformat(inputs["horizon_end"])
    flows = []
    for group in inputs["history_groups"]:
        dates = [date.fromisoformat(o["date"]) for o in group["occurrences"]]
        gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
        monthly = gaps and 25 <= median(gaps) <= 35
        if not (monthly and (group["kind"] == "income" or group["recurring"])):
            continue
        step = round(median(gaps))
        nxt = dates[-1] + timedelta(days=step)
        while nxt <= as_of:
            nxt += timedelta(days=step)
        proposal = {"label": label_for(group["description"]), "amount_minor": group["occurrences"][-1]["amount_minor"],
            "date": nxt.isoformat()}
        if nxt <= end and not covered(proposal, inputs["already_scheduled"]):
            flows.append({"ref": group["group_ref"], "kind": "upcoming_flow", "proposal": proposal,
                "question": ("This looks like regular income. Should I add the next one to your forecast?"
                    if proposal["amount_minor"] > 0 else "This looks like a regular bill. Should I plan for the next one?")})
    return flows


def assemble(flows, inputs):
    """The final question list: fixed-wording gap questions plus flow proposals.

    Args:
        flows: proposed flows.
        inputs: from build_inputs().

    Returns:
        List of questions.

    Used by: Seen in the "A few questions from Fina" dialog.
    """
    has_income = any(f["proposal"]["amount_minor"] > 0 for f in flows)
    gaps = [{"ref": g["gap_ref"], "kind": g["missing"], "question": GAP_TEXT[g["missing"]], "proposal": None}
        for g in inputs["profile_gaps"] if not (g["missing"] == "next_income" and has_income)]
    first = [q for q in gaps if q["kind"] in {"available_cash", "next_income"}]
    flows = sorted(flows, key=lambda f: f["proposal"]["amount_minor"] < 0)  # income first
    return (first + flows + [q for q in gaps if q not in first])[:6]


def fallback_questions(inputs):
    """Questions without the model, from detected patterns only.

    Args:
        inputs: from build_inputs().

    Returns:
        List of questions.

    Used by: When AI is off or the model's proposals are rejected.
    """
    return assemble(pattern_flows(inputs), inputs)


def grounded_flows(payload_questions, inputs):
    """Model proposals that cite a real payment group, copy one of its amounts and fall inside the window.

    Args:
        payload_questions: the model's questions.
        inputs: from build_inputs().

    Returns:
        Accepted flow proposals.
    """
    groups = {g["group_ref"]: g for g in inputs["history_groups"]}
    as_of, end = inputs["as_of"], inputs["horizon_end"]
    kept = {}
    for q in payload_questions:
        ref, text, proposal = q["ref"], q["question"].strip(), q["proposal"]
        if ref not in groups or ref in kept or not proposal or re.search(r"\d", text) or not 0 < len(text) <= 300:
            continue
        amounts = {o["amount_minor"] for o in groups[ref]["occurrences"]}
        try:
            valid_date = as_of < date.fromisoformat(proposal["date"]).isoformat() <= end
        except ValueError:
            valid_date = False
        label = proposal["label"].strip()
        if proposal["amount_minor"] not in amounts or not valid_date or not 0 < len(label) <= 80 or re.search(r"[<>]", label):
            continue
        if not covered(proposal, inputs["already_scheduled"]):
            kept[ref] = {"ref": ref, "kind": "upcoming_flow", "question": text,
                "proposal": {"label": label, "amount_minor": proposal["amount_minor"], "date": proposal["date"]}}
    return list(kept.values())


def merge_questions(payload_questions, inputs):
    """Model flows first, pattern flows for anything it skipped; None when the model added nothing usable.

    Args:
        payload_questions: the model's questions.
        inputs: from build_inputs().

    Returns:
        Questions or None.
    """
    model_flows = grounded_flows(payload_questions, inputs)
    if not model_flows:
        return None
    seen = {f["ref"] for f in model_flows}
    return assemble(model_flows + [f for f in pattern_flows(inputs) if f["ref"] not in seen], inputs)
