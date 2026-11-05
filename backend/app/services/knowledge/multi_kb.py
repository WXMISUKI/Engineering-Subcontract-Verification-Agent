"""多知识库结果聚合的纯逻辑。

该模块不读取配置、不访问数据库，也不直接调用 WeKnora；调用方负责先按绑定
逐库检索，再把每个 KB 已按相关性排序的结果交给本模块。跨 KB 不比较原始
score，只使用 KB 内排名和绑定权重做稳定的 RRF 聚合。

本文件实现 18 号文档 §2 的三条冻结决策：
- **D1** 去重键：只有 `knowledge_id` 与 chunk 标识**同时**存在时才跨 KB 去重；
- **D2** 状态判定：无可执行目标 ⇒ `undetermined`（`miss` 的前提是所有目标都成功且为空）；
- **D3** 权重必须是有限非负数且 ≤ 100，`NaN`/`±Inf`/超限一律拒绝，**不夹取**。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

# D3 的上限。与 binding_service.MAX_RETRIEVAL_WEIGHT 同值；这里必须独立定义，
# 因为本模块是纯函数层，不依赖数据库层（否则纯逻辑会被迫导入 ORM）。
MAX_AGGREGATION_WEIGHT = 100.0

# D2 的根因区分：两种情况都是 undetermined，但处置完全不同。
UNDETERMINED_NO_TARGET = "no_target"
UNDETERMINED_ALL_FAILED = "all_failed"


@dataclass(frozen=True)
class AggregatedKnowledgeResult:
    """多 KB 聚合结果及其三态/部分失败状态。"""

    status: str
    items: list[dict[str, Any]]
    failed_kbs: list[str]
    succeeded_kbs: list[str] = field(default_factory=list)
    undetermined_reason: Optional[str] = None
    rrf_k: int = 60


def coerce_weight(value: Any) -> float:
    """D3：把权重收敛成合法值，不合法即抛 `ValueError`。

    显式排除 `bool`（`True` 是 `int` 子类，不排掉会被当成 1.0）；显式排除
    `NaN`——`NaN < 0` 恒为假，靠比较式校验会把它静默放行，进而在 `weight/(k+rank)`
    里把整库聚合分污染成 `NaN`，排序退化成「全部并列」且零报错。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"binding weight 必须是数值，实得 {type(value).__name__}"
        )
    weight = float(value)
    if math.isnan(weight) or math.isinf(weight):
        raise ValueError("binding weight 必须是有限数值（不接受 NaN / Inf）")
    if weight < 0 or weight > MAX_AGGREGATION_WEIGHT:
        raise ValueError(
            f"binding weight 必须落在 [0, {MAX_AGGREGATION_WEIGHT:g}]，实得 {weight:g}"
        )
    return weight


def _dedupe_key(item: Mapping[str, Any], kb_id: str) -> tuple[str, str, str]:
    """Return a stable identity without collapsing opaque IDs across KBs.

    WeKnora responses do not always include a globally unique document identity.
    D1 要求「全局身份」必须**同时**具备跨库稳定的文档号与分片号：
    只有 `knowledge_id` 而分片号缺失时，同一文档下多个无 id 分片会被误判成同一条，
    所以此时必须退回 KB 内局部键。
    """
    knowledge_id = str(item.get("knowledge_id") or "").strip()
    chunk_id = str(item.get("id") or item.get("chunk_id") or "").strip()
    if knowledge_id and chunk_id:
        provider_id = item.get("provider_id")
        return f"provider:{provider_id}" if provider_id is not None else "global", knowledge_id, chunk_id
    fallback = chunk_id or str(item.get("document") or "").strip()
    return "kb", str(kb_id), fallback


def aggregate_ranked_results(
    ranked_by_kb: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    binding_weights: Mapping[str, float] | None = None,
    failed_kbs: Sequence[str] | None = None,
    limit: int = 10,
    rrf_k: int = 60,
) -> AggregatedKnowledgeResult:
    """按 KB 内排名做加权 RRF，并保持可审计的原始结果信息。

    `ranked_by_kb` 的每个序列必须已经由对应 KB 排好序（第一个元素为 rank 1）。
    WeKnora 的原始 score 仅原样保留在 `raw_scores`，绝不参与跨 KB 排序。
    """
    if limit < 0:
        raise ValueError("limit must be non-negative")
    if rrf_k < 0:
        raise ValueError("rrf_k must be non-negative")

    weights = binding_weights or {}
    failed = list(dict.fromkeys(str(kb) for kb in (failed_kbs or [])))
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}

    for kb_id, rows in ranked_by_kb.items():
        kb_key = str(kb_id)
        # D3：默认权重 1.0 同样走校验，保证「显式传入非法值」与「配置默认值被污染」
        # 两条路径都拦得住。
        weight = coerce_weight(weights.get(kb_key, 1.0))
        for rank, source_row in enumerate(rows, start=1):
            key = _dedupe_key(source_row, kb_key)
            contribution = weight / (rrf_k + rank)
            current = merged.get(key)
            if current is None:
                current = dict(source_row)
                current["kb_id"] = source_row.get("kb_id", kb_key)
                current["aggregation_score"] = 0.0
                current["aggregation_count"] = 0
                current["raw_scores"] = []
                current["source_kbs"] = []
                current["source_targets"] = []
                merged[key] = current
            current["aggregation_score"] += contribution
            current["aggregation_count"] += 1
            if "score" in source_row:
                current["raw_scores"].append(source_row["score"])
            raw_kb = source_row.get("kb_id", kb_key)
            if raw_kb not in current["source_kbs"]:
                current["source_kbs"].append(raw_kb)
            if kb_key not in current["source_targets"]:
                current["source_targets"].append(kb_key)

    items = sorted(
        merged.values(),
        key=lambda row: (-row["aggregation_score"], str(row.get("id") or "")),
    )[:limit]

    successful_kbs = [str(kb) for kb in ranked_by_kb]
    reason: Optional[str] = None
    if not successful_kbs:
        # D2：没有任何目标 ⇒ undetermined，**永不为 miss**。
        reason = UNDETERMINED_ALL_FAILED if failed else UNDETERMINED_NO_TARGET
        status = "undetermined"
    elif failed:
        # partial 与「有没有结果」无关：只要有成功、有失败就是 partial。
        status = "partial"
    else:
        status = "hit" if items else "miss"

    return AggregatedKnowledgeResult(
        status=status,
        items=items,
        failed_kbs=failed,
        succeeded_kbs=successful_kbs,
        undetermined_reason=reason,
        rrf_k=rrf_k,
    )


__all__ = [
    "MAX_AGGREGATION_WEIGHT",
    "UNDETERMINED_ALL_FAILED",
    "UNDETERMINED_NO_TARGET",
    "AggregatedKnowledgeResult",
    "aggregate_ranked_results",
    "coerce_weight",
]
