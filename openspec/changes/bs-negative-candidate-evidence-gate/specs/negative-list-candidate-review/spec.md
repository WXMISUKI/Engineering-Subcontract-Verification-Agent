## ADDED Requirements

### Requirement: Negative-list AI candidates use deterministic and semantic recall

The system SHALL enumerate every configured in-scope negative-list rule for each confirmed subject. Configured triggers and contexts SHALL remain high-precision deterministic recall signals, but their absence SHALL NOT by itself mean that the rule was assessed or excluded. When readable, subject-scoped engineering-scope excerpts exist, rules without a literal signal SHALL enter a rule-specific semantic candidate screen using the frozen scope excerpts and exact rule text, subject to the configured candidate budget. A signal or semantic candidate SHALL NOT establish a rule hit, miss, or factual conclusion.

#### Scenario: Literal rule signal is present
- **WHEN** a readable, subject-scoped engineering-scope excerpt contains the configured trigger and any required context
- **THEN** the rule enters the deterministic-recall candidate lane; the trigger alone does not establish applicability, hit, miss, or compliance

#### Scenario: Literal rule signal is absent but scope is readable
- **WHEN** no configured trigger is found but readable, subject-scoped engineering-scope excerpts are available
- **THEN** the rule enters the semantic candidate screen with its exact rule source and cited engineering-scope excerpts
- **AND** the result records whether screening completed, produced an advisory candidate, or failed; no candidate is not a deterministic rule miss or compliance conclusion

#### Scenario: No readable engineering scope is available
- **WHEN** no usable subject-scoped engineering-scope excerpt can be located
- **THEN** the rule remains unassessed with a specific scope-unlocated reason and creates no material finding; the system SHALL NOT claim the rule was screened or not applicable

#### Scenario: Relevant signal but missing threshold
- **WHEN** the material contains the rule-specific engineering signal but the threshold value is absent or unreadable
- **THEN** an advisory-only undecidable candidate MAY be created for human verification; the system SHALL NOT infer a hit or miss

### Requirement: Material citations must be relevant to their candidate rule

The system SHALL validate each proposed material quote as an exact source span in the same task, subject, material, and physical page, and validate its linkage to the candidate rule. Deterministic-recall candidates SHALL have the configured trigger and context in the cited quote. Semantic-recall candidates SHALL cite exact readable engineering-scope text evaluated against the exact frozen rule source; unrelated generic text SHALL NOT qualify. Every AI candidate finding remains an advisory-only manual prompt.

#### Scenario: Generic contract language is cited for a technical rule
- **WHEN** a candidate proposal cites only generic scope, payment, equipment, or management language that lacks the candidate rule signal
- **THEN** the evidence SHALL be rejected for that candidate and SHALL NOT become a human-review finding

#### Scenario: One quote applies to more than one rule
- **WHEN** a single quote independently contains the required signals and context for more than one rule
- **THEN** it MAY support a separate undecidable candidate for each rule; the system SHALL NOT deduplicate solely because the quote is identical

#### Scenario: Semantic candidate has no literal trigger
- **WHEN** a semantic-recall proposal has no configured trigger in its cited material quote
- **THEN** it MAY become an advisory-only manual prompt only if the exact quote exists in the current subject-scoped readable material and the exact rule source exists in the current frozen reference snapshot
- **AND** it SHALL NOT become a machine conclusion or deterministic rule hit

### Requirement: Candidate execution and cache versions remain honest

The system SHALL preserve partial execution status and isolate negative-candidate validation versions from other AI review groups.

#### Scenario: Old candidate validation result
- **WHEN** an event was produced under an older negative-candidate evidence contract
- **THEN** it SHALL remain historical, SHALL NOT be exposed as a current reviewable candidate, and SHALL require an explicit retry before new model calls
- **AND** unrelated AI review groups retain their current validation version

#### Scenario: Partial candidate batches
- **WHEN** one or more planned batches do not complete
- **THEN** the candidate result SHALL remain partial and SHALL NOT be presented as complete negative-list coverage

#### Scenario: Candidate-screening contract changes
- **WHEN** the deterministic/semantic screening inputs, result schema, or rule-to-material validation changes
- **THEN** the negative-candidate validation version SHALL change; old events remain historical and require an explicit retry before any new model call

#### Scenario: Semantic mode crosses the request and validation boundary
- **WHEN** a readable-scope candidate is planned with semantic recall
- **THEN** batch planning, wire input, response validation and persistence validation SHALL retain that recall mode and exact material/page/rule citation restrictions
- **AND** empty proposals SHALL disclose that no reviewable suggestion was formed, without asserting compliance or a rule miss

#### Scenario: Readable scope does not attribute unread pages
- **WHEN** an engineering scope excerpt is readable but other contract pages are unreadable
- **THEN** locating the excerpt alone SHALL NOT establish the unread pages' rule attribution
- **AND** missing or unknown execution results SHALL NOT be projected as OCR gaps without a verified reading-specific reason

### Requirement: Independent groups continue after an identified reading gap

The system SHALL continue independent review groups when a candidate is not run specifically because its source reading is incomplete. After human review it MAY produce a scope-limited report that explicitly identifies the uncovered rules, materials and physical pages; the report SHALL NOT claim full coverage or compliance.

#### Scenario: Unread material blocks negative candidates
- **WHEN** the A1 candidate lane has only identified source-reading gaps and later review groups are independently executable
- **THEN** later groups SHALL execute, and the job execution SHALL end in `review_required` with partial coverage rather than `failed` or full success
- **AND** the unread material and affected items SHALL remain unassessed and available for traceable human review

#### Scenario: Reading gaps are localized to rule scope
- **WHEN** one or more pages are unreadable or OCR-suspect
- **THEN** the system SHALL associate those pages only with candidate rules whose scope evidence can be traced to the affected page or engineering-scope segment
- **AND** a whole-contract reading flag SHALL NOT by itself mark every negative-list rule uncovered when its relevant scope evidence is complete
- **AND** if page-to-rule attribution cannot be established, the system SHALL disclose an unattributed scope gap and SHALL NOT claim affected rules were fully screened

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
