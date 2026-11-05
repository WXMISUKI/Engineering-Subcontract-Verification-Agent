"""工程分包审查智能体统一访问守卫（BS-1B 统一归属与越权防护）。

授权链（与 OC/SC 既有「统一会话策略」同构）：

    当前用户
      -> ConversationMapping(id, user_id, user_type, agent_id, is_deleted)
      -> BsTask.conversation_id
      -> 子资源.task_id（material / finding / event）

设计约束：
- 授权逻辑**只写在本模块**，禁止复制到各路由函数；路由一律委托本模块。
- 守卫一律**显式接收** `user_id` / `user_type`，不依赖 FastAPI 全局上下文，便于
  服务层与测试复用。
- 守卫**返回已校验实体**，调用方不得重复查询。
- 只读判断：**不得**因授权校验自动修正历史数据（不写库）。
- 代理 slug 的单一真源是 ``manifest["code"]``，此处引用它，不硬编码字面量。
"""
from __future__ import annotations

import logging

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...models.models import Agent, ConversationMapping
from .manifest import manifest
from .models import BsFinding, BsMaterial, BsTask

logger = logging.getLogger(__name__)


def require_bs_conversation(db: Session, conversation_id: int, user_id: int,
                            user_type: str) -> ConversationMapping:
    """校验会话归属，返回已校验的 ``ConversationMapping``。

    判定顺序（BS-1B §2.1，逐条为验收点）：
      1. 会话不存在 -> 404；
      2. 会话已软删除（``is_deleted`` 为真）-> 404（与既有「统一会话策略」一致）；
      3. ``user_id`` / ``user_type`` 不匹配 -> 403（**不得**伪装成 404）；
      4. 关联 ``Agent.slug`` 不等于本智能体 -> 403。
    """
    mapping = (db.query(ConversationMapping)
               .filter(ConversationMapping.id == conversation_id)
               .first())
    if mapping is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if mapping.is_deleted:
        # 统一会话策略：软删除的会话视同不存在（404），与 OC/SC 口径一致。
        raise HTTPException(status_code=404, detail="会话不存在")
    if mapping.user_id != user_id or mapping.user_type != user_type:
        raise HTTPException(status_code=403, detail="无权访问该会话")
    agent = db.query(Agent).filter(Agent.id == mapping.agent_id).first()
    if agent is None or agent.slug != manifest["code"]:
        raise HTTPException(status_code=403, detail="该会话不属于本智能体")
    return mapping


def require_bs_task(db: Session, task_id: int, user_id: int, user_type: str) -> BsTask:
    """校验任务归属，返回已校验的 ``BsTask``。

    1. 任务不存在 -> 404；
    2. 走会话守卫（会话不存在/软删 -> 404；非本人或非本 Agent -> 403）；
    3. 任务 ``created_by`` / ``created_by_type`` 与会话用户不一致 -> 403，
       并落一条可审计 ``warning`` 日志（只记录定位需要的字段，不打印敏感信息）。
    """
    task = db.query(BsTask).filter(BsTask.id == task_id).first()
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    require_bs_conversation(db, task.conversation_id, user_id, user_type)
    if task.created_by != user_id or task.created_by_type != user_type:
        # 归属异常：会话属于当前用户，但任务创建者与之一致性校验失败。
        # 这是历史数据/写入路径不一致的信号，必须可审计；但仍返回 403（不静默放行）。
        logger.warning(
            "[bs] 任务归属异常 task_id=%s conversation_id=%s 期望=%s/%s 实际=%s/%s",
            task.id, task.conversation_id, user_id, user_type,
            task.created_by, task.created_by_type)
        raise HTTPException(status_code=403, detail="无权访问该任务")
    return task


def require_bs_material(db: Session, material_id: int, user_id: int,
                        user_type: str) -> BsMaterial:
    """校验材料归属，返回已校验的 ``BsMaterial``。

    材料不存在 -> 404；否则按 ``material.task_id`` 反查任务，走统一任务守卫。
    """
    material = db.query(BsMaterial).filter(BsMaterial.id == material_id).first()
    if material is None:
        raise HTTPException(status_code=404, detail="材料不存在")
    require_bs_task(db, material.task_id, user_id, user_type)
    return material


def require_bs_finding(db: Session, finding_id: int, user_id: int,
                       user_type: str) -> BsFinding:
    """校验结论归属，返回已校验的 ``BsFinding``。

    结论不存在 -> 404；否则按 ``finding.task_id`` 反查任务，走统一任务守卫。
    """
    finding = db.query(BsFinding).filter(BsFinding.id == finding_id).first()
    if finding is None:
        raise HTTPException(status_code=404, detail="结论不存在")
    require_bs_task(db, finding.task_id, user_id, user_type)
    return finding
