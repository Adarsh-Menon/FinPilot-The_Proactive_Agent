# Design: an evaluation framework with DeepEval

Status: implemented in [iteration 19](../specs/iteration_19.md) (`evals/deepeval/`, `evals/goldens/`, and `Admin > Evaluations`; judge rubric calibration against human labels remains pending). Date: 2026-10-27 (updated 2026-10-28). Owner: FinPilot engineering.

FinPilot has nine model-backed agents and one completed quality evaluation (the advisor, 100
synthetic cases, passed on 2026-10-26). Prompt releases v17 to v19 were never evaluated. This
document proposes a framework that measures every agent on its own and the product end to end,
using deterministic checks first and DeepEval for what only a judge can score, and that gates
prompt releases on the results.

## 1. Principles (consistent with `evals/README.md`)

1. **Deterministic checks first.** Anything that can be checked exactly (intent labels, extracted
   amounts and dates, numbers in text, JSON shape, named securities, placeholders) is checked by code.
   An LLM judge scores only what code cannot: relevance, faithfulness of wording, tone, helpfulness.
2. **The judge is never the model under test**, runs through the same model gateway, uses a versioned
   rubric, and is calibrated against ~50 human-labelled examples per metric before its scores are
   trusted (report agreement; adjust thresholds rather than bending the rubric to fit).
3. **Synthetic data only.** No real owner data in datasets, prompts or judge calls. DeepEval runs
   locally; its hosted platform is not used, so no evaluation data leaves the environment.
4. **Critical failures are counted, not averaged.** One invented balance, one named security or one
   cross-tenant leak fails the run regardless of the average score.
5. **Every result is reproducible**: dataset version and hash, prompt release, model IDs, judge model
   and rubric version, seed and git commit are written with the scores.

## 2. Harness

- **DeepEval as the runner and judge layer:** datasets of `Golden`s, one `LLMTestCase` per case
  (input, actual output, expected output, and `retrieval_context` holding the server-computed facts
  the agent was given), metrics per agent, `deepeval test run` over pytest-style files in
  `evals/deepeval/`.
- **Judge model:** a custom class (subclass of `DeepEvalBaseLLM`) that calls the gateway with a model
  different from the one under evaluation.
- **Deterministic checks as custom metrics** (subclass of `BaseMetric`), so code checks and judge
  scores land in one report.
- **Real code path:** agents are called through the `MeshGateway` methods and server-side validation,
  so evaluations measure what owners actually see, not a bare prompt.
- **Datasets:** generated synthetically with a fixed seed, then reviewed by a person; development and
  frozen holdout splits; hand-written cases for messy real-world input (voice-style text such as
  "6004 Goa", Hinglish, UPI strings). Labels are never produced by the model being evaluated.

## 3. Per-agent evaluations

| Agent | What is measured | Deterministic metrics | DeepEval (judge) metrics | Proposed gate |
|---|---|---|---|---|
| Coordinator | Routing and extraction, including voice-style and misheard input | Intent accuracy and per-intent F1; amount and date exact match; unsupported topics routed safely | none needed | Intent macro-F1 ≥ 0.92; zero amounts not present in the message |
| Categorizer | Labels on the 500-case set described in `evals/README.md` | Accuracy, macro-F1, abstention rate and accuracy of accepted labels; consistency with owner rules | none needed | Macro-F1 ≥ 0.85 on holdout (existing proposed gate) |
| Investigator | Questions about missing income and bills | Every proposal maps to a detected recurring group; no invented amounts or dates | GEval "useful and not repetitive" | Zero invented values; usefulness at or above the calibrated threshold |
| Forecaster (Fina's answers) | Explanations of the forecast | Every number in the text equals a provided fact | Faithfulness (facts as `retrieval_context`), Answer Relevancy, GEval for hedged tone and no guarantees | Zero ungrounded numbers; faithfulness ≥ 0.9 |
| Advisor | Options and their "Why" | Existing 100-case checks: amounts and dates match the cited option, no essential cuts, no verdicts, no named securities | GEval "reasoning explains the trade-off", Answer Relevancy | Existing gate (≥ 98% grounded, zero critical) plus judge scores |
| Goals coach | Goal notes | Placeholder validity; no promises or rewards | GEval for encouraging, non-judgemental tone; Faithfulness | Zero invalid placeholders |
| Behavioural pattern agent | Pattern descriptions | Only reported categories and figures appear | Faithfulness, GEval "no blame, actionable" | Zero ungrounded figures |
| Risk reviewer | Approve or block decisions | Precision and **recall on unsafe outputs** from a labelled set of good and seeded-bad answers (named securities, invented numbers, guarantees, injected instructions) | none (it is a classifier) | Unsafe recall ≥ 0.98; approval of safe answers ≥ 0.9 |
| Voice transcriber | Speech to text on recorded Indian-English and Hinglish clips | Word error rate; exact match of amounts and dates | none | Amount and date accuracy ≥ 0.95 |
| Debt, investment readiness, plans, numeric checks | Calculations | Already covered by pytest unit and property tests | not applicable | Existing CI |

## 4. End-to-end evaluation

- **Conversation scenarios** through `/api/v1/assistant/message` with a synthetic account: multi-turn
  flows such as "add a goal by voice, then ask how it is going, then report an emergency". Scored with
  task-completion checks in code (the right confirm card with the right fields; the right goal dates
  after a re-plan) and DeepEval conversational metrics for coherence across turns.
- **Safety suite:** prompt injection in transaction descriptions, notes and voice transcripts; requests
  for another person's data; investment tips, crypto and tax questions; attempts to make Fina promise
  returns. Pass means a safe refusal or route, with zero leaks.
- **Product signal alongside:** the thumbs-up rate per agent from the admin Feedback tab, tracked against
  release versions, so offline scores and real satisfaction can be compared.

## 5. When evaluations run

| Trigger | What runs | Cost control |
|---|---|---|
| Every pull request | Deterministic checks on a small fixed sample with recorded model outputs (no live calls) | Free; catches validation and parsing regressions |
| New prompt release | Full per-agent suite with live models and the judge on the development split | Gate: the release file's `evaluation_status` is set by the run; a release cannot be selected while it is `not_run` or `failed` |
| Nightly | Holdout split, end-to-end and safety suites | Budgeted with the daily AI cost cap from iteration 18 |

Results go to `evals/results/` in the existing schema, and a summary (scores, gates, cost, date) is shown
on an "Evaluations" card in the admin portal.

## 6. Plan

| Phase | Scope | Exit criterion |
|---|---|---|
| 0. Harness | DeepEval set up; judge class and calibration; deterministic metrics as custom metrics | A dry run reports all metrics on a handful of cases |
| 1. Core agents | Datasets and suites for coordinator, categorizer, forecaster, advisor and risk reviewer | Baseline scores recorded for release v19 |
| 2. Remaining agents | Investigator, goals coach, behavioural pattern agent, voice transcriber | All agents have a baseline |
| 3. End to end and gating | Conversation scenarios, safety suite, release gating, admin Evaluations card | v20 is the first release selected through the gate |

## 7. Risks and open questions

- **Judge reliability.** LLM judges drift with model updates; calibration is repeated when the judge
  model changes, and deterministic checks remain the gate for anything numeric.
- **Evaluation cost.** Live suites call models twice (agent and judge); the nightly run is capped by the
  AI budget, and pull requests use recorded outputs.
- **Dataset realism.** Synthetic data can share the implementation's assumptions; hand-written messy
  cases and human review of generated cases reduce, but do not remove, that risk.
- **Voice clips.** The transcriber suite needs recorded speech; clips must be recorded for the purpose
  with consent, never taken from owners.
