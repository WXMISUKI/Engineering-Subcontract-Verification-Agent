"""工程分包审查智能体 HTTP 层。前缀 /api/plus/blockchain-supervision（registry 自动挂载）。"""
import logging
import json
import os
from copy import deepcopy
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, StrictInt
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from ...auth.unified_user import get_current_any_user
from ...database import SessionLocal, get_db
from ...limiter import limiter
from ...utils.download import attachment_disposition
from . import (access, admission, ai_advisory_snapshots, checklist, export, reporting, service,
               severity, zip_ingestion, knowledge_runs, reference_snapshots, rules, ruleset)
from . import source_manifest, finding_text, background_ai_review
from . import reference_visibility
from . import material_versions, supplement_generation, section_projection, material_confirmation
from . import parsing
from .rules import load_report_checklist
from .reference_access import read_reference_original
from .ai_current_guard import AiContextChanged, assert_ai_context_current
from .classify import ROLE_LABELS
from .manifest import manifest
from .models import STATUS_LABELS, BsFinding, BsMaterial, BsMaterialConfirmation, BsPacket, BsSubject, BsTask, BsTaskEvent

router = APIRouter(tags=[f"plus:{manifest['code']}"])
logger = logging.getLogger(__name__)


def _is_material_revision_schema_error(exc: OperationalError) -> bool:
    """识别正式库尚未完成 D9 列的只读故障，不吞掉其他数据库错误。"""
    text = str(exc).lower()
    return (
        "unknown column" in text
        and "bs_material" in text
        and ("revision_no" in text or "replacement_target_id" in text)
    )


@router.get("/ping")
def ping():
    return {"code": manifest["code"], "ok": True}


@router.get("/sources/admission")
def source_admission_audit(target_scope: str = "road_mvp",
                           user=Depends(get_current_any_user)):
    """来源准入只读审计；不读取原件、不上传、不激活、不写库。"""
    if target_scope not in {"road_mvp", "project_sample", "future_domain", "project_baseline"}:
        raise HTTPException(status_code=400, detail="target_scope 不在允许的审计范围内")
    return admission.project_manifest_admission(
        source_manifest.load_registered_manifest(), target_scope=target_scope
    )


class TaskCreateIn(BaseModel):
    conversation_id: int
    title: str
    scope_a1: bool = False
    scope_a3: bool = False
    # A-03（B1）标段锚点：可选（历史「一会话一核查」流程不强制填）
    project_name: Optional[str] = None
    contract_section: Optional[str] = None
    baseline_date: Optional[date] = None
    jurisdiction: Optional[str] = None
    profile_code: Optional[str] = None


class SectionIn(BaseModel):
    """A-03/P4-05 标段与路由字段：全可选；未出现的字段不改动。"""
    project_name: Optional[str] = None
    contract_section: Optional[str] = None
    baseline_date: Optional[date] = None
    jurisdiction: Optional[str] = None
    profile_code: Optional[str] = None


class MaterialIn(BaseModel):
    file_name: str
    size_kb: int = 0
    subject_id: Optional[int] = None
    packet_id: Optional[int] = None
    material_category: Optional[str] = None
    via: str = "upload"
    role: str = None  # 人工指定归属时传；缺省走自动归类


class RoleCorrectIn(BaseModel):
    role: str


class ScopeIn(BaseModel):
    scope_a1: bool
    scope_a3: bool


class BatchAdvisoryIn(BaseModel):
    run_id: str
    resume_partial: bool = False


class ReviewIn(BaseModel):
    decision: str  # accepted / ignored（旧 confirm / reject 仅作输入兼容）
    note: str = None


class MaterialConfirmationIn(BaseModel):
    decision: str
    token: str
    expected_confirmation_id: Optional[StrictInt] = None
    material_id: Optional[StrictInt] = None
    subitem_key: Optional[str] = None


class RerunIn(BaseModel):
    """重跑请求体（设计 98 §4.1）：整省可缺省=全量，兼容旧前端；scope 白名单在端点内 400。

    `item_ids` 刻意不写成 `list[str]`：契约要求「非字符串 → 400」而不是 pydantic 422，
    形态校验统一走 service._clean_rerun_item_ids，端点只负责把错误映射成 400。
    """
    scope: str = "all"
    item_ids: Optional[list] = None


class SupplementPlanIn(BaseModel):
    """P8-03C 补件局部核查计划；只登记计划，不立即执行核查。"""
    generation_id: str
    category: str
    item_ids: Optional[list] = None
    material_ids: Optional[list] = None
    subject_ids: Optional[list] = None
    material_status: str = "ready"
    parent_review_run_id: Optional[str] = None


class PageTextRefreshIn(BaseModel):
    material_id: StrictInt
    page_nos: list[StrictInt]


class SubjectIn(BaseModel):
    subject_type: Optional[str] = None
    name: Optional[str] = None


class SubjectTypeIn(BaseModel):
    subject_type: str


class SubjectNameIn(BaseModel):
    name: str


class PacketIn(BaseModel):
    subject_id: int
    batch_name: Optional[str] = None


class MaterialSubjectIn(BaseModel):
    subject_id: int
    packet_id: Optional[int] = None


@router.patch("/tasks/{task_id}/scope")
def patch_scope(task_id: int, body: ScopeIn, db: Session = Depends(get_db), user=Depends(get_current_any_user)):
    """setup 段调整核查范围（开始核查后 409）。"""
    task = _get_task(db, task_id, user)
    _require_setup(task, "核查已开始，范围不可变更")
    try:
        task = service.set_scope(db, task, scope_a1=body.scope_a1, scope_a3=body.scope_a3)
    except ValueError as e:
        raise HTTPException(status_code=409 if task.state != "setup" else 400, detail=str(e))
    return {"task_id": task.id, "scope": {"A1": task.scope_a1, "A3": task.scope_a3}}


@router.patch("/tasks/{task_id}/section")
def patch_section(task_id: int, body: SectionIn, db: Session = Depends(get_db),
                  user=Depends(get_current_any_user)):
    """setup 段调整标段锚点（A-03；开始核查后 409）。

    PATCH 语义：请求体只含要改的字段——以 `model_fields_set` 区分「未提供」与
    「显式置空」，未提供字段保留原值。照抄 patch_scope 的状态门（409 when != setup）。
    """
    task = _get_task(db, task_id, user)
    _require_setup(task, "核查已开始，标段信息不可变更")
    fields = body.model_fields_set
    kwargs = {
        "project_name": body.project_name if "project_name" in fields else task.project_name,
        "contract_section": body.contract_section if "contract_section" in fields else task.contract_section,
        "baseline_date": body.baseline_date if "baseline_date" in fields else task.baseline_date,
        "jurisdiction": body.jurisdiction if "jurisdiction" in fields else task.jurisdiction,
        "profile_code": body.profile_code if "profile_code" in fields else task.profile_code,
    }
    try:
        task = service.set_section(db, task, baseline_date_changed="baseline_date" in fields, **kwargs)
    except ValueError as e:
        raise HTTPException(status_code=409 if task.state != "setup" else 400, detail=str(e))
    return {"task_id": task.id, "section": _section_dto(task), "routing": _routing_dto(task)}


@router.delete("/tasks/{task_id}/materials/{material_id}")
def remove_material(task_id: int, material_id: int, db: Session = Depends(get_db),
                    user=Depends(get_current_any_user)):
    task = _get_task(db, task_id, user)
    try:
        service.delete_material(db, task, material_id)
    except LookupError:
        raise HTTPException(status_code=404, detail="材料不存在")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": True}


@router.get("/tasks")
def list_tasks(conversation_id: int, db: Session = Depends(get_db), user=Depends(get_current_any_user)):
    """会话内任务列表（历史恢复：刷新/切回会话后工作台按此回填）。

    BS-1B：先校验该会话归属当前用户及本智能体，查询再追加 created_by/created_by_type
    过滤——不得只依赖客户端传入的 conversation_id。
    """
    access.require_bs_conversation(db, conversation_id, user.id, user.user_type)
    tasks = (db.query(BsTask)
             .filter(BsTask.conversation_id == conversation_id,
                     BsTask.created_by == user.id,
                     BsTask.created_by_type == user.user_type)
             .order_by(BsTask.id.desc())
             .all())
    return {"tasks": [
        {"task_id": t.id, "title": t.title, "state": t.state,
         "scope": {"A1": t.scope_a1, "A3": t.scope_a3}, "scale": t.scale,
         "section": _section_dto(t),
         "routing": _routing_dto(t),
         "created_at": t.created_at.isoformat() if t.created_at else None}
        for t in tasks
    ]}


@router.post("/tasks")
def create_task(body: TaskCreateIn, db: Session = Depends(get_db), user=Depends(get_current_any_user)):
    # BS-1B：会话归属前置校验（不存在/软删 → 404；非本人或非本 Agent → 403）。
    access.require_bs_conversation(db, body.conversation_id, user.id, user.user_type)
    try:
        task = service.create_task(
            db, conversation_id=body.conversation_id, title=body.title,
            scope_a1=body.scope_a1, scope_a3=body.scope_a3, created_by=user.id,
            created_by_type=user.user_type,
            project_name=body.project_name, contract_section=body.contract_section,
            baseline_date=body.baseline_date,
            jurisdiction=body.jurisdiction, profile_code=body.profile_code,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"task_id": task.id, "state": task.state, "scale": task.scale,
            "section": _section_dto(task), "routing": _routing_dto(task)}


def _get_task(db: Session, task_id: int, user) -> BsTask:
    """任务读取（BS-1B 统一归属守卫）：委托 access.require_bs_task。

    保留原名以最小化 diff；授权逻辑集中在 ``access.py``，此处不得复制。
    """
    return access.require_bs_task(db, task_id, user.id, user.user_type)


def _require_setup(task: BsTask, message: str) -> None:
    """setup 写入口 HTTP 门禁（T10-B §7.3.3）。

    与 `service._setup_write_allowed` 同一口径：**只有 `setup` 可写**。`preparing`
    期间子材料正在派生，改归属/删材料会让「继承快照」与已落盘子材料不一致，故与
    running 同待遇拒绝，并给出**可读原因**（AGENTS「护栏必须可理解」）。
    """
    if task.state == service.TASK_STATE_PREPARING:
        raise HTTPException(
            status_code=409,
            detail="报审资料包正在准备中，暂时不能修改材料；请等待准备完成后再操作")
    if task.state != service.TASK_STATE_SETUP:
        raise HTTPException(status_code=409, detail=message)


def _subject_dto(s: BsSubject) -> dict:
    # T1-v2 §五：三列落库（T2 A-8）后一并输出 AI 识别与冲突状态
    name_status = service.subject_name_status(s)
    return {"subject_id": s.id, "stable_code": s.stable_code, "name": s.name,
            "name_status": name_status,
            "name_warning": service.subject_name_warning(s),
            "subject_type": s.subject_type, "type_confirmed": s.type_confirmed,
            "type_locked": s.type_locked,
            "ai_subject_type": s.ai_subject_type,
            "type_conflict": s.type_conflict,
            "type_conflict_reason": s.type_conflict_reason,
            "origin": s.origin, "retired": bool(s.retired)}


@router.post("/tasks/{task_id}/subjects")
def create_subject(task_id: int, body: SubjectIn, db: Session = Depends(get_db),
                   user=Depends(get_current_any_user)):
    task = _get_task(db, task_id, user)
    _require_setup(task, "核查已开始，不能新增分包主体")
    try:
        subject = service.create_subject(db, task, subject_type=body.subject_type,
                                         name=body.name, operator=user.id)
    except ValueError as exc:
        raise HTTPException(status_code=409 if task.state != "setup" else 400, detail=str(exc))
    return _subject_dto(subject)


@router.get("/tasks/{task_id}/subjects")
def list_subjects(task_id: int, db: Session = Depends(get_db), user=Depends(get_current_any_user)):
    task = _get_task(db, task_id, user)
    active_ids = service.active_subject_ids(db, task)
    subjects = (db.query(BsSubject)
                .filter(BsSubject.task_id == task.id, BsSubject.id.in_(active_ids))
                .order_by(BsSubject.id).all())
    return {"subjects": [_subject_dto(s) for s in subjects]}


