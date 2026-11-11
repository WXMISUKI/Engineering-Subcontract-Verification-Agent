"""出账 worker / 同步重试（设计 §4.8–4.9、§6.1–6.3、§11.4）。

不双扣的两道闸：
1. ``usage_charges.usage_id`` 唯一 —— 一条台账只出一张账单；
2. ``credit_ledger.idempotency_key = usage:{usage_id}`` —— 重复处理不重复扣。

价格版本按台账 ``started_at``（无则 ``created_at``）找生效的 published 版本；
找不到 → ``price_missing``，不扣费、留 outbox 待重试、由对账告警。
"""

import logging
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy.exc import IntegrityError

from ...database import SessionLocal
from ...models.billing_models import BillingOutbox, UsageCharge
from ...models.models import TokenUsage
from . import wallet
from .billing_settings import ensure_settings_row
from .normalizer import NormalizedUsage
from .pricing import price_usage
from .registry import get_active_price_version, get_default_model, get_model

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 8
BACKOFF_BASE_SECONDS = 60


def _attribution_from_row(row: TokenUsage) -> dict:
    """账单复制台账的归因列；历史行为 NULL，不补造。"""
    return {
        "billing_scene": row.billing_scene,
        "agent_id": row.agent_id,
        "billing_scope_type": row.billing_scope_type,
        "billing_scope_id": row.billing_scope_id,
    }


def _normalized_from_row(row: TokenUsage) -> NormalizedUsage:
    return NormalizedUsage(
        usage_status=row.usage_status or "unknown",
        cache_reported=bool(row.cache_reported) if row.cache_reported is not None else False,
        prompt_tokens=row.input_count,
        output_tokens=row.output_count,
        total_tokens=row.token_count,
        cache_hit=row.cache_hit_input_count,
        cache_miss=row.cache_miss_input_count,
        cache_write=row.cache_write_input_count,
        raw=row.raw_usage_json or {},
    )


def _mark_outbox_done(db, outbox: BillingOutbox) -> None:
    outbox.status = "done"
    outbox.attempts = int(outbox.attempts or 0) + 1
    outbox.last_error = None
    outbox.next_retry_at = None


def _mark_outbox_retry(db, outbox: BillingOutbox, error: str) -> None:
    outbox.attempts = int(outbox.attempts or 0) + 1
    outbox.last_error = str(error)[:2000]
    if outbox.attempts >= MAX_ATTEMPTS:
        outbox.status = "failed"
        outbox.next_retry_at = None
    else:
        outbox.status = "pending"
        delay = BACKOFF_BASE_SECONDS * (2 ** max(0, outbox.attempts - 1))
        outbox.next_retry_at = datetime.now() + timedelta(seconds=delay)


def process_usage(usage_id: int, *, db=None) -> dict:
    """对一条台账出账。幂等：重复调用不会双扣。"""
    own_session = db is None
    session = SessionLocal() if own_session else db
    try:
        outbox = session.query(BillingOutbox).filter(BillingOutbox.usage_id == usage_id).first()
        if outbox is None:
            return {"ok": False, "reason": "no_outbox"}
        if outbox.status == "done":
            return {"ok": True, "reason": "already_done"}

        row = session.query(TokenUsage).filter(TokenUsage.id == usage_id).first()
        if row is None:
            _mark_outbox_retry(session, outbox, "usage_not_found")
            if own_session:
                session.commit()
            return {"ok": False, "reason": "usage_not_found"}

        settings_row = ensure_settings_row(session)

        # 历史（上线前）用量：只报表、不补扣。
        # 归属以启动时显式打的 ``usage_status='legacy'`` 为准；不在这里用
        # created_at < billing_epoch 二次判断——MySQL/SQLite 的时间列默认只到秒，
        # 与 Python 微秒级 epoch 比较会把同一秒内的新台账误判成历史。
        if row.usage_status == "legacy":
            row.billing_status = "legacy"
            _mark_outbox_done(session, outbox)
            if own_session:
                session.commit()
            return {"ok": True, "reason": "legacy"}

        unattributed = row.user_type == "system" and row.user_id == 0
        if row.billable is False:
            row.billing_status = "exempt"
            _mark_outbox_done(session, outbox)
            if own_session:
                session.commit()
            return {"ok": True, "reason": "exempt"}

        # 全未知（上游完全没上报）：0 扣费、落行可见，不依赖价格版本。
        if row.usage_status == "unknown":
            charge = session.query(UsageCharge).filter(UsageCharge.usage_id == row.id).first()
            if charge is None:
                charge = UsageCharge(
                    usage_id=row.id, user_type=row.user_type, user_id=row.user_id,
                    model_id=None, price_version_id=None, unit_type=row.unit_type or "token",
                    billable_quantity=row.billable_quantity,
                    cache_hit_input_tokens=None, cache_miss_input_tokens=None,
                    cache_write_input_tokens=None, output_tokens=None,
                    raw_micro=0, charged_micro=0, status="unknown",
                    **_attribution_from_row(row),
                )
                try:
                    with session.begin_nested():
                        session.add(charge)
                        session.flush()
                except IntegrityError:
                    charge = session.query(UsageCharge).filter(UsageCharge.usage_id == row.id).first()
            row.billing_status = "unattributed" if unattributed else "unknown"
            _mark_outbox_done(session, outbox)
            if own_session:
                session.commit()
            else:
                session.flush()
            return {"ok": True, "usage_id": row.id, "status": "unknown", "charged_micro": 0}

        # 台账已指定模型时**不得**回退默认模型：AS runtime 可能与全局默认不同，
