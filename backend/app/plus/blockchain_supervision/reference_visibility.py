"""在线依据读取时重验授权，仅修改返回副本，不改冻结记录。"""
from copy import deepcopy

from ...models.models import ConversationMapping, KnowledgeBaseBinding, KnowledgeRef
from ...services.knowledge.context import OWNER_SR_CONVERSATION
from ...services.knowledge.binding_service import authorized_file_scope
from ...services.knowledge.reference_access import authorized_reference_provider


def _reference_authorized(db, task, ref, user):
    if (not isinstance(ref, dict) or type(ref.get("provider_id")) is not int
            or type(ref.get("binding_id")) is not int
            or not isinstance(ref.get("kb_id"), str) or not ref["kb_id"]
            or not isinstance(ref.get("knowledge_id"), str) or not ref["knowledge_id"]):
        return False
    mapping = db.get(ConversationMapping, task.conversation_id)
    binding = db.get(KnowledgeBaseBinding, ref["binding_id"])
    descriptor = KnowledgeRef(owner_type=OWNER_SR_CONVERSATION, owner_key=str(task.conversation_id),
                              provider_id=ref.get("provider_id"), kb_id=ref.get("kb_id"),
                              knowledge_id=ref.get("knowledge_id"))
    provider = authorized_reference_provider(db, descriptor, user)
    selected = authorized_file_scope(binding.selected_knowledge_ids) if binding else set()
    return bool(provider and binding and mapping and binding.agent_id == mapping.agent_id
                and binding.provider_id == provider.id and binding.external_kb_id == ref.get("kb_id")
                and binding.is_enabled and binding.allow_query
                and (selected is None or ref.get("knowledge_id") in selected))


def _all_authorized(db, task, references, user):
    cache = {}
    for ref in references:
        if not isinstance(ref, dict):
            return False
        key = tuple(ref.get(name) for name in ("provider_id", "binding_id", "kb_id", "knowledge_id"))
        if not all(value is None or isinstance(value, (int, str)) for value in key):
            return False
        if key not in cache:
            cache[key] = _reference_authorized(db, task, ref, user)
        if not cache[key]:
            return False
    return True


def visible_run(db, task, run, user):
    if not run:
        return run
    view = deepcopy(run)
    view.update(basis_sources=[], basis_source=None)
    for query in view.get("queries") or []:
        if isinstance(query, dict):
            query.pop("query_text", None)
    if not run.get("references"):
        return view
    if _all_authorized(db, task, run["references"], user):
        return view
    view.update(references=[], queries=[], finding_links=[], basis_sources=[], basis_source=None,
                status="undetermined")
    view.setdefault("limitations", []).append({"code": "reference_access_changed",
        "message": "参考依据授权已变化，暂不能查看原文，请联系管理员核对"})
    return view


def run_restricted(db, task, run, user):
    return bool(isinstance(run, dict) and not _all_authorized(db, task, run.get("references") or [], user))


def snapshot_restricted(db, task, snapshot, user):
    if not isinstance(snapshot, dict):
        return False
    containers = [*(snapshot.get("runs") or []), *(snapshot.get("steps") or [])]
    return not _all_authorized(db, task, (ref for container in containers if isinstance(container, dict)
                                          for ref in container.get("references") or []), user)


def visible_snapshot(db, task, snapshot, user):
    if not snapshot:
        return snapshot
    restricted_groups = {run.get("group_id") for run in snapshot.get("runs") or []
                         if isinstance(run, dict) and run_restricted(db, task, run, user)}
    view = deepcopy(snapshot)
    if "runs" in view:
        view["runs"] = [visible_run(db, task, run, user) for run in view.get("runs") or []]
    restricted = bool(restricted_groups)
    reference_cache = {}
    def permitted(ref):
        if not isinstance(ref, dict):
            return False
        key = tuple(ref.get(name) for name in ("provider_id", "binding_id", "kb_id", "knowledge_id"))
        if not all(value is None or isinstance(value, (int, str)) for value in key):
            return False
        if key not in reference_cache:
            reference_cache[key] = _reference_authorized(db, task, ref, user)
        return reference_cache[key]
    notice = {"code": "reference_access_changed", "message": "参考依据授权已变化，暂不能查看原文，请联系管理员核对"}
    for step in view.get("steps") or []:
        for query in step.get("queries") or []:
            if isinstance(query, dict):
                query.pop("query_text", None)
        if "online_sources" in step:
            safe_fields = {"step_id", "item_id", "kind", "doc_id", "clause_id", "list_no", "subject_types", "subject_ids"}
            step["online_sources"] = [{key: value for key, value in source.items() if key in safe_fields}
                                      for source in step["online_sources"] or [] if isinstance(source, dict)]
        refs = step.get("references") or []
        allowed = [ref for ref in refs if permitted(ref)]
        groups = step.get("expected_groups") or []
        impacted = (len(allowed) != len(refs) or None in restricted_groups
                    or (bool(restricted_groups) and not groups)
                    or bool(set(groups) & restricted_groups))
        if impacted:
            restricted = True
            step.update(references=allowed, queries=[], online_sources=[], fixed_basis=[], material_basis=[])
            step.setdefault("limitations", []).append(notice)
    if restricted:
        view.setdefault("limitations", []).append(notice)
    return view


def visible_version(db, task, version, user):
    view = deepcopy(version)
    snapshot = view.get("snapshot") if view else None
    if not isinstance(snapshot, dict):
        return view
    candidate_rows = [row for row in snapshot.get("findings") or []
                      if isinstance(row, dict) and row.get("kind") == "negative_candidate"]
    report = snapshot.get("report")
    candidate_coverage = (report.get("negative_list_coverage") if isinstance(report, dict) else None)
    if candidate_rows or (isinstance(candidate_coverage, dict) and candidate_coverage.get("state") == "available"):
        reference_snapshot = snapshot.get("reference_snapshot") or {}
        if not isinstance(reference_snapshot, dict):
            reference_snapshot = {}
        runs = [run for run in reference_snapshot.get("runs") or []
                if isinstance(run, dict) and run.get("group_id") == "a1"]
        legacy_run = snapshot.get("reference_run")
        if not runs and isinstance(legacy_run, dict) and legacy_run.get("group_id") == "a1":
            runs = [legacy_run]
        allowed = (len(runs) == 1 and bool(runs[0].get("references"))
                   and not run_restricted(db, task, runs[0], user)
                   and not snapshot_restricted(db, task, reference_snapshot, user))
        if not allowed:
            snapshot["findings"] = [row for row in snapshot.get("findings") or []
                                    if not isinstance(row, dict) or row.get("kind") != "negative_candidate"]
            # 冻结的报告/意见可能含模型建议及其统计，不能只移除引用后保留正文。
            snapshot["report"] = None
            snapshot["opinion"] = None
            snapshot.setdefault("limitations", []).append({"code": "candidate_access_changed",
                "message": "历史核查线索的依据不可用，暂不能查看相应内容，请联系管理员核对"})
    if "reference_run" in snapshot:
        snapshot["reference_run"] = visible_run(db, task, snapshot["reference_run"], user)
    if "reference_snapshot" in snapshot:
        snapshot["reference_snapshot"] = visible_snapshot(db, task, snapshot["reference_snapshot"], user)
    for kind in ("report", "opinion"):
        if isinstance(snapshot.get(kind), dict) and "reference_snapshot" in snapshot[kind]:
            snapshot[kind]["reference_snapshot"] = visible_snapshot(db, task, snapshot[kind]["reference_snapshot"], user)
    return view
