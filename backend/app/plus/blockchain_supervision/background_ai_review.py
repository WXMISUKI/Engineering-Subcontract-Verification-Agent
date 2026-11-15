"""独立分包 AI 核查后台任务的状态与生命周期。"""

import asyncio
import logging
import uuid
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy.orm import Session

from .models import BsTask, BsTaskEvent
from . import ai_advisory_snapshots, knowledge_runs, reference_snapshots, reporting

INSTANCE_ID = uuid.uuid4().hex
ACTIVE_JOBS = {}
STARTED = 'check.ai_background_started'
COMPLETED = 'check.ai_background_completed'
FAILED = 'check.ai_background_failed'
REVIEW_REQUIRED = 'check.ai_background_review_required'
PROGRESS = 'check.ai_background_progress'
logger = logging.getLogger(__name__)


class BackgroundReviewIncomplete(Exception):
    def __init__(self, categories=()):
        self.categories = sorted(set(categories) & ai_advisory_snapshots.FAILURE_CATEGORIES)
        super().__init__('部分 AI 核查未完成')


def project_job_state(event, *, active_job_ids, instance_id, progress_event=None, now=None,
                      stale_after_seconds=1800):
    if event is None:
        return {'state': 'not_started'}
    payload = event.payload_json or {}
    if (not isinstance(payload, dict) or not isinstance(payload.get('job_id'), str)
            or not payload['job_id'] or not isinstance(payload.get('review_run_id'), str)
            or not payload['review_run_id']):
        return {'state': 'unavailable'}
    if event.event_type == STARTED:
        state = 'running' if payload.get('instance_id') == instance_id and payload['job_id'] in active_job_ids else 'interrupted'
    elif event.event_type == COMPLETED:
        state = 'completed'
    elif event.event_type == FAILED:
        state = 'failed'
    elif event.event_type == REVIEW_REQUIRED:
        state = 'review_required'
    else:
        return {'state': 'unavailable'}
    result = {'state': state, 'job_id': payload['job_id'], 'review_run_id': payload['review_run_id']}
    if state in {'running', 'interrupted', 'failed'}:
        from .service import _safe_event_diagnostics
        progress_payload = _safe_event_diagnostics(progress_event.payload_json) if progress_event is not None else {}
        last_event = progress_event or event
        last_at = getattr(last_event, 'created_at', None)
        observed_at = now or datetime.now(last_at.tzinfo if last_at and last_at.tzinfo else None)
        if last_at is not None and last_at.tzinfo is None and observed_at.tzinfo is not None:
            last_at = last_at.replace(tzinfo=observed_at.tzinfo)
        elif last_at is not None and last_at.tzinfo is not None and observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=last_at.tzinfo)
        age_seconds = max(0, int((observed_at - last_at).total_seconds())) if last_at else None
        result.update({
            'request_id': progress_payload.get('request_id'),
            'current_group': progress_payload.get('group_id') or _safe_event_diagnostics({'group_id': payload.get('current_group')}).get('group_id'),
            'current_batch': ({'index': progress_payload.get('batch_index'),
                               'total': progress_payload.get('batch_total'),
                               'status': progress_payload.get('status')}
                              if progress_payload.get('batch_index') is not None else None),
            'last_progress_at': last_at.isoformat() if last_at else None,
            'progress_age_seconds': age_seconds,
            'stale_candidate': state == 'running' and (age_seconds is None or age_seconds >= stale_after_seconds),
            'diagnostic_state': ('无法核实' if progress_event is None else
                                 '已中断' if state == 'interrupted' else
                                 '执行失败' if state == 'failed' else '可定位'),
        })
    if state == 'completed':
        result['warning'] = payload.get('warning') is True
        result['unlocated_scope_count'] = payload.get('unlocated_scope_count', 0)
        no_signal_count = payload.get('not_assessed_no_signal_count', 0)
        if type(no_signal_count) is int and no_signal_count >= 0:
            result['not_assessed_no_signal_count'] = no_signal_count
        needs_human_review = payload.get('needs_human_review')
        if type(needs_human_review) is bool:
            result['needs_human_review'] = needs_human_review
        else:
            result['needs_human_review'] = result['warning'] and not (
                type(no_signal_count) is int and no_signal_count > 0)
    if state == 'failed':
        result['reason'] = payload.get('reason', 'execution_failed')
        categories = payload.get('failure_categories')
        result['failure_categories'] = [value for value in categories if isinstance(value, str)
                                        and value in ai_advisory_snapshots.FAILURE_CATEGORIES] if isinstance(categories, list) else []
    if state == 'review_required':
        result['reason'] = payload.get('reason', 'incomplete_material_scope')
        result['failure_categories'] = []
        result['diagnostic_state'] = '待人工复核'
    return result


