"""倒查编排；复用既有持久作业、租约、事件与报告版本表，无新增DDL。"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import uuid
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from . import ai_review_jobs, parsing, review_mode, retrospective_input
from .models import BsMaterial, BsReviewVersion, BsTask, BsTaskEvent
from .retrospective_contracts import JOB_GROUP, RUN_KEY, RunIdentity
from .retrospective_sources import digest, freeze_basis, freeze_material, infer_contract_date
from .retrospective_sources import infer_project_name

SPEC = "retrospective.accepted"
INPUT = "retrospective.input_frozen"
CHECKPOINT = "retrospective.batch_completed"
BATCH_STARTED = "retrospective.batch_started"


def usage_dedupe_key(run_id, batch_id):
    """完整请求身份稳定散列，避免规范补查子请求超过台账120字符上限。"""
    if not batch_id:
        raise ValueError("倒查用量缺少请求批次身份")
    return "bs-retro:" + digest({"run_id": run_id, "batch_id": batch_id})


def _progress(db, task_id, run_id, *, phase, label, index=0, total=1, batch_id=None):
    from . import service
    service.add_event(db, task_id, BATCH_STARTED, stage=phase, payload={
        'run_id': run_id, 'phase': phase, 'label': label, 'index': index, 'total': total,
        'batch_id': batch_id or f'progress:{phase}'}, commit=False)


def model_identity(client):
    """冻结非敏感身份；不保存密钥或可能含认证信息的端点。"""
    return {"request_model": getattr(client, "model", None), "provider": getattr(client, "provider", None),
            "endpoint_sha256": digest(getattr(client, "base_url", "")),
            "enable_thinking": getattr(client, "enable_thinking", False),
            "json_output": getattr(client, "json_output", False),
            "json_schema": getattr(client, "supports_json_schema", False) is True,
            "timeout": getattr(client, "timeout", None), "max_retries": getattr(client, "max_retries", 0)}


def _event(db, task_id, kind, run_id, batch_id=None):
    rows = (db.query(BsTaskEvent).filter_by(task_id=task_id, event_type=kind)
            .order_by(BsTaskEvent.sequence.desc()).all())
    return next((row.payload_json for row in rows
                 if (row.payload_json or {}).get("run_id") == run_id
                 and (batch_id is None or row.payload_json.get("batch_id") == batch_id)), None)


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
    progress_event = (db.query(BsTaskEvent).filter_by(task_id=task.id)
                      .filter(BsTaskEvent.event_type.in_([BATCH_STARTED, CHECKPOINT]))
                      .order_by(BsTaskEvent.sequence.desc()).all()) if run_id else []
    latest = next((row.payload_json for row in progress_event if (row.payload_json or {}).get("run_id") == run_id
                   and "phase" in (row.payload_json or {})), None)
    result["progress"] = {key: latest[key] for key in ("run_id", "phase", "label", "index", "total", "batch_id", "state") if key in latest} if latest else None
    if job and job.state == "failed" and not result["report"]:
        result["error"] = "合同倒查未完成，执行状态已保存；请查看限制说明或新建核查。"
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
    baseline = (task.baseline_date or datetime.now(ZoneInfo("Asia/Shanghai")).date()).isoformat()
    job = ai_review_jobs.enqueue(db, task.id, run_id, [JOB_GROUP])
    from .retrospective_upstream_contracts import VERSION as upstream_version
    service.add_event(db, task.id, SPEC, stage="check", payload={
        "run_id": run_id, "model_code": model, "model_identity": actual_identity,
        "baseline_date": baseline, "materials": manifest,
        "relations": relations, "input_version": retrospective_input.INPUT_VERSION,
        "upstream_version": upstream_version,
        "basis": freeze_basis(), "project_name": task.project_name}, commit=False)
    db.commit()
    schedule(db.get_bind(), job["id"])
    return status(db, task)


def schedule(engine, job_id):
    from .background_ai_review import ACTIVE_JOBS
    if job_id in ACTIVE_JOBS:
        return False
    worker = asyncio.create_task(run(engine, job_id))
    ACTIVE_JOBS[job_id] = worker
    worker.add_done_callback(lambda _task: ACTIVE_JOBS.pop(job_id, None))
    return True


def recovery_exhausted(db, job):
    from . import service
    task = db.get(BsTask, job["task_id"])
    if task is not None and (task.options_snapshot_json or {}).get(RUN_KEY) == job["review_run_id"]:
        task.state = "failed"
        service.add_event(db, task.id, "retrospective.failed", stage="check", payload={
            "run_id": job["review_run_id"], "reason": "后台租约多次中断，未自动重复未知付费请求"}, commit=False)


async def run(engine, job_id, *, client=None):
    from . import service
    from .retrospective_analysis import analyze
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
            db.commit()
        if not spec:
            raise ValueError("倒查输入快照不存在")
        actual_client = client if client is not None else client_from_env(model_code=spec["model_code"], max_retries=0, json_output=True)
        if client is None and model_identity(actual_client) != spec["model_identity"]:
            raise ValueError("模型端点或运行身份已变化，未继续倒查")
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
                    if spec.get("input_version") == retrospective_input.INPUT_VERSION and any(
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
                    source = freeze_material(material, file_sha256=manifest["file_sha256"])
                    source["relation"] = selected[material.id]
                    source["general_source_set_id"] = manifest.get("general_source_set_id")
                    source["general_source_set_version"] = manifest.get("general_source_set_version")
                    if spec.get("input_version") == retrospective_input.INPUT_VERSION and (
                            material.parse_state != "done" or not any(
                                p["readable"] and p["text"].strip() for p in source["pages"])):
                        raise ValueError("合同正文未能读取，未开始条款核查")
                    sources.append(source)
            sub_sources = [row for row in sources if row["role"] in {"sub", "labor"}]
            signed, _ = infer_contract_date(sub_sources)
            with Session(engine) as db:
                owned(db)
                _progress(db, task_id, run_id, phase='retrieval', label='检索负面清单及规范依据')
                db.commit()
            def retrieve_knowledge():
                from .retrospective_knowledge import retrieve
                with Session(engine) as query_db:
                    query_task = owned(query_db)
                    query_db.commit()  # 不在网络查询期间持有任务行锁。
                    knowledge = retrieve(query_db, query_task, sub_sources, spec['basis'], contract_date=signed)
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
                baseline_date=spec["baseline_date"], contract_date=signed).model_dump(mode="json")
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
            if done:
                return done["result"]
            if started:
                return {"state": "failed", "findings": [], "limitations": [
                    "一个条款批次在请求受理后中断、结果未保存，未自动重复付费调用，该范围未完成。"]}
            return None

        active_batch_id = None
        def before_batch(batch_id, index, total):
            nonlocal active_batch_id
            active_batch_id = batch_id
            with Session(engine) as db:
                owned(db)
                phase = ('upstream' if batch_id.startswith('upstream:') else
                         'coverage' if batch_id.startswith('basis:') else 'contract_check')
                label = {'coverage': '逐条核对冻结规范与合同', 'contract_check': '核查合同条款及内部一致性',
                         'upstream': '核对总分包合同约定及适用条件'}[phase]
                _progress(db, task_id, run_id, phase=phase, label=label,
                          index=index - 1, total=total, batch_id=batch_id)
                db.commit()

        def save_checkpoint(batch_id, result):
            with Session(engine) as db:
                owned(db)
                started = _event(db, task_id, BATCH_STARTED, run_id, batch_id) or {}
                progress = {key: started[key] for key in ("phase", "label", "total") if key in started}
                if "index" in started:
                    progress["index"] = started["index"] + 1
                progress["state"] = result.get("state", "completed")
                service.add_event(db, task_id, CHECKPOINT, stage="check", payload={
                    **progress, "run_id": run_id, "batch_id": batch_id, "result": result}, commit=False)
                db.commit()

        def usage(raw):
            # 复用可信用量记账；失败必须显式终止，不用未知账实状态宣告完成。
            from ...services.token_service import record, usage_counts
            from ...models.models import TokenUsage
            input_count, output_count, token_count = usage_counts(raw)
            key = usage_dedupe_key(run_id, active_batch_id)
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

        sub_sources = [row for row in frozen["sources"] if row["role"] in {"sub", "labor"}]
        result = await analyze(sub_sources, spec["basis"], frozen["identity"], client=actual_client,
                               load_checkpoint=load_checkpoint, before_batch=before_batch,
                               save_checkpoint=save_checkpoint, on_usage=usage, knowledge=frozen.get('knowledge'))
        if frozen['identity'].get('upstream_version'):
            from .retrospective_upstream import compare
            from .retrospective_contracts import summary
            # 原检查完全未执行成功时先保留失败，避免无边界追加后续请求。
            upstream = (await compare(frozen['sources'], frozen['identity'], client=actual_client,
                load_checkpoint=load_checkpoint, before_batch=before_batch,
                save_checkpoint=save_checkpoint, on_usage=usage)
                if result['execution_status'] != 'failed' else
                {'version': spec.get('upstream_version'), 'state': 'not_executed', 'findings': [],
                 'limitations': ['分包合同检查未执行完成，未继续上下游比较。']})
            result['upstream'] = upstream
            result['findings'] = list({row['finding_id']: row for row in
                                      result['findings'] + upstream['findings']}.values())
            result['limitations'].extend(upstream['limitations'])
            if upstream['state'] != 'completed' and result['execution_status'] == 'completed':
                result['execution_status'] = 'partial'
            result['summary'] = summary(result['execution_status'], len(result['findings']))
            result['scope_note'] = '本报告为分包合同文本及总包辅助比较；覆盖状态分别记录，不认证实际履约或合同全部合规。'
        elif any(row["role"] == "general" for row in spec["materials"]):
            result["limitations"].append("已上传总包合同，本阶段尚未执行上下游辅助比对；单合同检查结果不代表与总包一致。")
            if result["execution_status"] == "completed":
                from .retrospective_contracts import summary
                result["execution_status"] = "partial"
                result["summary"] = summary("partial", len(result["findings"]))
        project_name = spec["project_name"] or infer_project_name(sub_sources)
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
                "run_id": run_id, "execution_status": result["execution_status"],
                "finding_count": len(result["findings"])}, commit=False)
            db.commit()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        with Session(engine) as db:
            task = db.query(BsTask).filter_by(id=task_id).with_for_update().one_or_none()
            if (task is not None and (task.options_snapshot_json or {}).get(RUN_KEY) == run_id
                    and ai_review_jobs.finish(db, job_id, lease, "failed", type(exc).__name__)):
                task.state = "failed"
                service.add_event(db, task_id, "retrospective.failed", stage="check", payload={
                    "run_id": run_id, "reason": "合同倒查执行中断，未形成完整报告", "error_type": type(exc).__name__}, commit=False)
                db.commit()
    finally:
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass
