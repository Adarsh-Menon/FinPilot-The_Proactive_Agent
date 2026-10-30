"""Advisor: deterministic options ("levers") with exact numbers, explained by the model.

The server calculates every option and its effect: cutting a discretionary category,
paying a purchase later, a smaller purchase that keeps the reserve, completing the plan,
and a goal date that the current pace can reach. The advisor only chooses and explains
options, writing numbers as {placeholders} that the server fills; the risk reviewer then
approves or blocks the rendered advice. Essentials are never offered as cuts.
"""
import re
from datetime import date, timedelta
from .assistant import HEDGED, SYMBOLS, VERDICT
from .finance import simulate
from .goals import DAYS_PER_MONTH

ESSENTIAL = re.compile(r"rent|housing|health|medic|pharma|hospital|doctor|utilit|electric|water|gas|internet|phone bill|"
    r"insurance|loan|emi|mortgage|debt|education|school|tuition|fees|tax|grocer|childcare|salary|income|uncategorized", re.I)
PLACEHOLDER = re.compile(r"\{([a-z0-9_]+)\}")
CUT_SHARE = 0.25


def money(minor, currency):
    """Format minor units with the symbol, e.g. "₹3,932".

    Args:
        minor: amount.
        currency: ISO code.

    Returns:
        Text.
    """
    sign = "-" if minor < 0 else ""
    return f"{sign}{SYMBOLS.get(currency, currency + ' ')}{abs(minor) / 100:,.0f}"


def nice(day):
    """Format an ISO date as "7 Nov 2026".

    Args:
        day: ISO date.

    Returns:
        Text.
    """
    d = date.fromisoformat(day)
    return f"{d.day} {d.strftime('%b %Y')}"


def lowest_and_breach(timeline, opening, reserve):
    """Lowest balance and first date under the reserve in a timeline.

    Args:
        timeline: daily rows.
        opening: opening balance.
        reserve: reserve amount.

    Returns:
        (lowest minor units, breach ISO date), or (None, None) without balances.

    Used by: levers().
    """
    cash = [r["cash_minor"] for r in timeline if r["cash_minor"] is not None]
    if not cash:
        return None, None
    lowest = min(cash + ([opening] if opening is not None else []))
    breach = next((r["date"] for r in timeline if reserve is not None and r["cash_minor"] < reserve), None)
    return lowest, breach


def levers(run, profile, scenario, goals_with_progress):
    """Every option FinPilot can stand behind, each with its own facts.

    Options include delaying or shrinking a purchase, pausing subscriptions, trimming non-essential categories,
    giving a goal more time and completing the plan.

    Args:
        run: forecast run.
        profile: owner context.
        scenario: purchase being discussed, or None.
        goals_with_progress: goals with calculated progress.

    Returns:
        List of option dicts (ref, kind, summary, facts in minor units and ISO dates).

    Used by: The advisor. Seen as "Here's what would help" in chat and on Overview.
    """
    f, analysis = run["forecast"], run["analysis"]
    reserve, opening = f["reserve_minor"], f["opening_balance_minor"]
    base = simulate(run, scenario["amount_minor"], scenario["date"])["timeline"] if scenario else f["timeline"]
    current_low, current_breach = lowest_and_breach(base, opening, reserve)
    out = []
    if opening is None:
        return out
    # 1. Spend less in the largest discretionary categories.
    spend = sorted(((c, a) for c, a in analysis["by_category"].items() if a > 0 and not ESSENTIAL.search(c)), key=lambda x: -x[1])
    for i, (category, total) in enumerate(spend[:2], 1):
        monthly = round(total / max(f["history_days"], 1) * DAYS_PER_MONTH)
        saving = round(monthly * CUT_SHARE)
        if saving < 100:
            continue
        per_day = saving / DAYS_PER_MONTH
        adjusted = [{**r, "cash_minor": r["cash_minor"] + round(per_day * (n + 1))} for n, r in enumerate(base)]
        low, breach = lowest_and_breach(adjusted, opening, reserve)
        facts = {"category": category, "monthly_spend": monthly, "saving_per_month": saving, "new_lowest_balance": low}
        if breach:
            facts["new_reserve_breach"] = breach
        out.append({"ref": f"c{i}", "kind": "spend_less", "facts": facts,
            "summary": f"Spend a quarter less on {category}"})
    if scenario:
        # 2. Pay later: after the next income in the plan, if that helps.
        incomes = sorted(x["date"] for x in profile["scheduled_flows"]
            if x["amount_minor"] > 0 and scenario["date"] < x["date"] <= f["timeline"][-1]["date"])
        if incomes and (current_breach or (current_low is not None and current_low < 0)):
            later = simulate(run, scenario["amount_minor"], incomes[0])["timeline"]
            low, breach = lowest_and_breach(later, opening, reserve)
            if low is not None and low > current_low:
                facts = {"pay_on": incomes[0], "new_lowest_balance": low}
                if breach:
                    facts["new_reserve_breach"] = breach
                out.append({"ref": "mv", "kind": "pay_later", "facts": facts, "summary": "Pay after your next income"})
        # 3. A smaller amount that keeps the reserve for the whole forecast.
        if reserve is not None and current_breach:
            # Without the purchase, the tightest day from the purchase date onward sets how much fits.
            room = min(r["cash_minor"] - reserve for r in f["timeline"] if r["date"] >= scenario["date"])
            if 0 < room < scenario["amount_minor"]:
                out.append({"ref": "sm", "kind": "smaller_amount", "facts": {"max_amount": room, "purchase_amount": scenario["amount_minor"]},
                    "summary": "Spend a smaller amount"})
    # 4. Complete the plan when no income is entered.
    if not any(x["amount_minor"] > 0 for x in profile["scheduled_flows"] if x["date"] > f["as_of"]):
        out.append({"ref": "pl", "kind": "complete_plan", "facts": {}, "summary": "Add your upcoming income"})
    # 5. A goal date the current pace can reach.
    for i, goal in enumerate([g for g in goals_with_progress if g["progress"]["status"] in {"stretch", "off_track"}][:2], 1):
        p = goal["progress"]
        if p["pace_per_month_minor"] and p["pace_per_month_minor"] > 0:
            months = p["remaining_minor"] / p["pace_per_month_minor"]
            new_date = (date.fromisoformat(f["as_of"]) + timedelta(days=round(months * DAYS_PER_MONTH))).isoformat()
            out.append({"ref": f"g{i}", "kind": "goal_later", "facts": {"goal_name": goal["name"], "goal_new_date": new_date,
                "goal_pace_per_month": p["pace_per_month_minor"]}, "summary": f"Give {goal['name']} more time"})
    return out


