# -*- coding: utf-8 -*-
"""A2-04 第二段：资质等级与承包工程范围核对（纯函数，零 LLM）。

判定纪律（口径见 data/qualification_scope.json 的 meta.discipline）：
- 证书载明等级优先：证书写「二级」按旧版阈值核对，写「乙级」按新版阈值核对，
  不做跨版本换算；
- 等级表述不唯一（新旧混载/一页多项）→ 待核实，并定位证书页码；
- 分包合同未载明规模参数（基坑深度、单桩荷载、跨径、公路等级等）→ 待核实，
  不按最宽松档兜底，也不替读者估算；
- 有可量化阈值且实际值超限 → 不符合；指标齐备且均在限内 → 符合；
- 依据原文尚未核实的等级（甲/乙级）一律待核实，不推断统一改革实施日；
  已核的旧版五类范围仅用于对应证书等级，换证与有效期另项核查。

本模块只做结构化判定，不读写数据库；页码与引文由调用方传入的分包合同页、
证照页提供，落库与降级仍由 service._replace_findings 的统一闸门执行。
"""
from __future__ import annotations

import re

from .finding_text import bounded_finding_message
from .models import (STATUS_HIT, STATUS_MISS, STATUS_UNDECIDABLE, BsFinding)
from .rules import load_qualification_scope

KIND = "qualification_match"
_WS_RE = re.compile(r"\s+")
_CERT_WINDOW = 24


def _norm(text) -> str:
    return _WS_RE.sub("", str(text or ""))


def _num(value) -> str:
    number = float(value)
    return ("%d" % number) if number.is_integer() else ("%s" % value)


def _levels_in(text: str, data: dict) -> list[str]:
    hits = []
    for item in data.get("level_patterns") or []:
        if any(token in text for token in item.get("patterns") or []):
            hits.append(item["value"])
    return list(dict.fromkeys(hits))


def extract_parameters(text: str, data: dict | None = None) -> dict:
    """在去空白文本上按词表窗口抽取规模参数；只返回抽到的参数，不补默认值。"""
    data = data or load_qualification_scope()
    found = {}
    for param, spec in (data.get("quantity_patterns") or {}).items():
        candidates = []
        for pattern in spec.get("patterns") or []:
            for match in re.finditer(pattern, text):
                start = max(0, match.start() - 12)
                candidates.append({
                    "value": float(match.group(1)),
                    "unit": spec.get("unit"),
                    "label": spec.get("label") or param,
                    "quote": text[start: match.end() + 4],
                })
        if candidates:
            found[param] = _candidate_fact(candidates)
    return found


def _candidate_fact(candidates: list[dict]) -> dict:
    """保留同字段的候选，不把首个值当成已解决的规模事实。"""
    unique = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    values = list(dict.fromkeys(row["value"] for row in unique))
    return {**unique[0], "ambiguous": len(values) > 1,
            "values": values, "candidates": unique}


def extract_road_grade(text: str, data: dict | None = None) -> dict | None:
    data = data or load_qualification_scope()
    candidates = []
    for item in data.get("road_grade_patterns") or []:
        for pattern in item.get("patterns") or []:
            for match in re.finditer(pattern, text):
                start = max(0, match.start() - 12)
                candidates.append({"value": item["value"], "label": "公路等级",
                                   "quote": text[start: match.end() + 4]})
    return _candidate_fact(candidates) if candidates else None


def _extract_from_pages(material, data: dict) -> tuple[dict, dict | None]:
    params, grade = {}, None
    for page in getattr(material, "pages_json", None) or []:
        text = _norm(page.get("text"))
        if not text:
            continue
        for param, fact in extract_parameters(text, data).items():
            candidates = (params.get(param) or {}).get("candidates", []) + [
                {**row, "page": page.get("page")} for row in fact["candidates"]]
            params[param] = _candidate_fact(candidates)
        fact = extract_road_grade(text, data)
        if fact is not None:
            candidates = (grade or {}).get("candidates", []) + [
                {**row, "page": page.get("page")} for row in fact["candidates"]]
            grade = _candidate_fact(candidates)
    return params, grade


