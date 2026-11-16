"""任务级门禁：人工回执、读取和执行分别保留，读取判据无业务副作用。"""

from copy import deepcopy

import pytest

from app.plus.blockchain_supervision import service
from app.plus.blockchain_supervision.models import BsFinding, BsMaterial, BsReviewVersion, BsTask, BsTaskEvent


def seed_task(db):
    task = BsTask(conversation_id=101, title="门禁合成任务", created_by=1,
                  created_by_type="tester", state="running", scope_a1=False, scope_a3=False)
    db.add(task)
    db.flush()
    finding = BsFinding(task_id=task.id, item_id="A2-01", kind="person_consistency",
                        status="undecidable", message="身份需人工核对",
                        review_json={"decision": "accepted"})
    material = BsMaterial(task_id=task.id, file_name="合成材料.txt", role="packet", parse_state="done",
                          pages=1, pages_json=[{"page": 1, "text": "这是合成材料的完整可读正文，用于验证任务级读取与报告门禁，不能当作真实业务验收。"}])
    db.add_all([finding, material])
    db.flush()
    service.initialize_reference_review(db, task)
    db.commit()
    return task, finding, material


def test_readiness_is_read_only_and_separates_three_permissions(db_session):
    db = db_session
    task, finding, material = seed_task(db)
    before = (deepcopy(task.options_snapshot_json), deepcopy(material.degradations_json), deepcopy(finding.review_json))
    event_count = db.query(BsTaskEvent).count()
    dto = service.build_report_readiness(db, task)
    assert dto["schema_version"] == "bs.report-readiness.v1"
    assert dto["blocking_reasons"] == []
    assert (dto["draft_readable"], dto["confirmed_output_ready"], dto["task_close_ready"]) == (True, True, True), dto
    assert dto["reading_quality"]["state"] == "complete"
    assert dto["execution"]["state"] == "completed"
    assert (task.options_snapshot_json, material.degradations_json, finding.review_json) == before
    assert db.query(BsTaskEvent).count() == event_count
    assert db.query(BsReviewVersion).count() == 0
    assert not db.dirty and not db.new


@pytest.mark.parametrize("parse_state", ["done", "failed", "parsing"])
def test_accepted_findings_do_not_clear_reading_gaps(db_session, parse_state):
    db = db_session
    task, finding, material = seed_task(db)
    material.parse_state = parse_state
    material.degradations_json = [{"kind": "parse_incomplete", "total_pages": 2,
                                   "read_pages": [1], "unread_pages": [2], "ocr_suspect_pages": [2]}]
    db.flush()
    service.initialize_reference_review(db, task)
    db.commit()
    dto = service.build_report_readiness(db, task)
    assert dto["draft_readable"] is True
    assert dto["confirmed_output_ready"] is dto["task_close_ready"] is False
    assert dto["finding_review"]["blocking"] == []
    assert dto["reading_quality"]["pages"][0]["material_id"] == material.id
    assert dto["reading_quality"]["pages"][0]["page"] == 2
    if parse_state == "failed":
        assert dto["execution"]["state"] == "unknown"
    assert any(row["code"] == "reading_incomplete" for row in dto["blocking_reasons"])
    assert service.confirmed_output_readiness(db, task)["ok"] is False
    assert service.complete_review(db, task)["ok"] is False
    assert task.state == "running"
    assert db.query(BsReviewVersion).count() == 0


def test_pending_review_blocks_formal_but_not_draft(db_session):
    db = db_session
    task, finding, _ = seed_task(db)
    finding.review_json = {}
    db.commit()
    dto = service.build_report_readiness(db, task)
    assert dto["draft_readable"] is True
    assert dto["confirmed_output_ready"] is dto["task_close_ready"] is False
    assert dto["finding_review"]["blocking"][0]["finding_id"] == finding.id