@router.patch("/tasks/{task_id}/subjects/{subject_id}/type")
def confirm_subject_type(task_id: int, subject_id: int, body: SubjectTypeIn,
                         db: Session = Depends(get_db), user=Depends(get_current_any_user)):
    task = _get_task(db, task_id, user)
    _require_setup(task, "核查已开始，主体类型不可变更")
    try:
        subject = service.set_subject_type(db, task, subject_id, body.subject_type,
                                           operator=user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _subject_dto(subject)


@router.patch("/tasks/{task_id}/subjects/{subject_id}/name")
def rename_subject(task_id: int, subject_id: int, body: SubjectNameIn,
                   db: Session = Depends(get_db), user=Depends(get_current_any_user)):
    """setup 阶段确认或修改自动抽取的分包单位名称。"""
    task = _get_task(db, task_id, user)
    _require_setup(task, "核查已开始，主体名称不能修改")
    try:
        subject = service.rename_subject(db, task, subject_id, body.name, operator=user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409 if task.state != "setup" else 400, detail=str(exc))
    return _subject_dto(subject)


@router.post("/tasks/{task_id}/subjects/{subject_id}/lock")
def lock_subject_type(task_id: int, subject_id: int,
                      db: Session = Depends(get_db), user=Depends(get_current_any_user)):
    """显式提前锁定主体类型（lock_source=manual，T1-v2 §三）。

    不能替代 start_review 事务内的最终批量锁定：启动时对未锁定但已确认且
    无冲突的主体统一置锁（lock_source=start）。"""
    task = _get_task(db, task_id, user)
    _require_setup(task, "核查已开始，主体类型已随启动锁定")
    try:
        subject = service.lock_subject_type(db, task, subject_id,
                                            lock_source="manual", operator=user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _subject_dto(subject)


@router.post("/tasks/{task_id}/packets")
def create_packet(task_id: int, body: PacketIn, db: Session = Depends(get_db),
                  user=Depends(get_current_any_user)):
    task = _get_task(db, task_id, user)
    _require_setup(task, "核查已开始，不能新增报审资料批次")
    try:
        packet = service.create_packet(db, task, subject_id=body.subject_id,
                                       batch_name=body.batch_name, operator=user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"packet_id": packet.id, "subject_id": packet.subject_id, "batch_name": packet.batch_name}


@router.post("/tasks/{task_id}/materials/{material_id}/subject")
def assign_material_subject(task_id: int, material_id: int, body: MaterialSubjectIn,
                            db: Session = Depends(get_db), user=Depends(get_current_any_user)):
    task = _get_task(db, task_id, user)
    _require_setup(task, "核查已开始，不能再改派材料")
    try:
        if not service.is_active_subject(db, task, body.subject_id):
            raise LookupError("关联主体已无有效材料，请先上传分包合同")
        mat = service.assign_material_subject(db, task, material_id, body.subject_id,
                                              body.packet_id, operator=user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"material_id": mat.id, "subject_id": mat.subject_id, "packet_id": mat.packet_id}


def _section_dto(task: BsTask) -> dict:
    """A-03 标段锚点 DTO（列表/详情/标段端点共用，单一表源）。"""
    return {
        "project_name": task.project_name,
        "contract_section": task.contract_section,
        "baseline_date": task.baseline_date.isoformat() if task.baseline_date else None,
    }


def _routing_dto(task: BsTask) -> dict:
    """P4-05 路由字段单独输出，保持既有 section DTO 兼容。"""
    from .knowledge import resolve_task_route

    route = resolve_task_route(task)
    return {
        "jurisdiction": task.jurisdiction,
        "profile_code": task.profile_code,
        "resolved_profile": route["profile_code"],
        "status": route["status"],
        "reason_code": route["reason_code"],
    }


def _material_dto(m: BsMaterial, container_names: dict = None,
                  version_fact: dict | None = None) -> dict:
    """材料条目 DTO（单一表源，T4.1-T2 §2.1）。

    - `material_id` 是权威键；`id` 为兼容过渡**双写同值**（既有前端与测试读 `id`），
      由测试锁定两键恒等，避免过渡期长出两套不一致的键。
    - 新增来源三键：`container_material_id` / `container_file_name` /
      `container_entry_path`。**普通 PDF 与历史材料一律 null**（不回填伪来源）。
    - `container_file_name` 由父材料**查询组装**（见 get_task 的批量查回），
      不新增冗余数据库列——父改名即自动跟随，不会漂移。
    - T10-B §B4：容器材料的 `parse_state` 天然区分 `stored`/`preparing`/`done`/`failed`
      （契约 §7.2.4 冻结取值），故无需新增字段；`degradations` 透出准备失败的可读
      原因，前端不必解析内部结构（`kind` 只作稳定标识）。
    """
    names = container_names or {}
    version_fact = version_fact or material_versions.version_projection(m)
    return {
        "id": m.id, "material_id": m.id, "file_name": m.file_name, "size_kb": m.size_kb,
        "role": m.role,
        "subject_id": m.subject_id, "packet_id": m.packet_id,
        "role_label": ROLE_LABELS.get(m.role, m.role), "role_confirmed": m.role_confirmed,
        "via": m.via, "parse_state": m.parse_state,
        "container_material_id": m.container_material_id,
        "container_file_name": (names.get(m.container_material_id)
                                if m.container_material_id else None),
        "container_entry_path": m.container_entry_path,
        # BS-P2-01（设计 98 §6.2）：材料替代链。两键恒在（历史/普通材料为 null）；
        # D9/T2-A3 另由统一投影给出 version_role，前端不得再以列表图覆盖显式角色。
        "superseded_by_id": m.superseded_by_id,
        "superseded_at": (m.superseded_at.isoformat(timespec="seconds")
                          if m.superseded_at else None),
        # D9/T2-A3：版本角色/修订号来自统一只读投影，不是人工处置状态；历史 NULL
        # 不猜测回填，legacy_unknown 必须原样传到前端。
        "revision_no": version_fact["revision_no"],
        "version_role": version_fact["version_role"],
        "replacement_target_id": version_fact["replacement_target_id"],
        # 读取覆盖是材料页/报告共用的只读投影；没有页码时返回 None，不猜总页数。
        "reading_coverage": parsing.reading_coverage(m),
        "degradations": [
            {
                **{"kind": d.get("kind"), "message": d.get("message")},
                **{key: d[key] for key in (
                    "code", "expected_subject_name", "detected_subject_name", "confidence"
                ) if d.get(key) is not None},
            }
            for d in (m.degradations_json or [])
        ],
    }


@router.get("/tasks/{task_id}")
def get_task(task_id: int, db: Session = Depends(get_db), user=Depends(get_current_any_user)):
    task = _get_task(db, task_id, user)
    try:
        materials = db.query(BsMaterial).filter(BsMaterial.task_id == task_id).all()
    except OperationalError as exc:
        if _is_material_revision_schema_error(exc):
            logger.error("[bs] D9 material schema is not ready task=%s", task_id)
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "bs_material_schema_not_ready",
                    "message": "当前数据库尚未完成工程分包材料版本结构，暂不能进入工作台；请先完成 D9 结构迁移。",
                },
            ) from exc
        raise
    version_facts, _version_warnings = material_versions.project_materials(materials)
    active_ids = service.active_subject_ids(db, task)
    subjects = (db.query(BsSubject)
                .filter(BsSubject.task_id == task_id, BsSubject.id.in_(active_ids))
                .order_by(BsSubject.id).all())
    packets = db.query(BsPacket).filter(BsPacket.task_id == task_id).order_by(BsPacket.id).all()
    # T4.1-T2 §2.1：来源 DTO 的 `container_file_name` 由父材料组装 —— 一次性批量
    # 查回（不逐行查，避免 N+1），且不新增可能漂移的冗余列。
    container_ids = {m.container_material_id for m in materials if m.container_material_id}
    container_names = {}
    if container_ids:
        # 按 task_id 一并收窄：容器必属同任务（写入路径保证），这层过滤只是让
        # 「跨任务 id 复用」在数据被外部污染时也不会串名。
        container_names = dict(
            db.query(BsMaterial.id, BsMaterial.file_name)
            .filter(BsMaterial.task_id == task_id,
                    BsMaterial.id.in_(container_ids)).all())
    reference_snapshot = reference_visibility.visible_snapshot(
        db, task, reference_snapshots.get_reference_snapshot(db, task), user)
    zip_preparation = service.zip_preparation_summary(db, task)
    reading_coverage = service.task_reading_coverage(db, task)
    supplement_labels = service.supplement_material_labels(db, task.id)
    supplement_associations = service.supplement_issue_associations(db, task.id)
    # 抬头事实是只读投影：工作台在报告尚不可读时也能展示合同金额/签订日期，
    # 但仍只消费合同字段抽取结果，不自行从文件名猜值。
    contract_facts = reporting.contract_facts_for_materials(
        material_versions.active_input_materials(materials),
        baseline_date=task.baseline_date,
    )
    return {
        "task_id": task.id,
        "baseline_date_source": service.baseline_date_source(task),
        "title": task.title,
        "state": task.state,
        "scope": {"A1": task.scope_a1, "A3": task.scope_a3},
        "scale": task.scale,
        "section": _section_dto(task),
        "section_suggestions": section_projection.section_suggestions(
            material_versions.active_input_materials(materials)),
        "routing": _routing_dto(task),
        "options_snapshot": task.options_snapshot_json,
        "supplement_plan": service.get_supplement_generation_plan(task),
        "subjects": [_subject_dto(s) for s in subjects],
        "packets": [{"packet_id": p.id, "subject_id": p.subject_id,
                     "batch_name": p.batch_name} for p in packets],
        "materials": [{**_material_dto(m, container_names, version_facts.get(m.id)),
                       "supplement_label": supplement_labels.get(m.id),
                       "supplement_issue": supplement_associations.get(m.id)}
                      for m in materials],
        "contract_facts": contract_facts,
        "validation": service.validate_task_materials(db, task),
        "versions": service.list_review_versions(db, task),
        "reference_run": reference_visibility.visible_run(db, task, knowledge_runs.get_reference_run(db, task), user),
        "reference_snapshot": reference_snapshot,
        # T10-B §B4：ZIP 准备态汇总（前端据此展示「准备中/已就绪/失败待处理」）。
        # 只读派生，不写库；容器明细仍在 materials 里逐条给出 parse_state。
        "zip_preparation": zip_preparation,
        # P8-00：人工复核页直接消费任务级读取覆盖投影，避免只在 finding 文案里间接提示未读页。
        "reading_coverage": reading_coverage,
        "progress": service.task_progress(
            db, task, materials=materials, snapshot=reference_snapshot,
            zip_summary=zip_preparation,
        ),
    }


@router.get("/tasks/{task_id}/materials/{material_id}/history")
def material_history(task_id: int, material_id: int, db: Session = Depends(get_db),
                     user=Depends(get_current_any_user)):
    """材料替代链只读视图（D9/T2-A3）。

    先走任务归属守卫，再只在该任务范围内查材料；跨任务材料统一表现为 404，避免
    通过历史接口探测其他使用单位的数据。链路异常只作为 ``warnings`` 返回，不在
    读取请求中修复存量关系。
    """
    task = _get_task(db, task_id, user)
    materials = (db.query(BsMaterial)
                 .filter(BsMaterial.task_id == task.id)
                 .order_by(BsMaterial.id).all())
    by_id = {material.id: material for material in materials}
    if material_id not in by_id:
        raise HTTPException(status_code=404, detail="材料不存在")

    history = material_versions.build_material_history(materials, material_id)
    version_facts, _version_warnings = material_versions.project_materials(materials)
    container_ids = {m.container_material_id for m in materials if m.container_material_id}
    container_names = {}
    if container_ids:
        container_names = dict(
            db.query(BsMaterial.id, BsMaterial.file_name)
            .filter(BsMaterial.task_id == task.id,
                    BsMaterial.id.in_(container_ids)).all())
    history["chain"] = [
        _material_dto(by_id[row["material_id"]], container_names,
                      version_facts.get(row["material_id"]))
        for row in history["chain"]
    ]
    return history


@router.delete("/tasks/{task_id}")
def delete_task(task_id: int, db: Session = Depends(get_db), user=Depends(get_current_any_user)):
    """删除任务（P0-3）：任务与三张子表一并删除（显式删，不依赖库级 CASCADE）。"""
    task = _get_task(db, task_id, user)
    service.delete_task(db, task)
    return {"ok": True, "task_id": task_id}


