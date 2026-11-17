import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.plus.blockchain_supervision import ai_advisory_snapshots, negative_candidate_plan, router, service
from app.plus.blockchain_supervision.ai_evidence import validate_ai_proposal
from app.plus.blockchain_supervision import ai_review_batch
from app.plus.blockchain_supervision.ai_review_batch import CHECKPOINT_SCHEMA_VERSION
from app.plus.blockchain_supervision.models import BsFinding
from app.plus.blockchain_supervision import reporting


def _proposal(item_id, quote, *, message="请核对该工程事实和原件"):
    return {
        "item_id": item_id,
        "subject_id": 7,
        "status": "undecidable",
        "message": message,
        "material_evidence": [{"material_id": 3, "page": 1, "quote": quote}],
        "rule_evidence": [{"reference_id": "ref-1", "quote": "本条规则原文及工程条件"}],
    }


def _validate(item_id, quote):
    return validate_ai_proposal(
        _proposal(item_id, quote),
        task_id=1,
        subject_id=7,
        item_id=item_id,
        kind="negative_candidate",
        incomplete_material_scope=True,
        materials=[{"task_id": 1, "subject_id": 7, "material_id": 3, "page": 1, "text": quote}],
        references=[{"reference_id": "ref-1", "text": "本条规则原文及工程条件"}],
    )


def _planner_inputs(text):
    subject = SimpleNamespace(task_id=1, id=7, retired=False, type_confirmed=True,
                              type_conflict=False, subject_type="labor")
    material = SimpleNamespace(
        task_id=1, id=3, subject_id=7, role="labor", parse_state="done",
        pages_json=[{"page": 1, "text": text}],
    )
    run = {
        "task_id": 1,
        "group_id": "a1",
        "state": "completed",
        "run_id": "run-1",
        "review_run_id": "review-1",
        "material_fingerprint": "materials-1",
        "rule_fingerprint": "rules-1",
        "basis_sources": [
            {"kind": "negative_list", "item_id": "NL-B-05", "subject_ids": [7]},
            {"kind": "negative_list", "item_id": "NL-B-06", "subject_ids": [7]},
        ],
    }
    return subject, material, run


@pytest.mark.parametrize("quote", [
    "分包工程范围包括桥面施工，合同价款按工程量结算。",
    "甲方提供机械设备，乙方负责施工管理及工程款使用。",
])
@pytest.mark.parametrize("item_id", ["NL-B-05", "NL-B-06"])
def test_generic_scope_or_payment_quote_cannot_support_a_technical_rule(item_id, quote):
    result = _validate(item_id, quote)

    assert result.accepted is False
    assert "规则" in result.reason or "工程信号" in result.reason


def test_quote_for_one_rule_cannot_be_reused_for_another_rule():
    quote = "本工程现浇梁采用移动模架施工，跨径资料尚未确认。"

    assert _validate("NL-B-06", quote).accepted is True
    assert _validate("NL-B-05", quote).accepted is False


def test_same_quote_may_support_each_rule_when_it_contains_each_rule_signal():
    quote = "本工程现浇梁采用悬臂施工并使用移动模架，具体阈值待核。"

    assert _validate("NL-B-05", quote).accepted is True
    assert _validate("NL-B-06", quote).accepted is True


def test_relevant_trigger_without_threshold_remains_undecidable_for_human_review():
    quote = "本工程采用悬臂施工现浇梁，合同未列明节段重量或悬臂长度。"

    result = _validate("NL-B-05", quote)

    assert result.accepted is True
    assert result.proposal["status"] == "undecidable"


def test_semantic_recall_accepts_readable_scope_quote_without_literal_trigger():
    proposal = _proposal("NL-B-05", "工程范围包括预制梁安装及现场吊装作业，具体工法参数请结合原件核对。")
    result = validate_ai_proposal(
        proposal,
        task_id=1,
        subject_id=7,
        item_id="NL-B-05",
        kind="negative_candidate",
        recall_mode="semantic",
        incomplete_material_scope=False,
        materials=[{"task_id": 1, "subject_id": 7, "material_id": 3, "page": 1,
                    "text": proposal["material_evidence"][0]["quote"]}],
        references=[{"reference_id": "ref-1", "text": "本条规则原文及工程条件"}],
    )

    assert result.accepted is True
    assert result.proposal["advisory_only"] is True


