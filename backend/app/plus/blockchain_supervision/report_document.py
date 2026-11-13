"""面向业务报告正文的精简投影；机器明细仍保留在原报告 sections。"""

from __future__ import annotations
import re
from . import report_policy

_CATEGORIES = (
    ("要素完整性核查", {"completeness", "signature_chain"},
     ("报审资料", "主体资料", "人员资料", "合同及清单", "签字盖章及审批资料")),
    ("资质与内容合规性审查", {"qualification_match", "negative_list", "transfer_scope",
                                "illegal_subcontract", "a1_dependency"},
     ("分包性质与资质匹配", "拟分包内容与负面清单", "转包与违法分包情形", "审批及责任约定")),
    ("关键岗位人员核查", {"person_consistency"},
     ("主要人员及岗位", "人员与证件对应", "劳动关系及社保", "特殊工种人员")),
    ("证件证照有效性核查", {"cert_validity"},
     ("企业证照有效期", "人员证件有效期", "证件信息一致性", "证照真实性及核验情况")),
    ("内部信用与事故材料核查", {"internal_records"},
     ("核查材料情况", "内部记录核查情况", "核查范围及限制")),
)
_KIND_GROUP = {kind: group for group, kinds, _fields in _CATEGORIES for kind in kinds}
_SEAL_SOURCE_LIMIT = "；盖章判断仅适用于本地判章口径，不承诺主链路表现"

_KEY_POINTS = (
    (("报审资料", "报审资料"), ("主体资料", "主体资料"), ("人员资料", "人员资料"),
     ("合同及清单", "合同及清单"), ("审批程序", "签字盖章及审批资料")),
    (("主体资格", "分包性质与资质匹配"), ("负面清单", "拟分包内容与负面清单"),
     ("再分包与挂靠", "转包与违法分包情形"), ("审批及责任约定", "审批及责任约定")),
    (("关键岗位", "主要人员及岗位"), ("人员与证件对应", "人员与证件对应"),
     ("劳动关系社保", "劳动关系及社保"), ("特殊工种", "特殊工种人员")),
    (("企业证照有效期", "企业证照有效期"), ("人员证件有效期", "人员证件有效期"),
     ("证件信息一致性", "证件信息一致性"), ("证照真实性", "证照真实性及核验情况")),
    (("内部信用与事故记录", "内部记录核查情况"), ("核查范围", "核查范围及限制")),
)


def _limitation_groups(limitations: list[str]) -> list[dict]:
    """Group the original limitations without suppressing or rewriting them."""
    groups = {}
    for text in limitations:
        if any(word in text.lower() for word in ("ocr", "未读", "读取", "识别质量", "文字质量", "可读正文", "存疑页", "可疑页")):
            category = "材料识别与读取"
        elif "AI" in text or "模型" in text:
            category = "AI 辅助核查"
        elif any(word in text for word in ("证照", "证件", "信用", "真实性", "官方")):
            category = "证照与外部核验"
        elif any(word in text for word in ("依据", "合同", "工程量", "金额", "分包范围")):
            category = "依据与合同范围"
        else:
            category = "其他核查限制"
        groups.setdefault(category, []).append(text)
    return [{"label": label, "items": items} for label, items in groups.items()]


def _status_sentence(items: list[dict], complete: bool, has_results: bool) -> str:
    counts = {"hit": 0, "miss": 0, "undecidable": 0}
    for item in items:
        if item.get("status") in counts:
            counts[item["status"]] += 1
    if counts["hit"] or counts["undecidable"]:
        return "%s：需关注 %d 项、待核实 %d 项。" % (
            "本类已形成核查结果" if complete else "本类核查尚未完成，当前发现",
            counts["hit"], counts["undecidable"])
    if not complete:
        return "本类核查尚未完成，不能据此判断全部符合。"
    if not has_results:
        return "本类暂无可供归纳的核查结果，不能据此判断无问题。"
    return "本类已完成核查，未发现需要处理的问题。"


def _key_point_status(items: list[dict], complete: bool, reading_incomplete: bool) -> str:
    if any(item.get("status") == "hit" for item in items):
        return "需关注"
    if not items or not complete or reading_incomplete or any(item.get("status") == "undecidable" for item in items):
        return "待核实"
    return "已核查"


def _point_basis(items: list[dict], subject_types: dict | None = None) -> list[str]:
    groups = {}
    annex_groups = {}
    result = []
    for item in items:
        source = " ".join(str(item.get("source") or "").split())
        source = source.removeprefix("依据：").strip()
        if not source:
            continue
        if item.get("kind") == "signature_chain" and _SEAL_SOURCE_LIMIT in source:
            source = source.replace(_SEAL_SOURCE_LIMIT, "")
        match_annex = re.fullmatch(
            r"《2025 分包报审附件要求》(?:①(\d+)(?:/②(\d+))?|②(\d+))；第一现场指挥部 2025\.6\.20", source)
        if match_annex:
            professional, labor_pair, labor_only = match_annex.groups()
            subject_type = (subject_types or {}).get(item.get("subject_id"))
            item_id = str(item.get("item_id") or "")
            shared = professional and labor_pair and professional == labor_pair and item_id == f"RC-A{int(professional):02d}"
            specific = (professional and not labor_pair and item_id == f"RC-P{int(professional):02d}") or (
                labor_only and item_id == f"RC-L{int(labor_only):02d}")
            if shared or specific:
                source_type = None if shared else "professional" if professional else "labor"
                selected_type = subject_type if shared and subject_type in {"professional", "labor"} else source_type
                label = {"professional": "专业分包", "labor": "劳务分包"}.get(selected_type, "专业分包和劳务分包")
                suffix = ("（主体类型待确认）" if subject_type not in {"professional", "labor"} else
                          "（与主体类型不一致，需核实）" if source_type and source_type != subject_type else "")
                key = (label, suffix)
                if key not in annex_groups:
                    annex_groups[key] = []
                    result.append(("annex", key))
                number = professional or labor_only
                if number not in annex_groups[key]:
                    annex_groups[key].append(number)
                continue
        match = re.match(r"^(《[^》]+》)(第[一二三四五六七八九十百零〇0-9]+条).*（规则目录 [^）]+）$", source)
        if match:
            title, article = match.groups()
            if title not in groups:
                groups[title] = []
                result.append(("group", title))
            if article not in groups[title]:
                groups[title].append(article)
        elif ("source", source) not in result:
            result.append(("source", source))
    return [(value + "、".join(groups[value]) if kind == "group" else
             "《2025 分包报审附件要求》%s附件第%s项%s；第一现场指挥部 2025.6.20" %
             (value[0], "、".join(annex_groups[value]), value[1]) if kind == "annex" else value)
            for kind, value in result]


