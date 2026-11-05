"""补件影响范围规划（P8-03A）。

本模块只做纯规划，不写数据库、不启动核查。材料类别映射来自
``data/supplement_impact_map.json``；具体 item_id 必须由当前缺件清单
传入，避免前端按文案猜测影响范围。
"""
from __future__ import annotations

import json
from pathlib import Path


_MAP_PATH = Path(__file__).with_name("data") / "supplement_impact_map.json"


def _load_map() -> dict:
    return json.loads(_MAP_PATH.read_text(encoding="utf-8"))


def _clean_ids(values) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        return []
    return sorted({str(value).strip() for value in values if str(value).strip()})


def _ordered_ids(values) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        return []
    seen = set()
    result = []
    for value in values:
        item = str(value).strip()
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def build_supplement_impact_plan(*, category: str, item_ids=None,
                                 material_ids=None, subject_ids=None,
                                 expected_groups=None) -> dict:
    """构造补件局部重跑计划；未知或不完整输入必须返回可见回退原因。"""
    mapping = _load_map().get("categories", {})
    raw_category = str(category or "").strip()
    # 前端材料角色与补件影响类别是两层契约：todo 聚合可能返回 packet/sub/labor，
    # 影响映射使用更明确的 submission_packet/sub_contract/labor_contract。集中做别名
    # 归一化，避免一个普通报审附件因为角色名不同被误判为未知类别而全量回退。
    category_key = {
        "packet": "submission_packet",
        "general": "general_contract",
        "sub": "sub_contract",
        "labor": "labor_contract",
    }.get(raw_category, raw_category)
    item_ids = _clean_ids(item_ids)
    material_ids = _clean_ids(material_ids)
    subject_ids = _clean_ids(subject_ids)
    expected = _ordered_ids(expected_groups) or [
        "completeness", "signature_chain", "a1", "a2", "internal_records"
    ]

    entry = mapping.get(category_key)
    if entry is None:
        return {
            "mode": "full_fallback",
            "category": category_key or None,
            "item_ids": item_ids,
            "material_ids": material_ids,
            "subject_ids": subject_ids,
            "affected_groups": expected,
            "carried_forward_groups": [],
            "fallback_reason": "category_not_mapped",
        }

    affected = [group for group in entry.get("groups", []) if group in expected]
    if not affected:
        return {
            "mode": "full_fallback",
            "category": category_key,
            "item_ids": item_ids,
            "material_ids": material_ids,
            "subject_ids": subject_ids,
            "affected_groups": expected,
            "carried_forward_groups": [],
            "fallback_reason": "no_enabled_group",
        }

    fallback_reason = None
    if entry.get("requires_subject") and not subject_ids:
        fallback_reason = "subject_missing"
    elif entry.get("requires_item_ids") and not item_ids:
        fallback_reason = "item_ids_missing"

    mode = "partial" if fallback_reason is None else "group_fallback"
    carried = [group for group in expected if group not in affected]
    return {
        "mode": mode,
        "category": category_key,
        "item_ids": item_ids,
        "material_ids": material_ids,
        "subject_ids": subject_ids,
        "affected_groups": affected,
        "carried_forward_groups": carried,
        "fallback_reason": fallback_reason,
    }


__all__ = ["build_supplement_impact_plan"]