def _latest(db, task_id, review_run_id):
    return (db.query(BsTaskEvent).filter(BsTaskEvent.task_id == task_id,
            BsTaskEvent.event_type.in_((STARTED, COMPLETED, FAILED, REVIEW_REQUIRED)),
            BsTaskEvent.payload_json['review_run_id'].as_string() == review_run_id)
            .order_by(BsTaskEvent.sequence.desc()).first())


def job_status(db, task, review_run_id):
    if not review_run_id or (task.options_snapshot_json or {}).get(reference_snapshots.REVIEW_POINTER) != review_run_id:
        return {'state': 'unavailable'}
    state = project_job_state(_latest(db, task.id, review_run_id),
                              active_job_ids=set(ACTIVE_JOBS), instance_id=INSTANCE_ID)
    if state['state'] == 'completed' and not _completed_coverage_valid(db, task, review_run_id):
        return {'state': 'unavailable', 'last_recorded_state': 'completed',
                'job_id': state['job_id'], 'review_run_id': state['review_run_id'], 'diagnostic_state': '无法核实'}
    if state['state'] == 'review_required':
        state['scope_gap_review_available'] = _scope_gap_review_available(db, task, review_run_id)
        if not state['scope_gap_review_available']:
            return {'state': 'unavailable', 'last_recorded_state': 'review_required',
                    'job_id': state['job_id'], 'review_run_id': state['review_run_id'],
                    'diagnostic_state': '无法核实'}
    if state['state'] in {'running', 'interrupted', 'failed'}:
        progress = (db.query(BsTaskEvent).filter(BsTaskEvent.task_id == task.id,
                    BsTaskEvent.event_type.in_((PROGRESS, 'check.ai_batch_progress')),
                    BsTaskEvent.payload_json['job_id'].as_string() == state['job_id'])
                    .order_by(BsTaskEvent.sequence.desc()).first())
        state = project_job_state(_latest(db, task.id, review_run_id),
                                  active_job_ids=set(ACTIVE_JOBS), instance_id=INSTANCE_ID,
                                  progress_event=progress)
    if state['state'] == 'failed' and state.get('failure_categories') == ['incomplete_material_scope']:
        try:
            state['scope_gap_review_available'] = _scope_gap_review_available(db, task, review_run_id)
            if state['scope_gap_review_available']:
                # Older events used FAILED for this controlled coverage outcome; keep
                # the immutable event while projecting its current meaning accurately.
                state.update(state='review_required', reason='incomplete_material_scope', failure_categories=[],
                             diagnostic_state='待人工复核')
        except Exception:
            logger.warning('AI scope gap review availability could not be verified task_id=%s', task.id, exc_info=True)
            state['scope_gap_review_available'] = False
    return state


def _completed_coverage_valid(db, task, review_run_id):
    snapshot = reference_snapshots.get_reference_snapshot(db, task)
    if not snapshot or snapshot.get('review_run_id') != review_run_id or snapshot.get('state') != 'completed':
        return False
    groups = snapshot.get('ai_auto_groups')
    if not isinstance(groups, list):
        return False
    coverage = ai_advisory_snapshots.collect_current_coverage(db, task, snapshot, reporting.report_review_blocking)
    rows = (coverage or {}).get('groups')
    if not isinstance(rows, list) or {row.get('group_id') for row in rows} != set(groups):
        return False
    for row in rows:
        counts = row.get('counts') or {}
        if (row.get('state') != 'available' or any(type(counts.get(key)) is not int for key in
                ('not_started', 'pending', 'failed', 'partial', 'not_run'))
                or counts['not_started'] or counts['pending']
                or counts['failed'] > row.get('reviewable_failed', 0)
                or counts['partial'] > row.get('reviewable_partial', 0)
                or ai_advisory_snapshots.blocking_not_run_count(row) != 0):
            return False
    return True


