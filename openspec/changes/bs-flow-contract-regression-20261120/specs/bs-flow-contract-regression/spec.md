## ADDED Requirements

### Requirement: Admission Flow Wiring Is Regression-Tested
The system SHALL keep declared admission kernel entries callable and ensure every flow kernel kind is represented by the review close groups and report sections. The regression SHALL pin the expected manifest entries so renames or omissions require an explicit reviewed update.

#### Scenario: Maintainer changes an admission flow entry
- **WHEN** a kernel entry or flow kind changes
- **THEN** the focused regression identifies missing callables or mismatches with close groups and report sections

### Requirement: Flow Documentation Names Existing Validation Assets
The flow manifest, source comments, and ownership ledger SHALL refer only to existing tests and maintenance tools.

#### Scenario: Maintainer follows the flow manifest note
- **WHEN** a maintainer uses the stated validation path
- **THEN** the named test files exist and cover the stated contract
