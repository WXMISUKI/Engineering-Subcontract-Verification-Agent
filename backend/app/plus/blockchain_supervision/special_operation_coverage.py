"""明示分包工法与特种作业名单覆盖；不猜设备、人数或证书归属。"""
import re
import unicodedata

from .rules import load_special_operation_catalog


def _norm(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or ""))).upper()


def required_work_types(scope, catalog=None):
    catalog = catalog or load_special_operation_catalog()
    # 否定只作用于当前分句，不能把“不含焊接，另含汽车吊”整句删除。
    clauses = [clause.strip() for clause in re.split(r"[，,。；;\n]", scope or "") if clause.strip()]
    active = [clause for clause in clauses if not re.search(r"不涉及|不包括|不包含|不含|无需|无须", clause)]
    required = {}
    for key, row in catalog["work_types"].items():
        hits = [clause for clause in active if any(word in clause for word in row.get("scope_keywords") or [])]
        if hits:
            required[key] = {"work_type": key, "label": row["label"], "scope_quotes": hits,
                             "accepted_cert_types": row["accepted_cert_types"]}
    gaps = []
    if "signal_slinger" in required and "mobile_crane_driver" not in required:
        gaps.append("吊装设备类型未明确，需确认设备及司机工种和证书类型")
    if "work_at_height" in required:
        text = "；".join(required["work_at_height"]["scope_quotes"])
        if not re.search(r"登高架设|脚手架搭设|吊篮安装|吊篮拆卸", text):
            gaps.append("高处作业实际类型未明确，需确认架设或吊篮等具体工种")
    if "crane_installer" in required:
        gaps.append("起重机械安装拆卸须按实际设备类型核对证书，需确认设备")
    if not required:
        gaps.append("未能从明示分包范围确认特种作业工法，需人工确认适用工种")
    return {"required": list(required.values()), "gaps": gaps}


def _type_candidate_matches(key, declared, scope_quotes, row):
    text = _norm(declared)
    if not text:
        return False
    marker_match = any(all(
        bool(re.search(r"(?<![A-Z0-9])" + re.escape(marker) + r"(?![A-Z0-9])", text))
        if marker in {"Q1", "Q2"} else _norm(marker) in text
        for marker in group) for group in row.get("certificate_markers") or [])
    if not marker_match:
        return False
    if key == "work_at_height":
        scope = "；".join(scope_quotes)
        if re.search(r"登高架设|脚手架搭设", scope) and "登高架设作业" not in text:
            return False
        if re.search(r"吊篮安装|吊篮拆卸", scope) and "高处作业吊篮安装拆卸工" not in text:
            return False
    return True


def evaluate_coverage(scope, roster, *, reading_complete=True):
    catalog = load_special_operation_catalog()
    demand = required_work_types(scope, catalog)
    details = []
    for required in demand["required"]:
        key = required["work_type"]
        rule = catalog["work_types"][key]
        people = [fact for fact in roster if _norm(fact.get("post")) in
                  {_norm(alias) for alias in rule.get("roster_aliases") or []}]
        types = sorted({str(fact["certificate_type"]) for fact in people if fact.get("certificate_type")})
        accepted = [fact for fact in people if _type_candidate_matches(
            key, fact.get("certificate_type"), required["scope_quotes"], rule)]
        missing = [fact for fact in people if not _norm(fact.get("certificate_type"))]
        invalid = [fact for fact in people if _norm(fact.get("certificate_type")) and
                   not _type_candidate_matches(key, fact.get("certificate_type"), required["scope_quotes"], rule)]
        if not people:
            status = "not_included" if reading_complete else "undecidable"
            message = ("缺少%s实填工种名单" if reading_complete else "材料读取不完整，需确认%s名单") % rule["label"]
        elif missing or invalid:
            status = "undecidable"
            message = "%s名单已见，%s条类型缺失、%s条类型不匹配，需逐人补充确认（可接受：%s）" % (
                rule["label"], len(missing), len(invalid), "或".join(rule["accepted_cert_types"]))
        else:
            status = "miss"
            message = "%s名单已见，明示证书类型候选：%s；证件归属、有效期和真实性另行核对" % (rule["label"], "、".join(types))
        details.append({**required, "status": status, "message": message, "people": people,
                        "accepted_people": accepted, "invalid_people": invalid, "missing_type_people": missing,
                        "actual_count": len({(fact.get("name"), fact.get("post")) for fact in people}),
                        "certificate_types": types})
    statuses = {detail["status"] for detail in details}
    status = ("not_included" if "not_included" in statuses else
              "undecidable" if "undecidable" in statuses or demand["gaps"] or not reading_complete else "miss")
    return {**demand, "details": details, "status": status,
            "quantity_notice": "人数仅记录名单实填数量，未按设备班次或作业面推定法定需求，不单独据此否决"}
