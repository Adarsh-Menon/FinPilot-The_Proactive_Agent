"""LLM category proposals and deterministic financial calculations."""
import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from langgraph.graph import StateGraph, START, END
from typing import TypedDict
from . import hybrid
from .observability import record_categories, timed_node

AI_DAILY_LIMIT = 20                  # owner-requested AI categorizations (notes and retries) per day
NOT_DISPATCHED = {"paused", "not_configured"}
# Words that say how money moved rather than who it went to; never a merchant name.
BANK_WORDS = {"upi", "neft", "imps", "rtgs", "ach", "pos", "p2p", "payment", "pay", "ltd", "pvt", "limited", "inc", "india", "order",
    "ref", "txn", "rev", "yesb", "hdfc", "icic", "icici", "utib", "sbin", "kkbk", "ybl", "okaxis", "oksbi", "okhdfcbank", "apl",
    "the", "for", "and", "from", "with", "com", "www", "help", "debit", "credit", "card", "redacted", "trip", "food", "groc"}


def merchant_words(text):
    """The distinctive words of a description, without bank noise (UPI, POS, codes, handles, references).

    Args:
        text: a transaction description.

    Returns:
        List of lower-case words, e.g. ['zomato'] for a Zomato UPI line.

    Used by: merchant_key() and same_merchant().
    """
    text = re.sub(r"@[\w.]+", " ", text.lower())          # keep "swiggy" from swiggy@ybl, drop the bank handle
    return [w for w in re.findall(r"[a-z]+", text) if len(w) >= 3 and w not in BANK_WORDS and set(w) != {"x"}]


def merchant_key(description, note=None):
    """The words naming who was paid, used to apply an owner's label to the same merchant.

    A note that names the merchant ("zomato is food delivery") makes the rule broad; otherwise every distinctive
    word must match, so "Zomato gift card" covers only gift cards.

    Args:
        description: the labelled transaction's description.
        note: the owner's note to the AI, if any.

    Returns:
        Tuple of key words, or None when the description has none.

    Used by: Label spreading after a category fix and owner rules on import. Seen as "Matches your label for ...".
    """
    words = merchant_words(description)
    if note:
        named = [w for w in words if w in set(re.findall(r"[a-z]+", note.lower()))]
        if named:
            return (named[0],)
    return tuple(dict.fromkeys(words)) or None


def same_merchant(key, description):
    """True when a description contains every word of a merchant key.

    Args:
        key: from merchant_key().
        description: another transaction's description.

    Returns:
        bool.
    """
    words = set(merchant_words(description))
    return bool(key) and all(w in words for w in key)


def owner_rule(tx, examples):
    """The category of the owner's most specific earlier label for this merchant, or None when labels disagree.

    Args:
        tx: the transaction being categorized.
        examples: the owner's labelled examples (description, category, kind, note).

    Returns:
        (category, key text) or None.

    Used by: categorize_many(): owner labels are applied before any model call.
    """
    matches = {}
    for example in examples:
        key = merchant_key(example["description"], example.get("owner_note"))
        if same_merchant(key, tx["description"]) and example.get("kind", tx["kind"]) == tx["kind"]:
            matches.setdefault(len(key), set()).add((example["category"], " ".join(key)))
    if not matches:
        return None
    best = matches[max(matches)]
    return next(iter(best)) if len({category for category, _ in best}) == 1 else None


def ai_attempts(tx):
    """Model generations already spent on a transaction; rows from before the counter are inferred.

    Args:
        tx: transaction row.

    Returns:
        Number of AI attempts.

    Used by: Category history and the admin activity view.
    """
    if "category_ai_attempts" in tx:
        return tx["category_ai_attempts"]
    return int(tx.get("category_source") == "model" or tx.get("category_status") in {"needs_clarification", "unavailable"})