def _scope_gap_review_available(db, task, review_run_id):
    snapshot = reference_snapshots.get_reference_snapshot(db, task)
    if (not snapshot or snapshot.get('state') != 'completed' or snapshot.get('review_run_id') != review_run_id
            or snapshot.get('ai_auto_enabled') is not True):
        return False
    groups = snapshot.get('ai_auto_groups')
    if not isinstance(groups, list) or not groups or len(set(groups)) != len(groups):
        return False
    coverage = ai_advisory_snapshots.collect_current_coverage(db, task, snapshot, reporting.report_review_blocking)
    rows = (coverage or {}).get('groups')
    if ((coverage or {}).get('state') != 'available' or not isinstance(rows, list)
            or len(rows) != len(groups) or {row.get('group_id') for row in rows} != set(groups)):
        return False
    has_gap = False
    for row in rows:
        counts = row.get('counts') or {}
        if (row.get('state') != 'available' or any(type(counts.get(key)) is not int or counts[key] < 0
                for key in ('not_started', 'pending', 'failed', 'partial', 'not_run'))
                or counts['not_started'] or counts['pending']
                or counts['failed'] > row.get('reviewable_failed', 0)
                or counts['partial'] > row.get('reviewable_partial', 0)):
            return False
        blocking = ai_advisory_snapshots.blocking_not_run_count(row)
        gap = row.get('reading_gap_not_run', 0)
        if blocking is None or type(gap) is not int or gap < 0 or blocking != gap:
            return False
        if gap and set(row.get('failure_categories') or []) != {'incomplete_material_scope'}:
            return False
        has_gap |= gap > 0
    return has_gap


def running_job(db, task):
    review_id = (task.options_snapshot_json or {}).get(reference_snapshots.REVIEW_POINTER)
    return bool(review_id and job_status(db, task, review_id)['state'] == 'running')


def submit_background_job(db, task_id, user, review_run_id, *, retry=False):
    from . import service
    db.rollback()
    task = db.query(BsTask).filter_by(id=task_id).with_for_update().one_or_none()
    if task is None or task.created_by != user.id or task.created_by_type != user.user_type:
        raise PermissionError('任务不存在或无访问权限')
    if task.state not in ('running', 'review'):
        raise ValueError('当前任务不能进行 AI 辅助核查')
    snapshot = reference_snapshots.get_reference_snapshot(db, task)
    if (not snapshot or snapshot.get('state') != 'completed' or snapshot.get('review_run_id') != review_run_id
            or snapshot.get('ai_auto_enabled') is not True):
        raise ValueError('核查运行已变化，请刷新后重试')
    groups = snapshot.get('ai_auto_groups')
    runs = snapshot.get('runs') or []
    if (not isinstance(groups, list) or any(not isinstance(group, str) for group in groups)
            or len(set(groups)) != len(groups) or any(group not in reference_snapshots.CHECK_GROUPS for group in groups)
            or any(not any(run.get('group_id') == group and run.get('state') == 'completed' and run.get('run_id')
                           for run in runs) for group in groups)):
        raise ValueError('AI 核查范围或运行记录无效')
    existing = job_status(db, task, review_run_id)
    if existing['state'] in ('running', 'completed', 'review_required') or (existing['state'] in ('failed', 'interrupted') and not retry):
        return existing
    if existing['state'] == 'unavailable':
        raise ValueError('已有后台任务记录无法核实')
    if existing['state'] == 'interrupted':
        # 未收到终态的 started 可能属于另一工作进程，不能以本进程内存为空
        # 推断它已停止；任务行锁只串行化提交，不证明旧工作已退出。
        raise ValueError('无法确认先前后台任务已停止，请联系人工复核')
    if service.current_ai_advisory_pending(db, task):
        raise ValueError('已有 AI 核查仍在运行或租约未到期，请稍后再试')
    if retry and existing['state'] == 'failed':
        try:
            coverage = ai_advisory_snapshots.collect_current_coverage(db, task, snapshot, reporting.report_review_blocking)
            rows = (coverage or {}).get('groups')
            if (not isinstance(coverage, dict) or coverage.get('state') != 'available'
                    or not isinstance(rows, list) or len(rows) != len(groups)
                    or {row.get('group_id') for row in rows if isinstance(row, dict)} != set(groups)
                    or any(not isinstance(row, dict) or row.get('state') != 'available'
                           or not isinstance(row.get('counts'), dict)
                           or any(type(row['counts'].get(key)) is not int or row['counts'][key] < 0
                                  for key in ('not_started', 'pending', 'completed', 'partial', 'failed', 'not_run'))
                           or row['counts']['pending'] for row in rows)):
                raise ValueError('当前 AI 覆盖状态无法核实，请刷新或联系人工复核')
        except Exception as exc:
            raise ValueError('当前 AI 覆盖状态无法核实，请刷新或联系人工复核') from exc
    job_id = uuid.uuid4().hex
    service.add_event(db, task_id, STARTED, stage='check', payload={
        'job_id': job_id, 'review_run_id': review_run_id, 'instance_id': INSTANCE_ID,
        'groups': list(groups), 'retry': bool(retry)}, commit=False)
    db.commit()
    bind = db.get_bind()
    worker = asyncio.create_task(_run_background(bind, task_id, user.id, user.user_type,
                                                  review_run_id, job_id, list(groups), bool(retry)))
    ACTIVE_JOBS[job_id] = worker
    worker.add_done_callback(lambda _task: ACTIVE_JOBS.pop(job_id, None))
    return {'state': 'running', 'job_id': job_id, 'review_run_id': review_run_id}


