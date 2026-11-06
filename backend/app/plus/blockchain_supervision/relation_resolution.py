"""人员名单与证书的三态关系解析（补件证据链 S3）。

只消费已经落库的材料事实与既有名单/证号提取结果：不发起 OCR、不补齐缺失字符、
不因同名推断同一人。关系是**候选判定**，须经人工复核才能作为 A2-05 的通过证据；
主体冲突或主体未确认时关系仍然保留，但不构成有效通过证据（③A）。
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata

from sqlalchemy.exc import IntegrityError

from . import a2_check
from .certificate_number_check import roster_b_entries
from .material_facts import latest_material_facts
from .models import RELATION_SCHEMA_VERSION, BsEvidenceRelation, BsMaterial, BsSubject
from .personnel_evidence import extract_management_roster

RELATION_TYPE = "person_certificate"
RESOLVER_VERSION = "person-cert-relation.v1"

STATUS_MATCHED = "matched"
STATUS_CONFLICT = "conflict"
STATUS_UNRESOLVED = "unresolved"
STATUSES = frozenset({STATUS_MATCHED, STATUS_CONFLICT, STATUS_UNRESOLVED})

RESULT_MATCHED = "matched"
RESULT_CONFLICT = "conflict"
RESULT_UNRESOLVED = "unresolved"
RESULT_NOT_APPLICABLE = "not_applicable"

# 岗位 → 应交安证书类别。未登记的岗位不据此判定类别，不默认按 B 证处理。
POST_CERT_TYPE = {
    "项目经理": "B", "项目负责人": "B", "项目总工": "B", "技术负责人": "B",
    "安全员": "C", "专职安全员": "C", "安全负责人": "C", "专职安全生产管理人员": "C",
    "企业负责人": "A", "企业主要负责人": "A", "法定代表人": "A",
}

# 名单页即使抽到姓名/证号也不能当证书页：两侧必须来自不同证据位置。
_ROSTER_PAGE_RE = re.compile(r"人员名单|管理人员名单|名单及联系方式")
_QUALITY_RANK = {"readable": 0, "review_required": 1, "unreadable": 2}


def normalize_person_name(value) -> str:
    """姓名归一：只做 NFKC 与去空白，不做同音、简称或繁简替换。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")))


def normalize_certificate_number(value) -> str | None:
    """证书编号只做可逆规范化（NFKC、去空白与括号、大写），不猜数字。"""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"[\s()（）]", "", text)
    return text.upper() or None


def _dimension(field, expected, actual, result, reason=None, expected_ref=None, actual_ref=None):
    return {"field": field, "expected": expected, "actual": actual, "result": result,
            "reason": reason, "expected_ref": expected_ref, "actual_ref": actual_ref}


def _worst_quality(values) -> str:
    return max((v or "readable" for v in values), key=lambda q: _QUALITY_RANK.get(q, 1))


def _page_text(material, page_no) -> str:
    for page in getattr(material, "pages_json", None) or []:
        if isinstance(page, dict) and page.get("page") == page_no:
            return str(page.get("text") or "")
    return ""


def _raw(fields, path):
    fact = fields.get(path)
    return None if fact is None else fact.raw_value


def _certificate_type(fields):
    fact = fields.get("certificate.type")
    if fact is None:
        return None
    return (fact.normalized_value or fact.raw_value or "").strip().upper() or None


