"""FinaPoints: earned for reached goals and referrals, exchanged for demo vouchers.

The ledger is append-only and every entry has a per-owner idempotency key, so retries
never award twice. A goal earns points once, when it is ticked with its full target set aside
and savings transfers dated after the goal was created prove it. Points are never taken
back and the balance never goes below zero. Vouchers are ranked by the owner's own
spending pattern. Vouchers are demonstrations with no monetary value until a
funded partner integration exists.
"""
import re
import secrets
from datetime import date, timedelta
from .advisor import ESSENTIAL

GOAL_POINTS = 1000
REFERRAL_POINTS = 500
WELCOME_POINTS = 250
RUPEES_PER_1000 = 30                    # 1,000 FinaPoints = ₹30 (whole-number maths, no rounding drift)
CATALOG = [
    {"id": "food_30", "type": "food", "title": "₹30 food voucher", "points": 1000, "value_minor": 3000},
    {"id": "food_60", "type": "food", "title": "₹60 food voucher", "points": 2000, "value_minor": 6000},
    {"id": "entertainment_30", "type": "entertainment", "title": "₹30 entertainment voucher", "points": 1000, "value_minor": 3000},
    {"id": "shopping_30", "type": "shopping", "title": "₹30 shopping voucher", "points": 1000, "value_minor": 3000},
    {"id": "transport_30", "type": "transport", "title": "₹30 transport voucher", "points": 1000, "value_minor": 3000},
]
TYPES = {"food": r"food|dining|eating|cafe|restaurant|swiggy|zomato|delivery|lunch|snack",
    "entertainment": r"entertain|movie|cinema|stream|subscription|netflix|music|game|ott",
    "shopping": r"shop|cloth|fashion|amazon|flipkart|apparel|gadget",
    "transport": r"transport|uber|ola|cab|taxi|metro|fuel|petrol|travel|auto"}


def new_code():
    """A random 8-character code from an unambiguous alphabet (no 0/O or 1/I).

    Returns:
        The code string.

    Used by: Referral codes and demo voucher codes. Seen on the Referral page.
    """
    return "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))


def counted(entries):
    """Ledger entries that count toward the balance: no reversals, and goal points only when savings proved them.

    Args:
        entries: the owner's FinaPoints ledger entries (kind, points, ref, proof_minor...).

    Returns:
        The subset of entries that count.

    Used by: balance(), earned() and the points history on the Referral page.
    """
    return [e for e in entries if e["kind"] != "reversal" and not (e["kind"] == "goal" and "proof_minor" not in e)]


def balance(entries):
    """The owner's FinaPoints balance, never below zero.

    Args:
        entries: ledger entries.

    Returns:
        Points (int, >= 0).

    Used by: GET /api/v1/rewards and voucher redemption. Seen as "FinaPoints" on the Referral page.
    """
    return max(sum(e["points"] for e in counted(entries)), 0)


def earned(entries):
    """Total points earned from counted positive entries.

    Args:
        entries: ledger entries.

    Returns:
        Points (int).

    Used by: Seen as "Earned so far" on the Referral page.
    """
    return sum(e["points"] for e in counted(entries) if e["points"] > 0)


# Transfers into savings or investments: the proof that a goal's money was really put aside.
SAVINGS = re.compile(r"saving|\bsave\b|\bfd\b|fixed deposit|\brd\b|recurring deposit|\bsip\b|mutual fund|invest|\bppf\b|\bnps\b|"
    r"\bgoal|piggy|vault|\bjar\b|\bpot\b|\bgold\b", re.I)


def savings_deposits(transactions):
    """Money moved into savings, as [date, amount] in date order; money moved back out comes off the latest
       deposits.

    Args:
        transactions: the owner's ledger rows; only transfers whose text reads like savings, FD, RD, SIP etc. count.

    Returns:
        List of [ISO date, minor units] still in savings.

    Used by: allocate_proof().
    """
    deposits = []
    moves = sorted((t for t in transactions if t["kind"] == "transfer" and SAVINGS.search(f"{t['description']} {t.get('category') or ''}")),
        key=lambda t: t["date"])
    for t in moves:
        if t["amount_minor"] < 0:
            deposits.append([t["date"], -t["amount_minor"]])
            continue
        left = t["amount_minor"]
        while left and deposits:
            take = min(left, deposits[-1][1])
            deposits[-1][1] -= take
            left -= take
            if not deposits[-1][1]:
                deposits.pop()
    return deposits