async def _run_background(bind, task_id, user_id, user_type, review_run_id, job_id, groups, retry):
    from . import service
    from ...standalone.identity import User
    actor = SimpleNamespace(id=user_id, user_type=user_type)
    warning, needs_human_review = False, False
    unlocated_count, not_assessed_no_signal_count = 0, 0
    deferred_scope_gap = False
    phase = 'checking_group'
    try:
        for group in groups:
            with Session(bind) as db:
                phase = 'checking_group'
                task = db.query(BsTask).filter_by(id=task_id).with_for_update().one_or_none()
                account = db.get(User, user_id)
                if (not task or not account or not account.is_active or task.created_by != user_id
                        or task.created_by_type != user_type or task.state not in ('running', 'review')):
                    raise ValueError('任务或账号不可用')
                snapshot = reference_snapshots.get_reference_snapshot(db, task)
                if not snapshot or snapshot.get('review_run_id') != review_run_id or snapshot.get('state') != 'completed':
                    raise ValueError('核查运行已变化')
                run = knowledge_runs.get_reference_run(db, task, group_id=group)
                if not run or run.get('state') != 'completed':
                    raise ValueError('核查组尚未完成')
                coverage = ai_advisory_snapshots.collect_current_coverage(db, task, snapshot, reporting.report_review_blocking)
                status = next((row for row in (coverage or {}).get('groups', []) if row.get('group_id') == group), None)
                if not status or status.get('state') != 'available':
                    raise ValueError('核查状态不可用')
                counts = status['counts']
                if counts['pending']:
                    raise ValueError('先前核查仍在运行或租约未到期')
                blocking_not_run = ai_advisory_snapshots.blocking_not_run_count(status)
                if blocking_not_run is None:
                    raise ValueError('未执行项覆盖状态无效')
                reading_gap = status.get('reading_gap_not_run', 0)
                deferred_retry_gap = (reading_gap if type(reading_gap) is int and reading_gap > 0
                                      and reading_gap == blocking_not_run
                                      and set(status.get('failure_categories') or []) == {'incomplete_material_scope'}
                                      else 0)
                retryable = (counts['failed'] - status['reviewable_failed'] + counts['partial'] - status['reviewable_partial']
                             + blocking_not_run - deferred_retry_gap)
                service.add_event(db, task_id, PROGRESS, stage='check', payload={
                    'job_id': job_id, 'review_run_id': review_run_id, 'group_id': group}, commit=False)
                db.commit()
                if counts['not_started'] or (retry and retryable > 0):
                    phase = 'running_advisory'
                    ids = ai_advisory_snapshots.advisory_group_ids(run)
                    if ids:
                        await service.run_linked_batch_ai_advisory(db, task, actor, group_id=group,
                            finding_ids=ids, resume_partial=retry, expected_run_id=run['run_id'], retry_failed=retry,
                            background_job_id=job_id)
                    if group == 'a1':
                        await service.run_negative_candidate_ai_advisory(db, task, actor, max_batches=64,
                            expected_run_id=run['run_id'], retry=retry, background_job_id=job_id)
            with Session(bind) as db:
                phase = 'checking_coverage'
                task = db.get(BsTask, task_id)
                snapshot = reference_snapshots.get_reference_snapshot(db, task) if task else None
                if not snapshot or snapshot.get('review_run_id') != review_run_id:
                    raise ValueError('核查运行已变化')
                coverage = ai_advisory_snapshots.collect_current_coverage(db, task, snapshot, reporting.report_review_blocking)
                status = next((row for row in (coverage or {}).get('groups', []) if row.get('group_id') == group), None)
                if not status or status.get('state') != 'available':
                    raise ValueError('核查状态不可用')
                counts = status['counts']
                blocking_not_run = ai_advisory_snapshots.blocking_not_run_count(status)
                reading_gap = status.get('reading_gap_not_run', 0)
                defer_gap = (type(reading_gap) is int and reading_gap > 0
                             and blocking_not_run == reading_gap
                             and set(status.get('failure_categories') or []) == {'incomplete_material_scope'})
                if (counts['not_started'] or counts['pending'] or counts['failed'] > status['reviewable_failed']
                        or counts['partial'] > status['reviewable_partial']
                        or blocking_not_run is None or (blocking_not_run > 0 and not defer_gap)):
                    raise BackgroundReviewIncomplete(status.get('failure_categories') or [])
                deferred_scope_gap |= defer_gap
                warning |= bool(status['skipped'] or counts['partial'] or counts['not_run'] or counts['failed'])
                unlocated_count += status.get('unlocated_scope_reviewable', 0)
                not_assessed_no_signal_count += status.get('not_assessed_no_signal', 0)
                needs_human_review |= bool(
                    status['skipped'] or counts['partial'] or counts['failed']
                    or status.get('reviewable_not_run', 0) > status.get('unlocated_scope_reviewable', 0))
        if deferred_scope_gap:
            with Session(bind) as db:
                phase = 'recording_review_required'
                task = db.query(BsTask).filter_by(id=task_id).with_for_update().one_or_none()
                if not task or (task.options_snapshot_json or {}).get(reference_snapshots.REVIEW_POINTER) != review_run_id:
                    raise ValueError('核查运行已变化')
                service.add_event(db, task_id, REVIEW_REQUIRED, stage='check', payload={
                    'job_id': job_id, 'review_run_id': review_run_id, 'instance_id': INSTANCE_ID,
                    'reason': 'incomplete_material_scope', 'failure_categories': []}, commit=False)
                db.commit()
            return
        with Session(bind) as db:
            phase = 'finishing_job'
            task = db.query(BsTask).filter_by(id=task_id).with_for_update().one_or_none()
            if not task or (task.options_snapshot_json or {}).get(reference_snapshots.REVIEW_POINTER) != review_run_id:
                raise ValueError('核查运行已变化')
            service.add_event(db, task_id, COMPLETED, stage='check', payload={
                'job_id': job_id, 'review_run_id': review_run_id, 'instance_id': INSTANCE_ID,
                'warning': warning, 'needs_human_review': needs_human_review,
                'unlocated_scope_count': unlocated_count,
                'not_assessed_no_signal_count': not_assessed_no_signal_count}, commit=False)
            db.commit()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        failure_categories = (exc.categories if isinstance(exc, BackgroundReviewIncomplete)
                              else ['timeout'] if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) else [])
        if deferred_scope_gap:
            failure_categories = sorted(set(failure_categories) | {'incomplete_material_scope'})
        diagnostic = {'task_id': task_id, 'review_run_id': review_run_id, 'job_id': job_id,
                      'group_id': group if 'group' in locals() else None,
                      'phase': phase,
                      'exception_type': type(exc).__name__,
                      'failure_categories': failure_categories}
        logger.warning('AI background job failed task_id=%s review_run_id=%s job_id=%s group_id=%s '
                       'phase=%s exception_type=%s failure_categories=%s', task_id, review_run_id, job_id,
                       diagnostic['group_id'], phase, diagnostic['exception_type'], failure_categories,
                       extra={'diagnostic': diagnostic})
        try:
            with Session(bind) as db:
                task = db.query(BsTask).filter_by(id=task_id).with_for_update().one_or_none()
                if task and (task.options_snapshot_json or {}).get(reference_snapshots.REVIEW_POINTER) == review_run_id:
                    service.add_event(db, task_id, FAILED, stage='check', payload={
                        'job_id': job_id, 'review_run_id': review_run_id, 'instance_id': INSTANCE_ID,
                        'reason': 'execution_failed',
                        'exception_type': type(exc).__name__,
                        'failure_categories': failure_categories}, commit=False)
                    db.commit()
        except Exception as record_exc:
            record_diagnostic = {
                'task_id': task_id, 'review_run_id': review_run_id, 'job_id': job_id,
                'exception_type': type(record_exc).__name__}
            logger.warning('AI background job failure could not be recorded task_id=%s review_run_id=%s '
                           'job_id=%s exception_type=%s', task_id, review_run_id, job_id,
                           type(record_exc).__name__, extra={'diagnostic': record_diagnostic})
