"""End to end: multi-step scenarios through the public API, as a fresh synthetic owner each."""
import pytest
from deepeval.test_case import LLMTestCase

from checks import Check, dig, no_named_securities, score_case
from conftest import goldens

CASES = goldens("system")


def confirm(client, action):
    """Apply a confirm card the way the app's button does."""
    if action["type"] == "confirm_goal":
        g = action["goal"]
        return client.post("/api/v1/goals", json={"name": g["name"], "target_minor": g["target_minor"],
            "target_date": g["target_date"] or "2027-03-31"}).status_code == 201
    if action["type"] == "confirm_flow":
        f = action["flow"]
        data = client.get("/api/v1/profile").json()
        flow = {"label": f["label"], "amount_minor": f["amount_minor"] or 100000, "date": f["date"] or "2026-11-05"}
        profile = {**data["profile"], "scheduled_flows": [*data["profile"]["scheduled_flows"], flow]}
        return client.put("/api/v1/profile", json={"profile": profile, "expected_version": data["version"]}).status_code == 200
    return False


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_system_scenario(case, new_owner, chat):
    client = new_owner(goals=case.get("setup_goals", ()))
    run_id = client.post("/api/v1/forecasts", json={"horizon_days": 30}).json()["id"] if case.get("forecast") else None
    transcript, metrics = [], []
    for n, step in enumerate(case["steps"], 1):
        reply = chat(step["say"], page=step.get("page"), baseline_id=run_id, client=client)
        transcript.append(f"Owner: {step['say']}\nFina: {reply['answer']}")
        metrics.append(Check(f"step {n} route", lambda _, r=reply, s=step: (r["route"] in s["routes"],
            f"routed to {r['route']}; expected one of {s['routes']}")))
        if want := step.get("action"):
            metrics.append(Check(f"step {n} action", lambda _, r=reply, w=want: (dig(r, "action.type") == w,
                f"action {dig(r, 'action.type')}; expected {w}")))
        for path, value in step.get("action_fields", {}).items():
            metrics.append(Check(f"step {n} {path}", lambda _, r=reply, p=path, v=value: (dig(r, f"action.{p}") == v,
                f"{p} = {dig(r, f'action.{p}')}; expected {v}")))
        for text in step.get("answer_contains", []):
            metrics.append(Check(f"step {n} mentions {text}", lambda _, r=reply, t=text: (t.lower() in r["answer"].lower(), "")))
        for text in step.get("forbid_text", []):
            metrics.append(Check(f"step {n} omits {text}", lambda _, r=reply, t=text: (t.lower() not in r["answer"].lower(), "")))
        if step.get("grounded"):
            metrics.append(Check(f"step {n} grounded", lambda _, r=reply: (r.get("source") in {"ai", "calculation"},
                f"source {r.get('source')}")))
        if step.get("confirm") and reply.get("action"):
            applied = confirm(client, reply["action"])
            metrics.append(Check(f"step {n} confirm applied", lambda _, a=applied: (a, "")))
    output = "\n\n".join(transcript)
    if any(s.get("forbid_named_securities") for s in case["steps"]):
        metrics.append(no_named_securities())
    if want := case.get("check", {}).get("goal_names"):
        names = [g["name"] for g in client.get("/api/v1/goals").json()["goals"]]
        metrics.append(Check("goals created", lambda _, n=names, w=want: (all(x in n for x in w), f"goals {n}")))
    if want := case.get("check", {}).get("flow_labels_include"):
        labels = [f["label"] for f in client.get("/api/v1/profile").json()["profile"]["scheduled_flows"]]
        metrics.append(Check("plan updated", lambda _, ls=labels, w=want: (all(any(x.lower() in l.lower() for l in ls) for x in w),
            f"flows {ls}")))
    score_case("system", case, LLMTestCase(input=case["title"], actual_output=output, name=case["id"]), metrics)
