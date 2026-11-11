"""报告投影规则：严格数据验证与独立版本，不执行策略代码。"""

from copy import deepcopy
from datetime import date
import hashlib
import json
import re
from pathlib import Path

POLICY_DIR = Path(__file__).with_name("report_policy_data")


class InvalidReportPolicy(ValueError):
    """报告策略不合法，不能用于判定。"""


def _validate(policy, *, require_complete_bindings=False):
    required = {"schema_version", "policy_id", "version", "rules"}
    if not isinstance(policy, dict) or not required.issubset(policy) or set(policy) - required - {"bindings"}:
        raise InvalidReportPolicy("报告策略字段不完整或存在未知字段")
    if policy["schema_version"] != "bs-report-policy.v1":
        raise InvalidReportPolicy("不支持的报告策略格式")
    if any(not isinstance(policy[k], str) or not policy[k].strip() for k in ("policy_id", "version")):
        raise InvalidReportPolicy("报告策略身份或版本无效")
    if not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", policy["version"]):
        raise InvalidReportPolicy("报告策略版本格式无效")
    if not isinstance(policy["rules"], list):
        raise InvalidReportPolicy("报告策略规则必须为列表")
    bindings = policy.get("bindings")
    if "bindings" in policy:
        supported_fields = {
            "completeness": {"报审资料", "主体资料", "人员资料", "合同及清单", "签字盖章及审批资料"},
            "signature_chain": {"签字盖章及审批资料"}, "qualification_match": {"分包性质与资质匹配"},
            "negative_list": {"拟分包内容与负面清单"}, "transfer_scope": {"转包与违法分包情形"},
            "illegal_subcontract": {"转包与违法分包情形"}, "a1_dependency": {"审批及责任约定"},
            "person_consistency": {"劳动关系及社保"}, "cert_validity": {"证件信息一致性"},
            "internal_records": {"内部记录核查情况"},
        }
        if not isinstance(bindings, list):
            raise InvalidReportPolicy("报告要点映射必须为列表")
        seen_bindings = set()
        for binding in bindings:
            if not isinstance(binding, dict) or set(binding) != {"kind", "item_ids", "field"}:
                raise InvalidReportPolicy("报告要点映射字段无效")
            kind, field, ids = binding["kind"], binding["field"], binding["item_ids"]
            if (not isinstance(kind, str) or not isinstance(field, str)
                    or field not in supported_fields.get(kind, set()) or not isinstance(ids, list) or not ids
                    or any(not isinstance(x, str) or not x for x in ids) or len(set(ids)) != len(ids)):
                raise InvalidReportPolicy("报告要点映射内容无效")
            if kind == "completeness":
                if any(not re.fullmatch(r"RC-[A-Z0-9-]+", item_id) for item_id in ids):
                    raise InvalidReportPolicy("资料要点映射标识无效")
            elif kind == "cert_validity":
                if not set(ids).issubset({"A2-03", "A2-05"}):
                    raise InvalidReportPolicy("证件动态映射不可配置")
            elif ids != ["*"]:
                raise InvalidReportPolicy("该类要点映射必须覆盖整个核查类型")
            keys = {(kind, item_id) for item_id in ids}
            if seen_bindings & keys:
                raise InvalidReportPolicy("报告要点映射重复")
            seen_bindings.update(keys)
        required_bindings = {
            ("completeness", item_id) for item_id in (
                "RC-08", "RC-09", "RC-A01", "RC-A02", "RC-A03", "RC-A04", "RC-A05", "RC-A06",
                "RC-A07", "RC-A08", "RC-A09", "RC-A10", "RC-P11", "RC-P12", "RC-P13", "RC-P14",
                "RC-P15", "RC-P16", "RC-L11", "RC-L12", "RC-L13", "RC-L14")}
        required_bindings.update((kind, "*") for kind in (
            "signature_chain", "qualification_match", "negative_list", "transfer_scope", "illegal_subcontract",
            "a1_dependency", "person_consistency", "internal_records"))
        required_bindings.update({("cert_validity", "A2-03"), ("cert_validity", "A2-05")})
        if require_complete_bindings and not required_bindings.issubset(seen_bindings):
            raise InvalidReportPolicy("报告要点映射缺少既有核查事项")
    ids, matches = set(), set()
    fields = {"id", "kind", "item_id", "point", "subject_types", "predicate", "verdict", "basis", "approved"}
    for rule in policy["rules"]:
        if (not isinstance(rule, dict) or not fields.issubset(rule)
                or set(rule) - fields - {"effective_from", "effective_to"}):
            raise InvalidReportPolicy("报告规则字段不完整或存在未知字段")
        dates = []
        for field in ("effective_from", "effective_to"):
            value = rule.get(field)
            if value is None:
                dates.append(None)
                continue
            try:
                if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    raise ValueError("invalid date")
                dates.append(date.fromisoformat(value))
            except ValueError as exc:
                raise InvalidReportPolicy("报告规则生效日期无效") from exc
        if dates[0] and dates[1] and dates[0] > dates[1]:
            raise InvalidReportPolicy("报告规则生效日期范围无效")
        if any(not isinstance(rule[k], str) or not rule[k].strip() for k in ("id", "item_id", "basis")):
            raise InvalidReportPolicy("报告规则标识或依据无效")
        subjects = rule["subject_types"]
        supported = {"required_certificate_missing": {"RC-A02", "RC-A03", "RC-A04"},
                     "required_document_missing": {"RC-A05", "RC-A06"}}
        if (rule["kind"] != "completeness" or rule["point"] != "主体资料"
                or not isinstance(rule["predicate"], str)
                or rule["item_id"] not in supported.get(rule["predicate"], set()) or rule["verdict"] != "不符合"
                or rule["approved"] is not True or not isinstance(subjects, list)
                or not subjects or any(not isinstance(s, str) or s not in {"professional", "labor"} for s in subjects)
                or len(set(subjects)) != len(subjects)):
            raise InvalidReportPolicy("报告规则超出已确认的判定能力")
        if rule["id"] in ids:
            raise InvalidReportPolicy("报告规则标识重复")
        if bindings is not None and not any(row["kind"] == rule["kind"] and row["field"] == rule["point"]
                and ("*" in row["item_ids"] or rule["item_id"] in row["item_ids"]) for row in bindings):
            raise InvalidReportPolicy("报告规则与要点映射不一致")
        keys = {(rule["kind"], rule["item_id"], subject) for subject in subjects}
        if matches & keys:
            raise InvalidReportPolicy("报告规则适用范围重叠")
        ids.add(rule["id"])
        matches.update(keys)


