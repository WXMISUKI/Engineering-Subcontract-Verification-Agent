## Context

The current `build_review_context` is the authoritative place that enforces task/run, subject, material, reference, and readability scope. Task-level A1 findings may be clipped by existing finding/evidence anchors; absent a reliable anchor, the current implementation retains its broader context. Completeness findings use their own checklist keywords. A separate exploration applied `transfer_rules.json` `*_words` terms to A1 inputs, but the T15-05 baseline and candidate suggestions cited disjoint page sets. The selector must therefore represent evidence coverage and fallback explicitly rather than infer semantic equivalence from reduced page count.

Constraints:

- Fixed-rule checks remain on the complete currently available material scope.
- The selector can only narrow the already authorized, readable page set; it cannot add materials, subjects, or unread pages.
- The current partial-scope prompt and validator remain in force. AI advice and citation validation do not become deterministic business conclusions.
- Historical task data, snapshots, reports, and human decisions are immutable under this change.
- The feature remains default-off. Human review is currently paused at the user's request.

## Goals / Non-Goals

**Goals:**

- Reduce MoMA input only where an explicit rule-evidence mapping yields a traceable candidate scope.
- Preserve required evidence categories, material/subject boundaries, page identity, and partial-scope disclosure.
- Make fallback, audit metadata, and checkpoint identity deterministic and testable.

**Non-Goals:**

- Change fixed-rule scope or deterministic findings.
- Select negative-list candidates, completeness, signatures, personnel, or other kinds without a separately approved mapping.
- Add semantic search, external services, OCR, rule conclusions, or new business statuses.
- Automatically enable the selector, rewrite old snapshots, or modify shared/production tasks.

## Decisions

### 1. Keep one scope-selection seam

Place a pure selector behind `build_review_context`; keep authorization, subject isolation, reference linking, and page-readability checks in the existing context builder. Proposed internal interface:

```python
select_ai_scope(finding, eligible_pages, rule_mapping, *, strategy_version) -> ScopeSelection
```

`ScopeSelection` returns the selected subset, partial-scope flag, matched evidence-category identifiers, fallback reason, strategy version, selected/excluded material-subject-page identities, and a fingerprint of the actual input. It contains no page text in its audit fields and performs no database or model side effects.

### 2. Map selectors to required evidence categories

Use each eligible rule's required evidence categories as the coverage contract. Selector metadata references existing rule terms/pattern groups rather than copying their values into Python. Unsupported categories are explicit. A finding is selectable only when every category required for page narrowing is covered by a reliable mapped selector; otherwise the selector returns the existing eligible context with a fallback reason.

Current read-only audit covers 24 catalog rules; the 22 count refers to A1 findings in the historical sample, not catalog size. Eleven rules contain `*_words`; thirteen do not. Of those thirteen, six are pattern-only (`W16-03`, `W16-04`, `W16-05`, `V16-01`, `V16-02`, `L17-02`), `T15-01` has patterns plus `evidence_keywords`, and six have no `*_words`, `*_patterns`, or `evidence_keywords` (`T15-07`, `T15-10`, `W16-02`, `W16-07`, `V16-04`, `L17-03`). Missing `*_words` is not a no-hit result, and field presence does not approve a semantic mapping; unsupported items stay on baseline context.

Selector-field inventory (not yet a semantic category mapping):

- `T15-01`, `T15-02`: `evidence_keywords` groups; `T15-02` also has `partial_scope_words`.
- Mixed `*_words` and `*_patterns`: `T15-03`, `T15-04`, `T15-05`, `T15-06`, `T15-08`, `T15-09`, `W16-01`, `W16-06`, `V16-03`, `L17-01`.
- Pattern-only (no `*_words`): `W16-03`, `W16-04`, `W16-05`, `V16-01`, `V16-02`, `L17-02`. `T15-01` is separately noted above because it also has `evidence_keywords`.
- No selector fields: `T15-07`, `T15-10`, `W16-02`, `W16-07`, `V16-04`, `L17-03`.

The required-evidence category to selector-field mapping remains open for every eligible item. Field presence alone does not prove that a page-selection rule covers the item’s factual requirements.

#### Structured evidence coverage extension

