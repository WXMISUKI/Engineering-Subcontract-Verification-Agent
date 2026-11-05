"""核查阶段的大 PDF 内部切分与并行文本解析。

上传阶段仍保存原始文件；本模块只在正式核查阶段使用。切分是逻辑页范围，
不改变用户看到的文件，也不改变证据的原始页码。每个块都带有原材料 ID、
1-based 页码范围、块序号和稳定幂等键，供后续 OCR/证据聚合与重试使用。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
import hashlib
import os
from typing import Any


@dataclass(frozen=True)
class PdfChunk:
    """一个内部解析块；page_start/page_end 均为 1-based 且闭区间。"""

    material_id: int | str
    chunk_index: int
    page_start: int
    page_end: int
    idempotency_key: str
    subject_id: int | str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _key(material_id: int | str, index: int, start: int, end: int) -> str:
    """生成不依赖临时文件名的稳定块幂等键。"""
    return f"bs-material:{material_id}:chunk:{index}:pages:{start}-{end}"


def plan_pdf_chunks(
    pdf_path: str,
    *,
    material_id: int | str,
    subject_id: int | str | None = None,
    chunk_pages: int = 10,
) -> list[PdfChunk]:
    """按固定页范围规划 PDF 解析块，不创建派生文件。

    规划失败（非 PDF、损坏文件）由调用方按解析失败收口；chunk_pages 必须为正数。
    """
    if chunk_pages <= 0:
        raise ValueError("chunk_pages must be positive")
    import fitz  # 懒加载：普通上传路径不因切分能力引入 PDF 依赖开销

    with fitz.open(pdf_path) as doc:
        total = doc.page_count
    return [
        PdfChunk(
            material_id=material_id,
            subject_id=subject_id,
            chunk_index=index,
            page_start=start,
            page_end=min(start + chunk_pages - 1, total),
            idempotency_key=_key(material_id, index, start, min(start + chunk_pages - 1, total)),
        )
        for index, start in enumerate(range(1, total + 1, chunk_pages), start=1)
    ]


def _extract_one(pdf_path: str, chunk: PdfChunk) -> dict[str, Any]:
    import fitz

    with fitz.open(pdf_path) as doc:
        pages = []
        for page_no in range(chunk.page_start, chunk.page_end + 1):
            text = (doc.load_page(page_no - 1).get_text("text") or "").strip()
            pages.append({"page": page_no, "text": text})
    return {**chunk.as_dict(), "pages": pages, "error": None}


def parse_pdf_chunks(
    pdf_path: str,
    *,
    material_id: int | str,
    subject_id: int | str | None = None,
    chunk_pages: int = 10,
    max_workers: int = 4,
) -> list[dict[str, Any]]:
    """并行提取各块文本，按 chunk_index 稳定排序。

    每个块独立打开 PDF，块失败会以 ``error`` 字段返回而不会丢失其它块；
    调用方可按幂等键重试失败块。``max_workers`` 至少为 1。
    """
    if max_workers <= 0:
        raise ValueError("max_workers must be positive")
    chunks = plan_pdf_chunks(
        pdf_path, material_id=material_id, subject_id=subject_id, chunk_pages=chunk_pages
    )
    if not chunks:
        return []
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(max_workers, len(chunks))) as pool:
        futures = {pool.submit(_extract_one, pdf_path, chunk): chunk for chunk in chunks}
        for future in as_completed(futures):
            chunk = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:  # 块级失败可重试，保留块身份与页码范围
                results.append({**chunk.as_dict(), "pages": [], "error": str(exc)[:500]})
    return sorted(results, key=lambda item: item["chunk_index"])


__all__ = ["PdfChunk", "plan_pdf_chunks", "parse_pdf_chunks"]
