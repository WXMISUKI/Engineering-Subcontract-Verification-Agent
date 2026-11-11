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
from .roster_certificate_evidence import roster_certificate_entries
from .material_facts import latest_material_facts
from .models import RELATION_SCHEMA_VERSION, BsEvidenceRelation, BsMaterial, BsSubject
from .personnel_evidence import extract_management_roster

RELATION_TYPE = "person_certificate"
RESOLVER_VERSION = "person-cert-relation.v5"

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


def _dimension(field, expected, actual, result, reason=None, expected_ref=None,
               actual_ref=None, reason_code=None):
    return {"field": field, "expected": expected, "actual": actual, "result": result,
            "reason": reason, "reason_code": reason_code,
            "expected_ref": expected_ref, "actual_ref": actual_ref}


def _is_current_material(material) -> bool:
    """只有生效材料参与关系判定：已替代旧版与待生效替换候选都排除。

    历史事实仍按 append-only 保留，但它们证明的是旧版材料，不能继续产出
    当前关系；否则替换后旧证书会绕过替代链再次成为通过证据。
    """
    return (getattr(material, "superseded_by_id", None) is None
            and getattr(material, "replacement_target_id", None) is None)


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


def _page_observations(materials, facts_by_material, roster_pages,
                       generation_lookup=None) -> list[dict]:
    """逐页汇总证书事实；名单页与非证书页不产出观察。"""
    observations: list[dict] = []
    for material in materials:
        if getattr(material, "parse_state", None) != "done":
            continue
        generation = {"generation_id": None, "sequence": None, "conflict": False}
        if generation_lookup is not None:
            try:
                generation = _generation_info(generation_lookup(material))
            except Exception:
                generation = {"generation_id": None, "sequence": None, "conflict": False}
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
                "generation_id": generation["generation_id"],
                "generation_sequence": generation["sequence"],
                "generation_conflict": generation["conflict"],
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
    codes = [d.get("reason_code") for d in dimensions if d.get("reason_code")]
    if status == STATUS_CONFLICT:
        reason_code = "field_conflict"
    elif status == STATUS_UNRESOLVED:
        reason_code = codes[0] if codes else "unresolved"
    else:
        reason_code = None
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
        "reason_code": reason_code,
        "dimensions": dimensions,
        "left": left,
        "right": right,
        "fact_ids": sorted({fid for fid in (right.get("fact_ids") or [])}),
        "resolver_version": RESOLVER_VERSION,
        "schema_version": RELATION_SCHEMA_VERSION,
    }


def _number_evidence(row):
    """名单编号证据的稳定投影；页码、原文、来源与提取器版本随关系一起保留。"""
    if not row:
        return None
    return {
        "certificate_type": row.get("certificate_type"),
        "number": normalize_certificate_number(row.get("number")),
        "quote": row.get("quote"),
        "context_quote": row.get("context_quote"),
        "material_id": row.get("material_id"),
        "page": row.get("page"),
        "number_source": row.get("number_source"),
        "extractor_version": row.get("extractor_version"),
    }


def _select_number(rows, expected_type=None):
    """同一人员的名单编号证据：优先选岗位应交类别，同类别多编号才判歧义。

    同一人可能同时持有 B/C 证；不同类别本身不互斥，不能因为名单上还有
    另一类证书就把本岗位应交证书降级为 unresolved。
    """
    unique: dict = {}
    for row in rows or []:
        certificate_type = str(row.get("certificate_type") or "").upper()
        number = normalize_certificate_number(row.get("number"))
        if certificate_type in {"A", "B", "C"} and number:
            unique.setdefault((certificate_type, number), row)
    if expected_type:
        same_type = [row for key, row in unique.items() if key[0] == expected_type]
        if len(same_type) == 1:
            return same_type[0], same_type, None
        if len(same_type) > 1:
            return None, same_type, "field_ambiguous"
    if len(unique) == 1:
        return next(iter(unique.values())), list(unique.values()), None
    if len(unique) > 1:
        return None, list(unique.values()), "field_ambiguous"
    return None, [], "field_not_found"


def _generation_info(value):
    """把 service 返回的代次信息或测试里的字符串统一成内部结构。"""
    if isinstance(value, dict):
        return {"generation_id": value.get("generation_id") or None,
                "sequence": value.get("sequence"),
                "conflict": bool(value.get("conflict"))}
    return {"generation_id": value or None, "sequence": None, "conflict": False}


