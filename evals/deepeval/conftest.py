"""Shared fixtures for the live agent evaluations.

The agents run through the real API (validation, risk review and all) on a throwaway SQLite database
and master key, with live models from `.env`. Every owner is synthetic. Run from the repository root:

    .venv-evals/Scripts/python -m pytest evals/deepeval -q            # dev split
    EVAL_SPLIT=holdout .venv-evals/Scripts/python -m pytest evals/deepeval -q
    EVAL_JUDGE=off ...                                                # code checks only, no judge calls
"""
import csv
import io
import itertools
import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")      # nothing leaves the machine but model calls
if sys.platform == "win32":
    # Long runs with many test clients and DeepEval's async metrics can crash the default Proactor loop on Windows.
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.config import Settings  # noqa: E402
from backend.app.main import create_app  # noqa: E402
from backend.app.storage import Policy  # noqa: E402

GOLDENS = ROOT / "evals" / "goldens"
ORIGIN = {"Origin": "http://localhost:5173"}
SPLIT = os.environ.get("EVAL_SPLIT", "dev")
# The synthetic owner every scenario starts from.
PROFILE = {"currency": "INR", "timezone": "Asia/Kolkata", "as_of": "2026-10-18", "coverage_start": "2026-08-20",
    "coverage_confirmed": True, "opening_balance_minor": 5000000, "reserve_minor": 1000000,
    "scheduled_flows": [{"date": "2026-11-01", "label": "Salary", "amount_minor": 8500000},
        {"date": "2026-11-02", "label": "Rent", "amount_minor": -2800000}]}
_names = itertools.count(1)


def goldens(agent):
    """Cases for one agent from `evals/goldens/<agent>.json`, filtered to EVAL_SPLIT (dev, holdout or all)."""
    data = json.loads((GOLDENS / f"{agent}.json").read_text(encoding="utf-8"))
    return [c for c in data["cases"] if SPLIT == "all" or c["split"] == SPLIT]


def sample_csv():
    """The synthetic v1 statement with only pre-labelled rows, so setting up an owner costs no model calls."""
    rows = list(csv.DictReader(io.StringIO((ROOT / "samples" / "transactions-sample.csv").read_text(encoding="utf-8"))))
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(r for r in rows if r["category"])
    return out.getvalue()


RUN_ID = os.environ.get("EVAL_RUN_ID") or __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M%S")
RESULTS_DIR = ROOT / "evals" / "results" / "deepeval"


def pytest_sessionfinish(session, exitstatus):
    """Write this process's scored cases to evals/results/deepeval/<run id>/<suite>.json, plus the run's details.

    Several processes (one per suite) can share a run by setting the same EVAL_RUN_ID. Read by GET /ops/v1/evals.
    """
    from checks import RESULTS
    from judge import judge_enabled
    if not RESULTS:
        return
    import subprocess
    from backend.app.prompts import PromptCatalog
    folder = RESULTS_DIR / RUN_ID
    folder.mkdir(parents=True, exist_ok=True)
    settings = Settings()
    _, model, risk_model = settings.inference()
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        commit = None
    release = PromptCatalog(ROOT / "prompts").selected()
    meta_path = folder / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    datasets = {p.stem: json.loads(p.read_text(encoding="utf-8"))["dataset_version"] for p in GOLDENS.glob("*.json")}
    meta.update({"run_id": RUN_ID, "finished_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "split": SPLIT, "judge": judge_enabled(), "judge_model": os.environ.get("EVAL_JUDGE_MODEL") or risk_model,
        "agent_model": model, "risk_model": risk_model, "prompt_release": release["release_version"], "git_commit": commit,
        "datasets": datasets, "suites": sorted(set(meta.get("suites", [])) | {r["suite"] for r in RESULTS})})
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    for suite in {r["suite"] for r in RESULTS}:
        cases = [r for r in RESULTS if r["suite"] == suite]
        (folder / f"{suite}.json").write_text(json.dumps({"suite": suite, "cases": cases}, indent=1, default=str), encoding="utf-8")


@pytest.fixture(scope="session")
def app(tmp_path_factory):
    """The real FastAPI app on a temporary database and key, AI policy RUNNING; skipped without model credentials."""
    key, model, _ = Settings().inference()
    if not key or not model:
        pytest.skip("No model credentials in .env; live evaluations need them")
    folder = tmp_path_factory.mktemp("evals")
    application = create_app(Settings(database_url=f"sqlite:///{(folder / 'eval.db').as_posix()}",
        master_key_file=str(folder / "master.key"), environment="local", langsmith_tracing=False,
        telegram_bot_token="", telegram_chat_id=""))
    with TestClient(application, headers=ORIGIN):
        with application.state.sessions() as db:
            db.get(Policy, 1).mode = "RUNNING"
            db.commit()
        yield application


@pytest.fixture(scope="session")
def new_owner(app):
    """Factory: a fresh synthetic owner with the standard profile, optionally the sample history and goals."""
    def make(history=True, goals=()):
        client = TestClient(app, headers=ORIGIN)
        name = f"eval_owner_{next(_names)}"
        r = client.post("/api/v1/auth/register", json={"username": name, "password": "synthetic-eval-password-1"})
        client.headers["X-CSRF-Token"] = r.json()["csrf_token"]
        version = client.get("/api/v1/profile").json()["version"]
        client.put("/api/v1/profile", json={"profile": PROFILE, "expected_version": version})
        if history:
            preview = client.post("/api/v1/imports", json={"csv_text": sample_csv()}).json()
            client.post(f"/api/v1/imports/{preview['id']}/confirm", json={"expected_version": preview["version"]})
        for goal in goals:
            client.post("/api/v1/goals", json=goal)
        return client
    return make


@pytest.fixture(scope="session")
def chat(new_owner):
    """Ask Fina as a synthetic owner; rotates owners before the 20-questions-an-hour limit."""
    state = {"client": None, "asked": 0}

    def ask(question, page=None, baseline_id=None, client=None):
        if client is None:
            if state["client"] is None or state["asked"] >= 15:
                # Like the real chat, the owner has a current forecast, so purchases and explanations can be answered.
                state["client"], state["asked"] = new_owner(), 0
                state["run"] = state["client"].post("/api/v1/forecasts", json={"horizon_days": 30}).json()["id"]
            state["asked"] += 1
            client, baseline_id = state["client"], baseline_id or state["run"]
        body = {"question": question, "history": [], **({"page": page} if page else {}),
            **({"baseline_id": baseline_id} if baseline_id else {})}
        response = client.post("/api/v1/assistant/message", json=body)
        assert response.status_code == 200, response.text
        return response.json()
    return ask
