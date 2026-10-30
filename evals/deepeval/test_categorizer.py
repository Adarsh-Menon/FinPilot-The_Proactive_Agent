"""Categorizer: are imported transactions labelled with an acceptable category, and never a forbidden one?"""
import csv
import io

import pytest
from deepeval.test_case import LLMTestCase

from checks import Check, score_case
from conftest import goldens

CASES = goldens("categorizer")
GATE_ACCURACY = 0.85          # proposed gate from evals/README.md, applied to the cases that were labelled


@pytest.fixture(scope="module")
def labelled(new_owner):
    """Import every case as one statement for a fresh owner; returns the preview rows by description."""
    client = new_owner(history=False)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["date", "description", "amount_minor", "currency", "kind"])
    for i, case in enumerate(CASES):
        writer.writerow([f"2026-10-{1 + i % 17:02d}", case["description"], case["amount_minor"], "INR", case["kind"]])
    response = client.post("/api/v1/imports", json={"csv_text": out.getvalue()})
    assert response.status_code == 201, response.text
    return {row["description"]: row for row in response.json()["rows"]}


def same_label(label, wanted):
    """Free-text categories match when one's words contain the other's ("mutual fund sip" ~ "mutual fund", "dining" ~ "dining out")."""
    a, b = set(label.lower().replace("&", " ").split()), set(wanted.lower().replace("&", " ").split())
    return bool(a) and bool(b) and (a <= b or b <= a)


def verdict(case, row):
    """(passed, reason) for one row against its golden case."""
    label = (row.get("category") or "uncategorized").lower()
    if any(same_label(label, f) for f in case.get("forbid", [])):
        return False, f"forbidden label {label!r}"
    if label == "uncategorized":
        return bool(case.get("abstain_ok")), "abstained" + ("" if case.get("abstain_ok") else " where a label was expected")
    return any(same_label(label, a) for a in case["accept"]), f"labelled {label!r}; acceptable {case['accept']}"


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_categorizer_case(case, labelled):
    row = labelled[case["description"]]
    tc = LLMTestCase(input=case["description"], actual_output=row.get("category") or "uncategorized", name=case["id"],
        metadata={"source": row.get("category_source"), "status": row.get("category_status")})
    score_case("categorizer", case, tc, [Check("acceptable label", lambda _: verdict(case, row))])


def test_categorizer_accuracy_gate(labelled):
    results = [verdict(c, labelled[c["description"]])[0] for c in CASES]
    accuracy = sum(results) / len(results)
    tc = LLMTestCase(input=f"{len(results)} categorizer cases", actual_output=f"accuracy {accuracy:.2%}", name="accuracy-gate")
    score_case("categorizer", {"id": "accuracy-gate", "split": "all", "tags": ["gate"]}, tc, [Check("accuracy gate",
        lambda _: (accuracy >= GATE_ACCURACY, f"accuracy {accuracy:.2%}; gate {GATE_ACCURACY:.0%}"))])
