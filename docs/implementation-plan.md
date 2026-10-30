# Implementation and submission plan

Status: implementation has started; see [build status](build-status.md). The first increment spans parts of milestones 1-3 and the operations view; no milestone is claimed fully complete. Sequence work by acceptance gates rather than guessed calendar estimates.

## Milestone 0: specification baseline

Deliver the original brief, living product requirements, architecture, data/API contracts, safety decisions, evaluation protocol and this roadmap. Complete when original text integrity and local documentation links are verified. Subsequent product changes go into a numbered spec iteration. [Iteration 1](../specs/iteration_1.md) adds draft separate agent prompts and a source manifest; prompt loading, schemas and evaluations remain implementation work. [Iteration 2](../specs/iteration_2.md) implements source checksums, release history, validation and source selection/rollback.

## Milestone 1: secure foundation

Create FastAPI, React JavaScript, PostgreSQL migrations, Redis and local service configuration. Pin tested dependencies, including the web framework, OpenAI client, LangGraph and checkpoint backend. Add auth, tenant ownership, RLS, private storage, typed configuration, Mesh adapter, prompt manifests and operational event schemas. Add fail-closed policy reads from the beginning.

Gate: tenant A/B/admin denial tests pass across service and database boundaries; secrets stay server-side; mock transport rejects inference outside Mesh; one authorized real Mesh smoke call succeeds when credentials are configured. Mock inference must remain explicitly labeled in tests and never count as a sponsor integration demonstration.

## Milestone 2: ingest and clarify

Implement manual entry and bounded CSV preview/mapping/confirmation, idempotency and correction history. Add categorization with deterministic merchant rules plus Mesh reasoning for uncertain descriptions, and a resumable clarification queue. Add investigator profile/goal questions with confirmed-versus-proposed facts.

Gate: import twice without duplication; preserve legitimate identical purchases; distinguish transfers/refunds/income; show useful row errors; resume a pending owner clarification after restart; deny another tenant's resume. Run the category golden set and record measured results.

## Milestone 3: forecasting vertical slice

Implement deterministic aggregations, recurrence candidates, comparable-period anomalies, daily forecasts, scenario overlays, adviser and risk review. Persist facts and decision trails; stream safe agent progress to the owner. Add cash-flow statement and partial net-worth report. Add a conversation route that extracts scenario parameters and asks for missing inputs.

Gate: a realistic synthetic import yields a data-derived anomaly, a dated cash trajectory and a trip simulation with independently checked arithmetic. Advice cites actual sources and fails publication when unsupported. Run forecast rolling backtests against baselines and publish results with limitations.

## Milestone 4: proactive loop and operations

Implement accepted goals and period progress; dedicated APScheduler process, persistent jobs/outbox, one verified consented notification channel, quiet hours and deduplication. Add exact versioned caching, admin metrics/charts, optional sanitized LangSmith export, prompt manifests, evaluation display and manual/automatic kill-switch controls.

Gate: time-controlled integration tests prove a due job runs without a manual button; restart/retry does not multiply intents; opt-out and kill-switch prevent pending sends; changed transactions invalidate advice/cache; a kill switch blocks cached as well as new recommendations. Verify telemetry contains no seeded private canaries. Demonstrate a real channel in an authorized test account, separately from mocked CI delivery.

## Milestone 5: submission hardening

Complete golden data, frozen holdout and reproducible evaluation scripts; run all security and numerical gates. Add actual installation/run/evaluation instructions, exact dependency versions, sample inputs, screenshots or optional demo, architecture summary, measured results and known limitations. Scan for secrets and proprietary/customer data.

Gate: a clean checkout can be installed and demoed from documented commands; every hackathon requirement maps to executable evidence; no unimplemented feature is described as complete.

## Milestone 6: extended product

Add Excel and text-PDF parsing in restricted workers, then Mesh-compatible receipt extraction with user review. Extend goals with a reward ledger, simulated redemption and referral tracking. Add remaining notification channels with verified consent/provider behavior. Introduce document retrieval or external research only after a concrete evaluation shows its value. Real cashback, live investment research and financial integrations need the unresolved decisions in [design decisions](design-decisions.md).

Gate: each new input/tool/channel repeats privacy, parsing, Mesh-routing and isolation tests; reward retries cannot create extra coins; corrected/deleted transactions cannot leave unsupported awards silently valid. Reverse invalid earned coins without rewriting history; define treatment of already redeemed balances before enabling payouts.

## Planned repository layout

This tree is the target layout; some directories now exist. See build status for the implemented subset.

```text
backend/
  app/{api,auth,agents,domain,services,storage,tools,observability}/
  migrations/
frontend/src/{customer,admin,components}/
prompts/{common,coordinator,categorizer,investigator,analyzer,forecaster,advisor,risk,goals}/
workers/{parser,scheduler,delivery}/
tests/{unit,integration,security}/
evals/{datasets,manifests,results}/
specs/iteration_N.md
docs/
requirements.txt
.env.example
.github/workflows/agent-arena-2026-checks.yml
```

## Submission checklist

- [ ] Public GitHub repository with all application source and an appropriate license.
- [ ] Dependency manifest listing FastAPI and the OpenAI client used through Mesh, with tested versions.
- [ ] All application inference, including optional AI utilities/judges, routes through `https://api.meshapi.ai/v1`.
- [ ] Local `.env` is ignored; GitHub repository secrets `MESH_API_KEY` and `SUBMISSION_TOKEN` configured without exposing values.
- [ ] Official organizer workflow retrieved from [provided workflow URL](https://careerapi-production.krishnaik.in/api/ci/hackathons/agent-arena-2026/workflow.yml), reviewed, and placed at `.github/workflows/agent-arena-2026-checks.yml`.
- [ ] Workflow reviewed for secret handling, permissions, remote script execution and destinations before secrets are enabled.
- [ ] Python compile, dependency, Mesh usage and key-validity checks pass on the submitted commit.
- [ ] README explains actual implementation, cooperation, setup, design decisions and measured evaluation results.
- [ ] Synthetic data and repeatable evaluation commands are included; no customer data or credentials committed.
- [ ] Optional demo video/deployment is clearly labeled and matches the submitted code.

The workflow URL was attempted during planning but the browser retrieval returned an error. Its contents have not been verified or installed; do not invent a substitute workflow or claim the organizer checks pass. Credential setup, publication and live message delivery are later implementation tasks.