def categorize_many(transactions, gateway=None, *, existing_categories=(), owner_examples=()):
    """Categorize transactions: the owner's own labels first, then owner rules, then the AI for the rest.

    Args:
        transactions: rows to categorize (a given category is kept as the owner's).
        gateway: the model gateway, or None for no AI.
        existing_categories: the owner's category names as hints.
        owner_examples: the owner's labelled examples for few-shot learning and merchant rules.

    Returns:
        New row dicts with category, category_source ('owner'|'merchant_rule'|'model'|'unresolved'), status and
        reason.

    Used by: Imports, manual entry, AI retry and category notes. Seen in the Category column on Transactions.
    """
    rows = [dict(tx) for tx in transactions]
    pending = []
    for index, row in enumerate(rows):
        if row.get("category"):
            row["category_source"] = "owner"
        elif rule := owner_rule(row, owner_examples):
            # The owner already labelled this merchant: follow it without a model call.
            row.update(category=rule[0], category_source="merchant_rule", category_status="classified",
                category_reason=f"Matches your label for “{rule[1]}”.")
        else:
            row.update(category="uncategorized", category_source="unresolved")
            pending.append(index)
    if gateway is not None and pending:
        hints = list(existing_categories) + [row["category"] for row in rows if row["category_source"] == "owner"]
        proposals = gateway.categorize([rows[i] for i in pending], existing_categories=hints,
            owner_examples=owner_examples)
        for index, proposal in zip(pending, proposals, strict=True):
            rows[index].update(proposal)
            dispatched = proposal.get("category_status") not in NOT_DISPATCHED
            rows[index]["category_ai_attempts"] = rows[index].get("category_ai_attempts", 0) + dispatched
    record_categories(rows)
    return rows


def categorize(tx, gateway=None, *, existing_categories=(), owner_examples=()):
    """Categorize one transaction (see categorize_many).

    Args:
        tx: the transaction.
        gateway: the model gateway.
        existing_categories: category hints.
        owner_examples: labelled examples.

    Returns:
        The categorized row.

    Used by: POST /api/v1/transactions.
    """
    return categorize_many([tx], gateway, existing_categories=existing_categories, owner_examples=owner_examples)[0]