def _page_observations(materials, facts_by_material, roster_pages) -> list[dict]:
    """逐页汇总证书事实；名单页与非证书页不产出观察。"""
    observations: list[dict] = []
    for material in materials:
        if getattr(material, "parse_state", None) != "done":
            continue
        by_page: dict = {}
        for fact in facts_by_material.get(material.id, []) or []:
            by_page.setdefault(fact.page, []).append(fact)
        for page_no in sorted(by_page):
            if (material.id, page_no) in roster_pages:
                continue
            if _ROSTER_PAGE_RE.search(_page_text(material, page_no)):
                continue
            facts = by_page[page_no]
            fields: dict = {}
            for fact in facts:
                # 同页同字段取最早一行，不把多值拼成一个“更完整”的字段。
                fields.setdefault(fact.field_path, fact)
            if "certificate.number" not in fields and "certificate.type" not in fields:
                continue
            anchor = fields.get("certificate.number") or fields.get("certificate.type")
            observations.append({
                "material_id": material.id,
                "subject_id": getattr(material, "subject_id", None),
                "page": page_no,
                "name": _raw(fields, "person.name"),
                "company": _raw(fields, "company.name"),
                "certificate_type": _certificate_type(fields),
                "certificate_number": _raw(fields, "certificate.number"),
                "quality": _worst_quality([f.quality for f in facts]),
                "quote": anchor.quote if anchor is not None else None,
                "fact_ids": [f.id for f in facts if f.id is not None],
            })
    return observations


def _roster_index(materials):
    records: dict = {}
    for record in extract_management_roster(materials):
        key = normalize_person_name(record.get("name"))
        if key:
            records.setdefault(key, []).append(record)
    return records


def _relation(person_key, person_name, post, subject_id, left, right, dimensions,
              reason, status, generation_id=None):
    return {
        "task_id": left.get("task_id"),
        "subject_id": subject_id,
        "generation_id": generation_id,
        "relation_type": RELATION_TYPE,
        "person_key": person_key,
        "person_name": person_name,
        "post": post,
        "status": status,
        "reason": reason,
        "dimensions": dimensions,
        "left": left,
        "right": right,
        "fact_ids": sorted({fid for fid in (right.get("fact_ids") or [])}),
        "resolver_version": RESOLVER_VERSION,
        "schema_version": RELATION_SCHEMA_VERSION,
    }


