"""Goals coach: notes are grounded in the goal's calculated progress, encouraging, and never promise rewards."""
import pytest
from deepeval.metrics import FaithfulnessMetric, GEval
from deepeval.test_case import LLMTestCase, SingleTurnParams

from checks import Check, amounts_grounded, no_guarantees, score_case
from conftest import goldens
from judge import GatewayJudge, judge_enabled

CASES = goldens("goals_coach")


@pytest.fixture(scope="module")
def coached(new_owner):
    """One synthetic owner with history, a forecast and every case's goal; returns notes and progress by case id."""
    client = new_owner()
    client.post("/api/v1/forecasts", json={"horizon_days": 30})
    out = {}
    for case in CASES:
        goal = client.post("/api/v1/goals", json=case["goal"]).json()
        note = client.post(f"/api/v1/goals/{goal['id']}/coach").json()
        out[case["id"]] = (note, goal)
    return out


@pytest.fixture(scope="module")
def judge_metrics():
    if not judge_enabled():
        return []
    judge = GatewayJudge()
    return [FaithfulnessMetric(model=judge, threshold=0.8),
        GEval(name="encouraging, not judgemental", model=judge, threshold=0.7,
            evaluation_params=[SingleTurnParams.ACTUAL_OUTPUT],
            criteria="The note encourages without pressure or blame, is specific to this goal, and suggests a realistic "
                "next step when the goal is behind. It never promises rewards or certain outcomes.")]


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_goals_coach(case, coached, judge_metrics):
    note, goal = coached[case["id"]]
    p, g = goal["progress"], case["goal"]
    context = [f"Goal: {g['name']}", f"Target: ₹{g['target_minor'] // 100:,}", f"Saved: ₹{g['saved_minor'] // 100:,}",
        f"Target date: {g['target_date']}", f"Still needed: ₹{p['remaining_minor'] // 100:,}",
        f"Needed per month: ₹{p['needed_per_month_minor'] // 100:,}", f"Status: {p['status']}"]
    if p.get("pace_per_month_minor") is not None:
        context.append(f"Current pace per month: ₹{p['pace_per_month_minor'] // 100:,}")
    allowed = {g["target_minor"] // 100, g["saved_minor"] // 100, p["remaining_minor"] // 100, p["needed_per_month_minor"] // 100,
        abs(p.get("pace_per_month_minor") or 0) // 100}
    tc = LLMTestCase(input=f"How is my {g['name']} goal going?", actual_output=note.get("note", ""), retrieval_context=context,
        name=case["id"], metadata={"source": note.get("source")})
    named = Check("names the goal", lambda t: (g["name"].lower() in (t.actual_output or "").lower(), "mentions the goal by name"))
    score_case("goals_coach", case, tc, [named, amounts_grounded(allowed), no_guarantees(), *judge_metrics])
