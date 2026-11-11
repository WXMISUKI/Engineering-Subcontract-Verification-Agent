from types import SimpleNamespace
import asyncio
import pytest
import threading
import time
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.database import Base
from app.plus.blockchain_supervision.models import BsTask
from app.plus.blockchain_supervision import reference_snapshots, service
from app.standalone.identity import create_account
from app.standalone.main import create_app
from fastapi.testclient import TestClient

from app.plus.blockchain_supervision import background_ai_review as jobs


def test_running_job_is_not_declared_running_after_worker_process_restart():
    event = SimpleNamespace(event_type='check.ai_background_started', payload_json={
        'job_id': 'job-1', 'review_run_id': 'review-1', 'instance_id': 'old-process'})
    assert jobs.project_job_state(event, active_job_ids=set(), instance_id='new-process')['state'] == 'interrupted'


def test_live_job_and_finished_job_have_distinct_states():
    event = SimpleNamespace(event_type='check.ai_background_started', payload_json={
        'job_id': 'job-1', 'review_run_id': 'review-1', 'instance_id': 'instance'})
    assert jobs.project_job_state(event, active_job_ids={'job-1'}, instance_id='instance')['state'] == 'running'
    event.event_type = 'check.ai_background_completed'
    assert jobs.project_job_state(event, active_job_ids=set(), instance_id='new-process')['state'] == 'completed'


def test_running_job_projection_includes_last_progress_and_stale_diagnostic():
    now = time.time()
    event = SimpleNamespace(event_type=jobs.STARTED, created_at=None, payload_json={
        'job_id': 'job-1', 'review_run_id': 'review-1', 'instance_id': 'instance',
    })
    progress = SimpleNamespace(created_at=__import__('datetime').datetime.fromtimestamp(now - 2400), payload_json={
        'job_id': 'job-1', 'request_id': 'request-1', 'group_id': 'a2',
        'batch_index': 4, 'batch_total': 6, 'status': 'processing',
    })
    projected = jobs.project_job_state(event, active_job_ids={'job-1'}, instance_id='instance',
                                       progress_event=progress,
                                       now=__import__('datetime').datetime.fromtimestamp(now))
    assert projected['state'] == 'running'
    assert projected['current_group'] == 'a2'
    assert projected['current_batch'] == {'index': 4, 'total': 6, 'status': 'processing'}
    assert projected['request_id'] == 'request-1'
    assert projected['stale_candidate'] is True
    assert projected['last_progress_at'] is not None


def test_running_job_without_progress_is_explicitly_unknown():
    event = SimpleNamespace(event_type=jobs.STARTED, created_at=None, payload_json={
        'job_id': 'job-1', 'review_run_id': 'review-1', 'instance_id': 'instance',
    })
    projected = jobs.project_job_state(event, active_job_ids={'job-1'}, instance_id='instance')
    assert projected['last_progress_at'] is None
    assert projected['stale_candidate'] is True
    assert projected['diagnostic_state'] == '无法核实'


def test_job_status_uses_latest_group_or_batch_event(monkeypatch):
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    monkeypatch.setattr(jobs, 'ACTIVE_JOBS', {'job-1': object()})
    with Session(engine) as db:
        task = BsTask(id=1, conversation_id=1, title='synthetic', state='running', created_by=3, created_by_type='web',
                      options_snapshot_json={reference_snapshots.REVIEW_POINTER: 'review-1'})
        db.add(task)
        db.commit()
        service.add_event(db, task.id, jobs.STARTED, payload={
            'job_id': 'job-1', 'review_run_id': 'review-1', 'instance_id': jobs.INSTANCE_ID})
        service.add_event(db, task.id, 'check.ai_batch_progress', payload={
            'job_id': 'job-1', 'group_id': 'a2', 'batch_index': 6, 'batch_total': 6, 'status': 'completed'})
        service.add_event(db, task.id, jobs.PROGRESS, payload={
            'job_id': 'job-1', 'review_run_id': 'review-1', 'group_id': 'internal_records'})
        status = jobs.job_status(db, task, 'review-1')
        assert status['state'] == 'running'
        assert status['current_group'] == 'internal_records'
        assert status['current_batch'] is None


