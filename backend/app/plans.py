"""Goal planning across all goals: recovery routes for a goal that fell behind, and the
emergency reroute that re-estimates every goal after money is taken out.

All goals share one monthly pace (the forecast's balance growth), so dates are worked
out as a waterfall: savings fill goals in order of their target dates, and each goal's
reach date accounts for the goals ahead of it. Everything here is deterministic.
"""
from datetime import date, timedelta
from .goals import DAYS_PER_MONTH, progress


def add_months(as_of, months):
    """The date a number of months after a date, using 30.44-day months.

    Args:
        as_of: start date, ISO string.
        months: months to add (may be fractional).

    Returns:
        ISO date string, or None for negative, missing or more than 50 years.

    Used by: waterfall(), recovery() and emergency_plan() for reach dates.
    """
    if months is None or months < 0 or months > 600:
        return None
    return (date.fromisoformat(as_of) + timedelta(days=round(months * DAYS_PER_MONTH))).isoformat()


def waterfall(goals, pace, as_of):
    """Reach date for each active goal when one monthly pace funds goals in target-date order.

    Args:
        goals: goals (id, status, target_minor, saved_minor, target_date).
        pace: monthly balance growth in minor units from the latest forecast.
        as_of: balance date, ISO string.

    Returns:
        Dict of goal id -> {reach_date, on_time}.

    Used by: GET /api/v1/goals and emergency_plan(). Seen as "With all your goals: <date>" on each goal card.
    """
    ordered = sorted((g for g in goals if g["status"] == "active"), key=lambda g: g["target_date"])
    out, queued = {}, 0
    for g in ordered:
        queued += max(g["target_minor"] - g["saved_minor"], 0)
        reach = as_of if queued == 0 else add_months(as_of, queued / pace) if pace and pace > 0 else None
        out[g["id"]] = {"reach_date": reach, "on_time": bool(reach and reach <= g["target_date"])}
    return out


def recovery(goal, prog, as_of, options):
    """Routes back on track for a goal that is a stretch or off track, each with its reasoning.

    Keeps a shrinking balance negative, so no made-up date is offered when cuts cannot outrun the shortfall.

    Args:
        goal: the goal (saved_minor, target_minor, target_date).
        prog: its calculated progress from goals.progress().
        as_of: balance date, ISO string.
        options: the goal's levers (subscriptions and spending cuts) from the advisor.

    Returns:
        List of routes: catch_up, then new_date or fix_shortfall, then smaller_target; [] when on track.

    Used by: GET /api/v1/goals/{id}/recovery. Seen as "You've fallen behind. Here's how to get back on track" on a
    goal card.
    """
    if prog["status"] not in {"stretch", "off_track"}:
        return []
    # A shrinking balance stays negative here: cuts first have to cover the shortfall before anything reaches the goal.
    pace = prog["pace_per_month_minor"] or 0
    need, remaining, months = prog["needed_per_month_minor"], prog["remaining_minor"], max(prog["days_left"] / DAYS_PER_MONTH, 0)
    savings = [o for o in options if o["kind"] in {"cancel_subscription", "goal_spend_less", "back_to_usual"}][:2]
    extra = sum(o["facts"].get("monthly_cost") or o["facts"].get("saving_per_month") or 0 for o in savings)
    gap, monthly = max(need - pace, 0), pace + extra
    routes = [{"kind": "catch_up", "title": "Catch up by the original date",
        "facts": {"needed_per_month": need, "pace_now": pace, "gap_per_month": gap, "covered_by_cuts": min(extra, gap),
            "cuts": [o["summary"] for o in savings]},
        "why": "This keeps your date. The gap is what your current pace leaves short each month; the cuts listed are the "
            "ones that fit your spending without touching essentials."}]
    if monthly <= 0:
        routes.append({"kind": "fix_shortfall", "title": "Stop the balance shrinking first",
            "facts": {"shrinking_per_month": -pace, "covered_by_cuts": extra, "still_short": -monthly, "cuts": [o["summary"] for o in savings]},
            "why": "More goes out than comes in each month, so waiting longer never reaches the goal. Until that "
                "shortfall is closed, any date would be made up."})
        return routes
    reach = add_months(as_of, remaining / monthly)
    far = reach is None or reach > add_months(goal["target_date"], 24)
    routes.append({"kind": "new_date", "title": "Keep the amount, move the date",
        "facts": {"reach_date": reach, "monthly": monthly, "with_cuts": bool(extra), "far": far},
        "why": "At this pace it is years away, so moving the date alone isn't a real plan; combine it with the catch-up "
            "or a smaller first milestone." if far else
            "This needs no extra effort beyond the cuts above; it is the date your balance growth can actually reach."})
    reachable = goal["saved_minor"] + round(monthly * months)
    if 0 < reachable < goal["target_minor"]:
        routes.append({"kind": "smaller_target", "title": "Keep the date, aim for a first milestone",
            "facts": {"milestone": reachable, "target": goal["target_minor"], "target_date": goal["target_date"]},
            "why": "A smaller first milestone you can hit on time keeps momentum; the rest can follow as a second goal."})
    return routes


def emergency_plan(goals, amount, pace, as_of, run=None):
    """Take an emergency amount out of goal savings fairly and re-estimate every goal's date.

    Args:
        goals: the owner's goals.
        amount: emergency amount in minor units.
        pace: monthly balance growth in minor units.
        as_of: balance date, ISO string.
        run: latest forecast run, used to recompute each goal's status (optional).

    Returns:
        Dict with amount, from_goals, from_cash, per-goal rows (taken, saved before/after, reach before/after), a
        suggested rebuild goal and the reasoning.

    Used by: POST /api/v1/goals/reroute and /reroute/apply. Seen in the "Had an emergency?" re-plan on Goals and
    from Fina.
    """
    active = [g for g in goals if g["status"] == "active"]
    held = sum(g["saved_minor"] for g in active)
    from_goals = min(amount, held)
    before = waterfall(active, pace, as_of)
    changes, taken = [], 0
    for i, g in enumerate(active):
        share = from_goals - taken if i == len(active) - 1 else round(from_goals * g["saved_minor"] / held) if held else 0
        taken += share
        changes.append({**g, "saved_after": g["saved_minor"] - share, "taken": share})
    after = waterfall([{**g, "saved_minor": g["saved_after"]} for g in changes], pace, as_of)
    rows = [{"id": g["id"], "name": g["name"], "target_minor": g["target_minor"], "target_date": g["target_date"],
        "saved_before": g["saved_minor"], "saved_after": g["saved_after"], "taken": g["taken"],
        "reach_before": before[g["id"]]["reach_date"], "reach_after": after[g["id"]]["reach_date"],
        "on_time_after": after[g["id"]]["on_time"],
        "status_after": progress({**g, "saved_minor": g["saved_after"]}, as_of, run)["status"] if run else None} for g in changes]
    rebuild_months = 3 if not pace or pace <= 0 else max(1, round(amount / pace + 0.49))
    return {"amount": amount, "from_goals": from_goals, "from_cash": amount - from_goals, "goals": rows,
        "rebuild_goal": {"name": "Rebuild emergency fund", "target_minor": amount,
            "target_date": add_months(as_of, max(rebuild_months, 1)) or as_of},
        "why": "Taking from every goal in proportion to what it holds spreads the setback evenly instead of emptying one goal; "
            "dates are re-estimated with your current pace funding goals in date order."}
