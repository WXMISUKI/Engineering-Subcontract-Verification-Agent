"""BS-P3-03/P3-04：来源准入与三层规则关系的纯函数。

本模块不上传、不写数据库，也不改变现有核查内核；它把 manifest 和规则卡的
治理结果变成可审计的决定，供后续管理员接口、编译器和冲突出口复用。
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from numbers import Real
import hashlib
import json


ADMITTED_VECTOR_STATES = {"completed", "submitted", "processing"}
ADMISSION_STATES = {"hold", "admitted", "rejected"}
FLOOR_LEVELS = {"floor", "national_rule", "municipal_rule"}
VALID_RELATIONS = {"equal", "stricter", "narrower_scope", "below_floor", "unknown"}


def _decision(status: str, reason_code: str, *, row: Mapping | None = None, **extra) -> dict:
    result = {
        "status": status,
        "reason_code": reason_code,
        "admitted": status == "admitted",
    }
    if row is not None:
        result["relative_path"] = row.get("relative_path")
        result["scope"] = row.get("scope")
        result["authority_level"] = row.get("authority_level")
    result.update(extra)
    return result


def evaluate_manifest_admission(row: Mapping, *, target_scope: str = "road_mvp") -> dict:
    """依据清单状态判断材料是否可以进入目标依据库。"""
    if not isinstance(row, Mapping):
        return _decision("rejected", "manifest_row_invalid")
    if row.get("selection") in {"sample_only", "reference_only", "excluded"}:
        return _decision("rejected", "selection_excluded", row=row)
    if row.get("scope") != target_scope:
        return _decision("rejected", "scope_mismatch", row=row, target_scope=target_scope)
    if row.get("selection") != "selected":
        return _decision("hold", "selection_not_confirmed", row=row)
    if row.get("vector_ingest") not in ADMITTED_VECTOR_STATES:
        return _decision("hold", "vector_ingest_not_ready", row=row)
    if row.get("verification") in {None, "", "source_metadata_pending", "validity_pending", "ocr_required"}:
        if (row.get("vector_ingest") == "completed"
                and (row.get("ingest_record") or {}).get("status") == "completed"):
            return _decision("admitted", "already_ingested_audit_preserved", row=row)
        return _decision("hold", "source_verification_incomplete", row=row)
    return _decision("admitted", "manifest_and_ingest_verified", row=row)


def project_manifest_admission(manifest: Mapping, *, target_scope: str = "road_mvp") -> dict:
    """把来源清单投影成只读准入审计结果，不访问原件、不写状态。"""
    if not isinstance(manifest, Mapping):
        return {"status": "invalid", "reason_code": "manifest_invalid", "files": []}
    rows = []
    for index, row in enumerate(manifest.get("files") or []):
        decision = evaluate_manifest_admission(row, target_scope=target_scope)
        rows.append({
            "index": index,
            "relative_path": row.get("relative_path"),
            "file_name": row.get("file_name"),
            "scope": row.get("scope"),
            "category": row.get("category"),
            "selection": row.get("selection"),
            "verification": row.get("verification"),
            "vector_ingest": row.get("vector_ingest"),
            "status": decision["status"],
            "admitted": decision["admitted"],
            "reason_code": decision["reason_code"],
        })
    digest = hashlib.sha256(json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, default=str
    ).encode("utf-8")).hexdigest()
    counts = {status: sum(row["status"] == status for row in rows)
              for status in sorted(ADMISSION_STATES)}
    return {
        "schema_version": "bs-source-admission-audit.v1",
        "manifest_schema_version": manifest.get("schema_version"),
        "manifest_hash": digest,
        "generated_at": manifest.get("generated_at"),
        "target_scope": target_scope,
        "policy": manifest.get("policy") or {},
        "summary": {"total": len(rows), **counts},
        "files": rows,
    }


def validate_admission_transition(previous_status: str | None, next_status: str) -> dict:
    """准入决定的有限状态机；历史审计记录不可被新决定覆盖。"""
    if next_status not in ADMISSION_STATES:
        return {"allowed": False, "reason_code": "invalid_next_status"}
    if previous_status is None:
        return {"allowed": True, "reason_code": "initial_decision"}
    if previous_status not in ADMISSION_STATES:
        return {"allowed": False, "reason_code": "invalid_previous_status"}
    if previous_status == next_status:
        return {"allowed": True, "reason_code": "idempotent_decision"}
    # 重新核验可以从 hold/rejected 升格；已准入遇到变更必须先回 hold。
    allowed = {
        "hold": {"admitted", "rejected"},
        "rejected": {"hold", "admitted"},
        "admitted": {"hold"},
    }
    return {"allowed": next_status in allowed[previous_status],
            "reason_code": "state_transition_allowed" if next_status in allowed[previous_status]
            else "state_transition_forbidden"}


def _items(card: Mapping) -> set[str]:
    values = card.get("items") or card.get("material_items") or []
    return {str(value) for value in values if str(value).strip()}


def _threshold(card: Mapping) -> tuple[Real, str, str] | None:
    value = card.get("threshold")
    if not isinstance(value, Mapping):
        return None
    number, direction, unit = value.get("value"), value.get("direction"), value.get("unit")
    if not isinstance(number, Real) or isinstance(number, bool):
        return None
    if direction not in {"min", "max", "eq"} or not isinstance(unit, str) or not unit.strip():
        return None
    return number, direction, unit


def _compare_threshold(floor: Mapping, candidate: Mapping) -> str:
    left, right = _threshold(floor), _threshold(candidate)
    if left is None and right is None:
        return "equal"
    if left is None or right is None or left[2] != right[2] or left[1] != right[1]:
        return "unknown"
    if left[1] == "min":
        return "stricter" if right[0] > left[0] else "equal" if right[0] == left[0] else "below_floor"
    if left[1] == "max":
        return "stricter" if right[0] < left[0] else "equal" if right[0] == left[0] else "below_floor"
    return "equal" if right[0] == left[0] else "below_floor"


def _compare_enums(floor: Mapping, candidate: Mapping) -> str:
    left = set(str(value) for value in (floor.get("enum_values") or []))
    right = set(str(value) for value in (candidate.get("enum_values") or []))
    if not left and not right:
        return "equal"
    if not left or not right:
        return "unknown"
    if right < left:
        return "stricter"
    if right == left:
        return "equal"
    return "below_floor"


def relation_to_floor(floor_card: Mapping, candidate_card: Mapping) -> dict:
    """比较候选卡与 floor；低于 floor 或无法自动证明时不得静默放行。"""
    if not isinstance(floor_card, Mapping) or not isinstance(candidate_card, Mapping):
        return {"relation": "unknown", "action": "pending_human_compare", "reasons": ["card_invalid"]}
    reasons = []
    floor_items, candidate_items = _items(floor_card), _items(candidate_card)
    if candidate_items > floor_items:
        item_relation = "stricter"
    elif candidate_items == floor_items:
        item_relation = "equal"
    elif candidate_items < floor_items:
        item_relation = "narrower_scope"
    else:
        item_relation = "unknown"
    if item_relation == "narrower_scope":
        reasons.append("candidate_items_narrower")

    comparisons = [_compare_threshold(floor_card, candidate_card),
                   _compare_enums(floor_card, candidate_card)]
    if any(value == "below_floor" for value in comparisons):
        reasons.append("candidate_value_below_floor")
    if "unknown" in comparisons:
        reasons.append("value_comparison_unknown")
    if floor_card.get("text_posture") != candidate_card.get("text_posture"):
        if {floor_card.get("text_posture"), candidate_card.get("text_posture")} & {"may", "can"}:
            reasons.append("obligation_wording_changed")

    if reasons and any(reason.endswith("below_floor") or reason == "candidate_value_below_floor"
                       for reason in reasons):
        relation, action = "below_floor", "conflict_escalate"
    elif any(reason.endswith("unknown") or reason == "obligation_wording_changed" for reason in reasons):
        relation, action = "unknown", "pending_human_compare"
    elif item_relation == "narrower_scope":
        relation, action = "narrower_scope", "admit_as_additional_scope"
    elif item_relation == "stricter" or "stricter" in comparisons:
        relation, action = "stricter", "admit_override"
    else:
        relation, action = "equal", "admit_equal"
    return {"relation": relation, "action": action, "reasons": reasons,
            "item_relation": item_relation, "value_comparisons": comparisons}


def validate_card_shape(card: Mapping) -> list[str]:
    """规则卡编译前的基础结构校验，七道闸门由编译器组合调用。"""
    errors = []
    if not isinstance(card, Mapping):
        return ["card_invalid"]
    for field in ("card_id", "source_text", "clause_id"):
        if not str(card.get(field) or "").strip():
            errors.append("missing_%s" % field)
    statuses = set(card.get("statuses") or [])
    if statuses and statuses != {"hit", "miss", "undecidable"}:
        errors.append("three_state_incomplete")
    threshold = card.get("threshold")
    if threshold is not None and _threshold(card) is None:
        errors.append("threshold_invalid")
    return errors


__all__ = ["ADMISSION_STATES", "ADMITTED_VECTOR_STATES", "evaluate_manifest_admission",
           "project_manifest_admission",
           "relation_to_floor", "validate_admission_transition", "validate_card_shape"]
