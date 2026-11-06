# Supplement Evidence Linkage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. This session uses native execution because the stages share the existing task, material, finding, and review-version interfaces.

**Goal:** Make supplementary materials auditable evidence for the affected subcontract review findings without overwriting historical results.

**Architecture:** Add a normalized material-fact layer beside `BsMaterial`, derive personnel/certificate relations from facts and existing page text, project relation evidence into `BsFinding.evidence_json`, and keep the existing online OCR, generation, and human-review gates. The first increment is storage and extraction only; later increments consume its stable interfaces.

**Tech Stack:** Python, SQLAlchemy, FastAPI, pytest, existing online OCR/page JSON, React/Vite for review UI.

**Spec:** `docs/design/2026-11-06-supplement-evidence-linkage-design.md` and `openspec/changes/bs-supplement-evidence-linkage/specs/evidence-linkage/spec.md`.

## Global Constraints

- Do not rerun OCR automatically for pages already marked quality-suspect.
- Preserve original materials, AI predictions, historical findings, and human decisions.
- Do not write task attachments into the shared knowledge base.
- A missing or unreadable critical field is `unresolved`; never infer identity from filename or same name alone.
- Production services and existing user tasks remain untouched; use isolated tests and copied data.

## Review Focus

- Obscured name/company: facts are stored as missing and the relation remains unresolved.
- Certificate type B versus C: readable mismatch is contradiction; unreadable type is unresolved.
- Full-width spaces/parentheses in certificate numbers: normalization is reversible and does not change digits.
- Duplicate supplement upload: same hash is idempotent and does not duplicate facts.
- Legacy material without facts: remains readable and is never backfilled with guessed facts.

---

### Task 1: Persist material facts and extraction provenance

**Files:**
- Create: `backend/app/plus/blockchain_supervision/material_facts.py`
- Modify: `backend/app/plus/blockchain_supervision/models.py`
- Test: `backend/tests/test_bs_material_facts.py`

**Interfaces:**
- Produces `MaterialFact` ORM rows and `extract_material_facts(material) -> list[dict]`.
- Each fact contains `field_path`, `raw_value`, `normalized_value`, `page`, `quote`, `quality`, `confidence`, `source_kind`, and `extractor_version`.

- [ ] Write tests for field normalization, missing critical fields, page grounding, duplicate facts, and legacy materials.
- [ ] Run the focused tests and verify they fail because the fact model/extractor is absent.
- [ ] Add the nullable fact table and deterministic extraction/provenance helpers without changing existing material rows.
- [ ] Run focused tests and the model schema smoke; verify SQLite table creation and JSON serialization.

### Task 2: Persist facts when a material reaches parse complete

**Files:**
- Modify: `backend/app/plus/blockchain_supervision/service.py`
- Modify: `backend/app/plus/blockchain_supervision/schema.py`
- Test: `backend/tests/test_bs_material_facts.py`

- [ ] Add a failing test that uploads a synthetic parsed material and expects facts tied to its material revision and page.
- [ ] Wire fact extraction after successful online OCR ingestion, preserving parse failures and quality flags.
- [ ] Verify failed/quality-suspect pages create reviewable facts but never trigger another OCR call.

### Task 3: Build personnel/certificate relation candidates

**Files:**
- Create: `backend/app/plus/blockchain_supervision/evidence_relations.py`
- Modify: `backend/app/plus/blockchain_supervision/personnel_evidence.py`
- Test: `backend/tests/test_bs_evidence_relations.py`

- [ ] Add failing tests for matched, conflict, unresolved, same-name ambiguity, and certificate-type mismatch.
- [ ] Implement deterministic comparison over facts and existing roster extraction; retain both side references.
- [ ] Verify filename and same-name-only cases never become matched.

### Task 4: Project relation evidence into A2 findings

**Files:**
- Modify: `backend/app/plus/blockchain_supervision/service.py`
- Modify: `backend/app/plus/blockchain_supervision/certificate_number_check.py`
- Modify: `backend/app/plus/blockchain_supervision/a3_check.py`
- Test: `backend/tests/test_bs_evidence_relations.py`, `backend/tests/test_standalone_workflow.py`

- [ ] Add a failing integration test where a supplement changes A2-05 and the finding includes original and supplement pages.
- [ ] Replace generic certificate-only messaging with relation-scoped support/conflict/unresolved evidence.
- [ ] Preserve unaffected findings and historical snapshots.

### Task 5: Expose evidence in review UI and report gates

**Files:**
- Modify: `frontend/src/plus-ui/blockchain-supervision/components/FindingDetail.jsx`
- Modify: `frontend/src/plus-ui/blockchain-supervision/BlockchainSupervisionWorkbench.jsx`
- Modify: `backend/app/plus/blockchain_supervision/router.py`
- Test: `frontend/tests/PreviousSupplementReview.test.jsx`, new focused UI test

- [ ] Add a failing UI test for original evidence, supplement evidence, relation status, and missing-field reason.
- [ ] Add read-only evidence groups and preserve source-page navigation constraints.
- [ ] Verify unresolved critical relations continue to block confirmed reports.

### Task 6: Isolated negative and positive acceptance

**Files:**
- Modify: `openspec/changes/bs-supplement-evidence-linkage/tasks.md`
- Modify: `openspec/changes/bs-supplement-evidence-linkage/verification.md`

- [ ] Run the simulated obscured/wrong certificate as a negative supplement and capture the exact finding evidence.
- [ ] Run a same-subject clear certificate as a positive supplement; if unavailable, record the missing acceptance input without claiming completion.
- [ ] Run focused and full regression, OpenSpec strict validation, and `git diff --check`.
- [ ] Stop isolated containers and report the stage gate; wait for user confirmation before the next stage.
