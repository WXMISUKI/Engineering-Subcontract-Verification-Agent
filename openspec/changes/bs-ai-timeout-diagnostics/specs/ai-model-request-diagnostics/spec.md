## ADDED Requirements

### Requirement: Model request failures are diagnosable without exposing business content

The system SHALL record bounded diagnostics for AI model requests, including the logical task/job/request/model-code/group/batch correlation when available, request character count, item count, effective application and client timeout, elapsed time, exception type, timeout phase, and HTTP status. When the provider returns token usage, diagnostics MAY record validated non-negative input, output, and total token counts. The system MAY record a validated provider request ID and numeric Retry-After when returned by the provider. Diagnostics MUST NOT include credentials, endpoint URLs, prompts, material text, model response bodies, or arbitrary response headers.

#### Scenario: A batch request times out
- **WHEN** a model request exceeds the application timeout
- **THEN** a diagnostic log identifies the task, selected model code, and batch context, request size, application/client timeouts, elapsed time, and application-deadline phase without logging prompt or material content

#### Scenario: A provider transport timeout occurs
- **WHEN** the model client raises a timeout with a known connect, read, write, or pool timeout cause
- **THEN** diagnostics identify the bounded transport timeout phase without logging exception messages or request content

#### Scenario: A successful response includes token usage
- **WHEN** the provider returns validated input, output, and total token counts
- **THEN** diagnostics record those counts and the existing billing callback receives the original usage unchanged

#### Scenario: A batch is split after timeout
- **WHEN** timeout recovery submits child batches
- **THEN** each provider call is distinguishable as the initial request or a numbered split part while the diagnostic context remains outside model input and checkpoint identity

#### Scenario: The provider returns a rate limit
- **WHEN** a provider error has HTTP status 429 and response metadata
- **THEN** diagnostics record status 429 and only validated provider request ID and numeric Retry-After; response body and credentials remain absent

#### Scenario: Diagnostic context contains an unrecognized value
- **WHEN** optional diagnostic context contains unsupported keys or unbounded/invalid identifiers
- **THEN** those values are omitted from logs and do not alter model payload, batch results, or checkpoint keys

### Requirement: Batch retries have one bounded owner

The subcontract batch model client SHALL disable implicit SDK retries. The initial batch, retries and split children SHALL share a maximum of four requests and a 360-second deadline, with each model request limited to the smaller of the existing 120-second deadline and remaining batch time. A multi-item timeout MAY split once into two sequential children. A rate limit or transient server failure MAY be retried once per logical call while budget remains. Valid numeric Retry-After values SHALL be honored up to 15 seconds; invalid, excessive or unaffordable delays SHALL stop automatic retry. Authentication failures SHALL NOT be retried. Cancellation SHALL propagate. Failed or unexecuted batch results SHALL NOT be saved as reusable checkpoints; evidence-filtered partial results MAY be checkpointed while preserving their partial status.

#### Scenario: Provider rate limit is temporary
- **WHEN** the initial batch receives HTTP 429 with a permissible Retry-After and the next call succeeds within budget
- **THEN** the application waits at least the indicated duration, sends only one retry, and preserves the existing usage callback

#### Scenario: Split requests exhaust the shared budget
- **WHEN** initial attempts and split children consume the original batch budget or deadline
- **THEN** no further model requests are sent and uncompleted findings remain failed and available for human review or later retry

#### Scenario: One split child fails
- **WHEN** one child succeeds and another fails
- **THEN** the successful child result is retained for this execution, the failed child remains failed, and the original batch is not recorded as a complete checkpoint

### Requirement: Repeated empty OCR table rows do not consume model budget

The system SHALL omit runs of at least five consecutive table rows consisting exclusively of vertical bars and whitespace from batch model input. Retained material excerpts SHALL remain exact contiguous slices of original OCR text with their original offsets. Affected findings SHALL be marked as incomplete material scope and require human verification. Nonempty rows and isolated empty rows SHALL be preserved; a noise-only input SHALL NOT be treated as a completed model review.

#### Scenario: Empty table rows occur between source text
- **WHEN** a page contains a long run of empty table rows between two passages
- **THEN** the passages retain their original page and offsets, and the affected finding is marked incomplete

#### Scenario: A page contains only repeated empty rows
- **WHEN** all input for a finding consists only of repeated empty table rows
- **THEN** no model batch is created and the finding remains undetermined

### Requirement: Evidence-anchored task context remains bounded and explicit

For task-level transfer or illegal-subcontract findings with a registered material/page anchor, the system MAY limit model material input to the anchored page and adjacent pages. The system SHALL mark the resulting context as incomplete and SHALL retain the existing fail-closed behavior when no anchor exists or the anchor cannot be resolved.

#### Scenario: A finding has a material and page anchor
- **WHEN** the finding identifies a material and page, or evidence contains such an anchor
- **THEN** the model receives only the resolved anchor window, the result carries an incomplete-scope limitation, and the system does not infer absence from omitted pages

#### Scenario: A finding has no usable anchor
- **WHEN** a task-level finding has no material/page anchor
- **THEN** the existing material selection and fail-closed behavior remain unchanged
