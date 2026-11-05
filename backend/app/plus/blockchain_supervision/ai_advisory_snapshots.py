"""冻结当前代模型建议，并在历史读取时重新检查依据授权。"""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import HTTPException
from ...models.models import ConversationMapping, KnowledgeBaseBinding, KnowledgeRef
from ...services.knowledge.context import OWNER_SR_CONVERSATION
from ...services.knowledge.reference_access import authorized_reference_provider
from ...services.billing.bindings import effective_model_code_or_default
from . import ai_evidence, ai_review_batch, knowledge_runs, negative_candidate_plan
from .ai_current_guard import AiContextChanged, assert_ai_context_current
from .models import BsFinding, BsMaterial, BsTaskEvent
from .rules import ATTACHMENT_SEAL_ITEM, negative_list_rules
from .reference_snapshots import CHECK_GROUPS, REVIEW_POINTER


def advisory_pending(event):
    if not event or event.event_type not in {"check.ai_advisory_started", "check.negative_candidates_started"}:
        return False
    try:
        deadline = datetime.fromisoformat((event.payload_json or {})["expires_at"])
    except (KeyError, TypeError, ValueError):
        return True
    return deadline.tzinfo is None or deadline > datetime.now(timezone.utc)


def advisory_validation_current(event):
    """历史事件不更改，但只有当前校验版本的结果能作为当前建议复用。"""
    return bool(event and (event.payload_json or {}).get("validation_version") == ai_review_batch.CHECKPOINT_SCHEMA_VERSION)


def summarize_negative_list_entries(run, findings, candidates):
    """只聚合已有记录；AI 执行状态不能转换为规则结论。"""
    unavailable = {"state": "unavailable", "entries": []}
    if (type(run.get("task_id")) is not int or any(not isinstance(run.get(key), str) or not run[key]
            for key in ("run_id", "review_run_id", "material_fingerprint", "rule_fingerprint"))):
        return unavailable
    catalog = {row["id"] for row in negative_list_rules()}
    rows = {}
    for finding in findings:
        key = (finding.subject_id, finding.item_id)
        if finding.item_id not in catalog or finding.status not in {"hit", "miss", "undecidable"} or key in rows:
            return unavailable
        rows[key] = {"subject_id": finding.subject_id, "item_id": finding.item_id,
                     "rule_status": finding.status, "ai_execution": "not_recorded"}
    seen_ids = set()
    for candidate in candidates:
        if not isinstance(candidate, dict):
            return unavailable
        key = (candidate.get("subject_id"), candidate.get("item_id"))
        identity = candidate.get("candidate_id")
        state = candidate.get("execution_status")
        total, completed = candidate.get("batches_total"), candidate.get("batches_completed")
        if (type(key[0]) is not int or key[0] < 1 or key[1] not in catalog
                or not isinstance(identity, str) or not identity or identity in seen_ids
                or state not in {"completed", "partial", "failed", "not_run"}
                or type(total) is not int or type(completed) is not int or total < 0
                or completed < 0 or completed > total
                or (state in {"completed", "partial"} and total == 0)
                or ("proposals" in candidate and not isinstance(candidate["proposals"], list))
                or any(candidate.get(k) != run.get(k) for k in
                       ("task_id", "run_id", "review_run_id", "material_fingerprint", "rule_fingerprint"))
                or (key in rows and rows[key]["ai_execution"] != "not_recorded")):
            return unavailable
        seen_ids.add(identity)
        row = rows.setdefault(key, {"subject_id": key[0], "item_id": key[1],
                                    "rule_status": None, "ai_execution": "not_recorded"})
        if state == "completed" and not candidate.get("proposals"):
            state = "partial"
        row["ai_execution"] = state
    entries = sorted(rows.values(), key=lambda row: (row["subject_id"] or 0, row["item_id"]))
    return {"state": "available", "entries": entries,
            "ai_attempted": sum(row["ai_execution"] in {"completed", "partial"} for row in entries),
            "ai_unresolved": sum(row["ai_execution"] in {"partial", "failed", "not_run"} for row in entries)}