def _proven_required_material_missing(items: list[dict], policy=None, subject_types=None, baseline_date=None) -> bool:
    def allowed(item):
        if policy is None:
            return item.get("item_id") in {"RC-A02", "RC-A03", "RC-A04"}
        subject_type = (subject_types or {}).get(item.get("subject_id"))
        return any(rule["item_id"] == item.get("item_id") and rule["kind"] == item.get("kind")
                   and subject_type in rule["subject_types"] and report_policy.rule_applies_on(rule, baseline_date)
                   for rule in policy["rules"])
    return any(item.get("kind") == "completeness" and allowed(item)
           and item.get("status") == "hit" and item.get("material_missing") is True
           and not item.get("reading_limited") and str(item.get("source") or "").strip()
           and not ((item.get("human_disposition") or {}).get("code") == "accepted"
                    and str((item.get("human_disposition") or {}).get("note") or "").strip())
           for item in items)


def _proven_person_certificate_conflict(items, policy, subject_types, baseline_date):
    if not policy:
        return False
    for item in items:
        if (item.get("kind") != "cert_validity" or item.get("item_id") != "A2-05"
                or item.get("status") != "hit" or item.get("reading_limited")
                or not str(item.get("source") or "").strip()
                or ((item.get("human_disposition") or {}).get("code") == "accepted"
                    and str((item.get("human_disposition") or {}).get("note") or "").strip())):
            continue
        subject_type = (subject_types or {}).get(item.get("subject_id"))
        if not any(rule["kind"] == "cert_validity" and rule["item_id"] == "A2-05"
                   and rule["predicate"] == "verified_certificate_number_conflict"
                   and subject_type in rule["subject_types"] and report_policy.rule_applies_on(rule, baseline_date)
                   for rule in policy["rules"]):
            continue
        refs = item.get("evidence_list") or []
        valid = [ref for ref in refs if isinstance(ref, dict)
                 and ref.get("role") in {"support", "contradiction"}
                 and "certificate.number" in (ref.get("conflict_fields") or [])
                 and ref.get("material_id") is not None and type(ref.get("page")) is int and ref["page"] > 0
                 and ref.get("location_mode") in {"page", "precise"}
                 and str(ref.get("quote") or "").strip()
                 and ref.get("quality") not in {"unreadable", "review_required"}]
        if {ref["role"] for ref in valid} == {"support", "contradiction"}:
            return True
    return False


def _point_verdict(items: list[dict], complete: bool, reading_incomplete: bool, scope_pending: bool = False,
                   policy=None, subject_types=None, baseline_date=None) -> str:
    # hit 包括预警，severity 是排序档位，均不足以判断不符合或基本符合。
    if not items or not complete:
        return "待核实"
    if _proven_person_certificate_conflict(items, policy, subject_types, baseline_date):
        return "不符合"
    if reading_incomplete:
        return "待核实"
    # 三类必需证照采用用户确认的缺件口径；不扩展至其他材料。
    if _proven_required_material_missing(items, policy, subject_types, baseline_date):
        return "不符合"
    if any((item.get("human_disposition") or {}).get("code") == "accepted"
           and str((item.get("human_disposition") or {}).get("note") or "").strip() for item in items):
        return "待核实"
    if scope_pending:
        return "待核实"
    # A2-04 仅做证照文字类别比对，不覆盖等级、规模及真实性。
    if any(item.get("kind") == "qualification_match" and item.get("item_id") == "A2-04"
           for item in items):
        return "待核实"
    # 负面清单内核只对召回条目产出结果，单条未命中不证明整个适用清单已覆盖。
    if any(item.get("kind") == "negative_list" for item in items):
        return "待核实"
    def precise_evidence(item):
        ref = item.get("evidence")
        return (isinstance(ref, dict) and ref.get("material_id") is not None
                and type(ref.get("page")) is int and ref["page"] > 0
                and ref.get("location_mode") == "precise" and bool(str(ref.get("quote") or "").strip()))

    if all(item.get("status") == "miss" and not item.get("reading_limited")
           and (item.get("decision_trace") or {}).get("evidence_status") == "precise"
           and precise_evidence(item) and str(item.get("source") or "").strip() for item in items):
        return "符合"
    return "待核实"


def _key_point_summary(value: str) -> str:
    if value == "核查尚未完成或不在本次范围。":
        return "暂无可展示的核查结果，需核实。"
    return (value.replace("；本类核查尚未完成", "")
            .replace("需关注或核实：", "需核对：")
            .replace("，详见待处理事项摘要及人工意见", ""))


