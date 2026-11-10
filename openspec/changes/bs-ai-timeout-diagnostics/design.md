## Context

The model request path has an application-level 120-second timeout, batches up to 32 material segments, and a concurrency limit of two per event loop, not per process or provider account. A timeout on a multi-item batch can trigger two split requests. The current task-5 production evidence records a 120-second client timeout on one batch and another timeout during split recovery, but does not establish provider queueing, model processing, network delay, or rate limiting as the cause.

The local root `.env` already has the Qwen model, endpoint, and key configured and is ignored by Git. Direct backend Python tests do not load this file automatically; the provider-backed probe explicitly loaded it without printing values. In the current local batch path, billing binding/default selects the task model code, while `client_from_env(model_code=...)` requires it to match `LLM_MODEL` and uses the environment endpoint and key. This source inspection does not establish the configuration or exact deployed source of the historical production job.

## Goals / Non-Goals

**Goals:**

- Correlate each provider call with task, job, advisory request, group, batch number, and initial-versus-split attempt.
- Log bounded request size, item count, elapsed time, configured timeout, exception class, HTTP status, safe provider request ID, and numeric Retry-After.
- Keep diagnostic metadata outside model prompts, checkpoint keys, task results, and business findings.
- Use local provider-backed synthetic tests to measure request-size and concurrency behavior before changing operating parameters.

**Non-Goals:**

- Do not treat local synthetic success as proof that the historical production timeout is fixed.
- Do not change batch size, concurrency, billing behavior, task state, or checkpoint semantics solely from synthetic success. The user selected option A for a local candidate addressing the separately reproduced retry-amplification mechanism; historical production attribution remains open.
- Do not log credentials, endpoint URLs, headers other than allowlisted response metadata, prompts, material text, or model response bodies.
- Do not modify production data or deploy this diagnostic change in this phase.

## Decisions

### Keep the request interface and checkpoint identity stable

Pass an optional diagnostic context through the existing batch execution calls. It is consumed only by request logging and is never added to the wire payload or checkpoint namespace. This preserves existing model input and checkpoint reuse behavior.

Alternative considered: place diagnostic fields inside the request payload. Rejected because it changes model input and request-size accounting and could change checkpoint keys.

### Put request timing at the model-request seam

Record a generated model-request ID, model label, serialized request characters, item count, effective application timeout, elapsed milliseconds, exception type, and status at `semantic_review.request_json`. Existing batch logs retain the logical task and attempt details; the diagnostic context adds the advisory request and batch correlation.

Alternative considered: only add fields to task events. Rejected because provider calls can time out before a result event is saved, and event payloads should remain bounded operational state rather than a second telemetry store.

### Capture rate-limit response metadata conservatively

On failure, read only a bounded provider request ID from a known response header/SDK field and a numeric `Retry-After`. Do not log error bodies or arbitrary headers. This distinguishes an observed 429 from a local timeout without exposing provider response content.

### Do not tune from synthetic success alone

Local Qwen measurements completed for 8/16/32-item business-shaped synthetic batches, a 91K-character single batch, and two concurrent 91K-character batches. No 429 or 120-second timeout occurred. These measurements reject a deterministic failure at those tested sizes and concurrency, but they do not reproduce production output volume, upstream contention, account-level shared usage, or historical network conditions.

The SDK retry amplification was reproduced locally. On 2026-11-10 the user selected option A: disable SDK retries only for subcontract batch clients and implement bounded application-level recovery. The known historical job's provider-side trace remains unavailable.

### Local retry candidate

For each original batch, share at most four HTTP attempts and a 360-second monotonic deadline across the initial request, backoff, and at most two sequential split children. Each individual request retains the existing 120-second maximum, capped by remaining time. A multi-item timeout triggers one split level; a single-item timeout may retry once if budget remains. Retry 429 and 5xx at most once per logical request, with jittered backoff and a bounded valid Retry-After; when requested delay exceeds the allowed wait or remaining deadline, stop rather than retry early. Authentication and other non-transient failures are not retried. Preserve cancellation, incomplete results and checkpoint invariants. These values are local candidates, not provider quota claims; evaluate against fault injection, Qwen synthetic loads, and acceptance before deployment.

### Empty OCR table noise

An authorized local material produced a segment of 732 lines made exclusively of spaces, vertical bars and newlines; both halves of this no-content segment reached 120 seconds. Remove only runs of at least five consecutive Markdown table rows containing no cell content, using source spans so preserved excerpts retain exact offsets. Preserve nonempty cells, separators and isolated blank rows. Mark the affected finding's material scope incomplete and require manual verification. A noise-only input must not be counted as successfully reviewed. This eliminates a reproduced wasteful request, but does not prove the historical online timeout had the same cause.

## Risks / Trade-offs

- [Logs may contain opaque operational IDs] -> Validate identifier syntax and length; exclude keys, URLs, prompts, material text, and response bodies.
- [Synthetic inputs may underrepresent output-generation cost] -> Report input characters and provider usage where available; add a representative synthetic response-volume case before selecting final parameters.
- [Current container logs may rotate before a failure is reviewed] -> Future diagnostics should be captured promptly with timestamp, model, task, batch, provider request ID, status, Retry-After, and token usage.
- [Application and OpenAI client timeout values can differ] -> This phase logs the application-level timeout; a later configuration change must define one validated effective timeout and retain a small transport margin.

## Migration Plan

No database migration is required. Run backend tests and the provider-backed synthetic matrix locally. A candidate deployment for observing a new real failure is a separate gate and requires explicit review of current target state, backup, rollback image, and deployment authorization. Rollback is a code/image rollback; no historical task or result mutation is part of this change.

## Open Questions

- What provider status, request ID, Retry-After, input/output token counts, and server-side latency did the failed request report?
- Was the application task bound to `qwen3.7-flash` at failure time, and did another workload share the same provider key/account quota?
- Does the production provider count the request as accepted before the local 120-second timeout, and can a client retry therefore overlap an upstream request still in progress?
