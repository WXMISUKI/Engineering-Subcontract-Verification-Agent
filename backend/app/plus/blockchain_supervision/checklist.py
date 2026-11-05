"""主体级报审完整性清单的单一计算入口。

本模块只负责四件事：条目适用性、逐主体执行、九项计数和流程提示。项目补充
要求不复制基础条目；电子缺件与纸质份数/办理时序严格分开。
"""
from __future__ import annotations

import re

from . import a3_check, subject_reviews
from .attachment_source import is_attachment_source
from .cards import resolve_rule_cards
from .models import (BsFinding, BsMaterial, BsPacket, BsSubject,
                     STATUS_HIT, STATUS_MISS, STATUS_UNDECIDABLE)
from .rules import ATTACHMENT_SEAL_ITEM, load_report_checklist
from .parsing import material_page_readable


SCHEMA_VERSION = "1.0"
COUNT_UNIT = "checklist_item_per_subject"
COUNT_NOTE = "按主体逻辑核查项计数，不代表电子文件或纸质份数"

BASE_SHARED = tuple("RC-A%02d" % number for number in range(1, 11))
BASE_BY_TYPE = {
    "professional": BASE_SHARED + tuple("RC-P%02d" % number for number in range(11, 17)),
    "labor": BASE_SHARED + tuple("RC-L%02d" % number for number in range(11, 15)),
}
FORM_CHECKS = ("RC-08", "RC-09")


def active_subject_ids(db, task) -> set[int]:
    """返回主体级清单实际消费的活动主体；服务、todo 与报告共用。"""
    explicit_ids = {subject_id for (subject_id,) in db.query(BsSubject.id).filter(
        BsSubject.task_id == task.id, BsSubject.origin == "explicit",
        BsSubject.retired.is_(False)).all()}
    material_ids = {subject_id for (subject_id,) in db.query(BsMaterial.subject_id).join(
        BsSubject, BsSubject.id == BsMaterial.subject_id).filter(
        BsMaterial.task_id == task.id, BsMaterial.subject_id.isnot(None),
        BsSubject.retired.is_(False)).all()}
    packet_ids = {subject_id for (subject_id,) in db.query(BsPacket.subject_id).join(
        BsSubject, BsSubject.id == BsPacket.subject_id).filter(
        BsPacket.task_id == task.id, BsSubject.retired.is_(False),
        BsSubject.origin == "explicit").all()}
    return explicit_ids | material_ids | packet_ids


def _subject_status(subject) -> str:
    # 方案A：类型以用户所选资料类别为准；AI 证据分歧（type_conflict）只作提示，
    # 不再让主体停在未就绪、也不再压掉 16/14 项分母。
    if not subject_reviews.subject_is_confirmed(subject):
        return "type_pending"
    return "ready"


def _status_reason(subject, status: str) -> str | None:
    if status == "ready":
        return None
    if not getattr(subject, "name", None):
        return "分包单位名称待确认，暂不生成 16/14 项清单"
    if (not getattr(subject, "type_confirmed", False)
            or getattr(subject, "subject_type", None) not in {"professional", "labor"}):
        return "分包类型待确认，暂不生成 16/14 项清单"
    if getattr(subject, "origin", None) != "explicit":
        return "主体身份来自材料自动识别，需人工确认后再生成清单"
    return "主体身份待确认，暂不生成清单"


def _bucket(item: dict) -> str:
    if item["id"] in FORM_CHECKS:
        return "form_check"
    return "base"


def _execution_group(item: dict) -> str:
    return item.get("execution_group") or "completeness"


def _review_ignored(finding) -> bool:
    review = getattr(finding, "review_json", None) or {}
    return review.get("decision") == "ignored" or review.get("action") == "ignored"