@router.get("/tasks/{task_id}/ruleset")
def get_task_ruleset(task_id: int, db: Session = Depends(get_db),
                     user=Depends(get_current_any_user)):
    """任务规则集版本视图（BS-P2-04 / 设计 98 §3.3；只读）。

    前端据此在「历史审查版本」区显示「规则集：<task_version> → 当前 <current_version>
    （N 条规则变化）」，并给「按新规则重跑（N 条）」按钮（scope=items）；
    `declared_changed_item_ids` 为空而版本不同时退化为全量重跑并写明原因。
    """
    task = _get_task(db, task_id, user)
    return ruleset.task_ruleset_view(db, task)


@router.get("/tasks/{task_id}/versions")
def list_review_versions(task_id: int, db: Session = Depends(get_db),
                         user=Depends(get_current_any_user)):
    """列出任务历史审查版本；旧版本只读，供增量补充后的回放使用。"""
    task = _get_task(db, task_id, user)
    return {"versions": service.list_review_versions(db, task)}


@router.get("/tasks/{task_id}/versions/diff")
def diff_review_versions(task_id: int, a: str = Query(...), b: str = Query(...),
                         db: Session = Depends(get_db), user=Depends(get_current_any_user)):
    """两版逐项结论差异（BS-P2-02 / 设计 98 §5.1）。

    ⚠️ 本路由必须注册在 `/versions/{version_no}` **之前**：否则 `.../versions/diff`
    会先命中整数路径参数而 422。ref = 版本号整数或 `current`；版本不存在 → 404；
    非法 ref → 400；a==b 合法（结果全为仍存在）。
    """
    task = _get_task(db, task_id, user)
    try:
        return service.version_diff(db, task, a, b, user=user)
    except AiContextChanged as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/tasks/{task_id}/versions/{version_no}/materials/{material_id}/pages/{page_no}")
def get_review_version_page(task_id: int, version_no: int, material_id: int, page_no: int,
                            response: Response, db: Session = Depends(get_db),
                            user=Depends(get_current_any_user)):
    task = _get_task(db, task_id, user)
    page = service.get_review_version_page(db, task, version_no, material_id, page_no)
    if page is None:
        raise HTTPException(status_code=404, detail="该审查版本未记录此页识别文字")
    response.headers["Cache-Control"] = "private, no-store"
    return page


@router.get("/tasks/{task_id}/versions/{version_no}")
def get_review_version(task_id: int, version_no: int, db: Session = Depends(get_db),
                       user=Depends(get_current_any_user)):
    task = _get_task(db, task_id, user)
    version = service.get_review_version(db, task, version_no)
    if version is None:
        raise HTTPException(status_code=404, detail="审查版本不存在")
    return reference_visibility.visible_version(db, task, version, user)


@router.get("/observation-stats")
def observation_stats(db: Session = Depends(get_db), user=Depends(get_current_any_user)):
    """触发词召回观察数据（P2-3）：当前用户的任务按核查项聚合三态计数与不可判定占比。

    按占比降序，供决策「是否接 LLM 语义通道」；只读，不影响任何判定。
    归属过滤（Q6）：只统计 BsTask.created_by == 当前用户，防多团队串数据。
    BS-1B：叠加 created_by_type 过滤——User 与 PlatformUser 是两套同 id 异人表。
    """
    return {"items": service.observation_stats(db, created_by=user.id,
                                               created_by_type=user.user_type)}


@router.get("/materials/{material_id}/pages/{page_no}")
def material_page_text(material_id: int, page_no: int, response: Response,
                       db: Session = Depends(get_db), user=Depends(get_current_any_user)):
    """按原 PDF 物理页返回已存识别文字；不触发解析或越过材料归属校验。"""
    mat = access.require_bs_material(db, material_id, user.id, user.user_type)
    if page_no < 1:
        raise HTTPException(status_code=404, detail="未找到该页识别文字")
    page = next((p for p in (mat.pages_json or []) if isinstance(p, dict)
                 and p.get("page") == page_no and isinstance(p.get("text"), str)), None)
    if page is None:
        raise HTTPException(status_code=404, detail="未找到该页识别文字")
    response.headers["Cache-Control"] = "private, no-store"
    return {"material_id": mat.id, "page": page_no, "text": page["text"],
            "readable": service.parsing.material_page_readable(mat, page_no)}


@router.get("/materials/{material_id}/file")
def material_file(material_id: int, db: Session = Depends(get_db), user=Depends(get_current_any_user)):
    """材料原件（证据跳页：浏览器原生 PDF 视图支持 #page=N 定位）。"""
    # BS-1B：先反查材料归属（越权者即使该材料无 path 也必须拿到 403），再判文件态。
    mat = access.require_bs_material(db, material_id, user.id, user.user_type)
    if not mat.path:
        raise HTTPException(status_code=404, detail="材料文件不存在")
    fs_path = service.material_abs_path(mat.path)
    if not os.path.isfile(fs_path):
        raise HTTPException(status_code=404, detail="文件已丢失，请重新上传")
    # inline：证据跳页要在浏览器里直接打开 PDF 并 #page=N 定位，attachment 会变成下载
    return FileResponse(fs_path, media_type="application/pdf", filename=mat.file_name,
                        content_disposition_type="inline")


@router.get("/tasks/{task_id}/references/{reference_id}/file")
def reference_file(task_id: int, reference_id: str,
                   version_no: int | None = Query(None, ge=1),
                   db: Session = Depends(get_db), user=Depends(get_current_any_user)):
    """只代理任务已冻结且当前仍获授权的同版本规范PDF原件。"""
    data = read_reference_original(db, task_id=task_id, reference_id=reference_id,
                                   version_no=version_no, user=user)
    return Response(data, media_type="application/pdf", headers={
        "Content-Disposition": 'inline; filename="reference.pdf"',
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
    })


@router.post("/tasks/{task_id}/materials")
def add_material(task_id: int, body: MaterialIn, db: Session = Depends(get_db), user=Depends(get_current_any_user)):
    task = _get_task(db, task_id, user)
    _require_setup(task, "核查已开始，不能再上传材料")
    mat = service.add_material(db, task, file_name=body.file_name,
                               size_kb=body.size_kb, via=body.via, role=body.role,
                               subject_id=body.subject_id, packet_id=body.packet_id,
                               material_category=body.material_category)
    return {"material_id": mat.id, "role": mat.role, "role_label": ROLE_LABELS.get(mat.role, mat.role)}


@router.post("/tasks/{task_id}/materials/upload")
@limiter.limit("10/minute")
async def upload_material(task_id: int, request: Request, file: UploadFile = File(...),
                          role: Optional[str] = Form(None),
                          subject_id: Optional[int] = Form(None),
                          packet_id: Optional[int] = Form(None),
                          material_category: Optional[str] = Form(None),
                          replaces_material_id: Optional[int] = Form(None),
                          supplement_generation_id: Optional[str] = Form(None),
                          db: Session = Depends(get_db), user=Depends(get_current_any_user)):
    """真实文件上传：PDF 走落盘 → 后台文本提取；ZIP 只保存原包（T4.1-T2）。

    T4.1-T2 §7.2：ZIP 只做**格式双验**（扩展名 + 实际 ZIP 结构）后**保存原包**——
    不解包、不枚举内部条目、不解析内部 PDF、不跑 OCR、不校验内部归属、不产出任何
    结论。容器 `parse_state='stored'`，等准备阶段展开；上传响应不含内部条目信息。

    BS-P2-01/D9：可选表单字段 `replaces_material_id` 显式声明「本次上传替换哪份旧材料」。
    目标须属本任务且未被替代；替代目标先持久化在新候选行，待解析成功后由准备 worker
    调用原子 CAS 生效。上传失败/解析失败时旧材料继续有效，旧文件仍可取原文。
    """
    task = _get_task(db, task_id, user)
    _require_setup(task, "核查已开始，不能再上传材料")
    supplement_label = None
    if supplement_generation_id:
        plan = service.get_supplement_generation_plan(task)
        if not plan or plan.get("status") != "planned" or plan.get("generation_id") != supplement_generation_id:
            raise HTTPException(status_code=409, detail="补件计划已变化，请重新选择补件")
        category = (plan.get("impact") or {}).get("category")
        supplement_label = (reporting.missing_category_labels().get(category) or {}).get("label")
        if not supplement_label:
            raise HTTPException(status_code=409, detail="补件类型无法确认，请重新选择补件")
    # BS-P2-01：替换目标前置校验必须在落盘/建行之前——避免留下「新行已建、旧行没标」的
    # 半截状态；已替代的旧行不允许再次被替换（409），跨任务/不存在一律 400。
    replaced = None
    effective_role = role
    effective_subject_id = subject_id
    effective_packet_id = packet_id
    effective_material_category = material_category
    if replaces_material_id is not None:
        replaced = (db.query(BsMaterial)
                    .filter(BsMaterial.id == replaces_material_id,
                            BsMaterial.task_id == task.id).first())
        if replaced is None:
            raise HTTPException(status_code=400, detail="待替换材料不存在或不属于本任务")
        if replaced.superseded_by_id is not None:
            raise HTTPException(status_code=409, detail="该材料已被替换")
        if role is not None and role != replaced.role:
            raise HTTPException(status_code=409, detail="替换材料必须保持原材料归属类型")
        if subject_id is not None and subject_id != replaced.subject_id:
            raise HTTPException(status_code=409, detail="替换材料必须属于原材料主体")
        if material_category is not None and material_category != replaced.material_category:
            raise HTTPException(status_code=409, detail="替换材料必须保持原材料类别")
        if replaced.subject_id is None and (replaced.role or role) in ("sub", "labor"):
            raise HTTPException(status_code=409, detail="原材料尚未归属主体，先完成人工归属后再替换")
        # 替代操作继承原材料的主体/类别/角色，避免不同专业分包和劳务分包在
        # 重传时因前端未重复提交主体而产生边界漂移。
        effective_role = replaced.role
        effective_subject_id = replaced.subject_id
        effective_packet_id = replaced.packet_id if packet_id is None else packet_id
        effective_material_category = replaced.material_category
    if supplement_generation_id:
        category_roles = (reporting.missing_category_labels().get(category) or {}).get("roles") or []
        if effective_role not in category_roles:
            raise HTTPException(status_code=409, detail="上传材料类别与补件计划不一致，请重新选择")
        plan_subjects = {str(value) for value in (plan.get("subject_ids") or [])}
        if effective_role == "packet" and plan_subjects and str(effective_subject_id) not in plan_subjects:
            raise HTTPException(status_code=409, detail="上传材料主体与补件计划不一致，请重新选择")
    # A-09（B0）：非支持格式明确提示——按扩展名前置拒绝（不读内容、不落盘不留记录）。
    # T4.1-T2 起 PDF 与 ZIP 均接受；文案保留既有可读关键词（Word/图片）。
    upload_name = file.filename or ""
    lowered_name = upload_name.lower()
    is_pdf = lowered_name.endswith(".pdf")
    is_zip = lowered_name.endswith(".zip")
    if not (is_pdf or is_zip):
        raise HTTPException(
            status_code=400,
            detail="本智能体仅支持 PDF 与 ZIP 报审资料包，Word/图片请先转 PDF 后上传")
    # A-08（B0）最低解：read() 之前用 UploadFile.size 前置判——超限直接 413，
    # 超大文件不再整读进内存（内存尖峰消除）。starlette 0.50.0 下 size 在 handler
    # 入口即精确（实测 0.5MB→524288、3MB→3145729 逐字节 match）。
    max_bytes = service._upload_max_bytes()
    if file.size is not None and file.size > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"文件体积超过上限（{max_bytes // (1024 * 1024)}MB），请压缩或拆分后重新上传")
    # 读取阶段封顶（size 不可判定时的兜底；正常路径 size 已精确，此处不改变结论）。
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"文件体积超过上限（{max_bytes // (1024 * 1024)}MB），请压缩或拆分后重新上传")
    if not content:
        raise HTTPException(status_code=400, detail="空文件")
    if is_pdf:
        # 上传魔数校验（P1-2）：扩展名已过 + %PDF- 文件头双验，防伪装文件。
        if b"%PDF-" not in content[:1024]:
            raise HTTPException(status_code=400, detail="文件内容不是有效的 PDF（缺少 PDF 文件头），请重新导出后上传")
    else:
        # T4.1-T2 §7.2.1：落盘**前**只验 ZIP 结构（走 BytesIO，不写脏盘），
        # 且不枚举内部条目、不解包、不读正文。伪装/损坏/空包一律平实 400。
        try:
            zip_ingestion.validate_zip_bytes(content)
        except zip_ingestion.ZipIngestionError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    # P2-2：摄取重活段（文本提取+判章）挪后台线程——立即返回「解析中」，
    # 收口结果走 material.parsed / material.parse_failed 事件（SSE 时间线消费）。
    # 解析中 start_review 409（既有闸）保证半份文本不会进核查。
    # 上传阶段只接收用户已选择的归属；不读取文件内容来判断主体一致性。
    if effective_subject_id is not None:
        if not service.is_active_subject(db, task, effective_subject_id):
            raise HTTPException(status_code=404, detail="关联主体已无有效材料，请先上传分包合同")
        subject = db.query(BsSubject).filter(BsSubject.id == effective_subject_id,
                                              BsSubject.task_id == task.id).first()
        if subject is None:
            raise HTTPException(status_code=404, detail="关联主体不存在")
    if effective_packet_id is not None:
        packet = db.query(BsPacket).filter(BsPacket.id == effective_packet_id,
                                            BsPacket.task_id == task.id).first()
        if packet is None or (effective_subject_id is not None and packet.subject_id != effective_subject_id):
            raise HTTPException(status_code=404, detail="关联报审资料批次不存在")
    # 主体在正文解析后按唯一、明确的单位名称归并；不能在文件内容尚未读取时逐份造主体。
    supplement_link = ({"generation_id": supplement_generation_id, "category": category,
                        "label": supplement_label, "scope_labels": plan.get("supplement_labels") or []}
                       if supplement_label else None)
    if is_zip:
        # 只保存原包（T4.1-T2 §7.2）：不调 _finish_ingest、不起解析/判章、不解包
        mat = service.store_zip_container(
            db, task, file_name=file.filename, content=content, role=role,
            subject_id=effective_subject_id, packet_id=effective_packet_id,
            material_category=effective_material_category,
            replacement_target_id=replaced.id if replaced is not None else None,
            operator=user.id, supplement_link=supplement_link)
    else:
        mat = service.ingest_material(db, task, file_name=file.filename, content=content,
                                      role=effective_role, subject_id=effective_subject_id,
                                      packet_id=effective_packet_id,
                                      material_category=effective_material_category,
                                      replacement_target_id=(replaced.id if replaced is not None else None),
                                      defer_read=True,
                                      operator=user.id, supplement_link=supplement_link)
    if replaced is not None:
        # 解析跨请求进行；用任务事件保留上传操作者，worker 重启后仍能把
        # material.superseded 事件归因到发起替代的人。
        service.add_event(db, task.id, "material.replacement_requested", payload={
            "material_id": mat.id,
            "replacement_target_id": replaced.id,
            "operator": user.id,
        })
    # 契约（Q5 标注）：响应只含「上传已受理」信息。PDF 的解析/判章尚未收口、ZIP 的
    # 内部条目根本未读取，故不返回 pages/degradations/seal_hit、也不返回条目数——
    # 收口结果以 material.parsed / material.parse_failed 事件与 GET /tasks/{id} 详情为准。
    return {
        "material_id": mat.id, "file_name": mat.file_name, "role": mat.role,
        "subject_id": mat.subject_id, "packet_id": mat.packet_id,
        "role_label": ROLE_LABELS.get(mat.role, mat.role), "parse_state": mat.parse_state,
        "replacement_target_id": mat.replacement_target_id,
    }