def _evidence_group(generation_id, observations):
    """同一补件代次内的证书证据；同代次冲突才判歧义，不跨代次择一。"""
    identities = set()
    companies = set()
    materials: dict = {}
    for obs in observations:
        certificate_type = str(obs.get("certificate_type") or "").upper()
        number = normalize_certificate_number(obs.get("certificate_number"))
        if certificate_type and number:
            identities.add((certificate_type, number))
        company = a2_check.norm_company(obs.get("company"))
        if company:
            companies.add(company)
        material_id = obs.get("material_id")
        entry = materials.setdefault(material_id, {
            "pages": set(), "quotes": [], "fact_ids": set(), "names": [],
            "companies": [], "qualities": [], "subject_ids": set(),
            "certificate_types": [], "certificate_numbers": [],
        })
        if obs.get("page") is not None:
            entry["pages"].add(obs["page"])
        if obs.get("quote"):
            entry["quotes"].append(obs["quote"])
        entry["fact_ids"].update(obs.get("fact_ids") or [])
        if obs.get("name"):
            entry["names"].append(obs["name"])
        if obs.get("company"):
            entry["companies"].append(obs["company"])
        entry["qualities"].append(obs.get("quality"))
        if obs.get("subject_id") is not None:
            entry["subject_ids"].add(obs["subject_id"])
        if certificate_type:
            entry["certificate_types"].append(certificate_type)
        if obs.get("certificate_number"):
            entry["certificate_numbers"].append(obs["certificate_number"])

    status = "valid"
    reason = None
    if len(identities) > 1:
        status, reason = "ambiguous", "同一代次内存在多份互斥证书（类别或编号不一致）"
    elif len(companies) > 1:
        status, reason = "ambiguous", "同一代次内证书企业名称不一致"
    elif not identities:
        status, reason = "unreadable", "证书编号未识别"

    identity = next(iter(identities)) if len(identities) == 1 else None
    material_evidence = []
    for material_id in sorted(mid for mid in materials if mid is not None):
        entry = materials[material_id]
        material_evidence.append({
            "material_id": material_id,
            "page": min(entry["pages"]) if entry["pages"] else None,
            "pages": sorted(entry["pages"]),
            "quote": next((quote for quote in entry["quotes"] if quote), None),
            "fact_ids": sorted(entry["fact_ids"]),
            "name": next((name for name in entry["names"] if name), None),
            "company": next((company for company in entry["companies"] if company), None),
            "quality": _worst_quality(entry["qualities"]),
            "subject_id": next(iter(entry["subject_ids"])) if len(entry["subject_ids"]) == 1 else None,
            "certificate_type": next((value for value in entry["certificate_types"] if value), None),
            "certificate_number": next((value for value in entry["certificate_numbers"] if value), None),
        })
    sequences = [obs.get("generation_sequence") for obs in observations
                 if obs.get("generation_sequence") is not None]
    return {
        "generation_id": generation_id,
        "sequence": max(sequences) if sequences else None,
        "status": status,
        "reason": reason,
        "identity": identity,
        "certificate_type": identity[0] if identity else None,
        "certificate_number": identity[1] if identity else None,
        "quality": _worst_quality([obs.get("quality") for obs in observations]),
        "subject_ids": sorted({obs.get("subject_id") for obs in observations
                               if obs.get("subject_id") is not None}),
        "materials": material_evidence,
    }


