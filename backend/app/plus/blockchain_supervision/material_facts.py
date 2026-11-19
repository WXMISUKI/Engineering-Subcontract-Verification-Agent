"""可追溯的材料字段事实抽取与 append-only 落库。

本模块只消费已经落库的逐页文本，不发起 OCR、旋转或补读请求；抽取结果是候选
事实，不代表业务结论。落库遵循 append-only：事实行写入后不再更新或删除，重新
解析只追加新批次，历史批次保持可读。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date

from sqlalchemy.exc import IntegrityError

from .models import FACT_SCHEMA_VERSION, BsMaterialFact

EXTRACTOR_VERSION = "material-facts.v14"

# 合同字段事实路径：与报告基础事实同源，落库后供跨合同比对和范围画像读取。
# 金额统一万元、税率统一百分数，单位写进路径，避免裸值。
_CONTRACT_FACT_PATHS = {
    "project_name": "contract.project_name",
    "total_project_name": "contract.total_project_name",
    "subcontract_item_name": "contract.item_name",
    "subcontract_scope": "contract.scope",
    "general_contractor": "contract.party.general_contractor",
    "employer": "contract.party.employer",
    "subcontractor": "contract.party.subcontractor",
    "subcontract_amount": "contract.amount.incl_wan",
    "subcontract_amount_excl_tax": "contract.amount.excl_wan",
    "subcontract_tax_rate": "contract.tax_rate_pct",
}

# 白名单：写入数据库前校验，避免把来源或质量写成无法复现的自由文本。
SOURCE_KINDS = frozenset({"online_ocr_text", "local_ocr_text", "embedded_text_layer"})
QUALITY_STATES = frozenset({"readable", "review_required", "unreadable"})

# 姓名标签允许「姓 名」夹同行空格；刻意不接受表格竖线，避免把表头「| 姓名 | 岗位 |」的下一列当成姓名。
_NAME_RE = re.compile(
    r"姓[^\S\r\n]*名[^\S\r\n]*[:：]?[^\S\r\n]*([\u4e00-\u9fff]{2,10})")
# OCR 常把姓名与后续字段连排（「鄢明志性别」「陶瑜系列」），按首个字段边界截断。
_NAME_BOUNDARIES = (
    "性别", "职务", "岗位", "专业", "系列", "身份", "证件", "号码", "年龄", "出生",
    "电话", "备注", "工作单位", "民族", "住址", "签发", "有效期", "等级", "类别",
    "职称", "学历", "证号", "编号", "日期", "籍贯", "状态", "参保", "缴费", "姓名",
)
# 企业标签不止「企业名称」：交安证书原文写作「受聘单位」「单位名称」。
_COMPANY_RE = re.compile(
    r"(?:企业名称|受聘单位|单位名称)[^\S\r\n]*[:：][^\S\r\n]*([^\n|]{2,80})")
_COMPANY_BOUNDARIES = (
    "证书编号", "证号", "有效期", "考核部门", "发证机关", "详细地址", "统一社会信用代码",
    "法定代表人", "身份证", "性别", "出生日期", "民族", "住址", "签发机关", "企业名称", "受聘单位",
)
_CERT_TYPE_RE = re.compile(r"交安\s*([ABC])", re.I)
# 「证号」必须带冒号且前面不是「身份」：身份证号不是证书编号。
_CERT_NUMBER_RE = re.compile(
    r"(?<!身份)(?:证书编号|证号)[^\S\r\n]*[:：][^\S\r\n]*([^\n|]{3,80})")
_CERT_NUMBER_BOUNDARIES = (
    "有效期", "考核部门", "发证机关", "受聘单位", "企业名称", "身份证", "证书编号",
)
_VALIDITY_RE = re.compile(
    r"有效期\s*[:：]?\s*(20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?)"
    r"\s*(?:至|到|—|–|-|~|～)\s*(20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?)"
)


# 页文本常带 LaTeX/OCR 装饰（``$\text { 证书编号: } 0311...$``），只清边界符号不改正文。
_DECORATION = " ：:;；，,。、·<>《》{}[]【】\"'`$￥¥|"


def _trim_at_boundaries(value: str, boundaries) -> str:
    """按字段边界截断连排文本；只做截断和去边界装饰，不改写原值。"""
    text = re.sub(r"<[^>]*>.*$", "", value or "")
    cuts = [text.find(word) for word in boundaries if word and word in text]
    if cuts:
        text = text[:min(cuts)]
    return text.strip(_DECORATION)


def _clean_person_name(raw: str) -> str | None:
    """截断连排字段后仍须是 2-4 字中文姓名，否则视为未识别。"""
    name = _trim_at_boundaries(raw, _NAME_BOUNDARIES)
    return name if 2 <= len(name) <= 4 else None


def _clean_company(raw: str) -> str | None:
    """企业名称只取标签后的主体，不吞掉同行紧邻的证号等字段。"""
    value = _trim_at_boundaries(raw, _COMPANY_BOUNDARIES)
    if len(value) < 2 or value.startswith(("<", "{", '"')):
        return None
    return value


def _clean_certificate_number(raw: str) -> str | None:
    """证书编号保留原始书写（含空格括号），仅去掉相邻字段与 OCR 装饰。"""
    value = _trim_at_boundaries(raw, _CERT_NUMBER_BOUNDARIES)
    # 「证书编号:102591202105000857 2021 06 21」尾随日期是独立字段，不是编号的一部分。
    value = re.sub(r"\s+20\d{2}\s*[-./年]?\s*\d{1,2}\s*[-./月]?\s*\d{1,2}\s*日?\s*$", "", value)
    if len(value) < 3 or re.search(r"[\\$￥{}\"]", value):
        return None
    return value


def _normalize(value: str) -> str:
    """仅做可逆格式归一化，不猜测数字或补齐缺失字符。"""
    return re.sub(r"[\s\u3000()（）【】\[\]{}｛｝]", "", value or "")


def _date_normalize(value: str) -> str:
    value = value.replace("年", "-").replace("月", "-").replace("日", "")
    value = value.replace("/", "-").replace(".", "-")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return _normalize(value)


def _page_source_kind(page: dict) -> str:
    """按页面实际来源判定，不把所有文本都记成线上 OCR。"""
    meta = page.get("ocr_meta")
    if isinstance(meta, dict):
        source = str(meta.get("source") or "")
        if source in {"cloud", "cloud_rotated"}:
            return "online_ocr_text"
        if source == "local":
            return "local_ocr_text"
    return "embedded_text_layer"


def _quality(material, page: dict) -> str:
    page_no = page.get("page")
    for item in getattr(material, "degradations_json", None) or []:
        if not isinstance(item, dict):
            continue
        if item.get("kind") in {"parse_incomplete", "ocr_quality", "ocr_fallback"}:
            message = str(item.get("message") or "")
            if not message or re.search(rf"第\s*{page_no}\s*页", message):
                return "review_required"
    return "readable" if str(page.get("text") or "").strip() else "unreadable"


def _fact(material, page, field_path: str, raw_value: str, *, quote: str | None = None,
          quality: str, confidence=None, normalized_value: str | None = None,
          source_kind: str | None = None) -> dict:
    page_no = page.get("page")
    # 「无页码」统一记 0：唯一索引不允许 NULL（NULL 之间互不相等，会让幂等失效）。
    if page_no is None:
        page_no = 0
    return {
        "material_id": material.id,
        "task_id": getattr(material, "task_id", None),
        "field_path": field_path,
        "raw_value": raw_value,
        "normalized_value": _normalize(raw_value) if normalized_value is None else normalized_value,
        "page": page_no,
        "quote": quote or raw_value,
        "quality": quality,
        "confidence": confidence,
        "source_kind": source_kind or _page_source_kind(page),
        "extractor_version": EXTRACTOR_VERSION,
    }


def _contract_field_facts(material) -> list[dict]:
    """把合同明确标签抽取的事实落成 contract.* 行；只写命中项，不猜缺失。

    与报告基础事实共用同一抽取口径，避免报告、事实表、跨合同比对各存一套
    判据。金额已换算万元、税率以百分数记录，单位为路径的一部分。
    """
    if (getattr(material, "role", None) not in {"general", "sub", "labor"}
            or getattr(material, "parse_state", None) != "done"):
        return []
    from .report_basic_facts import extract_report_basic_facts
    facts = extract_report_basic_facts([material])
    pages = {page.get("page"): page for page in getattr(material, "pages_json", None) or []
             if isinstance(page, dict)}
    output = []
    for key, path in _CONTRACT_FACT_PATHS.items():
        fact = facts.get(key) or {}
        if fact.get("status") != "matched" or fact.get("value") in (None, ""):
            continue
        value = str(fact["value"])
        evidence = fact.get("evidence") or []
        seen = set()
        for row in evidence:
            page_no = row.get("page")
            if type(page_no) is not int or page_no < 1:
                page_no = 0
            identity = (page_no, value)
            if identity in seen:
                continue
            seen.add(identity)
            page = pages.get(page_no) or {"page": page_no, "text": ""}
            output.append(_fact(material, page, path, value,
                                quote=row.get("quote") or value,
                                quality=_quality(material, page)))
        if not evidence:
            page = {"page": 0, "text": ""}
            output.append(_fact(material, page, path, value, quote=value,
                                quality=_quality(material, page)))
    return output


def extract_material_facts(material) -> list[dict]:
    """从已落库页面文本提取可回指原文的字段事实。

    关键字段缺失时不生成伪造的空事实；已识别但质量存疑的字段保留原值并
    标记 ``review_required``，供后续关系解析进入人工队列。
    """
    facts: list[dict] = []
    for page in getattr(material, "pages_json", None) or []:
        if not isinstance(page, dict):
            continue
        text = str(page.get("text") or "")
        if not text.strip():
            continue
        quality = _quality(material, page)
        meta = page.get("ocr_meta") if isinstance(page.get("ocr_meta"), dict) else {}
        confidence = meta.get("confidence")

        type_match = _CERT_TYPE_RE.search(text)
        if type_match:
            facts.append(_fact(material, page, "certificate.type", type_match.group(1),
                               quote=type_match.group(0), quality=quality, confidence=confidence,
                               normalized_value=type_match.group(1).upper()))
        name_match = _NAME_RE.search(text)
        if name_match:
            name = _clean_person_name(name_match.group(1))
            if name:
                facts.append(_fact(material, page, "person.name", name,
                                   quote=name_match.group(0), quality=quality,
                                   confidence=confidence))
        company_match = _COMPANY_RE.search(text)
        if company_match:
            company = _clean_company(company_match.group(1))
            if company:
                facts.append(_fact(material, page, "company.name", company,
                                   quote=company_match.group(0), quality=quality,
                                   confidence=confidence))
        number_match = _CERT_NUMBER_RE.search(text)
        if number_match:
            number = _clean_certificate_number(number_match.group(1))
            if number:
                facts.append(_fact(material, page, "certificate.number", number,
                                   quote=number_match.group(0), quality=quality,
                                   confidence=confidence))
        validity_match = _VALIDITY_RE.search(text)
        if validity_match:
            start, end = validity_match.groups()
            facts.append(_fact(material, page, "certificate.validity.start", start,
                               quote=validity_match.group(0), quality=quality, confidence=confidence,
                               normalized_value=_date_normalize(start)))
            facts.append(_fact(material, page, "certificate.validity.end", end,
                               quote=validity_match.group(0), quality=quality, confidence=confidence,
                               normalized_value=_date_normalize(end)))
    facts.extend(_contract_field_facts(material))
    # 唯一键是材料/批次/字段/页：多人须保存在同页数组，不能反复写person.name。
    if getattr(material, "parse_state", None) == "done" and getattr(material, "role", None):
        from .personnel_evidence import (
            extract_management_roster, extract_person_relation_evidence, extract_person_supplement_evidence,
        )
        from .personnel_certificates import extract_person_certificates
        from .personnel_attendance import extract_attendance_records

        by_page = {}
        records = (extract_management_roster([material]) + extract_person_relation_evidence([material])
                   + extract_person_supplement_evidence([material]) + extract_person_certificates([material])
                   + extract_attendance_records([material]))
        for record in records:
            by_page.setdefault(record.get("page"), []).append(record)
        for page in getattr(material, "pages_json", None) or []:
            if not isinstance(page, dict) or not by_page.get(page.get("page")):
                continue
            records = by_page[page.get("page")]
            value = json.dumps(records, ensure_ascii=False, sort_keys=True)
            facts.append(_fact(material, page, "personnel.records", value,
                               normalized_value=value, quality=_quality(material, page),
                               quote="\n".join(record["quote"] for record in records)))
        from .equipment_identity import extract_equipment_records
        equipment = extract_equipment_records([material])
        for page in getattr(material, "pages_json", None) or []:
            rows = [row for row in equipment if row["page"] == page.get("page")]
            if rows:
                value = json.dumps(rows, ensure_ascii=False, sort_keys=True)
                facts.append(_fact(material, page, "equipment.records", value, normalized_value=value,
                    quality=_quality(material, page), quote="\n".join(row["quote"] for row in rows)))
        from .plan_approval import extract_plan_records
        plans = extract_plan_records([material])
        for page in getattr(material, "pages_json", None) or []:
            rows = [row for row in plans if row["page"] == page.get("page")]
            if rows:
                value = json.dumps(rows, ensure_ascii=False, sort_keys=True)
                facts.append(_fact(material, page, "plan.records", value, normalized_value=value,
                    quality=_quality(material, page), quote="\n".join(row["quote"] for row in rows)))
    from .contract_responsibility import extract_responsibility_records
    responsibilities = extract_responsibility_records([material])
    for page in getattr(material, "pages_json", None) or []:
        if not isinstance(page, dict):
            continue
        rows = [row for row in responsibilities if row["page"] == page.get("page")]
        if rows:
            value = json.dumps(rows, ensure_ascii=False, sort_keys=True)
            facts.append(_fact(material, page, "contract.responsibility_records", value, normalized_value=value,
                quality=_quality(material, page), quote="\n".join(row["quote"] for row in rows)))
    from .training_records import extract_training_records
    training = extract_training_records([material])
    for page in getattr(material, "pages_json", None) or []:
        if not isinstance(page, dict):
            continue
        rows = [row for row in training if row["page"] == page.get("page")]
        if rows:
            value = json.dumps(rows, ensure_ascii=False, sort_keys=True)
            facts.append(_fact(material, page, "training.records", value, normalized_value=value,
                quality=_quality(material, page), quote="\n".join(row["quote"] for row in rows)))
    from .performance_security import extract_security_records
    security = extract_security_records([material])
    for page in getattr(material, "pages_json", None) or []:
        if not isinstance(page, dict):
            continue
        rows = [row for row in security if row["page"] == page.get("page")]
        if rows:
            value = json.dumps(rows, ensure_ascii=False, sort_keys=True)
            facts.append(_fact(material, page, "security.records", value, normalized_value=value,
                quality=_quality(material, page), quote="\n".join(row["quote"] for row in rows)))
    from .contract_changes import extract_change_records
    changes = extract_change_records([material])
    for page in getattr(material, "pages_json", None) or []:
        if not isinstance(page, dict):
            continue
        rows = [row for row in changes if row["page"] == page.get("page")]
        if rows:
            value = json.dumps(rows, ensure_ascii=False, sort_keys=True)
            facts.append(_fact(material, page, "change.records", value, normalized_value=value,
                quality=_quality(material, page), quote="\n".join(row["quote"] for row in rows)))
    return facts


def material_content_sha256(material) -> str:
    """解析输入的内容指纹：页号 + 文本，顺序敏感。"""
    digest = hashlib.sha256()
    for page in getattr(material, "pages_json", None) or []:
        if not isinstance(page, dict):
            continue
        digest.update(str(page.get("page", "")).encode("utf-8"))
        digest.update(b"\x00")
        digest.update(str(page.get("text") or "").encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def default_parse_batch_id(content_sha256: str) -> str:
    """批次身份 = 内容指纹 + 抽取器版本。

    同一内容重复解析落到同一批次（重放幂等）；内容或抽取器变化即新批次，
    旧批次事实保留。
    """
    return f"{content_sha256[:32]}-{EXTRACTOR_VERSION}"[:64]


def _fact_exists(db, material_id, batch_id: str, field_path: str, page: int) -> bool:
    """同一批次键是否已存在；常规去重与并发冲突判定共用同一查询。"""
    return (db.query(BsMaterialFact.id)
            .filter(BsMaterialFact.material_id == material_id,
                    BsMaterialFact.parse_batch_id == batch_id,
                    BsMaterialFact.field_path == field_path,
                    BsMaterialFact.page == page)
            .first()) is not None


def _insert_facts(db, material, facts, *, batch_id: str, generation_id,
                  content_sha256: str) -> list:
    inserted: list = []
    for fact in facts:
        if fact.get("source_kind") not in SOURCE_KINDS:
            raise ValueError(f"未登记的 source_kind: {fact.get('source_kind')!r}")
        if fact.get("quality") not in QUALITY_STATES:
            raise ValueError(f"未登记的 quality: {fact.get('quality')!r}")
        if _fact_exists(db, fact["material_id"], batch_id,
                        fact["field_path"], fact["page"]):
            continue
        row = BsMaterialFact(
            task_id=fact["task_id"],
            material_id=fact["material_id"],
            generation_id=generation_id,
            field_path=fact["field_path"],
            raw_value=fact["raw_value"],
            normalized_value=fact["normalized_value"],
            page=fact["page"],
            quote=fact["quote"],
            quality=fact["quality"],
            confidence=fact["confidence"],
            source_kind=fact["source_kind"],
            parse_batch_id=batch_id,
            schema_version=FACT_SCHEMA_VERSION,
            extractor_version=fact["extractor_version"],
            content_sha256=content_sha256,
            material_version=getattr(material, "revision_no", None),
        )
        try:
            # 逐行 savepoint：并发写入撞唯一索引时只回滚本行插入，不把外层
            # 事务（材料解析结果）标记为失败。
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError:
            # 只把「确认键已存在」的冲突当重复；其他约束错误必须冒泡。
            if not _fact_exists(db, fact["material_id"], batch_id,
                                fact["field_path"], fact["page"]):
                raise
            continue
        inserted.append(row)
    return inserted


def persist_material_facts(db, material, *, generation_id: str | None = None,
                           parse_batch_id: str | None = None) -> list:
    """把抽取结果追加为不可变事实行，返回本次真正新增的行。

    同批次重放不重复插入；已有批次的行不会被更新或删除。写入前查重负责常规
    路径，数据库唯一约束负责并发兜底：冲突按重复处理，但只在确认冲突键已存在
    时吞掉 ``IntegrityError``，其他约束错误照常抛出。
    """
    content_sha256 = material_content_sha256(material)
    batch_id = parse_batch_id or default_parse_batch_id(content_sha256)
    if not batch_id or len(batch_id) > 64:
        raise ValueError("parse_batch_id 长度必须在 1..64 之间")
    return _insert_facts(db, material, extract_material_facts(material),
                         batch_id=batch_id, generation_id=generation_id,
                         content_sha256=content_sha256)


def persist_contract_facts(db, material) -> list:
    """历史合同补写仅追加contract.*，不触发新版人员/证书提取或创建新解析代。"""
    facts = _contract_field_facts(material)
    if not facts:
        return []
    current = latest_material_facts(db, material.id)
    content_sha256 = material_content_sha256(material)
    batch_id = (current[0].parse_batch_id if current else
                content_sha256[:32] + "-contract-facts.v1")
    for fact in facts:
        fact["extractor_version"] = "contract-facts.v1"
    return _insert_facts(db, material, facts, batch_id=batch_id,
                         generation_id=current[0].generation_id if current else None,
                         content_sha256=content_sha256)


def persist_contract_candidates(db, material, result: dict, *,
                                generation_id: str | None = None) -> list:
    """把双路 LLM 候选追加为 ``contract.*.candidate`` 事实。

    候选不改变正式结论，质量固定为 ``review_required``；批次身份包含 prompt/
    schema/模型指纹，模型或提示词变化会生成新批次，旧候选保留可追溯。
    """
    if not isinstance(result, dict) or result.get("available") is not True:
        return []
    version = result.get("version") or {}
    fingerprint = json.dumps(version, ensure_ascii=False, sort_keys=True)
    content_sha256 = material_content_sha256(material)
    batch_id = "%s-%s" % (content_sha256[:24],
                          hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:16])
    # 模型编码必须保留在 80 字符内，便于回答「这条候选由哪个模型产生」。
    # prompt/schema 只取末段短版本，完整值仍随事件 payload 保存。
    prompt_version = str(version.get("prompt_version") or "").rsplit(".", 1)[-1]
    schema_version = str(version.get("schema_version") or "").rsplit(".", 1)[-1]
    extractor = ("%s;model=%s;prompt=%s;schema=%s" % (
        version.get("resolver_version") or "contract-dual.v1",
        version.get("model_code") or "unknown", prompt_version, schema_version))[:80]
    pages = {page.get("page"): page for page in getattr(material, "pages_json", None) or []
             if isinstance(page, dict)}
    facts = []
    for field, row in (result.get("fields") or {}).items():
        evidence = row.get("evidence") or {}
        page_no = evidence.get("page")
        if type(page_no) is not int or page_no < 1:
            page_no = 0
        page = pages.get(page_no) or {"page": page_no, "text": ""}
        value = str(row.get("value") or "")
        if not value:
            continue
        facts.append({
            "material_id": material.id,
            "task_id": getattr(material, "task_id", None),
            "field_path": "contract.%s.candidate" % field,
            "raw_value": value,
            "normalized_value": _normalize(value),
            "page": page_no,
            "quote": evidence.get("quote") or value,
            "quality": "review_required",
            "confidence": row.get("confidence"),
            "source_kind": _page_source_kind(page),
            "extractor_version": extractor,
        })
    return _insert_facts(db, material, facts, batch_id=batch_id,
                         generation_id=generation_id, content_sha256=content_sha256)


def material_fact_batches(db, material_id: int) -> list[str]:
    """该材料的批次标识，按写入顺序（旧→新）。"""
    rows = (db.query(BsMaterialFact.parse_batch_id)
            .filter(BsMaterialFact.material_id == material_id)
            .order_by(BsMaterialFact.id)
            .all())
    seen: list[str] = []
    for (batch_id,) in rows:
        if batch_id not in seen:
            seen.append(batch_id)
    return seen


def latest_material_facts(db, material_id: int) -> list:
    """最新正式解析批次；模型候选及其批次不能替代人员/证照事实。"""
    candidate_batches = (db.query(BsMaterialFact.parse_batch_id)
                         .filter(BsMaterialFact.material_id == material_id,
                                 BsMaterialFact.field_path.like("%.candidate")))
    rows = (db.query(BsMaterialFact.parse_batch_id)
            .filter(BsMaterialFact.material_id == material_id,
                    ~BsMaterialFact.parse_batch_id.in_(candidate_batches))
            .order_by(BsMaterialFact.id).all())
    batches = list(dict.fromkeys(batch_id for (batch_id,) in rows))
    if not batches:
        return []
    return (db.query(BsMaterialFact)
            .filter(BsMaterialFact.material_id == material_id,
                    BsMaterialFact.parse_batch_id == batches[-1])
            .order_by(BsMaterialFact.field_path, BsMaterialFact.page, BsMaterialFact.id)
            .all())


def material_fact_history(db, material_id: int) -> list:
    """全部批次的事实行，按写入顺序；不删除、不覆盖。"""
    return (db.query(BsMaterialFact)
            .filter(BsMaterialFact.material_id == material_id)
            .order_by(BsMaterialFact.id)
            .all())


__all__ = [
    "EXTRACTOR_VERSION", "SOURCE_KINDS", "QUALITY_STATES",
    "extract_material_facts", "material_content_sha256", "default_parse_batch_id",
    "persist_material_facts", "persist_contract_facts", "persist_contract_candidates",
    "material_fact_batches", "latest_material_facts",
    "material_fact_history",
]
