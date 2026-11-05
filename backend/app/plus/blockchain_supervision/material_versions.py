"""D9 材料替代链的只读版本事实投影。

本模块只负责从已经读取的材料行派生展示事实，不连接数据库、不写入状态，也不把
``version_role`` 当成人工处置状态。这样页面、历史 API、报告和导出可以共享同一
口径；真正的替代生效仍由 service 的原子 CAS 事务负责。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Iterable


VERSION_ROLES = ("current", "superseded", "legacy_unknown")
_CURRENT_PARSE_STATES = frozenset({"done"})


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="seconds") if hasattr(value, "isoformat") else str(value)


def version_role(material, *, predecessor_ids: set[int] | None = None) -> str:
    """派生单行版本角色。

    只有明确存在替代边、或被同任务材料指向且解析成功的节点才可标为当前版；
    独立的存量 NULL 修订号行保持 ``legacy_unknown``，不做猜测回填。
    """
    if getattr(material, "superseded_by_id", None) is not None:
        return "superseded"
    if getattr(material, "replacement_target_id", None) is not None:
        return "legacy_unknown"
    if getattr(material, "parse_state", None) not in _CURRENT_PARSE_STATES:
        return "legacy_unknown"
    if getattr(material, "revision_no", None) is not None:
        return "current"
    if getattr(material, "id", None) in (predecessor_ids or set()):
        return "current"
    return "legacy_unknown"


def _projected_version_role(material, *, by_id: dict[int, object],
                            predecessor_ids: set[int], cache: dict[int, str],
                            visiting: set[int] | None = None) -> str:
    """Project a derived ZIP child with its parsed parent's version role."""
    material_id = getattr(material, "id", None)
    if material_id in cache:
        return cache[material_id]
    direct = version_role(material, predecessor_ids=predecessor_ids)
    if direct != "legacy_unknown":
        cache[material_id] = direct
        return direct
    if getattr(material, "parse_state", None) not in _CURRENT_PARSE_STATES:
        cache[material_id] = direct
        return direct
    parent_id = getattr(material, "container_material_id", None)
    parent = by_id.get(parent_id)
    if parent is None:
        cache[material_id] = direct
        return direct
    visiting = set(visiting or ())
    if material_id in visiting:
        cache[material_id] = direct
        return direct
    visiting.add(material_id)
    inherited = _projected_version_role(
        parent, by_id=by_id, predecessor_ids=predecessor_ids,
        cache=cache, visiting=visiting
    )
    cache[material_id] = inherited if inherited != "legacy_unknown" else direct
    return cache[material_id]


def version_projection(material, *, predecessor_ids: set[int] | None = None) -> dict:
    """返回可嵌入 DTO/报告的最小版本事实集合。"""
    material_id = getattr(material, "id", None)
    return {
        "material_id": material_id,
        "id": material_id,
        "revision_no": getattr(material, "revision_no", None),
        "replacement_target_id": getattr(material, "replacement_target_id", None),
        "version_role": version_role(material, predecessor_ids=predecessor_ids),
        "superseded_by_id": getattr(material, "superseded_by_id", None),
        "superseded_at": _iso(getattr(material, "superseded_at", None)),
    }


def _valid_edges(materials: list) -> tuple[dict[int, int], dict[int, list[int]], set[int], list[dict]]:
    """构建同任务有效边，并收集不改变数据的结构告警。"""
    by_id = {getattr(material, "id", None): material for material in materials}
    successor_by_id: dict[int, int] = {}
    predecessors_by_id: dict[int, list[int]] = defaultdict(list)
    predecessor_ids: set[int] = set()
    warnings: list[dict] = []
    for material in materials:
        source_id = getattr(material, "id", None)
        target_id = getattr(material, "superseded_by_id", None)
        if target_id is None:
            continue
        target = by_id.get(target_id)
        if target is None:
            warnings.append({"code": "missing_target", "material_id": source_id,
                             "target_id": target_id})
            continue
        if getattr(target, "task_id", None) != getattr(material, "task_id", None):
            warnings.append({"code": "cross_task_edge", "material_id": source_id,
                             "target_id": target_id})
            continue
        successor_by_id[source_id] = target_id
        predecessors_by_id[target_id].append(source_id)
        predecessor_ids.add(target_id)
    for values in predecessors_by_id.values():
        values.sort()
    return successor_by_id, dict(predecessors_by_id), predecessor_ids, warnings