def _relation_for(person_key, records, number_row, certs, *, material_index,
                  subject_names, generation_lookup=None):
    ordered = sorted(records, key=lambda r: (r.get("material_id") or 0, r.get("page") or 0))
    primary = ordered[0]
    person_name = primary.get("name")
    posts = {str(r.get("post")).strip() for r in ordered if r.get("post")}
    post = next(iter(posts)) if len(posts) == 1 else None
    material = material_index.get(primary.get("material_id"))
    subject_id = getattr(material, "subject_id", None)
    left = {
        "material_id": primary.get("material_id"),
        "page": primary.get("page"),
        "quote": primary.get("quote"),
        "name": person_name,
        "post": post,
        "evidence_kind": primary.get("evidence_kind"),
        "task_id": getattr(material, "task_id", None),
        "records": [{"material_id": r.get("material_id"), "page": r.get("page"),
                     "quote": r.get("quote"), "post": r.get("post")} for r in ordered],
    }
    task_id = left.get("task_id")
    left["task_id"] = task_id
    dimensions = []
    if len(posts) > 1:
        dimensions.append(_dimension(
            "person.post", sorted(posts), None, RESULT_UNRESOLVED,
            "同一人员在名单中存在多个岗位记录，不能据此判定应交证书类别"))

    if not certs:
        dimensions.append(_dimension(
            "certificate.presence", person_name, None, RESULT_UNRESOLVED,
            "未找到可唯一归到本人的证书页（未识别姓名或缺失证书页）"))
        return _relation(person_key, person_name, post, subject_id, left, {}, dimensions,
                         "未找到可唯一归到本人的证书页，请补充本人清晰证书", STATUS_UNRESOLVED)

    identities = {(c.get("certificate_type"), normalize_certificate_number(c.get("certificate_number")))
                  for c in certs}
    right = {
        "material_id": certs[0]["material_id"],
        "page": certs[0]["page"],
        "pages": sorted({c["page"] for c in certs}),
        "quote": certs[0]["quote"],
        "name": certs[0]["name"],
        "company": certs[0]["company"],
        "certificate_type": certs[0]["certificate_type"],
        "certificate_number": certs[0]["certificate_number"],
        "quality": _worst_quality([c["quality"] for c in certs]),
        "fact_ids": sorted({fid for c in certs for fid in c["fact_ids"] or []}),
    }
    generation_id = None
    if generation_lookup is not None:
        cert_material = material_index.get(certs[0]["material_id"])
        if cert_material is not None:
            try:
                generation_id = generation_lookup(cert_material)
            except Exception:  # 代次只是归属线索，取不到就留空，不影响关系本身
                generation_id = None
    # 同名不等于同人：名单或证书页跨多个主体时，字段比较已无意义，直接转人工。
    roster_subjects = {getattr(material_index.get(r.get("material_id")), "subject_id", None)
                       for r in ordered} - {None}
    cert_subjects = {c.get("subject_id") for c in certs if c.get("subject_id") is not None}
    ambiguous = []
    if len(roster_subjects) > 1:
        ambiguous.append("名单中同名记录分属多个主体")
    if len(cert_subjects) > 1:
        ambiguous.append("同名证书页分属多个主体")
    if ambiguous:
        dimensions.append(_dimension(
            "person.identity",
            subject_names.get(next(iter(sorted(roster_subjects)))) if len(roster_subjects) == 1 else None,
            subject_names.get(next(iter(sorted(cert_subjects)))) if len(cert_subjects) == 1 else None,
            RESULT_UNRESOLVED,
            "；".join(ambiguous) + "，不能仅凭姓名判定为同一人"))
        return _relation(person_key, person_name, post, subject_id, left, right, dimensions,
                         "同名人员分属多个主体，需人工确认名单与证书的对应关系",
                         STATUS_UNRESOLVED, generation_id)

    if len(identities) > 1:
        dimensions.append(_dimension(
            "certificate.identity", None,
            sorted({(c.get("certificate_type"), c.get("certificate_number")) for c in certs}),
            RESULT_UNRESOLVED, "同一人员存在多份互斥证书（类别或编号不一致），不能择一采信"))
        return _relation(person_key, person_name, post, subject_id, left, right, dimensions,
                         "同一人员存在多份互斥证书，需人工确认哪一份为准", STATUS_UNRESOLVED,
                         generation_id)

    cert_name = right.get("name")
    dimensions.append(_dimension(
        "person.name", person_name, cert_name,
        RESULT_MATCHED if normalize_person_name(cert_name) == person_key else RESULT_CONFLICT,
        None,
        expected_ref={"material_id": left.get("material_id"), "page": left.get("page")},
        actual_ref={"material_id": right.get("material_id"), "page": right.get("page")}))

    # 主体：证书材料明确归属另一主体时直接冲突，不进入字段比较。
    cert_subject_id = certs[0].get("subject_id")
    if subject_id is not None and cert_subject_id is not None:
        dimensions.append(_dimension(
            "subject", subject_names.get(subject_id), subject_names.get(cert_subject_id),
            RESULT_MATCHED if subject_id == cert_subject_id else RESULT_CONFLICT,
            None if subject_id == cert_subject_id else "证书材料明确归属另一主体，补件不能证明本主体人员"))

    expected_company = subject_names.get(subject_id)
    actual_company = right.get("company")
    if expected_company is None:
        dimensions.append(_dimension("company.name", None, actual_company, RESULT_UNRESOLVED,
                                     "名单侧未确定所属主体，无法核对证书企业"))
    elif not actual_company:
        dimensions.append(_dimension("company.name", expected_company, None, RESULT_UNRESOLVED,
                                     "证书企业名称未识别，无法核对是否同主体"))
    else:
        same = a2_check.norm_company(expected_company) == a2_check.norm_company(actual_company)
        dimensions.append(_dimension("company.name", expected_company, actual_company,
                                     RESULT_MATCHED if same else RESULT_CONFLICT,
                                     None if same else "证书企业名称与名单主体不一致"))

    expected_type = POST_CERT_TYPE.get(post) if post else None
    actual_type = right.get("certificate_type")
    if post is None:
        dimensions.append(_dimension("certificate.type", None, actual_type, RESULT_UNRESOLVED,
                                     "名单未提供可判定的岗位，不能确定应交证书类别"))
    elif expected_type is None:
        dimensions.append(_dimension("certificate.type", None, actual_type, RESULT_UNRESOLVED,
                                     "岗位「%s」无对应证书类别规则，需人工判定" % post))
    elif actual_type is None:
        dimensions.append(_dimension("certificate.type", expected_type, None, RESULT_UNRESOLVED,
                                     "证书类别未识别，无法证明为交安%s证" % expected_type))
    else:
        same = actual_type == expected_type
        dimensions.append(_dimension("certificate.type", expected_type, actual_type,
                                     RESULT_MATCHED if same else RESULT_CONFLICT,
                                     None if same else "岗位应交交安%s证，实际识别为交安%s证"
                                     % (expected_type, actual_type)))

    quality = right.get("quality")
    if quality not in (None, "readable"):
        dimensions.append(_dimension(
            "evidence.quality", "readable", quality, RESULT_UNRESOLVED,
            "证书页质量存疑（遮挡/模糊/需复核），字段一致也不能直接作为通过证据",
            actual_ref={"material_id": right.get("material_id"), "page": right.get("page")}))

    roster_number = normalize_certificate_number((number_row or {}).get("number"))
    cert_number = normalize_certificate_number(right.get("certificate_number"))
    if not cert_number:
        dimensions.append(_dimension("certificate.number", roster_number, None, RESULT_UNRESOLVED,
                                     "证书编号未识别，无法核对"))
    elif not roster_number:
        dimensions.append(_dimension("certificate.number", None, cert_number, RESULT_UNRESOLVED,
                                     "名单未提供可核对的证书编号，需人工核对原件"))
    else:
        same = roster_number == cert_number
        dimensions.append(_dimension("certificate.number", roster_number, cert_number,
                                     RESULT_MATCHED if same else RESULT_CONFLICT,
                                     None if same else "名单与证书编号不一致"))

    conflicts = [d["field"] for d in dimensions if d["result"] == RESULT_CONFLICT]
    unresolved = [d["field"] for d in dimensions if d["result"] == RESULT_UNRESOLVED]
    if conflicts:
        status = STATUS_CONFLICT
        reason = "字段冲突：%s" % "、".join(conflicts)
    elif unresolved:
        status = STATUS_UNRESOLVED
        reason = "无法确认：%s" % "、".join(unresolved)
    else:
        status = STATUS_MATCHED
        reason = "姓名、主体、证书类别与编号一致"
    return _relation(person_key, person_name, post, subject_id, left, right, dimensions,
                     reason, status, generation_id)