The original lexical selector remains a candidate-page locator, not proof of a rule's evidence coverage. User-selected extension design is in `structured-evidence-design.md`. A rule may become selectable only after a rule-specific adapter has enumerated all required source roles, subjects/objects, relations, polarity/counterevidence, and relevant physical page identities over the full currently eligible input. The adapter first runs as a non-mutating shadow projection. Missing or ambiguous linkage, incomplete enumeration, OCR/readability gaps, unknown relations, or rule-version mismatch must retain the baseline AI context. Existing `BsFinding` primary anchors and prose are not complete evidence graphs. T15-05 is the proposed first shadow adapter because its deterministic kernel exposes distinct side and same-sentence relation checks; this is a design choice, not approval to enable or register the rule.

### Deterministic-kernel constraints found during source review

These implementation contracts constrain any future selector mapping; keyword-field presence alone cannot satisfy them:

- `T15-01` aggregates general scope, subcontract scope, contract parties, and a composite whole-project transfer statement across general/subcontract materials. A range term alone, payment wording, or similar scopes cannot substitute for the composite scope-plus-transfer relation. The current candidate category terms do not prove that relation and cross-material attribution are preserved; keep this item baseline-only until those links are explicit.
- `T15-02` aggregates general scope with at least two subcontract scopes, a section/project anchor, and composite whole-project dismemberment/separate-award wording. Its miss path also depends on general-contract retention evidence plus partial subcontract scope; payment wording cannot substitute for coverage text. Candidate page hits cannot prove all contracts/scopes and anchor coverage remain represented; keep this item baseline-only until cross-material coverage and retention linkage are explicit.
- `T15-03` aggregates general-contract responsibility with procurement/lease evidence, and separately distinguishes third-party procurement from contractor self-procurement and owner-supplied materials. Its four required evidence categories do not encode all of those exception and polarity facts; a category hit alone cannot guarantee that the decisive pages are selected. Explanation text also cannot substitute for an invoice when determining a hit. Keep this item baseline-only until the selector contract explicitly covers these exception facts and the source links.
- `T15-04` requires six evidence sides, social-security proof artifacts (not only contractual mentions), a date period detected in a material containing that proof, and consistent contractor/third-party direction. The word selectors do not guarantee proof form, same-material period linkage, or retention of both sides of a direction conflict. Keep this item baseline-only until those relationships are explicitly represented and tested by the candidate mapping.
- `T15-05` combines labor scope, whole-project scope, management fee, and whole-project payment. Its positive relation patterns require the labor subject and whole-project scope/payment relations in the same sentence; separate side hits are insufficient, and scope-negation conflicts must remain visible. The page selector does not guarantee sentence-level relation or conflict preservation. Keep this item baseline-only until the selector can retain and audit complete relation spans or fall back.
- `T15-06` requires cooperation form, whole-project transfer, actual builder, and construction-management responsibility; cooperation keywords alone are explicitly insufficient, and positive transfer must be reconciled with transfer-negation evidence. Page keyword selection does not guarantee complete attribution across these sides or retention of a conflict elsewhere in the eligible context. Keep this item baseline-only until those links are explicit.
- `T15-07` is a dependency placeholder: the kernel always returns undecidable until W16/V16 exclusion results can be jointly linked to the subcontracting-party identity. It has no executable selector evidence contract and cannot be narrowed by keywords.
- `T15-08` binds fund period and payment evidence to the same material, checks contractor and subcontractor identity on that material, detects incomplete fund tables, and treats affirmative/negative transfer evidence conflict as undecidable. The page selector cannot guarantee same-material period linkage, both party identities, complete-table status, or retention of conflict evidence. Keep this item baseline-only until those links are represented in the selector contract and audited page set.
- `T15-09` requires the same joint-venture party to have non-construction, non-management, and management-fee evidence in one clause, alongside the agreement and work division; evidence of actual work creates a conflict. Page keywords cannot preserve same-clause and same-party attribution or guarantee conflict-page retention. Keep this item baseline-only until clause and party links are explicit in the selector contract.
- `T15-10` is the Article 15 open-term rule and has no deterministic criterion in the current catalog/kernel. It is registered as always undecidable and excluded from hit/miss counts; it has no selector evidence contract and must not be narrowed by generic legal keywords.
- `W16-04` requires a same-clause nominee statement and a comparison showing the contract-signing party differs from the actual implementation party. A subject-mismatch signal without an explicit nominee statement is undecidable. The current selector mapping contract consumes explicit literal evidence-category terms and has no complete reviewed mapping for these pattern-defined, same-clause facts; keep this item baseline-only until pattern-to-category and clause-link preservation are explicit.
- `W16-05` requires the construction-scope clause and construction-payment evidence to belong to the same material and aligned subject/project; positive construction signals must be reconciled with labor-only/negative evidence before a result is returned. Its L17-01/L17-02 overlap is resolved later at the finding-relation layer. Page keyword selection cannot guarantee the same-object linkage, conflict retention, or downstream cross-rule reconciliation; keep this item baseline-only until those contracts are carried through the selected context.
- `W16-06` requires a resubcontract/transfer chain, downstream party, and actual implementation evidence in the same clause. Self-implementation or no-further-subcontract evidence must be evaluated with polarity guards; local design/document duties do not prove project implementation. Page keyword selection cannot guarantee chain/party/clause linkage or preserve distant counterevidence; keep this item baseline-only until these relationships are explicit.
- `W16-03` reads its prohibition only from the general contract and compares it with an actual subcontract for the same review object and scope. Written-consent subject identity and consent/signing dates are extracted from their own clause windows; cross-project consent or post-signing consent is not a compliant basis. Page selection cannot guarantee role-separated sources, scope-type matching, and clause-bound event dates together; keep this item baseline-only.
- `W16-01` combines same-clause subcontract/qualification statements with structured qualification evidence (`business_license`, `qualification`, and `safety_permit`) supplied through a separate evidence channel. Cross-subject qualification, missing structured fields, and dual-negation statements remain undecidable. Text-page selection cannot supply or reliably link those structured facts to the subcontractor; keep this item baseline-only until structured-evidence identity and text-source linkage are explicitly represented.
- `V16-01` has two independent sides: supervision review prefers the A3-01 `RC-09` result, while employer written consent is established from material text. A3 absence/status, text polarity conflicts, mixed risk/legal sides, and cross-project/object mismatch have distinct outcomes. Page keyword selection cannot represent or replace the A3 result and cannot guarantee the two sides refer to the same review object; keep this item baseline-only until the structured dependency and object identity are part of the selector context.
- `V16-02` requires separately sourced general-contract and subcontract comparison evidence. A principle-violation signal can be blocked by an equipment/facility explanation, and simultaneous violation/consistency statements are undecidable. Page keyword selection cannot guarantee the correct contract-role sources remain aligned or retain explanation and counterevidence; keep this item baseline-only until role-specific source linkage and conflict handling are explicit.
- `V16-03` requires subcontract scope plus management-organization, deployed-personnel, and management evidence directed to the subcontractor itself. Negative statements about absent organization/personnel conflict with positive management evidence, and dual negation is undecidable. Page keyword selection cannot guarantee same-party direction or retain a distant conflicting page; keep this item baseline-only until attribution and conflict links are explicit.
- `V16-04` is an open-term rule with no deterministic criterion in the current catalog or kernel; it is always undecidable and excluded from hit/miss counts. It has no evidence-category contract to narrow, so keep it on its existing baseline context and do not infer selectors from general legal text.
- `L17-01` requires a labor subject, fee-taking action, and main/turnover material fee to form a same-clause relation. Bare material-fee terms, especially management-fee percentages, are explicitly excluded; W16-05 overlap is later reconciled only for the same object. Page keyword selection cannot guarantee clause relation, percentage exclusion, or same-object deduplication; keep this item baseline-only until those contracts are explicit.
- `L17-02` uses two evidence channels: responsibility language in labor materials and labor-side signature/compilation evidence in packet materials. The dependency check also reads these materials separately, preserves role/source distinctions, and returns undecidable when both sides are missing. Its overlap with W16-05 is deduplicated only for the same object. Page selection cannot guarantee labor/packet channel coverage or same-object relation reconciliation; keep this item baseline-only until both channels and object identity are represented.
- `L17-03` is an open-term rule without a deterministic criterion and remains undecidable; no keyword selector can be derived from the current contract.
- `W16-01` combines page text with structured qualification evidence (`qe`) and clause polarity guards. Selecting OCR pages alone does not cover license/safety-permit status; if structured evidence cannot be joined to exact source pages in the selected input, baseline fallback is required.
- `V16-03` requires subcontract scope, management organization, deployed personnel, and management evidence, while checking negative/affirmative and subject-direction conflicts. Missing any required side prevents selection; identified conflict pages must be retained or cause fallback.
- `L17-02` distinguishes contractor versus labor responsibility across plan/testing/survey/archive/quality-safety dimensions. Each required owner dimension needs a reviewed selector mapping; a general “方案/管理” term is not sufficient.

