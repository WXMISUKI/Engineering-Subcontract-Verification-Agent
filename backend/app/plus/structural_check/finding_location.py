from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re
import unicodedata
from typing import Any

from ..safety_review.extract import ExtractDoc, Line


_FUZZY_MIN_QUOTE_LENGTH = 6
_FUZZY_MIN_SCORE = 0.78
_FUZZY_MIN_COVERAGE = 0.72
_FUZZY_AMBIGUITY_DELTA = 0.04


def infer_location_mode(*, page: int | None, rects: list[Any] | None) -> str:
    normalized_page = int(page or 0)
    has_rects = bool(rects)
    if normalized_page <= 0:
        return "unresolved"
    if has_rects:
        return "precise"
    return "page"


@dataclass(frozen=True)
class LineMatchResult:
    line: Line | None
    strategy: str
    score: float = 0.0
    coverage: float = 0.0


def normalize_quote_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    normalized = (
        normalized.replace("\r", "")
        .replace("\n", "")
        .replace("\t", "")
        .replace(" ", "")
        .replace("　", "")
        .replace("，", ",")
        .replace("。", ".")
        .replace("：", ":")
        .replace("；", ";")
        .replace("（", "(")
        .replace("）", ")")
        .replace("【", "[")
        .replace("】", "]")
    )
    return normalized.strip()


def build_page_line_index(doc: ExtractDoc) -> dict[int, dict[str, int]]:
    page_to_line_numbers: dict[int, dict[str, int]] = {}
    page_counters: dict[int, int] = {}
    for line in doc.lines:
        page = max(1, int(line.page or 1))
        page_counters[page] = page_counters.get(page, 0) + 1
        page_to_line_numbers.setdefault(page, {})[line.id] = page_counters[page]
    return page_to_line_numbers


def format_line_id(page: int, page_line_number: int | None) -> str | None:
    if not page_line_number or page_line_number <= 0:
        return None
    return f"P{page}-L{page_line_number:03d}"


def line_rects(line: Line) -> list[list[float]]:
    rects: list[list[float]] = []
    for word in line.words or []:
        rects.append([
            float(word.x0),
            float(word.y0),
            float(word.x1),
            float(word.y1),
        ])
    return rects


def _page_lines(doc: ExtractDoc, page: int) -> list[Line]:
    return [line for line in doc.lines if int(line.page or 0) == page]


def _all_lines(doc: ExtractDoc) -> list[Line]:
    return list(doc.lines or [])


def _matching_metrics(quote: str, line_text: str) -> tuple[float, float]:
    matcher = SequenceMatcher(None, quote, line_text, autojunk=False)
    matched_chars = sum(block.size for block in matcher.get_matching_blocks())
    coverage = matched_chars / max(len(quote), 1)
    ratio = matcher.ratio()
    return coverage, ratio


def _find_fuzzy_line(page_lines: list[Line], normalized_quote: str, *, strategy_prefix: str = "fuzzy") -> LineMatchResult:
    if len(normalized_quote) < _FUZZY_MIN_QUOTE_LENGTH:
        return LineMatchResult(line=None, strategy=f"{strategy_prefix}_too_short")

    best_line: Line | None = None
    best_score = 0.0
    best_coverage = 0.0
    second_score = 0.0

    for line in page_lines:
        normalized_line = normalize_quote_text(line.text or "")
        if not normalized_line:
            continue
        coverage, ratio = _matching_metrics(normalized_quote, normalized_line)
        score = coverage * 0.7 + ratio * 0.3
        if score > best_score:
            second_score = best_score
            best_line = line
            best_score = score
            best_coverage = coverage
        elif score > second_score:
            second_score = score

    if best_line is None:
        return LineMatchResult(line=None, strategy=f"{strategy_prefix}_no_candidates")
    if best_score < _FUZZY_MIN_SCORE or best_coverage < _FUZZY_MIN_COVERAGE:
        return LineMatchResult(line=None, strategy=f"{strategy_prefix}_below_threshold", score=best_score, coverage=best_coverage)
    if second_score and (best_score - second_score) < _FUZZY_AMBIGUITY_DELTA:
        return LineMatchResult(line=None, strategy=f"{strategy_prefix}_ambiguous", score=best_score, coverage=best_coverage)
    return LineMatchResult(
        line=best_line,
        strategy=strategy_prefix,
        score=best_score,
        coverage=best_coverage,
    )


def _find_matching_line_in_scope(lines: list[Line], quote: str, *, strategy_prefix: str) -> LineMatchResult:
    exact_quote = str(quote or "").strip()
    if not exact_quote:
        return LineMatchResult(line=None, strategy="empty_quote")

    for line in lines:
        if exact_quote in (line.text or ""):
            return LineMatchResult(line=line, strategy=f"{strategy_prefix}_exact", score=1.0, coverage=1.0)

    normalized_quote = normalize_quote_text(exact_quote)
    if not normalized_quote:
        return LineMatchResult(line=None, strategy="empty_normalized_quote")
    for line in lines:
        if normalized_quote in normalize_quote_text(line.text or ""):
            return LineMatchResult(line=line, strategy=f"{strategy_prefix}_normalized", score=0.98, coverage=1.0)
    return _find_fuzzy_line(lines, normalized_quote, strategy_prefix=f"{strategy_prefix}_fuzzy")


