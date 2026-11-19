"""阅读门禁（设计 2026-11-18 §2.5）：未读/可疑页不得支撑「符合」结论。

规则执行侧已尽量逐页剔除未读页（a1/a2/a3/checklist），本模块是落库前最后一道
fail-closed 护栏：**只降级、不抛错**。

- 证据页落在未读 / 解析失败 / 低质可疑页 → 该「符合」结论降级为「缺项」；
- 结论只锚定整份材料、且该材料整体读取不完整 → 同样降级；
- 「不适用」不进入本门禁：它表达「本次不构成义务」，不是对页面内容的肯定。

降级而非阻断：单页未读不应让整组核查作废；但保留「符合」会把未读页渲染成已
核实，属于纸面失真，必须阻断。降级后结论仍进入报告「缺项」清单，处置是补读
或补件后重跑。
"""
from __future__ import annotations

from . import parsing
from .models import STATUS_LABELS, STATUS_MISS, STATUS_NOT_INCLUDED


def _evidence_refs(finding) -> list[tuple[int | None, int | None]]:
    """结论引用的 (material_id, page)；page 为 None 表示结论锚定整份材料。"""
    refs: list[tuple[int | None, int | None]] = []
    material_id = getattr(finding, "material_id", None)
    if material_id is not None:
        refs.append((material_id, getattr(finding, "page", None)))
    for item in getattr(finding, "evidence_json", None) or []:
        if isinstance(item, dict) and item.get("material_id") is not None:
            refs.append((item.get("material_id"), item.get("page")))
    return refs


def _downgrade_message(material, pages, old_status) -> str:
    name = getattr(material, "file_name", None) or "材料"
    if pages:
        reason = parsing.describe_reading_gap(material, pages, limit=5)
    else:
        reason = "整份材料读取不完整"
    return ("《%s》%s；原「%s」结论缺少可读页支撑，已按缺项处理，补读或补件后重新核查。"
            % (name, reason, STATUS_LABELS.get(old_status, old_status)))


def gate_reading_findings(findings, materials) -> list:
    """就地降级不可支撑的「符合」结论，返回被降级的结论列表（供日志/测试）。

    只处理 `status == miss`（符合）。判定顺序：
    1. 任一具页证据不可读 → 降级；
    2. 没有任何可读页证据，且锚定材料整体读取不完整 → 降级；
    3. 引用的材料不在本任务材料集合内 → 跳过（不做无据推断）。
    """
    material_by_id = {material.id: material for material in materials or []}
    downgraded = []
    for finding in findings:
        if getattr(finding, "status", None) != STATUS_MISS:
            continue
        page_refs = [(material_id, page)
                     for material_id, page in _evidence_refs(finding)
                     if page is not None and material_id in material_by_id]
        blocked = {}
        for material_id, page in page_refs:
            material = material_by_id[material_id]
            if not parsing.material_page_readable(material, page):
                blocked.setdefault(material_id, set()).add(page)
        if not blocked and not page_refs:
            # 无页级证据：只在结论锚定的材料整体读取不完整时降级。
            for material_id, page in _evidence_refs(finding):
                if page is not None or material_id not in material_by_id:
                    continue
                material = material_by_id[material_id]
                if not parsing.reading_coverage(material).get("reading_complete"):
                    blocked.setdefault(material_id, set())
        if not blocked:
            continue
        messages = [_downgrade_message(material_by_id[material_id], sorted(pages), finding.status)
                    for material_id, pages in blocked.items()]
        finding.status = STATUS_NOT_INCLUDED
        finding.message = "；".join(messages) + " " + (finding.message or "")
        downgraded.append(finding)
    return downgraded