def _scope_category(matcher_category: dict, data: dict) -> dict | None:
    key = str(matcher_category.get("key") or "")
    for category in data.get("categories") or []:
        if key in (category.get("matcher_categories") or []):
            return category
    for category in data.get("categories") or []:
        words = [category["key"]] + list(category.get("aliases") or [])
        if any(word in key for word in words):
            return category
    return None


_QUAL_SECTION_LABELS = ("资质类别及等级", "资质类别", "资质等级")
_QUAL_EXCERPT_RE = re.compile(r"专业承包|资质证书|建筑业企业资质")


def _qualification_section(text: str) -> str:
    for label in _QUAL_SECTION_LABELS:
        index = text.rfind(label)
        if index >= 0:
            return text[index:]
    return ""


def _category_listed(text: str, aliases: list) -> bool:
    """类别必须出现在“资质类别/资质等级”段，或页面本身就是资质摘录。

    营业执照的经营范围也常出现桥梁、桩基等字样，但那不证明资质等级；
    只有资质段或明确的资质证书摘录才进入等级核对。
    """
    section = _qualification_section(text)
    if section:
        return any(alias in section for alias in aliases)
    return bool(_QUAL_EXCERPT_RE.search(text)) and any(alias in text for alias in aliases)


def _certificates_for_category(cert_pages: list, category: dict) -> list[dict]:
    aliases = [category["key"]] + list(category.get("aliases") or [])
    return [page for page in cert_pages or []
            if _category_listed(page.get("text") or "", aliases)]


def parse_level(certificates: list, category: dict, data: dict) -> dict:
    """按「类别词后窗口 → 资质等级字段 → 全页唯一等级」顺序解析等级。"""
    aliases = [category["key"]] + list(category.get("aliases") or [])
    page_levels, window_levels = [], []
    for certificate in certificates:
        text = certificate.get("text") or ""
        for alias in aliases:
            index = text.find(alias)
            if index < 0:
                continue
            window = text[index + len(alias): index + len(alias) + _CERT_WINDOW]
            levels = _levels_in(window, data)
            if levels:
                window_levels.extend(levels)
                break
        field = re.search(r"资质等级\s*[:：]?\s*(一级|二级|三级|甲级|乙级)", text)
        if field:
            page_levels.append(field.group(1))
        else:
            levels = _levels_in(text, data)
            if len(levels) == 1:
                page_levels.append(levels[0])

    levels = list(dict.fromkeys(window_levels or page_levels))
    mode = "category_window" if window_levels else "certificate_field"
    return {"level": levels[0] if len(levels) == 1 else None,
            "levels": levels, "ambiguous": len(levels) > 1, "mode": mode}


def _basis_text(entry: dict, data: dict) -> str:
    basis = next((item for item in data.get("basis") or []
                  if item.get("basis_id") == "JS-2014-159"), {})
    doc_no = basis.get("doc_no") or "建市〔2014〕159号"
    clause = entry.get("basis_clause")
    pages = entry.get("source_pages") or []
    location = "，原PDF物理第%s页" % "、".join(map(str, pages)) if pages else ""
    return "%s%s%s" % (doc_no, " " + clause if clause else "", location)


def _applicable_checks(entry: dict, scope_text: str) -> list:
    checks = list(entry.get("checks") or [])
    matched = [check for check in checks
               if any(word in scope_text for word in (check.get("applies_to") or []))]
    return matched or checks


def _certificate_evidence(certificate: dict) -> dict:
    return {"material_id": certificate.get("material_id"),
            "page": certificate.get("page"),
            "quote": (certificate.get("text") or "")[:240],
            "source_kind": "qualification_certificate"}


def _scope_evidence(material_id, fact: dict) -> dict:
    return {"material_id": material_id, "page": fact.get("page"),
            "quote": fact.get("quote"), "source_kind": "contract_scope"}


