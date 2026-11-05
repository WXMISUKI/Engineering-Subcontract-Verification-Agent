"""工程分包审查的知识库边界适配器。

这里只负责把任务会话映射到公共知识层，不能自行读取凭据、拼 WeKnora URL 或扩大库范围。
当前未绑定分包知识库时返回 ``undetermined(no_target)``，调用方必须把它展示为依据不可用。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ...models.models import ConversationMapping
from ...services.knowledge.factory import MODULE_BLOCKCHAIN_SUPERVISION
from ...services.knowledge.multi_kb_query import (
    MultiKbQueryResult,
    apply_target_selection,
    resolve_query_targets,
    run_multi_kb_query,
)
from .models import BsTask
from . import source_manifest
from ...standalone.reference_mapping import apply_reference_mapping, load_collection_map


ROAD_DEFAULT_COLLECTIONS = (
    "road_shanghai_basis", "road_national_basis", "road_shanghai_checklist",
    "road_shanghai_rules",
)
ROAD_GROUP_COLLECTIONS = ("road_group_basis",)
SHARED_COLLECTIONS = ("shared_qualification", "shared_watchlist", "shared_cases")
SUPPORTED_JURISDICTIONS = {"road"}
SUPPORTED_PROFILES = {"road_default", "road_group"}


def resolve_agent_id(db: Session, task: BsTask) -> int | None:
    mapping = (db.query(ConversationMapping.agent_id)
               .filter(ConversationMapping.id == task.conversation_id,
                       ConversationMapping.is_deleted.is_(False)).first())
    return mapping[0] if mapping else None


def resolve_task_route(task: BsTask) -> dict:
    """解析任务的知识路由；身份不完整时走公路默认 profile，不扩大目标范围。"""
    jurisdiction = str(getattr(task, "jurisdiction", None) or "").strip().lower() or None
    explicit = str(getattr(task, "profile_code", None) or "").strip().lower() or None
    if jurisdiction and jurisdiction not in SUPPORTED_JURISDICTIONS:
        return {"status": "undetermined", "profile_code": None,
                "reason_code": "unsupported_jurisdiction",
                "reason": "当前辖域尚未配置可用的知识库路由"}
    if explicit and explicit not in SUPPORTED_PROFILES:
        return {"status": "undetermined", "profile_code": None,
                "reason_code": "unsupported_profile",
                "reason": "当前 profile 尚未配置可用的知识库路由"}
    if explicit:
        return {"status": "resolved", "profile_code": explicit,
                "reason_code": "explicit_profile", "reason": "任务显式指定 profile"}

    # 保持既有知识库路由，项目补充清单撤销不应缩小已配置的知识检索范围。
    def compact(value):
        return re.sub(r"[\s（）()—–－_\-/]", "", str(value or "")).lower()

    def matches(value):
        return "g15公路嘉金段" in value or ("g1503公路" in value and "s32公路" in value)

    def is_other(value):
        return any(marker in value for marker in ("其他项目", "其他标段", "非本项目", "非当前项目", "外部项目"))

    project = compact(getattr(task, "project_name", None))
    section = compact(getattr(task, "contract_section", None))
    conflict = (matches(project) and is_other(section)) or (matches(section) and is_other(project))
    if not conflict and matches(project + section):
        return {"status": "resolved", "profile_code": "road_group",
                "reason_code": "project_profile_matched", "reason": "项目匹配集团知识库路由"}

    return {"status": "resolved", "profile_code": "road_default",
            "reason_code": "road_default_fallback", "reason": "未指定集团 profile，使用公路默认路由"}


def _configured_kb_ids(collections: tuple[str, ...]) -> set[str]:
    from .knowledge_config import parse_bs_kb_map
    from ...config import get_settings

    raw = os.getenv("BS_WEKNORA_KB_MAP")
    if raw is None:
        raw = getattr(get_settings(), "BS_WEKNORA_KB_MAP", "") or ""
    mapping = parse_bs_kb_map(raw)
    if not mapping:
        mapping_path = os.environ.get('BS_REFERENCE_MAPPING_FILE', '').strip()
        if mapping_path:
            mapping = {'bs_' + collection: kb for collection, kb in load_collection_map(Path(mapping_path)).items()}
    ids: set[str] = set()
    for name in collections:
        value = mapping.get("bs_" + name)
        if isinstance(value, (list, tuple)):
            ids.update(str(item) for item in value if item)
        elif value:
            ids.add(str(value))
    return ids


def _is_group_or_unsupported_target(target) -> bool:
    text = "%s %s" % (getattr(target, "kb_name", ""), getattr(target, "kb_id", ""))
    text = text.lower()
    return any(marker in text for marker in ("group", "集团", "water", "waterway", "hydraulic", "水利", "水运"))


def _route_targets(targets: list, profile_code: str) -> list:
    collections = list(ROAD_DEFAULT_COLLECTIONS) + list(SHARED_COLLECTIONS)
    if profile_code == "road_group":
        collections.extend(ROAD_GROUP_COLLECTIONS)
    allowed_ids = _configured_kb_ids(tuple(collections))
    if allowed_ids or os.environ.get('BS_REFERENCE_MAPPING_FILE', '').strip() or os.environ.get('BS_WEKNORA_KB_MAP', '').strip():
        return [target for target in targets if target.kb_id in allowed_ids]
    # 测试/旧环境可能没有 BS_WEKNORA_KB_MAP；只作保守显示名兜底，不把未知目标扩成全量。
    if profile_code == "road_default":
        return [target for target in targets if not _is_group_or_unsupported_target(target)]
    return [target for target in targets if not any(
        marker in ("%s %s" % (getattr(target, "kb_name", ""), getattr(target, "kb_id", ""))).lower()
        for marker in ("water", "waterway", "hydraulic", "水利", "水运"))]


def _undetermined(reason: str, *, targets: list | None = None) -> MultiKbQueryResult:
    return MultiKbQueryResult(
        status="undetermined", items=[], failed_kbs=[], succeeded_kbs=[],
        undetermined_reason=reason, per_kb=[], total_targets=len(targets or []),
        target_scopes=[],
    )


def _confirmed_group_file_targets(targets: list, selection: Any) -> list:
    """只允许已确认的 118 号文精确查询，且不得超出绑定的文件范围。"""
    if not isinstance(selection, dict) or selection.get("kb_enabled") is False:
        return []
    try:
        entry = next(row for row in source_manifest.load_registered_manifest()["files"]
                     if row.get("document_no") == "沪城公路〔2025〕118号"
                     and row.get("selection") == "selected"
                     and row.get("target_collection") == "road_group_basis")
        record = entry["ingest_record"]
        mapping_path = os.environ.get('BS_REFERENCE_MAPPING_FILE', '').strip()
        if not mapping_path:
            return []
        source = {'original_sha256': entry['sha256'], 'kb_id': record['kb_id'],
                  'knowledge_id': record['knowledge_id']}
        mapped = apply_reference_mapping({'sources': [source]}, Path(mapping_path))['sources'][0]
        kb, file_id = mapped['kb_id'], mapped['knowledge_id']
    except (KeyError, StopIteration, OSError, ValueError, TypeError):
        return []
    if selection.get("kb_ids") != [kb] or selection.get("knowledge_ids") != [file_id]:
        return []
    if "files_by_kb" in selection or "targets" in selection:
        return []
    group_targets = _route_targets(targets, "road_group")
    return [target for target in group_targets if target.kb_id == kb
            and (target.knowledge_ids is None or file_id in target.knowledge_ids)]


def query_subcontract_knowledge(
    db: Session,
    task: BsTask,
    query_text: str,
    **kwargs: Any,
) -> MultiKbQueryResult:
    """按任务 profile 收窄工程分包绑定；不绑定或无法路由时不触网。"""
    agent_id = resolve_agent_id(db, task)
    if agent_id is None:
        return _undetermined("no_target")
    all_targets = resolve_query_targets(db, agent_id)
    if not all_targets:
        return _undetermined("no_target")
    route = resolve_task_route(task)
    if route["status"] != "resolved":
        return _undetermined(route["reason_code"])
    selection = kwargs.pop("selection", None)
    targets = _route_targets(all_targets, route["profile_code"])
    if route["profile_code"] == "road_default":
        existing = {target.target_key for target in targets}
        targets.extend(target for target in _confirmed_group_file_targets(all_targets, selection)
                       if target.target_key not in existing)
    if not targets:
        return _undetermined("profile_target_missing")
    targets = apply_target_selection(targets, selection)
    if not targets:
        return _undetermined("selection_target_missing")
    return run_multi_kb_query(
        targets, query_text, module=MODULE_BLOCKCHAIN_SUPERVISION, **kwargs,
    )

