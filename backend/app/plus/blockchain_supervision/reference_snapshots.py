"""冻结本代五步依据，并按精确分组运行身份构造只读聚合视图。"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from sqlalchemy.orm import Session

from .card_artifacts import build_review_flow_overlay
from .cards import resolve_rule_cards
from .models import BsTask, BsTaskEvent
from .rules import load_basis_clauses, load_review_flow

REVIEW_EVENT = "knowledge.review_started"
RUN_EVENT = "knowledge.reference_completed"
REVIEW_POINTER = "reference_review_id"
GROUP_POINTERS = "reference_run_ids"
LEGACY_POINTER = "reference_run_id"
# 补件编辑会清空当前代指针，但必须保留父代只读身份供安全 carried-forward 校验；
# 该字段只作为重核查输入，不作为当前代的活动指针。
SUPPLEMENT_PARENT_REFERENCE = "supplement_parent_reference"
CHECK_GROUPS = {
    "completeness": {"scope": "a3", "kinds": ["completeness"], "label": "报审资料齐全性核查"},
    "signature_chain": {"scope": "a3", "kinds": ["signature_chain"], "label": "签字盖章核查"},
    "a1": {"scope": "a1", "kinds": ["negative_list", "transfer_scope", "illegal_subcontract"],
           "label": "分包内容合规性核查"},
    "a2": {"scope": "a3", "kinds": ["cert_validity", "person_consistency", "qualification_match"],
           "label": "资质、证照与人员核查"},
    "internal_records": {"scope": "a1", "kinds": ["internal_records"], "label": "内部信用与事故材料核查"},
}
_START_EVENT = "knowledge.reference_started"
_SOURCE_MANIFEST_PATH = Path(__file__).parent / "data" / "source_manifest.json"


def expected_groups(task: BsTask) -> list[str]:
    """任务当前启用的参考核查组；顺序以 ``CHECK_GROUPS`` 为唯一来源。"""
    return [group for group, config in CHECK_GROUPS.items()
            if getattr(task, f'scope_{config["scope"]}')]


def reference_run_expired(run: dict) -> bool:
    deadline = run.get("deadline_at")
    return bool(deadline and datetime.now(timezone.utc) >= datetime.fromisoformat(deadline))


def _clause_index(library: dict) -> dict:
    def walk(node, parent):
        yield node["clause_id"], (node, parent)
        for child in node.get("children") or []:
            yield from walk(child, parent)

    return dict(pair for parent in library.get("clauses") or [] for pair in walk(parent, parent))


def _annex_excerpt(node: dict) -> str | None:
    """附件摘录截到清单末项为止：签字栏、表单表头与打印页码不属于依据内容。"""
    text = node.get("text") or ""
    items = node.get("listed_materials") or []
    if not text or not items:
        return text or None
    last = (items[-1].get("text") or "").strip()
    at = text.find(last) if last else -1
    return text[:at + len(last)] if at >= 0 else text


def _fixed_basis(coordinate: dict, clauses: dict, documents: dict) -> tuple[dict, dict | None]:
    """附件序号只能命中精确listed_materials项，不能退回整份附件冒充该项。"""
    basis = deepcopy(coordinate)
    basis.update(available=False, text=None)
    node, parent = clauses.get(coordinate["clause_id"], ({}, {}))
    document = documents.get(node.get("doc_id") or parent.get("doc_id"))
    selected = node
    if "list_no" in coordinate:
        selected = next((row for row in node.get("listed_materials") or []
                         if row.get("no") == coordinate["list_no"]), {})
    if not selected.get("text") or document is None:
        return basis, {"code": "fixed_basis_unavailable", "clause_id": coordinate["clause_id"],
                       "list_no": coordinate.get("list_no"),
                       "message": "固定依据坐标或精确附件项尚未登记完整，请人工核对原件"}
    basis.update(
        available=True, text=selected["text"], doc_id=document["doc_id"],
        doc_title=document.get("title"), doc_no=document.get("doc_no"),
        marker=node.get("marker"), page_start=parent.get("page_start"),
        page_end=parent.get("page_end"), human_verified=bool(document.get("human_verified")),
        source_kind=document.get("source_kind"), authority_level=document.get("authority_level"),
        document=deepcopy(document),
    )
    if "list_no" in coordinate:
        # 附件清单项本身只有一句话，脱离所在附件就成了无上下文的碎片；
        # 同批把附件全文与总项数随快照冻结，供界面展示上下文（不参与判定）。
        basis.update(list_total=len(node.get("listed_materials") or []),
                     context_text=_annex_excerpt(node))
    return basis, None


def _source_manifest_hash() -> str | None:
    """固定依据来源登记的字节指纹；缺失时保持不可判定，不替造来源身份。"""
    try:
        return hashlib.sha256(_SOURCE_MANIFEST_PATH.read_bytes()).hexdigest()
    except OSError:
        return None


def _basis_snapshot_identity(task: BsTask, package: dict, package_hash: str,
                             source_manifest_hash: str | None) -> dict:
    """为本代依据生成稳定身份，复用现有事件快照而不新增 schema。"""
    profile_code = getattr(task, "profile_code", None) or "legacy_unclassified"
    jurisdiction = getattr(task, "jurisdiction", None) or "unknown"
    baseline_date = getattr(task, "baseline_date", None)
    identity = {
        "package_version": package.get("version"),
        "package_hash": package_hash,
        "source_manifest_hash": source_manifest_hash,
        "profile_code": profile_code,
        "jurisdiction": jurisdiction,
        "baseline_date": baseline_date.isoformat() if hasattr(baseline_date, "isoformat") else baseline_date,
    }
    snapshot_id = "bs-basis-" + hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:20]
    return {
        "basis_snapshot_id": snapshot_id,
        "source_manifest_hash": source_manifest_hash,
        "profile_code": profile_code,
        "jurisdiction": jurisdiction,
        "baseline_date": identity["baseline_date"],
    }


def build_review_seed(task: BsTask, *, review_run_id: str, material_fingerprint: str,
                      rule_fingerprint: str, package: dict) -> dict:
    """仅在开启本代时读规则；原文、页码、校对与在线来源随seed冻结。"""
    baseline_date = getattr(task, "baseline_date", None)
    library = (load_basis_clauses(as_of=baseline_date)
               if baseline_date is not None else load_basis_clauses())
    clauses = _clause_index(library)
    documents = {document["doc_id"]: document for document in library.get("docs") or []}
    task_expected_groups = expected_groups(task)
    card_overlay = build_review_flow_overlay(
        cards=resolve_rule_cards(as_of=baseline_date) if baseline_date is not None else resolve_rule_cards()
    )
    card_ids_by_step = {step["step_id"]: step.get("card_ids") or []
                        for step in card_overlay.get("steps") or []}
    steps = []
    for step in load_review_flow()["steps"]:
        kinds = {kernel["kind"] for kernel in step.get("kernels") or []}
        fixed_basis, material_basis, limitations, seen = [], [], [], set()
        for coordinate in step.get("basis") or []:
            identity = (coordinate["clause_id"], coordinate.get("list_no"))
            if identity in seen:
                continue
            seen.add(identity)
            basis, limitation = _fixed_basis(coordinate, clauses, documents)
            if coordinate.get("basis_kind") == "material_requirement":
                material_basis.append(basis)
            else:
                fixed_basis.append(basis)
            if limitation:
                limitations.append(limitation)
        steps.append({
            "step_id": step["id"], "title": step["title"], "fixed_basis": fixed_basis,
            "material_basis": material_basis,
            "expected_groups": [group for group in task_expected_groups
                                if kinds.intersection(CHECK_GROUPS[group]["kinds"])],
            # V2：五步卡按本步内核 kind 计数。`kinds` 直接来自 review_flow.json 的 kernels
            # （唯一定义点），只读暴露给前端，避免在前端另存一份「步骤 → kind」表后漂移
            # ——a2 组横跨第二/三/四步，仅凭 expected_groups 无法把结论分回具体步骤。
            "kinds": [kernel["kind"] for kernel in step.get("kernels") or []],
            # P3-01：卡片身份是治理来源；现有条款原文仍从 basis_clauses 唯一读取。
            "card_ids": card_ids_by_step.get(step["id"], []),
            "online_sources": deepcopy([source for source in package.get("sources") or []
                                        if source.get("step_id") == step["id"]]),
            "limitations": limitations,
        })
    source_manifest_hash = _source_manifest_hash()
    package_hash = hashlib.sha256(json.dumps(
        package, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    snapshot_identity = _basis_snapshot_identity(task, package, package_hash, source_manifest_hash)
    return {
        "schema_version": "bs-reference-review.v1", "review_run_id": review_run_id,
        "material_fingerprint": material_fingerprint, "rule_fingerprint": rule_fingerprint,
        "basis_package_version": package["version"],
        "basis_package_hash": package_hash,
        "ruleset_version": rule_fingerprint,
        **snapshot_identity,
        "expected_groups": task_expected_groups, "steps": steps,
    }


def _prefixed_run(payload: dict, group: str) -> dict:
    run = deepcopy(payload)
    reference_ids = {}
    for reference in run.get("references") or []:
        identity = reference.get("reference_id")
        if identity:
            prefixed = identity if identity.startswith(f"{group}:") else f"{group}:{identity}"
            reference_ids[identity] = prefixed
            reference["reference_id"] = prefixed
    for link in run.get("finding_links") or []:
        link["reference_ids"] = [reference_ids.get(identity, identity)
                                 for identity in link.get("reference_ids") or []]
    return run


def _belongs_to_step(item: dict, step: dict, *, shared_group: bool) -> bool:
    if item.get("step_id"):
        return item["step_id"] == step["step_id"]
    if item.get("step_ids"):
        return step["step_id"] in item["step_ids"]
    if item.get("clause_id"):
        return any(source.get("clause_id") == item["clause_id"]
                   and ("list_no" not in item or source.get("list_no") == item["list_no"])
                   for source in [*step["fixed_basis"], *step.get("online_sources", [])])
    # a2跨第二、三、四步：无步骤或条款坐标的片段只留组回执，不猜所属步骤。
    return not shared_group


def _step_view(seed: dict, runs: dict, pending: list, missing: list, group_steps: dict) -> dict:
    step = deepcopy(seed)
    groups = step["expected_groups"]
    completed = [runs[group] for group in groups
                 if group in runs and runs[group]["state"] == "completed"]
    step["pending_groups"] = [group for group in groups if group in pending]
    step["missing_groups"] = [group for group in groups if group in missing]
    if not groups:
        state = "out_of_scope"
    elif len(completed) == len(groups):
        state = "completed"
    elif completed or step["pending_groups"]:
        state = "running"
    else:
        state = "not_started"
    step.update(state=state, references=[], queries=[])
    if groups and not step.get("online_sources"):
        step["limitations"].append({"code": "local_only",
            "message": "本步骤使用本代冻结的本地固定依据，本次未配置在线参考来源"})
    for run in completed:
        shared = len(group_steps[run["group_id"]]) > 1
        for key in ("references", "queries"):
            step[key].extend(deepcopy([item for item in run.get(key) or []
                                      if _belongs_to_step(item, step, shared_group=shared)]))
    for group in groups:
        if group in runs:
            step["limitations"].extend(deepcopy(runs[group].get("limitations") or []))
    return step


def project_reference_snapshot(snapshot: dict | None) -> dict | None:
    """只为多组步骤的限制补展示归属；来源只能是同一冻结快照中的run。

    当前及历史共用此投影，保留原run、依据与引用。已有归属的消息不再匹配
    原run消息，因而重复读取不会叠加前缀；不读取当前规则或改写旧证据。
    """
    if snapshot is None:
        return None
    view = deepcopy(snapshot)
    for step in view.get("steps") or []:
        groups = step.get("expected_groups") or []
        if len(groups) < 2:
            continue
        limitations = []
        for limitation in step.get("limitations") or []:
            owners = [run["group_id"] for run in view.get("runs") or []
                      if run.get("group_id") in groups and run["group_id"] in CHECK_GROUPS
                      and limitation in (run.get("limitations") or [])]
            if not owners:
                limitations.append(limitation)
                continue
            for group in owners:
                item = dict(limitation, group_id=group)
                if item.get("message"):
                    item["message"] = "%s部分：%s" % (
                        CHECK_GROUPS[group]["label"], item["message"].removeprefix("本组"))
                if item not in limitations:
                    limitations.append(item)
        step["limitations"] = limitations
    return view


def review_readiness(snapshot: dict | None) -> dict | None:
    """五步审查完成度的**只读投影**（P1-08）：只读快照 `steps` / `runs`，不另立判定。

    - `steps`：只留展示所需四列（step_id / title / state / expected_groups），
      不复制 fixed_basis 等重字段；
    - `selected_groups`：非 `out_of_scope` 步骤的 `expected_groups` 并集（去重保序）——
      范围外的步骤不参与完成度；
    - `completed_groups` / `pending_groups`：selected 中、快照 `runs[].state ==
      "completed"` 的组 / 其余。组完成态**只认快照 runs**（与 `_step_view` 同源，
      不重读规则、不看 finding、不按事件推测）；
    - `all_completed`：selected 全员完成（与快照顶层 `state="completed"` 同口径：
      selected 为空时亦为 True，因为没有在范围内的组就没有待完成项）。

    无快照或无 steps（历史任务 / 本代 seed 缺失，见 `review_seed_unavailable`）返回
    None，由消费方当作「无可投影」，不得补造当前五步。
    """
    if not snapshot:
        return None
    steps = snapshot.get("steps") or []
    if not steps:
        return None
    out_steps, selected = [], []
    for step in steps:
        groups = list(step.get("expected_groups") or [])
        state = step.get("state")
        out_steps.append({"step_id": step.get("step_id"), "title": step.get("title"),
                          "state": state, "expected_groups": groups})
        if state == "out_of_scope":
            continue
        for group in groups:
            if group not in selected:
                selected.append(group)
    completed_names = {run.get("group_id") for run in snapshot.get("runs") or []
                       if run.get("state") == "completed"}
    completed = [group for group in selected if group in completed_names]
    pending = [group for group in selected if group not in completed_names]
    return {"steps": out_steps, "selected_groups": selected,
            "completed_groups": completed, "pending_groups": pending,
            "all_completed": not pending}


def get_reference_snapshot(db: Session, task: BsTask) -> dict | None:
    """只读本代seed与各组精确run；不按最近事件推测、不重读依据、不触网。"""
    options = task.options_snapshot_json or {}
    review_id = options.get(REVIEW_POINTER)
    if not review_id:
        return None
    snapshot = {"schema_version": "bs-reference-snapshot.v1", "review_run_id": review_id,
                "state": "running", "steps": [], "runs": [], "pending_groups": [],
                "missing_groups": [], "limitations": []}
    seeds = (db.query(BsTaskEvent).filter(
        BsTaskEvent.task_id == task.id, BsTaskEvent.event_type == REVIEW_EVENT,
        BsTaskEvent.payload_json["review_run_id"].as_string() == review_id).all())
    if len(seeds) != 1:
        snapshot["limitations"].append({"code": "review_seed_unavailable",
            "message": "本代固定依据快照缺失或身份不唯一，请重新核查"})
        return snapshot
    seed = deepcopy(seeds[0].payload_json)
    snapshot.update({key: value for key, value in seed.items()
                     if key not in {"schema_version", "steps", "state", "runs", "limitations"}})
    snapshot["limitations"] = deepcopy(seed.get("limitations") or [])
    pointers = options.get(GROUP_POINTERS) or {}
    events = (db.query(BsTaskEvent).filter(
        BsTaskEvent.task_id == task.id, BsTaskEvent.event_type.in_([RUN_EVENT, _START_EVENT]),
        BsTaskEvent.payload_json["review_run_id"].as_string() == review_id).all())
    indexed = {}
    for record in events:
        payload = record.payload_json or {}
        identity = (record.event_type, payload.get("group_id"), payload.get("run_id"))
        indexed.setdefault(identity, []).append(payload)
    runs, pending, missing = {}, [], []
    for group in seed["expected_groups"]:
        run_id = pointers.get(group)
        if not run_id:
            missing.append(group)
            continue
        terminal = indexed.get((RUN_EVENT, group, run_id), [])
        if len(terminal) == 1 and terminal[0].get("state") == "completed":
            runs[group] = _prefixed_run(terminal[0], group)
            continue
        started = indexed.get((_START_EVENT, group, run_id), [])
        run = deepcopy(started[0]) if len(started) == 1 else {}
        expired = reference_run_expired(run)
        run.update(review_run_id=review_id, group_id=group, run_id=run_id,
                   state="expired" if expired else "running", references=[], queries=[])
        run["limitations"] = deepcopy(run.get("limitations") or [])
        pending.append(group)
        if not started or terminal:
            missing.append(group)
            run["limitations"].append({"code": "reference_run_unavailable",
                "message": "本组运行记录缺失或终态身份不唯一，请重新核查"})
        else:
            run["limitations"].append({"code": "reference_timeout" if expired else "reference_pending",
                "message": "上次参考查询已中断，请重新核查" if expired else "本组参考依据仍在处理中"})
        runs[group] = run
    group_steps = {group: [step["step_id"] for step in seed["steps"]
                           if group in step["expected_groups"]] for group in seed["expected_groups"]}
    snapshot.update(
        state="completed" if not pending and not missing else "running",
        runs=list(runs.values()), pending_groups=pending, missing_groups=missing,
        steps=[_step_view(step, runs, pending, missing, group_steps) for step in seed["steps"]],
    )
    return project_reference_snapshot(snapshot)


__all__ = ["REVIEW_EVENT", "RUN_EVENT", "REVIEW_POINTER", "GROUP_POINTERS", "LEGACY_POINTER",
           "SUPPLEMENT_PARENT_REFERENCE",
           "expected_groups",
           "CHECK_GROUPS", "build_review_seed", "get_reference_snapshot", "reference_run_expired",
           "project_reference_snapshot", "review_readiness"]