MONEY_FACTS = {"monthly_spend", "saving_per_month", "new_lowest_balance", "max_amount", "purchase_amount", "goal_pace_per_month",
    "monthly_cost", "goal_needed_per_month", "yearly_saving", "extra_per_month", "interest_saved", "sip_per_month",
    "surplus_per_month", "essentials_per_month", "fund_target", "still_needed", "usual_month", "recent_month"}
DATE_FACTS = {"new_reserve_breach", "pay_on", "goal_new_date", "reach_date", "current_reach_date", "new_payoff_date"}
MEANINGS = {"category": "Spending category name", "monthly_spend": "Average monthly spending in this category",
    "saving_per_month": "Monthly amount saved by spending a quarter less in this category",
    "new_lowest_balance": "Lowest balance in the forecast if this option is taken",
    "new_reserve_breach": "First date the balance would still go below the reserve with this option",
    "pay_on": "Date of the next income in the plan; paying then instead of the original date",
    "max_amount": "Largest purchase that keeps the balance above the reserve for the whole forecast",
    "purchase_amount": "The purchase amount the owner is considering",
    "goal_name": "Name of the goal", "goal_new_date": "Target date the current monthly pace can reach",
    "goal_pace_per_month": "How much the balance grows each month in the latest forecast",
    "subscription": "Name of a repeating monthly charge found in the owner's history",
    "monthly_cost": "What this subscription costs each month",
    "goal_needed_per_month": "Amount the goal needs each month to be reached on its target date",
    "share_of_need_percent": "Percent of the goal's monthly need this saving covers (a bare number; add % or 'percent' after it)",
    "reach_date": "Date the goal would be reached with this saving added to the current monthly pace",
    "current_reach_date": "Date the goal would be reached at the current monthly pace, without this saving",
    "months_sooner": "Whole months sooner the goal would be reached with this saving",
    "yearly_saving": "What this saving adds up to over a year",
    "debt_name": "Name of the debt", "extra_per_month": "Extra amount to pay on this debt each month",
    "rate_percent": "Yearly interest rate of the debt, in percent", "interest_saved": "Interest saved over the life of the debt by paying extra",
    "new_payoff_date": "Date the debt would be cleared with the extra payment",
    "sip_per_month": "Suggested monthly investment (SIP) amount: half of the surplus left after goals, rounded down",
    "surplus_per_month": "Money left each month after the balance growth covers every goal's monthly need",
    "essentials_per_month": "Spending on essentials such as rent, bills and groceries in the last 30 days",
    "fund_target": "Three months of essential spending", "still_needed": "How much more cash the emergency fund needs",
    "threshold_percent": "Interest rate above which paying off debt beats investing, in percent",
    "usual_month": "Spending in this category in the month before it rose", "recent_month": "Spending in this category in the last 30 days"}