def _catalog() -> dict[str, dict]:
    cards = {card["card_id"]: card for card in resolve_rule_cards()}
    catalog = {}
    for item in load_report_checklist():
        card = cards.get(item["id"])
        if card is None:
            raise ValueError("规则卡缺失：%s" % item["id"])
        catalog[item["id"]] = dict(
            item,
            checklist_bucket=_bucket(item),
            execution_group=_execution_group(item),
            rule_card={
                "card_id": card["card_id"],
                "resolution_status": card["resolution_status"],
                "clause_id": card.get("clause_id"),
                "authority_level": card.get("authority_level"),
            },
        )
    return catalog


def applicable_items(task, subject) -> list[dict]:
    """列出一个已确认主体的逻辑项；未知类型返回空集而非 16+14。"""
    if _subject_status(subject) != "ready":
        return []
    subject_type = getattr(subject, "subject_type", None)
    ids = BASE_BY_TYPE[subject_type]
    catalog = _catalog()
    return [catalog[item_id] for item_id in ids]


def is_material_item(item_id: str) -> bool:
    item = _catalog().get(item_id)
    return bool(item and _execution_group(item) == "completeness" and item.get("kind") == "document")


def is_material_missing_finding(finding) -> bool:
    return bool(getattr(finding, "status", None) == STATUS_HIT
                and is_material_item(getattr(finding, "item_id", "")))


def is_material_only_finding(finding) -> bool:
    """材料在场/匹配疑问交给清单；人员证件对应等实质问题仍需复核。"""
    if getattr(finding, "kind", None) != "completeness":
        return False
    item_id = getattr(finding, "item_id", "")
    item = _catalog().get(item_id)
    if not item or not is_material_item(item_id):
        return False
    status = getattr(finding, "status", None)
    if status == STATUS_HIT:
        return True
    if status != STATUS_UNDECIDABLE:
        return False
    # 这些条目核对人员、证件或授权的对应关系，不能只用“材料已提供”替代。
    substantive_items = {"RC-A10", "RC-P15", "RC-L13", "RC-P16", "RC-L14"}
    return not item.get("required_all") and item_id not in substantive_items


def is_material_confirmable_finding(finding) -> bool:
    """复合材料包需逐子项核实，不能用单份候选确认整包。"""
    item = _catalog().get(getattr(finding, "item_id", "")) or {}
    return is_material_only_finding(finding) and not item.get("required_all")


def missing_label(key: str) -> str | None:
    """将 checklist:<item>[:<subitem>] 稳定键解析为目录标签。"""
    parts = str(key or "").split(":")
    if len(parts) < 2 or parts[0] != "checklist":
        return None
    item = _catalog().get(parts[1])
    if not item:
        return None
    if len(parts) == 2:
        return item["label"]
    child = next((row for row in item.get("required_all") or [] if row.get("key") == parts[2]), None)
    return child.get("label") if child else item["label"]


def missing_child_label(key: str) -> str | None:
    """仅解析目录中真实存在的子项，不以父项名称填充未知键。"""
    parts = str(key or "").split(":")
    if len(parts) != 3 or parts[0] != "checklist":
        return None
    item = _catalog().get(parts[1]) or {}
    child = next((row for row in item.get("required_all") or [] if row.get("key") == parts[2]), None)
    return child.get("label") if child else None


def _verified_project_department_list(material, task, subject) -> bool:
    """RC-A01 只接受完整的结构化核实事实。

    `seal_json` 当前是既有的可扩展结构化证据容器。普通判章器只会写页码和
    bbox，不会写下列人工/专用解析器核实字段，因此不会被误判为通过。项目、
    主体、范围、数量和项目部印章缺一项都保持“无法核实”，而不是冒充缺文件。
    """
    return any(
        row.get("verified_present") is True
        and row.get("seal_role") == "project_department"
        and row.get("task_id") == task.id
        and row.get("subject_id") == subject.id
        and row.get("project_identity_verified") is True
        and row.get("scope_verified") is True
        and row.get("quantity_verified") is True
        for row in (getattr(material, "seal_json", None) or [])
        if isinstance(row, dict)
    )