def proven_goals(entries):
    """Goal ids that have already earned their points with savings proof.

    Args:
        entries: ledger entries.

    Returns:
        Set of goal ids.

    Used by: allocate_proof(), goal_entry() and achievements().
    """
    return {e["ref"] for e in entries if e["kind"] == "goal" and "proof_minor" in e}


def allocate_proof(goals, transactions, proven):
    """Savings found for each reached or rewarded goal, from transfers made on or after the goal was created.

    Each rupee proves one goal; already-rewarded goals keep first claim, then goals in creation order.

    Args:
        goals: goal records with id, status, saved_minor, target_minor and created (ISO date).
        transactions: the owner's ledger rows.
        proven: ids from proven_goals().

    Returns:
        Dict of goal id -> minor units of savings found for it.

    Used by: settle_goals() in main. Seen as "Proven by your savings transfers" or "waiting for proof" on ticked
    goals.
    """
    deposits = savings_deposits(transactions)
    reached = lambda g: g["status"] == "achieved" and g["saved_minor"] >= g["target_minor"]
    found = {}
    for g in sorted((g for g in goals if g["id"] in proven or reached(g)), key=lambda g: (g["id"] not in proven, g["created"])):
        got = 0
        for d in deposits:
            if d[0] >= g["created"] and got < g["target_minor"]:
                take = min(d[1], g["target_minor"] - got)
                d[1] -= take
                got += take
        found[g["id"]] = got
    return found


def proof_status(goal, found):
    """How much savings proof a goal has against its target.

    Args:
        goal: goal record (target_minor, created).
        found: minor units found by allocate_proof().

    Returns:
        Dict with verified, found_minor, needed_minor and since.

    Used by: The goal card's proof note.
    """
    return {"verified": found >= goal["target_minor"], "found_minor": min(found, goal["target_minor"]),
        "needed_minor": goal["target_minor"], "since": goal["created"]}


def goal_entry(goal_id, goal, entries, found):
    """The FinaPoints award a goal has earned, or None. Ticking alone earns nothing; points are never taken back.

    Args:
        goal_id: the goal's id.
        goal: goal record.
        entries: ledger entries.
        found: minor units of savings proof for this goal.

    Returns:
        A ledger entry dict (kind 'goal', 1,000 points, proof_minor, idempotency key) or None.

    Used by: settle_goals() in main. Seen as the "Goal complete" pop-up and +1,000 FinaPoints.
    """
    reached = goal["status"] == "achieved" and goal["saved_minor"] >= goal["target_minor"]
    if reached and found >= goal["target_minor"] and goal_id not in proven_goals(entries):
        return {"kind": "goal", "points": GOAL_POINTS, "ref": goal_id, "label": f"Reached {goal['name']}",
            "proof_minor": goal["target_minor"], "key": f"goal:{goal_id}:proven"}
    return None


def spending_by_type(transactions, as_of):
    """Non-essential spending in the last 30 days, grouped into voucher types (food, transport...).

    Args:
        transactions: the owner's ledger rows.
        as_of: balance date, ISO string.

    Returns:
        (totals by type in minor units, the biggest category behind each type).

    Used by: recommend().
    """
    start = (date.fromisoformat(as_of) - timedelta(days=29)).isoformat()
    totals, sources = {}, {}
    for t in transactions:
        category = t.get("category") or ""
        if t["kind"] != "expense" or not start <= t["date"] <= as_of or ESSENTIAL.search(category):
            continue
        for kind, pattern in TYPES.items():
            if re.search(pattern, f"{category} {t['description']}", re.I):
                totals[kind] = totals.get(kind, 0) - t["amount_minor"]
                sources.setdefault(kind, {}).setdefault(category or "other", 0)
                sources[kind][category or "other"] -= t["amount_minor"]
                break
    return totals, {k: max(v, key=v.get) for k, v in sources.items()}