def subscriptions(transactions, as_of):
    """Repeating monthly charges with a steady amount, outside essentials, from the last four months.

    Args:
        transactions: ledger rows.
        as_of: balance date.

    Returns:
        List of {name, monthly} subscriptions.

    Used by: levers() and goal_levers(). Seen as "Pause Netflix" style suggestions.
    """
    from .investigator import normalize
    start = date.fromisoformat(as_of) - timedelta(days=120)
    groups = {}
    for t in transactions:
        if t["kind"] == "expense" and start < date.fromisoformat(t["date"]) <= date.fromisoformat(as_of) \
                and not ESSENTIAL.search(f"{t.get('category') or ''} {t['description']}") and normalize(t["description"]):
            groups.setdefault(normalize(t["description"]), []).append(t)
    found = []
    for rows in groups.values():
        rows.sort(key=lambda t: t["date"])
        days = [(date.fromisoformat(b["date"]) - date.fromisoformat(a["date"])).days for a, b in zip(rows, rows[1:])]
        amounts = [abs(t["amount_minor"]) for t in rows]
        if len(rows) >= 2 and all(25 <= d <= 35 for d in days) and max(amounts) <= min(amounts) * 1.05:
            found.append({"name": " ".join(normalize(rows[-1]["description"]).split()[:3]).title(), "monthly": amounts[-1]})
    return sorted(found, key=lambda x: -x["monthly"])


def goal_levers(goal, prog, profile, transactions):
    """Options that bring one goal closer: pause a subscription or spend less on a discretionary category.

    Args:
        goal: the goal.
        prog: its progress.
        profile: owner context.
        transactions: ledger rows.

    Returns:
        List of options with the saving, share of the monthly need and how much sooner the goal is reached.

    Used by: Goal recovery routes and "Ways to get there sooner" on goal cards.
    """
    if prog["status"] in {"reached", "past_due"} or not prog["remaining_minor"]:
        return []
    from .investigator import normalize
    as_of = profile["as_of"]
    start, end = date.fromisoformat(profile["coverage_start"]), date.fromisoformat(as_of)
    days = max((end - start).days + 1, 1)
    subs = subscriptions(transactions, as_of)
    sub_names = {s["name"].lower() for s in subs}
    spend = {}
    for t in transactions:
        # Subscription charges are offered on their own, so category cuts leave them out to avoid counting twice.
        is_sub = " ".join(normalize(t["description"]).split()[:3]) in sub_names
        if t["kind"] == "expense" and not t.get("recurring") and not is_sub and start <= date.fromisoformat(t["date"]) <= end:
            category = t.get("category") or "uncategorized"
            spend[category] = spend.get(category, 0) - t["amount_minor"]
    pace, need, remaining = prog["pace_per_month_minor"], prog["needed_per_month_minor"], prog["remaining_minor"]

    def effect(saving):
        """What a monthly saving does for the goal: share of the need and the new reach date.

        Args:
            saving: monthly saving in minor units.

        Returns:
            Facts dict for the option.
        """
        facts = {"goal_name": goal["name"], "goal_needed_per_month": need, "yearly_saving": saving * 12,
            "share_of_need_percent": min(100, round(saving * 100 / need)) if need else 100}
        base = pace if pace and pace > 0 else 0
        reach = as_of_plus(as_of, remaining / (base + saving))
        if not base:
            if reach:
                facts["reach_date"] = reach  # with no growing balance, this saving alone sets the date
            return facts
        sooner = round(remaining / base - remaining / (base + saving))
        if sooner >= 1 and reach:  # a date only matters when it moves by at least a month
            facts.update(reach_date=reach, current_reach_date=as_of_plus(as_of, remaining / base), months_sooner=sooner)
        return facts

    out = []
    for i, sub in enumerate(subs[:2], 1):
        out.append({"ref": f"s{i}", "kind": "cancel_subscription", "summary": f"Pause {sub['name']}",
            "facts": {"subscription": sub["name"], "monthly_cost": sub["monthly"], **effect(sub["monthly"])}})
    cuts = sorted(((c, a) for c, a in spend.items() if a > 0 and not ESSENTIAL.search(c)), key=lambda x: -x[1])
    for i, (category, total) in enumerate(cuts[:2], 1):
        monthly = round(total / days * DAYS_PER_MONTH)
        saving = round(monthly * CUT_SHARE)
        if saving >= 100:
            out.append({"ref": f"c{i}", "kind": "goal_spend_less", "summary": f"Spend a quarter less on {category}",
                "facts": {"category": category, "monthly_spend": monthly, "saving_per_month": saving, **effect(saving)}})
    return out


