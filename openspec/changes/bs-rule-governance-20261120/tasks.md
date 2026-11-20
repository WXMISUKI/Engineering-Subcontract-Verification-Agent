## P0 - Evidence and ownership baseline

- [x] 0.1 Record admission, retrospective, and shared infrastructure boundaries from current source.
- [x] 0.2 Create the initial rule/entry/evidence/report/test mapping ledger.
- [x] 0.3 Narrowly exclude the confirmed historical runtime copy from this machine's CodeGraph index.
- [x] 0.4 Rebuild the local index and verify current symbol resolution excludes the historical copy.
- [x] 0.5 Validate this OpenSpec change and record P0 evidence and known gaps.

## P1 - Catalog design

- [x] 1.1 Define stable catalog fields, ownership states, versioning and validation invariants.
- [x] 1.2 Map shared infrastructure separately from admission-only and retrospective-only rules.
- [x] 1.3 Add catalog schema examples and tests/validation for duplicate IDs, missing owners and ambiguous applicability.
- [x] 1.4 Review compatibility impact without migrating runtime callers.

## P2 - Representative migration

- [x] 2.1 Select one low-risk deterministic rule slice using the P1 catalog.
- [x] 2.2 Capture current input/output behavior and callers before migration.
- [x] 2.3 Migrate the selected slice with compatibility preserved.
- [x] 2.4 Run focused regression and compare outputs; stop if behavior changes.

## P3 - Acceptance and maintenance guide

- [x] 3.1 Run focused admission regressions for the migrated rule and shared contracts.
- [x] 3.2 Run focused retrospective regressions for its explicit applicability boundary.
- [x] 3.3 Document how to add, change, validate and retire rules.
- [x] 3.4 Record actual commands, results, remaining gaps, and close the change only when all acceptance gates pass.
