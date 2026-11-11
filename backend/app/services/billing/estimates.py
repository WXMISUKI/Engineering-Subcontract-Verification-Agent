"""任务级预估 / 实际（设计 §18 第 2 条）。

底层仍是 token 四桶/非 token 计量；这里只做三件事：

1. **预估**：输入文本 + 输出上限 × 当前生效价格版本。算不出来就返回
   ``estimated=false`` + 原因，**绝不伪造 0**；预览（GET）不落库，只有
   发送/任务启动路径显式 ``persist=True`` 才写 ``billing_estimates``。
2. **汇总结算**：按 ``(user_type, user_id, billing_scope_type, billing_scope_id)``
   聚合账单与台账，显式区分「已结算 / 结算中 / 价格未配置 / 未知」；
   预估与结算价格版本不一致时显式标记。
3. **门槛**：enforce 模式下按预估金额或 ``billing_settings.min_balance_micro``
   做前置余额检查，返回结构化 402 detail；shadow/off 永不阻断。

归属：预估、账单、台账查询全部带 ``user_type/user_id``，scope 归属由
``attribution.is_scope_owner`` 在路由层校验（不属于当前用户 → 404）。
"""

from __future__ import annotations

import math
from typing import Optional

from sqlalchemy.exc import IntegrityError

from ...models.billing_models import BillingEstimate, BillingSettings, CreditLedger, UsageCharge
from ...models.models import Agent, TokenUsage
from . import money
from .billing_settings import ensure_settings_row
from .instrument import estimate_tokens
from .registry import (
    get_active_price_version,
    get_default_model,
    get_model,
    resolve_model_for_source,
)
from .scenes import normalize_scene, scene_for_scope


def _settings_for_read(db) -> Optional[BillingSettings]:
    """只读设置行：预览接口不得因为 GET 而创建/写入设置行。"""
    try:
        return db.query(BillingSettings).filter(BillingSettings.id == 1).first()
    except Exception:  # noqa: BLE001
        return None


def _rate(settings_row) -> int:
    return int(
        (settings_row.credits_per_yuan if settings_row is not None else None)
        or money.DEFAULT_CREDITS_PER_YUAN
    )


def _resolve_model(db, *, agent_slug: str | None, model_code: str | None, source: str | None = None):
    """模型取显式 code > 来源映射（chat→coze-default 等）> 平台默认。

    智能体不携带价格/等级（§18 第 4 条），agent_slug 只用于场景/归属。
    """
    if not model_code:
        from .model_context import current_model_code

        model_code = current_model_code()
    if model_code:
        return get_model(db, model_code)
    if source:
        return resolve_model_for_source(db, source)
    return get_default_model(db)


def _price_amount(
    *, input_tokens: int, output_tokens: int, price_version
) -> tuple[Optional[int], Optional[str]]:
    """按未命中输入 + 输出上限估；缺权重/缺输出上限返回 (None, 原因)。"""
    miss = price_version.cache_miss_input_micro_per_1k
    out = price_version.output_micro_per_1k
    if miss is None or out is None:
        return None, "price_incomplete"
    if int(output_tokens) <= 0:
        # 没有输出上限就是没法估——不允许把「未知输出」当 0 输出算出一个假值。
        return None, "output_bound_missing"
    raw = input_tokens * int(miss) / 1000 + output_tokens * int(out) / 1000
    return (int(math.ceil(raw)) if raw > 0 else 0), None


