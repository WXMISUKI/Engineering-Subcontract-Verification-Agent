## 1. Contract and regression

- [x] 1.1 Add regression tests proving generic quotes are rejected, rule-specific signals are required, threshold absence remains undecidable, and identical quotes are independently evaluated per rule.
- [x] 1.2 Add planning tests for no-signal candidates and configured context rules; verify those candidates remain not-run and do not create findings.
- [x] 1.3 Add candidate-only cache-version tests; verify unrelated AI groups retain the existing version.

## 2. Implementation

- [x] 2.1 Gate negative-list candidate inputs on the rule's configured trigger and context within readable scope excerpts.
- [x] 2.2 Gate accepted material quotes on the same rule-specific trigger/context in the cited quote.
- [x] 2.3 Version negative-candidate events/checkpoints independently and require explicit retry for old candidate results.

## 3. Acceptance

- [x] 3.1 Run focused backend regression and the existing negative-list/report tests.
- [x] 3.2 Replay the captured job evidence offline; all 19 persisted candidate quotes fail the current rule-signal gate. Replaying the task's 67 readable pages found 8 scope excerpts and no rule-specific trigger in any of 20 candidate rules, so the repaired planner leaves them not-run rather than creating findings.
- [x] 3.3 Confirm no fixed-rule coverage, report-gate, or vector-store changes occurred. Database reads used explicit read-only transactions; no model call, migration, or business-data write occurred.
- [x] 3.4 Validate the OpenSpec change and review the final diff.

## Verification evidence

- `backend`: `tests/test_ai_negative_candidate_relevance.py`, `test_ai_adjudication.py`, `test_ai_partial_scope_baseline.py`, `test_bs_scope_unlocated.py`, `test_bs_a1_kernels.py`, and `test_bs_background_ai_review.py` passed: 166 tests.
- The original task's 19 finding rows remain stored as historical data. Their previous event uses `bs-ai-batch-v21`; the candidate-only validator now treats that event as stale. A retry must be explicit and will use the new candidate scope/version.
- Local backend image `bs-backend-candidate:local` was rebuilt and the `8089` backend restarted. `/api/health` returns `ok`; the current `/tasks/2/findings` projection returns 0 negative-candidate findings because the persisted candidate event uses stale validation version `bs-ai-batch-v21`.
- A read-only database check found that all 19 existing candidate findings have `review_json.decision=accepted`. The task 2 no-model replan attempt was rejected before event creation because `task.state=done`; final read-back confirmed task state `done`, 1,040 event count, 19 findings, and 19 accepted receipts are unchanged. No model request or task write occurred.

## 4. Existing task disposition and local rollout

- [x] 4.1 Preserve the 19 accepted historical findings and receipts. The candidate-only validation version makes them stale for the current projection and prevents their decisions from carrying into a new candidate generation; stored rows/events and previously generated report snapshots remain historical.
- [ ] 4.2 Recompute the same business materials under the new candidate contract through an authorized new review generation. The local backend is updated, but the existing AI entrypoint rejects task 2 because it is `done` and only accepts `running/review`. Do not bypass the task-state guard or carry the old `accepted` decisions into a new generation.

## 5. Coverage disposition and job closure

- [x] 5.1 Keep `counts.not_run` as the audit total and add separate `not_assessed_no_signal` and `blocking_not_run` coverage fields; classify only complete-read candidates with `unavailable_reason=rule_signal_not_found` as non-blocking, leaving unread materials and unknown reasons fail-closed.
- [x] 5.2 Apply the same blocking count to background completion/retry, task progress, report readiness and task closure; expose the classification through the group-status API and front-end validation.
- [x] 5.3 Carry the no-signal count through the completed-job receipt and display a neutral warning that says no conclusion was formed; do not label it as needing human review unless another reviewable condition exists.
- [x] 5.4 Add offline regression coverage for no-signal completion without model invocation, true missing-scope blocking/review, incomplete-reading and unknown-reason blocking, report closure, API projection and front-end retry behavior.
- [x] 5.5 Preserve stored events and prior job outcomes; verify the repair needs no database migration and makes no external model request.

### Coverage disposition verification

- Backend: `tests/test_ai_negative_candidate_relevance.py`, `test_bs_scope_unlocated.py`, `test_bs_background_ai_review.py`, `test_bs_report_gate.py`, and `test_bs_ai_terminal_retry.py`: 69 passed.
- Frontend: `tests/autoAiReview.test.js`: 6 passed.
- Offline worker test verifies 20 complete-read no-signal candidates complete with a warning and no model call; report closure allows this state. Incomplete source reading and unexplained not-run candidates remain blocked.
- No database migration or business-data write was added. Negative-candidate plan/validation versions were advanced so events without the new source-reading fact cannot be reused as current coverage.

## 6. Incomplete material scope continuation (2026-11-13)

- [x] 6.1 Read-only verification of task 4 and original suspect pages; confirm personnel evidence and contract/signature or work-schedule pages are mixed in one material.
- [x] 6.2 Confirm with user: continue independent groups, retain uncovered status and human review, keep existing report gate.
- [x] 6.3 Add regression and implement continuation for verified material-scope gaps; preserve incomplete coverage and fail-closed handling for other failures. (Execution receipt semantics superseded by section 8: use review-required, not execution-failed.)
- [x] 6.4 Provide actionable access to each original suspect page; viewing does not change coverage. Reuse existing material replacement flow, without adding OCR editing or confirmation.
- [ ] 6.5 Verify retries, report gate, UI and offline real-scenario replay; run focused regressions and strict validation. Focused tests and real-task continuation passed; full report acceptance remains open.
- [ ] 6.6 Verify the complete real-material workflow within explicit authorization; record remaining limits without modifying historical outcomes.

