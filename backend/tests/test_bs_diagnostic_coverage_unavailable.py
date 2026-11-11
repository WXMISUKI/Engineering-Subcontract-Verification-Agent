import pytest
from types import SimpleNamespace
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.plus.blockchain_supervision import ai_advisory_snapshots as snapshots
from app.plus.blockchain_supervision import background_ai_review as jobs
from app.plus.blockchain_supervision import knowledge_runs, reference_snapshots, service
from app.plus.blockchain_supervision.ai_current_guard import AiContextChanged
from app.plus.blockchain_supervision.models import BsTask


@pytest.mark.parametrize(
    ('frozen', 'run_task_id', 'current', 'expected'),
    [('old-rule', 1, 'current-rule', 'rule_version_mismatch'), (None, 1, 'current-rule', 'unknown'),
     ('old-rule', 2, 'current-rule', 'unknown'), ('old-rule', 1, None, 'unknown')],
)
def test_coverage_reason_requires_matching_run_identity_and_known_fingerprint(monkeypatch, frozen, run_task_id, current, expected):
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    run = {'task_id': run_task_id, 'group_id': 'a2', 'run_id': 'run-1', 'review_run_id': 'review-1',
           'state': 'completed', 'rule_fingerprint': frozen}
    monkeypatch.setattr(knowledge_runs, 'get_reference_run', lambda _db, _task, *, group_id: run if group_id == 'a2' else None)
    monkeypatch.setattr(knowledge_runs, 'rule_fingerprint', lambda: current)
    monkeypatch.setattr(snapshots, 'assert_ai_context_current', lambda *_args, **_kwargs: (_ for _ in ()).throw(AiContextChanged()))
    monkeypatch.setattr(snapshots, 'timeout_reviewable_ids', lambda *_args: set())
    monkeypatch.setattr(snapshots, 'unlocated_scope_reviewable_ids', lambda *_args: set())
    with Session(engine) as db:
        task = BsTask(id=1, conversation_id=1, title='synthetic', state='running', created_by=3, created_by_type='web',
                      options_snapshot_json={reference_snapshots.REVIEW_POINTER: 'review-1'})
        db.add(task)
        db.commit()
        coverage = snapshots.collect_current_coverage(db, task, {'ai_auto_enabled': True, 'ai_auto_groups': ['a2']})
        assert coverage['groups'][0]['state'] == 'unavailable'
        assert coverage['unavailable_reason'] == expected


@pytest.mark.parametrize('reason', ['rule_version_mismatch', 'unknown'])
def test_progress_keeps_completed_job_and_projects_unavailable_coverage(monkeypatch, reason):
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    monkeypatch.setattr(snapshots, 'collect_current_coverage', lambda *_: {
        'state': 'available', 'groups': [{'group_id': 'a2', 'state': 'unavailable'}], 'unavailable_reason': reason})
    with Session(engine) as db:
        task = BsTask(id=1, conversation_id=1, title='synthetic', state='running', created_by=3, created_by_type='web',
                      options_snapshot_json={reference_snapshots.REVIEW_POINTER: 'review-1'})
        db.add(task)
        db.commit()
        service.add_event(db, task.id, jobs.COMPLETED, payload={'job_id': 'job-1', 'review_run_id': 'review-1'})
        progress = service.task_progress(db, task, materials=[],
            snapshot={'ai_auto_enabled': True, 'steps': [{'state': 'completed'}]}, zip_summary={'containers': []})
        assert progress['ai_coverage'] == {'state': 'unavailable', 'reason': reason}
        assert progress['ai_job']['state'] == 'unavailable'
        assert progress['ai_job']['last_recorded_state'] == 'completed'
        assert progress['ai_job']['job_id'] == 'job-1'
        assert progress['phase'] != 'completed'
        assert task.state == 'running'


def test_diagnostic_projection_does_not_expose_uncontrolled_values():
    event = SimpleNamespace(sequence=1, event_type='check.ai_batch_progress', stage='check', created_at=None,
        payload_json={'job_id': 'job-1', 'request_id': '材料正文 不应出现', 'group_id': 'a2',
                      'batch_index': 2, 'batch_total': 5, 'status': 'material content',
                      'failure_categories': ['timeout', 'secret content'], 'exception_type': 'private details'})
    diagnostics = service.event_dto(event)['diagnostics']
    assert diagnostics == {'job_id': 'job-1', 'group_id': 'a2', 'batch_index': 2, 'batch_total': 5,
                           'failure_categories': ['timeout']}


@pytest.mark.parametrize('event_type', [jobs.STARTED, jobs.PROGRESS, jobs.COMPLETED, jobs.FAILED])
def test_background_lifecycle_events_have_labels_and_safe_payloads(event_type):
    event = SimpleNamespace(sequence=1, event_type=event_type, stage='check', created_at=None,
        payload_json={'job_id': 'job-1', 'review_run_id': 'review-1', 'prompt': 'private material'})
    projected = service.event_dto(event)
    assert projected['label'] != '任务动态'
    assert projected['payload'] == {}
    assert projected['diagnostics'] == {'job_id': 'job-1', 'review_run_id': 'review-1'}
