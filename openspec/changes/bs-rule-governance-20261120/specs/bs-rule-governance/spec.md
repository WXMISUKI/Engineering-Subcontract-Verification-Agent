# bs-rule-governance Specification

## ADDED Requirements

### Requirement: Rule Ownership Is Traceable
The system SHALL maintain a rule catalog that identifies each maintained rule's authoritative source, applicable review mode, owning runtime module, entry point, evidence/result path, report projection, and regression tests. When a mapping is not yet verified, the catalog SHALL mark it as unknown rather than infer ownership.

#### Scenario: Maintainer changes a rule
- **WHEN** a maintainer selects a rule by stable identifier
- **THEN** the catalog identifies its authoritative content and the focused tests required to validate the change

### Requirement: Review Modes Keep Explicit Applicability Boundaries
The system SHALL distinguish admission-only, retrospective-only, and shared infrastructure/rules. Shared infrastructure SHALL NOT imply that admission verdict rules automatically apply to retrospective review or vice versa.

#### Scenario: A rule is referenced by both workflows
- **WHEN** a catalog entry is marked shared
- **THEN** its use by each workflow and any mode-specific applicability constraints are explicitly documented

### Requirement: Rule Content Keeps a Suitable Source of Truth
The system SHALL permit heterogeneous authoritative sources such as legal clause text, clause indexes, flow manifests, structured thresholds, deterministic code, and prompts. A catalog SHALL reference these sources and SHALL NOT duplicate their executable content.

#### Scenario: Rule source is migrated
- **WHEN** a rule moves to a new catalog-backed source
- **THEN** existing runtime behavior remains compatible until callers are migrated and regression-verified

### Requirement: CodeGraph Excludes Confirmed Historical Runtime Copies
The local CodeGraph index SHALL not present a confirmed historical runtime copy as current project source when resolving current symbols. The exclusion SHALL be narrow and SHALL not hide unrelated artifacts, backups, or temporary evidence from version control.

#### Scenario: Maintainer queries a current symbol
- **WHEN** CodeGraph resolves a symbol present in both source and the historical trial copy
- **THEN** only the current source is returned as project code