@pytest.mark.parametrize("proposal_status", ["hit", "undecidable"])
def test_valid_negative_candidate_is_manual_prompt_and_does_not_become_machine_blocker(proposal_status):
    candidate_id = "nc-" + "c" * 64
    reviewable = [{
        "candidate_id": candidate_id,
        "proposal": {
            "advisory_only": True,
            "status": proposal_status,
            "subject_id": 7,
            "item_id": "NL-B-05",
            "message": "请人工核对技术参数和原件。",
            "material_evidence": [{"material_id": 3, "page": 1, "quote": "本工程采用悬臂施工现浇梁。"}],
            "rule_evidence": [{"reference_id": "ref-1", "quote": "负面清单条款原文。"}],
        },
        "limitations": [],
    }]

    rows = negative_candidate_plan.make_candidate_review_findings(task_id=1, reviewable=reviewable)

    assert len(rows) == 1
    finding = rows[0][1]
    assert finding.finding_semantics == "manual_prompt"
    assert reporting.report_gate_blocking(finding) is False
    assert finding.status == "undecidable"


def test_unlocated_scope_candidate_remains_a_blocking_machine_gap():
    candidate = {
        "candidate_id": "nc-" + "d" * 64,
        "task_id": 1,
        "run_id": "run-1",
        "review_run_id": "review-1",
        "material_fingerprint": "materials-1",
        "rule_fingerprint": "rules-1",
        "subject_id": 7,
        "item_id": "NL-B-05",
        "contract_ids": [3],
        "reference_ids": ["ref-1"],
        "material_pages": [],
        "unavailable_reason": "contract_scope_unlocated",
        "incomplete_material_scope": True,
    }
    run = {
        "task_id": 1,
        "run_id": "run-1",
        "review_run_id": "review-1",
        "material_fingerprint": "materials-1",
        "rule_fingerprint": "rules-1",
        "references": [{"reference_id": "ref-1", "quote": "负面清单条款原文。"}],
    }
    returned = {**candidate, "execution_status": "not_run", "batches_total": 0,
                "batches_completed": 0, "proposals": []}

    rows = negative_candidate_plan.make_unlocated_scope_findings(
        task_id=1, prepared={"candidates": [candidate]}, result={"candidates": [returned]}, run=run)

    assert len(rows) == 1
    finding = rows[0][1]
    assert finding.finding_semantics is None
    assert reporting.report_gate_blocking(finding) is True


def test_candidate_sync_persists_semantics_and_rejects_semantic_drift():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    reviewable = [{
        "candidate_id": "nc-" + "e" * 64,
        "proposal": {
            "advisory_only": True,
            "status": "undecidable",
            "subject_id": 7,
            "item_id": "NL-B-05",
            "message": "请人工核对技术参数。",
            "material_evidence": [{"material_id": 3, "page": 1, "quote": "本工程采用悬臂施工现浇梁。"}],
            "rule_evidence": [{"reference_id": "ref-1", "quote": "负面清单条款原文。"}],
        },
        "limitations": [],
    }]
    with Session(engine) as db:
        task = SimpleNamespace(id=1)
        material = SimpleNamespace(id=3, task_id=1, subject_id=7,
                                   pages_json=[{"page": 1, "text": "本工程采用悬臂施工现浇梁。"}])
        records = service.sync_candidate_review_records(db, task, reviewable, [], [material])
        db.commit()
        finding = db.get(BsFinding, records[0]["finding_id"])
        assert finding.finding_semantics == "manual_prompt"
        finding.finding_semantics = "machine_conclusion"
        db.flush()
        with pytest.raises(service.AiContextChanged):
            service.sync_candidate_review_records(db, task, reviewable, records, [material])
    engine.dispose()