def report_display_sections(document: dict) -> list[dict]:
    """Place relevant limitations next to review points without changing frozen data."""
    sections = [{**section, "display_points": [dict(row) for row in
                 (section.get("key_points", section.get("fields")) or [])]}
                for section in document.get("sections") or []]
    scope_limitations = [str(value) for value in document.get("scope_limitations") or [] if value]
    if scope_limitations:
        sections.append({
            "title": "规则范围限制",
            "display_points": [{"label": "范围说明", "summary": value}
                               for value in scope_limitations],
        })
    # 旧版冻结投影尚未将事项摘要和人工备注放入要点；展示时补充，原快照不变。
    if document.get("schema_version") != "bs.business-report-document.v2":
        pending = document.get("pending_summaries")
        notes = document.get("human_notes")
        fallback_pending, fallback_notes = summarize_report_issues(document.get("issues") or [])
        for row in (pending if pending is not None else fallback_pending):
            category = row.get("category") or "其他核查"
            section = next((s for s in sections if s.get("title") == category), None)
            if section is None:
                section = {"title": category, "display_points": []}
                sections.append(section)
            summary = str(row.get("text") or "")
            for prefix, suffix in (("待核实：", "需要核实。"), ("需关注：", "需要关注。"), ("明确缺少材料：", "材料缺失。")):
                if summary.startswith(prefix):
                    summary = summary[len(prefix):].rstrip("。") + suffix
                    break
            section["display_points"].append({"label": "其他核查事项", "summary": summary})
        for row in (notes if notes is not None else fallback_notes):
            note = str(row.get("note") or "").strip()
            if not note or note == "未填写补充说明":
                continue
            category = row.get("category") or "其他核查"
            section = next((s for s in sections if s.get("title") == category), None)
            if section is None:
                section = {"title": category, "display_points": []}
                sections.append(section)
            section["display_points"].append({"label": str(row.get("label") or "人工补充"), "summary": "人工补充：" + note})
    limitations = (document.get("conclusion") or {}).get("limitations") or []
    if limitations and not any(section["display_points"] for section in sections):
        sections.append({"title": "材料与核查范围", "display_points": [{"label": "材料与核查范围", "summary": ""}]})
    targets = {point.get("label"): point for section in sections for point in section["display_points"]}
    for text in limitations:
        text = str(text)
        lower = text.lower()
        if any(word in lower for word in ("ocr", "未读", "读取", "识别质量", "文字质量", "质量可疑", "可读正文", "存疑页")):
            target = "报审资料"
        elif "AI" in text or "模型" in text:
            target = "报审资料"
        elif "信用" in text or "事故" in text:
            target = "内部信用与事故记录"
        elif any(word in text for word in ("证照", "证件", "真实性", "官方")):
            target = "证照真实性"
        elif any(word in text for word in ("金额", "工程量")):
            target = "合同及清单"
        elif any(word in text for word in ("合同", "依据", "分包范围")):
            target = "负面清单"
        else:
            target = "报审资料"
        point = targets.get(target)
        if point is None:
            point = next((p for s in sections for p in s["display_points"]), None)
        local_ai = "本阶段相关辅助核查未完成或依据关联不足，需结合相关资料核实。"
        global_ai = "部分 AI 辅助核查未完成，相关事项需结合现有结果人工核实。"
        if point is not None and text == global_ai and local_ai in str(point.get("summary") or ""):
            point["summary"] = point["summary"].replace(
                local_ai, "部分 AI 辅助核查未完成或依据关联不足，相关事项需结合现有资料核实。")
            continue
        if point is not None and text not in str(point.get("summary") or ""):
            placeholder = "当前没有可单独归纳的核查结果。"
            if str(point.get("summary") or "").startswith(placeholder):
                point["summary"] = point["summary"][len(placeholder):].lstrip()
            point["summary"] = " ".join(filter(None, [point.get("summary"), text]))
    return sections