These findings do not approve any item for candidate selection. The first implementation remains baseline-only for every item until its category mapping and source linkage are explicitly reviewed. In particular, `T15-01/02` keyword groups are useful inventory data but do not prove complete cross-material aggregation coverage.

Candidate inventory below is a source-field cross-reference for review only. It is not an approved semantic mapping. “Uncovered” means no current selector field directly names that required category; inferred matches must be checked against the full rule implementation before selector eligibility is granted.

| Item | Required evidence categories | Existing candidate selector fields | Uncovered / disposition |
| --- | --- | --- | --- |
| T15-01 | general_scope; sub_scope; contract_parties; whole_project_transfer | `evidence_keywords` contains all four named groups; `retention_patterns` | Baseline-only: category presence does not prove composite scope-plus-transfer relation or cross-material subject attribution |
| T15-02 | general_scope; subcontract_scopes; section_anchor; scope_coverage | `evidence_keywords` contains all four groups; `partial_scope_words`; `retention_patterns` | Baseline-only: requires at least two subcontract scopes, section/project anchor, composite coverage wording, and general-retention/partial-scope evidence for miss; payment terms are not coverage |
| T15-03 | procurement_responsibility; procurement_or_lease_contract; invoice_or_explanation; supplier_subject | `procurement_responsibility_patterns`; procurement-contract, invoice, supplier-subject, explanation word groups; self/third-party procurement patterns | Baseline-only: the four categories omit the full self-procurement, third-party polarity, owner-supplied-material exception and invoice-vs-explanation decision contract; current kernel tests cover these branches |
| T15-04 | site_management_org; deployed_personnel; manager_roster; labor_contract; social_security_or_wage_relation; project_manager_management | Site-org, deployed-personnel, roster, labor-contract, social-security, project-manager word groups; direction and proof patterns | Baseline-only: selectors do not guarantee social-security proof form, period on that proof material, or retention of conflicting directions; OCR/roster pages may separate linked facts |
| T15-05 | labor_scope; whole_project_scope; management_fee; whole_project_payment | labor-subject and management-fee words; labor/whole-scope/payment and negation patterns | Baseline-only: word/page hits do not preserve same-sentence labor-to-scope/payment relations or scope-negation conflicts; whole-project scope and payment also lack direct `*_words` groups |
| T15-06 | cooperation_form; actual_builder; whole_project_transfer; management_responsibility | cooperation-form and management-responsibility words; actual-builder, transfer, negation patterns | Baseline-only: cooperation terms alone are insufficient; complete transfer, actual builder, management direction, and transfer-negation conflicts must remain linked |
| T15-07 | employer_subject; contractor_subject; subcontractor_subject; subcontract_authority; illegal_subcontract_exclusion | None | Dependency placeholder; always undecidable until W16/V16 exclusions and party identity can be joined, no selector contract, and no candidate narrowing |
| T15-08 | payment_relationship; management_fee_deduction; pass_through_payment; fund_flow_statement; coverage_period | contractor/subcontractor, deduction, fund-flow, payment-relationship word groups; pass-through, payee, period-exclusion, incomplete-table patterns | Baseline-only: same-material period and party linkage, incomplete-table evidence, and positive/negative transfer conflicts are not guaranteed by page keyword hits |
| T15-09 | joint_venture_agreement; joint_work_division; actual_construction; actual_management; management_fee | agreement, division, management-fee words; actual-work, fee-only, no-construction, no-management patterns | Baseline-only: the same party's three decisive facts must occur in one clause, and actual-work conflicts must remain visible |
| T15-10 | additional_rule_text; deterministic_criterion | None; open-term rule | Open transfer term with no deterministic criterion; always undecidable, no selector contract, and no candidate narrowing |
| W16-01 | subcontractor_subject; qualification; license; safety_permit; required_condition | qualification-subject words; dual-negation, individual-award, no-qualification, subcontract-relation patterns | Baseline-only: three required qualification facts arrive through structured evidence, while page selectors cannot establish their subject linkage; cross-subject and dual-negation cases remain undecidable |
| W16-02 | subcontract_scope; negative_list_match; threshold_values | None | Baseline fallback; negative-list selection is excluded from this change |
| W16-03 | no_subcontract_clause; actual_subcontract; project_scope; employer_consent; consent_date; subcontract_signing_date | no-subcontract clauses, subcontract relation, consent and date patterns | Baseline-only: general-contract/subcontract roles, same-object scope comparison, and clause-bound consent/signing dates must remain linked; cross-project consent and post-signing consent are not compliant evidence |
| W16-04 | subcontractor_identity; named_subject; contract_and_material_subjects | nominee, subject-match, subject-mismatch patterns | Baseline-only: current mapping contract lacks a complete reviewed pattern-to-category mapping; same-clause nominee and signing/implementation subject comparison must both be preserved |
| W16-05 | labor_contract; construction_scope; labor_payment_terms; l17_related_evidence | construction-payment, construction-scope, labor-contract, labor-only-scope, labor-payment-term, labor-subject patterns | Baseline-only: construction scope and payment must align to the same material/object; conflicts must be checked first, and L17 overlap requires downstream relation reconciliation |
| W16-06 | contract_chain; downstream_subject; actual_implementation | downstream-subject words; implementation, resubcontract, self-implementation patterns | Baseline-only: resubcontract chain, downstream party, and actual implementation must be same-clause linked; self-implementation and polarity evidence must be retained |
| W16-07 | additional_rule_text; deterministic_criterion | None; open-term rule | Baseline fallback; no selector until a deterministic criterion is approved |
| V16-01 | supervision_review; employer_written_consent; consent_date | consent-ok/risk and supervision-ok/risk patterns | Baseline-only: supervision side is an A3 RC-09 dependency, consent is text evidence, and both sides require same-review-object identity with mixed/conflicting states preserved |
| V16-02 | subcontract_scope; negative_list_match; threshold_values | None | Baseline-only: general and subcontract contracts are distinct required sources; explanation evidence blocks a hit and simultaneous consistency/violation evidence is undecidable |
| V16-03 | site_management_org; deployed_personnel; subcontract_scope; management_evidence | deployed, management-evidence, management-org words; conflict, negation, direction, scope patterns | Baseline-only: all positive sides must point to the subcontractor itself; negative/positive management conflicts and dual-negation cases cannot be guaranteed by page selection |
| V16-04 | additional_rule_text; deterministic_criterion | None; open-term rule | Open term with no deterministic criterion; always undecidable, no selector contract, and no candidate narrowing |
| L17-01 | labor_contract; labor_fee; main_material_fee; main_turnover_material_fee | labor-contract words; hit-relation, labor-fee-only, labor-subject, percentage-exclusion patterns | Baseline-only: same-clause subject/action/material-fee relation, management-fee percentage exclusion, and same-object W16-05 relation reconciliation are required |
| L17-02 | technical_quality_safety_equipment_guidance; construction_plan_owner; testing_owner; survey_owner; archive_owner; quality_safety_owner | Contractor/labor responsibility and labor-sign patterns | Baseline-only: labor responsibility and packet signature are independent channels; same-object W16-05 deduplication must remain downstream |
| L17-03 | additional_rule_text; deterministic_criterion | None; open-term rule | Open term with no deterministic criterion; always undecidable, no selector contract, and no candidate narrowing |

