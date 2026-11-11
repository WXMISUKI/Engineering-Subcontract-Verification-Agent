"""统一计量入口与调用包装器（设计 §7、§6.1）。

所有模型调用应当：
1. 调 :func:`begin`（落 started 台账，独立事务）；
2. 调用 provider；
3. 在 ``finally`` 调 :func:`complete` / :func:`abort`（落实际用量 + outbox）。

对**已经在上层用完再记**的旧链路（token_service.record），走 ``build_completed_row``
+ ``enqueue_for_row``，同样保证有台账、有状态、不丢账；区别只是没有 started 阶段。

``current_subject`` 是请求级计费主体（user_type/user_id/source/stage/scope），
由认证依赖注入；后台线程/任务复制 contextvar，包装器无需层层传参。
"""

import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Optional

from ...database import SessionLocal
from ...models.models import TokenUsage
from . import wallet
from .registry import get_settings_row
from .ledger import (
    CallContext,
    LedgerUnavailable,
    build_completed_row,
    finish_usage,
    start_usage,
)
from .normalizer import normalize

logger = logging.getLogger(__name__)


@dataclass
class BillingSubject:
    user_type: str
    user_id: int
    source: str = "chat"
    stage: str | None = None
    request_id: str | None = None
    scope_type: str | None = None
    scope_id: str | None = None
    # 后台线程/任务用：会话级展示（/token-total）需要 conversation_id；只给 scope
    # 时用量能出账但气泡看不到。缺省 None 表示不归入任何会话。
    conversation_id: int | None = None
    # 归因（设计 §18 第 4 条）：scene 可由 source 推导，agent_id 解析不到就 None。
    billing_scene: str | None = None
    agent_id: int | None = None


_subject: ContextVar[Optional[BillingSubject]] = ContextVar("billing_subject", default=None)

# 无主体兜底：不伪造真实用户，落 system/0 且 billing_status='unattributed'。
SYSTEM_USER_TYPE = "system"
SYSTEM_USER_ID = 0


def set_subject(subject: Optional[BillingSubject]) -> None:
    _subject.set(subject)


def get_subject() -> Optional[BillingSubject]:
    return _subject.get()


def clear_subject() -> None:
    _subject.set(None)


@contextmanager
def billing_subject_cm(subject: Optional[BillingSubject]):
    """在后台线程/任务里显式绑定计费主体（不依赖认证 contextvar）。"""
    token = _subject.set(subject)
    try:
        yield subject
    finally:
        _subject.reset(token)


def _enforce_rejects_unattributed() -> bool:
    """enforce + require_ledger 时，无主体必须 fail-closed。"""
    session = SessionLocal()
    try:
        row = get_settings_row(session)
        return bool(row is not None and row.enforce_mode == "enforce" and row.require_ledger)
    except Exception:  # noqa: BLE001
        return False
    finally:
        session.close()


def _resolve_owner(*, subject, user_type, user_id, source: str) -> tuple[str, int, bool]:
    """显式 subject > 显式 user 参数 > contextvar > system/0 unattributed。

    返回 (user_type, user_id, unattributed)。enforce+require_ledger 且无主体时抛
    LedgerUnavailable（保持 fail-closed），其余情况落 system/0 以便对账可见。
    """
    if subject is not None:
        return subject.user_type, subject.user_id, False
    if user_type is not None and user_id is not None:
        return user_type, user_id, False
    ctx_subject = get_subject()
    if ctx_subject is not None:
        return ctx_subject.user_type, ctx_subject.user_id, False
    if _enforce_rejects_unattributed():
        raise LedgerUnavailable(f"缺少计费主体，enforce 模式拒绝未记账调用（source={source}）")
    return SYSTEM_USER_TYPE, SYSTEM_USER_ID, True


_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")


