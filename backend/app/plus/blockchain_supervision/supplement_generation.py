"""补件审查代与版本继承计划（P8-03B）。

本阶段只生成不可变的计划/投影，不修改任务、结论或审查版本；P8-03C
再把该计划接入真实代际创建和组级调度。
"""
from __future__ import annotations

from copy import deepcopy


def build_supplement_generation_plan(*, generation_id: str,
                                     parent_version_no: int | None,
                                     impact_plan: dict,
                                     material_ids=None,
                                     subject_ids=None,
                                     material_status: str = "ready",
                                     parent_review_run_id: str | None = None) -> dict:
    """构造补件新代计划；主体冲突/解析失败不得创建新代。"""
    material_ids = sorted({str(value) for value in (material_ids or []) if str(value).strip()})
    subject_ids = sorted({str(value) for value in (subject_ids or []) if str(value).strip()})
    if material_status in {"subject_conflict", "parse_failed", "unreadable"}:
        return {
            "status": "blocked",
            "trigger": "supplement",
            "generation_id": generation_id,
            "parent_version_no": parent_version_no,
            "material_ids": material_ids,
            "subject_ids": subject_ids,
            "blocked_reason": material_status,
        }

    return {
        "status": "planned",
        "trigger": "supplement",
        "generation_id": generation_id,
        "parent_generation_id": parent_review_run_id,
        "parent_version_no": parent_version_no,
        "material_ids": material_ids,
        "subject_ids": subject_ids,
        "mode": impact_plan.get("mode"),
        "affected_item_ids": list(impact_plan.get("item_ids") or []),
        "affected_groups": list(impact_plan.get("affected_groups") or []),
        "carried_forward_groups": list(impact_plan.get("carried_forward_groups") or []),
        "fallback_reason": impact_plan.get("fallback_reason"),
        "carry_policy": "unchanged_evidence_only",
        "affected_review_policy": "invalidate_and_recheck",
    }


def project_generation_findings(findings: list[dict], *, plan: dict,
                                origin_generation_id: str | None = None) -> list[dict]:
    """只读投影新代如何处理旧结论，供 API/测试/前端预览消费。"""
    affected_items = {str(item) for item in plan.get("affected_item_ids") or []}
    affected_materials = {str(item) for item in plan.get("material_ids") or []}
    projected = []
    for finding in findings or []:
        row = deepcopy(finding)
        affected = (
            str(finding.get("item_id")) in affected_items
            or str(finding.get("material_id")) in affected_materials
        )
        if affected:
            row["execution_mode"] = "recomputed"
            if finding.get("review"):
                row["review"] = {
                    "invalidated": {
                        "reason": "supplement_affected",
                        "previous": deepcopy(finding["review"]),
                    }
                }
        else:
            row["execution_mode"] = "carried_forward"
            row["origin_generation_id"] = origin_generation_id
        projected.append(row)
    return projected


def generation_metadata(plan: dict | None) -> dict:
    """当前或冻结计划的报告元数据；报告和证据包使用同一投影。"""
    if not isinstance(plan, dict) or not plan.get("generation_id"):
        return {}
    meta = {"generation_id": plan["generation_id"], "generation_status": plan.get("status")}
    if plan.get("execution_scope"):
        meta.update({
            "generation_scope": plan["execution_scope"],
            "generation_execution_groups": list(plan.get("execution_groups") or []),
            "generation_carried_forward_groups": list(plan.get("carried_forward_groups") or []),
        })
    if plan.get("fallback_reason"):
        meta["generation_fallback_reason"] = plan["fallback_reason"]
    if plan.get("fallback_message"):
        meta["generation_fallback_message"] = plan["fallback_message"]
    return meta


def fallback_message(scope: str | None, reason: str | None) -> str | None:
    if scope != "all" or not reason:
        return None
    if reason == "reference_material_changed":
        return "材料或主体发生变化，无法确认全部影响范围，已改为全量重新核查"
    if reason == "baseline_date_changed":
        return "核查基准日已更新，证照期限及适用规则需要重新核对，已改为全量重新核查"
    if reason == "section_information_changed":
        return "已补充项目或标段信息，相关内容需要重新核对，已改为全量重新核查"
    return "无法确认局部核查范围，已改为全量重新核查"


__all__ = ["build_supplement_generation_plan", "project_generation_findings",
           "generation_metadata", "fallback_message"]
