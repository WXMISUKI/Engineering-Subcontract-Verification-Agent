"""主体并档与孤儿清理：只读诊断（设计 136 §3–§4）。

只分类、不写库的是诊断部分；写操作按设计 136 T3（`apply_task`）与 T4（`rollback_event`）
分开实现，两组函数共用同一份诊断结果。

四类口径（每条主体只落一种）：

- 可并档：同名（`norm_company` 全等）+ 同 `subject_type`
- 可退休：空名 + 零引用（材料与报审包均无引用）
- 待定：同名但类型不同（同一单位不可能同时是劳务分包与专业分包，必有一侧标错，
  须先由人 `set_subject_type` 定类型）；或空名但仍有引用

有名字但零引用的主体**不退休**（设计 136 D6），只进清单供人过目；已替换材料
**算引用**（D7），避免误退仍在追溯链上的主体。
"""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy.orm import Session

from .a2_check import norm_company
from .models import BsMaterial, BsPacket, BsSubject, BsTask, BsTaskEvent
from .service import add_event

# 解析进行中的材料列表：半份文本会改结论，此时不得动主体引用（R5）。
# 与 start_review / rerun_task 的闸口径一致。
PARSE_IN_PROGRESS = ("uploading", "parsing")


def _clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def subject_references(db: Session, task_id: int) -> dict[int, list[str]]:
    """主体 → 引用项清单。已替换（superseded）的材料仍算引用。"""
    refs: dict[int, list[str]] = defaultdict(list)
    materials = (db.query(BsMaterial.subject_id, BsMaterial.id)
                 .filter(BsMaterial.task_id == task_id,
                         BsMaterial.subject_id.isnot(None)).all())
    for subject_id, material_id in materials:
        refs[subject_id].append(f"material:{material_id}")
    packets = (db.query(BsPacket.subject_id, BsPacket.id)
               .filter(BsPacket.task_id == task_id).all())
    for subject_id, packet_id in packets:
        refs[subject_id].append(f"packet:{packet_id}")
    return refs


def diagnose_task(db: Session, task: BsTask) -> dict:
    """对单个任务做三态分类，返回可并档组 / 可退休项 / 待定项 / 有名零引用清单。"""
    refs = subject_references(db, task.id)
    subjects = (db.query(BsSubject)
                .filter(BsSubject.task_id == task.id, BsSubject.retired.is_(False))
                .order_by(BsSubject.id).all())

    merge_groups: list[dict] = []
    retire: list[dict] = []
    pending: list[dict] = []
    named_unreferenced: list[dict] = []

    same_name: dict[str, list[BsSubject]] = defaultdict(list)
    for subject in subjects:
        name = _clean(subject.name)
        if not name:
            if refs.get(subject.id):
                pending.append({
                    "kind": "unnamed_with_refs",
                    "subject_ids": [subject.id],
                    "detail": "名称为空但仍被材料/报审包引用：需人工补名，不能按孤儿退休",
                })
            else:
                retire.append({
                    "subject_id": subject.id,
                    "stable_code": subject.stable_code,
                    "subject_type": subject.subject_type,
                })
            continue
        if not refs.get(subject.id):
            named_unreferenced.append({
                "subject_id": subject.id,
                "name": name,
                "stable_code": subject.stable_code,
            })
        same_name[norm_company(name)].append(subject)

    for members in same_name.values():
        if len(members) < 2:
            continue
        if len({member.subject_type for member in members}) > 1:
            pending.append({
                "kind": "type_mismatch",
                "subject_ids": [member.id for member in members],
                "detail": "同名但类型不同：同一单位不可能同时是劳务分包与专业分包，"
                          "先由人定类型再并档",
                "members": [{"subject_id": member.id,
                             "subject_type": member.subject_type,
                             "stable_code": member.stable_code,
                             "refs": refs.get(member.id, [])}
                            for member in members],
            })
            continue
        canonical = sorted(members, key=lambda m: (-len(refs.get(m.id, [])), m.id))[0]
        merge_groups.append({
            "name": _clean(canonical.name),
            "subject_type": canonical.subject_type,
            "canonical_id": canonical.id,
            "retire_ids": [member.id for member in members if member.id != canonical.id],
            "members": [{"subject_id": member.id,
                         "stable_code": member.stable_code,
                         "refs": refs.get(member.id, [])}
                        for member in members],
        })

    # 有名零引用只作提示；已进入并档组的成员由组信息负责呈现，避免同一主体在
    # 两张清单里各出现一次（人读清单时会误以为要分两步处理）。
    grouped_ids = {member["subject_id"]
                   for group in merge_groups for member in group["members"]}
    named_unreferenced = [item for item in named_unreferenced
                          if item["subject_id"] not in grouped_ids]

    return {
        "task_id": task.id,
        "state": task.state,
        "merge_groups": merge_groups,
        "retire": retire,
        "pending": pending,
        "named_unreferenced": named_unreferenced,
    }