def _field_summary(label: str, items: list[dict], complete: bool) -> str:
    if not items:
        return "当前没有可单独归纳的核查结果。" if complete else "核查尚未完成或不在本次范围。"
    pending = [item for item in items if item.get("status") in {"hit", "undecidable"}]
    passed = [item for item in items if item.get("status") == "miss"]
    parts = []
    for kinds, prefix in (({"completeness"}, "资料已查见"), (None, "已核查符合")):
        subset = [item for item in passed if (item.get("kind") in kinds if kinds else item.get("kind") != "completeness")]
        if subset:
            rule_kinds = {"negative_list", "transfer_scope", "illegal_subcontract", "a1_dependency"}
            rules = [item for item in subset if item.get("kind") in rule_kinds]
            other = [item for item in subset if item.get("kind") not in rule_kinds]
            if rules:
                parts.append("已核查 %d 项，未命中相应关注情形" % len(rules))
            if other:
                labels = list(dict.fromkeys(str(item.get("label") or "核查事项") for item in other))
                parts.append("%s：%s%s" % (prefix, "、".join(labels[:3]), "等事项" if len(labels) > 3 else ""))
    notes = list(dict.fromkeys(note for item in items
        if (item.get("human_disposition") or {}).get("code") == "accepted"
        for note in [" ".join(str((item.get("human_disposition") or {}).get("note") or "").split())]
        if note))
    if pending:
        def pending_label(item):
            if item.get("kind") in {"negative_list", "transfer_scope", "illegal_subcontract", "a1_dependency"}:
                return label + "相关事项"
            names = re.findall(r"「([^」]+)」", str(item.get("message") or "")) if item.get("item_id") == "A2-02" else []
            return names[0] + "有效期" if len(names) == 1 and names[0] in {
                "营业执照", "资质证书", "安全生产许可证", "施工劳务备案证书", "特种作业操作证", "安全生产考核合格证"
            } else str(item.get("label") or "核查事项")
        statements = []
        suggestions = []
        grouped_gaps = []
        unresolved_labels = []
        seen_signature_clues = set()
        grouped_uncertain = sum(1 for item in pending if item.get("status") == "undecidable"
                                and (item.get("missing") or {}).get("labels")) > 1
        group_plain_uncertain = sum(1 for item in pending if item.get("status") == "undecidable"
                                    and not (item.get("missing") or {}).get("labels")
                                    and not str((item.get("human_disposition") or {}).get("note") or "").strip()) > 3
        for item in pending:
            message = " ".join(str(item.get("message") or "").split())
            if item.get("source_kind") == "negative_candidate":
                # 引文校验仅证明摘录可定位，不能证明模型整段推理；摘要只展示
                # 已引用的原文线索，人工意见仍由下方 notes 独立保留。
                quotes = [" ".join(ref.get("quote", "").split())
                          for ref in (item.get("evidence_list") or [])
                          if isinstance(ref, dict) and isinstance(ref.get("quote"), str)]
                quote = next((value for value in quotes if 4 <= len(value) <= 100), "")
                message = ("材料原文提及“%s”；需结合完整资料和适用依据核对" % quote
                           if quote else "已有模型辅助核查线索，需结合完整资料和适用依据核对")
            message = re.sub(r"主体\s*\d+（([^（）]{1,120})）", r"\1", message)
            if (item.get("status") == "undecidable" and message.startswith("不可判定：")
                    and any(term in message[len("不可判定："):] for term in ("未", "无法", "不足", "缺"))):
                message = message[len("不可判定："):]
            item_label = pending_label(item)
            if len(message) > 200:
                # 仅使用完整句子，不在句中截断并伪造完整结论；详情仍保存在核查记录。
                sentences = re.split(r"(?<=[。！？])", message)
                brief = sentences[0].strip() if sentences and len(sentences[0].strip()) <= 180 else ""
                message = (brief + "（详细情况见核查记录）") if brief else "详细情况见核查记录"
            statement = (item_label + "：" + message.rstrip("。； ")
                         if message and len(message) <= 200 and item_label not in message
                         else message.rstrip("。； ") if message and len(message) <= 200 else item_label)
            refs = item.get("evidence_list") or []
            if (item.get("kind") == "signature_chain" and item.get("subject_id") is not None
                    and message.startswith("报审表中识别到总监理工程师意见及指挥部意见栏目")
                    and refs and all(isinstance(ref, dict) and type(ref.get("material_id")) is int
                                     and type(ref.get("page")) is int and isinstance(ref.get("quote"), str)
                                     and ref["quote"] for ref in refs)):
                key = (item["subject_id"], message,
                       tuple(sorted((ref["material_id"], ref["page"], ref["quote"]) for ref in refs)))
                if key in seen_signature_clues:
                    statement = ""
                else:
                    seen_signature_clues.add(key)
            missing = (item.get("missing") or {}).get("labels") or []
            if grouped_uncertain and item.get("status") == "undecidable" and missing:
                for gap in missing:
                    gap = str(gap).strip()
                    if gap and gap not in grouped_gaps:
                        grouped_gaps.append(gap)
            elif (group_plain_uncertain and item.get("status") == "undecidable" and not missing
                  # 日期是具体可核对事实，不能因待核事项数量较多而折叠掉。
                  and not (item.get("kind") == "cert_validity" and item.get("item_id") == "A2-02"
                           and re.search(r"\d{4}-\d{2}-\d{2}", message))
                  and not str((item.get("human_disposition") or {}).get("note") or "").strip()):
                if item_label not in unresolved_labels:
                    unresolved_labels.append(item_label)
            elif statement and statement not in statements:
                statements.append(statement)
            suggestion = " ".join(str(item.get("recommendation") or "").split()).rstrip("。； ")
            if (suggestion == "补充或核实与该项相关的原始资料，完成确认后重新核查"
                    and statement in statements
                     and any(action in message for action in ("需核对", "请核对", "结合原件核实", "需人工核实",
                                                               "人工核对原件", "请补充"))):
                suggestion = ""
            if suggestion and len(suggestion) <= 200 and suggestion not in suggestions and suggestion not in message:
                suggestions.append(suggestion)
        if statements:
            parts.append("；".join(statements))
        if grouped_gaps:
            parts.append("相关事项因以下资料或事实不足尚无法判断：" + "、".join(grouped_gaps[:4])
                         + ("等相关资料或事实" if len(grouped_gaps) > 4 else ""))
        if unresolved_labels:
            parts.append("尚需核实：" + "、".join(unresolved_labels[:3])
                         + ("等事项" if len(unresolved_labels) > 3 else ""))
        if suggestions:
            parts.append("；".join(suggestions))
    if notes:
        parts.append("人工补充：" + "；".join(notes))
    if any(item.get("kind") == "signature_chain" and _SEAL_SOURCE_LIMIT in str(item.get("source") or "")
           for item in items):
        parts.append("签章情况需结合原件核实")
    if not complete:
        parts.append("本类核查尚未完成")
    return "；".join(parts) + "。"


def _cross_stage_reference(item: dict, category: str, point: str) -> dict:
    """跨阶段只引用主要归属，避免重复问题原文和人工补充。"""
    if item.get("status") == "miss":
        return item
    return {**item, "message": "详见%s中的%s" % (category, point),
            "recommendation": None, "missing": None, "human_disposition": None}


def _field_matches(field: str, item: dict, policy=None) -> bool:
    kind = item.get("kind")
    item_id = str(item.get("item_id") or "")
    label = str(item.get("label") or "")
    if policy is not None and "bindings" in policy and not (kind == "cert_validity" and item_id == "A2-02"):
        return any(row["kind"] == kind and row["field"] == field
                   and ("*" in row["item_ids"] or item_id in row["item_ids"])
                   for row in policy["bindings"])
    if kind == "signature_chain":
        return field == "签字盖章及审批资料"
    if kind == "completeness":
        if item_id == "RC-08":
            return field == "报审资料"
        if item_id in {"RC-A02", "RC-A03", "RC-A04", "RC-A05", "RC-A06"}:
            return field == "主体资料"
        if item_id == "RC-A01":
            return field == "合同及清单"
        if item_id in {"RC-09", "RC-A10"}:
            return field == "签字盖章及审批资料"
        if item_id in {"RC-A07", "RC-A08", "RC-A09", "RC-P11", "RC-P12", "RC-P13",
                       "RC-P14", "RC-P15", "RC-P16", "RC-L11", "RC-L12", "RC-L13", "RC-L14"}:
            return field == "人员资料"
        return False
    fixed = {"qualification_match": "分包性质与资质匹配", "negative_list": "拟分包内容与负面清单",
             "transfer_scope": "转包与违法分包情形", "illegal_subcontract": "转包与违法分包情形",
             "a1_dependency": "审批及责任约定"}
    if kind in fixed:
        return field == fixed[kind]
    if kind == "person_consistency":
        return field == "劳动关系及社保"
    if kind == "cert_validity":
        if item_id in {"A2-03", "A2-05"}:
            return field == "证件信息一致性"
        if item_id == "A2-02" and item.get("status") in {"hit", "undecidable"}:
            names = re.findall(r"「([^」]+)」", str(item.get("message") or ""))
            if len(names) == 1:
                name = names[0]
                if name in {"营业执照", "资质证书", "安全生产许可证", "施工劳务备案证书"}:
                    return field == "企业证照有效期"
                if name in {"特种作业操作证", "安全生产考核合格证"}:
                    return field == "人员证件有效期"
        if item_id == "A2-02" and any(word in label for word in ("企业", "营业", "资质", "安全生产许可证")):
            return field == "企业证照有效期"
        if item_id == "A2-02" and any(word in label for word in ("人员", "身份证", "操作证", "岗位")):
            return field == "人员证件有效期"
        return False
    if kind == "internal_records":
        return field == "内部记录核查情况"
    return False