@pytest.mark.parametrize(("item_id", "quote", "expected"), [
    ("NL-B-11", "本工程上跨既有铁路，现浇梁施工。", True),
    ("NL-B-11", "本工程上跨既有道路，现浇梁施工。", False),
    ("NL-B-11", "本工程上跨既有道路；其他条款提到铁路。", False),
    ("NL-T-06", "V级围岩，开挖断面资料待核。", True),
    ("NL-T-06", "围岩类别待核，开挖断面资料待核。", False),
    ("NL-T-08", "隧道下穿既有铁路，长度资料待核。", True),
    ("NL-T-08", "隧道下穿施工，周边环境待核。", False),
])
def test_rule_context_must_be_present_in_the_cited_quote(item_id, quote, expected):
    assert _validate(item_id, quote).accepted is expected


def test_planner_semantically_screens_generic_scope_excerpt_without_literal_signals(monkeypatch):
    subject, material, run = _planner_inputs(
        "分包工程范围：普通桥面施工。合同价款按工程量结算。"
    )
    monkeypatch.setattr(negative_candidate_plan, "negative_list_rules", lambda: [
        {"id": "NL-B-05", "text": "悬臂施工现浇梁"},
        {"id": "NL-B-06", "text": "移动模架施工现浇梁"},
    ])
    monkeypatch.setattr(negative_candidate_plan, "references_for_source",
                        lambda _run, source: [{"reference_id": "ref-1", "quote": source["item_id"]}])
    monkeypatch.setattr(negative_candidate_plan, "material_page_readable", lambda *_args: True)
    monkeypatch.setattr(negative_candidate_plan, "material_reading_gap", lambda _material: None)
    monkeypatch.setattr(negative_candidate_plan, "reading_coverage", lambda _material: {"reading_complete": True})

    prepared = negative_candidate_plan.prepare_candidate_batch_inputs(
        task_id=1, run=run, subjects=[subject], materials=[material], findings=[],
        current_review_run_id="review-1", current_material_fingerprint="materials-1",
        current_rule_fingerprint="rules-1",
    )

    assert {item["item_id"] for item in prepared["items"]} == {"NL-B-05", "NL-B-06"}
    assert all(item["recall_mode"] == "semantic" for item in prepared["items"])
    assert all(row["unavailable_reason"] is None for row in prepared["candidates"])
    assert all(row["recall_mode"] == "semantic" for row in prepared["candidates"])
    assert all(row["material_pages"] for row in prepared["candidates"])
    assert all(row["source_reading_complete"] is True for row in prepared["candidates"])


@pytest.mark.parametrize("invalid_citation", [None, "page", "material", "reference", "quote"])
def test_semantic_candidate_survives_batch_boundary_with_exact_citations(monkeypatch, invalid_citation):
    quote = "分包工程范围：预制构件安装及现场吊装作业。"
    candidate_id = "nc-" + "e" * 64
    item = {"candidate_id": candidate_id, "task_id": 1, "subject_id": 7,
            "item_id": "NL-B-05", "kind": "negative_candidate", "recall_mode": "semantic",
            "materials": [{"task_id": 1, "subject_id": 7, "material_id": 3, "page": 1, "text": quote}],
            "references": [{"reference_id": "ref-1", "text": "本条规则原文及工程条件"}]}
    plan = ai_review_batch.plan_review_batches([item])
    assert not plan["limitations"]
    assert len(plan["batches"]) == 1
    batch = plan["batches"][0]
    assert batch["items"][0]["recall_mode"] == "semantic"
    proposal = {**_proposal("NL-B-05", quote), "candidate_id": candidate_id}
    if invalid_citation == "page":
        proposal["material_evidence"][0]["page"] = 2
    elif invalid_citation == "material":
        proposal["material_evidence"][0]["material_id"] = 9
    elif invalid_citation == "reference":
        proposal["rule_evidence"][0]["reference_id"] = "other-rule"
    elif invalid_citation == "quote":
        proposal["material_evidence"][0]["quote"] = "本工程采用悬臂施工现浇梁。"

    async def request_review(_client, system, payload, **_kwargs):
        assert "recall_mode=semantic" in system
        assert payload["items"][0]["recall_mode"] == "semantic"
        assert payload["items"][0]["allowed_reference_ids"] == ["ref-1"]
        return {"proposals": [proposal]}

    monkeypatch.setattr(ai_review_batch, "_request_review_json", request_review)
    result = asyncio.run(ai_review_batch.execute_review_batch(client=object(), batch=batch))[0]
    if invalid_citation:
        assert result["execution_status"] == "partial"
        assert result["proposals"] == []
    else:
        assert result["execution_status"] == "completed"
        assert result["proposals"][0]["advisory_only"] is True
        reviewable = negative_candidate_plan.reviewable_candidate_proposals(
            {"items": [item]}, {"candidates": [result]})
        assert len(reviewable) == 1
        assert reviewable[0]["proposal"]["status"] == "undecidable"


