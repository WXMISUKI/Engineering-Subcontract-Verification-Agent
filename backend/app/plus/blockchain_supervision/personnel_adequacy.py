# -*- coding: utf-8 -*-
"""DG/TJ 08-2225 人员配置判定（纯函数，不依赖数据库/OCR）。

边界：
- 只回答“岗位是否齐套、人数是否达到表列值、安全员是否达到法定下限”；
- B 类推荐配置不足只给“建议补正/提示”，不判违法分包；
- 关键岗位（项目负责人/技术/质量/安全）整岗缺失判缺项；
- 工程规模/作业人员人数无法确定时判待核实，不猜。
"""
from __future__ import annotations

from collections import Counter
from math import ceil, isfinite
import re

from .rules import load_personnel_adequacy


_POST_ALIASES = {
    "project_manager": ("项目负责人", "项目经理"),
    "technical_lead": ("技术负责人", "项目总工", "总工程师", "技术总工"),
    "construction_officer": ("施工员",),
    "quality_officer": ("质量员", "质量负责人", "质检员"),
    "safety_officer": ("安全员", "专职安全员", "安全负责人", "专职安全生产管理人员"),
    "standard_officer": ("标准员",),
    "material_officer": ("材料员",),
    "machinery_officer": ("机械员", "专职机械员"),
    "labor_officer": ("劳务员",),
    "document_officer": ("资料员",),
}

_LABOR_KEY_POSTS = {"project_manager", "safety_officer"}
_PROFESSIONAL_SCALES = {"small", "medium", "large"}


def _norm(value) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def normalize_post(label) -> str | None:
    """岗位中文名 → 稳定 key；已是 key 时原样返回；无法识别返回 None。"""
    text = _norm(label)
    if not text:
        return None
    if text in _POST_ALIASES:
        return text
    for key, aliases in _POST_ALIASES.items():
        if text in aliases:
            return key
    for key, aliases in _POST_ALIASES.items():
        if any(alias and alias in text for alias in aliases):
            return key
    return None


def _person(row: dict) -> dict:
    raw = row.get("posts")
    if raw is None:
        raw = row.get("post")
    if isinstance(raw, str):
        raw = re.split(r"[、,，/；;]+", raw)
    posts = []
    for value in raw or []:
        key = normalize_post(value)
        if key and key not in posts:
            posts.append(key)
    part_time = row.get("part_time")
    if isinstance(part_time, str):
        part_time = part_time.strip().lower() in {"是", "true", "1", "y", "yes"}
    else:
        part_time = bool(part_time)
    return {"name": str(row.get("name") or "").strip(), "posts": posts, "part_time": part_time}


def _match(params: dict, cond: dict) -> bool:
    field, op, expected = cond["field"], cond["op"], cond["value"]
    if field not in params or params[field] is None:
        return False
    try:
        actual = float(params[field])
        expected = float(expected)
    except (TypeError, ValueError):
        return False
    if op == ">=":
        return actual >= expected
    if op == ">":
        return actual > expected
    if op == "<=":
        return actual <= expected
    if op == "<":
        return actual < expected
    if op == "==":
        return actual == expected
    return False


def determine_scale(category: str | None, params: dict | None = None, *,
                    scale_fields: list[str] | None = None) -> dict:
    """最高档可由单项确定；较低档须排除其他维度触发更高档。"""
    data = load_personnel_adequacy()
    rule = (data.get("scale_rules") or {}).get(category or "")
    base = {"scale": None, "preliminary_scale": None, "complete": False,
            "missing_fields": [], "required_fields": [], "matched": [], "interpreted": False,
            "source_pages": (rule or {}).get("source_pages", [])}
    if not rule:
        return {**base, "reason": "未识别工程类别"}
    if rule.get("always"):
        return {**base,
            "scale": rule["always"],
            "reason": "原文固定档：%s" % rule.get("label", category),
            "interpreted": bool(rule.get("interpreted")),
            "complete": True,
        }
    conditions = [c for level in ("large", "medium") for c in (rule.get(level) or [])]
    known_fields = {c["field"] for c in conditions}
    required_fields = known_fields
    if scale_fields is not None:
        requested = set(scale_fields)
        if (not requested or not requested <= known_fields
                or "contract_amount_yi" not in requested):
            return {**base, "reason": "适用规模参数不完整，需核对分包范围"}
        required_fields = requested
    base["required_fields"] = sorted(required_fields)
    valid = {}
    for field in known_fields:
        value = (params or {}).get(field)
        try:
            if not isinstance(value, bool) and isfinite(float(value)) and float(value) >= 0:
                valid[field] = float(value)
        except (ValueError, TypeError, OverflowError):
            pass
    missing = sorted(required_fields - set(valid))
    base.update(missing_fields=missing, interpreted=bool(rule.get("interpreted")))
    if not valid:
        return {**base, "reason": "缺规模参数，需核对：" + "、".join(missing)}
    preliminary = rule.get("lowest_scale", "small")
    matched = []
    for level in ("large", "medium"):
        matched = [c for c in (rule.get(level) or []) if _match(valid, c)]
        if matched:
            preliminary = level
            break
    if preliminary == "large" or not missing:
        return {**base, "scale": preliminary, "complete": True, "matched": matched,
                "reason": "命中大型条件" if preliminary == "large" else "已核全部规模维度"}
    prefix = "按合同额初判" if set(valid) == {"contract_amount_yi"} else "按已知参数初判"
    return {**base, "preliminary_scale": preliminary, "matched": matched,
            "reason": prefix + {"small": "小型", "medium": "中型"}[preliminary]
                      + "；尚缺" + "、".join(missing) + "，更高档条件未能排除，待核实"}


