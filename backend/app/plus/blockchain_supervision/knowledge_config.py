"""工程分包审查知识库路由配置。"""
from __future__ import annotations

import os
from typing import Any, Dict

from ...services.knowledge.factory import parse_kb_map
from .workflow_config import BS_COLLECTION_PREFIX, bs_collection


def parse_bs_kb_map(raw: str) -> Dict[str, Any]:
    """解析 BS_WEKNORA_KB_MAP，拒绝非 bs_ 前缀的集合名。"""
    return parse_kb_map(
        raw,
        env_name="BS_WEKNORA_KB_MAP",
        prefix=BS_COLLECTION_PREFIX,
    )


def _bs_kb_ids_for_collection(collection_name: str) -> list[str]:
    """按 BS 完整集合名解析目标物理库，未登记时返回空列表。"""
    from ...config import get_settings

    raw = os.getenv("BS_WEKNORA_KB_MAP")
    if raw is None:
        raw = getattr(get_settings(), "BS_WEKNORA_KB_MAP", "") or ""
    value = parse_bs_kb_map(raw).get(bs_collection(collection_name))
    if not value:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]
