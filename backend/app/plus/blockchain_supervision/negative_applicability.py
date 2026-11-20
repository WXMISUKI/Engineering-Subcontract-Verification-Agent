# -*- coding: utf-8 -*-
"""负面清单适用性判定（纯函数，不依赖数据库/OCR）。

原则：
- 负面清单结论只有两种：通过 / 发现问题。
- 落进具体条目 → 出问题，写明第几项、原文、要改什么。
- 没落进具体条目 → 通过，不解释、不列不适用清单。
- 分包范围没写清 → 负面清单仍为通过；把“分包范围未载明/过宽”交给资料完整性判缺项，
  不在这里造一个“无法核对”的结论。
"""
from __future__ import annotations

import re

from .rules import load_negative_applicability, load_negative_list


def _norm(value) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _hits(text: str, words) -> list:
    return [word for word in (words or []) if word and word in text]


def classify_scope(scope_text: str, project_name: str = "") -> dict:
    """把分包范围文本分类为 domain + 结构部位。"""
    data = load_negative_applicability()
    keyword_map = data["keyword_map"]
    scope = _norm(scope_text)
    project = _norm(project_name)
    context = project + scope

    domains, parts, matched = set(), set(), []
    for part, words in (keyword_map.get("structure_parts") or {}).items():
        for word in _hits(scope, words):
            parts.add(part)
            matched.append({"type": "structure_part", "key": part, "word": word})
    for domain, words in (keyword_map.get("domains") or {}).items():
        for word in _hits(scope, words):
            domains.add(domain)
            matched.append({"type": "domain", "key": domain, "word": word})

    if not domains:
        bridge_context = _hits(context, keyword_map.get("bridge_context") or [])
        if parts & {"foundation", "substructure", "superstructure"} and bridge_context:
            domains.add("bridge")
        elif "tunnel" in parts:
            domains.add("tunnel")
        elif "roadbed" in parts:
            domains.add("roadbed")
        elif "pavement" in parts:
            domains.add("pavement")
        elif "traffic_safety" in parts:
            domains.add("traffic_safety")

    if "bridge" in domains and not (parts & {"foundation", "substructure", "superstructure"}):
        parts |= {"foundation", "substructure", "superstructure"}
    if "tunnel" in domains and "tunnel" not in parts:
        parts.add("tunnel")
    if "roadbed" in domains and "roadbed" not in parts:
        parts.add("roadbed")
    if "pavement" in domains and "pavement" not in parts:
        parts.add("pavement")
    if "traffic_safety" in domains and "traffic_safety" not in parts:
        parts.add("traffic_safety")

    return {
        "domains": sorted(domains),
        "structure_parts": sorted(parts),
        "matched": matched,
        "known": bool(domains),
        "scope_text": scope_text,
        "project_name": project_name,
    }


def _compare(actual: float, op: str, expected: float) -> bool:
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


def _threshold_result(rule: dict, params: dict) -> dict:
    thresholds = rule.get("thresholds") or []
    if not thresholds:
        return {"status": "met", "missing": []}
    missing = [t.get("key") for t in thresholds if params.get(t.get("key")) is None]
    if missing:
        return {"status": "missing", "missing": missing}
    results = []
    for threshold in thresholds:
        try:
            actual = float(params[threshold["key"]])
            expected = float(threshold["value"])
        except (KeyError, TypeError, ValueError):
            return {"status": "missing", "missing": [threshold.get("key")]}
        results.append(_compare(actual, threshold.get("op"), expected))
    logic = rule.get("threshold_logic") or "all"
    met = all(results) if logic == "all" else any(results)
    return {"status": "met" if met else "not_met", "missing": []}


def _exception_hits(rule: dict, scope_text: str) -> list:
    hits = []
    for phrase in rule.get("exceptions") or []:
        for token in re.split(r"[、,，;；]", str(phrase)):
            token = token.strip()
            if len(token) >= 2 and token in scope_text:
                hits.append(token)
    return sorted(set(hits))


def _rule_involved(rule: dict, app: dict, profile: dict, scope_text: str) -> bool:
    domains = set(profile.get("domains") or [])
    parts = set(profile.get("structure_parts") or [])
    app_domains = set(app.get("domains") or ([app["domain"]] if app.get("domain") else []))
    if not (app_domains & domains):
        return False
    app_parts = set(app.get("structure_parts") or [])
    if app_parts and not (app_parts & parts):
        return False
    work_types = app.get("work_types") or []
    if work_types and not _hits(scope_text, work_types):
        return False
    return True