def has_actions(diagnosis: dict) -> bool:
    """是否存在可执行（非待定）的变更项；全库扫描只打印有动作的任务。"""
    return bool(diagnosis["merge_groups"] or diagnosis["retire"])


def _gate_reason(db: Session, task: BsTask, force: bool) -> str | None:
    """R5 状态门禁：非 setup 默认拒绝（force 可放行）；解析进行中一律拒绝（force 也不放行）。

    `--force` 只放行"已开始核查的任务"这一种情形，不绕过解析闸：半份文本下
    改主体引用会让核查读到与结论不一致的主体集合。
    """
    pending = (db.query(BsMaterial.id)
               .filter(BsMaterial.task_id == task.id,
                       BsMaterial.parse_state.in_(PARSE_IN_PROGRESS)).count())
    if pending:
        return f"仍有 {pending} 份材料在解析中，解析完成后才能修复（force 不放行解析闸）"
    if task.state != "setup" and not force:
        return (f"任务处于 {task.state}，按 D1 默认只出清单；"
                f"确需修复请显式加 --force（会在事件流留「数据修复」标记）")
    return None


def apply_task(db: Session, task: BsTask, *, force: bool = False) -> dict:
    """执行并档与退休（设计 136 §4 R1–R6、§5 T3）。

    单事务提交：所有改指、退休标记与事件一起落库，任一失败整体回滚。
    事件 payload 记下每个被改指对象的**原主体 id**，它是 `--rollback` 的唯一依据。
    幂等：执行后再次调用，诊断结果为空 → 变更数 0、不写事件。
    """
    reason = _gate_reason(db, task, force)
    if reason:
        return {"ok": False, "reason": "gate", "detail": reason, "changes": 0}

    diagnosis = diagnose_task(db, task)
    data_fix = bool(force and task.state != "setup")
    merged_groups = 0
    retired_items = 0

    for group in diagnosis["merge_groups"]:
        retire_ids = list(group["retire_ids"])
        canonical_id = group["canonical_id"]
        materials = (db.query(BsMaterial)
                     .filter(BsMaterial.task_id == task.id,
                             BsMaterial.subject_id.in_(retire_ids)).all())
        packets = (db.query(BsPacket)
                   .filter(BsPacket.task_id == task.id,
                           BsPacket.subject_id.in_(retire_ids)).all())
        moved_materials = [{"id": mat.id, "from_subject_id": mat.subject_id}
                           for mat in materials]
        moved_packets = [{"id": packet.id, "from_subject_id": packet.subject_id}
                         for packet in packets]

        for mat in materials:
            mat.subject_id = canonical_id
        for packet in packets:
            packet.subject_id = canonical_id
        for subject_id in retire_ids:
            subject = db.get(BsSubject, subject_id)
            if subject is not None:
                subject.retired = True

        add_event(db, task.id, "subject.merged", payload={
            "name": group["name"],
            "subject_type": group["subject_type"],
            "from_subject_ids": retire_ids,
            "to_subject_id": canonical_id,
            "materials": moved_materials,
            "packets": moved_packets,
            "reason": "same_name",
            "data_fix": data_fix,
        }, commit=False)
        merged_groups += 1

    for item in diagnosis["retire"]:
        subject = db.get(BsSubject, item["subject_id"])
        if subject is None:
            continue
        subject.retired = True
        add_event(db, task.id, "subject.retired", payload={
            "subject_id": item["subject_id"],
            "stable_code": item.get("stable_code"),
            "subject_type": item.get("subject_type"),
            "reason": "orphan_anchor",
            "data_fix": data_fix,
        }, commit=False)
        retired_items += 1

    db.commit()
    return {
        "ok": True,
        "merged_groups": merged_groups,
        "retired_items": retired_items,
        "changes": merged_groups + retired_items,
        "data_fix": data_fix,
    }


# ── T4：按事件回放撤销 ────────────────────────────────────────────────────────

ROLLBACK_EVENT = "subject.rollback"
ROLLBACKABLE = ("subject.merged", "subject.retired")


def _rolled_back_ids(db: Session, task: BsTask) -> set[int]:
    ids: set[int] = set()
    rows = (db.query(BsTaskEvent.payload_json)
            .filter(BsTaskEvent.task_id == task.id,
                    BsTaskEvent.event_type == ROLLBACK_EVENT).all())
    for (payload,) in rows:
        if isinstance(payload, dict) and payload.get("rolled_back_event_id") is not None:
            ids.add(int(payload["rolled_back_event_id"]))
    return ids


