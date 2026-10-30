"""Deterministic checks as DeepEval metrics, so code checks and judge scores land in one report."""
import re

from deepeval.metrics import BaseMetric

# A named product to buy: fund houses, big listed companies or an exchange ticker. Education may say
# "index fund"; it may not say which one.
NAMED_SECURITY = re.compile(r"\b(?:hdfc|sbi|icici|axis|kotak|nippon|tata|reliance|infosys|tcs|adani|parag parikh|mirae|"
    r"quant|motilal|uti|aditya birla|dsp|franklin)\b[^.]{0,40}\b(?:fund|share|shares|stock|scheme|etf|bluechip|flexi ?cap)\b"
    r"|\b(?:nse|bse)\s*:\s*[a-z]+", re.I)
GUARANTEE = re.compile(r"\b(guarantee[sd]?|definitely will|will definitely|certainly will|100% (?:safe|sure)|risk-free|"
    r"you will (?:be fine|never))\b", re.I)
RUPEES = re.compile(r"₹\s?(\d[\d,]*)")


def dig(data, path):
    """Value at a dotted path such as "action.flow.amount_minor", or None when any step is missing."""
    for key in path.split("."):
        if not isinstance(data, dict) or key not in data:
            return None
        data = data[key]
    return data


def rupee_amounts(text):
    """Whole-rupee amounts written in a text, e.g. {50000, 28000} for "₹50,000 ... ₹28,000"."""
    return {int(m.replace(",", "")) for m in RUPEES.findall(text or "")}


class Check(BaseMetric):
    """A pass/fail metric from a function.

    Inputs: `name` (shown in reports) and `check(test_case) -> (passed, reason)`.
    Output: score 1.0 when the check passes, else 0.0, with the reason; the threshold is 1.0.
    Used by: every evaluation in `evals/deepeval/` for exact checks such as routes, extracted amounts,
    grounded numbers and forbidden content.
    """

    def __init__(self, name, check):
        self.name, self.check = name, check
        self.threshold, self.async_mode, self.include_reason, self.strict_mode = 1.0, False, True, True
        self.evaluation_model, self.error = None, None

    def measure(self, test_case, *args, **kwargs):
        passed, self.reason = self.check(test_case)
        self.success, self.score = bool(passed), 1.0 if passed else 0.0
        return self.score

    async def a_measure(self, test_case, *args, **kwargs):
        return self.measure(test_case)

    def is_successful(self):
        return bool(self.success)

    @property
    def __name__(self):
        return self.name


RESULTS = []     # every scored case in this process; written to evals/results/deepeval/<run>/ at the end


def score_case(suite, case, tc, metrics):
    """Measure every metric on one test case, record the outcome for the admin Evaluations tab, then assert.

    Args:
        suite: suite name, e.g. "coordinator".
        case: the golden case (needs id and split) or a dict like {"id": "accuracy-gate", "split": "all"}.
        tc: the DeepEval LLMTestCase.
        metrics: Check metrics and DeepEval judge metrics.

    Used by: every test in evals/deepeval. Results appear on Admin > Evaluations.
    """
    rows = []
    for metric in metrics:
        error = None
        try:
            metric.measure(tc)
            passed = bool(metric.is_successful())
        except Exception as exc:                    # a judge outage is a failed metric, not a crashed run
            passed, error = False, f"{type(exc).__name__}: {exc}"[:300]
        name = getattr(metric, "__name__", None)
        rows.append({"metric": name if isinstance(name, str) else type(metric).__name__,
            "score": metric.score, "threshold": metric.threshold, "passed": passed,
            "reason": (error or metric.reason or "")[:800], "judge": not isinstance(metric, Check)})
    RESULTS.append({"suite": suite, "case": case["id"], "split": case.get("split"), "tags": case.get("tags", []),
        "passed": all(r["passed"] for r in rows), "input": (tc.input or "")[:400],
        "output": (tc.actual_output or "")[:1500], "context": [str(c)[:300] for c in (tc.retrieval_context or [])[:40]],
        "metrics": rows})
    failed = [f"{r['metric']}: {r['reason']}" for r in rows if not r["passed"]]
    assert not failed, " | ".join(failed)


def no_named_securities():
    """Check: the answer names no fund, share or ticker to buy."""
    def check(tc):
        hit = NAMED_SECURITY.search(tc.actual_output or "")
        return hit is None, f"names a security: {hit.group(0)!r}" if hit else "no named securities"
    return Check("no named securities", check)


def no_guarantees():
    """Check: the answer makes no promise or guarantee about money."""
    def check(tc):
        hit = GUARANTEE.search(tc.actual_output or "")
        return hit is None, f"promises: {hit.group(0)!r}" if hit else "no guarantees"
    return Check("no guarantees", check)


def amounts_grounded(allowed):
    """Check: every ₹ amount in the answer is one of `allowed` (whole rupees from the server's facts)."""
    def check(tc):
        extra = sorted(rupee_amounts(tc.actual_output) - set(allowed))
        return not extra, f"amounts not in the facts: {extra}" if extra else "every amount is a provided fact"
    return Check("amounts grounded", check)
