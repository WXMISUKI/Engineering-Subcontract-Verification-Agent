"""纯读资质合规摘要投影，不接触数据库和发布状态。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


SUMMARY_SECTION_KINDS = frozenset({
    "completeness", "signature_chain", "qualification_match", "negative_list",
    "transfer_scope", "illegal_subcontract", "a1_dependency", "person_consistency",
    "cert_validity", "internal_records",
})
QUALIFICATION_KINDS = frozenset({
    "qualification_match", "negative_list", "transfer_scope", "illegal_subcontract", "a1_dependency",
})

# 结构化噪声词表是兼容旧 finding 的窄兜底；新 finding 应优先写入
# ``noise_only`` / ``noise_kind`` / ``decision_trace.noise_only``。这里不做自由
# 正则猜测，只有登记过的稳定语义才会被过滤，原始 finding 仍保留在机器审计中。
NOISE_KINDS = frozenset({"punctuation", "formatting", "wording"})
LEGACY_NOISE_MESSAGES = frozenset({
    "仅逗号不同", "仅标点不同", "标点差异", "换行差异", "格式差异",
    "错别字", "错别字差异", "仅错别字不同", "普通措辞差异", "措辞差异",
})


def _copy(value: Any) -> Any:
    return deepcopy(value)


def _evidence(ref: Any) -> dict[str, Any] | None:
    if not isinstance(ref, dict):
        return None
    result = {key: _copy(ref.get(key)) for key in (
        "material_id", "file_name", "page", "quote", "location_mode", "anchor"
    ) if ref.get(key) is not None}
    return result or None


def _item(item: dict[str, Any], source_kind: str) -> dict[str, Any]:
    audit_ref = {"item_id": item.get("item_id")}
    finding_id = item.get("finding_id", item.get("id"))
    subject = item.get("subject") if isinstance(item.get("subject"), dict) else {}
    subject_id = item.get("subject_id", subject.get("subject_id"))
    generation_id = item.get("generation_id")
    if finding_id is not None:
        audit_ref["finding_id"] = finding_id
    if subject_id is not None:
        audit_ref["subject_id"] = subject_id
    if generation_id is not None:
        audit_ref["generation_id"] = generation_id
    result = {
        "item_id": item.get("item_id"), "label": item.get("label") or item.get("item_id"),
        "status": item.get("status"), "status_label": item.get("status_label"),
        "subject": _copy(item.get("subject")), "message": item.get("message"),
        "recommendation": item.get("recommendation"), "missing": _copy(item.get("missing")),
        "evidence": _evidence(item.get("evidence")),
        "reading_limited": bool(item.get("reading_limited")), "source_kind": source_kind,
        "audit_ref": audit_ref,
    }
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


def _noise_reason(item: dict[str, Any]) -> str | None:
    """返回已登记的噪声原因；无法证明时保持 None。"""
    if item.get("noise_only") is True:
        return str(item.get("noise_kind") or "declared_noise")
    noise_kind = item.get("noise_kind")
    if noise_kind in NOISE_KINDS:
        return str(noise_kind)
    trace = item.get("decision_trace")
    if isinstance(trace, dict) and trace.get("noise_only") is True:
        return str(trace.get("noise_kind") or "decision_trace_noise")
    # 兼容上线前少量已落库的固定文案，不从任意 message 做模糊匹配。
    message = item.get("message")
    if isinstance(message, str) and message.strip() in LEGACY_NOISE_MESSAGES:
        return "legacy_registered_message"
    return None


def _contractor_summary(report: dict[str, Any]) -> dict[str, Any]:
    row = (report.get("contract_party_evidence") or {}).get("general_contractor")
    if not isinstance(row, dict):
        row = {"status": "missing", "reason_code": "party_role_missing"}
    status = row.get("status") if row.get("status") in {"matched", "unresolved", "conflict", "missing"} else "missing"
    labels = {
        "matched": "承包单位", "unresolved": "承包单位待核实",
        "conflict": "承包单位存在冲突，待核实", "missing": "承包单位未识别，待核实",
    }
    return {
        "role": "general_contractor", "status": status,
        "company": row.get("company") if status == "matched" else None,
        "candidate_company": row.get("candidate_company") if status == "unresolved" else None,
        "reason_code": row.get("reason_code"), "label": labels[status],
        "evidence": [_copy(item) for item in row.get("evidence") or [] if isinstance(item, dict)],
    }


def _subjects(report: dict[str, Any]) -> list[dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for section in report.get("sections") or []:
        if not isinstance(section, dict):
            continue
        for item in section.get("items") or []:
            subject = item.get("subject") if isinstance(item, dict) else None
            if not isinstance(subject, dict):
                continue
            key = str(subject.get("subject_id") or subject.get("stable_code") or subject.get("name") or "")
            if key:
                output[key] = {name: _copy(subject.get(name)) for name in (
                    "subject_id", "stable_code", "name", "subject_type"
                ) if subject.get(name) not in (None, "")}
    return list(output.values())


def _limitations(report: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("limitations", "scope_statements"):
        value = report.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value if item not in (None, ""))
    for row in report.get("reading_coverage") or []:
        if isinstance(row, dict) and row.get("summary_text"):
            values.append(str(row["summary_text"]))
    coverage = report.get("ai_coverage") or {}
    if isinstance(coverage, dict) and coverage.get("warning"):
        values.append(str(coverage["warning"]))
    return list(dict.fromkeys(values))


def build_qualification_summary(report: dict[str, Any]) -> dict[str, Any]:
    """从现有报告构造白名单摘要，不修改传入 report。"""
    report = report if isinstance(report, dict) else {}
    sections = {
        "qualification_compliance": [], "non_compliant": [], "missing": [],
        "undecidable": [], "key_evidence": [],
    }
    counts = {"hit": 0, "miss": 0, "undecidable": 0}
    audit_items = 0
    noise_filtered: list[dict[str, Any]] = []
    for section in report.get("sections") or []:
        if not isinstance(section, dict):
            continue
        kind = section["kind"]
        for raw in section.get("items") or []:
            if not isinstance(raw, dict) or raw.get("status") not in counts:
                continue
            audit_items += 1
            if kind not in SUMMARY_SECTION_KINDS:
                continue
            status = raw["status"]
            noise_reason = _noise_reason(raw) if status in {"hit", "undecidable"} else None
            if noise_reason is not None:
                noise_ref = {
                    "item_id": raw.get("item_id"),
                    "finding_id": raw.get("finding_id", raw.get("id")),
                    "subject_id": raw.get("subject_id")
                    or ((raw.get("subject") or {}).get("subject_id")
                        if isinstance(raw.get("subject"), dict) else None),
                    "generation_id": raw.get("generation_id"),
                    "reason": noise_reason,
                }
                noise_filtered.append({key: value for key, value in noise_ref.items() if value is not None})
                continue
            counts[status] += 1
            projected = _item(raw, kind)
            if status == "hit":
                sections["non_compliant"].append(projected)
            elif status == "undecidable":
                target = "missing" if raw.get("material_missing") or kind == "completeness" else "undecidable"
                sections[target].append(projected)
            # miss 只计合规数量，不把普通文案细节展开为业主问题。
            if kind in QUALIFICATION_KINDS and status in {"hit", "undecidable"}:
                sections["qualification_compliance"].append(projected)
            if projected.get("evidence") and status in {"hit", "undecidable"}:
                sections["key_evidence"].append(projected)
    return {
        "meta": _copy(report.get("meta") or {}),
        "subjects": _subjects(report),
        "general_contractor": _contractor_summary(report),
        "counts": {**counts, "total": sum(counts.values()), "audit_items": audit_items},
        "sections": sections,
        "limitations": _limitations(report),
        "audit_ref": {
            "source": "report", "machine_sections": "sections",
            "machine_contract_facts": "contract_facts", "machine_findings": audit_items,
            "reading_coverage": "reading_coverage",
            "noise_filtered": _copy(noise_filtered),
        },
        "publication": {
            "state": (report.get("meta") or {}).get("state"),
            "advisory_conclusion": _copy(report.get("advisory_conclusion") or {}),
        },
    }


__all__ = ["SUMMARY_SECTION_KINDS", "QUALIFICATION_KINDS", "build_qualification_summary"]