def estimate(
    db,
    *,
    user_type: str,
    user_id: int,
    scope_type: str,
    scope_id: str | None = None,
    agent_slug: str | None = None,
    input_text: str | None = None,
    max_output_tokens: int | None = None,
    model_code: str | None = None,
    source: str | None = None,
    scene: str | None = None,
    persist: bool = False,
    assume_defaults: bool = False,
) -> dict:
    resolved_scene = normalize_scene(scene or scene_for_scope(scope_type))
    # 计费旁路：旧库/测试库没有计费表时按「无法预估」返回，不阻断主业务。
    try:
        from .schema import billing_tables_available

        if not billing_tables_available(db.get_bind()):
            return {
                "estimated": False,
                "reason": "billing_unavailable",
                "scope_type": scope_type,
                "scope_id": scope_id,
                "scene": resolved_scene,
                "credits_per_yuan": money.DEFAULT_CREDITS_PER_YUAN,
            }
    except Exception:  # noqa: BLE001
        return {
            "estimated": False,
            "reason": "billing_unavailable",
            "scope_type": scope_type,
            "scope_id": scope_id,
            "scene": resolved_scene,
            "credits_per_yuan": money.DEFAULT_CREDITS_PER_YUAN,
        }

    settings_row = ensure_settings_row(db) if persist else _settings_for_read(db)
    rate = _rate(settings_row)
    model = _resolve_model(db, agent_slug=agent_slug, model_code=model_code, source=source)

    def _unavailable(reason: str, **extra) -> dict:
        payload = {
            "estimated": False,
            "reason": reason,
            "scope_type": scope_type,
            "scope_id": scope_id,
            "scene": resolved_scene,
            "credits_per_yuan": rate,
        }
        payload.update(extra)
        return payload

    if model is None:
        return _unavailable("model_missing", model_code=model_code, agent_slug=agent_slug)
    price_version = get_active_price_version(db, model.id)
    if price_version is None:
        return _unavailable("price_missing", model_code=model.code)

    # 输入：真实文本 > 场景默认（Plus 明确标注）> 无法估算
    if input_text is not None:
        input_tokens = estimate_tokens(input_text)
        input_basis = "request_text"
    elif assume_defaults and settings_row is not None and int(settings_row.scene_default_input_tokens or 0) > 0:
        input_tokens = int(settings_row.scene_default_input_tokens)
        input_basis = "scene_default"
    else:
        input_tokens = 0
        input_basis = "missing"

    # 输出：请求上限 > 模型 max_output > 场景/平台默认（明确标注）
    if max_output_tokens is not None:
        output_tokens = int(max_output_tokens)
        output_basis = "request"
    elif model.max_output:
        output_tokens = int(model.max_output)
        output_basis = "model_max_output"
    elif assume_defaults and settings_row is not None and int(settings_row.scene_default_output_tokens or 0) > 0:
        output_tokens = int(settings_row.scene_default_output_tokens)
        output_basis = "scene_default"
    elif settings_row is not None and int(settings_row.default_output_tokens or 0) > 0:
        output_tokens = int(settings_row.default_output_tokens)
        output_basis = "platform_default"
    else:
        output_tokens = 0
        output_basis = "missing"

    estimated_micro, reason = _price_amount(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        price_version=price_version,
    )
    if estimated_micro is None:
        return _unavailable(
            reason or "price_incomplete",
            model_code=model.code,
            price_version_id=price_version.id,
            input_tokens_est=input_tokens,
            output_tokens_est=output_tokens,
            input_basis=input_basis,
            output_basis=output_basis,
        )
    if estimated_micro <= 0:
        return _unavailable(
            "nothing_to_estimate", model_code=model.code,
            price_version_id=price_version.id,
            input_tokens_est=input_tokens,
            output_tokens_est=output_tokens,
        )

    agent_id = None
    if agent_slug:
        agent = db.query(Agent).filter(Agent.slug == agent_slug).first()
        agent_id = agent.id if agent is not None else None

    if persist and scope_id not in (None, ""):
        _upsert_estimate(
            db,
            user_type=user_type,
            user_id=user_id,
            scope_type=scope_type,
            scope_id=str(scope_id),
            model_code=model.code,
            scene=resolved_scene,
            agent_id=agent_id,
            price_version_id=price_version.id,
            estimated_micro=int(estimated_micro),
            input_tokens_est=input_tokens,
            output_tokens_est=output_tokens,
            input_basis=input_basis,
            output_basis=output_basis,
        )

    return {
        "estimated": True,
        "estimated_micro": int(estimated_micro),
        "estimated_credits": money.credits(estimated_micro),
        "estimated_yuan": money.yuan(estimated_micro, rate),
        "price_version_id": price_version.id,
        "model_code": model.code,
        "scene": resolved_scene,
        "agent_id": agent_id,
        "scope_type": scope_type,
        "scope_id": scope_id,
        "input_tokens_est": input_tokens,
        "output_tokens_est": output_tokens,
        # 估算依据：前端必须如实标注“按平台预期输出上限/按场景估算”，
        # 不能把平台假设说成真实输入。
        "input_basis": input_basis,
        "output_basis": output_basis,
        "estimate_basis": (
            "default_estimate"
            if input_basis == "scene_default" or output_basis in ("platform_default", "scene_default")
            else "request_estimate"
        ),
        "credits_per_yuan": rate,
        "threshold": int(estimated_micro),
        "threshold_micro": int(estimated_micro),
    }


