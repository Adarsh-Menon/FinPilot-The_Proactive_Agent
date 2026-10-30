# Iteration 3: first runnable development increment

Date: 2026-10-18. Author: Codex. Request: the user asked to start making the application after the specifications and prompt versioning were prepared.

## Previous state and change

The repository previously contained planning documents, prompt templates and a source-versioning CLI. This increment adds the FastAPI backend, React frontend, local storage, authentication, CSV/manual transactions, deterministic LangGraph forecast/scenario flow, operations view, runtime prompt verification, Mesh evaluation adapter, PostgreSQL RLS setup, local infrastructure and tests.

Affected requirements: foundational parts of FP-01 through FP-04, FP-06 through FP-08, FP-14 through FP-19 and FP-21. This does not claim completion of those requirements or of the overall product. [Build status](../docs/build-status.md) maps implemented parts and remaining work; [local development](../docs/local-development.md) gives executable instructions.

## Decisions and tradeoffs

User-authorized direction: begin implementation of the documented product. Engineering defaults in this increment: local single-process operation, one consolidated account, five supported currencies, strict CSV columns, SQLite for immediate local development, and PostgreSQL RLS/restricted-role tests for the intended database. SQLite is a development convenience, not a replacement for the PostgreSQL deployment design.

The first numerical baseline uses a trailing mean and explicit dated future flows. It is deliberately provisional; robust recurrence estimation, evaluated uncertainty, suitability/affordability gates and language-agent advice are unfinished. The initial LangGraph nodes are deterministic calculations and are labeled as such in the UI. Existing v1 prompts are unchanged. The initial Mesh adapter was evaluation-only; the revision below adds local development categorization dispatch.

## Acceptance and evidence

- A fresh local account can save context, confirm a CSV/manual ledger, calculate a date-specific baseline and simulate a purchase without changing actual transactions.
- Service-layer ownership, sessions/CSRF, idempotency, stale snapshots, deterministic amounts, prompt integrity and Mesh origin controls have executable tests.
- Dedicated PostgreSQL tests exercise RLS and operations restrictions; startup rejects privileged application connections.
- The React production build succeeds; browser visual/interaction verification remains unavailable in this session.
- Documentation distinguishes working code, engineering tests and unrun financial/model-quality evaluations.

## Data, security and evaluation impact

Initial schema is created by SQLAlchemy; PostgreSQL RLS/grants are applied by an owner-only migration command. No previous application data migration exists. The runtime resolves and verifies the source prompt manifest, but immutable evaluated deployment manifests remain future work. Sessions and private records stay server-side; operations receives allowlisted metadata. Production startup is unsupported pending remaining security/retention/processor/quality work.

The current engineering results are in [build status](../docs/build-status.md). The golden forecast/categorization/advice protocol remains NOT RUN. No credentials were required for mocked AI checks, and no financial messages, trades or payments are executed.


## Revision: LLM categorization and two-model routing

User direction: category decisions must come from an LLM rather than hardcoded
merchant rules; use one shared model for ordinary agents and a stronger model for
risk review. This revision stays within iteration 3.

- Removed merchant and kind-to-category rules from `finance.py`. Owner-entered
  categories remain authoritative; unresolved rows use the Mesh categorizer.
- Added immutable `categorizer/v2.md` and selected source release v2. Historical
  v1 templates remain byte-for-byte unchanged. The new prompt uses transaction
  meaning, the allowed taxonomy and explicit abstention, with no mapping rules.
- Connected manual entries and CSV previews to bounded, validated Mesh batches.
  Invalid or unavailable output remains uncategorized with a visible status;
  it does not fall back to keyword rules. Existing transactions are not migrated.
- Added `MESH_RISK_MODEL`; all other roles use `MESH_MODEL`. Risk requires its own
  configured model. Sonnet 5 / Opus 5.5 are initial candidates, subject to Mesh
  catalog verification and FinPilot evaluations; see [configuration](../docs/local-development.md#mesh-categorization-and-model-routing).
- Categories retain model/prompt provenance. Input fields, numeric calculations,
  forecast semantics and owner data boundaries remain enforced by the server.
- Operations now reports whether categorization can dispatch. `AI_PAUSED` blocks
  calls; `ADVICE_PAUSED` permits categorization. Late paused results are discarded.

Verification covers mocked Mesh success/failure, missing/duplicate/foreign references,
owner labels, batch boundaries, pause handling, role routing and API idempotency.
Live inference and category/risk quality evaluations remain NOT RUN. The risk/advice
workflow, complete PII detection and atomic policy dispatch barriers remain unfinished.


## Revision: LLM-created category names

Follow-up user direction: remove the fixed category vocabulary as well as merchant
rules. `Category` is now bounded, normalized text rather than an enum. The LLM can
create any meaningful label; only text validation applies. Manual input uses an
optional text field, and CSV owner labels accept the same open category names.

Added immutable categorizer v3 and selected prompt release v3. It receives optional
owner-scoped category hints and prior batch names to encourage reuse, with no allowed
category list. New labels persist in the existing JSON storage and flow through
category analysis without special mappings. Historical labels are not migrated.
The `uncategorized` unresolved marker and explicit owner overrides remain supported.

Tests cover novel model-generated categories, custom manual/CSV labels, normalization,
invalid text, per-owner hint isolation, cross-batch reuse hints and aggregation of new
labels. Financial/model-quality evaluations remain NOT RUN.