def test_task_progress_exposes_failed_ai_job_diagnostics(monkeypatch):
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    monkeypatch.setattr(jobs.ai_advisory_snapshots, 'collect_current_coverage', lambda *_: {
        'state': 'available', 'groups': [{'group_id': 'a2', 'state': 'available',
            'counts': {'not_started': 0, 'pending': 0, 'failed': 1, 'partial': 0, 'not_run': 0},
            'reviewable_failed': 0, 'reviewable_partial': 0, 'reviewable_not_run': 0}]})
    with Session(engine) as db:
        task = BsTask(id=1, conversation_id=1, title='synthetic', state='running', created_by=3, created_by_type='web',
                      options_snapshot_json={reference_snapshots.REVIEW_POINTER: 'review-1'})
        db.add(task)
        db.commit()
        service.add_event(db, task.id, jobs.FAILED, payload={
            'job_id': 'job-1', 'review_run_id': 'review-1', 'failure_categories': ['timeout']})
        snapshot = {'ai_auto_enabled': True, 'steps': [{'state': 'completed'}]}
        progress = service.task_progress(db, task, materials=[], snapshot=snapshot, zip_summary={'containers': []})
        assert progress['phase'] == 'failed'
        assert progress['ai_job']['state'] == 'failed'
        assert progress['ai_job']['failure_categories'] == ['timeout']


def test_malformed_job_event_never_fabricates_a_success():
    event = SimpleNamespace(event_type='check.ai_background_completed', payload_json={})
    assert jobs.project_job_state(event, active_job_ids=set(), instance_id='instance')['state'] == 'unavailable'


def test_incomplete_material_scope_is_preserved_as_a_failure_category():
    error = jobs.BackgroundReviewIncomplete(['incomplete_material_scope'])
    assert error.categories == ['incomplete_material_scope']
    event = SimpleNamespace(event_type=jobs.FAILED, payload_json={
        'job_id': 'job-1', 'review_run_id': 'review-1',
        'failure_categories': ['incomplete_material_scope']})
    projected = jobs.project_job_state(event, active_job_ids=set(), instance_id='instance')
    assert projected['failure_categories'] == ['incomplete_material_scope']


def test_completed_job_is_not_reused_when_current_coverage_is_incomplete(monkeypatch):
    snapshot = {'state': 'completed', 'review_run_id': 'review-1', 'ai_auto_enabled': True, 'ai_auto_groups': ['a1']}
    monkeypatch.setattr(reference_snapshots, 'get_reference_snapshot', lambda *_: snapshot)
    monkeypatch.setattr(jobs.ai_advisory_snapshots, 'collect_current_coverage', lambda *_: {'groups': [{
        'group_id': 'a1', 'state': 'available', 'counts': {'not_started': 1, 'pending': 0, 'failed': 0,
        'partial': 0, 'not_run': 0}, 'reviewable_failed': 0, 'reviewable_partial': 0, 'reviewable_not_run': 0}]})
    assert jobs._completed_coverage_valid(None, None, 'review-1') is False


def test_submit_returns_without_waiting_and_deduplicates_running_job(monkeypatch):
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(BsTask(id=1, conversation_id=1, title='synthetic', state='running', created_by=3, created_by_type='web',
                      options_snapshot_json={reference_snapshots.REVIEW_POINTER: 'review-1'}))
        db.commit()
        monkeypatch.setattr(reference_snapshots, 'get_reference_snapshot', lambda *_: {
            'state': 'completed', 'review_run_id': 'review-1', 'ai_auto_enabled': True,
            'ai_auto_groups': ['a1'], 'runs': [{'group_id': 'a1', 'run_id': 'a1-1', 'state': 'completed'}],
        })
        monkeypatch.setattr(service, 'current_ai_advisory_pending', lambda *_: False)
        gate = asyncio.Event()
        calls = []
        async def worker(*args, **kwargs):
            calls.append('started')
            await gate.wait()
        monkeypatch.setattr(jobs, '_run_background', worker)
        async def scenario():
            user = SimpleNamespace(id=3, user_type='web')
            first = jobs.submit_background_job(db, 1, user, 'review-1')
            second = jobs.submit_background_job(db, 1, user, 'review-1')
            assert first['state'] == second['state'] == 'running'
            assert first['job_id'] == second['job_id']
            await asyncio.sleep(0)
            assert calls == ['started']
            gate.set()
            await asyncio.gather(*list(jobs.ACTIVE_JOBS.values()))
        asyncio.run(scenario())