def rounded(value):
    """Round half up to a whole number of minor units.

    Args:
        value: a Decimal or number.

    Returns:
        int.
    """
    return int(Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def amount_text(minor, currency):
    """Format minor units with the currency code, e.g. "INR 1,234.50".

    Args:
        minor: amount in minor units.
        currency: ISO code.

    Returns:
        Formatted text.

    Used by: Pipeline step details.
    """
    return f"{currency} {minor / 100:,.2f}"


def categories(count):
    """"1 category" or "N categories".

    Args:
        count: how many.

    Returns:
        Text.
    """
    return f"{count} {'category' if count == 1 else 'categories'}"


def first_below(f, timeline, floor):
    """First date the balance falls under a floor, starting with the opening balance.

    Args:
        f: forecast dict (opening_balance_minor, as_of).
        timeline: daily rows with cash_minor.
        floor: threshold in minor units (0 or the reserve).

    Returns:
        ISO date, or None when it never does or the balance is unknown.

    Used by: check() and simulate(). Seen as reserve and negative-balance warnings on Overview.
    """
    if f["opening_balance_minor"] is None or floor is None:
        return None
    if f["opening_balance_minor"] < floor:
        return f["as_of"]
    return next((r["date"] for r in timeline if r["cash_minor"] < floor), None)


class State(TypedDict, total=False):
    """The forecast pipeline's state passed between LangGraph steps.

    Fields:
        profile: owner context.
        transactions: ledger rows.
        horizon: forecast days.
        analysis: behavioural analysis output.
        forecast: daily timeline and totals.
        checks: numeric checks.
        steps: per-step log shown on Agent activity.
    """
    profile: dict
    transactions: list
    horizon: int
    analysis: dict
    forecast: dict
    checks: dict
    steps: list


def analyze(state):
    """Pipeline step 1 (behavioural pattern agent): spending by category and month-over-month comparisons.

    Args:
        state: the LangGraph pipeline state (profile, transactions, horizon, and the results of earlier steps).

    Returns:
        State update with analysis and a step entry.

    Used by: The forecast graph. Seen as "A pattern worth a look" on Overview and on Agent activity.
    """
    profile, txs = state["profile"], state["transactions"]
    end = date.fromisoformat(profile["as_of"])
    start = date.fromisoformat(profile["coverage_start"])
    eligible = [t for t in txs if start <= date.fromisoformat(t["date"]) <= end]
    by_category = defaultdict(int)
    for tx in eligible:
        if tx["kind"] == "expense":
            by_category[tx["category"]] += -tx["amount_minor"]
    comparisons = []
    if profile["coverage_confirmed"] and (end - start).days >= 59:
        for category in sorted(by_category):
            recent = prior = 0
            refs = []
            for tx in eligible:
                if tx["kind"] != "expense" or tx["category"] != category:
                    continue
                age = (end - date.fromisoformat(tx["date"])).days
                if age < 60:
                    refs.append(tx["id"])
                if age < 30:
                    recent -= tx["amount_minor"]
                elif age < 60:
                    prior -= tx["amount_minor"]
            comparisons.append({"category": category, "current_minor": recent, "prior_minor": prior,
                "change_minor": recent - prior, "percent_change": rounded(Decimal(recent-prior) * 100 / prior) if prior else None,
                "period_days": 30, "evidence_refs": refs})
    uncategorized = sum(t["category"] == "uncategorized" for t in eligible)
    if comparisons:
        compared = f"Compared the latest 30 days with the 30 before for {categories(len(comparisons))}."
    elif not profile["coverage_confirmed"]:
        compared = "Skipped 30-day comparisons: history coverage is not confirmed."
    else:
        compared = "Skipped 30-day comparisons: fewer than 60 days of history."
    return {"analysis": {"by_category": dict(by_category), "comparisons": comparisons,
        "uncategorized_count": uncategorized,
        "transaction_count": len(eligible), "evidence_refs": [t["id"] for t in eligible]},
        "steps": [{"node": "analyzer", "method": "deterministic", "status": "completed", "details": [
            f"Read {len(eligible)} transactions dated {profile['coverage_start']} to {profile['as_of']}.",
            f"Grouped spending into {categories(len(by_category))}"
            + (f"; {uncategorized} transactions are uncategorized." if uncategorized else "."),
            compared]}]}


PREDICTOR = None       # set by create_app(): a hybrid.FoundationModel, or None for the trailing-mean baseline


def set_predictor(predictor):
    """Install the everyday-spending predictor used by the forecast step (None for the baseline).

    Args:
        predictor: a callable (daily series, horizon) -> per-day quantiles or None, such as hybrid.FoundationModel.

    Used by: create_app() at startup, from settings.forecast_model.
    """
    global PREDICTOR
    PREDICTOR = predictor


def daily_spending(transactions, start, end):
    """Everyday (non-recurring) spending per day over the history window, oldest first.

    Args:
        transactions: ledger rows.
        start: first history day (date).
        end: balance date (date).

    Returns:
        List of minor-unit totals, one per day from start to end inclusive.
    """
    series = [0] * ((end - start).days + 1)
    for t in transactions:
        when = date.fromisoformat(t["date"])
        if t["kind"] == "expense" and not t["recurring"] and start <= when <= end:
            series[(when - start).days] -= t["amount_minor"]
    return series


def forecast(state):
    """Pipeline step 2 (forecaster): a daily balance timeline from scheduled flows and forecast everyday spending.

    Hybrid when a foundation model is installed: the owner's scheduled income and bills stay exact, everyday spending is
    forecast by the frozen time-series model as quantiles, and 1,000 simulated futures give a likely range (10th-90th
    percentile) and the chance of dipping under the reserve or zero. Otherwise, or when the model fails, everyday spending
    is the trailing mean (the baseline).

    Args:
        state: the LangGraph pipeline state (profile, transactions, horizon, and the results of earlier steps).

    Returns:
        State update with forecast (timeline with cash and, for the hybrid, cash_low/cash_high per day; method; model;
        probabilities; limitations) and a step entry.

    Used by: The forecast graph. Seen as the chart and its shaded range on Overview, and in Fina's answers.
    """
    p = state["profile"]
    end, start = date.fromisoformat(p["as_of"]), date.fromisoformat(p["coverage_start"])
    days = (end-start).days + 1
    series = daily_spending(state["transactions"], start, end)
    daily = rounded(Decimal(sum(series)) / days)
    flows = defaultdict(int)
    for flow in p["scheduled_flows"]:
        flows[flow["date"]] += flow["amount_minor"]
    opening, horizon = p["opening_balance_minor"], state["horizon"]
    dates = [(end + timedelta(days=offset)).isoformat() for offset in range(1, horizon + 1)]
    scheduled = [flows[day] for day in dates]
    spend_q, model_note = None, None
    if PREDICTOR is not None and opening is not None and days >= 14:
        try:
            spend_q = PREDICTOR(series[-512:], horizon)
        except Exception as exc:       # never let the model break a forecast
            spend_q, model_note = None, type(exc).__name__
        if spend_q is None and model_note is None:
            model_note = "unavailable"
    timeline, cumulative = [], 0
    if spend_q:
        band = hybrid.simulate(opening, scheduled, spend_q, p["reserve_minor"], seed=sum(series) % 100_003)
        expected = [round(sum(q) / len(q)) for q in spend_q]
        daily = round(sum(expected) / len(expected))
        for i, day in enumerate(dates):
            net = scheduled[i] - expected[i]
            cumulative += net
            q = band["balance_q"][i]
            timeline.append({"date": day, "scheduled_minor": scheduled[i], "net_flow_minor": net, "cumulative_flow_minor": cumulative,
                "cash_minor": q[4], "cash_low_minor": q[0], "cash_high_minor": q[8]})
    else:
        for i, day in enumerate(dates):
            net = scheduled[i] - daily
            cumulative += net
            timeline.append({"date": day, "scheduled_minor": scheduled[i], "net_flow_minor": net, "cumulative_flow_minor": cumulative,
                "cash_minor": opening + cumulative if opening is not None else None})
    hybrid_on = bool(spend_q)
    limits = ([f"Everyday spending is forecast by a frozen time-series foundation model ({PREDICTOR.name}); the shaded range "
            "is the 10th-90th percentile of 1,000 simulated futures and is checked by backtests, not guaranteed."] if hybrid_on else
        ["Trailing-mean baseline for everyday spending" + (f" (the forecasting model was {model_note}, so the baseline was used)"
            if model_note else "") + "."]) + [
        "Only the dated future flows you entered are modeled; no salary or bill recurrence is inferred.",
        "Historical recurring expenses are excluded from variable spending; include their future dates in scheduled flows."]
    if not p["coverage_confirmed"]:
        limits.append("History coverage is unconfirmed; this is an illustrative projection, not an affordability assessment.")
    if days < 60:
        limits.append("Less than 60 days of history; the baseline is provisional.")
    if opening is None:
        limits.append("No opening balance: showing net cash flow only.")
    if p["reserve_minor"] is None:
        limits.append("No reserve policy: reserve headroom is unknown.")
    result = {"basis": "cash_balance" if opening is not None else "net_cash_flow",
        "currency": p["currency"], "as_of": p["as_of"], "history_days": days, "daily_variable_minor": daily,
        "opening_balance_minor": opening, "reserve_minor": p["reserve_minor"], "coverage_confirmed": p["coverage_confirmed"],
        "timeline": timeline, "limitations": limits,
        "method": "hybrid" if hybrid_on else "trailing_mean", "model": PREDICTOR.name if hybrid_on else None,
        "rule_version": "hybrid-tsfm-v1" if hybrid_on else "trailing-mean-v1",
        "uncertainty_method": "simulated-quantiles" if hybrid_on else "not_calibrated"}
    if hybrid_on:
        result.update(spend_q=spend_q, seed=sum(series) % 100_003, spend_total_q=band["spend_total_q"],
            below_reserve_probability=band["below_reserve_probability"], below_zero_probability=band["below_zero_probability"])
    method_line = (f"Everyday spending forecast by {PREDICTOR.name} (frozen foundation model), about "
        f"{amount_text(daily, p['currency'])} a day, with a likely range from 1,000 simulated futures." if hybrid_on else
        f"Average day-to-day spending is {amount_text(daily, p['currency'])}, from {days} days of non-recurring expenses.")
    return {"forecast": result,
        "steps": state["steps"] + [{"node": "forecaster", "method": "hybrid" if hybrid_on else "deterministic", "status": "completed",
            "details": [method_line,
            f"Added {len(p['scheduled_flows'])} dated upcoming flows you entered."
            + ("" if p["scheduled_flows"] else " With no income entered, the balance only goes down."),
            f"Projected {horizon} days ahead"
            + (f", ending at {amount_text(timeline[-1]['cash_minor'], p['currency'])}" if opening is not None
               else f"; net flow {amount_text(cumulative, p['currency'])} (no opening balance)")
            + (f" (likely {amount_text(timeline[-1]['cash_low_minor'], p['currency'])} to "
               f"{amount_text(timeline[-1]['cash_high_minor'], p['currency'])})." if hybrid_on else ".")]}]}

def check(state):
    """Pipeline step 3 (numeric checks): lowest balance, first dip below zero and below the reserve.

    Args:
        state: the LangGraph pipeline state (profile, transactions, horizon, and the results of earlier steps).

    Returns:
        State update with checks and a step entry.

    Used by: The forecast graph. Seen as the reserve warning and lowest-balance figures on Overview.
    """
    f = state["forecast"]
    cash = [r["cash_minor"] for r in f["timeline"] if r["cash_minor"] is not None]
    minimum = min(cash + [f["opening_balance_minor"]]) if cash else None
    negative, breach = first_below(f, f["timeline"], 0), first_below(f, f["timeline"], f["reserve_minor"])
    if minimum is None:
        details = ["No opening balance, so balance checks were skipped."]
    else:
        details = [f"Lowest projected balance is {amount_text(minimum, f['currency'])}.",
            f"Balance goes below zero on {negative}." if negative else "Balance stays above zero.",
            "No reserve set, so reserve checks were skipped." if f["reserve_minor"] is None
            else f"Balance goes below your reserve on {breach}." if breach else "Balance stays above your reserve."]
    if f.get("below_reserve_probability") is not None:
        details.append(f"Chance of dipping below your reserve: {round(f['below_reserve_probability'] * 100)}%.")
    return {"checks": {"minimum_cash_minor": minimum, "first_negative_date": negative, "first_reserve_breach": breach,
        "below_reserve_probability": f.get("below_reserve_probability"), "below_zero_probability": f.get("below_zero_probability"),
        "advice_status": "not_implemented", "affordability": "not_assessed"},
        "steps": state["steps"] + [{"node": "numeric_checks", "method": "deterministic", "status": "completed",
            "details": details}]}


builder = StateGraph(State)
for name, fn in [("analysis", analyze), ("projection", forecast), ("checks_node", check)]:
    builder.add_node(name, timed_node(name, fn))
builder.add_edge(START, "analysis")
builder.add_edge("analysis", "projection")
builder.add_edge("projection", "checks_node")
builder.add_edge("checks_node", END)
graph = builder.compile()


def simulate(baseline, amount, purchase_date):
    """A what-if purchase applied to a forecast, without changing it.

    Args:
        baseline: a saved forecast run.
        amount: purchase amount in minor units.
        purchase_date: ISO date.

    Returns:
        Dict with the adjusted timeline, ending delta, lowest balance, first reserve breach and first negative date.

    Used by: POST /api/v1/scenarios and Fina's purchase answers. Seen as "What if?" on Overview.
    """
    f = baseline["forecast"]
    if f.get("spend_q") and f["opening_balance_minor"] is not None:
        day = next((i for i, r in enumerate(f["timeline"]) if r["date"] >= purchase_date), len(f["timeline"]) - 1)
        band = hybrid.simulate(f["opening_balance_minor"], [r["scheduled_minor"] for r in f["timeline"]], f["spend_q"],
            f["reserve_minor"], seed=f.get("seed", 7), purchase=(day, amount))
        timeline = [{**row, "cash_minor": q[4], "cash_low_minor": q[0], "cash_high_minor": q[8],
            "cumulative_flow_minor": row["cumulative_flow_minor"] - (amount if row["date"] >= purchase_date else 0),
            "net_flow_minor": row["net_flow_minor"] - (amount if row["date"] == purchase_date else 0)}
            for row, q in zip(f["timeline"], band["balance_q"])]
        cash = [r["cash_minor"] for r in timeline]
        return {"timeline": timeline, "ending_delta_minor": -amount, "minimum_cash_minor": min(cash + [f["opening_balance_minor"]]),
            "first_reserve_breach": first_below(f, timeline, f["reserve_minor"]), "first_negative_date": first_below(f, timeline, 0),
            "below_reserve_probability": band["below_reserve_probability"], "below_zero_probability": band["below_zero_probability"],
            "currency": f["currency"], "affordability": "not_assessed", "limitations": f["limitations"]}
    timeline = []
    for row in f["timeline"]:
        adjustment = amount if row["date"] >= purchase_date else 0
        timeline.append({**row, "cash_minor": row["cash_minor"] - adjustment if row["cash_minor"] is not None else None,
            "cumulative_flow_minor": row["cumulative_flow_minor"] - adjustment,
            "net_flow_minor": row["net_flow_minor"] - (amount if row["date"] == purchase_date else 0)})
    reserve = f["reserve_minor"]
    cash = [r["cash_minor"] for r in timeline if r["cash_minor"] is not None]
    return {"timeline": timeline, "ending_delta_minor": -amount,
        "minimum_cash_minor": min(cash + [f["opening_balance_minor"]]) if cash else None,
        "first_reserve_breach": first_below(f, timeline, reserve),
        "first_negative_date": first_below(f, timeline, 0),
        "currency": f["currency"], "affordability": "not_assessed", "limitations": f["limitations"]}
