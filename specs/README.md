# Specification history

`iteration_0.md` is the verbatim original hackathon brief and founder notes, copied from the existing `temp.md` on 2026-10-17. It intentionally preserves wording, examples, and ambiguities. Do not rewrite it to incorporate later decisions.

The files in `docs/` are the proposed interpretation of that brief, not additional founder-approved requirements. Proposed defaults and deferred features are marked explicitly. Implementation status must remain honest.

For each substantive revision, add `iteration_1.md`, `iteration_2.md`, and so on. Record:

1. Date, author, and request or evidence motivating the revision.
2. Requirement IDs affected and the previous behavior.
3. Proposed/new behavior, rationale, tradeoffs, and acceptance criteria.
4. Decisions accepted by the user versus assumptions still awaiting confirmation.
5. Data migration, prompt version, security, and evaluation impact.
6. Implementation status and links to the changed code and measured results.

Update the living design documents alongside an iteration. Preserve earlier iteration files. Small editorial corrections to living documentation do not need an invented product iteration.

## Revisions

- [Iteration 0](iteration_0.md): preserved original brief and founder notes.
- [Iteration 1](iteration_1.md): separate versioned agent prompts and draft response contracts.
- [Iteration 2](iteration_2.md): checksum-pinned prompt releases, validation and source rollback.
- [Iteration 3](iteration_3.md): runnable local development foundation and forecast/scenario flow.
- [Iteration 4](iteration_4.md): content-free observability with Prometheus, Grafana and sanitized LangSmith traces.
- [Iteration 5](iteration_5.md): agent workflow view, category fixing with owner notes and AI retry, clearer scenario results.
- [Iteration 6](iteration_6.md): categorizer learning from owner corrections, its measurement plan, and a fact-checked forecast chat.
- [Iteration 7](iteration_7.md): admin portal at `/admin` with its own sign-in, pseudonymous user activity, request logs, embedded Grafana and LangSmith runs.
- [Iteration 8](iteration_8.md): investigator agent that asks for missing forecast facts and proposes confirmable income and bills.
- [Iteration 9](iteration_9.md): coordinator that routes Fina chat messages to explanations, what-ifs, confirmable plan and category changes, or setup questions.
- [Iteration 10](iteration_10.md): goals checklist with calculated progress and pace, Fina's grounded goal notes, and goals from chat.
- [Iteration 11](iteration_11.md): advisor over calculated options with risk review, and its 100-case evaluation (gate passed).
- [Iteration 12](iteration_12.md): application-level envelope encryption of owner data, so database access alone cannot read it.
- [Iteration 13](iteration_13.md): goal suggestions on the Goals page, including pausing subscriptions and trimming non-essential spending.
- [Iteration 14](iteration_14.md): behavioural pattern agent and the Outlook page: rising spending, what it means for goals if it continues, and advice.
- [Iteration 15](iteration_15.md): Outlook fixes, goal-aware Fina, FinaPoints referral rewards and per-agent feedback for admins.
- [Iteration 16](iteration_16.md): goal recovery routes, debt and investment-readiness agents, emergency re-plans, reasoning on advice, and achievement tokens.
- [Iteration 17](iteration_17.md): voice with Fina, FinaPoints proven by later savings and never negative, completion pop-ups, debts with only owed and rate, the FinPilot logo and loader, and honest recovery dates.
- [Iteration 18](iteration_18.md): phone alerts over Telegram, admin health board, AI cost budget with auto-pause, abuse view with suspension, and an admin audit log.
- [Iteration 19](iteration_19.md): hybrid forecaster (`amazon/chronos-bolt-small` quantiles + 1,000 correlated simulated futures with trailing-mean fallback), rolling backtests and realized accuracy, DeepEval evaluation harness, deterministic coordinator supervisor, and opt-in contribution sharing.