def test_execution_failure_is_not_cleared_by_review(db_session, monkeypatch):
    db = db_session
    task, _, _ = seed_task(db)
    monkeypatch.setattr(service, "ai_output_readiness", lambda *_: {
        "ok": False, "code": "ai_execution_incomplete", "detail": "必需核查尚未完成"})
    dto = service.build_report_readiness(db, task)
    assert dto["draft_readable"] is True
    assert dto["confirmed_output_ready"] is dto["task_close_ready"] is False
    assert dto["execution"]["state"] == "incomplete"
    assert service.complete_review(db, task)["ok"] is False
    assert task.state == "running"


def test_changed_inputs_never_use_draft_exception(db_session, monkeypatch):
    task, _, _ = seed_task(db_session)
    monkeypatch.setattr(service, "report_output_readiness", lambda *_args, **_kwargs: {
        "ok": False, "code": "review_inputs_changed", "detail": "输入已变化"})
    dto = service.build_report_readiness(db_session, task)
    assert not any(dto[key] for key in ("draft_readable", "confirmed_output_ready", "task_close_ready"))
    assert dto["execution"]["state"] == "stale"


def test_candidate_requires_publication_but_can_close_after_review(db_session, monkeypatch):
    task, _, _ = seed_task(db_session)
    monkeypatch.setattr(service, "supplement_publication", lambda *_: {"state": "candidate"})
    dto = service.build_report_readiness(db_session, task)
    assert dto["draft_readable"] is dto["confirmed_output_ready"] is False
    assert dto["task_close_ready"] is True
    assert service.confirmed_output_readiness(db_session, task)["code"] == "supplement_candidate_unpublished"
    assert service.task_close_readiness(db_session, task)["ok"] is True


def current_client(tmp_path, *, unread=False):
    from sqlalchemy.orm import Session
    from test_bs_report_version_http import _client
    client, engine, task_id = _client(tmp_path)
    with Session(engine) as db:
        task = db.get(BsTask, task_id)
        task.state, task.scope_a1, task.scope_a3 = "running", False, False
        material = BsMaterial(task_id=task_id, file_name="合成文件.txt", role="packet", parse_state="done",
                              pages=1, pages_json=[{"page": 1, "text": "这是合成材料的完整正文，用来验证当前审查代的门禁与原件读取状态，不代表业务验收。"}])
        if unread:
            material.degradations_json = [{"kind": "parse_incomplete", "total_pages": 2,
                                           "read_pages": [1], "unread_pages": [2]}]
        db.add_all([material, BsFinding(task_id=task_id, item_id="A2-01", kind="person_consistency",
                                       status="miss", message="合成结论")])
        db.flush()
        service.initialize_reference_review(db, task)
        db.commit()
    return client, engine, task_id


def test_http_draft_discloses_gaps_while_all_formal_entries_block(tmp_path, db_session):
    client, engine, task_id = current_client(tmp_path, unread=True)
    base = f"/api/plus/blockchain-supervision/tasks/{task_id}"
    detail = client.get(base)
    assert detail.status_code == 200, detail.text
    readiness = detail.json()["report_readiness"]
    assert readiness["draft_readable"] is True
    assert readiness["confirmed_output_ready"] is readiness["task_close_ready"] is False
    for suffix in ("/report", "/opinion"):
        response = client.get(base + suffix, params={"stage": "draft"})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["report_readiness"]["confirmed_output_ready"] is False
        assert "草稿" in payload["draft_notice"]
        assert payload["report_readiness"]["reading_quality"]["pages"][0]["page"] == 2
        assert payload["business_document"]["conclusion"]["status"] == "incomplete"
        if suffix == "/opinion":
            assert payload["conclusion"]["code"] == "pending"
            assert "counts" in payload["conclusion"]
            assert payload["signature"]["suggested_option"] is None
        else:
            assert "不形成正式结论" in payload["summary"]["headline"]
            assert "counts" in payload["summary"]
    for suffix in ("/report", "/opinion", "/report/export?fmt=docx&", "/report/export?fmt=pdf&",
                   "/opinion/export?fmt=docx&", "/opinion/export?fmt=pdf&", "/opinion/preview",
                   "/report/evidence-bundle"):
        url = base + suffix + ("stage=confirmed" if suffix.endswith("&") else "?stage=confirmed")
        response = client.get(url)
        assert response.status_code == 409, (suffix, response.status_code, response.headers.get("content-type"))
        assert response.json()["detail"]["code"] == "reading_incomplete"
    response = client.post(base + "/complete")
    assert response.status_code == 409, (response.status_code, response.headers.get("content-type"))
    from sqlalchemy.orm import Session
    with Session(engine) as db:
        assert db.get(BsTask, task_id).state == "running"
        assert db.query(BsReviewVersion).count() == 0
        assert not any("export" in row.event_type for row in db.query(BsTaskEvent).all())


@pytest.mark.parametrize("suffix", ["/report/export", "/opinion/export", "/opinion/preview", "/report/evidence-bundle"])
def test_current_draft_files_are_rejected_even_when_ready(tmp_path, db_session, suffix):
    client, _, task_id = current_client(tmp_path)
    response = client.get(f"/api/plus/blockchain-supervision/tasks/{task_id}{suffix}", params={"stage": "draft"})
    assert response.status_code == 409, (response.status_code, response.headers.get("content-type"))
    assert response.json()["detail"]["code"] == "draft_file_output_blocked"


def test_ready_formal_files_remain_available(tmp_path, db_session):
    client, _, task_id = current_client(tmp_path)
    base = f"/api/plus/blockchain-supervision/tasks/{task_id}"
    for suffix in ("/report/export", "/opinion/export", "/opinion/preview", "/report/evidence-bundle"):
        response = client.get(base + suffix, params={"stage": "confirmed"})
        assert response.status_code == 200, (suffix, response.status_code, response.headers.get("content-type"))
    markdown = client.get(base + "/opinion/preview-markdown", params={"stage": "draft"})
    assert markdown.status_code == 200
    assert markdown.json()["report_readiness"]["confirmed_output_ready"] is True


@pytest.mark.parametrize("state,count,groups,signal,expected", [
    ("available", {"not_run": 0}, ["a1"], False, True),
    ("available", {}, ["a1"], False, False),
    ("available", {"failed": 1}, ["a1"], False, False),
    ("available", {"partial": 1}, ["a1"], False, False),
    ("available", {"not_started": 1}, ["a1"], False, False),
    ("available", {"pending": 1}, ["a1"], False, False),
    ("available", {"not_run": 1}, ["a1"], True, True),
    ("available", {"not_run": 1}, ["a1"], False, False),
    ("stale", {}, ["a1"], False, False),
    ("available", {}, ["a2"], False, False),
])
def test_required_ai_execution_is_independent_of_reviewable_results(db_session, monkeypatch, state, count, groups, signal, expected):
    task, _, _ = seed_task(db_session)
    snapshot = service.reference_snapshots.get_reference_snapshot(db_session, task)
    snapshot.update(ai_auto_enabled=True, ai_auto_groups=["a1"])
    monkeypatch.setattr(service.reference_snapshots, "get_reference_snapshot", lambda *_: snapshot)
    monkeypatch.setattr(service, "current_ai_advisory_pending", lambda *_: False)
    monkeypatch.setattr(service.ai_advisory_snapshots, "collect_current_coverage", lambda *_: {
        "state": state, "groups": [{"group_id": group, "state": "available", "counts": count,
            "reviewable_failed": 99, "reviewable_partial": 99,
            "not_assessed_no_signal": 1 if signal else 0,
            "blocking_not_run": 0 if signal else count.get("not_run", 0)} for group in groups]})
    dto = service.build_report_readiness(db_session, task)
    assert dto["draft_readable"] is True
    assert dto["confirmed_output_ready"] is dto["task_close_ready"] is expected