def collect_current_negative_list_coverage(db, task, user):
    """仅投影当前审查代和当前授权下的规则、候选执行记录。"""
    unavailable = {"state": "unavailable", "entries": []}
    run = knowledge_runs.get_reference_run(db, task, group_id="a1")
    review_id = (task.options_snapshot_json or {}).get(REVIEW_POINTER)
    if (not run or run.get("state") != "completed" or not review_id
            or run.get("review_run_id") != review_id or run.get("task_id") != task.id):
        return unavailable
    try:
        assert_ai_context_current(db, task, run, user, allow_completed=True)
    except (AiContextChanged, HTTPException):
        return unavailable
    event = (db.query(BsTaskEvent).filter(BsTaskEvent.task_id == task.id,
        BsTaskEvent.event_type.in_(("check.negative_candidates_started", "check.negative_candidates_finished",
                                    "check.negative_candidates_failed")),
        BsTaskEvent.payload_json["run_id"].as_string() == run["run_id"])
        .order_by(BsTaskEvent.sequence.desc()).first())
    if (not event or event.event_type != "check.negative_candidates_finished"
            or not advisory_validation_current(event)):
        return unavailable
    payload = event.payload_json or {}
    result = payload.get("result")
    candidates = result.get("candidates") if isinstance(result, dict) else None
    if (payload.get("review_run_id") != review_id or not isinstance(candidates, list)
            or payload.get("model_code") != effective_model_code_or_default(db, "bs_task", task.id)):
        return unavailable
    ids = run.get("finding_ids")
    if not isinstance(ids, list) or any(type(value) is not int for value in ids):
        return unavailable
    findings = (db.query(BsFinding).filter(BsFinding.task_id == task.id,
        BsFinding.id.in_(ids), BsFinding.kind == "negative_list").all())
    return summarize_negative_list_entries(run, findings, candidates)


def active_candidate_finding_ids(db, task, user) -> set[int]:
    """只公开当前代、已完成且依据仍获授权的候选复核记录。"""
    run = knowledge_runs.get_reference_run(db, task, group_id="a1")
    review_id = (task.options_snapshot_json or {}).get(REVIEW_POINTER)
    if not run or run.get("state") != "completed" or not review_id or run.get("review_run_id") != review_id:
        return set()
    try:
        assert_ai_context_current(db, task, run, user, allow_completed=True)
    except (AiContextChanged, HTTPException):
        return set()
    event = (db.query(BsTaskEvent).filter(BsTaskEvent.task_id == task.id,
        BsTaskEvent.event_type.in_(("check.negative_candidates_started", "check.negative_candidates_finished",
                                    "check.negative_candidates_failed")),
        BsTaskEvent.payload_json["run_id"].as_string() == run["run_id"])
        .order_by(BsTaskEvent.sequence.desc()).first())
    if (not event or event.event_type != "check.negative_candidates_finished"
            or not advisory_validation_current(event)):
        return set()
    payload = event.payload_json or {}
    records = payload.get("finding_records")
    if payload.get("review_run_id") != review_id or not isinstance(records, list):
        return set()
    ids, keys = [], []
    references = {ref.get("reference_id"): ref.get("quote") for ref in run.get("references") or []}
    for row in records:
        if not isinstance(row, dict) or not isinstance(row.get("key"), str) or not row["key"].startswith("ncf-"):
            return set()
        evidence = row.get("rule_evidence")
        if (type(row.get("finding_id")) is not int or row["finding_id"] < 1
                or not isinstance(evidence, list) or not evidence
                or any(not isinstance(ref, dict) or not isinstance(ref.get("reference_id"), str)
                       or not isinstance(ref.get("quote"), str) or not ref["quote"].strip()
                       or not isinstance(references.get(ref.get("reference_id")), str)
                       or not ai_evidence.contains_quote(references[ref["reference_id"]], ref["quote"]) for ref in evidence)):
            return set()
        ids.append(row["finding_id"])
        keys.append(row["key"])
    return set(ids) if len(ids) == len(set(ids)) and len(keys) == len(set(keys)) else set()


