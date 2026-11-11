"""新用户计费初始化：钱包 + 欢迎积分（设计 §10.1）。

在**创建用户同一事务**里调用；幂等键 ``welcome:{user_type}:{user_id}``
保证重试/补建不会重复发积分。历史用户不补发（只对新建路径调用本函数）。
"""

import logging

from . import wallet
from .billing_settings import ensure_settings_row

logger = logging.getLogger(__name__)


def initialize_user_wallet(db, *, user_type: str, user_id: int) -> dict:
    settings_row = ensure_settings_row(db)
    wallet_row = wallet.get_or_create_wallet(db, user_type, user_id)
    amount = int(settings_row.welcome_credit_micro or 0)
    ledger = wallet.ensure_welcome_credit(
        db, user_type=user_type, user_id=user_id, amount_micro=amount
    )
    return {
        "wallet_created": wallet_row is not None,
        "welcome_micro": amount if ledger is not None else 0,
        "welcome_ledger_id": getattr(ledger, "id", None),
    }