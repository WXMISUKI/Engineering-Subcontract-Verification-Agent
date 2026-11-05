"""请求级来源健康事实；物理向量核验与原件实算、人工采用、现行性分别处理。"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import re
from typing import Any, Protocol

from .port import EngineProvenance
from .source_fingerprint import SOURCE_CHUNK_SET_FORMAT, chunk_set_fingerprint, read_records


@dataclass(frozen=True)
class SourceIntegrityRequest:
    collection_name: str
    source_id: str
    expected_collection_uuid: str | None = None
    expected_chunk_count: int | None = None
    expected_chunk_set_sha256: str | None = None
    chunk_set_format: str | None = None
    expected_original_sha256: str | None = None
    original_path: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not value.strip() for value in (self.collection_name, self.source_id)):
            raise ValueError("来源核验必须指定集合及来源身份")
        for value in (self.expected_chunk_set_sha256, self.expected_original_sha256):
            if value is not None and (not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None):
                raise ValueError("来源核验摘要必须是 SHA256")
        count = self.expected_chunk_count
        if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < 0):
            raise ValueError("来源切片数量必须是非负整数")

    def to_dict(self) -> dict[str, Any]:
        return {"collection_name": self.collection_name, "source_id": self.source_id,
                "expected_collection_uuid": self.expected_collection_uuid,
                "expected_chunk_count": self.expected_chunk_count,
                "expected_chunk_set_sha256": self.expected_chunk_set_sha256,
                "chunk_set_format": self.chunk_set_format,
                "expected_original_sha256": self.expected_original_sha256}


@dataclass(frozen=True)
class SourceIntegrityOutcome:
    request: SourceIntegrityRequest
    engine: EngineProvenance
    scope_complete: bool = False
    collection_uuid: str | None = None
    collection_count: int | None = None
    source_count: int | None = None
    chunk_set_sha256: str | None = None
    chunk_set_format: str | None = None
    original_sha256: str | None = None
    identity_status: str = "undetermined"
    chunk_count_status: str = "undetermined"
    chunk_set_status: str = "undetermined"
    original_status: str = "undetermined"
    errors: tuple[str, ...] = ()

    @property
    def vector_verified(self) -> bool:
        return (self.engine.primary_engine == self.engine.actual_engine == "chroma"
                and not self.engine.degraded and self.scope_complete
                and self.source_count is not None and self.source_count > 0
                and self.identity_status == self.chunk_count_status == self.chunk_set_status == "verified")

    @property
    def verified(self) -> bool:
        return self.vector_verified and self.original_status == "verified"

    @property
    def detail(self) -> str | None:
        return ";".join(self.errors) or None

    @property
    def reason(self) -> str | None:
        return self.errors[0] if self.errors else None

    def to_dict(self) -> dict[str, Any]:
        return {"request": self.request.to_dict(), "engine": self.engine.to_dict(),
                "scope_complete": self.scope_complete, "collection_uuid": self.collection_uuid,
                "collection_count": self.collection_count, "source_count": self.source_count,
                "chunk_set_sha256": self.chunk_set_sha256, "chunk_set_format": self.chunk_set_format,
                "original_sha256": self.original_sha256, "identity_status": self.identity_status,
                "chunk_count_status": self.chunk_count_status, "chunk_set_status": self.chunk_set_status,
                "original_status": self.original_status, "vector_verified": self.vector_verified,
                "verified": self.verified, "errors": list(self.errors), "detail": self.detail}


class SourceIntegrityPort(Protocol):
    def inspect_source(self, request: SourceIntegrityRequest) -> SourceIntegrityOutcome: ...


def _original_fact(request: SourceIntegrityRequest) -> tuple[str | None, str, str | None]:
    if request.original_path is None:
        return None, "undetermined", "original_unavailable"
    try:
        digest = hashlib.sha256()
        with Path(request.original_path).open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        observed = digest.hexdigest()
    except (OSError, TypeError, ValueError):
        return None, "undetermined", "original_unavailable"
    if request.expected_original_sha256 is None:
        return observed, "undetermined", "original_expected_hash_missing"
    if observed != request.expected_original_sha256:
        return observed, "mismatch", "original_hash_mismatch"
    return observed, "verified", None


def unavailable_source_integrity(
    request: SourceIntegrityRequest, code: str, *, engine: EngineProvenance | None = None,
) -> SourceIntegrityOutcome:
    original, original_status, original_error = _original_fact(request)
    provenance = engine or EngineProvenance("chroma", None, True, code)
    if not provenance.degraded:
        provenance = EngineProvenance(provenance.primary_engine, provenance.actual_engine, True, code)
    return SourceIntegrityOutcome(
        request, provenance,
        original_sha256=original, original_status=original_status,
        errors=tuple(value for value in (code, original_error) if value),
    )


def inspect_chroma_source(
    client: Any, request: SourceIntegrityRequest, *, engine: EngineProvenance | None = None,
) -> SourceIntegrityOutcome:
    """只读集合 UUID、完整来源切片及原件；不调用 embedding、不借用 SQLite 结果。"""
    provenance = engine or EngineProvenance("chroma", "chroma", False)
    if (provenance.primary_engine != "chroma" or provenance.actual_engine != "chroma"
            or provenance.degraded):
        return unavailable_source_integrity(request, "wrong_or_fallback_engine", engine=provenance)
    try:
        collection = client.get_collection(name=request.collection_name, embedding_function=None)
        observed_uuid = str(collection.id)
        collection_count = collection.count()
        if isinstance(collection_count, bool) or not isinstance(collection_count, int) or collection_count < 0:
            raise ValueError("集合计数无效")
        rows = read_records(collection, source=request.source_id)
        after = client.get_collection(name=request.collection_name, embedding_function=None)
        if str(after.id) != observed_uuid or after.count() != collection_count:
            return unavailable_source_integrity(request, "collection_changed_during_check", engine=provenance)
        if len(rows) > collection_count:
            return unavailable_source_integrity(request, "source_count_exceeds_collection", engine=provenance)
        observed_digest = chunk_set_fingerprint(rows)
    except ValueError:
        return unavailable_source_integrity(request, "source_records_invalid", engine=provenance)
    except Exception:
        # 不把任何异常正文（可能含路径、连接或凭据）送入报告，也不把未取到数说成 0。
        return unavailable_source_integrity(request, "source_backend_unavailable", engine=provenance)

    errors: list[str] = []
    identity_status = "undetermined"
    if request.expected_collection_uuid is None:
        errors.append("collection_uuid_missing")
    elif observed_uuid != request.expected_collection_uuid:
        identity_status = "mismatch"
        errors.append("collection_uuid_mismatch")
    else:
        identity_status = "verified"
    count_status = "undetermined"
    if request.expected_chunk_count is None:
        errors.append("expected_chunk_count_missing")
    elif len(rows) != request.expected_chunk_count:
        count_status = "mismatch"
        errors.append("chunk_count_mismatch")
    else:
        count_status = "verified"
    chunk_status = "undetermined"
    if request.chunk_set_format != SOURCE_CHUNK_SET_FORMAT:
        errors.append("chunk_fingerprint_format_unknown")
    elif request.expected_chunk_set_sha256 is None:
        errors.append("expected_chunk_fingerprint_missing")
    elif observed_digest != request.expected_chunk_set_sha256:
        chunk_status = "mismatch"
        errors.append("chunk_set_mismatch")
    else:
        chunk_status = "verified"
    if not rows:
        errors.append("source_empty")
    original, original_status, original_error = _original_fact(request)
    if original_error:
        errors.append(original_error)
    return SourceIntegrityOutcome(
        request, provenance, scope_complete=True, collection_uuid=observed_uuid,
        collection_count=collection_count, source_count=len(rows),
        chunk_set_sha256=observed_digest, chunk_set_format=SOURCE_CHUNK_SET_FORMAT,
        original_sha256=original, identity_status=identity_status, chunk_count_status=count_status,
        chunk_set_status=chunk_status, original_status=original_status, errors=tuple(errors),
    )