@router.post("/tasks/{task_id}/materials/{material_id}/role")
def correct_role(task_id: int, material_id: int, body: RoleCorrectIn,
                 db: Session = Depends(get_db), user=Depends(get_current_any_user)):
    task = _get_task(db, task_id, user)
    if body.role not in ROLE_LABELS:
        raise HTTPException(status_code=400, detail="未知归属类型")
    # T10-B §B1：原实现两侧都无状态门（只靠 UI 不给入口）——preparing/running 期
    # 直接调接口即可改名，属「界面按不住」的假门禁。
    _require_setup(task, "核查已开始，不能再更正材料归属")
    try:
        mat = service.set_material_role(db, task, material_id, body.role)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"material_id": mat.id, "role": mat.role}


@router.post("/tasks/{task_id}/start")
def start_review(task_id: int, response: Response,
                 db: Session = Depends(get_db), user=Depends(get_current_any_user)):
    """开始核查（契约 §7.3）：存在未准备 ZIP 容器时**受理为 `preparing`**（202），
    否则走既有同步启动路径（200 / 400 / 409）。

    202 与 200 的区别是「已受理但尚未开始」——前端据此展示「准备中」而非「已开始」；
    受理体不带 `ok=true`（契约 §7.3.2 禁止暗示已正式运行）。
    """
    task = _get_task(db, task_id, user)
    # 新环境映射缺失时，在后台准备及任务状态变更前给出可理解的提示。
    knowledge_runs.load_reference_package(as_of=task.baseline_date)
    # 契约 v3 §三：启动锁定事件的 operator 必须是当前认证用户（不得为 None）
    try:
        result = service.start_review(db, task, operator=user.id)
    except knowledge_runs.ReferenceRunSuperseded as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    if result.get("preparing"):
        response.status_code = 202
        return result
    if not result["ok"]:
        # 材料尚未就绪、稍后可重试 → 409；400 只留给硬缺口（如范围/构成不符）。
        # `zip_preparation_failed`：资料包整体失败是**用户必须先处理**的阻止
        # （移除或重传），语义同硬缺口 ⇒ 400（契约 §7.5.6「硬阻止启动」）。
        # ⚠️ 既有闸门的 HTTP 码一字未动（`subject_type_pending` 原就落 400，
        # 不在本次改动范围内）。
        code = 409 if result["reason"] == "parsing_pending" else 400
        raise HTTPException(status_code=code, detail=result["detail"])
    return {"task_id": task.id, "state": task.state, "soft_gaps": result["soft_gaps"]}


def _checked_run(db: Session, task: BsTask, kind: str, fn) -> list:
    """run_* 统一异常兜底（P0-2）：内核异常先回滚会话、落 check.failed 事件，再 500。

    旧结论保留：异常发生在 _replace_findings 之前，事务边界天然保证；
    回滚后再写事件，避免失败事务污染事件写入。500 文案平实中文，异常摘要只进
    事件 payload（前端时间线按事件类型模板化展示，见 P0-1 中文化白名单）。
    """
    try:
        return fn(db, task)
    except knowledge_runs.ReferenceRunSuperseded as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        db.rollback()
        summary = str(exc)[:200] or exc.__class__.__name__
        service.add_event(db, task.id, "check.failed", stage="check",
                          payload={"kind": kind, "message": summary})
        logger.warning("[bs] check failed task=%s kind=%s: %s", task.id, kind, exc)
        raise HTTPException(
            status_code=500,
            detail="本次核查执行中断，已记录；请重新发起，若反复失败请联系管理员")


@router.post("/tasks/{task_id}/check")
@limiter.limit("6/minute")
def run_check(task_id: int, request: Request, db: Session = Depends(get_db),
              user=Depends(get_current_any_user)):
    """A3-01 完整性对表（W1 确定性引擎；A1 内核在 W2）。

    A-11（B0）限流：核查端点是 CPU 密集（对表/内核全量重算），单任务正常只跑数次，
    6/minute 拦的是脚本化重跑风暴；正常交互远达不到。
    """
    task = _get_task(db, task_id, user)
    if task.state not in ("running", "review"):
        raise HTTPException(status_code=409, detail="请先完成两段式启动（POST /start）")
    findings = _checked_run(db, task, "completeness", service.run_completeness)
    return {"task_id": task.id,
            "reference_snapshot": reference_visibility.visible_snapshot(db, task, reference_snapshots.get_reference_snapshot(db, task), user),
            "counts": reporting.status_counts(findings),
            **_findings_payload(db, findings)}


def _advisory_target_ids(run):
    try:
        return ai_advisory_snapshots.advisory_target_ids(run)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


def _advisory_group_ids(run):
    _advisory_target_ids(run)
    return ai_advisory_snapshots.advisory_group_ids(run)


class BackgroundAiReviewIn(BaseModel):
    review_run_id: str
    retry: bool = False


@router.post('/tasks/{task_id}/ai-review', status_code=202)
@limiter.limit('10/minute')
async def submit_background_ai_review(task_id: int, body: BackgroundAiReviewIn, request: Request,
                                      db: Session = Depends(get_db), user=Depends(get_current_any_user)):
    _get_task(db, task_id, user)
    try:
        return background_ai_review.submit_background_job(db, task_id, user, body.review_run_id, retry=body.retry)
    except PermissionError:
        raise HTTPException(status_code=403, detail='无权访问该任务') from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.get('/tasks/{task_id}/ai-review')
def get_background_ai_review(task_id: int, review_run_id: str = Query(...),
                             db: Session = Depends(get_db), user=Depends(get_current_any_user)):
    task = _get_task(db, task_id, user)
    return background_ai_review.job_status(db, task, review_run_id)


@router.post("/tasks/{task_id}/check/{group_id}/ai-advisory-batch")
@limiter.limit("10/minute")
async def run_group_ai_advisory(task_id: int, group_id: str, body: BatchAdvisoryIn, request: Request,
                                 db: Session = Depends(get_db), user=Depends(get_current_any_user)):
    """仅对当前组中已关联原文的结论发起分批辅助判断。"""
    task = _get_task(db, task_id, user)
    if background_ai_review.running_job(db, task):
        raise HTTPException(status_code=409, detail='后台 AI 核查正在运行，请等待完成')
    if task.state not in ("running", "review"):
        raise HTTPException(status_code=409, detail="当前任务不能进行辅助核查，请先开始核查")
    if group_id not in reference_snapshots.CHECK_GROUPS:
        raise HTTPException(status_code=400, detail="未知核查步骤")
    run = knowledge_runs.get_reference_run(db, task, group_id=group_id)
    if not run or run.get("state") != "completed" or run.get("run_id") != body.run_id:
        raise HTTPException(status_code=409, detail="核查运行已变化，请刷新后重试")
    ids = _advisory_group_ids(run)
    skipped = len(_advisory_target_ids(run) - set(ids))
    if not ids and group_id != "a1":
        try:
            assert_ai_context_current(db, task, run, user, require_references=False)
        except AiContextChanged:
            raise HTTPException(status_code=409, detail="核查材料、结论或依据授权已变化，请刷新并重新核查") from None
        return {"task_id": task_id, "group_id": group_id, "run_id": run["run_id"], "results": [],
                "skipped": skipped, "limitation": "本步骤暂无已关联的可用依据，相关核查项需人工核实"}
    try:
        results = await service.run_linked_batch_ai_advisory(db, task, user, group_id=group_id,
                    finding_ids=ids, resume_partial=body.resume_partial, expected_run_id=body.run_id,
                    retry_failed=body.resume_partial) if ids else []
        if group_id == "a1":
            await service.run_negative_candidate_ai_advisory(db, task, user, max_batches=64,
                expected_run_id=body.run_id, retry=body.resume_partial)
    except service.AiAdvisoryInProgress:
        raise HTTPException(status_code=409, detail="部分辅助核查正在进行，请稍后刷新查看") from None
    except AiContextChanged:
        raise HTTPException(status_code=409, detail="核查材料、结论或依据授权已变化，请刷新并重新核查") from None
    return {"task_id": task_id, "group_id": group_id, "run_id": run["run_id"], "results": results,
            "skipped": skipped,
            "limitation": "部分核查项未关联可用依据，需人工核实" if skipped else None}