def filter_current_candidate_findings(db, task, user, findings: list) -> list:
    """当前任务隐藏暂缓项；候选行只在当前代及授权有效时展示。"""
    findings = [finding for finding in findings if getattr(finding, "item_id", None) != ATTACHMENT_SEAL_ITEM]
    if not any(getattr(finding, "kind", None) == "negative_candidate" for finding in findings):
        return findings
    allowed = active_candidate_finding_ids(db, task, user)
    return [finding for finding in findings
            if getattr(finding, "kind", None) != "negative_candidate" or finding.id in allowed]


def advisory_target_ids(run):
    finding_ids = set(run.get("finding_ids") or [])
    finding_ids -= {link.get("finding_id") for link in (run.get("finding_links") or [])
                    if isinstance(link, dict) and link.get("item_id") == ATTACHMENT_SEAL_ITEM}
    if run.get("execution_mode") == "partial":
        carried = run.get("carried_forward_finding_ids")
        if not isinstance(carried, list) or any(type(item) is not int for item in carried):
            raise ValueError("局部核查的沿用范围不完整，请重新核查")
        finding_ids -= set(carried)
    return finding_ids


def advisory_group_ids(run):
    finding_ids = advisory_target_ids(run)
    return list(dict.fromkeys(link["finding_id"] for link in run.get("finding_links") or []
                              if type(link.get("finding_id")) is int and link["finding_id"] in finding_ids
                              and link.get("reference_ids")))


def timeout_reviewable_ids(db, task, runs, reviewable_predicate):
    """只允许有可处理规则问题、当前原文材料和关联依据的超时项转人工。"""
    if reviewable_predicate is None:
        return set()
    ids = {value for run in runs for value in (run.get("finding_ids") or []) if type(value) is int}
    findings = db.query(BsFinding).filter(BsFinding.task_id == task.id, BsFinding.id.in_(ids)).all()
    materials = db.query(BsMaterial).filter_by(task_id=task.id, parse_state="done").all()
    allowed = set()
    for finding in findings:
        if finding.status not in {"hit", "undecidable"} or not reviewable_predicate(finding):
            continue
        if not any((finding.subject_id is None or material.subject_id == finding.subject_id)
                   and isinstance(material.pages_json, list)
                   and any(isinstance(page, dict) and isinstance(page.get("text"), str) and page["text"].strip()
                           for page in material.pages_json)
                   and material.superseded_by_id is None for material in materials):
            continue
        for run in runs:
            references = [r for r in run.get("references") or [] if isinstance(r, dict)]
            for link in run.get("finding_links") or []:
                if (link.get("finding_id") != finding.id or link.get("item_id") != finding.item_id
                        or link.get("kind") != finding.kind or link.get("subject_id") != finding.subject_id):
                    continue
                ref_ids = link.get("reference_ids") or []
                def authorized_reference(ref_id):
                    if not isinstance(ref_id, str) or not ref_id:
                        return False
                    matches = [ref for ref in references if ref.get("reference_id") == ref_id]
                    return (len(matches) == 1 and isinstance(matches[0].get("quote"), str)
                            and bool(matches[0]["quote"].strip())
                            and ("subject_ids" not in matches[0] or (isinstance(matches[0]["subject_ids"], list)
                                 and finding.subject_id in matches[0]["subject_ids"])))
                if isinstance(ref_ids, list) and ref_ids and all(authorized_reference(ref_id) for ref_id in ref_ids):
                    allowed.add(finding.id)
    return allowed


