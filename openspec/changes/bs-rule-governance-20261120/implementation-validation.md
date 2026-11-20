# Staged Implementation Validation

Date: 2026-11-20

## Scope

P0 only: initial ownership/flow/evidence/test mapping, local CodeGraph de-duplication, and OpenSpec validation. No product code, rule data, runtime service, business database, historical task, or report was changed.

## Evidence

- `docs/design/2026-11-20-rule-ownership-baseline.md` records the module-level rule/source/consumer/evidence/report/test map and explicit unknowns.
- Before index rebuild, CodeGraph reported 736 indexed files and returned both current `backend/.../rules.py` and the historical copy under `artifacts/bs-retrospective-trial-20261119/runtime/`.
- CodeGraph's installed README documents that Git ignore rules are honored. The exact historical runtime directory was added to this machine's `.git/info/exclude`; project `.gitignore` and other user artifacts were left intact.
- After `codegraph index --force --quiet`, CodeGraph reported 531 indexed files. Searching `load_review_flow` returned the current `backend/app/plus/blockchain_supervision/rules.py:151` only; querying the excluded runtime directory returned no files.
- `openspec validate bs-rule-governance-20261120 --strict --no-interactive`: passed.

## Limits and Follow-up

- The rule map is module/data-family level, not a verified row-by-row mapping of every rule ID to every evidence field and report section. Existing dimension inventories provide useful coverage input but are not a substitute for runtime tracing.
- The `rules.py` loader grouping is not proof of duplicate business rules. P1 must compare actual schemas, identifiers, callers, and validation ownership before consolidating anything.
- P0 was documentation and local-index governance only; no product tests were needed for that phase.

## P1 Catalog Contract

- Added `backend/app/plus/blockchain_supervision/data/rule_catalog.json` with partial asset mappings for admission flow, retrospective contract analysis, shared reference retrieval, and the P2 severity-matrix slice. This is governance metadata, not an exhaustive per-rule registry.
- Added the opt-in validator `rule_catalog.py`; no runtime caller imports it. It validates stable unique IDs, owner and source/test paths, explicit review-mode boundaries, allowed status/version fields, and prevents source/test paths escaping the repository.
- Added persistent regression tests in `backend/tests/test_bs_rule_catalog.py` for valid mode separation, duplicate IDs, missing owner, and contradictory/ambiguous mode applicability.
- Test command: `backend\\.venv\\Scripts\\python.exe -m pytest tests/test_bs_rule_catalog.py -q` from `backend/` — **6 passed**. Initial test execution found that `test_bs_review_flow.py` did not exist despite being named in code comments and the first ledger; the ledger now points to existing `test_bs_basis_validation.py` and records the missing dedicated test as a gap.
- Follow-up on 2026-11-20: the flow-wiring gap was closed in `bs-flow-contract-regression-20261120`. A dedicated `backend/tests/test_bs_review_flow.py` now pins manifest entries to callable symbols and checks alignment with close groups and report sections; see that change's `implementation-validation.md` for evidence. The earlier statement above records the P1 state at that time.
- Compatibility: no service/router/kernel/report code imports the catalog, so no runtime workflow or conclusion was changed in P1.
- Still open after P1: the catalog listed three partial capability assets, not every rule ID; P2 added one representative concrete slice, but whole-catalog rule-ID coverage remains incomplete.

## P2 Representative Migration

- Selected `severity_matrix.json`: deterministic, already data-driven, with one runtime consumer (`severity.py`) and a narrow loader boundary. CodeGraph showed no dedicated tests before migration.
- Before migration, added `test_bs_severity_rule_source.py` and ran it against the existing loader: **2 passed**. The test locks the full matrix equality and representative hit, uncertainty downgrade, low-severity and default behavior.
- Moved the source loader to `severity_rules.py`; `severity.py` now imports its domain-local loader. `rules.load_severity_matrix()` remains as a compatibility forwarder for any legacy external callers. The JSON and adjudication algorithm are unchanged.
- Added catalog entry `A13:severity-matrix` with admission-only applicability and `partial` status. It is partial because the directory is not a complete rule-ID inventory.
- After migration, the same baseline tests passed. Cross-mode regression command from `backend/`: `..\\backend\\.venv\\Scripts\\python.exe -m pytest tests/test_bs_rule_catalog.py tests/test_bs_severity_rule_source.py tests/test_bs_basis_validation.py tests/test_bs_review_mode.py tests/test_bs_retrospective_analysis.py tests/test_bs_retrospective_knowledge.py -q` — **57 passed**.
- OpenSpec strict validation passed after the migration. `git diff --check` exited 0; Git printed only its expected CRLF normalization notices for the two touched existing Python files.

## P3 Maintenance Guide and Remaining Gaps

- Added `docs/design/2026-11-20-rule-maintenance-guide.md` for source ownership, mode applicability, evidence/report links, tests, versioning and retirement.
- Cross-mode regression covers the migrated admission slice plus basis/review-mode/retrospective analysis/reference retrieval; it does not establish every admission rule or every retrospective clause family as complete.
- No full rule-ID catalog, no broad service/router refactor, and no historical task/report recomputation were performed.
- Final CodeGraph sync completed after source changes: 535 files indexed. `load_severity_matrix` resolves to the new owner and the intentional backward-compatible wrapper; the admission runtime imports the new owner, and the excluded historical runtime directory remains absent.
- Final OpenSpec validation: `openspec validate bs-rule-governance-20261120 --strict --no-interactive` — passed.
