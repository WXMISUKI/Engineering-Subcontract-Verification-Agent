# -*- coding: utf-8 -*-
"""文档正文拼装（§1.2.7「读整篇 / 续读」，2026-10-19）。

WeKnora **没有**「取文档正文」的单接口：`GET /knowledge/{id}` 只有元数据，
`/preview`、`/download` 是原始文件；正文只能按分块拉取后由客户端拼。本模块承载
「分块 → 正文」的**纯函数**（无网络、无数据库），适配器与工具都从这里取常量与
切片逻辑，避免 knowledge → as_runtime 的反向依赖。

拼接口径（2026-10-19 用户实测 WeKnora 源码与内网实例）：

- text 分块是正文骨架，按 `chunk_index` 升序；**顺序以服务端为准但仍防御性排序**。
- 扫描件的 text 分块常只有 `![page_N.jpg](resource://...)` 占位符，真正正文在
  `image_ocr` 分块；OCR 分块 `image_info[0].url` 与占位符 URL 一一对应，按 URL 回填。
- OCR 的 `chunk_index` 全为 0，**绝不能按 `seq_id` 排序**。
- 一个 text 块可能含多个占位符（实测 3–4 个），必须全量替换。
- 未被任何占位符引用的 OCR 块不静默丢弃：统计条数写进 `note`，由调用方如实展示。
- `image_caption` / `parent_text` 不参与拼接（适配器只请求 text + image_ocr）。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional

# 单次 read_document 返回的窗口（字）。检索片段上限 500 字，6000 字足够覆盖上下文。
READ_WINDOW_CHARS = 6000
# 分块分页：服务端 page_size 上限 100。
CHUNK_PAGE_SIZE = 100
# 防御性上限：超过即显式报错，避免异常响应导致无限翻页。
MAX_CHUNK_PAGES = 100

CHUNK_TYPE_TEXT = "text"
CHUNK_TYPE_OCR = "image_ocr"
# 只拉这两类；image_caption / parent_text 不参与正文拼接。
READ_CHUNK_TYPES = (CHUNK_TYPE_TEXT, CHUNK_TYPE_OCR)

# Markdown 图片占位符：![...](resource://...)
_PLACEHOLDER_RE = re.compile(r"!\[[^\]]*\]\((resource://[^)]+)\)")


def parse_image_info(raw: Any) -> List[Dict[str, Any]]:
    """`image_info` → list[dict]。可能是 JSON 字符串、dict 或 list；坏值按空处理。"""
    if not raw:
        return []
    value = raw
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, (list, tuple)):
        return [v for v in value if isinstance(v, dict)]
    return []


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _chunk_index(chunk: Dict[str, Any]) -> Optional[int]:
    return _as_int(chunk.get("chunk_index"))


def _sort_text_chunks(chunks: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按 `chunk_index` 升序；缺索引的排最后，同级保持服务端原始顺序（稳定排序）。"""
    items = list(chunks)
    return sorted(
        items,
        key=lambda c: (_chunk_index(c) is None,
                       _chunk_index(c) if _chunk_index(c) is not None else 0),
    )


def _ocr_urls(chunk: Dict[str, Any]) -> List[str]:
    urls: List[str] = []
    for info in parse_image_info(chunk.get("image_info")):
        url = str(info.get("url") or "").strip()
        if url and url not in urls:
            urls.append(url)
    return urls


