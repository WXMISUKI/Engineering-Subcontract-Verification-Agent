"""材料候选人工确认的版本绑定，不改变核查结论或问题复核回执。"""
import hashlib
import json

from .models import BsMaterial, BsMaterialConfirmation
from .material_versions import subject_assessment_issue
from .reference_snapshots import REVIEW_POINTER
from .rules import load_report_checklist


def is_material_confirmable_finding(finding):
    """确认材料记录；合同包按子项处理，人员问题复核仍独立保留。"""
    item_id = getattr(finding, "item_id", "")
    return (getattr(finding, "kind", None) == "completeness"
            and any(item.get("id") == item_id for item in load_report_checklist()))


def evidence_token(finding, material, run_id):
    """仅为同主体、已解析且仍有效的候选生成绑定指纹。"""
    refs = getattr(finding, "evidence_json", None) or [] if finding else []
    linked = bool(material and finding and (finding.material_id == material.id or any(
        isinstance(ref, dict) and ref.get("material_id") == material.id
        and type(ref.get("page")) is int and ref["page"] > 0
        and ref.get("location_mode") in ("page", "precise") for ref in refs)))
    if (not run_id or not finding or not material or not finding.subject_id
            or finding.task_id != material.task_id or not linked
            or finding.subject_id != material.subject_id
            or material.parse_state != "done" or material.superseded_by_id is not None
            or subject_assessment_issue(material)):
        return None
    data = {"run_id": run_id, "finding_id": finding.id, "item_id": finding.item_id,
            "task_id": finding.task_id, "subject_id": finding.subject_id, "material_id": material.id,
            "page": finding.page, "quote": finding.quote, "location_mode": finding.location_mode,
            "revision_no": material.revision_no, "pages": material.pages_json or [], "evidence": refs}
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def current_decision(record, finding, material, run_id):
    """已过期或证据变化的确认不影响当前材料状态。"""
    if not isinstance(record, dict) or record.get("decision") not in ("conforming", "nonconforming"):
        return None
    token = evidence_token(finding, material, run_id)
    if not token or record.get("token") != token:
        return None
    if any(record.get(key) != value for key, value in {
        "finding_id": finding.id, "subject_id": finding.subject_id,
        "material_id": material.id, "item_id": finding.item_id, "run_id": run_id,
    }.items()):
        return None
    return record["decision"]


def attach_confirmations(db, task, summary, findings):
    """为清单补充当前代可确认的候选和人工决定。"""
    run_id = (task.options_snapshot_json or {}).get(REVIEW_POINTER)
    by_id = {f.id: f for f in findings}
    materials = {m.id: m for m in db.query(BsMaterial).filter_by(task_id=task.id).all()}
    records = {}
    for row in (db.query(BsMaterialConfirmation).filter_by(task_id=task.id)
                .order_by(BsMaterialConfirmation.id).all()):
        records[(row.finding_id, row.item_id)] = {key: getattr(row, key) for key in
                                   ("id", "finding_id", "subject_id", "material_id", "item_id",
                                    "run_id", "token", "decision")}
    for subject in summary.get("subjects", []):
        for item in subject.get("checklist_items", []):
            finding = by_id.get(item.get("finding_id"))
            if not finding or finding.status != "undecidable" or not is_material_confirmable_finding(finding):
                continue
            material = materials.get(finding.material_id)
            token = evidence_token(finding, material, run_id)
            candidate_ids = [finding.material_id]
            for ref in finding.evidence_json or []:
                if isinstance(ref, dict) and ref.get("material_id") not in candidate_ids:
                    candidate_ids.append(ref.get("material_id"))
            candidates = []
            for candidate_id in candidate_ids:
                candidate = materials.get(candidate_id)
                candidate_token = evidence_token(finding, candidate, run_id)
                if candidate_token:
                    candidates.append({"material_id": candidate.id, "file_name": candidate.file_name,
                                       "token": candidate_token})
            record = records.get((finding.id, finding.item_id))
            selected = materials.get(record.get("material_id")) if record else material
            item["material_confirmation"] = {
                "token": token,
                "candidates": candidates,
                "confirmation_id": (record or {}).get("id"),
                "material_id": selected.id if selected else None,
                "decision": current_decision(record, finding, selected, run_id),
            }
            decision = item["material_confirmation"]["decision"]
            if decision:
                counts = subject["checklist_summary"]
                counts["undecidable_total"] -= 1
                if decision == "conforming":
                    counts["present_total"] += 1
                    item["material_state"] = "provided"
                else:
                    counts["nonconforming_total"] += 1
                    counts["missing_total"] += 1
                    item["material_state"] = "missing"
                    item["missing"] = True
    return summary