def _select_certificate_authority(certs):
    """按补件代次选权威证书；同代次冲突不猜，最新补件不可读不回退原件。"""
    grouped: dict = {}
    for cert in certs:
        grouped.setdefault(cert.get("generation_id") or None, []).append(cert)
    groups = [_evidence_group(generation_id, observations)
              for generation_id, observations in grouped.items()]
    supplements = [group for group in groups if group["generation_id"] is not None]
    originals = [group for group in groups if group["generation_id"] is None]

    authoritative = None
    primary = None
    issue = None
    reason = None
    generation_conflict = any(cert.get("generation_conflict") for cert in certs)
    if generation_conflict:
        for group in groups:
            group["role"] = "candidate"
        primary = groups[0] if groups else None
        issue, reason = "certificate_ambiguous", "补件代次存在冲突，无法确定权威证据"
    elif supplements:
        supplements.sort(key=lambda group: (group["sequence"] is not None,
                                            group["sequence"] or 0), reverse=True)
        top = supplements[0]
        top_key = (top["sequence"] is not None, top["sequence"] or 0)
        tied = [group for group in supplements
                if (group["sequence"] is not None, group["sequence"] or 0) == top_key]
        primary = top
        if len(tied) > 1:
            issue, reason = "certificate_ambiguous", "补件代次无法排序，不能确定哪一份为准"
        elif top["status"] == "ambiguous":
            issue, reason = "certificate_ambiguous", top["reason"]
        elif top["status"] == "unreadable":
            issue, reason = "supplement_unreadable", "补件证书编号未识别，不能回退原证书判通过"
        else:
            authoritative = top
        for group in groups:
            if group is top:
                group["role"] = ("candidate" if issue == "certificate_ambiguous"
                                 else "authoritative")
            else:
                group["role"] = "superseded"
    else:
        top = originals[0] if originals else None
        primary = top
        if top is None:
            issue, reason = "certificate_not_located", "未找到可唯一归到本人的证书页"
        elif top["status"] == "ambiguous":
            issue, reason = "certificate_ambiguous", top["reason"]
        elif top["status"] == "unreadable":
            issue, reason = "certificate_number_unreadable", "证书编号未识别，无法核对"
        else:
            authoritative = top
        if top is not None:
            top["role"] = "authoritative" if authoritative is top else "candidate"

    evidence = []
    for group in groups:
        for item in group["materials"]:
            evidence.append({
                **item,
                "certificate_type": group["certificate_type"] or item["certificate_type"],
                "certificate_number": group["certificate_number"] or item["certificate_number"],
                "generation_id": group["generation_id"],
                "generation_sequence": group["sequence"],
                "role": group.get("role") or "candidate",
            })
    evidence.sort(key=lambda item: (0 if item["role"] == "authoritative" else 1,
                                    item["material_id"], item["page"] or 0))
    return {"authoritative": authoritative, "primary": primary, "groups": groups,
            "evidence": evidence, "issue": issue, "reason": reason}


def _right_from_authority(authority):
    """把权威代次投影成兼容旧读取侧的 right；per-material 明细放在 certificate_evidence。"""
    primary = authority.get("primary")
    evidence = authority.get("evidence") or []
    if primary is None or not primary.get("materials"):
        return {"certificate_evidence": evidence} if evidence else {}
    first = primary["materials"][0]
    return {
        "material_id": first["material_id"],
        "page": first["page"],
        "pages": list(first["pages"]),
        "quote": first["quote"],
        "name": first["name"],
        "company": first["company"],
        "certificate_type": primary["certificate_type"],
        "certificate_number": primary["certificate_number"],
        "quality": primary["quality"],
        "fact_ids": sorted({fact_id for item in primary["materials"]
                            for fact_id in item["fact_ids"]}),
        "subject_id": (primary["subject_ids"][0]
                       if len(primary["subject_ids"]) == 1 else None),
        "generation_id": primary["generation_id"],
        "generation_sequence": primary["sequence"],
        "certificate_evidence": evidence,
    }


def _candidate_right(observations):
    """无持证人证书线索：保留为 candidate 证据，绝不并入权威证书。"""
    first = observations[0]
    evidence = []
    for obs in observations:
        evidence.append({
            "material_id": obs.get("material_id"),
            "page": obs.get("page"),
            "pages": [obs.get("page")] if obs.get("page") is not None else [],
            "quote": obs.get("quote"),
            "name": obs.get("name"),
            "company": obs.get("company"),
            "certificate_type": obs.get("certificate_type"),
            "certificate_number": obs.get("certificate_number"),
            "quality": obs.get("quality"),
            "fact_ids": sorted(obs.get("fact_ids") or []),
            "generation_id": obs.get("generation_id"),
            "generation_sequence": obs.get("generation_sequence"),
            "role": "candidate",
        })
    return {
        "material_id": first.get("material_id"),
        "page": first.get("page"),
        "pages": [first.get("page")] if first.get("page") is not None else [],
        "quote": first.get("quote"),
        "name": first.get("name"),
        "company": first.get("company"),
        "certificate_type": first.get("certificate_type"),
        "certificate_number": first.get("certificate_number"),
        "quality": first.get("quality"),
        "fact_ids": sorted(first.get("fact_ids") or []),
        "generation_id": first.get("generation_id"),
        "generation_sequence": first.get("generation_sequence"),
        "certificate_evidence": evidence,
    }


