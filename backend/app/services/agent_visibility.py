"""独立应用仅开放分包智能体；任务归属仍由分包守卫检查。"""
from fastapi import HTTPException
from ..plus.blockchain_supervision.manifest import manifest


def require_agent_access(agent, current_user, db):
    if (agent is None or agent.slug != manifest['code'] or current_user is None
            or not getattr(current_user, 'is_active', False)):
        raise HTTPException(status_code=403, detail='无权访问该智能体')
