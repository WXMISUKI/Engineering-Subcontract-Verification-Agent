"""Validate rule ownership metadata without loading or changing runtime rules."""
from __future__ import annotations

import json
from pathlib import Path


CATALOG_PATH = Path(__file__).with_name("data") / "rule_catalog.json"
_SCHEMA_VERSION = "bs-rule-catalog.v1"
_MODES = {"admission", "contract_retrospective"}
_SCOPES = {"admission_only", "retrospective_only", "shared_infrastructure", "unresolved"}
_STATUSES = {"verified", "partial", "unverified", "retired"}
_FIELDS = {
    "id", "title", "mode_scope", "applies_to", "mapping_status", "source_kind",
    "source_path", "source_ref", "basis_ids", "owner_module", "entrypoints",
    "evidence_inputs", "evidence_outputs", "report_projection", "tests", "version",
}


class InvalidRuleCatalog(ValueError):
    """The ownership catalog is incomplete or contradictory."""


def _require_text(value, field, entry_id):
    if not isinstance(value, str) or not value.strip():
        raise InvalidRuleCatalog(f"{entry_id}: {field} 必须为非空字符串")


def _require_text_list(value, field, entry_id, *, allow_empty=False):
    if (not isinstance(value, list) or (not value and not allow_empty)
            or any(not isinstance(item, str) or not item.strip() for item in value)
            or len(set(value)) != len(value)):
        raise InvalidRuleCatalog(f"{entry_id}: {field} 必须为不重复的字符串列表")


def validate_rule_catalog(catalog: dict, *, project_root: Path | None = None) -> dict:
    """Check stable identifiers, explicit mode scope and traceable source references."""
    if not isinstance(catalog, dict) or set(catalog) != {"schema_version", "catalog_version", "entries"}:
        raise InvalidRuleCatalog("目录顶层字段无效")
    if catalog["schema_version"] != _SCHEMA_VERSION:
        raise InvalidRuleCatalog("不支持的目录版本")
    _require_text(catalog["catalog_version"], "catalog_version", "catalog")
    if not isinstance(catalog["entries"], list):
        raise InvalidRuleCatalog("entries 必须为列表")

    root = project_root or Path(__file__).resolve().parents[4]
    seen = set()
    for entry in catalog["entries"]:
        if not isinstance(entry, dict) or set(entry) != _FIELDS:
            raise InvalidRuleCatalog("条目字段不完整或包含未知字段")
        entry_id = entry.get("id")
        _require_text(entry_id, "id", "entry")
        if entry_id in seen:
            raise InvalidRuleCatalog(f"规则目录 ID 重复：{entry_id}")
        seen.add(entry_id)

        for field in ("title", "source_kind", "source_path", "source_ref", "owner_module", "report_projection", "version"):
            _require_text(entry[field], field, entry_id)
        for field in ("basis_ids", "entrypoints", "evidence_inputs", "evidence_outputs", "tests"):
            _require_text_list(entry[field], field, entry_id,
                               allow_empty=field == "basis_ids" or entry["mode_scope"] == "unresolved")

        scope, modes, status = entry["mode_scope"], entry["applies_to"], entry["mapping_status"]
        if (not isinstance(scope, str) or scope not in _SCOPES
                or not isinstance(status, str) or status not in _STATUSES):
            raise InvalidRuleCatalog(f"{entry_id}: mode_scope 或 mapping_status 无效")
        if (not isinstance(modes, list)
                or any(not isinstance(mode, str) or mode not in _MODES for mode in modes)
                or len(modes) != len(set(modes))):
            raise InvalidRuleCatalog(f"{entry_id}: applies_to 必须是明确且不重复的审核模式列表")
        expected = {
            "admission_only": {"admission"},
            "retrospective_only": {"contract_retrospective"},
            "shared_infrastructure": _MODES,
            "unresolved": set(),
        }[scope]
        if set(modes) != expected:
            raise InvalidRuleCatalog(f"{entry_id}: mode_scope 与 applies_to 不一致或适用范围未明确")
        if scope == "unresolved" and status == "verified":
            raise InvalidRuleCatalog(f"{entry_id}: 未明确适用范围不能标记 verified")
        if status == "verified" and scope != "unresolved":
            for field in ("entrypoints", "evidence_inputs", "evidence_outputs", "tests"):
                if not entry[field]:
                    raise InvalidRuleCatalog(f"{entry_id}: verified 条目缺少 {field}")

        source = (root / entry["source_path"]).resolve()
        if not source.is_relative_to(root.resolve()) or not source.is_file():
            raise InvalidRuleCatalog(f"{entry_id}: source_path 不存在：{entry['source_path']}")
        for test_path in entry["tests"]:
            test_file = (root / test_path).resolve()
            if not test_file.is_relative_to(root.resolve()) or not test_file.is_file():
                raise InvalidRuleCatalog(f"{entry_id}: tests 路径不存在：{test_path}")

    return catalog


def load_rule_catalog(path: Path = CATALOG_PATH, *, project_root: Path | None = None) -> dict:
    """Load and validate catalog metadata; callers opt in explicitly."""
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InvalidRuleCatalog("规则归属目录无法读取或解析") from exc
    return validate_rule_catalog(catalog, project_root=project_root)
