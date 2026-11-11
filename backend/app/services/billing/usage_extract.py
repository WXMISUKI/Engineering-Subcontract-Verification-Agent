"""provider usage 提取与聚合（2026-10-26 返工 P0-1）。

三个自研 OpenAI 兼容客户端统一用 :func:`usage_to_dict` 把 SDK 对象转完整 dict，
**保留缓存字段**，不再只挑 prompt/completion/total 三键。上层聚合用
``empty_usage_sum/add_usage/usage_sum_to_raw``，保证缓存值从 provider 一路带到
``token_service.record(raw_usage=...)`` 与 normalizer 的四桶拆分。

三态不变：None=未知、0=已知零。聚合时缓存桶只有**所有调用都上报过**才求和；
只要有一次没上报，就整体按未知处理（raw 里留 ``_cache_status=partial`` 供审计），
绝不把未知当 0 累计。
"""

from typing import Any, Optional

TOKEN_FIELDS = ("prompt_tokens", "completion_tokens", "total_tokens")

CACHE_FIELD_NAMES = (
    "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
    "prompt_cache_write_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "prompt_tokens_details",
)


def _as_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_int(*values: Any) -> Optional[int]:
    for value in values:
        parsed = _as_int(value)
        if parsed is not None:
            return parsed
    return None


def _prune_none(value: Any) -> Any:
    """递归去掉 None，但保留显式 0。

    为什么必须去 None：OpenAI SDK 的 ``model_dump()`` 会把未上报的缓存字段
    序列化成 ``None``；若原样保留，normalizer 会误判「上报过缓存」并把未知
    写成命中 0。
    """
    if isinstance(value, dict):
        pruned = {}
        for key, item in value.items():
            item = _prune_none(item)
            if item is None:
                continue
            if isinstance(item, dict) and not item:
                continue
            pruned[key] = item
        return pruned
    if isinstance(value, list):
        return [_prune_none(item) for item in value]
    return value


def usage_to_dict(usage: Any) -> dict:
    """SDK usage 对象 → 完整 dict（保留缓存明细）。"""
    if usage is None:
        return {}
    if isinstance(usage, dict):
        data = dict(usage)
    elif hasattr(usage, "model_dump"):
        try:
            data = usage.model_dump()
        except Exception:  # noqa: BLE001
            data = {}
    elif hasattr(usage, "dict"):
        try:
            data = usage.dict()
        except Exception:  # noqa: BLE001
            data = {}
    else:
        data = {
            key: value
            for key, value in vars(usage).items()
            if not key.startswith("_")
        }
    return _prune_none(data)


def cache_values(usage: dict | None) -> tuple[Optional[int], Optional[int], Optional[int]]:
    """从任意 provider usage 取 (命中, 未命中, 缓存写入)。缺失即 None。"""
    usage = usage or {}
    details = usage.get("prompt_tokens_details")
    details_cached = details.get("cached_tokens") if isinstance(details, dict) else None
    hit = _first_int(
        usage.get("prompt_cache_hit_tokens"),
        usage.get("cache_read_input_tokens"),
        details_cached,
    )
    miss = _first_int(usage.get("prompt_cache_miss_tokens"))
    if miss is None and hit is not None:
        prompt = _first_int(
            usage.get("prompt_tokens"), usage.get("input_tokens"), usage.get("input_count")
        )
        if prompt is not None:
            miss = max(prompt - hit, 0)
    write = _first_int(
        usage.get("prompt_cache_write_tokens"),
        usage.get("cache_creation_input_tokens"),
    )
    return hit, miss, write


def usage_reports_cache(usage: dict | None) -> bool:
    usage = usage or {}
    return any(key in usage for key in CACHE_FIELD_NAMES)


def empty_usage_sum() -> dict:
    """可累加聚合器：内存字段全部 None 起（未知），calls/cache_calls 计条数。"""
    acc = {key: None for key in TOKEN_FIELDS}
    acc.update({"cache_hit": None, "cache_miss": None, "cache_write": None,
                "calls": 0, "cache_calls": 0})
    return acc


def add_usage(acc: dict, usage: dict | None) -> dict:
    """累加一次 usage。缺失分项保持未知，不按 0 相加。"""
    usage = usage or {}
    acc["calls"] = int(acc.get("calls") or 0) + 1
    for key in TOKEN_FIELDS:
        value = _as_int(usage.get(key))
        if value is None:
            continue
        acc[key] = int(acc[key] or 0) + value
    hit, miss, write = cache_values(usage)
    if any(value is not None for value in (hit, miss, write)):
        acc["cache_calls"] = int(acc.get("cache_calls") or 0) + 1
    for name, value in (("cache_hit", hit), ("cache_miss", miss), ("cache_write", write)):
        if value is None:
            continue
        acc[name] = int(acc[name] or 0) + value
    return acc


def usage_sum_to_raw(acc: dict | None) -> dict:
    """聚合结果 → normalizer 可消费的 raw_usage。

    - token 三键：已知才写；
    - 缓存：只有**所有调用都上报过缓存**才写缓存键；部分上报留
      ``_cache_status=partial`` 供台账审计，但不写半份缓存值（避免把未知当 0）。
    """
    acc = acc or {}
    raw: dict = {}
    for key in TOKEN_FIELDS:
        if acc.get(key) is not None:
            raw[key] = int(acc[key])
    calls = int(acc.get("calls") or 0)
    cache_calls = int(acc.get("cache_calls") or 0)
    if calls:
        # 阶梯计价选档依据：聚合行用它求平均每调输入，避免多次调用相加后推高档位。
        raw["calls"] = calls
    if calls and cache_calls == calls:
        if acc.get("cache_hit") is not None:
            raw["prompt_cache_hit_tokens"] = int(acc["cache_hit"])
        if acc.get("cache_miss") is not None:
            raw["prompt_cache_miss_tokens"] = int(acc["cache_miss"])
        if acc.get("cache_write") is not None:
            raw["cache_creation_input_tokens"] = int(acc["cache_write"])
    elif cache_calls:
        raw["_cache_status"] = "partial"
    elif calls:
        raw["_cache_status"] = "unreported"
    return raw