def _upsert_estimate(db, *, user_type: str, user_id: int, scope_type: str, scope_id: str, **fields) -> None:
    """按归属 upsert；并发唯一键冲突时重查更新，不 500。"""
    query = lambda: (  # noqa: E731
        db.query(BillingEstimate)
        .filter(
            BillingEstimate.user_type == user_type,
            BillingEstimate.user_id == int(user_id),
            BillingEstimate.scope_type == scope_type,
            BillingEstimate.scope_id == scope_id,
        )
        .first()
    )
    row = query()
    if row is None:
        row = BillingEstimate(
            user_type=user_type, user_id=int(user_id),
            scope_type=scope_type, scope_id=scope_id,
        )
        db.add(row)
    for key, value in fields.items():
        setattr(row, key, value)
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        row = query()
        if row is None:
            raise
        for key, value in fields.items():
            setattr(row, key, value)
        db.flush()


def _current_balance(db, user_type: str, user_id: int) -> Optional[int]:
    """当前余额：钱包优先，其次最后一条流水快照；NULL 保持 NULL（不显示 0）。"""
    try:
        from ...models.billing_models import UserWallet

        wallet = (
            db.query(UserWallet.balance_micro)
            .filter(UserWallet.user_type == user_type, UserWallet.user_id == int(user_id))
            .first()
        )
        if wallet is not None and wallet[0] is not None:
            return int(wallet[0])
    except Exception:  # noqa: BLE001
        pass
    row = (
        db.query(CreditLedger.balance_after_micro)
        .filter(CreditLedger.user_type == user_type, CreditLedger.user_id == int(user_id))
        .order_by(CreditLedger.id.desc())
        .first()
    )
    return int(row[0]) if row is not None and row[0] is not None else None


