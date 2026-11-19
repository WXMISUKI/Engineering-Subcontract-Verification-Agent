"""任务执行摘要：面向工作台的只读聚合投影。

摘要只回答“当前执行/读取/人工处置各处在什么状态”，不参与报告门禁，
也不写库、不启动或恢复核查。门禁权威仍是 ``build_report_readiness``。
"""
from __future__ import annotations

from . import background_ai_review, reference_snapshots, rule_source_coverage, service
from .models import BsMaterial
from .rules import load_config

SCHEMA_VERSION = "bs.execution-summary.v1"
_EXECUTION_STATES = {"completed", "stale", "not_started", "running", "incomplete", "unknown"}


def _quality(reading: dict) -> dict:
    materials = reading.get("materials") or []
    unread = sum(len(row.get("unread_pages") or []) for row in materials)
    suspect = sum(len(row.get("suspect_pages") or []) for row in materials)
    review = sum(len(row.get("review_pages") or []) for row in materials)
    parse_failed = any(row.get("parse_state") == "failed" for row in materials)
    state = ("failed" if parse_failed else
             "complete" if reading.get("status") == "complete" else
             "pending" if reading.get("status") == "incomplete" else "unknown")
    if review and state == "complete":
        state = "pending"
    reason_codes = []
    if parse_failed:
        reason_codes.append("material_parse_failed")
    if unread:
        reason_codes.append("unread_pages")
    if suspect:
        reason_codes.append("suspect_pages")
    if review:
        reason_codes.append("manual_page_review")
    if state == "unknown":
        reason_codes.append("reading_coverage_unavailable")
    return {
        "state": state,
        "affected_materials": sum(
            1 for row in materials
            if row.get("review_pages") or not row.get("reading_complete", False)
            or row.get("parse_state") == "failed"),
        "unread_page_count": unread,
        "suspect_page_count": suspect,
        "review_page_count": review,
        "reason_codes": reason_codes,
    }


def _ai_advisory(db, task) -> dict:
    snapshot = reference_snapshots.get_reference_snapshot(db, task)
    groups = list((snapshot or {}).get("ai_auto_groups") or [])
    if not snapshot or snapshot.get("ai_auto_enabled") is not True:
        return {"state": "not_required", "groups": groups}
    review_run_id = (task.options_snapshot_json or {}).get(reference_snapshots.REVIEW_POINTER)
    if not review_run_id:
        return {"state": "not_started", "groups": groups}
    try:
        job = background_ai_review.job_status(db, task, review_run_id)
    except Exception:
        job = {"state": "unavailable"}
    return {
        "state": job.get("state", "unavailable"),
        "job_id": job.get("job_id"),
        "queued": job.get("queued") is True,
        "current_group": job.get("current_group"),
        "last_progress_at": job.get("last_progress_at"),
        "diagnostic_state": job.get("diagnostic_state"),
        "groups": groups,
    }


def _activity(health: dict, ai: dict) -> dict:
    if ai.get("state") == "running" and not ai.get("queued"):
        state = "active_observed"
    elif health.get("stale_candidate") is True:
        state = "stale_candidate"
    elif ai.get("queued"):
        state = "queued"
    elif ai.get("state") in {"running", "unavailable"}:
        state = "unknown"
    else:
        state = "idle"
    return {
        "state": state,
        "last_event_type": health.get("last_event_type"),
        "age_seconds": health.get("age_seconds"),
        "recovery_action": health.get("recovery_action"),
    }


def _presentation_status(task_state: str, execution: str, quality: str,
                         human: str, activity: dict) -> str:
    if task_state == "done":
        return "done"
    if task_state == "review":
        return "waiting_review"
    if task_state == "preparing":
        return "preparing"
    if task_state == "setup":
        return "setup"
    if activity.get("state") == "active_observed" or execution == "running":
        return "running"
    if execution == "stale":
        return "stale"
    if execution in {"completed", "incomplete"} and (human == "pending" or quality in {"pending", "failed"}):
        return "waiting_human"
    if execution == "incomplete":
        return "waiting_human"
    if execution == "not_started":
        return "not_started"
    if activity.get("state") == "stale_candidate":
        return "stale"
    if execution == "completed":
        return "awaiting_close"
    return "unknown"


def _scope(db, task) -> dict:
    """核查范围画像：哪些规则因来源缺失未覆盖、哪些后续功能未启用。

    只读投影，不改变门禁；``db`` 为空（纯投影测试）时只返回配置态。
    """
    config = load_config()
    disabled = [key for key in ("amount_ratio", "workload_ratio")
                if not config.get(key, {}).get("enabled")]
    if db is None:
        return {"rule_source_summary": None, "uncovered_rule_ids": [],
                "disabled_features": disabled}
    materials = (db.query(BsMaterial)
                 .filter(BsMaterial.task_id == task.id).all())
    coverage = rule_source_coverage.build_rule_source_coverage(materials)
    uncovered = [row["rule_id"] for row in coverage.get("rules") or []
                 if row.get("state") != "covered"]
    return {"rule_source_summary": coverage.get("summary"),
            "uncovered_rule_ids": uncovered,
            "disabled_features": disabled}


def build(db, task, *, readiness=None, reading=None, now=None) -> dict:
    """组装只读摘要；``readiness``/``reading`` 可复用同请求已算好的投影。"""
    readiness = readiness if readiness is not None else service.build_report_readiness(db, task)
    reading = reading if reading is not None else service.task_reading_coverage(db, task)
    execution = readiness.get("execution") or {}
    execution_state = execution.get("state") if execution.get("state") in _EXECUTION_STATES else "unknown"
    finding_review = readiness.get("finding_review") or {}
    pending = finding_review.get("pending_actions")
    human_state = ("unknown" if type(pending) is not int else
                   "pending" if pending > 0 else "completed")
    quality = _quality(reading)
    ai = _ai_advisory(db, task)
    activity = _activity(service.task_activity_health(db, task, now=now), ai)
    reason_codes = []
    for row in (readiness.get("blocking_reasons") or []) + (readiness.get("warnings") or []):
        code = row.get("code") if isinstance(row, dict) else None
        if isinstance(code, str) and code not in reason_codes:
            reason_codes.append(code)
    return {
        "schema_version": SCHEMA_VERSION,
        "task_state": task.state,
        "presentation_status": _presentation_status(
            task.state, execution_state, quality["state"], human_state, activity),
        "report_ready": readiness.get("confirmed_output_ready") is True,
        "blocking": bool(readiness.get("blocking_reasons") or []) or human_state == "pending",
        "execution": {"state": execution_state, "reason_codes": reason_codes},
        "scope": _scope(db, task),
        "ai_advisory": ai,
        "quality": quality,
        "human_review": {
            "state": human_state,
            "pending_count": pending if type(pending) is int else None,
            "pending_machine_review": finding_review.get("pending_machine_review"),
            "pending_manual_prompt": finding_review.get("pending_manual_prompt"),
        },
        "activity": activity,
    }