This table is intentionally conservative: unresolved categories prevent candidate selection for that rule. The implementation task must first replace this inventory with a reviewed mapping or mark the item baseline-only; it must not treat a non-empty field list as sufficient coverage.

No item ID is approved for candidate selection at this stage. The integration accepts only explicitly mapped `transfer_scope` and `illegal_subcontract` item IDs, but the current mapping registry is intentionally empty. `negative_list` remains on the baseline path because its candidate rules and evidence contract differ.

### 3. Preserve local context without inventing completeness

Begin with current anchor pages and rule-evidence hits. Within each material and subject, retain the complete readable page interval between evidence-category hits plus bounded adjacent pages, so a long relation or table continuation between known evidence endpoints is not split. This does not discover an unmarked continuation after the last hit; mappings that require such linkage remain baseline-only. If the existing context has OCR/readability gaps or the deterministic finding is undecidable, retain the existing context instead of narrowing. Any reduced selection is marked incomplete, even when all configured keyword categories matched.

Unread/suspect pages remain excluded according to current readability policy. Their absence never counts as a selector match. Existing OCR/readability gaps, undecidable findings, ambiguous attribution, missing category mappings, empty results, or selector errors trigger fallback to the existing eligible context. If that existing context is itself incomplete, its existing limitation remains true. The selector does not claim to detect arbitrary semantic conflicts in pages it has not read; an unrecognized conflict remains a residual risk and cannot be treated as resolved by selection.

