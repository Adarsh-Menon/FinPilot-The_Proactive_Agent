# Iteration 5: visible agent workflow, category fixing and clearer scenarios

Date: 2026-10-26. Author: Claude Code. Request: after trying the app, the user asked to
(1) see the complete agent workflow, not only three calculation steps; (2) fix
uncategorized transactions by adding information or letting the model understand
common merchants (a bookstore is books, a pharmacy is health, cleaning is a home
service); (3) understand why a ₹2 purchase showed a negative result and why no
spending pattern appeared.

## Previous state and change

- **Agent activity** listed analyzer, forecaster and numeric checks with only
  "deterministic · completed". Categorization, which runs at ingestion, was invisible.
  It now shows every stage in order (ingestion, categorizer, analyzer, forecaster,
  numeric checks) with what each read and produced, step timing, the categorizer's
  model and prompt version, and outcome counts. Designed-but-inactive agents
  (coordinator, investigator, advisor, risk reviewer, goal tracker) are listed
  separately as not active. Forecast steps now carry plain-language `details` and
  `duration_ms`; numeric checks also report `first_reserve_breach`.
- **Uncategorized rows** had no remedy. In the user's ledger all 64 were added while
  AI was off (`not_configured` or `paused`), so no model had seen them. Owners can now
  click any category to set their own label or send a note to the categorizer, and
  retry the categorizer on all unresolved rows. Earlier labels are kept in
  `category_history` (last ten). Affected requirement: FP-04 (categorization with HITL),
  partially.
- **AI attempt limit.** At the user's request, each transaction gets at most 3 model
  generations in total, counted in `category_ai_attempts`: the ingestion attempt plus
  any retry or note. Attempts made while AI was off (`paused`, `not_configured`) do not
  count. After 3, note requests return 409 and bulk retry skips the row (reported as
  `exhausted`); owner labels always remain available. Rows from before the counter
  count as one attempt if a model already answered for them.
- **Customer versus operations wording.** Whether AI was paused or unconfigured is an
  operations detail. Customers now see neutral text ("Not categorized yet"), AI actions
  are hidden while the public health flag reports AI unavailable, and the API error
  says only that AI categorization is unavailable. The operations dashboard gains a
  "Categorization outcomes" table of aggregate counts by source and status since the
  API started, including rows that arrived while AI was off, with no tenant or text data.
- **Categorizer prompt.** v3 forbade inferring a merchant's business, so well-known
  merchants could be left unresolved. v4 allowed ordinary meaning, well-known merchants
  and owner notes; a live check showed it labeled a pharmacy `pharmacy`, so v5 adds
  "name the spending purpose, not the shop type". v4 and v5 are recorded here; later releases are in [iteration 6](iteration_6.md).
- **Scenario result** headlined "Change in ending cash −₹2", which read as a failure. It
  now shows ending cash with the purchase, the baseline without it, the difference,
  and the lowest balance against the reserve. The empty pattern panel now names the
  reason (unconfirmed coverage, or under 60 days of history) and links to the fix.

## Decisions and assumptions

Accepted from the user: categories must be fixable by the owner and by the model with
owner-supplied context; common merchant meaning is acceptable evidence. Assumptions
awaiting confirmation: owner labels always override the model; notes are stored on the
transaction and reused on retry; retry is capped at 200 rows per request; every
category change advances the ledger version and makes earlier forecasts stale.
Guided clarification questions from the model remain out of scope.

## Acceptance and evidence

- New tests cover the three-attempt limit (not counting AI-off attempts, blocking the
  fourth note, skipping exhausted rows in bulk retry, keeping the count on owner
  relabel), owner relabeling with history, validation of exactly one of
  category or note, version conflicts, 404 for unknown rows, 409 when AI is off,
  retry of paused rows, stale forecasts after relabeling, `owner_note` reaching the
  model alongside the minimized description, and per-step forecast explanations.
- Backend suite on 2026-10-26 at the end of this iteration: 57 passed, 3 PostgreSQL
  tests skipped. The test suite now ignores a developer's local `.env`. Prompt
  versioning: 6 tests passed, five releases validate. Frontend production build passed.
- Live categorizer v5 spot check with synthetic descriptions: Crossword bookstore →
  books, Apollo pharmacy → health, Urban Company → home services, Metro card top-up →
  transport, DMart supermarket → groceries, a bare UPI reference → abstained, and the
  same reference with the note "Paid the maid for home cleaning" → home services. This
  is not a quality evaluation; golden categorization evaluations remain NOT RUN.
- A headless browser drove the workflow view, scenario wording, category dialog and
  owner relabeling with a synthetic account.

## Data, security and evaluation impact

No schema migration: new fields (`category_note`, `category_history`) live in the
existing transaction JSON, and older rows remain readable. Notes are owner data,
minimized like descriptions before reaching the model, and treated as untrusted
evidence by the prompt. Step details are stored in the owner's run result and are not
part of telemetry; LangSmith export is unchanged. Prompt releases v4 and v5 are draft
with evaluation NOT RUN.