def summarize_coverage(snapshot, runs, events, *, timeout_reviewable_ids=None, scope_reviewable_ids=None):
    """仅描述本次自动核查范围；历史未标记的任务不推测覆盖情况。"""
    if not snapshot or snapshot.get("ai_auto_enabled") is not True:
        return None
    groups = snapshot.get("ai_auto_groups")
    if not isinstance(groups, list) or any(group not in CHECK_GROUPS for group in groups):
        return {"state": "unavailable", "groups": []}
    output = []
    for group in groups:
        run = next((r for r in runs if r.get("group_id") == group and r.get("state") == "completed"), None)
        if not run or not run.get("run_id"):
            output.append({"group_id": group, "label": CHECK_GROUPS[group]["label"], "state": "unavailable"})
            continue
        try:
            ids = advisory_target_ids(run)
            linked = set(advisory_group_ids(run))
        except ValueError:
            output.append({"group_id": group, "label": CHECK_GROUPS[group]["label"], "state": "unavailable"})
            continue
        counts = {key: 0 for key in ("not_started", "pending", "completed", "partial", "failed", "not_run")}
        latest = {}
        reviewable_partial = 0
        reviewable_not_run = 0
        unlocated_scope_reviewable = 0
        reviewable_failed_ids = []
        failure_categories = set()
        version_changed = False
        for event in events:
            payload = event.payload_json or {}
            finding_id = payload.get("finding_id")
            if payload.get("run_id") == run["run_id"] and finding_id in linked and finding_id not in latest:
                latest[finding_id] = event
        for finding_id in linked:
            event = latest.get(finding_id)
            empty_completed = False
            if event is not None and not advisory_validation_current(event):
                version_changed = True
            if event is None:
                state = "not_started"
            elif event.event_type == "check.ai_advisory_started":
                state = "pending" if advisory_pending(event) else "failed"
            elif not advisory_validation_current(event):
                state = "failed"
            elif event.event_type == "check.ai_advisory_failed":
                state = "failed"
            else:
                state = (event.payload_json or {}).get("execution_status", "completed")
                empty_completed = state == "completed" and (event.payload_json or {}).get("proposals") == []
                if empty_completed:
                    state = "partial"
                if state not in counts:
                    state = "failed"
            counts[state] += 1
            if state == "failed" and event is not None:
                categories = (event.payload_json or {}).get("failure_categories") or []
                if isinstance(categories, list):
                    failure_categories.update(c for c in categories if isinstance(c, str) and c in {
                        "timeout", "invalid_response", "input_budget", "model_call_error"})
                if (event.event_type == "check.ai_advisory_completed" and advisory_validation_current(event)
                        and categories == ["timeout"] and finding_id in (timeout_reviewable_ids or set())):
                    reviewable_failed_ids.append(finding_id)
            if state == "partial" and (empty_completed or (event.payload_json or {}).get("reviewable_partial") is True):
                reviewable_partial += 1
            if state == "not_run" and (event.payload_json or {}).get("reviewable_not_run") is True:
                reviewable_not_run += 1
        if group == "a1":
            candidate_event = next((event for event in events if
                event.event_type in {"check.negative_candidates_started", "check.negative_candidates_finished",
                                     "check.negative_candidates_failed"}
                and (event.payload_json or {}).get("run_id") == run["run_id"]), None)
            if candidate_event is not None and not advisory_validation_current(candidate_event):
                version_changed = True
            if candidate_event is None:
                counts["not_started"] += 1
            elif candidate_event.event_type == "check.negative_candidates_started":
                counts["pending" if advisory_pending(candidate_event) else "failed"] += 1
            elif not advisory_validation_current(candidate_event):
                counts["failed"] += 1
            elif candidate_event.event_type == "check.negative_candidates_failed":
                counts["failed"] += 1
            else:
                result = (candidate_event.payload_json or {}).get("result") or {}
                rows = result.get("candidates")
                if not isinstance(rows, list):
                    counts["failed"] += 1
                else:
                    for row in rows:
                        state = row.get("execution_status") if isinstance(row, dict) else None
                        state = state if state in counts else "failed"
                        counts[state] += 1
                        if state == "partial" and row.get("reviewable_partial") is True:
                            reviewable_partial += 1
                        if (state == "not_run" and row.get("unavailable_reason") == "contract_scope_unlocated"
                                and row.get("candidate_id") in (scope_reviewable_ids or set())):
                            reviewable_not_run += 1
                            unlocated_scope_reviewable += 1
                    if result.get("no_confirmed_subjects") or result.get("unresolved_subject_ids"):
                        counts["not_run"] += 1
        if version_changed:
            # 同组存在旧口径时，未开始的条目也须由用户主动重试，
            # 避免页面恢复把版本升级变成隐式模型调用。
            counts["failed"] += counts["not_started"]
            counts["not_started"] = 0
        output.append({"group_id": group, "label": CHECK_GROUPS[group]["label"], "state": "available", "counts": counts, "skipped": len(ids - linked), "reviewable_partial": reviewable_partial, "reviewable_not_run": reviewable_not_run, "unlocated_scope_reviewable": unlocated_scope_reviewable, "reviewable_failed": len(reviewable_failed_ids), "reviewable_timeout_finding_ids": sorted(reviewable_failed_ids), "failure_categories": sorted(failure_categories)})
    return {"state": "available", "groups": output}