Maintained synthetic tests cover multiple materials/subjects, a long cross-page evidence span, table continuation between category hits, unreadable-page fallback, and an undecidable finding fallback. They validate the selector contract, not real-material recall or expert business correctness.

### 4. Isolate AI snapshots and audit metadata

`initialize_reference_review` is the current creation point for `review_run_id` and the immutable review seed; it already freezes rule/material/basis inputs and AI auto-review groups. Resolve the approved default-off selector setting at this creation point and freeze `scope_strategy` plus its version into that review seed. Do not read a mutable environment flag per batch: changing configuration mid-run must not change the strategy of an existing review run. A newly created review run may resolve the new setting.

The existing seed field `ai_auto_enabled` controls whether automatic AI review runs. It is not the selector switch and SHALL NOT be reused: “run AI advice” and “narrow AI input” are separate behaviors. The selector setting must have its own explicit field/configuration and default-off semantics.

The current checkpoint batch key hashes `checkpoint_namespace`, validation version, and serialized batch; service callers already provide model/client identity in the namespace. Extend the namespace with the frozen scope strategy/version and ensure the serialized batch contains the actual selected page identities and text, so candidate and baseline results cannot cross-reuse. Also include the selected-input fingerprint in advisory event/checkpoint metadata for audit and mismatch diagnosis.