def _finding(task_id, *, item_id, source, material_id, status, message,
             page=None, quote=None, evidence=None) -> BsFinding:
    return BsFinding(
        task_id=task_id, material_id=material_id, item_id=item_id, kind=KIND,
        status=status, message=bounded_finding_message(message),
        quote=quote, page=page, evidence_json=evidence, source=source)


def evaluate_scope(*, task_id: int, item_id: str, source: str,
                   scope_categories: list, scope_text: str, scope_material,
                   certificate_pages: list, data: dict | None = None) -> list:
    """对已在第一段识别出的分包类别逐个核对等级与规模覆盖。"""
    data = data or load_qualification_scope()
    findings = []
    params, grade = _extract_from_pages(scope_material, data)
    scope_text = _norm(scope_text)

    for matcher_category in scope_categories or []:
        category = _scope_category(matcher_category, data)
        if category is None:
            # 评审口径：未录入承包工程范围的类别保持“类别匹配、等级待核实”，
            # 不能让报告把“只比了类别”说成“资质符合”。
            key = (matcher_category or {}).get("key") if isinstance(matcher_category, dict) else None
            certificates = _certificates_for_category(
                certificate_pages, {"key": key, "aliases": [key]}) if key else []
            if not certificates:
                continue
            certificate = certificates[0]
            findings.append(_finding(
                task_id=task_id, item_id=item_id, source=source,
                material_id=certificate.get("material_id"), status=STATUS_UNDECIDABLE,
                message="证书载明「%s」，但该类别的等级与承包工程范围依据尚未录入，需人工核对；"
                        "详见证照第%s页" % (key, certificate.get("page")),
                page=certificate.get("page"),
                quote=(certificate.get("text") or "")[:240],
                evidence=[_certificate_evidence(certificate)]))
            continue
        certificates = _certificates_for_category(certificate_pages, category)
        if not certificates:
            # 第一段已就类别覆盖给出结论，这里不重复报同一件事。
            continue
        certificate = certificates[0]
        evidence = [_certificate_evidence(certificate)]
        base = dict(task_id=task_id, item_id=item_id, source=source,
                    material_id=certificate.get("material_id"))
        parsed = parse_level(certificates, category, data)

        if parsed["ambiguous"]:
            findings.append(_finding(
                **base, status=STATUS_UNDECIDABLE,
                message="资质证书就「%s」同时出现%s等级表述，新旧版本或多项资质混载，"
                        "需按证书载明等级人工核对；详见证照第%s页"
                        % (category["key"], "、".join(parsed["levels"]),
                           certificate.get("page")),
                page=certificate.get("page"),
                quote=(certificate.get("text") or "")[:240], evidence=evidence))
            continue
        level = parsed["level"]
        if level is None:
            findings.append(_finding(
                **base, status=STATUS_UNDECIDABLE,
                message="资质证书未载明「%s」的等级表述，需补充载明等级的证书页后核对承包工程范围；"
                        "详见证照第%s页" % (category["key"], certificate.get("page")),
                page=certificate.get("page"),
                quote=(certificate.get("text") or "")[:240], evidence=evidence))
            continue
        if level in (data.get("pending_levels") or []):
            findings.append(_finding(
                **base, status=STATUS_UNDECIDABLE,
                message="证书载明「%s」为%s；%s"
                        % (category["key"], level, data.get("pending_level_reason")
                           or "该等级承包工程范围依据尚未录入"),
                page=certificate.get("page"),
                quote=(certificate.get("text") or "")[:240], evidence=evidence))
            continue

        entry = (category.get("levels") or {}).get(level)
        if entry is None or not entry.get("verified"):
            findings.append(_finding(
                **base, status=STATUS_UNDECIDABLE,
                message="证书载明「%s」为%s，该等级承包工程范围（%s）依据原文尚未归档，"
                        "本次按待核实处理" % (category["key"], level,
                                            entry.get("basis_clause") if entry else "未登记"),
                page=certificate.get("page"),
                quote=(certificate.get("text") or "")[:240], evidence=evidence))
            continue

        basis = _basis_text(entry, data)
        if entry.get("unlimited"):
            findings.append(_finding(
                **base, status=STATUS_MISS,
                message="证书载明「%s」为%s，%s，该等级承包工程范围不受规模限制（依据 %s）"
                        % (category["key"], level, entry.get("scope_text") or "", basis),
                page=certificate.get("page"),
                quote=(certificate.get("text") or "")[:240], evidence=evidence))
            continue

        exceeded, missing, confirmed, conflicts = [], [], [], []
        for check in _applicable_checks(entry, scope_text):
            fact = grade if check.get("kind") == "grade_allowed" else params.get(check.get("param"))
            if fact is not None and fact.get("ambiguous"):
                conflicts.append(check.get("label") or check.get("param"))
                evidence.extend(_scope_evidence(scope_material.id, row)
                                for row in fact["candidates"])
                continue
            if check.get("kind") == "grade_allowed":
                fact = grade
                if fact is None:
                    missing.append(check.get("label") or "公路等级")
                    continue
                if fact["value"] not in (check.get("allowed") or []):
                    exceeded.append("%s为%s，超出该等级可承担的%s"
                                    % (check.get("label"), fact["value"],
                                       "、".join(check.get("allowed") or [])))
                else:
                    confirmed.append("%s为%s" % (check.get("label"), fact["value"]))
                evidence.append(_scope_evidence(scope_material.id, fact))
                continue
            fact = params.get(check.get("param"))
            if fact is None:
                missing.append(check.get("label") or check.get("param"))
                continue
            limit = float(check.get("value"))
            shown = "%s%s" % (_num(fact["value"]), fact.get("unit") or "")
            if fact["value"] > limit:
                exceeded.append("%s为%s，超过该等级上限%s%s"
                                % (check.get("label"), shown, _num(check.get("value")),
                                   fact.get("unit") or ""))
            else:
                confirmed.append("%s为%s" % (check.get("label"), shown))
            evidence.append(_scope_evidence(scope_material.id, fact))

        if exceeded:
            findings.append(_finding(
                **base, status=STATUS_HIT,
                message="分包内容规模超出证书载明的「%s」%s承包工程范围：%s；%s；依据 %s"
                        % (category["key"], level, "；".join(exceeded),
                           entry.get("scope_text") or "", basis),
                page=certificate.get("page"),
                quote=(certificate.get("text") or "")[:240], evidence=evidence))
        elif conflicts:
            findings.append(_finding(
                **base, status=STATUS_UNDECIDABLE,
                message="分包合同的%s存在多个不同候选值，尚不能唯一归属本次分包单项，"
                        "需核对所列原页并明确适用规模后比较承包范围（依据 %s）"
                        % ("、".join(conflicts), basis),
                page=certificate.get("page"),
                quote=(certificate.get("text") or "")[:240], evidence=evidence))
        elif missing:
            findings.append(_finding(
                **base, status=STATUS_UNDECIDABLE,
                message="证书载明「%s」为%s，但分包合同未载明%s，不能比对承包范围上限；"
                        "需补充规模参数后核对（依据 %s）"
                        % (category["key"], level, "、".join(missing), basis),
                page=certificate.get("page"),
                quote=(certificate.get("text") or "")[:240], evidence=evidence))
        else:
            findings.append(_finding(
                **base, status=STATUS_MISS,
                message="证书载明「%s」为%s，分包内容%s未超过该等级承包工程范围（依据 %s）"
                        % (category["key"], level, "、".join(confirmed) or "规模参数",
                           basis),
                page=certificate.get("page"),
                quote=(certificate.get("text") or "")[:240], evidence=evidence))
    return findings


__all__ = ["KIND", "evaluate_scope", "extract_parameters", "extract_road_grade",
           "parse_level"]