def recommend(transactions, as_of, rising):
    """Voucher types ordered by the owner's habits: rising categories first, then the largest spend.

    Args:
        transactions: the owner's ledger rows.
        as_of: balance date, ISO string.
        rising: rising categories from the behavioural pattern agent, or None.

    Returns:
        (ordered types, reasons by type with category, spent_minor and rising_percent).

    Used by: GET /api/v1/rewards. Seen as "Vouchers for you" and their reasons on the Referral page.
    """
    totals, top_category = spending_by_type(transactions, as_of)
    rising_types = {}
    for p in rising or []:
        for kind, pattern in TYPES.items():
            if re.search(pattern, p["category"], re.I):
                rising_types.setdefault(kind, p)
                break
    order = sorted(totals, key=lambda k: (k not in rising_types, -totals[k]))
    reasons = {}
    for kind in order:
        if kind in rising_types:
            p = rising_types[kind]
            reasons[kind] = {"category": p["category"], "spent_minor": p["recent_month"], "rising_percent": p["increase_percent"]}
        else:
            reasons[kind] = {"category": top_category[kind], "spent_minor": totals[kind], "rising_percent": None}
    return order, reasons


# Achievements: earned once from verified events, each with a one-time FinaPoints bonus.
ACHIEVEMENTS = [
    {"id": "first_goal", "title": "First Summit", "about": "Reach your first goal", "metric": "goals_reached", "target": 1, "bonus": 250, "tier": "bronze"},
    {"id": "three_goals", "title": "Hat-trick", "about": "Reach three goals", "metric": "goals_reached", "target": 3, "bonus": 500, "tier": "silver"},
    {"id": "five_goals", "title": "Goal Getter", "about": "Reach five goals", "metric": "goals_reached", "target": 5, "bonus": 1000, "tier": "gold"},
    {"id": "planner", "title": "Planner", "about": "Have three goals on the go", "metric": "goals_active", "target": 3, "bonus": 100, "tier": "bronze"},
    {"id": "first_friend", "title": "Better Together", "about": "Invite a friend who joins", "metric": "friends", "target": 1, "bonus": 100, "tier": "bronze"},
    {"id": "first_voucher", "title": "Treat Yourself", "about": "Redeem your first voucher", "metric": "vouchers", "target": 1, "bonus": 0, "tier": "silver"},
    {"id": "debt_free", "title": "Debt Slayer", "about": "Clear a debt you track", "metric": "debts_cleared", "target": 1, "bonus": 500, "tier": "gold"},
    {"id": "investor_ready", "title": "Ready to Grow", "about": "Pass all investment readiness checks", "metric": "investor_ready", "target": 1, "bonus": 250, "tier": "platinum"},
]


def achievements(entries, extra):
    """Every achievement with progress, earned flag and whether it is newly earned.

    Args:
        entries: ledger entries.
        extra: counts not in the ledger: goals_active, debts_cleared, investor_ready.

    Returns:
        List of achievement dicts (id, title, about, target, progress, earned, new, bonus, tier).

    Used by: award_achievements() in main. Seen as the 3D tokens on the Referral page and the unlock pop-up.
    """
    counts = {"goals_reached": len(proven_goals(entries)),  # goals proven by savings transfers
        "friends": sum(e["kind"] == "referral" for e in entries), "vouchers": sum(e["kind"] == "redeem" for e in entries), **extra}
    earned_before = {e["ref"] for e in entries if e["kind"] == "achievement"}
    out = []
    for a in ACHIEVEMENTS:
        value = counts.get(a["metric"], 0)
        earned = a["id"] in earned_before or value >= a["target"]
        out.append({**a, "progress": min(value, a["target"]), "earned": earned, "new": earned and a["id"] not in earned_before})
    return out
