"""工程分包审查智能体服务层（W1：任务/材料/事件 + 文件摄取 + A3-01 对表编排）。"""
import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import uuid
from collections import Counter
from copy import deepcopy
from types import SimpleNamespace
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import func, or_, update
from sqlalchemy.orm import Session

from ...database import SessionLocal
from ...config import get_settings
from ...services import token_service
from ...services.billing.bindings import effective_model_code_or_default
from ...services.billing.model_context import use_model
from . import (a1_check, a2_check, a3_check, extract, internal_records, parsing,
               pinning, reporting, severity, signature_chain, zip_ingestion,
               semantic_review, knowledge_runs, reference_snapshots, subject_reviews,
               ruleset, rule_item_catalog, checklist, knowledge, material_versions, supplement_impact,
               supplement_generation, ai_guarded_review, ai_advisory_snapshots, ai_review_batch, reference_visibility,
               personnel_evidence, certificate_number_check, identity_observation, negative_candidate_plan,
               section_projection, material_confirmation)
from ..startup_conditions_check.llm import client_from_env, run_sync
from .classify import ROLE_LABELS, classify_material, validate_materials
from .finding_text import bounded_finding_message
from .ai_current_guard import AiContextChanged, assert_ai_context_current
from .identity_labels import subject_identity_label
from .material_facts import persist_material_facts
from .relation_resolution import resolve_task_relations
from .models import (PARSE_STATE_STORED, STATUS_LABELS, BsFinding, BsMaterial, BsMaterialConfirmation, BsPacket,
                     BsReviewVersion, BsSubject, BsTask, BsTaskEvent)
from .rules import load_config

logger = logging.getLogger(__name__)
_PENDING_SUBJECT_TYPE_SCANS = Counter()
_PENDING_SUBJECT_TYPE_SCANS_LOCK = threading.Lock()
_SUBJECT_IDENTITY_LOCK = threading.Lock()
SUPPORTED_JURISDICTIONS = {"road"}
SUPPORTED_PROFILES = {"road_default", "road_group"}


def _mark_subject_type_scan_pending(task_id: int, subject_id: int, delta: int) -> None:
    key = (task_id, subject_id)
    with _PENDING_SUBJECT_TYPE_SCANS_LOCK:
        _PENDING_SUBJECT_TYPE_SCANS[key] += delta
        if _PENDING_SUBJECT_TYPE_SCANS[key] <= 0:
            _PENDING_SUBJECT_TYPE_SCANS.pop(key, None)


def _material_sha256(path: str) -> str | None:
    """读取材料输入指纹；失败时返回 None，不阻断材料解析。"""
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _pending_subject_type_scans(task_id: int) -> list:
    with _PENDING_SUBJECT_TYPE_SCANS_LOCK:
        return sorted(subject_id for (pending_task_id, subject_id), count
                      in _PENDING_SUBJECT_TYPE_SCANS.items()
                      if pending_task_id == task_id and count > 0)


def _clean_str(value) -> str:
    """标段文本字段归一：首尾空白去除，空串/None → None（呈现层按「未填写」占位）。"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_date(value) -> "date":
    """核查基准日归一：date / datetime / 'YYYY-MM-DD' 字符串 → date；空 → None。

    HTTP 层 pydantic 已把 ISO 字符串解析成 date；直调 service 的路径（测试/脚本）
    允许传字符串或 date，此处统一归一，避免 sqlite/MySQL 落库口径歧义。
    非法格式 → ValueError（router 转 400），不静默丢弃。
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except (ValueError, TypeError):
        raise ValueError("核查基准日格式无效，应为 YYYY-MM-DD")


def _coerce_jurisdiction(value) -> str | None:
    value = _clean_str(value)
    if value is None:
        return None
    value = value.lower()
    if value not in SUPPORTED_JURISDICTIONS:
        raise ValueError("当前仅支持 road 辖域，其他体系暂未开放")
    return value


def _coerce_profile_code(value) -> str | None:
    value = _clean_str(value)
    if value is None:
        return None
    value = value.lower()
    if value not in SUPPORTED_PROFILES:
        raise ValueError("规则 profile 无效，应为 road_default 或 road_group")
    return value


def _section_snapshot(task: "BsTask") -> dict:
    """A-03 标段快照面（R24：快照须与任务实际标段一致，start 时固化）。"""
    return {
        "project_name": task.project_name,
        "contract_section": task.contract_section,
        "baseline_date": task.baseline_date.isoformat() if task.baseline_date else None,
    }


def _missing_section_suggestions(db: Session, task: "BsTask") -> dict:
    materials = db.query(BsMaterial).filter(BsMaterial.task_id == task.id).all()
    suggestions = section_projection.section_suggestions(material_versions.active_input_materials(materials))
    section = suggestions.get("contract_section") or {}
    source_project = section.get("derived_from_project")
    if source_project and task.project_name and re.sub(r"\s+", "", source_project) != re.sub(r"\s+", "", task.project_name):
        suggestions.pop("contract_section", None)
    return {field: candidate for field, candidate in suggestions.items() if not _clean_str(getattr(task, field))}


def _apply_section_suggestions(task: "BsTask", suggestions: dict) -> None:
    for field, candidate in suggestions.items():
        setattr(task, field, candidate["value"])


def _routing_snapshot(task: "BsTask") -> dict:
    """P4-05：保存任务路由原始输入，解析结果由知识层按当前绑定只读计算。"""
    route = knowledge.resolve_task_route(task)
    return {
        "jurisdiction": task.jurisdiction,
        "profile_code": task.profile_code,
        "resolved_profile": route["profile_code"],
        "status": route["status"],
        "reason_code": route["reason_code"],
    }


def _upload_max_bytes() -> int:
    """B0（A-08）：上传体积上限（MB → 字节），BLOCKCHAIN_SUPERVISION_UPLOAD_MAX_MB 可调。

    默认 200MB 与受管 Nginx 配置的 client_max_body_size 200m 对齐；
    配置读取与 structural_check 同口径（`from ...config import get_settings`）。
    """
    from ...config import get_settings

    return max(1, int(get_settings().BLOCKCHAIN_SUPERVISION_UPLOAD_MAX_MB)) * 1024 * 1024


def _check_upload_size(content: bytes) -> None:
    """B0（A-08）二道防线：超限 413——直调 service 的路径在写盘前被拒。

    router 层已用 UploadFile.size 前置判 + 读取封顶（内存尖峰消除）；本函数是
    「不经 router 直接调 ingest_material」的二道防线（与 structural_check 双层同构）。
    """
    limit = _upload_max_bytes()
    if len(content) > limit:
        raise HTTPException(
            413,
            f"文件体积超过上限（{limit // (1024 * 1024)}MB），请压缩或拆分后重新上传",
        )


def add_event(db: Session, task_id: int, event_type: str, *, stage: str = None,
              payload: dict = None, commit: bool = True) -> int:
    """写任务事件并返回序号。

    取号必须加锁读（ORDER BY sequence DESC LIMIT 1 FOR UPDATE）：REPEATABLE-READ 下
    普通读会拿到冻结快照，长事务内并发取号撞唯一键（OC 999072 定案口径）。
    """
    last = (
        db.query(BsTaskEvent.sequence)
        .filter(BsTaskEvent.task_id == task_id)
        .order_by(BsTaskEvent.sequence.desc())
        .limit(1)
        .with_for_update()
        .first()
    )
    seq = (last[0] if last else 0) + 1
    db.add(BsTaskEvent(task_id=task_id, sequence=seq, event_type=event_type,
                       stage=stage, payload_json=payload or {}))
    if commit:
        db.commit()
    else:
        db.flush()
    return seq


def record_anchor_recovery(db: Session, task_id: int, *, changes: list[dict],
                           method: str = "local_ocr_exact_quote") -> int:
    """在调用方事务内记录已验证的证据坐标恢复，不独立提交。"""
    if not changes:
        raise ValueError("坐标恢复记录不能为空")
    if method not in {"local_ocr_exact_quote", "local_ocr_tex_text_wrapper"}:
        raise ValueError("未知的坐标恢复方式")
    return add_event(db, task_id, "evidence.anchor_recovered", stage="review",
                     payload={"method": method, "count": len(changes),
                              "changes": changes}, commit=False)


def _default_baseline_date() -> date:
    """默认核查日期采用北京时间，不依赖服务器部署时区。"""
    return datetime.now(timezone(timedelta(hours=8))).date()


def baseline_date_source(task: BsTask) -> str:
    """旧任务的非空日期来源未知，按人工选择保留；空值可自动补齐。"""
    source = (task.options_snapshot_json or {}).get("baseline_date_source")
    return source if source in {"automatic", "manual"} else ("manual" if task.baseline_date else "automatic")


def create_task(db: Session, *, conversation_id: int, title: str,
                scope_a1: bool, scope_a3: bool, created_by: int,
                created_by_type: str = "web",
                project_name: str = None, contract_section: str = None,
                baseline_date=None, jurisdiction: str = None,
                profile_code: str = None) -> BsTask:
    if not scope_a1 and not scope_a3:
        raise ValueError("至少选择一个核查范围（A1 违法转包分包识别 / A3 报审资料合规核查）")
    task = BsTask(
        conversation_id=conversation_id, title=title,
        scope_a1=scope_a1, scope_a3=scope_a3,
        scale="standard",  # 标准档唯一实装；写进快照即锁定
        options_snapshot_json={"scope": {"A1": scope_a1, "A3": scope_a3}, "scale": "standard",
                               "baseline_date_source": "manual" if _coerce_date(baseline_date) else "automatic"},
        project_name=_clean_str(project_name),
        contract_section=_clean_str(contract_section),
        baseline_date=_coerce_date(baseline_date) or _default_baseline_date(),
        jurisdiction=_coerce_jurisdiction(jurisdiction),
        profile_code=_coerce_profile_code(profile_code),
        created_by=created_by, created_by_type=created_by_type,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    add_event(db, task.id, "task.created", payload={"title": title})
    return task


SUBJECT_TYPES = {"professional", "labor"}
SUBJECT_ORIGINS = {"explicit", "material_auto"}


def subject_name_status(subject: BsSubject) -> str:
    """返回主体名称的可消费状态，不把 OCR 自动抽取当成人工确认。"""
    if not _clean_str(subject.name):
        return "missing"
    if subject.origin == "material_auto":
        return "low_confidence"
    return "confirmed"


def subject_name_warning(subject: BsSubject) -> str | None:
    status = subject_name_status(subject)
    if status == "missing":
        return "未识别到分包单位名称，请在开始核查前补充确认"
    if status == "low_confidence":
        return "名称来自材料自动抽取，可能受 OCR 影响，请在开始核查前确认或修改"
    return None


def create_subject(db: Session, task: BsTask, *, subject_type: str = None,
                    name: str = None, operator=None,
                    origin: str = "explicit") -> BsSubject:
    """创建任务内主体。仅建立归集锚点，不执行16/14项核查。"""
    if not _setup_write_allowed(task):
        raise ValueError("核查已开始，不能新增分包主体")
    if subject_type is not None and subject_type not in SUBJECT_TYPES:
        raise ValueError("主体类型仅支持 professional / labor")
    if origin not in SUBJECT_ORIGINS:
        raise ValueError("主体来源不受支持")
    # 未确认类型时使用通用主体编号，待人工确认后仍保持稳定编号。
    prefix = ("专业分包" if subject_type == "professional" else
              "劳务分包" if subject_type == "labor" else "分包")
    count = db.query(BsSubject).filter(BsSubject.task_id == task.id,
                                       BsSubject.subject_type == subject_type).count()
    stable_code = f"{prefix}{count + 1}"
    while db.query(BsSubject).filter(BsSubject.task_id == task.id,
                                     BsSubject.stable_code == stable_code).first():
        count += 1
        stable_code = f"{prefix}{count + 1}"
    subject = BsSubject(task_id=task.id, stable_code=stable_code,
                        name=_clean_str(name), subject_type=subject_type,
                        type_confirmed=subject_type is not None,
                        type_locked=False, origin=origin, retired=False)
    db.add(subject)
    db.commit()
    db.refresh(subject)
    add_event(db, task.id, "subject.created", payload={
        "subject_id": subject.id, "stable_code": subject.stable_code,
        "subject_type": subject.subject_type, "operator": operator,
    })
    return subject


def rename_subject(db: Session, task: BsTask, subject_id: int, name: str, *, operator=None) -> BsSubject:
    """setup 阶段人工确认/修改主体名称，并把自动主体提升为人工维护主体。"""
    if not _setup_write_allowed(task):
        raise ValueError("核查已开始，不能修改分包主体名称")
    cleaned = _clean_str(name)
    if not cleaned:
        raise ValueError("分包单位名称不能为空")
    if len(cleaned) > 200:
        raise ValueError("分包单位名称不能超过 200 个字符")
    subject = (db.query(BsSubject)
               .filter(BsSubject.id == subject_id, BsSubject.task_id == task.id).first())
    if subject is None or subject.retired:
        raise LookupError("subject %s not in task %s" % (subject_id, task.id))
    normalized = a2_check.norm_company(cleaned)
    peers = (db.query(BsSubject)
             .filter(BsSubject.task_id == task.id, BsSubject.id != subject.id,
                     BsSubject.retired.is_(False)).all())
    if normalized and any(a2_check.norm_company(peer.name) == normalized
                          for peer in peers if peer.name):
        raise ValueError("该分包单位名称已用于其他主体，请先确认主体归属")
    old_name = subject.name
    subject.name = cleaned
    # origin=explicit 表示用户已经接管该主体的全局身份字段；不改变 stable_code。
    subject.origin = "explicit"
    db.commit()
    add_event(db, task.id, "subject.name_confirmed", payload={
        "subject_id": subject.id,
        "stable_code": subject.stable_code,
        "old_name": old_name,
        "name": cleaned,
        "operator": operator if operator is not None else "system",
    })
    return subject


def active_subject_ids(db: Session, task: BsTask) -> set[int]:
    """返回当前可用于配置/核查的主体。

    显式主体即使尚未绑定材料也必须保留；材料自动创建的临时主体只有仍被
    材料或报审批次引用时有效，删除最后一份材料后由退休逻辑隐藏。
    """
    return checklist.active_subject_ids(db, task)


def retire_orphan_auto_subjects(db: Session, task: BsTask) -> list[int]:
    """退休不再被任何材料/批次引用的自动主体，保留审计行不物理删除。"""
    referenced = active_subject_ids(db, task)
    candidates = (db.query(BsSubject)
                  .filter(BsSubject.task_id == task.id,
                          BsSubject.origin == "material_auto",
                          BsSubject.retired.is_(False)).all())
    retired_ids = []
    for subject in candidates:
        if subject.id not in referenced:
            subject.retired = True
            retired_ids.append(subject.id)
    return retired_ids


def is_active_subject(db: Session, task: BsTask, subject_id: int) -> bool:
    return subject_id in active_subject_ids(db, task)


def _reusable_subject_anchor(db: Session, task: BsTask,
                             subject_type: str) -> BsSubject | None:
    """回收本任务同类型的空名孤儿主体，作为新材料的上传锚点。

    只认「空名 + 未退休 + 当前没有任何材料引用」三条同时成立的主体：删除或重传
    留下的锚点正好满足。**正在承载材料的空名锚点不得复用**——两份不同分包单位的
    材料共用一个锚点后，先解析的一份会把单位名写进锚点，后一份在
    `assign_material_subject_by_exact_name` 里被判为同一主体而错误合并。
    """
    bound = {row[0] for row in db.query(BsMaterial.subject_id)
             .filter(BsMaterial.task_id == task.id,
                     BsMaterial.subject_id.isnot(None)).all()}
    anchors = (db.query(BsSubject)
               .filter(BsSubject.task_id == task.id,
                       BsSubject.subject_type == subject_type,
                       BsSubject.retired.is_(False),
                       or_(BsSubject.name.is_(None), BsSubject.name == ""))
               .order_by(BsSubject.id).all())
    for anchor in anchors:
        if anchor.id not in bound:
            return anchor
    return None


def assign_material_subject_by_exact_name(db: Session, task: BsTask,
                                          material: BsMaterial) -> BsSubject | None:
    """只按正文中唯一、明确的分包方单位名归并材料；不按文件名或模糊相似度猜测。"""
    if material.role not in ("sub", "labor", "packet"):
        return None
    if material.container_material_id is not None:
        return None

    # 上传阶段已建立的临时主体只用于材料归集。解析收口时补写正文识别出的
    # 单位名称，保持 stable_code 和报审资料绑定不变。
    existing = None
    if material.subject_id is not None:
        existing = db.query(BsSubject).filter(
            BsSubject.id == material.subject_id,
            BsSubject.task_id == task.id,
        ).first()
        if existing is None or existing.name:
            return existing

    candidate = extract.extract_subcontractor_name(material)
    if not candidate:
        return None

    with _SUBJECT_IDENTITY_LOCK:
        subjects = (db.query(BsSubject)
                    .filter(BsSubject.task_id == task.id)
                    .order_by(BsSubject.id).all())
        matches = [subject for subject in subjects
                   if subject.name and a2_check.norm_company(subject.name) == candidate]
        if len(matches) > 1:
            return None
        if existing is not None:
            subject = existing
            subject.name = candidate
            subject.subject_type = "professional" if material.role == "sub" else "labor"
            subject.type_confirmed = True
            subject.type_conflict = False
            subject.type_conflict_reason = None
            db.flush()
        elif matches:
            subject = matches[0]
        elif material.role in ("sub", "labor"):
            subject = create_subject(
                db, task,
                subject_type="professional" if material.role == "sub" else "labor",
                name=candidate,
                origin="material_auto",
            )
        else:
            return None

        if material.packet_id is not None:
            packet = db.query(BsPacket).filter(
                BsPacket.id == material.packet_id,
                BsPacket.task_id == task.id,
            ).first()
            if packet is not None and packet.subject_id != subject.id:
                return None

        material.subject_id = subject.id
        db.commit()
        add_event(db, task.id, "material.subject_assigned", payload={
            "material_id": material.id,
            "subject_id": subject.id,
            "packet_id": material.packet_id,
            "match": "exact_body_name",
        })
        return subject


def _subject_readable_material(material: BsMaterial):
    """主体判定只看可读文字，但保留原页数及原材料不变。"""
    pages = []
    for page in material.pages_json or []:
        if not isinstance(page, dict):
            continue
        no = page.get("page")
        readable = type(no) is int and no > 0 and parsing.material_page_readable(material, no)
        pages.append(page if readable else {**page, "text": ""})
    # 保留页数，避免多页资料包过滤后被误当作独立证照；不修改原始解析结果。
    return SimpleNamespace(id=material.id, file_name=material.file_name, role=material.role,
                           parse_state=material.parse_state, pages_json=pages,
                           degradations_json=getattr(material, "degradations_json", None))


def material_identified_subject(material: BsMaterial) -> str | None:
    """仅从可读页提取唯一主体；未读页和多候选均不作确定判断。"""
    usable = _subject_readable_material(material)
    names = set()
    detected = extract.extract_material_subject_name(usable)
    if detected:
        names.add(a2_check.norm_company(detected))
    names.update(a2_check.certificate_registered_companies(usable))
    for fact in personnel_evidence.extract_person_relation_evidence([usable]):
        names.update(a2_check.norm_company(value) for value in fact.get("employer_candidates", []) if value)
    names.discard("")
    return next(iter(names)) if len(names) == 1 else None


def material_subject_conflict(db: Session, task: BsTask,
                              material: BsMaterial) -> dict | None:
    """返回已选主体与材料正文主体不一致的可读冲突信息。

    上传时允许先选择主体，解析完成后再做正文校验。硬冲突保留解析完成状态，
    但阻止材料作为有效证据或替换激活；抽取不到唯一主体时返回 ``None``，继续走
    现有不可判定/人工确认路径。
    """
    if material.role not in ("sub", "labor", "packet") or material.subject_id is None:
        return None
    subject = db.query(BsSubject).filter(
        BsSubject.id == material.subject_id,
        BsSubject.task_id == task.id,
    ).first()
    if subject is None or not subject.name:
        return None
    detected = material_identified_subject(material)
    if not detected:
        return None
    expected_norm = a2_check.norm_company(subject.name)
    detected_norm = a2_check.norm_company(detected)
    if not expected_norm or expected_norm == detected_norm:
        return None
    return {
        "kind": "subject_conflict",
        "code": "material_subject_conflict",
        "message": "材料识别主体与当前选择主体不一致，材料未通过主体校验",
        "expected_subject_name": subject.name,
        "detected_subject_name": detected,
        "confidence": "high",
    }


def set_subject_type(db: Session, task: BsTask, subject_id: int,
                     subject_type: str, *, confirmed: bool = True,
                     operator=None) -> BsSubject:
    """人工确认/覆盖主体类型：**冲突的唯一解除入口**（契约 v3 §2.7）。

    解除范围仅限本主体当前冲突；同时把「当时已归属材料 id 集合」写进事件作为基线，
    供后续重算判断是否为「基线之外的新材料带来相反强证据」（§2.7 第 4 条）。
    """
    if not _setup_write_allowed(task):
        raise ValueError("核查已开始，主体类型不可变更")
    if subject_type not in SUBJECT_TYPES:
        raise ValueError("主体类型仅支持 professional / labor")
    subject = db.query(BsSubject).filter(BsSubject.id == subject_id,
                                         BsSubject.task_id == task.id).first()
    if subject is None or subject.retired:
        raise LookupError("subject %s not in task %s" % (subject_id, task.id))
    if subject.type_locked and subject.subject_type != subject_type:
        raise ValueError("主体类型已锁定，不能修改")
    # 冲突生命周期（契约 v3 §2.7）：人工确认即以人工为准——列值清零、原因清空，
    # 本次是否属于「覆盖AI识别」由事件 is_override 留痕。
    is_override = bool(subject.type_conflict)
    baseline_ids = _material_ids_of_subject(db, task, subject)
    subject.subject_type = subject_type
    subject.type_confirmed = bool(confirmed)
    subject.type_conflict = False
    subject.type_conflict_reason = None
    db.commit()
    add_event(db, task.id, "subject.type_confirmed", payload={
        "subject_id": subject.id, "stable_code": subject.stable_code,
        "subject_type": subject_type,
        "ai_subject_type": subject.ai_subject_type,
        "confirmed": bool(confirmed), "is_override": is_override,
        "type_conflict": False,
        # 基线（契约 v3 §2.7 第 4 条）：仅基线之外的新材料可重新置冲突
        "evidence_material_ids": baseline_ids,
        "operator": operator if operator is not None else "system",
    })
    return subject


def lock_subject_type(db: Session, task: BsTask, subject_id: int, *,
                      lock_source: str = "manual", operator=None) -> BsSubject:
    """锁定主体类型。lock_source：manual=用户在 setup 段显式提前锁定；
    start=start_review 事务内批量最终锁定（不能被本函数替代，T1-v2 §三）。"""
    if lock_source not in ("manual", "start"):
        raise ValueError("lock_source 仅支持 manual / start")
    subject = db.query(BsSubject).filter(BsSubject.id == subject_id,
                                         BsSubject.task_id == task.id).first()
    if subject is None or subject.retired:
        raise LookupError("subject %s not in task %s" % (subject_id, task.id))
    if subject.subject_type not in SUBJECT_TYPES or not subject.type_confirmed:
        raise ValueError("主体类型必须先人工确认")
    subject.type_locked = True
    db.commit()
    add_event(db, task.id, "subject.type_locked", payload={
        "subject_id": subject.id, "stable_code": subject.stable_code,
        "lock_source": lock_source, "operator": operator,
    })
    return subject


# ==================== 主体类型强证据识别（T3.2；契约 v3 §2.6/§2.7） ====================
#
# 规则来源（前置背景，2026-09-29 实测读取，契约 v3 文首登记）：
#   《2025年分包单位报审以及附件要求（6.20）》第 3/4 页——「专业分包报审表」/
#     「劳务分包报审表」标题及正文「我公司申请将下列工程进行专业分包 / 进行劳务分包」；
#   《20260913 区块链监管智能体功能需求清单》§A2-04「专业承包资质等级」。
# 纪律：
#   ① 判定依据是材料**可读取正文**（原生文本层 / 扫描件文字识别结果 = pages_json）；
#      **文件名只作材料归类线索，不参与类型判定**（T3.2 §2.1 纠正）；
#   ② 零 LLM：确定性词表匹配，本模块不引任何模型；
#   ③ 页内两类证据并列（模板未勾选）→ 该页不构成证据；**不投票、不按文件数量定夺**；
#   ④ 无强证据 → ai_subject_type=NULL（≠缺件、≠命中/未命中）；
#   ⑤ 词表属契约（附录 v3 §2.6.3）：改词表须先改契约再改代码。
_SUBJECT_TYPE_EVIDENCE = {
    "professional": ("专业分包报审表", "进行专业分包", "专业分包合同",
                     "专业承包资质", "专业承包"),
    "labor": ("劳务分包报审表", "进行劳务分包", "劳务分包合同", "劳务合作合同",
              "施工劳务企业资质", "劳务分包不分级", "劳务分包不分等级", "劳务分包资质"),
}

# 每份材料的页数预算（契约 v3 §2.6.2）：默认前 3 页，未得唯一结论续读至多前 10 页
SUBJECT_TYPE_PAGE_SOFT_LIMIT = 3
SUBJECT_TYPE_PAGE_HARD_LIMIT = 10
# 类型识别专用 OCR 分段（T3.2-R1 §2.1）：每段都是一次**有界**请求，两段页码并集
# 不超过原始第 SUBJECT_TYPE_PAGE_HARD_LIMIT 页。第一段（默认预算）已得唯一强证据
# 就不再请求第二段——识别侧 OCR 请求永不触及第 11 页及以后。
_TYPE_OCR_BATCHES = (
    tuple(range(1, SUBJECT_TYPE_PAGE_SOFT_LIMIT + 1)),                     # 原始 1..3
    tuple(range(SUBJECT_TYPE_PAGE_SOFT_LIMIT + 1,
                SUBJECT_TYPE_PAGE_HARD_LIMIT + 1)),                        # 原始 4..10
)
# 证据摘要与冲突原因里引用的证据条数上限（控长；type_conflict_reason 上限 200 字符）
_EVIDENCE_QUOTE_MAX = 30
_EVIDENCE_LIST_MAX = 5

_WS_RE = re.compile(r"\s+")

# 主体类型 → 展示名（冲突原因与事件文案用，平实中文）
_SUBJECT_TYPE_LABELS = {"professional": "专业分包", "labor": "劳务分包"}


def _norm_page_text(text) -> str:
    """页文本去空白归一化（与 a1_check._norm_pages 同口径）：词表按连续中文命中。"""
    return _WS_RE.sub("", text or "")


def _evidence_quote(text: str, phrases: tuple) -> str | None:
    """命中短语附近的短摘要（≤30 字符）——只留短语窗口，不落整页原文（§2.5 红线）。"""
    for kw in phrases:
        i = text.find(kw)
        if i >= 0:
            return text[i:i + _EVIDENCE_QUOTE_MAX]
    return None


def _type_scope_pages(pages_json) -> tuple:
    """按**原始 PDF 页码**取类型识别可检查的页（T3.2-R1 §2.2）。

    - 只保留 `1 <= page <= SUBJECT_TYPE_PAGE_HARD_LIMIT` 的记录，按页码升序；
    - 页码缺失/非法的记录**保守跳过**（绝不按记录序号顶替成页码，否则第 11 页
      会被前移当作第 10 页），并记入 `page_issues` 供人工判读——不静默吞掉；
    - 越界页（page > 10）既不参与判定也不记为异常：属性识别边界之外的正常内容。

    返回 ([(原始页码, 去空白归一化文本)], page_issues)
    """
    scoped, issues = [], []
    for idx, item in enumerate(pages_json or []):
        try:
            page_no = int((item or {}).get("page"))
        except (TypeError, ValueError):
            issues.append({"record": idx + 1,
                           "reason": "页码缺失或非法，保守跳过（不位移到其它页）"})
            continue
        if page_no < 1:
            issues.append({"record": idx + 1, "reason": "页码非正数，保守跳过"})
            continue
        if page_no > SUBJECT_TYPE_PAGE_HARD_LIMIT:
            continue
        scoped.append((page_no, _norm_page_text((item or {}).get("text"))))
    scoped.sort(key=lambda entry: entry[0])
    return scoped, issues


def _scan_pages_once(pages: list) -> dict:
    """页序扫描（pages 已按原始页码升序）：某页出现**单一类型**强证据即返回并停止。

    页内两类证据并存 → 该页不构成证据（继续读下一页），不投票。
    返回 {"kind", "page", "quote", "read"}；`read` = **已检查到的原始页码上界**
    （命中页本身；未命中时为最后一页的原始页码）——不得用记录条数冒充（§2.2）。
    """
    for page_no, text in pages:
        kinds = [k for k, phrases in _SUBJECT_TYPE_EVIDENCE.items()
                 if any(p in text for p in phrases)]
        if len(kinds) == 1:
            kind = kinds[0]
            return {"kind": kind, "page": page_no, "read": page_no,
                    "quote": _evidence_quote(text, _SUBJECT_TYPE_EVIDENCE[kind])}
    return {"kind": None, "page": None, "quote": None,
            "read": (pages[-1][0] if pages else 0)}


def scan_material_type_evidence(material, *, pages=None) -> dict:
    """单份材料的类型强证据扫描（确定性、纯函数：不写库、不调 OCR、不调 LLM）。

    `pages`：可选，调用方按**类型识别 OCR 边界**取回的页集（形如 `pages_json` 的
    `[{"page","text"}]`）；None 时读 `material.pages_json`。两种来源都只处理**原始
    页码 ≤ 10** 的记录（T3.2-R1 §2.2）——第 11 页及以后的内容永不参与判定。

    页数边界（契约 v3 §2.6.2 + T3.2-R1 §2.2）：先按默认预算读**原始前 3 页**；
    未得唯一明确类型再续读**原始第 4..10 页**；页内出现唯一强证据即停止读取该材料。

    返回 {"kind": 类型|None, "page": 原始文件页码|None, "quote": 短摘要|None,
          "pages_read": 已检查到的原始页码上界, "total_pages": 已落库页记录数,
          "page_issues": 页码异常记录（可读降级原因）}
    """
    source = material.pages_json if pages is None else pages
    scoped, issues = _type_scope_pages(source)
    soft = [entry for entry in scoped if entry[0] <= SUBJECT_TYPE_PAGE_SOFT_LIMIT]
    result = _scan_pages_once(soft)                     # 默认预算：原始前 3 页
    if result["kind"] is None and len(scoped) > len(soft):
        result = _scan_pages_once(scoped)               # 未得唯一明确类型 → 续读第 4..10 页
    return {"kind": result["kind"], "page": result["page"], "quote": result["quote"],
            "pages_read": result["read"], "total_pages": len(source or []),
            "page_issues": issues}


def _material_text_from_ocr(material) -> bool:
    """材料正文是否来自文字识别（扫描件）：收口降级记录带 `ocr_fallback` 标记。"""
    return any((d or {}).get("kind") == "ocr_fallback"
               for d in (material.degradations_json or []))


def _ocr_type_pages(abs_path: str) -> list:
    """主体类型识别专用**有界** OCR（T3.2-R1 §2.1）：分两段请求，命中即停。

    - 第一段：原始第 1..3 页（默认预算）；该段已得唯一强证据时**不再请求第二段**；
    - 第二段：原始第 4..10 页（仅在第一段无结论时请求）；
    - 两段页码并集 ≤ 原始第 10 页：识别侧 OCR 请求**永不触及第 11 页及以后**，
      也不先生成 30 页再在判定层截断。
    返回页集（形如 pages_json；页码为原始 1-based）；识别侧 OCR 不可用时返回 []。
    """
    pages = []
    for batch in _TYPE_OCR_BATCHES:
        got, _info = parsing.ocr_text_pages(abs_path, page_nos=list(batch))
        pages.extend(got or [])
        scoped, _issues = _type_scope_pages(pages)
        if _scan_pages_once(scoped)["kind"] is not None:
            break                       # 已得唯一强证据：不再请求后续页
    return pages


def _subject_type_pages(db: Session, task: BsTask, subject: BsSubject,
                        *, material_ids=None) -> dict:
    """主体下「正文来自文字识别」材料的类型识别有界页集（T3.2-R1 §2.1）。

    返回 {material_id: 页集}，供 `refresh_subject_ai_type` 消费——使识别在任何
    触发路径下都**只读有界 OCR 产出**（原始第 1..10 页），不消费正式核查的
    30 页 OCR 结果。文本层材料不在此列（识别直接读其已落库正文）。

    识别只在 setup 段运行（§2.4），非 setup 段直接返回空表、不发起 OCR。
    """
    if not _subject_scan_allowed(task):
        return {}
    selected_ids = set(material_ids) if material_ids is not None else None
    out = {}
    for mat in _subject_materials(db, task, subject):
        if selected_ids is not None and mat.id not in selected_ids:
            continue
        if not (mat.path and _material_text_from_ocr(mat)):
            continue
        abs_path = os.path.abspath(os.path.join(
            os.path.dirname(parsing.__file__), "..", "..", mat.path))
        pages = _ocr_type_pages(abs_path)
        if not pages:
            logger.warning("[bs] 类型识别有界 OCR 无产出，该材料本次不贡献类型证据 "
                           "material=%s", mat.id)
        out[mat.id] = pages
    return out


def _refresh_subject_ai_type_background(task_id: int, subject_id: int,
                                        material_id: int, bind, operator=None) -> None:
    """只为本次已归属扫描件异步补做类型 OCR；不阻塞归属请求、不重扫旧扫描件。"""
    session = SessionLocal(bind=bind)
    try:
        task = session.get(BsTask, task_id)
        subject = session.query(BsSubject).filter(
            BsSubject.id == subject_id, BsSubject.task_id == task_id).first()
        material = session.query(BsMaterial).filter(
            BsMaterial.id == material_id, BsMaterial.task_id == task_id).first()
        if (task is None or not _subject_scan_allowed(task) or subject is None
                or material is None or material.subject_id != subject_id):
            return
        pages = _subject_type_pages(
            session, task, subject, material_ids={material_id})
        refresh_subject_ai_type(session, task, subject, operator=operator,
                                pages_by_material=pages)
    except Exception as e:  # 识别失败不得影响已完成的材料归属
        logger.warning("[bs] 主体类型后台识别跳过 task=%s subject=%s material=%s: %s",
                       task_id, subject_id, material_id, e)
    finally:
        _mark_subject_type_scan_pending(task_id, subject_id, -1)
        session.close()


def _subject_materials(db: Session, task: BsTask, subject: BsSubject) -> list:
    rows = (db.query(BsMaterial)
            .filter(BsMaterial.task_id == task.id, BsMaterial.subject_id == subject.id)
            .order_by(BsMaterial.id).all())
    # 主体不匹配的材料不能成为该主体的类型识别证据。
    return [material for material in rows if not _subject_assessment_issue(material)]


def _material_ids_of_subject(db: Session, task: BsTask, subject: BsSubject) -> list:
    """主体当前已归属材料 id（人工确认时记基线；契约 v3 §2.7 第 4 条）。"""
    rows = (db.query(BsMaterial.id)
            .filter(BsMaterial.task_id == task.id, BsMaterial.subject_id == subject.id)
            .order_by(BsMaterial.id).all())
    return sorted(r[0] for r in rows)


def _type_confirm_baseline(db: Session, task_id: int, subject_id: int):
    """最近一次人工确认事件记录的材料基线；None = 从未走过确认 API（视为空基线）。

    基线用途：人工覆盖后，基线内材料的旧证据不得在重算时反复置冲突，否则人工
    覆盖将失去意义（契约 v3 §2.7 第 4 条）。
    """
    events = (db.query(BsTaskEvent)
              .filter(BsTaskEvent.task_id == task_id,
                      BsTaskEvent.event_type == "subject.type_confirmed")
              .order_by(BsTaskEvent.sequence).all())
    for ev in reversed(events):
        payload = ev.payload_json or {}
        if payload.get("subject_id") == subject_id:
            return set(payload.get("evidence_material_ids") or [])
    return None


def scan_subject_type_evidence(db: Session, task: BsTask, subject: BsSubject,
                               *, pages_by_material=None) -> dict:
    """汇总主体下全部已归属材料的强证据（只读；不写库、不改判定）。

    `pages_by_material`：`{material_id: 页集}`——按**类型识别 OCR 边界**（原始第
    1..10 页）取回的正文，仅扫描件需要（见 `_ocr_type_pages`）；未提供的材料读其
    `pages_json`（同样按原始页码 ≤10 截取）。识别侧不消费任何超出第 10 页的正文。
    """
    override = pages_by_material or {}
    materials = _subject_materials(db, task, subject)
    hits, issues = [], []
    for mat in materials:
        res = scan_material_type_evidence(mat, pages=override.get(mat.id))
        if res["page_issues"]:
            issues.append({"material_id": mat.id, "page_issues": res["page_issues"]})
        if res["kind"] is None:
            continue
        hits.append({"material_id": mat.id, "file_name": mat.file_name,
                     "page": res["page"], "kind": res["kind"],
                     "quote": res["quote"], "pages_read": res["pages_read"]})
    return {"scanned_materials": len(materials), "hits": hits, "page_issues": issues}


def _evidence_hint(hits: list) -> str:
    """证据指向的可读摘要：『《材料.pdf》第2页』（最多 2 条，控长）。"""
    parts = []
    for h in hits[:2]:
        page = ("第%s页" % h["page"]) if h.get("page") else ""
        parts.append("《%s》%s" % (h.get("file_name") or "材料", page))
    return "、".join(parts)


def _conflict_reason(ai, manual, conflict_hits: list, self_conflict: bool) -> str:
    # 只陈述证据事实：类型以用户所选资料类别为准（方案A），原因里不再要求人工改类型。
    if self_conflict:
        return ("材料中的类型证据互相矛盾（同时出现专业分包与劳务分包的证据：%s），"
                "无法自动判定类型" % _evidence_hint(conflict_hits))
    return ("材料中的类型证据指向%s（%s），与所选%s不一致"
            % (_SUBJECT_TYPE_LABELS.get(ai, ai), _evidence_hint(conflict_hits),
               _SUBJECT_TYPE_LABELS.get(manual, manual)))


def refresh_subject_ai_type(db: Session, task: BsTask, subject: BsSubject,
                            *, operator=None, pages_by_material=None) -> BsSubject:
    """主体类型识别（契约 v3 §2.6/§2.7）：确定性规则读材料正文强证据。

    - 证据唯一 → `ai_subject_type` = 该类型；与人工值不一致 → 置冲突；
    - 两类证据并存（强证据互相矛盾）→ `ai_subject_type=NULL` + 置冲突（**不投票**）；
    - 无证据 → NULL（≠缺件），且**不动**既有冲突；
    - **自动重算一律不得清除冲突**：唯一解除入口是人工确认（set_subject_type）；
    - 仅「基线之外的新材料」携带相反强证据才重新置冲突（§2.7 第 4 条）；
    - 非 setup 段直接返回：启动后快照已固化、类型已锁定，重算不得扰动（§2.4）；
    - `pages_by_material`：扫描件的**类型识别有界 OCR** 页集（原始页码 ≤10），
      由调用方按 T3.2-R1 §2.1 取回；识别自身不发起 OCR 请求。
    """
    if not _subject_scan_allowed(task):
        return subject
    scan = scan_subject_type_evidence(db, task, subject,
                                      pages_by_material=pages_by_material)
    hits = scan["hits"]
    professional_hits = [h for h in hits if h["kind"] == "professional"]
    labor_hits = [h for h in hits if h["kind"] == "labor"]
    self_conflict = bool(professional_hits and labor_hits)
    if self_conflict:
        ai, conflict_hits = None, hits
    elif labor_hits:
        ai, conflict_hits = "labor", labor_hits
    elif professional_hits:
        ai, conflict_hits = "professional", professional_hits
    else:
        ai, conflict_hits = None, []
    manual = subject.subject_type
    baseline = _type_confirm_baseline(db, task.id, subject.id) or set()
    fresh_hits = [h for h in conflict_hits if h["material_id"] not in baseline]
    conflict_now = bool(fresh_hits) and (
        self_conflict or bool(ai and manual and ai != manual))
    ai_changed = ai != subject.ai_subject_type
    conflict_raised = conflict_now and not bool(subject.type_conflict)
    if not ai_changed and not conflict_raised:
        return subject  # 无变化且无新冲突：不写列、不发事件（防重算抖动刷屏）
    subject.ai_subject_type = ai
    if conflict_now:
        subject.type_conflict = True
        subject.type_conflict_reason = _conflict_reason(
            ai, manual, conflict_hits, self_conflict)[:200]
    payload_operator = operator if operator is not None else "system"
    if ai_changed:
        add_event(db, task.id, "subject.ai_type_detected", payload={
            "subject_id": subject.id, "stable_code": subject.stable_code,
            "ai_subject_type": ai,
            "evidence": conflict_hits[:_EVIDENCE_LIST_MAX],
            "scanned_materials": scan["scanned_materials"],
            "page_issues": scan["page_issues"],
            "type_conflict": bool(subject.type_conflict),
            "operator": payload_operator,
        })
    if conflict_raised:
        add_event(db, task.id, "subject.type_conflict_detected", payload={
            "subject_id": subject.id, "stable_code": subject.stable_code,
            "ai_subject_type": ai, "subject_type": manual,
            "type_conflict_reason": subject.type_conflict_reason,
            "evidence": conflict_hits[:_EVIDENCE_LIST_MAX],
            "scanned_materials": scan["scanned_materials"],
            "page_issues": scan["page_issues"],
            "operator": payload_operator,
        })
    return subject


def create_packet(db: Session, task: BsTask, *, subject_id: int,
                  batch_name: str = None, operator=None) -> BsPacket:
    """为既有主体创建报审资料批次；不读取文件内容、不输出核查结论。"""
    if not _setup_write_allowed(task):
        raise ValueError("核查已开始，不能新增报审资料批次")
    subject = db.query(BsSubject).filter(BsSubject.id == subject_id,
                                         BsSubject.task_id == task.id).first()
    if subject is None:
        raise LookupError("subject %s not in task %s" % (subject_id, task.id))
    packet = BsPacket(task_id=task.id, subject_id=subject.id,
                      batch_name=_clean_str(batch_name))
    db.add(packet)
    db.commit()
    db.refresh(packet)
    add_event(db, task.id, "packet.created", payload={
        "packet_id": packet.id, "subject_id": subject.id,
        "batch_name": packet.batch_name, "operator": operator,
    })
    return packet


def assign_material_subject(db: Session, task: BsTask, material_id: int,
                            subject_id: int, packet_id: int = None,
                            operator=None) -> BsMaterial:
    """上传后归属主体；已解析材料复用正文重新核对主体。"""
    if not _setup_write_allowed(task):
        raise ValueError("核查已开始，不能再改派材料")
    mat = db.query(BsMaterial).filter(BsMaterial.id == material_id,
                                      BsMaterial.task_id == task.id).first()
    subject = db.query(BsSubject).filter(BsSubject.id == subject_id,
                                         BsSubject.task_id == task.id).first()
    if mat is None or subject is None or subject.retired:
        raise LookupError("material or subject not in task")
    packet = None
    if packet_id is not None:
        packet = db.query(BsPacket).filter(BsPacket.id == packet_id,
                                           BsPacket.task_id == task.id,
                                           BsPacket.subject_id == subject.id).first()
        if packet is None:
            raise LookupError("packet not in subject")
    try:
        mat.subject_id = subject.id
        mat.packet_id = packet.id if packet else None
        if mat.parse_state == "done":
            detected = material_identified_subject(mat)
            expected = a2_check.norm_company(subject.name or "")
            if detected and expected:
                existing = [d for d in (mat.degradations_json or [])
                            if not isinstance(d, dict) or d.get("kind") not in {"subject_conflict", "subject_unresolved"}]
                conflict = material_subject_conflict(db, task, mat)
                if conflict:
                    existing.append(conflict)
                    add_event(db, task.id, "material.subject_conflict", payload={
                        "material_id": mat.id, "file_name": mat.file_name,
                        **conflict,
                    }, commit=False)
                mat.degradations_json = existing
        db.commit()
        add_event(db, task.id, "material.subject_assigned", payload={
            "material_id": mat.id, "subject_id": subject.id,
            "packet_id": packet.id if packet else None, "operator": operator,
        })
    except Exception:
        raise
    return mat


def subject_materials(db: Session, task: BsTask, subject_id: int) -> list:
    """返回主体全部材料（合同/档案、报审批次与补传），供核查阶段聚合取证。"""
    subject = db.query(BsSubject).filter(BsSubject.id == subject_id,
                                         BsSubject.task_id == task.id).first()
    if subject is None:
        raise LookupError("subject %s not in task %s" % (subject_id, task.id))
    return (db.query(BsMaterial).filter(BsMaterial.task_id == task.id,
                                        BsMaterial.subject_id == subject.id)
            .order_by(BsMaterial.id).all())


def add_material(db: Session, task: BsTask, *, file_name: str, size_kb: int = 0,
                 via: str = "upload", role: str = None, subject_id: int = None,
                 packet_id: int = None, material_category: str = None) -> BsMaterial:
    """新增材料：自动归类只是初始值（role=None 时按文件名归类，人工指定则标记 confirmed）。"""
    # T10-B §B1：既有路径缺状态门（router 层有、service 直调没有），准备期必须一并拒绝，
    # 否则 `preparing` 只是「界面按不住」的假门禁（调用方仍可从服务层插材料）。
    if not _material_write_allowed(task):
        raise ValueError("报审资料包正在准备中，暂时不能修改材料；请等待准备完成")
    manual = role is not None
    mat = BsMaterial(
        task_id=task.id, file_name=file_name,
        subject_id=subject_id, packet_id=packet_id,
        material_category=material_category,
        role=role or classify_material(file_name),
        role_confirmed=manual, via=via, size_kb=size_kb,
    )
    db.add(mat)
    db.commit()
    db.refresh(mat)
    add_event(db, task.id, "material.added",
              payload={"file_name": file_name, "role": mat.role, "via": via})
    return mat


def _material_in_replacement_history(db: Session, task: BsTask, material: BsMaterial) -> bool:
    """Return whether a material or its ZIP parent is an immutable old version."""
    if material.superseded_by_id is not None:
        return True
    if material.container_material_id is None:
        return False
    parent = (db.query(BsMaterial)
              .filter(BsMaterial.id == material.container_material_id,
                      BsMaterial.task_id == task.id)
              .first())
    return parent is not None and parent.superseded_by_id is not None


def set_material_role(db: Session, task: BsTask, material_id: int, role: str) -> BsMaterial:
    """人工更正归属（v6 原型的「归属」片：只记人改动项，不要求逐份确认）。"""
    # T10-B §B1：service 与 router **两侧都缺**状态门（原实现只靠 UI 不给入口）。
    if not _material_write_allowed(task):
        raise ValueError("报审资料包正在准备中，暂时不能更正材料归属；请等待准备完成")
    mat = db.query(BsMaterial).filter(BsMaterial.id == material_id, BsMaterial.task_id == task.id).first()
    if mat is None:
        raise LookupError("material %s not in task %s" % (material_id, task.id))
    if _material_in_replacement_history(db, task, mat):
        raise ValueError("材料已进入替代历史，为保留审计记录不能修改")
    if mat.replacement_target_id is not None:
        raise ValueError("材料替代意图已登记，不能修改归类，请取消后重新上传")
    mat.role = role
    mat.role_confirmed = True
    mat.material_category = role if role != "unknown" else None
    if role not in ("sub", "labor", "packet"):
        mat.subject_id = None
        mat.packet_id = None
    elif role in ("sub", "labor") and mat.subject_id is not None:
        subject = db.query(BsSubject).filter(
            BsSubject.id == mat.subject_id, BsSubject.task_id == task.id
        ).first()
        if subject is not None:
            subject.subject_type = "professional" if role == "sub" else "labor"
            subject.type_confirmed = True
            subject.type_conflict = False
            subject.type_conflict_reason = None
    if role in ("sub", "labor") and mat.subject_id is None:
        # 类型改正发生在正文解析前时，也必须立即建立临时主体，
        # 否则材料计数已变为分包但启动闸仍会认为没有分包主体。
        if mat.parse_state == "done":
            assign_material_subject_by_exact_name(db, task, mat)
        if mat.subject_id is None:
            with _SUBJECT_IDENTITY_LOCK:
                subject = create_subject(
                    db, task,
                    subject_type="professional" if role == "sub" else "labor",
                    name=None,
                    origin="material_auto",
                )
                mat.subject_id = subject.id
    db.commit()
    retire_orphan_auto_subjects(db, task)
    db.commit()
    add_event(db, task.id, "material.role_corrected",
              payload={"material_id": mat.id, "role": role,
                       "subject_id": mat.subject_id, "packet_id": mat.packet_id})
    return mat


def material_composition(materials: list) -> dict:
    counts = Counter(m.role for m in materials)
    return {ROLE_LABELS[r]: counts.get(r, 0) for r in
            ("general", "sub", "labor", "packet", "unknown")}


def set_scope(db: Session, task: BsTask, *, scope_a1: bool, scope_a3: bool) -> BsTask:
    """setup 段改核查范围（v6 两段式：选项在开始核查前可调，start 时随快照固化）。"""
    if not _setup_write_allowed(task):
        raise ValueError("核查已开始，范围不可变更")
    if not scope_a1 and not scope_a3:
        raise ValueError("至少选择一个核查范围（A1 违法转包分包识别 / A3 报审资料合规核查）")
    task.scope_a1 = scope_a1
    task.scope_a3 = scope_a3
    db.commit()
    db.refresh(task)
    return task


def set_section(db: Session, task: BsTask, *, project_name: str = None,
                contract_section: str = None, baseline_date=None,
                jurisdiction: str = None, profile_code: str = None,
                baseline_date_changed: bool = False) -> BsTask:
    """setup 段改标段锚点（A-03）：复用 set_scope 的状态门——开始核查后 409。

    五字段全部可选（缺省不改动该项语义由 router 用 `model_fields_set` 承载）：
    本函数按传入值覆盖（None 表示置空），与 set_scope 的「全量覆盖」写法一致；
    历史任务无此概念，故一律 nullable、不回填。
    """
    if not _setup_write_allowed(task):
        raise ValueError("核查已开始，标段信息不可变更")
    task.project_name = _clean_str(project_name)
    task.contract_section = _clean_str(contract_section)
    task.baseline_date = _coerce_date(baseline_date)
    if baseline_date_changed:
        task.options_snapshot_json = {**(task.options_snapshot_json or {}),
                                      "baseline_date_source": "manual" if task.baseline_date else "automatic"}
    task.jurisdiction = _coerce_jurisdiction(jurisdiction)
    task.profile_code = _coerce_profile_code(profile_code)
    db.commit()
    db.refresh(task)
    return task


def delete_material(db: Session, task: BsTask, material_id: int) -> None:
    """setup 段移除材料（传错文件重传；落盘文件保留，仅删记录避免误伤同路径复用）。

    T4.1-T2 §2.3 / 契约 §7.4.7：删除 **ZIP 容器**时先**显式**删除其全部派生材料
    记录，再删容器本身；删除单个派生子材料只删它自己——既不误删父容器，也不动同
    容器的其他兄弟（子材料按 `container_material_id` 精确定位）。落盘文件沿用项目
    「删除记录不贸然删可能共享路径」的既有策略，一律保留。

    显式删除而非依赖库级 CASCADE：与 `delete_task` 同纪律（sqlite 需
    PRAGMA foreign_keys=ON 才生效，显式删使 sqlite/MySQL 行为必然一致）。
    """
    if not _setup_write_allowed(task):
        raise ValueError("核查已开始，不能再移除材料")
    mat = db.query(BsMaterial).filter(BsMaterial.id == material_id, BsMaterial.task_id == task.id).first()
    if mat is None:
        raise LookupError("material %s not in task %s" % (material_id, task.id))
    children = (db.query(BsMaterial)
                .filter(BsMaterial.task_id == task.id,
                        BsMaterial.container_material_id == mat.id)
                .all())
    # D9/M8：替代链是审计事实，删除任一出链/入链节点都会制造悬空指针或抹掉
    # 历史。删除 ZIP 容器时也要把即将级联删除的派生子材料一起检查。
    delete_candidates = [mat, *children]
    candidate_ids = [item.id for item in delete_candidates]
    has_outbound_edge = any(item.superseded_by_id is not None
                            for item in delete_candidates)
    parent_is_historical = _material_in_replacement_history(db, task, mat)
    has_inbound_edge = (db.query(BsMaterial.id)
                        .filter(BsMaterial.task_id == task.id,
                                BsMaterial.superseded_by_id.in_(candidate_ids))
                        .first() is not None)
    if has_outbound_edge or has_inbound_edge or parent_is_historical:
        raise ValueError("材料已进入替代历史，为保留审计记录不能删除")
    for child in children:
        db.delete(child)
    db.delete(mat)
    # 先提交材料删除，再按剩余引用判断自动主体是否应退休。
    db.commit()
    retire_orphan_auto_subjects(db, task)
    db.commit()


def _has_subject_conflict(material: BsMaterial) -> bool:
    return any(isinstance(item, dict) and item.get("kind") == "subject_conflict"
               for item in (material.degradations_json or []))


def _subject_assessment_issue(material: BsMaterial) -> str | None:
    return material_versions.subject_assessment_issue(material)


def _certificate_supplement_needs_subject(db: Session, task: BsTask, material: BsMaterial) -> bool:
    plan = get_supplement_generation_plan(task) or {}
    if not set(plan.get("affected_item_ids") or []).intersection({"RC-A02", "RC-A03", "RC-A04"}):
        return False
    linked_id = material.id
    if material.container_material_id is not None:
        # ZIP 可包含合同、方案等其他材料，只有证照子文件参与证照主体核验。
        text = " ".join(str(page.get("text") or "") for page in (material.pages_json or [])
                        if isinstance(page, dict))
        if not any(word in (material.file_name or "") or word in text
                   for word in ("营业执照", "资质证书", "安全生产许可证")):
            return False
        linked_id = material.container_material_id
    events = (db.query(BsTaskEvent).filter_by(task_id=task.id, event_type="material.supplement_linked").all())
    return any((event.payload_json or {}).get("generation_id") == plan.get("generation_id")
               and (event.payload_json or {}).get("material_id") == linked_id for event in events)


def _social_supplement_needs_employer(db: Session, task: BsTask, material: BsMaterial) -> bool:
    plan = get_supplement_generation_plan(task) or {}
    if (plan.get("impact") or {}).get("category") != "personnel_social":
        return False
    if not personnel_evidence.social_evidence_materials([material]):
        return False
    linked_id = material.container_material_id or material.id
    events = db.query(BsTaskEvent).filter_by(task_id=task.id, event_type="material.supplement_linked").all()
    return any((event.payload_json or {}).get("generation_id") == plan.get("generation_id")
               and (event.payload_json or {}).get("material_id") == linked_id for event in events)


def _labor_supplement_needs_employer(db: Session, task: BsTask, material: BsMaterial) -> bool:
    plan = get_supplement_generation_plan(task) or {}
    if (plan.get("impact") or {}).get("category") != "personnel_social":
        return False
    if "labor_contract" not in personnel_evidence.relation_evidence_kinds(material):
        return False
    linked_id = material.container_material_id or material.id
    events = db.query(BsTaskEvent).filter_by(task_id=task.id, event_type="material.supplement_linked").all()
    return any((event.payload_json or {}).get("generation_id") == plan.get("generation_id")
               and (event.payload_json or {}).get("material_id") == linked_id for event in events)


def _personnel_certificate_needs_employer(db: Session, task: BsTask, material: BsMaterial) -> bool:
    plan = get_supplement_generation_plan(task) or {}
    if (plan.get("impact") or {}).get("category") != "personnel_social":
        return False
    text = " ".join(str(page.get("text") or "") for page in (material.pages_json or []) if isinstance(page, dict))
    if not any(word in (material.file_name or "") or word in text for word in ("建造师", "执业证书", "岗位证书")):
        return False
    linked_id = material.container_material_id or material.id
    events = db.query(BsTaskEvent).filter_by(task_id=task.id, event_type="material.supplement_linked").all()
    return any((event.payload_json or {}).get("generation_id") == plan.get("generation_id")
               and (event.payload_json or {}).get("material_id") == linked_id for event in events)


def _rule_materials(db: Session, task: BsTask) -> list:
    """规则引擎可用材料集合（契约 §7.4.6：父容器/旧版/待生效候选不作证据）。

    为什么必须在 service 边界剔除：容器准备完成会转 `parse_state='done'`
    （§7.2.4 冻结取值），而各规则内核的材料筛选口径是「role + parse_state」
    （如 A3 的 `role=='packet' and parse_state=='done'`）——容器 `role` 继承用户
    选择（报审包场景下正是 `packet`），不排除就会让 **ZIP 本体冒充一份报审资料包**
    进入对表与取证，直接把「包内文件读不出」洗成「资料已齐」。

    剔除放在编排层而非规则内核：内核按任务书 §五 属只读复用，不得为 ZIP 加特例；
    编排层本来就负责「给内核喂什么材料」。派生**子材料**不在容器剔除集内（它们才是
    真正的证据候选）；已替代旧版和带 `replacement_target_id` 的候选也在此处排除，
    防止新旧版本或尚未生效的候选同时进入规则。
    """
    materials = db.query(BsMaterial).filter(BsMaterial.task_id == task.id).all()
    containers = task_zip_containers(db, task)
    container_ids = {m.id for m in containers}
    # ZIP 子材料继承父容器的版本事实。替代旧容器或仍在等待生效的
    # 新容器时，旧/候选容器下的子材料也不能继续进入新一轮规则输入；
    # 否则只排除容器行会让旧 ZIP 内容绕过替代链重新参与核查。
    inactive_container_ids = {
        m.id for m in containers
        if m.superseded_by_id is not None or m.replacement_target_id is not None
    }
    return [m for m in materials
            if m.id not in container_ids
            and not _subject_assessment_issue(m)
            and m.container_material_id not in inactive_container_ids
            and m.superseded_by_id is None
            and m.replacement_target_id is None]


def _supplement_preparation_materials(db: Session, task: BsTask) -> list:
    """补件需准备的普通材料与待替换候选；容器本体仍走 ZIP 准备流程。"""
    containers = task_zip_containers(db, task)
    container_ids = {m.id for m in containers}
    candidate_container_ids = {m.id for m in containers if m.replacement_target_id is not None}
    candidates = (db.query(BsMaterial)
                  .filter(BsMaterial.task_id == task.id, BsMaterial.superseded_by_id.is_(None))
                  .all())
    plan = get_supplement_generation_plan(task) or {}
    current_generation = plan.get("generation_id")
    planned_materials = {str(value) for value in (plan.get("material_ids") or [])}
    linked_generations = {}
    for event in db.query(BsTaskEvent).filter_by(task_id=task.id, event_type="material.supplement_linked").all():
        payload = event.payload_json or {}
        if payload.get("material_id") is not None and payload.get("generation_id"):
            linked_generations.setdefault(payload["material_id"], set()).add(payload["generation_id"])
    rows = _rule_materials(db, task)
    seen = {m.id for m in rows}
    for material in candidates:
        known_generations = linked_generations.get(material.container_material_id or material.id, set())
        prior_unusable = (_subject_assessment_issue(material) and current_generation
                          and known_generations and current_generation not in known_generations
                          and str(material.id) not in planned_materials
                          and material.replacement_target_id is None
                          and material.container_material_id not in candidate_container_ids)
        if prior_unusable:
            continue
        if (material.id not in seen and material.id not in container_ids
                and (_subject_assessment_issue(material) or material.replacement_target_id is not None
                     or material.container_material_id in candidate_container_ids)):
            rows.append(material)
            seen.add(material.id)
    return rows


def validate_task_materials(db: Session, task: BsTask) -> dict:
    """两段式第二段（开始核查）的校验口径：硬阻止 + 软明示。

    材料集走 `_rule_materials`（剔除父容器、已替代旧版和待生效候选）：容器不是证据，
    旧版/候选也不能参与构成计数；解析中的候选仍从全量材料单独计算 pending 闸。
    """
    all_materials = db.query(BsMaterial).filter(BsMaterial.task_id == task.id).all()
    materials = _rule_materials(db, task)
    result = validate_materials([m.role for m in materials], scope_a3=task.scope_a3)
    result["composition"] = material_composition(materials)
    # 当前补件准备范围由后端统一判定；历史异常件不应阻塞无关补件。
    # 待替换的新件需要进入准备流程读取，故不以其旧目标的异常阻断启动。
    plan = get_supplement_generation_plan(task) or {}
    preparing_materials = _supplement_preparation_materials(db, task) if plan.get("status") == "planned" else []
    pending_targets = {m.replacement_target_id for m in preparing_materials if m.replacement_target_id is not None}
    result["subject_issue_material_ids"] = sorted(
        m.id for m in preparing_materials if _subject_assessment_issue(m) and m.id not in pending_targets
    )
    # `uploaded` 是延迟准备的正常中间态：start_review 会通过
    # `pending_pdf_materials` 统一受理为 preparing；只有 uploading/parsing 才是
    # 当前请求不可再次受理的并发摄取窗口。
    result["parsing_pending"] = any(
        m.parse_state in ("uploading", "parsing") for m in all_materials
    )
    # T10-B §B3 / 契约 §7.5.6：**失败子材料必须留下可消费的 soft gap**。
    # 只写事件与材料状态不够——`soft_gaps` 才会随 options_snapshot 固化并被报告消费；
    # 少了它，「某份子 PDF 读不出」会静默消失，A3 对表把该有一份当没有 ⇒ 误判缺件。
    # 硬阻止由 ZIP 容器整体失败另闸承担（failed_zip_containers），不在此列。
    failed_children = [m for m in materials
                       if m.container_material_id is not None
                       and m.parse_state == "failed"]
    if failed_children:
        names = "、".join(m.file_name for m in failed_children[:3])
        more = "等 %d 份" % len(failed_children) if len(failed_children) > 3 else ""
        result["soft_gaps"] = list(result["soft_gaps"]) + [
            "报审资料包内 %s%s文件无法读取，涉及这些文件的核查项会判为不可判定，"
            "请检查后重新上传" % (names, more)]
    active_ids = active_subject_ids(db, task)
    subjects = (db.query(BsSubject).filter(BsSubject.task_id == task.id,
                                            BsSubject.id.in_(active_ids)).all()) if active_ids else []
    name_gaps = [subject_name_warning(subject) for subject in subjects]
    result["name_gaps"] = [gap for gap in name_gaps if gap]
    if result["name_gaps"]:
        result["soft_gaps"] = list(result["soft_gaps"]) + result["name_gaps"]
    result["failed_children"] = [{"material_id": m.id, "file_name": m.file_name}
                                 for m in failed_children]
    unassigned_children = [m for m in all_materials
                           if m.container_material_id is not None
                           and m.parse_state in ("done", "failed")
                           and m.subject_id is None]
    if unassigned_children:
        result["hard_gaps"] = list(result["hard_gaps"]) + [
            "报审资料包内存在未归类子材料，请逐个确认分包主体后再开始核查（当前 %d 份）"
            % len(unassigned_children)]
        result["hard_ok"] = False
    result["unassigned_children"] = [
        {"material_id": m.id, "file_name": m.file_name}
        for m in unassigned_children
    ]
    return result


def ingest_material(db: Session, task: BsTask, *, file_name: str, content: bytes,
                    via: str = "upload", role: str = None, subject_id: int = None,
                    packet_id: int = None, material_category: str = None,
                    replacement_target_id: int = None,
                    background: bool = False, defer_read: bool = False, operator=None,
                    supplement_link: dict = None) -> BsMaterial:
    """真实文件摄取：落盘 → 记录 parse_state=parsing 收口 → 重活段（文本提取+判章）。

    P2-2：background=True 时重活段挪后台线程（自带会话、按 material_id 重新定位），
    上传请求立即返回，大文件不再阻塞响应；收口结果经 material.parsed /
    material.parse_failed 事件流出（SSE 时间线已消费）。默认同步——种子路径与
    既有测试的确定性依赖。start_review 的 parsing_pending 409 闸（07 §4.1）是
    异步窗口的硬防线。
    """
    # B0（A-08）：直调 service 的二道防线——不经 router（如种子/脚本）也不放过超大文件。
    _check_upload_size(content)
    rel_path = parsing.save_upload(task.id, file_name, content)
    mat = BsMaterial(
        task_id=task.id, file_name=file_name,
        subject_id=subject_id, packet_id=packet_id,
        material_category=material_category,
        replacement_target_id=replacement_target_id,
        role=role or classify_material(file_name),
        role_confirmed=role is not None, via=via, path=rel_path,
        size_kb=max(1, round(len(content) / 1024)),
        parse_state="uploaded" if defer_read else "parsing",
    )
    db.add(mat)
    db.flush()
    if supplement_link:
        add_event(db, task.id, "material.supplement_linked",
                  payload={**supplement_link, "material_id": mat.id}, commit=False)
    db.commit()
    db.refresh(mat)
    # 先建立稳定的材料归集锚点，再异步/延迟读取正文。该主体不是审核结论，
    # 解析收口后只会补充真实单位名称，避免报审资料上传时下拉框为空。
    # 锚点必须**可回收**：删除或重传会留下无人引用的空名主体，若每次上传都新建
    # 就会无限堆积（任务 77 实测 5 个主体，其中 3 个空名孤儿）。
    if mat.subject_id is None and mat.role in ("sub", "labor"):
        with _SUBJECT_IDENTITY_LOCK:
            subject_type = "professional" if mat.role == "sub" else "labor"
            subject = _reusable_subject_anchor(db, task, subject_type)
            if subject is None:
                subject = create_subject(db, task, subject_type=subject_type, name=None)
            mat.subject_id = subject.id
            db.commit()
            db.refresh(mat)
    # 上传正文尚未解析。类型识别由后台摄取收口（_finish_ingest）统一触发；
    # 在 HTTP 请求内扫主体材料会重复 OCR 已有扫描件并阻塞后续上传。
    if defer_read:
        return mat  # HTTP 上传只保存；由唯一 preparing worker 在开始核查后读取。
    if background:
        threading.Thread(target=_finish_ingest, args=(task.id, mat.id, db.get_bind()),
                         daemon=True, name=f"bs-ingest-{mat.id}").start()
        return mat
    _finish_ingest(task.id, mat.id, db.get_bind())
    db.refresh(mat)  # 重活段经独立会话提交，请求会话需刷新才能看到收口态
    return mat


def supersede_material(db: Session, task: BsTask, old_material_id: int,
                       new_material_id: int, *, operator=None) -> BsMaterial:
    """兼容旧调用名，统一委托 D9 解析成功后的原子 CAS。"""
    return activate_material_replacement(
        db, task, old_material_id, new_material_id, operator=operator
    )


class MaterialReplacementError(ValueError):
    """Base error for the D9 material-replacement domain boundary."""


class MaterialReplacementBoundaryError(MaterialReplacementError):
    """The requested old/new pair is not a legal replacement edge."""


class MaterialReplacementConflict(MaterialReplacementError):
    """The old node changed before this replacement could win its CAS."""


def _replacement_chain_reaches(db: Session, *, task_id: int,
                               start_id: int, target_id: int) -> bool:
    """Return whether following ``superseded_by_id`` reaches ``target_id``.

    Existing corrupt cycles are rejected as a boundary error instead of being
    traversed forever or silently extended.
    """
    seen = set()
    current_id = start_id
    while current_id is not None:
        if current_id == target_id:
            return True
        if current_id in seen:
            raise MaterialReplacementBoundaryError("现有材料替代链已形成环，不能继续替代")
        seen.add(current_id)
        current = db.get(BsMaterial, current_id)
        if current is None or current.task_id != task_id:
            raise MaterialReplacementBoundaryError("材料替代链存在跨任务或缺失节点")
        current_id = current.superseded_by_id
    return False


def activate_material_replacement(db: Session, task: BsTask, old_material_id: int,
                                  new_material_id: int, *, operator=None) -> BsMaterial:
    """Atomically activate a parsed replacement with an old-node CAS.

    Upload orchestration calls it only after the new material reaches
    ``parse_state=done``.  A persisted ``replacement_target_id`` keeps the
    intent recoverable across the deferred preparation worker and service restart.
    """
    # 用户只能在 setup 发起替换；已登记的替换意图会在准备 worker 解析成功后于
    # preparing 内部生效。running/review/done 均不得再改变材料链。
    if task is None or task.state not in ("setup", "preparing"):
        raise MaterialReplacementBoundaryError("核查已开始，不能替换材料")
    if old_material_id == new_material_id:
        raise MaterialReplacementBoundaryError("材料不能替换自身")

    old = db.get(BsMaterial, old_material_id)
    new = db.get(BsMaterial, new_material_id)
    if old is None or old.task_id != task.id:
        raise MaterialReplacementBoundaryError("待替换材料必须属于同一任务")
    if new is None or new.task_id != task.id:
        raise MaterialReplacementBoundaryError("新材料必须属于同一任务")
    if new.replacement_target_id not in (None, old.id):
        raise MaterialReplacementBoundaryError("待替代目标与材料替代请求不一致")
    if old.superseded_by_id is not None:
        raise MaterialReplacementConflict("该材料已被替换，请刷新后查看当前版本")
    if new.parse_state != "done":
        raise MaterialReplacementBoundaryError("新材料必须解析成功后才能生效")
    if _subject_assessment_issue(new):
        raise MaterialReplacementBoundaryError("新材料主体校验未通过，不能替换原材料")
    if old.subject_id != new.subject_id:
        raise MaterialReplacementBoundaryError("新旧材料必须属于同一主体")
    if (old.material_category != new.material_category or old.role != new.role):
        raise MaterialReplacementBoundaryError("新旧材料必须属于同一材料类别")
    if _replacement_chain_reaches(
        db, task_id=task.id, start_id=new.id, target_id=old.id
    ):
        raise MaterialReplacementBoundaryError("材料替代关系不能形成环")
    if new.superseded_by_id is not None:
        raise MaterialReplacementBoundaryError("新材料已被替代，不能作为当前版本")

    next_revision = None if old.revision_no is None else old.revision_no + 1
    if new.revision_no not in (None, next_revision):
        raise MaterialReplacementBoundaryError("新材料修订号与待替换链不一致")
    superseded_at = datetime.now()
    try:
        result = db.execute(
            update(BsMaterial)
            .where(
                BsMaterial.id == old.id,
                BsMaterial.task_id == task.id,
                BsMaterial.superseded_by_id.is_(None),
            )
            .values(superseded_by_id=new.id, superseded_at=superseded_at)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise MaterialReplacementConflict("该材料已被替换，请刷新后查看当前版本")
        new.revision_no = next_revision
        # 待生效意图只服务于解析期间的恢复；成功后由旧节点的替代指针和事件
        # 承担历史关系，清空工作流字段，避免规则输入把已生效节点继续当候选。
        new.replacement_target_id = None
        add_event(db, task.id, "material.superseded", payload={
            "old_material_id": old.id,
            "new_material_id": new.id,
            "operator": operator,
        }, commit=False)
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(old)
    db.refresh(new)
    logger.info("[bs] 材料替代原子生效 task=%s old=%s new=%s",
                task.id, old.id, new.id)
    return new


def _activate_pending_replacement(session: Session, task: BsTask, material: BsMaterial,
                                  *, operator=None) -> bool:
    """收口一个已解析候选的待生效替代意图。

    解析结果先按既有规则提交；CAS 冲突或边界问题只留下可读降级并保留候选，
    不回滚成功的解析事实，也不把旧材料误标为已替代。下次材料编辑/重试可据
    ``replacement_target_id`` 定位该意图。
    """
    if material is None:
        return False
    target_id = material.replacement_target_id
    if target_id is None:
        return False
    if operator is None:
        requested = (session.query(BsTaskEvent)
                     .filter(BsTaskEvent.task_id == task.id,
                             BsTaskEvent.event_type == "material.replacement_requested")
                     .order_by(BsTaskEvent.id.desc()).all())
        for event in requested:
            payload = event.payload_json or {}
            if payload.get("material_id") == material.id:
                operator = payload.get("operator")
                break
    try:
        activate_material_replacement(
            session, task, target_id, material.id, operator=operator
        )
        return True
    except MaterialReplacementError as exc:
        session.rollback()
        current = session.get(BsMaterial, material.id)
        if current is None:
            return False
        current.degradations_json = list(current.degradations_json or []) + [{
            "kind": "replacement_activation_failed",
            "message": str(exc),
        }]
        session.commit()
        logger.warning("[bs] 材料替代意图未能生效 task=%s material=%s target=%s: %s",
                       task.id, material.id, target_id, exc)
        return False


def _emit_material_page_progress(session, task_id, material, current, total, source="text", run_id=None, done_pages=None, total_work_pages=None):
    """把文本层逐页读取进度写入 SSE；页数未知时只发送已读页。"""
    add_event(session, task_id, "material.page_progress", payload={
        "material_id": material.id,
        "file_name": material.file_name,
        "current_page": int(current),
        "total_pages": int(total) if total else None,
        "page": int(current), "source": source,
        "run_id": run_id,
        "done_pages": done_pages,
        "total_work_pages": total_work_pages,
    })


def _material_supplement_generation(db: Session, material: BsMaterial) -> str | None:
    """本材料所属的补件代次；ZIP 子材料认其容器的链接事件。

    只接受**唯一**代次：同一材料出现多个互斥代次、或根本没有链接事件时返回
    ``None``。事实照写、代次留空，让复核看见「证据存在但归属未定」，不靠猜测
    补归属（S2-D1）。
    """
    linked_id = material.container_material_id or material.id
    payloads = (
        db.query(BsTaskEvent.payload_json)
        .filter(BsTaskEvent.task_id == material.task_id,
                BsTaskEvent.event_type == "material.supplement_linked")
        .all()
    )
    generations = set()
    for (payload,) in payloads:
        payload = payload or {}
        if payload.get("material_id") == linked_id and payload.get("generation_id"):
            generations.add(str(payload["generation_id"]))
    return generations.pop() if len(generations) == 1 else None


def _finish_ingest(task_id: int, material_id: int, bind,
                   *, refresh_type: bool = True) -> None:
    """重活段（P2-2 挪出请求线程）：自带会话，文本提取 → 判章 → 事件落库。

    判章复用 OC _local_seal_detect（红/HSV/黑白三通道，扫描件覆盖）；检测器内部
    吞异常返回 []，空列表≠缺章证据。失败收口 parse_state=failed + parse_failed
    事件，不阻断其他材料。

    `refresh_type=False`（T10-B §7.3.4 批次屏障）：只做文件级解析，**跳过**主体类型
    识别这一步。ZIP 子材料会并发解析，若各自在收口时写主体 AI 类型/冲突状态，就是
    契约明令禁止的「并发覆写主体冲突状态」；正确做法是全部子材料收口后由
    `_prepare_zip_background` 在批次屏障后串行补做一次交叉识别。
    """
    session = SessionLocal(bind=bind)
    pending_subject_id = None
    try:
        mat = session.get(BsMaterial, material_id)
        if mat is None:
            return  # 上传后立即删任务的窗口：记录已不在，无处收口
        parse_run_id = str(uuid.uuid4())
        add_event(session, task_id, "material.parse_started", payload={
            "material_id": mat.id, "file_name": mat.file_name, "run_id": parse_run_id,
        })
        owner = session.get(BsTask, task_id)
        input_sha256 = None
        completed_pages = {"text": set(), "ocr": set(), "retry": set(), "rotation": set(), "seal": set()}

        def emit_page(current, total, source, total_work):
            phase = ("text" if source == "text" else "retry" if source in {"retry", "retry_failed"}
                     else source if source in {"rotation", "seal"} else "ocr")
            if current > 0:
                completed_pages[phase].add(current)
            try:
                _emit_material_page_progress(session, task_id, mat, current, total, source,
                                             parse_run_id, len(completed_pages[phase]), total_work)
            except Exception:
                # 进度事件不是材料内容；数据库错误须回滚会话，避免污染后续解析收口。
                session.rollback()
                logger.warning("[bs] 材料页进度写入失败 task=%s material=%s page=%s",
                               task_id, material_id, current, exc_info=True)
        try:
            abs_path = os.path.abspath(os.path.join(
                os.path.dirname(parsing.__file__), "..", "..", mat.path))
            input_sha256 = _material_sha256(abs_path)
            # 独立版不依赖广场计费；材料归属由任务权限和解析事件保留。
            pages, degradations = parsing.parse_material_pages(abs_path, progress=emit_page)
            if _material_sha256(abs_path) != input_sha256:
                raise ValueError("材料原件在读取期间发生变化，请重新上传")
            ocr_recovered = sum(d.get("recovered_pages", 0) for d in degradations
                                if d.get("kind") == "ocr_fallback")
            mat.pages_json = pages
            mat.pages = next((d["total_pages"] for d in degradations if "total_pages" in d),
                             len(pages) or None)
            mat.parse_state = "done"
            mat.degradations_json = degradations
        except Exception as e:  # 解析失败不吞：状态落 failed，错误进降级记录
            mat.parse_state = "failed"
            mat.degradations_json = [{"kind": "parse_failed", "message": str(e)[:200]}]
            # add_event 尾部 commit 会一并提交本会话全部脏变更——材料状态与事件
            # 同事务落库（分次 commit 有竞态：轮询见终态时事件可能尚未可见）
            add_event(session, task_id, "material.parse_failed",
                      payload={"material_id": mat.id, "file_name": mat.file_name,
                               "run_id": parse_run_id,
                               "input_sha256": input_sha256})
            return
        # D-1：事实在收口事务内写入——材料状态、事实批次与 material.parsed 由
        # 本会话一次性提交，避免「材料已 done 但事实还看不见」的中间态。
        # 抽取失败不回滚已读出的材料，只降级 facts_failed（D-2：不自动重跑 OCR）。
        facts_inserted = None
        try:
            inserted_facts = persist_material_facts(
                session, mat, generation_id=_material_supplement_generation(session, mat),
            )
            facts_inserted = len(inserted_facts)
        except Exception as e:  # 事实是候选证据，抽取异常不得吞掉解析结果
            logger.warning("[bs] 材料事实抽取失败 material=%s: %s", material_id, e,
                           exc_info=True)
            degraded = {"kind": "facts_failed", "message": str(e)[:200]}
            # 同步写进局部 degradations：下游降级摘要与冲突分支都以它为准。
            degradations = list(degradations or []) + [degraded]
            mat.degradations_json = list(mat.degradations_json or []) + [degraded]
        # S3：名单与证书事实对齐成三态关系（候选判定）。关系随材料收口一并提交；
        # 解析异常只降级 relations_failed，不影响已读出的文本与事实。
        relations_inserted = None
        if owner is not None:
            try:
                relations_inserted = len(resolve_task_relations(
                    session, owner,
                    generation_lookup=lambda m: _material_supplement_generation(session, m),
                ))
            except Exception as e:
                logger.warning("[bs] 人员证件关系解析失败 material=%s: %s", material_id, e,
                               exc_info=True)
                degraded = {"kind": "relations_failed", "message": str(e)[:200]}
                degradations = list(degradations or []) + [degraded]
                mat.degradations_json = list(mat.degradations_json or []) + [degraded]
        if owner is not None:
            conflict = material_subject_conflict(session, owner, mat)
            selected_subject = session.get(BsSubject, mat.subject_id) if mat.subject_id is not None else None
            cert_needs = _certificate_supplement_needs_subject(session, owner, mat)
            social_needs = _social_supplement_needs_employer(session, owner, mat)
            labor_needs = _labor_supplement_needs_employer(session, owner, mat)
            personnel_cert_needs = _personnel_certificate_needs_employer(session, owner, mat)
            readable_subject_material = _subject_readable_material(mat)
            relations = personnel_evidence.extract_person_relation_evidence([readable_subject_material])
            employers = {a2_check.norm_company(name) for fact in relations
                         for name in fact.get("employer_candidates", []) if name}
            required_kinds = ({"social_record"} if social_needs else set()) | ({"labor_contract"} if labor_needs else set())
            relation_incomplete = bool(required_kinds) and not personnel_evidence.relation_segments_have_unique_employers(readable_subject_material, required_kinds)
            unknown = (selected_subject is None or not selected_subject.name
                       or (cert_needs and not material_identified_subject(mat))
                       or (required_kinds and (len(employers) != 1 or relation_incomplete))
                       or (personnel_cert_needs and len(a2_check.certificate_registered_companies(readable_subject_material)) != 1))
            if conflict is None and (cert_needs or social_needs or labor_needs or personnel_cert_needs) and unknown:
                conflict = {"kind": "subject_unresolved", "code": "material_subject_unresolved",
                            "message": "无法确认材料所属主体，请核对材料或重新上传清晰材料"}
            if conflict is not None:
                # 读取结果保留供核对，但不作为有效证据或激活替换链。
                mat.degradations_json = list(degradations or []) + [conflict]
                payload = {
                    "material_id": mat.id,
                    "file_name": mat.file_name,
                    "run_id": parse_run_id,
                    "input_sha256": input_sha256,
                    "pages": mat.pages,
                    "ocr_recovered": ocr_recovered,
                    "layout_pages": sum(d.get("layout_pages", 0) for d in degradations
                                        if d.get("kind") == "ocr_fallback"),
                    "degraded": True,
                    **conflict,
                }
                if conflict["kind"] == "subject_conflict":
                    add_event(session, task_id, "material.subject_conflict", payload=payload)
                else:
                    add_event(session, task_id, "material.subject_unresolved", payload=payload)
                session.commit()
                return
        if owner is not None and mat.role in ("sub", "labor", "packet"):
            try:
                assign_material_subject_by_exact_name(session, owner, mat)
            except Exception as e:  # 自动归属失败不得影响材料读取收口
                logger.warning("[bs] 材料主体归并跳过 material=%s: %s", material_id, e)
        # 图像判章（W4，复用 OC _local_seal_detect，只 import 不改现役）
        try:
            # 进度事件独立事务，避免提交尚未完成的材料状态。
            with SessionLocal(bind=bind) as progress_session:
                _emit_material_page_progress(progress_session, task_id, mat, 0, mat.pages or 0,
                                             "seal", parse_run_id, 0, 0)
        except Exception:
            logger.warning("[bs] 印章阶段进度写入失败 task=%s material=%s", task_id, material_id, exc_info=True)
        try:
            from .seal_detect import _local_seal_detect
            mat.seal_json = _local_seal_detect(abs_path)
        except Exception as e:  # pragma: no cover —— 判章不可用不阻断摄取
            logger.warning("[bs] 判章跳过 %s: %s", mat.file_name, e)
            mat.seal_json = []
        # 同上：材料收口态与 material.parsed 事件同一事务
        request_errors = {}
        for degradation in degradations:
            if degradation.get("kind") == "ocr_request_errors":
                for error in degradation.get("request_errors", []):
                    if type(error.get("page")) is int:
                        request_errors.setdefault(error.get("category"), set()).add(error["page"])
        add_event(session, task_id, "material.parsed", payload={
            "material_id": mat.id, "file_name": mat.file_name, "pages": mat.pages,
            "degraded": bool(degradations), "ocr_recovered": ocr_recovered,
            "request_errors": [{"category": category, "pages": sorted(pages)}
                               for category, pages in request_errors.items()],
            "run_id": parse_run_id,
            "input_sha256": input_sha256,
            "input_sha256_verified": True,
            "facts_inserted": facts_inserted,
            "relations_inserted": relations_inserted,
            "layout_pages": sum(d.get("layout_pages", 0) for d in degradations
                                 if d.get("kind") == "ocr_fallback"),
        })
        owner = session.get(BsTask, task_id)
        if owner is not None:
            _activate_pending_replacement(session, owner, session.get(BsMaterial, mat.id))
    except Exception:  # pragma: no cover —— 后台线程兜底：任何未预期异常不留无声
        logger.exception("[bs] 后台摄取收口失败 material=%s", material_id)
    finally:
        if pending_subject_id is not None:
            _mark_subject_type_scan_pending(task_id, pending_subject_id, -1)
        session.close()


def recover_interrupted_ingests(db: Session) -> int:
    """Q1 启动恢复（含 T10-B §7.3.6 的 ZIP 准备复位）。

    两类孤儿：

    1. **材料级**：进程退出会杀死后台收口线程，残留的 uploading/parsing 材料
       永远等不到收口，parsing_pending 409 闸会把任务卡死。启动时统一置 failed +
       parse_interrupted 降级记录 + material.parse_failed 事件（时间线可见），
       用户删除重传即可。幂等：只动这两个状态，done/failed 不碰。
    2. **任务级（T10-B）**：进程退出杀死的准备 worker 会把任务永久留在 `preparing`
       ——契约 §7.3.6 明令不得如此。复位为 `setup`（**绝不**自动宣称 running），
       复用其已成功收口的子材料与已 `done` 的容器；未完成容器保持 `stored`
       （可重试），整体 `failed` 的容器**不重置**（那是需要用户处理的硬阻止）。
       同时写可读恢复事件。重启后再次 start 走正常受理，幂等复用不重复派生。

    返回处理的材料条数（保持既有契约；任务复位数记日志与事件，不改返回值语义）。
    """
    stale = (db.query(BsMaterial)
             .filter(BsMaterial.parse_state.in_(("uploading", "parsing")))
             .all())
    for mat in stale:
        mat.parse_state = "failed"
        mat.degradations_json = (list(mat.degradations_json or []) + [{
            "kind": "parse_interrupted",
            "message": "解析中断（服务重启），请删除该材料并重新上传",
        }])
        # add_event 尾部 commit 一并提交材料状态（收口态与事件同事务，与
        # _finish_ingest 同纪律）；material.parse_failed 已在 EVENT_LABELS。
        add_event(db, mat.task_id, "material.parse_failed",
                  payload={"material_id": mat.id, "file_name": mat.file_name,
                           "reason": "service_restart"})
        logger.info("[bs] 启动恢复：材料 %s（%s）置为失败", mat.id, mat.file_name)

    # ——任务级：`preparing` 复位（不得永久停留）——
    stuck = (db.query(BsTask)
             .filter(BsTask.state == TASK_STATE_PREPARING)
             .order_by(BsTask.id).all())
    for task in stuck:
        containers = task_zip_containers(db, task)
        ready = [m for m in containers if m.parse_state == "done"]
        failed = [m for m in containers if _is_failed_container(m)]
        retryable = [m for m in containers if m not in ready and m not in failed]
        task.state = TASK_STATE_SETUP
        add_event(db, task.id, "zip.preparation_recovered", payload={
            "reason": "service_restart",
            "message": "资料包准备被服务重启中断，已回到可继续状态；"
                       "请重新点击开始核查，已读取的资料会直接复用",
            "containers": [{"material_id": m.id, "file_name": m.file_name,
                            "parse_state": m.parse_state} for m in containers],
            "ready_containers": [m.id for m in ready],
            "retryable_containers": [m.id for m in retryable],
            "failed_containers": [m.id for m in failed],
        })
        logger.info("[bs] 启动恢复：任务 %s 从 preparing 复位 setup（已就绪容器 %s / "
                    "待重试 %s）", task.id, len(ready), len(retryable))
        # 任务整体事件已在上面统一 commit；此处仅记数不重复提交。
    # 仅恢复已过期租约；其他服务进程的有效运行不能因本进程启动而被作废。
    reference_task_ids = [row[0] for row in db.query(BsTask.id)
                          .filter(BsTask.state.in_(["running", "review"])).all()]
    for task_id in reference_task_ids:
        db.rollback()
        owner = db.query(BsTask).filter(BsTask.id == task_id).with_for_update().first()
        if owner is not None:
            recover_reference_runs(db, owner)
        db.commit()
    return len(stale)


# ==================== T4.1-T2：ZIP 容器保存与安全派生 ====================

# 材料 `path` 的基准目录（与 parsing.save_upload / router.material_file 同口径）
_APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(parsing.__file__), "..", ".."))

# 派生子材料展示名长度上限：bs_material.file_name 列宽 String(255)
_DISPLAY_NAME_MAX = 255

# ZIP 准备生命周期（契约 §7.3，T10-B）：`setup` 与 `running` 之间的**持久化**中间态。
# 复用既有 `BsTask.state`（String(40)，`preparing` 9 字符）——**不需要 schema 迁移**；
# 也不新增 job 表：任务态本身就是唯一准备 job 的标识（见 `_accept_zip_preparation`）。
TASK_STATE_SETUP = "setup"
TASK_STATE_PREPARING = "preparing"


def _setup_write_allowed(task) -> bool:
    """setup 写入口门禁：**只有 `setup` 可写**。

    `preparing`（ZIP 资料包展开中）是启动前的准备段，不属于可编辑窗口——
    契约 §7.3.3 要求准备期间上传/删除/归属/改型/重复启动**一律拒绝**
    （此时子材料正在派生，改归属会让继承口径与已落盘子材料不一致）。
    """
    return task is not None and task.state == TASK_STATE_SETUP


def _material_write_allowed(task) -> bool:
    """材料写入口门禁（`add_material` / `set_material_role` 专用）。

    **只拒 `preparing`**，不放宽也不收紧其余态：这两个函数在 router 层已有
    `state != 'setup' → 409` 的 HTTP 门（既有行为），service 层此前**完全无门**
    ——既有测试与脚本依赖直调它们造数据（running/review 态也会调）。若在 service
    层改成一并拒绝非 setup，就会把「补 preparing 门」变成「顺手收紧既有语义」，
    造成与 T10-B 无关的回归。契约 §7.3.3 要求的正是 preparing 期必须拒绝。
    """
    return task is not None and task.state != TASK_STATE_PREPARING


def _subject_scan_allowed(task) -> bool:
    """主体类型识别是否可运行：`setup` 与 `preparing` 均可。

    契约 §2.4「识别只在 setup 段」的边界是**启动之后**（running/review/done 快照
    已固化、类型已锁定，重算不得扰动）；`preparing` 尚未启动，且契约 §7.3.4 明确
    要求子材料收口后必须补做类型交叉识别——若按字面只在 setup 跑，ZIP 子材料
    携带的类型证据将永远不参与判定。
    """
    return task is not None and task.state in (TASK_STATE_SETUP, TASK_STATE_PREPARING)


def material_abs_path(rel_path: str) -> str:
    """材料相对路径（相对 app/）→ 绝对路径。"""
    return os.path.abspath(os.path.join(_APP_ROOT, rel_path))


def _child_display_name(container_name: str, entry_path: str) -> str:
    """派生子材料展示名：`容器名 / 容器内相对路径`（契约 §7.4.3）。

    只用可能重名的 basename 无法区分同容器内 `a/合同.pdf` 与 `b/合同.pdf`，所以
    必须带相对路径。超长（深目录 + 长中文名）时**截头保尾**：相对路径才是区分
    同容器内不同 PDF 的关键，容器名可由 `container_file_name` 回查。
    """
    name = "%s / %s" % (container_name or "", entry_path)
    if len(name) <= _DISPLAY_NAME_MAX:
        return name
    return "…" + name[-(_DISPLAY_NAME_MAX - 1):]


def _container_extract_dir(container: BsMaterial) -> str:
    """容器专属解包目录：与原包同级的 `extracted/`。

    由**原包所在目录**派生（而非文件名），配合 `store_zip_container` 给每个容器
    分配的随机子目录，保证同任务多容器互不覆盖，且始终落在本任务目录之内。
    """
    return os.path.join(os.path.dirname(material_abs_path(container.path)), "extracted")


def zip_containers(db: Session, task: BsTask) -> list:
    """任务内**尚未准备**的 ZIP 容器材料（`parse_state='stored'`，契约 §7.2.4）。

    `stored` 是容器专用取值：原包已保存、待准备阶段展开。它既不进
    `parsing_pending` 闸（该闸只扫 uploading/parsing），也不代表内容已读取。
    """
    return (db.query(BsMaterial)
            .filter(BsMaterial.task_id == task.id,
                    BsMaterial.parse_state == PARSE_STATE_STORED)
            .order_by(BsMaterial.id).all())


def _task_children(db: Session, task: BsTask) -> list:
    """任务内全部容器派生子材料（按 id 升序，稳定可断言）。"""
    return (db.query(BsMaterial)
            .filter(BsMaterial.task_id == task.id,
                    BsMaterial.container_material_id.isnot(None))
            .order_by(BsMaterial.id).all())


def _container_ids_from_events(db: Session, task_id: int) -> set:
    """从容器相关事件里回收容器材料 ID（T10-B §B4）。

    为什么需要它：容器身份在库里**没有专属列**（唯一定位靠「有子材料指向它」），
    而「整体失败」的容器**没有子材料**（契约 §7.5.6：整包安全展开后才建子材料）、
    子材料也可能被用户在 setup 段逐个删掉。事件是持久且随任务删除的权威留痕，
    用它兜住这两种形态，避免容器在 DTO/闸门里「隐身」。
    """
    rows = (db.query(BsTaskEvent.event_type, BsTaskEvent.payload_json)
            .filter(BsTaskEvent.task_id == task_id).all())
    ids = set()
    for etype, payload in rows:
        if not etype.startswith("zip."):
            continue
        p = payload or {}
        cid = p.get("container_material_id")
        if cid:
            ids.add(cid)
        for item in (p.get("containers") or []):
            if isinstance(item, dict) and item.get("material_id"):
                ids.add(item["material_id"])
    return ids


def task_zip_containers(db: Session, task: BsTask) -> list:
    """任务内**全部** ZIP 容器（含 `stored`/`done`/`failed` 三态）。

    与 `zip_containers`（只取待准备态）互补：详情 DTO、重复 start 的受理回执、
    恢复复位都需要看见已收口/已失败的容器，单看 `stored` 会漏掉它们。

    识别是**三路并集**（库内无专属列，见 `_container_ids_from_events` 说明）：
    ① `parse_state='stored'`（尚未准备，此时还没有任何事件）；② 被现有子材料
    指向的父材料；③ 容器相关事件里登记的 ID（覆盖「整体失败无子材料」与
    「子材料被逐个删除」两种形态）。
    """
    rows = (db.query(BsMaterial).filter(BsMaterial.task_id == task.id)
            .order_by(BsMaterial.id).all())
    parent_ids = {m.container_material_id for m in rows if m.container_material_id}
    event_ids = _container_ids_from_events(db, task.id)
    return [m for m in rows
            if m.id in parent_ids or m.id in event_ids
            or m.parse_state == PARSE_STATE_STORED]


def _is_failed_container(mat) -> bool:
    """容器是否**整体准备失败**：由 `_fail_container` 写入的专属降级标记判定。

    刻意不靠文件名后缀或「有子材料」推断——`zip_extract_failed` 是唯一由失败收口
    函数写入的标记，单材料解析失败（子材料 failed）不带它，两者语义必须分得开。
    """
    return any((d or {}).get("kind") == "zip_extract_failed"
               for d in (mat.degradations_json or []))


def failed_zip_containers(db: Session, task: BsTask) -> list:
    """准备整体失败的 ZIP 容器（契约 §7.5.6：**硬阻止**启动）。

    与「待准备」是两个闸：`stored` 是「稍后就好」（受理准备），`failed` 是
    「必须用户处理」（移除或重新上传）——混成一个会让损坏包被反复重试或静默放行。
    """
    return [m for m in task_zip_containers(db, task) if _is_failed_container(m)]


def zip_preparation_summary(db: Session, task: BsTask) -> dict:
    """任务详情的 ZIP 准备汇总（T10-B §B4，只读派生）。

    `state` 是**任务视角**的四态：无容器 → `none`；有 `stored` 或任务在 `preparing`
    → `preparing`；存在整体失败容器 → `failed`；其余（容器全 `done`）→ `done`。
    优先级 `failed` > `preparing` > `done`：失败是用户必须先处理的硬阻止，不能被
    「另有一个容器还在准备」掩盖。
    """
    containers = task_zip_containers(db, task)
    if not containers:
        return {"state": "none", "containers": []}
    children = _task_children(db, task)
    by_container = {}
    for child in children:
        by_container.setdefault(child.container_material_id, []).append(child)
    failed = [c for c in containers if _is_failed_container(c)]
    pending = [c for c in containers if c.parse_state == PARSE_STATE_STORED]
    if failed:
        state = "failed"
    elif pending or task.state == TASK_STATE_PREPARING:
        state = "preparing"
    else:
        state = "done"
    rows = []
    for c in containers:
        kids = by_container.get(c.id) or []
        rows.append({
            "container_material_id": c.id, "file_name": c.file_name,
            "parse_state": c.parse_state,
            "child_count": len(kids),
            "children_done": sum(1 for k in kids if k.parse_state == "done"),
            "children_failed": sum(1 for k in kids if k.parse_state == "failed"),
            "message": _container_failure_message(c) or None,
        })
    return {"state": state, "containers": rows}


def task_progress(db: Session, task: BsTask, *, materials=None, snapshot=None,
                  zip_summary=None) -> dict:
    """Return the server-authoritative progress projection for a task.

    The projection counts selected check steps only. Material reading and AI
    work have separate progress because their units are not comparable.
    """
    materials = list(materials) if materials is not None else (
        db.query(BsMaterial).filter(BsMaterial.task_id == task.id).all()
    )
    active = [m for m in materials if m.superseded_by_id is None]
    direct = [m for m in active
              if m.container_material_id is None
              and not str(m.file_name or "").lower().endswith(".zip")]
    children = [m for m in active if m.container_material_id is not None]
    material_rows = direct + children
    preparation = zip_summary if zip_summary is not None else zip_preparation_summary(db, task)
    containers = preparation.get("containers") or []
    unresolved_container = any(
        row.get("parse_state") not in {"done", "failed"}
        and int(row.get("child_count") or 0) == 0 for row in containers
    )

    if material_rows:
        material_total = len(material_rows)
        material_done = sum(1 for m in material_rows if m.parse_state in {"done", "failed"})
        material_failed = sum(1 for m in material_rows if m.parse_state == "failed")
        current_material = next(
            (m for m in material_rows if m.parse_state in {"uploaded", "parsing", PARSE_STATE_STORED}),
            None,
        )
        if unresolved_container:
            material_total = None
    else:
        material_total = sum(int(row.get("child_count") or 0) for row in containers)
        material_done = sum(
            int(row.get("children_done") or 0) + int(row.get("children_failed") or 0)
            for row in containers
        )
        material_failed = sum(int(row.get("children_failed") or 0) for row in containers)
        current_material = next(
            (row for row in containers if row.get("parse_state") in {"stored", "uploaded", "parsing"}),
            None,
        )
        # A ZIP container with no children is still an unresolved total until
        # expansion reports its child count; do not turn that into 0%/100%.
        if containers and material_total == 0 and any(
            row.get("parse_state") not in {"done", "failed"} for row in containers
        ):
            material_total = None

    if snapshot is None:
        snapshot = reference_snapshots.get_reference_snapshot(db, task) or {}
    if not isinstance(snapshot, dict):
        snapshot = {}
    steps = list(snapshot.get("steps") or []) if isinstance(snapshot, dict) else []
    selected_steps = [step for step in steps if step.get("state") != "out_of_scope"]
    check_total = len(selected_steps) or None
    terminal_states = {"completed", "failed", "error", "out_of_scope"}
    check_done = sum(1 for step in selected_steps if step.get("state") in terminal_states)
    if not selected_steps and isinstance(snapshot, dict):
        runs = list(snapshot.get("runs") or [])
        if runs:
            check_total = len(runs)
            check_done = sum(1 for run in runs if run.get("state") in terminal_states)

    events = (db.query(BsTaskEvent)
              .filter(BsTaskEvent.task_id == task.id)
              .order_by(BsTaskEvent.sequence.desc())
              .limit(200).all())
    semantic_event = next((event for event in events if event.event_type == "check.semantic_progress"), None)
    latest_start = next((event for event in events if event.event_type in {"task.started", "task.rerun", "check.completed"}), None)
    if semantic_event is not None and latest_start is not None and semantic_event.sequence <= latest_start.sequence:
        semantic_event = None
    completed_kinds = {
        (event.payload_json or {}).get("kind")
        for event in events if event.event_type == "check.completed"
    }
    completed_kinds.discard(None)
    if check_total is None and completed_kinds:
        check_done = len(completed_kinds)

    done = task.state == "done"
    checks_done = check_total is not None and check_done >= check_total
    check_failed = any(step.get("state") in {"failed", "error"} for step in selected_steps)
    ai_state = None
    ai_current = None
    if checks_done and snapshot.get("ai_auto_enabled") is True and not done:
        try:
            coverage = ai_advisory_snapshots.collect_current_coverage(db, task, snapshot, reporting.report_review_blocking)
        except Exception:
            logger.warning("[bs] AI progress coverage unavailable task_id=%s", task.id, exc_info=True)
            coverage = None
        groups = (coverage or {}).get("groups") or []
        if not coverage or coverage.get("state") != "available" or any(g.get("state") != "available" for g in groups):
            ai_state = "unknown"
        elif any((g.get("counts") or {}).get(key, 0) for g in groups for key in ("not_started", "pending")):
            ai_state = "running"
            pending_groups = [g for g in groups if (g.get("counts") or {}).get("pending", 0) > 0]
            if len(pending_groups) == 1:
                ai_current = pending_groups[0]
        elif any((g.get("counts") or {}).get("failed", 0) > g.get("reviewable_failed", 0)
                 or (g.get("counts") or {}).get("partial", 0) > g.get("reviewable_partial", 0)
                 or (g.get("counts") or {}).get("not_run", 0) > g.get("reviewable_not_run", 0)
                 for g in groups):
            ai_state = "failed"
        else:
            ai_state = "completed"
    if task.state == "setup":
        phase = "waiting"
    elif task.state == "preparing":
        phase = "reading"
    elif done:
        phase = "completed"
    elif checks_done and (check_failed or ai_state == "failed"):
        phase = "failed"
    elif checks_done and ai_state in {"running", "unknown"}:
        phase = "ai_checking"
    elif checks_done:
        phase = "completed"
    else:
        phase = "checking"

    total_units = None
    completed_units = check_done
    percent = 100 if done else None
    if check_total is not None:
        total_units = check_total
        if phase == "checking":
            percent = round(completed_units * 100 / total_units)
        elif phase == "completed":
            percent = 100

    current_step = next((step for step in selected_steps if step.get("state") == "running"), None)
    if phase == "waiting":
        current = {"key": "waiting", "label": "等待上传材料", "current": 0, "total": material_total}
    elif phase == "reading":
        name = current_material.get("file_name") if isinstance(current_material, dict) else getattr(current_material, "file_name", None)
        material_id = current_material.get("material_id") if isinstance(current_material, dict) else getattr(current_material, "id", None)
        start_event = next((event for event in events
                            if event.event_type == "material.parse_started"
                            and (event.payload_json or {}).get("material_id") == material_id), None)
        if start_event is None:
            # 大文件的逐页事件可能挤出最近 200 条，仍须保持同一解析批次的标识。
            starts = (db.query(BsTaskEvent)
                      .filter(BsTaskEvent.task_id == task.id,
                              BsTaskEvent.event_type == "material.parse_started")
                      .order_by(BsTaskEvent.sequence.desc()))
            start_event = next((event for event in starts
                                if (event.payload_json or {}).get("material_id") == material_id), None)
        run_id = (start_event.payload_json or {}).get("run_id") if start_event else None
        page_event = next((event for event in events
                           if event.event_type == "material.page_progress"
                           and (event.payload_json or {}).get("material_id") == material_id
                           and (run_id is None or (event.payload_json or {}).get("run_id") == run_id)), None)
        page_data = page_event.payload_json or {} if page_event is not None else {}
        current = {
            "key": "material_reading", "label": (
                f"正在补读可疑页面：{name}" if page_data.get("source") in {"retry", "retry_failed"} else
                f"正在处理旋转页面：{name}" if page_data.get("source") == "rotation" else
                f"正在识别材料印章：{name}" if page_data.get("source") == "seal" else
                f"正在读取材料：{name}" if name else "正在读取材料"),
            "current": material_done, "total": material_total,
            "file_name": name, "current_page": page_data.get("current_page"),
            "material_id": material_id, "run_id": run_id,
            "total_pages": page_data.get("total_pages"), "source": page_data.get("source"),
            "done_pages": page_data.get("done_pages"), "total_work_pages": page_data.get("total_work_pages"),
            "remaining_seconds": None,
        }
    elif phase == "completed":
        current = {"key": "completed", "label": "核查已完成，等待人工复核", "current": 1, "total": 1}
    elif phase == "ai_checking":
        label = (f"正在核查：{ai_current['label']}" if ai_current and ai_current.get("label")
                 else "正在进行 AI 辅助核查" if ai_state == "running" else "正在确认 AI 核查状态")
        current = {"key": "ai_checking", "label": label, "current": None, "total": None}
    elif phase == "failed":
        current = {"key": "failed", "label": "部分核查未完成，请重试或人工核实", "current": None, "total": None}
    elif semantic_event is not None:
        payload = semantic_event.payload_json or {}
        current = {
            "key": "contract_semantic", "label": "正在进行合同条款比对",
            "current": payload.get("current"), "total": payload.get("total"),
        }
    else:
        current = {
            "key": current_step.get("step_id", "intelligent_check") if current_step else "intelligent_check",
            "label": current_step.get("title", "正在执行五步智能核查") if current_step else "正在执行五步智能核查",
            "current": check_done, "total": check_total,
        }

    if material_total is None:
        unknown_reason = "等待材料读取总量确认"
    elif check_total is None and not done:
        unknown_reason = "等待五步核查计划冻结"
    else:
        unknown_reason = None

    stages = [
        {
            "key": "material_reading", "label": "材料读取",
            "state": "completed" if material_total is not None and material_done >= material_total else "running" if phase == "reading" else "waiting",
            "completed_units": material_done, "total_units": material_total,
        },
        {
            "key": "intelligent_check", "label": "五步智能核查",
            "state": "failed" if phase == "failed" else "completed" if phase == "completed" else "running" if phase in {"checking", "ai_checking"} else "waiting",
            "completed_units": check_done, "total_units": check_total,
        },
        {
            "key": "finalizing", "label": "结果收口",
            "state": "completed" if phase == "completed" else "waiting",
            "completed_units": 1 if phase == "completed" else 0, "total_units": 1,
        },
    ]
    return {
        "mode": "determinate" if percent is not None else "indeterminate",
        "phase": phase,
        "percent": percent,
        "completed_units": completed_units,
        "total_units": total_units,
        "current_task": current,
        "stages": stages,
        "material_failed": material_failed,
        "unknown_reason": unknown_reason,
        "activity_health": task_activity_health(db, task),
    }


def task_activity_health(db: Session, task: BsTask, *, stale_after_seconds: int = 1800,
                         now: datetime | None = None) -> dict:
    """只读识别运行态是否长时间没有事件，不自动改变任务状态。

    该投影供前端恢复提示和运维审计消费；`stale_candidate` 只表示“需要刷新/重试或人工判断”，
    不能被当作失败或完成。时间采用数据库事件的本地 naive 时间，避免跨时区比较时静默误报。
    """
    event = (db.query(BsTaskEvent)
             .filter(BsTaskEvent.task_id == task.id)
             .order_by(BsTaskEvent.sequence.desc()).first())
    observed_at = now or datetime.now()
    last_event_at = event.created_at if event is not None else None
    age_seconds = None
    if last_event_at is not None:
        if getattr(last_event_at, "tzinfo", None) is not None and observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=last_event_at.tzinfo)
        elif getattr(last_event_at, "tzinfo", None) is None and observed_at.tzinfo is not None:
            last_event_at = last_event_at.replace(tzinfo=observed_at.tzinfo)
        age_seconds = max(0, int((observed_at - last_event_at).total_seconds()))
    terminal_events = {"task.completed", "check.failed"}
    stale_candidate = (
        task.state in {"running", "review"}
        and event is not None
        and event.event_type not in terminal_events
        and age_seconds is not None
        and age_seconds >= stale_after_seconds
    )
    return {
        "stale_candidate": stale_candidate,
        "last_event_type": event.event_type if event else None,
        "last_event_sequence": event.sequence if event else None,
        "last_event_at": last_event_at.isoformat() if last_event_at else None,
        "age_seconds": age_seconds,
        "recovery_action": "refresh_or_retry_or_human_review" if stale_candidate else None,
    }


def store_zip_container(db: Session, task: BsTask, *, file_name: str, content: bytes,
                        role: str = None, subject_id: int = None, packet_id: int = None,
                        material_category: str = None, replacement_target_id: int = None,
                        operator=None, supplement_link: dict = None) -> BsMaterial:
    """保存 ZIP 原包为容器材料（T4.1-T2 §2.3 / 契约 §7.2）。

    **只落盘 + 登记**：不解包、不枚举内部条目、不解析内部 PDF、不跑 OCR、不校验
    内部归属、不产出任何结论。容器 `parse_state` 固定 `stored`（§7.2.4 冻结值），
    既不触发 `parsing_pending` 409 闸，也不代表子材料已展开——「子材料是否已展开」
    与「容器状态」是两件事，后者由 T3 的准备编排转 `done`/`failed`。

    对齐既有 PDF 摄取的三条纪律：`_check_upload_size` 二道防线、完整保留用户传来的
    归属字段、只记「已受理」不返回内部条目信息。
    """
    _check_upload_size(content)
    # 容器原包独占子目录：同名容器重复上传不再互相覆盖（覆盖会静默改变派生来源）
    rel_path = parsing.save_upload(task.id, file_name, content,
                                   subdir="containers/" + uuid.uuid4().hex[:12])
    mat = BsMaterial(
        task_id=task.id, file_name=file_name,
        subject_id=subject_id, packet_id=packet_id,
        material_category=material_category,
        replacement_target_id=replacement_target_id,
        role=role or classify_material(file_name),
        role_confirmed=role is not None, via="upload", path=rel_path,
        size_kb=max(1, round(len(content) / 1024)),
        parse_state=PARSE_STATE_STORED,
    )
    db.add(mat)
    db.flush()
    if supplement_link:
        add_event(db, task.id, "material.supplement_linked",
                  payload={**supplement_link, "material_id": mat.id}, commit=False)
    db.commit()
    db.refresh(mat)
    # 复用既有 material.added（已在前端事件白名单内，上传即有可见反馈）。
    # 这里刻意不产出任何解析/缺件类事件：容器尚未读取，更不是证据。
    add_event(db, task.id, "material.added",
              payload={"file_name": file_name, "role": mat.role, "via": "upload",
                       "container": True, "operator": operator})
    logger.info("[bs] ZIP 容器已保存 task=%s material=%s（待准备阶段展开）", task.id, mat.id)
    return mat


def container_children(db: Session, task: BsTask, container: BsMaterial) -> list:
    """容器的派生材料（按 id 升序，稳定可断言）。"""
    return (db.query(BsMaterial)
            .filter(BsMaterial.task_id == task.id,
                    BsMaterial.container_material_id == container.id)
            .order_by(BsMaterial.id).all())


def _fail_container(db: Session, task: BsTask, container: BsMaterial,
                    message: str, operator=None) -> dict:
    """容器准备失败收口：追加可读降级 + 置 `failed` + 记事件（同一事务）。

    `add_event` 尾部 commit 一并提交容器状态（收口态与事件同事务，与
    `_finish_ingest` 同纪律；分次提交有「轮询见终态但事件尚未可见」的竞态）。
    """
    container.degradations_json = list(container.degradations_json or []) + [
        {"kind": "zip_extract_failed", "message": message}]
    container.parse_state = "failed"
    add_event(db, task.id, "zip.container_failed", payload={
        "container_material_id": container.id,
        "file_name": container.file_name,
        "reason": message,
        "operator": operator,
    })
    logger.info("[bs] ZIP 容器准备失败 task=%s container=%s: %s",
                task.id, container.id, message)
    return {"ok": False, "reason": "zip_extract_failed", "message": message,
            "children": [], "created": 0, "reused": 0, "skipped_non_pdf": 0}


def materialize_zip_children(db: Session, task: BsTask, container: BsMaterial,
                             *, operator=None) -> dict:
    """把 ZIP 容器安全展开为派生 PDF 子材料（T4.1-T2 §2.3，**供 T3 的准备编排调用**）。

    T2 只提供这个窄接口，**不接后台 job**：子材料初始状态固定 `uploaded`（尚未由
    T3 调度解析），本函数不调用 `_finish_ingest`、不并发跑 OCR；容器转 `done` 也由
    T3 统一负责——T2 **不假装准备已完成**。

    幂等（契约 §7.4.2）：子材料按 `(container_material_id, container_entry_path)`
    唯一键复用，重复调用不重复建行，也**不覆盖**已收口的 `done`/`failed` 状态。
    并发互斥不在本函数职责内：由 T3 的持久化 `preparing` 态保证同一容器只有一份
    worker（契约 §7.3.3）；唯一索引是最后一道硬保证。

    失败语义（契约 §7.5.6）：不安全/损坏/零 PDF → 容器追加可读降级并置 `failed`。
    子材料只在**整包安全展开成功后**才创建，因此失败路径不存在半批子材料。

    返回 `{"ok", "reason", "message", "children", "created", "reused",
    "skipped_non_pdf"}`。
    """
    if task is None or container is None or container.task_id != task.id:
        raise LookupError("container material %s not in task" % (container and container.id))
    if container.container_material_id is not None:
        raise ValueError("派生材料不能再作为容器展开")
    if not container.path:
        return _fail_container(db, task, container,
                               "资料包原文件缺失，请删除该资料包后重新上传", operator)

    try:
        result = zip_ingestion.extract_pdf_entries(
            material_abs_path(container.path),
            _container_extract_dir(container),
            max_entries=zip_ingestion.max_entries(),
            max_uncompressed_bytes=zip_ingestion.max_uncompressed_bytes(),
        )
    except zip_ingestion.ZipIngestionError as exc:
        return _fail_container(db, task, container, str(exc), operator)

    created = 0
    reused = 0
    children = []
    # 多文件报审包无法安全假设所有子材料属于同一主体。容器主体仍保留为
    # 上传上下文，但子材料必须逐个确认；单 PDF 包继续沿用历史继承行为。
    inherit_container_subject = len(result.pdfs) == 1
    for pdf in result.pdfs:
        existing = (db.query(BsMaterial)
                    .filter(BsMaterial.task_id == task.id,
                            BsMaterial.container_material_id == container.id,
                            BsMaterial.container_entry_path == pdf.entry_path)
                    .first())
        if existing is not None:
            # 幂等复用：不重复建行，也不覆盖已收口（done/failed）或进行中的状态
            reused += 1
            children.append(existing)
            continue
        child = BsMaterial(
            task_id=task.id,
            subject_id=container.subject_id if inherit_container_subject else None,
            packet_id=container.packet_id if inherit_container_subject else None,
            material_category=container.material_category,
            file_name=_child_display_name(container.file_name, pdf.entry_path),
            role=container.role,
            role_confirmed=container.role_confirmed,
            via=container.via,
            path=os.path.relpath(pdf.absolute_path, _APP_ROOT),
            size_kb=max(1, round(pdf.size_bytes / 1024)),
            parse_state="uploaded",
            container_material_id=container.id,
            container_entry_path=pdf.entry_path,
        )
        db.add(child)
        children.append(child)
        created += 1
    db.commit()
    for child in children:
        db.refresh(child)

    add_event(db, task.id, "zip.container_expanded", payload={
        "container_material_id": container.id,
        "file_name": container.file_name,
        "child_count": len(children),
        "created": created,
        "reused": reused,
        "skipped_non_pdf": result.skipped_non_pdf,
        "operator": operator,
    })
    logger.info("[bs] ZIP 容器已展开 task=%s container=%s 子材料 %s（新增 %s / 复用 %s）",
                task.id, container.id, len(children), created, reused)
    return {"ok": True, "reason": None, "message": None, "children": children,
            "created": created, "reused": reused,
            "skipped_non_pdf": result.skipped_non_pdf}


# ==================== 重跑回执继承（BS-P1-13 / 设计 98 §4.2） ====================
# carry 键与「结论指纹」字段是同一件事的唯一定义点：键用来找旧条，指纹用来判断
# 「结论是否变了」。散落两处时，改一处必漏另一处（AGENTS 纪律 9）。
_CARRY_KEY_FIELDS = ("subject_id", "item_id", "material_id", "page")
_CARRY_CONCLUSION_FIELDS = (
    "status", "severity", "confidence", "message", "quote", "page",
    "missing_json", "evidence_json", "location_mode",
)


def _carry_index(carry: list) -> dict:
    """按主体稳定键归并携带表；旧行缺 subject_id 时留在 NULL 命名空间。"""
    index = {}
    for item in carry or []:
        if not isinstance(item, dict):
            continue
        key = tuple(item.get(field) for field in _CARRY_KEY_FIELDS)
        index.setdefault(key, []).append(item)
    return index


def _inherit_review(finding: BsFinding, carry_index: dict) -> dict | None:
    """回执继承（设计 98 §4.2）：结论未变→旧回执逐字保留；变了→标「原复核意见失效」。

    `at` 取当前时间（ISO8601），`previous` 存旧回执原文（只作留痕，不提供恢复入口）。
    返回值一律经 `_json_safe`：回执可能来自历史 JSON 列的 date/Decimal 兼容形态。
    """
    key = tuple(getattr(finding, field) for field in _CARRY_KEY_FIELDS)
    candidates = carry_index.get(key)
    if not candidates:
        return None  # 新增条目：从未复核开始
    old = candidates.pop(0)  # 同键多条逐条配对，用掉即出队，避免一条旧回执被复用两次
    def _same_value(left, right) -> bool:
        # 结构化证据/缺件的键序不应制造假变化；比较前统一为稳定 JSON。
        if isinstance(left, (dict, list)) or isinstance(right, (dict, list)):
            return json.dumps(_json_safe(left), ensure_ascii=False, sort_keys=True) == \
                json.dumps(_json_safe(right), ensure_ascii=False, sort_keys=True)
        return left == right

    if all(_same_value(getattr(finding, field), old.get(field))
           for field in _CARRY_CONCLUSION_FIELDS):
        return _json_safe(deepcopy(old.get("review_json")))
    return _json_safe({
        "invalidated": {
            "at": datetime.now().isoformat(timespec="seconds"),
            "reason": "conclusion_changed",
            "previous": old.get("review_json"),
        }
    })


def _replace_findings(db: Session, task: BsTask, findings: list, *, commit: bool = True,
                      expected_kinds=None) -> list:
    """重跑替换：按本次产出/调度组的 kind 集合删旧再插（重跑不翻倍）。

    普通入口的 kind 集合从产出结论推导——run_a1_checks 只会删 A1 四 kind，
    天然不碰 completeness；参考组入口额外传 `expected_kinds`，确保本次合法
    产出零行时也能清理该组旧结论，不把上一代结果冒充本代。

    BS-P1-13（设计 98 §4.2）局部重跑：读 `task.options_snapshot_json["rerun"]`——
    - `scope=items`：只保留 `item_id ∈ item_ids` 的产出（其余丢弃不落库），删除也
      只碰「kind ∈ 本次产出 kinds 且 item_id ∈ item_ids」的旧行；
    - `scope=all`：保持原口径（按 kind 全删）；
    - 回执继承：对每条要落库的结论按 `(subject_id, item_id, material_id, page)` 在 carry 里找旧条，
      结论未变→逐字保留旧 review_json；变了→`{"invalidated": {...}}`；找不到→None。
      检查阶段照常跑全部分组（内核粒度到角色级，按 item_id 切只是「跑完后只采纳范围内
      产出」），因此这里必须做范围过滤，而不是省掉计算。

    A-13（B2）：落库前**单点**为每条结论赋 severity/confidence（确定性矩阵，
    severity.py）——集中此处而非散落各 run_*，避免漏项。discrepancy_json 语义为
    「规则值 vs 人工复核回执冲突」留痕，本批无消费场景，故只加列不写值（不造空写入路径）。
    """
    subject_ids = {finding.subject_id for finding in findings
                   if getattr(finding, "subject_id", None) is not None}
    if subject_ids:
        valid_ids = {subject_id for (subject_id,) in db.query(BsSubject.id).filter(
            BsSubject.task_id == task.id, BsSubject.id.in_(subject_ids)).all()}
        if valid_ids != subject_ids:
            raise ValueError("finding subject_id 必须属于同一任务")
    rerun = (task.options_snapshot_json or {}).get("rerun")
    rerun = rerun if isinstance(rerun, dict) else {}
    scoped_ids = None
    if rerun.get("scope") == "items":
        scoped_ids = {item for item in (rerun.get("item_ids") or [])
                      if isinstance(item, str) and item}
    # 「本次产出 kinds」取过滤前的全量产出：某个 kind 若只产出范围外条目，按 item_id
    # 过滤后不会出现在 findings 里，但它本来也不该删任何范围内旧行（双重条件兜底）。
    kinds = {f.kind for f in findings} | set(expected_kinds or ())
    if scoped_ids is not None:
        findings = [f for f in findings if f.item_id in scoped_ids]
    query = (db.query(BsFinding)
             .filter(BsFinding.task_id == task.id, BsFinding.kind.in_(kinds)))
    if scoped_ids is not None:
        query = query.filter(BsFinding.item_id.in_(scoped_ids))
    # 同步移除会话中已加载的旧结论，避免局部重跑后会话继续持有失效对象。
    query.delete(synchronize_session="fetch")
    carry_index = _carry_index(rerun.get("carry") or [])
    for f in findings:
        f.message = bounded_finding_message(f.message)
        f.severity = severity.map_bs_severity(f)
        f.confidence = severity.bs_confidence(f)
        # 结论指纹必须在 message/severity/confidence 归一之后再比对（见上）。
        f.review_json = _inherit_review(f, carry_index)
        db.add(f)
    if commit:
        db.commit()
    else:
        db.flush()
    return findings


def _reference_group_findings(db: Session, task_id: int, group_id: str) -> list[BsFinding]:
    """按参考组读取当前全部结论，供终态链接与 carry 资格共用。"""
    kinds = reference_snapshots.CHECK_GROUPS[group_id]["kinds"]
    return (db.query(BsFinding)
            .filter(BsFinding.task_id == task_id, BsFinding.kind.in_(kinds))
            .order_by(BsFinding.id).all())


def _explain_supplement_certificate_category(findings, materials, associations):
    """只对明确关联到核查项的补件提供类别线索，不推定持证人。"""
    required = {'RC-P11': 'B', 'RC-P12': 'B', 'RC-P13': 'C', 'RC-L11': 'C'}
    for finding in findings:
        expected = required.get(finding.item_id)
        if not expected or finding.status != 'undecidable':
            continue
        other = 'C' if expected == 'B' else 'B'
        for material in materials:
            link = associations.get(material.id) or {}
            linked_items = link.get('item_ids') or [link.get('item_id')]
            if (finding.item_id not in linked_items or link.get('subject_id') != finding.subject_id
                    or material.subject_id != finding.subject_id or material.parse_state != 'done'
                    or material.superseded_by_id is not None or _subject_assessment_issue(material)):
                continue
            for page in material.pages_json or []:
                if not isinstance(page, dict):
                    continue
                number, text = page.get('page'), page.get('text') or ''
                if not isinstance(text, str) or not parsing.material_page_readable(material, number):
                    continue
                match = re.search(r'交安\s*' + other + r'\s*[（(]?\s*\d', text, re.I)
                if not match or re.search(r'交安\s*' + expected + r'\s*[（(]?\s*\d', text, re.I):
                    continue
                finding.message = (f'关联补件第{number}页识别到交安 {other} 证线索，本项要求交安 {expected} 证；'
                                   f'请核对持证人及原件并补充相应证件；{finding.message}')
                finding.evidence_json = list(finding.evidence_json or []) + [{
                    'material_id': material.id, 'page': number, 'quote': match.group(),
                    'location_mode': 'page', 'evidence_kind': 'certificate_category_clue',
                }]
                break


def run_completeness(db: Session, task: BsTask) -> list:
    """A3-01 对表编排：材料须全部 done（start_review 已保证），三态结论落库。"""
    ids = active_subject_ids(db, task)
    if not ids:
        # 历史任务可能仍只有 subject_id=NULL 的完整性结论。没有可确认主体时，
        # 新的主体级内核无从执行；必须在参考组初始化/提交和 replace 之前停止，
        # 否则“本次没有产出”会被误解释为合法空结果并擦除历史结论与人工回执。
        raise knowledge_runs.ReferenceRunSuperseded(
            "请先确认分包主体后再运行要素完整性核查；历史核查结果已保留")
    reference_plan = _prepare_reference_group(db, task, "completeness")
    materials = _rule_materials(db, task)
    subjects = (db.query(BsSubject)
                .filter(BsSubject.task_id == task.id, BsSubject.id.in_(ids))
                .order_by(BsSubject.id).all()) if ids else []
    findings = checklist.run_subject_completeness(task, subjects, materials)
    _explain_supplement_certificate_category(findings, materials, supplement_issue_associations(db, task.id))
    pinning.pin_findings(findings, materials)
    return _persist_reference_group(db, task, reference_plan, findings)


def run_a1_checks(db: Session, task: BsTask) -> list:
    """A1 判定编排（W2 + R05 统一接线）：金额占比 + 负面清单比较 + A1←A3 依赖
    + 第十五条转包三类（T15-01..09）+ 第十六/十七条违法分包 14 条（W16/V16/L17）。

    注意 T15-10（开放条款）已由 `a1_check.run_open_term_registrations` 补登记（BS-P1-11，
    2026-10-22）：它只登记、不参与结论统计，报告里标「仅登记」。

    依赖项（V16-01/L17-02）读已落库的 A3-01 结论；A3 未跑时按拍板口径判不可判定。

    R05 接线口径（冻结记账节见 `a1_check.py` A1-RULE-04，接线前该内核明确"不接入"）：
    - 新内核为纯函数：A3/负面清单结论在本函数内**各算一次并显式传入**，不重复执行上游核查；
    - 新内核的 V16-01/L17-02 与旧 `a1_dependency` 同 item_id → 归并只留一条（新口径更保守），
      取代条数写入事件 payload["superseded"]，不静默丢弃。
    """
    if not task.scope_a1:
        raise ValueError("任务未选择 A1 核查范围")
    reference_plan = _prepare_reference_group(db, task, "a1")
    materials = _rule_materials(db, task)
    a3_findings = (db.query(BsFinding)
                   .filter(BsFinding.task_id == task.id, BsFinding.kind == "completeness")
                   .all())
    negative_findings = a1_check.run_negative_list_check(task, materials)
    # 占比类范围闸（口径修正）：分母是总包合同金额、且业主部署文件二、（六）把「分包合同
    # 总合同额匹配」列为后续功能 → 本期默认关闭（config.json enabled=false，能力保留）。
    # 关闭时不产出 finding，改由 reporting.scope_statements 在纸面上声明范围。
    ratio_findings = []
    if load_config().get("amount_ratio", {}).get("enabled"):
        ratio_findings += a1_check.run_amount_ratio_check(task, materials)
    if load_config().get("workload_ratio", {}).get("enabled"):
        ratio_findings += a1_check.run_workload_ratio_check(task, materials)
    findings = (ratio_findings
                + negative_findings
                + a1_check.run_a3_dependent_checks(task, a3_findings, materials)
                + a1_check.run_transfer_scope_check(task, materials)
                + a1_check.run_transfer_materials_check(task, materials)
                + a1_check.run_transfer_relations_check(task, materials)
                + a1_check.run_illegal_subcontract_check(
                    task, materials, a3_findings=a3_findings,
                    negative_list_findings=negative_findings))
    # BS-P1-11：补登记无内核承载的兜底条款（第十五条（十）T15-10）。放编排层而非塞进
    # 两个既有内核——它们各有「恰好 N 条」形状锁，插入会连带改契约；此处纯追加。
    findings += a1_check.run_open_term_registrations(task, findings)
    findings, superseded = a1_check.supersede_dependency_findings(findings)
    pinning.pin_findings(findings, materials)
    return _persist_reference_group(db, task, reference_plan, findings, extra={"superseded": superseded})


def initialize_reference_review(db: Session, task: BsTask, *, rule_fingerprint: str = None,
                                ai_auto_groups: list[str] | None = None) -> dict:
    """调用方持有任务锁且已flush业务输入；新代及固定依据在业务事务内冻结。"""
    knowledge_runs.clear_reference_run(task)
    review_id = uuid.uuid4().hex
    frozen_rule_fingerprint = rule_fingerprint or knowledge_runs.rule_fingerprint()
    seed = reference_snapshots.build_review_seed(
        task, review_run_id=review_id,
        material_fingerprint=knowledge_runs.material_fingerprint(db, task),
        rule_fingerprint=frozen_rule_fingerprint,
        package=knowledge_runs.load_reference_package(as_of=task.baseline_date))
    seed["ai_auto_enabled"] = True
    seed["ai_auto_groups"] = list(seed["expected_groups"] if ai_auto_groups is None else ai_auto_groups)
    task.options_snapshot_json = {**(task.options_snapshot_json or {}),
        reference_snapshots.REVIEW_POINTER: review_id, reference_snapshots.GROUP_POINTERS: {}}
    add_event(db, task.id, reference_snapshots.REVIEW_EVENT, stage="check", payload=seed, commit=False)
    return seed


def recover_reference_runs(db: Session, task: BsTask) -> int:
    """在任务锁内恢复中断/过期的精确组；从不复活以前的pending。"""
    recovered = 0
    for group_id in reference_snapshots.CHECK_GROUPS:
        run = knowledge_runs.get_reference_run(db, task, group_id=group_id)
        if not run or run["state"] == "completed":
            continue
        if run["state"] == "expired":
            knowledge_runs.restore_reference_run(task, run)
            add_event(db, task.id, "knowledge.reference_failed", stage="check", payload={
                "run_id": run["run_id"], "review_run_id": run.get("review_run_id"), "group_id": group_id,
                "reason": "reference_timeout",
                "message": "上次核查已中断，已保留上一批有效结果，请重新核查"}, commit=False)
            recovered += 1
    return recovered


def _prepare_reference_group(db: Session, task: BsTask, group_id: str) -> dict:
    db.rollback()
    db.refresh(task, with_for_update=True)
    rerun = (task.options_snapshot_json or {}).get("rerun")
    planned_groups = rerun.get("rerun_groups") if isinstance(rerun, dict) else None
    if isinstance(planned_groups, list) and group_id not in planned_groups:
        db.commit()
        raise knowledge_runs.ReferenceRunSuperseded(
            "本组不在本次重跑范围，请刷新页面或重新选择重跑范围")
    if not (task.options_snapshot_json or {}).get(reference_snapshots.REVIEW_POINTER):
        initialize_reference_review(db, task)
    recover_reference_runs(db, task)
    seed = reference_snapshots.get_reference_snapshot(db, task)
    if (not seed or seed.get("material_fingerprint") != knowledge_runs.material_fingerprint(db, task)
            or seed.get("rule_fingerprint") != knowledge_runs.rule_fingerprint()):
        db.commit()
        raise knowledge_runs.ReferenceRunSuperseded("材料或依据版本已变化，请重新核查本任务后再继续")
    current = knowledge_runs.get_reference_run(db, task, group_id=group_id)
    if current and current["state"] == "running":
        db.commit()
        raise knowledge_runs.ReferenceRunSuperseded("本组核查正在执行，请等待本次完成后再试")
    if group_id == "a1":
        upstream = knowledge_runs.get_reference_run(db, task, group_id="completeness")
        if upstream and upstream["state"] == "running":
            db.commit()
            raise knowledge_runs.ReferenceRunSuperseded("完整性核查正在执行，请完成后再进行第二步核查")
    plan = knowledge_runs.prepare_reference_run(db, task, group_id=group_id)
    db.commit()  # 计算/网络阶段不占用任务锁；写回前再核对本组和generation。
    return plan


def _persist_reference_group(db: Session, task: BsTask, reference_plan: dict,
                              findings: list, *, extra: dict = None) -> list:
    # 参考查询之前释放业务事务；返回后重新锁读，旧run不得覆盖完成/重跑后的结果。
    db.rollback()
    db.refresh(task, with_for_update=True)
    knowledge_runs.assert_reference_run_current(db, task, reference_plan, active=False)
    # 计算尚未发布批次，不应消耗在线依据查询租约；确认输入与并发状态后才开始计时。
    reference_plan["deadline_at"] = (datetime.now(timezone.utc) + timedelta(
        seconds=knowledge_runs.REFERENCE_RUN_LEASE_SECONDS)).isoformat()
    knowledge_runs.activate_reference_run(task, reference_plan)
    add_event(db, task.id, "knowledge.reference_started", stage="check", payload=reference_plan)
    try:
        return _finish_reference_run(db, task, reference_plan, findings, extra=extra)
    except knowledge_runs.ReferenceRunSuperseded:
        raise
    except Exception:
        db.rollback()
        db.refresh(task, with_for_update=True)
        # 只恢复本次失败请求的指针；并发新run已经接管时不能替它清理。
        knowledge_runs.restore_reference_run(task, reference_plan)
        add_event(db, task.id, "knowledge.reference_failed", stage="check",
                  payload={"run_id": reference_plan["run_id"], "review_run_id": reference_plan["review_run_id"],
                           "group_id": reference_plan["group_id"], "reason": "execution_error",
                           "message": "本次参考查询未完成，已保留上一批结果，请重新核查"})
        raise


def _finish_reference_run(db: Session, task: BsTask, reference_plan: dict,
                           findings: list, *, extra: dict = None) -> list:
    """激活后的接线事务；调用方负责失败时恢复旧批次指针。"""
    reference_run = knowledge_runs.retrieve_reference_run(db, task, reference_plan)
    db.rollback()
    db.refresh(task, with_for_update=True)
    try:
        knowledge_runs.assert_reference_run_current(db, task, reference_plan)
    except knowledge_runs.ReferenceRunSuperseded:
        knowledge_runs.restore_reference_run(task, {**reference_plan, "previous_run_id": None})
        add_event(db, task.id, "knowledge.reference_discarded", stage="check",
                  payload={"run_id": reference_plan["run_id"], "review_run_id": reference_plan["review_run_id"],
                           "group_id": reference_plan["group_id"], "reason": "inputs_or_run_changed"})
        raise
    persisted_findings = _replace_findings(
        db, task, findings, commit=False,
        expected_kinds=reference_snapshots.CHECK_GROUPS[reference_plan["group_id"]]["kinds"],
    )
    if reference_plan["group_id"] == "completeness":
        dependent = knowledge_runs.get_reference_run(db, task, group_id="a1")
        if (dependent and dependent["state"] == "completed"
                and dependent.get("dependency_fingerprint") != knowledge_runs.dependency_fingerprint(db, "a1", task.id)):
            knowledge_runs.restore_reference_run(task, {**dependent, "previous_run_id": None})
            add_event(db, task.id, "knowledge.reference_invalidated", stage="check", payload={
                "run_id": dependent["run_id"], "review_run_id": dependent.get("review_run_id"),
                "group_id": "a1", "reason": "upstream_changed",
                "message": "完整性核查已更新，请重新执行第二步核查后再收口"}, commit=False)
    group_findings = _reference_group_findings(
        db, task.id, reference_plan["group_id"])
    knowledge_runs.link_reference_findings(reference_run, group_findings)
    rerun = (task.options_snapshot_json or {}).get("rerun") or {}
    if rerun.get("scope") == "items":
        recomputed = set(rerun.get("item_ids") or [])
        reference_run["execution_mode"] = "partial"
        reference_run["recomputed_item_ids"] = [
            item_id for item_id in rerun.get("item_ids") or []
            if rule_item_catalog.item_group_catalog().get(item_id) == reference_plan["group_id"]]
        reference_run["carried_forward_finding_ids"] = [
            finding.id for finding in group_findings if finding.item_id not in recomputed]
    add_event(db, task.id, knowledge_runs.RUN_EVENT, stage="check",
              payload=reference_run, commit=False)
    add_event(db, task.id, "check.completed", payload={
        "kind": reference_plan["group_id"],
        "counts": Counter(f.status for f in persisted_findings),
        **(extra or {}),
        "reference_run_id": reference_run["run_id"],
    }, commit=False)
    db.commit()
    return persisted_findings


def run_signature_chain(db: Session, task: BsTask) -> list:
    """A3-02 签字链双轨编排（W3-2）：法定链/集团链各判 + 先报批后实施时序。"""
    if not task.scope_a3:
        raise ValueError("任务未选择 A3 核查范围")
    reference_plan = _prepare_reference_group(db, task, "signature_chain")
    materials = _rule_materials(db, task)
    ids = active_subject_ids(db, task)
    subjects = (db.query(BsSubject)
                .filter(BsSubject.task_id == task.id, BsSubject.id.in_(ids))
                .order_by(BsSubject.id).all()) if ids else []
    confirmed_ids = {subject.id for subject in subjects if subject_reviews.subject_is_confirmed(subject)}
    findings = signature_chain.run_signature_chain_checks(
        task, materials, require_assigned=True, allowed_subject_ids=confirmed_ids)
    pinning.pin_findings(findings, materials)
    return _persist_reference_group(db, task, reference_plan, findings)


def _run_subject_checks(db: Session, task: BsTask, materials, check, **kwargs) -> list:
    ids = active_subject_ids(db, task)
    subjects = (db.query(BsSubject).filter(BsSubject.task_id == task.id,
                                          BsSubject.id.in_(ids)).order_by(BsSubject.id).all()) if ids else []
    return subject_reviews.run_subject_checks(task, subjects, materials, check, **kwargs)


def run_cert_validity(db: Session, task: BsTask, *, persist: bool = True) -> list:
    """A2 批次1（拍板：A2 并入报审核查链路）：证件有效期核查 + 注册单位一致性核查。

    复用 OC validity 引擎（只 import 不改现役）；挂 A3 范围闸——证件材料
    物理上在报审附件清单内，未选 A3 时无核查对象。

    ★ 设计 80 B-3：现场指挥部 2025.6.20 要求三是**三件事**（注册在本单位 / 执业范围 /
       在有效期内）。原先只做「有效期内」，导致**别家公司的资质证书附在包里也判有效**。
       本函数现在同时跑两条内核，共用同一 kind：
       - `a2_check.run_cert_validity_check`（A2-02 有效期）
       - `a2_check.run_cert_registered_to_check`（A2-03 注册单位一致性）
       共用一个 kind ＝报告分节、前端注册、收口闸（`_SCOPE_REQUIRED_KINDS`）都不用改，
       两条 item_id 在报告里各自成行、各自可复核。
    """
    if not task.scope_a3:
        raise ValueError("任务未选择 A3 核查范围")
    materials = _rule_materials(db, task)
    findings = _run_subject_checks(
        db, task, materials,
        lambda current_task, owned: a2_check.run_cert_validity_check(
            current_task, owned, baseline=current_task.baseline_date),
        item_id=a2_check.ITEM_ID, kind="cert_validity", source=a2_check.SOURCE)
    findings += _run_subject_checks(
        db, task, materials, a2_check.run_cert_registered_to_check,
        item_id=a2_check.ITEM_REG, kind="cert_validity", source=a2_check.SOURCE_R,
        use_expected_company=True)
    findings += _run_subject_checks(
        db, task, materials, certificate_number_check.run_b_certificate_number_check,
        item_id=certificate_number_check.ITEM_ID, kind="cert_validity",
        source=certificate_number_check.SOURCE, allow_empty=True)
    findings += _run_subject_checks(
        db, task, materials, identity_observation.run_identity_observations,
        item_id=identity_observation.ITEM_ID, kind="cert_validity",
        source=identity_observation.SOURCE, allow_empty=True)
    if not persist:
        return findings
    _replace_findings(db, task, findings)
    add_event(db, task.id, "check.completed", payload={
        "kind": "cert_validity",
        "counts": Counter(f.status for f in findings),
    })
    return findings


def run_person_consistency(db: Session, task: BsTask, *, persist: bool = True) -> list:
    """A2 批次2：社保资料核查（覆盖口径：社保信息须覆盖主要管理人员；缴费时长待集团 B2 答复）。"""
    if not task.scope_a3:
        raise ValueError("任务未选择 A3 核查范围")
    materials = _rule_materials(db, task)
    findings = _run_subject_checks(
        db, task, materials, a2_check.run_person_consistency_check,
        item_id=a2_check.ITEM_UNIT, kind="person_consistency", source=a2_check.SOURCE_P,
        use_expected_company=True)
    if not persist:
        return findings
    _replace_findings(db, task, findings)
    add_event(db, task.id, "check.completed", payload={
        "kind": "person_consistency",
        "counts": Counter(f.status for f in findings),
    })
    return findings


def run_qualification_match(db: Session, task: BsTask, *, persist: bool = True) -> list:
    """A2 批次3：资质匹配（词表保守口径，命中只提示不定性）。"""
    if not task.scope_a3:
        raise ValueError("任务未选择 A3 核查范围")
    materials = _rule_materials(db, task)
    findings = _run_subject_checks(
        db, task, materials, a2_check.run_qualification_match_check,
        item_id=a2_check.ITEM_QUAL, kind="qualification_match", source=a2_check.SOURCE_Q)
    if not persist:
        return findings
    _replace_findings(db, task, findings)
    add_event(db, task.id, "check.completed", payload={
        "kind": "qualification_match",
        "counts": Counter(f.status for f in findings),
    })
    return findings


class AiAdvisoryInProgress(AiContextChanged):
    """同一结论的辅助核查已在执行。"""


def _latest_ai_advisory_event(db, task_id, run_id, finding_id):
    return (db.query(BsTaskEvent).filter(BsTaskEvent.task_id == task_id,
        BsTaskEvent.event_type.in_(("check.ai_advisory_started", "check.ai_advisory_completed", "check.ai_advisory_failed")),
        BsTaskEvent.payload_json["run_id"].as_string() == run_id,
        BsTaskEvent.payload_json["finding_id"].as_integer() == finding_id)
        .order_by(BsTaskEvent.sequence.desc()).first())


def ai_advisory_pending(event) -> bool:
    return ai_advisory_snapshots.advisory_pending(event)


def ai_checkpoint_client_fingerprint(client) -> str | None:
    """仅存带服务端密钥的摘要，不保存或暴露端点、模型及凭据。"""
    values = [getattr(client, key, None) for key in ("base_url", "model", "api_key")]
    if any(not isinstance(value, str) or not value for value in values):
        return None
    values.append(bool(getattr(client, "enable_thinking", False)))
    if getattr(client, "provider", "legacy") == "moma":
        values.append("moma")
    payload = json.dumps(["bs-ai-checkpoint-client-v1", *values], ensure_ascii=False, separators=(",", ":"))
    secret = get_settings().SECRET_KEY.encode("utf-8")
    return hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def current_ai_advisory_pending(db: Session, task: BsTask) -> bool:
    """按当前核查代的每项最新事件判断，不被历史租约阻塞。"""
    from . import background_ai_review
    if background_ai_review.running_job(db, task):
        return True
    current_runs = {run_id for group in reference_snapshots.CHECK_GROUPS
                    if (run_id := knowledge_runs.group_pointer(task, group))}
    if not current_runs:
        return False
    a1_run_id = knowledge_runs.group_pointer(task, "a1")
    if a1_run_id and _negative_candidate_pending(_latest_negative_candidate_event(db, task.id, a1_run_id)):
        return True
    events = (db.query(BsTaskEvent).filter(BsTaskEvent.task_id == task.id,
        BsTaskEvent.event_type.in_(("check.ai_advisory_started", "check.ai_advisory_completed", "check.ai_advisory_failed")),
        BsTaskEvent.payload_json["run_id"].as_string().in_(current_runs))
        .order_by(BsTaskEvent.sequence.desc()).all())
    seen = set()
    for event in events:
        payload = event.payload_json or {}
        key = (payload.get("run_id"), payload.get("finding_id"))
        if key in seen:
            continue
        seen.add(key)
        if ai_advisory_pending(event):
            return True
    return False


def _record_ai_advisory_usage(bind, *, usage_batches, user_id, user_type, conversation_id, task_id, model_code):
    recorded = True
    for raw in usage_batches:
        with SessionLocal(bind=bind) as billing_db:
            try:
                token_service.record(db=billing_db, user_id=int(user_id or 0), user_type=user_type or "web",
                    conversation_id=conversation_id, input_count=None, output_count=None, token_count=None,
                    raw_usage=raw, model_code=model_code, source="plus:blockchain-supervision",
                    stage="ai_advisory", billing_scene="subcontract_review", scope_type="bs_task", scope_id=str(task_id))
                billing_db.commit()
            except Exception:
                billing_db.rollback()
                recorded = False
                logger.warning("[bs] AI 辅助核查用量记账失败 task=%s", task_id, exc_info=True)
    return recorded


def _mark_ai_usage_failure(result):
    message = "本次模型用量记录失败，请联系管理员核对用量和费用；核查建议仍需人工复核"
    if message not in result.setdefault("limitations", []):
        result["limitations"].append(message)


async def run_linked_ai_advisory(db: Session, task: BsTask, user, *, group_id: str,
                                 finding_id: int, client=None) -> dict:
    """逐项生成并单独留痕；固定规则和人工复核不被改写。"""
    task_id, user_id, user_type, conversation_id = task.id, task.created_by, task.created_by_type, task.conversation_id
    model_code = effective_model_code_or_default(db, "bs_task", task_id)
    validation_version = ai_review_batch.CHECKPOINT_SCHEMA_VERSION
    bind = db.get_bind()
    db.rollback()
    request_id = uuid.uuid4().hex
    with SessionLocal(bind=bind) as reservation_db:
        current_task = reservation_db.query(BsTask).filter_by(id=task_id).with_for_update().one_or_none()
        if current_task is None:
            raise AiContextChanged("核查任务不存在")
        run = knowledge_runs.get_reference_run(reservation_db, current_task, group_id=group_id)
        if not run:
            raise AiContextChanged("当前参考运行不存在")
        assert_ai_context_current(reservation_db, current_task, run, user)
        previous = _latest_ai_advisory_event(reservation_db, task_id, run["run_id"], finding_id)
        if previous and previous.event_type == "check.ai_advisory_completed":
            data = previous.payload_json or {}
            if (data.get("validation_version") == validation_version and data.get("model_code") == model_code
                    and (data.get("execution_status") == "completed" or ("execution_status" not in data and data.get("proposals")))):
                return {"proposals": deepcopy(data.get("proposals") or []),
                        "limitations": deepcopy(data.get("limitations") or []),
                        "execution_status": data.get("execution_status", "completed")}
        if ai_advisory_pending(previous):
            raise AiAdvisoryInProgress("该项辅助核查正在进行，请稍后刷新")
        add_event(reservation_db, task_id, "check.ai_advisory_started", stage="check", payload={
            "run_id": run["run_id"], "group_id": group_id, "finding_id": finding_id, "request_id": request_id,
            "validation_version": validation_version,
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat()}, commit=False)
        reservation_db.commit()
    usage_record_failed = False

    def collect_usage(raw):
        nonlocal usage_record_failed
        if not _record_ai_advisory_usage(bind, usage_batches=[raw if isinstance(raw, dict) else {}],
                user_id=user_id, user_type=user_type, conversation_id=conversation_id,
                task_id=task_id, model_code=model_code):
            usage_record_failed = True

    def save(db, current_task, run, target_id, result):
        latest = _latest_ai_advisory_event(db, current_task.id, run["run_id"], target_id)
        if not latest or latest.event_type != "check.ai_advisory_started" or (latest.payload_json or {}).get("request_id") != request_id:
            raise AiContextChanged("辅助核查请求已被替代")
        if usage_record_failed:
            _mark_ai_usage_failure(result)
        add_event(db, current_task.id, "check.ai_advisory_completed", stage="check", payload={
            "run_id": run["run_id"], "review_run_id": run.get("review_run_id"), "group_id": group_id,
            "finding_id": target_id, "request_id": request_id, "model_code": model_code,
            "validation_version": validation_version, **result}, commit=False)

    try:
        with use_model(model_code):
            result = await ai_guarded_review.run_guarded_review(
                session_factory=lambda: SessionLocal(bind=bind), task_id=task_id, group_id=group_id,
                finding_id=finding_id, user=user, client=client or client_from_env(model_code=model_code),
                material_loader=_rule_materials, on_usage=collect_usage, save_result=save)
            if usage_record_failed:
                _mark_ai_usage_failure(result)
            return result
    except Exception:
        with SessionLocal(bind=bind) as failure_db:
            locked = failure_db.query(BsTask).filter_by(id=task_id).with_for_update().one_or_none()
            if locked:
                latest = _latest_ai_advisory_event(failure_db, task_id, run["run_id"], finding_id)
                if latest and latest.event_type == "check.ai_advisory_started" and (latest.payload_json or {}).get("request_id") == request_id:
                    add_event(failure_db, task_id, "check.ai_advisory_failed", stage="check", payload={
                        "run_id": run["run_id"], "group_id": group_id, "finding_id": finding_id, "request_id": request_id,
                        "validation_version": validation_version}, commit=False)
                    failure_db.commit()
        raise


def _latest_negative_candidate_event(db, task_id: int, run_id: str):
    return (db.query(BsTaskEvent).filter(BsTaskEvent.task_id == task_id,
        BsTaskEvent.event_type.in_(("check.negative_candidates_started", "check.negative_candidates_finished",
                                    "check.negative_candidates_failed")),
        BsTaskEvent.payload_json["run_id"].as_string() == run_id)
        .order_by(BsTaskEvent.sequence.desc()).first())


def sync_candidate_review_records(db: Session, task: BsTask, reviewable: list, previous_records: list,
                                  materials: list, *, unlocated: list = ()) -> list[dict]:
    """在调用方事务内同步当前代候选；调用方负责锁、授权与事件同事务提交。"""
    if not isinstance(previous_records, list):
        raise AiContextChanged("候选复核记录不完整")
    previous = {}
    previous_ids = set()
    for record in previous_records:
        if (not isinstance(record, dict) or not isinstance(record.get("key"), str)
                or type(record.get("finding_id")) is not int or record["finding_id"] < 1
                or record["key"] in previous or record["finding_id"] in previous_ids):
            raise AiContextChanged("候选复核记录身份不唯一")
        previous[record["key"]] = record
        previous_ids.add(record["finding_id"])
    material_map = {material.id: material for material in materials
                    if getattr(material, "task_id", None) == task.id}
    prepared = negative_candidate_plan.make_candidate_review_findings(task_id=task.id, reviewable=reviewable)
    prepared.extend(unlocated)
    if len({key for key, _, _ in prepared}) != len(prepared):
        raise AiContextChanged("候选复核记录身份不唯一")
    for _, finding, _ in prepared:
        for ref in finding.evidence_json or []:
            material = material_map.get(ref["material_id"])
            if material is None or getattr(material, "subject_id", None) != finding.subject_id:
                raise AiContextChanged("候选材料或主体已变化")
    pinning.pin_findings([finding for _, finding, _ in prepared], list(material_map.values()))
    for _, finding, _ in prepared:
        finding.severity = severity.map_bs_severity(finding)
        finding.confidence = severity.bs_confidence(finding)
    new_keys = {key for key, _, _ in prepared}
    prior_rows = {}
    for key, record in previous.items():
        old = db.get(BsFinding, record["finding_id"])
        if old is None or old.task_id != task.id or old.kind != "negative_candidate":
            raise AiContextChanged("候选复核记录已变化")
        if key not in new_keys and old.review_json:
            raise AiContextChanged("已人工处理的候选发生变化，请重新核查并保留历史版本")
        prior_rows[key] = old
    for key, old in prior_rows.items():
        if key not in new_keys:
            db.delete(old)
    result = []
    for key, finding, rule_refs in prepared:
        if key in prior_rows:
            old = prior_rows[key]
            fields = ("subject_id", "item_id", "kind", "status", "message", "material_id", "page", "quote",
                      "source", "evidence_json", "location_mode", "severity", "confidence")
            if (any(getattr(old, field) != getattr(finding, field) for field in fields)
                    or previous[key].get("rule_evidence") != rule_refs):
                raise AiContextChanged("候选复核内容已变化")
            finding = old
        else:
            db.add(finding)
            db.flush()
        result.append({"key": key, "finding_id": finding.id, "rule_evidence": deepcopy(rule_refs)})
    return result


def _negative_candidate_pending(event) -> bool:
    if not event or event.event_type != "check.negative_candidates_started":
        return False
    try:
        deadline = datetime.fromisoformat((event.payload_json or {})["expires_at"])
    except (KeyError, TypeError, ValueError):
        return True
    return deadline.tzinfo is None or deadline > datetime.now(timezone.utc)


async def run_negative_candidate_ai_advisory(db: Session, task: BsTask, user, *, max_batches: int,
                                             expected_run_id: str, client=None, retry: bool = False) -> dict:
    """任务级互斥与用量护栏；仅保存内部候选，不直接产生业务结论。"""
    if type(max_batches) is not int or max_batches < 1 or not isinstance(expected_run_id, str) or not expected_run_id:
        raise ValueError("候选核查预算或运行标识无效")
    task_id, user_id, user_type, conversation_id = task.id, task.created_by, task.created_by_type, task.conversation_id
    model_code = effective_model_code_or_default(db, "bs_task", task_id)
    validation_version, bind, request_id = ai_review_batch.CHECKPOINT_SCHEMA_VERSION, db.get_bind(), uuid.uuid4().hex
    db.rollback()
    with SessionLocal(bind=bind) as reservation_db:
        current = reservation_db.query(BsTask).filter_by(id=task_id).with_for_update().one_or_none()
        if current is None:
            raise AiContextChanged("核查任务不存在")
        run = knowledge_runs.get_reference_run(reservation_db, current, group_id="a1")
        if not run or run.get("run_id") != expected_run_id:
            raise AiContextChanged("核查运行已变化")
        assert_ai_context_current(reservation_db, current, run, user, require_references=False)
        previous = _latest_negative_candidate_event(reservation_db, task_id, run["run_id"])
        data = (previous.payload_json or {}) if previous else {}
        if _negative_candidate_pending(previous):
            raise AiAdvisoryInProgress("负面清单辅助核查正在进行，请稍后刷新")
        prepared = negative_candidate_plan.prepare_candidate_batch_inputs(
            task_id=task_id, run=run, subjects=reservation_db.query(BsSubject).filter_by(task_id=task_id).all(),
            materials=_rule_materials(reservation_db, current),
            findings=reservation_db.query(BsFinding).filter_by(task_id=task_id).all(),
            current_review_run_id=(current.options_snapshot_json or {}).get(reference_snapshots.REVIEW_POINTER),
            current_material_fingerprint=knowledge_runs.material_fingerprint(reservation_db, current),
            current_rule_fingerprint=knowledge_runs.rule_fingerprint())
        last_finished = (reservation_db.query(BsTaskEvent).filter(
            BsTaskEvent.task_id == task_id,
            BsTaskEvent.event_type == "check.negative_candidates_finished",
            BsTaskEvent.payload_json["run_id"].as_string() == run["run_id"])
            .order_by(BsTaskEvent.sequence.desc()).first())
        previous_records = deepcopy((last_finished.payload_json or {}).get("finding_records", [])) if last_finished else []
        if previous and not retry:
            if (previous.event_type == "check.negative_candidates_finished" and data.get("model_code") == model_code
                    and data.get("validation_version") == validation_version and isinstance(data.get("result"), dict)):
                if data.get("model_invoked"):
                    try:
                        with use_model(model_code):
                            check_client = client if client is not None else client_from_env(model_code=model_code)
                            check_fingerprint = ai_checkpoint_client_fingerprint(check_client)
                    except Exception:
                        check_fingerprint = None
                    if not check_fingerprint or check_fingerprint != data.get("client_fingerprint"):
                        return {"candidates": [], "state": "failed", "reason": "model_changed_retry_required"}
                cached_result = deepcopy(data["result"])
                cached_result["reviewable_proposals"] = negative_candidate_plan.reviewable_candidate_proposals(
                    prepared, cached_result)
                unlocated = negative_candidate_plan.make_unlocated_scope_findings(
                    task_id=current.id, prepared=prepared, result=cached_result, run=run)
                records = sync_candidate_review_records(reservation_db, current,
                    cached_result["reviewable_proposals"], previous_records, _rule_materials(reservation_db, current),
                    unlocated=unlocated)
                cached_result["finding_records"] = records
                if "finding_records" not in data or records != data["finding_records"]:
                    add_event(reservation_db, task_id, "check.negative_candidates_finished", stage="check",
                        payload={**deepcopy(data), "result": deepcopy(cached_result), "finding_records": records},
                        commit=False)
                    reservation_db.commit()
                return cached_result
            return {"candidates": [], "state": "failed", "reason": "retry_required"}
        batches = ai_review_batch.plan_review_batches(prepared["items"])["batches"]
        needs_model = bool(batches) and len(batches) <= max_batches
        add_event(reservation_db, task_id, "check.negative_candidates_started", stage="check", payload={
            "run_id": run["run_id"], "review_run_id": run.get("review_run_id"), "request_id": request_id,
            "validation_version": validation_version,
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()}, commit=False)
        reservation_db.commit()

    usage_record_failed = False
    saved_records = []
    def collect_usage(raw):
        nonlocal usage_record_failed
        if not _record_ai_advisory_usage(bind, usage_batches=[raw if isinstance(raw, dict) else {}], user_id=user_id,
            user_type=user_type, conversation_id=conversation_id, task_id=task_id, model_code=model_code):
            usage_record_failed = True

    def owned(session, current_run):
        latest = _latest_negative_candidate_event(session, task_id, current_run["run_id"])
        if not latest or (latest.payload_json or {}).get("request_id") != request_id or not _negative_candidate_pending(latest):
            raise AiContextChanged("负面清单辅助核查已过期或被替代")

    def batch_progress(current_run, index, total, status, failure_categories=None):
        with SessionLocal(bind=bind) as progress_db:
            locked = progress_db.query(BsTask).filter_by(id=task_id).with_for_update().one_or_none()
            if locked is None:
                raise AiContextChanged("核查任务不存在")
            assert_ai_context_current(progress_db, locked, current_run, user)
            owned(progress_db, current_run)
            add_event(progress_db, task_id, "check.ai_batch_progress", stage="check", payload={
                "group_id": "a1", "category": "negative_candidates", "batch_index": index,
                "batch_total": total, "status": status, "failure_categories": failure_categories or [],
                "run_id": current_run["run_id"], "request_id": request_id}, commit=False)
            progress_db.commit()

    def renew(current_run):
        with SessionLocal(bind=bind) as lease_db:
            locked = lease_db.query(BsTask).filter_by(id=task_id).with_for_update().one_or_none()
            if locked is None:
                raise AiContextChanged("核查任务不存在")
            assert_ai_context_current(lease_db, locked, current_run, user)
            owned(lease_db, current_run)
            add_event(lease_db, task_id, "check.negative_candidates_started", stage="check", payload={
                "run_id": current_run["run_id"], "review_run_id": current_run.get("review_run_id"),
                "request_id": request_id, "validation_version": validation_version,
                "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()}, commit=False)
            lease_db.commit()

    def save(save_db, current, current_run, result):
        nonlocal saved_records
        owned(save_db, current_run)
        if usage_record_failed:
            for candidate in result["candidates"]:
                _mark_ai_usage_failure(candidate)
        result["reviewable_proposals"] = negative_candidate_plan.reviewable_candidate_proposals(prepared, result)
        unlocated = negative_candidate_plan.make_unlocated_scope_findings(
            task_id=current.id, prepared=prepared, result=result, run=current_run)
        saved_records = sync_candidate_review_records(save_db, current, result["reviewable_proposals"],
                                                       previous_records, _rule_materials(save_db, current),
                                                       unlocated=unlocated)
        result["finding_records"] = deepcopy(saved_records)
        add_event(save_db, current.id, "check.negative_candidates_finished", stage="check", payload={
            "run_id": current_run["run_id"], "review_run_id": current_run.get("review_run_id"),
            "request_id": request_id, "model_code": model_code, "validation_version": validation_version,
            "model_invoked": needs_model, "client_fingerprint": fingerprint, "result": result,
            "finding_records": deepcopy(saved_records)}, commit=False)

    def load_checkpoint(current_run, batch_key):
        with SessionLocal(bind=bind) as checkpoint_db:
            event = (checkpoint_db.query(BsTaskEvent).filter(
                BsTaskEvent.task_id == task_id, BsTaskEvent.event_type == "check.negative_candidate_checkpoint",
                BsTaskEvent.payload_json["run_id"].as_string() == current_run["run_id"],
                BsTaskEvent.payload_json["batch_key"].as_string() == batch_key)
                .order_by(BsTaskEvent.sequence.desc()).first())
            return deepcopy((event.payload_json or {}).get("results")) if event else None

    def save_checkpoint(checkpoint_db, current, current_run, batch_key, results):
        owned(checkpoint_db, current_run)
        if usage_record_failed:
            for result in results:
                _mark_ai_usage_failure(result)
        add_event(checkpoint_db, current.id, "check.negative_candidate_checkpoint", stage="check", payload={
            "run_id": current_run["run_id"], "batch_key": batch_key, "request_id": request_id,
            "results": results}, commit=False)

    try:
        with use_model(model_code):
            actual_client = (client if client is not None else client_from_env(model_code=model_code)) if needs_model else None
            fingerprint = ai_checkpoint_client_fingerprint(actual_client) if actual_client is not None else None
            result = await ai_guarded_review.run_guarded_negative_candidates(
                session_factory=lambda: SessionLocal(bind=bind), task_id=task_id, user=user, client=actual_client,
                material_loader=_rule_materials, max_batches=max_batches, on_usage=collect_usage,
                before_batch=renew, save_result=save, load_checkpoint=load_checkpoint,
                save_checkpoint=save_checkpoint, on_batch_progress=batch_progress,
                checkpoint_namespace=f"{validation_version}:{model_code}:{fingerprint or request_id}")
        if usage_record_failed:
            for candidate in result["candidates"]:
                _mark_ai_usage_failure(candidate)
        result["reviewable_proposals"] = negative_candidate_plan.reviewable_candidate_proposals(prepared, result)
        result["finding_records"] = saved_records
        return result
    except Exception:
        with SessionLocal(bind=bind) as failure_db:
            locked = failure_db.query(BsTask).filter_by(id=task_id).with_for_update().one_or_none()
            latest = _latest_negative_candidate_event(failure_db, task_id, run["run_id"]) if locked else None
            if latest and latest.event_type == "check.negative_candidates_started" and (latest.payload_json or {}).get("request_id") == request_id:
                add_event(failure_db, task_id, "check.negative_candidates_failed", stage="check", payload={
                    "run_id": run["run_id"], "request_id": request_id, "validation_version": validation_version}, commit=False)
                failure_db.commit()
        raise


async def run_linked_batch_ai_advisory(db: Session, task: BsTask, user, *, group_id: str,
                                      finding_ids: list[int], client=None, resume_partial: bool = False,
                                      expected_run_id: str | None = None, retry_failed: bool = True) -> list[dict]:
    """按项共用单项租约，分批建议单独留痕；固定结论与人工处置不变。"""
    if not finding_ids or any(type(value) is not int for value in finding_ids) or len(set(finding_ids)) != len(finding_ids):
        raise ValueError("核查结论标识无效或重复")
    task_id, user_id, user_type, conversation_id = task.id, task.created_by, task.created_by_type, task.conversation_id
    model_code = effective_model_code_or_default(db, "bs_task", task_id)
    validation_version = ai_review_batch.CHECKPOINT_SCHEMA_VERSION
    bind = db.get_bind()
    db.rollback()
    request_id = uuid.uuid4().hex
    cached, reserved = {}, []
    with SessionLocal(bind=bind) as reservation_db:
        current = reservation_db.query(BsTask).filter_by(id=task_id).with_for_update().one_or_none()
        if current is None:
            raise AiContextChanged("核查任务不存在")
        run = knowledge_runs.get_reference_run(reservation_db, current, group_id=group_id)
        if not run:
            raise AiContextChanged("当前参考运行不存在")
        if expected_run_id is not None and run.get("run_id") != expected_run_id:
            raise AiContextChanged("核查运行已变化")
        assert_ai_context_current(reservation_db, current, run, user)
        if not set(finding_ids).issubset(set(run.get("finding_ids") or [])):
            raise AiContextChanged("核查结论不属于当前运行")
        for finding_id in finding_ids:
            previous = _latest_ai_advisory_event(reservation_db, task_id, run["run_id"], finding_id)
            data = (previous.payload_json or {}) if previous else {}
            if (previous and previous.event_type == "check.ai_advisory_completed"
                    and data.get("model_code") == model_code
                    and data.get("validation_version") == validation_version
                    and (data.get("execution_status") == "completed"
                         or (data.get("execution_status") == "partial"
                             and (not resume_partial or data.get("reviewable_partial") is True))
                         or (data.get("execution_status") == "not_run"
                             and data.get("reviewable_not_run") is True)
                         or ("execution_status" not in data and data.get("proposals")))):
                cached[finding_id] = {key: deepcopy(data[key]) for key in (
                    "finding_id", "proposals", "limitations", "execution_status", "batches_total", "batches_completed", "reviewable_partial", "reviewable_not_run",
                    "model_proposal_count", "rejection_reasons", "failure_categories") if key in data}
                cached[finding_id].setdefault("execution_status", "completed")
            elif ai_advisory_pending(previous):
                raise AiAdvisoryInProgress("部分核查项的辅助核查正在进行，请稍后刷新")
            elif (previous and not retry_failed and data.get("validation_version") == validation_version
                  and (previous.event_type == "check.ai_advisory_failed"
                       or (previous.event_type == "check.ai_advisory_completed"
                           and data.get("execution_status") in {"failed", "not_run"}))):
                cached[finding_id] = {"finding_id": finding_id, "proposals": [],
                    "limitations": deepcopy(data.get("limitations") or ["上次辅助核查未完成，请人工核实或重试"]),
                    "execution_status": data.get("execution_status") if data.get("execution_status") in {"failed", "not_run"} else "failed",
                    "reviewable_not_run": data.get("reviewable_not_run") is True}
            else:
                reserved.append(finding_id)
        for finding_id in reserved:
            add_event(reservation_db, task_id, "check.ai_advisory_started", stage="check", payload={
                "run_id": run["run_id"], "group_id": group_id, "finding_id": finding_id, "request_id": request_id,
                "validation_version": validation_version,
                "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()}, commit=False)
        reservation_db.commit()
    if not reserved:
        return [cached[finding_id] for finding_id in finding_ids]
    usage_record_failed = False
    def collect_usage(raw):
        nonlocal usage_record_failed
        if not _record_ai_advisory_usage(bind, usage_batches=[raw if isinstance(raw, dict) else {}], user_id=user_id,
            user_type=user_type, conversation_id=conversation_id, task_id=task_id, model_code=model_code):
            usage_record_failed = True

    def owned(db, current_run):
        for finding_id in reserved:
            latest = _latest_ai_advisory_event(db, task_id, current_run["run_id"], finding_id)
            if (not latest or (latest.payload_json or {}).get("request_id") != request_id
                    or not ai_advisory_pending(latest)):
                raise AiContextChanged("辅助核查请求已过期或被替代")

    def renew(current_run):
        with SessionLocal(bind=bind) as lease_db:
            locked = lease_db.query(BsTask).filter_by(id=task_id).with_for_update().one_or_none()
            if locked is None:
                raise AiContextChanged("核查任务不存在")
            assert_ai_context_current(lease_db, locked, current_run, user)
            owned(lease_db, current_run)
            for finding_id in reserved:
                add_event(lease_db, task_id, "check.ai_advisory_started", stage="check", payload={
                    "run_id": current_run["run_id"], "group_id": group_id, "finding_id": finding_id, "request_id": request_id,
                    "validation_version": validation_version,
                    "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()}, commit=False)
            lease_db.commit()

    def batch_progress(current_run, index, total, status, failure_categories=None):
        with SessionLocal(bind=bind) as progress_db:
            locked = progress_db.query(BsTask).filter_by(id=task_id).with_for_update().one_or_none()
            if locked is None:
                raise AiContextChanged("核查任务不存在")
            assert_ai_context_current(progress_db, locked, current_run, user)
            owned(progress_db, current_run)
            add_event(progress_db, task_id, "check.ai_batch_progress", stage="check", payload={
                "group_id": group_id, "batch_index": index, "batch_total": total,
                "status": status, "failure_categories": failure_categories or [], "run_id": current_run["run_id"], "request_id": request_id}, commit=False)
            progress_db.commit()

    def save(save_db, current, current_run, results):
        owned(save_db, current_run)
        for result in results:
            if usage_record_failed:
                _mark_ai_usage_failure(result)
            add_event(save_db, current.id, "check.ai_advisory_completed", stage="check", payload={
                "run_id": current_run["run_id"], "review_run_id": current_run.get("review_run_id"),
                "group_id": group_id, "request_id": request_id, "model_code": model_code,
                "validation_version": validation_version, **result}, commit=False)

    def load_checkpoint(current_run, batch_key):
        with SessionLocal(bind=bind) as checkpoint_db:
            event = (checkpoint_db.query(BsTaskEvent).filter(
                BsTaskEvent.task_id == task_id, BsTaskEvent.event_type == "check.ai_batch_checkpoint",
                BsTaskEvent.payload_json["run_id"].as_string() == current_run["run_id"],
                BsTaskEvent.payload_json["batch_key"].as_string() == batch_key)
                .order_by(BsTaskEvent.sequence.desc()).first())
            return deepcopy((event.payload_json or {}).get("results")) if event else None

    def save_checkpoint(checkpoint_db, current, current_run, batch_key, results):
        owned(checkpoint_db, current_run)
        if usage_record_failed:
            for result in results:
                _mark_ai_usage_failure(result)
        add_event(checkpoint_db, current.id, "check.ai_batch_checkpoint", stage="check", payload={
            "run_id": current_run["run_id"], "batch_key": batch_key, "request_id": request_id,
            "results": results}, commit=False)

    try:
        with use_model(model_code):
            actual_client = client if client is not None else client_from_env(model_code=model_code)
            client_fingerprint = ai_checkpoint_client_fingerprint(actual_client)
            output = await ai_guarded_review.run_guarded_batch_review(
                session_factory=lambda: SessionLocal(bind=bind), task_id=task_id, group_id=group_id,
                finding_ids=reserved, user=user, client=actual_client,
                material_loader=_rule_materials, on_usage=collect_usage, save_result=save, before_batch=renew,
                load_checkpoint=load_checkpoint, save_checkpoint=save_checkpoint, on_batch_progress=batch_progress,
                checkpoint_namespace=f"{validation_version}:{model_code}:{client_fingerprint or request_id}")
        if usage_record_failed:
            for result in output:
                _mark_ai_usage_failure(result)
        by_id = {**cached, **{result["finding_id"]: result for result in output}}
        return [by_id[finding_id] for finding_id in finding_ids]
    except Exception:
        with SessionLocal(bind=bind) as failure_db:
            locked = failure_db.query(BsTask).filter_by(id=task_id).with_for_update().one_or_none()
            if locked:
                for finding_id in reserved:
                    latest = _latest_ai_advisory_event(failure_db, task_id, run["run_id"], finding_id)
                    if latest and latest.event_type == "check.ai_advisory_started" and (latest.payload_json or {}).get("request_id") == request_id:
                        add_event(failure_db, task_id, "check.ai_advisory_failed", stage="check", payload={
                            "run_id": run["run_id"], "group_id": group_id, "finding_id": finding_id, "request_id": request_id,
                            "validation_version": validation_version}, commit=False)
                failure_db.commit()
        raise


def run_semantic_checks(db: Session, task: BsTask) -> list:
    """模型只提议待复核风险；多侧引文经核心验证，原规则不被覆盖。"""
    materials = _rule_materials(db, task)
    task_id = task.id
    from ...services.billing.bindings import effective_model_code_or_default
    from ...services.billing.model_context import use_model

    model_code = effective_model_code_or_default(db, "bs_task", task_id)
    add_event(db, task_id, "check.semantic_started", payload={})

    def progress(phase, material_id, current, total):
        add_event(db, task_id, "check.semantic_progress", payload={
            "phase": phase, "material_id": material_id, "current": current, "total": total})

    # 语义审查的模型成本必须进台账（Codex P1-D）：回调在异步侧收集原始 usage，
    # 回到同步侧统一写，避免在事件循环里操作业务 Session。
    usage_batches: list[dict] = []

    def collect_usage(raw):
        usage_batches.append(raw if isinstance(raw, dict) else {})

    with use_model(model_code):
        result = run_sync(semantic_review.compare_contracts(
            materials, on_progress=progress, on_usage=collect_usage))
    if usage_batches:
        from ...services.token_service import record as _record_token

        # 独立计费会话：不能逐条 commit 业务 Session，否则会把任务事件等脏写入
        # 一并提交，破坏核查事务边界。
        for raw in usage_batches:
            billing_db = SessionLocal(bind=db.get_bind())
            try:
                _record_token(
                    db=billing_db,
                    user_id=int(task.created_by or 0),
                    user_type=task.created_by_type or "web",
                    conversation_id=task.conversation_id,
                    input_count=None,
                    output_count=None,
                    token_count=None,
                    raw_usage=raw,
                    model_code=model_code,
                    source="plus:blockchain-supervision",
                    stage="semantic_review",
                    billing_scene="subcontract_review",
                    scope_type="bs_task",
                    scope_id=str(task.id),
                )
                billing_db.commit()
            except Exception:  # noqa: BLE001 —— 计费旁路失败不阻断核查
                billing_db.rollback()
                logger.warning("[bs] 语义审查用量记账失败 task=%s", task_id, exc_info=True)
            finally:
                billing_db.close()
    findings = []
    for index, proposal in enumerate(result['findings']):
        refs = [dict(ref, location_mode='precise') for ref in proposal['evidence']]
        ref = next((r for r in refs if any(m.id == r['material_id'] and m.role != 'general' for m in materials)), refs[0])
        findings.append(BsFinding(task_id=task_id, item_id=f'BS-SEM-{index + 1}',
            kind='contract_semantic', status='hit', message=('待人工确认：' + proposal['message'])[:500],
            material_id=ref['material_id'], quote=ref['quote'], page=ref['page'],
            location_mode='precise', evidence_json=refs,
            source='总包与分包合同原文比对；语义风险提议，非违法定性'))
    limitations = list(dict.fromkeys(result['limitations']))
    if not findings and not limitations:
        findings.append(BsFinding(task_id=task_id, item_id='BS-SEM-NONE',
            kind='contract_semantic', status='undecidable',
            message='已完成合同条款比对，未提出有双侧原文依据的风险；不代表全部条款合规，请按需人工复核',
            source='合同语义核查范围与执行限制'))
    for index, reason in enumerate(limitations):
        findings.append(BsFinding(task_id=task_id, item_id=f'BS-SEM-L-{index + 1}',
            kind='contract_semantic', status='undecidable', message=reason[:500],
            source='合同语义核查范围与执行限制'))
    _replace_findings(db, task, findings)
    add_event(db, task_id, "check.completed", payload={
        "kind": "contract_semantic", "counts": Counter(f.status for f in findings)})
    return findings


def run_a2_checks(db: Session, task: BsTask) -> list:
    """A2 全部内核（主体资格与社保资料分组）：已接入批次按清单递增。"""
    if not task.scope_a3:
        raise ValueError("任务未选择 A3 核查范围")
    reference_plan = _prepare_reference_group(db, task, "a2")
    findings = (run_cert_validity(db, task, persist=False)
                + run_person_consistency(db, task, persist=False)
                + run_qualification_match(db, task, persist=False))
    return _persist_reference_group(db, task, reference_plan, findings)


def run_internal_records(db: Session, task: BsTask) -> list:
    """方案 A：仅用任务内业主材料核查信用与事故记录。"""
    if not task.scope_a1:
        raise ValueError("任务未选择 A1 核查范围")
    reference_plan = _prepare_reference_group(db, task, "internal_records")
    materials = _rule_materials(db, task)
    subjects = (db.query(BsSubject.id, BsSubject.name)
                .filter(BsSubject.task_id == task.id, BsSubject.name.isnot(None),
                        BsSubject.origin != "material_auto").all())
    subject_names_by_id = {subject_id: name for subject_id, name in subjects}
    findings = internal_records.run_internal_records_check(
        task, materials, subject_names_by_id=subject_names_by_id)
    pinning.pin_findings(findings, materials)
    return _persist_reference_group(db, task, reference_plan, findings, extra={"external_sources": False})


# ==================== T10-B：ZIP 准备生命周期（契约 §7.3） ====================
#
# 状态机（冻结）：
#   setup + stored ZIP --首次 start（单事务）--> preparing + 唯一准备 worker
#   --全部子材料收口--> 主体类型交叉识别
#        ├─ 无缺口 ---> 原子锁型/快照 -> running
#        └─ 冲突/未确认 -> 不落锁 -> setup + 可读 gaps
#   preparing + 重复 start ---> 返回同一受理态，不建第二 worker
#   进程重启发现 preparing --> 复位 setup，幂等复用成功子材料
#
# 唯一 job 机制（**不需要新增 job 表/字段**）：持久化的 `BsTask.state='preparing'`
# 即唯一 job 标识——它由任务主键行承载、跨进程可见；受理用条件 UPDATE
# （`WHERE state='setup'`）抢占，rowcount 是「本请求是否赢得受理」的唯一判据。
# 现有 material 状态无法承担此职责（容器/子材料行不表达「谁在跑」），但它也不需要
# ——互斥只发生在「任务级受理」这一个点上，多写一个 job 表反而引入第二套状态源。

def _container_failure_message(container) -> str:
    """容器整体失败的可读原因（取 `_fail_container` 写入的降级文案）。"""
    for d in (container.degradations_json or []):
        if (d or {}).get("kind") == "zip_extract_failed":
            return d.get("message") or ""
    return ""


def _preparing_acceptance(db: Session, task: BsTask) -> dict:
    """`preparing` 态的重复 start 受理回执：返回同一准备态，不建第二 worker。

    **刻意不带 `ok` 键**：受理态不是「启动成功」，`ok=true` 会被误读成已进入
    running（契约 §7.3.2 明令不得暗示已正式运行）。前端与测试按 `preparing`/`state`
    两个显式字段判定。
    """
    containers = task_zip_containers(db, task)
    pending = [m for m in containers if m.parse_state == PARSE_STATE_STORED]
    message = ("报审资料包正在准备中，完成后将自动完成类型识别；"
               "请稍后刷新查看，无需重复点击开始核查" if pending else
               "材料正在读取，完成并通过确认后将开始核查；请稍后刷新查看，无需重复点击")
    return {
        "preparing": True, "reason": "preparing",
        "state": TASK_STATE_PREPARING,
        "detail": {
            "state": TASK_STATE_PREPARING,
            "message": message,
            "containers": [{"material_id": m.id, "file_name": m.file_name,
                            "parse_state": m.parse_state} for m in containers],
            "pending_containers": len(pending),
        },
    }


def _accept_zip_preparation(db: Session, task: BsTask, *, operator=None) -> dict:
    """首次 start：**单事务**把任务置 `preparing` 并起唯一准备 worker。

    原子抢占：条件 UPDATE（`WHERE state='setup'`）的 rowcount 即唯一判据——
    rowcount==1 的请求赢得受理并创建 worker；rowcount==0 说明并发/重复请求已被
    别的请求受理，直接返回同一受理态，**不建第二 worker、不重复解包**。
    事件在抢占成功后补写（与 start 成功路径同纪律：状态原子性由 UPDATE 保证，
    事件属审计留痕）。
    """
    claimed = (db.query(BsTask)
               .filter(BsTask.id == task.id, BsTask.state == TASK_STATE_SETUP)
               .update({"state": TASK_STATE_PREPARING}, synchronize_session=False))
    db.commit()
    db.refresh(task)
    if claimed != 1:
        return _preparing_acceptance(db, task)
    containers = task_zip_containers(db, task)
    add_event(db, task.id, "zip.preparation_started", payload={
        "container_count": len(containers),
        "containers": [{"material_id": m.id, "file_name": m.file_name}
                       for m in containers],
        "operator": operator,
    })
    threading.Thread(target=_prepare_zip_background,
                     args=(task.id, db.get_bind(), operator),
                     daemon=True, name=f"bs-zip-prepare-{task.id}").start()
    logger.info("[bs] ZIP 准备受理 task=%s，容器 %s 个", task.id, len(containers))
    return _preparing_acceptance(db, task)


def _prepare_one_container(session: Session, task: BsTask, container: BsMaterial,
                           *, operator=None, defer_replacement: bool = False) -> dict:
    """单容器准备：安全派生（复用 T2 窄接口）→ 逐个子 PDF 走既有解析收口 → 容器 `done`。

    已展开过（重试/恢复复用）就不再调用 `materialize_zip_children`——该函数本身幂等，
    但重复调用会**重新解包写盘**；契约 §7.3.6 要求重启后「幂等复用成功子材料」，
    所以这里按「已有子材料即跳过解包」短路，把重复写文件也一并消掉。
    """
    if _is_failed_container(container):
        return {"ok": False, "reason": "zip_preparation_failed"}
    children = container_children(session, task, container)
    if children:
        out = {"ok": True, "children": children, "created": 0, "reused": len(children)}
        logger.info("[bs] ZIP 容器已有子材料，跳过解包 task=%s container=%s 子材料 %s",
                    task.id, container.id, len(children))
    else:
        out = materialize_zip_children(session, task, container, operator=operator)
        children = out.get("children") or []
    if not out.get("ok"):
        return {"ok": False, "reason": out.get("reason") or "zip_preparation_failed"}
    # 文件级解析/OCR：复用既有收口函数。`refresh_type=False` 是关键——主体级识别
    # 必须等**全部**子材料收口后在批次屏障后串行做（契约 §7.3.4），否则并发写主体
    # 冲突状态/事件即是被禁止的那件事。
    for child in children:
        if child.parse_state in ("uploaded", "parsing"):
            _finish_ingest(task.id, child.id, session.get_bind(), refresh_type=False)
    session.expire_all()
    container = session.get(BsMaterial, container.id)
    if _is_failed_container(container):
        return {"ok": False, "reason": "zip_preparation_failed"}
    if container.parse_state != "done":
        container.parse_state = "done"
        add_event(session, task.id, "zip.container_ready", payload={
            "container_material_id": container.id,
            "file_name": container.file_name,
            "child_count": len(children),
            "operator": operator,
        })
    refreshed = session.get(BsMaterial, container.id)
    if not defer_replacement:
        _activate_pending_replacement(session, task, refreshed, operator=operator)
    return {"ok": True}


def _settle_children(session: Session, task: BsTask, *, operator=None) -> list:
    """批次屏障：确保全部子材料已达终态（`done`/`failed`）。

    串行解析下 `_finish_ingest` 返回时子材料已收口；此处显式复核并**兜底收口**
    （仍停留 uploaded/parsing 的一律按失败收口）——批次屏障之后任何「非终态」
    材料都会让主体识别与启动闸读到半份状态，宁可显式失败也不留悬空。
    """
    session.expire_all()
    children = _task_children(session, task)
    for child in children:
        if child.parse_state in ("done", "failed"):
            continue
        child.parse_state = "failed"
        child.degradations_json = list(child.degradations_json or []) + [{
            "kind": "parse_interrupted",
            "message": "该文件在资料包准备时未能完成读取，请重新上传资料包",
        }]
        add_event(session, task.id, "material.parse_failed",
                  payload={"material_id": child.id, "file_name": child.file_name})
        logger.info("[bs] 批次屏障兜底收口子材料 %s（%s）", child.id, child.file_name)
    return children


def _cross_scan_subject_types(session: Session, task: BsTask, *, operator=None) -> list:
    """批次屏障后的主体类型**交叉识别**（串行，单会话单事务语义）。

    只对「有派生子材料的主体」重跑 `refresh_subject_ai_type`（主体级识别本身读该
    主体全部材料正文，见 `scan_subject_type_evidence`）；文件名与 ZIP 内路径**不
    参与**判定（契约 §7.1.3）。识别只写主体三列 + 事件，不改子材料状态。
    """
    session.expire_all()
    children = _task_children(session, task)
    subject_ids = sorted({c.subject_id for c in children if c.subject_id})
    touched = []
    for subject_id in subject_ids:
        subject = session.get(BsSubject, subject_id)
        if subject is None:
            continue
        try:
            pages = _subject_type_pages(session, task, subject)
            refresh_subject_ai_type(session, task, subject, operator=operator,
                                    pages_by_material=pages)
            touched.append(subject_id)
        except Exception as e:  # 识别失败不得阻断准备收口（材料读取已完成才是主产物）
            logger.warning("[bs] 准备阶段主体类型交叉识别跳过 subject=%s: %s",
                           subject_id, e)
    return touched


def _reset_to_setup(session: Session, task_id: int, cause: str,
                    payload: dict) -> None:
    """准备段异常收口：任务复位 `setup` + 写可读事件（**绝不**自动宣称 running）。

    `cause` 三分支各自用**字面量**事件类型发射——`test_all_emitted_events_are_registered`
    以静态正则扫描 `add_event` 的第三个实参，参数化 event_type 会让新事件逃过白名单
    校验（该契约测试的设计意图就是「发射端必须可静态枚举」）。
    """
    session.expire_all()
    task = session.get(BsTask, task_id)
    if task is None or task.state != TASK_STATE_PREPARING:
        return
    task.state = TASK_STATE_SETUP
    if cause == "container_failed":
        add_event(session, task_id, "zip.preparation_failed", payload=payload)
    elif cause in ("type_gaps", "material_gaps"):
        add_event(session, task_id, "zip.preparation_awaiting_confirmation",
                  payload=payload)
    else:  # pragma: no cover —— 兜底分支，当前无调用方
        add_event(session, task_id, "zip.preparation_recovered", payload=payload)


def _finish_preparation(session: Session, task: BsTask, *, operator=None) -> dict:
    """准备完成后的启动收口：应用**既有人工确认/冲突启动闸**。

    无缺口 → 复用既有单事务原子锁型 + 快照 → `running`；有缺口 → 不落锁、不运行
    A1/A2/A3，回可编辑 `setup` 并给出可读原因（契约 §7.3.5）。
    """
    session.expire_all()
    task = session.get(BsTask, task.id)
    check = validate_task_materials(session, task)
    # 子材料是在准备阶段才落库的；首次 start 时 HTTP 闸门尚未看见它们，
    # 因此准备收口必须再次执行硬缺口校验。多文件 ZIP 存在未归类子材料时，
    # 复位 setup 并等待逐件主体确认，绝不能直接落锁进入 running。
    if not check.get("hard_ok"):
        _reset_to_setup(session, task.id, "material_gaps", {
            "reason": "hard_gaps",
            "gaps": [{"issue": "unassigned_child_material",
                      "message": gap} for gap in check.get("hard_gaps", [])],
            "operator": operator,
        })
        logger.info("[bs] ZIP 准备完成但存在材料硬缺口 task=%s", task.id)
        return {"ok": False, "reason": "hard_gaps", "detail": check}
    supplement_edit = (task.options_snapshot_json or {}).get("supplement_edit") or {}
    if supplement_edit.get("prepare_for_supplement"):
        result = activate_supplement_plan(session, task, allow_preparing=True, operator=operator)
    else:
        result = _apply_start_gates(session, task, check=check, operator=operator)
    if result.get("ok"):
        return result
    detail = result.get("detail") or {}
    gaps = detail.get("gaps") or [] if isinstance(detail, dict) else []
    if not gaps and isinstance(detail, dict) and detail.get("message"):
        gaps = [{"message": detail["message"]}]
    _reset_to_setup(session, task.id, "type_gaps", {
        "reason": result.get("reason"),
        "gaps": gaps,
        "operator": operator,
    })
    logger.info("[bs] ZIP 准备完成但有启动缺口 task=%s reason=%s",
                task.id, result.get("reason"))
    return result


def pending_pdf_materials(db: Session, task: BsTask) -> list:
    """已保存但尚未读取的普通文件；不包括登记记录、ZIP 或派生子材料。"""
    return [m for m in db.query(BsMaterial).filter_by(task_id=task.id).all()
            if m.parse_state == "uploaded" and m.path
            and m.container_material_id is None
            and not m.file_name.lower().endswith('.zip')]


def _prepare_zip_background(task_id: int, bind, operator=None) -> None:
    """唯一后台准备 worker（进程内线程；无容器时不会被创建）。

    顺序即契约：**逐容器派生 → 文件级解析收口 → 批次屏障 → 主体类型交叉识别 →
    启动收口**。任一步异常都收敛到「复位 setup + 可读事件」，不留永久 `preparing`。
    """
    session = SessionLocal(bind=bind)
    try:
        task = session.get(BsTask, task_id)
        if task is None or task.state != TASK_STATE_PREPARING:
            return  # 恢复/删除竞态：任务已离开准备态，不再动作
        supplement_edit = (task.options_snapshot_json or {}).get("supplement_edit") or {}
        supplement_preparing = bool(supplement_edit.get("prepare_for_supplement"))
        if supplement_preparing:
            pdf_ids = [m.id for m in _supplement_preparation_materials(session, task)
                       if m.parse_state == "failed"
                       or (m.parse_state == "uploaded" and m.path and m.via == "upload")]
        else:
            pdf_ids = [m.id for m in pending_pdf_materials(session, task)]
        for mid in pdf_ids:
            session.rollback()  # 独立读取会话提交后，必须结束 MySQL 旧快照。
            task = session.get(BsTask, task_id)
            if task is None or task.state != TASK_STATE_PREPARING:
                return
            mat = session.get(BsMaterial, mid)
            eligible = ("uploaded", "failed") if supplement_preparing else ("uploaded",)
            if mat is None or mat.parse_state not in eligible:
                continue
            mat.parse_state = 'parsing'
            session.commit()
            _finish_ingest(task_id, mid, bind, refresh_type=False)
        session.rollback()
        task = session.get(BsTask, task_id)
        if task is None or task.state != TASK_STATE_PREPARING:
            return
        containers = zip_containers(session, task)
        for container in containers:
            supplement_edit = (task.options_snapshot_json or {}).get("supplement_edit") or {}
            out = _prepare_one_container(
                session, task, container, operator=operator,
                defer_replacement=bool(supplement_edit.get("prepare_for_supplement")))
            if not out.get("ok"):
                failed = failed_zip_containers(session, task)
                _reset_to_setup(session, task.id, "container_failed", {
                    "reason": out.get("reason"),
                    "containers": [{"material_id": m.id, "file_name": m.file_name,
                                    "message": _container_failure_message(m)}
                                   for m in failed],
                    "operator": operator,
                })
                return
        session.rollback()
        task = session.get(BsTask, task_id)
        if task is None or task.state != TASK_STATE_PREPARING:
            return
        _settle_children(session, task, operator=operator)
        if supplement_preparing:
            session.rollback()
            task = session.get(BsTask, task_id)
            if task is None or task.state != TASK_STATE_PREPARING:
                return
            incomplete = [m for m in _supplement_preparation_materials(session, task)
                          if _subject_assessment_issue(m) or m.parse_state in ("failed", "parsing", "uploading")
                          or (m.parse_state == "uploaded" and m.path and m.via == "upload")
                          or (m.replacement_target_id is not None and m.parse_state == "done")]
            if incomplete:
                names = "、".join(m.file_name for m in incomplete[:3])
                _reset_to_setup(session, task_id, "material_gaps", {
                    "reason": next((_subject_assessment_issue(m) for m in incomplete if m.parse_state == "done" and _subject_assessment_issue(m)), "parse_failed"),
                    "gaps": [{"material_id": m.id, "message": "材料「%s」读取或主体校验未完成，请检查后重试" % m.file_name}
                             for m in incomplete],
                    "message": "材料（%s）未准备完成" % names,
                    "operator": operator,
                })
                return
        result = _finish_preparation(session, task, operator=operator)
        if result.get("ok"):
            # 准备完成事件（契约 §7.3.5：无缺口 → 已进 running；事件在 start 收口
            # 之后补记，仅供时间线展示阶段推进，不参与状态原子性）。
            add_event(session, task.id, "zip.preparation_completed", payload={
                "state": "running",
                "container_count": len(task_zip_containers(session, task)),
                "operator": operator if operator is not None else "system",
            })
    except Exception:
        logger.exception("[bs] ZIP 准备后台失败 task=%s", task_id)
        try:
            session.rollback()
            _reset_to_setup(session, task_id, "container_failed", {
                "reason": "unexpected_error",
                "containers": [],
                "operator": operator,
            })
        except Exception:  # pragma: no cover —— 兜底失败只记日志，不再抛
            logger.exception("[bs] ZIP 准备失败复位未能写入 task=%s", task_id)
    finally:
        session.close()


def _subject_start_gate(db: Session, task: BsTask) -> tuple[list, dict | None]:
    """首次启动与补件共用的主体确认闸，不修改任务或锁型。"""
    pending_subject_ids = set(_pending_subject_type_scans(task.id))
    active_ids = active_subject_ids(db, task)
    if pending_subject_ids:
        pending_subjects = (db.query(BsSubject)
                            .filter(BsSubject.task_id == task.id,
                                    BsSubject.id.in_(pending_subject_ids & active_ids))
                            .order_by(BsSubject.id).all())
        return [], {"ok": False, "reason": "subject_type_pending",
                "detail": {"message": "主体类型识别仍在处理中，请稍后重试启动核查",
                           "gaps": [{
                               "subject_id": s.id, "stable_code": s.stable_code,
                               "issue": "type_check_pending",
                               "message": "主体「%s」的扫描件类型识别仍在处理中，请稍后重试"
                                          % subject_identity_label(s),
                           } for s in pending_subjects]}}
    subjects = (db.query(BsSubject).filter(BsSubject.task_id == task.id,
                                           BsSubject.id.in_(active_ids))
                .order_by(BsSubject.id).all())
    type_gaps = []
    for s in subjects:
        # 类型只认用户上传时选的资料类别（方案A）：AI 证据反对只记 type_conflict
        # 供台账提示，不再阻断启动。真正缺的只有「从未确认过类型」。
        if s.subject_type not in SUBJECT_TYPES or not s.type_confirmed:
            type_gaps.append({
                "subject_id": s.id, "stable_code": s.stable_code,
                "issue": "type_unconfirmed",
                "message": "主体「%s」的类型尚未人工确认，请在启动前确认为专业或劳务"
                           % subject_identity_label(s)})
    if type_gaps:
        return [], {"ok": False, "reason": "subject_type_gaps",
                "detail": {"message": "存在类型尚未确认的分包主体，请确认后重新开始核查",
                           "gaps": type_gaps}}
    return subjects, None


def _apply_start_gates(db: Session, task: BsTask, *, check: dict, operator=None) -> dict:
    """既有启动闸门 + 成功路径，供 start_review 与准备收口共用。"""
    db.refresh(task, with_for_update=True)
    if task.state in {"running", "review", "done"}:
        return {"ok": True, "soft_gaps": ((task.options_snapshot_json or {}).get("parse") or {}).get("soft_gaps", [])}
    subjects, error = _subject_start_gate(db, task)
    if error:
        return error
    # ——以下为启动成功路径：规则文件先完整校验，再改任何 ORM 状态——
    # 同时冻结 runtime 指纹，避免 manifest 与本代 reference seed 分别指向两次发布。
    ruleset_manifest, runtime_rule_fingerprint = _new_frozen_ruleset_inputs()
    newly_locked = [s for s in subjects if not s.type_locked]
    try:
        section_sources = _missing_section_suggestions(db, task)
        _apply_section_suggestions(task, section_sources)
        date_source = baseline_date_source(task)
        if date_source == "automatic":
            task.baseline_date = _default_baseline_date()
        for s in newly_locked:
            s.type_locked = True
        # BS-P2-04 / 117-B：规则归属必须在启动成功的同一事务内冻结。后续收口、
        # 历史快照和版本差异都只读这一份；不得在读取旧结论时重新读取工作树当前规则。
        task.ruleset_version = ruleset_manifest["version"]
        task.options_snapshot_json = {
            "baseline_date_source": date_source,
            "semantic_review_version": 1,
            "ruleset_manifest": ruleset_manifest,
            "task": {"id": task.id, "title": task.title,
                      "routing": _routing_snapshot(task)},
            "scope": {"A1": task.scope_a1, "A3": task.scope_a3},
            "scale": task.scale,
            "materials": check["composition"],
            "parse": {"all_done": True, "soft_gaps": check["soft_gaps"]},
            # A-03（B1）：标段锚点随两段式快照固化（旧键一个不动，增键 section）
            "section": _section_snapshot(task),
            "section_auto_sources": section_sources,
            # T1-v2 §三：主体快照（subject_id/稳定编号/人工类型/AI类型/冲突态）
            "subjects": [{"subject_id": s.id, "stable_code": s.stable_code,
                          "subject_type": s.subject_type,
                          "ai_subject_type": s.ai_subject_type,
                          "type_conflict": bool(s.type_conflict)} for s in subjects],
        }
        task.state = "running"
        db.flush()
        initialize_reference_review(
            db, task, rule_fingerprint=runtime_rule_fingerprint)
        if knowledge_runs.rule_fingerprint() != runtime_rule_fingerprint:
            raise knowledge_runs.ReferenceRunSuperseded(
                "规则文件在启动期间发生变化，请重试启动核查")
        db.commit()
    except Exception:
        db.rollback()
        db.refresh(task)
        raise
    for s in newly_locked:
        add_event(db, task.id, "subject.type_locked", payload={
            "subject_id": s.id, "stable_code": s.stable_code,
            "lock_source": "start", "operator": operator,
        })
    add_event(db, task.id, "task.started",
              payload={"snapshot": task.options_snapshot_json, "soft_gaps": check["soft_gaps"]})
    return {"ok": True, "soft_gaps": check["soft_gaps"]}


def start_review(db: Session, task: BsTask, *, operator=None) -> dict:
    """开始核查：硬缺口直接拒绝（400 语义由 router 转）；解析中阻止（07 §4.1，
    与参照体「解析中放行」有意不同——A1 要算跨文件覆盖率并集，半份文本会改结论）。
    通过则固化五项快照并置 running。

    契约 v3 §三 启动即锁定（硬规则）：事务内校验全部分包主体「类型已确认」，
    任一不满足 → 阻止启动、不改状态、不落锁；全部通过 →
    原子置 type_locked=True + 主体快照随 options_snapshot 固化 → 进 running。
    AI 证据与所选类型不一致（type_conflict）只作为提示记入快照，不再阻止启动（方案A）。
    原子性实现：置锁/快照/状态全部先暂存，单次 db.commit() 落库；事件在提交后
    补写（add_event 自带 commit，属审计留痕，不参与状态原子性）。

    T10-B（契约 §7.3）：存在 `stored` ZIP 容器时不直接拒绝，而是**受理**为持久
    `preparing` 态并起唯一后台准备 worker（异步响应，`router` 转 202）；准备完成后
    由 worker 复用同一套启动闸门收口。已 `preparing` 的重复/并发 start 返回同一
    受理态，不建第二 worker。整体失败的容器**硬阻止**（必须用户处理）。

    `operator`：当前认证用户标识，由 HTTP 端点透传（契约 v3 §三 操作者硬规则）；
    本流程产生的每个 `subject.type_locked` 事件都必须携带它，不得写 None/固定值。
    """
    db.rollback()
    db.refresh(task, with_for_update=True)
    # ① 已受理（含并发/重复点击）：返回同一准备态，不建第二 worker（契约 §7.3.3）
    if task.state == TASK_STATE_PREPARING:
        return _preparing_acceptance(db, task)
    if task.state in {"running", "review", "done"}:
        return {"ok": True, "soft_gaps": ((task.options_snapshot_json or {}).get("parse") or {}).get("soft_gaps", [])}
    check = validate_task_materials(db, task)
    if not check["hard_ok"]:
        return {"ok": False, "reason": "hard_gaps", "detail": check}
    if check["parsing_pending"]:
        return {"ok": False, "reason": "parsing_pending", "detail": check}
    # ② 整体失败的容器必须硬阻止（契约 §7.5.6）：它不是「稍后就好」，必须用户移除
    #    或重新上传。与「待准备」是两个闸，不得混为一谈。
    failed = failed_zip_containers(db, task)
    if failed:
        return {"ok": False, "reason": "zip_preparation_failed",
                "detail": {
                    "message": "报审资料包无法准备，请移除或重新上传后再开始核查",
                    "gaps": [{
                        "material_id": m.id, "file_name": m.file_name,
                        "issue": "zip_preparation_failed",
                        "message": _container_failure_message(m)
                                   or "资料包无法展开，请移除后重新上传",
                    } for m in failed]}}
    # ③ 待准备容器：受理为 `preparing`（**替换** T4.1-T2 的 zip_preparation_required
    #    临时拒绝分支；其位置与闸门先后未动——仍在 parsing_pending 之后、类型闸之前）。
    if zip_containers(db, task) or pending_pdf_materials(db, task):
        return _accept_zip_preparation(db, task, operator=operator)
    return _apply_start_gates(db, task, check=check, operator=operator)


def dumps(payload) -> str:
    return json.dumps(payload, ensure_ascii=False)


def observation_stats(db: Session, created_by: int = None,
                      created_by_type: str = None) -> list:
    """P2-3 触发词召回观察数据：按 kind+item_id 聚合三态计数与不可判定占比。

    created_by 传入时只统计该用户的任务（Q6 归属过滤，router 默认带当前用户）；
    None 保留全量口径。BS-1B 起叠加 created_by_type：User.id 与 PlatformUser.id 是
    两套表、同 id 不同人，仅按 id 过滤不足以隔离（web/miniapp 两类用户可能撞 id）。
    按占比降序——长期高不可判定占比的核查项就是 LLM 语义通道的候选名单。只读聚合，
    不动判定逻辑、不建新表（16 号方案 P2 表口径：先积累数据再决策）。
    """
    q = (db.query(BsFinding.kind, BsFinding.item_id, BsFinding.status,
                  func.count(BsFinding.id))
         .group_by(BsFinding.kind, BsFinding.item_id, BsFinding.status))
    if created_by is not None:
        q = (q.join(BsTask, BsFinding.task_id == BsTask.id)
             .filter(BsTask.created_by == created_by))
        if created_by_type is not None:
            q = q.filter(BsTask.created_by_type == created_by_type)
    rows = q.all()
    agg = {}
    for kind, item_id, status, n in rows:
        cell = agg.setdefault((kind, item_id),
                              {"kind": kind, "item_id": item_id,
                               "total": 0, "hit": 0, "miss": 0, "undecidable": 0})
        cell["total"] += n
        if status in cell:
            cell[status] += n
    items = []
    for (kind, item_id), c in agg.items():
        und = c["undecidable"]
        c["undecidable_ratio"] = (und / c["total"]) if c["total"] else 0.0
        items.append(c)
    items.sort(key=lambda x: (-x["undecidable_ratio"], x["kind"], x["item_id"]))
    return items


def delete_task(db: Session, task: BsTask) -> None:
    """硬删除任务（P0-3）：显式删三张子表再删主表。

    不依赖 FK ondelete CASCADE——sqlite 需 PRAGMA foreign_keys=ON 才生效，
    夹具未开时静默残留孤儿行；显式删除使 sqlite/MySQL 行为必然一致。
    """
    db.query(BsMaterialConfirmation).filter(BsMaterialConfirmation.task_id == task.id).delete(synchronize_session=False)
    db.query(BsFinding).filter(BsFinding.task_id == task.id).delete(synchronize_session=False)
    db.query(BsMaterial).filter(BsMaterial.task_id == task.id).delete(synchronize_session=False)
    db.query(BsReviewVersion).filter(BsReviewVersion.task_id == task.id).delete(synchronize_session=False)
    db.query(BsTaskEvent).filter(BsTaskEvent.task_id == task.id).delete(synchronize_session=False)
    db.delete(task)
    db.commit()
    logger.info("[bs] task %s hard deleted", task.id)


def review_finding(db: Session, finding: BsFinding, *, reviewer: str,
                   decision: str, note: str = None) -> None:
    """人工复核回执落列（P1-3 最小闭环）。

    单列单对象：再复核覆盖旧回执（最后回执为准）；decision 白名单校验在
    router 层 400，此处只管落库。不改判定逻辑、不建新表。
    """
    finding.review_json = {
        "reviewer": reviewer,
        "decision": decision,
        "note": (note or "").strip() or None,
        "at": datetime.now().isoformat(timespec="seconds"),
    }
    db.commit()
    logger.info("[bs] finding %s reviewed: %s by %s", finding.id, decision, reviewer)


# 收口闸「必须有结论」的分组（范围 → 结论类型）。
# 真源＝data/review_flow.json 五步主线登记的内核 kind（rules.in_scope_kinds()）：
# 这里的清单只做范围切分，**必须**与流程图一致，由 tests/test_bs_review_flow.py 对锚。
# 教训（2026-10-14）：此前本表把已转「后续功能」的占比类算成必查，占比默认关闭后
# 任务永远收不了口（409 一直挂「尚无结论：分包金额占比核算」）。
_SCOPE_REQUIRED_KINDS = {scope: tuple(kind for group in reference_snapshots.CHECK_GROUPS.values()
                                    if group["scope"] == scope for kind in group["kinds"])
                         for scope in ("a1", "a3")}


def _json_safe(value):
    """快照只落 JSON 基础类型，兼容历史 Decimal/date 等字段。"""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _new_frozen_ruleset_manifest() -> dict:
    """生成一次任务级审计清单，并补齐非哈希元数据。

    `generated_at` 和相对路径只用于审计，不进入 `text_lf_hash`；因此同一规则在不同
    启动时刻仍有同一业务版本，但每个任务能说明清单何时、从哪里冻结。
    """
    manifest = _json_safe(ruleset.manifest())
    manifest["generated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    for name, entry in (manifest.get("files") or {}).items():
        if isinstance(entry, dict):
            entry["path"] = "data/%s" % name
    return manifest


def _new_frozen_ruleset_inputs() -> tuple[dict, str]:
    """在稳定 runtime 指纹窗口内冻结业务清单和 reference seed 指纹。"""
    before = knowledge_runs.rule_fingerprint()
    manifest = _new_frozen_ruleset_manifest()
    after = knowledge_runs.rule_fingerprint()
    if before != after:
        raise knowledge_runs.ReferenceRunSuperseded(
            "规则文件在版本冻结期间发生变化，请稍后重试")
    return manifest, after


def _frozen_ruleset_manifest(task: BsTask) -> dict | None:
    """读取任务启动/重跑时冻结的规则清单；历史任务缺失时保持 ``None``。

    版本列和 manifest 必须成对且相互一致。历史任务可能只有空列或旧选项快照，
    此时不能把当前工作树清单补挂到旧结论上，统一返回 ``None``。
    """
    options = task.options_snapshot_json if isinstance(task.options_snapshot_json, dict) else {}
    manifest = options.get("ruleset_manifest")
    if not isinstance(manifest, dict):
        return None
    version = manifest.get("version")
    if not task.ruleset_version or version != task.ruleset_version:
        return None
    return _json_safe(manifest)


def _review_version_snapshot(db: Session, task: BsTask, *, trigger: str) -> dict:
    materials = (db.query(BsMaterial).filter(BsMaterial.task_id == task.id)
                 .order_by(BsMaterial.id).all())
    # 主体行只记**本代实际参与核查**的主体，口径与 checklist.active_subject_ids 一致。
    # 已退休主体（设计 136 并档/孤儿清理）仍在库中、事件流可查，但不能写进结论快照：
    # 否则快照会声称核查了 N 个主体而实际只算了 M 个，分母与结论对不上。
    subjects = (db.query(BsSubject)
                .filter(BsSubject.task_id == task.id,
                        BsSubject.retired.is_(False))
                .order_by(BsSubject.id).all())
    findings = (db.query(BsFinding).filter(BsFinding.task_id == task.id)
                .order_by(BsFinding.id).all())
    findings = ai_advisory_snapshots.filter_current_candidate_findings(
        db, task, SimpleNamespace(id=task.created_by, user_type=task.created_by_type), findings)
    material_rows = [{
        "material_id": material.id,
        "file_name": material.file_name,
        "path": material.path,
        "role": material.role,
        "material_category": material.material_category,
        "subject_id": material.subject_id,
        "packet_id": material.packet_id,
        "parse_state": material.parse_state,
        "pages": material.pages,
        "size_kb": material.size_kb,
        "degradations": material.degradations_json or [],
        # BS-P2-01（设计 98 §6.2）：版本快照的材料行同样带替代链两列，
        # 历史快照无此键 ⇒ 读取侧用 .get() 回落 None，不回填。
        "revision_no": material.revision_no,
        "replacement_target_id": material.replacement_target_id,
        "superseded_by_id": material.superseded_by_id,
        "superseded_at": (material.superseded_at.isoformat(timespec="seconds")
                          if material.superseded_at else None),
    } for material in materials]
    subject_rows = [{
        "subject_id": subject.id,
        "stable_code": subject.stable_code,
        "name": subject.name,
        "name_status": subject_name_status(subject),
        "subject_type": subject.subject_type,
        "type_confirmed": bool(subject.type_confirmed),
        "type_locked": bool(subject.type_locked),
        "ai_subject_type": subject.ai_subject_type,
        "type_conflict": bool(subject.type_conflict),
        "type_conflict_reason": subject.type_conflict_reason,
    } for subject in subjects]
    finding_rows = [{
        "finding_id": finding.id,
        "subject_id": finding.subject_id,
        "material_id": finding.material_id,
        "item_id": finding.item_id,
        "kind": finding.kind,
        "material_only": checklist.is_material_only_finding(finding),
        "status": finding.status,
        "message": finding.message,
        "quote": finding.quote,
        "page": finding.page,
        "location_mode": finding.location_mode,
        "evidence": finding.evidence_json,
        "source": finding.source,
        "review": finding.review_json,
        "severity": finding.severity,
        "confidence": finding.confidence,
        "discrepancy": finding.discrepancy_json,
    } for finding in findings]
    ruleset_manifest = _frozen_ruleset_manifest(task)
    # 旧代规则与当前代码不一致时不能用新目录重建旧报告；只冻结原始问题和人工回执。
    stale_rules = bool(trigger != "completed" and task.ruleset_version != ruleset.current_version())
    return _json_safe({
        "schema_version": "bs-review-version.v1",
        "trigger": trigger,
        # BS-P2-04（设计 98 §3.2）：本版结论所用规则数据版本；历史任务/历史快照为 null。
        "ruleset_version": task.ruleset_version,
        # 117-B：版本号之外保留算法、双哈希和文件清单，便于隔离读回与审计。
        "ruleset_manifest": ruleset_manifest,
        "task": {
            "task_id": task.id,
            "title": task.title,
            "state": task.state,
            "scope": {"A1": bool(task.scope_a1), "A3": bool(task.scope_a3)},
            "scale": task.scale,
            "section": _section_snapshot(task),
            "options_snapshot": task.options_snapshot_json,
        },
        "materials": material_rows,
        # 仅冻结本代有效输入；已替代材料不应影响历史读取覆盖。
        "reading_coverage": task_reading_coverage(db, task),
        "subjects": subject_rows,
        "findings": finding_rows,
        "material_confirmations": [{
            "finding_id": row.finding_id, "subject_id": row.subject_id,
            "material_id": row.material_id, "item_id": row.item_id,
            "run_id": row.run_id, "token": row.token, "decision": row.decision,
            "operator_id": row.operator_id, "operator_type": row.operator_type,
            "created_at": row.created_at,
        } for row in db.query(BsMaterialConfirmation).filter_by(task_id=task.id)
          .order_by(BsMaterialConfirmation.id).all()],
        "reference_run": knowledge_runs.get_reference_run(db, task),
        "reference_snapshot": reference_snapshots.get_reference_snapshot(db, task),
        "ai_advisories": ai_advisory_snapshots.collect_current_advisories(db, task),
        "report": None if stale_rules else reporting.build_report(db, task),
        "opinion": None if stale_rules else reporting.build_opinion(db, task),
        **({"report_unavailable_reason": "ruleset_version_mismatch" if task.ruleset_version
            else "ruleset_version_unknown"} if stale_rules else {}),
    })


def supplement_scope_labels(findings, category, item_ids=None, subject_ids=None, checklist_summary=None):
    """从补件计划所选主体和条目的缺件汇总冻结展示范围。"""
    items = {str(value) for value in (item_ids or [])}
    subjects = {str(value) for value in (subject_ids or [])}
    selected = [finding for finding in findings
                if (not items or str(finding.item_id) in items)
                and (not subjects or str(finding.subject_id) in subjects)]
    summary = reporting.missing_summary(selected, checklist_summary)
    bucket = next((row for row in summary.get("categories", [])
                   if row.get("category") == category), None)
    # 用户从缺件清单的具体条目进入时，优先使用同一主体、同一条目的清单名称；
    # 通用 evidence key（如 license）可能只映射为宽泛的“证照”。
    if (bucket or category == "submission_packet") and items and subjects and checklist_summary:
        selected_pairs = {(str(finding.subject_id), str(finding.item_id)) for finding in selected}
        labels = []
        matched_pairs = set()
        for subject in checklist_summary.get("subjects", []):
            subject_id = str(subject.get("subject_id"))
            for item in subject.get("checklist_items", []):
                pair = (subject_id, str(item.get("item_id")))
                if (pair in selected_pairs
                        and item.get("missing") and item.get("label")):
                    labels.append(item["label"])
                    matched_pairs.add(pair)
        if labels and matched_pairs == selected_pairs:
            return list(dict.fromkeys(labels))
    return list((bucket or {}).get("labels") or [])


def plan_supplement_generation(db: Session, task: BsTask, *, generation_id: str,
                               category: str, item_ids=None, material_ids=None,
                               subject_ids=None, material_status: str = "ready",
                               parent_review_run_id: str | None = None) -> dict:
    """P8-03C：把补件影响计划持久化到任务快照并登记事件。

    本函数只创建/更新计划，不改变 task.state、不删除 findings、不启动核查。
    真实代际切换和局部调度由后续阶段显式调用，避免上传成功被误认为核查完成。
    """
    if not generation_id or len(str(generation_id)) > 120:
        raise ValueError("补件审查代 generation_id 无效")
    db.rollback()
    db.refresh(task, with_for_update=True)
    if task.state == TASK_STATE_PREPARING:
        return {"status": "blocked", "blocked_reason": "task_preparing"}

    existing = dict(task.options_snapshot_json or {}).get("supplement_plan")
    if isinstance(existing, dict) and existing.get("generation_id") == generation_id:
        return deepcopy(existing)
    edit = (task.options_snapshot_json or {}).get("supplement_edit") or {}
    if (isinstance(existing, dict) and isinstance(edit, dict) and existing.get("generation_id")
            and existing.get("generation_id") == edit.get("generation_id")
            and existing.get("status") in {"planned", "running"}
            and not report_output_readiness(db, task).get("ok")):
        return {"status": "blocked", "blocked_reason": "supplement_in_progress"}

    impact = supplement_impact.build_supplement_impact_plan(
        category=category,
        item_ids=item_ids,
        material_ids=material_ids,
        subject_ids=subject_ids,
        expected_groups=reference_snapshots.expected_groups(task),
    )
    latest = (db.query(BsReviewVersion.version_no)
              .filter(BsReviewVersion.task_id == task.id)
              .order_by(BsReviewVersion.version_no.desc()).first())
    plan = supplement_generation.build_supplement_generation_plan(
        generation_id=str(generation_id),
        parent_version_no=latest[0] if latest else None,
        parent_review_run_id=parent_review_run_id,
        impact_plan=impact,
        material_ids=material_ids,
        subject_ids=subject_ids,
        material_status=material_status,
    )
    if isinstance(existing, dict) and existing.get("status") in {"running", "completed"}:
        prior_plan = deepcopy(existing)
        prior_plan.pop("previous_supplement_plan", None)
        plan["previous_supplement_plan"] = prior_plan
    plan["impact"] = impact
    findings = db.query(BsFinding).filter(BsFinding.task_id == task.id).all()
    subjects = db.query(BsSubject).filter(BsSubject.task_id == task.id).all()
    plan["supplement_labels"] = supplement_scope_labels(
        findings, impact.get("category"), item_ids, subject_ids,
        material_confirmation.attach_confirmations(
            db, task, checklist.build_summary(task, subjects, findings), findings))
    options = dict(task.options_snapshot_json or {})
    options["supplement_plan"] = _json_safe(plan)
    task.options_snapshot_json = options
    db.flush()
    if plan.get("status") == "planned":
        add_event(db, task.id, "task.supplement_planned", stage="setup", payload=plan)
    else:
        add_event(db, task.id, "task.supplement_blocked", stage="setup", payload=plan)
    db.commit()
    db.refresh(task)
    return deepcopy(plan)


def supplement_issue_associations(db: Session, task_id: int) -> dict:
    """只投影可证明属于同一主体及计划明确列出的核查项，不猜测多主体映射。"""
    events = (db.query(BsTaskEvent).filter(BsTaskEvent.task_id == task_id,
              BsTaskEvent.event_type.in_(["task.supplement_planned", "material.supplement_linked"]))
              .order_by(BsTaskEvent.sequence).all())
    plans, links = {}, {}
    for event in events:
        payload = event.payload_json or {}
        generation = payload.get("generation_id")
        if not generation:
            continue
        if event.event_type == "task.supplement_planned":
            items = payload.get("affected_item_ids") or []
            subjects = payload.get("subject_ids") or []
            if items and len(subjects) == 1 and str(subjects[0]).isdigit():
                clean_items = list(dict.fromkeys(str(item).strip() for item in items if str(item).strip()))
                if len(clean_items) == 1:
                    plans[generation] = {"item_id": clean_items[0], "subject_id": int(subjects[0])}
                elif clean_items:
                    plans[generation] = {"item_ids": clean_items, "subject_id": int(subjects[0])}
        elif isinstance(payload.get("material_id"), int):
            links[payload["material_id"]] = generation
    result = {}
    for material in db.query(BsMaterial).filter_by(task_id=task_id).all():
        association = plans.get(links.get(material.container_material_id or material.id))
        if association and material.subject_id == association["subject_id"]:
            result[material.id] = dict(association)
    return result


def supplement_material_labels(db: Session, task_id: int) -> dict:
    """只投影已显式关联的上传材料；计划中的旧材料不属于上传结果。"""
    events = (db.query(BsTaskEvent)
              .filter(BsTaskEvent.task_id == task_id,
                      BsTaskEvent.event_type == "material.supplement_linked")
              .order_by(BsTaskEvent.sequence).all())
    labels = {}
    for event in events:
        payload = event.payload_json or {}
        material_id, label = payload.get("material_id"), payload.get("label")
        scope = payload.get("scope_labels")
        if isinstance(scope, list):
            parts = [item.strip() for item in scope if isinstance(item, str) and item.strip()]
            if parts:
                label = "、".join(dict.fromkeys(parts))
        if isinstance(material_id, int) and isinstance(label, str) and label.strip():
            labels[material_id] = label.strip()
    return labels


def get_supplement_generation_plan(task: BsTask) -> dict | None:
    """读取当前补件计划；只读投影，不补造历史计划。"""
    plan = (task.options_snapshot_json or {}).get("supplement_plan")
    return deepcopy(plan) if isinstance(plan, dict) else None


def create_review_version(db: Session, task: BsTask, *, trigger: str,
                          frozen_material_pages: dict[int, list[int]] | None = None) -> BsReviewVersion:
    """保存当前结果快照；调用方应与作废旧结论放在同一事务内。"""
    if trigger not in {"completed", "rerun", "supplement", "material_edit"}:
        raise ValueError("不支持的审查版本触发原因")
    latest = (db.query(BsReviewVersion.version_no)
              .filter(BsReviewVersion.task_id == task.id)
              .order_by(BsReviewVersion.version_no.desc()).first())
    version_no = (latest[0] if latest else 0) + 1
    snapshot = _review_version_snapshot(db, task, trigger=trigger)
    if frozen_material_pages:
        frozen = {}
        for material_id, page_nos in frozen_material_pages.items():
            if (type(material_id) is not int or not isinstance(page_nos, list) or not page_nos
                    or any(type(n) is not int or n < 1 for n in page_nos)
                    or len(set(page_nos)) != len(page_nos)):
                raise ValueError("冻结材料页无效")
            material = db.get(BsMaterial, material_id)
            if material is None or material.task_id != task.id:
                raise ValueError("冻结材料不属于当前任务")
            page_map = {}
            for n in page_nos:
                matches = [p for p in (material.pages_json or []) if isinstance(p, dict)
                           and type(p.get("page")) is int and p["page"] == n]
                if len(matches) != 1 or not isinstance(matches[0].get("text"), str):
                    raise ValueError("冻结材料页缺少识别文字或页码不唯一")
                page_map[n] = matches[0]
            frozen[str(material_id)] = {str(n): {
                "material_id": material_id, "page": n, "text": page_map[n]["text"],
                "readable": parsing.material_page_readable(material, n),
            } for n in page_nos}
        snapshot["frozen_material_pages"] = frozen
    version = BsReviewVersion(
        task_id=task.id,
        version_no=version_no,
        trigger=trigger,
        snapshot_json=snapshot,
    )
    db.add(version)
    db.flush()
    return version


def list_review_versions(db: Session, task: BsTask) -> list[dict]:
    rows = (db.query(BsReviewVersion).filter(BsReviewVersion.task_id == task.id)
            .order_by(BsReviewVersion.version_no.desc()).all())
    out = []
    for row in rows:
        findings = (row.snapshot_json or {}).get("findings") or []
        counts = Counter(f.get("status") for f in findings)
        out.append({
            "version_id": row.id,
            "version_no": row.version_no,
            "trigger": row.trigger,
            "created_at": row.created_at.isoformat(timespec="seconds") if row.created_at else None,
            "finding_counts": {key: counts.get(key, 0) for key in ("hit", "miss", "undecidable")},
            # BS-P2-04（设计 98 §4.4）：规则集版本取自快照；本列上线前存的版本为 None。
            "ruleset_version": (row.snapshot_json or {}).get("ruleset_version"),
            "ruleset_manifest": (row.snapshot_json or {}).get("ruleset_manifest"),
        })
    return out


def get_review_version(db: Session, task: BsTask, version_no: int) -> dict | None:
    row = (db.query(BsReviewVersion)
           .filter(BsReviewVersion.task_id == task.id,
                   BsReviewVersion.version_no == version_no).first())
    if row is None:
        return None
    snapshot = deepcopy(row.snapshot_json)
    if snapshot:
        # 分页正文按页授权读取，避免历史版本详情批量返回敏感材料和大段 OCR。
        snapshot.pop("frozen_material_pages", None)
    if snapshot and "ai_advisories" in snapshot:
        snapshot["ai_advisories"] = ai_advisory_snapshots.redact_unavailable_advisories(
            db, task, snapshot["ai_advisories"])
        if isinstance(snapshot.get("report"), dict) and "ai_advisories" in snapshot["report"]:
            snapshot["report"]["ai_advisories"] = deepcopy(snapshot["ai_advisories"])
    if snapshot and "reference_snapshot" in snapshot:
        snapshot["reference_snapshot"] = reference_snapshots.project_reference_snapshot(
            snapshot["reference_snapshot"])
        for kind in ("report", "opinion"):
            if isinstance(snapshot.get(kind), dict) and "reference_snapshot" in snapshot[kind]:
                snapshot[kind]["reference_snapshot"] = deepcopy(snapshot["reference_snapshot"])
    return {
        "version_id": row.id,
        "version_no": row.version_no,
        "trigger": row.trigger,
        "created_at": row.created_at.isoformat(timespec="seconds") if row.created_at else None,
        # 与列表同口径（取快照顶层）；历史快照无此键 → None，不补造。
        "ruleset_version": (row.snapshot_json or {}).get("ruleset_version"),
        "ruleset_manifest": (row.snapshot_json or {}).get("ruleset_manifest"),
        "snapshot": snapshot,
    }


def get_review_version_page(db: Session, task: BsTask, version_no: int,
                            material_id: int, page_no: int) -> dict | None:
    """只取该审查版本明确冻结的页；旧版本缺少快照时不回退当前文字。"""
    row = (db.query(BsReviewVersion).filter(BsReviewVersion.task_id == task.id,
                                           BsReviewVersion.version_no == version_no).first())
    if row is None:
        return None
    pages = (row.snapshot_json or {}).get("frozen_material_pages") or {}
    entry = (pages.get(str(material_id)) or {}).get(str(page_no))
    if (not isinstance(entry, dict) or entry.get("material_id") != material_id or entry.get("page") != page_no
            or not isinstance(entry.get("text"), str) or type(entry.get("readable")) is not bool):
        return None
    return deepcopy(entry)


# ==================== 版本差异（BS-P2-02 / 设计 98 §5.1） ====================
# 跨版本键：`(item_id, material_id, page)`——74 §M2 定；finding_id 重跑后重建不可用。
_DIFF_BUCKETS = ("added", "resolved", "persisted", "status_changed")
# persisted/status_changed 的「变了哪些字段」只看这四项（status 单独成桶）。
_DIFF_VALUE_FIELDS = ("severity", "message", "quote", "page")
# 明细每桶上限（任务规模远小于此，仅为防御）；超出给 truncated 布尔。
_DIFF_ITEM_LIMIT = 500
_DIFF_NOTES = (
    "四数互斥且并集=两版并集；「已解决」=上一版有、本版没有（不等于问题已解决，可能是规则不再产出）",
    "「仅登记」兜底条款计入明细与四数，但不构成业务结论（open_term=true）",
    "四数基于两版全部逐项结论（含未命中），不是仅问题数；运营解读时「新增/已解决」指结论行",
)


def _parse_version_ref(ref) -> int | None:
    """版本引用解析：整数版本号，或字面量 "current"（当前库内结论）。

    None=current；非法 → ValueError（router 转 400）。`a==b` 合法。
    """
    text = str(ref).strip()
    if text == "current":
        return None
    if not re.fullmatch(r"[0-9]+", text):
        raise ValueError("版本参数仅支持版本号整数或 current")
    value = int(text)
    if value < 1:
        raise ValueError("版本号必须为正整数")
    return value


def _diff_group(rows: list) -> dict:
    """按跨版本键归并，同键多条按 finding_id 稳定排序（逐条配对用）。"""
    grouped = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = (row.get("subject_id"), row.get("item_id"),
               row.get("material_id"), row.get("page"))
        grouped.setdefault(key, []).append(row)
    for key in grouped:
        grouped[key].sort(key=lambda row: (row.get("finding_id") is None,
                                           row.get("finding_id") or 0))
    return grouped


def _diff_changed_fields(a: dict, b: dict, *, include_status: bool = False) -> list:
    fields = ["status"] if include_status else []
    fields += [name for name in _DIFF_VALUE_FIELDS if a.get(name) != b.get(name)]
    return fields


def _diff_entry(key: tuple, a: dict | None, b: dict | None, changed_fields: list,
                open_ids: set) -> dict:
    subject_id, item_id, material_id, page = key
    return {
        "subject_id": subject_id, "item_id": item_id,
        "material_id": material_id, "page": page,
        "kind": (b or a).get("kind"),
        "status_a": None if a is None else a.get("status"),
        "status_b": None if b is None else b.get("status"),
        "severity_a": None if a is None else a.get("severity"),
        "severity_b": None if b is None else b.get("severity"),
        "message_a": None if a is None else a.get("message"),
        "message_b": None if b is None else b.get("message"),
        "quote_a": None if a is None else a.get("quote"),
        "quote_b": None if b is None else b.get("quote"),
        # 兜底条款由目录派生（与 reporting.is_open_term 同一数据源），不硬编码 ID 清单。
        "open_term": item_id in open_ids,
        "changed_fields": changed_fields,
    }


def _diff_side(db: Session, task: BsTask, version_no: int | None, ref: str, user) -> dict:
    if version_no is None:
        rows = (db.query(BsFinding).filter(BsFinding.task_id == task.id)
                .order_by(BsFinding.id).all())
        visible = ai_advisory_snapshots.filter_current_candidate_findings(db, task, user, rows)
        if len(visible) != len(rows):
            raise AiContextChanged("当前候选线索的核查代或依据授权已变化，暂不能比较版本")
        rows = visible
        findings = [{"finding_id": row.id, "subject_id": row.subject_id,
                     "item_id": row.item_id,
                     "material_id": row.material_id, "page": row.page, "kind": row.kind,
                     "status": row.status, "severity": row.severity,
                     "message": row.message, "quote": row.quote} for row in rows]
        return {"ref": ref, "version_no": None, "trigger": None, "created_at": None,
                "ruleset_version": task.ruleset_version,
                "ruleset_manifest": _frozen_ruleset_manifest(task), "findings": findings}
    row = (db.query(BsReviewVersion)
           .filter(BsReviewVersion.task_id == task.id,
                   BsReviewVersion.version_no == version_no).first())
    if row is None:
        raise LookupError("审查版本不存在")
    snapshot = reference_visibility.visible_version(
        db, task, {"snapshot": row.snapshot_json or {}}, user)["snapshot"]
    if any(item.get("code") == "candidate_access_changed" for item in snapshot.get("limitations") or []
           if isinstance(item, dict)):
        raise AiContextChanged("历史候选线索的依据授权已变化，暂不能比较版本")
    return {
        "ref": ref, "version_no": row.version_no, "trigger": row.trigger,
        "created_at": row.created_at.isoformat(timespec="seconds") if row.created_at else None,
        "ruleset_version": snapshot.get("ruleset_version"),
        "ruleset_manifest": snapshot.get("ruleset_manifest"),
        "findings": snapshot.get("findings") or [],
    }


def version_diff(db: Session, task: BsTask, ref_a, ref_b, *, user=None) -> dict:
    """两版逐项结论差异（设计 98 §5.1）：四数互斥、覆盖并集，键 (item_id, material_id, page)。

    ref = 版本号整数或字面量 "current"；版本不存在 → LookupError（router 404），
    非法 ref → ValueError（router 400）。`a==b` 合法（结果全为仍存在）。
    """
    version_a = _parse_version_ref(ref_a)
    version_b = _parse_version_ref(ref_b)
    user = user or SimpleNamespace(id=task.created_by, user_type=task.created_by_type)
    side_a = _diff_side(db, task, version_a, str(ref_a).strip(), user)
    side_b = _diff_side(db, task, version_b, str(ref_b).strip(), user)
    grouped_a = _diff_group(side_a["findings"])
    grouped_b = _diff_group(side_b["findings"])
    open_ids = reporting.open_term_item_ids()  # 一次读盘，不逐条目重读规则目录
    buckets = {bucket: [] for bucket in _DIFF_BUCKETS}
    for key in sorted(set(grouped_a) | set(grouped_b),
                      key=lambda k: (str(k[0]), k[1] is None, k[1] or 0,
                                     k[2] is None, k[2] or 0)):
        rows_a = grouped_a.get(key) or []
        rows_b = grouped_b.get(key) or []
        paired = min(len(rows_a), len(rows_b))
        for index in range(paired):
            a, b = rows_a[index], rows_b[index]
            if a.get("status") == b.get("status"):
                buckets["persisted"].append(
                    _diff_entry(key, a, b, _diff_changed_fields(a, b), open_ids))
            else:
                buckets["status_changed"].append(
                    _diff_entry(key, a, b, _diff_changed_fields(a, b, include_status=True),
                                open_ids))
        for a in rows_a[paired:]:
            buckets["resolved"].append(_diff_entry(key, a, None, ["status"], open_ids))
        for b in rows_b[paired:]:
            buckets["added"].append(_diff_entry(key, None, b, ["status"], open_ids))
    items = {}
    truncated = {}
    for bucket in _DIFF_BUCKETS:
        rows = buckets[bucket]
        truncated[bucket] = len(rows) > _DIFF_ITEM_LIMIT
        items[bucket] = rows[:_DIFF_ITEM_LIMIT]
    counts = {bucket: len(buckets[bucket]) for bucket in _DIFF_BUCKETS}
    # 分母一并交回（设计 98 §5.1 响应合同）：四数是「两版逐项结论」的子集，
    # 没有 total_a/total_b 的话「四数相加≠两版总数」会被读成漏项。
    counts["total_a"] = len(side_a["findings"])
    counts["total_b"] = len(side_b["findings"])
    sides = {}
    for label, side, ref in (("a", side_a, ref_a), ("b", side_b, ref_b)):
        sides[label] = {
            "ref": side["ref"], "version_no": side["version_no"],
            "trigger": side["trigger"], "created_at": side["created_at"],
            "ruleset_version": side["ruleset_version"],
            "ruleset_manifest": side["ruleset_manifest"],
            "finding_count": len(side["findings"]),
        }
    return {
        "task_id": task.id,
        "a": sides["a"],
        "b": sides["b"],
        "counts": counts,
        "items": dict(items, truncated=truncated),
        "notes": list(_DIFF_NOTES),
    }


def task_reading_coverage(db: Session, task: BsTask) -> dict:
    """当前任务规则输入材料的读取覆盖投影。

    `parse_state=done` 只表示解析流程退出，不代表每页都有正文；最终收口和
    确认稿导出必须消费这里的统一投影。没有活动输入材料时返回 ``complete=None``，
    保留历史/契约任务的兼容读取口径，真正的已上传任务会得到 True/False。
    """
    materials = (db.query(BsMaterial)
                 .filter(BsMaterial.task_id == task.id)
                 .order_by(BsMaterial.id).all())
    inputs = material_versions.active_input_materials(materials)
    rows = []
    for material in inputs:
        coverage = parsing.reading_coverage(material)
        rows.append({
            "material_id": material.id,
            "file_name": material.file_name,
            "parse_state": material.parse_state,
            **coverage,
        })
    complete = None if not rows else all(row["reading_complete"] for row in rows)
    incomplete = [row for row in rows if not row["reading_complete"]]
    return {
        "complete": complete,
        "status": ("unknown" if complete is None else
                   ("complete" if complete else "incomplete")),
        "materials": rows,
        "incomplete_materials": incomplete,
    }


def review_output_readiness(db: Session, task: BsTask) -> dict:
    """只读核对当前结果的技术完整性；不恢复运行、不建批次、不复核或冻结。

    新任务必须有同代、同输入的所选组终态。无批次记录的历史任务只兼容已保存的
    全部必需结论，报告正文另明示其运行记录缺口，绝不补造本轮运行证据。
    """
    reading = task_reading_coverage(db, task)
    if any(row.get("parse_state") == "failed" for row in reading["materials"]):
        return {"ok": False, "code": "reading_incomplete", "missing_groups": [],
                "pending_groups": [], "reading_coverage": reading,
                "detail": "有材料解析失败，请重新读取材料后再完成核查"}
    selected = {group_id: group for group_id, group in reference_snapshots.CHECK_GROUPS.items()
                if (task.scope_a1 if group["scope"] == "a1" else task.scope_a3)}
    kinds = {kind for (kind,) in db.query(BsFinding.kind)
             .filter(BsFinding.task_id == task.id).distinct()}
    missing = [group_id for group_id, group in selected.items()
               if any(kind not in kinds for kind in group["kinds"])]
    pending = []
    query_pending = False
    snapshot = reference_snapshots.get_reference_snapshot(db, task)
    if snapshot:
        expected = snapshot.get("expected_groups")
        if (not isinstance(expected, list) or not all(isinstance(group, str) for group in expected)
                or len(expected) != len(selected) or set(expected) != set(selected)):
            return {"ok": False, "code": "review_incomplete", "missing_groups": list(selected),
                    "pending_groups": [], "detail": "本轮核查运行记录缺失或身份不唯一，请重新核查后再生成报告",
                    "reading_coverage": reading}
        if (snapshot.get("material_fingerprint") != knowledge_runs.material_fingerprint(db, task)
                or snapshot.get("rule_fingerprint") != knowledge_runs.rule_fingerprint()):
            return {"ok": False, "code": "review_inputs_changed", "missing_groups": [],
                    "pending_groups": [], "detail": "材料或依据版本已变化，请重新核查本任务后再生成报告",
                    "reading_coverage": reading}
        pending = list(snapshot.get("pending_groups") or [])
        query_pending = any(run.get("state") == "running" for run in snapshot.get("runs") or [])
        missing = list(dict.fromkeys([*(snapshot.get("missing_groups") or []), *missing]))
        if snapshot.get("state") != "completed" and not missing and not pending:
            missing = list(selected)
    else:
        # 兼容旧单组运行指针：仍在查询的任务不能拿此前的结论出完整报告。
        reference_run = knowledge_runs.get_reference_run(db, task)
        if reference_run and reference_run["state"] != "completed":
            pending = [reference_run.get("group_id", "a1")]
            query_pending = reference_run["state"] == "running"
    if missing or pending:
        groups = list(dict.fromkeys([*missing, *pending]))
        labels = "、".join(reference_snapshots.CHECK_GROUPS[group]["label"] for group in groups)
        return {"ok": False, "code": "review_incomplete", "missing_groups": missing,
                "pending_groups": pending, "reading_coverage": reading,
                "detail": ("参考依据仍在查询，" if query_pending else "")
                          + "以下核查分组尚未完成：%s。请完成后再生成报告" % labels}
    return {"ok": True, "missing_groups": [], "pending_groups": [],
            "reading_coverage": reading}


def supplement_active(task: BsTask) -> bool:
    options = task.options_snapshot_json or {}
    plan, edit = options.get("supplement_plan") or {}, options.get("supplement_edit") or {}
    return bool(isinstance(plan, dict) and isinstance(edit, dict) and plan.get("generation_id")
                and plan.get("generation_id") == edit.get("generation_id")
                and plan.get("status") in {"planned", "running"})


def report_output_readiness(db: Session, task: BsTask, *, require_reading_complete: bool = False) -> dict:
    """报告读取的只读判据：允许已启动任务先查看中间结果。

    这条判据刻意不替代 ``review_output_readiness``：后者仍用于收口和生成
    历史版本，必须要求所有选定分组完成；本函数只决定报告读取/导出是否有
    足够的运行痕迹。材料或依据指纹变化、当前代快照缺失等完整性问题仍返回
    409，防止把旧结果伪装成当前结果。
    """
    options = task.options_snapshot_json or {}
    plan = options.get("supplement_plan") or {}
    edit = options.get("supplement_edit") or {}
    if supplement_active(task):
        if plan.get("status") != "running" or task.state in {TASK_STATE_SETUP, TASK_STATE_PREPARING}:
            return {"ok": False, "code": "supplement_in_progress", "missing_groups": [],
                    "pending_groups": [], "detail": "补件尚未完成，本轮报告暂不可用；可查看上次结果"}
        readiness = review_output_readiness(db, task)
        if not readiness["ok"]:
            return readiness
        snapshot = reference_snapshots.get_reference_snapshot(db, task)
        if not snapshot:
            return {"ok": False, "code": "review_incomplete", "missing_groups": [],
                    "pending_groups": [], "detail": "本轮核查记录不可用，请刷新或重试"}
        if snapshot.get("ai_auto_enabled") is True:
            if current_ai_advisory_pending(db, task):
                return {"ok": False, "code": "supplement_in_progress", "missing_groups": [],
                        "pending_groups": [], "detail": "本轮 AI 辅助核查尚在执行，请等待完成"}
            from . import background_ai_review
            if background_ai_review.job_status(db, task, snapshot.get("review_run_id"))["state"] != "completed":
                return {"ok": False, "code": "supplement_in_progress", "missing_groups": [],
                        "pending_groups": [], "detail": "本轮 AI 辅助核查尚未完成，请等待或重试"}
            coverage = ai_advisory_snapshots.collect_current_coverage(db, task, snapshot, reporting.report_review_blocking)
            groups = (coverage or {}).get("groups") or []
            if (not coverage or coverage.get("state") != "available"
                    or {group.get("group_id") for group in groups} != set(snapshot.get("ai_auto_groups") or [])
                    or any(group.get("state") != "available" or
                           any((group.get("counts") or {}).get(name, 0) for name in ("not_started", "pending")) or
                           (group.get("counts") or {}).get("failed", 0) > group.get("reviewable_failed", 0) or
                           (group.get("counts") or {}).get("partial", 0) > group.get("reviewable_partial", 0) or
                           (group.get("counts") or {}).get("not_run", 0) > group.get("reviewable_not_run", 0)
                           for group in groups)):
                return {"ok": False, "code": "supplement_in_progress", "missing_groups": [],
                        "pending_groups": [], "detail": "本轮 AI 辅助核查尚未完成，请等待或重试"}
    reading = task_reading_coverage(db, task)
    if require_reading_complete and any(row.get("parse_state") == "failed" for row in reading["materials"]):
        return {"ok": False, "code": "reading_incomplete", "missing_groups": [],
                "pending_groups": [], "reading_coverage": reading,
                "detail": "有材料解析失败，请重新读取材料后再生成确认报告"}
    finding_count = (db.query(BsFinding)
                     .filter(BsFinding.task_id == task.id)
                     .count())
    options = task.options_snapshot_json or {}
    review_pointer = options.get(reference_snapshots.REVIEW_POINTER)
    legacy_pointer = options.get(reference_snapshots.LEGACY_POINTER)
    snapshot = reference_snapshots.get_reference_snapshot(db, task)

    # 没有启动指针、没有任何结论：仍保留前置提示，避免空白报告误导用户。
    if not review_pointer and not legacy_pointer and not finding_count:
        return {
            "ok": False, "code": "review_not_started",
            "missing_groups": [], "pending_groups": [],
            "detail": "本轮尚未开始核查，请先完成材料上传并开始核查后再查看报告",
        }

    # 当前代启动过但固定依据 seed 丢失/身份不唯一，不能把历史 findings 当作当前代报告。
    if review_pointer and (not snapshot or any(
            item.get("code") == "review_seed_unavailable"
            for item in snapshot.get("limitations") or [])):
        return {
            "ok": False, "code": "review_incomplete",
            "missing_groups": list(reference_snapshots.CHECK_GROUPS),
            "pending_groups": [],
            "detail": "本代固定依据快照缺失或身份不唯一，请重新核查后再生成报告",
        }

    if review_pointer and snapshot:
        selected_groups = [group_id for group_id, group in reference_snapshots.CHECK_GROUPS.items()
                           if (task.scope_a1 if group["scope"] == "a1" else task.scope_a3)]
        expected_groups = snapshot.get("expected_groups")
        if (not isinstance(expected_groups, list)
                or len(expected_groups) != len(selected_groups)
                or set(expected_groups) != set(selected_groups)):
            return {
                "ok": False, "code": "review_incomplete",
                "missing_groups": selected_groups, "pending_groups": [],
                "detail": "本代固定依据快照的核查分组不完整或身份不唯一，请重新核查后再生成报告",
            }

    if legacy_pointer and not review_pointer:
        legacy_run = knowledge_runs.get_reference_run(db, task)
        if not legacy_run or legacy_run.get("state") != "completed":
            return {
                "ok": False, "code": "review_incomplete",
                "missing_groups": [],
                "pending_groups": [legacy_run.get("group_id", "a1")]
                    if legacy_run else [],
                "detail": "参考依据运行记录尚未完成或已失效，请重新核查后再生成报告",
            }

    # 指纹变化是硬阻断：中间报告也不能静默混用新材料/新规则与旧结论。
    if snapshot:
        material_fingerprint = knowledge_runs.material_fingerprint(db, task)
        if (snapshot.get("material_fingerprint") != material_fingerprint
                or snapshot.get("rule_fingerprint") != knowledge_runs.rule_fingerprint()):
            return {
                "ok": False, "code": "review_inputs_changed",
                "missing_groups": [], "pending_groups": [],
                "detail": "材料或依据版本已变化，请重新核查本任务后再生成报告",
            }

    coverage = reporting.build_coverage(db, task, finding_count=finding_count)
    return {
        "ok": True,
        "missing_groups": coverage["missing_groups"],
        "pending_groups": coverage["pending_groups"],
        "coverage": coverage,
        "limitations": coverage["limitations"],
        "reading_coverage": reading,
    }


def complete_review(db: Session, task: BsTask) -> dict:
    """收口任务：范围分组有结论且所有命中问题均已采纳或忽略后才允许置 done。

    拍板口径：不做 awaiting_human_review 闸（人工复核走线下，P1-3 回执留痕）。
    缺组返回 ok=False + detail 文案（组名取 CHECK_GROUPS 单一表源）；
    done 态重复收口幂等返回。
    """
    # HTTP归属校验仅有读取；先释放其RR快照，再锁定当前版本进行收口。
    db.rollback()
    db.refresh(task, with_for_update=True)
    if task.state == "done":
        return {"ok": True}
    if task.state not in ("running", "review"):
        return {"ok": False, "detail": "请先完成两段式启动并完成核查，再收口任务"}
    if recover_reference_runs(db, task):
        db.commit()
        db.refresh(task, with_for_update=True)
    readiness = review_output_readiness(db, task)
    if not readiness["ok"]:
        return readiness
    if current_ai_advisory_pending(db, task):
        return {"ok": False, "detail": "AI 辅助核查正在进行，请等待完成后再收口任务"}
    reference_snapshot = reference_snapshots.get_reference_snapshot(db, task)
    if reference_snapshot and reference_snapshot.get("ai_auto_enabled") is True:
        coverage = ai_advisory_snapshots.collect_current_coverage(db, task, reference_snapshot, reporting.report_review_blocking)
        groups = (coverage or {}).get("groups") or []
        if (not coverage or coverage.get("state") != "available"
                or {group.get("group_id") for group in groups} != set(reference_snapshot.get("ai_auto_groups") or [])
                or any(group.get("state") != "available" for group in groups)):
            return {"ok": False, "detail": "AI 辅助核查状态暂不可确认，请返回核查页刷新或重试"}
        if coverage and any(group.get("counts", {}).get("not_started", 0)
                            for group in coverage.get("groups") or [] if group.get("state") == "available"):
            return {"ok": False, "detail": "仍有 AI 辅助核查尚未开始，请返回核查页等待或重试"}
        if any((group.get("counts") or {}).get("pending", 0)
               or (group.get("counts") or {}).get("failed", 0) > group.get("reviewable_failed", 0)
               or (group.get("counts") or {}).get("partial", 0) > group.get("reviewable_partial", 0)
               or (group.get("counts") or {}).get("not_run", 0) > group.get("reviewable_not_run", 0)
               for group in coverage.get("groups") or []):
            return {"ok": False, "detail": "部分 AI 辅助核查未完成，请返回核查页重试"}
    pending_hits = (db.query(BsFinding)
                    .filter(BsFinding.task_id == task.id,
                            BsFinding.status.in_(("hit", "undecidable")))
                    .all())
    pending_hits = ai_advisory_snapshots.filter_current_candidate_findings(
        db, task, SimpleNamespace(id=task.created_by, user_type=task.created_by_type), pending_hits)
    pending_count = sum(
        1 for finding in pending_hits
        if reporting.report_review_blocking(finding) and reporting.canonical_review_decision(
            (finding.review_json or {}).get("decision")) not in ("accepted", "ignored")
    )
    if pending_count:
        return {"ok": False,
                "detail": "还有 %d 项问题尚未处理，请在左侧选择“已采纳”或“已忽略”后再完成任务收口" % pending_count}
    # BS-P2-04（117-B §3.1）：规则版本已经在启动/重跑时冻结；收口只清除回执
    # 携带表并沿用冻结值。历史任务没有冻结值时继续为 NULL，不用当前规则补造。
    options = dict(task.options_snapshot_json or {})
    options.pop("rerun", None)
    supplement_plan = options.get("supplement_plan")
    supplement_completed = False
    if isinstance(supplement_plan, dict) and supplement_plan.get("status") == "running":
        # P8-03C：补件代只有在当前代真正收口后才进入 completed；避免长期显示为“运行中”。
        supplement_plan = dict(supplement_plan)
        supplement_plan.update({
            "status": "completed",
            "completed_at": datetime.now().isoformat(timespec="seconds"),
            "completed_version_no": None,  # 在创建收口版本后回填
        })
        options["supplement_plan"] = supplement_plan
        supplement_completed = True
    task.options_snapshot_json = options
    version = create_review_version(db, task, trigger="completed")
    if supplement_completed:
        supplement_plan["completed_version_no"] = version.version_no
        task.options_snapshot_json = options
    task.state = "done"
    db.commit()
    add_event(db, task.id, "task.completed",
              payload={"state": "done", "version_no": version.version_no})
    if supplement_completed:
        add_event(db, task.id, "task.supplement_completed", stage="check", payload={
            "generation_id": supplement_plan.get("generation_id"),
            "version_no": version.version_no,
            "execution_scope": supplement_plan.get("execution_scope"),
        })
    return {"ok": True, "version_no": version.version_no}


def _clean_rerun_item_ids(item_ids) -> tuple:
    """校验 `scope=items` 的 item_ids（设计 98 §4.1）：1–200 个、每个 1–40 字符、去重。

    返回 `(cleaned, error)`：error 非 None 时由 router 转 400。**不要求 item_id 在旧结论里
    存在**——规则新增的条目也要能重跑。直调 service 的路径同样守住这道校验。
    """
    if not isinstance(item_ids, (list, tuple)):
        return None, "按条目重跑时必须提供 item_ids 数组"
    cleaned = []
    for item in item_ids:
        if not isinstance(item, str):
            return None, "item_ids 只能是字符串"
        text = item.strip()
        if not 1 <= len(text) <= 40:
            return None, "item_id 长度须为 1–40 个字符"
        if text not in cleaned:
            cleaned.append(text)
    if not 1 <= len(cleaned) <= 200:
        return None, "按条目重跑须提供 1–200 个 item_id"
    return cleaned, None


def _single_linked_packet_pdf(db: Session, task: BsTask, generation_id: str | None):
    """只识别本代唯一新上传的普通报审 PDF；其他形态不推断。"""
    if not generation_id:
        return None
    events = (db.query(BsTaskEvent).filter_by(task_id=task.id)
              .order_by(BsTaskEvent.sequence).all())
    edits = [e for e in events if e.event_type == "task.supplement_edit_started"
             and (e.payload_json or {}).get("generation_id") == generation_id]
    links = [e for e in events if e.event_type == "material.supplement_linked"
             and (e.payload_json or {}).get("generation_id") == generation_id]
    if len(edits) != 1 or len(links) != 1 or links[0].sequence <= edits[0].sequence:
        return None
    mid = (links[0].payload_json or {}).get("material_id")
    if type(mid) is not int:
        return None
    mat = db.get(BsMaterial, mid)
    if (mat is None or mat.task_id != task.id or mat.role != "packet"
            or mat.via != "upload" or mat.parse_state != "done"
            or not str(mat.file_name or "").lower().endswith(".pdf")
            or mat.subject_id is None or mat.container_material_id is not None
            or mat.replacement_target_id is not None or mat.superseded_by_id is not None
            or internal_records.is_internal_record_candidate(mat)):
        return None
    return mat


def _carry_forward_source_runs(db: Session, task: BsTask, group_ids: list[str], *,
                               material_fingerprint: str,
                               rule_fingerprint: str,
                               parent_reference: dict | None = None) -> tuple[dict, str | None]:
    """严格读取旧代唯一终态；任一事实不可证明时由调用方全量回退。

    补件编辑会清空当前活动指针，但会把父代指针放入只读
    ``supplement_parent_reference``。无法证明材料变化的完整影响时，必须回退全量。
    """
    if not group_ids:
        return {}, None
    options = task.options_snapshot_json or {}
    parent = parent_reference if isinstance(parent_reference, dict) else {}
    review_id = (options.get(reference_snapshots.REVIEW_POINTER)
                 or parent.get(reference_snapshots.REVIEW_POINTER))
    if not review_id:
        return {}, "reference_review_unavailable"
    seeds = db.query(BsTaskEvent).filter(
        BsTaskEvent.task_id == task.id,
        BsTaskEvent.event_type == reference_snapshots.REVIEW_EVENT,
        BsTaskEvent.payload_json["review_run_id"].as_string() == review_id,
    ).all()
    if len(seeds) != 1:
        return {}, "reference_review_unavailable"
    seed = seeds[0].payload_json or {}
    if seed.get("material_fingerprint") != material_fingerprint:
        return {}, "reference_material_changed"
    if seed.get("rule_fingerprint") != rule_fingerprint:
        return {}, "reference_runtime_rule_changed"

    source_runs = {}
    parent_pointers = parent.get(reference_snapshots.GROUP_POINTERS) or {}
    current_pointers = options.get(reference_snapshots.GROUP_POINTERS) or {}
    for group_id in group_ids:
        run_id = (current_pointers.get(group_id) or parent_pointers.get(group_id)
                  or knowledge_runs.group_pointer(task, group_id))
        if not run_id:
            return {}, "reference_carry_unavailable"
        terminals = db.query(BsTaskEvent).filter(
            BsTaskEvent.task_id == task.id,
            BsTaskEvent.event_type == knowledge_runs.RUN_EVENT,
            BsTaskEvent.payload_json["review_run_id"].as_string() == review_id,
            BsTaskEvent.payload_json["group_id"].as_string() == group_id,
            BsTaskEvent.payload_json["run_id"].as_string() == run_id,
        ).all()
        if len(terminals) != 1 or (terminals[0].payload_json or {}).get("state") != "completed":
            return {}, "reference_carry_unavailable"
        payload = deepcopy(terminals[0].payload_json)
        findings = _reference_group_findings(db, task.id, group_id)
        finding_ids = sorted(finding.id for finding in findings)
        if sorted(payload.get("finding_ids") or []) != finding_ids:
            return {}, "reference_findings_changed"
        if (not payload.get("finding_fingerprint")
                or payload["finding_fingerprint"] != knowledge_runs.finding_fingerprint(findings)):
            return {}, "reference_findings_changed"
        linked_ids = {link.get("finding_id") for link in payload.get("finding_links") or []}
        if not linked_ids <= set(finding_ids):
            return {}, "reference_findings_changed"
        source_runs[group_id] = payload
    return source_runs, None


def _write_carried_forward_runs(db: Session, task: BsTask, *, seed: dict,
                                source_runs: dict, previous_ruleset_version: str | None) -> None:
    """在新代写显式沿用终态；旧终态原文、限制、链接和原时间戳保持不变。"""
    carried_at = datetime.now().isoformat(timespec="seconds")
    for group_id, source in source_runs.items():
        payload = deepcopy(source)
        origin = {
            "review_run_id": source.get("review_run_id"),
            "run_id": source.get("run_id"),
            "ruleset_version": previous_ruleset_version,
            "material_fingerprint": source.get("material_fingerprint"),
            "rule_fingerprint": source.get("rule_fingerprint"),
        }
        payload.update({
            "run_id": uuid.uuid4().hex,
            "review_run_id": seed["review_run_id"],
            "state": "completed",
            "execution_mode": "carried_forward",
            "carried_at": carried_at,
            "origin": origin,
            "previous_run_id": source.get("run_id"),
            "expected_previous_id": None,
            "material_fingerprint": seed["material_fingerprint"],
            "rule_fingerprint": seed["rule_fingerprint"],
            "dependency_fingerprint": knowledge_runs.dependency_fingerprint(
                db, group_id, task.id),
            "task_state": task.state,
            "ruleset_version": task.ruleset_version,
        })
        knowledge_runs.activate_reference_run(task, payload)
        add_event(db, task.id, knowledge_runs.RUN_EVENT, stage="check",
                  payload=payload, commit=False)


def _prepare_targeted_page_refresh(db: Session, task: BsTask, material_id: int, page_nos: list[int], *, prepare) -> dict:
    """只准备候选，冻结所基于的任务输入；不修改任务或创建核查代。"""
    bind, task_id = db.get_bind(), task.id
    with Session(bind=bind) as read_db:
        current = read_db.get(BsTask, task_id)
        if current is None or current.state not in ("running", "review", "done"):
            raise ValueError("任务状态不允许补读")
        material = next((m for m in _rule_materials(read_db, current) if m.id == material_id), None)
        if material is None or material.parse_state != "done" or not material.path:
            raise ValueError("材料不可补读或不属于当前有效材料")
        unread = parsing.material_reading_gap(material)
        if (not isinstance(page_nos, list) or not page_nos or unread is None
                or any(type(n) is not int or n not in unread for n in page_nos)):
            raise ValueError("只能补读已明确标记为未读的页")
        parsed = (read_db.query(BsTaskEvent).filter(
            BsTaskEvent.task_id == task_id, BsTaskEvent.event_type == "material.parsed",
            BsTaskEvent.payload_json["material_id"].as_integer() == material_id,
        ).order_by(BsTaskEvent.sequence.desc()).first())
        source_sha = (parsed.payload_json or {}).get("input_sha256") if parsed else None
        if (not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", source_sha)
                or (parsed.payload_json or {}).get("input_sha256_verified") is not True):
            raise ValueError("原解析未记录经读取前后核验的原件指纹，不能安全复用")
        fingerprint = knowledge_runs.material_fingerprint(read_db, current)
        rule_hash = knowledge_runs.rule_fingerprint()
        review_id = (current.options_snapshot_json or {}).get(reference_snapshots.REVIEW_POINTER)
        state, path, pages = current.state, material.path, deepcopy(material.pages_json or [])
    candidate = prepare(material_abs_path(path), pages, page_nos, expected_sha256=source_sha)
    with Session(bind=bind) as verify_db:
        latest = verify_db.get(BsTask, task_id)
        if (latest is None or latest.state != state
                or (latest.options_snapshot_json or {}).get(reference_snapshots.REVIEW_POINTER) != review_id
                or knowledge_runs.material_fingerprint(verify_db, latest) != fingerprint
                or knowledge_runs.rule_fingerprint() != rule_hash):
            raise ValueError("任务输入在补读期间变化，候选已丢弃")
    return {**candidate, "task_id": task_id, "material_id": material_id,
            "input_material_fingerprint": fingerprint, "input_rule_fingerprint": rule_hash,
            "review_run_id": review_id, "task_state": state}


def prepare_targeted_roster_refresh(db: Session, task: BsTask, material_id: int, page_nos: list[int]) -> dict:
    return _prepare_targeted_page_refresh(db, task, material_id, page_nos,
                                          prepare=parsing.prepare_targeted_roster_candidate)


def prepare_targeted_page_text_refresh(db: Session, task: BsTask, material_id: int, page_nos: list[int]) -> dict:
    """准备证件页正文候选；只读且必须保留待核状态。"""
    candidate = _prepare_targeted_page_refresh(db, task, material_id, page_nos,
                                               prepare=parsing.prepare_targeted_page_text_candidate)
    candidate["signature"] = _page_text_candidate_signature(candidate)
    return candidate


_PAGE_TEXT_REFRESH_EVENT = "material.page_text_refresh"
_PAGE_TEXT_REFRESH_LEASE_SECONDS = 180


def _latest_page_text_refresh(db: Session, task_id: int):
    return (db.query(BsTaskEvent).filter_by(task_id=task_id, event_type=_PAGE_TEXT_REFRESH_EVENT)
            .order_by(BsTaskEvent.sequence.desc()).first())


def _page_text_refresh_live(payload: dict) -> bool:
    if payload.get("state") != "running":
        return False
    try:
        deadline = datetime.fromisoformat(payload["expires_at"])
        return deadline.tzinfo is not None and deadline > datetime.now(timezone.utc)
    except (KeyError, TypeError, ValueError):
        return False


def _page_text_refresh_deadline_valid(payload: dict) -> bool:
    try:
        return datetime.fromisoformat(payload["expires_at"]).tzinfo is not None
    except (KeyError, TypeError, ValueError):
        return False


def get_page_text_refresh(db: Session, task_id: int) -> dict | None:
    event = _latest_page_text_refresh(db, task_id)
    if event is None:
        return None
    result = dict(event.payload_json or {})
    if result.get("state") == "running" and not _page_text_refresh_live(result):
        result["state"] = "failed"
        result["reason"] = "expired" if _page_text_refresh_deadline_valid(result) else "invalid_lease"
    return result


def claim_page_text_refresh(db: Session, task: BsTask, material_id: int, page_nos: list[int]) -> dict:
    """任务行锁保护唯一租约；重复请求复用，不保存材料正文。"""
    if (type(material_id) is not int or not isinstance(page_nos, list) or not page_nos or len(page_nos) > 20
            or any(type(n) is not int or n < 1 for n in page_nos) or len(set(page_nos)) != len(page_nos)):
        raise ValueError("补读材料或页码无效")
    db.rollback()
    db.refresh(task, with_for_update=True)
    last = _latest_page_text_refresh(db, task.id)
    current = dict(last.payload_json or {}) if last else {}
    if current.get("state") == "running" and not _page_text_refresh_deadline_valid(current):
        raise ValueError("补读运行记录异常，请联系管理员处理")
    if _page_text_refresh_live(current):
        if current.get("material_id") != material_id or current.get("pages") != sorted(page_nos):
            raise ValueError("当前任务已有其他补读正在进行")
        return {**current, "accepted": False}
    if task.state not in ("running", "review", "done"):
        raise ValueError("任务状态不允许补读")
    material = next((m for m in _rule_materials(db, task) if m.id == material_id), None)
    unread = parsing.material_reading_gap(material) if material is not None else None
    if (material is None or material.parse_state != "done" or not material.path or unread is None
            or any(page not in unread for page in page_nos)):
        raise ValueError("只能补读当前有效材料中已明确标记为未读的页")
    payload = {"run_id": uuid.uuid4().hex, "state": "running", "material_id": material_id,
               "pages": sorted(page_nos), "expires_at": (datetime.now(timezone.utc)
                    + timedelta(seconds=_PAGE_TEXT_REFRESH_LEASE_SECONDS)).isoformat()}
    add_event(db, task.id, _PAGE_TEXT_REFRESH_EVENT, stage="check", payload=payload, commit=False)
    db.commit()
    return {**payload, "accepted": True}


def renew_page_text_refresh(db: Session, task_id: int, run_id: str) -> bool:
    db.rollback()
    task = db.query(BsTask).filter_by(id=task_id).with_for_update().first()
    event = _latest_page_text_refresh(db, task_id) if task is not None else None
    current = dict(event.payload_json or {}) if event else {}
    if current.get("run_id") != run_id or not _page_text_refresh_live(current):
        db.rollback()
        return False
    current["expires_at"] = (datetime.now(timezone.utc) + timedelta(seconds=_PAGE_TEXT_REFRESH_LEASE_SECONDS)).isoformat()
    add_event(db, task_id, _PAGE_TEXT_REFRESH_EVENT, stage="check", payload=current, commit=False)
    db.commit()
    return True


def finish_page_text_refresh(db: Session, task_id: int, run_id: str, state: str) -> bool:
    if state not in {"failed", "completed"}:
        raise ValueError("补读终态无效")
    db.rollback()
    task = db.query(BsTask).filter_by(id=task_id).with_for_update().first()
    event = _latest_page_text_refresh(db, task_id) if task is not None else None
    current = dict(event.payload_json or {}) if event else {}
    if current.get("run_id") != run_id or not _page_text_refresh_live(current):
        db.rollback()
        return False
    current["state"] = state
    add_event(db, task_id, _PAGE_TEXT_REFRESH_EVENT, stage="check", payload=current, commit=False)
    db.commit()
    return True


def _owns_page_text_refresh(db: Session, task_id: int, run_id: str, candidate: dict) -> bool:
    event = _latest_page_text_refresh(db, task_id)
    lease = dict(event.payload_json or {}) if event else {}
    return (lease.get("run_id") == run_id and _page_text_refresh_live(lease)
            and lease.get("material_id") == candidate.get("material_id")
            and lease.get("pages") == sorted(candidate.get("requested_pages") or []))


def _run_page_text_refresh(task_id: int, material_id: int, page_nos: list[int], run_id: str, bind) -> None:
    """后台补读仅生成待核候选；核查代创建及完成状态同事务提交。"""
    stopped = threading.Event()

    def heartbeat():
        while not stopped.wait(_PAGE_TEXT_REFRESH_LEASE_SECONDS / 3):
            try:
                with SessionLocal(bind=bind) as heartbeat_db:
                    if not renew_page_text_refresh(heartbeat_db, task_id, run_id):
                        return
            except Exception as exc:
                logger.warning("[bs] page refresh heartbeat failed task=%s error=%s", task_id, type(exc).__name__)

    thread = threading.Thread(target=heartbeat, daemon=True, name="bs-page-refresh-heartbeat")
    thread.start()
    try:
        with SessionLocal(bind=bind) as session:
            task = session.get(BsTask, task_id)
            if task is None:
                return
            candidate = prepare_targeted_page_text_refresh(session, task, material_id, page_nos)
            if not candidate.get("pages"):
                finish_page_text_refresh(session, task_id, run_id, "failed")
                return
            result = rerun_task(session, task, page_text_candidate=candidate, page_refresh_run_id=run_id)
            if not result.get("ok"):
                finish_page_text_refresh(session, task_id, run_id, "failed")
    except Exception as exc:
        logger.warning("[bs] page refresh failed task=%s error=%s", task_id, type(exc).__name__)
        try:
            with SessionLocal(bind=bind) as failure_db:
                finish_page_text_refresh(failure_db, task_id, run_id, "failed")
        except Exception as failure:
            logger.warning("[bs] page refresh failure recording failed task=%s error=%s", task_id, type(failure).__name__)
    finally:
        stopped.set()
        thread.join(timeout=1)


def _launch_page_text_refresh(task_id: int, material_id: int, page_nos: list[int], run_id: str, bind) -> None:
    threading.Thread(target=_run_page_text_refresh, args=(task_id, material_id, page_nos, run_id, bind),
                     daemon=True, name="bs-page-refresh").start()


def start_page_text_refresh(db: Session, task: BsTask, material_id: int, page_nos: list[int]) -> dict:
    lease = claim_page_text_refresh(db, task, material_id, page_nos)
    if lease["accepted"]:
        try:
            _launch_page_text_refresh(task.id, material_id, sorted(page_nos), lease["run_id"], db.get_bind())
        except Exception:
            finish_page_text_refresh(db, task.id, lease["run_id"], "failed")
            raise
    return lease


def _page_text_candidate_signature(candidate: dict) -> str:
    data = {key: value for key, value in candidate.items() if key != "signature"}
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hmac.new(get_settings().SECRET_KEY.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _validate_text_page_candidate(db: Session, task: BsTask, candidate: dict) -> tuple:
    """校验服务端生成的候选和当前任务一致；返回材料及待替换页副本。"""
    if not isinstance(candidate, dict) or not isinstance(candidate.get("signature"), str):
        raise ValueError("补读候选签名无效")
    try:
        valid_signature = hmac.compare_digest(candidate["signature"], _page_text_candidate_signature(candidate))
    except (TypeError, ValueError):
        valid_signature = False
    if not valid_signature:
        raise ValueError("补读候选签名无效")
    db.query(BsMaterial.id).filter(BsMaterial.task_id == task.id).with_for_update().all()
    db.query(BsSubject.id).filter(BsSubject.task_id == task.id).with_for_update().all()
    if (candidate.get("status") != "review_required" or candidate.get("task_id") != task.id
            or candidate.get("task_state") != task.state
            or candidate.get("review_run_id") != (task.options_snapshot_json or {}).get(reference_snapshots.REVIEW_POINTER)
            or candidate.get("input_rule_fingerprint") != knowledge_runs.rule_fingerprint()
            or candidate.get("input_material_fingerprint") != knowledge_runs.material_fingerprint(db, task)):
        raise ValueError("任务输入或核查代已变化，请重新补读")
    material = (db.query(BsMaterial).filter(BsMaterial.id == candidate.get("material_id"),
                BsMaterial.task_id == task.id).with_for_update().first())
    if (material is None or material.parse_state != "done" or not material.path
            or material.id not in {m.id for m in _rule_materials(db, task)}):
        raise ValueError("材料不再是当前有效材料")
    source_sha = candidate.get("source_sha256")
    if (not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", source_sha)
            or _material_sha256(material_abs_path(material.path)) != source_sha):
        raise ValueError("材料原件已变化，请重新补读")
    pages = candidate.get("pages")
    unread = parsing.material_reading_gap(material)
    if not isinstance(pages, list) or not pages or unread is None:
        raise ValueError("补读候选无可用未读页")
    by_page = {}
    for page in pages:
        if (not isinstance(page, dict) or type(page.get("page")) is not int or page["page"] not in unread
                or page["page"] in by_page or not isinstance(page.get("text"), str) or not page["text"].strip()
                or type(page.get("quality_suspect")) is not bool):
            raise ValueError("补读候选页无效")
        by_page[page["page"]] = page
    existing = material.pages_json or []
    numbers = [page.get("page") for page in existing if isinstance(page, dict)]
    if (len(numbers) != len(existing) or any(type(number) is not int or number < 1 for number in numbers)
            or len(set(numbers)) != len(numbers) or not set(by_page).issubset(numbers)):
        raise ValueError("原解析页码无效")
    replacements = []
    for page in existing:
        new = by_page.get(page["page"])
        if new is None:
            replacements.append(deepcopy(page))
            continue
        updated = {key: deepcopy(value) for key, value in new.items() if key != "quality_suspect"}
        meta = updated.get("ocr_meta") if isinstance(updated.get("ocr_meta"), dict) else {}
        updated["ocr_meta"] = {**meta, "quality": "ocr_review_required"}
        replacements.append(updated)
    return material, replacements, sorted(by_page)


def _validate_roster_page_candidate(db: Session, task: BsTask, candidate: dict) -> BsMaterial:
    """生效前验证候选仍基于当前任务，且仅新增指定页的名单线索。"""
    # 在计算任务输入指纹前锁定已存在的材料和主体，避免验证后被并发修改。
    db.query(BsMaterial.id).filter(BsMaterial.task_id == task.id).with_for_update().all()
    db.query(BsSubject.id).filter(BsSubject.task_id == task.id).with_for_update().all()
    if (not isinstance(candidate, dict) or candidate.get("status") != "completed"
            or candidate.get("task_id") != task.id or candidate.get("task_state") != task.state
            or candidate.get("review_run_id") != (task.options_snapshot_json or {}).get(reference_snapshots.REVIEW_POINTER)
            or candidate.get("input_rule_fingerprint") != knowledge_runs.rule_fingerprint()
            or candidate.get("input_material_fingerprint") != knowledge_runs.material_fingerprint(db, task)):
        raise ValueError("任务输入或核查代已变化，请重新补读")
    material_id = candidate.get("material_id")
    material = (db.query(BsMaterial).filter(BsMaterial.id == material_id,
                BsMaterial.task_id == task.id).with_for_update().first())
    if (material is None or material.parse_state != "done" or not material.path
            or material_id not in {m.id for m in _rule_materials(db, task)}):
        raise ValueError("材料不再是当前有效材料")
    source_sha = candidate.get("source_sha256")
    if (not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", source_sha)
            or _material_sha256(material_abs_path(material.path)) != source_sha):
        raise ValueError("材料原件已变化，请重新补读")
    original, proposed, changed = material.pages_json or [], candidate.get("pages"), candidate.get("changed_pages")
    unread = parsing.material_reading_gap(material)
    if (not isinstance(proposed, list) or not isinstance(changed, list) or not changed
            or any(type(n) is not int for n in changed) or len(set(changed)) != len(changed)
            or unread is None or any(n not in unread for n in changed) or len(proposed) != len(original)):
        raise ValueError("补读候选无变化或页范围无效")
    seen = set()
    for before, after in zip(original, proposed):
        if not isinstance(before, dict) or not isinstance(after, dict) or before.get("page") != after.get("page"):
            raise ValueError("补读候选页码不一致")
        no = before.get("page")
        if type(no) is not int or no in seen:
            raise ValueError("补读候选页码不唯一")
        seen.add(no)
        if no not in changed:
            if before != after:
                raise ValueError("补读候选修改了其他页")
            continue
        old_meta = before.get("ocr_meta") if isinstance(before.get("ocr_meta"), dict) else {}
        new_meta = after.get("ocr_meta") if isinstance(after.get("ocr_meta"), dict) else {}
        clues = new_meta.get("rotation_roster_clues")
        if (not isinstance(clues, list) or not clues or before == after
                or {k: v for k, v in after.items() if k != "ocr_meta"} !=
                   {k: v for k, v in before.items() if k != "ocr_meta"}
                or {k: v for k, v in new_meta.items() if k != "rotation_roster_clues"} !=
                   {k: v for k, v in old_meta.items() if k != "rotation_roster_clues"}):
            raise ValueError("补读候选修改了原文或其他字段")
        text = before.get("text") or ""
        for clue in clues:
            if (not isinstance(clue, dict) or set(clue) != {"name", "post"}
                    or not isinstance(clue.get("name"), str) or not isinstance(clue.get("post"), str)
                    or not re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", clue["name"])
                    or not re.fullmatch(r"[\u4e00-\u9fa5]{2,10}", clue["post"])
                    or clue["name"] not in text or clue["post"] not in text):
                raise ValueError("补读候选缺少可核对的原文")
    if not set(changed).issubset(seen):
        raise ValueError("补读候选页不存在")
    return material


def rerun_task(db: Session, task: BsTask, *, scope: str = "all", item_ids=None,
               allow_setup: bool = False, allow_preparing: bool = False,
               version_trigger: str = "rerun", operator=None,
               supplement_generation_id: str | None = None, page_candidate: dict | None = None,
               page_text_candidate: dict | None = None, page_refresh_run_id: str | None = None) -> dict:
    """任务内重核查（A-02 + BS-P1-13）：清旧结论 + 免重解析 + 状态回 running。

    请求体可选（缺省=全量，**向后兼容旧前端**）：`scope="all"`（默认）或
    `scope="items"` + `item_ids`（设计 98 §4.1）。

    与「新建任务」不同，重核查保留同一任务的材料与选项快照，只把既有结论作废
    并回到可再次核查的状态——避免用户为「换了判断口径/补了一份材料后重跑」重建
    任务、重传重解析。

    闸口（返回 ok=False 由 router 转 409/400）：
      ① scope/item_ids 形态非法 → reason="request"（router 400）；
      ② state 必须 ∈ {running, review, done}——setup 段尚未启动，谈不上「重核查」；
      ③ 解析中（uploading/parsing）阻止：与 start_review 同口径，半份文本会改结论。

    **免重解析**（核心）：材料 parse_state=done 的直接复用已落库的 pages_json /
    seal_json，**不重跑 `_finish_ingest` 重活段**（文本提取 + 判章是 CPU 重段）；
    只有 failed 材料（上次解析失败）才借道重活段重试——用户不必删除重传即得一次
    重试机会。

    **局部重跑≠增量计算**（设计 98 §4.2）：检查阶段照常跑全部分组，按 item_id 切只是
    「跑完后只采纳范围内的产出」。顺序固定：
      ① 先快照旧版本（trigger="rerun"）；
      ② 收集回执携带表 carry（只有 `review_json` 非空的条目才需要继承）；
      ③ 删旧结论（all 全删；items 只删 item_id ∈ item_ids 的行，其余行原样不动）；
      ④ state=running、重置参考批次、发 `task.rerun` 事件（payload 带范围与计数）。
    回执继承本身落在 `_replace_findings`（按主体稳定键逐条配对）。

    计数口径（21 号独立复核：对外数字要说清分母与边界）——三类互斥：
      reused=复用已解析产物（done）；retried=**真正调了 `_finish_ingest` 重跑**的
      （failed）；untouched=非 done 且非 failed（如台账登记 `via=ledger` 的 uploaded
      材料，无文件内容可解析）——零动作，既不重解析、也不计入 retried，避免
      「retried」虚高（曾误把 uploaded 也计进 retried，实际零动作）。
    """
    if scope not in ("all", "items"):
        return {"ok": False, "reason": "request", "detail": "重跑范围仅支持 all / items"}
    if scope == "items":
        cleaned, error = _clean_rerun_item_ids(item_ids)
        if error:
            return {"ok": False, "reason": "request", "detail": error}
        item_ids = cleaned
    else:
        item_ids = None
    db.rollback()
    db.refresh(task, with_for_update=True)
    if page_refresh_run_id is not None and (page_text_candidate is None or not _owns_page_text_refresh(
            db, task.id, page_refresh_run_id, page_text_candidate)):
        return {"ok": False, "reason": "page_refresh_superseded", "detail": "补读已过期或被更新请求取代"}
    allowed_states = (("running", "review", "done")
                      + ((TASK_STATE_SETUP,) if allow_setup else ())
                      + ((TASK_STATE_PREPARING,) if allow_preparing else ()))
    if version_trigger == "supplement":
        allowed_states = (TASK_STATE_SETUP,) + ((TASK_STATE_PREPARING,) if allow_preparing else ())
    if task.state not in allowed_states:
        return {"ok": False, "reason": "state",
                "detail": "请先完成两段式启动并开始核查，再重新核查"}
    if page_candidate is not None and page_text_candidate is not None:
        return {"ok": False, "reason": "page_candidate_scope", "detail": "一次只能使用一种补读候选"}
    any_candidate = page_candidate if page_candidate is not None else page_text_candidate
    if any_candidate is not None:
        if version_trigger != "rerun" or scope != "all":
            return {"ok": False, "reason": "page_candidate_scope", "detail": "单页补读后需重新执行全部核查"}
        try:
            if page_text_candidate is not None:
                _validate_text_page_candidate(db, task, page_text_candidate)
            else:
                _validate_roster_page_candidate(db, task, page_candidate)
        except (ValueError, OSError) as exc:
            return {"ok": False, "reason": "page_candidate_stale", "detail": str(exc)}
    check = validate_task_materials(db, task)
    if check["parsing_pending"]:
        return {"ok": False, "reason": "parsing_pending", "detail": check}
    expected_groups = reference_snapshots.expected_groups(task)
    if scope == "items":
        try:
            # 先只做合法性/任务范围预检，必须早于失败材料重试、版本快照和任何删除。
            rule_item_catalog.plan_rerun(
                expected_groups=expected_groups, scope=scope, item_ids=item_ids,
                changed_item_ids=())
        except rule_item_catalog.RerunSelectionError as exc:
            return {"ok": False, "reason": "request", "detail": exc.as_detail()}
    materials = (_supplement_preparation_materials(db, task)
                 if version_trigger == "supplement" else _rule_materials(db, task))
    if any_candidate is not None and any(m.parse_state != "done" for m in materials):
        return {"ok": False, "reason": "page_candidate_materials", "detail": "存在尚未完成读取的材料，请先处理"}
    # ② 免重解析：done 复用（跳过重活段）；failed 借道重试。重活段独立会话提交，
    # 逐份调完后刷新请求会话，才能看到收口态（与 ingest_material 同纪律）。
    reused_ids = []
    retried_ids = []
    prepared_ids = []
    untouched_ids = []
    parse_failures = []
    for mat in materials:
        material_id, file_name = mat.id, mat.file_name
        if version_trigger == "supplement" and _subject_assessment_issue(mat):
            parse_failures.append({"material_id": material_id, "file_name": file_name,
                                   "parse_state": mat.parse_state,
                                   "degradations": mat.degradations_json or []})
            continue
        if mat.parse_state == "done":
            reused_ids.append(material_id)   # pages_json / seal_json 直接复用，不再解析
            if version_trigger == "supplement" and mat.replacement_target_id is not None:
                parse_failures.append({"material_id": material_id, "file_name": file_name,
                                       "parse_state": mat.parse_state,
                                       "degradations": mat.degradations_json or []})
            continue
        if mat.parse_state == "failed":  # 仅失败材料真跑重活段重试
            retried_ids.append(material_id)
            _finish_ingest(task.id, material_id, db.get_bind())
            if version_trigger != "supplement":
                continue
        elif mat.parse_state == "uploaded" and mat.path and mat.via == "upload":
            # 兜底处理有真实文件路径的 uploaded 材料，须在快照/删结论前收口。
            prepared_ids.append(material_id)
            _finish_ingest(task.id, material_id, db.get_bind())
        else:
            # 登记材料零动作；未生效替换候选不能借登记状态绕过激活门禁。
            untouched_ids.append(material_id)
            if version_trigger == "supplement" and mat.replacement_target_id is not None:
                parse_failures.append({"material_id": material_id, "file_name": file_name,
                                       "parse_state": mat.parse_state,
                                       "degradations": mat.degradations_json or []})
            continue
        db.expire_all()
        refreshed = db.get(BsMaterial, material_id)
        if (refreshed is None or refreshed.parse_state != "done"
                or (version_trigger == "supplement"
                    and refreshed.replacement_target_id is not None)):
            parse_failures.append({
                "material_id": material_id,
                "file_name": refreshed.file_name if refreshed is not None else file_name,
                "parse_state": refreshed.parse_state if refreshed is not None else None,
                "degradations": (refreshed.degradations_json or []) if refreshed else [],
            })
    if parse_failures:
        db.rollback()
        db.refresh(task, with_for_update=True)
        names = "、".join(str(item["file_name"] or item["material_id"])
                         for item in parse_failures[:3])
        if len(parse_failures) > 3:
            names += "等 %d 份" % len(parse_failures)
        return {
            "ok": False,
            "reason": next((d.get("kind") for item in parse_failures if item.get("parse_state") == "done"
                            for d in item.get("degradations", []) if isinstance(d, dict)
                            and d.get("kind") in {"subject_conflict", "subject_unresolved"}), "parse_failed"),
            "detail": {
                "message": f"补件材料（{names}）读取、主体校验或替换未完成，请检查材料后重试；未创建新的核查代",
                "materials": parse_failures,
            },
        }
    # 失败材料重解析使用独立会话且可能耗时；完成后必须重新获取任务锁，再冻结本次
    # 真实执行要使用的规则输入。不能在重活段之前读版本、在之后读 runtime 指纹。
    db.rollback()
    db.refresh(task, with_for_update=True)
    if page_refresh_run_id is not None and not _owns_page_text_refresh(db, task.id, page_refresh_run_id, page_text_candidate):
        return {"ok": False, "reason": "page_refresh_superseded", "detail": "补读已过期或被更新请求取代"}
    if task.state not in allowed_states:
        return {"ok": False, "reason": "state",
                "detail": "任务状态已变化，请刷新后重新发起核查"}
    candidate_material = None
    replacement_pages, changed_pages = None, None
    if any_candidate is not None:
        try:
            if page_text_candidate is not None:
                candidate_material, replacement_pages, changed_pages = _validate_text_page_candidate(db, task, page_text_candidate)
            else:
                candidate_material = _validate_roster_page_candidate(db, task, page_candidate)
                replacement_pages, changed_pages = page_candidate["pages"], page_candidate["changed_pages"]
        except (ValueError, OSError) as exc:
            return {"ok": False, "reason": "page_candidate_stale", "detail": str(exc)}
    newly_locked = []
    current_supplement_plan = None
    prepared_count, retried_count, reused_count = len(prepared_ids), len(retried_ids), len(reused_ids)
    if version_trigger == "supplement":
        current_supplement_plan = get_supplement_generation_plan(task)
        if (not current_supplement_plan or current_supplement_plan.get("status") != "planned"
                or current_supplement_plan.get("generation_id") != supplement_generation_id):
            return {"ok": False, "reason": "supplement_plan_state",
                    "detail": "补件计划已变化，请刷新后重新发起核查"}
        check = validate_task_materials(db, task)
        if check["unassigned_children"]:
            return {"ok": False, "reason": "hard_gaps", "detail": {
                "message": "报审资料包内有未归属的材料，请先确认主体",
                "materials": check["unassigned_children"],
            }}
        subjects, error = _subject_start_gate(db, task)
        if error:
            return error
        edit = (task.options_snapshot_json or {}).get("supplement_edit") or {}
        prepared_set = set(prepared_ids) | set(edit.get("preparation_uploaded_ids") or [])
        retried_set = set(retried_ids) | set(edit.get("preparation_retry_ids") or [])
        prepared_count, retried_count = len(prepared_set), len(retried_set)
        reused_count = len(set(reused_ids) - prepared_set - retried_set)
        newly_locked = [s for s in subjects if not s.type_locked]
        for subject in newly_locked:
            subject.type_locked = True
        db.flush()
    previous_ruleset_version = task.ruleset_version
    previous_ruleset_manifest = _frozen_ruleset_manifest(task)
    current_ruleset_manifest, runtime_rule_fingerprint = _new_frozen_ruleset_inputs()
    ruleset_diff = None
    if (previous_ruleset_manifest is None
            or previous_ruleset_manifest.get("hash_algorithm") != ruleset.HASH_ALGORITHM
            or not isinstance(previous_ruleset_manifest.get("files"), dict)):
        # 历史任务没有可比较的旧清单：字段仍显式返回 null，不能用 [] 冒充
        # “已证明没有变化”。旧 raw-byte 清单同样不能与 text-lf-v1 强行比较。
        changed_item_ids = None
    else:
        ruleset_diff = ruleset.diff_manifests(
            previous_ruleset_manifest, current_ruleset_manifest)
        changed_item_ids = ruleset_diff["changed_item_ids"]

    # 当前 manifest 没有可验证的 parent_version 链：只有规则内容未变时，才能证明按请求
    # 缩组安全；任何已变化或历史不可比较情形都显式回退全量，不把单版 changed_item_ids
    # 误当成跨多代完整影响面。
    planning_changed_item_ids = [] if ruleset_diff and not ruleset_diff["changed"] else None
    date_source = baseline_date_source(task)
    next_baseline = _default_baseline_date() if date_source == "automatic" else task.baseline_date
    baseline_changed = next_baseline != task.baseline_date
    section_sources = _missing_section_suggestions(db, task)
    plan = rule_item_catalog.plan_rerun(
        expected_groups=expected_groups, scope=scope, item_ids=item_ids,
        changed_item_ids=planning_changed_item_ids)
    if baseline_changed and plan["scope"] == "items":
        plan = rule_item_catalog.fallback_to_full(plan, expected_groups=expected_groups, reason="baseline_date_changed")
    if section_sources and plan["scope"] == "items":
        plan = rule_item_catalog.fallback_to_full(plan, expected_groups=expected_groups, reason="section_information_changed")
    if ruleset_diff and ruleset_diff["changed"] and plan["fallback_reason"]:
        plan["fallback_reason"] = "ruleset_lineage_unavailable"
    current_material_fingerprint = knowledge_runs.material_fingerprint(db, task)
    comparison_fingerprint = current_material_fingerprint
    supplement_comparison_available = True
    if version_trigger == "supplement":
        current_options = dict(task.options_snapshot_json or {})
        edit = current_options.get("supplement_edit") or {}
        if "previous_parent_reference_present" in edit:
            # 只为对比父代还原进入编辑前的过程指针；本代与历史报告仍使用原有指纹口径。
            if edit["previous_parent_reference_present"]:
                current_options[reference_snapshots.SUPPLEMENT_PARENT_REFERENCE] = edit.get("previous_parent_reference")
            else:
                current_options.pop(reference_snapshots.SUPPLEMENT_PARENT_REFERENCE, None)
            new_pdf = _single_linked_packet_pdf(db, task, supplement_generation_id)
            required = {"completeness", "signature_chain", "a1", "a2"}
            rerun_groups = set(plan.get("rerun_groups") or [])
            excluded = ({new_pdf.id} if new_pdf is not None and required <= rerun_groups else None)
            comparison_fingerprint = knowledge_runs.material_fingerprint(
                db, task, options_snapshot=current_options, excluded_material_ids=excluded)
        else:
            supplement_comparison_available = False
    supplement_parent = ((task.options_snapshot_json or {}).get(
        reference_snapshots.SUPPLEMENT_PARENT_REFERENCE)
        if version_trigger == "supplement" else None)
    if plan["scope"] == "items" and not supplement_comparison_available:
        validated_runs, carry_error = {}, "supplement_input_scope_unverified"
    else:
        validated_runs, carry_error = _carry_forward_source_runs(
            db, task, expected_groups if plan["scope"] == "items" else [],
            material_fingerprint=comparison_fingerprint,
            rule_fingerprint=runtime_rule_fingerprint,
            parent_reference=supplement_parent)
    if carry_error:
        plan = rule_item_catalog.fallback_to_full(
            plan, expected_groups=expected_groups, reason=carry_error)
        source_runs = {}
    else:
        source_runs = {group: validated_runs[group]
                       for group in plan["carried_forward_groups"]}
    # ③-a 先快照旧版本（含旧 findings/回执/ruleset_version）；快照前清掉上一轮
    # carry——版本快照不应携带更早一轮的旧回执副本（设计 98 §4.3）。
    options = dict(task.options_snapshot_json or {})
    options.pop("rerun", None)
    task.options_snapshot_json = options
    frozen_pages = ({candidate_material.id: changed_pages} if candidate_material else None)
    try:
        previous_version_no = ((task.options_snapshot_json or {}).get("supplement_edit") or {}).get("previous_version_no") if version_trigger == "supplement" else None
        if previous_version_no is not None:
            version = (db.query(BsReviewVersion).filter_by(task_id=task.id, version_no=previous_version_no,
                                                          trigger="supplement").one_or_none())
            if version is None:
                raise ValueError("补件前审查快照不存在，不能启动重核")
        else:
            version = create_review_version(db, task, trigger=version_trigger, frozen_material_pages=frozen_pages)
        task.baseline_date = next_baseline
        _apply_section_suggestions(task, section_sources)
        if candidate_material is not None:
            candidate_material.pages_json = deepcopy(replacement_pages)
            if page_text_candidate is not None:
                candidate_material.degradations_json = [*(candidate_material.degradations_json or []),
                    {"kind": "reading_quality_review_invalidated", "pages": list(changed_pages)},
                    {"kind": "parse_incomplete", "unread_pages": list(changed_pages),
                     "reason": "page_refresh_review_required"}]
            db.flush()
    except Exception:
        db.rollback()
        raise
    # ③-b 收集回执携带表：只带有回执的条目（没回执的结论无需继承）。
    carry = []
    prior_findings = [] if version_trigger == "supplement" else (
        db.query(BsFinding).filter(BsFinding.task_id == task.id).order_by(BsFinding.id).all())
    for finding in prior_findings:
        if not finding.review_json:
            continue
        carry.append({
            "subject_id": finding.subject_id,
            "item_id": finding.item_id,
            "material_id": finding.material_id,
            "quote": finding.quote,
            "page": finding.page,
            "status": finding.status,
            "severity": finding.severity,
            "confidence": finding.confidence,
            "message": finding.message,
            "missing_json": _json_safe(finding.missing_json),
            "evidence_json": _json_safe(finding.evidence_json),
            "location_mode": finding.location_mode,
            "review_json": _json_safe(deepcopy(finding.review_json)),
        })
    # ④ 删旧结论：以实际计划为准；安全回退时全删，局部时只删实际条目。
    delete_query = db.query(BsFinding).filter(BsFinding.task_id == task.id)
    if plan["item_ids"] is not None:
        delete_query = delete_query.filter(BsFinding.item_id.in_(plan["item_ids"]))
    # 上面收集回执时可能已将旧结论加载进会话；删除时同步其状态。
    delete_query.delete(synchronize_session="fetch")
    # ⑤ 重置状态回 running（幂等：本就在 running 亦无副作用），carry 随选项快照落库。
    options = dict(task.options_snapshot_json or {})
    options["baseline_date_source"] = date_source
    options["section"] = _section_snapshot(task)
    options["section_auto_sources"] = {**(options.get("section_auto_sources") or {}), **section_sources}
    options["semantic_review_version"] = 1
    options["ruleset_manifest"] = current_ruleset_manifest
    options["rerun"] = {
        **plan,
        "carry": carry,
        "requested_at": datetime.now().isoformat(timespec="seconds"),
    }
    task.options_snapshot_json = options
    task.ruleset_version = current_ruleset_manifest["version"]
    knowledge_runs.clear_reference_run(task)
    task.state = "running"
    db.flush()
    try:
        seed = initialize_reference_review(
            db, task, rule_fingerprint=runtime_rule_fingerprint, ai_auto_groups=plan["rerun_groups"])
        _write_carried_forward_runs(
            db, task, seed=seed, source_runs=source_runs,
            previous_ruleset_version=previous_ruleset_version)
        if knowledge_runs.rule_fingerprint() != runtime_rule_fingerprint:
            raise knowledge_runs.ReferenceRunSuperseded(
                "规则文件在重跑准备期间发生变化，本次未作废旧结论，请重试")
        scope_count = len(plan["item_ids"]) if plan["item_ids"] is not None else None
        event_payload = {
            "state": "running", "reused": reused_count, "retried": retried_count,
            "prepared": prepared_count,
            "untouched": len(untouched_ids), "superseded_version_no": version.version_no,
            "previous_ruleset_version": previous_ruleset_version,
            "ruleset_version": task.ruleset_version,
            "changed_item_ids": changed_item_ids,
            **plan,
            "scope_count": scope_count, "carry_count": len(carry),
        }
        if any_candidate is not None:
            event_payload["page_refresh"] = {"material_id": candidate_material.id,
                "pages": list(changed_pages), "source_sha256": any_candidate["source_sha256"],
                "engine": any_candidate.get("engine"),
                "review_required": page_text_candidate is not None}
            if page_text_candidate is not None:
                event_payload["page_refresh"]["missing_pages"] = list(page_text_candidate.get("missing_pages") or [])
        add_event(db, task.id, "task.rerun", payload=event_payload, commit=False)
        for subject in newly_locked:
            add_event(db, task.id, "subject.type_locked", payload={
                "subject_id": subject.id, "stable_code": subject.stable_code,
                "lock_source": "supplement", "operator": operator,
            }, commit=False)
        activated_plan = None
        if current_supplement_plan is not None:
            fallback_reason = plan.get("fallback_reason") or current_supplement_plan.get("fallback_reason")
            activated_plan = {
                **current_supplement_plan,
                "status": "running",
                "activated_at": datetime.now().isoformat(timespec="seconds"),
                "execution_scope": plan.get("scope"),
                "execution_item_ids": plan.get("item_ids"),
                "execution_groups": plan.get("rerun_groups") or [],
                "prepared": prepared_count,
                "carried_forward_groups": plan.get("carried_forward_groups") or [],
                "fallback_reason": fallback_reason,
                "fallback_message": supplement_generation.fallback_message(plan.get("scope"), fallback_reason),
                "superseded_version_no": version.version_no,
            }
            options = dict(task.options_snapshot_json or {})
            options["supplement_plan"] = _json_safe(activated_plan)
            task.options_snapshot_json = options
            db.flush()
            add_event(db, task.id, "task.supplement_activated", stage="check",
                      payload=activated_plan, commit=False)
        if any_candidate is not None and _material_sha256(material_abs_path(candidate_material.path)) != any_candidate["source_sha256"]:
            raise ValueError("原件在核查代创建期间变化，本次补读未生效")
        if page_refresh_run_id is not None:
            if not _owns_page_text_refresh(db, task.id, page_refresh_run_id, page_text_candidate):
                raise ValueError("补读已过期，本次结果未生效")
            lease = dict(_latest_page_text_refresh(db, task.id).payload_json)
            lease.update(state="completed", missing_pages=list(page_text_candidate.get("missing_pages") or []))
            add_event(db, task.id, _PAGE_TEXT_REFRESH_EVENT, stage="check", payload=lease, commit=False)
        db.commit()
    except Exception:
        db.rollback()
        db.refresh(task)
        raise
    db.refresh(task)
    return {"ok": True, "reused": reused_count, "retried": retried_count,
            "prepared": prepared_count,
            "untouched": len(untouched_ids), "superseded_version_no": version.version_no,
            "previous_ruleset_version": previous_ruleset_version,
            "ruleset_version": task.ruleset_version,
            "changed_item_ids": changed_item_ids,
            **plan,
            "scope_count": scope_count, "carry_count": len(carry),
            "supplement_plan": activated_plan}


def enter_supplement_edit(db: Session, task: BsTask) -> dict:
    """进入补件编辑态，保留上一代 findings 与报告快照。"""
    db.rollback()
    db.refresh(task, with_for_update=True)
    plan = get_supplement_generation_plan(task)
    if not plan or plan.get("status") != "planned":
        return {"ok": False, "reason": "supplement_plan_required",
                "detail": "请先生成补件局部核查计划，再进入补件编辑"}
    if task.state == TASK_STATE_SETUP:
        edit = (task.options_snapshot_json or {}).get("supplement_edit") or {}
        return {"ok": True, "state": TASK_STATE_SETUP, "already_setup": True,
                "previous_version_no": edit.get("previous_version_no")}
    if task.state == TASK_STATE_PREPARING:
        return {"ok": False, "reason": "preparing",
                "detail": "材料仍在准备中，请等待准备完成后再补件"}
    if task.state not in ("running", "review", "done"):
        return {"ok": False, "reason": "state", "detail": "当前状态不允许进入补件编辑"}
    check = validate_task_materials(db, task)
    if check["parsing_pending"]:
        return {"ok": False, "reason": "parsing_pending",
                "detail": "已有材料仍在解析，请等待解析完成后再补件"}
    previous_state = task.state
    # Freeze before clearing the reference pointer and before new uploads.
    previous_version = create_review_version(db, task, trigger="supplement")
    frozen = deepcopy(previous_version.snapshot_json)
    frozen_options = frozen["task"]["options_snapshot"]
    previous_plan = plan.get("previous_supplement_plan")
    if previous_plan is None:
        frozen_options.pop("supplement_plan", None)
    else:
        frozen_options["supplement_plan"] = deepcopy(previous_plan)
    previous_version.snapshot_json = frozen
    options = dict(task.options_snapshot_json or {})
    # 保留父代只读指针。当前代指针会在进入 setup 后清空，但补件代际仍需
    # 严格校验父代各组终态，才能安全决定哪些组可以 carried-forward。
    parent_reference = {
        reference_snapshots.REVIEW_POINTER: options.get(reference_snapshots.REVIEW_POINTER),
        reference_snapshots.GROUP_POINTERS: deepcopy(
            options.get(reference_snapshots.GROUP_POINTERS) or {}),
    }
    old_parent_present = reference_snapshots.SUPPLEMENT_PARENT_REFERENCE in options
    old_parent = deepcopy(options.get(reference_snapshots.SUPPLEMENT_PARENT_REFERENCE))
    if parent_reference[reference_snapshots.REVIEW_POINTER]:
        options[reference_snapshots.SUPPLEMENT_PARENT_REFERENCE] = parent_reference
    options["supplement_edit"] = {
        "entered_at": datetime.now().isoformat(timespec="seconds"),
        "previous_state": previous_state,
        "generation_id": plan.get("generation_id"),
        "previous_version_no": previous_version.version_no,
        "preserve_findings": True,
        "previous_parent_reference_present": old_parent_present,
        "previous_parent_reference": old_parent,
    }
    task.options_snapshot_json = options
    task.state = TASK_STATE_SETUP
    knowledge_runs.clear_reference_run(task)
    db.commit()
    db.refresh(task)
    add_event(db, task.id, "task.supplement_edit_started", stage="setup", payload={
        "state": task.state,
        "previous_state": previous_state,
        "generation_id": plan.get("generation_id"),
        "previous_version_no": previous_version.version_no,
        "findings_preserved": True,
    })
    return {"ok": True, "state": task.state, "already_setup": False,
            "findings_preserved": True, "previous_version_no": previous_version.version_no}


def activate_supplement_plan(db: Session, task: BsTask, *, allow_preparing: bool = False,
                             operator=None) -> dict:
    """激活补件计划：按局部条目/核查组进入 running，旧结论只读保留。"""
    db.rollback()
    db.refresh(task, with_for_update=True)
    current = get_supplement_generation_plan(task)
    if not current:
        return {"ok": False, "reason": "supplement_plan_required",
                "detail": "未找到可激活的补件计划"}
    if current.get("status") == "blocked":
        return {"ok": False, "reason": current.get("blocked_reason", "supplement_blocked"),
                "detail": "补件计划已被阻止，不能启动新审查代"}
    if current.get("status") == "running":
        return {"ok": True, "already_running": True, **current}
    if current.get("status") != "planned":
        return {"ok": False, "reason": "supplement_plan_state",
                "detail": "补件计划当前状态不允许激活"}
    if task.state == TASK_STATE_PREPARING and not allow_preparing:
        return _preparing_acceptance(db, task)
    if task.state != TASK_STATE_SETUP and not (allow_preparing and task.state == TASK_STATE_PREPARING):
        return {"ok": False, "reason": "state",
                "detail": "请先进入补件编辑并完成材料上传"}

    if not allow_preparing:
        check = validate_task_materials(db, task)
        if check["parsing_pending"]:
            return {"ok": False, "reason": "parsing_pending", "detail": "材料正在读取，请稍后重试"}
        failed = failed_zip_containers(db, task)
        if failed:
            return {"ok": False, "reason": "zip_preparation_failed", "detail": {
                "message": "报审资料包无法准备，请移除或重新上传后重试",
                "materials": [{"material_id": m.id, "file_name": m.file_name} for m in failed],
            }}
        prepare_materials = [m for m in _supplement_preparation_materials(db, task)
                             if m.parse_state == "failed"
                             or (m.parse_state == "uploaded" and m.path and m.via == "upload")]
        if zip_containers(db, task) or prepare_materials:
            options = dict(task.options_snapshot_json or {})
            edit = dict(options.get("supplement_edit") or {})
            edit["prepare_for_supplement"] = True
            edit["generation_id"] = current.get("generation_id")
            edit["preparation_uploaded_ids"] = [m.id for m in prepare_materials if m.parse_state == "uploaded"]
            edit["preparation_retry_ids"] = [m.id for m in prepare_materials if m.parse_state == "failed"]
            options["supplement_edit"] = edit
            task.options_snapshot_json = options
            db.flush()
            return _accept_zip_preparation(db, task, operator=operator)

    # ZIP 替换候选在准备阶段先保留旧版本；主体确认和全部子材料读取成功后才生效。
    pending_containers = [m for m in task_zip_containers(db, task)
                          if m.replacement_target_id is not None and m.parse_state == "done"]
    if pending_containers:
        check = validate_task_materials(db, task)
        if check["unassigned_children"]:
            return {"ok": False, "reason": "hard_gaps", "detail": {
                "message": "报审资料包内有未归属的材料，请先确认主体",
                "materials": check["unassigned_children"],
            }}
        _, error = _subject_start_gate(db, task)
        if error:
            return error
        for container in pending_containers:
            children = container_children(db, task, container)
            if not children or any(child.parse_state != "done" or _subject_assessment_issue(child) for child in children):
                return {"ok": False, "reason": "parse_failed", "detail": {
                    "message": "替换资料包内材料尚未全部读取，请检查后重新上传",
                    "materials": [{"material_id": container.id, "file_name": container.file_name}],
                }}
            if not _activate_pending_replacement(db, task, container, operator=operator):
                return {"ok": False, "reason": "parse_failed", "detail": {
                    "message": "资料包替换未生效，请检查材料后重试",
                    "materials": [{"material_id": container.id, "file_name": container.file_name}],
                }}

    mode = current.get("mode")
    affected_items = list(current.get("affected_item_ids") or [])
    if mode == "partial":
        # 补件类别的影响映射先决定核查组，再展开该组的合法条目；只传
        # 缺件清单项会漏掉同一材料同时影响的 A2/证照结论。
        groups = list(current.get("affected_groups") or [])
        new_pdf = _single_linked_packet_pdf(db, task, current.get("generation_id"))
        if new_pdf is not None:
            groups = list(dict.fromkeys([*groups, "completeness", "signature_chain", "a1", "a2"]))
        item_ids = []
        for group in groups:
            item_ids.extend(rule_item_catalog.item_ids_for_group(group))
        item_ids = list(dict.fromkeys([*affected_items, *item_ids]))
        scope = "items"
    elif mode == "group_fallback":
        groups = list(current.get("affected_groups") or [])
        item_ids = []
        for group in groups:
            item_ids.extend(rule_item_catalog.item_ids_for_group(group))
        scope = "items"
    else:
        scope, item_ids = "all", None

    result = rerun_task(
        db,
        task,
        scope=scope,
        item_ids=item_ids,
        allow_setup=True,
        allow_preparing=allow_preparing,
        version_trigger="supplement",
        operator=operator,
        supplement_generation_id=current.get("generation_id"),
    )
    if not result.get("ok"):
        return result
    db.refresh(task)
    return {"ok": True, **result["supplement_plan"], "task_state": task.state}


def enter_material_edit(db: Session, task: BsTask) -> dict:
    """将已启动任务退回材料修改态，保留材料解析产物并作废旧结论。"""
    db.rollback()
    db.refresh(task, with_for_update=True)
    if task.state == TASK_STATE_SETUP:
        return {"ok": True, "state": TASK_STATE_SETUP, "already_setup": True}
    if task.state == TASK_STATE_PREPARING:
        return {"ok": False, "detail": "资料包正在准备中，暂时不能修改材料；请等待准备完成"}
    if task.state not in ("running", "review", "done"):
        return {"ok": False, "detail": "当前任务暂不能修改材料"}
    check = validate_task_materials(db, task)
    if check["parsing_pending"]:
        return {"ok": False, "detail": "材料仍在解析中，请等待读取完成后再修改"}
    has_findings = db.query(BsFinding.id).filter(BsFinding.task_id == task.id).first() is not None
    # BS-P1-13（设计 98 §4.3）：补件路径不继承回执（横幅已如实说明），
    # 快照前清掉 carry，避免历史版本携带上一轮的旧回执副本。
    options = dict(task.options_snapshot_json or {})
    options.pop("rerun", None)
    task.options_snapshot_json = options
    version = create_review_version(db, task, trigger="material_edit") if has_findings else None
    db.query(BsFinding).filter(BsFinding.task_id == task.id).delete(synchronize_session=False)
    subjects = db.query(BsSubject).filter(BsSubject.task_id == task.id).all()
    for subject in subjects:
        subject.type_locked = False
    task.state = TASK_STATE_SETUP
    knowledge_runs.clear_reference_run(task)
    db.commit()
    db.refresh(task)
    payload = {"state": TASK_STATE_SETUP}
    if version is not None:
        payload["superseded_version_no"] = version.version_no
    add_event(db, task.id, "task.material_edit_started", payload=payload)
    result = {"ok": True, "state": task.state, "already_setup": False}
    if version is not None:
        result["superseded_version_no"] = version.version_no
    return result


# ==================== 事件流（P0-1）：读端点 + 中文化白名单 ====================

# 事件中文化白名单（唯一表源）：新增 event_type 必须在此登记（契约测试锁发射端）。
EVENT_LABELS = {
    _PAGE_TEXT_REFRESH_EVENT: "材料页面补读",
    "task.supplement_planned": "补件局部核查计划已生成",
    "task.supplement_blocked": "补件局部核查计划被阻止",
    "task.supplement_edit_started": "补件编辑已开始",
    "task.supplement_activated": "补件局部核查已激活",
    "task.supplement_completed": "补件新代已收口",
    "material.subject_conflict": "主体校验未通过",
    "material.subject_unresolved": "材料主体待确认",
    "material.page_progress": "材料页读取进展",
    "material.parse_started": "开始读取材料",
    "material.supplement_linked": "材料已关联补件范围",
    "knowledge.review_started": "本轮核查依据已冻结",
    "knowledge.reference_started": "正在查询本组参考依据",
    "knowledge.reference_completed": "本组参考依据查询结束",
    "knowledge.reference_discarded": "旧批次参考结果已作废",
    "knowledge.reference_failed": "参考依据查询中断，已保留原结果",
    "knowledge.reference_invalidated": "上游核查已更新，需要重算第二步",
    "check.semantic_started": "正在比对合同条款",
    "check.semantic_progress": "合同核查进展",
    "check.ai_advisory_completed": "AI 辅助核查已完成",
    "check.ai_advisory_started": "AI 辅助核查进行中",
    "check.ai_advisory_failed": "AI 辅助核查未完成",
    "check.ai_batch_checkpoint": "AI 辅助核查进展已保存",
    "check.ai_batch_progress": "AI 批次核查进展",
    "check.negative_candidates_started": "负面清单辅助核查进行中",
    "check.negative_candidates_finished": "负面清单辅助核查状态已更新",
    "check.negative_candidates_failed": "负面清单辅助核查未完成",
    "check.negative_candidate_checkpoint": "负面清单核查进展已保存",
    "task.created": "已创建核查任务",
    "material.added": "已登记材料",
    "material.replacement_requested": "已登记材料替代意图",
    "material.role_corrected": "已更正材料归属",
    "material.parsed": "材料已读取",
    "evidence.anchor_recovered": "已补充原文定位坐标",
    "material.parse_failed": "材料无法读取",
    # BS-P2-01（设计 98 §6.1）：显式替换旧材料后旧行标记被替代；payload 只带
    # old/new 材料 ID 与 operator，不写文件名/路径以外的内容。
    "material.superseded": "材料已被替换",
    "task.started": "核查开始",
    "check.completed": "一组核查完成",
    "check.failed": "核查中断",
    "task.completed": "任务收口完成",
    "task.rerun": "已重新核查",
    "task.material_edit_started": "已返回材料修改",
    "report.exported": "报告导出记录",
    # T1-v2 主体中心事件（附录 §2.5）：创建/确认/锁定/批次/归属 + AI 识别与冲突
    "subject.created": "已创建分包主体",
    "subject.name_confirmed": "已确认分包单位名称",
    "subject.type_confirmed": "已确认主体类型",
    "subject.type_locked": "已锁定主体类型",
    "subject.ai_type_detected": "AI已识别主体类型",
    "subject.type_conflict_detected": "主体类型识别冲突",
    "packet.created": "已创建报审资料批次",
    "material.subject_assigned": "材料已归属主体",
    # T4.1-T2 ZIP 容器派生（契约 §7.5.7：新事件必须登记白名单并通过静态契约测试；
    # payload 含 operator、容器材料 ID、子材料数量/收口结果，不写整页 OCR 原文）
    "zip.container_expanded": "报审资料包已展开",
    "zip.container_failed": "报审资料包无法展开",
    # T10-B ZIP 准备生命周期（契约 §7.3）：受理/收口/复位四事件。payload 只带容器
    # 材料 ID、文件名、计数与可读原因，**不写** ZIP 全路径、临时目录或整页 OCR 原文。
    "zip.preparation_started": "报审资料包开始准备",
    "zip.container_ready": "报审资料包已准备完成",
    "zip.preparation_completed": "报审资料包准备完成",
    "zip.preparation_awaiting_confirmation": "报审资料包准备完成，待确认主体类型",
    "zip.preparation_failed": "报审资料包准备失败",
    "zip.preparation_recovered": "报审资料包准备已恢复",
}

# SSE 轮询节奏：空闲 1s 一查；连续 15 次空闲发一次 keepalive（约 15s，防前端失联误判）
SSE_IDLE_SECONDS = 1.0
SSE_KEEPALIVE_TICKS = 15


def sse_event(event_type: str, payload: dict, seq: int | None = None) -> str:
    """SSE 帧：id 行供 EventSource 断线自动携带 Last-Event-ID 续传。"""
    lines = []
    if seq is not None:
        lines.append("id: %d" % seq)
    lines.append("event: %s" % event_type)
    lines.append("data: %s" % json.dumps(payload, ensure_ascii=False, default=str))
    return "\n".join(lines) + "\n\n"


def _event_message(ev: BsTaskEvent) -> str | None:
    """事件 → 用户可读摘要（平实中文，禁实现侧词汇；无摘要回 None 只显 label）。"""
    p = ev.payload_json or {}
    t = ev.event_type
    if t == "check.ai_batch_progress":
        index, total = p.get("batch_index"), p.get("batch_total")
        if type(index) is not int or type(total) is not int or not (1 <= index <= total):
            return None
        group = "负面清单核查" if p.get("category") == "negative_candidates" else (reference_snapshots.CHECK_GROUPS.get(p.get("group_id")) or {}).get("label", "核查")
        status = {"processing": "正在调用模型", "completed": "批次结果已保存", "reused": "已复用保存的批次结果", "partial": "部分建议需人工核实", "failed": "模型调用未完成，请重试", "incomplete": "批次未完成，需重试或人工核实"}.get(p.get("status"))
        from ..request_errors import ERROR_MESSAGES
        categories = p.get("failure_categories") or []
        details = [ERROR_MESSAGES[category] for category in ERROR_MESSAGES if category in categories]
        if status and details:
            status += "：" + "；".join(details)
        return "%s · 第 %s/%s 批 · %s" % (group, index, total, status) if status else None
    if t == "task.rerun" and isinstance(p.get("page_refresh"), dict):
        refresh = p["page_refresh"]
        if refresh.get("review_required") is True:
            pages = [page for page in refresh.get("pages") or [] if type(page) is int and page > 0]
            missing = [page for page in refresh.get("missing_pages") or [] if type(page) is int and page > 0]
            parts = []
            if pages:
                parts.append("第 %s 页%s识别文字待核，请对照原件" % (
                    "、".join(map(str, pages[:8])), "等共 %d 页" % len(pages) if len(pages) > 8 else ""))
            if missing:
                parts.append("第 %s 页%s未取得识别文字" % (
                    "、".join(map(str, missing[:8])), "等共 %d 页" % len(missing) if len(missing) > 8 else ""))
            return "；".join(parts) or None
    if t == "material.subject_conflict":
        return "材料%s主体不一致，当前主体为%s，识别为%s，主体校验未通过" % (
            p.get("file_name") or "",
            p.get("expected_subject_name") or "未知主体",
            p.get("detected_subject_name") or "未知主体",
        )
    if t == "material.subject_unresolved":
        return "材料%s无法确认所属主体，请核对或重新上传" % (p.get("file_name") or "")
    if t == knowledge_runs.RUN_EVENT:
        if p.get("execution_mode") == "carried_forward":
            return "沿用上一轮已完成结果（本轮未重新查询）"
        messages = [item["message"] for item in p.get("limitations", []) if item.get("message")]
        prefix = "已取得 %d 项参考原文" % len(p.get("references") or [])
        return "；".join([prefix, *messages])
    if t == "knowledge.reference_invalidated":
        return "完整性核查已更新，请重新执行第二步核查后再收口"
    if t == "task.supplement_activated" and p.get("fallback_message"):
        return p["fallback_message"]
    if t == "task.supplement_activated":
        def group_labels(key):
            groups = p.get(key)
            if not isinstance(groups, list):
                return []
            return [reference_snapshots.CHECK_GROUPS[group]["label"] for group in groups
                    if isinstance(group, str) and group in reference_snapshots.CHECK_GROUPS]

        rerun, carried = group_labels("execution_groups"), group_labels("carried_forward_groups")
        parts = (["重新核查：" + "、".join(rerun)] if rerun else [])
        if carried:
            parts.append("沿用已完成结果：" + "、".join(carried))
        return "；".join(parts) or None
    if t == "material.added":
        role = ROLE_LABELS.get(p.get("role"))
        name = p.get("file_name") or "材料"
        return "「%s」%s" % (name, ("已归为「%s」" % role) if role else "已登记")
    if t == "material.role_corrected":
        return "一份材料已人工更正为「%s」" % ROLE_LABELS.get(p.get("role"), p.get("role") or "")
    if t == "material.parsed":
        name = p.get("file_name") or "材料"
        pages = p.get("pages")
        text = "「%s」已可读取" % name
        if p.get("ocr_recovered"):
            text += "（已用文字识别读取 %s 页，结果可能有误差）" % p["ocr_recovered"]
        elif p.get("degraded"):
            text += "（部分内容需人工翻看原件）"
        elif pages:
            text += "（%s 页）" % pages
        from ..request_errors import ERROR_MESSAGES
        for error in p.get("request_errors") or []:
            if not isinstance(error, dict) or error.get("category") not in ERROR_MESSAGES:
                continue
            error_pages = sorted({page for page in error.get("pages", []) if type(page) is int and page > 0})
            if error_pages:
                text += "；材料识别第%s页%s" % ("、".join(map(str, error_pages)), ERROR_MESSAGES[error["category"]])
        return text
    if t == "material.page_progress":
        name = p.get("file_name") or "材料"
        phase = {"text": "文本读取", "failed": "文字识别失败", "retry": "可疑页面补读",
                 "retry_failed": "可疑页面补读失败", "rotation": "旋转页面处理", "seal": "印章识别"}.get(p.get("source"), "文字识别")
        current, total = p.get("current_page"), p.get("total_pages")
        done, work_total = p.get("done_pages"), p.get("total_work_pages")
        page = f"{'读取失败' if p.get('source') in {'failed', 'retry_failed'} else '最近完成'}第 {current}/{total} 页" if type(current) is int and current > 0 and type(total) is int and total > 0 else "处理中"
        count = f"，已处理 {done}/{work_total} 页" if type(done) is int and type(work_total) is int and work_total > 0 else ""
        return f"{name} · {phase} · {page}{count}"
    if t in {"check.ai_advisory_started", "check.ai_advisory_completed", "check.ai_advisory_failed"}:
        group = reference_snapshots.CHECK_GROUPS.get(p.get("group_id")) or {}
        return group.get("label")
    if t == "material.parse_failed":
        if p.get("reason") == "service_restart":
            return "「%s」读取因服务重启中断，请重新上传" % (p.get("file_name") or "材料")
        return "「%s」无法读取，请检查文件是否损坏" % (p.get("file_name") or "材料")
    if t == "material.superseded":
        return "一份材料已被新上传的材料替换，旧文件仍保留可查"
    if t == "check.semantic_started":
        return "正在提取合同条款并比对总包与分包约定"
    if t == "check.semantic_progress":
        return ("已提取合同条款第 %s/%s 批" % (p.get('current'), p.get('total'))
                if p.get('phase') == 'extraction' else "一份分包合同的条款比对已完成")
    if t == "check.completed":
        counts = p.get("counts") or {}
        parts = ["%s 项%s" % (v, STATUS_LABELS.get(k, k)) for k, v in counts.items() if v]
        return "、".join(parts) if parts else None
    if t == "check.failed":
        return p.get("message") or "本次核查执行中断，可重新发起"
    # 主体中心事件（契约 v3 §2.5）：时间线必须能读懂「识别/冲突/确认/锁定」在发生什么
    code = p.get("stable_code") or ""
    if t == "subject.created":
        kind = _SUBJECT_TYPE_LABELS.get(p.get("subject_type"))
        return "已建立分包主体「%s」%s" % (code, ("（%s）" % kind) if kind else "")
    if t == "subject.type_confirmed":
        kind = _SUBJECT_TYPE_LABELS.get(p.get("subject_type"))
        return "已确认主体「%s」的类型为%s" % (code, kind or "待定")
    if t == "subject.type_locked":
        return "已锁定主体「%s」的类型，后续不可再改" % code
    if t == "subject.ai_type_detected":
        kind = _SUBJECT_TYPE_LABELS.get(p.get("ai_subject_type"))
        if kind:
            return "材料内容显示主体「%s」可能是%s" % (code, kind)
        return "材料内容暂不足以判断主体「%s」的类型" % code
    if t == "subject.type_conflict_detected":
        return "主体「%s」的 AI 类型证据与所选类别不一致：%s（已按所选类别核查）" % (
            code, p.get("type_conflict_reason") or "证据指向另一种类型")
    # T10-B ZIP 准备生命周期（契约 §7.3）：只讲「发生了什么、下一步做什么」，
    # 不暴露路径/条目数以外的实现细节，也不写容器内文件清单。
    if t == "zip.preparation_started":
        if p.get("container_count") == 0:
            return "材料已开始读取，准备完成后将开始核查"
        return "报审资料包已开始准备，将自动读取包内文件；完成后可开始核查"
    if t == "zip.container_ready":
        return "「%s」已准备完成，包内 %s 份文件已读取" % (
            p.get("file_name") or "报审资料包", p.get("child_count") or 0)
    if t == "zip.preparation_completed":
        if p.get("container_count") == 0:
            return "材料准备完成，核查已开始"
        return "报审资料包准备完成，核查已开始"
    if t == "zip.preparation_awaiting_confirmation":
        gaps = p.get("gaps") or []
        first = (gaps[0].get("message") if gaps else None)
        return "材料准备未完成：%s" % (first or "仍有主体类型待确认，请确认后重新开始核查")
    if t == "zip.preparation_failed":
        containers = p.get("containers") or []
        first = (containers[0].get("message") if containers else None)
        if p.get("reason") == "service_restart":
            return None
        return "报审资料包准备失败：%s" % (first or "请移除或重新上传该资料包后再开始核查")
    if t == "zip.preparation_recovered":
        return ("资料包准备被服务重启中断，已回到可继续状态；"
                "请重新点击开始核查，已读取的资料会直接复用")
    return None


def event_dto(ev: BsTaskEvent) -> dict:
    """时间线仅输出进度；参考原文和模型建议须经独立接口重新校验授权。"""
    sensitive = {reference_snapshots.REVIEW_EVENT, "knowledge.reference_started", knowledge_runs.RUN_EVENT,
                 "check.ai_advisory_started", "check.ai_advisory_completed",
                 "check.ai_advisory_failed", "check.ai_batch_checkpoint", "check.ai_batch_progress",
                 "check.negative_candidates_started", "check.negative_candidates_finished",
                 "check.negative_candidates_failed", "check.negative_candidate_checkpoint"}
    payload = {} if ev.event_type in sensitive else ev.payload_json or {}
    raw = ev.payload_json or {}
    progress_key = None
    if ev.event_type in {"check.ai_advisory_started", "check.ai_advisory_completed", "check.ai_advisory_failed",
                         "check.negative_candidates_started", "check.negative_candidates_finished",
                         "check.negative_candidates_failed", "check.ai_batch_progress",
                         "check.ai_batch_checkpoint", "check.negative_candidate_checkpoint"} and raw.get("run_id") and raw.get("request_id"):
        progress_key = hashlib.sha256(f'{raw["run_id"]}:{raw["request_id"]}'.encode()).hexdigest()[:16]
    label = EVENT_LABELS.get(ev.event_type, "任务动态")
    if ev.event_type == "check.ai_advisory_completed":
        if raw.get("execution_status") == "partial":
            label = "AI 辅助核查部分结果待核实"
        elif raw.get("execution_status") in {"failed", "not_run"}:
            label = "AI 辅助核查未完成"
    return {
        "sequence": ev.sequence,
        "event_type": ev.event_type,
        "label": label,
        "message": _event_message(ev),
        "stage": ev.stage,
        "payload": payload,
        "progress_key": progress_key,
        "created_at": ev.created_at.isoformat(timespec="seconds") if ev.created_at else None,
    }


async def stream_events(*, task_id: int, since_sequence: int = 0, session_factory=None):
    """SSE 增量事件流（对齐 OC stream_events 形态，BS 简化口径）：

    - 按 sequence 增量轮询转 SSE，since_sequence 断点续传（Last-Event-ID 同源）；
    - 断点越界（> 当前最大 sequence，任务曾重建）自动回退 0 全量重放；
    - keepalive 用带 event 类型的事件（注释无法被前端监听）；
    - 终态 done / 任务已删除 → 发 done 后收流。BS 无 rerun/runner 子进程/人工闸，
      故无 OC 的翻转缓冲、看门狗与 stream.hold 分支。

    session_factory 可注入（流生命周期独立于请求级依赖；测试 override 成 sqlite
    factory——默认 app.database.SessionLocal 在测试进程里指向 dev MySQL，直接用会查错库）。
    """
    db = (session_factory or SessionLocal)()
    try:
        last = since_sequence
        if last > 0:
            max_seq = (db.query(func.max(BsTaskEvent.sequence))
                       .filter(BsTaskEvent.task_id == task_id).scalar()) or 0
            if last > max_seq:
                last = 0
                yield sse_event("stream.reset", {"task_id": task_id})
        idle = 0
        while True:
            events = (db.query(BsTaskEvent)
                      .filter(BsTaskEvent.task_id == task_id,
                              BsTaskEvent.sequence > last)
                      .order_by(BsTaskEvent.sequence).all())
            chunks = []
            for ev in events:
                last = ev.sequence
                idle = 0
                chunks.append(sse_event(ev.event_type, event_dto(ev), seq=ev.sequence))
            for chunk in chunks:
                yield chunk
            db.expire_all()
            task = db.get(BsTask, task_id)
            state = task.state if task else 'deleted'
            # SSE会话只读：结束本轮事务，否则MySQL可重复读快照看不到新事件。
            db.rollback()
            if state == 'deleted':
                yield sse_event("done", {"task_id": task_id, "state": "deleted"})
                return
            if state == "done":
                yield sse_event("done", {"task_id": task_id, "state": "done"})
                return
            idle += 1
            if idle >= SSE_KEEPALIVE_TICKS:
                idle = 0
                yield sse_event("keepalive", {"task_id": task_id})
            await asyncio.sleep(SSE_IDLE_SECONDS)
    finally:
        db.close()
