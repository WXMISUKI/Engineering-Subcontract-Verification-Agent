"""S4：把人员证件三态关系投影为 A2-05 结论（补件证据链）。

关系是候选判定，finding 才是对外结论。本模块只做确定性投影：

- ``matched``    -> ``miss``（名单与证书一致）
- ``conflict``   -> ``hit``（存在明确冲突）
- ``unresolved`` -> ``undecidable``（需要人工确认）

投影前必须重新校验关系的当前性：关系引用的材料仍在当前材料集合、材料已解析
完成、未被替代且不是待生效替换候选；证书侧事实行还必须属于该材料最新批次。
最新关系行若引用已失效材料，回看同一 ``(subject_id, person_key)`` 的历史行，
只采用仍引用当前材料的那一版。

定位纪律：文本命中不等于有 PDF 坐标。只有 ``pinning.reference_location_mode``
认可的 ``pdf_page`` / ``rendered_page`` / 非空 ``bbox`` 才允许 ``precise``，
且探针页码必须与关系记录页一致；其余最多 ``page``，页码不可信时清空为
``unresolved``，绝不为了高亮伪造文字框。
"""

from __future__ import annotations

import json

from . import pinning
from .certificate_number_check import ITEM_ID, SOURCE
from .material_facts import latest_material_facts
from .models import BsEvidenceRelation, BsFinding
from .relation_resolution import (
    STATUS_CONFLICT,
    STATUS_MATCHED,
    STATUS_UNRESOLVED,
    STATUSES,
    normalize_certificate_number,
    relation_reason_code,
)

# A2-05 当前业务口径只覆盖项目经理；其他岗位关系由各自条目消费，不能塞入本条。
PROJECT_MANAGER_POSTS = frozenset({"项目经理"})

_STATUS_TO_FINDING = {
    STATUS_MATCHED: "miss",
    STATUS_CONFLICT: "hit",
    STATUS_UNRESOLVED: "undecidable",
}

_FIELD_LABELS = {
    "person.name": "姓名",
    "person.identity": "人员身份",
    "person.post": "岗位",
    "subject": "主体归属",
    "company.name": "企业名称",
    "certificate.type": "证书类别",
    "certificate.number": "证书编号",
    "certificate.presence": "证书",
    "certificate.identity": "证书信息",
    "evidence.quality": "证书页质量",
}


def _is_current_material(material) -> bool:
    """材料当前且可读；与 ``_rule_materials`` 的口径保持一致并去掉未解析页。"""
    return (getattr(material, "parse_state", None) == "done"
            and getattr(material, "superseded_by_id", None) is None
            and getattr(material, "replacement_target_id", None) is None)


def _fact_ids(relation) -> set[int]:
    right = getattr(relation, "right_json", None) or {}
    values = list(right.get("fact_ids") or []) + list(getattr(relation, "fact_ids_json", None) or [])
    out = set()
    for value in values:
        try:
            out.add(int(value))
        except (TypeError, ValueError):
            continue
    return out


def _relation_is_current(relation, material_index, latest_fact_ids, valid_subject_ids) -> bool:
    """关系仍可投影的完整判据；任一条件缺失都保守放弃，不猜。"""
    if getattr(relation, "status", None) not in STATUSES:
        return False
    subject_id = getattr(relation, "subject_id", None)
    if subject_id is None:
        return False
    if valid_subject_ids is not None and subject_id not in valid_subject_ids:
        return False
    left = getattr(relation, "left_json", None) or {}
    right = getattr(relation, "right_json", None) or {}
    if material_index.get(left.get("material_id")) is None:
        return False
    cert_material_id = right.get("material_id")
    if cert_material_id is None:
        # 未找到证书页是合法的 unresolved 关系，不需要事实批次校验。
        return True
    if material_index.get(cert_material_id) is None:
        return False
    latest = latest_fact_ids.get(cert_material_id)
    if latest is None:
        return False
    fact_ids = _fact_ids(relation)
    # 引用旧批次事实会让替换前的证书绕过替代链重新成为证据。
    return bool(fact_ids) and fact_ids.issubset(set(latest))


def latest_current_relations(db, task_id: int, materials, *, relation_rows=None,
                             latest_fact_ids=None, valid_subject_ids=None) -> list:
    """按 ``(subject_id, person_key)`` 取最新一版仍然当前的关系行。

    ``relation_rows`` / ``latest_fact_ids`` 仅供定向测试注入；生产路径从数据库读取。
    最新行引用已替代材料或旧事实时，继续回看同组历史行，避免用失效证书覆盖
    仍有效的旧证据，也避免把历史限制误当成当前结论。
    """
    material_index = {material.id: material for material in materials
                      if _is_current_material(material)}
    if relation_rows is None:
        if db is None:
            raise ValueError("relation_rows 与 db 不能同时为空")
        relation_rows = (db.query(BsEvidenceRelation)
                         .filter(BsEvidenceRelation.task_id == task_id)
                         .order_by(BsEvidenceRelation.id)
                         .all())
    if latest_fact_ids is None:
        if db is None:
            raise ValueError("latest_fact_ids 与 db 不能同时为空")
        latest_fact_ids = {
            material_id: {fact.id for fact in latest_material_facts(db, material_id)
                          if getattr(fact, "id", None) is not None}
            for material_id in material_index
        }
    groups: dict = {}
    for row in sorted(relation_rows, key=lambda item: getattr(item, "id", 0) or 0):
        groups.setdefault((row.subject_id, row.person_key), []).append(row)
    selected = []
    for rows in groups.values():
        for row in reversed(rows):
            if _relation_is_current(row, material_index, latest_fact_ids, valid_subject_ids):
                selected.append(row)
                break
    return selected