def _complete_groups(coverage: dict) -> set[str]:
    return set(coverage.get("completed_groups") or [])


def _selected_groups(coverage: dict) -> set[str]:
    return set(coverage.get("selected_groups") or [])


def _category_complete(kinds: set[str], selected: set[str], completed: set[str],
                       items: list[dict]) -> bool:
    relevant = {kind for kind in kinds if _kind_selected(kind, selected)}
    if not relevant:
        return False
    if any(not _kind_selected(kind, selected) for kind in kinds):
        return False
    return all(_kind_completed(kind, completed) for kind in relevant)


def _kind_selected(kind: str, selected: set[str]) -> bool:
    group = "a2" if kind in {"qualification_match", "person_consistency", "cert_validity"} else (
        "a1" if kind in {"negative_list", "transfer_scope", "illegal_subcontract", "a1_dependency"}
        else kind)
    return group in selected


def _kind_completed(kind: str, completed: set[str]) -> bool:
    group = "a2" if kind in {"qualification_match", "person_consistency", "cert_validity"} else (
        "a1" if kind in {"negative_list", "transfer_scope", "illegal_subcontract", "a1_dependency"}
        else kind)
    return group in completed


def _field_groups(title: str, field: str) -> set[str]:
    if title == "要素完整性核查":
        groups = {"completeness", "signature_chain"} if field == "签字盖章及审批资料" else {"completeness"}
    elif title == "资质与内容合规性审查":
        groups = {"a2"} if field == "分包性质与资质匹配" else {"a1"}
    elif title == "关键岗位人员核查":
        groups = {"completeness"} if field in {"主要人员及岗位", "特殊工种人员"} else {"a2"}
    elif title == "证件证照有效性核查":
        groups = {"a2"}
    else:
        groups = {"internal_records"}
    return groups


def _field_selected(title: str, field: str, selected: set[str]) -> bool:
    return bool(_field_groups(title, field).intersection(selected))


def _pending_checklist_labels(report: dict, title: str, field: str, policy=None) -> list[str]:
    if "completeness" not in _field_groups(title, field):
        return []
    labels = []
    for subject in (report.get("checklist_summary") or {}).get("subjects") or []:
        for row in subject.get("checklist_items") or []:
            if row.get("status") != "pending":
                continue
            item = {**row, "kind": "completeness"}
            if _field_matches(field, item, policy):
                label = str(row.get("label") or "适用资料")
                if label not in labels:
                    labels.append(label)
    return labels


def _checklist_scope_unavailable(report: dict, title: str, field: str, items: list[dict], policy=None) -> bool:
    if "completeness" not in _field_groups(title, field):
        return False
    summary = report.get("checklist_summary")
    if not isinstance(summary, dict):
        return True
    subjects = summary.get("subjects")
    if not isinstance(subjects, list) or not subjects:
        return True
    projected = {(item.get("subject_id"), item.get("item_id"), item.get("finding_id"))
                 for item in items if item.get("kind") == "completeness"}
    for subject in subjects:
        if (not isinstance(subject, dict) or subject.get("status") != "ready"
                or not isinstance(subject.get("checklist_items"), list)):
            return True
        for row in subject["checklist_items"]:
            if not isinstance(row, dict):
                return True
            if row.get("status") == "pending" or not _field_matches(field, {**row, "kind": "completeness"}, policy):
                continue
            key = (subject.get("subject_id"), row.get("item_id"), row.get("finding_id"))
            if row.get("finding_id") is None or key not in projected:
                return True
    return False


def _point_ai_incomplete(ai_coverage, groups: set[str]) -> bool:
    """仅使用可确认归属的 AI 分组；覆盖结构不完整时保持保守。"""
    if ai_coverage is None:
        return False
    if not isinstance(ai_coverage, dict) or ai_coverage.get("state") != "available":
        return True
    rows = ai_coverage.get("groups")
    known = {"a1", "a2", "completeness", "signature_chain", "internal_records"}
    if not isinstance(rows, list) or any(not isinstance(row, dict) or row.get("group_id") not in known for row in rows):
        return True
    if len({row["group_id"] for row in rows}) != len(rows):
        return True
    for row in rows:
        if row["group_id"] not in groups:
            continue
        counts = row.get("counts")
        required = {"not_started", "pending", "completed", "partial", "failed", "not_run"}
        if (row.get("state") != "available" or not isinstance(counts, dict) or not required.issubset(counts)
                or any(type(counts[key]) is not int or counts[key] < 0 for key in required)):
            return True
        skipped = row.get("skipped")
        if type(skipped) is not int or skipped < 0 or skipped:
            return True
        if any(counts[state] for state in ("not_started", "pending", "partial", "failed", "not_run")):
            return True
    return False


