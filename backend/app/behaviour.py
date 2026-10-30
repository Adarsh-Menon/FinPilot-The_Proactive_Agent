"""Behavioural pattern agent: what is changing in the owner's spending, and where it leads.

Patterns compare the latest 30 days with the 30 before, per non-essential category.
"If this continues" assumes each rising category keeps rising by the same amount every
month, and projects that onto each goal's monthly pace and onto the forecast's reserve.
Advice for every warning comes from the advisor's options. All numbers are calculated
here; the model only explains them through server-filled placeholders.
"""
import re
from datetime import date, timedelta
from .advisor import ESSENTIAL, calculated_items, goal_levers, money, nice, subscriptions
from .assistant import HEDGED, VERDICT
from .goals import DAYS_PER_MONTH

MIN_INCREASE = 50_000      # at least ₹500 a month more
MIN_RATIO = 1.2            # and at least 20% more than the month before
MIN_NEW = 100_000          # new spending (nothing the month before) of at least ₹1,000
STATUS_RANK = {"off_track": 0, "stretch": 1, "on_track": 2}
STATUS_TEXT = {"on_track": "on track", "stretch": "a stretch", "off_track": "off track"}


def windows(as_of):
    """The last 30 days and the 30 days before them.

    Args:
        as_of: balance date.

    Returns:
        ((start, end), (start, end)) dates.
    """
    end = date.fromisoformat(as_of)
    return (end - timedelta(days=29), end), (end - timedelta(days=59), end - timedelta(days=30))


def window_end(transactions, as_of):
    """End of the comparison window: the latest expense, so days not yet imported do not look like less spending.

    Args:
        transactions: ledger rows.
        as_of: balance date.

    Returns:
        ISO date.
    """
    days = [t["date"] for t in transactions if t["kind"] == "expense" and t["date"] <= as_of]
    return max(days) if days else as_of


def scan(transactions, profile):
    """Rising and new non-essential spending, plus every category checked; None without 60 days of history.

    Args:
        transactions: ledger rows.
        profile: owner context.

    Returns:
        Dict with rising patterns and the categories checked, or None.

    Used by: outlook(), voucher recommendations and Fina's outlook answers.
    """
    end = window_end(transactions, profile["as_of"])
    (r_start, r_end), (p_start, p_end) = windows(end)
    if date.fromisoformat(profile["coverage_start"]) > p_start:
        return None
    recent, prior, counts = {}, {}, {}
    for t in transactions:
        category = t.get("category") or "uncategorized"
        if t["kind"] != "expense" or t.get("recurring") or ESSENTIAL.search(category):
            continue
        day = date.fromisoformat(t["date"])
        if r_start <= day <= r_end:
            recent[category] = recent.get(category, 0) - t["amount_minor"]
            counts[category] = counts.get(category, [0, 0])
            counts[category][0] += 1
        elif p_start <= day <= p_end:
            prior[category] = prior.get(category, 0) - t["amount_minor"]
            counts.setdefault(category, [0, 0])[1] += 1
    found, new = [], []
    for category, now in recent.items():
        before = prior.get(category, 0)
        if before > 0 and now - before >= MIN_INCREASE and now >= before * MIN_RATIO:
            found.append({"category": category, "recent_month": now, "prior_month": before, "extra_per_month": now - before,
                "increase_percent": round((now - before) * 100 / before), "recent_count": counts[category][0],
                "prior_count": counts[category][1]})
        elif before == 0 and now >= MIN_NEW:
            # A zero baseline is new spending, not an infinite rise; it may be one-off, so it is not projected.
            new.append({"category": category, "recent_month": now, "recent_count": counts[category][0]})
    checked = [{"category": c, "recent_month": recent.get(c, 0), "prior_month": prior.get(c, 0),
        "change_percent": round((recent.get(c, 0) - prior[c]) * 100 / prior[c]) if prior.get(c) else None}
        for c in set(recent) | set(prior)]
    return {"rising": sorted(found, key=lambda p: -p["extra_per_month"])[:3], "new": sorted(new, key=lambda n: -n["recent_month"]),
        "checked": sorted(checked, key=lambda c: -(c["recent_month"] + c["prior_month"]))[:8],
        "window": {"recent": [r_start.isoformat(), r_end.isoformat()], "prior": [p_start.isoformat(), p_end.isoformat()]}}


