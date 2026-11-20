"""单页补读候选的持久状态与租约仓储。

候选正文只在详情请求中返回，普通列表/事件不复制正文；所有状态迁移都以
状态 + 租约条件更新，避免并发线程互相覆盖。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, update
from sqlalchemy.orm import Session

from .models import BsPageTextCandidate, BsTask

PREPARING_LEASE_SECONDS = 180
PENDING_REVIEW_SECONDS = 7 * 24 * 60 * 60
ACTIVE_STATES = {"requested", "preparing", "pending_review", "confirmed"}
TERMINAL_STATES = {"failed", "expired", "invalidated", "consumed"}


def utc_naive_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _utc_naive(value: datetime | None) -> datetime:
    if value is None:
        return utc_naive_now()
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _snapshot(row: BsPageTextCandidate) -> dict:
    return {
        "id": row.id,
        "task_id": row.task_id,
        "material_id": row.material_id,
        "page_no": row.page_no,
        "status": row.status,
        "candidate_version": row.candidate_version,
        "review_run_id": row.review_run_id,
        "source_sha256": row.source_sha256,
        "input_material_fingerprint": row.input_material_fingerprint,
        "input_rule_fingerprint": row.input_rule_fingerprint,
        "candidate_text": row.candidate_text,
        "reviewed_text": row.reviewed_text,
        "reviewed_by": row.reviewed_by,
        "reviewed_by_type": row.reviewed_by_type,
        "reviewed_at": row.reviewed_at.isoformat(timespec="seconds") if row.reviewed_at else None,
        "candidate_meta": row.candidate_meta_json or {},
        "candidate_signature": row.candidate_signature,
        "lease_token": row.lease_token,
        "lease_expires_at": (row.lease_expires_at.isoformat(timespec="seconds")
                             if row.lease_expires_at else None),
        "expires_at": row.expires_at.isoformat(timespec="seconds") if row.expires_at else None,
        "reason": row.reason,
        "confirmed_at": row.confirmed_at.isoformat(timespec="seconds") if row.confirmed_at else None,
        "consumed_at": row.consumed_at.isoformat(timespec="seconds") if row.consumed_at else None,
        "rerun_version_no": row.rerun_version_no,
    }


def candidate_payload(row: BsPageTextCandidate, *, include_text: bool = False) -> dict:
    """返回候选投影；正文与元数据只在待确认详情中返回。"""
    payload = _snapshot(row)
    if not (include_text and row.status in {"pending_review", "confirmed"}):
        payload.pop("candidate_text", None)
        payload.pop("reviewed_text", None)
        payload.pop("candidate_meta", None)
    return payload


def get_candidate(db: Session, *, candidate_id: str, task_id: int) -> BsPageTextCandidate | None:
    return db.query(BsPageTextCandidate).filter_by(id=candidate_id, task_id=task_id).one_or_none()


def list_candidates(db: Session, task_id: int, status: str | None = None) -> list[BsPageTextCandidate]:
    query = db.query(BsPageTextCandidate).filter_by(task_id=task_id)
    if status:
        query = query.filter(BsPageTextCandidate.status == status)
    return query.order_by(BsPageTextCandidate.created_at.desc(), BsPageTextCandidate.id.desc()).all()


def _validate_input(*, task_id: int, material_id: int, page_no: int, source_sha256: str,
                    input_material_fingerprint: str, input_rule_fingerprint: str) -> None:
    if type(task_id) is not int or task_id <= 0 or type(material_id) is not int or material_id <= 0:
        raise ValueError("候选任务或材料无效")
    if type(page_no) is not int or page_no < 1:
        raise ValueError("候选页码无效")
    if not isinstance(source_sha256, str) or len(source_sha256) != 64:
        raise ValueError("候选原件指纹无效")
    if not isinstance(input_material_fingerprint, str) or not input_material_fingerprint:
        raise ValueError("候选材料指纹无效")
    if not isinstance(input_rule_fingerprint, str) or not input_rule_fingerprint:
        raise ValueError("候选规则指纹无效")


def create_candidate(
    db: Session,
    *,
    task_id: int,
    material_id: int,
    page_no: int,
    review_run_id: str | None,
    source_sha256: str,
    input_material_fingerprint: str,
    input_rule_fingerprint: str,
    now: datetime | None = None,
) -> BsPageTextCandidate:
    """登记或复用同一当前输入的活动候选；终态候选允许重新生成。"""
    now = _utc_naive(now)
    _validate_input(task_id=task_id, material_id=material_id, page_no=page_no,
                    source_sha256=source_sha256,
                    input_material_fingerprint=input_material_fingerprint,
                    input_rule_fingerprint=input_rule_fingerprint)
    task = db.query(BsTask).filter_by(id=task_id).with_for_update().one_or_none()
    if task is None:
        raise ValueError("核查任务不存在")
    active = (db.query(BsPageTextCandidate)
              .filter(
                  BsPageTextCandidate.task_id == task_id,
                  BsPageTextCandidate.material_id == material_id,
                  BsPageTextCandidate.page_no == page_no,
                  BsPageTextCandidate.review_run_id == review_run_id,
                  BsPageTextCandidate.source_sha256 == source_sha256,
                  BsPageTextCandidate.input_material_fingerprint == input_material_fingerprint,
                  BsPageTextCandidate.input_rule_fingerprint == input_rule_fingerprint,
                  BsPageTextCandidate.status.in_(ACTIVE_STATES),
              )
              .order_by(BsPageTextCandidate.created_at.desc(), BsPageTextCandidate.id.desc())
              .first())
    if (active is not None and active.status == "pending_review"
            and active.expires_at is not None and active.expires_at <= now):
        active.status = "expired"
        active.reason = "候选已过期"
        active = None
    if active is not None:
        return active
    candidate = BsPageTextCandidate(
        id=uuid.uuid4().hex, task_id=task_id, material_id=material_id, page_no=page_no,
        status="requested", candidate_version=1, review_run_id=review_run_id,
        source_sha256=source_sha256,
        input_material_fingerprint=input_material_fingerprint,
        input_rule_fingerprint=input_rule_fingerprint,
        created_at=now, updated_at=now,
    )
    db.add(candidate)
    db.flush()
    return candidate


def claim_candidate(db: Session, *, candidate_id: str, now: datetime | None = None,
                    lease_seconds: int = PREPARING_LEASE_SECONDS) -> dict | None:
    now = _utc_naive(now)
    candidate = (db.query(BsPageTextCandidate).filter_by(id=candidate_id)
                 .with_for_update().one_or_none())
    if candidate is None:
        return None
    eligible = candidate.status == "requested" or (
        candidate.status == "preparing" and candidate.lease_expires_at is not None
        and candidate.lease_expires_at <= now)
    if not eligible:
        return None
    candidate.status = "preparing"
    candidate.lease_token = uuid.uuid4().hex
    candidate.lease_expires_at = now + timedelta(seconds=max(1, int(lease_seconds)))
    candidate.reason = None
    db.flush()
    return _snapshot(candidate)


def store_pending_candidate(db: Session, *, candidate_id: str, lease_token: str, text: str,
                            meta: dict, signature: str, now: datetime | None = None) -> bool:
    now = _utc_naive(now)
    if not isinstance(text, str) or not text.strip() or not isinstance(meta, dict):
        raise ValueError("候选正文或元数据无效")
    result = db.execute(update(BsPageTextCandidate).where(and_(
        BsPageTextCandidate.id == candidate_id,
        BsPageTextCandidate.status == "preparing",
        BsPageTextCandidate.lease_token == lease_token,
        BsPageTextCandidate.lease_expires_at > now,
    )).values(
        status="pending_review", candidate_text=text, candidate_meta_json=meta,
        candidate_signature=signature, lease_token=None, lease_expires_at=None,
        expires_at=now + timedelta(seconds=PENDING_REVIEW_SECONDS), reason=None,
    ))
    return result.rowcount == 1


def fail_candidate(db: Session, *, candidate_id: str, lease_token: str, reason: str,
                   now: datetime | None = None) -> bool:
    now = _utc_naive(now)
    result = db.execute(update(BsPageTextCandidate).where(and_(
        BsPageTextCandidate.id == candidate_id,
        BsPageTextCandidate.status == "preparing",
        BsPageTextCandidate.lease_token == lease_token,
        BsPageTextCandidate.lease_expires_at > now,
    )).values(status="failed", lease_token=None, lease_expires_at=None,
              reason=str(reason or "候选生成失败")[:2000]))
    return result.rowcount == 1


def mark_consumed(db: Session, *, candidate_id: str, rerun_version_no: int | None,
                  now: datetime | None = None) -> bool:
    result = db.execute(update(BsPageTextCandidate).where(and_(
        BsPageTextCandidate.id == candidate_id,
        BsPageTextCandidate.status == "confirmed",
    )).values(status="consumed", consumed_at=_utc_naive(now),
              lease_token=None, lease_expires_at=None,
              rerun_version_no=rerun_version_no))
    return result.rowcount == 1


def mark_invalidated(db: Session, *, candidate_id: str, reason: str) -> bool:
    result = db.execute(update(BsPageTextCandidate).where(and_(
        BsPageTextCandidate.id == candidate_id,
        BsPageTextCandidate.status == "confirmed",
    )).values(status="invalidated", reason=str(reason or "候选确认未完成")[:2000]))
    return result.rowcount == 1


def expire_candidates(db: Session, *, now: datetime | None = None) -> int:
    """巡检：过期未领取的候选置 expired，保留状态行供审计。"""
    now = _utc_naive(now)
    result = db.execute(update(BsPageTextCandidate).where(or_(
        and_(BsPageTextCandidate.status == "preparing",
             BsPageTextCandidate.lease_expires_at <= now),
        and_(BsPageTextCandidate.status == "pending_review",
             BsPageTextCandidate.expires_at <= now),
    )).values(status="expired", lease_token=None, lease_expires_at=None,
              reason="候选已过期"))
    return int(result.rowcount or 0)