def task_summary(
    db,
    *,
    scope_type: str,
    scope_id: str,
    user_type: str,
    user_id: int,
) -> dict:
    settings_row = _settings_for_read(db)
    rate = _rate(settings_row)
    sid = str(scope_id)

    estimate_row = (
        db.query(BillingEstimate)
        .filter(
            BillingEstimate.user_type == user_type,
            BillingEstimate.user_id == int(user_id),
            BillingEstimate.scope_type == scope_type,
            BillingEstimate.scope_id == sid,
        )
        .first()
    )
    estimate_payload = None
    if estimate_row is not None:
        estimate_payload = {
            "estimated": True,
            "estimated_micro": int(estimate_row.estimated_micro or 0),
            "estimated_credits": money.credits(estimate_row.estimated_micro),
            "estimated_yuan": money.yuan(estimate_row.estimated_micro, rate),
            "price_version_id": estimate_row.price_version_id,
            "model_code": estimate_row.model_code,
            "scene": estimate_row.scene,
            "agent_id": estimate_row.agent_id,
            "input_tokens_est": estimate_row.input_tokens_est,
            "output_tokens_est": estimate_row.output_tokens_est,
            "input_basis": estimate_row.input_basis,
            "output_basis": estimate_row.output_basis,
            "estimate_basis": (
                "default_estimate"
                if (estimate_row.input_basis == "scene_default"
                    or estimate_row.output_basis in ("platform_default", "scene_default"))
                else "request_estimate"
            ),
            "created_at": estimate_row.created_at,
        }

    charges = (
        db.query(UsageCharge)
        .filter(
            UsageCharge.user_type == user_type,
            UsageCharge.user_id == int(user_id),
            UsageCharge.billing_scope_type == scope_type,
            UsageCharge.billing_scope_id == sid,
        )
        .all()
    )
    actual_micro = sum(int(c.charged_micro or 0) for c in charges)
    unknown_rows = sum(1 for c in charges if (c.status or "") == "unknown")
    partial_rows = sum(1 for c in charges if (c.status or "") == "partial")
    price_versions = sorted({int(c.price_version_id) for c in charges if c.price_version_id})

    # 结算链路状态：price_missing / pending 不产生账单，不能显示成「尚无消耗」。
    usage_rows = (
        db.query(TokenUsage)
        .filter(
            TokenUsage.user_type == user_type,
            TokenUsage.user_id == int(user_id),
            TokenUsage.billing_scope_type == scope_type,
            TokenUsage.billing_scope_id == sid,
        )
        .all()
    )
    started_rows = sum(1 for r in usage_rows if (r.status or "") == "started")
    settlement = {
        "usage_rows": len(usage_rows),
        # 运行中的 started 行也是「结算中」，不能显示成「尚无消耗」。
        "running_rows": started_rows,
        "pending_rows": started_rows + sum(
            1 for r in usage_rows
            if (r.status or "completed") == "completed"
            and (r.billing_status or "pending") == "pending"
        ),
        "price_missing_rows": sum(1 for r in usage_rows if (r.billing_status or "") == "price_missing"),
        "unknown_usage_rows": sum(1 for r in usage_rows if (r.usage_status or "") == "unknown"),
        "partial_usage_rows": sum(1 for r in usage_rows if (r.usage_status or "") == "partial"),
        "interrupted_rows": sum(1 for r in usage_rows if (r.status or "") == "interrupted"),
        "failed_rows": sum(1 for r in usage_rows if (r.status or "") == "failed"),
    }

    by_model: dict[Optional[int], dict] = {}
    by_scene: dict[str, dict] = {}
    for c in charges:
        m = by_model.setdefault(c.model_id, {"model_id": c.model_id, "micro": 0, "rows": 0})
        m["micro"] += int(c.charged_micro or 0)
        m["rows"] += 1
        scene_key = c.billing_scene or "other"
        s = by_scene.setdefault(scene_key, {"billing_scene": scene_key, "micro": 0, "rows": 0})
        s["micro"] += int(c.charged_micro or 0)
        s["rows"] += 1

    model_codes = {}
    if by_model:
        from ...models.billing_models import LlmModel

        model_codes = {
            row[0]: row[1]
            for row in db.query(LlmModel.id, LlmModel.code)
            .filter(LlmModel.id.in_([k for k in by_model if k is not None]))
            .all()
        }
    for item in by_model.values():
        item["model_code"] = model_codes.get(item["model_id"])
        item["amount_micro"] = item.pop("micro")
        item["amount_credits"] = money.credits(item["amount_micro"])
        item["amount_yuan"] = money.yuan(item["amount_micro"], rate)
    for item in by_scene.values():
        item["amount_micro"] = item.pop("micro")
        item["amount_credits"] = money.credits(item["amount_micro"])
        item["amount_yuan"] = money.yuan(item["amount_micro"], rate)

    # 只要实际版本集合里出现任何与预估不同的版本就标记（含混合版本），
    # 不能只在“预估版本完全缺席”时才提示。
    mismatch = bool(
        estimate_payload
        and estimate_payload.get("price_version_id")
        and price_versions
        and set(price_versions) != {int(estimate_payload["price_version_id"])}
    )

    actual_payload = {
        "actual_micro": actual_micro,
        "actual_credits": money.credits(actual_micro),
        "actual_yuan": money.yuan(actual_micro, rate),
        "rows": len(charges),
        "unknown_rows": unknown_rows,
        "partial_rows": partial_rows,
        "price_version_ids": price_versions,
        "has_unknown": unknown_rows > 0,
        "settlement": settlement,
    }

    current_balance = _current_balance(db, user_type, user_id)
    return {
        "scope_type": scope_type,
        "scope_id": sid,
        "credits_per_yuan": rate,
        "estimate": estimate_payload,
        "actual": actual_payload,
        "by_model": list(by_model.values()),
        "by_scene": list(by_scene.values()),
        "unknown_rows": unknown_rows,
        "partial_rows": partial_rows,
        "settlement": settlement,
        "price_version_mismatch": mismatch,
        "estimate_price_version_id": (estimate_payload or {}).get("price_version_id"),
        "actual_price_version_ids": price_versions,
        "current_balance_micro": current_balance,
        "current_balance_credits": money.credits(current_balance),
        "current_balance_yuan": money.yuan(current_balance, rate),
    }


def _gate_detail(
    *,
    required_micro: int,
    available_micro: int,
    min_balance_micro: int,
    estimated_micro: Optional[int],
    model_code: Optional[str],
    scene: str,
    scope_type: Optional[str],
    scope_id,
) -> dict:
    return {
        "code": "INSUFFICIENT_CREDITS",
        "message": "积分不足，无法开始该任务",
        # 没有可靠预估时为 None（不是 0，避免把「未知」当「免费」）。
        "estimated_micro": estimated_micro,
        "required_micro": int(required_micro),
        "available_micro": int(available_micro),
        "min_balance_micro": int(min_balance_micro),
        "model_code": model_code,
        "scene": scene,
        "scope_type": scope_type,
        "scope_id": (str(scope_id) if scope_id is not None else None),
    }