@router.get("/tasks/{task_id}/check/{group_id}/ai-advisory-batch")
def get_group_ai_advisory_status(task_id: int, group_id: str, db: Session = Depends(get_db),
                                 user=Depends(get_current_any_user)):
    """读取当前代逐项辅助核查进度，不返回模型内容。"""
    task = _get_task(db, task_id, user)
    if group_id not in reference_snapshots.CHECK_GROUPS:
        raise HTTPException(status_code=400, detail="未知核查步骤")
    run = knowledge_runs.get_reference_run(db, task, group_id=group_id)
    if not run or run.get("state") != "completed":
        raise HTTPException(status_code=409, detail="本步骤核查尚未完成")
    ids = _advisory_group_ids(run)
    try:
        assert_ai_context_current(db, task, run, user, allow_completed=True, require_references=bool(ids))
    except AiContextChanged:
        raise HTTPException(status_code=409, detail="核查材料、结论或依据授权已变化，请刷新并重新核查") from None
    events = []
    if ids or group_id == "a1":
        events = (db.query(BsTaskEvent).filter(BsTaskEvent.task_id == task_id,
            BsTaskEvent.event_type.in_(("check.ai_advisory_started", "check.ai_advisory_completed", "check.ai_advisory_failed",
                "check.negative_candidates_started", "check.negative_candidates_finished", "check.negative_candidates_failed")),
            BsTaskEvent.payload_json["run_id"].as_string() == run["run_id"])
            .order_by(BsTaskEvent.sequence.desc()).all())
    coverage = ai_advisory_snapshots.summarize_coverage(
        {"ai_auto_enabled": True, "ai_auto_groups": [group_id]}, [run], events,
        timeout_reviewable_ids=ai_advisory_snapshots.timeout_reviewable_ids(
            db, task, [run], reporting.report_review_blocking),
        scope_reviewable_ids=ai_advisory_snapshots.unlocated_scope_reviewable_ids(
            db, task, user, run, events))["groups"][0]
    return {"task_id": task_id, "group_id": group_id, "run_id": run["run_id"],
            "counts": coverage["counts"], "skipped": coverage["skipped"],
            "reviewable_partial": coverage["reviewable_partial"], "reviewable_not_run": coverage["reviewable_not_run"],
            "unlocated_scope_reviewable": coverage["unlocated_scope_reviewable"],
            "reviewable_failed": coverage["reviewable_failed"],
            "failure_categories": coverage["failure_categories"]}


@router.post("/tasks/{task_id}/findings/{finding_id}/ai-advisory")
@limiter.limit("6/minute")
async def run_finding_ai_advisory(task_id: int, finding_id: int, request: Request,
                                  db: Session = Depends(get_db), user=Depends(get_current_any_user)):
    """仅依据当前核查项及其关联原文生成待人工核实的建议。"""
    task = _get_task(db, task_id, user)
    if task.state not in ("running", "review"):
        raise HTTPException(status_code=409, detail="当前任务不能进行辅助核查，请先开始核查")
    finding = db.query(BsFinding).filter_by(id=finding_id, task_id=task_id).first()
    if finding is None:
        raise HTTPException(status_code=404, detail="当前任务没有该核查项")
    groups = [name for name, config in reference_snapshots.CHECK_GROUPS.items()
              if finding.kind in config["kinds"]]
    if len(groups) != 1:
        raise HTTPException(status_code=409, detail="该核查项暂不支持辅助核查，请人工核实")
    try:
        result = await service.run_linked_ai_advisory(db, task, user, group_id=groups[0], finding_id=finding_id)
    except service.AiAdvisoryInProgress:
        raise HTTPException(status_code=409, detail="该项辅助核查正在进行，请稍后刷新查看结果") from None
    except AiContextChanged:
        raise HTTPException(status_code=409, detail="核查材料、结论或依据授权已变化，请刷新并重新核查") from None
    return {"task_id": task_id, "finding_id": finding_id, **result}


@router.get("/tasks/{task_id}/findings/{finding_id}/ai-advisory")
def get_finding_ai_advisory(task_id: int, finding_id: int, db: Session = Depends(get_db),
                             user=Depends(get_current_any_user)):
    """只返回仍属于当前核查代、且来源仍获授权的最新建议。"""
    task = _get_task(db, task_id, user)
    finding = db.query(BsFinding).filter_by(id=finding_id, task_id=task_id).first()
    if finding is None:
        raise HTTPException(status_code=404, detail="当前任务没有该核查项")
    groups = [name for name, config in reference_snapshots.CHECK_GROUPS.items()
              if finding.kind in config["kinds"]]
    if len(groups) != 1:
        return {"finding_id": finding_id, "advisory": None}
    run = knowledge_runs.get_reference_run(db, task, group_id=groups[0])
    if not run or run.get("state") != "completed":
        return {"finding_id": finding_id, "advisory": None}
    event = (db.query(BsTaskEvent).filter(BsTaskEvent.task_id == task_id,
        BsTaskEvent.event_type.in_(("check.ai_advisory_started", "check.ai_advisory_completed", "check.ai_advisory_failed")),
        BsTaskEvent.payload_json["finding_id"].as_integer() == finding_id,
        BsTaskEvent.payload_json["run_id"].as_string() == run.get("run_id"))
        .order_by(BsTaskEvent.sequence.desc()).first())
    if event is None:
        return {"finding_id": finding_id, "advisory": None}
    try:
        assert_ai_context_current(db, task, run, user, allow_completed=True)
    except AiContextChanged:
        return {"finding_id": finding_id, "advisory": None,
                "limitation": "材料、核查结果或依据授权已变化，原建议不再作为当前结果"}
    if event.event_type == "check.ai_advisory_started":
        pending = service.ai_advisory_pending(event)
        return {"finding_id": finding_id, "advisory": None, "retryable": not pending,
                "limitation": "辅助核查正在进行，请稍后刷新查看结果" if pending else "上次辅助核查已中断，可以重新生成"}
    if event.event_type == "check.ai_advisory_failed":
        return {"finding_id": finding_id, "advisory": None, "retryable": True,
                "limitation": "上次辅助核查未完成，可以重新生成"}
    if not ai_advisory_snapshots.advisory_validation_current(event):
        return {"finding_id": finding_id, "advisory": None, "retryable": True,
                "limitation": "核查建议的校验规则已更新，请重新生成"}
    return {"finding_id": finding_id, "advisory": event.payload_json}


@router.post("/tasks/{task_id}/check/a1")
@limiter.limit("6/minute")
def run_a1_check(task_id: int, request: Request, db: Session = Depends(get_db),
                 user=Depends(get_current_any_user)):
    """A1 判定（W2）：A1-01 金额占比 + A1-02 负面清单阈值比较 + V16-01/L17-02 依赖判定。

    A-11（B0）限流：同 run_check——CPU 密集内核，6/minute 只拦重跑风暴。
    """
    task = _get_task(db, task_id, user)
    if task.state not in ("running", "review"):
        raise HTTPException(status_code=409, detail="请先完成两段式启动（POST /start）")
    if not task.scope_a1:
        raise HTTPException(status_code=409, detail="任务未选择 A1 核查范围")
    findings = _checked_run(db, task, "a1", service.run_a1_checks)
    return {"task_id": task.id,
            "reference_snapshot": reference_visibility.visible_snapshot(db, task, reference_snapshots.get_reference_snapshot(db, task), user),
            "reference_run": reference_visibility.visible_run(db, task, knowledge_runs.get_reference_run(db, task), user),
            "counts": reporting.status_counts(findings),
            **_findings_payload(db, findings)}


@router.post("/tasks/{task_id}/check/a3-signature")
@limiter.limit("6/minute")
def run_a3_signature(task_id: int, request: Request, db: Session = Depends(get_db),
                     user=Depends(get_current_any_user)):
    """A3-02 签字链双轨（W3-2）：法定链/集团链各判 + 先报批后实施时序。

    A-11（B0）限流：同 run_check——CPU 密集内核，6/minute 只拦重跑风暴。
    """
    task = _get_task(db, task_id, user)
    if task.state not in ("running", "review"):
        raise HTTPException(status_code=409, detail="请先完成两段式启动（POST /start）")
    if not task.scope_a3:
        raise HTTPException(status_code=409, detail="任务未选择 A3 核查范围")
    findings = _checked_run(db, task, "signature_chain", service.run_signature_chain)
    return {"task_id": task.id,
            "reference_snapshot": reference_visibility.visible_snapshot(db, task, reference_snapshots.get_reference_snapshot(db, task), user),
            "counts": reporting.status_counts(findings),
            **_findings_payload(db, findings)}


@router.post("/tasks/{task_id}/check/a2")
@limiter.limit("6/minute")
def run_a2_check(task_id: int, request: Request, db: Session = Depends(get_db),
                 user=Depends(get_current_any_user)):
    """A2 主体资格与社保资料（并入报审核查链路）：全部已接入批次。

    A-11（B0）限流：同 run_check——CPU 密集内核，6/minute 只拦重跑风暴。
    """
    task = _get_task(db, task_id, user)
    if task.state not in ("running", "review"):
        raise HTTPException(status_code=409, detail="请先完成两段式启动（POST /start）")
    if not task.scope_a3:
        raise HTTPException(status_code=409, detail="任务未选择 A3 核查范围")
    findings = _checked_run(db, task, "a2", service.run_a2_checks)
    return {"task_id": task.id,
            "reference_snapshot": reference_visibility.visible_snapshot(db, task, reference_snapshots.get_reference_snapshot(db, task), user),
            "counts": reporting.status_counts(findings),
            **_findings_payload(db, findings)}


@router.post("/tasks/{task_id}/check/internal-records")
@limiter.limit("6/minute")
def run_internal_records_check(task_id: int, request: Request,
                               db: Session = Depends(get_db),
                               user=Depends(get_current_any_user)):
    """方案 A：仅核对任务内业主材料，不访问外部信用或事故网站。"""
    task = _get_task(db, task_id, user)
    if task.state not in ("running", "review"):
        raise HTTPException(status_code=409, detail="请先完成两段式启动（POST /start）")
    if not task.scope_a1:
        raise HTTPException(status_code=409, detail="任务未选择 A1 核查范围")
    findings = _checked_run(db, task, "internal_records", service.run_internal_records)
    return {"task_id": task.id,
            "reference_snapshot": reference_visibility.visible_snapshot(db, task, reference_snapshots.get_reference_snapshot(db, task), user),
            "counts": reporting.status_counts(findings),
            **_findings_payload(db, findings)}


@router.post("/tasks/{task_id}/check/semantic")
@limiter.limit("6/minute")
def run_semantic_check(task_id: int, request: Request, db: Session = Depends(get_db),
                       user=Depends(get_current_any_user)):
    task = _get_task(db, task_id, user)
    if task.state not in ("running", "review"):
        raise HTTPException(status_code=409, detail="请先开始核查")
    findings = _checked_run(db, task, "contract_semantic", service.run_semantic_checks)
    return {"task_id": task.id, **_findings_payload(db, findings)}


@router.post("/tasks/{task_id}/complete")
def complete_task(task_id: int, db: Session = Depends(get_db), user=Depends(get_current_any_user)):
    """收口任务（P0-2）：范围要求的全部分组均有结论 → state=done + task.completed。

    拍板口径：不做 awaiting_human_review 闸，人工复核走线下（P1-3 回执留痕）。
    """
    task = _get_task(db, task_id, user)
    result = service.complete_review(db, task)
    if not result["ok"]:
        raise HTTPException(status_code=409, detail=result["detail"])
    return {"task_id": task.id, "state": task.state}


@router.post("/tasks/{task_id}/supplement-plan")
def create_supplement_plan(task_id: int, body: SupplementPlanIn,
                           db: Session = Depends(get_db),
                           user=Depends(get_current_any_user)):
    """登记补件局部核查计划，供 P8-03C 后续调度消费。"""
    task = _get_task(db, task_id, user)
    try:
        plan = service.plan_supplement_generation(
            db,
            task,
            generation_id=body.generation_id,
            category=body.category,
            item_ids=body.item_ids,
            material_ids=body.material_ids,
            subject_ids=body.subject_ids,
            material_status=body.material_status,
            parent_review_run_id=body.parent_review_run_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if plan.get("status") == "blocked":
        raise HTTPException(status_code=409, detail={
            "code": plan.get("blocked_reason", "supplement_plan_blocked"),
            "message": "补件计划暂不能执行，请先处理阻塞原因",
            "plan": plan,
        })
    return {"task_id": task.id, "supplement_plan": plan}


@router.get("/tasks/{task_id}/supplement-plan")
def read_supplement_plan(task_id: int, db: Session = Depends(get_db),
                         user=Depends(get_current_any_user)):
    task = _get_task(db, task_id, user)
    return {"task_id": task.id, "supplement_plan": service.get_supplement_generation_plan(task)}


@router.post("/tasks/{task_id}/supplement-edit")
def start_supplement_edit(task_id: int, db: Session = Depends(get_db),
                          user=Depends(get_current_any_user)):
    task = _get_task(db, task_id, user)
    result = service.enter_supplement_edit(db, task)
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result)
    return {"task_id": task.id, **result}


