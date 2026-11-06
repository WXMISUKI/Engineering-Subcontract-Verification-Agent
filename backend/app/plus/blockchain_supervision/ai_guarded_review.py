"""模型调用的短事务边界；结果仅供调用方在保存时再次持锁校验。"""
import asyncio
from copy import deepcopy
import hashlib
import json
import logging
from types import SimpleNamespace
from weakref import WeakKeyDictionary, ref

from ...models.models import Agent, ConversationMapping, KnowledgeBaseBinding, KnowledgeProvider
from . import knowledge_runs
from .ai_current_guard import AiContextChanged, assert_ai_context_current
from .ai_review_context import build_review_context, review_linked_finding
from .ai_review_batch import CHECKPOINT_SCHEMA_VERSION, execute_review_batch, plan_review_batches
from .negative_candidate_plan import prepare_candidate_batch_inputs
from .reference_snapshots import REVIEW_POINTER
from .models import BsFinding, BsMaterial, BsSubject, BsTask


_MODEL_BATCH_CONCURRENCY = 2
_MODEL_BATCH_LIMITS = WeakKeyDictionary()
logger = logging.getLogger(__name__)


def _batch_checkpointable(results):
    """证据校验后部分建议被拒绝仍属已执行；失败或未执行不能复用。"""
    return bool(results) and all(row.get("execution_status") in {"completed", "partial"} for row in results)


async def _run_bounded_batches(batches, execute):
    """同一进程事件循环最多同时执行两批，结果按原计划顺序返回。"""
    loop = asyncio.get_running_loop()
    semaphore_ref = _MODEL_BATCH_LIMITS.get(loop)
    semaphore = semaphore_ref() if semaphore_ref is not None else None
    if semaphore is None:
        semaphore = asyncio.Semaphore(_MODEL_BATCH_CONCURRENCY)
        _MODEL_BATCH_LIMITS[loop] = ref(semaphore)

    async def run_one(index, batch):
        async with semaphore:
            return await execute(index, batch)

    jobs = iter(enumerate(batches, 1))
    pending = {}
    outcomes = [None] * len(batches)
    failure = None

    def fill():
        while len(pending) < _MODEL_BATCH_CONCURRENCY:
            try:
                index, batch = next(jobs)
            except StopIteration:
                break
            pending[asyncio.create_task(run_one(index, batch))] = index

    fill()
    try:
        while pending:
            done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                index = pending.pop(task)
                try:
                    outcomes[index - 1] = task.result()
                except BaseException as exc:
                    if failure is None:
                        failure = exc
            if failure is None:
                fill()
    finally:
        for task in pending:
            if not task.done():
                task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    if failure is not None:
        raise failure
    return outcomes


def _batch_input_limitation(reason):
    if reason == "input_budget_exceeded":
        return "核查输入超出单次处理容量，本项未执行，请重试或人工核实"
    return "核查所需材料或规则依据不完整或不可用，本项未执行，请人工核实"


def _batch_progress_state(parts, *, fresh):
    if not fresh:
        return "reused"
    if any(part.get("execution_status") in {"failed", "not_run"} for part in parts):
        return "failed"
    if any(part.get("execution_status") != "completed" or part.get("assessment_incomplete") is True for part in parts):
        return "partial"
    return "completed"


def _merge_batch_diagnostics(result, part):
    """旧检查点缺少诊断时保持未知，不把缺失数据当成零。"""
    if result["model_proposal_count"] is None:
        return
    count, reasons = part.get("model_proposal_count"), part.get("rejection_reasons")
    if type(count) is not int or count < 0 or not isinstance(reasons, list):
        result["model_proposal_count"] = None
        result["rejection_reasons"] = None
        return
    result["model_proposal_count"] += count
    result["rejection_reasons"] = list(dict.fromkeys(result["rejection_reasons"] + reasons))


