# Iteration 1: separate versioned agent prompts

Date: 2026-10-18. Author: Codex, following the user's request to check the docs and iteration 0 and separate the agent prompts.

## Request and previous state

The user requested separate prompts for agents. [Iteration 0](iteration_0.md) requires distinct cooperating agents and prompt versioning. The living architecture described eight roles, but the repository contained no prompt templates.

Affected requirements: FP-02, FP-04 through FP-11, FP-17, FP-18, FP-19 and FP-21. Financial behavior and product scope remain based on the existing design.

## Change and rationale

Add [the prompt catalog](../prompts/README.md), a shared v1 system template, one distinct v1 template for each of coordinator, categorizer, investigator, analyzer, forecaster, advisor, risk reviewer and goal tracker, and a draft source manifest. Each role defines inputs, permitted tool families, procedure and structured payload. Shared rules cover provenance, privacy, policy, uncertainty and action boundaries.

The shared template avoids duplicating cross-cutting rules while separate role templates make responsibilities reviewable. The runtime must compose both; a role file alone is not the complete instruction set. Deterministic services retain financial calculations, authorization, publication, delivery and reward mutations.

## Accepted direction and proposed defaults

- User-requested direction: separate prompts for the agents.
- Proposed implementation defaults: `prompts/<agent>/v1.md`, a shared common prompt, JSON response envelope and manifest-selected versions. These details have not been independently approved or evaluated.
- Existing unresolved product decisions (subscription usage window, rewards, reserve policy, providers) remain unresolved; prompts must not silently finalize them.

## Acceptance criteria and impact

- All eight roles have distinct prompt files and matching manifest entries; local references resolve.
- Prompt instructions follow the existing agent/tool boundaries and preserve deterministic numeric and publication gates.
- Every template is visibly a draft, and evaluation status remains NOT RUN until measured results exist.
- Runtime integration must implement strict response/tool schemas, immutable release checksums, tenant enforcement and owner-bound clarification handling before use.
- Before rollout, run the existing golden/evidence/security evaluations for the selected prompt/model configuration and record an evaluation artifact. Draft templates alone cannot establish passing behavior.

No data migration is required: there is no application schema/runtime yet. Prompt version starts at v1; published versions must remain immutable. Admin exposure is limited to static templates/version metadata. [Iteration 0](iteration_0.md) and its original source are preserved.

Implementation status: prompt source and documentation added; runtime loader, executable schemas, Mesh calls and evaluations are not implemented. No model accuracy or safety results are claimed.