def _scope_quality(profile: dict, scope_text: str) -> str:
    """给资料完整性用：missing / broad / ok。负面清单结论不受它影响。"""
    if not profile.get("known"):
        return "missing"
    if "bridge" in (profile.get("domains") or []):
        if "预制构件" in scope_text:
            return "ok" if _hits(scope_text, ["箱梁", "预制梁", "梁板", "立柱", "盖梁"]) else "broad"
        if "现浇梁" in scope_text:
            return "ok" if _hits(scope_text, ["落地式支撑架", "附着式支撑架", "悬臂", "移动模架"]) else "broad"
        specific = [
            "桩基", "沉井", "围堰", "基坑", "地基处理", "承台", "墩柱", "盖梁",
            "箱梁", "梁板", "预制梁", "上部结构", "下部结构",
            "吊装", "架设", "顶推", "悬臂", "移动模架",
        ]
        if not _hits(scope_text, specific):
            return "broad"
    if "tunnel" in (profile.get("domains") or []):
        specific = ["盾构", "TBM", "沉管", "二衬", "出碴", "竖井", "斜井", "瓦斯", "下穿", "岩溶", "深埋", "围岩"]
        if not _hits(scope_text, specific):
            return "broad"
    return "ok"


def evaluate_scope(scope_text: str, project_name: str = "", params: dict | None = None,
                   rules: list | None = None) -> dict:
    """负面清单结论：通过 / 发现问题。"""
    data = load_negative_applicability()
    applicability = data.get("rules") or {}
    rules = rules if rules is not None else load_negative_list()["rules"]
    params = params or {}
    scope = _norm(scope_text)
    profile = classify_scope(scope_text, project_name)
    scope_quality = _scope_quality(profile, scope)

    if not profile.get("known"):
        return {
            "status": "pass",
            "summary": "通过",
            "problems": [],
            "scope_quality": scope_quality,
            "scope_missing": True,
            "scope": profile,
            "audit": {"total": len(rules), "checked": [], "not_involved": len(rules)},
        }

    problems, checked, not_involved = [], [], 0
    for rule in rules:
        rule_id = rule.get("id")
        app = applicability.get(rule_id)
        if not app:
            continue
        if not _rule_involved(rule, app, profile, scope):
            not_involved += 1
            continue

        exceptions = _exception_hits(rule, scope)
        if exceptions:
            problems.append({
                "rule_id": rule_id, "article": rule.get("article"), "text": rule.get("text"),
                "verdict": "除外待确认",
                "message": "本分包范围涉及%s「%s」，但含除外情形（%s）；需按实际工作内容拆分确认是否属于禁止分包。"
                           % (rule.get("article"), rule.get("text"), "、".join(exceptions)),
                "action": "按实际工作内容拆分确认是否属于除外情形",
                "exceptions": exceptions,
            })
            continue

        if rule.get("kind") == "province_delegated":
            checked.append({
                "rule_id": rule_id, "article": rule.get("article"), "text": rule.get("text"),
                "reason": "省级增补事项；上海细则第九条已明文暂不增补",
            })
            continue

        threshold = _threshold_result(rule, params)
        if threshold["status"] == "missing":
            problems.append({
                "rule_id": rule_id, "article": rule.get("article"), "text": rule.get("text"),
                "verdict": "需确认",
                "message": "本分包范围可能涉及%s「%s」，但未载明%s，无法判断是否达到禁止条件；需补充%s及对应工作内容说明。"
                           % (rule.get("article"), rule.get("text"),
                              "、".join(threshold["missing"]), "、".join(threshold["missing"])),
                "action": "补充%s及对应工作内容说明" % "、".join(threshold["missing"]),
                "missing_parameters": threshold["missing"],
            })
        elif threshold["status"] == "not_met":
            checked.append({
                "rule_id": rule_id, "article": rule.get("article"), "text": rule.get("text"),
                "reason": "参数未达到禁止阈值",
            })
        else:
            problems.append({
                "rule_id": rule_id, "article": rule.get("article"), "text": rule.get("text"),
                "verdict": "违反",
                "message": "本分包范围涉及《公路工程施工分包负面清单（2024年版）》%s「%s」，属于禁止分包的主体和关键性工作；应调整分包范围或由承包人自行施工。"
                           % (rule.get("article"), rule.get("text")),
                "action": "调整分包范围或由承包人自行施工",
            })

    return {
        "status": "problem" if problems else "pass",
        "summary": ("发现 %d 项问题" % len(problems)) if problems else "通过",
        "problems": problems,
        "scope_quality": scope_quality,
        "scope_missing": False,
        "scope": profile,
        "audit": {
            "total": len(rules),
            "checked": checked,
            "not_involved": not_involved,
        },
    }


__all__ = ["classify_scope", "evaluate_scope"]
