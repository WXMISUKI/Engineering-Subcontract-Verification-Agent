## ADDED Requirements

### Requirement: Optional source absence is a scope limitation, not a business finding

The system SHALL distinguish optional-source coverage from business findings and execution status. The general-contractor contract is optional for starting subcontract review when at least one professional or labor subcontract contract is present. If an applicable rule requires general-contractor evidence and that optional source is absent, the system SHALL mark only that rule as not covered for the current review generation. It SHALL NOT create a `hit`, `miss`, or `undecidable` finding for that absence.

#### Scenario: Subcontract packet has no general-contractor contract
- **WHEN** the task has at least one valid professional or labor subcontract contract and no general-contractor contract
- **THEN** subcontract review may proceed
- **AND** rules that do not depend on the general-contractor source execute normally
- **AND** applicable rules that do depend on it are listed as not covered due to an optional source
- **AND** the absence is neither counted as a finding nor treated as compliance

#### Scenario: General-contractor source is not relevant to an applicable rule
- **WHEN** an applicable rule's frozen source contract does not require general-contractor evidence
- **THEN** the missing general-contractor contract does not limit that rule or create a scope notice

#### Scenario: Required rule evidence is missing
- **WHEN** a rule's own required evidence is missing or insufficient
- **THEN** the existing rule-specific undecidable behavior remains available
- **AND** the missing optional general-contractor source is not used to mask that required-evidence gap

#### Scenario: Multiple general-contractor contracts are assigned
- **WHEN** more than one general-contractor contract is assigned to a task
- **THEN** the existing material-admission/attribution error is preserved
- **AND** the system does not silently reduce the error to an informational scope notice

### Requirement: Optional source states and rule execution are distinct

The system SHALL distinguish a source that is absent, processing, readable, terminally unreadable, or not reliably attributed. Rule applicability and execution state SHALL remain distinct from `hit`, `miss`, and `undecidable` business results.

#### Scenario: Optional source is still processing
- **WHEN** an optional general-contractor material is uploaded and its existing parse/read workflow is still running
- **THEN** the source is represented as processing, not absent or covered
- **AND** existing processing/readiness gates remain in force until processing reaches a terminal state

#### Scenario: Optional source reaches an unreadable terminal state
- **WHEN** parsing has terminated without the pages required by a general-dependent rule
- **THEN** only dependent rules are marked not covered with an unreadable-source reason
- **AND** unrelated rules remain eligible to execute
- **AND** the system does not claim the unreadable source was reviewed

#### Scenario: Optional source attribution is ambiguous
- **WHEN** the source cannot be reliably linked to the applicable task, subject, or contract chain
- **THEN** dependent rules do not consume it as verified evidence
- **AND** the source/attribution limitation remains separately visible from a business finding

### Requirement: Scope limitations are independent, non-actionable workbench items

The system SHALL expose current-generation scope limitations in the review workbench, including the pending-work area, while keeping them separate from findings, finding counts, review receipts, and finding disposition actions.

#### Scenario: Workbench displays an optional-source limitation
- **WHEN** the current generation contains one or more optional-source limitations
- **THEN** the workbench displays a neutral scope-notice group with the affected rule and reason
- **AND** the notice offers no accept/ignore action and does not require user disposition
- **AND** it is not counted as a finding or as a missing required upload

#### Scenario: No dependent rule is limited
- **WHEN** the optional source is absent but no applicable rule depends on it
- **THEN** no general-contractor scope notice is shown

### Requirement: Reports disclose scope limitations without treating them as findings

All formal report surfaces SHALL use the same current-generation scope projection. A scope limitation alone SHALL NOT block formal report generation after all other required execution and report gates are satisfied. The report SHALL identify the rules and scope not covered and SHALL NOT describe the review as fully covered or fully passed.

#### Scenario: Only optional-source limitations remain
- **WHEN** all required rule executions are complete and no actual blocking finding remains, but one or more optional-source limitations remain
- **THEN** the formal report, opinion letter, Word/PDF, evidence package, and corresponding APIs may be generated
- **AND** each surface consistently discloses the limitations
- **AND** no surface presents the result as full-scope or full compliance

#### Scenario: A real blocking undecidable item remains
- **WHEN** the current generation contains a blocking `undecidable` finding without an accepted/ignored disposition under existing policy
- **THEN** the existing formal-report gate still blocks the output
- **AND** the optional-source limitation does not override that gate

#### Scenario: Required execution failed or is incomplete
- **WHEN** a required check is failed, not run, or incomplete
- **THEN** existing execution/readiness gates remain in force
- **AND** a scope limitation cannot be used as a successful execution result

### Requirement: Supplement generations resolve scope limitations only after successful re-review

The system SHALL preserve historical generations and SHALL resolve a current scope limitation only after the source has been processed, attributed, and successfully covered by the affected rule(s) in a new review generation.

#### Scenario: General-contractor material is uploaded but not yet reviewed
- **WHEN** a general-contractor material is uploaded after a scope-limited generation
- **THEN** upload alone does not clear the current limitation or mark the dependent rule covered
- **AND** the prior generation remains unchanged

#### Scenario: New generation successfully reviews the source
- **WHEN** a new generation has readable, correctly attributed source evidence and completes every affected rule
- **THEN** its current scope projection no longer lists the resolved limitation
- **AND** its findings, report, and coverage refer to the new generation
- **AND** the prior generation's limitation, findings, reports, and human decisions remain available unchanged

#### Scenario: New source remains unreadable or unlinked
- **WHEN** the new generation cannot reliably read or attribute the uploaded source
- **THEN** the dependent limitation remains with the precise reason
- **AND** no successful coverage is inferred from upload presence alone

### Requirement: Business source coverage is independent from AI page selection

The system SHALL keep optional-source business semantics separate from the MoMA AI scope selector. AI page selection SHALL NOT change fixed-rule findings or report conclusions. A selector that cannot prove its required source/evidence coverage SHALL continue to use its existing baseline fallback.

#### Scenario: T15-05 lacks its cross-source evidence source
- **WHEN** the general-contractor source required by a candidate T15-05 selector mapping is absent or unreliable
- **THEN** business coverage follows the frozen rule-source matrix
- **AND** the AI selector does not narrow pages on incomplete relation coverage
- **AND** no AI selector flag change alters fixed-rule results or the optional-source admission rule