def test_no_signal_is_not_safe_to_skip_when_contract_reading_has_gaps(monkeypatch):
    subject, material, run = _planner_inputs("分包工程范围：普通桥面施工。")
    monkeypatch.setattr(negative_candidate_plan, "negative_list_rules", lambda: [
        {"id": "NL-B-05", "text": "悬臂施工现浇梁"},
    ])
    monkeypatch.setattr(negative_candidate_plan, "references_for_source",
                        lambda _run, source: [{"reference_id": "ref-1", "quote": source["item_id"]}])
    monkeypatch.setattr(negative_candidate_plan, "material_page_readable", lambda *_args: True)
    monkeypatch.setattr(negative_candidate_plan, "material_reading_gap", lambda _material: [2])
    monkeypatch.setattr(negative_candidate_plan, "reading_coverage", lambda _material: {"reading_complete": False})

    prepared = negative_candidate_plan.prepare_candidate_batch_inputs(
        task_id=1, run=run, subjects=[subject], materials=[material], findings=[],
        current_review_run_id="review-1", current_material_fingerprint="materials-1",
        current_rule_fingerprint="rules-1",
    )

    assert prepared["candidates"][0]["unavailable_reason"] is None
    assert prepared["candidates"][0]["recall_mode"] == "semantic"
    assert prepared["candidates"][0]["scope_attribution"] == "unresolved"
    assert prepared["candidates"][0]["source_reading_complete"] is False
    assert prepared["items"][0]["incomplete_material_scope"] is True


def test_planner_prefers_deterministic_signal_and_semantically_screens_other_rules(monkeypatch):
    subject, material, run = _planner_inputs(
        "分包工程范围：现浇梁采用移动模架施工，跨径资料待核。"
    )
    monkeypatch.setattr(negative_candidate_plan, "negative_list_rules", lambda: [
        {"id": "NL-B-05", "text": "悬臂施工现浇梁"},
        {"id": "NL-B-06", "text": "移动模架施工现浇梁"},
    ])
    monkeypatch.setattr(negative_candidate_plan, "references_for_source",
                        lambda _run, source: [{"reference_id": "ref-1", "quote": source["item_id"]}])
    monkeypatch.setattr(negative_candidate_plan, "material_page_readable", lambda *_args: True)
    monkeypatch.setattr(negative_candidate_plan, "material_reading_gap", lambda _material: None)
    monkeypatch.setattr(negative_candidate_plan, "reading_coverage", lambda _material: {"reading_complete": True})

    prepared = negative_candidate_plan.prepare_candidate_batch_inputs(
        task_id=1, run=run, subjects=[subject], materials=[material], findings=[],
        current_review_run_id="review-1", current_material_fingerprint="materials-1",
        current_rule_fingerprint="rules-1",
    )

    assert [item["item_id"] for item in prepared["items"]] == ["NL-B-05", "NL-B-06"]
    modes = {item["item_id"]: item["recall_mode"] for item in prepared["items"]}
    assert modes == {"NL-B-05": "semantic", "NL-B-06": "deterministic"}
    candidates = {row["item_id"]: row for row in prepared["candidates"]}
    assert candidates["NL-B-05"]["unavailable_reason"] is None
    assert candidates["NL-B-06"]["unavailable_reason"] is None


