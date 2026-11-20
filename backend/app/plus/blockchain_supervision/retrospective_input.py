"""倒查上传门槛及合同关系；与准入完整性校验分开，不触发解析或模型。"""
from .models import BsMaterial

RELATIONS_KEY = "retrospective_contract_relations"
INPUT_VERSION = "bs.retrospective.dual-contract.v1"
ROLES = {"general", "sub", "labor"}
RELATIONS = {"main", "attachment", "supplement"}


def current_materials(db, task):
    return (db.query(BsMaterial).filter_by(task_id=task.id, superseded_by_id=None)
            .order_by(BsMaterial.id).all())


def readiness(db, task, *, relations=None):
    rows = current_materials(db, task)
    active = [row for row in rows if row.replacement_target_id is None]
    groups = {"general": [m for m in active if m.role == "general"],
              "sub": [m for m in active if m.role in {"sub", "labor"}]}
    gaps = []
    for key, label in (("general", "总包"), ("sub", "分包")):
        if not groups[key]:
            gaps.append(f"请上传{label}合同PDF")
    if any(m.role not in ROLES or not m.file_name.lower().endswith(".pdf") for m in active):
        gaps.append("倒查仅接收总包、专业分包或劳务分包合同PDF，不要求准入资料")
    pending = any(m.parse_state in {"uploading", "parsing"} for m in rows)
    if pending:
        gaps.append("合同正在读取，请稍后开始")
    if any(m.replacement_target_id is not None for m in rows):
        gaps.append("合同替换尚未生效，请等待读取完成或移除失败的新文件")
    if any(m.parse_state not in {"uploaded", "done"} for m in active):
        gaps.append("存在未能读取的合同，请移除或重新上传")
    if any(m.parse_state == "done" and not any(str(p.get("text") or "").strip()
                                              for p in m.pages_json or []) for m in active):
        gaps.append("存在未取得正文的合同，请重新上传")
    generals = groups["general"]
    if len(generals) > 1:
        ids = {m.general_source_set_id for m in generals}
        versions = {m.general_source_set_version for m in generals}
        if (len(ids) != 1 or None in ids or len(versions) != 1
                or type(next(iter(versions))) is not int or next(iter(versions)) < 1):
            gaps.append("多份总包文件须明确关联为同一总包来源集")
    if len({m.subject_id for m in groups["sub"] if m.subject_id is not None}) > 1:
        gaps.append("首版仅支持一个分包合同来源集，多个分包主体请分别新建核查")
    by_id = {m.id: m for m in active}
    supplied = relations is not None
    selected = relations if supplied else (task.options_snapshot_json or {}).get(RELATIONS_KEY, [])
    mapping = {}
    for item in selected:
        mid, relation = item.get("material_id"), item.get("relation")
        if mid not in by_id:
            if supplied:
                gaps.append("合同关系含无效或不属于本任务的材料")
            continue
        if mid in mapping or relation not in RELATIONS:
            gaps.append("合同关系重复或无效")
            continue
        mapping[mid] = relation
    for key, label in (("general", "总包"), ("sub", "分包")):
        members = groups[key]
        if len(members) == 1 and members[0].id not in mapping:
            mapping[members[0].id] = "main"
        if members and (any(m.id not in mapping for m in members)
                        or sum(mapping.get(m.id) == "main" for m in members) != 1):
            gaps.append(f"请明确一份{label}合同正文，其余文件选择附件或补充协议")
    return {"input_version": INPUT_VERSION, "hard_ok": not gaps,
            "hard_gaps": list(dict.fromkeys(gaps)), "soft_gaps": [], "parsing_pending": pending,
            "contract_relations": [{"material_id": mid, "relation": mapping[mid]} for mid in sorted(mapping)]}


def save_relations(db, task, relations, *, operator):
    from . import service
    task = service._lock_material_setup_task(db, task)
    if task.state != "setup":
        raise ValueError("核查已开始，不能修改合同关系")
    ids = {m.id for m in current_materials(db, task) if m.replacement_target_id is None}
    keys = [r["material_id"] for r in relations]
    if (len(keys) != len(set(keys)) or not set(keys) <= ids
            or any(r["relation"] not in RELATIONS for r in relations)):
        raise ValueError("合同关系含无效、重复或不属于本任务的材料")
    task.options_snapshot_json = {**(task.options_snapshot_json or {}), RELATIONS_KEY: relations}
    service.add_event(db, task.id, "retrospective.contracts_changed", stage="upload",
                      payload={"relations": relations, "operator": operator}, commit=False)
    db.commit()
    return readiness(db, task)