def project_materials(materials: Iterable) -> tuple[dict[int, dict], list[dict]]:
    """批量投影同一任务材料，并返回仅供审计展示的结构告警。"""
    rows = list(materials)
    _, _, predecessor_ids, warnings = _valid_edges(rows)
    by_id = {getattr(material, "id", None): material for material in rows}
    role_cache: dict[int, str] = {}
    return (
        {
            getattr(material, "id", None): {
                **version_projection(material, predecessor_ids=predecessor_ids),
                "version_role": _projected_version_role(
                    material, by_id=by_id, predecessor_ids=predecessor_ids,
                    cache=role_cache
                ),
            }
            for material in rows
        },
        warnings,
    )


def subject_assessment_issue(material) -> str | None:
    """解析成功不代表主体核验通过；返回不能参与核查的原因。"""
    for item in (getattr(material, "degradations_json", None) or []):
        if isinstance(item, dict) and item.get("kind") in {"subject_conflict", "subject_unresolved"}:
            return item["kind"]
    return None


def active_input_materials(materials: Iterable) -> list:
    """返回当前规则输入候选。

    这里使用持久化替代事实，而不是把 `legacy_unknown` 猜成 `current`：存量未建链
    材料继续沿用既有兼容输入口径；明确已替代或仍待生效的候选不会进入新一轮规则。
    ZIP 子材料继承父容器的替代边：旧容器的子材料不能绕过容器链继续作为新输入，
    待生效新容器的子材料也要等容器 CAS 成功后再进入输入。
    """
    rows = list(materials)
    container_ids = {
        getattr(material, "container_material_id", None)
        for material in rows
        if getattr(material, "container_material_id", None) is not None
    }
    container_ids.update(
        getattr(material, "id", None)
        for material in rows
        if str(getattr(material, "file_name", "") or "").lower().endswith(".zip")
    )
    by_id = {getattr(material, "id", None): material for material in rows}
    inactive_container_ids = {
        material_id for material_id in container_ids
        if (material := by_id.get(material_id)) is not None
        and (getattr(material, "superseded_by_id", None) is not None
             or getattr(material, "replacement_target_id", None) is not None)
    }
    return [
        material for material in rows
        if getattr(material, "id", None) not in container_ids
        and not subject_assessment_issue(material)
        and getattr(material, "container_material_id", None) not in inactive_container_ids
        if getattr(material, "superseded_by_id", None) is None
        and getattr(material, "replacement_target_id", None) is None
    ]


def project_material_rows(materials: Iterable) -> tuple[list[dict], list[dict]]:
    """输出报告/证据包共用的材料版本清单及结构告警。"""
    rows = list(materials)
    facts, warnings = project_materials(rows)
    projected = []
    for material in rows:
        fact = facts[getattr(material, "id", None)]
        projected.append({
            **fact,
            "file_name": getattr(material, "file_name", None),
            "role": getattr(material, "role", None),
            "material_category": getattr(material, "material_category", None),
            "subject_id": getattr(material, "subject_id", None),
            "packet_id": getattr(material, "packet_id", None),
            "parse_state": getattr(material, "parse_state", None),
        })
    return projected, warnings