def test_other_account_cannot_reserve_task():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(BsTask(id=1, conversation_id=1, title='synthetic', state='running', created_by=3, created_by_type='web'))
        db.commit()
        with pytest.raises(PermissionError):
            jobs.submit_background_job(db, 1, SimpleNamespace(id=4, user_type='web'), 'review-1')


def test_http_submit_is_nonblocking_deduplicated_and_owner_scoped(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + (tmp_path / 'job.sqlite').as_posix(), connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    create_account(engine, 'alice', 'Alice-password-123')
    create_account(engine, 'bob', 'Bob-password-12345')
    monkeypatch.setattr(reference_snapshots, 'get_reference_snapshot', lambda *_: {
        'state': 'completed', 'review_run_id': 'review-1', 'ai_auto_enabled': True,
        'ai_auto_groups': ['a1'], 'runs': [{'group_id': 'a1', 'run_id': 'a1-1', 'state': 'completed'}],
    })
    monkeypatch.setattr(service, 'current_ai_advisory_pending', lambda *_: False)
    monkeypatch.setattr(jobs, '_completed_coverage_valid', lambda *_: True)
    release = threading.Event()
    async def worker(bind, task_id, user_id, user_type, review_run_id, job_id, groups, retry):
        while not release.is_set():
            await asyncio.sleep(.01)
        with Session(bind) as worker_db:
            service.add_event(worker_db, task_id, jobs.COMPLETED, stage='check', payload={
                'job_id': job_id, 'review_run_id': review_run_id, 'instance_id': jobs.INSTANCE_ID})
    monkeypatch.setattr(jobs, '_run_background', worker)
    with TestClient(create_app(engine, 's' * 64, cookie_secure=False)) as client:
        assert client.post('/api/login', json={'username': 'alice', 'password': 'Alice-password-123'}).status_code == 200
        conversation = client.post('/api/conversations').json()['id']
        base = '/api/plus/blockchain-supervision'
        task_id = client.post(base + '/tasks', json={'conversation_id': conversation, 'title': 'synthetic', 'scope_a1': True}).json()['task_id']
        with Session(engine) as db:
            task = db.get(BsTask, task_id)
            task.state = 'running'
            task.options_snapshot_json = {reference_snapshots.REVIEW_POINTER: 'review-1'}
            db.commit()
        url = f'{base}/tasks/{task_id}/ai-review'
        started = time.monotonic()
        first = client.post(url, json={'review_run_id': 'review-1'})
        assert first.status_code == 202 and time.monotonic() - started < 2
        second = client.post(url, json={'review_run_id': 'review-1'})
        assert second.status_code == 202 and second.json()['job_id'] == first.json()['job_id']
        assert client.get(url, params={'review_run_id': 'review-1'}).json()['state'] == 'running'
        assert client.post('/api/login', json={'username': 'bob', 'password': 'Bob-password-12345'}).status_code == 200
        assert client.post(url, json={'review_run_id': 'review-1'}).status_code == 403
        assert client.get(url, params={'review_run_id': 'review-1'}).status_code == 403
        release.set()
        assert client.post('/api/login', json={'username': 'alice', 'password': 'Alice-password-123'}).status_code == 200
        for _ in range(100):
            state = client.get(url, params={'review_run_id': 'review-1'}).json()['state']
            if state == 'completed':
                break
            time.sleep(.01)
        assert state == 'completed'


def test_unconfirmed_interrupted_job_cannot_restart_even_without_a_lease(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + (tmp_path / 'restart.sqlite').as_posix(), connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    create_account(engine, 'alice', 'Alice-password-123')
    snapshot = {'state': 'completed', 'review_run_id': 'review-1', 'ai_auto_enabled': True,
                'ai_auto_groups': ['a1'], 'runs': [{'group_id': 'a1', 'run_id': 'a1-1', 'state': 'completed'}]}
    monkeypatch.setattr(reference_snapshots, 'get_reference_snapshot', lambda *_: snapshot)
    monkeypatch.setattr(jobs, '_completed_coverage_valid', lambda *_: True)
    lease = {'pending': True}
    monkeypatch.setattr(service, 'current_ai_advisory_pending', lambda *_: lease['pending'])
    with TestClient(create_app(engine, 's' * 64, cookie_secure=False)) as client:
        assert client.post('/api/login', json={'username': 'alice', 'password': 'Alice-password-123'}).status_code == 200
        conversation = client.post('/api/conversations').json()['id']
        base = '/api/plus/blockchain-supervision'
        task_id = client.post(base + '/tasks', json={'conversation_id': conversation, 'title': 'synthetic', 'scope_a1': True}).json()['task_id']
        with Session(engine) as db:
            task = db.get(BsTask, task_id)
            task.state = 'running'
            task.options_snapshot_json = {reference_snapshots.REVIEW_POINTER: 'review-1'}
            service.add_event(db, task_id, jobs.STARTED, stage='check', payload={
                'job_id': 'old-job', 'review_run_id': 'review-1', 'instance_id': 'previous-process'})
            db.commit()
        url = f'{base}/tasks/{task_id}/ai-review'
        assert client.get(url, params={'review_run_id': 'review-1'}).json()['state'] == 'interrupted'
        assert client.post(url, json={'review_run_id': 'review-1'}).json()['state'] == 'interrupted'
        assert client.post(url, json={'review_run_id': 'review-1', 'retry': True}).status_code == 409
        lease['pending'] = False
        response = client.post(url, json={'review_run_id': 'review-1', 'retry': True})
        assert response.status_code == 409
        assert '无法确认' in response.json()['detail']
        assert client.get(url, params={'review_run_id': 'review-1'}).json()['job_id'] == 'old-job'


def test_worker_processes_all_groups_and_preserves_completed_partial(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + (tmp_path / 'worker.sqlite').as_posix(), connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    create_account(engine, 'alice', 'Alice-password-123')
    with Session(engine) as db:
        db.add(BsTask(id=1, conversation_id=1, title='synthetic', state='running', created_by=1, created_by_type='web',
                      options_snapshot_json={reference_snapshots.REVIEW_POINTER: 'review-1'}))
        db.commit()
    groups = ['a1', 'a2', 'internal_records']
    runs = {group: {'group_id': group, 'run_id': group+'-run', 'state': 'completed'} for group in groups}
    snapshot = {'state': 'completed', 'review_run_id': 'review-1', 'ai_auto_enabled': True,
                'ai_auto_groups': groups, 'runs': list(runs.values())}
    monkeypatch.setattr(reference_snapshots, 'get_reference_snapshot', lambda *_: snapshot)
    monkeypatch.setattr(jobs.knowledge_runs, 'get_reference_run', lambda _db, _task, group_id: runs[group_id])
    monkeypatch.setattr(jobs.ai_advisory_snapshots, 'advisory_group_ids', lambda _run: [1])
    pending = {'a1': False, 'a2': True, 'internal_records': True}
    def coverage(*_args):
        rows = []
        for group in groups:
            counts = {'not_started': int(pending[group]), 'pending': 0, 'completed': 0,
                      'partial': 0 if pending[group] else 1, 'failed': 0, 'not_run': 0}
            rows.append({'group_id': group, 'state': 'available', 'counts': counts, 'skipped': 0,
                         'reviewable_partial': counts['partial'], 'reviewable_failed': 0,
                         'reviewable_not_run': 0, 'unlocated_scope_reviewable': 0})
        return {'state': 'available', 'groups': rows}
    monkeypatch.setattr(jobs.ai_advisory_snapshots, 'collect_current_coverage', coverage)
    calls = []
    async def linked(_db, _task, _user, *, group_id, **_kwargs):
        calls.append(group_id)
        pending[group_id] = False
    async def negative(*_args, **_kwargs):
        calls.append('negative')
    monkeypatch.setattr(service, 'run_linked_batch_ai_advisory', linked)
    monkeypatch.setattr(service, 'run_negative_candidate_ai_advisory', negative)
    asyncio.run(jobs._run_background(engine, 1, 1, 'web', 'review-1', 'job-1', groups, False))
    assert calls == ['a2', 'internal_records']
    with Session(engine) as db:
        event = jobs._latest(db, 1, 'review-1')
        assert event.event_type == jobs.COMPLETED
        assert event.payload_json['warning'] is True


def test_worker_failure_never_records_completion(tmp_path, monkeypatch, caplog):
    engine = create_engine('sqlite:///' + (tmp_path / 'failure.sqlite').as_posix(), connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    create_account(engine, 'alice', 'Alice-password-123')
    with Session(engine) as db:
        db.add(BsTask(id=1, conversation_id=1, title='synthetic', state='running', created_by=1, created_by_type='web',
                      options_snapshot_json={reference_snapshots.REVIEW_POINTER: 'review-1'}))
        db.commit()
    snapshot = {'state': 'completed', 'review_run_id': 'review-1', 'ai_auto_enabled': True,
                'ai_auto_groups': ['a2'], 'runs': [{'group_id': 'a2', 'run_id': 'a2-run', 'state': 'completed'}]}
    monkeypatch.setattr(reference_snapshots, 'get_reference_snapshot', lambda *_: snapshot)
    monkeypatch.setattr(jobs.knowledge_runs, 'get_reference_run', lambda *_args, **_kwargs: snapshot['runs'][0])
    monkeypatch.setattr(jobs.ai_advisory_snapshots, 'advisory_group_ids', lambda _run: [1])
    status = {'group_id': 'a2', 'state': 'available', 'counts': {'not_started': 1, 'pending': 0,
              'completed': 0, 'partial': 0, 'failed': 0, 'not_run': 0}, 'skipped': 0,
              'reviewable_partial': 0, 'reviewable_failed': 0, 'reviewable_not_run': 0}
    monkeypatch.setattr(jobs.ai_advisory_snapshots, 'collect_current_coverage', lambda *_: {'groups': [status]})
    async def fail(*_args, **_kwargs):
        raise TimeoutError('synthetic timeout')
    monkeypatch.setattr(service, 'run_linked_batch_ai_advisory', fail)
    asyncio.run(jobs._run_background(engine, 1, 1, 'web', 'review-1', 'job-1', ['a2'], False))
    with Session(engine) as db:
        event = jobs._latest(db, 1, 'review-1')
        assert event.event_type == jobs.FAILED
        assert event.payload_json['exception_type'] == 'TimeoutError'
        assert event.payload_json['failure_categories'] == ['timeout']
    assert 'task_id=1' in caplog.text
    assert 'review_run_id=review-1' in caplog.text
    assert 'job_id=job-1' in caplog.text
    assert 'synthetic timeout' not in caplog.text


def test_event_dto_exposes_only_allowlisted_diagnostics():
    event = SimpleNamespace(
        sequence=7,
        event_type='check.ai_batch_progress',
        stage='check',
        payload_json={'job_id': 'job-1', 'request_id': 'request-1', 'batch_index': 2,
                      'batch_total': 5, 'prompt': 'must not be exposed'},
        created_at=None,
    )
    projected = service.event_dto(event)
    assert projected['diagnostics'] == {
        'job_id': 'job-1', 'request_id': 'request-1', 'batch_index': 2, 'batch_total': 5,
    }
    assert 'prompt' not in projected['diagnostics']
