"""分包知识绑定的只读授权范围。"""
import json

from ...models.models import KnowledgeBaseBinding


def enabled_query_bindings(db, agent_id):
    return (db.query(KnowledgeBaseBinding)
            .filter(KnowledgeBaseBinding.agent_id == agent_id,
                    KnowledgeBaseBinding.is_enabled.is_(True),
                    KnowledgeBaseBinding.allow_query.is_(True))
            .order_by(KnowledgeBaseBinding.priority.asc(), KnowledgeBaseBinding.id.asc()).all())


def authorized_file_scope(stored):
    if stored is None or stored == '' or (isinstance(stored, str) and not stored.strip()):
        return None
    try:
        values = json.loads(stored) if isinstance(stored, str) else stored
    except (ValueError, TypeError):
        return ()
    if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
        return ()
    return tuple(dict.fromkeys(value.strip() for value in values))
