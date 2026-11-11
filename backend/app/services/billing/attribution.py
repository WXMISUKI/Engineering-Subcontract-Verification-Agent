"""智能体归因解析（设计 §18 第 4 条）。

规则：**只从既有归属列读，不猜**。
- 会话类：``conversation_mappings.agent_id``（chat / SR / OC / BS / SC 的任务都有
  conversation_id）；
- AS 会话：``as_sessions.agent_id``；
- Plus 任务：先按 ``scope_type`` 找到任务行拿 ``conversation_id``，再回到会话表；
- 解析不到（scope 缺失、行已删、列不存在）→ None，落库 NULL。

所有 plus 模型的导入都放在函数内：这些模块较重，计费旁路不应在导入期拉起它们。
"""

from __future__ import annotations

from typing import Optional

# scope_type → (模块路径, 类名, 主键列, conversation_id 列)
_SCOPE_TABLES = {
    "oc_task": ("app.plus.startup_conditions_check.models", "OcReviewTask", "id", "conversation_id"),
    "bs_task": ("app.plus.blockchain_supervision.models", "BsTask", "id", "conversation_id"),
    "sc_task": ("app.plus.structural_check.models", "ScDocSnapshot", "id", "conversation_id"),
    "sr_conversation": (None, None, "id", "conversation_id"),
}


def _import_model(path: str, name: str):
    import importlib

    return getattr(importlib.import_module(path), name)


def scope_conversation_id(db, scope_type: Optional[str], scope_id: Optional[str]) -> Optional[int]:
    """按 scope 找回 conversation_id；找不到返回 None。"""
    if db is None or not scope_type or scope_id in (None, ""):
        return None
    spec = _SCOPE_TABLES.get(scope_type)
    if spec is None:
        return None
    module_path, class_name, _pk_field, conv_field = spec
    try:
        if class_name is None:
            # sr_conversation 的 scope_id 本身就是 conversation_id
            return int(scope_id)
        model = _import_model(module_path, class_name)
        row = db.get(model, int(scope_id))
    except Exception:  # noqa: BLE001 —— 归因失败不阻断业务
        return None
    if row is None:
        return None
    value = getattr(row, conv_field, None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def scope_owner(db, scope_type: Optional[str], scope_id) -> Optional[tuple[str, int]]:
    """返回 scope 的归属 ``(user_type, user_id)``；查不到返回 None。

    只用于**鉴权**：不属于当前用户一律按 404 处理（不暴露存在性）。
    认不出的 scope_type 也返回 None。
    """
    if db is None or not scope_type or scope_id in (None, ""):
        return None
    try:
        if scope_type == "as_session":
            from ...models.models import AsSession

            row = db.get(AsSession, str(scope_id))
            if row is None:
                return None
            return (row.user_type, int(row.user_id))
        if scope_type == "chat":
            from ...models.models import ConversationMapping

            row = db.get(ConversationMapping, int(scope_id))
            if row is None:
                return None
            return (row.user_type, int(row.user_id))
        if scope_type == "sr_conversation":
            from ...models.models import ConversationMapping

            row = db.get(ConversationMapping, int(scope_id))
            if row is None:
                return None
            return (row.user_type, int(row.user_id))
        if scope_type == "oc_task":
            model = _import_model(
                "app.plus.startup_conditions_check.models", "OcReviewTask"
            )
            row = db.get(model, int(scope_id))
            if row is None:
                return None
            return (row.created_by_type or "web", int(row.created_by))
        if scope_type == "bs_task":
            model = _import_model("app.plus.blockchain_supervision.models", "BsTask")
            row = db.get(model, int(scope_id))
            if row is None:
                return None
            return (row.created_by_type or "web", int(row.created_by))
        if scope_type == "sc_task":
            model = _import_model("app.plus.structural_check.models", "ScDocSnapshot")
            row = db.get(model, int(scope_id))
            if row is None:
                return None
            conv_id = getattr(row, "conversation_id", None)
            if conv_id is None:
                return None
            from ...models.models import ConversationMapping

            mapping = db.get(ConversationMapping, int(conv_id))
            if mapping is None:
                return None
            return (mapping.user_type, int(mapping.user_id))
    except Exception:  # noqa: BLE001 —— 鉴权失败按「查不到」处理
        return None
    return None


def is_scope_owner(
    db, *, user_type: str, user_id: int, scope_type: Optional[str], scope_id
) -> bool:
    owner = scope_owner(db, scope_type, scope_id)
    return owner is not None and owner == (user_type, int(user_id))


def resolve_agent_id(
    db,
    *,
    agent_id: Optional[int] = None,
    conversation_id: Optional[int] = None,
    as_session_id: Optional[str] = None,
    scope_type: Optional[str] = None,
    scope_id: Optional[str] = None,
) -> Optional[int]:
    """显式 agent_id > AS 会话 > scope 任务 > 会话映射。解析不到返回 None。"""
    if agent_id is not None:
        return int(agent_id)
    if db is None:
        return None

    if as_session_id:
        try:
            from ...models.models import AsSession

            row = db.query(AsSession.agent_id).filter(AsSession.id == as_session_id).first()
            if row is not None and row[0] is not None:
                return int(row[0])
        except Exception:  # noqa: BLE001
            pass

    conv_id = conversation_id
    if conv_id is None:
        conv_id = scope_conversation_id(db, scope_type, scope_id)
    if conv_id is None:
        return None
    try:
        from ...models.models import ConversationMapping

        row = (
            db.query(ConversationMapping.agent_id)
            .filter(ConversationMapping.id == int(conv_id))
            .first()
        )
        if row is not None and row[0] is not None:
            return int(row[0])
    except Exception:  # noqa: BLE001
        return None
    return None