def patterns(transactions, profile):
    """Rising non-essential categories only; None when history does not cover 60 days.

    Args:
        transactions: ledger rows.
        profile: owner context.

    Returns:
        List of patterns or None.
    """
    result = scan(transactions, profile)
    return None if result is None else result["rising"]


def months_from(as_of, months):
    """The date some months after the balance date.

    Args:
        as_of: ISO date.
        months: months.

    Returns:
        ISO date or None beyond 50 years.
    """
    if months <= 0 or months > 600:
        return None
    return (date.fromisoformat(as_of) + timedelta(days=round(months * DAYS_PER_MONTH))).isoformat()


def status_for(pace, needed):
    """Goal status for a pace against what the goal needs.

    Args:
        pace: monthly pace or None.
        needed: monthly need.

    Returns:
        'on_track', 'stretch', 'off_track' or None.
    """
    if pace is None:
        return None
    return "on_track" if pace >= needed else "stretch" if pace > 0 else "off_track"


def outlook(profile, transactions, goals, run):
    """The Outlook: rising patterns, what they mean for goals and the reserve if they continue, and advice.

    Args:
        profile: owner context.
        transactions: ledger rows.
        goals: goals with progress.
        run: forecast run.

    Returns:
        Dict with patterns, warnings (goal or reserve, with facts) and options.

    Used by: GET /api/v1/outlook. Seen on the Outlook page.
    """
    scanned = scan(transactions, profile)
    if scanned is None:
        return {"ready": False, "reason": "history", "patterns": [], "warnings": []}
    found = scanned["rising"]
    as_of, extra = profile["as_of"], sum(p["extra_per_month"] for p in found)
    pattern_categories = {p["category"] for p in found}
    back_to_usual = [{"ref": f"b{i}", "kind": "back_to_usual", "summary": f"Bring {p['category']} back to its usual level",
        "facts": {"category": p["category"], "usual_month": p["prior_month"], "recent_month": p["recent_month"],
            "saving_per_month": p["extra_per_month"]}} for i, p in enumerate(found, 1)]
    warnings = []
    if extra:
        for i, goal in enumerate(goals, 1):
            prog = goal["progress"]
            if prog["status"] not in STATUS_RANK or prog["pace_per_month_minor"] is None:
                continue
            months = max(prog["days_left"] / DAYS_PER_MONTH, 0)
            # Linear continuation: each month adds the same increase again, so the average drag grows with time.
            drag = round(extra * (months + 1) / 2)
            pace, needed, remaining = prog["pace_per_month_minor"], prog["needed_per_month_minor"], prog["remaining_minor"]
            pace_if = pace - drag
            status_if = status_for(pace_if, needed)
            reach_now = months_from(as_of, remaining / pace) if pace > 0 else None
            reach_if = months_from(as_of, remaining / pace_if) if pace_if > 0 else None
            slips = reach_now and reach_if and (date.fromisoformat(reach_if) - date.fromisoformat(reach_now)).days >= 30
            if STATUS_RANK[status_if] >= STATUS_RANK[prog["status"]] and not slips:
                continue
            facts = {"goal_name": goal["name"], "status_now": STATUS_TEXT[prog["status"]], "status_if": STATUS_TEXT[status_if],
                "needed_per_month": needed, "pace_now": pace, "pace_if_continues": pace_if, "goal_target_date": goal["target_date"]}
            if reach_now:
                facts["reach_date_now"] = reach_now
            if reach_if:
                facts["reach_date_if"] = reach_if
            options = back_to_usual + [o for o in goal_levers(goal, prog, profile, transactions)
                if o["kind"] != "goal_spend_less" or o["facts"]["category"] not in pattern_categories]
            warnings.append({"ref": f"w{i}", "kind": "goal", "facts": facts, "summary": f"{goal['name']} is at risk",
                "advice": options[:3]})
        reserve = run and run["forecast"]["reserve_minor"]
        timeline = run["forecast"]["timeline"] if run else []
        if reserve is not None and timeline and timeline[0]["cash_minor"] is not None:
            daily = extra / DAYS_PER_MONTH
            adjusted = [(r["date"], r["cash_minor"] - round(daily * (n + 1))) for n, r in enumerate(timeline)]
            breach_if = next((d for d, cash in adjusted if cash < reserve), None)
            breach_now = run["checks"].get("first_reserve_breach")
            if breach_if and (not breach_now or breach_if < breach_now):
                facts = {"reserve": reserve, "breach_date_if": breach_if, "lowest_if": min(c for _, c in adjusted)}
                if breach_now:
                    facts["breach_date_now"] = breach_now
                warnings.append({"ref": "wr", "kind": "reserve", "facts": facts, "summary": "Your reserve is at risk",
                    "advice": back_to_usual[:3]})
    return {"ready": True, "has_forecast": run is not None, "patterns": found, "warnings": warnings,
        "new_spending": scanned["new"], "checked": scanned["checked"], "window": scanned["window"],
        "subscriptions": len(subscriptions(transactions, as_of))}


