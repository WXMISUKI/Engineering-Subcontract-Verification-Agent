"""AI 对无法核实项的结构化判定与落地契约。"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.plus.blockchain_supervision import service, knowledge_runs
from app.plus.blockchain_supervision.ai_evidence import validate_ai_proposal
from app.plus.blockchain_supervision.models import BsFinding, BsTask


def _proposal(status="miss", *, item_id="RC-A01", message="已核对材料，符合要求"):
    return {
        "item_id": item_id,
        "subject_id": 1,
        "status": status,
        "message": message,
        "material_evidence": [{"material_id": 1, "page": 1, "quote": "合同范围包括预制板梁"}],
        "rule_evidence": [{"reference_id": "R1", "quote": "分包范围应当明确"}],
    }


def _materials():
    return [{"task_id": 1, "subject_id": 1, "material_id": 1, "page": 1,
             "text": "合同范围包括预制板梁"}]


def _references():
    return [{"reference_id": "R1", "text": "分包范围应当明确"}]


def test_ai_miss_is_allowed_only_with_complete_non_absence_scope():
    ok = validate_ai_proposal(_proposal(), task_id=1, subject_id=1, item_id="RC-A01",
                              materials=_materials(), references=_references(),
                              kind="qualification_match", incomplete_material_scope=False)
    assert ok.accepted is True
    assert ok.proposal["status"] == "miss"

    incomplete = validate_ai_proposal(_proposal(), task_id=1, subject_id=1, item_id="RC-A01",
                                      materials=_materials(), references=_references(),
                                      kind="qualification_match", incomplete_material_scope=True)
    assert incomplete.accepted is False

    completeness = validate_ai_proposal(_proposal(item_id="RC-A02"), task_id=1, subject_id=1,
                                       item_id="RC-A02", materials=_materials(), references=_references(),
                                       kind="completeness", incomplete_material_scope=False)
    assert completeness.accepted is False


def test_ai_miss_updates_finding_and_keeps_audit():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(BsTask(id=1, conversation_id=1, title="ai", created_by=1, created_by_type="web"))
        finding = BsFinding(task_id=1, item_id="RC-A01", kind="qualification_match",
                            status="undecidable", message="证据不足")
        db.add(finding)
        db.commit()

        changed = service._apply_ai_adjudication(
            db, 1, {"finding_id": finding.id, "execution_status": "completed",
                    "proposals": [_proposal()]},
            model_code="model-x", run_id="run-1", request_id="req-1")
        db.commit()
        db.refresh(finding)
        assert changed is True
        assert finding.status == "miss"
        assert finding.message == "已核对材料，符合要求"
        assert finding.ai_adjudication_json["status"] == "miss"
        assert finding.ai_adjudication_json["previous_status"] == "undecidable"
        assert finding.ai_adjudication_json["run_id"] == "run-1"


def test_ai_does_not_overwrite_human_review():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(BsTask(id=1, conversation_id=1, title="ai", created_by=1, created_by_type="web"))
        finding = BsFinding(task_id=1, item_id="RC-A01", kind="qualification_match",
                            status="undecidable", message="证据不足",
                            review_json={"decision": "accepted", "note": "人工已确认"})
        db.add(finding)
        db.commit()
        changed = service._apply_ai_adjudication(
            db, 1, {"finding_id": finding.id, "execution_status": "completed",
                    "proposals": [_proposal()]},
            model_code="model-x", run_id="run-1", request_id="req-1")
        db.commit()
        db.refresh(finding)
        assert changed is False
        assert finding.status == "undecidable"
        assert finding.review_json["decision"] == "accepted"


@pytest.mark.parametrize("status", ["undecidable", "hit", "miss"])
def test_ai_projection_preserves_input_and_upstream_fingerprints(status):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(BsTask(id=1, conversation_id=1, title="ai", created_by=1, created_by_type="web"))
        finding = BsFinding(task_id=1, item_id="RC-A01", kind="completeness",
                            status="undecidable", message="证据不足")
        db.add(finding)
        db.commit()
        baseline = knowledge_runs.finding_fingerprint([finding])
        dependency = knowledge_runs.dependency_fingerprint(db, "a1", 1)
        for attempt in range(2):
            service._apply_ai_adjudication(
                db, 1, {"finding_id": finding.id, "execution_status": "completed",
                        "proposals": [_proposal(status)]},
                model_code="model-x", run_id="run-1", request_id=str(attempt))
            db.commit()
            assert knowledge_runs.finding_fingerprint([finding]) == baseline
            assert knowledge_runs.dependency_fingerprint(db, "a1", 1) == dependency
        finding.message = "外部修改"
        db.flush()
        assert knowledge_runs.finding_fingerprint([finding]) != baseline
        assert knowledge_runs.dependency_fingerprint(db, "a1", 1) != dependency
        db.rollback()
        finding.item_id = "RC-A02"
        assert knowledge_runs.finding_fingerprint([finding]) != baseline


def test_legacy_undecidable_is_recoverable_but_legacy_hit_is_not():
    finding = BsFinding(id=1, task_id=1, item_id="RC-A01", kind="completeness",
                        status="undecidable", message="证据不足")
    baseline = knowledge_runs.finding_fingerprint([finding])
    finding.ai_adjudication_json = {"status": "undecidable"}
    assert knowledge_runs.finding_fingerprint([finding]) == baseline
    finding.status = "miss"
    prior_miss = knowledge_runs.finding_fingerprint([finding])
    finding.ai_adjudication_json = None
    baseline_miss = knowledge_runs.finding_fingerprint([finding])
    finding.ai_adjudication_json = {"status": "undecidable", "previous_status": "miss"}
    assert knowledge_runs.finding_fingerprint([finding]) == baseline_miss
    assert prior_miss != baseline_miss
    finding.ai_adjudication_json = {"status": "hit"}
    finding.status = "hit"
    assert knowledge_runs.finding_fingerprint([finding]) != baseline


def test_legacy_hit_cannot_be_rewritten_as_a_verified_input():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(BsTask(id=1, conversation_id=1, title="ai", created_by=1, created_by_type="web"))
        finding = BsFinding(task_id=1, item_id="RC-A01", kind="completeness", status="hit",
                            message="旧判定", ai_adjudication_json={"status": "hit"})
        db.add(finding)
        db.commit()
        with pytest.raises(ValueError, match="原始核查结论无法核实"):
            service._apply_ai_adjudication(
                db, 1, {"finding_id": finding.id, "execution_status": "completed",
                        "proposals": [_proposal("miss")]},
                model_code="model-x", run_id="run-1", request_id="req-1")
        assert finding.status == "hit"
        assert finding.ai_adjudication_json == {"status": "hit"}