SCALE_FIELD_LABELS = {"roadbed_km": "路基长度", "pavement_area_wan_m2": "路面面积",
                      "span_m": "桥梁单跨", "bridge_length_m": "桥梁总长",
                      "contract_amount_yi": "分包合同额"}


def evaluate_technical_title(proofs: list[dict], scale: str | None) -> dict:
    """已对应人员的职称证书等级维度；其他证种/未知名称不扩大解释。"""
    rule = load_personnel_adequacy()["post_requirements"]["technical_lead"]
    aliases = {title: grade for grade, titles in rule["title_aliases"].items() for title in titles}
    records, by_number, gaps = [], {}, []
    for proof in proofs:
        title = re.sub(r"^中华人民共和国|(?:正本|副本|[（(](?:正本|副本|电子证书|电子版|复印件)[）)])$", "",
                       proof.get("document_title") or "")
        if title not in rule["certificate_documents"]:
            continue
        values = proof.get("professional_title_candidates") or []
        number = proof.get("certificate_number")
        if number:
            by_number.setdefault(number, set()).update(re.sub(r"\s+", "", value) for value in values)
        if not number or len(values) != 1:
            gaps.append("职称或证号未唯一提取，需核对职称证书原件并补齐唯一字段")
            continue
        value = re.sub(r"\s+", "", values[0])
        grade = aliases.get(value)
        if grade:
            records.append({"grade": grade, "title": value, "certificate_number": number,
                            "allowed_scales": rule["title_by_scale"][grade]})
        else:
            gaps.append("职称尚不能唯一对应工程技术类等级，需核对证书原件及工程技术类等级证明")
    if any(len(values) > 1 for values in by_number.values()):
        return {"status": "undecidable", "reason": "同一职称证号的等级字段冲突，需核对原件"}
    if scale not in {"small", "medium", "large"}:
        return {"status": "undecidable", "reason": "分包工程规模尚未确定，需补齐适用规模参数"}
    covering = next((record for record in records if scale in record["allowed_scales"]), None)
    if covering:
        return {"status": "miss", "reason": "已提交工程技术类职称等级覆盖该规模维度", **covering}
    if gaps or not records:
        return {"status": "undecidable", "reason": "；".join(dict.fromkeys(gaps)) or "需核对可对应的工程技术类职称证书原页"}
    return {"status": "hit", "reason": "已提交工程技术类职称等级未覆盖该分包规模，需补充相应等级证明或调整人员",
            "submitted_titles": [record["title"] for record in records]}


def extract_scale_parameters(scopes: list[dict]) -> tuple[dict, list[dict], list[str]]:
    """复用资质规模候选抽取；补充附录A路基/路面单位，保留同页跨页冲突。"""
    from .qualification_scope import extract_parameters

    candidates = []
    for record in scopes:
        text = _norm(record.get("scope"))
        for name, fact in extract_parameters(text).items():
            field = {"single_span_m": "span_m", "total_length_m": "bridge_length_m"}.get(name)
            if field:
                candidates.extend({**record, **candidate, "field": field}
                                  for candidate in fact["candidates"])
        for field, pattern in (
            ("roadbed_km", r"路基(?:工程)?(?:长度|长)?[:：]?(\d+(?:\.\d+)?)(公里|千米|km|米|m)(?![²2])"),
            ("pavement_area_wan_m2", r"路面(?:工程)?面积[:：]?(\d+(?:\.\d+)?)(万)?(?:平方米|平米|㎡|m²|m2)"),
        ):
            for match in re.finditer(pattern, text, re.IGNORECASE):
                value = float(match.group(1))
                if field == "roadbed_km" and match.group(2).lower() in {"米", "m"}:
                    value /= 1000
                if field == "pavement_area_wan_m2" and not match.group(2):
                    value /= 10000
                candidates.append({**record, "field": field, "value": value,
                                   "parameter_quote": match.group(0)})
    params, conflicts = {}, []
    for field in {row["field"] for row in candidates}:
        values = {row["value"] for row in candidates if row["field"] == field}
        if len(values) == 1:
            params[field] = next(iter(values))
        else:
            conflicts.append(field)
    return params, candidates, sorted(conflicts)


def scale_reason_text(reason: str) -> str:
    for field, label in SCALE_FIELD_LABELS.items():
        reason = reason.replace(field, label)
    return reason


def legal_safety_minimum(contract_amount_wan, data: dict | None = None) -> int | None:
    """交安 25 号令第十四条：按年度施工产值/合同额计算专职安全员下限。

    档位从 data/personnel_adequacy.json 读取（规则数据的唯一来源），
    数据缺失时才回落到与数据文件一致的默认档位，避免代码与规则各写一套。
    """
    if contract_amount_wan is None:
        return None
    try:
        amount = float(contract_amount_wan)
    except (TypeError, ValueError):
        return None
    rule = (data or load_personnel_adequacy()).get("legal_safety_minimum") or {}
    for band in rule.get("thresholds") or []:
        if "lt" in band and amount < float(band["lt"]):
            if band.get("min") is not None:
                return int(band["min"])
            if band.get("divisor"):
                return max(1, ceil(amount / float(band["divisor"])))
            return None
        if "gte" in band and amount >= float(band["gte"]):
            return int(band["min"]) if band.get("min") is not None else None
    if amount < 5000:
        return 1
    if amount < 20000:
        return ceil(amount / 5000)
    return 5


def _labor_band(data: dict, worker_count: int) -> dict:
    for band in data["labor"]["bands"]:
        upper = band.get("max")
        if worker_count >= band["min"] and (upper is None or worker_count <= upper):
            return band
    return data["labor"]["bands"][-1]


def _result(**kwargs) -> dict:
    base = {
        "subcontract_type": None,
        "scale": None,
        "scale_info": None,
        "worker_count": None,
        "required": {},
        "actual": {},
        "missing_key_posts": [],
        "shortfalls": {},
        "advisories": [],
        "details": [],
        "unknown_posts": [],
        "legal_safety_minimum": None,
        "verdict": "符合",
        "severity": None,
        "basis_ids": [],
        "reason": None,
    }
    base.update(kwargs)
    return base


def _safety_shortfall(actual: int, legal_min: int | None) -> list[dict]:
    if legal_min is None or actual >= legal_min:
        return []
    return [{"post": "safety_officer", "label": "安全员", "required": legal_min,
             "actual": actual, "verdict": "不符合", "severity": "阻断", "action": "补正",
             "message": "安全员现报 %d 人，低于交安25号令第十四条按合同额计算的法定下限 %d 人"
                        % (actual, legal_min)}]


def evaluate_staffing(
    *,
    subcontract_type: str,
    roster: list[dict] | None,
    scale: str | None = None,
    category: str | None = None,
    params: dict | None = None,
    worker_count: int | None = None,
    contract_amount_wan: float | None = None,
    has_special_equipment: bool = True,
    scale_fields: list[str] | None = None,
) -> dict:
    """评估人员配置；返回判定、缺口、建议与依据坐标。"""
    data = load_personnel_adequacy()
    posts_meta = data["posts"]
    persons = [_person(row) for row in (roster or []) if isinstance(row, dict)]
    counts = Counter(post for person in persons for post in person["posts"])
    unknown = sorted({str(row.get("post") or "").strip() for row in (roster or [])
                      if isinstance(row, dict) and row.get("post")
                      and normalize_post(row.get("post")) is None})

    if not persons:
        return _result(
            subcontract_type=subcontract_type, worker_count=worker_count,
            verdict="缺项", severity="条件",
            reason="未提供主要管理人员名单或名单无可识别岗位",
            basis_ids=data["meta"]["basis_ids"],
        )

    scale_info = None
    legal_min = legal_safety_minimum(contract_amount_wan)
    if subcontract_type == "labor":
        if worker_count is None:
            return _result(
                subcontract_type="labor", verdict="待核实", severity="条件",
                reason="缺作业人员人数，无法套用劳务分包配置表",
                actual=dict(counts), unknown_posts=unknown,
                basis_ids=data["meta"]["basis_ids"],
            )
        band = _labor_band(data, int(worker_count))
        required = {k: v for k, v in band.items()
                    if k in posts_meta or k in {"safety_officer", "labor_officer", "project_manager"}}
        if band.get("safety_ratio_permille"):
            required["safety_officer"] = max(
                int(required.get("safety_officer", 0)),
                ceil(int(worker_count) * int(band["safety_ratio_permille"]) / 1000),
            )
        if band.get("labor_ratio_permille"):
            required["labor_officer"] = max(
                int(required.get("labor_officer", 0)),
                ceil(int(worker_count) * int(band["labor_ratio_permille"]) / 1000),
            )
    else:
        if scale not in _PROFESSIONAL_SCALES:
            scale_info = determine_scale(category, params, scale_fields=scale_fields)
            if not scale_info.get("scale"):
                safety = _safety_shortfall(counts.get("safety_officer", 0), legal_min)
                return _result(
                    subcontract_type="professional", verdict="不符合" if safety else "待核实",
                    severity="阻断" if safety else "条件", details=safety,
                    legal_safety_minimum=legal_min,
                    reason=scale_info.get("reason") or "工程规模无法确定",
                    actual=dict(counts), unknown_posts=unknown, scale_info=scale_info,
                    basis_ids=data["meta"]["basis_ids"],
                )
            scale = scale_info["scale"]
        required = dict(data["professional"]["by_scale"][scale])
        if not has_special_equipment:
            required.pop("machinery_officer", None)

    details, advisories, missing_key, shortfalls = [], [], [], {}
    key_posts = {k for k, meta in posts_meta.items() if meta.get("key")}
    if subcontract_type == "labor":
        key_posts = set(_LABOR_KEY_POSTS)

    for post, need in required.items():
        actual = counts.get(post, 0)
        if actual >= need:
            continue
        meta = posts_meta.get(post, {})
        label = meta.get("label", post)
        is_key = post in key_posts
        if actual == 0 and is_key:
            missing_key.append(post)
            details.append({
                "post": post, "label": label, "required": need, "actual": 0,
                "verdict": "缺项", "severity": "条件", "action": "补正",
                "message": "关键岗位「%s」未配置，按标准应配 %d 人" % (label, need),
            })
        elif actual == 0:
            advisories.append({
                "post": post, "label": label, "required": need, "actual": 0,
                "verdict": "符合", "severity": "提示", "action": "建议补正",
                "message": "「%s」未配置，标准表列为 %d 人；属推荐配置不足" % (label, need),
            })
        else:
            shortfalls[post] = {"required": need, "actual": actual}
            advisories.append({
                "post": post, "label": label, "required": need, "actual": actual,
                "verdict": "符合", "severity": "提示", "action": "建议补正",
                "message": "「%s」现报 %d 人，标准表列为 %d 人，人数不足" % (label, actual, need),
            })

    details.extend(_safety_shortfall(counts.get("safety_officer", 0), legal_min))

    scale_used = scale if subcontract_type == "professional" else None
    for person in persons:
        if len(person["posts"]) <= 1:
            continue
        if not person["part_time"]:
            advisories.append({
                "post": "/".join(person["posts"]), "label": person["name"] or "同一人员",
                "required": None, "actual": None,
                "verdict": "符合", "severity": "提示", "action": "说明",
                "message": "同一人员出现在多个岗位，未见兼职声明，需补充任命/兼职说明",
            })
            continue
        allowed = set((data["professional"]["part_time_allowed"].get(scale_used) or []))
        extras = person["posts"][1:]
        if scale_used and any(post not in allowed for post in extras):
            advisories.append({
                "post": "/".join(person["posts"]), "label": person["name"] or "同一人员",
                "required": None, "actual": None,
                "verdict": "符合", "severity": "提示", "action": "建议补正",
                "message": "兼职岗位不在该规模可兼职范围（%s）" % "、".join(sorted(allowed)),
            })
        if len(extras) > 1:
            advisories.append({
                "post": "/".join(person["posts"]), "label": person["name"] or "同一人员",
                "required": None, "actual": None,
                "verdict": "符合", "severity": "提示", "action": "建议补正",
                "message": "同一人员兼职超过 1 项，不符合 3.1.6 条",
            })

    if any(item["verdict"] == "不符合" for item in details):
        verdict, severity = "不符合", "阻断"
    elif missing_key:
        verdict, severity = "缺项", "条件"
    elif any(item["verdict"] == "待核实" for item in details):
        verdict, severity = "待核实", "条件"
    else:
        verdict, severity = "符合", ("提示" if advisories else None)

    return _result(
        subcontract_type=subcontract_type,
        scale=scale_used,
        scale_info=scale_info,
        worker_count=worker_count,
        required=dict(required),
        actual=dict(counts),
        missing_key_posts=missing_key,
        shortfalls=shortfalls,
        advisories=advisories,
        details=details,
        unknown_posts=unknown,
        legal_safety_minimum=legal_min,
        verdict=verdict,
        severity=severity,
        basis_ids=data["meta"]["basis_ids"],
        reason=None,
    )


__all__ = [
    "determine_scale",
    "evaluate_staffing",
    "legal_safety_minimum",
    "normalize_post",
]
