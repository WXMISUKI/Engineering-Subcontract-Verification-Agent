"""冻结倒查文本、条款位置及依据；只读，不触发OCR或改写材料。"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace
from collections import Counter

from . import basis_validation, parsing

CLAUSE_HEADING = re.compile(r"(?m)^[ \t]*(第[一二三四五六七八九十百零〇\d]+[条章节][^\n]{0,80}|\d{1,2}(?:\.\d{1,2}){1,3}[、.\s：:]?[^\n]{0,80})")


def repetitive_heading_evidence(quote):
    """高度重复的编号标题不能证明两项实质约定矛盾；不改写或修复原始文本。"""
    lines = [re.sub(r'\s+', '', line) for line in quote.splitlines() if line.strip()]
    headings = [match.group(1) for line in lines
                if (match := re.fullmatch(r'\d+(?:\.\d+){1,4}[、.：:]?([^\d]{2,24})', line))]
    if not headings:
        return False
    title, count = Counter(headings).most_common(1)[0]
    # 少量正常标题/实质条文的重复不触发；至少六个相同短标题占绝大多数正文。
    text_length = sum(len(re.sub(r'[\d.、：:]', '', line)) for line in lines)
    return count >= 6 and count * len(title) >= .8 * text_length


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def infer_project_name(sources):
    """复用显式工程名称抽取；只取正文可读页，冲突不猜测。"""
    from .extract import extract_project_name
    # 同一协议书分别列总包工程和分包工程名称；不能把两个层级当作同名冲突。
    main_pages = [page for source in sources if source.get('relation') == 'main'
                  for page in source['pages'] if page['readable']]
    for label in ('总包工程名称', '分包工程名称'):
        explicit = {match.group(1).strip(' ：:;；，,。') for page in main_pages
                    for match in re.finditer(label + r'\s*[:：]\s*([^\r\n]{4,160})', page['text'])}
        if explicit:
            return next(iter(explicit)) if len(explicit) == 1 else None
    values = {value for source in sources if source.get("relation") == "main"
              for page in source["pages"] if page["readable"]
              if (value := extract_project_name(SimpleNamespace(pages_json=[page])))}
    return next(iter(values)) if len(values) == 1 else None


def _repeated_empty_markdown_rows(lines):
    nonempty = [line.strip() for line in lines if line.strip()]
    empty_rows = sum(bool(re.fullmatch(r'\|[ |\t]*\|', line)) for line in nonempty)
    return empty_rows >= 20 and empty_rows * 5 >= len(nonempty) * 4


def freeze_material(material, *, file_sha256=None, recheck_cached_quality=False) -> dict:
    pages = []
    repeated, empty_tables = set(), set()
    for page in material.pages_json or []:
        number = page.get("page")
        if type(number) is int and number > 0:
            text = str(page.get("text") or "")
            readable = parsing.material_page_readable(material, number)
            if recheck_cached_quality and readable:
                # 原页可读确认不能恢复已退化的识别正文。
                lines = text.splitlines()
                if parsing.repetitive_numbered_headings(lines):
                    repeated.add(number)
                    readable = False
                if _repeated_empty_markdown_rows(lines):
                    empty_tables.add(number)
                    readable = False
            pages.append({"page": number, "text": text,
                          "readable": readable,
                          "source_kind": page.get("source_kind") or "unknown"})
    numbers = [row["page"] for row in pages]
    if len(numbers) != len(set(numbers)):
        raise ValueError("材料出现重复物理页码，不能形成可靠倒查输入")
    pages.sort(key=lambda row: row["page"])
    coverage = parsing.reading_coverage(material)
    suspect = repeated | empty_tables
    if suspect:
        coverage.update(reading_complete=False, degraded=True,
                        read_pages=sorted(set(coverage["read_pages"]) - suspect),
                        suspect_pages=sorted(set(coverage["suspect_pages"]) | suspect),
                        unread_pages=sorted(set(coverage["unread_pages"]) | suspect))
    if repeated:
        coverage["repetitive_heading_pages"] = sorted(repeated)
    if empty_tables:
        coverage["repeated_empty_table_pages"] = sorted(empty_tables)
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


def original_citation(ref: dict, sources: list[dict], *, bounds: tuple[int, int] | None = None) -> dict | None:
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
            lower, upper = bounds if bounds is not None else (0, len(text))
            if not 0 <= lower < upper <= len(text):
                return None
            positions = [index for index in range(lower, upper) if not text[index].isspace()]
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
        coverage = source["reading_coverage"]
        blanks = set(coverage.get("blank_pages") or [])
        gaps = sorted(set(range(1, source["total_pages"] + 1)) - readable - blanks)
        only_blank = (bool(blanks) and set(coverage.get("unread_pages") or []) == blanks
                      and not coverage.get("suspect_pages") and not coverage.get("over_limit_pages"))
        if gaps or not readable or not (coverage.get("reading_complete") or only_blank):
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
    # 清单叶子不能脱离“不得施工分包”的前提及上位结构/例外理解。
    negative = json.loads((Path(__file__).with_name('data') / 'negative_list_2024.json').read_text(encoding='utf-8'))
    rules = {'MOT-NL-2024#' + row['article']: row for row in negative['rules']}
    for row in result:
        if row['doc_id'] == 'MOT-NL-2024':
            rule = rules.get(row['clause_id'], {})
            row['scope_context'] = '\n'.join(filter(None, [negative['meta']['preamble'], rule.get('parent_context')]))
    return result + _historical_basis()


def _historical_basis(root=None) -> list[dict]:
    """倒查专用官方选段；与准入及暂停专项的数据分离，不伪造人工确认。"""
    from html import unescape
    root = Path(root) if root is not None else Path(__file__).with_name("data") / "retrospective_basis"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    documents, originals = {}, {}
    for document in manifest["documents"]:
        path = root / document["source_file"]
        if path.resolve().parent != root.resolve():
            raise ValueError("历史依据来源路径无效")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != document["source_sha256"]:
            raise ValueError("历史依据原件指纹不一致")
        # HTML标签间空白不改变原文；不纠错、不替换数字或标点。
        text = unescape(re.sub(r"<[^>]+>", "", raw.decode("utf-8")))
        compact = re.sub(r"\s+", "", text)
        if document["effective_source_quote"] not in compact:
            raise ValueError("历史依据施行日期缺少原文支持")
        documents[document["doc_id"]] = document
        originals[document["doc_id"]] = compact
    rows = []
    for clause in manifest["clauses"]:
        doc_id = clause["doc_id"]
        if re.sub(r"\s+", "", clause["text"]) not in originals[doc_id]:
            raise ValueError("历史依据选段与官方原件不一致")
        rows.append({**clause, "document": documents[doc_id], "human_verified": False,
                     "page_start": None, "page_end": None})
    return rows


_CONTRACT_SIGNING_SECTION = re.compile(
    r"(?m)^[ \t#|$]*(?:\\text[ \t]*\{[ \t]*)?"
    r"(?:第[一二三四五六七八九十\d]+部分[ \t、.：:]*)?"
    r"(?:(?P<agreement>(?:专业分包|劳务分包|分包)?合同协议书)"
    r"|(?P<terms>通用合同条款|专用合同条款)"
    r"|(?P<annex>合同附件|(?:施工)?安全生产(?:合同|协议书)|廉政(?:合同|协议书)"
    r"|治安防范、消防安全责任书(?:[（(]分包[）)])?|文明施工管理协议书|规范用工协议书))"
    r"[ \t|$}]*\r?$"
)
_CONTRACT_SIGNING_FIELD = re.compile(
    r"(?m)(?:^|[|。！？；;])[ \t]*(?:(?:本?(?:专业分包|劳务分包|劳务合作|分包)?合同|本协议"
    r"|甲方|乙方|分包人|分包发包方)[ \t]*)?"
    r"(?:签订日期|签约日期|签署日期|签订时间)\s*[:：]?\s*"
    r"(20\d{2})\s*[年./／－-]\s*(\d{1,2})\s*[月./／－-]\s*(\d{1,2})(?!\d)\s*日?"
)


def _main_contract_text(source):
    pages = [(page["text"], list(_CONTRACT_SIGNING_SECTION.finditer(page["text"])))
             for page in source["pages"] if page["readable"]]
    has_agreement = any(header.lastgroup == "agreement" for _, headers in pages for header in headers)
    in_main = not has_agreement
    for text, headers in pages:
        start = 0
        for header in headers:
            if header.lastgroup == "terms" and not has_agreement:
                continue
            if in_main:
                yield text[start:header.start()]
            in_main = header.lastgroup == "agreement"
            start = header.end()
        if in_main:
            yield text[start:]


def infer_contract_date(sources) -> tuple[str | None, list[str]]:
    """在主分包协议范围内接受明确签约字段，遇到日期冲突时保留未知。"""
    from datetime import date

    values = set()
    for source in sources:
        if source.get("role") not in {"sub", "labor"} or source.get("relation", "main") != "main":
            continue
        for text in _main_contract_text(source):
            for match in _CONTRACT_SIGNING_FIELD.finditer(text):
                try:
                    values.add(date(*map(int, match.groups())).isoformat())
                except ValueError:
                    pass
    if len(values) == 1:
        return values.pop(), []
    return None, ["未取得唯一可靠的合同签订日期，历史适用依据不作确定性定性；现行提示单列。"]
