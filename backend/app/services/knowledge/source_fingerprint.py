"""物理向量记录指纹权威：P06 原样迁入，JSON 类型与 IEEE754 binary32 精确比较。"""
from __future__ import annotations

import hashlib
import json
import math
import struct

EMBEDDING_COMPARISON = "ieee754-binary32-exact"
SOURCE_CHUNK_SET_FORMAT = "sc-records-binary32-v1"


def plain(value):
    if hasattr(value, "tolist"):
        return plain(value.tolist())
    if hasattr(value, "item"):
        return plain(value.item())
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value


def fingerprint(value):
    raw = json.dumps(plain(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def field_fingerprint(field, value):
    if field == "embedding":
        # Chroma SDK 写入固定 float32；HTTP 短十进制与 Rust 本地展开可能不同。
        # 比较每项 IEEE754 位型，不使用容差，也不修改实际复制值；1 ULP 变化仍失败。
        value = {"encoding": EMBEDDING_COMPARISON, "bytes": struct.pack(f"<{len(value)}f", *value).hex()}
    return fingerprint(value)


def record_fingerprint(row):
    # 正文/ID/metadata 仍按严格 JSON 值和类型比较，不能把 int、float、bool 合并。
    return fingerprint({key: field_fingerprint(key, row[key]) for key in ("id", "document", "metadata", "embedding")})


def read_records(collection, *, source=None, page_size=200):
    if page_size < 1:
        raise ValueError("page_size 必须为正数")
    rows, offset = [], 0
    while True:
        kwargs = {"where": {"source": source}} if source is not None else {}
        got = collection.get(limit=page_size, offset=offset, include=["documents", "metadatas", "embeddings"], **kwargs)
        ids = got.get("ids") or []
        if not ids:
            break
        columns = [got.get(key) for key in ("documents", "metadatas", "embeddings")]
        if any(column is None or len(column) != len(ids) for column in columns):
            raise ValueError("Chroma 响应缺少完整 documents/metadatas/embeddings")
        for index, rid in enumerate(ids):
            row = plain({"id": rid, "document": columns[0][index], "metadata": columns[1][index], "embedding": columns[2][index]})
            vector = row["embedding"]
            if not isinstance(rid, str) or not rid or not isinstance(row["document"], str) or not isinstance(row["metadata"], dict):
                raise ValueError("试点记录缺少 ID/正文/metadata，拒绝推测或补写")
            if not isinstance(vector, list) or not vector or any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in vector):
                raise ValueError("试点向量缺失或包含非法数值")
            if source is not None and row["metadata"].get("source") != source:
                raise ValueError("源文件范围不匹配，拒绝扩大范围")
            rows.append(row)
        offset += len(ids)
        if len(ids) < page_size:
            break
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("读取结果含重复 ID，源在分页期间可能变化")
    return sorted(rows, key=lambda row: row["id"])



def chunk_set_fingerprint(rows):
    """按稳定 ID 排序的逐记录摘要；不同于历史 raw-JSON records_sha256。"""
    return fingerprint([record_fingerprint(row) for row in sorted(rows, key=lambda row: row["id"])])
