"""对账与告警（设计 §6.2–6.3、§11.4）。

管理端固定展示五类计数与覆盖率：
``pending / unknown / partial / interrupted / legacy``。

告警项（不静默）：
- ``completed`` 且可计费，超 TTL 仍无账单；
- 有账单但无 ``credit_ledger(consume)``；
- 钱包余额 ≠ 流水累加。
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import func, or_

from ...models.billing_models import CreditLedger, UsageCharge, UserWallet
from ...models.models import TokenUsage

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = ("rated", "partial", "unknown", "legacy", "exempt")


def expire_stale_started(db, *, ttl_minutes: int = 15, now: datetime | None = None) -> int:
    """崩溃留下的 ``started`` 台账：超 TTL 标 interrupted，不扣费、管理端可见。"""
    now = now or datetime.now()
    cutoff = now - timedelta(minutes=int(ttl_minutes))
    changed = (
        db.query(TokenUsage)
        .filter(TokenUsage.status == "started")
        .filter((TokenUsage.started_at.is_(None)) | (TokenUsage.started_at < cutoff))
        .update(
            {
                TokenUsage.status: "interrupted",
                TokenUsage.billing_status: "unknown",
                TokenUsage.usage_status: TokenUsage.usage_status,
            },
            synchronize_session=False,
        )
    )
    db.flush()
    if changed:
        logger.warning("[对账] 超时 started 台账标 interrupted：%s 行", changed)
    return int(changed or 0)


def _count(db, *conds) -> int:
    return int(db.query(func.count(TokenUsage.id)).filter(*conds).scalar() or 0)


def reconcile(
    db,
    *,
    ttl_minutes: int = 15,
    now: datetime | None = None,
    mark_stale: bool = True,
) -> dict:
    now = now or datetime.now()
    if mark_stale:
        expire_stale_started(db, ttl_minutes=ttl_minutes, now=now)
        db.commit()

    completed_billable = (
        TokenUsage.status == "completed",
        TokenUsage.billable.is_(True),
        or_(TokenUsage.usage_status.is_(None), TokenUsage.usage_status != "legacy"),
    )
    counts = {
        "unknown": _count(db, TokenUsage.usage_status == "unknown"),
        "partial": _count(db, TokenUsage.usage_status == "partial"),
        "interrupted": _count(db, TokenUsage.status == "interrupted"),
        "legacy": _count(db, TokenUsage.usage_status == "legacy"),
        "failed": _count(db, TokenUsage.status == "failed"),
        "started": _count(db, TokenUsage.status == "started"),
        "exempt": _count(db, TokenUsage.billing_status == "exempt"),
        "unattributed": _count(db, TokenUsage.user_type == "system"),
        "price_missing": _count(db, TokenUsage.billing_status == "price_missing"),
        "rated": _count(db, TokenUsage.billing_status == "rated"),
    }

    cutoff = now - timedelta(minutes=int(ttl_minutes))
    pending_condition = (
        TokenUsage.status == "completed",
        TokenUsage.billable.is_(True),
        or_(TokenUsage.usage_status.is_(None), TokenUsage.usage_status != "legacy"),
        ((TokenUsage.billing_status.is_(None)) | (TokenUsage.billing_status == "pending")),
    )
    counts["pending"] = _count(db, *pending_condition)
    stale_cutoff = cutoff
    counts["pending_stale"] = _count(
        db,
        *pending_condition,
        func.coalesce(TokenUsage.finished_at, TokenUsage.created_at) < stale_cutoff,
    )

    denom = (
        counts["rated"] + counts["partial"] + counts["unknown"] + counts["pending"]
    )
    coverage = round(counts["rated"] / denom, 4) if denom else None

    anomalies = []

    # 1) completed + billable + 超 TTL 无账单
    stale_no_charge = (
        db.query(TokenUsage.id)
        .outerjoin(UsageCharge, UsageCharge.usage_id == TokenUsage.id)
        .filter(*completed_billable)
        .filter(UsageCharge.id.is_(None))
        .filter(func.coalesce(TokenUsage.finished_at, TokenUsage.created_at) < cutoff)
        .limit(50)
        .all()
    )
    if stale_no_charge:
        anomalies.append({
            "code": "COMPLETED_WITHOUT_CHARGE",
            "count": len(stale_no_charge),
            "usage_ids": [row[0] for row in stale_no_charge],
        })

    # 2) 有账单但无对应消费流水
    charges_without_ledger = (
        db.query(UsageCharge.id, UsageCharge.usage_id)
        .outerjoin(
            CreditLedger,
            (CreditLedger.usage_id == UsageCharge.usage_id) & (CreditLedger.entry_type == "consume"),
        )
        .filter(UsageCharge.charged_micro > 0)
        .filter(CreditLedger.id.is_(None))
        .limit(50)
        .all()
    )
    if charges_without_ledger:
        anomalies.append({
            "code": "CHARGE_WITHOUT_LEDGER",
            "count": len(charges_without_ledger),
            "usage_ids": [row[1] for row in charges_without_ledger],
        })

    # 3) 钱包余额 ≠ 流水累加
    mismatch = []
    ledger_sums = {
        (row[0], row[1]): row[2]
        for row in (
            db.query(CreditLedger.user_type, CreditLedger.user_id,
                     func.sum(CreditLedger.amount_micro))
            .group_by(CreditLedger.user_type, CreditLedger.user_id)
            .all()
        )
    }
    for w in db.query(UserWallet).all():
        expected = int(ledger_sums.get((w.user_type, w.user_id), 0) or 0)
        if int(w.balance_micro or 0) != expected:
            mismatch.append({
                "user_type": w.user_type,
                "user_id": w.user_id,
                "balance_micro": int(w.balance_micro or 0),
                "ledger_sum_micro": expected,
            })
    if mismatch:
        anomalies.append({"code": "WALLET_BALANCE_MISMATCH", "count": len(mismatch), "items": mismatch[:50]})

    for anomaly in anomalies:
        logger.warning("[对账告警] %s 数量=%s", anomaly["code"], anomaly["count"])

    # 归因覆盖：场景 / 智能体维度只做可见统计，不改口径（历史行 NULL 显示为 other/未归属）。
    window_start = now - timedelta(days=30)
    scene_key = func.coalesce(TokenUsage.billing_scene, "other")
    by_scene = {
        row[0]: int(row[1] or 0)
        for row in (
            db.query(scene_key, func.count(TokenUsage.id))
            .filter(func.coalesce(TokenUsage.finished_at, TokenUsage.created_at) >= window_start)
            .group_by(scene_key)
            .all()
        )
    }
    agent_unattributed = _count(
        db,
        TokenUsage.agent_id.is_(None),
        TokenUsage.status == "completed",
        func.coalesce(TokenUsage.finished_at, TokenUsage.created_at) >= window_start,
    )

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "counts": counts,
        "coverage": coverage,
        "by_scene_30d": by_scene,
        "agent_unattributed_30d": agent_unattributed,
        "anomalies": anomalies,
    }