def _decide_gate(
    db,
    *,
    user_type: str,
    user_id: int,
    estimated_micro: Optional[int],
    model_code: Optional[str],
    scene: str,
    scope_type: Optional[str],
    scope_id,
    settings_row,
) -> dict:
    from . import wallet

    mode = (settings_row.enforce_mode if settings_row is not None else "shadow")
    if mode != "enforce":
        return {"ok": True}
    min_balance = int(settings_row.min_balance_micro or 0) if settings_row is not None else 0
    # 有可靠预估时**只看预估**：min_balance 是「拿不到预估」时的兜底门槛，
    # 不是每笔请求的额外下限。若 Plus 确实需要额外最低余额，另加显式参数，
    # 不要再用 max 把两者混成一个数（会把有预估的正常请求误拦）。
    if estimated_micro is not None:
        required = int(estimated_micro)
    else:
        required = min_balance
    available = wallet.available_micro(db, user_type, user_id)
    overdraft = int(settings_row.overdraft_limit_micro or 0) if settings_row is not None else 0
    if available + overdraft >= required:
        return {"ok": True}
    return {
        "ok": False,
        "detail": _gate_detail(
            required_micro=required,
            available_micro=available,
            min_balance_micro=min_balance,
            estimated_micro=estimated_micro,
            model_code=model_code,
            scene=scene,
            scope_type=scope_type,
            scope_id=scope_id,
        ),
    }


def assert_affordable(db, *, user_type: str, user_id: int, estimate_payload: dict) -> dict:
    """聊天类前置检查：有预估按预估，无预估按 min_balance（enforce 才生效）。"""
    settings_row = _settings_for_read(db)
    estimated_micro = (
        int(estimate_payload.get("estimated_micro"))
        if estimate_payload.get("estimated") and estimate_payload.get("estimated_micro") is not None
        else None
    )
    return _decide_gate(
        db,
        user_type=user_type,
        user_id=user_id,
        estimated_micro=estimated_micro,
        model_code=estimate_payload.get("model_code"),
        scene=estimate_payload.get("scene")
        or scene_for_scope(estimate_payload.get("scope_type")),
        scope_type=estimate_payload.get("scope_type"),
        scope_id=estimate_payload.get("scope_id"),
        settings_row=settings_row,
    )


def task_gate(
    db,
    *,
    user_type: str,
    user_id: int,
    scope_type: str,
    scope_id=None,
    input_text: str | None = None,
    max_output_tokens: int | None = None,
    model_code: str | None = None,
    source: str | None = None,
    persist: bool = False,
    estimate_task: bool = True,
    scene: str | None = None,
) -> dict:
    """Plus 任务启动门槛。

    有可靠输入时按真实输入估；没有就按场景默认估算并标注（``scene_default``）；
    ``estimate_task=False``（如 TTS 非 token 计价）只做最低余额闸门，不伪造预估。
    """
    settings_row = _settings_for_read(db)
    # 估算用于任务页展示，shadow/off 也照常算；只有 enforce 才拦截。
    payload = None
    if estimate_task:
        payload = estimate(
            db,
            user_type=user_type,
            user_id=user_id,
            scope_type=scope_type,
            scope_id=scope_id,
            input_text=input_text,
            max_output_tokens=max_output_tokens,
            model_code=model_code,
            source=source,
            persist=persist,
            assume_defaults=True,
        )
    if (settings_row.enforce_mode if settings_row is not None else "shadow") != "enforce":
        return {"ok": True, "estimate": payload}
    estimated_micro = (
        int(payload["estimated_micro"])
        if payload and payload.get("estimated") and payload.get("estimated_micro") is not None
        else None
    )
    return _decide_gate(
        db,
        user_type=user_type,
        user_id=user_id,
        estimated_micro=estimated_micro,
        model_code=(payload or {}).get("model_code"),
        scene=scene or (payload or {}).get("scene") or scene_for_scope(scope_type),
        scope_type=scope_type,
        scope_id=scope_id,
        settings_row=settings_row,
    )