def as_of_plus(as_of, months):
    """The date some months after the balance date.

    Args:
        as_of: ISO date.
        months: months (fractional).

    Returns:
        ISO date or None beyond 50 years.
    """
    if months <= 0 or months > 600:
        return None
    return (date.fromisoformat(as_of) + timedelta(days=round(months * DAYS_PER_MONTH))).isoformat()


def for_model(options, currency):
    """Options as the advisor model sees them: placeholder keys with values and meanings.

    Args:
        options: from levers().
        currency: ISO code.

    Returns:
        List of options for the prompt.
    """
    shown = []
    for o in options:
        # Money in major units (rupees), matching how the rendered advice reads to the risk reviewer.
        facts = {f"{o['ref']}_{k}": {"value": round(v / 100, 2) if k in MONEY_FACTS else v, "meaning": MEANINGS[k]}
            for k, v in o["facts"].items()}
        shown.append({"ref": o["ref"], "kind": o["kind"], "summary": o["summary"], "facts": facts})
    return {"currency": currency, "money_unit": "major currency units", "options": shown}


def show(key, value, currency):
    """Display one option fact (money, date or number) by its key.

    Args:
        key: scoped fact key, e.g. 'o1_monthly_cost'.
        value: its value.
        currency: ISO code.

    Returns:
        Text.
    """
    name = key.split("_", 1)[1]
    if name in MONEY_FACTS:
        return money(value, currency)
    if name in DATE_FACTS:
        return nice(value)
    return str(value)  # counts and percentages as bare numbers, so "{x}%" and "{x} percent" both read correctly


def why_for(o, currency):
    """The reasoning behind an option, from its own numbers: what it is based on and what it trades off.

    Args:
        o: the option.
        currency: ISO code.

    Returns:
        A "Why: ..." sentence.

    Used by: Seen as the "Why:" line under every suggestion.
    """
    f = {k: (money(v, currency) if k in MONEY_FACTS else nice(v) if k in DATE_FACTS else v) for k, v in o["facts"].items()}
    kind = o["kind"]
    if kind in {"spend_less", "goal_spend_less"}:
        return (f"Why: {f['category']} is one of your largest non-essential costs at about {f['monthly_spend']} a month. "
            "A quarter less is a realistic change that leaves essentials untouched.")
    if kind == "cancel_subscription":
        return (f"Why: {f['subscription']} charges {f['monthly_cost']} every month and repeats automatically. Pausing is easy "
            "to undo if you miss it.")
    if kind == "back_to_usual":
        return (f"Why: this category was about {f['usual_month']} a month before rising to {f['recent_month']}. Going back "
            "reverses only the increase, not your usual habits.")
    if kind == "pay_later":
        return f"Why: paying on {f['pay_on']} means your next income arrives first, so the purchase does not come out of your buffer."
    if kind == "smaller_amount":
        return f"Why: {f['max_amount']} is the most that keeps your balance above your reserve on every day of the forecast."
    if kind == "complete_plan":
        return "Why: without upcoming income the forecast can only go down, so every other suggestion looks worse than reality."
    if kind == "goal_later":
        return f"Why: at your current pace of {f['goal_pace_per_month']} a month, {f['goal_new_date']} is when the money is really there."
    if kind == "pay_extra_debt":
        return (f"Why: {f['debt_name']} charges {o['facts']['rate_percent']}% a year, your highest rate, so each extra rupee "
            f"there saves the most interest ({f['interest_saved']} in total).")
    if kind == "start_sip":
        return (f"Why: after your goals you have about {f['surplus_per_month']} a month spare; investing half keeps the other "
            "half as a cushion while your money starts compounding.")
    if kind == "build_emergency_fund":
        return (f"Why: your essentials cost about {f['essentials_per_month']} a month; three months ({f['fund_target']}) "
            "covers a job gap or medical bill without borrowing.")
    if kind == "clear_costly_debt":
        return f"Why: debt above {o['facts']['threshold_percent']}% a year costs more than investments typically earn."
    return ""


