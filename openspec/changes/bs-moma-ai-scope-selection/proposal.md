## Why

Task-level MoMA review currently uses evidence-anchor clipping when an eligible finding has reliable page anchors; without anchors it keeps the existing broader context. A trial using rule keywords reduced model input, but the selected pages and validated suggestions differed from baseline, so keyword selection cannot be enabled as a general optimization without explicit evidence-category coverage, conservative fallback, auditability, and checkpoint isolation.

## What Changes

- Add an opt-in, default-off AI context selector for explicitly mapped task-level transfer-scope and illegal-subcontract findings.
- Select pages by required rule-evidence categories using references to the authoritative rule data; keep the candidate set within the already authorized and readable context.
- Fall back to the existing context when selectors are missing, unreliable, empty, or cannot cover required categories; preserve existing partial-scope guards and disclose any remaining scope limits.
- Record non-content scope metadata and isolate AI checkpoints by selector strategy and actual selected input.
- Preserve the complete fixed-rule material scope, deterministic results, historical snapshots, and human decisions.
- Exclude negative-list selection until it has a separately reviewed selector mapping.

## Capabilities

### New Capabilities

### Modified Capabilities
- `background-ai-review`: add the opt-in AI input-scope selection, fallback, disclosure, audit, and checkpoint requirements.

## Impact

- Likely backend areas: `ai_review_context.py`, a private scope-selection module, rule selector metadata, `ai_review_batch.py`, `ai_evidence.py`, and AI advisory snapshot/checkpoint plumbing.
- Likely frontend/report areas: advisory scope limitation display and report projection; only if the current contract does not already expose the limitation.
- Tests: pure selector cases, fixed-rule invariance, prompt/validator behavior, checkpoint isolation, report projection, and isolated replay.
- No database migration is assumed. Reuse current event/snapshot contracts where sufficient; any required schema change needs a separate reviewed decision.
- No provider, billing, OCR usage aggregation, task binding, deployment, or production-data changes are included.

## Approval Gate

This proposal and its tasks are review material only. Do not implement or enable the selector until the user explicitly approves the implementation phase. Default behavior remains the current baseline.