def pattern_text(p, currency):
    """One rising pattern as a sentence.

    Args:
        p: pattern dict.
        currency: ISO code.

    Returns:
        Text such as "Food delivery: ₹18,000 in the last 30 days, up from ₹9,000...".

    Used by: Outlook > Patterns to watch.
    """
    return (f"{p['category'].capitalize()}: {money(p['recent_month'], currency)} in the last 30 days, up from "
        f"{money(p['prior_month'], currency)} the month before (+{p['increase_percent']}%"
        + (f", {p['recent_count']} purchases instead of {p['prior_count']})." if p["recent_count"] != p["prior_count"] else ")."))


def warning_text(w, currency):
    """One warning as a sentence (goal at risk or reserve dip).

    Args:
        w: warning dict.
        currency: ISO code.

    Returns:
        Text.

    Used by: Outlook warnings.
    """
    f = w["facts"]
    if w["kind"] == "reserve":
        text = (f"If this keeps up, your balance would dip under your {money(f['reserve'], currency)} reserve on "
            f"{nice(f['breach_date_if'])}, with a low of {money(f['lowest_if'], currency)}.")
        return text + (f" Today's forecast has that happening on {nice(f['breach_date_now'])}." if "breach_date_now" in f else "")
    text = (f"If this keeps up, {f['goal_name']} goes from {f['status_now']} to {f['status_if']}. It needs "
        f"{money(f['needed_per_month'], currency)} a month; your pace would fall from {money(f['pace_now'], currency)} to "
        f"{money(f['pace_if_continues'], currency)} a month.")
    if "reach_date_if" in f and "reach_date_now" in f:
        text += f" You'd get there around {nice(f['reach_date_if'])} instead of {nice(f['reach_date_now'])}."
    elif "reach_date_now" in f:
        text += f" At that pace it wouldn't be reached at all, compared with {nice(f['reach_date_now'])} today."
    return text


def advice_items(options, currency):
    """Advice for the Outlook, in Fina's calculated voice.

    Args:
        options: options.
        currency: ISO code.

    Returns:
        Rendered items.
    """
    return calculated_items(options, currency)