def render_items(items, options, currency):
    """Fill each advice item's placeholders from its own option; None for any unsupported item.

    Args:
        items: the advisor model's items.
        options: the options it was given.
        currency: ISO code.

    Returns:
        Rendered items, or None when any item is not grounded.

    Used by: Advice in chat, on Overview and goal ideas.
    """
    by_ref = {o["ref"]: o for o in options}
    rendered = []
    for item in items:
        option = by_ref.get(item["ref"])
        if option is None or re.search(r"\d", item["title"]):
            return None
        keys = {f"{option['ref']}_{k}": v for k, v in option["facts"].items()}
        text = item["text"]
        if any(k not in keys for k in PLACEHOLDER.findall(text)) or re.search(r"\d", PLACEHOLDER.sub("", text)):
            return None
        # An option that still dips under the reserve must say when; leaving it out would mislead.
        breach_key = f"{option['ref']}_new_reserve_breach"
        if breach_key in keys and "{" + breach_key + "}" not in text:
            return None
        filled = PLACEHOLDER.sub(lambda m: show(m.group(1), keys[m.group(1)], currency), text)
        if VERDICT.search(HEDGED.sub("", filled)):
            return None
        rendered.append({"ref": option["ref"], "kind": option["kind"], "title": item["title"].strip(), "text": filled,
            "why": why_for(option, currency)})
    return rendered or None


def calculated_items(options, currency):
    """Code-written wording of the same options, in Fina's voice.

    Args:
        options: options.
        currency: ISO code.

    Returns:
        List of {ref, title, text, why}.

    Used by: Fallback when AI is off or rejected; Outlook, Invest and Debts advice.
    """
    items = []
    for o in options[:3]:
        v = {k: (money(x, currency) if k in MONEY_FACTS else nice(x) if k in DATE_FACTS else x) for k, x in o["facts"].items()}
        if o["kind"] == "back_to_usual":
            text = (f"Going back to about {v['usual_month']} a month on {v['category']}, from {v['recent_month']}, frees up "
                f"{v['saving_per_month']} a month and stops the slide.")
        elif o["kind"] == "spend_less":
            text = (f"You spend about {v['monthly_spend']} a month on {v['category']}. A quarter less would free up "
                f"{v['saving_per_month']} a month and lift your lowest balance to {v['new_lowest_balance']}.")
            text += f" You'd still dip under your reserve from {v['new_reserve_breach']}." if "new_reserve_breach" in v else ""
        elif o["kind"] == "pay_later":
            text = f"Paying on {v['pay_on']}, after your next income, keeps your lowest balance at {v['new_lowest_balance']}."
            text += f" You'd still dip under your reserve from {v['new_reserve_breach']}." if "new_reserve_breach" in v else ""
        elif o["kind"] == "smaller_amount":
            text = (f"Up to {v['max_amount']} instead of {v['purchase_amount']} would keep you above your reserve for the "
                "whole forecast.")
        elif o["kind"] in {"cancel_subscription", "goal_spend_less"}:
            what = (f"Pausing {v['subscription']} would free up {v['monthly_cost']} a month" if o["kind"] == "cancel_subscription"
                else f"You spend about {v['monthly_spend']} a month on {v['category']}; a quarter less would free up {v['saving_per_month']}")
            text = (f"{what}, or {v['yearly_saving']} a year: about {o['facts']['share_of_need_percent']}% of the "
                f"{v['goal_needed_per_month']} {v['goal_name']} needs each month.")
            if "months_sooner" in v and "reach_date" in v:
                text += f" Added to your current pace, you'd get there by {v['reach_date']}, about {v['months_sooner']} months sooner."
            elif "reach_date" in v:
                text += f" On its own, setting that aside would get you there by {v['reach_date']}."
        elif o["kind"] == "pay_extra_debt":
            text = (f"Paying {v['extra_per_month']} more each month on {v['debt_name']} clears it {o['facts']['months_sooner']} "
                f"months sooner, by {v['new_payoff_date']}, and saves about {v['interest_saved']} in interest.")
        elif o["kind"] == "start_sip":
            text = f"A monthly SIP of {v['sip_per_month']} in a broad, low-cost fund is a comfortable start for your situation."
        elif o["kind"] == "build_emergency_fund":
            text = f"Aim for {v['fund_target']} in easy-to-reach savings; you need about {v['still_needed']} more."
        elif o["kind"] == "clear_costly_debt":
            text = f"Put spare money toward {v['debt_name']} before investing."
        elif o["kind"] == "complete_plan":
            text = "Your forecast has no upcoming income in it. Adding your paydays in Your context will show a truer picture."
        else:
            text = f"At your current pace of {v['goal_pace_per_month']} a month, {v['goal_name']} is reachable by {v['goal_new_date']}."
        items.append({"ref": o["ref"], "kind": o["kind"], "title": o["summary"], "text": text, "why": why_for(o, currency)})
    return items