def _relation_for(person_key, records, number_row, number_candidates, number_issue,
                  certs, *, material_index, subject_names, unattributed_certs=None):
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
        "number_evidence": _number_evidence(number_row),
        "number_candidates": ([_number_evidence(row) for row in number_candidates]
                              if number_issue == "field_ambiguous" else []),
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
            "同一人员在名单中存在多个岗位记录，不能据此判定应交证书类别",
            reason_code="field_ambiguous"))

    if not certs:
        subject_unattributed = [
            obs for obs in (unattributed_certs or [])
            if (obs.get("subject_id") == subject_id
                or (obs.get("subject_id") is None and subject_id is None))
            and obs.get("certificate_type") and obs.get("certificate_number")
        ]
        if subject_unattributed:
            expected_type = POST_CERT_TYPE.get(post) if post else None
            expected_label = ("交安%s证" % expected_type) if expected_type else "本岗位应交证书"
            types = "、".join(sorted({str(obs.get("certificate_type")) for obs in subject_unattributed}))
            numbers = "、".join(sorted({normalize_certificate_number(obs.get("certificate_number"))
                                        for obs in subject_unattributed
                                        if normalize_certificate_number(obs.get("certificate_number"))}))
            reason = ("存在无法确认持证人的交安 %s 证线索（%s），不能作为%s；请补件或人工核实"
                      % (types, numbers, expected_label))
            dimensions.append(_dimension(
                "certificate.presence", person_name, None, RESULT_UNRESOLVED,
                reason, reason_code="certificate_holder_unconfirmed"))
            right = _candidate_right(subject_unattributed)
            return _relation(person_key, person_name, post, subject_id, left, right, dimensions,
                             reason, STATUS_UNRESOLVED, right.get("generation_id"))
        dimensions.append(_dimension(
            "certificate.presence", person_name, None, RESULT_UNRESOLVED,
            "未找到可唯一归到本人的证书页（未识别姓名或缺失证书页）",
            reason_code="certificate_not_located"))
        return _relation(person_key, person_name, post, subject_id, left, {}, dimensions,
                         "未找到可唯一归到本人的证书页，请补充本人清晰证书", STATUS_UNRESOLVED)

    authority = _select_certificate_authority(certs)
    right = _right_from_authority(authority)
    generation_id = right.get("generation_id")
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
            "；".join(ambiguous) + "，不能仅凭姓名判定为同一人",
            reason_code="subject_unresolved"))
        return _relation(person_key, person_name, post, subject_id, left, right, dimensions,
                         "同名人员分属多个主体，需人工确认名单与证书的对应关系",
                         STATUS_UNRESOLVED, generation_id)

    if authority["issue"]:
        roster_number = normalize_certificate_number((number_row or {}).get("number"))
        if authority["issue"] == "certificate_not_located":
            dimension = _dimension("certificate.presence", person_name, None,
                                   RESULT_UNRESOLVED, authority["reason"],
                                   reason_code="certificate_not_located")
        elif authority["issue"] in {"supplement_unreadable", "certificate_number_unreadable"}:
            dimension = _dimension("certificate.number", roster_number, None,
                                   RESULT_UNRESOLVED, authority["reason"],
                                   reason_code=authority["issue"])
        else:
            dimension = _dimension(
                "certificate.identity", None,
                [{"material_id": item["material_id"], "page": item["page"],
                  "generation_id": item["generation_id"], "role": item["role"],
                  "certificate_type": item["certificate_type"],
                  "certificate_number": item["certificate_number"]}
                 for item in authority["evidence"]],
                RESULT_UNRESOLVED, authority["reason"] or "证书证据无法确认",
                reason_code=authority["issue"])
        dimensions.append(dimension)
        return _relation(person_key, person_name, post, subject_id, left, right, dimensions,
                         authority["reason"] or "证书证据无法确认", STATUS_UNRESOLVED,
                         generation_id)

    cert_name = right.get("name")
    same_name = normalize_person_name(cert_name) == person_key
    dimensions.append(_dimension(
        "person.name", person_name, cert_name,
        RESULT_MATCHED if same_name else RESULT_CONFLICT,
        None,
        expected_ref={"material_id": left.get("material_id"), "page": left.get("page")},
        actual_ref={"material_id": right.get("material_id"), "page": right.get("page")},
        reason_code=None if same_name else "field_conflict"))

    # 主体：证书材料明确归属另一主体时直接冲突，不进入字段比较。
    cert_subject_id = right.get("subject_id")
    if subject_id is not None and cert_subject_id is None:
        dimensions.append(_dimension(
            "subject", subject_names.get(subject_id), None, RESULT_UNRESOLVED,
            "证书材料未归属主体，主体未确认时不能作为本主体的通过证据",
            reason_code="subject_unresolved"))
    elif subject_id is not None and cert_subject_id is not None:
        dimensions.append(_dimension(
            "subject", subject_names.get(subject_id), subject_names.get(cert_subject_id),
            RESULT_MATCHED if subject_id == cert_subject_id else RESULT_CONFLICT,
            None if subject_id == cert_subject_id else "证书材料明确归属另一主体，补件不能证明本主体人员",
            reason_code=None if subject_id == cert_subject_id else "field_conflict"))

    expected_company = subject_names.get(subject_id)
    actual_company = right.get("company")
    if expected_company is None:
        dimensions.append(_dimension("company.name", None, actual_company, RESULT_UNRESOLVED,
                                     "名单侧未确定所属主体，无法核对证书企业",
                                     reason_code="subject_unresolved"))
    elif not actual_company:
        dimensions.append(_dimension("company.name", expected_company, None, RESULT_UNRESOLVED,
                                     "证书企业名称未识别，无法核对是否同主体",
                                     reason_code="field_not_found"))
    else:
        same = a2_check.norm_company(expected_company) == a2_check.norm_company(actual_company)
        dimensions.append(_dimension("company.name", expected_company, actual_company,
                                     RESULT_MATCHED if same else RESULT_CONFLICT,
                                     None if same else "证书企业名称与名单主体不一致",
                                     reason_code=None if same else "field_conflict"))

    expected_type = POST_CERT_TYPE.get(post) if post else None
    actual_type = right.get("certificate_type")
    if post is None:
        dimensions.append(_dimension("certificate.type", None, actual_type, RESULT_UNRESOLVED,
                                     "名单未提供可判定的岗位，不能确定应交证书类别",
                                     reason_code="post_unmapped"))
    elif expected_type is None:
        dimensions.append(_dimension("certificate.type", None, actual_type, RESULT_UNRESOLVED,
                                     "岗位「%s」无对应证书类别规则，需人工判定" % post,
                                     reason_code="post_unmapped"))
    elif actual_type is None:
        dimensions.append(_dimension("certificate.type", expected_type, None, RESULT_UNRESOLVED,
                                     "证书类别未识别，无法证明为交安%s证" % expected_type,
                                     reason_code="extraction_failed"))
    else:
        same = actual_type == expected_type
        dimensions.append(_dimension("certificate.type", expected_type, actual_type,
                                     RESULT_MATCHED if same else RESULT_CONFLICT,
                                     None if same else "岗位应交交安%s证，实际识别为交安%s证"
                                     % (expected_type, actual_type),
                                     reason_code=None if same else "field_conflict"))

    quality = right.get("quality")
    if quality not in (None, "readable"):
        dimensions.append(_dimension(
            "evidence.quality", "readable", quality, RESULT_UNRESOLVED,
            "证书页质量存疑（遮挡/模糊/需复核），字段一致也不能直接作为通过证据",
            actual_ref={"material_id": right.get("material_id"), "page": right.get("page")},
            reason_code="material_unreadable"))

    roster_number = normalize_certificate_number((number_row or {}).get("number"))
    cert_number = normalize_certificate_number(right.get("certificate_number"))
    roster_ref = ({"material_id": number_row.get("material_id"),
                   "page": number_row.get("page"),
                   "quote": number_row.get("quote")} if number_row else None)
    if not cert_number:
        dimensions.append(_dimension("certificate.number", roster_number, None, RESULT_UNRESOLVED,
                                     "证书编号未识别，无法核对",
                                     reason_code="extraction_failed"))
    elif number_issue == "field_ambiguous":
        dimensions.append(_dimension(
            "certificate.number", None,
            [{"certificate_type": row.get("certificate_type"),
              "number": normalize_certificate_number(row.get("number")),
              "material_id": row.get("material_id"), "page": row.get("page")}
             for row in number_candidates],
            RESULT_UNRESOLVED,
            "名单中同一人员存在多个互斥证书编号，不能择一采信",
            reason_code="field_ambiguous"))
    elif not roster_number:
        dimensions.append(_dimension("certificate.number", None, cert_number, RESULT_UNRESOLVED,
                                     "名单未提供可核对的证书编号，需人工核对原件",
                                     reason_code=number_issue or "field_not_found"))
    else:
        same = roster_number == cert_number
        dimensions.append(_dimension("certificate.number", roster_number, cert_number,
                                     RESULT_MATCHED if same else RESULT_CONFLICT,
                                     None if same else "名单与证书编号不一致",
                                     expected_ref=roster_ref,
                                     reason_code=None if same else "field_conflict"))

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
    materials = [m for m in materials if _is_current_material(m)]
    subject_names = subject_names or {}
    material_index = {m.id: m for m in materials}
    records = _roster_index(materials)
    roster_pages = {(r.get("material_id"), r.get("page")) for r in
                    extract_management_roster(materials)}
    observations = _page_observations(materials, facts_by_material, roster_pages,
                                       generation_lookup=generation_lookup)
    number_entries: dict = {}
    for row in roster_certificate_entries(materials):
        key = normalize_person_name(row.get("name"))
        if key:
            number_entries.setdefault(key, []).append(row)
    certs_by_name: dict = {}
    unattributed_certs = []
    for observation in observations:
        key = normalize_person_name(observation.get("name"))
        if key:
            certs_by_name.setdefault(key, []).append(observation)
        else:
            unattributed_certs.append(observation)
    relations = []
    for key in sorted(records):
        posts = {str(r.get("post")).strip() for r in records[key] if r.get("post")}
        post = next(iter(posts)) if len(posts) == 1 else None
        expected_type = POST_CERT_TYPE.get(post) if post else None
        number_row, number_candidates, number_issue = _select_number(
            number_entries.get(key, []), expected_type)
        relations.append(_relation_for(
            key, records[key], number_row, number_candidates, number_issue,
            certs_by_name.get(key, []), unattributed_certs=unattributed_certs,
            material_index=material_index, subject_names=subject_names))
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
        "number_evidence": relation.get("left", {}).get("number_evidence"),
        "reason_code": relation.get("reason_code"),
        "right": [relation.get("right", {}).get("material_id"),
                  relation.get("right", {}).get("page")],
        "generation_id": relation.get("right", {}).get("generation_id"),
        "certificate_evidence": relation.get("right", {}).get("certificate_evidence") or [],
        "fact_ids": sorted(relation.get("fact_ids") or []),
        "status": relation.get("status"),
        "resolver_version": relation.get("resolver_version"),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True)
                          .encode("utf-8")).hexdigest()