def _material_matches_item_keywords(material, item: dict) -> bool:
    """按目录关键词识别同一逻辑项的全部材料候选，不依赖首个命中顺序。"""
    keywords = item.get("keywords") or []
    if any(keyword in (getattr(material, "file_name", None) or "") for keyword in keywords):
        return True
    for page in (getattr(material, "pages_json", None) or []):
        text = page.get("text") if isinstance(page, dict) else str(page or "")
        if any(keyword in (text or "") for keyword in keywords):
            return True
    return False


def _subcontract_quantity_list_clue(materials):
    """明确的分包工程量清单可帮助翻页，不能替代专用清单或盖章事实。"""
    candidates = []
    for material in materials:
        if not is_attachment_source(material) or getattr(material, "parse_state", None) != "done":
            continue
        for page in getattr(material, "pages_json", None) or []:
            if (not isinstance(page, dict) or type(page.get("page")) is not int or page["page"] < 1
                    or not material_page_readable(material, page["page"])):
                continue
            text = page.get("text") if isinstance(page.get("text"), str) else ""
            head = text[:200]
            if ("分包工程量清单" in head and not re.search(r"附件目录|填写示例|空白模板", head)):
                candidates.append((material.id, page.get("page")))
    return candidates[0] if len(candidates) == 1 else None


def run_subject_completeness(task, subjects: list, materials: list) -> list[BsFinding]:
    """逐 active 主体运行第 1 步；只把本主体材料交给内核。"""
    findings: list[BsFinding] = []
    for subject in subjects:
        if _subject_status(subject) != "ready":
            continue
        owned = [material for material in materials
                 if getattr(material, "subject_id", None) == subject.id]
        items = [item for item in applicable_items(task, subject)
                 if _execution_group(item) == "completeness"]
        current = a3_check.run_completeness_check(
            task, owned, subject_types={subject.subject_type}, items=items,
        )
        item_by_id = {item["id"]: item for item in items}
        for finding in current:
            finding.subject_id = subject.id
            if finding.item_id == "RC-A01":
                candidates = [material for material in owned
                              if _material_matches_item_keywords(
                                  material, item_by_id["RC-A01"])]
                verified = next((material for material in candidates
                                 if _verified_project_department_list(
                                     material, task, subject)), None)
                if verified is not None:
                    finding.status = STATUS_MISS
                    finding.material_id = verified.id
                    finding.quote = None
                    finding.page = None
                    finding.message = (
                        "分包工程清单的项目归属、分包范围和数量及项目部红章"
                        "已完成结构化核实"
                    )
                    finding.missing_json = None
                elif candidates:
                    candidate_ids = {material.id for material in candidates}
                    if finding.material_id not in candidate_ids:
                        finding.material_id = candidates[0].id
                    finding.status = STATUS_UNDECIDABLE
                    finding.message = (
                        "已见分包工程清单，但项目归属、分包范围和数量及项目部红章"
                        "的结构化核实不完整，需人工核对原件"
                    )
                    finding.missing_json = None
                else:
                    clue = _subcontract_quantity_list_clue(owned)
                    if clue:
                        finding.material_id, finding.page = clue
                        finding.quote = "分包工程量清单"
                        finding.status = STATUS_UNDECIDABLE
                        candidate = next(material for material in owned if material.id == finding.material_id)
                        seal_signal = any(
                            isinstance(seal, dict) and type(seal.get("page_idx")) is int
                            and seal["page_idx"] == finding.page
                            and seal.get("verified_absent") is not True
                            and isinstance(seal.get("bbox"), (list, tuple)) and len(seal["bbox"]) == 4
                            and all(type(value) in (int, float) for value in seal["bbox"])
                            and seal["bbox"][2] > seal["bbox"][0] and seal["bbox"][3] > seal["bbox"][1]
                            for seal in getattr(candidate, "seal_json", None) or [])
                        finding.message = ("已定位分包工程量清单线索；本页检出盖章痕迹，是否为要求的分包工程清单需核对原件"
                                           if seal_signal else
                                           "已定位分包工程量清单线索；是否为要求的分包工程清单及盖章情况需核对原件")
                        finding.missing_json = None
        findings.extend(current)
    return findings


