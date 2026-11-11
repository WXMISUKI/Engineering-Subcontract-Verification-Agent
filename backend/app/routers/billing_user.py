"""用户端积分接口（设计 §18；对标 P0-1/P0-2/P0-3）。

- ``/api/user/wallet``：余额、冻结、可用、本月消耗（积分 + 元双口径）；
- ``/api/user/credits/ledger``：分页流水，含变动后余额、场景与智能体名；
- ``/api/user/usage``：按天/模型/场景聚合；
- ``/api/user/billing/estimate`` / ``/billing/task``：任务级预估与实际。

金额一律返回 ``*_micro/*_credits/*_yuan`` 三件套；微积分只做存储/计算。
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from ..auth.unified_user import UnifiedUser, get_current_any_user
from ..database import get_db
from ..limiter import limiter
from ..models.billing_models import CreditLedger, LlmModel, UsageCharge, UserWallet
from ..models.models import Agent, TokenUsage
from ..services.billing import attribution
from ..services.billing import estimates as estimates_svc
from ..services.billing import money, wallet as wallet_svc
from ..services.billing.scenes import SCENE_NAMES, scene_name

router = APIRouter(prefix="/api/user", tags=["积分与用量"])

VALID_ENTRY_TYPES = {"grant", "consume", "adjust", "reversal", "refund"}


def _require_scope_access(db, current_user: UnifiedUser, scope_type: str, scope_id) -> None:
    """不属于当前用户的 scope 一律 404（不暴露存在性；管理员走管理端接口）。"""
    if scope_id in (None, ""):
        return
    if not attribution.is_scope_owner(
        db,
        user_type=current_user.user_type,
        user_id=current_user.id,
        scope_type=scope_type,
        scope_id=scope_id,
    ):
        raise HTTPException(status_code=404, detail="资源不存在")


def _month_start() -> datetime:
    now = datetime.now()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _rate(db) -> int:
    """只读设置：GET 不得因为读取汇率而 flush/创建设置行。"""
    from ..models.billing_models import BillingSettings

    row = db.query(BillingSettings).filter(BillingSettings.id == 1).first()
    return int(
        (row.credits_per_yuan if row is not None else None)
        or money.DEFAULT_CREDITS_PER_YUAN
    )


@router.get("/wallet", summary="我的积分余额")
def my_wallet(
    db: Session = Depends(get_db),
    current_user: UnifiedUser = Depends(get_current_any_user),
):
    rate = _rate(db)
    wallet_row = db.query(UserWallet).filter(
        UserWallet.user_type == current_user.user_type,
        UserWallet.user_id == current_user.id,
    ).first()
    balance = int(wallet_row.balance_micro or 0) if wallet_row else 0
    frozen = int(wallet_row.frozen_micro or 0) if wallet_row else 0
    # 本月消耗取**账单**而不是流水 consume：阶梯/历史重算后账单才是实际口径；
    # 流水里是「旧 consume + 差额 adjust」，直接相加会把旧价重新算进来。
    spent_micro = (
        db.query(func.coalesce(func.sum(UsageCharge.charged_micro), 0))
        .filter(
            UsageCharge.user_type == current_user.user_type,
            UsageCharge.user_id == current_user.id,
            UsageCharge.status.in_(("rated", "partial")),
            UsageCharge.created_at >= _month_start(),
        )
        .scalar()
    )
    month_micro = abs(int(spent_micro or 0))
    payload = {
        "credits_per_yuan": rate,
        "wallet_exists": wallet_row is not None,
    }
    payload.update(money.fields("balance", balance, rate))
    payload.update(money.fields("frozen", frozen, rate))
    payload.update(money.fields("available", balance - frozen, rate))
    payload.update(money.fields("month_consumed", month_micro, rate))
    return payload


@router.get("/credits/ledger", summary="我的积分流水")
def my_credit_ledger(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    entry_type: str | None = Query(None, description="grant/consume/adjust/reversal/refund"),
    db: Session = Depends(get_db),
    current_user: UnifiedUser = Depends(get_current_any_user),
):
    if entry_type and entry_type not in VALID_ENTRY_TYPES:
        raise HTTPException(status_code=400, detail=f"entry_type 只允许 {sorted(VALID_ENTRY_TYPES)}")
    rate = _rate(db)
    query = db.query(CreditLedger).filter(
        CreditLedger.user_type == current_user.user_type,
        CreditLedger.user_id == current_user.id,
    )
    if entry_type:
        query = query.filter(CreditLedger.entry_type == entry_type)
    total = int(query.count())
    rows = (
        query.order_by(CreditLedger.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    agent_ids = {r.agent_id for r in rows if r.agent_id is not None}
    agent_names = {
        row[0]: row[1]
        for row in db.query(Agent.id, Agent.name).filter(Agent.id.in_(agent_ids)).all()
    } if agent_ids else {}

    items = []
    for r in rows:
        amount_micro = int(r.amount_micro or 0)
        # 历史流水可能没有余额快照：保持 None，不把「未知」显示成 0。
        balance_after = int(r.balance_after_micro) if r.balance_after_micro is not None else None
        items.append({
            "id": r.id,
            "entry_type": r.entry_type,
            "grant_source": r.grant_source,
            "billing_scene": r.billing_scene,
            "scene_name": scene_name(r.billing_scene),
            "agent_id": r.agent_id,
            "agent_name": agent_names.get(r.agent_id),
            "amount_micro": amount_micro,
            "amount_credits": money.credits(amount_micro),
            "amount_yuan": money.yuan(amount_micro, rate),
            "balance_after_micro": balance_after,
            "balance_after_credits": money.credits(balance_after) if balance_after is not None else None,
            "balance_after_yuan": money.yuan(balance_after, rate) if balance_after is not None else None,
            "reason": r.reason,
            "ref_type": r.ref_type,
            "ref_id": r.ref_id,
            "created_at": r.created_at,
        })
    return {
        "credits_per_yuan": rate,
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": items,
    }


@router.get("/usage", summary="我的用量（按天/模型/场景/智能体）")
def my_usage(
    days: int = Query(30, ge=1, le=365),
    group_by: str = Query("model", pattern="^(day|model|scene|agent)$"),
    db: Session = Depends(get_db),
    current_user: UnifiedUser = Depends(get_current_any_user),
):
    rate = _rate(db)
    since = datetime.now() - timedelta(days=days)
    base = db.query(TokenUsage).filter(
        TokenUsage.user_type == current_user.user_type,
        TokenUsage.user_id == current_user.id,
        TokenUsage.created_at >= since,
    )
    charge_base = db.query(UsageCharge).filter(
        UsageCharge.user_type == current_user.user_type,
        UsageCharge.user_id == current_user.id,
        UsageCharge.created_at >= since,
    )

    items = []
    if group_by == "day":
        usage_rows = (
            base.with_entities(
                func.date(TokenUsage.created_at),
                func.count(TokenUsage.id),
                func.sum(TokenUsage.input_count),
                func.sum(TokenUsage.output_count),
                func.sum(case((TokenUsage.usage_status == "unknown", 1), else_=0)),
            )
            .group_by(func.date(TokenUsage.created_at))
            .all()
        )
        charge_rows = dict(
            charge_base.with_entities(
                func.date(UsageCharge.created_at), func.sum(UsageCharge.charged_micro)
            )
            .group_by(func.date(UsageCharge.created_at))
            .all()
        )
        for day, calls, in_sum, out_sum, unknown_count in usage_rows:
            key = str(day)
            charged = int(charge_rows.get(day, 0) or 0)
            items.append({
                "day": key,
                "calls": int(calls or 0),
                "unknown_calls": int(unknown_count or 0),
                "input_tokens": int(in_sum) if in_sum is not None else None,
                "output_tokens": int(out_sum) if out_sum is not None else None,
                "charged_micro": charged,
                "charged_credits": money.credits(charged),
                "charged_yuan": money.yuan(charged, rate),
            })
        items.sort(key=lambda x: x["day"], reverse=True)
    elif group_by == "scene":
        # NULL 与显式 other 在 SQL 里先归一，避免两条并列再手工相加（会双计/漏计）。
        usage_scene = func.coalesce(TokenUsage.billing_scene, "other")
        charge_scene = func.coalesce(UsageCharge.billing_scene, "other")
        usage_rows = (
            base.with_entities(
                usage_scene,
                func.count(TokenUsage.id),
                func.sum(case((TokenUsage.usage_status == "unknown", 1), else_=0)),
            )
            .group_by(usage_scene)
            .all()
        )
        charge_rows = dict(
            charge_base.with_entities(charge_scene, func.sum(UsageCharge.charged_micro))
            .group_by(charge_scene)
            .all()
        )
        for key, calls, unknown_count in usage_rows:
            key = key or "other"
            charged = int(charge_rows.get(key, 0) or 0)
            items.append({
                "billing_scene": key,
                "scene_name": SCENE_NAMES.get(key, scene_name(key)),
                "calls": int(calls or 0),
                "unknown_calls": int(unknown_count or 0),
                "charged_micro": charged,
                "charged_credits": money.credits(charged),
                "charged_yuan": money.yuan(charged, rate),
            })
        items.sort(key=lambda x: x["charged_micro"], reverse=True)
    elif group_by == "agent":
        usage_rows = (
            base.with_entities(
                TokenUsage.agent_id,
                func.count(TokenUsage.id),
                func.sum(case((TokenUsage.usage_status == "unknown", 1), else_=0)),
            )
            .group_by(TokenUsage.agent_id)
            .all()
        )
        charge_rows = dict(
            charge_base.with_entities(UsageCharge.agent_id, func.sum(UsageCharge.charged_micro))
            .group_by(UsageCharge.agent_id)
            .all()
        )
        agent_ids = [agent_id for agent_id, _calls, _unknown in usage_rows if agent_id is not None]
        agent_names = (
            dict(db.query(Agent.id, Agent.name).filter(Agent.id.in_(agent_ids)).all())
            if agent_ids else {}
        )
        for agent_id, calls, unknown_count in usage_rows:
            charged = int(charge_rows.get(agent_id, 0) or 0)
            items.append({
                "agent_id": agent_id,
                "agent_name": agent_names.get(agent_id) or (
                    "未归属" if agent_id is None else f"智能体 #{agent_id}"
                ),
                "calls": int(calls or 0),
                "unknown_calls": int(unknown_count or 0),
                "charged_micro": charged,
                "charged_credits": money.credits(charged),
                "charged_yuan": money.yuan(charged, rate),
            })
        items.sort(key=lambda x: x["charged_micro"], reverse=True)
    else:
        usage_rows = (
            base.with_entities(
                TokenUsage.model_code,
                TokenUsage.unit_type,
                func.count(TokenUsage.id),
                func.sum(TokenUsage.input_count),
                func.sum(TokenUsage.output_count),
                func.sum(TokenUsage.billable_quantity),
                func.sum(case((TokenUsage.usage_status == "unknown", 1), else_=0)),
            )
            .group_by(TokenUsage.model_code, TokenUsage.unit_type)
            .all()
        )
        model_ids = {
            row[0]: row[1]
            for row in db.query(LlmModel.code, LlmModel.id).all()
        }
        charge_sums = dict(
            charge_base.with_entities(UsageCharge.model_id, func.sum(UsageCharge.charged_micro))
            .group_by(UsageCharge.model_id)
            .all()
        )
        for model_code, unit_type, calls, in_sum, out_sum, qty_sum, unknown_count in usage_rows:
            charged = int(charge_sums.get(model_ids.get(model_code), 0) or 0)
            items.append({
                "model_code": model_code,
                "unit_type": unit_type,
                "calls": int(calls or 0),
                "unknown_calls": int(unknown_count or 0),
                "input_tokens": int(in_sum) if in_sum is not None else None,
                "output_tokens": int(out_sum) if out_sum is not None else None,
                "billable_quantity": float(qty_sum) if qty_sum is not None else None,
                "charged_micro": charged,
                "charged_credits": money.credits(charged),
                "charged_yuan": money.yuan(charged, rate),
            })
    return {"days": days, "group_by": group_by, "credits_per_yuan": rate, "items": items}


def _with_available(db, current_user: UnifiedUser, payload: dict) -> dict:
    payload["available_micro"] = wallet_svc.available_micro(
        db, current_user.user_type, current_user.id
    )
    payload["available_credits"] = money.credits(payload["available_micro"])
    payload["available_yuan"] = money.yuan(payload["available_micro"], payload["credits_per_yuan"])
    return payload


@router.get("/billing/estimate", summary="任务级预估（纯预览，不落库）")
@limiter.limit("60/minute")
def billing_estimate(
    request: Request,
    scope_type: str = Query(..., min_length=1, max_length=32),
    scope_id: str | None = Query(None, max_length=64),
    agent_slug: str | None = Query(None, max_length=100),
    input_text: str | None = Query(None, max_length=8000),
    max_output_tokens: int | None = Query(None, ge=0),
    model_code: str | None = Query(None, max_length=64),
    source: str | None = Query(None, max_length=32),
    scene: str | None = Query(None, max_length=32),
    db: Session = Depends(get_db),
    current_user: UnifiedUser = Depends(get_current_any_user),
):
    _require_scope_access(db, current_user, scope_type, scope_id)
    payload = estimates_svc.estimate(
        db,
        user_type=current_user.user_type,
        user_id=current_user.id,
        scope_type=scope_type,
        scope_id=scope_id,
        agent_slug=agent_slug,
        input_text=input_text,
        max_output_tokens=max_output_tokens,
        model_code=model_code,
        source=source,
        scene=scene,
        persist=False,  # GET 只算不写：避免草稿预览批量造行/覆盖他人预估
    )
    return _with_available(db, current_user, payload)


class EstimatePersistBody(BaseModel):
    scope_type: str = Field(..., min_length=1, max_length=32)
    scope_id: str = Field(..., min_length=1, max_length=64)
    agent_slug: str | None = Field(None, max_length=100)
    # 长提示词放 body：不进 URL/访问日志。
    input_text: str | None = Field(None, max_length=8000)
    max_output_tokens: int | None = Field(None, ge=0)
    model_code: str | None = Field(None, max_length=64)
    source: str | None = Field(None, max_length=32)
    scene: str | None = Field(None, max_length=32)
    assume_defaults: bool = False


@router.post("/billing/estimate", summary="任务级预估（显式落库）")
@limiter.limit("20/minute")
def persist_billing_estimate(
    request: Request,
    body: EstimatePersistBody,
    db: Session = Depends(get_db),
    current_user: UnifiedUser = Depends(get_current_any_user),
):
    _require_scope_access(db, current_user, body.scope_type, body.scope_id)
    payload = estimates_svc.estimate(
        db,
        user_type=current_user.user_type,
        user_id=current_user.id,
        scope_type=body.scope_type,
        scope_id=body.scope_id,
        agent_slug=body.agent_slug,
        input_text=body.input_text,
        max_output_tokens=body.max_output_tokens,
        model_code=body.model_code,
        source=body.source,
        scene=body.scene,
        persist=True,
        assume_defaults=body.assume_defaults,
    )
    db.commit()
    return _with_available(db, current_user, payload)


@router.get("/billing/task", summary="任务级预估与实际")
@limiter.limit("60/minute")
def billing_task(
    request: Request,
    scope_type: str = Query(..., min_length=1, max_length=32),
    scope_id: str = Query(..., min_length=1, max_length=64),
    db: Session = Depends(get_db),
    current_user: UnifiedUser = Depends(get_current_any_user),
):
    _require_scope_access(db, current_user, scope_type, scope_id)
    return estimates_svc.task_summary(
        db,
        scope_type=scope_type,
        scope_id=scope_id,
        user_type=current_user.user_type,
        user_id=current_user.id,
    )