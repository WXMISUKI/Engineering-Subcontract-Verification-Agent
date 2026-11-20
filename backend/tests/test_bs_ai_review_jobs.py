import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.plus.blockchain_supervision import ai_review_jobs, reference_snapshots, service
from app.plus.blockchain_supervision import background_ai_review as jobs
from app.plus.blockchain_supervision.models import BsAiReviewJob, BsTask, BsTaskEvent
from app.standalone.identity import User


def make_session():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    return engine


def make_task(db, *, task_state='running', run_id='review-1'):
    db.add(User(id=3, username='synthetic', password_hash='unused'))
    db.flush()
    task = BsTask(id=1, conversation_id=1, title='synthetic', state=task_state,
                  created_by=3, created_by_type='web',
                  options_snapshot_json={reference_snapshots.REVIEW_POINTER: run_id})
    db.add(task)
    db.commit()
    return task


def test_enqueue_claim_lease_and_terminal_states_are_idempotent():
    engine = make_session()
    with Session(engine) as db:
        make_task(db)
        job = ai_review_jobs.enqueue(db, 1, 'review-1', ['a2'])
        db.commit()
        assert job['state'] == 'queued' and job['attempt'] == 1

        claimed = ai_review_jobs.claim(db, job['id'])
        db.commit()
        assert claimed['state'] == 'running' and claimed['lease_token']
        assert ai_review_jobs.claim(db, job['id']) is None

        assert ai_review_jobs.heartbeat(db, job['id'], 'wrong-token') is False
        assert ai_review_jobs.heartbeat(db, job['id'], claimed['lease_token']) is True
        assert ai_review_jobs.finish(db, job['id'], 'wrong-token', 'completed') is False
        assert ai_review_jobs.finish(db, job['id'], claimed['lease_token'], 'completed') is True
        db.commit()

        again = ai_review_jobs.enqueue(db, 1, 'review-1', ['a2'])
        db.commit()
        assert again['state'] == 'completed'
        assert again['attempt'] == 1


def test_expired_lease_is_requeued_once_then_failed_after_max_attempts():
    engine = make_session()
    with Session(engine) as db:
        make_task(db)
        job = ai_review_jobs.enqueue(db, 1, 'review-1', ['a2'])
        db.commit()
        claimed = ai_review_jobs.claim(db, job['id'])
        db.commit()
        assert claimed['lease_token']
        row = ai_review_jobs.get_job_by_id(db, job['id'])
        row.lease_expires_at = ai_review_jobs.utc_naive_now()
        db.commit()

        handled = ai_review_jobs.requeue_expired(db, max_attempts=2)
        db.commit()
        assert [(item['state'], item['attempt']) for item in handled] == [('queued', 2)]
        assert ai_review_jobs.claim(db, job['id'])['state'] == 'running'
        db.commit()

        row = ai_review_jobs.get_job_by_id(db, job['id'])
        row.lease_expires_at = ai_review_jobs.utc_naive_now()
        db.commit()
        handled = ai_review_jobs.requeue_expired(db, max_attempts=2)
        db.commit()
        assert [item['state'] for item in handled] == ['failed']
        assert ai_review_jobs.get_job_by_id(db, job['id']).state == 'failed'


def test_job_status_projects_queued_row_as_running_and_never_as_interrupted():
    engine = make_session()
    with Session(engine) as db:
        task = make_task(db)
        job = ai_review_jobs.enqueue(db, 1, 'review-1', ['a2'])
        db.commit()
        service.add_event(db, task.id, jobs.STARTED, payload={
            'job_id': job['id'], 'review_run_id': 'review-1', 'instance_id': 'dead-process'})

        status = jobs.job_status(db, task, 'review-1')

        assert status['state'] == 'running'
        assert status['queued'] is True
        assert status['diagnostic_state'] == '排队中'
        assert status['job_id'] == job['id']


def test_job_status_projects_expired_lease_as_waiting_recovery():
    engine = make_session()
    with Session(engine) as db:
        task = make_task(db)
        job = ai_review_jobs.enqueue(db, 1, 'review-1', ['a2'])
        db.commit()
        claimed = ai_review_jobs.claim(db, job['id'])
        db.commit()
        service.add_event(db, task.id, jobs.STARTED, payload={
            'job_id': job['id'], 'review_run_id': 'review-1', 'instance_id': 'dead-process'})
        row = ai_review_jobs.get_job_by_id(db, job['id'])
        row.lease_expires_at = ai_review_jobs.utc_naive_now()
        db.commit()
        assert claimed['lease_token']

        status = jobs.job_status(db, task, 'review-1')

        assert status['state'] == 'running'
        assert status['queued'] is True
        assert status['diagnostic_state'] == '等待恢复'


def test_resume_requeues_and_starts_valid_job_with_recovery_event(monkeypatch):
    engine = make_session()
    with Session(engine) as db:
        make_task(db)
        job = ai_review_jobs.enqueue(db, 1, 'review-1', ['a2'])
        db.commit()
        job_id = job['id']
        ai_review_jobs.get_job_by_id(db, job_id).attempt = 2
        db.commit()
        snapshot = {'state': 'completed', 'review_run_id': 'review-1', 'ai_auto_enabled': True,
                    'ai_auto_groups': ['a2']}
    monkeypatch.setattr(reference_snapshots, 'get_reference_snapshot', lambda *_: snapshot)
    monkeypatch.setattr(jobs, 'ACTIVE_JOBS', {})
    calls = []

    async def fake_run(bind, task_id, user_id, user_type, review_run_id, job_id_arg, groups, retry,
                       *, recovered=False):
        calls.append((job_id_arg, tuple(groups), recovered))

    monkeypatch.setattr(jobs, '_run_background', fake_run)

    async def scenario():
        started = await jobs.resume_queued_jobs(engine)
        for _ in range(20):
            await asyncio.sleep(0)
            if calls:
                break
        return started

    assert asyncio.run(scenario()) == 1
    assert calls == [(job_id, ('a2',), True)]


def test_resume_abandons_job_when_task_no_longer_runnable(monkeypatch):
    engine = make_session()
    with Session(engine) as db:
        make_task(db, task_state='setup')
        job = ai_review_jobs.enqueue(db, 1, 'review-1', ['a2'])
        db.commit()
        job_id = job['id']
    monkeypatch.setattr(jobs, 'ACTIVE_JOBS', {})
    calls = []

    async def fake_run(*args, **kwargs):
        calls.append(args)

    monkeypatch.setattr(jobs, '_run_background', fake_run)
    assert asyncio.run(jobs.resume_queued_jobs(engine)) == 0
    with Session(engine) as db:
        assert calls == []
        assert ai_review_jobs.get_job_by_id(db, job_id).state == 'failed'
        failed = db.query(BsTaskEvent).filter_by(event_type=jobs.FAILED).one()
        assert failed.payload_json['reason'] == 'task_finished'