def _clip(value, limit: int = 480) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _label(field: str) -> str:
    return _FIELD_LABELS.get(field, field or "字段")


def _display(value) -> str:
    if value is None or value == "" or value == [] or value == {}:
        return "无法确认"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _side_text(ref: dict | None, side: str) -> str:
    if ref and ref.get("page"):
        return "%s第%s页" % (side, ref["page"])
    return side


def _message(relation, status: str, left_ref: dict | None, right_ref: dict | None) -> str:
    """只写人员、字段和左右两侧依据；不附质量页清单或技术维度列表。"""
    name = getattr(relation, "person_name", None) or getattr(relation, "person_key", None) or "该人员"
    left_text = _side_text(left_ref, "名单")
    right_text = _side_text(right_ref, "证书")
    dimensions = [dim for dim in (getattr(relation, "dimensions_json", None) or [])
                  if isinstance(dim, dict)]
    if status == STATUS_CONFLICT:
        parts = []
        for dim in dimensions:
            if dim.get("result") != "conflict":
                continue
            label = _label(dim.get("field"))
            parts.append("%s（名单「%s」，证书「%s」）" % (
                label, _display(dim.get("expected")), _display(dim.get("actual"))))
        detail = "、".join(parts) or _clip(getattr(relation, "reason", None) or "字段不一致", 220)
        return _clip("项目经理%s的交安 B 证信息不一致：%s；%s，%s，请核对原件"
                     % (name, detail, left_text, right_text))
    if status == STATUS_MATCHED:
        number = None
        for dim in dimensions:
            if dim.get("field") == "certificate.number" and dim.get("result") == "matched":
                number = dim.get("expected")
                break
        suffix = "，证书编号「%s」" % number if number else ""
        return _clip("项目经理%s的名单与交安 B 证信息一致%s；%s，%s"
                     % (name, suffix, left_text, right_text))
    reasons = []
    for dim in dimensions:
        if dim.get("result") != "unresolved":
            continue
        text = str(dim.get("reason") or "").strip()
        if not text:
            text = "%s无法确认" % _label(dim.get("field"))
        if text not in reasons:
            reasons.append(text)
    detail = "；".join(reasons[:3]) or _clip(getattr(relation, "reason", None) or "关键字段无法确认", 240)
    return _clip("项目经理%s无法确认：%s；%s，%s" % (name, detail, left_text, right_text))


def _located_ref(task_id: int, ref: dict | None, materials, *, role: str) -> dict | None:
    """把关系侧引用钉扎成可渲染证据；不完整坐标只保留页级入口。"""
    if not isinstance(ref, dict) or not ref.get("material_id"):
        return None
    out = {"material_id": ref.get("material_id"), "page": ref.get("page"),
           "quote": ref.get("quote"), "role": role}
    for key in ("name", "company", "certificate_type", "certificate_number", "pages", "quality",
                "number_source", "extractor_version", "context_quote",
                "generation_id", "generation_sequence", "certificate_evidence",
                "reason_code"):
        if ref.get(key) is not None:
            out[key] = ref[key]
    if not out.get("quote"):
        out["location_mode"] = "unresolved"
        out["page"] = None
        return out
    probe = BsFinding(task_id=task_id, material_id=out["material_id"], page=out["page"],
                      quote=out["quote"], evidence_json=[dict(out)])
    pinning.pin_findings([probe], materials)
    pinned = dict(probe.evidence_json[0]) if probe.evidence_json else dict(out)
    mode = pinning.reference_location_mode(pinned)
    original_page = out.get("page")
    if mode == "precise" and probe.page == original_page:
        out.update(pinned)
        out["page"] = probe.page
        out["location_mode"] = "precise"
    elif mode == "precise":
        # 引文在别的页命中，说明关系记录的页位置不可信，不保留幽灵页。
        out["page"] = None
        out["location_mode"] = "unresolved"
        out["anchor"] = {"status": "unresolved", "reason": "page_mismatch"}
    else:
        page = probe.page if probe.location_mode in ("page", "precise") else None
        out["page"] = page
        out["location_mode"] = "page" if page else "unresolved"
        anchor = pinned.get("anchor") if isinstance(pinned.get("anchor"), dict) else {}
        anchor = {key: value for key, value in anchor.items() if key != "bbox"}
        anchor["status"] = "page_only" if page else "unresolved"
        out["anchor"] = anchor
    out["role"] = role
    return out