MONEY = {"recent_month", "prior_month", "extra_per_month", "needed_per_month", "pace_now", "pace_if_continues", "reserve", "lowest_if"}
DATES = {"goal_target_date", "reach_date_now", "reach_date_if", "breach_date_if", "breach_date_now"}
MEANINGS = {"category": "Spending category that is rising", "recent_month": "Spending in this category in the last 30 days",
    "prior_month": "Spending in this category in the 30 days before that", "extra_per_month": "How much more was spent than the month before",
    "increase_percent": "Percent increase over the month before", "recent_count": "Number of purchases in the last 30 days",
    "prior_count": "Number of purchases in the 30 days before that", "goal_name": "Name of the goal at risk",
    "status_now": "The goal's status today", "status_if": "The goal's status if the rising spending continues",
    "needed_per_month": "Amount the goal needs each month to be reached on time",
    "pace_now": "How much the balance grows each month in today's forecast",
    "pace_if_continues": "Average monthly growth of the balance if the rising spending keeps rising until the target date",
    "goal_target_date": "The goal's target date", "reach_date_now": "When the goal is reached at today's pace",
    "reach_date_if": "When the goal would be reached if the rising spending continues",
    "reserve": "Cash the owner wants to keep in reserve", "breach_date_if": "First date the balance would go below the reserve if the rising spending continues",
    "breach_date_now": "First date the balance goes below the reserve in today's forecast",
    "lowest_if": "Lowest balance in the forecast if the rising spending continues"}
REQUIRED = {"goal": "status_if", "reserve": "breach_date_if"}


def for_model(view):
    """Warnings and patterns as the pattern agent sees them: scoped placeholder keys, money in major units.

    Args:
        view: from outlook().

    Returns:
        Dict for the prompt.
    """
    def scoped(ref, facts):
        """Prefix a warning's or pattern's facts with its ref and add meanings.

        Args:
            ref: e.g. 'w1' or 'p1'.
            facts: the facts.

        Returns:
            Scoped facts dict.
        """
        return {f"{ref}_{k}": {"value": round(v / 100, 2) if k in MONEY else v, "meaning": MEANINGS[k]} for k, v in facts.items()}
    return {"currency_note": "money in major currency units",
        "warnings": [{"ref": w["ref"], "kind": w["kind"], "summary": w["summary"], "facts": scoped(w["ref"], w["facts"])} for w in view["warnings"]],
        "patterns": [{"ref": f"p{i}", "facts": scoped(f"p{i}", p)} for i, p in enumerate(view["patterns"], 1)]}


def show(name, value, currency):
    """Display one pattern or warning fact.

    Args:
        name: fact name.
        value: value.
        currency: ISO code.

    Returns:
        Text.
    """
    if name in MONEY:
        return money(value, currency)
    if name in DATES:
        return nice(value)
    return str(value)


def render_narrative(items, view, currency):
    """Fill the pattern agent's placeholders from the cited warning and patterns; None if unsupported.

    Args:
        items: model items.
        view: from outlook().
        currency: ISO code.

    Returns:
        Rendered items or None.

    Used by: "Explain with Fina" on the Outlook.
    """
    by_ref = {w["ref"]: w for w in view["warnings"]}
    pattern_keys = {f"p{i}_{k}": (k, v) for i, p in enumerate(view["patterns"], 1) for k, v in p.items()}
    out, seen = [], set()
    for item in items:
        warning = by_ref.get(item["ref"])
        if warning is None or item["ref"] in seen or re.search(r"\d", item["title"]):
            return None
        keys = {**pattern_keys, **{f"{warning['ref']}_{k}": (k, v) for k, v in warning["facts"].items()}}
        text = item["text"]
        used = re.findall(r"\{([a-z0-9_]+)\}", text)
        if any(k not in keys for k in used) or re.search(r"\d", re.sub(r"\{[a-z0-9_]+\}", "", text)):
            return None
        if f"{warning['ref']}_{REQUIRED[warning['kind']]}" not in used:
            return None  # every warning must state its consequence
        filled = re.sub(r"\{([a-z0-9_]+)\}", lambda m: show(*keys[m.group(1)], currency), text)
        if VERDICT.search(HEDGED.sub("", filled)):
            return None
        seen.add(item["ref"])
        out.append({"ref": item["ref"], "title": item["title"].strip(), "text": filled})
    return out or None
