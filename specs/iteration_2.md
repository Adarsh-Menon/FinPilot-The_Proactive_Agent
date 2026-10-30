# Iteration 2: executable prompt source versioning

Date: 2026-10-18. Author: Codex. Request: the user explicitly asked for prompt versioning after the separate agent prompts were added.

## Previous behavior and change

[Iteration 1](iteration_1.md) added versioned filenames and a draft catalog, but had no checksum validation, immutable release history or selection/rollback command. This revision implements those source-management operations in [scripts/prompt-versions.mjs](../scripts/prompt-versions.mjs) with the workflow in [docs/prompt-versioning.md](../docs/prompt-versioning.md).

Affected requirements: FP-02, FP-16, FP-17, FP-18, FP-19 and FP-21. Prompt text and financial behavior are unchanged. All initial templates remain v1; the initial release records their existing bytes.

## Accepted direction and implementation choices

The user requested prompt versioning. Independent role/common versions, SHA-256-pinned source snapshots, a source-selection pointer, hash-linked history and a dependency-free Node.js CLI are implementation choices. Source selection is separate from future evaluated deployment approval. Once recorded, even draft template versions are preserved; changes require a new version.

The manifest shape changes from the initial inline catalog to a hash-pinned release pointer. There is no runtime consumer or database migration yet. Future loaders must resolve the pointer, verify hashes and pin the composed common/role prompts. Cache and run provenance must include the release hash; a common-rule change requires evaluation across all agents.

## Verification and limits

Acceptance checks passed: the initial source release validates all nine templates; six automated tests cover version creation and rollback, historical prompt edits, historical release edits, shared-template changes, invalid inputs and pointer tampering. Local documentation links are checked separately. Prior iteration files and original prompt templates are preserved.

The CLI provides source integrity checks, not cryptographic authorship or protection against wholesale history replacement. Git history/review remains required. Runtime loading, admin UI/promotion, strict response schemas, Mesh integration and model quality evaluations remain unimplemented. All source releases remain draft / NOT RUN; no financial-quality results are claimed.