def _explicit_absence(seals, task_id: int, subject_id: int) -> bool:
    """只接受当前任务、当前主体的分包单位公章明确缺失事实。"""
    return any(
        row.get("verified_absent") is True
        and row.get("seal_role") == "subcontractor"
        and row.get("task_id") == task_id
        and row.get("subject_id") == subject_id
        for row in (seals or []) if isinstance(row, dict)
    )


def _explicit_presence(seals, task_id: int, subject_id: int) -> bool:
    """只接受已核实且明确绑定当前分包主体的公章事实。

    本地判章器只产出 page_idx/bbox 等图像信号；该信号不能证明印章主体，
    因此不得把普通非空 seal_json 当成“附件已盖分包单位公章”。项目部红章
    是 RC-A01 的另一种证据，也不得跨规则复用为分包单位公章。
    """
    return any(
        row.get("verified_present") is True
        and row.get("seal_role") == "subcontractor"
        and row.get("task_id") == task_id
        and row.get("subject_id") == subject_id
        for row in (seals or []) if isinstance(row, dict)
    )


def run_attachment_seal_check(task, subjects: list, materials: list) -> list[BsFinding]:
    """按 2025 附件要求核对红章；无可靠证据时不得伪造缺章。"""
    item = {"id": ATTACHMENT_SEAL_ITEM, "source": "2025年分包报审资料附件要求（2025.6.20）第三项第3条"}
    findings = []
    for subject in subjects:
        if _subject_status(subject) != "ready":
            continue
        owned = [material for material in materials
                 if getattr(material, "subject_id", None) == subject.id]
        absence_refs = []
        for material in owned:
            for seal in getattr(material, "seal_json", None) or []:
                if not isinstance(seal, dict) or not _explicit_absence(
                        [seal], task.id, subject.id):
                    continue
                page = seal.get("page_idx")
                if page is None:
                    page = seal.get("page")
                absence_refs.append({
                    "material_id": material.id,
                    "page": page,
                    "evidence_kind": "verified_seal_absence",
                    "seal_role": "subcontractor",
                })
        absence_refs.sort(key=lambda ref: (
            ref["material_id"], ref["page"] if isinstance(ref["page"], int) else -1,
        ))
        if absence_refs:
            status = STATUS_HIT
            message = "已人工标记存在未加盖分包单位公章的附件"
        elif owned and all(
                getattr(material, "parse_state", None) == "done"
                and _explicit_presence(
                    getattr(material, "seal_json", None), task.id, subject.id,
                )
                for material in owned):
            status = STATUS_MISS
            message = "全部附件均已有绑定当前分包主体的公章核实记录"
        else:
            status = STATUS_UNDECIDABLE
            signal_count = sum(bool(getattr(material, "seal_json", None)) for material in owned)
            message = (
                "无法确认全部附件均已加盖分包单位公章；"
                + ("已检出 %d 份材料存在印章图像信号，但仍需人工核对印章主体与附件覆盖" % signal_count
                   if signal_count else "印章图像/OCR 信号不足，需人工核对原件")
            )
        if absence_refs:
            refs = absence_refs
            anchor_material_id = absence_refs[0]["material_id"]
            anchor_page = absence_refs[0]["page"]
        else:
            refs = []
            anchor_material_id = None
            anchor_page = None
            for material in owned:
                for seal in getattr(material, "seal_json", None) or []:
                    if not isinstance(seal, dict) or seal.get("verified_absent"):
                        continue
                    page = seal.get("page_idx")
                    if page is None:
                        page = seal.get("page")
                    refs.append({"material_id": material.id,
                                 "page": page,
                                 "evidence_kind": "seal_signal"})
        findings.append(BsFinding(
            task_id=task.id, subject_id=subject.id, material_id=anchor_material_id,
            page=anchor_page, item_id=item["id"],
            kind="signature_chain", status=status, message=message,
            source=item["source"], evidence_json=refs or None,
        ))
    return findings


