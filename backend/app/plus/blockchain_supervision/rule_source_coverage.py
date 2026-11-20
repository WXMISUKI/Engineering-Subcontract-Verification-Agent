"""逐规则来源覆盖投影（只读）。

回答“这条规则为什么没有自动核查”：来源未提供、仍在读取、不可读、已覆盖，
或尚未建立可靠分类器。不运行核查、不写快照、不授予正式输出资格。
"""
from __future__ import annotations

from . import rules
from .parsing import material_reading_gap

SCHEMA_VERSION = "bs.rule-source-coverage.v1"

_ROLE_MAP = {
    "general_contract": ("general",),
    "contract_parties": ("general",),
    "general_scope": ("general",),
    "subcontract": ("sub", "labor"),
    "subcontract_scope": ("sub", "labor"),
    "sub_scope": ("sub", "labor"),
    "labor_contract": ("sub", "labor"),
    "qualification": ("sub", "labor"),
    "license": ("sub", "labor"),
    "safety_permit": ("sub", "labor"),
    "manager_roster": ("sub", "labor"),
    "deployed_personnel": ("sub", "labor"),
    "social_security_or_wage_relation": ("sub", "labor"),
    "site_management_org": ("sub", "labor"),
    "project_manager_management": ("sub", "labor"),
    "review_packet": ("sub", "labor", "packet"),
    "supervision_review": ("sub", "labor", "packet"),
}

_LABELS = {
    "general_contract": "总包合同",
    "contract_parties": "合同双方主体",
    "general_scope": "总包合同工程范围",
    "subcontract": "本单位分包/劳务合同",
    "subcontract_scope": "本单位分包范围",
    "sub_scope": "本单位分包范围",
    "labor_contract": "劳动合同/劳务关系",
    "qualification": "实际资质证书",
    "license": "实际营业执照",
    "safety_permit": "实际安全生产许可证",
    "manager_roster": "管理人员名单",
    "deployed_personnel": "实际派驻人员",
    "social_security_or_wage_relation": "社保或工资关系",
    "site_management_org": "现场管理机构",
    "project_manager_management": "项目经理履职",
    "review_packet": "本主体报审材料",
    "supervision_review": "监理审查原文",
    "technical_context": "技术参数与适用上下文",
    "provincial_supplement": "辖区省级增补清单",
    "checklist_source": "报告清单逐项来源",
}

_CHECKLIST_KEYS = (
    ("营业执照", "license"),
    ("资质", "qualification"),
    ("安全生产许可证", "safety_permit"),
    ("劳动合同", "labor_contract"),
    ("分包合同", "subcontract"),
)


def _checklist_keys(item) -> tuple[str, ...]:
    keys = tuple(child.get("key") for child in item.get("required_all") or ()
                 if child.get("key"))
    if keys:
        return keys
    text = "%s %s" % (item.get("label") or "", " ".join(item.get("keywords") or ()))
    for keyword, key in _CHECKLIST_KEYS:
        if keyword in text:
            return (key,)
    return ("checklist_source",)

_PENDING = {"uploaded", "parsing", "stored"}


def _material_state(material) -> str:
    if getattr(material, "parse_state", "done") in _PENDING:
        return "pending"
    if material_reading_gap(material) is not None:
        return "unreadable"
    return "readable"


def _measure(source_key: str, materials, states) -> dict:
    roles = _ROLE_MAP.get(source_key)
    result = {"source_key": source_key, "label": _LABELS.get(source_key, source_key)}
    if roles is None:
        return {**result, "state": "unknown", "reason": "source_classifier_unavailable",
                "material_refs": []}
    candidates = [m for m in materials if getattr(m, "role", None) in roles]
    refs = [{"material_id": getattr(m, "id", None)} for m in candidates]
    if not candidates:
        return {**result, "state": "uncovered", "reason": "source_not_provided",
                "material_refs": []}
    candidate_states = [states.get(getattr(m, "id", None)) or _material_state(m)
                        for m in candidates]
    if any(state == "pending" for state in candidate_states):
        return {**result, "state": "unknown", "reason": "source_reading_pending",
                "material_refs": refs}
    if any(state == "unreadable" for state in candidate_states):
        return {**result, "state": "unknown", "reason": "source_unreadable",
                "material_refs": refs}
    return {**result, "state": "covered", "reason": None, "material_refs": refs}


def _aggregate(requirements: list[dict]) -> tuple[str, list[str]]:
    states = {row["state"] for row in requirements}
    reasons = sorted({row["reason"] for row in requirements if row.get("reason")})
    if "unknown" in states:
        return "unknown", reasons or ["source_classifier_unavailable"]
    if "uncovered" in states:
        return "uncovered", reasons or ["source_not_provided"]
    return "covered", reasons


def _rule(rule_id: str, label: str, keys: tuple[str, ...], materials, states) -> dict:
    requirements = [_measure(key, materials, states) for key in keys]
    state, reasons = _aggregate(requirements)
    return {"rule_id": rule_id, "label": label, "state": state,
            "reason_codes": reasons, "requirements": requirements}


def build_rule_source_coverage(materials) -> dict:
    """按负面清单、转包规则和报告清单生成只读来源覆盖。"""
    materials = list(materials or [])
    states = {getattr(m, "id", None): _material_state(m) for m in materials}
    rules_out = []
    for item in rules.load_negative_list().get("rules") or []:
        keys = ("provincial_supplement",) if item.get("id") == "NL-R-01" else (
            "subcontract", "technical_context")
        rules_out.append(_rule(item.get("id"), item.get("text") or item.get("id"),
                               keys, materials, states))
    for item in rules.load_transfer_rules().get("rules") or []:
        keys = tuple(item.get("required_evidence") or ()) or ("subcontract",)
        if item.get("requires_general_contract"):
            keys = ("general_contract",) + keys
        rules_out.append(_rule(item.get("id"), item.get("title") or item.get("id"),
                               keys, materials, states))
    for item in rules.load_report_checklist():
        keys = _checklist_keys(item)
        rules_out.append(_rule(item.get("id"), item.get("label") or item.get("id"),
                               keys, materials, states))
    summary = {"covered": 0, "uncovered": 0, "unknown": 0}
    for row in rules_out:
        summary[row["state"]] = summary.get(row["state"], 0) + 1
    return {"schema_version": SCHEMA_VERSION, "policy": "read_only",
            "rules": rules_out, "summary": summary}
