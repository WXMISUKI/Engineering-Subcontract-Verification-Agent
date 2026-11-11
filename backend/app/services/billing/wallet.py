"""钱包 / 只追加流水 / 预授权（设计 §4.4–4.6、§11.3）。

金额全部是**微积分整数**。所有余额变更都在同一事务里「锁钱包 → 写流水 →
改余额」，``credit_ledger.idempotency_key`` 唯一保证重复提交不重复加减。
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.exc import IntegrityError

from ...models.billing_models import CreditHold, CreditLedger, UserWallet

logger = logging.getLogger(__name__)


class InsufficientCredits(RuntimeError):
    def __init__(self, required_micro: int, available_micro: int):
        super().__init__("积分不足")
        self.required_micro = int(required_micro)
        self.available_micro = int(available_micro)


class NegativeBalanceRejected(RuntimeError):
    pass


def get_wallet(db, user_type: str, user_id: int) -> Optional[UserWallet]:
    return db.query(UserWallet).filter(
        UserWallet.user_type == user_type, UserWallet.user_id == user_id
    ).first()


def get_or_create_wallet(db, user_type: str, user_id: int) -> UserWallet:
    wallet = get_wallet(db, user_type, user_id)
    if wallet is not None:
        return wallet
    wallet = UserWallet(user_type=user_type, user_id=user_id,
                        balance_micro=0, frozen_micro=0, version=0)
    db.add(wallet)
    db.flush()
    return wallet


def _lock_wallet(db, user_type: str, user_id: int) -> UserWallet:
    wallet = (
        db.query(UserWallet)
        .filter(UserWallet.user_type == user_type, UserWallet.user_id == user_id)
        .with_for_update()
        .first()
    )
    if wallet is None:
        wallet = get_or_create_wallet(db, user_type, user_id)
    return wallet


def available_micro(db, user_type: str, user_id: int) -> int:
    wallet = get_wallet(db, user_type, user_id)
    if wallet is None:
        return 0
    return int(wallet.balance_micro or 0) - int(wallet.frozen_micro or 0)


def append_ledger(
    db,
    *,
    user_type: str,
    user_id: int,
    entry_type: str,
    amount_micro: int,
    idempotency_key: str,
    grant_source: str | None = None,
    ref_type: str | None = None,
    ref_id: str | None = None,
    usage_id: int | None = None,
    price_version_id: int | None = None,
    operator_id: int | None = None,
    reason: str | None = None,
    billing_scene: str | None = None,
    agent_id: int | None = None,
    billing_scope_type: str | None = None,
    billing_scope_id: str | None = None,
) -> tuple[CreditLedger, UserWallet]:
    """追加一条流水并更新余额快照。幂等键命中时原样返回，不再加减。"""
    existing = db.query(CreditLedger).filter(
        CreditLedger.idempotency_key == idempotency_key
    ).first()
    if existing is not None:
        wallet = get_wallet(db, user_type, user_id)
        return existing, wallet

    wallet = _lock_wallet(db, user_type, user_id)
    amount = int(amount_micro)
    new_balance = int(wallet.balance_micro or 0) + amount
    ledger = CreditLedger(
        user_type=user_type,
        user_id=user_id,
        entry_type=entry_type,
        grant_source=grant_source,
        amount_micro=amount,
        balance_after_micro=new_balance,
        ref_type=ref_type,
        ref_id=ref_id,
        usage_id=usage_id,
        price_version_id=price_version_id,
        operator_id=operator_id,
        reason=(reason or None),
        billing_scene=billing_scene,
        agent_id=agent_id,
        billing_scope_type=billing_scope_type,
        billing_scope_id=(str(billing_scope_id) if billing_scope_id is not None else None),
        idempotency_key=idempotency_key,
    )
    db.add(ledger)
    wallet.balance_micro = new_balance
    wallet.version = int(wallet.version or 0) + 1
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        # 并发重复：唯一键兜底，回读原流水，余额已由另一事务更新。
        existing = db.query(CreditLedger).filter(
            CreditLedger.idempotency_key == idempotency_key
        ).first()
        if existing is None:
            raise
        return existing, get_wallet(db, user_type, user_id)
    return ledger, wallet


def adjust(
    db,
    *,
    user_type: str,
    user_id: int,
    amount_micro: int,
    idempotency_key: str,
    entry_type: str = "adjust",
    grant_source: str = "admin",
    operator_id: int | None = None,
    reason: str | None = None,
    allow_negative: bool = False,
) -> tuple[CreditLedger, UserWallet]:
    """管理员发放/扣减/冲正。amount 有符号；重复提交按幂等键返回原结果。"""
    existing = db.query(CreditLedger).filter(
        CreditLedger.idempotency_key == idempotency_key
    ).first()
    if existing is not None:
        return existing, get_wallet(db, user_type, user_id)
    wallet = get_wallet(db, user_type, user_id) or get_or_create_wallet(db, user_type, user_id)
    if int(wallet.balance_micro or 0) + int(amount_micro) < 0 and not allow_negative:
        raise NegativeBalanceRejected("调整后余额不能为负（如需允许请显式允许负数）")
    return append_ledger(
        db,
        user_type=user_type,
        user_id=user_id,
        entry_type=entry_type,
        amount_micro=int(amount_micro),
        idempotency_key=idempotency_key,
        grant_source=grant_source,
        ref_type="admin_adjust",
        ref_id=idempotency_key,
        operator_id=operator_id,
        reason=reason,
        billing_scene="admin_adjust",
    )


def ensure_welcome_credit(db, *, user_type: str, user_id: int, amount_micro: int) -> Optional[CreditLedger]:
    """新用户欢迎积分：幂等键 ``welcome:{user_type}:{user_id}``。"""
    if amount_micro is None or int(amount_micro) <= 0:
        return None
    key = f"welcome:{user_type}:{user_id}"
    existing = db.query(CreditLedger).filter(CreditLedger.idempotency_key == key).first()
    if existing is not None:
        return existing
    ledger, _ = append_ledger(
        db,
        user_type=user_type,
        user_id=user_id,
        entry_type="grant",
        amount_micro=int(amount_micro),
        idempotency_key=key,
        grant_source="welcome",
        ref_type="welcome",
        ref_id=str(user_id),
        reason="新用户欢迎积分",
        billing_scene="welcome",
    )
    return ledger


def consume_for_usage(db, *, user_type: str, user_id: int, usage_id: int,
                      charged_micro: int, price_version_id: int | None = None,
                      reason: str | None = None,
                      billing_scene: str | None = None,
                      agent_id: int | None = None,
                      billing_scope_type: str | None = None,
                      billing_scope_id: str | None = None) -> tuple[CreditLedger, UserWallet]:
    """按账单扣费。幂等键 ``usage:{usage_id}``：重复出账不双扣。"""
    return append_ledger(
        db,
        user_type=user_type,
        user_id=user_id,
        entry_type="consume",
        amount_micro=-abs(int(charged_micro)),
        idempotency_key=f"usage:{usage_id}",
        ref_type="usage",
        ref_id=str(usage_id),
        usage_id=usage_id,
        price_version_id=price_version_id,
        reason=reason,
        billing_scene=billing_scene,
        agent_id=agent_id,
        billing_scope_type=billing_scope_type,
        billing_scope_id=billing_scope_id,
    )


def hold(
    db,
    *,
    user_type: str,
    user_id: int,
    held_micro: int,
    scope_type: str | None = None,
    scope_id: str | None = None,
    model_id: int | None = None,
    ttl_seconds: int = 900,
    overdraft_limit_micro: int = 0,
) -> CreditHold:
    """预授权：冻结 held_micro。可用（含透支上限）不足 → InsufficientCredits（→ 402）。"""
    held = max(0, int(held_micro))
    wallet = _lock_wallet(db, user_type, user_id)
    available = int(wallet.balance_micro or 0) - int(wallet.frozen_micro or 0)
    if available + int(overdraft_limit_micro) < held:
        raise InsufficientCredits(held, available)
    wallet.frozen_micro = int(wallet.frozen_micro or 0) + held
    wallet.version = int(wallet.version or 0) + 1
    record = CreditHold(
        user_type=user_type,
        user_id=user_id,
        scope_type=scope_type,
        scope_id=str(scope_id) if scope_id is not None else None,
        model_id=model_id,
        held_micro=held,
        settled_micro=0,
        status="held",
        expires_at=datetime.now() + timedelta(seconds=ttl_seconds),
    )
    db.add(record)
    db.flush()
    return record


def settle(db, hold_row: CreditHold, *, actual_micro: int,
           price_version_id: int | None = None,
           overdraft_limit_micro: int = 0) -> CreditHold:
    """结算实际用量：释放冻结，按实际扣费（允许到透支上限）。"""
    if hold_row.status != "held":
        return hold_row
    wallet = _lock_wallet(db, hold_row.user_type, hold_row.user_id)
    held = int(hold_row.held_micro or 0)
    actual = max(0, int(actual_micro))
    wallet.frozen_micro = max(0, int(wallet.frozen_micro or 0) - held)
    hold_row.settled_micro = actual
    hold_row.status = "settled"
    hold_row.settled_at = datetime.now()
    if actual > 0:
        # 余额可被扣到负，但不超过透支上限之外太多：这里不拒绝已产生的用量，
        # 只记录；enforce 的前置检查负责在调用前拦截。
        append_ledger(
            db,
            user_type=hold_row.user_type,
            user_id=hold_row.user_id,
            entry_type="consume",
            amount_micro=-actual,
            idempotency_key=f"hold:{hold_row.id}:settle",
            ref_type="hold",
            ref_id=str(hold_row.id),
            price_version_id=price_version_id,
            reason="预授权结算",
        )
    wallet.version = int(wallet.version or 0) + 1
    db.flush()
    return hold_row


def release(db, hold_row: CreditHold) -> CreditHold:
    """释放预授权（失败/中断）：不扣费。"""
    if hold_row.status != "held":
        return hold_row
    wallet = _lock_wallet(db, hold_row.user_type, hold_row.user_id)
    held = int(hold_row.held_micro or 0)
    if held:
        wallet.frozen_micro = max(0, int(wallet.frozen_micro or 0) - held)
        wallet.version = int(wallet.version or 0) + 1
    hold_row.status = "released"
    hold_row.settled_at = datetime.now()
    db.flush()
    return hold_row