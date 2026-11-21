"""倒查编排；复用既有持久作业、租约、事件与报告版本表，无新增DDL。"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import uuid

from sqlalchemy.orm import Session

from . import ai_review_jobs, parsing, review_mode, retrospective_input
from .models import BsMaterial, BsReviewVersion, BsTask, BsTaskEvent
from .retrospective_analysis import ExecutionBoundaryError
from .retrospective_contracts import JOB_GROUP, PROMPT_VERSION, RUN_KEY, RunIdentity
from .retrospective_sources import digest, freeze_basis, freeze_material, infer_contract_date, reading_limitations
from .retrospective_sources import infer_project_name

SPEC = "retrospective.accepted"
INPUT = "retrospective.input_frozen"
CHECKPOINT = "retrospective.batch_completed"
BATCH_STARTED = "retrospective.batch_started"
FAILED = "retrospective.failed"
RESUMED = "retrospective.resumed"
COMPLETION_POLICY = "required-stages-v1"


class IncompleteRun(ExecutionBoundaryError):
    def __init__(self, reason, *, phase, batch_id=None, retryable=False):
        super().__init__(reason)
        self.details = {"reason": reason, "phase": phase, "batch_id": batch_id, "retryable": retryable}


PHASES = {
    'contract_check': '核查合同条款及内部一致性',
    'coverage': '逐条核对冻结规范与合同',
    'upstream_extract': '提取总包与分包合同的身份和条款',
    'upstream_relationship': '核对总分包合同的项目、主体和范围',
    'upstream': '核对总分包合同约定及适用条件',
    'review': '复核候选问题和合同上下文',
    'citation_check': '核对问题引用原页',
}


def _batch_phase(batch_id):
    if batch_id.startswith('upstream:review:') or batch_id.startswith('final:') or batch_id.endswith(':review'):
        return 'review'
    if batch_id.startswith('upstream:extract:'):
        return 'upstream_extract'
    if batch_id.startswith('upstream:relationship:'):
        return 'upstream_relationship'
    if batch_id.startswith('upstream:'):
        return 'upstream'
    if batch_id.startswith('basis:'):
        return 'coverage'
    if batch_id.startswith('visual:'):
        return 'citation_check'
    return 'contract_check'


def usage_dedupe_key(run_id, batch_id, *, attempt=1):
    """完整请求身份稳定散列，避免规范补查子请求超过台账120字符上限。"""
    if not batch_id:
        raise ValueError("倒查用量缺少请求批次身份")
    identity = {"run_id": run_id, "batch_id": batch_id}
    if attempt > 1:
        identity["attempt"] = attempt
    return "bs-retro:" + digest(identity)


def _progress(db, task_id, run_id, *, phase, label, index=0, total=1, batch_id=None, attempt=1):
    from . import service
    service.add_event(db, task_id, BATCH_STARTED, stage=phase, payload={
        'run_id': run_id, 'phase': phase, 'label': label, 'index': index, 'total': total,
        'batch_id': batch_id or f'progress:{phase}', 'attempt': attempt}, commit=False)


def model_identity(client):
    """冻结非敏感身份；不保存密钥或可能含认证信息的端点。"""
    return {"request_model": getattr(client, "model", None), "provider": getattr(client, "provider", None),
            "endpoint_sha256": digest(getattr(client, "base_url", "")),
            "enable_thinking": getattr(client, "enable_thinking", False),
            "json_output": getattr(client, "json_output", False),
            "json_schema": getattr(client, "supports_json_schema", False) is True,
            "timeout": getattr(client, "timeout", None), "max_retries": getattr(client, "max_retries", 0)}


def _event(db, task_id, kind, run_id, batch_id=None):
    rows = db.query(BsTaskEvent.payload_json).filter(
        BsTaskEvent.task_id == task_id, BsTaskEvent.event_type == kind,
        BsTaskEvent.payload_json["run_id"].as_string() == run_id)
    if batch_id is not None:
        rows = rows.filter(BsTaskEvent.payload_json["batch_id"].as_string() == batch_id)
    row = rows.order_by(BsTaskEvent.sequence.desc()).first()
    return row[0] if row else None


def _path(material):
    if not material.path:
        return None
    root = Path(__file__).resolve().parents[2]
    path = (root / material.path).resolve()
    if not path.is_relative_to(root / "uploads") or not path.is_file():
        raise ValueError("合同原件路径无效或文件不存在")
    return path


def _file_hash(material):
    path = _path(material)
    if path is None:
        return None
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(part)
    return checksum.hexdigest()


def _render_citation_page(material, page, expected_hash):
    from ..structural_check.ocr_text import _render_pages
    from .retrospective_visual import DPI
    path = _path(material)
    if path is None or _file_hash(material) != expected_hash:
        raise ValueError('问题引用原件已变化，停止原页核对')
    rendered = _render_pages(str(path), [page - 1], DPI)
    if page - 1 not in rendered or _file_hash(material) != expected_hash:
        raise ValueError('问题引用页不存在或原件在渲染期间变化')
    return rendered[page - 1]['png']


def _materials(db, task_id):
    return (db.query(BsMaterial).filter_by(task_id=task_id, superseded_by_id=None,
                                         replacement_target_id=None).order_by(BsMaterial.id).all())


def status(db, task, *, version_no=None):
    run_id = (task.options_snapshot_json or {}).get(RUN_KEY)
    job = ai_review_jobs.get_job(db, task.id, run_id) if run_id else None
    result = {"task_id": task.id, "review_mode": review_mode.RETROSPECTIVE,
              "state": job.state if job else "not_started", "run_id": run_id, "report": None}
    versions = db.query(BsReviewVersion).filter_by(task_id=task.id)
    if version_no is not None:
        versions = versions.filter_by(version_no=version_no)
    version = versions.order_by(BsReviewVersion.version_no.desc()).first()
    report = (version.snapshot_json or {}).get("retrospective_report") if version else None
    if report and (version_no is not None or report["identity"]["run_id"] == run_id):
        result.update(report=deepcopy(report), version_no=version.version_no,
                      state=report["execution_status"])
    progress_event = (db.query(BsTaskEvent.payload_json).filter(
        BsTaskEvent.task_id == task.id, BsTaskEvent.event_type.in_([BATCH_STARTED, CHECKPOINT]),
        BsTaskEvent.payload_json["run_id"].as_string() == run_id,
        BsTaskEvent.payload_json["phase"].as_string().isnot(None))
        .order_by(BsTaskEvent.sequence.desc()).first()) if run_id else None
    latest = progress_event[0] if progress_event else None
    result["progress"] = {key: latest[key] for key in ("run_id", "phase", "label", "index", "total", "batch_id", "state") if key in latest} if latest else None
    if job and job.state in {"failed", "interrupted"} and not result["report"]:
        failure = _event(db, task.id, FAILED, run_id) or {}
        result["state"] = "failed"
        result["error"] = failure.get("reason") or "合同倒查未完成，请查看时间线。"
        result["failure"] = {key: failure[key] for key in ("phase", "batch_id", "reason") if key in failure}
        result["can_resume"] = failure.get("retryable") is True
    return result


def accept(db, task, actor, *, relations=None):
    from . import service
    from ...services.billing.bindings import effective_model_code_or_default
    from ..startup_conditions_check.llm import client_from_env

    db.rollback()
    task = db.query(BsTask).filter_by(id=task.id).populate_existing().with_for_update().one()
    if not review_mode.is_retrospective(task):
        raise ValueError("该任务不是合同倒查阶段")
    if task.created_by != actor.id or task.created_by_type != actor.user_type:
        raise PermissionError("任务不存在或无访问权限")
    if (task.options_snapshot_json or {}).get(RUN_KEY):
        return status(db, task)
    if task.state != "setup":
        raise ValueError("当前任务状态不能开始合同倒查")
    # SQLite不支持行锁；CAS同时保护多进程重复受理，MySQL继续沿用上方行锁。
    claimed = db.query(BsTask).filter_by(id=task.id, state="setup").update(
        {"state": "running"}, synchronize_session=False)
    if claimed != 1:
        db.rollback()
        db.refresh(task)
        if (task.options_snapshot_json or {}).get(RUN_KEY):
            return status(db, task)
        raise ValueError("倒查任务状态已变化，请刷新")
    validation = retrospective_input.readiness(db, task, relations=relations)
    if not validation["hard_ok"]:
        raise ValueError("；".join(validation["hard_gaps"]))
    relations = validation["contract_relations"]
    materials = _materials(db, task.id)
    manifest = [{"material_id": row.id, "role": row.role, "file_sha256": _file_hash(row),
                 "revision_no": row.revision_no, "subject_id": row.subject_id,
                 "general_source_set_id": row.general_source_set_id,
                 "general_source_set_version": row.general_source_set_version} for row in materials]
    if any(not row["file_sha256"] for row in manifest):
        raise ValueError("合同原件不存在，不能开始核查")
    model = effective_model_code_or_default(db, "bs_task", task.id)
    try:
        actual_identity = model_identity(client_from_env(model_code=model, max_retries=0, json_output=True))
    except Exception as exc:
        raise ValueError("所选模型尚未配置可用端点或密钥，合同倒查尚未开始") from exc
    run_id = "retro-" + uuid.uuid4().hex
    task.options_snapshot_json = {**(task.options_snapshot_json or {}), RUN_KEY: run_id}
    task.state, task.started_at = "running", datetime.now(timezone.utc).replace(tzinfo=None)
    baseline_source = service.baseline_date_source(task)
    baseline = (task.baseline_date.isoformat()
                if baseline_source == "manual" and task.baseline_date else None)
    current_notice_date = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    job = ai_review_jobs.enqueue(db, task.id, run_id, [JOB_GROUP])
    service.add_event(db, task.id, SPEC, stage="check", payload={
        "run_id": run_id, "model_code": model, "model_identity": actual_identity,
        "baseline_date": baseline, "baseline_date_source": baseline_source,
        "current_notice_date": current_notice_date, "materials": manifest,
        "relations": relations, "input_version": retrospective_input.INPUT_VERSION,
        **_build_versions(), "completion_policy": COMPLETION_POLICY,
        "basis": freeze_basis(), "project_name": task.project_name}, commit=False)
    db.commit()
    schedule(db.get_bind(), job["id"])
    return status(db, task)


def resume(db, task, actor):
    from . import service

    db.rollback()
    task = db.query(BsTask).filter_by(id=task.id).populate_existing().with_for_update().one()
    if not review_mode.is_retrospective(task):
        raise ValueError("该任务不是合同倒查阶段")
    if task.created_by != actor.id or task.created_by_type != actor.user_type:
        raise PermissionError("任务不存在或无访问权限")
    current = status(db, task)
    if current["state"] in {"queued", "running"} or current["report"]:
        return current
    run_id = current["run_id"]
    spec = _event(db, task.id, SPEC, run_id) if run_id else None
    if not spec or spec.get("completion_policy") != COMPLETION_POLICY or not current.get("can_resume"):
        raise ValueError(current.get("error") or "该任务没有可续跑的合同核查")
    _validate_build(spec, _event(db, task.id, INPUT, run_id))
    _validate_manifest(db, task.id, spec)
    changed = db.query(BsTask).filter_by(id=task.id, state="failed").update(
        {"state": "running", "completed_at": None}, synchronize_session=False)
    if changed != 1:
        db.rollback()
        db.refresh(task)
        return status(db, task)
    job = ai_review_jobs.enqueue(db, task.id, run_id, [JOB_GROUP], retry=True)
    previous = current["failure"]
    stage_label = PHASES.get(previous.get("phase"))
    continuation = f"从「{stage_label}」未完成的范围继续。" if stage_label else "继续未完成的范围。"
    service.add_event(db, task.id, RESUMED, stage="check", payload={
        "run_id": run_id, "attempt": job["attempt"],
        "public_message": "将复用成功检查点，" + continuation,
        "previous_failure": previous,
    }, commit=False)
    db.commit()
    schedule(db.get_bind(), job["id"])
    return status(db, task)


def _build_versions():
    from .retrospective_coverage import VERSION as coverage_version
    from .retrospective_upstream_contracts import VERSION as upstream_version
    from .retrospective_visual import VERSION as visual_version

    return {"prompt_version": PROMPT_VERSION, "coverage_version": coverage_version,
            "upstream_version": upstream_version, "visual_version": visual_version}


def _validate_build(spec, frozen=None):
    if any(spec.get(key) != value for key, value in _build_versions().items()):
        raise ValueError("核查规则或分析版本已变化，请恢复原版本或新建核查任务。")
    if frozen:
        identity = frozen["identity"]
        if (identity.get("material_fingerprint") != digest(frozen["sources"])
                or identity.get("basis_fingerprint") != digest(spec["basis"])
                or identity.get("knowledge_fingerprint") != digest(frozen.get("knowledge"))):
            raise ValueError("冻结正文或核查依据已变化，请新建核查任务。")


def _validate_manifest(db, task_id, spec):
    materials = {row.id: row for row in _materials(db, task_id)}
    if set(materials) != {row["material_id"] for row in spec["materials"]}:
        raise ValueError("合同材料集合已变化，请新建核查任务。")
    for frozen in spec["materials"]:
        material = materials[frozen["material_id"]]
        if (_file_hash(material) != frozen["file_sha256"] or any(
                getattr(material, field) != frozen[field] for field in (
                    "role", "revision_no", "subject_id", "general_source_set_id", "general_source_set_version"))):
            raise ValueError("合同原件、归属或版本已变化，请新建核查任务。")


def _require_readable_sources(sources):
    gaps = reading_limitations(sources)
    if gaps:
        notes = []
        for source in sources:
            for field, label in (("repetitive_heading_pages", "重复编号标题"), ("repeated_empty_table_pages", "重复空表格行")):
                if affected := source["reading_coverage"].get(field):
                    pages = "、".join(map(str, affected))
                    notes.append(f"《{source['file_name']}》第{pages}页的已存识别结果出现大量{label}。")
        raise IncompleteRun(" ".join(gaps + notes) + " 条款核查未开始，请查看材料读取记录。", phase="parse")


def schedule(engine, job_id):
    from .background_ai_review import ACTIVE_JOBS
    existing = ACTIVE_JOBS.get(job_id)
    if existing is not None and not existing.done():
        return False
    worker = asyncio.create_task(run(engine, job_id))
    ACTIVE_JOBS[job_id] = worker

    def remove_worker(finished):
        if ACTIVE_JOBS.get(job_id) is finished:
            ACTIVE_JOBS.pop(job_id)

    worker.add_done_callback(remove_worker)
    return True


def recovery_exhausted(db, job):
    from . import service
    task = db.get(BsTask, job["task_id"])
    if task is not None and (task.options_snapshot_json or {}).get(RUN_KEY) == job["review_run_id"]:
        task.state = "failed"
        reason = "后台租约多次中断，系统未自动重复结果未知的付费请求。"
        service.add_event(db, task.id, FAILED, stage="check", payload={
            "run_id": job["review_run_id"], "reason": reason, "public_message": reason}, commit=False)


async def run(engine, job_id, *, client=None):
    from . import retrospective_analysis, retrospective_upstream, service
    from .retrospective_report import build_report
    from ...standalone.identity import User
    from ..startup_conditions_check.llm import client_from_env

    with Session(engine) as db:
        job = ai_review_jobs.claim(db, job_id)
        db.commit()
    if job is None:
        return
    task_id, run_id, lease = job["task_id"], job["review_run_id"], job["lease_token"]

    def owned(db):
        task = db.query(BsTask).filter_by(id=task_id).populate_existing().with_for_update().one_or_none()
        if (task is None or not review_mode.is_retrospective(task) or task.state != "running"
                or (task.options_snapshot_json or {}).get(RUN_KEY) != run_id):
            raise ValueError("倒查任务或输入代次已变化")
        account = db.get(User, task.created_by)
        if account is None or not account.is_active:
            raise ValueError("账号不可用")
        from .access import require_bs_task
        require_bs_task(db, task_id, task.created_by, task.created_by_type)
        if not ai_review_jobs.heartbeat(db, job_id, lease):
            raise ValueError("倒查后台租约已失效")
        return task

    owner = asyncio.current_task()
    async def keep_alive():
        while True:
            await asyncio.sleep(30)
            try:
                with Session(engine) as db:
                    owned(db)
                    db.commit()
            except Exception:
                owner.cancel()
                return

    heartbeat = asyncio.create_task(keep_alive())
    try:
        with Session(engine) as db:
            owned(db)
            spec = _event(db, task_id, SPEC, run_id)
            frozen = _event(db, task_id, INPUT, run_id)
            resumed = _event(db, task_id, RESUMED, run_id) or {}
            db.commit()
        if not spec:
            raise ValueError("倒查输入快照不存在")
        require_completion = spec.get("completion_policy") == COMPLETION_POLICY
        explicit_attempt = resumed.get("attempt", 1)
        attempt = job["attempt"]
        actual_client = client if client is not None else client_from_env(model_code=spec["model_code"], max_retries=0, json_output=True)
        if client is None and model_identity(actual_client) != spec["model_identity"]:
            raise IncompleteRun("模型端点或运行身份已变化，请恢复原配置后重新核查。", phase="setup",
                                retryable=require_completion)
        if require_completion:
            with Session(engine) as db:
                owned(db)
                try:
                    _validate_build(spec, frozen)
                    _validate_manifest(db, task_id, spec)
                except ValueError as exc:
                    raise IncompleteRun(str(exc), phase="parse") from exc
                db.commit()
        # 已冻结的正文不可因重启重新读取，未完成读取仅用既有解析器处理。
        if frozen is None:
            sources = []
            selected = {row["material_id"]: row["relation"] for row in spec["relations"]}
            readable_manifests = [row for row in spec["materials"] if row["material_id"] in selected]
            for material_index, manifest in enumerate(readable_manifests, 1):
                with Session(engine) as db:
                    owned(db)
                    _progress(db, task_id, run_id, phase='parse', label='解析合同文件',
                              index=material_index - 1, total=len(readable_manifests))
                    db.commit()
                if manifest["material_id"] not in selected:
                    continue
                with Session(engine) as db:
                    owned(db)
                    material = db.get(BsMaterial, manifest["material_id"])
                    if material is None or material.task_id != task_id or _file_hash(material) != manifest["file_sha256"]:
                        raise ValueError("合同原件已变化，不能沿用原运行")
                    if spec.get("input_version") in retrospective_input.MANIFEST_INPUT_VERSIONS and any(
                            getattr(material, key) != manifest[key] for key in (
                                "role", "revision_no", "subject_id", "general_source_set_id", "general_source_set_version")):
                        raise ValueError("合同归属或版本已变化，不能沿用原运行")
                    path = _path(material)
                    needs_read = material.parse_state == "uploaded" and path is not None
                    if material.parse_state == "parsing":
                        # 上次进程在解析中断开；未知OCR响应不自动再付费。
                        material.parse_state = "failed"
                        material.degradations_json = [{"kind": "parse_interrupted", "message": "合同读取中断，未自动重复请求"}]
                    if needs_read:
                        material.parse_state = "parsing"
                    db.commit()
                if needs_read:
                    try:
                        pages, degradations = await asyncio.to_thread(parsing.parse_material_pages, str(path))
                        with Session(engine) as db:
                            owned(db)
                            material = db.get(BsMaterial, manifest["material_id"])
                            if _file_hash(material) != manifest["file_sha256"]:
                                raise ValueError("读取期间合同原件发生变化")
                            material.pages_json, material.degradations_json, material.parse_state = pages, degradations, "done"
                            material.pages = max([row["page"] for row in pages] + [
                                row.get("total_pages") or 0 for row in degradations], default=0)
                            db.commit()
                    except Exception as exc:
                        with Session(engine) as db:
                            owned(db)
                            material = db.get(BsMaterial, manifest["material_id"])
                            material.parse_state = "failed"
                            material.degradations_json = [{"kind": "parse_failed", "message": type(exc).__name__}]
                            db.commit()
                with Session(engine) as db:
                    material = db.get(BsMaterial, manifest["material_id"])
                    source = freeze_material(material, file_sha256=manifest["file_sha256"],
                                             recheck_cached_quality=require_completion)
                    source["relation"] = selected[material.id]
                    source["general_source_set_id"] = manifest.get("general_source_set_id")
                    source["general_source_set_version"] = manifest.get("general_source_set_version")
                    if spec.get("input_version") in retrospective_input.MANIFEST_INPUT_VERSIONS and (
                            material.parse_state != "done" or not any(
                                p["readable"] and p["text"].strip() for p in source["pages"])):
                        if require_completion:
                            raise IncompleteRun(f"《{source['file_name']}》未取得可用合同正文，请查看材料读取记录。",
                                                phase="parse")
                        raise ValueError("合同正文未能读取，未开始条款核查")
                    sources.append(source)
            if require_completion:
                _require_readable_sources(sources)
            sub_sources = [row for row in sources if row["role"] in {"sub", "labor"}]
            signed, _ = infer_contract_date(sub_sources)
            baseline_date = spec.get("baseline_date") or signed
            baseline_date_source = ("manual" if spec.get("baseline_date") else
                                    "subcontract_signing_date" if signed else "unknown")
            with Session(engine) as db:
                owned(db)
                _progress(db, task_id, run_id, phase='retrieval', label='检索负面清单及规范依据')
                db.commit()
            def retrieve_knowledge():
                from .retrospective_knowledge import retrieve
                with Session(engine) as query_db:
                    query_task = owned(query_db)
                    query_db.commit()  # 不在网络查询期间持有任务行锁。
                    knowledge = retrieve(query_db, query_task, sources, spec['basis'],
                                         contract_date=signed, baseline_date=baseline_date)
                    owned(query_db)
                    query_db.commit()
                    return knowledge
            knowledge = await asyncio.to_thread(retrieve_knowledge)
            from .retrospective_coverage import plan
            coverage_plan = plan(spec['basis'])
            identity = RunIdentity(run_id=run_id, task_id=task_id, model_code=spec["model_code"],
                model_identity=spec["model_identity"],
                material_fingerprint=digest(sources), basis_fingerprint=digest(spec["basis"]),
                knowledge_fingerprint=digest(knowledge),
                coverage_version=coverage_plan['version'], coverage_fingerprint=digest(coverage_plan),
                upstream_version=spec.get('upstream_version'),
                visual_version=spec.get('visual_version'),
                baseline_date=baseline_date, baseline_date_source=baseline_date_source,
                current_notice_date=spec.get("current_notice_date"),
                contract_date=signed).model_dump(mode="json")
            frozen = {"run_id": run_id, "sources": sources, "identity": identity, 'knowledge': knowledge}
            with Session(engine) as db:
                owned(db)
                service.add_event(db, task_id, INPUT, stage="check", payload=frozen, commit=False)
                db.commit()

        def load_checkpoint(batch_id):
            with Session(engine) as db:
                owned(db)
                done = _event(db, task_id, CHECKPOINT, run_id, batch_id)
                started = _event(db, task_id, BATCH_STARTED, run_id, batch_id)
                db.commit()
            if require_completion:
                if started and (not done or started.get("attempt", 1) > done.get("attempt", 1)):
                    raise IncompleteRun("合同核查请求已开始，结果未保存，系统未自动重复付费。请核对服务端记录。",
                                        phase=_batch_phase(batch_id), batch_id=batch_id)
                if not done:
                    return None
                failure = done.get("failure")
                if failure is not None:
                    if failure["retryable"] and explicit_attempt > done.get("attempt", 1):
                        return None
                    raise IncompleteRun(**failure)
                return done["result"]
            if done:
                return done["result"]
            if started:
                return {"state": "failed", "findings": [], "limitations": [
                    "一个条款批次在请求受理后中断、结果未保存，未自动重复付费调用，该范围未完成。"]}
            return None

        active_batch_id = None
        batch_limits = {'contract_check': retrospective_analysis.MAX_BATCHES,
                        'upstream_extract': retrospective_upstream.MAX_EXTRACTION_BATCHES}
        batch_starts = dict.fromkeys(batch_limits, 0)

        def before_batch(batch_id, index, total):
            nonlocal active_batch_id
            phase = _batch_phase(batch_id)
            starts_contract_batch = phase == 'contract_check' and batch_id.endswith(':contract')
            # 成功检查点和同批次的后续复核子请求不占用新的批次名额。
            if require_completion and (starts_contract_batch or phase == 'upstream_extract'):
                if batch_starts[phase] >= batch_limits[phase]:
                    raise IncompleteRun(
                        f"「{PHASES[phase]}」达到单轮上限（{batch_limits[phase]}批）。"
                        f"点击重新核查可从下一批（第{index}/{total}批）继续。",
                        phase=phase, batch_id=batch_id, retryable=True)
                batch_starts[phase] += 1
            active_batch_id = batch_id
            with Session(engine) as db:
                owned(db)
                _progress(db, task_id, run_id, phase=phase, label=PHASES[phase],
                          index=index - 1, total=total, batch_id=batch_id, attempt=attempt)
                db.commit()

        def save_checkpoint(batch_id, result):
            failure = None
            with Session(engine) as db:
                owned(db)
                started = _event(db, task_id, BATCH_STARTED, run_id, batch_id) or {}
                progress = {key: started[key] for key in ("phase", "label", "total") if key in started}
                if "index" in started:
                    progress["index"] = started["index"] + 1
                progress["state"] = result.get("state", "completed")
                if result.get("restored_from"):
                    progress.update(phase="coverage", label="已复用保存的明细恢复规范核查批次")
                if require_completion and progress["state"] == "failed":
                    from ...models.models import TokenUsage
                    phase = _batch_phase(batch_id)
                    error_type = result.get("error_type") or (result.get("failure") or {}).get("error_type")
                    checked_response = error_type in {"ValueError", "ValidationError", "JSONDecodeError",
                                                     "InvalidModelResponse", "TypeError", "KeyError"}
                    accounted = (db.query(TokenUsage.id).filter_by(
                        dedupe_key=usage_dedupe_key(run_id, active_batch_id, attempt=attempt)
                    ).one_or_none() is not None) if active_batch_id else False
                    retryable = checked_response and accounted
                    index = progress.get("index")
                    position = f"第{index}批" if index else "当前批次"
                    prefix = f"{PHASES[phase]}的{position}未完成"
                    if retryable:
                        failure_code = (result.get("failure_detail") or {}).get("code")
                        descriptions = {"skipped_contract_timing": "合同日期或期限计算约定被略过",
                                        "wrong_term_evidence": "条款描述与所选原文编号不符"}
                        description = descriptions.get(failure_code, "模型结果未通过校验")
                        reason = f"{prefix}：{description}，可重新核查。"
                    elif error_type == "StructuredOutputTruncated":
                        reason = prefix + "：模型输出达到长度限制，响应被截断。请核对模型输出配置和批次范围。"
                    elif error_type in {"TimeoutError", "APITimeoutError", "ReadTimeout", "ConnectTimeout"}:
                        reason = prefix + "：模型请求超时，需核对请求状态及计费记录。"
                    elif error_type == "InputBudgetExceeded":
                        reason = prefix + "：输入超过单次处理预算，请核对批次切分。"
                    else:
                        reason = prefix + "：请核对服务状态及请求记录。"
                    failure = IncompleteRun(reason, phase=phase, batch_id=batch_id, retryable=retryable)
                service.add_event(db, task_id, CHECKPOINT, stage="check", payload={
                    **progress, "run_id": run_id, "batch_id": batch_id, "attempt": attempt, "result": result,
                    **({"failure": failure.details} if failure else {})}, commit=False)
                db.commit()
            if failure:
                raise failure

        def usage(raw):
            # 复用可信用量记账；失败必须显式终止，不用未知账实状态宣告完成。
            from ...services.token_service import record, usage_counts
            from ...models.models import TokenUsage
            input_count, output_count, token_count = usage_counts(raw)
            key = usage_dedupe_key(run_id, active_batch_id, attempt=attempt if require_completion else 1)
            with Session(engine) as db:
                task = owned(db)
                record(db=db, user_id=task.created_by, user_type=task.created_by_type,
                       conversation_id=task.conversation_id, input_count=input_count, output_count=output_count,
                       token_count=token_count, raw_usage=raw, model_code=spec["model_code"],
                       dedupe_key=key,
                       source="plus:blockchain-supervision", stage="contract_retrospective",
                       billing_scene="subcontract_review", scope_type="bs_task", scope_id=str(task_id))
                # 共享记账入口保留旁路语义；倒查必须确认写入成功才继续请求。
                db.flush()
                if db.query(TokenUsage.id).filter_by(dedupe_key=key).one_or_none() is None:
                    raise RuntimeError("倒查用量未落盘，已停止后续请求")
                db.commit()

        if require_completion:
            _require_readable_sources(frozen["sources"])
        sub_sources = [row for row in frozen["sources"] if row["role"] in {"sub", "labor"}]
        result = await retrospective_analysis.analyze(
            sub_sources, spec["basis"], frozen["identity"], client=actual_client,
            load_checkpoint=load_checkpoint, before_batch=before_batch,
            save_checkpoint=save_checkpoint, on_usage=usage, knowledge=frozen.get('knowledge'),
            checkpoint_subrequests=require_completion)
        if require_completion:
            coverage = result["coverage"]
            if not coverage["batches_total"] or coverage["batches_completed"] != coverage["batches_total"]:
                raise IncompleteRun(
                    f"合同条款只完成{coverage['batches_completed']}/{coverage['batches_total']}批，请查看批次范围。",
                    phase="contract_check")
            basis_coverage = result.get("basis_coverage") or {}
            basis_execution = basis_coverage.get("execution", {})
            if basis_execution.get("state") != "completed":
                completed = basis_execution.get("batches_completed", 0)
                total = basis_execution.get("batches_total", 0)
                incomplete = next((row for row in basis_coverage.get("scans", [])
                                   if row.get("error_type") == "IncompleteStoredCheckpoint"), {})
                reason = ("汇总检查点缺少可核对的扫描或复核明细，请核对该批次记录。" if incomplete
                          else "请核对缺失或不完整的批次记录。")
                raise IncompleteRun(
                    f"规范核查仅确认完成{completed}/{total}批。" + reason,
                    phase="coverage", batch_id=incomplete.get("checkpoint_id"))
        has_general = any(row['role'] == 'general' for row in frozen['sources'])
        if not has_general and spec.get("input_version") == retrospective_input.INPUT_VERSION:
            result['upstream'] = {'state': 'not_applicable', 'findings': [], 'limitations': [],
                                  'reason': '未提供总包合同，本次不包含总分包比对。'}
        elif frozen['identity'].get('upstream_version'):
            from .retrospective_contracts import summary
            # 原检查完全未执行成功时先保留失败，避免无边界追加后续请求。
            upstream = (await retrospective_upstream.compare(
                frozen['sources'], frozen['identity'], client=actual_client,
                load_checkpoint=load_checkpoint, before_batch=before_batch,
                save_checkpoint=save_checkpoint, on_usage=usage, require_complete_response=require_completion)
                if result['execution_status'] != 'failed' else
                {'version': spec.get('upstream_version'), 'state': 'not_executed', 'findings': [],
                 'limitations': ['分包合同检查未执行完成，未继续上下游比较。']})
            result['upstream'] = upstream
            if require_completion:
                counts = upstream.get("coverage") or {}
                if not counts.get("batches_total") or counts["batches_completed"] != counts["batches_total"]:
                    raise IncompleteRun("总分包条款未全部提取，请查看提取批次。", phase="upstream")
            result['findings'] = list({row['finding_id']: row for row in
                                      result['findings'] + upstream['findings']}.values())
            result['limitations'].extend(upstream['limitations'])
            if upstream['state'] != 'completed' and result['execution_status'] == 'completed':
                result['execution_status'] = 'partial'
            result['summary'] = summary(result['execution_status'], len(result['findings']))
        elif any(row["role"] == "general" for row in spec["materials"]):
            result["limitations"].append("已上传总包合同，本阶段尚未执行上下游辅助比对；单合同检查结果不代表与总包一致。")
            if result["execution_status"] == "completed":
                from .retrospective_contracts import summary
                result["execution_status"] = "partial"
                result["summary"] = summary("partial", len(result["findings"]))
        if frozen['identity'].get('visual_version') and result['execution_status'] != 'failed':
            from .retrospective_visual import verify_findings

            def render_page(material_id, page, file_sha256):
                with Session(engine) as db:
                    owned(db)
                    material = db.get(BsMaterial, material_id)
                    if material is None or material.task_id != task_id:
                        raise ValueError('问题引用原件不属于当前任务')
                    db.commit()  # 渲染期间不占用任务行锁。
                    return _render_citation_page(material, page, file_sha256)

            result = await verify_findings(result, frozen['sources'], client=actual_client,
                render_page=render_page, load_checkpoint=load_checkpoint,
                before_batch=before_batch, save_checkpoint=save_checkpoint, on_usage=usage)
            visual = result["citation_visual"]
            if require_completion and visual["pages_checked"] != visual["pages_total"]:
                raise IncompleteRun("问题引用的原页复核尚未完成，请查看原页核对记录。", phase="citation_check")
        project_name = spec["project_name"] or infer_project_name(sub_sources)
        if require_completion:
            result["required_stages_completed"] = True
        with Session(engine) as db:
            owned(db)
            _progress(db, task_id, run_id, phase='report', label='汇总问题并生成报告')
            db.commit()
        report = build_report(result, project_name=project_name, sources=frozen["sources"])
        with Session(engine) as db:
            task = owned(db)
            latest = (db.query(BsReviewVersion).filter_by(task_id=task_id)
                      .order_by(BsReviewVersion.version_no.desc()).first())
            db.add(BsReviewVersion(task_id=task_id, version_no=latest.version_no + 1 if latest else 1,
                                  trigger="completed", snapshot_json={"retrospective_report": report}))
            failed = result["execution_status"] == "failed"
            if not ai_review_jobs.finish(db, job_id, lease, "failed" if failed else "completed"):
                raise ValueError("倒查租约失效，不能保存报告")
            task.state = "failed" if failed else "done"
            task.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            task.elapsed_seconds = max(0., (task.completed_at - task.started_at).total_seconds()) if task.started_at else None
            service.add_event(db, task_id, "retrospective.completed", stage="report", payload={
                "run_id": run_id, "execution_status": report["execution_status"],
                "finding_count": len(report["findings"])}, commit=False)
            db.commit()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        with Session(engine) as db:
            task = db.query(BsTask).filter_by(id=task_id).with_for_update().one_or_none()
            if (task is not None and (task.options_snapshot_json or {}).get(RUN_KEY) == run_id
                    and ai_review_jobs.finish(db, job_id, lease, "failed", type(exc).__name__)):
                task.state = "failed"
                details = exc.details if isinstance(exc, IncompleteRun) else {
                    "reason": "合同倒查执行中断，未形成完整报告，请查看时间线。"}
                service.add_event(db, task_id, FAILED, stage="check", payload={
                    "run_id": run_id, "error_type": type(exc).__name__,
                    "public_message": details["reason"], **details}, commit=False)
                db.commit()
    finally:
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass
