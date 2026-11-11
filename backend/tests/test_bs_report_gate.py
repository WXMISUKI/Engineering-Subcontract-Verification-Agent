"""报告门禁：只有未处置的无法核实项阻断正式报告；不符合项不阻断。"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.plus.blockchain_supervision import reporting, service
from app.plus.blockchain_supervision.models import BsFinding, BsTask
from app.plus.blockchain_supervision.router import _require_no_pending_hits
from app.standalone.identity import Base


def _db(tmp_path):
    engine = create_engine(
        "sqlite:///" + (tmp_path / "report-gate.sqlite").as_posix(),
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return engine


def _task(db, task_id=1):
    task = BsTask(id=task_id, conversation_id=1, title="report-gate", created_by=1,
                  created_by_type="web", state="running", options_snapshot_json={})
    db.add(task)
    db.commit()
    return task


def test_report_gate_only_blocks_undecidable():
    assert reporting.report_gate_blocking(SimpleNamespace(
        item_id="T15-01", status="hit", evidence_json=[])) is False
    assert reporting.report_gate_blocking(SimpleNamespace(
        item_id="T15-02", status="miss", evidence_json=[])) is False
    assert reporting.report_gate_blocking(SimpleNamespace(
        item_id="T15-03", status="undecidable", evidence_json=[])) is True


def test_confirmed_report_blocks_pending_undecidable_but_not_hit(tmp_path):
    engine = _db(tmp_path)
    with Session(engine) as db:
        task = _task(db)
        db.add(BsFinding(task_id=task.id, item_id="T15-01", kind="completeness",
                         status="hit", message="明确不符合", evidence_json=[]))
        db.commit()
        _require_no_pending_hits(db, task.id, "confirmed")

        db.add(BsFinding(task_id=task.id, item_id="T15-02", kind="completeness",
                         status="undecidable", message="证据不足", evidence_json=[]))
        db.commit()
        with pytest.raises(HTTPException) as exc:
            _require_no_pending_hits(db, task.id, "confirmed")
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "pending_undecidable_review"


def test_draft_preview_is_not_the_formal_report_gate(tmp_path):
    engine = _db(tmp_path)
    with Session(engine) as db:
        task = _task(db)
        db.add(BsFinding(task_id=task.id, item_id="T15-01", kind="completeness",
                         status="undecidable", message="证据不足", evidence_json=[]))
        db.commit()
        _require_no_pending_hits(db, task.id, "draft")


def test_complete_review_allows_unresolved_hit_but_blocks_unresolved_undecidable(tmp_path, monkeypatch):
    engine = _db(tmp_path)
    with Session(engine) as db:
        task = _task(db)
        db.add(BsFinding(task_id=task.id, item_id="T15-01", kind="completeness",
                         status="hit", message="明确不符合", evidence_json=[]))
        db.commit()

        monkeypatch.setattr(service, "review_output_readiness",
                            lambda db, task: {"ok": True, "code": "ready",
                                              "detail": "", "missing_groups": [], "pending_groups": []})
        monkeypatch.setattr(service, "current_ai_advisory_pending", lambda db, task: False)
        monkeypatch.setattr(service.reference_snapshots, "get_reference_snapshot", lambda db, task: None)
        monkeypatch.setattr(service, "create_review_version",
                            lambda db, task, trigger="completed": SimpleNamespace(version_no=1))
        monkeypatch.setattr(service, "add_event", lambda *args, **kwargs: None)

        result = service.complete_review(db, task)
        assert result == {"ok": True, "version_no": 1}

        task.state = "running"
        db.add(BsFinding(task_id=task.id, item_id="T15-02", kind="completeness",
                         status="undecidable", message="证据不足", evidence_json=[]))
        db.commit()
        blocked = service.complete_review(db, task)
        assert blocked["ok"] is False
        assert "无法核实" in blocked["detail"]


def test_unprocessed_hit_uses_ai_qualitative_label():
    disposition = reporting.human_disposition(None, "hit")
    assert disposition["label"] == "AI 定性（未经人工处置）"
    undecidable = reporting.human_disposition(None, "undecidable")
    assert undecidable["label"] != disposition["label"]
