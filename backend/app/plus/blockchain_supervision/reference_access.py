"""按冻结引用和当前权限读取同版本原件，不存原件、不重新检索。"""
from __future__ import annotations

import hashlib
import re

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...models.models import Agent, ConversationMapping, KnowledgeRef
from ...services.agent_visibility import require_agent_access
from ...services.knowledge.context import OWNER_SR_CONVERSATION
from ...services.knowledge.reference_access import (
    ReferenceOriginalAccessError, authorized_reference_provider, read_authorized_reference_original,
)
from . import access, reference_snapshots
from .models import BsReviewVersion, BsTask

MAX_ORIGINAL_BYTES = 20 * 1024 * 1024


def _frozen_reference(db: Session, task: BsTask, reference_id: str, version_no: int | None) -> dict:
    if version_no is None:
        snapshot = reference_snapshots.get_reference_snapshot(db, task)
    else:
        version = db.query(BsReviewVersion).filter_by(task_id=task.id, version_no=version_no).first()
        if version is None:
            raise HTTPException(404, "审查版本不存在")
        snapshot = (version.snapshot_json or {}).get("reference_snapshot")
    # canonical run视图是引用ID单一真源；步骤聚合会重复同一引用，不按文档名猜测。
    found = [reference for run in (snapshot or {}).get("runs") or []
             for reference in run.get("references") or []
             if run.get("state") == "completed" and reference.get("reference_id") == reference_id]
    if len(found) != 1:
        raise HTTPException(404, "本次审查没有该原文引用，请从审查依据中打开")
    return found[0]


def _reference_descriptor(task: BsTask, reference: dict) -> KnowledgeRef:
    """临时归属对象只交给公共授权端口，不写knowledge_ref。"""
    return KnowledgeRef(
        owner_type=OWNER_SR_CONVERSATION, owner_key=str(task.conversation_id),
        provider_id=reference.get("provider_id"), kb_id=reference.get("kb_id"),
        knowledge_id=reference.get("knowledge_id"),
    )


def _authorize(db: Session, task: BsTask, reference: dict, user):
    provider = authorized_reference_provider(db, _reference_descriptor(task, reference), user)
    if provider is None:
        raise HTTPException(404, "该原文当前未获授权，请联系管理员核对知识库权限")
    mapping = db.get(ConversationMapping, task.conversation_id)
    agent = db.get(Agent, mapping.agent_id)
    require_agent_access(agent, user, db)
    return provider


def read_reference_original(db: Session, *, task_id: int, reference_id: str,
                            version_no: int | None, user) -> bytes:
    """任务守卫先于一切外部读取，下载后复核撤权及引用生命周期。"""
    task = access.require_bs_task(db, task_id, user.id, user.user_type)
    reference = _frozen_reference(db, task, reference_id, version_no)
    expected = reference.get("original_sha256")
    if not isinstance(expected, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", expected):
        raise HTTPException(409, "该引用未冻结可核验的原件版本，请人工查阅原件")
    provider = _authorize(db, task, reference, user)
    connection = (provider.id, provider.base_url, provider.secret_ref, provider.tenant_ref)
    try:
        data = read_authorized_reference_original(
            db, _reference_descriptor(task, reference), user, max_bytes=MAX_ORIGINAL_BYTES)
    except ReferenceOriginalAccessError as exc:
        raise HTTPException(exc.status_code, exc.message) from None
    if hashlib.sha256(data).hexdigest() != expected.lower():
        raise HTTPException(409, "知识库原件与本次审查冻结版本不一致，请人工核对，历史引用仍保留")
    if not data.startswith(b"%PDF-"):
        raise HTTPException(409, "该引用原件不是可预览的PDF，请人工查阅原件")
    # 网络等待时允许撤权/重跑；释放旧数据库快照后再次核验，不以历史授权放行。
    db.rollback()
    db.expire_all()
    current_task = access.require_bs_task(db, task_id, user.id, user.user_type)
    current_reference = _frozen_reference(db, current_task, reference_id, version_no)
    current_provider = _authorize(db, current_task, current_reference, user)
    if (current_reference != reference or connection != (
            current_provider.id, current_provider.base_url, current_provider.secret_ref,
            current_provider.tenant_ref)):
        raise HTTPException(409, "原文引用或访问配置已变化，请刷新后重新打开")
    return data
