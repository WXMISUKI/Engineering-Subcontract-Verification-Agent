"""人工提示为独立语义；历史和技术门禁不因展示变化而放行。"""
from copy import deepcopy

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, inspect, text

from app.plus.blockchain_supervision import reporting, service
from app.plus.blockchain_supervision.models import BsFinding, BsMaterial, BsTask
from app.plus.blockchain_supervision.router import _finding_dto, _require_no_pending_frozen_findings
from app.standalone.schema_migrations import ensure_standalone_schema


@pytest.mark.parametrize("semantics,blocked,label", [
    (None, True, "无法核实"), ("unknown_future_value", True, "无法核实"),
    ("machine_conclusion", True, "无法核实"), ("manual_prompt", False, "人工提示"),
])
def test_explicit_semantics_only_changes_prompt_gate_and_label(semantics, blocked, label):
    finding = BsFinding(item_id="V16-01", kind="illegal_subcontract", status="undecidable",
                        message="缺少系统外事实", finding_semantics=semantics)
    dto = _finding_dto(finding)
    assert dto["finding_semantics"] == semantics
    assert dto["status"] == "undecidable"
    assert dto["status_label"] == dto["decision_trace"]["status_label"] == label
    assert reporting.report_gate_blocking(finding) is blocked


def test_migration_is_idempotent_and_does_not_infer_history():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE bs_finding (id INTEGER PRIMARY KEY, status VARCHAR(20))"))
        connection.execute(text("INSERT INTO bs_finding VALUES (1, 'undecidable')"))
    ensure_standalone_schema(engine)
    ensure_standalone_schema(engine)
    columns = {column["name"]: column for column in inspect(engine).get_columns("bs_finding")}
    assert columns["finding_semantics"]["nullable"] is True
    with engine.connect() as connection:
        assert connection.execute(text("SELECT status, finding_semantics FROM bs_finding")).one() == ("undecidable", None)
    engine.dispose()


@pytest.mark.parametrize("semantics,blocked", [(None, True), ("unknown", True), ("manual_prompt", False)])
def test_frozen_gate_uses_only_frozen_semantics(semantics, blocked):
    row = {"finding_id": 1, "item_id": "V16-01", "status": "undecidable", "review": None}
    if semantics is not None:
        row["finding_semantics"] = semantics
    snapshot = {"findings": [row]}
    original = deepcopy(snapshot)
    if blocked:
        with pytest.raises(HTTPException) as exc:
            _require_no_pending_frozen_findings(snapshot, "confirmed")
        assert exc.value.detail["code"] == "pending_undecidable_review"
    else:
        _require_no_pending_frozen_findings(snapshot, "confirmed")
    assert snapshot == original


def test_semantics_change_invalidates_review_without_losing_previous():
    finding = BsFinding(item_id="V16-01", status="undecidable", message="待核对",
                        finding_semantics="manual_prompt")
    old = {field: getattr(finding, field) for field in service._CARRY_KEY_FIELDS + service._CARRY_CONCLUSION_FIELDS}
    old.update(finding_semantics="machine_conclusion", review_json={"decision": "accepted", "note": "旧意见"})
    review = service._inherit_review(finding, service._carry_index([old]))
    assert review["invalidated"]["previous"] == old["review_json"]
    old["finding_semantics"] = "manual_prompt"
    inherited = service._inherit_review(finding, service._carry_index([old]))
    assert inherited["decision"] == old["review_json"]["decision"]
    assert inherited["note"] == old["review_json"]["note"]
    assert inherited["carried"] is True
    assert inherited["carried_at"]


def _seed(db):
    task = BsTask(conversation_id=101, title="提示语义隔离任务", created_by=1,
                  created_by_type="tester", state="running", scope_a1=False, scope_a3=False)
    db.add(task)
    db.flush()
    prompt = BsFinding(task_id=task.id, item_id="V16-01", kind="illegal_subcontract",
                       status="undecidable", message="请人工核对实际履行",
                       finding_semantics="manual_prompt")
    finding = BsFinding(task_id=task.id, item_id="A2-01", kind="person_consistency",
                        status="miss", message="机器已核对", finding_semantics="machine_conclusion")
    material = BsMaterial(task_id=task.id, file_name="合成材料.txt", role="packet", parse_state="done",
                          pages=1, pages_json=[{"page": 1, "text": "这是完整可读的合成材料正文，仅供隔离验证人工提示与机器结论的独立语义。"}])
    db.add_all([prompt, finding, material])
    db.flush()
    service.initialize_reference_review(db, task)
    db.commit()
    return task, prompt, finding, material


def test_roundtrip_report_and_freeze_keep_prompt_separate(db_session):
    db = db_session
    task, prompt, finding, _ = _seed(db)
    db.expire_all()
    assert db.get(BsFinding, prompt.id).finding_semantics == "manual_prompt"
    report = reporting.build_report(db, task)
    assert report["summary"]["counts"] == {"hit": 0, "miss": 1, "undecidable": 0}
    assert report["summary"]["manual_prompt"]["count"] == 1
    item = next(item for section in report["sections"] for item in section["items"] if item["finding_id"] == prompt.id)
    assert item["status"] == "undecidable" and item["status_label"] == "人工提示"
    assert item["finding_semantics"] == "manual_prompt" and not item["report_review_blocking"]
    snapshot = service._review_version_snapshot(db, task, trigger="completed")
    frozen = next(row for row in snapshot["findings"] if row["finding_id"] == prompt.id)
    assert frozen["finding_semantics"] == "manual_prompt"
    prompt.finding_semantics = "machine_conclusion"
    assert frozen["finding_semantics"] == "manual_prompt"
    _require_no_pending_frozen_findings(snapshot, "confirmed")


@pytest.mark.parametrize("gap", [None, "reading", "execution", "identity"])
def test_prompt_keeps_execution_identity_gates_and_nonblocking_reading_warning(db_session, monkeypatch, gap):
    db = db_session
    task, prompt, finding, material = _seed(db)
    if gap == "reading":
        material.degradations_json = [{"kind": "parse_incomplete", "total_pages": 2, "read_pages": [1], "unread_pages": [2]}]
        db.flush()
        service.initialize_reference_review(db, task)
    elif gap == "execution":
        monkeypatch.setattr(service, "ai_output_readiness", lambda *_: {
            "ok": False, "code": "ai_execution_incomplete", "detail": "必需核查失败"})
    elif gap == "identity":
        finding.status = "undecidable"
        finding.message = "身份冲突"
    db.commit()
    dto = service.build_report_readiness(db, task)
    assert dto["confirmed_output_ready"] is dto["task_close_ready"] is (gap in {None, "reading"})
    if gap == "reading":
        assert dto["reading_quality"]["state"] == "pending"
        assert dto["warnings"][0]["code"] == "reading_quality_limited"
    assert all(row["finding_id"] != prompt.id for row in dto["finding_review"]["blocking"])


def test_output_normalization_never_infers_legacy_semantics():
    legacy = {"status": "undecidable", "message": "普通人工提示", "item_id": "V16-01"}
    explicit = {**legacy, "finding_semantics": "manual_prompt"}
    payload = {"sections": [{"items": [legacy, explicit]}]}
    reporting.normalize_output_payload(payload, "report")
    assert legacy["status_label"] == "无法核实" and "finding_semantics" not in legacy
    assert explicit["status_label"] == "人工提示"


def test_snapshot_saves_semantics_independently_of_report_counts(db_session):
    task, prompt, _, _ = _seed(db_session)
    snapshot = service._review_version_snapshot(db_session, task, trigger="completed")
    row = next(row for row in snapshot["findings"] if row["finding_id"] == prompt.id)
    assert row["finding_semantics"] == "manual_prompt"
    prompt.finding_semantics = "machine_conclusion"
    assert row["finding_semantics"] == "manual_prompt"