def test_invalid_rule_context_fails_closed_without_aborting_candidate_planning(monkeypatch):
    monkeypatch.setattr(negative_candidate_plan, "load_negative_matchers", lambda: {
        "matchers": {"NL-B-11": {"triggers": ["上跨"], "context": "("}},
    })

    assert negative_candidate_plan._has_rule_signal("NL-B-11", "上跨铁路") is False


def test_irrelevant_candidate_proposal_is_rejected_and_batch_stays_partial(monkeypatch):
    quote = "分包工程范围包括桥面施工，合同价款按工程量结算。"
    candidate_id = "nc-" + "a" * 64
    entry = {
        "candidate_id": candidate_id,
        "item_id": "NL-B-05",
        "kind": "negative_candidate",
        "item_label": "悬臂施工现浇梁",
        "subject_id": 7,
        "incomplete_material_scope": True,
        "references": [{"reference_id": "ref-1", "text": "本条规则原文及工程条件"}],
        "limitations": [],
        "material": {"task_id": 1, "subject_id": 7, "material_id": 3,
                     "page": 1, "text": quote, "text_start": 0, "text_end": len(quote)},
    }

    async def request_review(*_args, **_kwargs):
        proposal = _proposal("NL-B-05", quote)
        proposal["candidate_id"] = candidate_id
        return {"proposals": [proposal]}

    monkeypatch.setattr(ai_review_batch, "_request_review_json", request_review)
    result = asyncio.run(ai_review_batch.execute_review_batch(
        client=object(), batch={"task_id": 1, "subject_id": 7, "items": [entry]},
    ))[0]

    assert result["execution_status"] == "partial"
    assert result["proposals"] == []
    assert result["rejection_reasons"]


def test_old_negative_candidate_validation_is_stale_without_invalidating_other_ai_groups():
    candidate_old = SimpleNamespace(event_type="check.negative_candidates_finished", payload_json={
        "validation_version": CHECKPOINT_SCHEMA_VERSION,
    })
    candidate_current = SimpleNamespace(event_type="check.negative_candidates_finished", payload_json={
        "validation_version": negative_candidate_plan.VALIDATION_VERSION,
    })
    ordinary_current = SimpleNamespace(event_type="check.ai_advisory_completed", payload_json={
        "validation_version": CHECKPOINT_SCHEMA_VERSION,
    })
    ordinary_candidate_version = SimpleNamespace(event_type="check.ai_advisory_completed", payload_json={
        "validation_version": negative_candidate_plan.VALIDATION_VERSION,
    })

    assert ai_advisory_snapshots.advisory_validation_current(candidate_old) is False
    assert ai_advisory_snapshots.advisory_validation_current(candidate_current) is True
    assert ai_advisory_snapshots.advisory_validation_current(ordinary_current) is True
    assert ai_advisory_snapshots.advisory_validation_current(ordinary_candidate_version) is False


def test_old_human_review_receipts_are_not_carried_into_new_candidate_generation():
    records = [{"key": "old-key", "finding_id": 19, "rule_evidence": []}]
    stale = SimpleNamespace(event_type="check.negative_candidates_finished", payload_json={
        "validation_version": CHECKPOINT_SCHEMA_VERSION,
        "finding_records": records,
    })
    current = SimpleNamespace(event_type="check.negative_candidates_finished", payload_json={
        "validation_version": negative_candidate_plan.VALIDATION_VERSION,
        "finding_records": records,
    })

    assert service._current_negative_candidate_records(
        stale, negative_candidate_plan.VALIDATION_VERSION) == []
    assert service._current_negative_candidate_records(
        current, negative_candidate_plan.VALIDATION_VERSION) == records


