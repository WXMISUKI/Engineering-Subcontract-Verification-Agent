"""系统页/行定位钉扎（P13，2026-09-02）。

背景：结构核查的 finding 定位数据此前完全来自 LLM 输出（agent_runner 提示
「line_id 与 rects 可选、不要臆造」），LLM 常给口头页码/缺 rects，导致前端
按 LLM 页码跳 PDF 时跳到错误页/幽灵页，问题难以溯源。

本模块在明细落库前用 PDF 文本层行索引（ExtractDoc.lines，与 safety_review
行定位同源）对每条 finding 的 quote/内容做**确定性**匹配，重写真实
page/line_id/rects，替换 LLM 页码：
  - 命中行（单行/跨行连续）→ page + line_id=L{n} + 词级 bbox rects，location_mode=precise；
  - 未命中（扫描件无文本层 / OCR 偏差 / 关键词过短）→ 清空定位字段，
    location_mode=unresolved，宁缺勿错**不编造页码**（与开工条件
    pin_finding_pages / front 判定链路『无定位不跳幽灵页』的原则一致）。
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from .finding_location import (
    build_page_line_index,
    find_matching_line,
    format_line_id,
    line_rects as _fl_line_rects,
)

# 关键词最短长度（归一化后）：过短易误命中，宁缺勿错
_MIN_KEYWORD_LEN = 4


def normalize_pin_text(s: str) -> str:
    """归一化检索文本：去空白/全角标点，仅保留有效字符（两侧调用保持一致）。"""
    s = (
        s.replace("（", "(")
        .replace("）", ")")
        .replace("：", ":")
        .replace("，", ",")
        .replace("；", ";")
        .replace("。", "")
        .replace("、", "")
        .replace("《", "")
        .replace("》", "")
        .replace("「", "")
        .replace("」", "")
        .replace("“", "")
        .replace("”", "")
    )
    return re.sub(r"[\s\u3000]+", "", s)


def _keyword_candidates(finding: dict) -> list[str]:
    """从 finding 提取匹配用关键词（quote 优先，其次 content/text；保留原顺序）。"""
    cands: list[str] = []
    for key in ("quote", "content", "text"):
        v = finding.get(key)
        if isinstance(v, str) and v.strip():
            t = normalize_pin_text(v)
            if len(t) >= _MIN_KEYWORD_LEN and t not in cands:
                cands.append(t)
        if len(cands) >= 2:  # 最多两条候选，避免长文本误匹配扩散
            break
    return cands


def _line_rects(line: Any) -> list[list[float]]:
    """行内词级 bbox → 前端高亮用的 [[x0,y0,x1,y1], ...]（PDF 点坐标，与 pdfjs 同参照）。"""
    rects: list[list[float]] = []
    for w in getattr(line, "words", None) or []:
        vals = [getattr(w, k, None) for k in ("x0", "y0", "x1", "y1")]
        if None not in vals:
            rects.append([float(v) for v in vals])
    return rects


def _match_line_block(
    cands: list[str], pool: list[tuple[Any, str]]
) -> dict | None:
    """候选词 → 单行命中或跨行连续命中；返回 {page, line_id, rects}。"""
    if not cands:
        return None
    for cand in cands:
        # 单行包含
        for line, norm_text in pool:
            if cand in norm_text:
                return {
                    "page": getattr(line, "page", None),
                    "line_id": getattr(line, "id", None),
                    "rects": _line_rects(line),
                }
        # 跨行连续命中（quote 被换行打断的场景）。
        # P32（2026-09-17）：只保留与命中区间重叠的行——原实现把块首无关行
        # （如小节标题/图名）一并写入 rects，前端框选因此覆盖无关区域。
        for start in range(len(pool)):
            acc = ""
            block: list[tuple[Any, str]] = []
            for line, norm_text in pool[start:]:
                acc += norm_text
                block.append((line, norm_text))
                if len(acc) >= len(cand):
                    hit_idx = acc.find(cand)
                    if hit_idx >= 0:
                        kept: list[Any] = []
                        cursor = 0
                        for bl_line, bl_text in block:
                            span_start, span_end = cursor, cursor + len(bl_text)
                            if span_end > hit_idx and span_start < hit_idx + len(cand):
                                kept.append(bl_line)
                            cursor = span_end
                        rects: list[list[float]] = []
                        for bl in kept:
                            rects.extend(_line_rects(bl))
                        return {
                            "page": getattr(kept[0], "page", None),
                            "line_id": getattr(kept[0], "id", None),
                            "rects": rects,
                        }
                    break
    return None


def _fuzzy_hit(doc: Any, finding: dict) -> dict | None:
    """P24（2026-09-14）：精确匹配未命中时的模糊兜底（复用 finding_location 匹配器）。

    LLM 引文常有轻微改写/丢字（如 L/400 写成 400、LaTeX 符号被吞），
    精确子串必然失配 → 此前整条清空定位。现回退到 finding_location 的
    行级模糊匹配（SequenceMatcher，score≥0.78/coverage≥0.72/歧义拒绝），
    引文确实存在时恢复 page/line_id/rects；仍不命中才判不可定位（宁缺勿错）。
    """
    if doc is None:
        return None
    quote = str(finding.get("quote") or finding.get("content") or finding.get("text") or "").strip()
    if not quote:
        return None
    match = find_matching_line(doc, page=0, quote=quote)
    if match.line is None:
        return None
    page = max(1, int(getattr(match.line, "page", 1) or 1))
    line_id = format_line_id(page, build_page_line_index(doc).get(page, {}).get(match.line.id))
    return {"page": page, "line_id": line_id, "rects": _fl_line_rects(match.line)}


def _make_anchor(doc: Any, line: Any) -> dict:
    """Line → 定位锚点 {page, line_id, rects}（line_id 按页内序格式化）。"""
    page = max(1, int(getattr(line, "page", 1) or 1))
    idx = build_page_line_index(doc).get(page, {}).get(getattr(line, "id", None))
    return {
        "page": page,
        "line_id": format_line_id(page, idx),
        "rects": _fl_line_rects(line),
    }


def _match_chunk_anchor(pool: list[tuple[Any, str]], doc: Any, chunk_norm: str, all_lines: list[Any]) -> dict | None:
    """单个句子分片 → 锚点：先精确（单行/跨行连续），再行级模糊（丢字/改写兜底）。"""
    hit = _match_line_block([chunk_norm], pool)
    if hit and hit["page"] is not None:
        return hit
    if doc is not None:
        from .finding_location import _find_fuzzy_line  # 同包复用私有匹配器（含歧义拒绝）

        match = _find_fuzzy_line(all_lines, chunk_norm)
        if match.line is not None:
            return _make_anchor(doc, match.line)
    return None


def _anchor_location_mode(anchor: dict) -> str:
    return "precise" if anchor.get("rects") else "page"


def _pin_quote_anchors(
    quote: str,
    *,
    pool: list[tuple[Any, str]],
    doc: Any,
    all_lines: list[Any],
    max_anchors: int = 5,
) -> list[dict]:
    """引文 → 多锚点（P24 多点定位）。

    句子级分片（。；，等切分，过滤归一化后 <8 字的碎片防泛化句误命中），
    每片独立精确/模糊钉扎；命中锚点按 (page, line_id) 去重保序。
    单句引文退化为单锚点；多段引文产出多个锚点供前端多点「定位 Pn」。
    """
    if not quote.strip():
        return []
    chunks: list[str] = []
    for part in re.split(r"[。；;，,！!？?\n]", quote):
        norm = normalize_pin_text(part)
        if len(norm) >= 8 and norm not in chunks:
            chunks.append(norm)
    if not chunks:
        whole = normalize_pin_text(quote)
        if len(whole) >= _MIN_KEYWORD_LEN:
            chunks = [whole]
    anchors: list[dict] = []
    seen: set[tuple[Any, Any]] = set()
    for chunk in chunks:
        anchor = _match_chunk_anchor(pool, doc, chunk, all_lines)
        if anchor is None:
            continue
        key = (anchor.get("page"), anchor.get("line_id"))
        if key in seen:
            continue
        seen.add(key)
        anchor["location_mode"] = _anchor_location_mode(anchor)
        anchors.append(anchor)
        if len(anchors) >= max_anchors:
            break
    return anchors


def pin_finding_locations(findings: list[dict] | None, lines: Iterable[Any]) -> None:
    """对 findings 逐条做系统定位钉扎（原地修改 finding dict）。

    find: dict —— agent finding（需含 quote/content 之一，可含 page/line_id/rects，
             P24：可选 quotes 数组——同一发现涉及多段原文时逐段钉扎）
    lines —— ExtractDoc 或 ExtractDoc.lines：任意含 page/id/text/words 的对象可迭代
             （P24：直接传 ExtractDoc 亦可，模糊兜底需要完整 doc）

    钉扎语义（P13 延续 + P24 扩展）：
      - 主 quote 精确命中（单行/跨行连续）→ precise；
      - 精确未命中 → 句子分片精确/模糊兜底：主锚点回填 page/line_id/rects，
        其余命中分片写入 finding["extra_locations"]（多点定位，宁缺勿错不编造）；
      - 全部未命中 → 清空定位字段，location_mode=unresolved。
    """
    if not findings:
        return
    # P24：兼容两种入参——完整 ExtractDoc（模糊兜底可用）或 lines 可迭代（仅精确匹配）
    doc = lines if hasattr(lines, "lines") else None
    line_iter = doc.lines if doc is not None else (lines or [])
    all_lines: list[Any] = list(line_iter)
    pool: list[tuple[Any, str]] = []
    for ln in all_lines:
        norm = normalize_pin_text(getattr(ln, "text", "") or "")
        if norm:
            pool.append((ln, norm))
    if not pool:
        # 无文本层：全部按「不可定位」处理（宁缺勿错）
        for f in findings:
            if isinstance(f, dict):
                f["page"] = None
                f["line_id"] = None
                f["rects"] = []
                f["location_mode"] = "unresolved"
        return

    def _apply_primary(f: dict, quote: str) -> bool:
        """分片兜底：主锚点回填主定位字段。命中返回 True。"""
        anchors = _pin_quote_anchors(quote, pool=pool, doc=doc, all_lines=all_lines)
        if not anchors:
            return False
        primary = anchors[0]
        f["page"] = primary["page"]
        f["line_id"] = primary["line_id"]
        f["rects"] = primary["rects"]
        f["location_mode"] = primary["location_mode"]
        return True

    for f in findings:
        if not isinstance(f, dict):
            continue
        quote = str(f.get("quote") or "").strip()
        hit = _match_line_block(_keyword_candidates(f), pool) if quote else None
        extras: list[dict] = []
        if hit and hit["page"] is not None:
            f["page"] = hit["page"]
            f["line_id"] = hit["line_id"]
            f["rects"] = hit["rects"]
            f["location_mode"] = "precise"
        else:
            # 精确未命中 → 分片模糊兜底（P24）：主锚点恢复 + 其余分片多点定位
            if not _apply_primary(f, quote or str(f.get("content") or "")):
                f["page"] = None
                f["line_id"] = None
                f["rects"] = []
                f["location_mode"] = "unresolved"
        # P24 多点定位：quotes 数组（涉及多段原文）逐段钉扎为 extra_locations
        for extra_quote in f.get("quotes") or []:
            if isinstance(extra_quote, str) and extra_quote.strip():
                anchors = _pin_quote_anchors(extra_quote, pool=pool, doc=doc, all_lines=all_lines, max_anchors=2)
                for anchor in anchors:
                    extras.append({"quote": extra_quote.strip(), **anchor})
        if extras:
            f["extra_locations"] = extras[:8]