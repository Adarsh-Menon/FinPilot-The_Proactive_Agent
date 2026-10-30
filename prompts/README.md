# FinPilot agent prompts

Status: draft v1 prompt templates derived from [iteration 0](../specs/iteration_0.md) and the [agent contracts](../docs/architecture.md). The runtime loader verifies these templates and the operations UI shows their versions. Language-agent execution and model-quality evaluation are pending. These are prompts for the FinPilot application agents.

Each agent has its own responsibility, inputs, output contract and tool boundary. The common prompt supplies shared rules; it does not replace the agent-specific prompt.

| Agent | Prompt | Job |
| --- | --- | --- |
| Coordinator | [coordinator/v1.md](coordinator/v1.md) | Interpret intent and propose the next permitted graph step. |
| Categorizer | [categorizer/v1.md](categorizer/v1.md) | Classify transactions or request clarification. |
| Investigator | [investigator/v1.md](investigator/v1.md) | Collect missing context and propose confirmed profile updates. |
| Analyzer | [analyzer/v1.md](analyzer/v1.md) | Interpret computed patterns and anomalies. |
| Forecaster | [forecaster/v1.md](forecaster/v1.md) | Request and explain deterministic forecasts and scenarios. |
| Advisor | [advisor/v1.md](advisor/v1.md) | Propose specific evidence-backed actions. |
| Risk reviewer | [risk/v1.md](risk/v1.md) | Review actions against facts and deterministic checks. |
| Goal tracker | [goals/v1.md](goals/v1.md) | Propose feasible goals and report verified progress. |

## Assembly and runtime contract

1. Resolve [manifest.json](manifest.json) to its checksum-pinned release under `releases/`, then load the exact common and agent template paths from that release. Verify checksums; do not discover a version by sorting filenames.
2. Assemble trusted instructions in this order: [common/v1.md](common/v1.md), then the selected agent prompt. Agent-specific instructions cannot weaken the common rules.
3. Supply a separate server-created context with `run_id`, `snapshot_ref`, source versions, `as_of`, timezone, policy mode/revision, capability names and remaining budgets. Identity and authorization remain outside model control. Include only the task data the selected agent needs.
4. Supply sanitized user text, financial evidence and tool results as labeled untrusted data messages, never as substituted system instructions. The model cannot set policy or grant capabilities.
5. Bind only the role's permitted typed tools through the Mesh adapter. Tool families named in these prompts are design contracts, not existing callable implementations. If a required tool/result is unavailable, report that limitation; do not fabricate a call or result.
6. Validate the response against the common envelope and the selected role's payload. Resolve all references against the authorized immutable snapshot; recompute numeric claims. Reject invalid output rather than treating prose as a successful result.

Implement strict JSON Schemas and tool schemas before integration. Dates use ISO 8601; money uses `{ "amount_minor": integer, "currency": "ISO-4217-code" }`. A numeric claim is `{ "metric": string, "value": number, "unit": string, "currency": string|null, "calculation_ref": string }`, copied from a validated deterministic result. Do not mix currencies or use floating-point major units for money. Nullable fields stay `null` when unknown; arrays may be empty.

All terminal responses use this envelope:

```json
{
  "agent": "selected manifest agent key",
  "prompt_version": "selected agent template version",
  "status": "completed | partial | needs_clarification | blocked",
  "snapshot_ref": "server-provided snapshot reference",
  "payload": {},
  "evidence_refs": [],
  "questions": [],
  "limitations": []
}
```

The strings containing alternatives describe enums, not literal response values. Each question contains `key`, `field`, `question`, `related_refs`, and `required` (boolean). The service allocates persistent clarification IDs and binds them to the owner and pending interrupt. `evidence_refs` contains existing references, never invented identifiers. Each limitation is a concise string. `payload` contains the fields listed in the role prompt; a blocked result may use an empty object. `completed` means that agent's task finished, not that advice is published or a mutation is authorized. Tool calls happen through the bound interface before the terminal JSON response.

## Versions and release

[Source versioning](../docs/prompt-versioning.md) is implemented: immutable release snapshots pin every template with SHA-256, and the CLI validates history, records new versions and selects a previous source release. [manifest.json](manifest.json) selects a source snapshot; it is not deployment approval. All source snapshots remain draft / not_run.

Create a new template version for every change to recorded content. The runtime must derive prompt versions from the verified manifest and record model/configuration/evaluation provenance. Runtime checksum loading and static admin inventory are implemented. Runtime rollout controls and model evaluations remain pending. See the [versioning workflow](../docs/prompt-versioning.md) for commands and [evaluation protocol](../evals/README.md) for quality gates.

Parsing, arithmetic, statement generation, scheduling, delivery, publication and reward mutations remain deterministic services. They do not need extra LLM prompts. The coordinator also uses deterministic routing first and invokes its prompt only for language interpretation that needs it.
