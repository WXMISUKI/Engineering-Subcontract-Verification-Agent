"""Exact matching of explicitly registered cross-page OCR reference fragments.

This helper performs no authorization or original-file verification. Its caller
must verify the current binding, document identity, SHA-256 and registered page
range before using any result. Page hints remain provider hints.
"""
from __future__ import annotations

import re


def _compact_with_positions(text: str) -> tuple[str, list[int]]:
    # WeKnora 的 OCR 正文可能保留 Markdown 的加粗标记（如
    # ``**第十五条**``）。这些标记不是原文字符，不能让它们导致已登记
    # 条款无法匹配；其余字符仍保留原始位置，便于返回可审计的原文片段。
    positions = [i for i, ch in enumerate(text) if not ch.isspace() and ch not in "*`"]
    return "".join(text[i].translate(str.maketrans("（）", "()")) for i in positions), positions


def match_ordered_page_fragments(report: dict, fragments: list[dict]) -> list[dict] | None:
    """从完整全文轨迹按页及顺序匹配登记片段；不推断缺失文字。"""
    trace = report.get("assembly_trace") or {}
    text, segments = report.get("text"), trace.get("segments")
    if (report.get("status") != "ok" or report.get("scope_complete") is not True
            or report.get("offset") != 0 or report.get("done") is not True
            or trace.get("complete") is not True or trace.get("coordinate_space") != "full_document"
            or not isinstance(text, str) or not isinstance(segments, list) or not fragments):
        return None
    previous_end = 0
    pages = {}
    for segment in segments:
        if not isinstance(segment, dict):
            return None
        start, end = segment.get("start"), segment.get("end")
        if type(start) is not int or type(end) is not int or start < previous_end or end <= start or end > len(text):
            return None
        previous_end = end
        page = segment.get("page_hint")
        if segment.get("chunk_type") == "image_ocr" and page in {f.get("page") for f in fragments}:
            if (segment.get("page_hint_source") != "image_placeholder_label"
                    or not isinstance(segment.get("chunk_id"), str)
                    or not isinstance(segment.get("container_chunk_id"), str)):
                return None
            pages.setdefault(page, []).append(segment)
    result, last_end = [], 0
    for fragment in fragments:
        page, role, expected = fragment.get("page"), fragment.get("role"), fragment.get("text")
        rows = pages.get(page) or []
        if type(page) is not int or role not in {"preamble", "item", "scope"} or not isinstance(expected, str) or not expected or not rows:
            return None
        first = rows[0]
        body = text[first["start"]:first["end"]]
        if len(rows) > 1 and (len(rows) != 2 or any(not r.get("boundary_overlap") for r in rows)
                or any(r["chunk_id"] != first["chunk_id"] or text[r["start"]:r["end"]] != body for r in rows)):
            return None
        compact, positions = _compact_with_positions(body)
        needle, _ = _compact_with_positions(expected)
        index = compact.find(needle) if needle else -1
        if index < 0 or compact.find(needle, index + 1) >= 0:
            return None
        begin, end = first["start"] + positions[index], first["start"] + positions[index + len(needle) - 1] + 1
        if begin < last_end:
            return None
        last_end = end
        result.append({"role": role, "page_hint": page, "chunk_id": first["chunk_id"],
                       "container_chunk_id": first["container_chunk_id"], "quote": text[begin:end]})
    return result


def match_cross_page_reference(report: dict, expected: str, *, pages: list[int],
                               separators: list[str]) -> list[dict] | None:
    """Return exact source excerpts or None; never fabricate or correct OCR text."""
    trace = report.get("assembly_trace") or {}
    text = report.get("text")
    if (report.get("status") != "ok" or report.get("scope_complete") is not True
            or report.get("offset") != 0 or report.get("done") is not True
            or trace.get("complete") is not True or trace.get("coordinate_space") != "full_document"
            or not isinstance(text, str) or not isinstance(expected, str)
            or len(pages) < 2 or len(separators) != len(pages) - 1
            or any(type(page) is not int or page < 1 for page in pages)
            or any(b != a + 1 for a, b in zip(pages, pages[1:]))):
        return None
    parts = []
    remainder = expected
    for page, separator in zip(pages, separators):
        if (not isinstance(separator, str) or not re.fullmatch(r"[-—]\s*" + str(page) + r"\s*[-—]", separator)
                or remainder.count(separator) != 1
                or not re.search(r"(?m)^\s*" + re.escape(separator) + r"\s*$", remainder)):
            return None
        part, remainder = remainder.split(separator, 1)
        parts.append(part)
    parts.append(remainder)
    segments = trace.get("segments")
    if not isinstance(segments, list):
        return None
    previous_end = 0
    candidates = {page: [] for page in pages}
    for segment in segments:
        if not isinstance(segment, dict):
            return None
        start, end = segment.get("start"), segment.get("end")
        if (type(start) is not int or type(end) is not int or start < previous_end
                or end <= start or end > len(text)):
            return None
        previous_end = end
        page = segment.get("page_hint")
        if segment.get("chunk_type") == "image_ocr" and page in candidates:
            if (segment.get("page_hint_source") != "image_placeholder_label"
                    or not isinstance(segment.get("chunk_id"), str)
                    or not isinstance(segment.get("container_chunk_id"), str)):
                return None
            candidates[page].append(segment)
    result = []
    previous_start = -1
    for page, part in zip(pages, parts):
        rows = candidates[page]
        if not rows:
            return None
        first = rows[0]
        body = text[first["start"]:first["end"]]
        if len(rows) > 1 and (len(rows) != 2 or any(not row.get("boundary_overlap") for row in rows)
                or any(row["chunk_id"] != first["chunk_id"] or text[row["start"]:row["end"]] != body
                       for row in rows)):
            return None
        if first["start"] <= previous_start:
            return None
        previous_start = first["start"]
        needle, _ = _compact_with_positions(part)
        compact, positions = _compact_with_positions(body)
        index = compact.find(needle) if needle else -1
        if index < 0 or compact.find(needle, index + 1) >= 0:
            return None
        quote = body[positions[index]:positions[index + len(needle) - 1] + 1]
        result.append({"page_hint": page, "chunk_id": first["chunk_id"],
                       "container_chunk_id": first["container_chunk_id"], "quote": quote})
    return result