def estimate_tokens(text: str | None) -> int:
    """保守的本地 token 估算（仅在 provider 不回报 usage 时使用）。

    CJK 字符按 1 token/字；其余字符按 4 字符/token。这是**下界估算**，
    只用于 embedding 这类请求侧可确定输入量的场景，不用于假装精确。
    """
    text = text or ""
    cjk = len(_CJK_RE.findall(text))
    rest = max(0, len(text) - cjk)
    return cjk + (rest + 3) // 4


def begin(
    *,
    user_type: str,
    user_id: int,
    source: str,
    stage: str | None = None,
    model_code: str | None = None,
    unit_type: str | None = None,
    request_quantity=None,
    request_id: str | None = None,
    conversation_id: int | None = None,
    as_session_id: str | None = None,
    scope_type: str | None = None,
    scope_id: str | None = None,
    billing_scene: str | None = None,
    agent_id: int | None = None,
    unattributed: bool = False,
    db=None,
) -> CallContext:
    return start_usage(
        user_id=user_id,
        user_type=user_type,
        source=source,
        stage=stage,
        model_code=model_code,
        unit_type=unit_type,
        request_quantity=request_quantity,
        request_id=request_id,
        conversation_id=conversation_id,
        as_session_id=as_session_id,
        scope_type=scope_type,
        scope_id=scope_id,
        billing_scene=billing_scene,
        agent_id=agent_id,
        unattributed=unattributed,
        db=db,
    )


def complete(
    ctx: CallContext,
    *,
    raw_usage: dict | None = None,
    quantity=None,
    status: str = "completed",
    model_code: str | None = None,
    unit_type: str | None = None,
    unit_scale: int | None = None,
    process_now: bool = True,
    db=None,
) -> dict:
    if ctx is None or ctx.usage_id is None:
        return {"ok": False, "reason": "no_usage_row"}
    return finish_usage(
        ctx.usage_id,
        raw_usage=raw_usage,
        quantity=quantity,
        status=status,
        model_code=model_code or ctx.model_code,
        unit_type=unit_type,
        unit_scale=unit_scale,
        process_now=process_now,
        db=db,
    )


def abort(ctx: CallContext, *, status: str = "failed", db=None) -> dict:
    """失败/中断：不扣费，但必须落台账状态（设计 §6.2）。"""
    return complete(ctx, status=status, process_now=False, db=db)


def begin_from_subject(*, source: str | None = None, stage: str | None = None,
                       model_code: str | None = None, unit_type: str | None = None,
                       request_quantity=None, scope_type: str | None = None,
                       scope_id: str | None = None,
                       billing_scene: str | None = None,
                       agent_id: int | None = None,
                       as_session_id: str | None = None,
                       conversation_id: int | None = None,
                       subject: BillingSubject | None = None,
                       required: bool = False) -> CallContext:
    """解析计费主体并落 started 台账。

    顺序：显式 ``subject`` > contextvar > system/0 unattributed。
    无主体时不再静默跳过：enforce+require_ledger 抛 ``LedgerUnavailable``，
    否则落一条 system/0、``billing_status='unattributed'`` 的台账供对账。
    """
    subject = subject or get_subject()
    if subject is None:
        if required or _enforce_rejects_unattributed():
            raise LedgerUnavailable("缺少计费主体，enforce 模式拒绝未记账调用")
        return begin(
            user_type=SYSTEM_USER_TYPE,
            user_id=SYSTEM_USER_ID,
            source=source or "unknown",
            stage=stage,
            model_code=model_code,
            unit_type=unit_type,
            request_quantity=request_quantity,
            scope_type=scope_type,
            scope_id=scope_id,
            billing_scene=billing_scene,
            agent_id=agent_id,
            as_session_id=as_session_id,
            conversation_id=conversation_id,
            unattributed=True,
        )
    return begin(
        user_type=subject.user_type,
        user_id=subject.user_id,
        source=source or subject.source,
        stage=stage or subject.stage,
        model_code=model_code,
        unit_type=unit_type,
        request_quantity=request_quantity,
        request_id=subject.request_id,
        scope_type=scope_type or subject.scope_type,
        scope_id=scope_id or subject.scope_id,
        billing_scene=billing_scene or subject.billing_scene,
        agent_id=agent_id if agent_id is not None else subject.agent_id,
        as_session_id=as_session_id,
        conversation_id=conversation_id if conversation_id is not None else subject.conversation_id,
    )