@pytest.mark.parametrize(("reason", "source_reading_complete", "not_assessed", "blocking", "categories"), [
    ("rule_signal_not_found", True, 1, 0, []),
    ("rule_signal_not_found", False, 0, 1, ["incomplete_material_scope"]),
    ("unknown_reason", True, 0, 1, ["incomplete_material_scope"]),
])
def test_not_run_candidate_coverage_uses_reason_disposition(
    reason, source_reading_complete, not_assessed, blocking, categories,
):
    run = {
        "task_id": 1,
        "group_id": "a1",
        "state": "completed",
        "run_id": "run-1",
        "review_run_id": "review-1",
        "material_fingerprint": "materials-1",
        "rule_fingerprint": "rules-1",
        "finding_ids": [],
        "finding_links": [],
    }
    event = SimpleNamespace(event_type="check.negative_candidates_finished", payload_json={
        "run_id": "run-1",
        "validation_version": negative_candidate_plan.VALIDATION_VERSION,
        "result": {"candidates": [{
            "candidate_id": "nc-" + "b" * 64,
            "task_id": 1,
            "run_id": "run-1",
            "review_run_id": "review-1",
            "material_fingerprint": "materials-1",
            "rule_fingerprint": "rules-1",
            "subject_id": 7,
            "item_id": "NL-B-05",
            "execution_status": "not_run",
            "batches_total": 0,
            "batches_completed": 0,
            "unavailable_reason": reason,
            "source_reading_complete": source_reading_complete,
        }]},
    })

    group = ai_advisory_snapshots.summarize_coverage(
        {"ai_auto_enabled": True, "ai_auto_groups": ["a1"]}, [run], [event]
    )["groups"][0]

    assert group["counts"]["not_run"] == 1
    assert group["not_assessed_no_signal"] == not_assessed
    assert group["blocking_not_run"] == blocking
    assert group["reading_gap_not_run"] == int(reason == "rule_signal_not_found" and source_reading_complete is False)
    assert group["failure_categories"] == categories


def test_reading_gap_report_projection_requires_current_attributable_sources():
    from app.plus.blockchain_supervision import reading_gap_scope
    run = {"task_id": 1, "run_id": "run-1", "review_run_id": "review-1",
           "material_fingerprint": "m1", "rule_fingerprint": "r1"}
    row = {**run, "candidate_id": "nc-1", "item_id": "NL-B-01", "subject_id": 2,
           "execution_status": "not_run", "unavailable_reason": "rule_signal_not_found",
           "source_reading_complete": False, "scope_attribution": "attributed", "contract_ids": [7]}
    material = SimpleNamespace(id=7, subject_id=2, task_id=1, file_name="material.pdf", superseded_by_id=None)
    coverage = {7: {"reading_complete": False, "unread_pages": [63, 64], "total_pages": 67}}
    result = reading_gap_scope.project([row], run, [material], coverage)
    assert result == [{"material_id": 7, "file_name": "material.pdf", "pages": [63, 64], "rule_ids": ["NL-B-01"]}]
    assert reading_gap_scope.project([{**row, "material_fingerprint": "stale"}], run, [material], coverage) is None
    assert reading_gap_scope.project([row], run, [material], {7: {"reading_complete": False, "unread_pages": []}}) is None
    assert reading_gap_scope.project([{**row, "subject_id": 3}], run, [material], coverage) is None


def test_reading_gap_projection_does_not_assign_all_rules_when_scope_is_unattributed():
    from app.plus.blockchain_supervision import reading_gap_scope
    run = {"task_id": 1, "run_id": "run-1", "review_run_id": "review-1",
           "material_fingerprint": "m1", "rule_fingerprint": "r1"}
    row = {**run, "candidate_id": "nc-1", "item_id": "NL-B-01", "subject_id": 2,
           "execution_status": "not_run", "unavailable_reason": "rule_signal_not_found",
           "source_reading_complete": False, "scope_attribution": "unresolved", "contract_ids": [7]}
    material = SimpleNamespace(id=7, subject_id=2, task_id=1, file_name="material.pdf", superseded_by_id=None)
    coverage = {7: {"reading_complete": False, "unread_pages": [63, 64], "total_pages": 67}}

    result = reading_gap_scope.project([row], run, [material], coverage)

    assert result == [{"material_id": 7, "file_name": "material.pdf", "pages": [63, 64],
                       "rule_ids": [], "unattributed": True}]
    assert "具体规则归属待人工核对" in reading_gap_scope.statements(result)[0]