# 静默换模型会按错价计费。找不到就 price_missing，让管理端看见。
        if row.model_code:
            model = get_model(session, row.model_code)
        else:
            model = get_default_model(session)
        at = row.started_at or row.created_at or datetime.now()
        price_version = get_active_price_version(session, model.id, at) if model is not None else None
        if model is None or price_version is None:
            row.billing_status = "price_missing"
            _mark_outbox_retry(
                session, outbox,
                f"price_missing model_code={row.model_code} at={at}",
            )
            session.commit() if own_session else session.flush()
            logger.warning(
                "[计费] price_missing：usage=%s model_code=%s at=%s，未扣费，等待价格版本",
                row.id, row.model_code, at,
            )
            return {"ok": False, "reason": "price_missing"}

        unit_type = row.unit_type or model.unit_type or "token"
        result = price_usage(
            unit_type=unit_type,
            usage=_normalized_from_row(row) if unit_type == "token" else None,
            quantity=row.billable_quantity,
            unit_scale=int(row.unit_scale or model.unit_scale or 1),
            price_version=price_version,
            treat_unreported_as_miss=bool(settings_row.treat_unreported_cache_as_miss),
            min_charge_micro=int(settings_row.min_charge_micro or 0),
        )

        charge = session.query(UsageCharge).filter(UsageCharge.usage_id == row.id).first()
        if charge is None:
            charge = UsageCharge(
                usage_id=row.id,
                user_type=row.user_type,
                user_id=row.user_id,
                model_id=model.id,
                price_version_id=price_version.id,
                unit_type=unit_type,
                billable_quantity=row.billable_quantity,
                cache_hit_input_tokens=row.cache_hit_input_count,
                cache_miss_input_tokens=row.cache_miss_input_count,
                cache_write_input_tokens=row.cache_write_input_count,
                output_tokens=row.output_count,
                raw_micro=int(result.raw_micro),
                charged_micro=int(result.charged_micro),
                status=result.status,
                **_attribution_from_row(row),
            )
            try:
                with session.begin_nested():
                    session.add(charge)
                    session.flush()
            except IntegrityError:
                charge = session.query(UsageCharge).filter(UsageCharge.usage_id == row.id).first()

        if (
            charge is not None
            and int(charge.charged_micro or 0) > 0
            and charge.status in ("rated", "partial")
            and not unattributed  # system/0 只做成本可见，不扣任何用户钱包
        ):
            wallet.consume_for_usage(
                session,
                user_type=row.user_type,
                user_id=row.user_id,
                usage_id=row.id,
                charged_micro=int(charge.charged_micro),
                price_version_id=price_version.id,
                reason=f"模型调用 {row.model_code or model.code}",
                billing_scene=row.billing_scene,
                agent_id=row.agent_id,
                billing_scope_type=row.billing_scope_type,
                billing_scope_id=row.billing_scope_id,
            )

        row.billing_status = (
            "unattributed" if unattributed
            else (charge.status if charge is not None else result.status)
        )
        _mark_outbox_done(session, outbox)
        if own_session:
            session.commit()
        else:
            session.flush()
        return {
            "ok": True,
            "usage_id": row.id,
            "status": row.billing_status,
            "charged_micro": int(charge.charged_micro) if charge is not None else 0,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("[计费] 出账失败 usage=%s", usage_id)
        if own_session:
            session.rollback()
        else:
            # 调用方持有事务，不在这里回滚/重开；由调用方的保存点决定成败。
            return {"ok": False, "reason": "error", "error": str(exc)}
        try:
            retry_session = SessionLocal()
            try:
                outbox = retry_session.query(BillingOutbox).filter(
                    BillingOutbox.usage_id == usage_id
                ).first()
                if outbox is not None and outbox.status != "done":
                    _mark_outbox_retry(retry_session, outbox, str(exc))
                    retry_session.commit()
            finally:
                retry_session.close()
        except Exception:  # noqa: BLE001
            logger.exception("[计费] 标记出账重试失败 usage=%s", usage_id)
        return {"ok": False, "reason": "error", "error": str(exc)}


def run_pending(limit: int = 50, *, now: datetime | None = None) -> dict:
    """worker 入口：取到期的 pending 任务逐条出账。"""
    now = now or datetime.now()
    session = SessionLocal()
    try:
        rows = (
            session.query(BillingOutbox)
            .filter(
                BillingOutbox.status == "pending",
                (BillingOutbox.next_retry_at.is_(None)) | (BillingOutbox.next_retry_at <= now),
            )
            .order_by(BillingOutbox.id)
            .limit(int(limit))
            .all()
        )
        usage_ids = [r.usage_id for r in rows]
    finally:
        session.close()

    processed = 0
    ok = 0
    failed = 0
    for usage_id in usage_ids:
        result = process_usage(usage_id)
        processed += 1
        if result.get("ok"):
            ok += 1
        else:
            failed += 1
    return {"processed": processed, "ok": ok, "failed": failed}