## 1. Safe request diagnostics

- [x] 1.1 Add request-size, item-count, timeout, elapsed-time, exception, HTTP status, safe provider request ID, and numeric Retry-After logging.
- [x] 1.2 Propagate task/job/advisory-request/group/batch/split identifiers outside model payload and checkpoint identity.
- [x] 1.3 Add regression tests for timeout, 429 metadata redaction, split correlation, and concurrency bounds.
- [x] 1.4 Run focused backend regression tests and `git diff --check`.
- [x] 1.5 Run one real-Qwen synthetic batch through the diagnostic path and verify safe job/request/batch correlation in logs.
- [x] 1.6 Add selected model code, client timeout, timeout phase, and safe token-usage diagnostics without changing the billing callback.
- [x] 1.7 Verify the locked SDK's hidden retry behavior with the actual client and a local mock transport.
- [x] 1.8 Measure combined SDK and application retry/split behavior under controlled failures.
- [x] 1.9 Compare retry policy alternatives using controlled transient, persistent, and batch-size-sensitive failures.
- [x] 1.11 Obtain the requested architecture decision before changing retry ownership: user selected option A.
- [x] 1.13 Implement and test batch-scoped SDK retry disablement and shared bounded recovery, including safe 429/5xx backoff and timeout splitting.
- [ ] 1.14 Validate failure injection, checkpoint reuse, real-provider synthetic requests and local acceptance before any deployment.
- [x] 1.15 Remove only repeated empty OCR table rows from model input while preserving source offsets and marking incomplete scope; verify real-material behavior and evidence safety.
- [x] 1.16 Bound task-level AI context to registered material/page anchors while preserving an explicit incomplete-scope limitation; verify with real task-5 isolated evidence.
- [x] 1.10 Provide and locally validate an optional Uvicorn log configuration that makes only the request-diagnostic logger's INFO records visible.
- [x] 1.12 Verify and document that the current batch concurrency limit is per event loop rather than per process.

## 2. Local provider-backed baseline

- [x] 2.1 Confirm root `.env` model configuration is present without printing values; keep it ignored by Git.
- [x] 2.2 Probe the configured Qwen3.7 Flash endpoint with a minimal synthetic request and an 8-item diagnostic-context request.
- [x] 2.3 Measure 8/16/32-item business-shaped synthetic requests and a 91K-character request.
- [x] 2.4 Measure two concurrent 16-item and two concurrent 91K-character requests.
- [x] 2.5 Measure a near-budget 32-item request that produces and validates 32 synthetic proposals.
- [ ] 2.6 Obtain provider-side telemetry for the historical 120-second timeout; local synthetic tests did not reproduce it.

## 3. Attribute and repair

- [ ] 3.1 Compare a failed provider request's status/request ID/Retry-After/input-output tokens/latency with the local baseline.
- [ ] 3.2 Decide whether the cause is payload/output volume, provider queueing or rate limit, network latency, model binding, or shared account usage.
- [ ] 3.3 Implement only the evidence-backed adjustment to chunking, concurrency, timeout, retry/backoff, or connection lifecycle.
- [ ] 3.4 Re-run provider-backed tests and backend regressions; verify no result loss or checkpoint regression.
- [ ] 3.5 Complete local acceptance before proposing any target-environment deployment.