Store selected/excluded material IDs and page numbers, matched category IDs, fallback reason, scope flag, counts, strategy version, and selected-input fingerprint in current advisory result events. The same fingerprint participates in checkpoint identity but is excluded from the model wire request. Do not log OCR text, prompts, model responses, or credentials. Preserve compatibility when reading old review seeds that lack scope metadata by treating them as the baseline strategy; never synthesize candidate metadata for historical runs.

### 5. Preserve the baseline by default

The candidate strategy is enabled only by the approved server-side feature flag `BS_MOMA_SCOPE_SELECTOR_ENABLED`, whose default is off. Its resolved value is frozen in each review seed when the review generation is created. Rollback disables candidate selection for newly created review runs and returns to the current context-builder behavior. Existing review runs keep their frozen strategy; old events, checkpoints, reports, and human decisions are not rewritten. Checkpoint versioning prevents cross-strategy reuse.

## Risks / Trade-offs

- Incomplete category mappings can silently hide facts. Mitigation: fail back to baseline when any required category lacks a reliable selector; test mapping completeness.
- One-page adjacency misses longer evidence chains. Mitigation: include only structurally reliable continuation logic; otherwise fall back, and test multi-page/table cases.
- Similar terms across subjects or materials can misattribute evidence. Mitigation: operate only within the existing eligible page set and retain material/subject identity on every selected page.
- Candidate outputs vary with model nondeterminism. Mitigation: compare same snapshot, model, prompt, and strategy repeatedly where budget allows; never treat one paired result as accuracy proof.
- Full fallback can erase cost savings. This is an intentional safety trade-off; report fallback frequency and input volume before proposing wider enablement.
- The current running acceptance container is bound to a different model and shared writable resources. Mitigation: use a newly verified isolated SQLite and upload environment for acceptance.

## Migration Plan

No schema migration is planned. Implement under a disabled setting, add a distinct checkpoint strategy identity, and validate against an isolated snapshot. Rollback consists of disabling the setting and using the baseline context path. Any unavoidable schema or deployment change requires a separate approval and plan.

## Open Questions

- Which exact rule item IDs and evidence categories have sufficiently reliable selectors for a first implementation? Current decision: none until deterministic-kernel and source-linkage review is complete.
- What page structures are available to prove table continuation or cross-page linkage, and when must the selector fall back?
- Which event/report fields already expose scope limitations, and is any new user-visible field necessary?
- What isolated sample/call budget and business acceptance criteria should be approved before implementation?
- How should default-off be controlled and frozen for reproducibility? Resolved: server-side feature flag default off, with the resolved strategy/version frozen into each AI review-run snapshot. A task-level user setting is out of scope.
- The user has asked to defer blind review for now; no expert acceptance is claimed.
