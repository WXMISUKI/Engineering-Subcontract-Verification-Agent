"""AI 辅助核查的持久任务仓储：入队、租约、心跳、终态和过期恢复。"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .models import BsAiReviewJob

# 租约需覆盖单批模型调用的最长时间：批间会续租，单批超时恢复也会续租，
# 但过短的租约会让存活执行者被误判为死亡并重复计费。
LEASE_SECONDS = 300
MAX_ATTEMPTS = 3


def utc_naive_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _snapshot(row: BsAiReviewJob) -> dict:
    return {
        "id": row.id,
        "task_id": row.task_id,
        "review_run_id": row.review_run_id,
        "groups": list(row.groups_json or []),
        "state": row.state,
        "attempt": row.attempt,
        "lease_token": row.lease_token,
        "lease_expires_at": row.lease_expires_at,
        "last_error": row.last_error,
    }


def get_job(db: Session, task_id: int, review_run_id: str) -> BsAiReviewJob | None:
    return db.query(BsAiReviewJob).filter_by(
        task_id=task_id, review_run_id=review_run_id).one_or_none()


def get_job_by_id(db: Session, job_id: str) -> BsAiReviewJob | None:
    return db.query(BsAiReviewJob).filter_by(id=job_id).one_or_none()


def list_queued(db: Session) -> list[BsAiReviewJob]:
    return (db.query(BsAiReviewJob).filter(BsAiReviewJob.state == "queued")
            .order_by(BsAiReviewJob.created_at.asc(), BsAiReviewJob.id.asc()).all())


def enqueue(db: Session, task_id: int, review_run_id: str, groups, *,
            retry: bool = False) -> dict:
    """幂等入队；运行中的未过期任务不重复入队，失败/过期可重试。"""
    row = get_job(db, task_id, review_run_id)
    now = utc_naive_now()
    if row is None:
        row = BsAiReviewJob(
            id=uuid.uuid4().hex, task_id=task_id, review_run_id=review_run_id,
            groups_json=list(groups or []), state="queued", attempt=1,
        )
        db.add(row)
    elif row.state == "running" and row.lease_expires_at and row.lease_expires_at > now:
        return _snapshot(row)
    elif row.state in {"completed", "review_required"} and not retry:
        return _snapshot(row)
    else:
        row.state = "queued"
        row.groups_json = list(groups or row.groups_json or [])
        row.attempt = (row.attempt or 0) + 1
        row.lease_token = None
        row.lease_expires_at = None
        row.last_error = None
    db.flush()
    return _snapshot(row)


def claim(db: Session, job_id: str, *, lease_seconds: int = LEASE_SECONDS) -> dict | None:
    """原子领取一个 queued 或租约已过期的 running 任务。"""
    now = utc_naive_now()
    row = (db.query(BsAiReviewJob).filter(BsAiReviewJob.id == job_id)
           .with_for_update().one_or_none())
    if row is None:
        return None
    expired = row.lease_expires_at is not None and row.lease_expires_at <= now
    if row.state not in {"queued", "running"} or (row.state == "running" and not expired):
        return None
    row.state = "running"
    row.lease_token = uuid.uuid4().hex
    row.lease_expires_at = now + timedelta(seconds=max(1, int(lease_seconds)))
    row.last_error = None
    db.flush()
    return _snapshot(row)


def heartbeat(db: Session, job_id: str, lease_token: str, *,
              lease_seconds: int = LEASE_SECONDS) -> bool:
    row = (db.query(BsAiReviewJob).filter(
        BsAiReviewJob.id == job_id, BsAiReviewJob.state == "running",
        BsAiReviewJob.lease_token == lease_token).with_for_update().one_or_none())
    if row is None:
        return False
    row.lease_expires_at = utc_naive_now() + timedelta(seconds=max(1, int(lease_seconds)))
    db.flush()
    return True


def finish(db: Session, job_id: str, lease_token: str, state: str,
           error: str | None = None) -> bool:
    """只有仍持有租约的执行者才能写终态，避免旧执行者覆盖新结果。"""
    if state not in {"completed", "failed", "interrupted", "review_required"}:
        raise ValueError("非法任务终态")
    row = (db.query(BsAiReviewJob).filter(
        BsAiReviewJob.id == job_id, BsAiReviewJob.state == "running",
        BsAiReviewJob.lease_token == lease_token).with_for_update().one_or_none())
    if row is None:
        return False
    row.state = state
    row.lease_token = None
    row.lease_expires_at = None
    row.last_error = (error or "")[:1000] or None
    db.flush()
    return True


def abandon(db: Session, job_id: str, reason: str) -> bool:
    """放弃未领取的排队任务（任务已结束、运行代次变化或范围失效）。"""
    row = (db.query(BsAiReviewJob).filter(
        BsAiReviewJob.id == job_id, BsAiReviewJob.state == "queued")
        .with_for_update().one_or_none())
    if row is None:
        return False
    row.state = "failed"
    row.last_error = reason[:1000]
    db.flush()
    return True


def requeue_expired(db: Session, *, max_attempts: int = MAX_ATTEMPTS) -> list[dict]:
    """启动/巡检恢复：租约过期的 running 任务重新排队，超过次数则失败。

    返回所有被处理任务的处理后快照（``state`` 为 ``queued`` 或 ``failed``），
    调用方据此写恢复或失败事件；没有租约持有者时这些任务不可能再自行推进。
    """
    now = utc_naive_now()
    rows = (db.query(BsAiReviewJob).filter(
        BsAiReviewJob.state == "running",
        BsAiReviewJob.lease_expires_at.isnot(None),
        BsAiReviewJob.lease_expires_at <= now,
    ).with_for_update().all())
    handled = []
    for row in rows:
        if (row.attempt or 0) >= max_attempts:
            row.state = "failed"
            row.last_error = "租约过期，已达最大重试次数"
        else:
            row.state = "queued"
            row.attempt = (row.attempt or 0) + 1
            row.last_error = "租约过期，已重新排队"
        row.lease_token = None
        row.lease_expires_at = None
        handled.append(_snapshot(row))
    db.flush()
    return handled