@router.post("/tasks/{task_id}/supplement-rerun")
def activate_supplement_plan(task_id: int, response: Response, db: Session = Depends(get_db),
                             user=Depends(get_current_any_user)):
    task = _get_task(db, task_id, user)
    result = service.activate_supplement_plan(db, task, operator=user.id)
    if result.get("preparing"):
        response.status_code = 202
        return {"task_id": task.id, **result}
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result)
    return {"task_id": task.id, **result}


@router.post("/tasks/{task_id}/page-text-refresh", status_code=202)
@limiter.limit("6/minute")
def start_page_text_refresh(task_id: int, body: PageTextRefreshIn, request: Request,
                            db: Session = Depends(get_db), user=Depends(get_current_any_user)):
    task = _get_task(db, task_id, user)
    if os.getenv("BS_PAGE_TEXT_REFRESH_ENABLED") != "1":
        raise HTTPException(status_code=404, detail="功能暂不可用")
    try:
        return service.start_page_text_refresh(db, task, body.material_id, body.page_nos)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.get("/tasks/{task_id}/page-text-refresh")
def page_text_refresh_status(task_id: int, response: Response, db: Session = Depends(get_db),
                             user=Depends(get_current_any_user)):
    _get_task(db, task_id, user)
    if os.getenv("BS_PAGE_TEXT_REFRESH_ENABLED") != "1":
        raise HTTPException(status_code=404, detail="功能暂不可用")
    response.headers["Cache-Control"] = "private, no-store"
    return {"refresh": service.get_page_text_refresh(db, task_id)}


@router.post("/tasks/{task_id}/rerun")
def rerun_task(task_id: int, body: Optional[RerunIn] = None,
               db: Session = Depends(get_db), user=Depends(get_current_any_user)):
    """任务内重核查（A-02，P0 + BS-P1-13 局部重跑）：作废旧结论 + 免重解析 + 回 running。

    请求体**可选**（缺省=全量，向后兼容旧前端）：`{"scope": "items", "item_ids": [...]}`
    （设计 98 §4.1）。`scope` 仅接受 all / items，其他或 item_ids 形态非法 → 400。

    闸口语义（照 start_review 端点形态）：
      - 解析中（uploading/parsing）→ 409（半份文本会改结论，与 start_review 同口径）；
      - state 不属 {running, review, done}（即 setup 段）→ 409（尚未启动，谈不上重核查）。
    免重解析：parse_state=done 的材料复用已落库 pages_json/seal_json，不重跑重活段。
    """
    task = _get_task(db, task_id, user)
    scope = (body.scope if body is not None else "all") or "all"
    item_ids = body.item_ids if body is not None else None
    if scope not in ("all", "items"):
        raise HTTPException(status_code=400, detail="重跑范围仅支持 all / items")
    if scope == "items":
        cleaned, error = service._clean_rerun_item_ids(item_ids)
        if error:
            raise HTTPException(status_code=400, detail=error)
        item_ids = cleaned
    else:
        item_ids = None
    try:
        result = service.rerun_task(db, task, scope=scope, item_ids=item_ids)
    except knowledge_runs.ReferenceRunSuperseded as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    if not result["ok"]:
        # 请求形态错误 → 400；两闸（解析中 / 非核查态）都是「当前不可重核查」→ 409。
        code = 400 if result.get("reason") == "request" else 409
        raise HTTPException(status_code=code, detail=result["detail"])
    payload = {"task_id": task.id, "state": task.state,
               "reused": result["reused"], "retried": result["retried"],
               "untouched": result["untouched"],
               # 117-B §3.2：重跑响应与审计事件共用同一版本投影。历史任务没有
               # 旧 manifest 时 changed_item_ids 为 null（不可判定），不得伪装成空变化。
               "previous_ruleset_version": result["previous_ruleset_version"],
               "ruleset_version": result["ruleset_version"],
               "changed_item_ids": result["changed_item_ids"],
               # BS-P1-13：范围与回执继承计数同响应交回（与 task.rerun 事件同口径）。
               "scope": result["scope"], "item_ids": result["item_ids"],
               "scope_count": result["scope_count"], "carry_count": result["carry_count"],
               "requested_scope": result["requested_scope"],
               "requested_item_ids": result["requested_item_ids"],
               "rerun_groups": result["rerun_groups"],
               "carried_forward_groups": result["carried_forward_groups"],
               "fallback_reason": result["fallback_reason"]}
    # 与 task.rerun 事件载荷同口径：旧结论被作废后必须把版本号交回前端，
    # 否则前端只能从事件流反查，接口层静默丢字段＝口径不一致。
    if "superseded_version_no" in result:
        payload["superseded_version_no"] = result["superseded_version_no"]
    return payload


@router.post("/tasks/{task_id}/material-edit")
def material_edit(task_id: int, db: Session = Depends(get_db), user=Depends(get_current_any_user)):
    """返回材料修改阶段；保留材料和解析结果，旧结论作废。"""
    task = _get_task(db, task_id, user)
    result = service.enter_material_edit(db, task)
    if not result["ok"]:
        raise HTTPException(status_code=409, detail=result["detail"])
    body = {"task_id": task.id, "state": task.state,
            "already_setup": result.get("already_setup", False)}
    # 同 rerun：只有真的作废了旧版本（has_findings）才有版本号，此字段按需出现。
    if "superseded_version_no" in result:
        body["superseded_version_no"] = result["superseded_version_no"]
    return body


@router.get("/tasks/{task_id}/findings")
def list_findings(task_id: int, kind: str = None,
                  db: Session = Depends(get_db), user=Depends(get_current_any_user)):
    task = _get_task(db, task_id, user)
    q = db.query(BsFinding).filter(BsFinding.task_id == task_id)
    if kind:
        q = q.filter(BsFinding.kind.in_(("negative_list", "negative_candidate"))
                     if kind == "negative_list" else BsFinding.kind == kind)
    findings = ai_advisory_snapshots.filter_current_candidate_findings(db, task, user, q.order_by(BsFinding.id).all())
    negative_coverage = (ai_advisory_snapshots.collect_current_negative_list_coverage(db, task, user)
                         if any(f.item_id == "W16-02" for f in findings) else None)
    snapshot = reference_snapshots.get_reference_snapshot(db, task)
    coverage = ai_advisory_snapshots.collect_current_coverage(db, task, snapshot, reporting.report_review_blocking)
    timeout_ids = {value for group in (coverage or {}).get("groups", [])
                   for value in group.get("reviewable_timeout_finding_ids", [])}
    return {**_findings_payload(db, findings, as_of=task.baseline_date, negative_coverage=negative_coverage, timeout_ids=timeout_ids),
            "reference_snapshot": reference_visibility.visible_snapshot(
                db, task, reference_snapshots.get_reference_snapshot(db, task), user),
            "reference_run": reference_visibility.visible_run(db, task, knowledge_runs.get_reference_run(db, task), user)}


@router.post("/tasks/{task_id}/material-confirmations/{finding_id}")
def confirm_material(task_id: int, finding_id: int, body: MaterialConfirmationIn,
                     db: Session = Depends(get_db), user=Depends(get_current_any_user)):
    _get_task(db, task_id, user)
    task = db.query(BsTask).filter_by(id=task_id).with_for_update().populate_existing().one()
    if task.state not in ("running", "review"):
        raise HTTPException(status_code=409, detail="当前任务状态不能确认材料，请刷新页面")
    readiness = service.report_output_readiness(db, task)
    snapshot = reference_snapshots.get_reference_snapshot(db, task)
    if (not readiness.get("ok") or reference_visibility.snapshot_restricted(db, task, snapshot, user)
            or reference_visibility.run_restricted(db, task, knowledge_runs.get_reference_run(db, task), user)):
        raise HTTPException(status_code=409, detail="核查材料或依据已变化，请刷新并重新核查后确认")
    if body.decision not in ("conforming", "nonconforming"):
        raise HTTPException(status_code=400, detail="请选择符合或不符合")
    finding = db.query(BsFinding).filter_by(id=finding_id, task_id=task_id).with_for_update().first()
    if not finding:
        raise HTTPException(status_code=404, detail="材料核查项不存在")
    if body.subitem_key is not None:
        raise HTTPException(status_code=409, detail="该项目补充要求已移除，请刷新页面")
    if finding.status != "undecidable" or not material_confirmation.is_material_confirmable_finding(finding):
        raise HTTPException(status_code=409, detail="该项不是可确认的待核实材料")
    subject = db.query(BsSubject).filter_by(id=finding.subject_id, task_id=task_id, retired=False).first()
    if not subject or finding.item_id not in {item["id"] for item in checklist.applicable_items(task, subject)}:
        raise HTTPException(status_code=409, detail="该主体或材料要求已变化，请刷新页面")
    selected_material_id = body.material_id if body.material_id is not None else finding.material_id
    material = db.query(BsMaterial).filter_by(id=selected_material_id, task_id=task_id).with_for_update().first()
    run_id = (task.options_snapshot_json or {}).get(reference_snapshots.REVIEW_POINTER)
    token = material_confirmation.evidence_token(finding, material, run_id)
    if not token or token != body.token:
        raise HTTPException(status_code=409, detail="核查或材料已变化，请刷新后重新确认")
    record_item_id = finding.item_id
    latest = (db.query(BsMaterialConfirmation).filter_by(task_id=task_id, finding_id=finding.id, item_id=record_item_id)
              .order_by(BsMaterialConfirmation.id.desc()).with_for_update().first())
    if (latest.id if latest else None) != body.expected_confirmation_id:
        raise HTTPException(status_code=409, detail="材料确认已被更新，请刷新后重新确认")
    row = BsMaterialConfirmation(task_id=task_id, finding_id=finding.id, subject_id=finding.subject_id,
                                 material_id=material.id, item_id=record_item_id, run_id=run_id,
                                 token=token, decision=body.decision, operator_id=user.id,
                                 operator_type=user.user_type)
    db.add(row)
    db.commit()
    return {"finding_id": finding.id, "decision": row.decision, "confirmation_id": row.id}


@router.get("/tasks/{task_id}/todo")
def get_todo(task_id: int, db: Session = Depends(get_db), user=Depends(get_current_any_user)):
    """待办面板的**唯一数据源**（BS-P1-06）：聚合缺件/可补件标记/审查完成度。

    前端只做展示，不得自己归并缺件或另算完成度。口径全部复用既有单一定义点：

    - 缺件归并 = `reporting.missing_summary`（含每类 `roles` / `actionable`，
      roles 取自 `data/missing_material_map.json`）；本端点不新增第二套归并逻辑；
    - 兜底条款（`is_open_term`）**不进 `categories`**，只在 `total_undecidable` 里，
      并由 `open_term_count` 单独披露（与报告 `summary.open_term` 同口径）；
    - `total_items` 是明确材料缺失的补件分母（材料清单项 `hit` 且非兜底），
      `undecidable` 一律进入人工核实而不反推为缺件；`without_missing_count` 是明确
      缺件中进不了任何材料类的历史/兼容条数；一条结论可缺多类材料 ⇒ 各类 `count` 之和可 ≥
      `total_items`（同一结论在每类各计一次）；
    - `readiness` 只读本代冻结快照（`reference_snapshots.review_readiness`），
      无快照（历史任务 / seed 缺失）时为 None，不补造当前五步。

    鉴权与 list_findings 一致：`_get_task` 先行（任务不存在 → 404；越权 → 403）。
    与 `build_report` 同口径剔除已忽略的结论（命中项才可忽略，不影响不可判定计数）。
    """
    task = _get_task(db, task_id, user)
    findings = (db.query(BsFinding)
                .filter(BsFinding.task_id == task_id)
                .order_by(BsFinding.id).all())
    findings = ai_advisory_snapshots.filter_current_candidate_findings(db, task, user, findings)
    visible = [f for f in findings if not reporting.is_ignored_review(f.review_json)]
    undecidable = [f for f in visible if f.status == "undecidable"]
    active_ids = service.active_subject_ids(db, task)
    subjects = (db.query(BsSubject)
                .filter(BsSubject.task_id == task.id, BsSubject.id.in_(active_ids))
                .order_by(BsSubject.id).all()) if active_ids else []
    checklist_summary = material_confirmation.attach_confirmations(
        db, task, checklist.build_summary(task, subjects, findings), findings)
    summary = reporting.missing_summary(visible, checklist_summary)
    return {
        "task_id": task.id,
        "total_undecidable": len(undecidable),
        "open_term_count": sum(1 for f in undecidable if reporting.is_open_term(f)),
        "total_items": summary["total_items"],
        "without_missing_count": summary["without_missing_count"],
        "categories": summary["categories"],
        "readiness": reference_snapshots.review_readiness(
            reference_snapshots.get_reference_snapshot(db, task)),
        "checklist_summary": checklist_summary,
    }