def relation_reason_code(relation):
    """从内存关系或 ORM 行取稳定原因码；旧行没有 reason_code 时回退到维度。"""
    if isinstance(relation, dict):
        code = relation.get("reason_code")
        dimensions = relation.get("dimensions") or []
        status = relation.get("status")
    else:
        code = getattr(relation, "reason_code", None)
        dimensions = getattr(relation, "dimensions_json", None) or []
        status = getattr(relation, "status", None)
    if code:
        return code
    codes = [d.get("reason_code") for d in dimensions
             if isinstance(d, dict) and d.get("reason_code")]
    if status == STATUS_CONFLICT:
        return "field_conflict"
    if status == STATUS_UNRESOLVED:
        return codes[0] if codes else "unresolved"
    return None


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
                     .filter(BsMaterial.task_id == task.id,
                             BsMaterial.superseded_by_id.is_(None),
                             BsMaterial.replacement_target_id.is_(None))
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
    """该任务最新一批关系行，按主体与人员取最后一版。

    键必须带主体：同名人员分属不同分包主体时共用同一个 person_key，
    只按姓名收敛会把其中一条关系整条吞掉，A2-05 因此少报一个待核人员。
    """
    rows = (db.query(BsEvidenceRelation)
            .filter(BsEvidenceRelation.task_id == task_id)
            .order_by(BsEvidenceRelation.id)
            .all())
    latest: dict = {}
    for row in rows:
        latest[(row.subject_id, row.person_key)] = row
    return list(latest.values())


__all__ = [
    "RELATION_TYPE", "RESOLVER_VERSION", "STATUSES",
    "STATUS_MATCHED", "STATUS_CONFLICT", "STATUS_UNRESOLVED",
    "POST_CERT_TYPE", "normalize_person_name", "normalize_certificate_number",
    "build_person_certificate_relations", "relation_fingerprint",
    "relation_reason_code", "persist_relations", "resolve_task_relations",
    "latest_relations",
]