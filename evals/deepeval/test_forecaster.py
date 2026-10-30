"""Forecaster (Fina's answers): grounded in the server's facts, hedged, and actually answering the question."""
import pytest
from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric, GEval
from deepeval.test_case import LLMTestCase, SingleTurnParams

from backend.app.assistant import build_facts, show
from checks import amounts_grounded, no_guarantees, rupee_amounts, score_case
from conftest import PROFILE, goldens
from judge import GatewayJudge, judge_enabled

CASES = goldens("forecaster")


@pytest.fixture(scope="module")
def forecast(new_owner):
    """A synthetic owner with history and a 30-day forecast; returns (client, run id, fact lines, allowed rupees)."""
    client = new_owner()
    run = client.post("/api/v1/forecasts", json={"horizon_days": 30}).json()
    facts = build_facts(run, PROFILE)
    lines = [f"{f['meaning']}: {show(k, f['value'], 'INR')}" for k, f in facts["facts"].items() if f["value"] is not None]
    allowed = rupee_amounts("\n".join(lines))     # exactly the amounts as Fina is allowed to write them
    return client, run["id"], lines, allowed


@pytest.fixture(scope="module")
def judge_metrics():
    """Judge metrics, or none when EVAL_JUDGE=off."""
    if not judge_enabled():
        return []
    judge = GatewayJudge()
    return [FaithfulnessMetric(model=judge, threshold=0.8), AnswerRelevancyMetric(model=judge, threshold=0.7),
        GEval(name="hedged and kind", model=judge, threshold=0.7,
            evaluation_params=[SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT],
            criteria="The answer is warm and plain-spoken, explains rather than judges, uses hedged language about the "
                "future (forecasts are estimates), and never promises an outcome or tells the person what they must do.")]


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_forecaster(case, forecast, chat, judge_metrics):
    client, run_id, lines, allowed = forecast
    reply = chat(case["question"], baseline_id=run_id, client=client)
    tc = LLMTestCase(input=case["question"], actual_output=reply["answer"], retrieval_context=lines, name=case["id"],
        metadata={"route": reply["route"], "source": reply.get("source")})
    score_case("forecaster", case, tc, [amounts_grounded(allowed), no_guarantees(), *judge_metrics])
