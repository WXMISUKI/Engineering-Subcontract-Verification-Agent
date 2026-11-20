# Design: Rule Ownership Governance

## Current Boundaries

- Admission: `review_mode.py` defaults missing historical mode snapshots to admission. `review_flow.json` declares the admission sequence and kernel kinds; `rules.py` loads it through the basis validation gate. Rule-specific kernels and projections remain separate modules.
- Retrospective: `retrospective_runtime.py` and `retrospective_analysis.py` own the retrospective task lifecycle and contract clause analysis. `retrospective_sources.py` freezes readable source text and coordinates; `retrospective_knowledge.py` reuses reference-package retrieval with an explicit baseline date. The retrospective path does not run the admission five-step completion workflow.
- Shared infrastructure: task models/storage, material parsing/readability, frozen basis source validation, selected reference retrieval, and report/document infrastructure have callers on both paths. Sharing a helper does not imply sharing a rule's applicability or verdict policy.

## Ownership Principle

The unified structure is a **catalog/manifest of ownership and traceability**, not a universal executable rule schema. Each entry should identify stable ID, title, source type and path, authority/basis IDs, applicable review modes, owning module, runtime entry, evidence input/output, report projection, lifecycle/version, and regression tests. Mode-specific fields may be absent; applicability must be explicit.

Rule content remains in its suitable authority:

- legal/basis clause text: `basis_clauses.json`;
- clause-to-item coordinates: `basis_index.json`;
- admission sequencing and kernel mapping: `review_flow.json`;
- negative-list definitions and matchers: their separate data files;
- structured thresholds/catalogs: their typed domain JSON;
- deterministic adjudication: focused kernel modules;
- retrospective prompt/validation/orchestration: retrospective modules.

## CodeGraph Hygiene

The historical trial runtime is an untracked duplicate of source, not a supported execution target. Exclude only `artifacts/bs-retrospective-trial-20261119/runtime/` through local `.git/info/exclude`, then rebuild the local CodeGraph index. Keep other artifacts and backups visible to Git and available for audit.

## Migration Strategy

1. P0 records observed ownership and evidence paths, excludes the confirmed duplicate runtime from the local index, and verifies symbol resolution.
2. P1 specifies/validates the catalog schema and records explicit shared versus mode-specific ownership without runtime behavior changes.
3. P2 migrates one deterministic, low-risk data slice; compare pre/post inputs and outputs and retain the old source until all callers have migrated.
4. P3 runs focused admission and retrospective regressions and closes the maintenance guide with evidence and known gaps.

No stage changes legal meaning, historical snapshots, services, or shared databases.
