"""用量 + 价格版本 → 微积分（设计 §5.1 / §5.3）。

- token：``sum(桶/1000 × 权重)``；非 token：``数量/unit_scale × 单价``；
- 每条账单向上取整到 1 微积分（``ceil``）；
- 价格版本缺权重 → 该桶按未知处理，只计已知桶，标 ``partial``；
- 全部未知 → 0 扣费，标 ``unknown``；找不到价格版本 → ``price_missing``。
"""

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from .normalizer import NormalizedUsage


@dataclass
class PricingResult:
    raw_micro: int
    charged_micro: int
    status: str  # rated / partial / unknown / price_missing
    priced_buckets: int
    unknown_buckets: int
    detail: dict


def _ceil(value: Decimal) -> int:
    return int(math.ceil(value))


def _micro(raw: Decimal, min_charge_micro: int) -> tuple[int, int]:
    if raw <= 0:
        return 0, 0
    charged = _ceil(raw)
    if min_charge_micro and charged < int(min_charge_micro):
        charged = int(min_charge_micro)
    return _ceil(raw), charged


def _base_weights(price_version) -> dict:
    return {
        "hit": price_version.cache_hit_input_micro_per_1k,
        "miss": price_version.cache_miss_input_micro_per_1k,
        "write": price_version.cache_write_input_micro_per_1k,
        "output": price_version.output_micro_per_1k,
    }


def _tier_input_tokens(usage: NormalizedUsage) -> Optional[int]:
    """本次计价口径的输入 token；未知返回 None（不猜档）。

    优先用 prompt_tokens；缺失时用命中+未命中桶。聚合台账若带
    ``raw.calls``，调用方按平均每调输入选档，避免把多次调用相加后推高到
    更高档位。
    """
    if usage.prompt_tokens is not None:
        return int(usage.prompt_tokens)
    buckets = usage.billing_buckets(treat_unreported_as_miss=True)
    hit, miss = buckets.get("hit"), buckets.get("miss")
    if hit is None and miss is None:
        return None
    return int(hit or 0) + int(miss or 0)


def select_weights(usage: NormalizedUsage, price_version) -> dict:
    """基础四桶 + 输入长度阶梯（2026-11-05，对齐百炼）。

    有 ``tiers_json`` 时按单次调用输入 token 选档；档位按
    ``up_to_input_tokens`` 升序，选第一个覆盖该输入量的档，超出最大档用最后一档。
    聚合台账用 ``raw.calls`` 求平均每调输入（calls 缺失按 1 次）。
    输入 token 未知时退回基础价并保持 partial/unknown，不猜档。
    """
    weights = _base_weights(price_version)
    tiers = getattr(price_version, "tiers_json", None)
    if not tiers:
        return weights
    input_tokens = _tier_input_tokens(usage)
    if input_tokens is None:
        return weights
    calls = 1
    raw = usage.raw if isinstance(usage.raw, dict) else {}
    try:
        calls = max(int(raw.get("calls") or 1), 1)
    except (TypeError, ValueError):
        calls = 1
    per_call = Decimal(input_tokens) / Decimal(calls)
    chosen = None
    try:
        ordered = sorted(
            (t for t in tiers if isinstance(t, dict) and t.get("up_to_input_tokens") is not None),
            key=lambda t: int(t["up_to_input_tokens"]),
        )
    except (TypeError, ValueError):
        return weights
    for tier in ordered:
        if per_call <= Decimal(int(tier["up_to_input_tokens"])):
            chosen = tier
            break
    if chosen is None and ordered:
        chosen = ordered[-1]
    if not chosen:
        return weights
    for key, field in (
        ("hit", "cache_hit_input_micro_per_1k"),
        ("miss", "cache_miss_input_micro_per_1k"),
        ("write", "cache_write_input_micro_per_1k"),
        ("output", "output_micro_per_1k"),
    ):
        value = chosen.get(field)
        if value is not None:
            weights[key] = value
    return weights


def price_token(
    usage: NormalizedUsage,
    price_version,
    *,
    treat_unreported_as_miss: bool = True,
    min_charge_micro: int = 0,
) -> PricingResult:
    """按四桶权重计费（有阶梯时先按单次输入 token 选档）。"""
    if price_version is None:
        return PricingResult(0, 0, "price_missing", 0, 0, {})

    buckets = usage.billing_buckets(treat_unreported_as_miss=treat_unreported_as_miss)
    weights = select_weights(usage, price_version)

    raw = Decimal(0)
    priced = 0
    unknown = 0
    counted = {}
    for name, count in buckets.items():
        weight = weights.get(name)
        if count is None or weight is None:
            unknown += 1
            continue
        counted[name] = int(count)
        # 权重单位是「微积分 / 千 token」
        raw += Decimal(int(count)) * Decimal(int(weight)) / Decimal(1000)
        priced += 1

    if usage.usage_status == "unknown":
        # 只拿到 total 且模型配置了兜底单价时才计费；否则如实 unknown。
        if (
            usage.total_tokens is not None
            and price_version.total_fallback_micro_per_1k is not None
        ):
            raw = (
                Decimal(int(usage.total_tokens))
                * Decimal(int(price_version.total_fallback_micro_per_1k))
                / Decimal(1000)
            )
            raw_micro, charged = _micro(raw, min_charge_micro)
            return PricingResult(
                raw_micro, charged, "rated", 1, 0,
                {"total_fallback": int(usage.total_tokens)},
            )
        return PricingResult(0, 0, "unknown", 0, 4, {})

    if priced == 0:
        return PricingResult(0, 0, "unknown", 0, unknown, {})

    status = "rated" if (usage.usage_status == "complete" and unknown == 0) else "partial"
    raw_micro, charged = _micro(raw, min_charge_micro)
    return PricingResult(raw_micro, charged, status, priced, unknown, counted)


def price_quantity(
    quantity: Optional[Decimal | int | float],
    price_version,
    unit_scale: int,
    *,
    min_charge_micro: int = 0,
) -> PricingResult:
    """非 token 计费：quantity / unit_scale × unit_micro_per_unit。"""
    if price_version is None:
        return PricingResult(0, 0, "price_missing", 0, 0, {})
    if quantity is None:
        return PricingResult(0, 0, "unknown", 0, 1, {})
    if price_version.unit_micro_per_unit is None:
        return PricingResult(0, 0, "unknown", 0, 1, {})
    scale = int(unit_scale or 1)
    raw = Decimal(str(quantity)) * Decimal(int(price_version.unit_micro_per_unit)) / Decimal(scale)
    raw_micro, charged = _micro(raw, min_charge_micro)
    return PricingResult(raw_micro, charged, "rated", 1, 0, {"quantity": str(quantity)})


def price_usage(
    *,
    unit_type: str,
    usage: Optional[NormalizedUsage],
    quantity,
    unit_scale: int,
    price_version,
    treat_unreported_as_miss: bool = True,
    min_charge_micro: int = 0,
) -> PricingResult:
    if unit_type == "token":
        return price_token(
            usage or NormalizedUsage(usage_status="unknown", cache_reported=False),
            price_version,
            treat_unreported_as_miss=treat_unreported_as_miss,
            min_charge_micro=min_charge_micro,
        )
    return price_quantity(
        quantity, price_version, unit_scale, min_charge_micro=min_charge_micro
    )