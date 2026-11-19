# -*- coding: utf-8 -*-
"""证照页字段事实抽取。

只读取已落库的逐页文本，不发起 OCR、不补读、不生成业务结论。
输出仅用于报告“已核实事实”段落，字段缺失时保持为空，由报告写“未识别”。
"""
from __future__ import annotations

import re
from datetime import date

_JSON_FIELD = r'"%s"\s*:\s*"(?:<[^>]*>,)?([^"]*)"'
_LABEL_FIELD = r"(?:^|\n)\s*%s\s*[:：]\s*([^\n|]+)"
_INLINE_FIELD = r"%s\s*[:：]\s*([^\n|]+)"
_DECORATION = " ：:;；，,。、·<>《》{}[]【】\"'`$￥¥|"
_VALUE_CUT = re.compile(
    r"\s*(?:有效期至|有效期限|许可证有效日期|发证日期|发证机关|备案编号|证书编号|"
    r"许可证编号|安全生产许可证编号|统一社会信用代码|法定代表人|法人/负责人|"
    r"企业名称|营业名称|详细地址|住所|净资产|经济性质|许可范围|备注)\s*[:：]"
)
_DATE_RE = re.compile(r"(\d{4})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})")


def _clean(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"<[^>]*>", "", text)
    return text.strip(_DECORATION)


def _trim(value: str) -> str:
    """截断同一行后续字段，避免证书编号把“有效期至”一起吞掉。"""
    return _VALUE_CUT.split(str(value or ""), 1)[0]


def _normalize_date(value: str) -> str:
    """取文本中最后一个日期并归一为 YYYY-MM-DD（有效期区间取截止日）。"""
    matches = _DATE_RE.findall(str(value or ""))
    if not matches:
        return ""
    year, month, day = matches[-1]
    try:
        return date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        return ""


def _field(text: str, *keys: str) -> str:
    for key in keys:
        for pattern in (_JSON_FIELD, _LABEL_FIELD, _INLINE_FIELD):
            match = re.search(pattern % re.escape(key), text)
            if not match:
                continue
            value = _clean(_trim(match.group(1)))
            if value:
                return value
    return ""


def _pages(material):
    pages = getattr(material, "pages_json", None) or []
    for page in pages:
        if not isinstance(page, dict):
            continue
        text = page.get("text") or page.get("content") or ""
        if text:
            yield page.get("page"), str(text)


def _html_cell(text: str, label: str) -> str:
    match = re.search(
        r"<td>\s*%s\s*[:：]?\s*</td>\s*<td>\s*([^<]+?)\s*</td>" % re.escape(label), text)
    return _clean(match.group(1)) if match else ""


def _merge_facts(current: dict, candidate: dict) -> dict:
    if not current:
        return candidate
    current_score = sum(bool(value) for key, value in current.items()
                        if key not in {"page", "file_name"})
    candidate_score = sum(bool(value) for key, value in candidate.items()
                          if key not in {"page", "file_name"})
    base, extra = (candidate, current) if candidate_score > current_score else (current, candidate)
    for key, value in extra.items():
        if value and not base.get(key):
            base[key] = value
    return base


def _document_type(title: str, text: str) -> str:
    sample = (title or "") + "\n" + text[:300]
    if "安全生产许可证" in sample:
        return "safety_license"
    if "资质证书" in sample or ("资质类别" in sample and "资质等级" in sample):
        return "qualification"
    if "营业执照" in sample:
        return "business_license"
    return ""


def _business_license(text, page, file_name):
    return {
        "page": page,
        "file_name": file_name,
        "name": _field(text, "营业名称", "企业名称", "名称"),
        "uscc": _field(text, "统一社会信用代码"),
        "legal_rep": _field(text, "法人/负责人", "法定代表人", "法人"),
        "registered_capital": _field(text, "注册资本"),
        "business_scope": _field(text, "经营范围"),
        "address": _field(text, "营业场所/住所", "住所", "地址"),
        "registered_at": (_normalize_date(_field(text, "注册日期", "成立日期"))
                          or _field(text, "注册日期", "成立日期")),
    }


def _qualification(text, page, file_name):
    if "施工劳务备案证书" in text:
        title, category = "建筑业企业施工劳务备案证书", "施工劳务备案"
    elif "施工劳务资质" in text:
        title, category = "施工劳务资质证书", "施工劳务"
    else:
        title = _field(text, "证照标题", "证书标题") or "建筑业企业资质证书"
        category = _field(text, "资质类别及等级", "资质类别", "承包工程范围") \
            or _html_cell(text, "资质类别")
    level = _field(text, "资质等级") or _html_cell(text, "资质等级")
    cert_no = _field(text, "证书编号", "证号") or _html_cell(text, "证书编号")
    valid_until = _normalize_date(_field(text, "有效期至", "有效期限", "有效期"))
    return {
        "page": page,
        "file_name": file_name,
        "title": title,
        "name": _field(text, "营业名称", "企业名称", "名称"),
        "uscc": _field(text, "统一社会信用代码"),
        "category": category,
        "level": level,
        "cert_no": cert_no,
        "valid_until": valid_until,
    }