def _merged_conflicts(db: Session, task: BsTask, payload: dict):
    """校验 `subject.merged` 回放的可行性，返回 (冲突说明, 还原动作)。

    任一冲突整单拒绝：半截回滚会留下既不是修前也不是修后的中间态，比不修更难解释。
    """
    to_id = payload.get("to_subject_id")
    from_ids = list(payload.get("from_subject_ids") or [])
    materials = payload.get("materials") or []
    packets = payload.get("packets") or []
    if to_id is None or not from_ids:
        return ["事件缺少 canonical 或原主体 id，无法回放"], None

    conflicts: list[str] = []
    canonical = db.get(BsSubject, to_id)
    if canonical is None or canonical.task_id != task.id:
        conflicts.append(f"canonical 主体 #{to_id} 已不存在")
    elif canonical.retired:
        conflicts.append(f"canonical 主体 #{to_id} 已被退休（期间又发生过并档）")

    for label, rows, model in (("材料", materials, BsMaterial),
                               ("报审包", packets, BsPacket)):
        for entry in rows:
            row = db.get(model, entry.get("id"))
            if row is None or row.task_id != task.id:
                conflicts.append(f"{label} #{entry.get('id')} 已不存在")
            elif row.subject_id != to_id:
                conflicts.append(
                    f"{label} #{row.id} 已改指到 #{row.subject_id}，不再是 #{to_id}")

    for sid in from_ids:
        subject = db.get(BsSubject, sid)
        if subject is None or subject.task_id != task.id:
            conflicts.append(f"主体 #{sid} 已不存在")
        elif not subject.retired:
            conflicts.append(f"主体 #{sid} 已不是退休状态（期间被重新启用）")

    def restore() -> None:
        for entry in materials:
            row = db.get(BsMaterial, entry["id"])
            if row is not None:
                row.subject_id = entry["from_subject_id"]
        for entry in packets:
            row = db.get(BsPacket, entry["id"])
            if row is not None:
                row.subject_id = entry["from_subject_id"]
        for sid in from_ids:
            subject = db.get(BsSubject, sid)
            if subject is not None:
                subject.retired = False

    return conflicts, restore


def _retired_conflicts(db: Session, task: BsTask, payload: dict):
    sid = payload.get("subject_id")
    if sid is None:
        return ["事件缺少 subject_id，无法回放"], None

    conflicts: list[str] = []
    subject = db.get(BsSubject, sid)
    if subject is None or subject.task_id != task.id:
        conflicts.append(f"主体 #{sid} 已不存在")
    elif not subject.retired:
        conflicts.append(f"主体 #{sid} 已不是退休状态（期间被重新启用）")

    def restore() -> None:
        row = db.get(BsSubject, sid)
        if row is not None:
            row.retired = False

    return conflicts, restore


def rollback_event(db: Session, task: BsTask, event_id: int, *,
                   force: bool = False) -> dict:
    """按事件回放撤销一次并档或退休（设计 136 T4、§6 验收 6）。

    三条已决口径（2026-10-29 用户确认）：

    - 回滚本身写一条 `subject.rollback`，payload 记被撤销的事件 id，事件流可核对；
    - 任一冲突（对象已改指、被退休主体又被启用、canonical 已退休）**整单拒绝**；
    - 与 `--apply` 共用状态/解析门禁：回滚同样是改主体引用，不该有更宽的口子。

    禁止链式回滚：`subject.rollback` 本身不可再被回滚。
    """
    reason = _gate_reason(db, task, force)
    if reason:
        return {"ok": False, "reason": "gate", "detail": reason, "changes": 0}

    event = db.get(BsTaskEvent, event_id)
    if event is None or event.task_id != task.id:
        return {"ok": False, "reason": "not_found",
                "detail": f"任务 {task.id} 下不存在事件 #{event_id}", "changes": 0}
    if event.event_type not in ROLLBACKABLE:
        return {"ok": False, "reason": "unsupported",
                "detail": f"事件 #{event_id} 是 {event.event_type}，可回滚的只有 "
                          f"{'、'.join(ROLLBACKABLE)}（不允许链式回滚）",
                "changes": 0}
    if event_id in _rolled_back_ids(db, task):
        return {"ok": False, "reason": "conflict",
                "detail": f"事件 #{event_id} 已被回滚过，重复回滚会叠加撤销",
                "changes": 0}

    payload = event.payload_json if isinstance(event.payload_json, dict) else {}
    if event.event_type == "subject.merged":
        conflicts, restore = _merged_conflicts(db, task, payload)
        restored_payload = {key: payload.get(key) for key in
                            ("from_subject_ids", "to_subject_id", "materials", "packets")}
    else:
        conflicts, restore = _retired_conflicts(db, task, payload)
        restored_payload = {"subject_id": payload.get("subject_id")}

    if conflicts:
        return {"ok": False, "reason": "conflict",
                "detail": "；".join(conflicts) + "。整单拒绝，未修改任何数据",
                "changes": 0}

    data_fix = bool(force and task.state != "setup")
    restore()
    add_event(db, task.id, ROLLBACK_EVENT, payload={
        "rolled_back_event_id": event.id,
        "rolled_back_event_type": event.event_type,
        "data_fix": data_fix,
        **restored_payload,
    }, commit=False)
    db.commit()
    return {"ok": True, "rolled_back": event.id, "changes": 1, "data_fix": data_fix}
