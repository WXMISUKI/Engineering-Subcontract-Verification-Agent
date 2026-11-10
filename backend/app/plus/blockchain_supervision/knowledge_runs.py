"""五步分组参考运行：只记录依据与限制，不参与固定规则判定。

当前指针属于任务，终态属于追加事件，历史版本复制终态。网络工作线程不写DB。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
from pathlib import Path
from ...standalone.reference_mapping import MappingError, apply_reference_mapping
from types import SimpleNamespace
import time
import uuid

from sqlalchemy.orm import Session

from ...models.models import KnowledgeBaseBinding, KnowledgeRef
from ...services.knowledge.context import OWNER_SR_CONVERSATION
from ...services.knowledge.factory import ProviderConnection, acquire_provider_backend
from ...services.knowledge.fulltext import document_from_report, reconstruct_text_chunks
from ...services.knowledge.reference_access import (
    ReferenceOriginalAccessError, authorized_reference_provider, read_authorized_reference_original,
)
from .cross_page_reference import match_cross_page_reference, match_ordered_page_fragments
from .negative_reference import match_negative_fragments
from .text_overlap_reference import match_text_parts
from .knowledge import query_subcontract_knowledge, resolve_agent_id, resolve_task_route
from .models import BsFinding, BsMaterial, BsSubject, BsTask, BsTaskEvent
from .rules import load_basis_clauses, load_basis_index, load_negative_list, load_report_checklist
from .reference_snapshots import (CHECK_GROUPS, GROUP_POINTERS, LEGACY_POINTER,
                                  REVIEW_POINTER, RUN_EVENT, reference_run_expired)

_DATA_DIR = Path(__file__).with_name("data")
REFERENCE_RUN_LEASE_SECONDS = 60
REFERENCE_ORIGINAL_MAX_BYTES = 20 * 1024 * 1024


class ReferenceRunSuperseded(Exception):
    """任务或核查输入已变化，当前请求不能写回。"""


class ReferenceBasisUnavailable(ValueError):
    """已登记参考的文件、条款或附件项无法解析。"""


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     default=str).encode("utf-8")).hexdigest()


def rule_fingerprint() -> str:
    """覆盖实际规则/条款/来源JSON及确定性内核代码，不保存规则副本。"""
    files = list(_DATA_DIR.glob("*.json"))
    files += [Path(__file__).with_name(name) for name in (
        "a1_check.py", "a2_check.py", "a3_check.py", "signature_chain.py", "internal_records.py",
        "checklist.py", "extract.py", "rules.py", "severity.py", "pinning.py",
        "personnel_evidence.py", "certificate_number_check.py", "identity_observation.py")]
    files += [Path(__file__).parent.parent / "startup_conditions_check" / "validity.py"]
    return _digest({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(files)})


def material_fingerprint(db: Session, task: BsTask, *, options_snapshot: dict | None = None,
                         excluded_material_ids: set[int] | None = None) -> str:
    """输入含解析正文、材料归属、主体、基准日和启动选项；不写出私密正文。"""
    def rows(model):
        return [{c.name: getattr(row, c.name) for c in model.__table__.columns
                 if c.name not in {"created_at", "updated_at"}}
                for row in db.query(model).filter(model.task_id == task.id)
                .order_by(model.id).populate_existing().all()
                if model is not BsMaterial or row.id not in (excluded_material_ids or set())]
    source_options = task.options_snapshot_json if options_snapshot is None else options_snapshot
    options = {k: v for k, v in (source_options or {}).items()
               if k not in {LEGACY_POINTER, GROUP_POINTERS, REVIEW_POINTER,
                            # BS-P1-13（设计 98 §4.2）：`rerun` 是重跑回执携带表——它随
                            # 重跑写入、在收口/补件时清除，属过程簿记而非业务输入。
                            # 若不排除，清除那一刻指纹就变，收口后读报告会被误判
                            # 「核查输入已变化」而 409（历史真实坑，见 rerun_task）。
                            "rerun",
                            # 117-B：清单（含 generated_at）描述规则归属，不是材料输入。
                            # 规则变化由 rule_fingerprint 与任务冻结 manifest 单独承载。
                            "ruleset_manifest",
                            # P8-03C：补件计划/编辑态是调度过程簿记，不改变材料正文、主体
                            # 或规则输入；若纳入指纹会让 carried-forward 永远被误判为失效。
                            "supplement_plan", "supplement_edit"}}
    return _digest({"materials": rows(BsMaterial), "subjects": rows(BsSubject),
                    "task": {"conversation_id": task.conversation_id,
                             "scope_a1": task.scope_a1, "scope_a3": task.scope_a3,
                             "scale": task.scale, "baseline_date": task.baseline_date,
                             "project_name": task.project_name,
                             "contract_section": task.contract_section,
                             "jurisdiction": task.jurisdiction,
                             "profile_code": task.profile_code,
                             "options": options}})


def load_reference_package(as_of=None) -> dict:
    package = json.loads((_DATA_DIR / "reference_package.json").read_text(encoding="utf-8"))
    mapping_path = os.environ.get('BS_REFERENCE_MAPPING_FILE', '').strip()
    if not mapping_path:
        raise MappingError('知识库尚未完成初始化，参考依据无法核实')
    package = apply_reference_mapping(package, Path(mapping_path))
    timing = next((s for s in package.get("sources", []) if s.get("item_id") == "A3-02-timing"
                   and s.get("clause_excerpt")), None)
    if timing:
        package["sources"].append({**timing, "item_id": "A3-02-legal"})
    templates = {s["subject_types"][0]: s for s in package.get("sources", [])
                 if s.get("item_id") == "RC-A01" and len(s.get("subject_types") or []) == 1}
    for item in load_report_checklist():
        match = re.fullmatch(r"RC-([APL])(\d{2})", item.get("id", ""))
        if not match or item["id"] == "RC-A01":
            continue
        category, number = match.group(1), int(match.group(2))
        for subject in item.get("packet_types") or []:
            if subject not in templates or (category == "P" and subject != "professional") or (
                    category == "L" and subject != "labor"):
                continue
            source = {**templates[subject], "item_id": item["id"], "section_item_anchor": True,
                      "clause_id": "ZHB-2025-0620#" + ("一、" if subject == "professional" else "二、") + str(number),
                      "physical_page": None}
            source.pop("expected_context", None)
            package["sources"].append(source)
    template = next((source for source in package.get("sources", [])
                     if source.get("item_id") == "NL-B-01" and source.get("required_negative_preamble") is True), None)
    if template:
        for rule in load_negative_list().get("rules", []):
            if rule.get("kind") != "prohibited" or rule.get("id") == template["item_id"]:
                continue
            source = {**template, "item_id": rule["id"], "clause_id": "MOT-NL-2024#" + rule["article"],
                      "negative_parts": True}
            source.pop("expected_context", None)
            package["sources"].append(source)
    local_template = next((source for source in package.get("sources", [])
                           if source.get("item_id") == "W16-01" and source.get("local_illegal_rule") is True), None)
    if local_template:
        index = load_basis_index().get("items", {})
        item_ids = [f"W16-0{n}" for n in range(2, 8)] + [f"V16-0{n}" for n in range(1, 5)] + [f"L17-0{n}" for n in range(1, 4)]
        for item_id in item_ids:
            mapping = index.get(item_id) or {}
            if mapping.get("doc_id") != "SHJT-2026-8" or not mapping.get("clause_id"):
                continue
            source = {**local_template, "item_id": item_id, "clause_id": mapping["clause_id"]}
            if item_id.startswith("V16-"):
                source["query_text"] = "有下列情形之一的视为施工分包违法 分包合同 未经监理人审查"
            elif item_id.startswith("L17-"):
                source["query_text"] = "第十七条 以劳务合作名义进行施工分包 劳务作业费用 材料款"
            source.pop("expected_context", None)
            source.pop("separate_prefix", None)
            package["sources"].append(source)
        for item_id in ("T15-03", "T15-06", "T15-08", "T15-10"):
            mapping = index.get(item_id) or {}
            if mapping.get("doc_id") != "SHJT-2026-8" or not mapping.get("clause_id"):
                continue
            source = {**local_template, "item_id": item_id, "kind": "transfer_scope", "clause_id": mapping["clause_id"],
                      "local_transfer_rule": True, "query_text": "第十五条 禁止将承包的公路工程进行转包 有下列情形之一",
                      "local_preamble_variant": "第十五条禁止将承包的公路工程进行转包.有下列情形之一的，属于转包:"}
            for key in ("local_illegal_rule", "expected_context", "separate_prefix"):
                source.pop(key, None)
            package["sources"].append(source)
    if as_of is None:
        return package
    library = (load_basis_clauses(as_of=as_of)
               if as_of is not None else load_basis_clauses())
    active_doc_ids = {doc.get("doc_id") for doc in library.get("docs") or []}
    return {**package, "sources": [source for source in package.get("sources") or []
                                    if source.get("doc_id") in active_doc_ids],
            "as_of": as_of.isoformat() if hasattr(as_of, "isoformat") else str(as_of)}


def group_pointer(task: BsTask, group_id: str = "a1") -> str | None:
    options = task.options_snapshot_json or {}
    if GROUP_POINTERS in options:
        return (options.get(GROUP_POINTERS) or {}).get(group_id)
    return options.get(LEGACY_POINTER) if group_id == "a1" else None


def dependency_fingerprint(db: Session, group_id: str, task_id: int) -> str | None:
    if group_id != "a1":
        return None
    rows = db.query(BsFinding).filter_by(task_id=task_id, kind="completeness").order_by(BsFinding.id).all()
    owner = db.get(BsTask, task_id)
    return _digest({"upstream_run_id": group_pointer(owner, "completeness") if owner else None,
                    "findings": [{c.name: getattr(row, c.name) for c in BsFinding.__table__.columns
                     if c.name not in {"created_at", "review_json", "discrepancy_json"}} for row in rows]})


def finding_fingerprint(findings: list[BsFinding]) -> str:
    """结论内容指纹；人工处置可变，不影响一条参考终态是否仍对应原结论。"""
    fields = [column.name for column in BsFinding.__table__.columns
              if column.name not in {"created_at", "review_json", "discrepancy_json"}]
    rows = [{field: getattr(finding, field) for field in fields} for finding in findings]
    rows.sort(key=lambda row: (
        row.get("subject_id") or -1, row.get("item_id") or "", row.get("material_id") or -1,
        row.get("page") or -1, row.get("id") or -1))
    return _digest(rows)


def prepare_reference_run(db: Session, task: BsTask, *, group_id: str = "a1") -> dict:
    package = load_reference_package(as_of=task.baseline_date)
    sources = [s for s in package["sources"] if s["kind"] in CHECK_GROUPS[group_id]["kinds"]]
    unresolved = []
    if any("subject_types" in s for s in sources):
        subjects = db.query(BsSubject).filter_by(task_id=task.id, retired=False).order_by(BsSubject.id).all()
        unresolved = [s.id for s in subjects if not s.type_confirmed or s.subject_type not in ("professional", "labor")]
        scoped = []
        for source in sources:
            if "subject_types" not in source:
                scoped.append(source)
                continue
            ids = [s.id for s in subjects if s.type_confirmed and s.subject_type in source["subject_types"]]
            if ids:
                scoped.append({**source, "subject_ids": ids})
        sources = scoped
    previous = get_reference_run(db, task, group_id=group_id)
    now = datetime.now(timezone.utc)
    return {
        "schema_version": "bs-reference-run.v2", "run_id": uuid.uuid4().hex, "task_id": task.id,
        "group_id": group_id, "review_run_id": (task.options_snapshot_json or {}).get(REVIEW_POINTER),
        "step_id": sources[0]["step_id"] if sources else None,
        "purpose": "reference_only", "state": "running",
        "changes_deterministic_verdicts": False,
        "started_at": now.isoformat(),
        "deadline_at": (now + timedelta(seconds=REFERENCE_RUN_LEASE_SECONDS)).isoformat(),
        "material_fingerprint": material_fingerprint(db, task),
        "rule_fingerprint": rule_fingerprint(),
        "dependency_fingerprint": dependency_fingerprint(db, group_id, task.id),
        "basis_package_version": package["version"], "basis_package_hash": _digest(package),
        "basis_sources": sources, "basis_source": sources[0] if sources else None,
        "subject_scope_unresolved": unresolved,
        "expected_previous_id": group_pointer(task, group_id),
        "previous_run_id": previous["run_id"] if previous and previous["state"] == "completed" else None,
        "task_state": task.state,
    }


def clear_reference_run(task: BsTask) -> None:
    options = dict(task.options_snapshot_json or {})
    for key in (LEGACY_POINTER, GROUP_POINTERS, REVIEW_POINTER):
        options.pop(key, None)
    task.options_snapshot_json = options


def activate_reference_run(task: BsTask, run: dict) -> None:
    options = dict(task.options_snapshot_json or {})
    options.pop(LEGACY_POINTER, None)
    pointers = dict(options.get(GROUP_POINTERS) or {})
    pointers[run.get("group_id", "a1")] = run["run_id"]
    options[GROUP_POINTERS] = pointers
    task.options_snapshot_json = options


def restore_reference_run(task: BsTask, run: dict) -> None:
    if (group_pointer(task, run["group_id"]) != run["run_id"]
            or (task.options_snapshot_json or {}).get(REVIEW_POINTER) != run.get("review_run_id")):
        return
    options = dict(task.options_snapshot_json or {})
    pointers = dict(options.get(GROUP_POINTERS) or {})
    pointers.pop(run["group_id"], None)
    if run.get("previous_run_id"):
        pointers[run["group_id"]] = run["previous_run_id"]
    options[GROUP_POINTERS] = pointers
    options.pop(LEGACY_POINTER, None)
    task.options_snapshot_json = options


def assert_reference_run_current(db: Session, task: BsTask, run: dict, *, active=True) -> None:
    """调用方先结束网络阶段只读事务，再锁读任务，避免复用ORM/数据库旧快照。"""
    # HTTP层已有启动闸；旧内核直登调用允许保持setup，但不允许网络等待时跨生命周期。
    same_state = task.state == run["task_state"] or {task.state, run["task_state"]} <= {"running", "review"}
    expected = run["run_id"] if active else run["expected_previous_id"]
    if (task.state in {"done", "preparing"} or not same_state
            or (task.options_snapshot_json or {}).get(REVIEW_POINTER) != run.get("review_run_id")
            or group_pointer(task, run["group_id"]) != expected or (active and reference_run_expired(run))
            or material_fingerprint(db, task) != run["material_fingerprint"]
            or dependency_fingerprint(db, run["group_id"], task.id) != run["dependency_fingerprint"]
            or rule_fingerprint() != run["rule_fingerprint"]):
        raise ReferenceRunSuperseded("核查输入或运行批次已变化，请刷新后重新核查")


def _clause(source: dict, *, as_of=None) -> tuple[dict, dict]:
    library = load_basis_clauses(as_of=as_of)
    doc = next((d for d in library["docs"] if d["doc_id"] == source["doc_id"]), None)
    if doc is None:
        raise ReferenceBasisUnavailable("参考文件未登记或不适用于基准日")
    for parent in library["clauses"]:
        if parent.get("doc_id") != source["doc_id"]:
            continue
        for node in [parent, *(parent.get("children") or [])]:
            if node["clause_id"] == source["clause_id"]:
                if "clause_excerpt" in source:
                    excerpt = source["clause_excerpt"]
                    compact = "".join((node.get("text") or "").split())
                    needle = "".join(excerpt.split()) if isinstance(excerpt, str) else ""
                    start = compact.find("承包人应在分包工程实施前")
                    if (source.get("item_id") not in {"A3-02-timing", "A3-02-legal"} or source.get("kind") != "signature_chain"
                            or source.get("doc_id") != "MOT-2024-2"
                            or source.get("clause_id") != "MOT-2024-2#第十二条"
                            or start < 0 or not needle or needle != compact[start:]
                            or compact.count(needle) != 1):
                        raise ReferenceBasisUnavailable("报审时序参考节选未能核实")
                    node = {**node, "text": excerpt}
                if source.get("project_registration_rule") is True:
                    prefix = (parent.get("text") or "").splitlines()[0].strip() if parent.get("text") else ""
                    if (source.get("item_id") != "A2-03" or source.get("kind") != "cert_validity"
                            or source.get("doc_id") != "ZHB-2025-0620" or source.get("clause_id") != "ZHB-2025-0620#三、2"
                            or prefix != "三、相关要求"):
                        raise ReferenceBasisUnavailable("注册证书要求与核查项或项目依据未对齐")
                    node = {**node, "local_preamble": prefix}
                if source.get("local_qualification_rule") is True:
                    prefix = re.split(r"[（(]一[）)]", parent.get("text") or "", maxsplit=1)[0].strip()
                    if (source.get("item_id") != "A2-04" or source.get("kind") != "qualification_match"
                            or source.get("doc_id") != "SHJT-2026-8"
                            or source.get("clause_id") != "SHJT-2026-8#第十条（四）" or not prefix):
                        raise ReferenceBasisUnavailable("资质要求与核查项或适用条件未对齐")
                    node = {**node, "local_preamble": prefix}
                if source.get("local_transfer_rule") is True:
                    item = source.get("item_id")
                    numbers = {"T15-03": "三", "T15-06": "六", "T15-08": "八", "T15-10": "十"}
                    mapping = load_basis_index().get("items", {}).get(item) or {}
                    prefix = re.split(r"[（(]一[）)]", parent.get("text") or "", maxsplit=1)[0].strip()
                    scope = "分包人有前款第一项、第二项、第三项、第五项、第八项规定行为的，也属于转包。"
                    if (item not in numbers or source.get("kind") != "transfer_scope" or source.get("doc_id") != "SHJT-2026-8"
                            or mapping.get("doc_id") != source["doc_id"] or mapping.get("clause_id") != source["clause_id"]
                            or source["clause_id"] != f"SHJT-2026-8#第十五条（{numbers[item]}）" or not prefix
                            or (item in {"T15-03", "T15-08"} and not _excerpt(parent.get("text") or "", scope))):
                        raise ReferenceBasisUnavailable("转包条款与固定规则或适用范围未对齐")
                    variant = source.get("local_preamble_variant")
                    expected_variant = "".join(prefix.split()).translate(str.maketrans({"。": ".", "：": ":"}))
                    if not isinstance(variant, str) or "".join(variant.split()) != expected_variant:
                        raise ReferenceBasisUnavailable("转包总前提的文本差异超出已登记标点范围")
                    node = {**node, "local_preamble": prefix,
                            "local_preamble_variant": variant,
                            **({"local_scope_supplement": scope} if item in {"T15-03", "T15-08"} else {})}
                if source.get("local_illegal_rule") is True:
                    mapping = load_basis_index().get("items", {}).get(source.get("item_id")) or {}
                    match = re.fullmatch(r"(W16|V16|L17)-0([1-7])", source.get("item_id") or "")
                    family, number = (match.group(1), int(match.group(2))) if match else (None, 0)
                    if family == "V16":
                        prefix = (node.get("group") or "") + "："
                    else:
                        prefix = re.split(r"[（(]一[）)]", parent.get("text") or "", maxsplit=1)[0]
                    article = "第十七条" if family == "L17" else "第十六条"
                    suffix = "#r2" if family == "V16" else ""
                    expected_id = f"SHJT-2026-8#{article}（{'一二三四五六七'[number - 1]}）{suffix}" if match else None
                    if (not match or source.get("kind") != "illegal_subcontract"
                            or (family == "V16" and number > 4) or (family == "L17" and number > 3)
                            or source.get("doc_id") != "SHJT-2026-8" or mapping.get("doc_id") != source["doc_id"]
                            or mapping.get("clause_id") != source["clause_id"] or source["clause_id"] != expected_id or not prefix
                            or not _excerpt(parent.get("text") or "", prefix)
                            or (source.get("separate_prefix") is not None and
                                "".join(prefix.split()) != "".join(source["separate_prefix"].split()))):
                        raise ReferenceBasisUnavailable("违法分包条款与固定规则或总前提未对齐")
                    node = {**node, "local_preamble": prefix.strip()}
                if source.get("section_item_anchor") is True:
                    match = re.fullmatch(r"RC-([APL])(\d{2})", source.get("item_id", ""))
                    item = next((r for r in load_report_checklist() if r.get("id") == source.get("item_id")), None)
                    subject_types = source.get("subject_types") or []
                    subject = subject_types[0] if len(subject_types) == 1 else None
                    expected_section = {"professional": "一、", "labor": "二、"}.get(subject)
                    if (not match or not item or subject not in item.get("packet_types", []) or not expected_section
                            or source.get("doc_id") != "ZHB-2025-0620" or source.get("kind") != "completeness"
                            or source["clause_id"] != "ZHB-2025-0620#" + expected_section + str(int(match.group(2)))):
                        raise ReferenceBasisUnavailable("清单条目与主体类型不匹配")
                    lines = (parent.get("text") or "").splitlines()
                    prefix = str(int(match.group(2))) + "．"
                    candidates = [line.strip() for line in lines if line.strip().startswith(prefix)]
                    if len(candidates) != 1 or not _excerpt(candidates[0], node.get("text") or ""):
                        raise ReferenceBasisUnavailable("清单条目或章节标题未核实")
                    node = {**node, "section_anchor": lines[0].strip(), "item_line": candidates[0]}
                if source.get("national_transfer_parts") is True:
                    local_id = source.get("local_clause_id")
                    mapping = load_basis_index().get("items", {}).get(source.get("item_id")) or {}
                    local = next((child for section in library["clauses"]
                                  if section.get("doc_id") == "SHJT-2026-8"
                                  for child in section.get("children") or [] if child.get("clause_id") == local_id), None)
                    def compact(value):
                        return "".join((value or "").split()).translate(str.maketrans("（）", "()"))
                    if (source.get("kind") != "transfer_scope" or source.get("doc_id") != "MOT-2024-2"
                            or mapping.get("clause_id") != local_id or not local or not compact(node.get("text"))
                            or compact(node.get("text")) != compact(local.get("text"))):
                        raise ReferenceBasisUnavailable("国家参考与本地固定规则的条文未对齐")
                    variant = source.get("retrieval_text_variant")
                    if variant is not None and (not isinstance(variant, str) or not variant
                            or compact(variant) == compact(node.get("text"))
                            or compact(variant).replace("，", "") != compact(node.get("text")).replace("，", "")):
                        raise ReferenceBasisUnavailable("参考文本差异超出已登记的标点范围")
                if source.get("required_negative_preamble") is True:
                    library = load_negative_list()
                    rule = next((r for r in library.get("rules", []) if r.get("id") == source.get("item_id")), None)
                    if (source.get("kind") != "negative_list" or source.get("doc_id") != "MOT-NL-2024"
                            or not rule or source["clause_id"] != "MOT-NL-2024#" + rule.get("article", "")
                            or not _excerpt(node.get("text") or "", rule.get("text") or "")
                            or not library.get("meta", {}).get("preamble")):
                        raise ReferenceBasisUnavailable("负面清单条目或总前提未登记")
                context = source.get("expected_context")
                if context is not None and (not isinstance(context, str) or
                        not _excerpt(parent.get("text") or "", context) or
                        not _excerpt(context, node.get("text") or "")):
                    raise ReferenceBasisUnavailable("参考条款上下文未登记或不匹配")
                prefix = source.get("separate_prefix")
                if prefix is not None:
                    compact = "".join((context or "").split())
                    head = "".join(prefix.split()) if isinstance(prefix, str) else ""
                    leaf = "".join((node.get("text") or "").split())
                    if (not head or not leaf or compact.count(head) != 1 or compact.count(leaf) != 1
                            or compact.find(head) + len(head) > compact.find(leaf)):
                        raise ReferenceBasisUnavailable("参考条款前提与条文关系未核实")
                if "list_no" in source:
                    number = source["list_no"]
                    if type(number) is not int or number < 1:
                        raise ReferenceBasisUnavailable("参考附件序号无效")
                    selected = next((row for row in node.get("listed_materials") or []
                                     if row.get("no") == number and row.get("text")), None)
                    if selected is None:
                        raise ReferenceBasisUnavailable("参考附件项未登记")
                    selected_node = {**node, "text": selected["text"], "list_no": number}
                    if source.get("attachment_title_anchor") is True:
                        allowed = {"SHJT-2026-8#附件3": "professional", "SHJT-2026-8#附件4": "labor"}
                        subject_type = allowed.get(source.get("clause_id"))
                        lines = (node.get("text") or "").splitlines()
                        if (source.get("doc_id") != "SHJT-2026-8" or not subject_type
                                or source.get("subject_types") != [subject_type] or len(lines) < 2
                                or not lines[1].strip() or not _excerpt(node["text"], selected["text"])):
                            raise ReferenceBasisUnavailable("参考附件标题或适用主体未核实")
                        selected_node["attachment_anchor"] = lines[1].strip()
                    fragments = source.get("excerpt_fragments")
                    if fragments is not None:
                        if (source.get("attachment_title_anchor") is not True or not isinstance(fragments, list)
                                or not 1 <= len(fragments) <= 5):
                            raise ReferenceBasisUnavailable("参考材料节选配置无效")
                        text = "".join(selected["text"].split())
                        cursor = 0
                        for fragment in fragments:
                            needle = "".join(fragment.split()) if isinstance(fragment, str) else ""
                            position = text.find(needle, cursor) if needle else -1
                            if position < 0 or text.count(needle) != 1:
                                raise ReferenceBasisUnavailable("参考材料节选未能按顺序核实")
                            cursor = position + len(needle)
                        selected_node["excerpt_fragments"] = fragments
                    return doc, selected_node
                return doc, node
    raise ReferenceBasisUnavailable("参考条款未登记")


def _excerpt(text: str, expected: str) -> str | None:
    positions = [i for i, char in enumerate(text) if not char.isspace()]
    compact = "".join(text[i] for i in positions)
    needle = "".join(expected.split())
    start = compact.find(needle)
    if start < 0 or not needle:
        return None
    return text[positions[start]:positions[start + len(needle) - 1] + 1]


def _verify_authorized_original(db: Session, task: BsTask, row: dict, source: dict, deadline: float) -> bool:
    """只读核验同库同文件原件；读取前后重新授权，不持久化原件。"""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return False
    reference = KnowledgeRef(owner_type=OWNER_SR_CONVERSATION, owner_key=str(task.conversation_id),
        provider_id=row.get("provider_id"), kb_id=source["kb_id"], knowledge_id=source["knowledge_id"])
    user = SimpleNamespace(id=task.created_by, user_type=task.created_by_type)
    try:
        provider = authorized_reference_provider(db, reference, user)
        if provider is None:
            return False
        connection = (provider.id, provider.base_url, provider.secret_ref, provider.tenant_ref)
        data = read_authorized_reference_original(db, reference, user,
            max_bytes=REFERENCE_ORIGINAL_MAX_BYTES, timeout_seconds=min(2.0, remaining / 2))
        if time.monotonic() > deadline or hashlib.sha256(data).hexdigest() != source["original_sha256"].lower():
            return False
        with Session(bind=db.get_bind()) as fresh:
            current_task = fresh.get(BsTask, task.id)
            if (current_task is None or current_task.conversation_id != task.conversation_id
                    or current_task.created_by != task.created_by
                    or current_task.created_by_type != task.created_by_type):
                return False
            current = authorized_reference_provider(fresh, reference, user)
            return time.monotonic() <= deadline and current is not None and connection == (
                current.id, current.base_url, current.secret_ref, current.tenant_ref)
    except Exception:
        return False


def _cross_page_connection(db: Session, task: BsTask, scope: dict, source: dict):
    """Read current owner, route, binding and provider in a fresh transaction."""
    with Session(bind=db.get_bind()) as fresh:
        current = fresh.get(BsTask, task.id)
        if (current is None or current.conversation_id != task.conversation_id
                or current.created_by != task.created_by or current.created_by_type != task.created_by_type
                or resolve_task_route(current) != resolve_task_route(task)):
            return None
        binding = fresh.get(KnowledgeBaseBinding, scope.get("binding_id"))
        if (binding is None or not binding.is_enabled or not binding.allow_query
                or binding.provider_id != scope.get("provider_id") or binding.external_kb_id != source["kb_id"]):
            return None
        reference = KnowledgeRef(owner_type=OWNER_SR_CONVERSATION, owner_key=str(task.conversation_id),
            provider_id=scope["provider_id"], kb_id=source["kb_id"], knowledge_id=source["knowledge_id"])
        provider = authorized_reference_provider(fresh, reference,
            SimpleNamespace(id=task.created_by, user_type=task.created_by_type))
        if provider is None or binding.agent_id != resolve_agent_id(fresh, task):
            return None
        return ProviderConnection(provider.id, provider.provider_type, provider.base_url,
                                  provider.secret_ref, provider.tenant_ref, str(provider.updated_at or ""))


def _fetch_verified_cross_page(db: Session, task: BsTask, scope: dict, source: dict,
                               clause: dict, deadline: float):
    spec = source.get("cross_page") or {}
    pages = spec.get("pages")
    if (not isinstance(pages, list) or len(pages) < 2 or pages[0] != clause.get("page_start")
            or pages[-1] != clause.get("page_end") or source.get("physical_page") != pages[0]):
        return None
    try:
        connection = _cross_page_connection(db, task, scope, source)
        remaining = deadline - time.monotonic()
        if connection is None or remaining <= 0:
            return None
        with acquire_provider_backend(connection, timeout_seconds=remaining) as backend:
            report = backend.fetch_document_text_reported(source["knowledge_id"], kb_id=source["kb_id"], max_chars=None)
        parts = match_cross_page_reference(report, clause["text"], pages=pages, separators=spec.get("separators") or [])
        if (not parts or not _verify_authorized_original(db, task, scope, source, deadline)
                or _cross_page_connection(db, task, scope, source) != connection):
            return None
        return parts
    except Exception:
        return None


def _fetch_verified_local_rule_pages(db: Session, task: BsTask, scope: dict, source: dict,
                                     clause: dict, deadline: float):
    item = source.get("item_id")
    if item not in {"V16-02", "V16-03", "V16-04"} or source.get("local_illegal_rule") is not True:
        return None
    leaf = clause.get("text") or ""
    fragments = [{"page": 9, "role": "preamble", "text": clause.get("local_preamble")}]
    if item == "V16-02":
        if leaf.count("又不能进行合理解释并提供相应证明的；") != 1:
            return None
        first, _ = leaf.split("又不能进行合理解释并提供相应证明的；")
        fragments.extend([{"page": 9, "role": "item", "text": first + "又"},
                          {"page": 10, "role": "item", "text": "不能进行合理解释并提供相应证明的；"}])
    else:
        fragments.append({"page": 10, "role": "item", "text": leaf})
    try:
        connection = _cross_page_connection(db, task, scope, source)
        remaining = deadline - time.monotonic()
        if connection is None or remaining <= 0:
            return None
        with acquire_provider_backend(connection, timeout_seconds=remaining) as backend:
            report = backend.fetch_document_text_reported(source["knowledge_id"], kb_id=source["kb_id"], max_chars=None)
        parts = match_ordered_page_fragments(report, fragments)
        if (not parts or not _verify_authorized_original(db, task, scope, source, deadline)
                or _cross_page_connection(db, task, scope, source) != connection):
            return None
        return parts
    except Exception:
        return None


def _fetch_verified_local_transfer_pages(db: Session, task: BsTask, scope: dict, source: dict,
                                         clause: dict, deadline: float):
    item = source.get("item_id")
    if item not in {"T15-03", "T15-06", "T15-08", "T15-10"} or source.get("local_transfer_rule") is not True:
        return None
    leaf = clause.get("text") or ""
    fragments = [{"page": 7, "role": "preamble", "text": clause.get("local_preamble")}]
    if item == "T15-03":
        boundary = "个人采购、租赁"
        if leaf.count(boundary) != 1:
            return None
        first, last = leaf.split(boundary)
        fragments.extend([{"page": 7, "role": "item", "text": first + "个人"},
                          {"page": 8, "role": "item", "text": "采购、租赁" + last}])
    else:
        fragments.append({"page": 9 if item == "T15-10" else 8, "role": "item", "text": leaf})
    if item in {"T15-03", "T15-08"}:
        fragments.append({"page": 9, "role": "scope", "text": clause.get("local_scope_supplement")})
    try:
        connection = _cross_page_connection(db, task, scope, source)
        remaining = deadline - time.monotonic()
        if connection is None or remaining <= 0:
            return None
        with acquire_provider_backend(connection, timeout_seconds=remaining) as backend:
            report = backend.fetch_document_text_reported(source["knowledge_id"], kb_id=source["kb_id"], max_chars=None)
        parts = match_ordered_page_fragments(report, fragments)
        variant_used = False
        if not parts and clause.get("local_preamble_variant"):
            fragments[0] = {**fragments[0], "text": clause["local_preamble_variant"]}
            parts = match_ordered_page_fragments(report, fragments)
            variant_used = bool(parts)
        if (not parts or not _verify_authorized_original(db, task, scope, source, deadline)
                or _cross_page_connection(db, task, scope, source) != connection):
            return None
        return parts, variant_used
    except Exception:
        return None


def _fetch_verified_text_parts(db: Session, task: BsTask, scope: dict, source: dict,
                               clause: dict, preamble: str, deadline: float):
    try:
        connection = _cross_page_connection(db, task, scope, source)
        remaining = deadline - time.monotonic()
        if connection is None or remaining <= 0:
            return None
        with acquire_provider_backend(connection, timeout_seconds=remaining) as backend:
            report = backend.fetch_document_text_reported(source["knowledge_id"], kb_id=source["kb_id"], max_chars=None)
        chunks = report.get("chunks") or []
        if (report.get("status") != "ok" or report.get("scope_complete") is not True
                or any(not isinstance(row, dict) or row.get("knowledge_id") != source["knowledge_id"]
                       for row in chunks)):
            return None
        document = document_from_report(report)
        if document is None:
            # 兼容尚未返回 assembly_trace 的旧 Provider；带图片占位符的
            # 不完整报告仍会由 reconstruct_text_chunks 明确拒绝。
            text_chunks = [row for row in chunks if row.get("chunk_type") == "text"]
            document = reconstruct_text_chunks(text_chunks)
        parts = match_text_parts(document, preamble, clause["text"]) if document else None
        if (not parts or not _verify_authorized_original(db, task, scope, source, deadline)
                or _cross_page_connection(db, task, scope, source) != connection):
            return None
        return parts
    except Exception:
        return None


def _reference_group_budget(source_count: int) -> float:
    """按来源数量分配有界时间，保留至少半个运行租约供后续持久化。"""
    return min(30.0, max(10.0, source_count * 0.5))


def retrieve_reference_run(db: Session, task: BsTask, run: dict) -> dict:
    """每组按来源归并查询，总预算有界；没有在线来源时如实保存本地依据模式。"""
    terminal = deepcopy(run)
    terminal.update(state="completed", status="local_only", references=[],
                    authorized_scope=[], queries=[], finding_links=[], limitations=[])
    if run.get("subject_scope_unresolved"):
        terminal["limitations"].append({"code": "subject_type_unresolved",
            "message": "部分主体的分包类型尚未确认，无法选择对应在线依据，请人工核实"})
    sources = run.get("basis_sources") or []
    if not sources:
        if run.get("subject_scope_unresolved"):
            terminal["status"] = "undetermined"
        terminal["limitations"].append({"code": "local_only",
            "message": "本组使用已冻结的本地固定依据，尚无已配置的在线参考来源"})
        return terminal
    deadline = time.monotonic() + _reference_group_budget(len(sources))
    statuses = []
    query_cache, original_cache = {}, {}
    results = []
    for source in sources:
        result = _retrieve_source(db, task, run, source, max(0.0, deadline - time.monotonic()),
                                  query_cache=query_cache, original_cache=original_cache)
        results.append(result)
    for index, source in enumerate(sources):
        previous = results[index]
        queries = previous.get("queries") or []
        key = (source.get("query_text"), source.get("kb_id"), source.get("knowledge_id"))
        cached = query_cache.get(key)
        reason = queries[0].get("reason") if queries else None
        targets = queries[0].get("targets") or [] if queries else []
        timed_out = (reason == "timeout" or (reason == "all_failed" and targets and
                     all(t.get("error_code") in {"provider_timeout", "deadline_exceeded"} for t in targets)))
        if (previous["status"] == "undetermined" and timed_out and cached
                and (cached[0].status in {"hit", "partial"} or cached[1] < 2)
                and deadline > time.monotonic()):
            recovered = _retrieve_source(db, task, run, source, deadline - time.monotonic(),
                                         query_cache=query_cache, original_cache=original_cache)
            if recovered.get("references"):
                recovered["limitations"].append({"code": "reference_query_recovered",
                    "message": "部分参考查询曾超时，已在本轮重试并恢复；请结合引用状态核对原文"})
                results[index] = recovered
    for source, result in zip(sources, results):
        statuses.append(result["status"])
        for key in ("authorized_scope", "queries", "limitations"):
            for item in result[key]:
                if item not in terminal[key]:
                    terminal[key].append(item)
        terminal["references"].extend({**ref, "basis_source_key": _digest(source)}
                                      for ref in result["references"])
    for number, ref in enumerate(terminal["references"], 1):
        ref["reference_id"] = "ref-%d" % number
    terminal["status"] = ("ok" if all(s == "ok" for s in statuses)
                          and not run.get("subject_scope_unresolved") else "partial") if terminal["references"] else "undetermined"
    return terminal


def _retrieve_source(db: Session, task: BsTask, run: dict, source: dict, budget: float,
                     *, query_cache: dict | None = None, original_cache: dict | None = None) -> dict:
    deadline = time.monotonic() + max(0.0, budget)
    terminal = deepcopy(run)
    terminal.update(state="completed", status="undetermined", references=[],
                    authorized_scope=[], queries=[], finding_links=[], limitations=[])
    if budget <= 0:
        terminal["limitations"].append({"code": "deadline_exceeded",
            "message": "参考依据查询超时，请稍后重试或人工查阅原件；原核查结果保留"})
        terminal["queries"] = [{"clause_id": source.get("clause_id"), "status": "undetermined",
                                "reason": "deadline_exceeded", "used_for_decision": False}]
        return terminal
    try:
        doc, clause = _clause(source, as_of=task.baseline_date)
    except ReferenceBasisUnavailable:
        terminal["limitations"].append({"code": "reference_basis_unavailable",
            "clause_id": source.get("clause_id"),
            **({"list_no": source["list_no"]} if "list_no" in source else {}),
            "message": "参考依据的文件、条款或附件项未能核实，请联系管理员或人工查阅；原核查结果保留"})
        return terminal
    query_hash = _digest({
        "query_text": source.get("query_text"),
        "doc_id": source.get("doc_id"),
        "clause_id": source.get("clause_id"),
        **({"list_no": source["list_no"]} if "list_no" in source else {}),
        **({"expected_context": source["expected_context"]} if "expected_context" in source else {}),
        **({"separate_prefix": source["separate_prefix"]} if "separate_prefix" in source else {}),
        "attachment_title_anchor": source.get("attachment_title_anchor") is True,
        "section_item_anchor": source.get("section_item_anchor") is True,
        **({"excerpt_fragments": source["excerpt_fragments"]} if "excerpt_fragments" in source else {}),
        **({"clause_excerpt": source["clause_excerpt"]} if "clause_excerpt" in source else {}),
        "required_negative_preamble": source.get("required_negative_preamble") is True,
        "kb_id": source.get("kb_id"),
        "knowledge_id": source.get("knowledge_id"),
    })
    try:
        cache_key = (source["query_text"], source["kb_id"], source["knowledge_id"])
        cached = query_cache.get(cache_key) if query_cache is not None else None
        result, attempts = cached if cached is not None else (None, 0)
        errors = [item.get("error_code") for item in result.per_kb] if result is not None else []
        retryable = (result is not None and result.status == "undetermined" and (
            result.undetermined_reason == "timeout" or
            (result.undetermined_reason == "all_failed" and errors and
             all(error in {"provider_timeout", "deadline_exceeded"} for error in errors))))
        if result is None or (retryable and attempts < 2 and budget > 0):
            result = query_subcontract_knowledge(
                db, task, source["query_text"],
                selection={"kb_ids": [source["kb_id"]], "knowledge_ids": [source["knowledge_id"]]},
                top_k_per_kb=5, limit=5, per_kb_timeout_seconds=min(8.0, budget),
                overall_timeout_seconds=budget)
            if query_cache is not None:
                query_cache[cache_key] = (result, attempts + 1)
    except Exception:
        # 不保留异常正文：HTTP/凭据错误可能含连接与秘密；业务三态不受影响。
        terminal["limitations"].append({"code": "reference_unavailable",
                                        "message": "参考依据查询暂不可用，请稍后重试或人工查阅原件"})
        terminal["queries"] = [{"clause_id": source["clause_id"], "status": "undetermined",
                                 **({"list_no": source["list_no"]} if "list_no" in source else {}),
                                 "reason": "reference_unavailable", "query_hash": query_hash,
                                 "used_for_decision": False}]
        return terminal
    terminal["authorized_scope"] = deepcopy(result.target_scopes)
    terminal["queries"] = [{"query_text": source["query_text"], "clause_id": source["clause_id"],
                             **({"list_no": source["list_no"]} if "list_no" in source else {}),
                             "status": result.status, "reason": result.undetermined_reason,
                             "query_hash": query_hash, "used_for_decision": False,
                             "total_targets": result.total_targets,
                             "targets": [{k: item[k] for k in (
                                 "provider_id", "binding_id", "kb_id", "status", "error_code")
                                 if k in item} for item in result.per_kb]}]
    if result.status not in {"hit", "partial"}:
        reason = result.undetermined_reason or "no_original_reference"
        errors = [item.get("error_code") for item in result.per_kb]
        if reason == "all_failed" and errors and all(
                error in {"provider_timeout", "deadline_exceeded"} for error in errors):
            message = "参考依据查询超时，请稍后重试或人工查阅原件；原核查结果保留"
        elif reason == "no_target":
            message = "未配置本次参考依据的可用授权，请联系管理员"
        elif reason in {"selection_target_missing", "profile_target_missing"}:
            message = "当前任务类型未包含该参考文件，或文件未获授权；请联系管理员核对任务类型与知识库权限"
        else:
            message = "未取得可用参考原文，请人工查阅依据；原核查结果保留"
        terminal["limitations"].append({"code": reason, "message": message})
    fingerprint_missing = False
    fingerprint_mismatch = False
    text_difference = False
    verified_original = original_cache if original_cache is not None else {}
    use_parts = source.get("negative_parts") is True or source.get("national_transfer_parts") is True
    if use_parts and result.status in {"hit", "partial"}:
        eligible = {}
        for row in result.items:
            meta = row.get("metadata") or {}
            allowed = any(s["provider_id"] == row.get("provider_id") and s["binding_id"] == row.get("binding_id")
                          and s["kb_id"] == source["kb_id"] and (s["knowledge_ids"] is None
                          or source["knowledge_id"] in s["knowledge_ids"]) for s in result.target_scopes)
            if (not allowed or row.get("kb_id") != source["kb_id"] or row.get("knowledge_id") != source["knowledge_id"]
                    or meta.get("chunk_type") != "text"):
                continue
            fingerprint = meta.get("original_sha256")
            if fingerprint and fingerprint != source["original_sha256"]:
                fingerprint_mismatch = True
                continue
            key = (row.get("provider_id"), row.get("binding_id"), source["kb_id"], source["knowledge_id"],
                   source["original_sha256"])
            eligible.setdefault(key, []).append(row)
        if source.get("negative_parts"):
            rule = next((r for r in load_negative_list()["rules"] if r.get("id") == source["item_id"]), None)
            preamble = load_negative_list()["meta"]["preamble"]
        else:
            rule = {"kind": "prohibited", "text": clause["text"], "exceptions": []}
            parent = next(c for c in load_basis_clauses(as_of=task.baseline_date)["clauses"]
                          if c.get("clause_id") == "MOT-2024-2#第十五条")
            preamble = parent["text"].split("（一）", 1)[0]
        matches = []
        for key, rows in eligible.items():
            parts = match_negative_fragments(rows, rule, preamble) if rule else None
            if not parts:
                continue
            if key not in verified_original:
                verified_original[key] = _verify_authorized_original(db, task, rows[0], source, deadline)
            if verified_original[key]:
                matches.append((key, parts))
            else:
                fingerprint_missing = True
        if not matches and source.get("text_overlap_fallback") is True and not fingerprint_mismatch:
            scopes = [s for s in result.target_scopes if s.get("kb_id") == source["kb_id"]
                      and s.get("provider_id") is not None and s.get("binding_id") is not None
                      and (s.get("knowledge_ids") is None or source["knowledge_id"] in s["knowledge_ids"])]
            if len(scopes) == 1:
                scope = scopes[0]
                parts = _fetch_verified_text_parts(db, task, scope, source, clause, preamble, deadline)
                if not parts and source.get("retrieval_text_variant"):
                    parts = _fetch_verified_text_parts(db, task, scope, source,
                        {**clause, "text": source["retrieval_text_variant"]}, preamble, deadline)
                    text_difference = bool(parts)
                if parts:
                    key = (scope["provider_id"], scope["binding_id"], source["kb_id"],
                           source["knowledge_id"], source["original_sha256"])
                    matches.append((key, parts))
        if len(matches) == 1:
            key, parts = matches[0]
            terminal["references"].append({
                "reference_id": "ref-1", "purpose": "reference_only", "used_for_decision": False,
                "provider_id": key[0], "binding_id": key[1], "kb_id": key[2], "knowledge_id": key[3],
                "chunk_id": parts[0]["chunk_id"], "chunk_type": "text", "quote_parts": parts,
                "doc_id": source["doc_id"], "doc_title": doc["title"], "doc_no": doc.get("doc_no"),
                "clause_id": clause["clause_id"], "quote": "\n…\n".join(p["quote"] for p in parts),
                **({"reference_completeness": "partial"} if text_difference else {}),
                "original_sha256": source["original_sha256"], "fingerprint_verification": "authorized_original_sha256",
                "physical_page": source.get("physical_page"), "human_verified": bool(source.get("human_verified")),
            })
    for row in ([] if use_parts or source.get("local_transfer_rule") else result.items):
        metadata = row.get("metadata") or {}
        allowed = any(s["provider_id"] == row.get("provider_id")
                      and s["binding_id"] == row.get("binding_id")
                      and s["kb_id"] == source["kb_id"]
                      and (s["knowledge_ids"] is None or source["knowledge_id"] in s["knowledge_ids"])
                      for s in result.target_scopes)
        if (not allowed or row.get("kb_id") != source["kb_id"]
                or row.get("knowledge_id") != source["knowledge_id"]
                or metadata.get("chunk_type") not in {"text", "image_ocr"}):
            continue
        fingerprint = metadata.get("original_sha256")
        if fingerprint and fingerprint != source["original_sha256"]:
            fingerprint_mismatch = True
            continue
        fragments = clause.get("excerpt_fragments") or []
        expected = fragments[0] if fragments else source.get("expected_context", clause["text"])
        if source.get("required_negative_preamble") is True:
            expected = load_negative_list()["meta"]["preamble"] + "\n" + expected
        excerpt = _excerpt(row.get("document") or "", expected)
        if not excerpt:
            continue
        parts = None
        if (source.get("local_illegal_rule") is True or source.get("local_qualification_rule") is True
                or source.get("project_registration_rule") is True):
            document = row.get("document") or ""
            anchor = _excerpt(document, clause.get("local_preamble") or "")
            leaf = _excerpt(document, clause.get("text") or "")
            compact = "".join(document.split())
            first, item = "".join((anchor or "").split()), "".join((leaf or "").split())
            if (not first or not item or compact.count(first) != 1 or compact.count(item) != 1
                    or compact.find(first) + len(first) > compact.find(item)):
                continue
            parts = [{"role": "preamble", "chunk_id": row.get("id"), "quote": anchor},
                     {"role": "item", "chunk_id": row.get("id"), "quote": leaf}]
            excerpt = "\n…\n".join(part["quote"] for part in parts)
        if source.get("section_item_anchor") is True:
            document = row.get("document") or ""
            anchor = _excerpt(document, clause.get("section_anchor") or "")
            item_line = _excerpt(document, clause.get("item_line") or "")
            compact = "".join(document.split())
            first, item = "".join((anchor or "").split()), "".join((item_line or "").split())
            if (not first or not item or compact.count(first) != 1 or compact.count(item) != 1
                    or compact.find(first) + len(first) > compact.find(item)):
                continue
            parts = [{"role": "section_title", "chunk_id": row.get("id"), "quote": anchor},
                     {"role": "item", "chunk_id": row.get("id"), "quote": item_line}]
            excerpt = "\n…\n".join(part["quote"] for part in parts)
        if source.get("attachment_title_anchor") is True:
            document = row.get("document") or ""
            anchor = _excerpt(document, clause.get("attachment_anchor") or "")
            compact = "".join(document.split())
            first = "".join((anchor or "").split())
            items = [_excerpt(document, fragment) for fragment in fragments] if fragments else [excerpt]
            cursor = compact.find(first) + len(first) if first and compact.count(first) == 1 else -1
            matched_parts = []
            for item in items:
                needle = "".join((item or "").split())
                position = compact.find(needle, cursor) if needle and cursor >= 0 else -1
                if position < 0 or compact.count(needle) != 1:
                    cursor = -1
                    break
                matched_parts.append({"role": "item", "chunk_id": row.get("id"), "quote": item})
                cursor = position + len(needle)
            if cursor < 0:
                continue
            parts = [{"role": "form_title", "chunk_id": row.get("id"), "quote": anchor},
                     *matched_parts]
            excerpt = "\n…\n".join(part["quote"] for part in parts)
        if source.get("separate_prefix"):
            first = _excerpt(excerpt, source["separate_prefix"])
            leaf = _excerpt(excerpt, clause["text"])
            if not first or not leaf:
                continue
            parts = [{"role": "preamble", "chunk_id": row.get("id"), "quote": first},
                     {"role": "item", "chunk_id": row.get("id"), "quote": leaf}]
            excerpt = "\n…\n".join(part["quote"] for part in parts)
        verification_method = "chunk_metadata_sha256"
        if not fingerprint:
            fingerprint_missing = True
            scope = (row.get("provider_id"), row.get("binding_id"), source["kb_id"], source["knowledge_id"],
                     source["original_sha256"])
            if scope not in verified_original:
                verified_original[scope] = _verify_authorized_original(db, task, row, source, deadline)
            if not verified_original[scope]:
                continue
            verification_method = "authorized_original_sha256"
        terminal["references"].append({
            "reference_id": "ref-1", "purpose": "reference_only",
            "used_for_decision": False,
            "provider_id": row["provider_id"], "binding_id": row["binding_id"],
            "kb_id": source["kb_id"], "knowledge_id": source["knowledge_id"],
            "chunk_id": row.get("id"), "chunk_type": metadata["chunk_type"],
            "doc_id": source["doc_id"], "doc_title": doc["title"], "doc_no": doc.get("doc_no"),
            "clause_id": clause["clause_id"], "quote": excerpt,
            **({"reference_completeness": "partial"} if fragments or source.get("clause_excerpt") else {}),
            **({"quote_parts": parts} if parts else {}),
            **({"subject_ids": source["subject_ids"]} if "subject_ids" in source else {}),
            **({"subject_types": source["subject_types"]} if "subject_types" in source else {}),
            **({"list_no": source["list_no"]} if "list_no" in source else {}),
            "original_sha256": source["original_sha256"],
            "fingerprint_verification": verification_method,
            "physical_page": source.get("physical_page"),
            "human_verified": bool(source.get("human_verified")),
        })
        break  # 每个已登记子条款只保留一次完整原文，不冻结无关分块。
    if (not terminal["references"] and source.get("section_item_anchor") is True
            and not fingerprint_mismatch and result.status in {"hit", "partial"}):
        scopes = [s for s in result.target_scopes if s.get("kb_id") == source["kb_id"]
                  and s.get("provider_id") is not None and s.get("binding_id") is not None
                  and (s.get("knowledge_ids") is None or source["knowledge_id"] in s["knowledge_ids"])]
        if len(scopes) == 1:
            scope = scopes[0]
            parts = _fetch_verified_text_parts(db, task, scope, source,
                                               {**clause, "text": clause["item_line"]},
                                               clause["section_anchor"], deadline)
            if parts:
                terminal["references"].append({
                    "reference_id": "ref-1", "purpose": "reference_only", "used_for_decision": False,
                    "provider_id": scope["provider_id"], "binding_id": scope["binding_id"],
                    "kb_id": source["kb_id"], "knowledge_id": source["knowledge_id"],
                    "chunk_id": parts[0]["chunk_id"], "chunk_type": "text", "quote_parts": parts,
                    "doc_id": source["doc_id"], "doc_title": doc["title"], "doc_no": doc.get("doc_no"),
                    "clause_id": clause["clause_id"], "quote": "\n…\n".join(p["quote"] for p in parts),
                    **({"subject_ids": source["subject_ids"]} if "subject_ids" in source else {}),
                    **({"subject_types": source["subject_types"]} if "subject_types" in source else {}),
                    "original_sha256": source["original_sha256"],
                    "fingerprint_verification": "authorized_original_sha256",
                    "physical_page": source.get("physical_page"),
                    "human_verified": bool(source.get("human_verified")),
                })
    if (not terminal["references"] and source.get("local_transfer_rule") is True
            and not fingerprint_mismatch and result.status in {"hit", "partial"}):
        eligible = [scope for scope in result.target_scopes
                    if scope.get("kb_id") == source["kb_id"] and scope.get("provider_id") is not None
                    and scope.get("binding_id") is not None and (scope.get("knowledge_ids") is None
                    or source["knowledge_id"] in scope["knowledge_ids"])]
        if len(eligible) == 1:
            scope = eligible[0]
            fetched = _fetch_verified_local_transfer_pages(db, task, scope, source, clause, deadline)
            if fetched:
                parts, text_difference = fetched
                terminal["references"].append({
                    "reference_id": "ref-1", "purpose": "reference_only", "used_for_decision": False,
                    "provider_id": scope["provider_id"], "binding_id": scope["binding_id"],
                    "kb_id": source["kb_id"], "knowledge_id": source["knowledge_id"],
                    "chunk_id": parts[0]["chunk_id"], "chunk_type": "image_ocr", "quote_parts": parts,
                    "doc_id": source["doc_id"], "doc_title": doc["title"], "doc_no": doc.get("doc_no"),
                    "clause_id": clause["clause_id"], "quote": "\n…\n".join(part["quote"] for part in parts),
                    **({"reference_completeness": "partial"} if text_difference else {}),
                    "original_sha256": source["original_sha256"], "fingerprint_verification": "authorized_original_sha256",
                    "physical_page": 7, "physical_page_end": 8 if source["item_id"] == "T15-06" else 9,
                    "human_verified": bool(source.get("human_verified")),
                })
    if (not terminal["references"] and source.get("local_illegal_rule") is True
            and source.get("item_id") in {"V16-02", "V16-03", "V16-04"}
            and not fingerprint_mismatch and result.status in {"hit", "partial"}):
        eligible = [scope for scope in result.target_scopes
                    if scope.get("kb_id") == source["kb_id"] and scope.get("provider_id") is not None
                    and scope.get("binding_id") is not None and (scope.get("knowledge_ids") is None
                    or source["knowledge_id"] in scope["knowledge_ids"])]
        if len(eligible) == 1:
            scope = eligible[0]
            parts = _fetch_verified_local_rule_pages(db, task, scope, source, clause, deadline)
            if parts:
                terminal["references"].append({
                    "reference_id": "ref-1", "purpose": "reference_only", "used_for_decision": False,
                    "provider_id": scope["provider_id"], "binding_id": scope["binding_id"],
                    "kb_id": source["kb_id"], "knowledge_id": source["knowledge_id"],
                    "chunk_id": parts[0]["chunk_id"], "chunk_type": "image_ocr", "quote_parts": parts,
                    "doc_id": source["doc_id"], "doc_title": doc["title"], "doc_no": doc.get("doc_no"),
                    "clause_id": clause["clause_id"], "quote": "\n…\n".join(part["quote"] for part in parts),
                    "original_sha256": source["original_sha256"], "fingerprint_verification": "authorized_original_sha256",
                    "physical_page": 9, "physical_page_end": 10, "human_verified": bool(source.get("human_verified")),
                })
    if (not terminal["references"] and source.get("cross_page") and not fingerprint_mismatch
            and result.status in {"hit", "partial"}):
        eligible = [scope for scope in result.target_scopes
                    if scope.get("kb_id") == source["kb_id"] and scope.get("provider_id") is not None
                    and scope.get("binding_id") is not None and (scope.get("knowledge_ids") is None
                    or source["knowledge_id"] in scope["knowledge_ids"])]
        if len(eligible) == 1:
            scope = eligible[0]
            parts = _fetch_verified_cross_page(db, task, scope, source, clause, deadline)
            if parts:
                terminal["references"].append({
                    "reference_id": "ref-1", "purpose": "reference_only", "used_for_decision": False,
                    "provider_id": scope["provider_id"], "binding_id": scope["binding_id"],
                    "kb_id": source["kb_id"], "knowledge_id": source["knowledge_id"],
                    "chunk_id": parts[0]["chunk_id"], "chunk_type": "image_ocr",
                    "doc_id": source["doc_id"], "doc_title": doc["title"], "doc_no": doc.get("doc_no"),
                    "clause_id": clause["clause_id"], "quote": "\n".join(part["quote"] for part in parts),
                    "quote_parts": parts, "original_sha256": source["original_sha256"],
                    "fingerprint_verification": "authorized_original_sha256",
                    "physical_page": source["cross_page"]["pages"][0],
                    "physical_page_end": source["cross_page"]["pages"][-1],
                    "human_verified": bool(source.get("human_verified")),
                })
    if terminal["references"]:
        terminal["status"] = "partial" if result.status == "partial" or source.get("excerpt_fragments") or source.get("clause_excerpt") or text_difference else "ok"
        if source.get("clause_excerpt"):
            terminal["limitations"].append({"code": "reference_excerpt_incomplete",
                "message": "该依据仅为条文节选，不涵盖签署链全部节点；监理签署日期不能代替发包人书面同意，请核对完整原件及实际报审手续"})
        if text_difference:
            terminal["limitations"].append({"code": "reference_text_difference",
                "message": "知识库文本与原件的标点存在差异，引用保留知识库原文；请核对完整原件"})
        if source.get("excerpt_fragments"):
            terminal["limitations"].append({"code": "reference_excerpt_incomplete",
                "message": "该人员材料要求仅引用部分原文，完整要求请核对原件；不能据此认定材料齐全"})
        if source.get("human_verified") is True:
            terminal["limitations"].append({"code": "reference_only",
                "message": "参考文件仅作参考，仍需核对项目适用范围；不替代当前规则依据与核查结论"})
        else:
            terminal["limitations"].append({"code": "reference_only_unverified",
                "message": "参考文件仅作参考，整份原件尚未人工校对；不替代当前规则依据与核查结论"})
        if source.get("local_scope_notice") is True:
            terminal["limitations"].append({"code": "local_scope_supplement",
                "message": "国家办法原文未包含上海细则对分包人适用范围的补充；请结合本地固定依据核对"})
        if source.get("local_qualification_rule") is True:
            terminal["limitations"].append({"code": "qualification_reference_scope",
                "message": "该条来自上海细则，请核实项目所在地及适用版本；具体类别、等级、承揽范围及证照真实性仍需核实"})
        if source.get("project_registration_rule") is True:
            terminal["limitations"].append({"code": "project_registration_scope",
                "message": "该注册证书要求来自特定项目资料，请先核实本项目适用性，不能据此证明证书真实或注册有效"})
    elif not terminal["limitations"]:
        if fingerprint_mismatch:
            terminal["limitations"].append({"code": "reference_fingerprint_mismatch",
                "message": "召回原文的原件指纹与登记版本不一致，请人工查阅依据；原核查结果保留"})
        elif fingerprint_missing:
            terminal["limitations"].append({"code": "reference_fingerprint_missing",
                "message": "召回原文缺少原件指纹，无法确认版本，请人工查阅依据；原核查结果保留"})
        else:
            terminal["limitations"].append({"code": "no_original_reference",
                "message": "未召回同版本完整条款原文，请人工查阅依据；原核查结果保留"})
    if result.status == "partial":
        terminal["limitations"].append({"code": "partial_retrieval",
            "message": "部分参考查询未完成，已保留可用原文及查询限制"})
    return terminal


def references_for_source(run: dict, source: dict) -> list[dict]:
    """仅返回与本次来源实例和主体范围一致的参考。"""
    sources = run.get("basis_sources") or []
    references = run.get("references") or []
    legacy = not any(r.get("basis_source_key") for r in references)
    def belongs(ref, source):
        if (ref.get("doc_id") != source.get("doc_id") or ref.get("clause_id") != source.get("clause_id")
                or ref.get("list_no") != source.get("list_no") or ref.get("subject_ids") != source.get("subject_ids")):
            return False
        if ref.get("basis_source_key"):
            return ref["basis_source_key"] == _digest(source)
        # 历史运行没有来源实例键；只有候选配置唯一时才允许保守关联。
        return legacy and sum(1 for item in sources if all(item.get(k) == source.get(k)
            for k in ("doc_id", "clause_id", "list_no", "subject_ids"))) == 1
    return [ref for ref in references if belongs(ref, source)]


def link_reference_findings(run: dict, findings: list) -> None:
    sources = run.get("basis_sources") or []
    run["finding_links"] = [{"finding_id": f.id, "subject_id": f.subject_id,
                              "item_id": f.item_id, "kind": f.kind,
                              "clause_id": source["clause_id"], "relation": "reference_only",
                              **({"list_no": source["list_no"]} if "list_no" in source else {}),
                              "reference_ids": [r["reference_id"] for r in references_for_source(run, source)]}
                             for source in sources
                             for f in findings if f.item_id == source["item_id"]
                             and f.kind == source["kind"]
                             and ("subject_ids" not in source or f.subject_id in source["subject_ids"])]
    run["finding_ids"] = [f.id for f in findings]
    run["finding_kinds"] = sorted({f.kind for f in findings})
    run["finding_fingerprint"] = finding_fingerprint(findings)
    run["finished_at"] = datetime.now(timezone.utc).isoformat()


def get_reference_run(db: Session, task: BsTask, *, group_id: str = "a1") -> dict | None:
    """只读当前精确run终态；旧任务无指针即无引用，不临时补查。"""
    run_id = group_pointer(task, group_id)
    if not run_id:
        return None
    query = db.query(BsTaskEvent).filter(BsTaskEvent.task_id == task.id,
              BsTaskEvent.payload_json["run_id"].as_string() == run_id)
    review_id = (task.options_snapshot_json or {}).get(REVIEW_POINTER)
    if review_id:
        query = query.filter(BsTaskEvent.payload_json["review_run_id"].as_string() == review_id,
                             BsTaskEvent.payload_json["group_id"].as_string() == group_id)
    event = query.filter(BsTaskEvent.event_type == RUN_EVENT).first()
    if event:
        return deepcopy(event.payload_json)
    started = query.filter(BsTaskEvent.event_type == "knowledge.reference_started").first()
    run = deepcopy(started.payload_json) if started else {"run_id": run_id, "group_id": group_id,
                                                          "review_run_id": review_id}
    run.setdefault("group_id", group_id)
    run.setdefault("review_run_id", review_id)
    expired = reference_run_expired(run)
    run.update(state="expired" if expired else "running", purpose="reference_only", references=[],
               limitations=[{"code": "reference_timeout" if expired else "reference_pending",
                   "message": "上次参考查询已中断，请重新核查" if expired else "参考依据正在查询，请稍后刷新"}])
    return run