def test_reading_gap_projection_separates_materials_and_subjects():
    from app.plus.blockchain_supervision import reading_gap_scope
    run = {"task_id": 1, "run_id": "run-1", "review_run_id": "review-1",
           "material_fingerprint": "m1", "rule_fingerprint": "r1"}
    base = {**run, "execution_status": "not_run", "unavailable_reason": "rule_signal_not_found",
            "source_reading_complete": False, "scope_attribution": "attributed"}
    rows = [{**base, "candidate_id": "nc-1", "item_id": "NL-B-01", "subject_id": 2, "contract_ids": [7, 8]},
            {**base, "candidate_id": "nc-2", "item_id": "NL-T-01", "subject_id": 3, "contract_ids": [9]}]
    materials = [SimpleNamespace(id=i, subject_id=subject, task_id=1, file_name=f"{i}.pdf", superseded_by_id=None)
                 for i, subject in [(7, 2), (8, 2), (9, 3)]]
    coverage = {7: {"reading_complete": False, "unread_pages": [3], "total_pages": 5},
                8: {"reading_complete": True},
                9: {"reading_complete": False, "unread_pages": [10], "total_pages": 10}}
    assert reading_gap_scope.project(rows, run, materials, coverage) == [
        {"material_id": 7, "file_name": "7.pdf", "pages": [3], "rule_ids": ["NL-B-01"]},
        {"material_id": 9, "file_name": "9.pdf", "pages": [10], "rule_ids": ["NL-T-01"]},
    ]
    assert reading_gap_scope.project([{**rows[0], "contract_ids": [7, 9]}], run, materials, coverage) is None
    materials[0].superseded_by_id = 8
    assert reading_gap_scope.project(rows, run, materials, coverage) is None


@pytest.mark.parametrize("reason", [None, "unknown_reason", "reference_missing"])
def test_unrun_candidate_does_not_become_an_ocr_gap_without_reading_reason(reason):
    from app.plus.blockchain_supervision import reading_gap_scope
    candidate = {"execution_status": "not_run", "unavailable_reason": reason,
                 "source_reading_complete": False}
    assert reading_gap_scope.project([candidate], {}, [], {}) == []


def test_reading_gap_disclosure_keeps_unattributed_gaps_when_other_rules_are_known():
    from app.plus.blockchain_supervision import reading_gap_scope
    rows = [{"file_name": "材料.pdf", "pages": [3], "rule_ids": ["NL-B-01"], "unattributed": True}]
    text = "\n".join(reading_gap_scope.statements(rows))
    assert "NL-B-01" in text
    assert "尚未明确具体规则归属" in text


def test_group_status_api_projects_no_signal_and_blocking_counts(monkeypatch):
    run = {"group_id": "a1", "run_id": "run-1", "state": "completed"}
    coverage_row = {"counts": {"not_run": 20}, "skipped": 0, "reviewable_partial": 0,
                    "reviewable_not_run": 0, "not_assessed_no_signal": 20, "blocking_not_run": 0,
                    "unlocated_scope_reviewable": 0, "reviewable_failed": 0, "failure_categories": []}

    class Query:
        def filter(self, *_args):
            return self

        def order_by(self, *_args):
            return self

        def all(self):
            return []

    monkeypatch.setattr(router, "_get_task", lambda *_args: SimpleNamespace(id=1))
    monkeypatch.setattr(router.knowledge_runs, "get_reference_run", lambda *_args, **_kwargs: run)
    monkeypatch.setattr(router, "_advisory_group_ids", lambda _run: [])
    monkeypatch.setattr(router, "assert_ai_context_current", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(router.ai_advisory_snapshots, "timeout_reviewable_ids", lambda *_args: set())
    monkeypatch.setattr(router.ai_advisory_snapshots, "unlocated_scope_reviewable_ids", lambda *_args: set())
    monkeypatch.setattr(router.ai_advisory_snapshots, "summarize_coverage",
                        lambda *_args, **_kwargs: {"groups": [coverage_row]})

    status = router.get_group_ai_advisory_status(
        1, "a1", SimpleNamespace(query=lambda *_args: Query()), user=SimpleNamespace(id=1))

    assert status["not_assessed_no_signal"] == 20
    assert status["blocking_not_run"] == 0
