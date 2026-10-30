"""Offline checks on the evaluation goldens: well-formed, unique, and deterministic cases still hold."""
import json
from datetime import date
from pathlib import Path

import pytest

from backend.app.coordinator import flow_draft, goal_draft

GOLDENS = Path(__file__).resolve().parents[2] / "evals" / "goldens"
FILES = sorted(GOLDENS.glob("*.json"))
REQUIRED = {"coordinator": {"question", "routes"}, "categorizer": {"description", "kind", "amount_minor", "accept"},
    "forecaster": {"question"}, "goals": {"goal"}, "risk": {"question", "mutation", "expect"}, "system": {"title", "steps"}}


@pytest.mark.parametrize("path", FILES, ids=[p.stem for p in FILES])
def test_golden_files_are_well_formed(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["dataset_version"] and data["agent"] in REQUIRED and data["cases"]
    ids = [c["id"] for c in data["cases"]]
    assert len(ids) == len(set(ids))
    for case in data["cases"]:
        assert case["split"] in {"dev", "holdout"} and REQUIRED[data["agent"]] <= set(case), case["id"]
    splits = {c["split"] for c in data["cases"]}
    assert splits == {"dev", "holdout"}, f"{path.stem} needs both a dev and a holdout split"


def test_page_drafts_match_their_goldens():
    """Voice-card cases are decided by code, so they must pass without any model call."""
    cases = json.loads((GOLDENS / "coordinator.json").read_text(encoding="utf-8"))["cases"]
    as_of = date(2026, 10, 18)
    for case in [c for c in cases if c.get("page")]:
        if case["page"] == "goals":
            reply = {"action": {"type": "confirm_goal", "goal": goal_draft(case["question"], as_of)}}
        else:
            kind, draft = flow_draft(case["question"], as_of)
            reply = {"action": {"type": "confirm_value", **draft} if kind == "profile_value" else {"type": "confirm_flow", "flow": draft}}
        for path, want in case.get("expect", {}).items():
            value = reply
            for key in path.split("."):
                value = value[key]
            assert value == want, f"{case['id']}: {path} = {value!r}, expected {want!r}"
