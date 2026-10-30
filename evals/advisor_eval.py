"""Advisor evaluation on synthetic personas (no real user data).

Usage:  .venv/Scripts/python evals/advisor_eval.py --cases 100 --seed 7

Each case builds a synthetic profile and ledger, runs the deterministic forecast and
options, then the live advisor and risk reviewer through the configured provider. Every
answer that would be shown is re-checked independently: each amount and date in the
text must equal a formatted fact of the option it cites, no essential category may be
offered as a cut, and no verdict or guarantee may appear. Results are written to
evals/results/advisor-<date>.json in the protocol's schema.
"""
import argparse
import hashlib
import json
import random
import re
import statistics
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.advisor import ESSENTIAL, calculated_items, for_model, levers, money, nice, render_items  # noqa: E402
from backend.app.assistant import HEDGED, VERDICT, build_facts  # noqa: E402
from backend.app.config import Settings  # noqa: E402
from backend.app.finance import graph  # noqa: E402
from backend.app.goals import progress  # noqa: E402
from backend.app.mesh import MeshGateway  # noqa: E402
from backend.app.observability import Telemetry  # noqa: E402
from backend.app.prompts import PromptCatalog  # noqa: E402

DATASET_VERSION = "advisor-synthetic-v1"
AS_OF = date(2026, 10, 18)
AMOUNT = r"-?₹\d(?:[\d,]*\d)?"  # a rupee amount, without a trailing comma from the sentence
QUESTIONS = ["What could make this work?", "What should I do?", "How can I stay above my reserve?",
    "Any ideas to make this easier?", "What would you suggest?"]


def persona(rng, case):
    """A synthetic owner: profile, ledger, optional purchase and goal."""
    opening = rng.choice([20_000, 45_000, 80_000, 150_000, 300_000]) * 100
    reserve = None if rng.random() < 0.2 else int(round(opening * rng.uniform(0.2, 0.7), -2))
    start = AS_OF - timedelta(days=89)
    rows, n = [], 0
    def add(day, desc, amount, kind, category, recurring=False):
        nonlocal n
        n += 1
        rows.append({"id": f"{case}-{n}", "date": day.isoformat(), "description": desc, "amount_minor": amount, "currency": "INR",
            "kind": kind, "category": category, "recurring": recurring})
    food = rng.randint(100, 700) * 100
    for d in range(90):
        day = start + timedelta(days=d)
        if rng.random() < 0.6:
            add(day, "Swiggy order", -int(round(food * rng.uniform(0.5, 1.5), -2)), "expense", "food delivery")
        if d % 7 == 0:
            add(day, "DMart", -rng.randint(8, 30) * 10_000, "expense", "groceries")
        if d % 9 == 0 and rng.random() < 0.7:
            add(day, "Amazon", -rng.randint(5, 40) * 10_000, "expense", "shopping")
        if d % 30 == 3:
            add(day, "Flat rent", -rng.choice([12_000, 18_000, 25_000]) * 100, "expense", "rent", True)
            add(day, "Netflix", -64_900, "expense", "entertainment")
    flows = []
    if rng.random() < 0.55:
        flows.append({"date": (AS_OF + timedelta(days=rng.randint(3, 20))).isoformat(), "label": "Salary",
            "amount_minor": rng.choice([40_000, 70_000, 120_000]) * 100})
    profile = {"currency": "INR", "timezone": "Asia/Kolkata", "as_of": AS_OF.isoformat(), "coverage_start": start.isoformat(),
        "coverage_confirmed": True, "opening_balance_minor": opening, "reserve_minor": reserve, "scheduled_flows": flows}
    scenario = None
    if rng.random() < 0.75:
        scenario = {"amount_minor": int(round(opening * rng.choice([0.1, 0.35, 0.7, 1.1]), -2)),
            "date": (AS_OF + timedelta(days=rng.randint(2, 25))).isoformat()}
    goal = None
    if rng.random() < 0.35:
        goal = {"name": rng.choice(["Trip", "Laptop", "Emergency fund"]), "target_minor": rng.choice([50_000, 150_000, 400_000]) * 100,
            "target_date": (AS_OF + timedelta(days=rng.randint(60, 300))).isoformat(), "saved_minor": rng.choice([0, 10_000]) * 100,
            "status": "active"}
    return profile, rows, scenario, goal, rng.choice(QUESTIONS)


def formatted_facts(option, currency):
    """Every string the option's facts can render to; shown text may only contain these numbers."""
    values = set()
    for v in option["facts"].values():
        if isinstance(v, int):
            values.add(money(v, currency))
        elif isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            values.add(nice(v))
    return values


