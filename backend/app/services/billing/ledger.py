"""统一用量台账：start / finish / outbox（设计 §4.7、§6.1）。

三段式：
1. ``start`` 在模型调用前插入 ``status='started'`` 行，**独立事务立即提交**，
   不随业务消息事务回滚；enforce+trequire_ledger 下写不进去就抛
   ``LedgerUnavailable``，由调用方拒绝请求（503）；
2. ``finish`` 在 ``finally`` 更新实际用量/状态；
3. 同一事务写 ``billing_outbox(status=pending)``，随后同步尝试出账；
   失败由 worker 重试，且 ``usage_charges.usage_id`` 唯一保证不双扣。

任何异常路径都不允许「直接 return 不写台账」。
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...database import SessionLocal
from ...models.billing_models import BillingOutbox, LlmProvider
from ...models.models import TokenUsage
from .attribution import resolve_agent_id
from .normalizer import NormalizedUsage, normalize
from .registry import get_model, get_settings_row
from .scenes import normalize_scene

logger = logging.getLogger(__name__)


# 台账插入即登记出账任务（同一事务、同一 flush）。
#
# 用 mapper ``after_insert`` 而不是在 ``record()`` 里显式 ``flush()+insert``：
# 旧的 token_service 契约是「只 db.add、提交时机由调用方决定」（测试会断言
# ``session.new`` 数量），提前 flush 会破坏它；after_insert 在调用方真正
# flush/commit 时触发，行已有 id，可直接写入 outbox。started 行不登记，
# 由 finish 更新路径显式登记。
@event.listens_for(Session, "before_flush")
def _enqueue_outbox_on_flush(session, flush_context, instances):  # noqa: ANN001
    """台账插入前挂上出账任务（同一 flush / 同一事务）。

    旧库/局部测试库没有计费表时只写台账、不登记出账，不阻断业务 flush。
    """
    new_rows = [obj for obj in session.new if isinstance(obj, TokenUsage)]
    if not new_rows:
        return
    from .schema import billing_tables_available

    if not billing_tables_available(session.get_bind()):
        return
    for row in new_rows:
        if getattr(row, "_skip_billing_enqueue", False):
            continue
        # 列默认值在 INSERT 时才生效；这里按「None=默认 completed」判断。
        if (getattr(row, "status", None) or "completed") != "completed":
            continue
        if getattr(row, "billable", True) is False:
            row.billing_status = "exempt"
            continue
        if getattr(row, "usage_status", None) == "legacy":
            row.billing_status = "legacy"
            continue
        if getattr(row, "outbox_row", None) is not None:
            continue
        row.outbox_row = BillingOutbox(status="pending", attempts=0, next_retry_at=_now())
        row.billing_status = "unattributed" if row.user_type == "system" else "pending"


class LedgerUnavailable(RuntimeError):
    """enforce 模式下台账写不进去（require_ledger=true）。"""


@dataclass
class CallContext:
    usage_id: Optional[int]
    model_code: Optional[str]
    provider_code: Optional[str]
    unit_type: str
    unit_scale: int
    billable: bool
    enabled: bool = True


def _now() -> datetime:
    return datetime.now()


def _is_local(model) -> bool:
    """本地模型（RapidOCR 等）无外部费用：只统计、不扣费。"""
    return bool(model is not None and (model.code or "").startswith("rapidocr"))


def _resolve_meta(db, model_code: Optional[str], unit_type: Optional[str], source: Optional[str] = None):
    from .model_context import current_model_code
    from .registry import default_model_code, resolve_model_for_source

    # 显式 code > 当前任务/会话上下文 > 来源映射 > 全局默认（设计 §8.1）。
    # Plus 各模块的深层调用不层层传参，统一在任务边界用 use_model() 设置上下文；
    # 这样「选了 plus 却按 flash 记账」的静默错配被消除。
    if not model_code:
        model_code = current_model_code()
    if db is not None:
        model = get_model(db, model_code) if model_code else resolve_model_for_source(db, source)
    else:
        model = None
    resolved_code = (model.code if model is not None else (model_code or default_model_code()))
    provider_code = None
    if model is not None:
        provider_obj = db.query(LlmProvider).filter(LlmProvider.id == model.provider_id).first()
        if provider_obj is not None:
            provider_code = provider_obj.code
            if provider_obj.protocol == "local":
                return model, resolved_code, provider_code, "page", 1, False
    resolved_unit = (unit_type or (model.unit_type if model is not None else "token") or "token")
    scale = int((model.unit_scale if model is not None else 1) or 1)
    return model, resolved_code, provider_code, resolved_unit, scale, not _is_local(model)


def start_usage(
    *,
    user_id: int,
    user_type: str,
    source: str,
    stage: str | None = None,
    conversation_id: int | None = None,
    as_session_id: str | None = None,
    chat_id: str | None = None,
    dedupe_key: str | None = None,
    model_code: str | None = None,
    unit_type: str | None = None,
    request_quantity=None,
    request_id: str | None = None,
    scope_type: str | None = None,
    scope_id: str | None = None,
    billing_scene: str | None = None,
    agent_id: int | None = None,
    unattributed: bool = False,
    db=None,
) -> CallContext:
    """落一条 started 台账。默认使用独立 session 并立即提交。"""
    own_session = db is None
    session = SessionLocal() if own_session else db

    try:
        # 设置表读取放进 try：旧库/测试库无计费表时，shadow 语义是「记 critical
        # 继续」，不能把业务调用打断。
        settings_row = get_settings_row(session)
        mode = (settings_row.enforce_mode if settings_row is not None else "shadow")
        require_ledger = bool(settings_row.require_ledger) if settings_row is not None else True
        model, resolved_code, provider_code, resolved_unit, scale, billable = _resolve_meta(
            session, model_code, unit_type, source
        )
        quantity = None
        if request_quantity is not None:
            quantity = Decimal(str(request_quantity))
        row = TokenUsage(
            billing_scene=normalize_scene(billing_scene, source),
            agent_id=resolve_agent_id(
                session,
                agent_id=agent_id,
                conversation_id=conversation_id,
                as_session_id=as_session_id,
                scope_type=scope_type,
                scope_id=scope_id,
            ),
            billing_scope_type=scope_type,
            billing_scope_id=(str(scope_id) if scope_id is not None else None),
            user_id=user_id,
            user_type=user_type,
            conversation_id=conversation_id,
            chat_id=chat_id,
            dedupe_key=dedupe_key,
            input_count=None,
            output_count=None,
            token_count=None,
            source=source,
            stage=stage,
            as_session_id=as_session_id,
            unit_type=resolved_unit,
            unit_scale=scale,
            billable_quantity=quantity,
            billable=billable,
            status="started",
            started_at=_now(),
            model_code=resolved_code,
            provider_code=provider_code,
            request_id=request_id,
            billing_status=("unattributed" if unattributed else None),
        )
        session.add(row)
        session.flush()
        if own_session:
            session.commit()
        return CallContext(
            usage_id=row.id,
            model_code=resolved_code,
            provider_code=provider_code,
            unit_type=resolved_unit,
            unit_scale=scale,
            billable=billable,
        )
    except Exception as exc:  # noqa: BLE001
        if own_session:
            try:
                session.rollback()
            except Exception:  # noqa: BLE001
                pass
        mode = locals().get("mode", "shadow")
        require_ledger = locals().get("require_ledger", True)
        if mode == "enforce" and require_ledger:
            raise LedgerUnavailable("用量台账写入失败（enforce/require_ledger）") from exc
        logger.critical("用量台账 start 写入失败，按 shadow 继续：%s", exc, exc_info=True)
        return CallContext(None, model_code, None, unit_type or "token", 1, True, enabled=False)
    finally:
        if own_session:
            session.close()


def enqueue_outbox(db, usage: TokenUsage) -> None:
    """为一条已完成的台账登记出账任务（同事务）。已存在则忽略。"""
    if usage is None or usage.id is None:
        return
    existing = db.query(BillingOutbox.id).filter(BillingOutbox.usage_id == usage.id).first()
    if existing:
        usage.billing_status = usage.billing_status or ("unattributed" if usage.user_type == "system" else "pending")
        return
    try:
        with db.begin_nested():
            db.add(BillingOutbox(usage_id=usage.id, status="pending", attempts=0, next_retry_at=_now()))
            db.flush()
    except IntegrityError:
        # 并发重复：另一路已登记，按已登记处理
        pass
    usage.billing_status = "unattributed" if usage.user_type == "system" else "pending"


def _apply_normalized(row: TokenUsage, norm: NormalizedUsage) -> None:
    row.input_count = norm.prompt_tokens
    row.output_count = norm.output_tokens
    row.token_count = norm.total_tokens
    row.cache_hit_input_count = norm.cache_hit
    row.cache_miss_input_count = norm.effective_cache_miss
    row.cache_write_input_count = norm.cache_write
    row.cache_reported = norm.cache_reported
    row.raw_usage_json = norm.raw or None
    row.usage_status = norm.usage_status


def finish_usage(
    usage_id: int | None,
    *,
    raw_usage: dict | None = None,
    usage: NormalizedUsage | None = None,
    quantity=None,
    status: str = "completed",
    billable: bool | None = None,
    usage_status: str | None = None,
    model_code: str | None = None,
    unit_type: str | None = None,
    unit_scale: int | None = None,
    db=None,
    process_now: bool = True,
) -> dict:
    """在 finally 更新台账实际用量，并登记 outbox。"""
    if usage_id is None:
        return {"ok": False, "reason": "no_usage_row"}
    own_session = db is None
    session = SessionLocal() if own_session else db
    try:
        row = session.query(TokenUsage).filter(TokenUsage.id == usage_id).first()
        if row is None:
            return {"ok": False, "reason": "usage_not_found"}
        norm = usage or (normalize(raw_usage) if raw_usage is not None else None)
        if norm is not None:
            _apply_normalized(row, norm)
        # 先落数量/单位/模型，再判定 usage_status——视觉兜底会在 finish 时把
        # token 单位改成 image，判定必须看到最终单位。
        if quantity is not None:
            row.billable_quantity = Decimal(str(quantity))
        if billable is not None:
            row.billable = billable
        if model_code:
            row.model_code = model_code
        if unit_type:
            row.unit_type = unit_type
        if unit_scale is not None:
            row.unit_scale = int(unit_scale)
        if usage_status is not None:
            row.usage_status = usage_status
        elif norm is None and row.usage_status is None:
            # 非 token 调用的数量在请求侧确定，即使 provider 没回 usage 也是已知；
            # 只有 token 调用拿不到 usage 才落 unknown，不静默留空。
            if (row.unit_type or "token") != "token" and row.billable_quantity is not None:
                row.usage_status = "complete"
            else:
                row.usage_status = "unknown"
        row.status = status
        row.finished_at = _now()
        # 只有成功生成且可计费才登记出账；失败/中断不扣费但保留台账。
        if status == "completed" and row.billable:
            enqueue_outbox(session, row)
        elif not row.billable:
            row.billing_status = "exempt"
        elif row.user_type == "system":
            row.billing_status = "unattributed"
        else:
            row.billing_status = "unknown"
        if own_session:
            session.commit()
        else:
            session.flush()
        result = {"ok": True, "usage_id": row.id, "status": row.status,
                  "usage_status": row.usage_status}
    except Exception as exc:  # noqa: BLE001
        logger.critical("用量台账 finish 失败 usage_id=%s：%s", usage_id, exc, exc_info=True)
        if own_session:
            try:
                session.rollback()
            except Exception:  # noqa: BLE001
                pass
        return {"ok": False, "reason": "finish_failed", "error": str(exc)}
    finally:
        if own_session:
            session.close()

    if process_now and result.get("ok") and status == "completed" and result.get("usage_id"):
        try:
            from .outbox import process_usage

            process_usage(result["usage_id"])
        except Exception:  # noqa: BLE001 —— 出账失败不影响台账，worker 会重试
            logger.warning("同步出账失败，等待 worker 重试 usage_id=%s", result.get("usage_id"), exc_info=True)
    return result


def build_completed_row(
    *,
    user_id: int,
    user_type: str,
    source: str,
    stage: str | None = None,
    conversation_id: int | None = None,
    as_session_id: str | None = None,
    chat_id: str | None = None,
    dedupe_key: str | None = None,
    raw_usage: dict | None = None,
    model_code: str | None = None,
    unit_type: str | None = None,
    quantity=None,
    billable: bool | None = None,
    billing_scene: str | None = None,
    agent_id: int | None = None,
    scope_type: str | None = None,
    scope_id: str | None = None,
    unattributed: bool = False,
    db=None,
) -> TokenUsage:
    """构造一条已完成的台账行（不 add，由调用方决定事务）。

    供 ``token_service.record`` 与一次性非 token 记录复用；出账由
    :func:`enqueue_for_row` 在同一事务登记。
    """
    model, resolved_code, provider_code, resolved_unit, scale, default_billable = _resolve_meta(
        db, model_code, unit_type, source
    )
    norm = normalize(raw_usage) if unit_type in (None, "token") else None
    row = TokenUsage(
        billing_scene=normalize_scene(billing_scene, source),
        agent_id=resolve_agent_id(
            db,
            agent_id=agent_id,
            conversation_id=conversation_id,
            as_session_id=as_session_id,
            scope_type=scope_type,
            scope_id=scope_id,
        ),
        billing_scope_type=scope_type,
        billing_scope_id=(str(scope_id) if scope_id is not None else None),
        user_id=user_id,
        user_type=user_type,
        conversation_id=conversation_id,
        chat_id=chat_id,
        dedupe_key=dedupe_key,
        source=source,
        stage=stage,
        as_session_id=as_session_id,
        unit_type=resolved_unit,
        unit_scale=scale,
        billable_quantity=(Decimal(str(quantity)) if quantity is not None else None),
        billable=default_billable if billable is None else bool(billable),
        billing_status=("unattributed" if unattributed else None),
        status="completed",
        started_at=_now(),
        finished_at=_now(),
        model_code=resolved_code,
        provider_code=provider_code,
    )
    if norm is not None:
        _apply_normalized(row, norm)
    else:
        row.usage_status = "complete"
    return row


def enqueue_for_row(db, row: TokenUsage) -> None:
    """把已完成行登记到 outbox（已在事务里）。失败只告警，不阻断业务。"""
    if row is None:
        return
    if row.billable is False:
        row.billing_status = "exempt"
        return
    if row.usage_status == "legacy":
        row.billing_status = "legacy"
        return
    if row.status != "completed":
        return
    try:
        if row.id is None:
            db.flush()
        enqueue_outbox(db, row)
    except Exception as exc:  # noqa: BLE001
        logger.critical("台账登记出账失败 usage=%s：%s", getattr(row, "id", None), exc, exc_info=True)