def _evidence_roles(status: str, right: dict | None) -> tuple[str, str | None]:
    if status == STATUS_CONFLICT:
        return "support", "contradiction"
    if status == STATUS_MATCHED:
        return "support", "support"
    if right is None:
        return "expected", None
    quality = str(right.get("quality") or "").lower()
    return "expected", "unreadable" if quality not in ("", "readable") else "expected"


def _project_one(task, relation, materials) -> BsFinding | None:
    status = getattr(relation, "status", None)
    finding_status = _STATUS_TO_FINDING.get(status)
    if finding_status is None:
        return None
    left = getattr(relation, "left_json", None) or {}
    right = getattr(relation, "right_json", None) or {}
    left_role, right_role = _evidence_roles(status, right if right else None)
    # 名单主行与编号续行可能不是同一段文本：优先用 number_evidence 指向
    # 实际决定期望值的续行，主行只作为上下文保留在关系 JSON 中。
    number_evidence = left.get("number_evidence")
    left_source = ({**left, **number_evidence}
                   if isinstance(number_evidence, dict) and number_evidence.get("material_id")
                   else left)
    reason_code = relation_reason_code(relation)
    if reason_code:
        left_source = {**left_source, "reason_code": reason_code}
        right = {**right, "reason_code": reason_code}
    left_ref = _located_ref(task.id, left_source, materials, role=left_role)
    if left_ref is None:
        return None
    right_ref = (_located_ref(task.id, right, materials, role=right_role)
                 if right_role and right.get("material_id") else None)
    if status == STATUS_CONFLICT and left_ref and right_ref:
        dimensions = [dim for dim in (getattr(relation, "dimensions_json", None) or []) if isinstance(dim, dict)]
        matched = {dim.get("field") for dim in dimensions if dim.get("result") == "matched"}
        identity_proven = {"person.name", "subject", "company.name", "certificate.type"}.issubset(matched)
        quality_uncertain = any(dim.get("field") == "evidence.quality" and dim.get("result") != "matched"
                                for dim in dimensions)
        fields = sorted({dim.get("field") for dim in dimensions
                         if isinstance(dim, dict) and dim.get("result") == "conflict"
                         and dim.get("field") == "certificate.number"
                         and normalize_certificate_number(dim.get("expected"))
                         and normalize_certificate_number(dim.get("actual"))
                         and normalize_certificate_number(dim.get("expected")) != normalize_certificate_number(dim.get("actual"))})
        if fields and identity_proven and not quality_uncertain:
            left_ref["conflict_fields"] = fields
            right_ref["conflict_fields"] = fields
    evidence = [ref for ref in (left_ref, right_ref) if ref]
    return BsFinding(
        task_id=task.id,
        subject_id=getattr(relation, "subject_id", None),
        item_id=ITEM_ID,
        kind="cert_validity",
        status=finding_status,
        source=SOURCE,
        material_id=left_ref.get("material_id"),
        page=left_ref.get("page"),
        quote=left_ref.get("quote"),
        location_mode=left_ref.get("location_mode"),
        evidence_json=evidence,
        message=_message(relation, status, left_ref, right_ref),
    )


def project_person_certificate_relations(db, task, materials, *, relations=None,
                                         relation_rows=None, latest_fact_ids=None,
                                         valid_subject_ids=None) -> list:
    """把当前有效的项目经理关系投影为 A2-05 findings。

    ``relations`` 可直接注入已选中的关系行；否则按当前性规则从数据库读取。
    ``materials`` 必须是本轮规则可用材料集合，定位只在该集合内进行。
    """
    if relations is None:
        relations = latest_current_relations(
            db, task.id, materials, relation_rows=relation_rows,
            latest_fact_ids=latest_fact_ids, valid_subject_ids=valid_subject_ids)
    findings = []
    for relation in relations:
        if getattr(relation, "post", None) not in PROJECT_MANAGER_POSTS:
            continue
        finding = _project_one(task, relation, materials)
        if finding is not None:
            findings.append(finding)
    return findings


def merge_projected_findings(findings: list, projected: list, *, item_id: str = ITEM_ID) -> list:
    """用投影替换同一主体的旧 A2-05，保留其他主体与无关系时的旧内核回退。"""
    covered = {finding.subject_id for finding in projected
               if getattr(finding, "subject_id", None) is not None}
    if not covered:
        return findings
    old = [finding for finding in findings if finding.item_id == item_id]
    old_subject_ids = {finding.subject_id for finding in old
                       if getattr(finding, "subject_id", None) is not None}
    # 旧内核的全局待核结论只在它覆盖的主体都已有投影时才移除，避免吞掉其余主体。
    drop_global = old_subject_ids.issubset(covered)
    kept = [finding for finding in findings
            if not (finding.item_id == item_id
                    and (finding.subject_id in covered
                         or (finding.subject_id is None and drop_global)))]
    return kept + projected


__all__ = [
    "PROJECT_MANAGER_POSTS",
    "latest_current_relations",
    "project_person_certificate_relations",
    "merge_projected_findings",
]
