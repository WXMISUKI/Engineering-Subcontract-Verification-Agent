"""Business summaries for reports; original findings and decisions remain unchanged."""
from __future__ import annotations

import re
from collections import OrderedDict


def basis_documents(sources):
    """List documents once, retaining clause coordinates only in source findings."""
    titles = []
    for source in sources:
        titles.extend(re.findall(r"《([^》]+)》", str(source or "")))
    full_rules = next((title for title in titles if "公路工程施工分包管理实施细则" in title), None)
    aliases = {"沪交行规〔2026〕8号": full_rules, "实施细则": full_rules}
    output = []
    for title in titles:
        title = aliases.get(title) or title
        if title not in output: output.append(title)
    # Some configured internal documents have no book-title marks.
    for source in sources:
        source = str(source or "")
        if "集团报审表" in source and "集团报审表" not in output: output.append("集团报审表")
        if "业主内部警示名单" in source and "业主内部信用与事故记录" not in output:
            output.append("业主内部信用与事故记录")
    return ["《%s》" % title for title in output]


def reading_affects_item(item, reading_coverage):
    refs = item.get("evidence_list") or ([item["evidence"]] if item.get("evidence") else [])
    gaps = {row.get("material_id"): set(row.get("unread_pages") or []) | set(row.get("suspect_pages") or [])
            for row in reading_coverage if isinstance(row, dict)}
    return any(isinstance(ref, dict) and ref.get("page") in gaps.get(ref.get("material_id"), set()) for ref in refs)


_THEMES = {
    "signature_chain": "审批及签字程序", "qualification_match": "资质与承揽范围",
    "negative_list": "分包负面清单", "transfer_scope": "转包风险",
    "illegal_subcontract": "违法分包与劳务边界", "a1_dependency": "分包责任约定",
    "person_consistency": "管理人员劳动关系与社保", "cert_validity": "人员证件有效性及注册关系",
    "personnel_adequacy": "岗位配置与人员配备",
    "internal_records": "企业信用与事故记录", "contract_semantic": "分包合同条款",
    "amount_ratio": "分包金额", "workload_ratio": "分包工程量",
}


def _theme(item):
    if item.get("kind") == "completeness":
        code = item.get("item_id") or ""
        if code in {"RC-A02", "RC-A03", "RC-A04", "RC-A05", "RC-A06"}: return "企业资格与业绩资料"
        if code == "RC-A01": return "分包工程清单"
        if code == "RC-A10": return "授权委托资料"
        return "人员及履约资料"
    return _THEMES.get(item.get("kind"), item.get("summary_theme") or item.get("label") or "其他核查事项")


def summarize_items(items, verdicts, subject_name):
    groups = OrderedDict()
    verdict_by_identity = {id(item): verdict for item, verdict in zip(items, verdicts)}
    for item, verdict in zip(items, verdicts):
        disposition = item.get("human_disposition") or item.get("review") or {}
        reviewed = disposition.get("code", disposition.get("decision")) == "accepted"
        prompt = item.get("finding_semantics") == "manual_prompt"
        category = ("提示事项" if prompt else "缺项" if item.get("material_missing") is True
                    else "不符合项" if verdict == "不符合" else "符合项" if verdict == "符合"
                    else "已复核保留事项" if reviewed else "证据不足事项")
        theme = _theme(item)
        fact = str(item.get("message") or item.get("label") or theme).strip()
        disposition_note = str(disposition.get("note") or "").strip()
        key = (item.get("subject_id"), category, theme, fact,
               disposition.get("code", disposition.get("decision")), disposition_note,
               str(item.get("recommendation") or "").strip())
        row = groups.setdefault(key, {"category": category, "theme": theme, "fact": fact, "members": []})
        row["members"].append(item)
    rows = []
    for row in groups.values():
        members, category, theme, fact = row["members"], row["category"], row["theme"], row["fact"]
        labels = list(dict.fromkeys(str(label) for item in members
                                   for label in ((item.get("missing") or {}).get("labels") or [item.get("label") or theme])))
        reviewed = sum((item.get("human_disposition") or {}).get("code") == "accepted"
                       or (item.get("review") or {}).get("decision") == "accepted" for item in members)
        if category == "缺项":
            fact = str(members[0].get("message") or "当前核查记录列示资料缺项：%s。" % "、".join(labels)).strip()
            recommendation = "补齐上述资料，核对原件及本项目、本分包单位归属。"
        elif category == "符合项":
            fact = str(members[0].get("message") or "%s已有核查证据支持。" % theme).strip()
            recommendation = "保留对应材料及核查记录。"
        elif category == "不符合项":
            recommendation = "；".join(dict.fromkeys(str(item.get("recommendation") or "按上述问题补正后复核。") for item in members))
        else:
            if not fact:
                fact = "%s：本项尚未形成机器结论，需结合所列材料核对。" % theme
            recommendation = "结合本项目%s材料补充说明或核对原件，保留人工复核结果。" % theme
        notes = list(dict.fromkeys(str((item.get("human_disposition") or item.get("review") or {}).get("note") or "").strip()
                                   for item in members))
        for note in notes:
            if note and (category == "提示事项" or reviewed):
                fact += " 人工复核说明：%s。" % note.rstrip("。")
        location_pages = OrderedDict()
        for item in members:
            refs = item.get("evidence_list") or ([item["evidence"]] if item.get("evidence") else [])
            for ref in refs:
                if not isinstance(ref, dict) or type(ref.get("page")) is not int or ref["page"] <= 0:
                    continue
                location_pages.setdefault(ref.get("file_name") or "材料", set()).add(ref["page"])
        locations = ["%s第%s页" % (name, "、".join(str(page) for page in sorted(pages)))
                     for name, pages in location_pages.items()]
        if locations:
            fact += " 材料定位：%s。" % "、".join(locations)
        responsibility = ("承包人" if members[0].get("kind") in {"signature_chain", "negative_list", "transfer_scope", "a1_dependency"}
                          else "承包人" if members[0].get("kind") == "illegal_subcontract"
                          else "分包人")
        rows.append({"number": len(rows) + 1, "category": category, "theme": theme, "fact": fact,
                     "recommendation": recommendation, "responsible_party": responsibility,
                     "finding_ids": [item["finding_id"] for item in members if item.get("finding_id") is not None],
                     "item_ids": list(dict.fromkeys(item["item_id"] for item in members if item.get("item_id"))),
                     "count": len(members), "basis": basis_documents([item.get("source") for item in members]),
                     "verdicts": [verdict_by_identity[id(item)] for item in members],
                     "subject_id": members[0].get("subject_id")})
    return rows


def category_summaries(groups):
    output = []
    for category in ("缺项", "符合项", "不符合项", "已复核保留事项", "证据不足事项", "提示事项"):
        selected = [row for row in groups if row["category"] == category
                    or category == "不符合项" and "不符合" in row["verdicts"]]
        if selected:
            themes = "、".join(dict.fromkeys(row["theme"] for row in selected))
            count = (sum(row["verdicts"].count("不符合") for row in selected)
                     if category == "不符合项" else sum(row["count"] for row in selected))
            output.append({"category": category, "text": "%s：涉及%s，共%s项，归并为%s个主题；概要及处理要求见附件二。" %
                           (category, themes, count, len(set(row["theme"] for row in selected)))})
    return output
