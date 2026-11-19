from types import SimpleNamespace

from app.plus.blockchain_supervision import (
    background_ai_review, execution_summary, reference_snapshots, service,
)
from app.plus.blockchain_supervision.execution_summary import build


def make_task(state='running', run_id='review-1'):
    options = {reference_snapshots.REVIEW_POINTER: run_id} if run_id else {}
    return SimpleNamespace(id=1, state=state, options_snapshot_json=options,
                           created_by=1, created_by_type='web')


def readiness(**overrides):
    base = {
        'execution': {'state': 'incomplete'},
        'finding_review': {'pending_actions': 2, 'pending_machine_review': 1, 'pending_manual_prompt': 1},
        'blocking_reasons': [{'code': 'ai_execution_incomplete'}],
        'warnings': [],
        'confirmed_output_ready': False,
    }
    base.update(overrides)
    return base


def reading(**overrides):
    base = {
        'status': 'incomplete',
        'materials': [{'material_id': 1, 'parse_state': 'done', 'reading_complete': False,
                       'unread_pages': [2, 3], 'suspect_pages': [4], 'review_pages': []}],
    }
    base.update(overrides)
    return base


def patch_sources(monkeypatch, *, snapshot, job, health):
    monkeypatch.setattr(reference_snapshots, 'get_reference_snapshot', lambda *_: snapshot)
    monkeypatch.setattr(background_ai_review, 'job_status', lambda *_: job)
    monkeypatch.setattr(service, 'task_activity_health', lambda *a, **k: health)


def test_active_ai_job_is_presented_as_running_and_not_as_stale(monkeypatch):
    patch_sources(
        monkeypatch,
        snapshot={'ai_auto_enabled': True, 'ai_auto_groups': ['a2']},
        job={'state': 'running', 'queued': False, 'job_id': 'job-1', 'current_group': 'a2',
             'last_progress_at': '2026-11-18T08:00:00+08:00', 'diagnostic_state': '可定位'},
        health={'stale_candidate': True, 'last_event_type': 'check.ai_background_progress', 'age_seconds': 2400},
    )

    result = build(None, make_task(), readiness=readiness(), reading=reading())

    assert result['schema_version'] == execution_summary.SCHEMA_VERSION
    assert result['presentation_status'] == 'running'
    assert result['activity']['state'] == 'active_observed'
    assert result['ai_advisory']['state'] == 'running'
    assert result['ai_advisory']['current_group'] == 'a2'
    assert result['execution']['reason_codes'] == ['ai_execution_incomplete']


def test_scope_projection_discloses_disabled_followup_features(monkeypatch):
    patch_sources(
        monkeypatch,
        snapshot={'ai_auto_enabled': False, 'ai_auto_groups': []},
        job={'state': 'completed'},
        health={'stale_candidate': False, 'last_event_type': 'check.completed', 'age_seconds': 5},
    )

    result = build(None, make_task(), readiness=readiness(), reading=reading())

    assert result['scope']['disabled_features'] == ['amount_ratio', 'workload_ratio']
    assert result['scope']['uncovered_rule_ids'] == []
    assert result['scope']['rule_source_summary'] is None


def test_pending_human_and_reading_quality_block_presentation_but_not_report_gate(monkeypatch):
    patch_sources(
        monkeypatch,
        snapshot={'ai_auto_enabled': True, 'ai_auto_groups': ['a2']},
        job={'state': 'completed'},
        health={'stale_candidate': False, 'last_event_type': 'check.ai_background_completed', 'age_seconds': 15},
    )

    result = build(None, make_task(), readiness=readiness(), reading=reading())

    assert result['presentation_status'] == 'waiting_human'
    assert result['blocking'] is True
    assert result['human_review']['state'] == 'pending'
    assert result['human_review']['pending_count'] == 2
    assert result['quality']['state'] == 'pending'
    assert result['quality']['unread_page_count'] == 2
    assert result['quality']['suspect_page_count'] == 1
    assert result['activity']['state'] == 'idle'


def test_parse_failure_is_reported_as_quality_failure_without_claiming_ocr_error(monkeypatch):
    patch_sources(
        monkeypatch,
        snapshot={'ai_auto_enabled': False, 'ai_auto_groups': []},
        job={'state': 'not_required'},
        health={'stale_candidate': False, 'last_event_type': 'material.parse_failed', 'age_seconds': 4},
    )
    quality = {'status': 'unknown', 'materials': [
        {'material_id': 1, 'parse_state': 'failed', 'reading_complete': False,
         'unread_pages': [], 'suspect_pages': [], 'review_pages': []},
    ]}

    result = build(None, make_task(), readiness=readiness(
        finding_review={'pending_actions': 0}), reading=quality)

    assert result['quality']['state'] == 'failed'
    assert result['quality']['reason_codes'] == ['material_parse_failed']
    assert result['ai_advisory']['state'] == 'not_required'
    assert result['human_review']['state'] == 'completed'


def test_done_task_keeps_done_presentation(monkeypatch):
    patch_sources(
        monkeypatch,
        snapshot={'ai_auto_enabled': True, 'ai_auto_groups': ['a2']},
        job={'state': 'completed'},
        health={'stale_candidate': False, 'last_event_type': 'task.completed', 'age_seconds': 3},
    )

    result = build(None, make_task(state='done'),
                   readiness=readiness(confirmed_output_ready=True, blocking_reasons=[]),
                   reading=reading(status='complete', materials=[
                       {'material_id': 1, 'parse_state': 'done', 'reading_complete': True,
                        'unread_pages': [], 'suspect_pages': [], 'review_pages': []}]))

    assert result['presentation_status'] == 'done'
    assert result['report_ready'] is True
    assert result['quality']['state'] == 'complete'
