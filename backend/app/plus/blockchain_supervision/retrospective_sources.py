"""冻结倒查文本、条款位置及依据；只读，不触发OCR或改写材料。"""
from __future__ import annotations

import hashlib
import json
import re
from types import SimpleNamespace

from . import basis_validation, parsing

CLAUSE_HEADING = re.compile(r"(?m)^[ \t]*(第[一二三四五六七八九十百零〇\d]+[条章节][^\n]{0,80}|\d{1,2}(?:\.\d{1,2}){1,3}[、.\s：:]?[^\n]{0,80})")


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def infer_project_name(sources):
    """复用显式工程名称抽取；只取正文可读页，冲突不猜测。"""
    from .extract import extract_project_name
    values = {value for source in sources if source.get("relation") == "main"
              for page in source["pages"] if page["readable"]
              if (value := extract_project_name(SimpleNamespace(pages_json=[page])))}
    return next(iter(values)) if len(values) == 1 else None


def freeze_material(material, *, file_sha256=None) -> dict:
    pages = []
    for page in material.pages_json or []:
        number = page.get("page")
        if type(number) is int and number > 0:
            pages.append({"page": number, "text": str(page.get("text") or ""),
                          "readable": parsing.material_page_readable(material, number),
                          "source_kind": page.get("source_kind") or "unknown"})
    numbers = [row["page"] for row in pages]
    if len(numbers) != len(set(numbers)):
        raise ValueError("材料出现重复物理页码，不能形成可靠倒查输入")
    pages.sort(key=lambda row: row["page"])
    coverage = parsing.reading_coverage(material)
    # 文件页数来自PDF解析元数据；旧文本记录有最大页码时也不掩盖缺页。
    total = max(getattr(material, "pages", 0) or 0, coverage.get("total_pages") or 0,
                max(numbers, default=0))
    return {"material_id": material.id, "file_name": material.file_name, "role": material.role,
            "subject_id": getattr(material, "subject_id", None),
            "revision_no": getattr(material, "revision_no", None),
            "file_sha256": file_sha256, "parse_state": material.parse_state,
            "total_pages": total, "pages": pages, "reading_coverage": coverage}


def clause_index(source: dict) -> list[dict]:
    """跨页延续同一条款，片段仍保持物理页与偏移，不拼造原文。"""
    clauses, current = [], None
    for page in source["pages"]:
        if not page["readable"] or not page["text"].strip():
            current = None  # 不越过未读页假装条款连续。
            continue
        text = page["text"]
        marks = list(CLAUSE_HEADING.finditer(text))
        boundaries = sorted(set([0, len(text)] + [match.start() for match in marks]))
        headings = {match.start(): match.group(1).strip() for match in marks}
        for start, end in zip(boundaries, boundaries[1:]):
            if not text[start:end].strip():
                continue
            if start in headings or current is None:
                current = {"clause_id": f"{source['material_id']}:{page['page']}:{start}",
                           "title": headings.get(start, "未标明条款编号的正文"), "spans": []}
                clauses.append(current)
            current["spans"].append({"material_id": source["material_id"], "page": page["page"],
                                     "start": start, "end": end, "text": text[start:end]})
    return clauses


def analysis_batches(sources: list[dict], *, max_chars=10000, overlap_chars=1600) -> list[dict]:
    """覆盖每一可读片段；跨批带有限前文，并保留全合同条款目录供跨段召回。"""
    if max_chars < 100 or overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError("倒查分块预算无效")
    result, previous = [], []
    for source in sources:
        current, size = [], 0
        for clause in clause_index(source):
            for span in clause["spans"]:
                for offset in range(0, len(span["text"]), max_chars):
                    part = {**span, "text": span["text"][offset:offset + max_chars],
                            "start": span["start"] + offset, "clause": clause["title"]}
                    part["end"] = part["start"] + len(part["text"])
                    if current and size + len(part["text"]) > max_chars:
                        result.append({"parts": current, "context": previous})
                        previous = _tail(current, overlap_chars)
                        current, size = [], 0
                    current.append(part)
                    size += len(part["text"])
        if current:
            result.append({"parts": current, "context": previous})
        previous = []  # 附件是独立来源，不能把两份文件拼成一段原文。
    return [{"batch_id": digest(row), **row} for row in result]