def build_person_certificate_relations(materials, facts_by_material, *,
                                       subject_names=None,
                                       generation_lookup=None) -> list[dict]:
    """把名单记录与证书事实对齐成三态关系；只对名单上的人员产出关系。"""
    materials = list(materials)
    subject_names = subject_names or {}
    material_index = {m.id: m for m in materials}
    records = _roster_index(materials)
    roster_pages = {(r.get("material_id"), r.get("page")) for r in
                    extract_management_roster(materials)}
    observations = _page_observations(materials, facts_by_material, roster_pages)
    numbers = {}
    for row in roster_b_entries(materials):
        key = normalize_person_name(row.get("name"))
        if key and key not in numbers:
            numbers[key] = row
    certs_by_name: dict = {}
    for observation in observations:
        key = normalize_person_name(observation.get("name"))
        if key:
            certs_by_name.setdefault(key, []).append(observation)
    relations = []
    for key in sorted(records):
        relations.append(_relation_for(
            key, records[key], numbers.get(key), certs_by_name.get(key, []),
            material_index=material_index, subject_names=subject_names,
            generation_lookup=generation_lookup))
    return relations


def relation_fingerprint(relation) -> str:
    """关系指纹：同一任务内并发/重复写入的唯一键，含事实行与解析器版本。"""
    payload = {
        "task_id": relation.get("task_id"),
        "subject_id": relation.get("subject_id"),
        "relation_type": relation.get("relation_type"),
        "person_key": relation.get("person_key"),
        "left": [relation.get("left", {}).get("material_id"),
                 relation.get("left", {}).get("page")],
        "right": [relation.get("right", {}).get("material_id"),
                  relation.get("right", {}).get("page")],
        "fact_ids": sorted(relation.get("fact_ids") or []),
        "status": relation.get("status"),
        "resolver_version": relation.get("resolver_version"),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True)
                          .encode("utf-8")).hexdigest()