def unlocated_scope_reviewable_ids(db, task, user, run, events):
    """只有当前授权下实际存在且身份一致的人工记录才允许继续。"""
    if not run or run.get('group_id') != 'a1':
        return set()
    event = next((event for event in events if event.event_type in {
        'check.negative_candidates_started', 'check.negative_candidates_finished', 'check.negative_candidates_failed'
    } and (event.payload_json or {}).get('run_id') == run.get('run_id')), None)
    if not event or event.event_type != 'check.negative_candidates_finished' or not advisory_validation_current(event):
        return set()
    payload = event.payload_json or {}
    result_payload = payload.get('result')
    if not isinstance(result_payload, dict):
        return set()
    candidates = result_payload.get('candidates')
    records = payload.get('finding_records')
    if not isinstance(candidates, list) or not isinstance(records, list):
        return set()
    if (any(not isinstance(row, dict) or not isinstance(row.get('candidate_id'), str) for row in candidates)
            or len({row['candidate_id'] for row in candidates}) != len(candidates)):
        return set()
    active = active_candidate_finding_ids(db, task, user)
    records_by_key = {row.get('key'): row for row in records if isinstance(row, dict)}
    if len(records_by_key) != len(records):
        return set()
    result = set()
    for candidate in candidates:
        if not isinstance(candidate, dict) or candidate.get('execution_status') != 'not_run' or candidate.get('unavailable_reason') != 'contract_scope_unlocated':
            continue
        candidate_id = candidate.get('candidate_id')
        if not isinstance(candidate_id, str) or not candidate_id.startswith('nc-'):
            continue
        contracts = candidate.get('contract_ids')
        if (not isinstance(contracts, list) or not contracts
                or any(type(value) is not int or value < 1 for value in contracts)
                or candidate.get('material_pages') != [] or candidate.get('incomplete_material_scope') is not True
                or candidate.get('batches_total') != 0 or candidate.get('batches_completed') != 0
                or candidate.get('proposals') != []):
            continue
        if any(candidate.get(key) != run.get(key) or not run.get(key) for key in
               ('task_id', 'run_id', 'review_run_id', 'material_fingerprint', 'rule_fingerprint')):
            continue
        record = records_by_key.get(negative_candidate_plan.unlocated_scope_key(candidate_id))
        if not record or record.get('finding_id') not in active:
            continue
        refs = record.get('rule_evidence') or []
        ids = candidate.get('reference_ids')
        if (not isinstance(ids, list) or not ids or any(not isinstance(value, str) or not value for value in ids)
                or not isinstance(refs, list)
                or any(not isinstance(ref, dict) or not isinstance(ref.get('reference_id'), str) for ref in refs)
                or sorted(ids) != sorted(ref.get('reference_id') for ref in refs)):
            continue
        finding = db.get(BsFinding, record['finding_id'])
        if (finding is None or finding.task_id != task.id or finding.subject_id != candidate.get('subject_id')
                or finding.item_id != candidate.get('item_id') or finding.kind != 'negative_candidate'
                or finding.status != 'undecidable' or finding.source != '分包范围待核实'
                or finding.material_id is not None or finding.page is not None or finding.quote or finding.evidence_json):
            continue
        result.add(candidate_id)
    return result


