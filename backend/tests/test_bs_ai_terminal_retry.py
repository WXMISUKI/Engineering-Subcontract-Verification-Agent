import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.plus.blockchain_supervision import background_ai_review as jobs, reference_snapshots, service
from app.plus.blockchain_supervision.models import BsTask


def _coverage(*, pending=0):
    return {'state': 'available', 'groups': [
        {'group_id': 'a1', 'state': 'available', 'counts': {
            'not_started': 0, 'pending': pending, 'completed': 0, 'failed': 2, 'partial': 0, 'not_run': 0},
         'reviewable_failed': 0, 'reviewable_partial': 0, 'reviewable_not_run': 0},
        {'group_id': 'a2', 'state': 'available', 'counts': {
            'not_started': 1, 'pending': 0, 'completed': 0, 'failed': 0, 'partial': 0, 'not_run': 0},
         'reviewable_failed': 0, 'reviewable_partial': 0, 'reviewable_not_run': 0},
    ]}


def _task(db):
    task = BsTask(id=1, conversation_id=1, title='synthetic', state='running', created_by=3,
                  created_by_type='web', options_snapshot_json={reference_snapshots.REVIEW_POINTER: 'review-1'})
    db.add(task)
    db.commit()
    return task


def _snapshot():
    return {'state': 'completed', 'review_run_id': 'review-1', 'ai_auto_enabled': True,
            'ai_auto_groups': ['a1', 'a2'], 'steps': [{'state': 'completed'}],
            'runs': [{'group_id': 'a1', 'run_id': 'a1-1', 'state': 'completed'},
                     {'group_id': 'a2', 'run_id': 'a2-1', 'state': 'completed'}]}


def test_failed_job_with_unstarted_groups_is_failed_not_running(monkeypatch):
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    monkeypatch.setattr(jobs.ai_advisory_snapshots, 'collect_current_coverage', lambda *_: _coverage())
    monkeypatch.setattr(service, 'current_ai_advisory_pending', lambda *_: False)
    with Session(engine) as db:
        task = _task(db)
        service.add_event(db, task.id, jobs.FAILED, payload={
            'job_id': 'job-1', 'review_run_id': 'review-1', 'failure_categories': ['timeout']})
        progress = service.task_progress(db, task, materials=[], snapshot=_snapshot(), zip_summary={'containers': []})
        assert progress['phase'] == 'failed'
        assert progress['ai_job']['state'] == 'failed'


def test_failed_job_with_active_lease_stays_busy(monkeypatch):
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    monkeypatch.setattr(jobs.ai_advisory_snapshots, 'collect_current_coverage', lambda *_: _coverage(pending=1))
    monkeypatch.setattr(service, 'current_ai_advisory_pending', lambda *_: True)
    with Session(engine) as db:
        task = _task(db)
        service.add_event(db, task.id, jobs.FAILED, payload={'job_id': 'job-1', 'review_run_id': 'review-1'})
        progress = service.task_progress(db, task, materials=[], snapshot=_snapshot(), zip_summary={'containers': []})
        assert progress['phase'] == 'ai_checking'


def test_foreign_unfinished_job_cannot_be_restarted(monkeypatch):
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    monkeypatch.setattr(reference_snapshots, 'get_reference_snapshot', lambda *_: _snapshot())
    monkeypatch.setattr(service, 'current_ai_advisory_pending', lambda *_: False)
    monkeypatch.setattr(jobs, 'ACTIVE_JOBS', {})
    async def noop(*_args, **_kwargs):
        pass
    monkeypatch.setattr(jobs, '_run_background', noop)
    async def scenario():
        with Session(engine) as db:
            task = _task(db)
            service.add_event(db, task.id, jobs.STARTED, payload={
                'job_id': 'other-worker-job', 'review_run_id': 'review-1', 'instance_id': 'other-worker'})
            try:
                with pytest.raises(ValueError, match='无法确认'):
                    jobs.submit_background_job(db, task.id, SimpleNamespace(id=3, user_type='web'), 'review-1', retry=True)
            finally:
                active = list(jobs.ACTIVE_JOBS.values())
                for worker in active:
                    worker.cancel()
                await asyncio.gather(*active, return_exceptions=True)
    asyncio.run(scenario())


def test_retry_rejects_unavailable_current_coverage(monkeypatch):
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    monkeypatch.setattr(reference_snapshots, 'get_reference_snapshot', lambda *_: _snapshot())
    monkeypatch.setattr(service, 'current_ai_advisory_pending', lambda *_: False)
    monkeypatch.setattr(jobs.ai_advisory_snapshots, 'collect_current_coverage', lambda *_: {'state': 'unavailable'})
    async def noop(*_args, **_kwargs):
        pass
    monkeypatch.setattr(jobs, '_run_background', noop)
    monkeypatch.setattr(jobs, 'ACTIVE_JOBS', {})
    async def scenario():
        with Session(engine) as db:
            task = _task(db)
            service.add_event(db, task.id, jobs.FAILED, payload={'job_id': 'job-1', 'review_run_id': 'review-1'})
            try:
                with pytest.raises(ValueError, match='无法核实'):
                    jobs.submit_background_job(db, task.id, SimpleNamespace(id=3, user_type='web'), 'review-1', retry=True)
            finally:
                active = list(jobs.ACTIVE_JOBS.values())
                for worker in active:
                    worker.cancel()
                await asyncio.gather(*active, return_exceptions=True)
    asyncio.run(scenario())


def test_failed_job_can_resume_same_review_once_without_new_rules_or_findings(monkeypatch):
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    monkeypatch.setattr(reference_snapshots, 'get_reference_snapshot', lambda *_: _snapshot())
    monkeypatch.setattr(service, 'current_ai_advisory_pending', lambda *_: False)
    monkeypatch.setattr(jobs.ai_advisory_snapshots, 'collect_current_coverage', lambda *_: _coverage())
    monkeypatch.setattr(jobs, 'ACTIVE_JOBS', {})
    async def noop(*_args, **_kwargs):
        await asyncio.Event().wait()
    monkeypatch.setattr(jobs, '_run_background', noop)
    async def scenario():
        with Session(engine) as db:
            task = _task(db)
            service.add_event(db, task.id, jobs.FAILED, payload={'job_id': 'old-job', 'review_run_id': 'review-1'})
            user = SimpleNamespace(id=3, user_type='web')
            unchanged = jobs.submit_background_job(db, task.id, user, 'review-1', retry=False)
            assert unchanged['job_id'] == 'old-job'
            first = jobs.submit_background_job(db, task.id, user, 'review-1', retry=True)
            worker = jobs.ACTIVE_JOBS[first['job_id']]
            try:
                assert first['state'] == 'running' and first['review_run_id'] == 'review-1'
                assert first['job_id'] != 'old-job'
                same_process = jobs.submit_background_job(db, task.id, user, 'review-1', retry=True)
                assert same_process['job_id'] == first['job_id']
                monkeypatch.setattr(jobs, 'INSTANCE_ID', 'other-worker')
                with pytest.raises(ValueError, match='无法确认'):
                    jobs.submit_background_job(db, task.id, user, 'review-1', retry=True)
                assert db.get(BsTask, 1).options_snapshot_json[reference_snapshots.REVIEW_POINTER] == 'review-1'
            finally:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
    asyncio.run(scenario())
