"""积分的三种口径（设计 §18 第 3 条）。

- ``micro``：**存储与计算**唯一单位（1 积分 = 1,000,000 微积分）；
- ``credits``：用户看的整数/小数积分；
- ``yuan``：法币锚，由 ``billing_settings.credits_per_yuan`` 换算。

同一数值不在这里之外重复定义换算系数；所有对外字段都经本文件生成。
"""

from __future__ import annotations

from typing import Optional

# 微积分换算系数只在 models.billing_models 定义一次，这里引用，别再复制。
from ...models.billing_models import MICRO_PER_CREDIT

DEFAULT_CREDITS_PER_YUAN = 100


def read_rate(db) -> int:
    """读计费设置的积分/元汇率；只读，不创建设置行。

    多个端点需要同一口径，集中在这里定义一次，避免各处各写一份读取逻辑
    （默认常量 ``DEFAULT_CREDITS_PER_YUAN`` 仍只在本文件定义）。
    """
    from ...models.billing_models import BillingSettings  # 延迟导入，避免扩大模块依赖面

    row = db.query(BillingSettings).filter(BillingSettings.id == 1).first()
    return int(
        (row.credits_per_yuan if row is not None else None) or DEFAULT_CREDITS_PER_YUAN
    )


def credits(micro: Optional[int]) -> float:
    return round(int(micro or 0) / MICRO_PER_CREDIT, 6)


def yuan(micro: Optional[int], credits_per_yuan: Optional[int]) -> float:
    rate = int(credits_per_yuan or DEFAULT_CREDITS_PER_YUAN) or DEFAULT_CREDITS_PER_YUAN
    return round(int(micro or 0) / MICRO_PER_CREDIT / rate, 6)


def fields(prefix: str, micro: Optional[int], credits_per_yuan: Optional[int]) -> dict:
    """生成 ``<prefix>_micro/<prefix>_credits/<prefix>_yuan`` 三件套。"""
    micro_v = int(micro or 0)
    return {
        f"{prefix}_micro": micro_v,
        f"{prefix}_credits": credits(micro_v),
        f"{prefix}_yuan": yuan(micro_v, credits_per_yuan),
    }