def _merge_duplicate_issues(items: list[dict]) -> list[dict]:
    """Only merge identical statements with a confirmed subject and source location."""
    merged = []
    by_key = {}
    for item in items:
        refs = item.get("evidence") or []
        subject_id = item.get("subject_id")
        locations = tuple(sorted({(str(ref.get("material_id")), ref.get("page"), str(ref.get("quote") or ""))
                                  for ref in refs if isinstance(ref, dict)
                                  and ref.get("material_id") is not None and type(ref.get("page")) is int
                                  and ref.get("page") > 0}))
        disposition = item.get("human_disposition") or {}
        key = (subject_id, item.get("category"), item.get("label"), item.get("status"),
               item.get("message"), item.get("basis"), item.get("recommendation"),
               disposition.get("code"), disposition.get("note"), locations,
               item.get("material_missing") is True,
               tuple(sorted(str(label) for label in ((item.get("missing") or {}).get("labels") or []))))
        finding_id = item.get("finding_id")
        target = by_key.get(key) if subject_id is not None and locations and finding_id is not None else None
        if target is not None:
            if finding_id not in target["finding_ids"]:
                target["finding_ids"].append(finding_id)
            if item.get("item_id") not in target["item_ids"]:
                target["item_ids"].append(item.get("item_id"))
            continue
        row = {**item, "finding_ids": [finding_id] if finding_id is not None else [],
               "item_ids": [item["item_id"]] if item.get("item_id") else []}
        merged.append(row)
        if subject_id is not None and locations and finding_id is not None:
            by_key[key] = row
    return merged


def summarize_report_issues(issues: list[dict]) -> tuple[list[dict], list[dict]]:
    """Summarize by category and status without changing the underlying findings."""
    grouped = {}
    notes = []
    for issue in issues:
        disposition = issue.get("human_disposition") or {}
        code = disposition.get("code")
        if code == "ignored":
            continue
        category = str(issue.get("category") or "其他核查")
        label = str(issue.get("label") or "核查事项")
        if code == "accepted":
            notes.append({"category": category, "label": label,
                          "note": str(disposition.get("note") or "未填写补充说明")})
            continue
        explicit_missing = issue.get("status") == "hit" and issue.get("material_missing") is True
        status = "明确缺少材料" if explicit_missing else ("需关注" if issue.get("status") == "hit" else "待核实")
        missing_labels = (issue.get("missing") or {}).get("labels") or [] if explicit_missing else []
        fallback = label if explicit_missing else (issue.get("summary_theme") or label)
        themes = [str(value) for value in missing_labels if value] or [str(fallback)]
        labels = grouped.setdefault((category, status), [])
        for theme in themes:
            if theme not in labels:
                labels.append(theme)
    pending = [{"category": category, "status": status,
                "text": "%s：%s。" % (status, "、".join(labels))}
               for (category, status), labels in grouped.items()]
    return pending, notes


def _summary_theme(item: dict, policy=None) -> str:
    for _title, kinds, fields in _CATEGORIES:
        if item.get("kind") in kinds:
            return next((field for field in fields if _field_matches(field, item, policy)),
                        str(item.get("label") or "核查事项"))
    return str(item.get("label") or "核查事项")