def audit(items, options, currency):
    """Independent check of shown advice. Returns a list of critical problems."""
    by_ref, problems = {o["ref"]: o for o in options}, []
    for item in items:
        option = by_ref.get(item["ref"])
        if option is None:
            problems.append("unknown option")
            continue
        text = f"{item['title']} {item['text']}"
        allowed = formatted_facts(option, currency)
        for amount in re.findall(AMOUNT, text):
            if amount not in allowed:
                problems.append(f"ungrounded amount {amount}")
        for day in re.findall(r"\b\d{1,2} [A-Z][a-z]{2} \d{4}\b", text):
            if day not in allowed:
                problems.append(f"ungrounded date {day}")
        leftover = re.sub(AMOUNT + r"|\b\d{1,2} [A-Z][a-z]{2} \d{4}\b", "", text)
        if re.search(r"\d", leftover):
            problems.append("stray number")
        if "new_reserve_breach" in option["facts"] and nice(option["facts"]["new_reserve_breach"]) not in text:
            problems.append("hides remaining reserve breach")
        if option["kind"] == "spend_less" and ESSENTIAL.search(option["facts"]["category"]):
            problems.append("essential cut")
        if VERDICT.search(HEDGED.sub("", text)):
            problems.append("verdict or guarantee")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=100)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    settings = Settings()
    key, model, risk_model = settings.inference()
    gateway = MeshGateway(key, model, PromptCatalog(ROOT / "prompts"), lambda: "RUNNING", risk_model=risk_model,
        telemetry=Telemetry(), provider=settings.inference_provider)
    rng = random.Random(args.seed)
    counts = {"cases": 0, "with_options": 0, "model_grounded": 0, "risk_approved": 0, "shown_ai": 0, "shown_calculated": 0,
        "model_errors": 0}
    failures, latencies, fallback_problems = [], [], 0
    started = time.time()
    for case in range(args.cases):
        profile, rows, scenario, goal, question = persona(rng, case)
        result = graph.invoke({"profile": profile, "transactions": rows, "horizon": 30})
        run = {k: result[k] for k in ("analysis", "forecast", "checks")}
        goals = [{**goal, "progress": progress(goal, profile["as_of"], run)}] if goal else []
        options = levers(run, profile, scenario, goals)
        counts["cases"] += 1
        if not options:
            continue
        counts["with_options"] += 1
        facts = build_facts(run, profile, scenario)
        shown = for_model(options, "INR")
        situation = {"purchase_considered": scenario is not None,
            "below_reserve": bool(facts["facts"].get("first_reserve_breach_with_purchase" if scenario else "first_reserve_breach_without_purchase")),
            "below_zero": bool(facts["facts"].get("first_below_zero_date_with_purchase" if scenario else "first_below_zero_date_without_purchase"))}
        t = time.time()
        items, source = None, "calculation"
        try:
            proposed, _ = gateway.advise(shown, question, situation)
            items = render_items(proposed, options, "INR")
            if items:
                counts["model_grounded"] += 1
                text = "\n".join(f"{i['title']}: {i['text']}" for i in items)
                approved, failed_checks = gateway.review_explanation(facts, question, text, "advice", shown["options"])
                if approved:
                    counts["risk_approved"] += 1
                    source = "ai"
        except Exception as error:  # provider or schema failure: counted, never shown
            counts["model_errors"] += 1
            failures.append({"case_id": case, "stage": "model", "error": type(error).__name__})
        latencies.append(time.time() - t)
        final = items if source == "ai" else calculated_items(options, "INR")
        counts["shown_ai" if source == "ai" else "shown_calculated"] += 1
        problems = audit(final, options, "INR")
        if problems:
            failures.append({"case_id": case, "stage": f"shown_{source}", "problems": problems})
            fallback_problems += source != "ai"
        print(f"case {case:3d}: {len(options)} options, shown={source:11s} {'PROBLEMS ' + str(problems) if problems else 'ok'}", flush=True)
    gateway.close()
    shown_total = counts["shown_ai"] + counts["shown_calculated"]
    critical = sum(1 for f in failures if f["stage"].startswith("shown"))
    # Only record a commit when FinPilot itself is tracked, not a parent repository.
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", "evals/advisor_eval.py"], capture_output=True, cwd=ROOT).returncode == 0
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT).stdout.strip() if tracked else None
    manifest = json.loads((ROOT / "prompts/manifest.json").read_text())
    report = {"status": "completed", "evaluated": "advisor + risk review", "git_commit": commit,
        "dataset_version": DATASET_VERSION, "dataset_sha256": hashlib.sha256(f"{DATASET_VERSION}:{args.seed}:{args.cases}".encode()).hexdigest(),
        "split": "synthetic-generated", "seed": args.seed, "model_id": model, "risk_model_id": risk_model,
        "prompt_manifest": manifest["selected_release"], "environment": "local development", "sample_count": counts["cases"],
        "elapsed_seconds": round(time.time() - started),
        "metrics": {**counts,
            "fully_grounded_rate": round((shown_total - critical) / shown_total, 4) if shown_total else None,
            "critical_failures": critical, "model_grounding_rate": round(counts["model_grounded"] / counts["with_options"], 4) if counts["with_options"] else None,
            "risk_approval_rate_of_grounded": round(counts["risk_approved"] / counts["model_grounded"], 4) if counts["model_grounded"] else None,
            "ai_shown_rate": round(counts["shown_ai"] / shown_total, 4) if shown_total else None,
            "latency_p50_s": round(statistics.median(latencies), 1) if latencies else None,
            "latency_p95_s": round(sorted(latencies)[int(len(latencies) * 0.95) - 1], 1) if len(latencies) >= 20 else None},
        "gate": {"rule": ">= 98% fully grounded shown answers and zero critical failures across >= 100 cases",
            "passed": bool(shown_total and counts["cases"] >= 100 and critical == 0 and (shown_total - critical) / shown_total >= 0.98),
            "sample_meets_minimum": counts["cases"] >= 100},
        "failures": failures,
        "limitations": ["Synthetic personas from one generator; not real-world accuracy.",
            "Independent audit checks numbers, dates, essentials and verdict phrases; it does not judge usefulness or tone.",
            "Single run; model outputs vary between runs.", "Golden labels are not used: the check is grounding, not a gold answer."]}
    out = ROOT / "evals/results" / f"advisor-{date.today().isoformat()}-seed{args.seed}-n{args.cases}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["metrics"], indent=2))
    print("gate passed:", report["gate"]["passed"], "| written:", out.relative_to(ROOT))


if __name__ == "__main__":
    main()