def _tail(parts, budget):
    output = []
    for part in reversed(parts):
        if budget <= 0:
            break
        text = part["text"][-budget:]
        output.insert(0, {**part, "text": text, "start": part["end"] - len(text)})
        budget -= len(text)
    return output


def original_citation(ref: dict, sources: list[dict]) -> dict | None:
    """只忽略空白做匹配，并返回来源中的原始字节文本和位置；不替换错字/数字。"""
    mid, number, quote = (ref.get(key) for key in ("material_id", "page", "quote"))
    if type(mid) is not int or type(number) is not int or not isinstance(quote, str):
        return None
    needle = re.sub(r"\s+", "", quote)
    if len(needle) < 4:
        return None
    for source in sources:
        if source["material_id"] != mid:
            continue
        for page in source["pages"]:
            if page["page"] != number or not page["readable"]:
                continue
            text = page["text"]
            positions = [index for index, char in enumerate(text) if not char.isspace()]
            compact = "".join(text[index] for index in positions)
            at = compact.find(needle)
            if at < 0:
                return None
            start, end = positions[at], positions[at + len(needle) - 1] + 1
            clause = next((row["title"] for row in clause_index(source)
                           if any(span["page"] == number and span["start"] <= start < span["end"]
                                  for span in row["spans"])), "未标明条款编号的正文")
            marker = re.match(r"(?:第[一二三四五六七八九十百零〇\d]+[条章节]|\d{1,2}(?:\.\d{1,2}){1,3})", clause)
            if marker:
                clause = marker.group()
            return {"material_id": mid, "file_name": source["file_name"], "page": number,
                    "quote": text[start:end], "clause": clause, "start": start, "end": end}
    return None


def reading_limitations(sources: list[dict]) -> list[str]:
    limitations = []
    for source in sources:
        readable = {row["page"] for row in source["pages"] if row["readable"] and row["text"].strip()}
        blanks = set(source["reading_coverage"].get("blank_pages") or [])
        gaps = sorted(set(range(1, source["total_pages"] + 1)) - readable - blanks)
        if gaps or not readable or not source["reading_coverage"].get("reading_complete"):
            suffix = "第" + "、".join(map(str, gaps)) + "页" if gaps else "部分页面"
            limitations.append(f"《{source['file_name']}》{suffix}未取得可靠正文，本次不据此判断条款遗漏或合同全部合规。")
    return limitations


def freeze_basis() -> list[dict]:
    """只取现有正式依据库；不接暂停专项或文件中的开发指令。"""
    basis = basis_validation.load_review_basis()
    library = basis_validation.load_basis_library()
    result = []
    for clause in basis_validation.clause_texts(library).values():
        doc_id = clause.get("doc_id") or clause["clause_id"].split("#", 1)[0]
        doc = (basis.get("documents") or {}).get(doc_id) or {}
        if (doc.get("document_role") != "full_text" or not clause.get("text")
                or doc_id == "MOF-2022-136-ARCHIVE"):
            continue
        result.append({"clause_id": clause["clause_id"], "text": clause["text"],
                       "marker": clause.get("marker") or "", "doc_id": doc_id,
                       "document": doc, "human_verified": clause.get("human_verified") is True,
                       "page_start": clause.get("page_start") or clause.get("page_physical"),
                       "page_end": clause.get("page_end") or clause.get("page_physical")})
    return result


def infer_contract_date(sources) -> tuple[str | None, list[str]]:
    """仅接受明确日期标签；冲突不选首值，不把开工日当签约日。"""
    from datetime import date
    values = set()
    for source in sources:
        if source.get("relation", "main") != "main":
            continue
        for page in source["pages"]:
            if not page["readable"]:
                continue
            for match in re.finditer(r"(?:签订日期|签约日期|签署日期|合同签订时间)\s*[:：]?\s*(20\d{2})[年./-]\s*(\d{1,2})[月./-]\s*(\d{1,2})日?", page["text"]):
                try:
                    values.add(date(*map(int, match.groups())).isoformat())
                except ValueError:
                    pass
    if len(values) == 1:
        return values.pop(), []
    return None, ["未取得唯一可靠的合同签订日期，历史适用依据不作确定性定性；现行提示单列。"]
