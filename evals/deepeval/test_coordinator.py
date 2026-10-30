"""Coordinator: does Fina route each message correctly and extract exactly what was said?"""
import pytest
from deepeval.test_case import LLMTestCase

from checks import Check, dig, no_named_securities, score_case
from conftest import goldens

CASES = goldens("coordinator")


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_coordinator(case, chat):
    reply = chat(case["question"], page=case.get("page"))
    tc = LLMTestCase(input=case["question"], actual_output=reply["answer"], name=case["id"],
        metadata={"route": reply["route"], "action": reply.get("action")}, tags=case.get("tags"))
    metrics = [Check("route", lambda _: (reply["route"] in case["routes"],
        f"routed to {reply['route']}; expected one of {case['routes']}"))]
    if expect := case.get("expect"):
        def extracted(_):
            wrong = {path: dig(reply, path) for path, want in expect.items() if dig(reply, path) != want}
            return not wrong, f"mismatched fields: {wrong}" if wrong else "every expected field matches"
        metrics.append(Check("extraction", extracted))
    if case.get("forbid_named_securities"):
        metrics.append(no_named_securities())
    score_case("coordinator", case, tc, metrics)