_LINE_ID_PATTERN = re.compile(r"^[Pp](\d+)[-_]?[Ll](\d+)$")
_GLOBAL_LINE_PATTERN = re.compile(r"^[Ll](\d+)$")


def resolve_line_id_anchor(doc: "ExtractDoc", raw_line_id: str) -> Line | None:
    """按行号规格直解定位（P13，2026-09-02）：
    - P{页}-L{n}：第 page 页按文档顺序第 n 行（与摘录中标注一致）；
    - L{n}：全局第 n 行（doc.lines[n-1]）。
    LLM 在带行号的原文摘录中引用行号时，这是唯一确定性坐标，无需全文模糊匹配。"""
    raw = str(raw_line_id or "").strip()
    if not raw:
        return None
    m = _LINE_ID_PATTERN.match(raw)
    if m:
        page, n = int(m.group(1)), int(m.group(2))
        seq = [ln for ln in doc.lines if int(ln.page or 0) == page]
        if 1 <= n <= len(seq):
            return seq[n - 1]
        return None
    m = _GLOBAL_LINE_PATTERN.match(raw)
    if m:
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(doc.lines):
            return doc.lines[idx]
    return None


def _find_cross_line_block(lines: list[Line], normalized_quote: str, *, strategy_prefix: str = "cross") -> LineMatchResult:
    """跨行连续匹配（P13，2026-09-02）：quote 被换行打断时，按文档顺序拼接连续行，
    命中即返回首行为锚（引文的第一行即定位起点）。"""
    if not normalized_quote or len(normalized_quote) < 6:
        return LineMatchResult(line=None, strategy=f"{strategy_prefix}_too_short")
    for start in range(len(lines)):
        acc = ""
        for line in lines[start:]:
            acc += normalize_quote_text(line.text or "")
            if len(acc) >= len(normalized_quote):
                if normalized_quote in acc:
                    return LineMatchResult(
                        line=lines[start],
                        strategy=f"{strategy_prefix}_line_block",
                        score=0.9,
                        coverage=1.0,
                    )
                break
    return LineMatchResult(line=None, strategy=f"{strategy_prefix}_miss")


def find_matching_line(
    doc: "ExtractDoc",
    *,
    page: int,
    quote: str,
    raw_line_id: str | None = None,
) -> LineMatchResult:
    if page <= 0:
        return _find_matching_line_in_scope(_all_lines(doc), quote, strategy_prefix="global")

    # P13：LLM 直接引用摘录行号 → 确定性定位，优先于全文模糊匹配。
    # 弱校验：仅当 quote 与行文本高度一致才采信（防 LLM 幻写行号指向无关行）。
    if raw_line_id:
        anchor_line = resolve_line_id_anchor(doc, raw_line_id)
        if anchor_line is not None:
            a_text = normalize_quote_text(anchor_line.text or "")
            q_text = normalize_quote_text(quote)
            if not q_text:
                return LineMatchResult(line=anchor_line, strategy="line_id_anchor", score=1.0, coverage=1.0)
            if a_text and (q_text in a_text or a_text in q_text):
                return LineMatchResult(line=anchor_line, strategy="line_id_anchor", score=1.0, coverage=1.0)
            if a_text and SequenceMatcher(None, q_text, a_text, autojunk=False).ratio() >= 0.45:
                return LineMatchResult(line=anchor_line, strategy="line_id_anchor", score=1.0, coverage=1.0)

    page_lines = _page_lines(doc, page)
    scoped_match = _find_matching_line_in_scope(page_lines, quote, strategy_prefix="page")
    if scoped_match.line is not None:
        return scoped_match

    global_match = _find_matching_line_in_scope(_all_lines(doc), quote, strategy_prefix="global")
    if global_match.line is not None:
        return global_match

    # P13：跨行连续兜底（quote 被换行打断时的真实场景）
    cross_match = _find_cross_line_block(_all_lines(doc), normalize_quote_text(quote))
    if cross_match.line is not None:
        return cross_match
    return scoped_match if scoped_match.line is not None else LineMatchResult(line=None, strategy=scoped_match.strategy)


