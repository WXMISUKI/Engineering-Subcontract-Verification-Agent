## ADDED Requirements

### Requirement: Negative-list AI candidates require rule-specific signals

The system SHALL use the configured trigger and optional context for each negative-list rule as a candidate-recall signal. A signal SHALL NOT establish a rule hit, miss, or factual conclusion.

#### Scenario: No rule-specific signal
- **WHEN** no readable, subject-scoped candidate excerpt contains the rule trigger and any configured context
- **THEN** that rule remains not run for the AI candidate lane, creates no material finding, and is not counted as a deterministic negative-list miss

#### Scenario: Relevant signal but missing threshold
- **WHEN** the material contains the rule-specific engineering signal but the threshold value is absent or unreadable
- **THEN** an advisory-only undecidable candidate MAY be created for human verification; the system SHALL NOT infer a hit or miss

### Requirement: Material citations must be relevant to their candidate rule

The system SHALL validate that each proposed material quote itself contains the candidate rule's trigger and any configured context, in the same task, subject, material, and physical page. A signal elsewhere in the page SHALL NOT validate an unrelated quote.

#### Scenario: Generic contract language is cited for a technical rule
- **WHEN** a candidate proposal cites only generic scope, payment, equipment, or management language that lacks the candidate rule signal
- **THEN** the evidence SHALL be rejected for that candidate and SHALL NOT become a human-review finding

#### Scenario: One quote applies to more than one rule
- **WHEN** a single quote independently contains the required signals and context for more than one rule
- **THEN** it MAY support a separate undecidable candidate for each rule; the system SHALL NOT deduplicate solely because the quote is identical

### Requirement: Candidate execution and cache versions remain honest

The system SHALL preserve partial execution status and isolate negative-candidate validation versions from other AI review groups.

#### Scenario: Old candidate validation result
- **WHEN** an event was produced under an older negative-candidate evidence contract
- **THEN** it SHALL remain historical, SHALL NOT be exposed as a current reviewable candidate, and SHALL require an explicit retry before new model calls
- **AND** unrelated AI review groups retain their current validation version

#### Scenario: Partial candidate batches
- **WHEN** one or more planned batches do not complete
- **THEN** the candidate result SHALL remain partial and SHALL NOT be presented as complete negative-list coverage

### Requirement: Independent groups continue after an identified reading gap

The system SHALL continue independent review groups when a candidate is not run specifically because its source reading is incomplete. After human review it MAY produce a scope-limited report that explicitly identifies the uncovered rules, materials and physical pages; the report SHALL NOT claim full coverage or compliance.

#### Scenario: Unread material blocks negative candidates
- **WHEN** the A1 candidate lane has only identified source-reading gaps and later review groups are independently executable
- **THEN** later groups SHALL execute, and the job execution SHALL end in `review_required` with partial coverage rather than `failed` or full success
- **AND** the unread material and affected items SHALL remain unassessed and available for traceable human review

#### Scenario: A review-required result is not retried as an execution failure
- **WHEN** all configured groups have reached terminal states and the only blocking coverage is attributable to identified source-reading gaps
- **THEN** the system SHALL expose the completed execution and incomplete coverage as separate states
- **AND** it SHALL direct the user to human review without offering a same-generation model retry
- **AND** genuine model, service, lease, or coverage failures SHALL remain failed and retain their explicit retry path

#### Scenario: Historical material-scope failure event
- **WHEN** a historical failed event has only the `incomplete_material_scope` category and current coverage proves all groups terminal with only identified reading gaps
- **THEN** the system MAY project the current status as `review_required` without rewriting or deleting the historical event
- **AND** the system SHALL fail closed if the current coverage no longer proves that condition

#### Scenario: Scope-limited report after review
- **WHEN** all independent groups have terminal reviewable outcomes, the only unresolved AI candidates have identified reading gaps with attributable rule IDs, material IDs and pages, and required human reviews are complete
- **THEN** the system MAY close the task with a scope-limited report that lists those rules and pages across the report and its exports
- **AND** the system SHALL NOT count the uncovered candidates as passes, misses, or reviewed findings

#### Scenario: Failure cannot be attributed to a reading gap
- **WHEN** a group has an unknown failure, pending work, invalid coverage, or changed context
- **THEN** the system SHALL NOT treat that failure as an identified reading gap or continue on that basis
