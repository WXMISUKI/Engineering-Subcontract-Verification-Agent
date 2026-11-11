import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.plus.blockchain_supervision import background_ai_review as jobs, reference_snapshots, service
from app.plus.blockchain_supervision.models import BsTask


def _task(db):
    task = BsTask(
        id=1,
        conversation_id=1,
        title="synthetic-recovery",
        state="running",
        created_by=3,
        created_by_type="web",
        options_snapshot_json={},
    )
    db.add(task)
    db.commit()
    return task


def test_rerun_rejects_active_ai_before_material_or_snapshot_mutation(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        task = _task(db)
        monkeypatch.setattr(service, "current_ai_advisory_pending", lambda *_: True)
        monkeypatch.setattr(
            service,
            "validate_task_materials",
            lambda *_: pytest.fail("active AI must be rejected before material validation"),
        )

        result = service.rerun_task(db, task)

        assert result["ok"] is False
        assert result["reason"] == "ai_advisory_pending"


def test_rerun_rejects_foreign_started_job_as_execution_uncertain(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        task = _task(db)
        task.options_snapshot_json = {reference_snapshots.REVIEW_POINTER: "review-1"}
        db.commit()
        service.add_event(db, task.id, jobs.STARTED, payload={
            "job_id": "foreign-job",
            "review_run_id": "review-1",
            "instance_id": "other-worker",
        })
        monkeypatch.setattr(service, "current_ai_advisory_pending", lambda *_: False)
        monkeypatch.setattr(
            service,
            "validate_task_materials",
            lambda *_: pytest.fail("uncertain execution must be rejected before material validation"),
        )

        result = service.rerun_task(db, task)

        assert result["ok"] is False
        assert result["reason"] == "execution_uncertain"


def test_rerun_continues_when_background_job_is_not_started(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        task = _task(db)
        monkeypatch.setattr(service, "current_background_execution_state", lambda *_: "not_started")
        monkeypatch.setattr(service, "current_ai_advisory_pending", lambda *_: False)
        monkeypatch.setattr(service, "validate_task_materials", lambda *_: {"parsing_pending": True})

        result = service.rerun_task(db, task)

        assert result["ok"] is False
        assert result["reason"] == "parsing_pending"
