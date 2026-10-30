"""Debt agent: loans, EMIs, credit cards and rent, what they cost, and how fast they end.

It combines debts the owner enters (balance, interest rate, monthly payment) with
repeating obligations it detects in the ledger, measures pressure as obligations against
monthly income, and works out payoff dates and interest with standard amortization. The
advisor receives its options (pay extra on the costliest debt first); the Outlook shows it.
"""
import math
import re
from datetime import date, timedelta
from .goals import DAYS_PER_MONTH
from .investigator import normalize

OBLIGATION = re.compile(r"\b(emi|loan|mortgage|credit card|card bill|rent|lease)\b", re.I)
HIGH_INTEREST = 12.0   # % a year; above this, paying down beats most savings options


def payoff(balance, rate_percent, monthly, extra=0):
    """Months to clear a balance and the total interest paid, by standard amortization.

    Args:
        balance: amount still owed, in minor units (paise).
        rate_percent: yearly interest rate, e.g. 36 for 36%.
        monthly: regular monthly payment in minor units.
        extra: additional monthly payment in minor units (default 0).

    Returns:
        (months, interest_minor). (0, 0) when nothing is owed; (None, None) when the payment never clears the
        balance.

    Used by: analyse() for each debt's payoff date and the pay-extra option. Seen on Outlook > Debts and fixed
    payments (the "Cleared by" column and "Pay a little extra" advice).
    """
    payment, r = monthly + extra, rate_percent / 100 / 12
    if balance <= 0:
        return 0, 0
    if payment <= 0:
        return None, None
    if r == 0:
        months = balance / payment
        return math.ceil(months), 0
    if payment <= balance * r:
        return None, None
    months = -math.log(1 - r * balance / payment) / math.log(1 + r)
    n = math.ceil(months)
    return n, max(round(payment * months - balance), 0)


def detected(transactions, as_of):
    """Repeating monthly obligations in the last 120 days that look like loans, EMIs, cards or rent.

    Args:
        transactions: the owner's ledger rows (dicts with date, description, amount_minor, kind, category).
        as_of: the owner's balance date, ISO string.

    Returns:
        List of {name, kind ('rent' or 'loan'), monthly_minor, last_paid}, largest payment first.

    Used by: analyse(). Seen as the "found in your history" rows on Outlook > Debts, where the owner enters owed and
    rate.
    """
    start = (date.fromisoformat(as_of) - timedelta(days=120)).isoformat()
    groups = {}
    for t in transactions:
        if t["amount_minor"] >= 0 or not start < t["date"] <= as_of:
            continue
        text = f"{t.get('category') or ''} {t['description']}"
        if t["kind"] == "repayment" or OBLIGATION.search(text):
            groups.setdefault(normalize(t["description"]) or t.get("category") or "obligation", []).append(t)
    found = []
    for name, rows in groups.items():
        rows.sort(key=lambda t: t["date"])
        if len(rows) >= 2 or any(t.get("recurring") for t in rows):
            kind = "rent" if re.search(r"rent|lease", f"{rows[-1].get('category') or ''} {name}", re.I) else "loan"
            found.append({"name": name.title()[:60], "kind": kind, "monthly_minor": -rows[-1]["amount_minor"], "last_paid": rows[-1]["date"]})
    return sorted(found, key=lambda d: -d["monthly_minor"])


def monthly_income(profile, transactions):
    """Income per month: scheduled income in the next 31 days, else income in the last 30 days.

    Args:
        profile: the owner's saved context (as_of, scheduled_flows).
        transactions: the owner's ledger rows.

    Returns:
        Income in minor units (0 when none is known).

    Used by: analyse() for the fixed-payment pressure ratio shown on Outlook > Debts.
    """
    as_of = profile["as_of"]
    soon = (date.fromisoformat(as_of) + timedelta(days=31)).isoformat()
    scheduled = sum(f["amount_minor"] for f in profile["scheduled_flows"] if f["amount_minor"] > 0 and as_of < f["date"] <= soon)
    if scheduled:
        return scheduled
    start = (date.fromisoformat(as_of) - timedelta(days=30)).isoformat()
    return sum(t["amount_minor"] for t in transactions if t["kind"] == "income" and start < t["date"] <= as_of)


def analyse(debts, profile, transactions):
    """The debt agent: payoff dates, obligations against income, advisor options and suggested goals.

    Args:
        debts: debts the owner tracks (name, kind, balance_minor, rate_percent, monthly_minor).
        profile: the owner's saved context.
        transactions: the owner's ledger rows.

    Returns:
        Dict with debts (each with payoff_months, interest_minor, payoff_date, high_interest), detected,
        obligations_minor, income_minor, ratio_percent, pressure ('healthy'|'elevated'|'high'), options for the
        advisor, suggested_goals and costliest.

    Used by: GET /api/v1/debts and the Outlook. Seen on Outlook > Debts and fixed payments, its advice and "Make it
    a goal".
    """
    as_of = profile["as_of"]
    entered_names = {normalize(d["name"]) for d in debts}
    found = [d for d in detected(transactions, as_of) if normalize(d["name"]) not in entered_names]
    rows = []
    for d in debts:
        months, interest = payoff(d["balance_minor"], d["rate_percent"], d["monthly_minor"])
        rows.append({**d, "payoff_months": months, "interest_minor": interest,
            "payoff_date": (date.fromisoformat(as_of) + timedelta(days=round(months * DAYS_PER_MONTH))).isoformat() if months else None,
            "high_interest": d["rate_percent"] >= HIGH_INTEREST and d["kind"] != "rent"})
    obligations = sum(d["monthly_minor"] for d in debts) + sum(d["monthly_minor"] for d in found)
    income = monthly_income(profile, transactions)
    ratio = round(obligations * 100 / income) if income else None
    pressure = None if ratio is None else "high" if ratio >= 50 else "elevated" if ratio >= 35 else "healthy"
    options = []
    payable = sorted((r for r in rows if r["kind"] != "rent" and r["balance_minor"] > 0 and r["payoff_months"]),
        key=lambda r: -r["rate_percent"])
    if payable:
        top = payable[0]
        extra = max(50_000, round(top["monthly_minor"] * 0.1, -2))
        months, interest = payoff(top["balance_minor"], top["rate_percent"], top["monthly_minor"], extra)
        if months and months < top["payoff_months"]:
            options.append({"ref": "d1", "kind": "pay_extra_debt", "summary": f"Pay a little extra on {top['name']}",
                "facts": {"debt_name": top["name"], "extra_per_month": extra, "rate_percent": top["rate_percent"],
                    "months_sooner": top["payoff_months"] - months, "interest_saved": top["interest_minor"] - interest,
                    "new_payoff_date": (date.fromisoformat(as_of) + timedelta(days=round(months * DAYS_PER_MONTH))).isoformat()}})
    goals = [{"name": f"Pay off {r['name']}", "target_minor": r["balance_minor"], "target_date": r["payoff_date"]}
        for r in payable if r["payoff_date"]]
    return {"debts": rows, "detected": found, "obligations_minor": obligations, "income_minor": income, "ratio_percent": ratio,
        "pressure": pressure, "options": options, "suggested_goals": goals,
        "costliest": payable[0]["name"] if payable else None}
