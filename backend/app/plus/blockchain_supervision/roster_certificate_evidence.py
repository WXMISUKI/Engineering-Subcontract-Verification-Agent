"""名单交安证书编号的通用证据提取（A/B/C）。

本模块只回答“名单上的某个人对应哪个交安证书编号”，不判断任何检查项的
业务适用范围。A2-05 是否只关心“项目经理 + B 证”，由投影层自行筛选。

输出契约稳定为：姓名、岗位、证书类别、规范化编号、原文、续行上下文、
材料/页码、来源（布局或文本表格）、提取器版本。取不到就返回空，不猜。
"""
from __future__ import annotations

import re
import unicodedata

from .parsing import material_reading_gap, reading_coverage
from .personnel_evidence import extract_management_roster

EXTRACTOR_VERSION = "roster-cert.v1"

# 交安 A/B/C 证编号：沪交安 B（25）G00206 / 沪交安C(22)G00822
_NUMBER = re.compile(
    r"[\u4e00-\u9fff]交安\s*([ABC])\s*[(（]?\s*\d{2}\s*[)）]?\s*G\s*\d{5}(?![A-Za-z0-9])",
    re.I,
)

# 名单职务列可能出现的岗位；这里只用于定位表格行，不用于推导证书类别。
ROSTER_POSTS = frozenset({
    "项目经理", "项目负责人", "项目副经理", "项目总工", "总工程师", "技术负责人",
    "安全员", "专职安全员", "安全负责人", "专职安全生产管理人员",
    "企业负责人", "企业主要负责人", "法定代表人",
    "材料员", "资料员", "标准员", "质量员", "施工员", "劳务员", "机械员",
    "预算员", "测量员", "试验员",
})


def _nfkc(value) -> str:
    return unicodedata.normalize("NFKC", str(value or ""))


def normalize_number(value):
    """返回 ``(规范化编号, 证书类别)``；不匹配时返回 ``(None, None)``。"""
    text = _nfkc(value).strip()
    match = _NUMBER.fullmatch(text)
    if not match:
        return None, None
    normalized = re.sub(r"[\s()]", "", match.group()).upper()
    return normalized, match.group(1).upper()


def extract_numbers(text):
    """提取文本中的全部交安证书编号，返回 ``(编号, 原文, 类别)``。

    ``原文`` 保持输入中的实际字符（含全角括号/空格），规范化只用于比较。
    """
    rows = []
    for match in _NUMBER.finditer(str(text or "")):
        raw = match.group()
        normalized = re.sub(r"[\s()（）]", "", _nfkc(raw)).upper()
        rows.append((normalized, raw, match.group(1).upper()))
    return rows


def _blocks(page):
    meta = page.get("ocr_meta") or {}
    if not isinstance(meta, dict) or meta.get("quality") != "ocr_ok":
        return []
    result = []
    for block in meta.get("layout") or []:
        if not isinstance(block, dict) or not isinstance(block.get("text"), str):
            continue
        box = block.get("bbox")
        if (not isinstance(box, (list, tuple)) or len(box) != 4
                or any(type(v) not in (int, float) for v in box)
                or box[2] <= box[0] or box[3] <= box[1]):
            continue
        result.append((block["text"].strip(), box))
    return result


def page_readable(material, page) -> bool:
    """与 A2-05 既有口径一致：解析缺口、疑似页、OCR 失败页均不算可读。"""
    gap = material_reading_gap(material)
    if gap is None:
        return True
    if not gap:
        return False
    coverage = reading_coverage(material)
    failed = {number for record in getattr(material, "degradations_json", None) or []
              if isinstance(record, dict) and record.get("kind") == "parse_incomplete"
              for number in record.get("ocr_failed_pages") or [] if type(number) is int}
    return page.get("page") not in set(gap) | set(coverage["suspect_pages"]) | failed


def _entry(material, page, *, name, post, number, raw, certificate_type,
           number_source, quote, context_quote=None):
    return {
        "name": name,
        "post": post,
        "certificate_type": certificate_type,
        "number": number,
        "quote": quote or raw,
        "context_quote": context_quote,
        "raw": raw,
        "material_id": material.id,
        "page": page.get("page"),
        "number_source": number_source,
        "extractor_version": EXTRACTOR_VERSION,
    }


