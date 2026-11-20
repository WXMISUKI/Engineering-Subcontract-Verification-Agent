# Change: Pin admission flow contracts to runtime and report outputs

## Why

The admission flow manifest and source comments refer to a dedicated regression test and renderer that were absent. Existing basis validation tests verify clause bindings, but they do not ensure that declared kernel entries remain callable or that flow kinds remain represented in close groups and report sections.

## What Changes

- Add a persistent regression test for the flow manifest's kernel entry points.
- Assert that flow kinds remain aligned with runtime close groups and report sections.
- Correct stale source comments and the flow manifest maintenance note.
- Update the ownership ledger and record exact validation evidence.

## Boundaries

- No admission or retrospective business rule changes.
- No rule-data changes, database writes, service restart, or historical report recomputation.
- The test proves declared entry and projection wiring, not semantic correctness or full runtime execution of every kernel.