def record_non_token(
    *,
    user_type: str | None = None,
    user_id: int | None = None,
    subject: BillingSubject | None = None,
    source: str,
    unit_type: str,
    quantity,
    model_code: str | None = None,
    stage: str | None = None,
    status: str = "completed",
    billable: bool | None = None,
    conversation_id: int | None = None,
    as_session_id: str | None = None,
    billing_scene: str | None = None,
    agent_id: int | None = None,
    scope_type: str | None = None,
    scope_id: str | None = None,
    db=None,
    process_now: bool = True,
) -> Optional[int]:
    """一次性落一条非 token 用量（页/张/字符/秒）并入 outbox。

    归属：显式 subject > 显式 user > contextvar > system/0 unattributed。
    """
    own_session = db is None
    session = SessionLocal() if own_session else db
    try:
        resolved_type, resolved_id, unattributed = _resolve_owner(
            subject=subject, user_type=user_type, user_id=user_id, source=source
        )
        # subject 里带的归因（OCR 继承父任务）在调用方未显式传时生效。
        billing_scene = billing_scene or (subject.billing_scene if subject else None)
        if agent_id is None and subject is not None:
            agent_id = subject.agent_id
        scope_type = scope_type or (subject.scope_type if subject else None)
        scope_id = scope_id or (subject.scope_id if subject else None)
        if conversation_id is None and subject is not None:
            conversation_id = subject.conversation_id
        row = build_completed_row(
            user_id=resolved_id,
            user_type=resolved_type,
            source=source,
            stage=stage,
            conversation_id=conversation_id,
            as_session_id=as_session_id,
            model_code=model_code,
            unit_type=unit_type,
            quantity=quantity,
            billable=billable,
            billing_scene=billing_scene,
            agent_id=agent_id,
            scope_type=scope_type,
            scope_id=scope_id,
            unattributed=unattributed,
            db=session,
        )
        row.status = status
        if status != "completed":
            row.billing_status = "unattributed" if unattributed else "unknown"
        session.add(row)
        session.flush()
        if own_session:
            session.commit()
        usage_id = row.id
    except Exception as exc:  # noqa: BLE001
        logger.critical("非 token 台账写入失败 source=%s unit=%s：%s", source, unit_type, exc, exc_info=True)
        if own_session:
            try:
                session.rollback()
            except Exception:  # noqa: BLE001
                pass
        return None
    finally:
        if own_session:
            session.close()

    if process_now and status == "completed" and own_session:
        try:
            from .outbox import process_usage

            process_usage(usage_id)
        except Exception:  # noqa: BLE001
            logger.warning("非 token 同步出账失败，等待 worker usage=%s", usage_id, exc_info=True)
    return usage_id