def build_report_document(report: dict) -> dict:
    """Create the confirmed business report while preserving machine audit data."""
    basic = report.get("basic_information") or {}
    meta = report.get("meta") or {}
    coverage = report.get("coverage") or {}
    reference = report.get("reference_snapshot") or {}
    policy = report_policy.read_frozen_policy(reference["report_policy"]) if "report_policy" in reference else None
    scope_limitations = [str(value) for value in report.get("scope_limitations") or [] if value]
    scope_limited = bool((report.get("scope_coverage") or {}).get("state") == "partial"
                         and scope_limitations)
    selected, completed = _selected_groups(coverage), _complete_groups(coverage)
    subject_types = {row.get("subject_id"): row.get("subject_type")
                     for row in (report.get("checklist_summary") or {}).get("subjects") or []
                     if row.get("subject_id") is not None and row.get("status") == "ready"
                     and row.get("type_confirmed") is True and not row.get("type_conflict")
                     and row.get("subject_type") in {"labor", "professional"}}
    reading_incomplete = any(
        isinstance(row, dict) and (row.get("summary_text") or row.get("unread_pages") or row.get("suspect_pages"))
        for row in report.get("reading_coverage") or []
    )
    ai_coverage = report.get("ai_coverage")
    ai_incomplete = bool(isinstance(ai_coverage, dict) and (
        ai_coverage.get("state") != "available" or any(
            group.get("state") != "available" or any(
                (group.get("counts") or {}).get(state, 0)
                for state in ("not_started", "pending", "partial", "failed", "not_run"))
            for group in ai_coverage.get("groups") or [])))
    all_items = [dict(item, kind=section.get("kind"), category=_KIND_GROUP.get(section.get("kind"), section.get("title")))
                 for section in report.get("sections") or []
                 for item in section.get("items") or []]
    visible_items = [item for item in all_items
                     if not (item.get("human_disposition") or {}).get("code") == "ignored"
                     and (item.get("review") or {}).get("decision") != "ignored"
                     and not item.get("open_term")
                     and item.get("status") in {"hit", "undecidable"}]
    categories = []
    for category_index, (title, kinds, fields) in enumerate(_CATEGORIES):
        selected_kinds = {kind for kind in kinds if _kind_selected(kind, selected)}
        rows = [item for item in visible_items if item.get("kind") in kinds]
        category_items = [item for item in all_items if item.get("kind") in kinds
                          and not item.get("open_term") and item.get("status") in {"hit", "miss", "undecidable"}]
        applicable = bool(selected_kinds) and all(_kind_selected(kind, selected) for kind in kinds)
        required_groups = {"a1" if kind in {"negative_list", "transfer_scope", "illegal_subcontract", "a1_dependency"}
                           else "a2" if kind in {"qualification_match", "person_consistency", "cert_validity"}
                           else kind for kind in selected_kinds}
        complete = applicable and bool(required_groups) and all(group in completed for group in required_groups)
        active_category_items = [item for item in category_items
                                 if (item.get("human_disposition") or {}).get("code") != "ignored"
                                 and (item.get("review") or {}).get("decision") != "ignored"]
        by_label = {field: [row for row in active_category_items if _field_matches(field, row, policy)] for field in fields}
        if title == "关键岗位人员核查":
            supporting = [item for item in all_items if not item.get("open_term")
                          and item.get("status") in {"hit", "miss", "undecidable"}
                          and (item.get("human_disposition") or {}).get("code") != "ignored"
                          and (item.get("review") or {}).get("decision") != "ignored"]
            by_label["主要人员及岗位"].extend(_cross_stage_reference(item, "要素完整性核查", "人员资料") for item in supporting
                if item.get("kind") == "completeness" and item.get("item_id") == "RC-A09")
            by_label["特殊工种人员"].extend(_cross_stage_reference(item, "要素完整性核查", "人员资料") for item in supporting
                if item.get("kind") == "completeness" and item.get("item_id") in {"RC-P16", "RC-L14"})
            by_label["人员与证件对应"].extend(_cross_stage_reference(item, "证件证照有效性核查", "证件信息一致性") for item in supporting
                if item.get("kind") == "cert_validity" and item.get("item_id") == "A2-05")
        summaries = {field: _field_summary(field, by_label[field], complete) for field in fields}
        if title == "资质与内容合规性审查" and any(
                item.get("item_id") == "A2-04" for item in by_label["分包性质与资质匹配"]):
            field = "分包性质与资质匹配"
            summaries[field] = summaries[field].replace("已核查符合：", "已完成初步文字比对：")
            summaries[field] += "该比对未核定资质等级、工程规模及证照真实性。"
        if title == "资质与内容合规性审查" and by_label["拟分包内容与负面清单"]:
            summaries["拟分包内容与负面清单"] += "现有结果仅覆盖已识别事项，适用清单覆盖完整性需核实。"
        if title == "关键岗位人员核查":
            field_groups = {"主要人员及岗位": "completeness", "特殊工种人员": "completeness",
                            "人员与证件对应": "a2", "劳动关系及社保": "a2"}
            for field, group in field_groups.items():
                if group not in selected:
                    summaries[field] = "本次未选择相关核查。"
                else:
                    summaries[field] = _field_summary(field, by_label[field], group in completed)
        if title == "证件证照有效性核查":
            summaries["证照真实性及核验情况"] = "当前报告未提供可单独归纳的原件或官方真实性核验结果。"
        if title == "内部信用与事故材料核查":
            summaries["核查范围及限制"] = "本项依据本次提供的内部材料，未开展外部信用渠道查询。"
        displayed_fields = {source for _label, source in _KEY_POINTS[category_index]}
        disclosed_ai_groups = set()
        for field in fields:
            if not _field_selected(title, field, selected):
                summaries[field] = "本次未选择，未开展该项核查。"
            else:
                pending_labels = _pending_checklist_labels(report, title, field, policy)
                if pending_labels:
                    summaries[field] += "适用资料%s尚未完成核查。" % "、".join(pending_labels)
                if _checklist_scope_unavailable(report, title, field, by_label[field], policy):
                    summaries[field] += "主体或适用资料范围尚未确认。"
                ai_groups = frozenset(_field_groups(title, field))
                if (field in displayed_fields and (ai_groups not in disclosed_ai_groups
                        or _proven_required_material_missing(by_label[field], policy, subject_types, meta.get("baseline_date")))
                        and _point_ai_incomplete(ai_coverage, set(ai_groups))):
                    summaries[field] += "本阶段相关辅助核查未完成或依据关联不足，需结合相关资料核实。"
                    disclosed_ai_groups.add(ai_groups)
        summary = _status_sentence(rows, complete, bool(category_items))
        if selected_kinds and reading_incomplete:
            if complete and category_items and not rows:
                summary = "当前已形成的核查结果中，未发现需要处理的问题。"
            summary += "部分材料读取不完整，影响范围需结合原件核实；详见总体结论。"
        categories.append({
            "title": title,
            "status": "complete" if complete and category_items and not reading_incomplete else "incomplete" if applicable else "not_selected",
            "summary": summary,
            "fields": [{"label": field, "summary": summaries[field],
                        "basis": _point_basis(by_label[field], subject_types),
                        "verdict": ("—" if (not _field_selected(title, field, selected)
                                             or (title == "内部信用与事故材料核查" and field == "核查范围及限制"))
                            else _point_verdict(by_label[field],
                            _field_groups(title, field).issubset(selected) and _field_groups(title, field).issubset(completed),
                            reading_incomplete,
                            _point_ai_incomplete(ai_coverage, _field_groups(title, field))
                            or bool(_pending_checklist_labels(report, title, field, policy))
                            or _checklist_scope_unavailable(report, title, field, by_label[field], policy),
                            policy, subject_types, meta.get("baseline_date"))),
                        "status": _key_point_status(by_label[field],
                            ((field_groups.get(field) in completed) if title == "关键岗位人员核查" and field in field_groups else complete),
                            reading_incomplete)}
                       for field in fields],
            "issues": rows,
        })
    for index, section in enumerate(categories):
        fields = {field["label"]: field for field in section["fields"]}
        section["key_points"] = [{"label": label, "summary": _key_point_summary(fields[source]["summary"]),
                                  "status": fields[source]["status"], "basis": fields[source]["basis"],
                                  "verdict": fields[source]["verdict"]}
                                 for label, source in _KEY_POINTS[index]]
        title, kinds, field_names = _CATEGORIES[index]
        unmapped = [item for item in visible_items if item.get("kind") in kinds
                    and not any(_field_matches(field, item, policy) for field in field_names)]
        if unmapped:
            section["key_points"].append({"label": "其他核查事项",
                "summary": _field_summary("其他核查事项", unmapped, section["status"] == "complete"),
                "status": _key_point_status(unmapped, section["status"] == "complete", reading_incomplete),
                "basis": _point_basis(unmapped, subject_types),
                "verdict": _point_verdict(unmapped, section["status"] == "complete", reading_incomplete or ai_incomplete)})
    # 配置开启的其他核查结果仍需展示，不能随待处理章节删除而丢失；不扩充核查范围。
    other_items = [item for item in visible_items if item.get("kind") not in _KIND_GROUP]
    if other_items:
        categories[0]["key_points"].append({"label": "其他已执行核查事项",
            "summary": _field_summary("其他核查事项", other_items, False),
            "status": "待核实", "basis": _point_basis(other_items, subject_types), "verdict": "待核实"})
    subcontract_type = basic.get("subcontract_type")
    type_summary = ("当前识别类型：%s；事项性质及适用要求需结合合同核实。" % subcontract_type
                    if isinstance(subcontract_type, str) and subcontract_type.strip()
                    else "事项类型及性质待核实。")
    type_selected = bool({"a1", "a2"}.intersection(selected))
    categories[1]["key_points"].insert(0, {"label": "事项定性",
        "summary": type_summary if type_selected else "本次未选择，未开展该项核查。",
        "status": "待核实", "basis": [], "verdict": "待核实" if type_selected else "—"})
    issues = _merge_duplicate_issues([{
        "category": item.get("category") or item.get("kind"),
        "label": item.get("label") or item.get("item_id"),
        "summary_theme": _summary_theme(item, policy),
        "status": item.get("status"),
        "material_missing": item.get("material_missing") is True,
        "missing": item.get("missing"),
        "message": item.get("message"),
        "basis": item.get("source"),
        "recommendation": item.get("recommendation"),
        "human_disposition": item.get("human_disposition"),
        "evidence": item.get("evidence_list") or ([item["evidence"]] if item.get("evidence") else []),
        "item_id": item.get("item_id"),
        "finding_id": item.get("finding_id"),
        "subject_id": item.get("subject_id") or (item.get("subject") or {}).get("subject_id"),
    } for item in visible_items])
    pending_summaries, human_notes = summarize_report_issues(issues)
    if not coverage.get("complete") or ai_incomplete or reading_incomplete or any(row["status"] == "incomplete" for row in categories):
        conclusion_status = "incomplete"
        conclusion = "本次核查尚未覆盖全部适用范围或结果不足，当前结论不完整。"
        if issues:
            conclusion += "当前已有需关注或待核实事项，详见待处理事项摘要及人工意见。"
    elif issues:
        conclusion_status = "attention"
        conclusion = "本次核查发现需关注或待核实事项，详见待处理事项摘要及人工意见。"
    elif scope_limited:
        conclusion_status = "scope_limited"
        conclusion = ("在本次已覆盖核查范围内，未发现需要处理的问题；部分规则本轮未覆盖，"
                      "相关规则未形成符合或不符合结论，不能解释为完整通过。具体规则见范围限制。")
    else:
        conclusion_status = "no_issue"
        conclusion = "在本次已完成核查范围内，未发现需要处理的问题。"
    for status, label in (("hit", "需关注"), ("undecidable", "待核实")):
        names = list(dict.fromkeys(str(item.get("category") or "核查事项")
                                   for item in issues if item.get("status") == status))
        if names:
            conclusion += "%s：%s%s。" % (label, "、".join(names[:3]), "等" if len(names) > 3 else "")
    scope_limitation_set = set(scope_limitations)
    limitations = list(dict.fromkeys(
        [str(value) for value in report.get("scope_statements") or []
         if str(value) not in scope_limitation_set]
        + [str(row.get("summary_text")) for row in report.get("reading_coverage") or []
           if row.get("summary_text")]
        + [str(row.get("message")) for row in (report.get("limitations") or [])
           if isinstance(row, dict) and row.get("message")]
    ))
    if ai_incomplete:
        limitations.append("部分 AI 辅助核查未完成，相关事项需结合现有结果人工核实。")
    def project_value(key):
        fact = basic.get(key) or {}
        if fact.get("status") == "conflict" and fact.get("source") != "task":
            return None
        return fact.get("value") or meta.get(key)

    information = {
        "工程名称": project_value("project_name"),
        "标段": project_value("contract_section"),
        "施工总承包单位": ((basic.get("general_contractor") or {}).get("company")
                         or ((basic.get("general_contractor") or {}).get("value"))),
        "建设单位（业主）": ((basic.get("employer") or {}).get("company")
                          or ((basic.get("employer") or {}).get("value"))),
        "分包单位": ((basic.get("subcontractor") or {}).get("company")
                    or ((basic.get("subcontractor") or {}).get("value"))),
        "分包类型": basic.get("subcontract_type"),
        "拟分包范围": (basic.get("subcontract_scope") or {}).get("value"),
        "拟分包金额": ("%s%s" % ((basic.get("subcontract_amount") or {}).get("value"),
                               (basic.get("subcontract_amount") or {}).get("unit") or "")
                    if (basic.get("subcontract_amount") or {}).get("value") is not None else None),
        "核查基准日": basic.get("baseline_date") or meta.get("baseline_date"),
        "报告日期": meta.get("generated_at"),
    }
    field_status = {"工程名称": (basic.get("project_name") or {}).get("status"),
                    "标段": (basic.get("contract_section") or {}).get("status"),
                    "施工总承包单位": (basic.get("general_contractor") or {}).get("status"),
                    "建设单位（业主）": (basic.get("employer") or {}).get("status"),
                    "分包单位": (basic.get("subcontractor") or {}).get("status"),
                    "拟分包范围": (basic.get("subcontract_scope") or {}).get("status"),
                    "拟分包金额": (basic.get("subcontract_amount") or {}).get("status")}
    return {"schema_version": "bs.business-report-document.v2",
            "basic_information": information, "basic_information_status": field_status,
            "sections": categories, "issues": issues,
            "scope_limitations": scope_limitations,
            "pending_summaries": pending_summaries, "human_notes": human_notes,
            "conclusion": {"status": conclusion_status, "text": conclusion,
                           "limitations": limitations, "limitation_groups": _limitation_groups(limitations)}}