def _findings_payload(db: Session, findings: list, *, as_of=None, negative_coverage=None, timeout_ids=None) -> dict:
    """结论列表统一出口（刀D，设计 104 §3）。

    依据条款视图一次解析、整批复用：`basis_lookup()` 要读三份 JSON，逐条调用会把
    一次列表请求变成 N 次读盘；且依据库是只读数据，同一响应内必须同源。
    """
    basis_map = rules.configured_basis_lookup(as_of=as_of)
    subject_ids = {f.subject_id for f in findings if getattr(f, "subject_id", None) is not None}
    subjects_by_id = ({subject.id: subject for subject in db.query(BsSubject)
                       .filter(BsSubject.id.in_(subject_ids)).all()} if subject_ids else {})
    return {"findings": [_finding_dto(f, basis_map, subjects_by_id, negative_coverage, timeout_ids) for f in findings]}


def _finding_dto(f: BsFinding, basis_map: dict = None, subjects_by_id: dict = None, negative_coverage=None, timeout_ids=None) -> dict:
    # 刀D（设计 104 §3）：条款视图=条目→依据库坐标（文号/条号/原文/校对状态）。
    # 查不到就是 None，前端如实显示“该条目未建立条款坐标”，不猜条款。
    basis = (basis_map or {}).get(f.item_id) if basis_map is not None else None
    open_term = reporting.is_open_term(f)
    subject = (subjects_by_id or {}).get(getattr(f, "subject_id", None))
    item = next((row for row in load_report_checklist() if row.get("id") == f.item_id), None)
    supplement_hint = ({"label": item["label"], "category": "submission_packet", "item_id": f.item_id}
                       if f.kind == "completeness" and f.status == "undecidable"
                       and item and item.get("kind") == "document" else None)
    return {
        "id": f.id, "item_id": f.item_id, "kind": "negative_list" if f.kind == "negative_candidate" else f.kind,
        "subject_id": f.subject_id,
        "subject": ({"subject_id": subject.id, "stable_code": subject.stable_code,
                     "name": subject.name, "subject_type": subject.subject_type}
                    if subject is not None else None),
        "status": f.status, "status_label": STATUS_LABELS.get(f.status, f.status),
        "material_only": checklist.is_material_only_finding(f),
        "report_review_blocking": reporting.report_review_blocking(f),
        # open_term 是只读展示标记；保留原始三态 status，不在接口层新增判定状态。
        "open_term": open_term,
        # A-13（B2）：严重度/置信档位（前端消费；矩阵判定见 severity.py）
        "severity": f.severity,
        "severity_label": severity.SEVERITY_LABELS.get(f.severity, f.severity),
        "confidence": f.confidence,
        "message": finding_text.display_finding_message(f, negative_coverage, f.id in (timeout_ids or set())), "quote": f.quote, "page": f.page, "source": f.source,
        "location_mode": f.location_mode,
        "material_id": f.material_id,
        "evidence": f.evidence_json or [],
        "decision_trace": reporting.decision_trace(f, basis),
        # 刀D（设计 104 §3）：规则依据视图（只读，来自依据库；None=未收录坐标）。
        "basis": basis,
        # BS-P1-06 / BS-P1-12：逐条缺件的**唯一来源**（keys/labels/categories/
        # by_category/source 由 reporting.finding_missing 统一给出）。前端不得再从
        # message 拆「（缺：…）」文案——文案已改过一次，拆文案每改一次就坏一次。
        "missing": reporting.finding_missing(f),
        "supplement_hint": supplement_hint,
        "review": reporting.review_dto(f.review_json),
    }


@router.patch("/findings/{finding_id}/review")
def review_finding(finding_id: int, body: ReviewIn,
                   db: Session = Depends(get_db), user=Depends(get_current_any_user)):
    """人工处置回执：accepted/ignored；兼容旧 confirm/reject 请求。

    BsFinding.review_json 单列落痕（NULL=未复核），再复核覆盖（最后回执为准）；
    报告与导出物条目随之展示回执。复核针对已落库结论，重跑核查重写结论时
    旧回执随 _replace_findings 自然作废。

    当前候选规则：`accepted`/`ignored` 可作用于命中和无法核实项，说明可选；
    `verified` 不再是业务动作，旧客户端提交时直接返回 400，不写入新回执。
    """
    # BS-1B：先反查 finding 归属（不存在 → 404，越权 → 403），再做既有门禁。
    finding = access.require_bs_finding(db, finding_id, user.id, user.user_type)
    if finding.kind == "negative_candidate":
        task = _get_task(db, finding.task_id, user)
        if finding.id not in ai_advisory_snapshots.active_candidate_finding_ids(db, task, user):
            raise HTTPException(status_code=409, detail="该核查线索已失效或依据授权发生变化，请重新核查")
    decision = reporting.canonical_review_decision(body.decision)
    if decision not in ("accepted", "ignored"):
        raise HTTPException(status_code=400,
                            detail="decision 仅支持 accepted / ignored")
    if finding.status not in ("hit", "undecidable"):
        raise HTTPException(status_code=409, detail="只有不符合或无法核实项可以采纳或忽略")
    if decision == "ignored" and (not isinstance(body.note, str) or not body.note.strip()):
        raise HTTPException(status_code=400, detail="忽略必须填写理由")
    reviewer = getattr(user, "display_name", None) or "用户"
    task = _get_task(db, finding.task_id, user)
    negative_coverage = (ai_advisory_snapshots.collect_current_negative_list_coverage(
        db, task, user) if finding.item_id == "W16-02" else None)
    coverage = ai_advisory_snapshots.collect_current_coverage(
        db, task, reference_snapshots.get_reference_snapshot(db, task), reporting.report_review_blocking)
    timeout_ids = {value for group in (coverage or {}).get("groups", [])
                   for value in group.get("reviewable_timeout_finding_ids", [])}
    service.review_finding(db, finding, reviewer=reviewer,
                           decision=decision, note=body.note)
    return _findings_payload(db, [finding], negative_coverage=negative_coverage, timeout_ids=timeout_ids)["findings"][0]


_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def _sse_session_factory():
    """SSE 流用会话工厂（可依赖注入）：流生命周期独立于请求级依赖。"""
    return SessionLocal


@router.get("/tasks/{task_id}/events")
@limiter.limit("60/minute")
def task_events(task_id: int, request: Request, after_seq: int = Query(None, ge=0),
                db: Session = Depends(get_db), user=Depends(get_current_any_user),
                session_factory=Depends(_sse_session_factory)):
    """任务事件流（P0-1）：DB 按 sequence 增量轮询转 SSE。

    续传双通道：显式 after_seq 查询参数优先，否则取 EventSource 断线自动
    携带的 Last-Event-ID 头（流内 id 行同源）。终态 done / 任务已删除收流。

    A-11（B0）限流：SSE 是长连接，恶意的重连风暴会耗尽连接与 DB 轮询预算。
    **SSE 单独口径**：限值取宽松的 60/minute，与核查端点的 6/minute 刻意区分——
    EventSource 正常断线重连几秒一次（增量轮询的基线频次本就高于交互点击），
    60/minute 只拦「同一路径每分钟 60 次以上」的滥用，不锁正常用户（沿用 G6
    「观察端点不宜过紧」的既定纪律，此处只做滥用兜底）。
    """
    _get_task(db, task_id, user)
    if after_seq is None:
        try:
            after_seq = max(0, int(request.headers.get("Last-Event-ID") or "0"))
        except ValueError:
            after_seq = 0
    db.close()  # 流用独立 session_factory；请求级连接提前归还
    return StreamingResponse(
        service.stream_events(task_id=task_id, since_sequence=after_seq,
                              session_factory=session_factory),
        media_type="text/event-stream", headers=_SSE_HEADERS)


_REVIEW_STAGES = ("draft", "confirmed")
# 报告读取不由人工处置决定；此上限只服务于旧客户端仍可能读取的结构化诊断。
_BLOCKING_LIMIT = 100


def _blocking_detail(rows: list, message: str) -> dict:
    """历史结构化诊断的兼容投影；不参与当前报告或意见书的读取资格。

    `rows` 为统一形态 dict（finding_id/item_id/label/message/severity/status）。
    排序 severity→item_id 稳定；超过 100 项只回前 100，并标 `truncated`，
    `count` 始终是**全量**未处置条数（读者不能把截断后的条数当总数）。
    """
    ordered = sorted(rows, key=lambda row: (
        reporting.SEVERITY_RANK.get(row.get("severity"), 3),
        str(row.get("item_id") or ""), row.get("finding_id") or 0))
    detail = {
        "code": "legacy_blocking_detail",
        "message": message,
        "count": len(ordered),
        "blocking": [{
            "finding_id": row.get("finding_id"),
            "item_id": row.get("item_id"),
            "label": row.get("label") or row.get("item_id"),
            "message": row.get("message"),
            "severity": row.get("severity"),
            "severity_label": severity.SEVERITY_LABELS.get(
                row.get("severity"), row.get("severity")),
            "status": row.get("status"),
            "status_label": reporting.status_label(row.get("status")),
        } for row in ordered[:_BLOCKING_LIMIT]],
    }
    if len(ordered) > _BLOCKING_LIMIT:
        detail["truncated"] = True
    return detail


def _require_no_pending_hits(db: Session, task_id: int, stage: str = "draft", *, user=None) -> None:
    """报告稿别与人工门禁：draft 可查看，confirmed 必须处理不可判定项。

    任务 83 的页面一直按“无法核实处理后才能生成报告”工作；此前 API/导出层
    仍允许 confirmed 投影，造成页面与接口口径漂移。命中项继续按方案 A 保留在
    报告中，只有未处置且要求人工复核的 undecidable 会阻止 confirmed，
    与报告及工作台共享同一非阻断判据，并返回可读数量和条目。
    """
    if stage not in _REVIEW_STAGES:
        raise HTTPException(status_code=400, detail="stage 仅支持 draft / confirmed")
    if stage != "confirmed":
        return
    pending = (db.query(BsFinding)
               .filter(BsFinding.task_id == task_id,
                       BsFinding.status == "undecidable")
               .all())
    if user is not None:
        task = db.get(BsTask, task_id)
        pending = ai_advisory_snapshots.filter_current_candidate_findings(db, task, user, pending)
    pending = [finding for finding in pending
               if reporting.report_review_blocking(finding)
               and reporting.canonical_review_decision(
                   (finding.review_json or {}).get("decision")) not in ("accepted", "ignored")]
    if pending:
        raise HTTPException(status_code=409, detail={
            "code": "pending_undecidable_review",
            "message": "还有 %d 项无法核实待处理，处理后才能生成确认版报告。" % len(pending),
            "count": len(pending),
            "finding_ids": [finding.id for finding in pending[:_BLOCKING_LIMIT]],
        })
    return


def _require_no_pending_frozen_findings(snapshot: dict, stage: str) -> None:
    """历史确认稿只依据该版冻结的处置和证据，不借用当前任务状态。"""
    if stage != "confirmed":
        return
    rows = snapshot.get("findings")
    if (not isinstance(rows, list) or any(not isinstance(row, dict)
            or row.get("status") not in {"hit", "miss", "undecidable"} for row in rows)):
        raise HTTPException(status_code=409, detail={"code": "historical_review_incomplete",
            "message": "该历史版本缺少完整核查状态，不能生成确认版报告。"})
    pending = []
    for row in rows:
        if row.get("status") != "undecidable":
            continue
        finding = BsFinding(item_id=row.get("item_id"), status=row.get("status"),
                            evidence_json=row.get("evidence") or [])
        review = row.get("review") if isinstance(row.get("review"), dict) else {}
        if (row.get("material_only") is not True and reporting.report_review_blocking(finding)
                and reporting.canonical_review_decision(review.get("decision")) not in ("accepted", "ignored")):
            pending.append(row.get("finding_id"))
    if pending:
        raise HTTPException(status_code=409, detail={
            "code": "pending_undecidable_review",
            "message": "该历史版本还有 %d 项无法核实待处理，不能生成确认版报告。" % len(pending),
            "count": len(pending), "finding_ids": pending[:_BLOCKING_LIMIT],
        })