def _zero_counts() -> dict[str, int]:
    return {key: 0 for key in (
        "base_total", "form_checks_total", "project_supplements_applicable_total",
        "applicable_total", "present_total", "nonconforming_total", "missing_total",
        "undecidable_total", "pending_total",
    )}


def build_summary(task, subjects: list, findings: list) -> dict:
    """生成 /todo 与报告共用的权威主体摘要。"""
    result_rows = []
    for subject in subjects:
        status = _subject_status(subject)
        hints = []
        profile_codes = []
        counts = _zero_counts()
        checklist_items = []
        if status == "ready":
            items = applicable_items(task, subject)
            applicable_ids = {item["id"] for item in items}
            counts["base_total"] = sum(item["checklist_bucket"] == "base" for item in items)
            counts["form_checks_total"] = sum(item["checklist_bucket"] == "form_check" for item in items)
            counts["project_supplements_applicable_total"] = sum(
                item["checklist_bucket"] == "project_supplement" for item in items)
            counts["applicable_total"] = len(items)
            current = {}
            for finding in findings:
                if (getattr(finding, "subject_id", None) == subject.id
                        and getattr(finding, "item_id", None) in applicable_ids):
                    current[finding.item_id] = finding
            for finding in current.values():
                if finding.status == STATUS_MISS:
                    counts["present_total"] += 1
                elif finding.status == STATUS_HIT:
                    counts["nonconforming_total"] += 1
                    if is_material_missing_finding(finding):
                        counts["missing_total"] += 1
                elif finding.status == STATUS_UNDECIDABLE:
                    counts["undecidable_total"] += 1
            for item in items:
                finding = current.get(item["id"])
                checklist_items.append({
                    "item_id": item["id"],
                    "label": item.get("label"),
                    "checklist_bucket": item.get("checklist_bucket"),
                    "execution_group": item.get("execution_group"),
                    "status": getattr(finding, "status", "pending"),
                    "finding_id": getattr(finding, "id", None),
                    "missing": bool(finding and is_material_missing_finding(finding)),
                    "review_state": (
                        "ignored" if finding and _review_ignored(finding) else "unprocessed"
                    ) if finding else "unprocessed",
                    "location_mode": getattr(finding, "location_mode", None),
                    "material_id": getattr(finding, "material_id", None),
                    "page": getattr(finding, "page", None),
                })
            resolved = (counts["present_total"] + counts["nonconforming_total"]
                        + counts["undecidable_total"])
            counts["pending_total"] = counts["applicable_total"] - resolved
        result_rows.append({
            "subject_id": subject.id,
            "stable_code": subject.stable_code,
            "name": subject.name,
            "subject_type": subject.subject_type,
            "type_confirmed": bool(subject.type_confirmed),
            "type_conflict": bool(subject.type_conflict),
            # 冲突原因随摘要下发：前端只在主体卡里放一行提示，不另开确认流程。
            "type_conflict_reason": getattr(subject, "type_conflict_reason", None),
            "status": status,
            "status_reason": _status_reason(subject, status),
            "profile_codes": profile_codes,
            "procedural_hints": hints,
            "checklist_summary": counts,
            # 明细投影与计数分离，避免破坏既有 summary 结构；前端可按需展开，
            # 不得从展示文案反推适用项或完成度。
            "checklist_items": checklist_items,
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "count_unit": COUNT_UNIT,
        "count_note": COUNT_NOTE,
        "profile_codes": [],
        "guidance_notes": [],
        "subjects": result_rows,
    }