async def _execute_with_timeout_recovery(batch, *, client, on_usage=None, renew=None):
    """可拆批首次超时后拆成两半，各调用一次；不虚构完整材料范围。"""
    entries = batch.get("items") or []
    initial = await execute_review_batch(client=client, batch=batch, on_usage=on_usage,
                                         split_on_timeout=len(entries) >= 2)
    if (len(entries) < 2 or not initial or any(part.get("execution_status") != "failed"
            or part.get("failure_category") != "timeout" for part in initial)):
        return initial
    halves = (entries[:len(entries) // 2], entries[len(entries) // 2:])
    merged = {}
    for half in halves:
        if renew:
            renew()
        child = {**batch, "items": [{**deepcopy(entry), "incomplete_material_scope": True} for entry in half]}
        parts = await execute_review_batch(client=client, batch=child, on_usage=on_usage, max_attempts=1)
        for part in parts:
            identity = ("finding_id", part.get("finding_id")) if "finding_id" in part else ("candidate_id", part.get("candidate_id"))
            if identity not in merged:
                merged[identity] = deepcopy(part)
                continue
            current = merged[identity]
            current["proposals"].extend(part.get("proposals") or [])
            current["limitations"] = list(dict.fromkeys(current.get("limitations", []) + part.get("limitations", [])))
            _merge_batch_diagnostics(current, part)
            current["assessment_incomplete"] = bool(current.get("assessment_incomplete") or part.get("assessment_incomplete"))
            if part.get("execution_status") == "failed" or current.get("execution_status") == "failed":
                categories = {value for value in (current.get("failure_category"), part.get("failure_category")) if value}
                current["execution_status"] = "failed"
                current["failure_category"] = categories.pop() if len(categories) == 1 else "model_call_error"
            elif part.get("execution_status") != "completed" or current.get("execution_status") != "completed":
                current["execution_status"] = "partial"
    return list(merged.values())


def _lock_save_inputs(db, task_id, run):
    """保存事务按固定顺序锁定本轮核查和授权行；调用方不得提前提交。"""
    task = db.query(BsTask).filter_by(id=task_id).with_for_update().one_or_none()
    if task is None:
        raise AiContextChanged("核查任务不存在")
    mapping = db.query(ConversationMapping).filter_by(id=task.conversation_id).with_for_update().one_or_none()
    if mapping is not None:
        db.query(Agent).filter_by(id=mapping.agent_id).with_for_update().all()
    for model in (BsSubject, BsMaterial, BsFinding):
        db.query(model).filter_by(task_id=task_id).order_by(model.id).with_for_update().all()
    refs = run.get("references") or []
    provider_ids = sorted({ref["provider_id"] for ref in refs if type(ref.get("provider_id")) is int})
    binding_ids = sorted({ref["binding_id"] for ref in refs if type(ref.get("binding_id")) is int})
    db.query(KnowledgeProvider).filter(KnowledgeProvider.id.in_(provider_ids)).order_by(KnowledgeProvider.id).with_for_update().all()
    db.query(KnowledgeBaseBinding).filter(KnowledgeBaseBinding.id.in_(binding_ids)).order_by(KnowledgeBaseBinding.id).with_for_update().all()
    return task


async def run_guarded_review(*, session_factory, task_id, group_id, finding_id, user,
                             client, material_loader, on_usage=None, save_result=None):
    """调用前后独立会话复验；可选保存回调只能在本事务写入，不得提交。"""
    with session_factory() as db:
        task = db.get(BsTask, task_id)
        if task is None:
            raise AiContextChanged("核查任务不存在")
        run = knowledge_runs.get_reference_run(db, task, group_id=group_id)
        if not run:
            raise AiContextChanged("当前参考运行不存在")
        before = assert_ai_context_current(db, task, run, user)
        finding = db.get(BsFinding, finding_id)
        if finding is None or finding.task_id != task_id:
            raise AiContextChanged("核查结论不存在")
        frozen_finding = SimpleNamespace(**{key: getattr(finding, key) for key in
            ("id", "task_id", "subject_id", "material_id", "page", "item_id", "kind", "evidence_json")})
        frozen_materials = [SimpleNamespace(**{key: deepcopy(getattr(material, key)) for key in
            ("id", "task_id", "subject_id", "parse_state", "pages_json", "degradations_json", "role", "file_name",
             "container_material_id", "container_entry_path")})
            for material in material_loader(db, task)]
        subject_names_by_id = {subject.id: subject.name for subject in db.query(BsSubject).filter(
            BsSubject.task_id == task_id, BsSubject.origin != "material_auto",
            BsSubject.retired.is_(False), BsSubject.name.isnot(None)).all()}
        frozen_run = deepcopy(run)
    result = await review_linked_finding(client=client, finding=frozen_finding,
        materials=frozen_materials, run=frozen_run, on_usage=on_usage, subject_names_by_id=subject_names_by_id)
    with session_factory() as db:
        task = _lock_save_inputs(db, task_id, frozen_run) if save_result else db.get(BsTask, task_id)
        if task is None or assert_ai_context_current(db, task, frozen_run, user) != before:
            raise AiContextChanged("模型调用期间核查输入或授权已变化")
        if save_result is not None:
            save_result(db, task, frozen_run, finding_id, deepcopy(result))
            db.commit()
    return result


async def run_guarded_negative_candidates(*, session_factory, task_id, user, client, material_loader,
                                          max_batches: int, on_usage=None, before_batch=None, save_result=None,
                                          load_checkpoint=None, save_checkpoint=None, checkpoint_namespace=None,
                                          on_batch_progress=None):
    """对当前代内部候选分批判断；租约、计费及持久化由调用方负责。"""
    if type(max_batches) is not int or max_batches < 1:
        raise ValueError("候选核查批次数量预算无效")
    if (load_checkpoint or save_checkpoint) and (not isinstance(checkpoint_namespace, str) or not checkpoint_namespace):
        raise ValueError("候选批次恢复缺少模型版本标识")
    with session_factory() as db:
        task = db.get(BsTask, task_id)
        if task is None:
            raise AiContextChanged("核查任务不存在")
        run = knowledge_runs.get_reference_run(db, task, group_id="a1")
        if not run:
            raise AiContextChanged("当前参考运行不存在")
        before = assert_ai_context_current(db, task, run, user, require_references=False)
        subjects = db.query(BsSubject).filter_by(task_id=task_id).all()
        materials = material_loader(db, task)
        findings = db.query(BsFinding).filter_by(task_id=task_id).all()
        prepared = prepare_candidate_batch_inputs(
            task_id=task_id, run=run, subjects=subjects, materials=materials, findings=findings,
            current_review_run_id=(task.options_snapshot_json or {}).get(REVIEW_POINTER),
            current_material_fingerprint=knowledge_runs.material_fingerprint(db, task),
            current_rule_fingerprint=knowledge_runs.rule_fingerprint())
        unresolved = [subject.id for subject in subjects if not subject.retired and
                      (not subject.type_confirmed or subject.type_conflict or subject.subject_type not in {"labor", "professional"})]
        no_confirmed = not any(not subject.retired and subject.type_confirmed and not subject.type_conflict
                               and subject.subject_type in {"labor", "professional"} for subject in subjects)
        frozen_run = deepcopy(run)
    plan = plan_review_batches(prepared["items"])
    results = {candidate["candidate_id"]: {**candidate, "execution_status": "not_run", "proposals": [],
               "limitations": [], "batches_total": 0, "batches_completed": 0,
               "model_proposal_count": 0, "rejection_reasons": []}
               for candidate in prepared["candidates"]}
    if len(plan["batches"]) > max_batches:
        for item in prepared["items"]:
            result = results[item["candidate_id"]]
            result["unavailable_reason"] = "batch_budget_exceeded"
            result["limitations"].append("核查所需批次数超过本次预算，本项未执行")
        plan["batches"] = []
    partial, failed = set(), set()
    for limitation in plan["limitations"]:
        results[limitation["candidate_id"]]["limitations"].append(_batch_input_limitation(limitation.get("reason")))
    for batch in plan["batches"]:
        for candidate_id in {entry["candidate_id"] for entry in batch["items"]}:
            results[candidate_id]["batches_total"] += 1
    async def execute_one(batch_index, batch):
        with session_factory() as db:
            task = db.get(BsTask, task_id)
            if task is None or assert_ai_context_current(db, task, frozen_run, user) != before:
                raise AiContextChanged("模型调用前核查输入或授权已变化")
        if before_batch:
            before_batch(frozen_run)
        batch_key = hashlib.sha256(json.dumps({"namespace": checkpoint_namespace,
            "validation_version": CHECKPOINT_SCHEMA_VERSION, "batch": batch},
            ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        parts = load_checkpoint(frozen_run, batch_key) if load_checkpoint else None
        fresh = parts is None
        if fresh:
            if on_batch_progress:
                on_batch_progress(frozen_run, batch_index, len(plan["batches"]), "processing")
            parts = await _execute_with_timeout_recovery(batch, client=client, on_usage=on_usage,
                renew=(lambda: before_batch(frozen_run)) if before_batch else None)
        expected = {entry["candidate_id"] for entry in batch["items"]}
        if (not isinstance(parts, list) or any(not isinstance(part, dict) for part in parts)
                or len(parts) != len(expected) or {part.get("candidate_id") for part in parts} != expected):
            raise AiContextChanged("批次结果不完整或与候选不一致")
        with session_factory() as db:
            task = _lock_save_inputs(db, task_id, frozen_run) if fresh and save_checkpoint else db.get(BsTask, task_id)
            if task is None or assert_ai_context_current(db, task, frozen_run, user) != before:
                raise AiContextChanged("模型调用期间核查输入或授权已变化")
            if fresh and save_checkpoint and _batch_checkpointable(parts):
                save_checkpoint(db, task, frozen_run, batch_key, deepcopy(parts))
                db.commit()
        if on_batch_progress:
            state = _batch_progress_state(parts, fresh=fresh)
            categories = sorted({part.get("failure_category") for part in parts if part.get("failure_category")})
            on_batch_progress(frozen_run, batch_index, len(plan["batches"]), state, categories)
        if fresh and any(part.get("failure_category") for part in parts):
            logger.warning("AI batch failed task=%s group=negative batch=%s/%s categories=%s",
                           task_id, batch_index, len(plan["batches"]),
                           sorted({part["failure_category"] for part in parts if part.get("failure_category")}))
        return parts

    for parts in await _run_bounded_batches(plan["batches"], execute_one):
        for part in parts:
            result = results[part["candidate_id"]]
            result["proposals"].extend(part["proposals"])
            result["limitations"].extend(part["limitations"])
            if part.get("failure_category"):
                result.setdefault("failure_categories", [])
                if part["failure_category"] not in result["failure_categories"]:
                    result["failure_categories"].append(part["failure_category"])
            _merge_batch_diagnostics(result, part)
            if part["execution_status"] == "completed":
                result["batches_completed"] += 1
                if part.get("assessment_incomplete", not part["proposals"]):
                    partial.add(part["candidate_id"])
            elif part["execution_status"] == "partial":
                partial.add(part["candidate_id"])
            else:
                failed.add(part["candidate_id"])
    for key, result in results.items():
        total = result["batches_total"]
        if total:
            result["execution_status"] = ("failed" if key in failed else "partial" if key in partial
                                          or result["batches_completed"] != total else "completed")
        if result["execution_status"] == "failed" and result["proposals"]:
            result["proposals"] = []
            result["limitations"].append("部分模型调用失败，本项建议未进入复核")
        result["state"] = result["execution_status"]
        result["reviewable_partial"] = result["execution_status"] == "partial" and key not in failed
        result["limitations"] = list(dict.fromkeys(result["limitations"]))
    output = {"candidates": list(results.values()), "unresolved_subject_ids": unresolved,
              "no_confirmed_subjects": no_confirmed}
    if save_result:
        with session_factory() as db:
            task = _lock_save_inputs(db, task_id, frozen_run)
            if assert_ai_context_current(db, task, frozen_run, user, require_references=False) != before:
                raise AiContextChanged("保存前核查输入或授权已变化")
            save_result(db, task, frozen_run, deepcopy(output))
            db.commit()
    return output


async def run_guarded_batch_review(*, session_factory, task_id, group_id, finding_ids, user,
                                   client, material_loader, on_usage=None, save_result=None, before_batch=None,
                                   load_checkpoint=None, save_checkpoint=None, checkpoint_namespace=None,
                                   on_batch_progress=None):
    """隔离编排同组分批调用；调用方须另行实现跨请求租约与计费。"""
    if not finding_ids or any(type(item) is not int for item in finding_ids) or len(set(finding_ids)) != len(finding_ids):
        raise ValueError("核查结论标识无效或重复")
    if (load_checkpoint or save_checkpoint) and (not isinstance(checkpoint_namespace, str) or not checkpoint_namespace):
        raise ValueError("批次恢复缺少模型版本标识")
    with session_factory() as db:
        task = db.get(BsTask, task_id)
        if task is None:
            raise AiContextChanged("核查任务不存在")
        run = knowledge_runs.get_reference_run(db, task, group_id=group_id)
        if not run:
            raise AiContextChanged("当前参考运行不存在")
        before = assert_ai_context_current(db, task, run, user)
        if not set(finding_ids).issubset(set(run.get("finding_ids") or [])):
            raise AiContextChanged("核查结论不属于当前运行")
        findings = [db.get(BsFinding, finding_id) for finding_id in finding_ids]
        if any(finding is None or finding.task_id != task_id for finding in findings):
            raise AiContextChanged("核查结论不存在")
        materials = [SimpleNamespace(**{key: deepcopy(getattr(material, key)) for key in
            ("id", "task_id", "subject_id", "parse_state", "pages_json", "degradations_json", "role", "file_name",
             "container_material_id", "container_entry_path")})
            for material in material_loader(db, task)]
        names = {subject.id: subject.name for subject in db.query(BsSubject).filter(
            BsSubject.task_id == task_id, BsSubject.origin != "material_auto",
            BsSubject.retired.is_(False), BsSubject.name.isnot(None)).all()}
        frozen_run = deepcopy(run)
        items = []
        for finding in findings:
            context = build_review_context(finding=finding, materials=materials, run=frozen_run,
                                           subject_names_by_id=names)
            items.append({"finding_id": finding.id, "task_id": task_id, "item_id": finding.item_id,
                          "subject_id": finding.subject_id, **context})
    plan = plan_review_batches(items)
    results = {item["finding_id"]: {"finding_id": item["finding_id"], "proposals": [],
        "limitations": list(item["limitations"]), "execution_status": "not_run",
        "reviewable_not_run": (
            item.get("material_unavailable") is True
            and item.get("materials") == []
            and isinstance(item.get("references"), list)
            and bool(item["references"])
            and all(isinstance(ref, dict) and isinstance(ref.get("reference_id"), str)
                    and ref["reference_id"] and isinstance(ref.get("text"), str)
                    and ref["text"].strip() for ref in item["references"])
        ),
        "batches_total": 0, "batches_completed": 0,
        "model_proposal_count": 0, "rejection_reasons": []} for item in items}
    partially_validated, failed_calls = set(), set()
    for limitation in plan["limitations"]:
        if limitation.get("reason") == "input_budget_exceeded":
            results[limitation["finding_id"]]["reviewable_not_run"] = False
        results[limitation["finding_id"]]["limitations"].append(_batch_input_limitation(limitation.get("reason")))
    for batch in plan["batches"]:
        for finding_id in {entry["finding_id"] for entry in batch["items"]}:
            results[finding_id]["batches_total"] += 1
    async def execute_one(batch_index, batch):
        with session_factory() as db:
            task = db.get(BsTask, task_id)
            if task is None or assert_ai_context_current(db, task, frozen_run, user) != before:
                raise AiContextChanged("模型调用前核查输入或授权已变化")
        if before_batch is not None:
            before_batch(frozen_run)
        batch_key = hashlib.sha256(json.dumps({"namespace": checkpoint_namespace, "batch": batch}, ensure_ascii=False, sort_keys=True,
                                              separators=(",", ":")).encode("utf-8")).hexdigest()
        batch_result = load_checkpoint(frozen_run, batch_key) if load_checkpoint is not None else None
        fresh = batch_result is None
        if fresh:
            if on_batch_progress:
                on_batch_progress(frozen_run, batch_index, len(plan["batches"]), "processing")
            batch_result = await _execute_with_timeout_recovery(batch, client=client, on_usage=on_usage,
                renew=(lambda: before_batch(frozen_run)) if before_batch else None)
        expected_ids = {entry["finding_id"] for entry in batch["items"]}
        if (not isinstance(batch_result, list) or any(not isinstance(row, dict) for row in batch_result)
                or len(batch_result) != len(expected_ids)
                or {row.get("finding_id") for row in batch_result} != expected_ids):
            raise AiContextChanged("批次结果不完整或与当前核查项不一致")
        with session_factory() as db:
            task = _lock_save_inputs(db, task_id, frozen_run) if fresh and save_checkpoint else db.get(BsTask, task_id)
            if task is None or assert_ai_context_current(db, task, frozen_run, user) != before:
                raise AiContextChanged("模型调用期间核查输入或授权已变化")
            if fresh and save_checkpoint is not None and _batch_checkpointable(batch_result):
                save_checkpoint(db, task, frozen_run, batch_key, deepcopy(batch_result))
                db.commit()
        if on_batch_progress:
            state = _batch_progress_state(batch_result, fresh=fresh)
            categories = sorted({part.get("failure_category") for part in batch_result if part.get("failure_category")})
            on_batch_progress(frozen_run, batch_index, len(plan["batches"]), state, categories)
        if fresh and any(row.get("failure_category") for row in batch_result):
            logger.warning("AI batch failed task=%s group=%s batch=%s/%s categories=%s",
                           task_id, group_id, batch_index, len(plan["batches"]),
                           sorted({row["failure_category"] for row in batch_result if row.get("failure_category")}))
        return batch_result

    for batch_result in await _run_bounded_batches(plan["batches"], execute_one):
        for part in batch_result:
            result = results[part["finding_id"]]
            result["proposals"].extend(part["proposals"])
            result["limitations"].extend(part["limitations"])
            if part.get("failure_category"):
                result.setdefault("failure_categories", [])
                if part["failure_category"] not in result["failure_categories"]:
                    result["failure_categories"].append(part["failure_category"])
            _merge_batch_diagnostics(result, part)
            if part["execution_status"] == "completed":
                result["batches_completed"] += 1
                if part.get("assessment_incomplete", not part.get("proposals")):
                    partially_validated.add(part["finding_id"])
            elif part["execution_status"] == "partial":
                partially_validated.add(part["finding_id"])
            else:
                failed_calls.add(part["finding_id"])
    for result in results.values():
        total, completed = result["batches_total"], result["batches_completed"]
        if total:
            result["reviewable_not_run"] = False
            result["execution_status"] = ("failed" if result["finding_id"] in failed_calls else
                                          "completed" if completed == total and result["finding_id"] not in partially_validated else
                                          "partial" if completed or result["finding_id"] in partially_validated else "failed")
            if completed != total:
                result["limitations"].append(
                    "部分模型调用未完成，请人工核实或重试" if result["finding_id"] in failed_calls
                    else "部分模型建议未通过校验，相关内容需人工核实")
        result["reviewable_partial"] = result["execution_status"] == "partial" and result["finding_id"] not in failed_calls
        result["limitations"] = list(dict.fromkeys(result["limitations"]))
        if any(entry["finding_id"] == result["finding_id"] and entry["material"]["text_start"] > 0
               for batch in plan["batches"] for entry in batch["items"]):
            result["limitations"].append("材料按页内片段分批，跨片段语义和证据需人工核实")
    output = list(results.values())
    if save_result is not None:
        with session_factory() as db:
            task = _lock_save_inputs(db, task_id, frozen_run)
            if assert_ai_context_current(db, task, frozen_run, user) != before:
                raise AiContextChanged("保存前核查输入或授权已变化")
            save_result(db, task, frozen_run, deepcopy(output))
            db.commit()
    return output
