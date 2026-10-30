"""Grounded explanations of a stored forecast and optional purchase scenario.

The server computes every fact. The model writes numbers and dates only as {fact_key}
placeholders, which the server fills from the same facts, so it cannot invent or
misformat a value. Answers with stray digits, unknown placeholders, an affordability
verdict or a guarantee are replaced by the deterministic explanation.
"""
import re
from datetime import date
from .finance import simulate

SYMBOLS = {"INR": "₹", "AED": "AED ", "USD": "$", "EUR": "€", "GBP": "£"}
VERDICT = re.compile(r"\byou\s+(?:can|cannot|can't|can not|could|couldn't|could not|should|shouldn't)\s+(?:not\s+)?afford\b"
    r"|\bguarantee|\bdefinitely\b|\bsafe to (?:buy|spend)\b", re.I)
HEDGED = re.compile(r"\b(?:whether|if)\s+you\s+(?:can|could)\s+afford\b", re.I)
PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")

# Plain-language meaning of every fact, so the model can pick the right one by name.
MEANINGS = {
    "today": "Date of the starting balance (as of)",
    "horizon_end": "Last day of the forecast",
    "horizon_days": "Number of days forecast",
    "history_days": "Days of past transactions used for the spending estimate",
    "starting_balance": "Available cash on the as-of date",
    "reserve": "Cash the owner wants to keep in reserve",
    "daily_spending_estimate": "Estimated everyday spending per day, from past non-recurring expenses",
    "upcoming_income_total": "Total upcoming income the owner entered within the forecast",
    "upcoming_outflow_total": "Total upcoming bills the owner entered within the forecast",
    "balance_at_horizon_end_without_purchase": "Expected balance on the last forecast day (the middle of the likely range)",
    "balance_at_horizon_end_likely_low": "Low end of the likely balance range on the last forecast day (1 in 10 futures end lower)",
    "balance_at_horizon_end_likely_high": "High end of the likely balance range on the last forecast day (1 in 10 futures end higher)",
    "chance_below_reserve_percent": "Chance, in percent, that the balance dips below the reserve at some point in the forecast",
    "chance_below_zero_percent": "Chance, in percent, that the balance dips below zero at some point in the forecast",
    "chance_below_reserve_with_purchase_percent": "Chance, in percent, of dipping below the reserve if the purchase is made",
    "lowest_balance_without_purchase": "Lowest balance during the forecast if nothing is bought",
    "first_reserve_breach_without_purchase": "First date the balance goes below the reserve if nothing is bought",
    "first_below_zero_date_without_purchase": "First date the balance goes below zero if nothing is bought",
    "purchase_amount": "Amount of the purchase being simulated",
    "purchase_date": "Date the purchase is paid",
    "balance_after_purchase_on_purchase_date": "Balance at the end of the purchase date, after paying for it",
    "balance_at_horizon_end_with_purchase": "Balance on the last forecast day if the purchase is made",
    "lowest_balance_with_purchase": "Lowest balance during the forecast if the purchase is made",
    "first_reserve_breach_with_purchase": "First date the balance goes below the reserve if the purchase is made",
    "first_below_zero_date_with_purchase": "First date the balance goes below zero if the purchase is made",
    "top_category_1_amount": "Spending in the largest category during the history period",
    "top_category_2_amount": "Spending in the second-largest category during the history period",
    "top_category_3_amount": "Spending in the third-largest category during the history period",
}
COUNTS = {"horizon_days", "history_days", "chance_below_reserve_percent", "chance_below_zero_percent",
    "chance_below_reserve_with_purchase_percent"}
DATES = {"today", "horizon_end", "purchase_date", "first_reserve_breach_without_purchase",
    "first_below_zero_date_without_purchase", "first_reserve_breach_with_purchase", "first_below_zero_date_with_purchase"}


def major(minor):
    """Minor units to major units with two decimals.

    Args:
        minor: amount or None.

    Returns:
        float or None.
    """
    return None if minor is None else round(minor / 100, 2)


def money(value, currency):
    """Format a major-unit amount with its symbol, or "unknown".

    Args:
        value: amount in major units.
        currency: ISO code.

    Returns:
        Text such as "₹25,000".
    """
    return "unknown" if value is None else f"{SYMBOLS.get(currency, currency + ' ')}{value:,.0f}"


def show(key, value, currency):
    """Display one fact: counts as numbers, dates as "7 Nov 2026", money with the symbol.

    Args:
        key: fact name.
        value: its value.
        currency: ISO code.

    Returns:
        Display text.

    Used by: render() and calculated_answer().
    """
    if key in COUNTS:
        return str(value)
    if key in DATES:
        d = date.fromisoformat(value)
        return f"{d.day} {d.strftime('%b %Y')}"
    return money(value, currency)


def percent(probability):
    """A probability (0-1) as a whole percent, or None.

    Args:
        probability: float or None.

    Returns:
        int percent or None.
    """
    return None if probability is None else round(probability * 100)


def build_facts(run, profile, scenario_input=None):
    """The facts Fina may use about a forecast, keyed by self-describing names; None means unknown.

    Args:
        run: forecast run (forecast, checks, analysis).
        profile: owner context.
        scenario_input: a purchase being discussed (amount_minor, date), or None.

    Returns:
        Dict with basis, currency, scenario_present and facts {key: {value, meaning}}.

    Used by: Fina's explanations, the advisor and the risk reviewer. Also the evaluation goldens' context.
    """
    f, checks, analysis = run["forecast"], run["checks"], run["analysis"]
    end = f["timeline"][-1]["date"]
    flows = [x for x in profile["scheduled_flows"] if f["as_of"] < x["date"] <= end]
    spending = sorted(analysis["by_category"].items(), key=lambda item: -item[1])[:3]
    values = {"today": f["as_of"], "horizon_end": end, "horizon_days": len(f["timeline"]),
        "history_days": f["history_days"], "starting_balance": major(f["opening_balance_minor"]),
        "reserve": major(f["reserve_minor"]), "daily_spending_estimate": major(f["daily_variable_minor"]),
        "upcoming_income_total": major(sum(x["amount_minor"] for x in flows if x["amount_minor"] > 0)),
        "upcoming_outflow_total": major(-sum(x["amount_minor"] for x in flows if x["amount_minor"] < 0)),
        "balance_at_horizon_end_without_purchase": major(f["timeline"][-1]["cash_minor"]),
        "lowest_balance_without_purchase": major(checks["minimum_cash_minor"]),
        "first_reserve_breach_without_purchase": checks.get("first_reserve_breach"),
        "first_below_zero_date_without_purchase": checks["first_negative_date"],
        "balance_at_horizon_end_likely_low": major(f["timeline"][-1].get("cash_low_minor")),
        "balance_at_horizon_end_likely_high": major(f["timeline"][-1].get("cash_high_minor")),
        "chance_below_reserve_percent": percent(f.get("below_reserve_probability")),
        "chance_below_zero_percent": percent(f.get("below_zero_probability"))}
    for i, (_, amount) in enumerate(spending, 1):
        values[f"top_category_{i}_amount"] = major(amount)
    if scenario_input:
        result = simulate(run, scenario_input["amount_minor"], scenario_input["date"])
        on_day = next(r for r in result["timeline"] if r["date"] == scenario_input["date"])
        values.update({"purchase_amount": major(scenario_input["amount_minor"]), "purchase_date": scenario_input["date"],
            "balance_after_purchase_on_purchase_date": major(on_day["cash_minor"]),
            "balance_at_horizon_end_with_purchase": major(result["timeline"][-1]["cash_minor"]),
            "lowest_balance_with_purchase": major(result["minimum_cash_minor"]),
            "first_reserve_breach_with_purchase": result["first_reserve_breach"],
            "first_below_zero_date_with_purchase": result["first_negative_date"],
            "chance_below_reserve_with_purchase_percent": percent(result.get("below_reserve_probability"))})
    usable = {k: v for k, v in values.items() if v is not None}
    return {"basis": f["basis"], "currency": f["currency"], "coverage_confirmed": f["coverage_confirmed"],
        "scenario_present": scenario_input is not None,
        "top_categories": [c for c, _ in spending],
        "facts": {k: {"value": v, "meaning": MEANINGS[k]} for k, v in usable.items()},
        "not_available": sorted(k for k, v in values.items() if v is None),
        "limitations": f["limitations"]}


def render(summary, facts):
    """Fill a model answer's {placeholders} from the facts; None when the text is not fully grounded.

    Args:
        summary: model text with placeholders.
        facts: from build_facts().

    Returns:
        Final text or None.

    Used by: check_answer().
    """
    known = facts["facts"]
    if any(name not in known for name in PLACEHOLDER.findall(summary)):
        return None
    if re.search(r"\d", PLACEHOLDER.sub("", summary)):
        return None
    return PLACEHOLDER.sub(lambda m: show(m.group(1), known[m.group(1)]["value"], facts["currency"]), summary)


def check_answer(summary, facts):
    """Decide whether a model answer may be shown: grounded and free of verdicts.

    Args:
        summary: model text with placeholders.
        facts: from build_facts().

    Returns:
        (text, None) when it may be shown, else (None, reason).

    Used by: Fina's chat answers before risk review.
    """
    text = render(summary, facts)
    if text is None:
        return None, "number"
    if VERDICT.search(HEDGED.sub("", text)):
        return None, "verdict"
    return text, None


def intent(question):
    """The kind of question asked, to shape the calculated answer.

    Args:
        question: the owner's question.

    Returns:
        'purchase', 'trend', 'levers' or 'overview'.

    Used by: calculated_answer().
    """
    q = question.lower()
    if re.search(r"afford|buy|purchase|spend on|get (?:a|the|this)", q):
        return "purchase"
    if re.search(r"down|drop|decreas|falling|shrink|fast|why .*less|losing", q):
        return "trend"
    if re.search(r"change|improve|help|better|fix|what can i|how can i|should i do", q):
        return "levers"
    return "overview"


def calculated_answer(facts, question=""):
    """Fina's explanation written by code from the same facts, with the likely range when the forecast has one.

    Args:
        facts: from build_facts().
        question: the owner's question.

    Returns:
        Answer text.

    Used by: Fina's chat when the model is off or its answer is rejected. Seen in the chat as "worked out from your forecast".
    """
    text = base_answer(facts, question)
    known = facts["facts"]
    if "balance_at_horizon_end_likely_low" in known and "balance_at_horizon_end_likely_high" in known:
        low = show("balance_at_horizon_end_likely_low", known["balance_at_horizon_end_likely_low"]["value"], facts["currency"])
        high = show("balance_at_horizon_end_likely_high", known["balance_at_horizon_end_likely_high"]["value"], facts["currency"])
        text += f" That's the middle estimate: 8 in 10 likely futures end between {low} and {high}"
        chance = known.get("chance_below_reserve_with_purchase_percent") or known.get("chance_below_reserve_percent")
        text += f", with about a {chance['value']}% chance of dipping below your reserve." if chance else "."
    return text


def base_answer(facts, question=""):
    """Fina's explanation written by code from the same facts, shaped by the question.

    Args:
        facts: from build_facts().
        question: the owner's question.

    Returns:
        Answer text.

    Used by: Fina's chat when the model is off or its answer is rejected. Seen in the chat as "worked out from your
    forecast".
    """
    v = {k: show(k, x["value"], facts["currency"]) for k, x in facts["facts"].items()}
    kind, scenario = intent(question), facts["scenario_present"]
    no_income = not facts["facts"].get("upcoming_income_total", {}).get("value")
    if facts["basis"] == "net_cash_flow":
        return ("I'd love to help with this, but I don't know your available cash yet, so I can only see money "
            "coming in and going out, not your balance. Add it in Your context and ask me again; "
            "I'll be able to tell you much more.")
    tight = any(k in v for k in ("first_below_zero_date_with_purchase", "first_reserve_breach_with_purchase")) if scenario         else any(k in v for k in ("first_below_zero_date_without_purchase", "first_reserve_breach_without_purchase"))
    parts = []
    if kind == "purchase" and not scenario:
        parts.append("Happy to look at that with you. Pop the amount and date into the What if panel and ask me again, "
            "and I'll walk you through exactly what it does to your balance.")
        if "balance_at_horizon_end_without_purchase" in v:
            parts.append(f"For now, without any new purchase, you're heading from {v['starting_balance']} to "
                f"{v['balance_at_horizon_end_without_purchase']} by {v['horizon_end']}.")
        return " ".join(parts)
    if scenario:
        opener = ("I can see why this one feels tight." if tight else "Let's look at this together.")
        parts.append(f"{opener} If you pay {v['purchase_amount']} on {v['purchase_date']}, you'd have "
            f"{v['balance_after_purchase_on_purchase_date']} left that day and {v['balance_at_horizon_end_with_purchase']} "
            f"by {v['horizon_end']}, instead of {v['balance_at_horizon_end_without_purchase']} without it.")
        if "first_below_zero_date_with_purchase" in v:
            parts.append(f"What worries me is that your balance would go below zero on {v['first_below_zero_date_with_purchase']}.")
        elif "first_reserve_breach_with_purchase" in v:
            parts.append(f"The part I'd pay attention to: it dips under the {v['reserve']} you wanted to keep aside, "
                f"starting {v['first_reserve_breach_with_purchase']}.")
        elif "lowest_balance_with_purchase" in v and "reserve" in v:
            parts.append(f"Even at its lowest you'd still have {v['lowest_balance_with_purchase']}, "
                f"which stays above the {v['reserve']} you wanted to keep aside.")
    else:
        opener = "I hear you, watching your balance shrink isn't a nice feeling." if kind == "trend" and tight             else "Here's how your next few weeks look."
        parts.append(f"{opener} You're starting at {v['starting_balance']} and heading to "
            f"{v['balance_at_horizon_end_without_purchase']} by {v['horizon_end']}.")
        if "first_below_zero_date_without_purchase" in v:
            parts.append(f"Your balance goes below zero on {v['first_below_zero_date_without_purchase']}.")
        elif "first_reserve_breach_without_purchase" in v:
            parts.append(f"It slips under your {v['reserve']} reserve on {v['first_reserve_breach_without_purchase']}.")
    parts.append(f"Most of that comes from everyday spending of about {v['daily_spending_estimate']} a day, "
        f"based on your last {v['history_days']} days"
        + (", and since there's no upcoming income in your plan yet, nothing is pulling the balance back up." if no_income else "."))
    if kind == "levers" or no_income:
        parts.append("The quickest way to sharpen this picture is to add your next paydays and bills in Your context"
            + ("; then try a different amount or date in What if." if scenario else "."))
    parts.append("I won't give you a yes or no, that's your call, but these are the numbers I'd want you to see.")
    return " ".join(parts)
