## ADDED Requirements

### Requirement: Opt-in AI input scope selection preserves fixed-rule scope

The system SHALL apply a candidate page selector only to explicitly mapped MoMA task-level transfer-scope or illegal-subcontract AI advice when the feature is explicitly enabled. The default SHALL preserve the current AI context behavior. Fixed-rule checks SHALL continue to use the complete currently available material scope.

#### Scenario: Selector disabled
- **WHEN** the selector setting is absent or disabled
- **THEN** AI context uses the current baseline scope behavior, and fixed-rule inputs and results are unchanged

#### Scenario: Selector enabled for an eligible rule
- **WHEN** an explicitly mapped task-level rule has a verified evidence-coverage contract
- **THEN** selected pages are a subset of the current authorized, readable, subject/material-scoped context, and every page retains its original material ID and physical page number
- **AND** lexical keyword or pattern matches are candidate locators only; narrowing requires verified source role, subject/object linkage, required relations, and positive and negative evidence coverage for that rule
- **AND** the reduced AI scope is marked incomplete and carries a user-readable limitation
- **AND** fixed-rule inputs, findings, and report conclusions are unchanged by the AI selector

#### Scenario: Rule has no complete selector mapping
- **WHEN** an eligible finding has no selector mapping or one or more required evidence categories cannot be reliably covered
- **THEN** the selector falls back to the existing eligible AI context and records a fallback reason

#### Scenario: No reliable keyword hit
- **WHEN** no configured selector matches any page in the current eligible context
- **THEN** the system uses the existing eligible context; it does not turn an empty candidate into an empty AI result or a business conclusion

#### Scenario: OCR, attribution, or evidence uncertainty
- **WHEN** source pages are unread/suspect, material or subject attribution is ambiguous, or the selector cannot establish the required scope
- **THEN** unread pages are not treated as matches, the current readability/attribution limitations remain visible, and selection falls back to the existing eligible context

#### Scenario: Existing structured evidence identifies a conflict
- **WHEN** existing rule/evidence processing explicitly identifies incompatible sources or values for a required evidence category
- **THEN** the selector retains the implicated eligible pages or falls back to the existing eligible context, and the AI advice remains limited and cannot claim the conflict was resolved
- **AND** the selector does not claim to detect arbitrary semantic conflicts that are not represented by an existing structured signal

#### Scenario: Structured evidence coverage is incomplete
- **WHEN** a rule adapter cannot enumerate all required evidence relations or their counterevidence from the complete eligible input, cannot prove source role/subject/object linkage, or encounters an unknown relation
- **THEN** it SHALL retain the existing eligible AI context and record a specific fallback reason
- **AND** neither an existing finding's primary page anchor nor its natural-language message SHALL be treated as proof that all required evidence sides are represented

#### Scenario: Rule-specific evidence adapter is evaluated in shadow mode
- **WHEN** a new rule adapter has not passed isolated synthetic and real-material coverage checks
- **THEN** it SHALL run only as a non-mutating shadow projection and SHALL NOT alter the model page set or register the rule as selectable
- **AND** shadow output SHALL not be treated as business acceptance

### Requirement: Candidate scope is auditable and checkpoint-isolated

The system SHALL include the selector strategy version and actual selected-input fingerprint in AI checkpoint identity. Audit metadata SHALL identify selected page references, evidence-category identifiers, scope status, and fallback reason without storing material text, prompts, or credentials.

#### Scenario: Selector strategy changes
- **WHEN** a task is evaluated using a different selector version or selected page set
- **THEN** an AI checkpoint from the other strategy/input is not reused

#### Scenario: Strategy is frozen for a review run
- **WHEN** a review run is created while the selector is disabled or enabled
- **THEN** the resolved strategy and version are frozen in that review run's seed and remain unchanged for all batches in that run
- **AND** changing the server setting affects only a newly created review run
- **AND** a legacy review seed without selector metadata is read as baseline, without inferred candidate metadata

#### Scenario: Roll back to baseline
- **WHEN** the selector is disabled after an isolated trial
- **THEN** new requests use baseline context and cannot consume candidate-scope checkpoints; existing historical events, reports, and human decisions remain unchanged

### Requirement: Partial AI scope cannot imply full-package absence or compliance

The system SHALL preserve the existing partial-scope prompt and evidence-validation restrictions for every selected subset. A citation that resolves within the selected subset SHALL NOT by itself establish semantic correctness or complete package coverage.

#### Scenario: Model reviews selected pages only
- **WHEN** model input is narrower than the existing eligible context
- **THEN** definitive compliance or package-wide absence claims are rejected or remain for human verification, and the scope limitation is available to the advisory/report projection
