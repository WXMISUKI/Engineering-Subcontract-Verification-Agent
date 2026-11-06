"""可追溯的材料字段事实抽取与 append-only 落库。

本模块只消费已经落库的逐页文本，不发起 OCR、旋转或补读请求；抽取结果是候选
事实，不代表业务结论。落库遵循 append-only：事实行写入后不再更新或删除，重新
解析只追加新批次，历史批次保持可读。
"""

from __future__ import annotations

import hashlib
import re
from datetime import date

from sqlalchemy.exc import IntegrityError

from .models import FACT_SCHEMA_VERSION, BsMaterialFact

EXTRACTOR_VERSION = "material-facts.v1"

# 白名单：写入数据库前校验，避免把来源或质量写成无法复现的自由文本。
SOURCE_KINDS = frozenset({"online_ocr_text", "local_ocr_text", "embedded_text_layer"})
QUALITY_STATES = frozenset({"readable", "review_required", "unreadable"})

_NAME_RE = re.compile(r"姓名[^\S\r\n]*[:：]?[^\S\r\n]*([\u4e00-\u9fff]{2,4})")
_COMPANY_RE = re.compile(r"企业名称[^\S\r\n]*[:：]?([^\n|]{2,80})")
_CERT_TYPE_RE = re.compile(r"交安\s*([ABC])", re.I)
_CERT_NUMBER_RE = re.compile(r"(?:证书编号|证号)\s*[:：]?\s*([^\n|]{3,80})")
_VALIDITY_RE = re.compile(
    r"有效期\s*[:：]?\s*(20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?)"
    r"\s*(?:至|到|—|–|-|~|～)\s*(20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}日?)"
)


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
            facts.append(_fact(material, page, "person.name", name_match.group(1),
                               quote=name_match.group(0), quality=quality, confidence=confidence))
        company_match = _COMPANY_RE.search(text)
        if company_match:
            raw = company_match.group(1).strip(" ：:;；，,")
            facts.append(_fact(material, page, "company.name", raw,
                               quote=company_match.group(0), quality=quality, confidence=confidence))
        number_match = _CERT_NUMBER_RE.search(text)
        if number_match:
            raw = number_match.group(1).strip(" ：:;；，,")
            facts.append(_fact(material, page, "certificate.number", raw,
                               quote=number_match.group(0), quality=quality, confidence=confidence))
        validity_match = _VALIDITY_RE.search(text)
        if validity_match:
            start, end = validity_match.groups()
            facts.append(_fact(material, page, "certificate.validity.start", start,
                               quote=validity_match.group(0), quality=quality, confidence=confidence,
                               normalized_value=_date_normalize(start)))
            facts.append(_fact(material, page, "certificate.validity.end", end,
                               quote=validity_match.group(0), quality=quality, confidence=confidence,
                               normalized_value=_date_normalize(end)))
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

    inserted: list = []
    for fact in extract_material_facts(material):
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
    """最新批次的事实行；用于业务读取，历史批次仍可单独查询。"""
    batches = material_fact_batches(db, material_id)
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
    "persist_material_facts", "material_fact_batches", "latest_material_facts",
    "material_fact_history",
]
