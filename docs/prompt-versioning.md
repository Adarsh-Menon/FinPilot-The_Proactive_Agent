# Prompt versioning

Source versioning is implemented by the dependency-free [Node.js CLI](../scripts/prompt-versions.mjs). Runtime checksum loading and static admin inventory are now implemented; admin rollout controls, inference evaluation and deployment approval remain pending. Use Node.js 22 or newer; checks were run with Node.js 24.14.0.

## Three separate identifiers

- **Template version:** `advisor/v1.md`, `advisor/v2.md`, etc. Each role and the common instructions advance independently.
- **Release version:** `prompts/releases/v1.json`, etc. Each immutable source snapshot pins all nine templates by path, version and SHA-256, and records its creation time, reason and parent hash.
- **Selected source release:** `prompts/manifest.json` points to one release and pins its hash. Selecting a source snapshot does not approve it for production.

Every recorded template is frozen, including draft releases. Edit a new version rather than changing an existing file. Hashes cover exact bytes; [.gitattributes](../.gitattributes) disables newline conversion for these files. Keep them in Git with code review: hashes detect inconsistency, but are not signatures and cannot prevent someone who can rewrite the entire history from replacing it.

## Inspect and validate

Run from the repository root:

```powershell
node scripts/prompt-versions.mjs list
node scripts/prompt-versions.mjs validate
node --test tests/prompt-versions.test.mjs
```

Validation checks every recorded release, historical template checksum, parent hash and selected-release pointer. A modified old prompt fails validation even when it is no longer selected. The six isolated tests cover updates/rollback, historical prompt mutation, history mutation, common-template changes, invalid inputs and pointer tampering. These are source-integrity tests, not financial or LLM evaluations.

## Make a change

For the first advisor revision:

```powershell
Copy-Item prompts/advisor/v1.md prompts/advisor/v2.md
# Edit v2.md, including its version heading, then record the finished content:
node scripts/prompt-versions.mjs snapshot --set advisor=v2 --reason "Clarify handling of cancellation fees"
node scripts/prompt-versions.mjs validate
```

The command requires an unused increasing template version and a reason. It creates the next release snapshot without overwriting an earlier one, then selects it. Unchanged roles retain their existing versions. Use the next available version on later changes. A command failure after writing a snapshot can leave it recorded but unselected; inspect `list` and use `select` to finish selection after resolving the failure.

For a shared-rule change, create and edit `prompts/common/v2.md`, then use `--set common=v2`. Multiple changes can share one snapshot with repeated `--set` options. A shared prompt change affects all agents, so every composed prompt must be reevaluated. There is no claim of evaluation reuse in this initial implementation.

## Roll back source selection

```powershell
node scripts/prompt-versions.mjs select v1
```

Selection validates the entire history first and preserves newer snapshots. If later work branches from an older selected release, the new release records `based_on_release` separately from its chronological parent. Source rollback does not bypass a kill switch or make an unevaluated version deployable.

## Runtime and admin integration contract

The future runtime must resolve the selected release and verify its hash and the common/role checksums before loading instructions. Compose common instructions followed by the role prompt, and derive version metadata from that verified release; do not trust a model's claimed version. Cache keys and evidence provenance must include the release hash, common and agent versions, model/configuration and relevant data/policy versions.

Record a run's source release/hash, common and agent version/hash, model identifier, inference settings, graph/tool/schema versions and evaluation artifact. Every new snapshot currently has `draft` / `not_run` status. Future immutable deployment manifests must separately bind these source checksums to exact model/settings/schema versions and passing evaluation artifacts; do not edit a historical source snapshot to mark it evaluated.

The admin UI should show selected/deployed versions, per-agent/common versions, change reason/history, evaluation status, and authorized rollout/rollback controls. Expose only static templates and content-free metadata. Production changes require policy authorization, passing quality/security gates and an audit entry; resumed runs retain or safely restart their original prompt configuration. The initial admin UI exposes static version metadata and the runtime verifies source hashes. Deployment promotion and live inference enforcement remain pending.