def normalize_finding(
    finding: dict[str, Any],
    *,
    doc: ExtractDoc,
    page_line_index: dict[int, dict[str, int]],
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    warnings: list[dict[str, str]] = []
    if not isinstance(finding, dict):
        return None, [{"code": "invalid_finding", "message": "finding is not an object"}]

    raw_page = finding.get("page")
    try:
        page = int(raw_page)
    except (TypeError, ValueError):
        page = 0
    line_raw = str(finding.get("line_id") or "").strip() or None

    # P13：允许仅凭摘录行号（P{页}-L{行序} / L{n}）直接定页——行号是权威坐标，
    # 即使在 LLM 漏给 page/quote 时也能确定性定位（行文本回填/兜底）。
    anchor = resolve_line_id_anchor(doc, line_raw) if line_raw else None
    if page <= 0:
        if anchor is not None:
            page = max(1, int(anchor.page or 1))
        else:
            return None, [{"code": "missing_page", "message": "finding missing required page"}]

    quote = str(finding.get("quote") or "").strip()
    if not quote:
        if anchor is not None:
            quote = (anchor.text or "").strip()
        else:
            return None, [{"code": "missing_quote", "message": "finding missing required quote"}]

    doc_pages = max(int(doc.pages or 0), max((int(line.page or 0) for line in doc.lines), default=0))
    match = find_matching_line(doc, page=page, quote=quote, raw_line_id=line_raw)
    normalized = dict(finding)
    normalized["page"] = page
    normalized["quote"] = quote
    normalized["line_id"] = None
    normalized["rects"] = []
    normalized["location_mode"] = "page"

    if match.line is None:
        # P13：LLM 页码超范围/不符时，用 quote 在全文做全局检索恢复真实页（单行→跨行），
        # 避免「引文真实存在却因页码无效直接废弃成不可定位」。
        if doc_pages > 0:
            page_out_of_range = page > doc_pages
            glb_match = None
            if page_out_of_range:
                glb_match = _find_matching_line_in_scope(_all_lines(doc), quote, strategy_prefix="global")
                if glb_match.line is None:
                    norm_q = normalize_quote_text(quote)
                    if norm_q:
                        glb_match = _find_cross_line_block(_all_lines(doc), norm_q, strategy_prefix="global_cross")
            if glb_match is not None and glb_match.line is not None:
                gline = glb_match.line
                gpage = max(1, int(gline.page or 1))
                page_line_number = page_line_index.get(gpage, {}).get(gline.id)
                normalized["page"] = gpage
                normalized["line_id"] = format_line_id(gpage, page_line_number)
                normalized["rects"] = line_rects(gline)
                normalized["location_mode"] = infer_location_mode(page=gpage, rects=normalized["rects"])
                warnings.append(
                    {
                        "code": "location_page_recovered_globally",
                        "message": f"reported page {page} exceeded document pages {doc_pages}; "
                        f"quote located on page {gpage} via global text search",
                    }
                )
                return normalized, warnings

        if doc_pages > 0 and page > doc_pages:
            normalized["page"] = 0
            normalized["location_mode"] = "unresolved"
            warnings.append(
                {
                    "code": "location_page_out_of_range",
                    "message": f"reported page {page} exceeds extracted document pages {doc_pages}; finding downgraded to non-clickable quote",
                }
            )
        warnings.append(
            {
                "code": "location_mapping_failed_after_calibration",
                "message": f"page and quote were retained but no matching OCR/text line was resolved ({match.strategy})",
            }
        )
        normalized["location_mode"] = infer_location_mode(page=normalized.get("page"), rects=normalized.get("rects"))
        return normalized, warnings

    matched_line = match.line
    actual_page = max(1, int(matched_line.page or page or 1))
    if actual_page != page:
        warnings.append(
            {
                "code": "location_page_corrected",
                "message": f"reported page {page} was corrected to extracted page {actual_page} using quote remapping",
            }
        )
    normalized["page"] = actual_page

    if match.strategy in {"page_fuzzy", "global_fuzzy"}:
        warnings.append(
            {
                "code": "location_mapping_fuzzy",
                "message": f"quote matched page text through fuzzy OCR calibration (score={match.score:.3f}, coverage={match.coverage:.3f})",
            }
        )

    page_line_number = page_line_index.get(actual_page, {}).get(matched_line.id)
    line_id = format_line_id(actual_page, page_line_number)
    rects = line_rects(matched_line)

    if line_id is None:
        warnings.append(
            {
                "code": "missing_line_id",
                "message": "quote matched page text but no stable line id was resolved",
            }
        )
    if not rects:
        warnings.append(
            {
                "code": "missing_rects",
                "message": "quote matched page text but no stable bounding rects were resolved",
            }
        )

    normalized["line_id"] = line_id
    normalized["rects"] = rects
    normalized["location_mode"] = infer_location_mode(page=normalized.get("page"), rects=rects)
    return normalized, warnings


def normalize_findings_for_doc(
    findings: list[Any],
    *,
    doc: ExtractDoc,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    normalized_findings: list[dict[str, Any]] = []
    contract_warnings: list[dict[str, str]] = []
    page_line_index = build_page_line_index(doc)
    for finding in findings or []:
        normalized, warnings = normalize_finding(
            finding,
            doc=doc,
            page_line_index=page_line_index,
        )
        contract_warnings.extend(warnings)
        if normalized is not None:
            normalized_findings.append(normalized)
    return normalized_findings, contract_warnings