def _relation_exists(db, task_id, fingerprint: str) -> bool:
    return (db.query(BsEvidenceRelation.id)
            .filter(BsEvidenceRelation.task_id == task_id,
                    BsEvidenceRelation.relation_key == fingerprint)
            .first()) is not None


def persist_relations(db, relations) -> list:
    """追加关系行，返回本次真正新增的行；已有指纹跳过，历史行不更新不删除。"""
    inserted: list = []
    for relation in relations:
        if relation.get("status") not in STATUSES:
            raise ValueError("未登记的关系状态: %r" % (relation.get("status"),))
        if not relation.get("task_id") or not relation.get("person_key"):
            continue
        fingerprint = relation_fingerprint(relation)
        if _relation_exists(db, relation["task_id"], fingerprint):
            continue
        row = BsEvidenceRelation(
            task_id=relation["task_id"],
            subject_id=relation.get("subject_id"),
            generation_id=relation.get("generation_id"),
            relation_type=relation.get("relation_type") or RELATION_TYPE,
            person_key=relation["person_key"],
            person_name=relation.get("person_name"),
            post=relation.get("post"),
            status=relation["status"],
            reason=relation.get("reason"),
            dimensions_json=relation.get("dimensions") or [],
            left_json=relation.get("left") or {},
            right_json=relation.get("right") or {},
            fact_ids_json=relation.get("fact_ids") or [],
            relation_key=fingerprint,
            resolver_version=relation.get("resolver_version") or RESOLVER_VERSION,
            schema_version=relation.get("schema_version") or RELATION_SCHEMA_VERSION,
        )
        try:
            # 逐行 savepoint：并发撞唯一索引时只回滚本行，不污染材料收口事务。
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError:
            if not _relation_exists(db, relation["task_id"], fingerprint):
                raise
            continue
        inserted.append(row)
    return inserted


def resolve_task_relations(db, task, materials=None, *, generation_lookup=None) -> list:
    """按任务重算关系并追加落库；facts 取每个材料的最新批次。"""
    if materials is None:
        materials = (db.query(BsMaterial)
                     .filter(BsMaterial.task_id == task.id)
                     .order_by(BsMaterial.id)
                     .all())
    else:
        materials = list(materials)
    facts_by_material = {m.id: latest_material_facts(db, m.id) for m in materials}
    subject_names = {row.id: row.name for row in
                     db.query(BsSubject).filter(BsSubject.task_id == task.id).all()}
    relations = build_person_certificate_relations(materials, facts_by_material,
                                                   subject_names=subject_names,
                                                   generation_lookup=generation_lookup)
    return persist_relations(db, relations)


def latest_relations(db, task_id: int) -> list:
    """该任务最新一批关系行（按流水号取每个人员最后一版）。"""
    rows = (db.query(BsEvidenceRelation)
            .filter(BsEvidenceRelation.task_id == task_id)
            .order_by(BsEvidenceRelation.id)
            .all())
    latest: dict = {}
    for row in rows:
        latest[row.person_key] = row
    return list(latest.values())


__all__ = [
    "RELATION_TYPE", "RESOLVER_VERSION", "STATUSES",
    "STATUS_MATCHED", "STATUS_CONFLICT", "STATUS_UNRESOLVED",
    "POST_CERT_TYPE", "normalize_person_name", "normalize_certificate_number",
    "build_person_certificate_relations", "relation_fingerprint",
    "persist_relations", "resolve_task_relations", "latest_relations",
]