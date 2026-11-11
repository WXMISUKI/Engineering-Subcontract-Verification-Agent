## Why

AI review batches have reached the application's 120-second timeout in the target environment, but existing evidence cannot distinguish model processing time, provider queueing, network delay, or rate limiting. Local requests to the configured Qwen3.7 Flash endpoint succeed even near the current request-size ceiling, so changing production batch or concurrency settings without better correlation would be speculative.

## What Changes

- Add bounded, privacy-safe model request diagnostics correlated with task, background job, advisory request, group, batch, and timeout-split stage.
- Record request size, item count, elapsed time, exception type, HTTP status, safe provider request ID, and numeric Retry-After when available.
- Establish a local provider-backed workload matrix and preserve the measured results and evidence limits.
- Defer batch-size, concurrency, timeout, and retry-policy changes until the production failure pattern is attributable.

## Capabilities

### New Capabilities
- `ai-model-request-diagnostics`: Correlate model request outcomes with task and batch execution while excluding credentials and business content.

### Modified Capabilities

## Impact

- Backend request execution and batch orchestration in `semantic_review.py`, `ai_review_batch.py`, `ai_guarded_review.py`, and `service.py`.
- Backend tests for diagnostic safety, batch correlation, timeout splitting, and bounded concurrency.
- No database schema, task result, billing contract, provider endpoint, or deployment changes in this phase.