### Local candidate evidence

- After explicit authorization, local backend/frontend were rebuilt and updated. Task 4 retried as job `e9f1d4d8d68648ac8e41d288942d4300`; earlier events remained intact. A1 was not rerun, A2 produced 7 reviewable partial results, and internal records produced 1 reviewable not-run result. The job ended failed with `incomplete_material_scope`; 20 A1 candidates remain blocked, not assessed as passes.
- Browser verification opened the existing findings and navigated from the reading warning to physical PDF page 63. The task showed 34 pending human-review items and report generation remained unavailable. No findings were reviewed or modified during the check.
- Focused backend tests: 74 passed. Frontend tests: 25 passed. OpenSpec strict validation passed; candidate image build succeeded. These results do not establish correctness of every business conclusion or complete report acceptance.
- Because the user declined OCR editing and page viewing does not change coverage, the 20 candidates cannot be resolved merely by viewing the original. User subsequently authorized a scope-limited report after human review, explicitly listing uncovered rules and pages; implementation and validation are pending.

## 7. Scope-limited report

- [x] 7.1 Define and validate an auditable current-generation projection linking uncovered candidate rule IDs to active materials and unread physical pages; fail closed on missing or conflicting data.
- [x] 7.2 Allow task closure only when all other execution and human-review gates pass; preserve the failed AI job and uncovered status.
- [x] 7.3 Include the scope limitation, affected rule IDs and pages in the report UI and Word/PDF exports, without a full-compliance claim.
- [ ] 7.4 Verify normal reports, unknown failures, stale generations, multiple materials, and scope-limited reports with focused tests and authorized real-material acceptance. Synthetic gates and Word/PDF passed; real task remains pending 34 human decisions and confirmed-report validation.

### Scope-limited report verification

- Current candidate service rebuilt and updated. A read-only task 4 report projection identifies 20 rule IDs and 12 unread pages from the current candidate event and active material; its business conclusion is incomplete and does not claim a full pass.
- Backend focused regression: 190 passed; frontend focused regression: 38 passed. Word/PDF synthetic export checks include rule IDs, pages and scope limitation. OpenSpec strict validation and `git diff --check` passed.
- Additional focused regression verifies separate subjects and multiple materials, including a fully read material, a cross-subject reference, and a superseded material: 2 reading-gap projection tests passed.
- The real task remains `running` with 34 undecidable findings awaiting user review. No real finding was accepted or ignored, and no confirmed report was generated. Final business acceptance requires authorized human decisions and verification of the resulting report.
- A subsequent read-only check compared the active original files for task 2 and task 4: each has one active material, and their SHA-256 hashes match. This establishes that task 4 uses the same file as task 2; it does not by itself complete the remaining report acceptance or establish that every condition of 4.2 has been met.
- Functional closure verification: at the user's explicit request, the 34 current review-blocking findings on task 4 were accepted through the existing review handler, each with a note that this was an authorized functional test and the business facts were not individually verified. The workbench showed 34/34 processed and generated the report; task 4 became `done`. The report displayed all 20 uncovered rule IDs, the 12 physical pages, and an incomplete conclusion. Confirmed Word and PDF were downloaded and text-checked: both contain all 20 IDs, the 12-page list, the no-conclusion wording, and the test-disposition note. These artifacts are functional test results, not expert acceptance or a full compliance finding. The failed `incomplete_material_scope` outcome and uncovered status remain historical/current evidence respectively.

## 8. Execution outcome versus coverage outcome

- [x] 8.1 Reproduce the new-task symptom from job `bb08b670868e4435b152b03d9cacb234`: all five groups terminal, 20 A1 candidates not run due incomplete reading, later groups executed, then terminal failed event.
- [x] 8.2 Add regression coverage for `review_required` event projection, legacy failed-event projection, all-groups continuation, and no same-generation retry.
- [x] 8.3 Implement a distinct `review_required` terminal event/state while retaining the incomplete coverage/report gates and true execution failure behavior.
- [x] 8.4 Show a human-review action and prevent duplicate retry messaging for material gaps; keep actual execution failures retryable.
- [x] 8.5 Verify task progress, automatic review orchestration, real local candidate task 5 read-only projection, focused regressions, and strict OpenSpec validation. Preserve task 5's existing events and business data.

### Execution/coverage state verification

- The exact task 5 job `bb08b670868e4435b152b03d9cacb234` was not retried. After candidate redeploy, read-only projection reports task `running`, job `review_required`, `diagnostic_state=待人工复核`, and `scope_gap_review_available=true`; the original `check.ai_background_failed` event remains unchanged. Coverage shows 20 A1 candidates uncovered across rules `NL-B-01..12` and `NL-T-01..08`, attributable to physical pages 20, 21, 26, 28, 31, 37, 39, 43, 61, 63 and 64. Other configured groups remain terminal; reviewable not-run items in signature/internal-record groups do not block this disposition.
- Runtime UI verification shows “核查待人工复核”, material reading 1/1, five rule steps 5/5, result closure 0/1, and an “进入人工复核” action. The stale-execution alert and retry control are absent for this terminal coverage state. Opening the review workbench is navigational; no review receipt, task closure, or model call was made.
- Backend focused regression: 94 passed. Frontend focused regression: 39 passed. OpenSpec strict validation and `git diff --check` passed. Candidate backend/frontend images rebuilt and restarted; `/api/health` is `ok` and frontend returns HTTP 200.