def _zip_child_chain(rows: list, target, *, parent_history: dict) -> tuple[list[int], list[dict]] | None:
    """Map a ZIP child to the matching child across its parent's replacement chain."""
    entry_path = getattr(target, "container_entry_path", None)
    parent_chain = parent_history.get("chain") or []
    if not entry_path or len(parent_chain) <= 1:
        return None
    children_by_parent: dict[int, list] = defaultdict(list)
    for material in rows:
        parent_id = getattr(material, "container_material_id", None)
        if parent_id is not None and getattr(material, "container_entry_path", None) == entry_path:
            children_by_parent[parent_id].append(material)

    chain_ids: list[int] = []
    warnings: list[dict] = []
    for parent in parent_chain:
        parent_id = parent.get("material_id")
        children = sorted(children_by_parent.get(parent_id, []),
                          key=lambda material: getattr(material, "id", 0))
        if not children:
            warnings.append({"code": "missing_container_child",
                             "container_material_id": parent_id,
                             "container_entry_path": entry_path})
            continue
        if len(children) > 1:
            warnings.append({"code": "multiple_container_children",
                             "container_material_id": parent_id,
                             "container_entry_path": entry_path,
                             "material_ids": [getattr(child, "id", None) for child in children]})
        chain_ids.append(getattr(children[0], "id", None))
    return chain_ids, warnings


def build_material_history(materials: Iterable, material_id: int) -> dict:
    """返回单个材料所在替代链，顺序固定为旧版到当前候选版。

    历史链异常只读暴露为 ``warnings``，不会修复或改写存量边；循环/多前驱时
    采用稳定的最小 ID 路径并标记 ``invalid``，保证 API 不会无限遍历。
    """
    rows = list(materials)
    by_id = {getattr(material, "id", None): material for material in rows}
    target = by_id.get(material_id)
    if target is None:
        raise LookupError("材料不存在")

    successor_by_id, predecessors_by_id, predecessor_ids, edge_warnings = _valid_edges(rows)
    chain_ids = [material_id]
    seen = {material_id}
    warnings: list[dict] = []

    # 先向旧版回溯；多前驱不符合线性替代契约，取最小 ID 保持可重复。
    cursor = material_id
    while predecessors_by_id.get(cursor):
        parents = predecessors_by_id[cursor]
        if len(parents) > 1:
            warnings.append({"code": "multiple_predecessors", "material_id": cursor,
                             "predecessor_ids": list(parents)})
        parent = parents[0]
        if parent in seen:
            warnings.append({"code": "cycle", "material_id": parent, "target_id": cursor})
            break
        chain_ids.insert(0, parent)
        seen.add(parent)
        cursor = parent

    # 再向新版本前进；每个节点最多一个直接替代目标。
    cursor = material_id
    while cursor in successor_by_id:
        next_id = successor_by_id[cursor]
        if next_id in seen:
            warnings.append({"code": "cycle", "material_id": cursor, "target_id": next_id})
            break
        chain_ids.append(next_id)
        seen.add(next_id)
        cursor = next_id

    chain_set = set(chain_ids)
    target_parent_id = getattr(target, "container_material_id", None)
    if target_parent_id is not None and len(chain_ids) == 1:
        parent_history = build_material_history(rows, target_parent_id)
        child_chain = _zip_child_chain(rows, target, parent_history=parent_history)
        if child_chain is not None:
            chain_ids, child_warnings = child_chain
            chain_set = set(chain_ids)
            warnings.extend(child_warnings)
    warnings.extend(
        warning for warning in edge_warnings
        if warning.get("material_id") in chain_set
    )
    by_id = {getattr(material, "id", None): material for material in rows}
    role_cache: dict[int, str] = {}
    projected = {
        getattr(material, "id", None): {
            **version_projection(material, predecessor_ids=predecessor_ids),
            "version_role": _projected_version_role(
                material, by_id=by_id, predecessor_ids=predecessor_ids,
                cache=role_cache
            ),
        }
        for material in rows
        if getattr(material, "id", None) in chain_set
    }
    chain = [projected[item_id] for item_id in chain_ids if item_id in projected]
    chain_status = "invalid" if warnings else ("ok" if len(chain) > 1 else "unlinked")
    return {
        "task_id": getattr(target, "task_id", None),
        "material_id": material_id,
        "chain": chain,
        "chain_status": chain_status,
        "warnings": warnings,
    }