def _safety_license(text, page, file_name, *, allow_business_scope=True):
    valid_until = _normalize_date(_field(
        text, "许可证有效日期", "有效期至", "有效期限", "营业期限", "有效期"))
    cert_no = (_field(text, "安全生产许可证编号", "许可证编号", "证书编号", "证号")
               or _html_cell(text, "安全生产许可证号"))
    return {
        "page": page,
        "file_name": file_name,
        "title": "安全生产许可证",
        "name": _field(text, "营业名称", "企业名称", "名称"),
        "uscc": _field(text, "统一社会信用代码"),
        "scope": _field(text, *( ("许可范围", "经营范围") if allow_business_scope else ("许可范围",) )) or _html_cell(text, "许可范围"),
        "cert_no": cert_no,
        "valid_until": valid_until,
    }


def extract_safety_permit_page(text, page, file_name):
    """独立安许原页段事实：许可范围不借营业执照经营范围，调用方负责证照形态。"""
    facts = _safety_license(text, page, file_name, allow_business_scope=False)
    return facts if _has_substantive_fields("safety_license", facts) else {}


def _has_substantive_fields(kind: str, facts: dict) -> bool:
    """证照页须读到可核对字段才算“已提供”，仅出现名称/条款/类别不算。

    比广场版更严格：资质和安全许可必须有证号或有效期，避免合同条款里的
    “施工劳务备案证书”“许可范围”被当成证照页。
    """
    if kind == "business_license":
        return bool(facts.get("uscc") or facts.get("business_scope") or facts.get("legal_rep"))
    if kind in ("qualification", "safety_license"):
        return bool(facts.get("cert_no") or facts.get("valid_until"))
    return False


def extract_certificate_facts(materials) -> dict:
    """按“营业执照 / 资质证书 / 安全生产许可证”归并字段，优先保留信息更完整的一页。

    ``qualification`` 保留信息最完整的一条（兼容既有报告）；``qualifications``
    按证书页保留多条，供“主资质 + 配套资质”展示，不把同页重复记录算成多条。
    """
    result = {"business_license": {}, "qualification": {}, "safety_license": {}, "qualifications": []}
    for material in materials or []:
        file_name = getattr(material, "file_name", "") or ""
        for page, text in _pages(material):
            title = _field(text, "证照标题", "证照类型", "证书标题")
            kind = _document_type(title, text)
            if kind == "business_license":
                candidate = _business_license(text, page, file_name)
            elif kind == "qualification":
                candidate = _qualification(text, page, file_name)
                if not _has_substantive_fields(kind, candidate):
                    continue
                identity = (candidate.get("cert_no"), candidate.get("category"), candidate.get("level"))
                if any(identity) and not any(
                        (existing.get("cert_no"), existing.get("category"), existing.get("level")) == identity
                        for existing in result["qualifications"]):
                    result["qualifications"].append(candidate)
            elif kind == "safety_license":
                candidate = _safety_license(text, page, file_name)
            else:
                continue
            # 合同条款/清单里提到证照名称，不等于提供了证照页；无可核对字段不入库。
            if kind != "qualification" and not _has_substantive_fields(kind, candidate):
                continue
            result[kind] = _merge_facts(result.get(kind) or {}, candidate)

    # 安许编号/有效期常在证照页之外的单页出现，做一次补充归并。
    safety = result.get("safety_license") or {}
    if safety:
        for material in materials or []:
            file_name = getattr(material, "file_name", "") or ""
            for page, text in _pages(material):
                if "安全生产许可证" not in text:
                    continue
                cert_no = (_field(text, "安全生产许可证编号", "许可证编号", "证书编号", "证号")
                           or _html_cell(text, "安全生产许可证号"))
                valid_until = _normalize_date(_field(
                    text, "许可证有效日期", "有效期至", "有效期限", "营业期限", "有效期"))
                if cert_no and not safety.get("cert_no"):
                    safety["cert_no"] = cert_no
                if valid_until and not safety.get("valid_until"):
                    safety["valid_until"] = valid_until
                if not safety.get("file_name"):
                    safety["file_name"] = file_name
                    safety["page"] = page
    return result


def qualification_evidence(facts: dict) -> dict:
    """把证照事实投影成 W16-01 所需的三侧资格证据（只表示“已提供”）。"""
    facts = facts or {}
    license_facts = facts.get("business_license") or {}
    qualification = facts.get("qualification") or {}
    safety = facts.get("safety_license") or {}
    return {
        "business_license": bool(
            license_facts.get("name") or license_facts.get("uscc") or license_facts.get("page")),
        "qualification": bool(
            qualification.get("cert_no") or qualification.get("valid_until")
            or qualification.get("category") or qualification.get("page")),
        "safety_permit": bool(
            safety.get("cert_no") or safety.get("valid_until")
            or safety.get("scope") or safety.get("page")),
    }