def policy_digest(policy):
    _validate(policy)
    content = json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def rule_applies_on(rule, baseline):
    start, end = rule.get("effective_from"), rule.get("effective_to")
    if not start and not end:
        return True  # 原规则未声明时间边界，沿用现行依据筛选口径。
    try:
        day = baseline if isinstance(baseline, date) else date.fromisoformat(baseline)
    except (TypeError, ValueError):
        return False
    return (not start or day >= date.fromisoformat(start)) and (not end or day <= date.fromisoformat(end))


def freeze_policy(policy):
    _validate(policy, require_complete_bindings=True)
    digest = policy_digest(policy)
    return {"content": deepcopy(policy), "sha256": digest}


def read_frozen_policy(snapshot):
    if not isinstance(snapshot, dict) or set(snapshot) != {"content", "sha256"}:
        raise InvalidReportPolicy("报告策略快照结构无效")
    if not isinstance(snapshot["sha256"], str) or policy_digest(snapshot["content"]) != snapshot["sha256"]:
        raise InvalidReportPolicy("报告策略快照校验失败")
    return deepcopy(snapshot["content"])


def load_current_policy():
    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise InvalidReportPolicy("报告策略包含重复字段")
            result[key] = value
        return result
    try:
        policy = json.loads((POLICY_DIR / "policy.json").read_text(encoding="utf-8"), object_pairs_hook=unique_fields)
    except (OSError, ValueError) as exc:
        raise InvalidReportPolicy("报告策略文件无法读取或解析") from exc
    _validate(policy, require_complete_bindings=True)
    if "bindings" not in policy:
        raise InvalidReportPolicy("当前报告策略缺少要点映射")
    return policy
