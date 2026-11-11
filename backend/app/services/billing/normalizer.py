"""上游 usage → 统一计量口径（设计 §5.1 / §5.2）。

三态铁律：``None = 未知``、``0 = 已知零``。本模块**不补零**：
- 上游没给输入 → 输入未知，不能当 0；
- 缓存从未上报 → hit 未知（保留 None），计费按未命中处理并标
  ``cache_reported=False``，但**不把未知写成 hit=0**。

支持口径：
- OpenAI:      prompt_tokens / completion_tokens / prompt_tokens_details.cached_tokens
- DeepSeek:    prompt_cache_hit_tokens / prompt_cache_miss_tokens
- Anthropic:   input_tokens / output_tokens / cache_read_input_tokens / cache_creation_input_tokens
- DashScope:   同 OpenAI（cached_tokens）
- Coze:        input_count / output_count / token_count（无缓存）
"""

from dataclasses import dataclass, field
from typing import Any, Optional


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


@dataclass
class NormalizedUsage:
    usage_status: str  # complete / partial / unknown
    cache_reported: bool
    prompt_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cache_hit: Optional[int] = None
    cache_miss: Optional[int] = None       # provider 明确回报的未命中
    cache_write: Optional[int] = None
    raw: dict = field(default_factory=dict)

    @property
    def token_count(self) -> Optional[int]:
        """total 的别名，便于与 TokenUsage.token_count 对齐。"""
        return self.total_tokens

    @property
    def effective_cache_miss(self) -> Optional[int]:
        """计费用的未命中输入。

        - provider 明确回报 miss → 用它；
        - 只回报 prompt 与 hit → prompt - hit；
        - 缓存未上报 → 全部输入按未命中（设计 §5.1），hit 仍保留 None 以示未知。
        """
        if self.cache_miss is not None:
            return self.cache_miss
        if self.prompt_tokens is None:
            return None
        if self.cache_hit is not None:
            miss = self.prompt_tokens - self.cache_hit
            return miss if miss >= 0 else 0
        return self.prompt_tokens

    def billing_buckets(self, treat_unreported_as_miss: bool = True) -> dict:
        """返回可直接计价的分桶。

        未知桶返回 None；未上报缓存的输入在 ``treat_unreported_as_miss`` 时
        记入未命中桶（与设计定稿一致）。
        """
        if not self.cache_reported:
            if treat_unreported_as_miss:
                # 计费口径：未上报缓存 → 全部输入按未命中、命中/写入按已知零。
                # 注意台账仍保留 hit=None（未知），这里只是**计价**视角。
                return {"hit": 0, "miss": self.prompt_tokens, "write": 0,
                        "output": self.output_tokens}
            return {"hit": None, "miss": None, "write": None, "output": self.output_tokens}
        miss = self.cache_miss
        hit = self.cache_hit
        if miss is None and self.prompt_tokens is not None and hit is not None:
            miss = max(self.prompt_tokens - hit, 0)
        return {
            "hit": hit,
            "miss": miss,
            "write": self.cache_write if self.cache_reported else None,
            "output": self.output_tokens,
        }


def normalize(raw_usage: dict | None) -> NormalizedUsage:
    """把任意上游 usage 归一为四桶。无法识别的结构按 unknown 处理。"""
    if not isinstance(raw_usage, dict) or not raw_usage:
        return NormalizedUsage(usage_status="unknown", cache_reported=False)

    # 兼容 Coze 的 input_count/output_count/token_count 命名。
    prompt = _first_int(
        raw_usage.get("prompt_tokens"),
        raw_usage.get("input_tokens"),
        raw_usage.get("input_count"),
    )
    output = _first_int(
        raw_usage.get("completion_tokens"),
        raw_usage.get("output_tokens"),
        raw_usage.get("output_count"),
    )
    total = _first_int(raw_usage.get("total_tokens"), raw_usage.get("token_count"))
    if total is None and prompt is not None and output is not None:
        total = prompt + output

    # 缓存命中：OpenAI/DashScope 走 prompt_tokens_details.cached_tokens；
    # DeepSeek 走 prompt_cache_hit_tokens；Anthropic 走 cache_read_input_tokens。
    details = raw_usage.get("prompt_tokens_details")
    details_cached = _as_int(details.get("cached_tokens")) if isinstance(details, dict) else None
    hit = _first_int(
        details_cached,
        raw_usage.get("prompt_cache_hit_tokens"),
        raw_usage.get("cache_read_input_tokens"),
        raw_usage.get("cache_hit_input_tokens"),
    )
    miss = _first_int(
        raw_usage.get("prompt_cache_miss_tokens"),
        raw_usage.get("cache_miss_input_tokens"),
    )
    write = _first_int(
        raw_usage.get("prompt_cache_write_tokens"),
        raw_usage.get("cache_creation_input_tokens"),
        raw_usage.get("cache_write_input_tokens"),
    )

    # 「是否上报过缓存」看是否出现任一缓存字段；出现 0 也算已上报（已知零）。
    cache_keys = (
        "prompt_tokens_details", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens",
        "prompt_cache_write_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
        "cache_hit_input_tokens", "cache_miss_input_tokens", "cache_write_input_tokens",
    )
    cache_reported = any(key in raw_usage for key in cache_keys)

    # 已上报缓存时，缺省字段是**已知零**而不是未知：
    # - OpenAI/DashScope 不报 write（不计缓存写入费）→ write=0；
    # - 只报了其中一桶时，另一桶按 0 处理（Anthropic 的 read/write 字段缺省即 0）。
    # 未上报缓存（如 Coze）保持 None，绝不把未知写成 0。
    if cache_reported:
        if write is None:
            write = 0
        if hit is None and miss is None:
            hit = 0
        elif hit is None and miss is not None and prompt is not None:
            hit = max(prompt - miss, 0)

    # usage_status：
    # - prompt 与 output 都未知 → unknown（即便有 total，也不猜比例）；
    # - 任一已知、任一未知 → partial；
    # - 两者都已知 → complete。
    if prompt is None and output is None:
        usage_status = "unknown"
    elif prompt is None or output is None:
        usage_status = "partial"
    else:
        usage_status = "complete"

    return NormalizedUsage(
        usage_status=usage_status,
        cache_reported=cache_reported,
        prompt_tokens=prompt,
        output_tokens=output,
        total_tokens=total,
        cache_hit=hit,
        cache_miss=miss,
        cache_write=write,
        raw=raw_usage,
    )