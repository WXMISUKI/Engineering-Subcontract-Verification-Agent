"""独立分包 AI 核查后台任务的状态与生命周期。"""

import asyncio
import logging
import uuid
from types import SimpleNamespace

from sqlalchemy.orm import Session

from .models import BsTask, BsTaskEvent
from . import ai_advisory_snapshots, knowledge_runs, reference_snapshots, reporting

INSTANCE_ID = uuid.uuid4().hex
ACTIVE_JOBS = {}
STARTED = 'check.ai_background_started'
COMPLETED = 'check.ai_background_completed'
FAILED = 'check.ai_background_failed'
PROGRESS = 'check.ai_background_progress'
logger = logging.getLogger(__name__)


class BackgroundReviewIncomplete(Exception):
    def __init__(self, categories=()):
        self.categories = sorted(set(categories) & {'timeout', 'invalid_response', 'input_budget', 'model_call_error'})
        super().__init__('部分 AI 核查未完成')


def project_job_state(event, *, active_job_ids, instance_id):
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
    else:
        return {'state': 'unavailable'}
    result = {'state': state, 'job_id': payload['job_id'], 'review_run_id': payload['review_run_id']}
    if state == 'completed':
        result['warning'] = payload.get('warning') is True
        result['unlocated_scope_count'] = payload.get('unlocated_scope_count', 0)
    if state == 'failed':
        result['reason'] = payload.get('reason', 'execution_failed')
        categories = payload.get('failure_categories')
        result['failure_categories'] = [value for value in categories if isinstance(value, str)
                                        and value in {'timeout', 'invalid_response', 'input_budget', 'model_call_error'}] if isinstance(categories, list) else []
    return result


def _latest(db, task_id, review_run_id):
    return (db.query(BsTaskEvent).filter(BsTaskEvent.task_id == task_id,
            BsTaskEvent.event_type.in_((STARTED, COMPLETED, FAILED)),
            BsTaskEvent.payload_json['review_run_id'].as_string() == review_run_id)
            .order_by(BsTaskEvent.sequence.desc()).first())


def job_status(db, task, review_run_id):
    if not review_run_id or (task.options_snapshot_json or {}).get(reference_snapshots.REVIEW_POINTER) != review_run_id:
        return {'state': 'unavailable'}
    state = project_job_state(_latest(db, task.id, review_run_id),
                              active_job_ids=set(ACTIVE_JOBS), instance_id=INSTANCE_ID)
    if state['state'] == 'completed' and not _completed_coverage_valid(db, task, review_run_id):
        return {'state': 'unavailable'}
    if state['state'] == 'running':
        progress = (db.query(BsTaskEvent).filter(BsTaskEvent.task_id == task.id,
                    BsTaskEvent.event_type == PROGRESS,
                    BsTaskEvent.payload_json['job_id'].as_string() == state['job_id'])
                    .order_by(BsTaskEvent.sequence.desc()).first())
        if progress:
            state['current_group'] = (progress.payload_json or {}).get('group_id')
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
                or counts['not_run'] > row.get('reviewable_not_run', 0)):
            return False
    return True


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
    if existing['state'] in ('running', 'completed') or (existing['state'] in ('failed', 'interrupted') and not retry):
        return existing
    if existing['state'] == 'unavailable':
        raise ValueError('已有后台任务记录无法核实')
    if service.current_ai_advisory_pending(db, task):
        raise ValueError('已有 AI 核查仍在运行或租约未到期，请稍后再试')
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
    warning, unlocated_count = False, 0
    try:
        for group in groups:
            with Session(bind) as db:
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
                retryable = (counts['failed'] - status['reviewable_failed'] + counts['partial'] - status['reviewable_partial']
                             + counts['not_run'] - status['reviewable_not_run'])
                service.add_event(db, task_id, PROGRESS, stage='check', payload={
                    'job_id': job_id, 'review_run_id': review_run_id, 'group_id': group}, commit=False)
                db.commit()
                if counts['not_started'] or (retry and retryable > 0):
                    ids = ai_advisory_snapshots.advisory_group_ids(run)
                    if ids:
                        await service.run_linked_batch_ai_advisory(db, task, actor, group_id=group,
                            finding_ids=ids, resume_partial=retry, expected_run_id=run['run_id'], retry_failed=retry)
                    if group == 'a1':
                        await service.run_negative_candidate_ai_advisory(db, task, actor, max_batches=64,
                            expected_run_id=run['run_id'], retry=retry)
            with Session(bind) as db:
                task = db.get(BsTask, task_id)
                snapshot = reference_snapshots.get_reference_snapshot(db, task) if task else None
                if not snapshot or snapshot.get('review_run_id') != review_run_id:
                    raise ValueError('核查运行已变化')
                coverage = ai_advisory_snapshots.collect_current_coverage(db, task, snapshot, reporting.report_review_blocking)
                status = next((row for row in (coverage or {}).get('groups', []) if row.get('group_id') == group), None)
                if not status or status.get('state') != 'available':
                    raise ValueError('核查状态不可用')
                counts = status['counts']
                if (counts['not_started'] or counts['pending'] or counts['failed'] > status['reviewable_failed']
                        or counts['partial'] > status['reviewable_partial'] or counts['not_run'] > status['reviewable_not_run']):
                    raise BackgroundReviewIncomplete(status.get('failure_categories') or [])
                warning |= bool(status['skipped'] or counts['partial'] or counts['not_run'] or counts['failed'])
                unlocated_count += status.get('unlocated_scope_reviewable', 0)
        with Session(bind) as db:
            task = db.query(BsTask).filter_by(id=task_id).with_for_update().one_or_none()
            if not task or (task.options_snapshot_json or {}).get(reference_snapshots.REVIEW_POINTER) != review_run_id:
                raise ValueError('核查运行已变化')
            service.add_event(db, task_id, COMPLETED, stage='check', payload={
                'job_id': job_id, 'review_run_id': review_run_id, 'instance_id': INSTANCE_ID,
                'warning': warning, 'unlocated_scope_count': unlocated_count}, commit=False)
            db.commit()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning('AI background job failed task=%s type=%s', task_id, type(exc).__name__)
        try:
            with Session(bind) as db:
                task = db.query(BsTask).filter_by(id=task_id).with_for_update().one_or_none()
                if task and (task.options_snapshot_json or {}).get(reference_snapshots.REVIEW_POINTER) == review_run_id:
                    service.add_event(db, task_id, FAILED, stage='check', payload={
                        'job_id': job_id, 'review_run_id': review_run_id, 'instance_id': INSTANCE_ID,
                        'reason': 'execution_failed',
                        'failure_categories': exc.categories if isinstance(exc, BackgroundReviewIncomplete) else []}, commit=False)
                    db.commit()
        except Exception:
            logger.warning('AI background job failure could not be recorded task=%s', task_id)