def trace_document_assembly(text_chunks: Iterable[Dict[str, Any]],
                            ocr_chunks: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Report verifiable source spans; completeness here is not OCR accuracy or page coverage.

    Offsets refer to the unsliced assembled document. Do not expose resource URLs.
    Reject ambiguous skeletons, duplicate/missing image mappings and orphan OCR.
    A repeated placeholder is traced only at adjacent chunk boundaries; both
    copies remain in the output and are marked, not silently deduplicated.
    """
    text_list, ocr_list = list(text_chunks), list(ocr_chunks)
    failure = {"complete": False, "segments": [], "coordinate_space": "full_document"}
    indexes = [_chunk_index(chunk) for chunk in text_list]
    if (not text_list or any(index is None or index < 0 for index in indexes)
            or len(set(indexes)) != len(indexes)):
        return failure
    if any(not isinstance(c.get("id"), str) or not c["id"] for c in text_list + ocr_list):
        return failure
    if len({c["id"] for c in text_list + ocr_list}) != len(text_list) + len(ocr_list):
        return failure
    by_url: Dict[str, Dict[str, Any]] = {}
    for chunk in ocr_list:
        urls = _ocr_urls(chunk)
        if len(urls) != 1 or urls[0] in by_url or not chunk.get("content"):
            return failure
        by_url[urls[0]] = chunk
    used: set[str] = set()
    occurrences: Dict[str, tuple[int, int, str, bool, Dict[str, Any]]] = {}
    segments: List[Dict[str, Any]] = []
    cursor = 0
    for position, chunk in enumerate(_sort_text_chunks(text_list)):
        if position:
            cursor += 1
        content = str(chunk.get("content") or "")
        matches = list(_PLACEHOLDER_RE.finditer(content))
        if content.count("![") != len(matches):
            return failure
        start = 0
        for match in matches:
            before = content[start:match.start()]
            if before:
                segments.append({"chunk_id": chunk["id"], "chunk_type": CHUNK_TYPE_TEXT,
                                 "start": cursor, "end": cursor + len(before)})
                cursor += len(before)
            url = match.group(1)
            if url not in by_url:
                return failure
            previous = occurrences.get(url)
            overlap = previous is not None
            if overlap:
                prev_position, prev_index, prev_markup, prev_at_end, prev_segment = previous
                if (position != prev_position + 1 or _chunk_index(chunk) != prev_index + 1
                        or prev_markup != match.group(0) or not prev_at_end
                        or content[:match.start()].strip() or prev_segment.get("boundary_overlap")):
                    return failure
                prev_segment["boundary_overlap"] = True
            used.add(url)
            ocr = by_url[url]
            size = len(str(ocr["content"]))
            segment = {"chunk_id": ocr["id"], "chunk_type": CHUNK_TYPE_OCR,
                       "container_chunk_id": chunk["id"], "start": cursor, "end": cursor + size}
            # The image label is only a provider hint, not proof of a PDF physical page.
            label = match.group(0).split("](", 1)[0]
            page_marker = re.search(r"_page_(\d+)\.(?:jpg|jpeg|png|webp)$", label, re.IGNORECASE)
            if page_marker and int(page_marker.group(1)) > 0:
                segment.update(page_hint=int(page_marker.group(1)),
                               page_hint_source="image_placeholder_label")
            if overlap:
                segment["boundary_overlap"] = True
            segments.append(segment)
            occurrences[url] = (position, _chunk_index(chunk), match.group(0),
                                not content[match.end():].strip(), segment)
            cursor += size
            start = match.end()
        tail = content[start:]
        if tail:
            segments.append({"chunk_id": chunk["id"], "chunk_type": CHUNK_TYPE_TEXT,
                             "start": cursor, "end": cursor + len(tail)})
            cursor += len(tail)
    if used != set(by_url) or not segments:
        return failure
    return {"complete": True, "segments": segments, "coordinate_space": "full_document"}


def reconstruct_text_chunks(chunks: Iterable[Dict[str, Any]]) -> Dict[str, Any] | None:
    """Reconstruct text only when source offsets and every overlap agree exactly.

    Caller must verify source authorization, identity and complete pagination.
    Image/OCR placeholders are not supported; segment offsets remain traceable.
    """
    rows = list(chunks)
    if not rows:
        return None
    if any(not isinstance(row, dict) or row.get("chunk_type") != CHUNK_TYPE_TEXT
           or not isinstance(row.get("id"), str) or not row["id"]
           or not isinstance(row.get("knowledge_id"), str) or not row["knowledge_id"]
           or type(row.get("chunk_index")) is not int or type(row.get("start_at")) is not int
           or type(row.get("end_at")) is not int or not isinstance(row.get("content"), str)
           or "![" in row["content"] for row in rows):
        return None
    rows.sort(key=lambda row: row["chunk_index"])
    if ([row["chunk_index"] for row in rows] != list(range(len(rows)))
            or len({row["id"] for row in rows}) != len(rows)
            or len({row["knowledge_id"] for row in rows}) != 1):
        return None
    text = ""
    segments = []
    previous_start = 0
    for row in rows:
        start, end, content = row["start_at"], row["end_at"], row["content"]
        if (start < previous_start or start < 0 or end <= start or end - start != len(content)
                or start > len(text)):
            return None
        overlap = min(end, len(text)) - start
        if text[start:start + overlap] != content[:overlap]:
            return None
        if end > len(text):
            text += content[overlap:]
        segments.append({"chunk_id": row["id"], "start": start, "end": end})
        previous_start = start
    return {"text": text, "segments": segments}


def document_from_report(report: Dict[str, Any]) -> Dict[str, Any] | None:
    """将 ``fetch_document_text_reported`` 的完整装配报告转为可引用正文。

    扫描件的正文由 ``text`` 占位块和 ``image_ocr`` 块共同装配。调用方不能
    只取 ``text`` 块再调用 :func:`reconstruct_text_chunks`，否则会把合法的
    OCR 正文误判为不可用。这里仅接受服务端已经完成且带完整偏移轨迹的报告；
    缺 OCR、占位符未替换或轨迹不连续时继续 fail-closed。
    """
    if not isinstance(report, dict) or report.get("status") != "ok":
        return None
    if report.get("scope_complete") is not True:
        return None
    text = report.get("text")
    trace = report.get("assembly_trace")
    segments = trace.get("segments") if isinstance(trace, dict) else None
    if (not isinstance(text, str) or not text or not isinstance(trace, dict)
            or trace.get("complete") is not True
            or trace.get("coordinate_space") != "full_document"
            or not isinstance(segments, list) or not segments):
        return None
    previous_end = 0
    for segment in segments:
        if (not isinstance(segment, dict) or not isinstance(segment.get("chunk_id"), str)
                or not segment["chunk_id"] or type(segment.get("start")) is not int
                or type(segment.get("end")) is not int):
            return None
        start, end = segment["start"], segment["end"]
        if (start < previous_end or start < 0 or end <= start or end > len(text)
                or text[previous_end:start].strip()):
            return None
        previous_end = end
    if text[previous_end:].strip():
        return None
    return {"text": text, "segments": segments}


def assemble_document(
    text_chunks: Iterable[Dict[str, Any]],
    ocr_chunks: Iterable[Dict[str, Any]],
) -> Dict[str, Any]:
    """把分块拼成完整正文。

    返回 `{"text", "chunk_offsets", "note"}`：
    - `text`：完整正文（未截窗）；
    - `chunk_offsets`：`chunk_index → 拼接后起始字符偏移`（供默认定位到引用块）；
    - `note`：三态说明（未引用的 OCR 块、纯 OCR 文档页序不可判定等），可为空串。
    """
    text_list = list(text_chunks)
    ocr_list = list(ocr_chunks)

    ocr_by_url: Dict[str, str] = {}
    for chunk in ocr_list:
        content = str(chunk.get("content") or "")
        for url in _ocr_urls(chunk):
            ocr_by_url.setdefault(url, content)

    if not text_list:
        # 只有 OCR 块：没有任何页码/顺序锚点，只能按服务端返回顺序拼，并明说不可判定。
        text = "\n".join(str(c.get("content") or "") for c in ocr_list)
        note = ""
        if text:
            note = "未取得 text 分块，按服务端返回顺序拼接 OCR 正文；页序不可判定。"
        return {"text": text, "chunk_offsets": {}, "note": note}

    referenced: set[str] = set()

    def _sub(match: re.Match) -> str:
        url = match.group(1)
        replacement = ocr_by_url.get(url)
        if replacement is None:
            return match.group(0)  # 找不到就保留占位符，不伪造正文
        referenced.add(url)
        return replacement

    parts: List[str] = []
    chunk_offsets: Dict[int, int] = {}
    cursor = 0
    for index, chunk in enumerate(_sort_text_chunks(text_list)):
        if index:
            cursor += 1  # 与下面的 "\n".join 对齐
        chunk_index = _chunk_index(chunk)
        if chunk_index is not None:
            chunk_offsets[chunk_index] = cursor
        segment = _PLACEHOLDER_RE.sub(_sub, str(chunk.get("content") or ""))
        parts.append(segment)
        cursor += len(segment)

    text = "\n".join(parts)

    unreferenced = 0
    for chunk in ocr_list:
        urls = _ocr_urls(chunk)
        if not urls or not any(u in referenced for u in urls):
            unreferenced += 1
    note = ""
    if unreferenced:
        note = f"另有 {unreferenced} 个 OCR 分块未被正文占位符引用，未拼入正文。"
    return {"text": text, "chunk_offsets": chunk_offsets, "note": note}


def slice_document(
    text: str,
    *,
    offset: int = 0,
    max_chars: Optional[int] = READ_WINDOW_CHARS,
) -> Dict[str, Any]:
    """按窗口切片。`max_chars=None` 返回全文（供工具缓存完整正文）。"""
    total = len(text)
    try:
        start = int(offset or 0)
    except (TypeError, ValueError):
        start = 0
    start = max(0, min(start, total))
    if max_chars is None:
        end = total
    else:
        end = min(start + max(0, int(max_chars)), total)
    return {
        "text": text[start:end],
        "total_chars": total,
        "offset": start,
        "next_offset": end,
        "done": end >= total,
    }


def resolve_start_offset(
    chunk_offsets: Dict[int, int],
    chunk_index: Any,
) -> int:
    """引用块在正文中的起点；索引缺失/解析不到时回落到 0（文档开头）。"""
    index = _as_int(chunk_index)
    if index is None:
        return 0
    return int(chunk_offsets.get(index, 0))


__all__ = [
    "CHUNK_PAGE_SIZE",
    "CHUNK_TYPE_OCR",
    "CHUNK_TYPE_TEXT",
    "MAX_CHUNK_PAGES",
    "READ_CHUNK_TYPES",
    "READ_WINDOW_CHARS",
    "assemble_document",
    "document_from_report",
    "parse_image_info",
    "resolve_start_offset",
    "slice_document",
]