def _mark_stage(payload: dict, stage: str) -> dict:
    """稿别标注统一走 reporting.mark_stage（报告层单一实现）。"""
    return reporting.mark_stage(payload, stage)


def _attach_generation_identity(payload: dict, *, task: BsTask,
                                version_no: int | None, snapshot: dict | None = None,
                                db: Session) -> dict:
    """在报告/意见书只读投影中带出同代身份，避免导出物与当前代混淆。"""
    meta = dict(payload.get("meta") or {})
    options = (snapshot or {}).get("task", {}).get("options_snapshot") if snapshot else None
    if not isinstance(options, dict):
        options = task.options_snapshot_json or {}
    plan = options.get("supplement_plan") if isinstance(options, dict) else None
    versions = service.list_review_versions(db, task)
    latest_version = max(versions, key=lambda row: row.get("version_no", 0)) if versions else None
    meta["review_version_no"] = version_no if version_no is not None else (
        latest_version.get("version_no") if latest_version else None
    )
    meta.update(supplement_generation.generation_metadata(plan))
    payload["meta"] = meta
    return payload


def _require_current_output_ready(db: Session, task: BsTask, stage: str,
                                  *, require_reading_complete: bool = False, user=None) -> None:
    """当前初稿/确认稿共用报告可读性前提；不把收口闸门带入报告读取。"""
    if stage not in _REVIEW_STAGES:
        raise HTTPException(status_code=400, detail="stage 仅支持 draft / confirmed")
    readiness = service.report_output_readiness(
        db, task, require_reading_complete=require_reading_complete)
    if not readiness["ok"]:
        raise HTTPException(status_code=409, detail={
            "code": readiness["code"], "message": readiness["detail"],
            "missing_groups": readiness["missing_groups"],
            "pending_groups": readiness["pending_groups"],
        })
    _require_no_pending_hits(db, task.id, stage, user=user)


def _output_payload(db: Session, task: BsTask, kind: str, stage: str,
                    version_no: int | None, *, user, require_reading_complete: bool = False) -> dict:
    """当前报告走既有守门；历史只读冻结内容与回执，绝不借当前结果补齐。

    历史版本返回前做**展示归一化**（`reporting.normalize_output_payload`）：
    counts 中文旧键→英文键、status_label/decision_label 按状态/处置重取；
    只改返回值、不写库，快照本体保持 append-only（设计 100 §2.3）。调用方的
    report_digest/opinion_digest 在本函数返回之后计算，故与当前报告同口径。
    """
    if version_no is None:
        _require_current_output_ready(
            db, task, stage, require_reading_complete=require_reading_complete, user=user)
        if (reference_visibility.snapshot_restricted(db, task, reference_snapshots.get_reference_snapshot(db, task), user)
                or reference_visibility.run_restricted(db, task, knowledge_runs.get_reference_run(db, task), user)):
            raise HTTPException(status_code=409, detail="参考依据授权已变化，请联系管理员核对后再查看或导出报告")
        builder = reporting.build_report if kind == "report" else reporting.build_opinion
        output = builder(db, task)
        if "reference_snapshot" in output:
            output["reference_snapshot"] = reference_visibility.visible_snapshot(db, task, output["reference_snapshot"], user)
        return _attach_generation_identity(
            _mark_stage(output, stage), task=task, version_no=None, db=db)
    if stage not in _REVIEW_STAGES:
        raise HTTPException(status_code=400, detail="stage 仅支持 draft / confirmed")
    version = service.get_review_version(db, task, version_no)
    if version is None:
        raise HTTPException(status_code=404, detail="审查版本不存在")
    snapshot = reference_visibility.visible_version(db, task, version, user)["snapshot"] or {}
    raw_snapshot = version["snapshot"] or {}
    if (reference_visibility.snapshot_restricted(db, task, raw_snapshot.get("reference_snapshot"), user)
            or reference_visibility.run_restricted(db, task, raw_snapshot.get("reference_run"), user)):
        raise HTTPException(status_code=409, detail="历史报告的参考依据授权已变化，请联系管理员核对")
    if not isinstance(snapshot.get("findings"), list) or not isinstance(snapshot.get(kind), dict) or not snapshot[kind]:
        if snapshot.get("report_unavailable_reason") == "ruleset_version_mismatch":
            raise HTTPException(status_code=409, detail="该历史版本使用的规则已变更，未保存可还原的旧版报告；原始核查结果和人工记录仍保留")
        if snapshot.get("report_unavailable_reason") == "ruleset_version_unknown":
            raise HTTPException(status_code=409, detail="该历史版本未记录规则版本，无法还原旧版报告；原始核查结果和人工记录仍保留")
        raise HTTPException(status_code=409, detail="该历史版本未留存可导出的核查报告")
    _require_no_pending_frozen_findings(snapshot, stage)
    payload = deepcopy(snapshot[kind])
    # S2内层报告尚无此字段，引用只能来自同一历史版本的顶层快照。
    payload["reference_snapshot"] = deepcopy(snapshot.get("reference_snapshot"))
    # 归一化只作用于返回值；report_digest 由调用方在归一化之后计算。
    payload = reporting.normalize_output_payload(payload, kind)
    return _attach_generation_identity(
        _mark_stage(payload, stage), task=task, version_no=version_no,
        snapshot=snapshot, db=db)


@router.get("/tasks/{task_id}/report")
def get_report(task_id: int, stage: str = "draft", version_no: int | None = None, db: Session = Depends(get_db),
               user=Depends(get_current_any_user)):
    """核查报告 v0：纯聚合已落库结论（不重算），面向非技术读者。

    report_digest 为展示用校验码（sha256，generated_at 不参与哈希），
    在哈希计算之后注入，不参与哈希体本身；完整值与复算口径见证据包。
    """
    task = _get_task(db, task_id, user)
    report = _output_payload(db, task, "report", stage, version_no, user=user)
    report["report_digest"] = export.report_digest(report)
    return report


def _attachment_response(data: bytes, media_type: str, file_name: str) -> Response:
    """内存字节流附件下载；头构造收敛到 `app.utils.download`（RFC 5987 单一定义点）。"""
    return Response(content=data, media_type=media_type,
                    headers={"Content-Disposition": attachment_disposition(file_name, fallback="report")})


@router.get("/tasks/{task_id}/report/export")
def export_report(task_id: int, fmt: str = "docx", stage: str = "draft", version_no: int | None = None,
                  db: Session = Depends(get_db), user=Depends(get_current_any_user)):
    """报告导出（W3-3）：Word/PDF，只渲染 build_report 聚合结果，不重算。"""
    task = _get_task(db, task_id, user)
    report = _output_payload(
        db, task, "report", stage, version_no, user=user,
        require_reading_complete=(stage == "confirmed" and version_no is None))
    digest = export.report_digest(report)
    exported_at = datetime.now().isoformat(timespec="seconds")
    safe_title = export.safe_filename(task.title)
    if fmt == "docx":
        data = export.word_bytes(report, digest=digest, exported_at=exported_at)
        mime, ext = ("application/vnd.openxmlformats-officedocument"
                     ".wordprocessingml.document"), "docx"
    elif fmt == "pdf":
        data, mime, ext = (export.pdf_bytes(report, digest=digest, exported_at=exported_at),
                           "application/pdf", "pdf")
    else:
        raise HTTPException(status_code=400, detail="fmt 仅支持 docx / pdf")
    export.record_export(db, task, fmt, digest, exported_at)
    return _attachment_response(data, mime, "%s-核查报告.%s" % (safe_title, ext))


@router.get("/tasks/{task_id}/report/evidence-bundle")
def download_evidence_bundle(task_id: int, stage: str = "draft",
                             db: Session = Depends(get_db),
                             user=Depends(get_current_any_user)):
    """简化存证（W3-3）：报告 JSON（哈希+时间戳存证块）+ 材料原件副本 zip。"""
    task = _get_task(db, task_id, user)
    _require_current_output_ready(
        db, task, stage, require_reading_complete=(stage == "confirmed"), user=user)
    if (reference_visibility.snapshot_restricted(db, task, reference_snapshots.get_reference_snapshot(db, task), user)
            or reference_visibility.run_restricted(db, task, knowledge_runs.get_reference_run(db, task), user)):
        raise HTTPException(status_code=409, detail="参考依据授权已变化，请联系管理员核对后再导出证据包")
    data, digest, exported_at = export.evidence_bundle_bytes(db, task, stage=stage, user=user)
    export.record_export(db, task, "evidence-bundle", digest, exported_at)
    safe_title = export.safe_filename(task.title)
    return _attachment_response(data, "application/zip",
                                "%s-证据包.zip" % safe_title)


@router.get("/tasks/{task_id}/opinion")
def get_opinion(task_id: int, stage: str = "draft", version_no: int | None = None, db: Session = Depends(get_db),
                user=Depends(get_current_any_user)):
    """审查意见书（对齐 118 号文附件1）：纯聚合已落库结论，不重算。

    建议结论从严：有命中即建议「不符合」；存在不可判定项则**不出结论**。
    第 1 步材料缺件进入补件清单，后续步骤异常单列为人工核实项（AGENTS 纪律 4）。
    `opinion_digest` 为内容指纹，口径与报告校验码一致。
    """
    task = _get_task(db, task_id, user)
    opinion = _output_payload(db, task, "opinion", stage, version_no, user=user)
    opinion["opinion_digest"] = export.report_digest(opinion)
    return opinion


@router.get("/tasks/{task_id}/opinion/preview")
def preview_opinion(task_id: int, stage: str = "draft", version_no: int | None = None,
                    db: Session = Depends(get_db), user=Depends(get_current_any_user)):
    """只读预览同一意见书渲染内容，不记录导出事件。"""
    task = _get_task(db, task_id, user)
    opinion = _output_payload(db, task, "opinion", stage, version_no, user=user,
                              require_reading_complete=(stage == "confirmed" and version_no is None))
    digest = export.report_digest(opinion)
    data = export.opinion_pdf_bytes(opinion, digest=digest)
    return Response(content=data, media_type="application/pdf", headers={
        "Cache-Control": "private, no-store", "X-Opinion-Digest": digest})


@router.get("/tasks/{task_id}/opinion/preview-markdown")
def preview_opinion_markdown(task_id: int, stage: str = "draft", version_no: int | None = None,
                             db: Session = Depends(get_db), user=Depends(get_current_any_user)):
    task = _get_task(db, task_id, user)
    opinion = _output_payload(db, task, "opinion", stage, version_no, user=user,
                              require_reading_complete=(stage == "confirmed" and version_no is None))
    digest = export.report_digest(opinion)
    return {"markdown": export.opinion_markdown(opinion, digest), "opinion_digest": digest}


@router.get("/tasks/{task_id}/opinion/export")
def export_opinion(task_id: int, fmt: str = "docx", stage: str = "draft", version_no: int | None = None, expected_digest: str | None = None, db: Session = Depends(get_db),
                   user=Depends(get_current_any_user)):
    """审查意见书导出（Word/PDF）：只渲染 build_opinion 聚合结果，不重算。"""
    task = _get_task(db, task_id, user)
    opinion = _output_payload(
        db, task, "opinion", stage, version_no, user=user,
        require_reading_complete=(stage == "confirmed" and version_no is None))
    digest = export.report_digest(opinion)
    if expected_digest is not None and expected_digest != digest:
        raise HTTPException(status_code=409, detail="意见书内容已变化，请重新生成预览后再导出")
    exported_at = datetime.now().isoformat(timespec="seconds")
    if fmt == "docx":
        data = export.opinion_word_bytes(opinion, digest=digest, exported_at=exported_at)
        mime, ext = ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx")
    elif fmt == "pdf":
        data = export.opinion_pdf_bytes(opinion, digest=digest, exported_at=exported_at)
        mime, ext = "application/pdf", "pdf"
    else:
        raise HTTPException(status_code=400, detail="fmt 仅支持 docx / pdf")
    export.record_export(db, task, "opinion-%s" % fmt, digest, exported_at)
    return _attachment_response(
        data,
        mime,
        "%s-审查意见书.%s" % (export.safe_filename(task.title), ext))