def layout_certificate_entries(materials) -> list[dict]:
    """从带坐标的名单表格按行列对齐提取编号；任一姓名/岗位/编号不唯一即放弃该行。"""
    rows = []
    for material in materials:
        for page in material.pages_json or []:
            if not page_readable(material, page):
                continue
            text = page.get("text") or ""
            if "名单" not in text:
                continue
            blocks = _blocks(page)
            headers = {name: [b for t, b in blocks if t == name] for name in ("姓名", "职务")}
            cert_headers = [b for t, b in blocks if "持证" in t and "证号" in t]
            if any(len(v) != 1 for v in headers.values()) or len(cert_headers) != 1:
                continue
            name_head, role_head, cert_head = headers["姓名"][0], headers["职务"][0], cert_headers[0]
            if not (name_head[2] < role_head[0] and role_head[2] < cert_head[0]):
                continue
            role_blocks = sorted(
                [(t, b) for t, b in blocks if t in ROSTER_POSTS and b[1] > role_head[3]
                 and name_head[2] < (b[0] + b[2]) / 2 < cert_head[0]],
                key=lambda v: (v[1][1] + v[1][3]) / 2,
            )
            for index, (role, box) in enumerate(role_blocks):
                y = (box[1] + box[3]) / 2
                upper = ((y + (role_blocks[index + 1][1][1] + role_blocks[index + 1][1][3]) / 2) / 2
                         if index + 1 < len(role_blocks) else box[3])
                name_hits = [(t, b) for t, b in blocks
                             if re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", t)
                             and name_head[0] <= (b[0] + b[2]) / 2 <= name_head[2]
                             and min(b[3], box[3]) - max(b[1], box[1]) > 0]
                number_hits = [(num, raw, ctype) for t, b in blocks
                               if cert_head[0] - (cert_head[2] - cert_head[0]) <= b[0] <= cert_head[2]
                               and box[1] <= (b[1] + b[3]) / 2 < upper
                               for num, raw, ctype in extract_numbers(t)]
                if len(name_hits) == len(number_hits) == 1:
                    name = name_hits[0][0]
                    number, raw, ctype = number_hits[0]
                    if name in text and _nfkc(raw) in _nfkc(text):
                        rows.append(_entry(
                            material, page, name=name, post=role, number=number, raw=raw,
                            certificate_type=ctype, number_source="layout", quote=raw,
                        ))
    return rows


def text_numbers_for_fact(page, fact):
    """名单行与空姓名续行中的全部编号；返回 ``(编号, 原文, 类别, 原文行)``。

    主行与续行都要扫描：同一人可能主行写 B 证、续行写 C 证，不能因为主行
    已命中就跳过续行。按 ``(编号, 类别)`` 去重，保留首次出现的原文行。
    """
    found = []
    seen = set()

    def add(number, raw, ctype, quote):
        key = (number, ctype)
        if key not in seen:
            seen.add(key)
            found.append((number, raw, ctype, quote))

    for number, raw, ctype in extract_numbers(fact.get("quote") or ""):
        add(number, raw, ctype, fact.get("quote") or raw)
    # 识别表格可能把持证信息拆成空姓名续行，且列发生偏移；只收本行之后的连续续行。
    lines = (page.get("text") or "").splitlines()
    starts = []
    for index, line in enumerate(lines):
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 3 and cells[1:3] == [fact["name"], fact["post"]]:
            starts.append(index)
    if len(starts) != 1:
        return found
    for line in lines[starts[0] + 1:]:
        if "|" not in line:
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3 or cells[0] or cells[1]:
            break
        for number, raw, ctype in extract_numbers(line):
            add(number, raw, ctype, line.strip())
    return found


def text_certificate_entries(materials) -> list[dict]:
    """从文本表格（含空姓名续行）提取编号；同一人多个编号全部保留，由关系层判歧义。"""
    by_id = {material.id: material for material in materials}
    rows = []
    for fact in extract_management_roster(materials):
        material = by_id.get(fact.get("material_id"))
        if material is None:
            continue
        page = next((p for p in material.pages_json or []
                     if isinstance(p, dict) and p.get("page") == fact.get("page")), None)
        if page is None or not page_readable(material, page):
            continue
        for number, raw, ctype, quote in text_numbers_for_fact(page, fact):
            rows.append(_entry(
                material, page, name=fact.get("name"), post=fact.get("post"),
                number=number, raw=raw, certificate_type=ctype,
                number_source="text_table", quote=quote,
                context_quote=fact.get("quote"),
            ))
    return rows


def roster_certificate_entries(materials) -> list[dict]:
    """全部可定位的名单编号证据；同一材料/人员/类别/编号去重，保留首个来源。"""
    rows = layout_certificate_entries(materials) + text_certificate_entries(materials)
    seen = set()
    result = []
    for row in rows:
        key = (row.get("material_id"), _nfkc(row.get("name")).strip(),
               row.get("certificate_type"), row.get("number"))
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


__all__ = [
    "EXTRACTOR_VERSION", "ROSTER_POSTS", "normalize_number", "extract_numbers",
    "page_readable", "layout_certificate_entries", "text_certificate_entries",
    "text_numbers_for_fact",
    "roster_certificate_entries",
]