def collect_current_coverage(db, task, snapshot, reviewable_predicate=None):
    if not snapshot or snapshot.get("ai_auto_enabled") is not True:
        return None
    user = SimpleNamespace(id=task.created_by, user_type=task.created_by_type)
    runs = []
    for group in CHECK_GROUPS:
        run = knowledge_runs.get_reference_run(db, task, group_id=group)
        if not run or run.get("state") != "completed":
            continue
        try:
            assert_ai_context_current(db, task, run, user, allow_completed=True, require_references=False)
        except (AiContextChanged, HTTPException):
            continue
        runs.append(run)
    events = (db.query(BsTaskEvent).filter(BsTaskEvent.task_id == task.id,
        BsTaskEvent.event_type.in_(("check.ai_advisory_started", "check.ai_advisory_completed", "check.ai_advisory_failed",
                                    "check.negative_candidates_started", "check.negative_candidates_finished",
                                    "check.negative_candidates_failed")))
        .order_by(BsTaskEvent.sequence.desc()).all())
    a1_run = next((run for run in runs if run.get('group_id') == 'a1'), None)
    return summarize_coverage(snapshot, runs, events,
        timeout_reviewable_ids=timeout_reviewable_ids(db, task, runs, reviewable_predicate),
        scope_reviewable_ids=unlocated_scope_reviewable_ids(db, task, user, a1_run, events))


def collect_current_advisories(db, task):
    user = SimpleNamespace(id=task.created_by, user_type=task.created_by_type)
    events = (db.query(BsTaskEvent).filter(BsTaskEvent.task_id == task.id,
        BsTaskEvent.event_type.in_(("check.ai_advisory_started", "check.ai_advisory_completed", "check.ai_advisory_failed")))
        .order_by(BsTaskEvent.sequence.desc()).all())
    if not events:
        return []
    result = []
    for group in CHECK_GROUPS:
        run = knowledge_runs.get_reference_run(db, task, group_id=group)
        if not run or run.get("state") != "completed":
            continue
        if not any((event.payload_json or {}).get("run_id") == run.get("run_id") for event in events):
            continue
        try:
            assert_ai_context_current(db, task, run, user, allow_completed=True)
        except (AiContextChanged, HTTPException):
            continue
        sources = [{key: ref.get(key) for key in ("provider_id", "binding_id", "kb_id", "knowledge_id")}
                   for ref in run.get("references") or []]
        seen = set()
        for event in events:
            payload = event.payload_json or {}
            finding_id = payload.get("finding_id")
            if (payload.get("run_id") != run["run_id"] or finding_id not in (run.get("finding_ids") or [])
                    or finding_id in seen):
                continue
            seen.add(finding_id)
            if event.event_type == "check.ai_advisory_completed" and advisory_validation_current(event):
                result.append({**deepcopy(payload), "source_access": deepcopy(sources)})
    return result


def redact_unavailable_advisories(db, task, advisories):
    """只改返回副本；历史原始快照保持不可变。"""
    user = SimpleNamespace(id=task.created_by, user_type=task.created_by_type)
    mapping = db.get(ConversationMapping, task.conversation_id)
    output = []
    for advisory in advisories:
        sources = advisory.get("source_access") or []
        allowed = bool(sources) and mapping is not None
        for source in sources:
            if (not isinstance(source, dict) or type(source.get("provider_id")) is not int
                    or type(source.get("binding_id")) is not int
                    or not isinstance(source.get("kb_id"), str) or not source["kb_id"]
                    or not isinstance(source.get("knowledge_id"), str) or not source["knowledge_id"]):
                allowed = False
                break
            descriptor = KnowledgeRef(owner_type=OWNER_SR_CONVERSATION, owner_key=str(task.conversation_id),
                provider_id=source.get("provider_id"), kb_id=source.get("kb_id"), knowledge_id=source.get("knowledge_id"))
            provider = authorized_reference_provider(db, descriptor, user)
            binding = db.get(KnowledgeBaseBinding, source.get("binding_id"))
            if (provider is None or binding is None or mapping is None or binding.agent_id != mapping.agent_id
                    or binding.provider_id != provider.id or binding.external_kb_id != source.get("kb_id")
                    or not binding.is_enabled or not binding.allow_query):
                allowed = False
                break
        if allowed:
            output.append(deepcopy(advisory))
        else:
            output.append({"finding_id": advisory.get("finding_id"), "group_id": advisory.get("group_id"),
                           "limitation": "历史建议的依据当前未获授权，暂不能查看内容"})
    return output