def record_tokens_local(
    *,
    user_type: str | None = None,
    user_id: int | None = None,
    subject: BillingSubject | None = None,
    source: str,
    raw_usage: dict | None,
    model_code: str | None = None,
    stage: str | None = None,
    estimated_input_tokens: int | None = None,
    conversation_id: int | None = None,
    as_session_id: str | None = None,
    billing_scene: str | None = None,
    agent_id: int | None = None,
    scope_type: str | None = None,
    scope_id: str | None = None,
    db=None,
    process_now: bool = True,
) -> Optional[int]:
    """没有上层 db 的 token 计量（embedding 等）。

    provider 未回报 usage 时用 ``estimated_input_tokens`` 记输入，
    ``raw_usage_json`` 保留 ``{"estimated": true}`` 留证。
    归属：显式 subject > 显式 user > contextvar > system/0 unattributed。
    """
    raw = raw_usage
    if raw is None and estimated_input_tokens is not None:
        raw = {"prompt_tokens": int(estimated_input_tokens), "estimated": True}
    own_session = db is None
    session = SessionLocal() if own_session else db
    try:
        resolved_type, resolved_id, unattributed = _resolve_owner(
            subject=subject, user_type=user_type, user_id=user_id, source=source
        )
        billing_scene = billing_scene or (subject.billing_scene if subject else None)
        if agent_id is None and subject is not None:
            agent_id = subject.agent_id
        scope_type = scope_type or (subject.scope_type if subject else None)
        scope_id = scope_id or (subject.scope_id if subject else None)
        if conversation_id is None and subject is not None:
            conversation_id = subject.conversation_id
        norm = normalize(raw)
        row = build_completed_row(
            user_id=resolved_id,
            user_type=resolved_type,
            source=source,
            stage=stage,
            conversation_id=conversation_id,
            as_session_id=as_session_id,
            model_code=model_code,
            unit_type="token",
            billing_scene=billing_scene,
            agent_id=agent_id,
            scope_type=scope_type,
            scope_id=scope_id,
            unattributed=unattributed,
            db=session,
        )
        row.input_count = norm.prompt_tokens
        row.output_count = norm.output_tokens
        row.token_count = norm.total_tokens
        row.cache_hit_input_count = norm.cache_hit
        row.cache_miss_input_count = norm.effective_cache_miss
        row.cache_write_input_count = norm.cache_write
        row.cache_reported = norm.cache_reported
        row.usage_status = norm.usage_status
        row.raw_usage_json = norm.raw or None
        session.add(row)
        session.flush()
        if own_session:
            session.commit()
        usage_id = row.id
    except Exception as exc:  # noqa: BLE001
        logger.critical("token 台账写入失败 source=%s：%s", source, exc, exc_info=True)
        if own_session:
            try:
                session.rollback()
            except Exception:  # noqa: BLE001
                pass
        return None
    finally:
        if own_session:
            session.close()

    if process_now and own_session:
        try:
            from .outbox import process_usage

            process_usage(usage_id)
        except Exception:  # noqa: BLE001
            logger.warning("token 同步出账失败，等待 worker usage=%s", usage_id, exc_info=True)
    return usage_id


def enforce_mode(db=None) -> str:
    own = db is None
    session = SessionLocal() if own else db
    try:
        row = get_settings_row(session)
        return row.enforce_mode if row is not None else "shadow"
    finally:
        if own:
            session.close()


def estimate_token_call_micro(
    db,
    *,
    model_code: str | None,
    prompt_text: str | None,
    max_output_tokens: int | None,
) -> int:
    """预授权/前置余额检查的保守估算：输入按未命中、输出按 max_output 上限。"""
    import math

    from .registry import get_active_price_version, get_model

    model = get_model(db, model_code)
    if model is None:
        return 0
    price = get_active_price_version(db, model.id)
    if price is None:
        return 0
    input_tokens = estimate_tokens(prompt_text)
    miss_weight = int(price.cache_miss_input_micro_per_1k or 0)
    out_weight = int(price.output_micro_per_1k or 0)
    output_bound = int(max_output_tokens or model.max_output or 0)
    raw = input_tokens * miss_weight / 1000 + output_bound * out_weight / 1000
    return int(math.ceil(raw)) if raw > 0 else 0


def guard_balance(db, *, user_type: str, user_id: int, required_micro: int) -> bool:
    """enforce 模式下的前置余额检查。返回 False 表示余额不足（调用方 → 402）。

    shadow/off 永不阻断；只有 enforce 才看余额与透支上限。
    """
    row = get_settings_row(db)
    mode = row.enforce_mode if row is not None else "shadow"
    if mode != "enforce":
        return True
    available = wallet.available_micro(db, user_type, user_id)
    overdraft = int(row.overdraft_limit_micro or 0) if row is not None else 0
    return available + overdraft >= max(0, int(required_micro))