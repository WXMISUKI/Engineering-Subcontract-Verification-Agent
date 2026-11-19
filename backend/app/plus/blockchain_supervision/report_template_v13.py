"""V1.3 只读报告投影；不执行规则，不产生审批决定。"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import re

from . import report_document, report_policy, report_content_summary
from .business_report import BUSINESS_MODULES

TEMPLATE_ID = "v1.3"
SCHEMA_VERSION = "bs.review-opinion-document.v1"
MAPPING_VERSION = "2026-11-17.4"
UNKNOWN = "材料未载明"
_TEMPLATE_BASIS = (
    "《中华人民共和国建筑法》",
    "《建设工程质量管理条例》（国务院令第279号）",
    "《建设工程安全生产管理条例》（国务院令第393号）",
    "《公路建设市场管理办法》《公路水运工程安全生产监督管理办法》",
    "《公路工程施工分包管理办法》《公路工程施工分包负面清单》",
    "《保障农民工工资支付条例》（国务院令第724号）",
    "上海市公路建设市场及分包管理现行规定",
    "项目招标文件、总承包合同专用条款及发包人分包管理与报审程序",
)
_DIMENSION_POINTS = {
    "compliance": {"审批程序", "负面清单", "再分包与挂靠", "审批及责任约定"},
    "qualification_credit": {"主体资料", "主体资格", "企业证照有效期", "证照真实性", "内部信用与事故记录", "核查范围"},
    "personnel": {"人员资料", "关键岗位", "人员与证件对应", "劳动关系社保", "特殊工种", "人员证件有效期"},
    "contract_clauses": {"合同及清单"},
    "quality_safety_progress": {"审批及责任约定"},
}
_COMPLETENESS_DIMENSIONS = {
    "主体资料": "qualification_credit",
    "人员资料": "personnel",
    "合同及清单": "contract_clauses",
    "签字盖章及审批资料": "compliance",
}


def _text(value):
    return str(value).strip() if value not in (None, "") else UNKNOWN


def _basis_key(value):
    return re.sub(r"[《》\s]", "", str(value or ""))


def _merge_basis_documents(actual):
    """Keep the reference template basis and append deduplicated task sources."""
    merged = list(_TEMPLATE_BASIS)
    keys = {_basis_key(value) for value in merged}
    for value in actual:
        value = str(value or "").strip()
        if not value:
            continue
        key = _basis_key(value)
        if key and key not in keys:
            merged.append(value if value.startswith("《") else "《%s》" % value)
            keys.add(key)
    return merged


def _item_text(info):
    item = str(info.get("分包事项") or "分包").strip()
    subcontract_type = str(info.get("分包类型") or "").strip()
    if item in {"分包", "工程分包"}:
        return subcontract_type or item
    if not subcontract_type or item.endswith(subcontract_type):
        return item
    base_type = subcontract_type[:-2] if subcontract_type.endswith("分包") else subcontract_type
    if base_type and item.endswith(base_type):
        return item + "分包"
    return item + subcontract_type


def _dimension_summary(dimensions):
    counts = {status: sum(row.get("verdict") == status for row in dimensions)
              for status in ("符合", "不符合", "缺项")}
    unclassified = sum(row.get("verdict") == "—" for row in dimensions)
    values = ["%s个维度%s" % (counts[status], status)
              for status in ("符合", "不符合", "缺项") if counts[status]]
    if unclassified:
        values.append("%s个维度未形成初审意见" % unclassified)
    return "、".join(values) or "暂未形成初审意见"


def _scope_limitations(report, reading_coverage):
    """Turn internal scope/OCR diagnostics into short, reader-facing boundaries."""
    limits = []
    for value in report.get("scope_limitations") or []:
        value = str(value or "").strip()
        if value and "负面清单 AI 辅助候选未覆盖规则" not in value:
            limits.append(value)

    statements = [str(value or "").strip() for value in report.get("scope_statements") or []
                  if str(value or "").strip()
                  and "负面清单 AI 辅助候选未覆盖规则" not in str(value)]
    source_limits = [value for value in statements if "部分参考依据" in value]
    credit_limits = [value for value in statements if "内部信用" in value or "外部网站" in value]
    if source_limits or credit_limits:
        limits.append(
            "依据与外部核验边界：部分参考依据未取得或查询不完整；企业信用与事故仅核对已提供的内部材料，"
            "未查询外部网站，缺少资料不代表无风险。"
        )

    contract_limits = [value for value in statements
                       if "未上传总包合同" in value or "未提供总包合同" in value]
    ratio_limits = [value for value in statements if "金额占比" in value or "工程量占比" in value]
    semantic_limits = [value for value in statements if "合同语义核查" in value]
    if contract_limits:
        limits.append("合同核查边界：未上传总包合同，需依据该合同原文判断的事项尚未核查。")
    if ratio_limits or semantic_limits:
        limits.append("合同及比例核查边界：分包金额占比、工程量占比及合同语义核查本次未启用。")

    qualification_limits = [value for value in statements if "资质仅按工程类别" in value]
    if qualification_limits:
        limits.append(
            "资质边界：本次仅按工程类别文字初筛，资质等级与规模、同人同岗、实际到岗及证照真实性尚未自动核验。"
        )

    consumed = set(source_limits + credit_limits + contract_limits + ratio_limits
                   + semantic_limits + qualification_limits)
    limits.extend(value for value in statements if value not in consumed)

    gaps = report.get("ai_reading_gap_scope") or []
    for gap in gaps:
        if not isinstance(gap, dict):
            continue
        pages = gap.get("pages") or []
        rule_ids = gap.get("rule_ids") or []
        page_text = "、".join(str(page) for page in pages) or "待核实"
        if rule_ids:
            if len(rule_ids) == 20 and rule_ids[:12] == ["NL-B-%02d" % i for i in range(1, 13)]:
                rule_text = "NL-B-01至NL-B-12、NL-T-01至NL-T-08"
            else:
                rule_text = "、".join(str(rule_id) for rule_id in rule_ids)
        else:
            rule_text = "相关负面清单规则"
        limits.append(
            "负面清单说明：负面清单用于判断拟分包事项是否属于禁止分包的主体工程或关键性工作；"
            "NL-B对应桥梁工程情形，NL-T对应隧道工程情形。本次AI辅助候选未覆盖%s，材料第%s页识别不完整，"
            "相关规则未形成符合或不符合结论；这不表示已认定存在违规。"
            % (rule_text, page_text)
        )
        break

    for coverage in reading_coverage:
        if not isinstance(coverage, dict):
            continue
        pages = coverage.get("suspect_pages") or coverage.get("unread_pages") or []
        if not pages:
            continue
        file_name = coverage.get("file_name") or "报审材料"
        page_text = "、".join(str(page) for page in pages)
        limits.append(
            "OCR质量提示：%s第%s页文字识别质量可疑，相关内容只能作为候选依据，不能替代原件核对；"
            "建议补传清晰的逐页扫描件（建议300dpi以上、页面正向、无阴影和裁切），或人工核对原件后重新核查。"
            % (file_name, page_text)
        )
        break
    return limits


def _active(item):
    return ((item.get("review") or {}).get("decision") != "ignored"
            and (item.get("human_disposition") or {}).get("code") != "ignored")


def _group(kind):
    if kind in {"qualification_match", "person_consistency", "cert_validity"}:
        return "a2"
    if kind in {"negative_list", "transfer_scope", "illegal_subcontract", "a1_dependency"}:
        return "a1"
    return kind


def _page_count(report, subject_id):
    materials = report.get("submission_materials") or []
    materials = [row for row in materials if row.get("role") != "general"
                 and row.get("subject_id") in {None, subject_id}]
    by_id = {row["material_id"]: row for row in materials if row.get("material_id") is not None}
    if by_id and all(type(row.get("pages")) is int and row["pages"] > 0 for row in by_id.values()):
        return sum(row["pages"] for row in by_id.values())
    return None


def _submitted_date(value):
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    try:
        parsed = date.fromisoformat(value[:10])
    except ValueError:
        match = re.fullmatch(r"(\d{1,2})月(\d{1,2})日", value)
        return "%s月%s日" % match.groups() if match else None
    return "%s月%s日" % (parsed.month, parsed.day)


def _dimension_verdict(selected, mapped, completed, *, has_completeness=False):
    verdicts = ["缺项" if item.get("material_missing") is True and verdict == "不符合" else verdict
                for item, verdict in selected]
    if "不符合" in verdicts:
        return "不符合"
    if "缺项" in verdicts:
        return "缺项"
    required_groups = {_group(kind) for kind in mapped}
    if has_completeness:
        required_groups.add(_group("completeness"))
    if (not selected or not required_groups or any(v != "符合" for v in verdicts)
            or not required_groups.issubset(completed)
            or any(item.get("scope_limited") for item, _ in selected)):
        return "—"
    return "符合"


def build_document(report: dict) -> dict:
    """Freeze a self-contained presentation from this report's facts only."""
    subjects = (report.get("checklist_summary") or {}).get("subjects") or []
    if len(subjects) != 1:
        return {"template_id": TEMPLATE_ID, "available": False,
                "reason": "V1.3 第一版仅支持单分包主体任务；请继续使用旧模板。"}
    meta = report.get("meta") or {}
    basic = report.get("basic_information") or {}
    legacy = report.get("business_document") or {}
    information = deepcopy(legacy.get("basic_information") or {})
    for field, status in (legacy.get("basic_information_status") or {}).items():
        if status == "conflict":
            information[field] = None
    information.pop("报告日期", None)
    submitted = (basic.get("submitted_date") or {}).get("value")
    pages = _page_count(report, subjects[0].get("subject_id"))
    if submitted: information["报送日期"] = submitted
    if pages is not None: information["材料页数"] = "%s页" % pages
    item_fact = basic.get("subcontract_item_name") or {}
    if item_fact.get("status") == "matched": information["分包事项"] = item_fact.get("value")
    scope_name = information.get("拟分包范围")
    # 当前没有独立事项短名；长范围留在基本情况，避免把整段合同范围放进标题。
    item_name = information.get("分包事项") or (scope_name if isinstance(scope_name, str) and len(scope_name) <= 30 else "工程分包")
    item_name = re.sub(r"(?:劳务|专业)(?:分包)?$", "", item_name)
    title = ("%s%s审查意见（AI初审）" % (item_name, information.get("分包类型") or "")
             if item_name != "工程分包" else "工程分包审查意见（AI初审）")
    items = [dict(item, kind=section.get("kind"), subject_id=item.get("subject_id") or (item.get("subject") or {}).get("subject_id")) for section in report.get("sections") or []
             for item in section.get("items") or [] if _active(item)]
    machines = [item for item in items if not item.get("open_term")
                and item.get("finding_semantics") != "manual_prompt"
                and item.get("status") in {"hit", "miss", "undecidable"}]
    reference = report.get("reference_snapshot") or {}
    policy = report_policy.read_frozen_policy(reference["report_policy"]) if "report_policy" in reference else None
    subject_types = {s.get("subject_id"): s.get("subject_type") for s in subjects
                     if s.get("status") == "ready" and s.get("type_confirmed") is True
                     and not s.get("type_conflict")}
    completed = set((report.get("coverage") or {}).get("completed_groups") or [])
    reading_coverage = report.get("reading_coverage") or []
    verdicts = [report_document._point_verdict(
        [{**item, "reading_limited": report_content_summary.reading_affects_item(item, reading_coverage)}],
        _group(item.get("kind")) in completed,
        report_content_summary.reading_affects_item(item, reading_coverage), bool(item.get("scope_limited")),
        policy, subject_types, meta.get("baseline_date")) for item in machines]
    counts = {label: verdicts.count(label) for label in ("符合", "待核实", "不符合")}
    # A confirmed applicable-material gap is a report category in its own right;
    # legacy nonconformity policy covers only a subset of required documents.
    dimension_verdicts = ["缺项" if item.get("material_missing") is True and item.get("status") == "hit"
                         and _group(item.get("kind")) in completed and not item.get("scope_limited")
                         and not report_content_summary.reading_affects_item(item, reading_coverage)
                         else verdict for item, verdict in zip(machines, verdicts)]
    missing = sum(item.get("material_missing") is True for item in machines)
    issues = []
    for item in items:
        if item.get("open_term") or item.get("status") not in {"hit", "undecidable"}:
            continue
        prompt = item.get("finding_semantics") == "manual_prompt"
        evidence = item.get("evidence_list") or ([item["evidence"]] if item.get("evidence") else [])
        locations = ["%s 第%s页：%s" % (ref.get("file_name") or "材料%s" % ref.get("material_id"),
                     ref.get("page") or "待核实", ref.get("quote") or "未提供摘录")
                     for ref in evidence if isinstance(ref, dict)]
        issues.append({"number": len(issues) + 1, "finding_id": item.get("finding_id"),
                       "item_id": item.get("item_id"),
                       "category": "普通人工提示" if prompt else "明确缺项" if item.get("material_missing") is True
                                   else "需关注" if item.get("status") == "hit" else "待核实",
                       "label": _text(item.get("label")), "fact": _text(item.get("message")),
                       "basis": _text(item.get("source")), "evidence": locations,
                       "recommendation": _text(item.get("recommendation")),
                       "responsible_party": "待人工明确",
                       "human_note": (item.get("human_disposition") or {}).get("note") or ""})
    # Count individual records included in machine-result statistics. This is
    # distinct from the five execution groups, eight presentation dimensions,
    # completeness-only checklist, and human-review issue rows.
    checklist_count = len(machines)
    points = [point for section in legacy.get("sections") or [] for point in section.get("key_points") or []]
    dimensions = []
    for code, label, kinds, scope in BUSINESS_MODULES[1:-1]:
        # internal_records 仅为内部信用资料，不证明工资保障核查已开展。
        mapped = () if code == "wages" else kinds
        selected = [(item, verdict) for item, verdict in zip(machines, dimension_verdicts) if item.get("kind") in mapped]
        selected += [(item, verdict) for item, verdict in zip(machines, dimension_verdicts)
                     if item.get("kind") == "completeness"
                     and _COMPLETENESS_DIMENSIONS.get(report_document._summary_theme(item, policy)) == code]
        related_points = [point for point in points if point.get("label") in _DIMENSION_POINTS.get(code, set())]
        state = "已覆盖" if selected else "未形成核查结果" if mapped else "本版本未覆盖"
        verdict = _dimension_verdict(
            selected, mapped, completed,
            has_completeness=any(item.get("kind") == "completeness" for item, _ in selected),
        )
        dimensions.append({"code": code, "title": label.removesuffix("审查"), "coverage": state,
                           "verdict": verdict, "scope": scope,
                           "summary": ("已形成 %d 项相关机器结论；仅代表已覆盖要点。" % len(selected)
                                       if selected else "当前没有足以评价该维度的自动核查结果。"),
                           "basis": report_content_summary.basis_documents([item.get("source") for item, _ in selected]),
                           "points": deepcopy(related_points),
                           "limitation": "该维度完整性、真实性及未覆盖要点仍需人工核实。"})
    limits = list(dict.fromkeys(_scope_limitations(report, report.get("reading_coverage") or [])
            + [str(x.get("message") or x) for x in report.get("limitations") or [] if isinstance(x, dict)]))
    limits += ["八维度为报告编排范围，不表示八个维度均已完成核查。",
               "核查事项按纳入机器结论统计的逐项记录计数；五个核查阶段和八个审查维度只是分组，普通提示、开放条款及人工复核问题项不计入该数。",
               "本报告未进行原件逐页人工核对；审批意见、补正期限及签字由审查人在导出 Word 中填写。"]
    if any(item.get("open_term") for item in items):
        limits.append("另有 %d 项开放兜底条款仅登记，需结合适用法规人工核实。" % sum(bool(item.get("open_term")) for item in items))
    basis = _merge_basis_documents(report_content_summary.basis_documents([item.get("source") for item in machines]))
    total = len(machines)
    summary = ""
    document = {"schema_version": SCHEMA_VERSION, "template_id": TEMPLATE_ID, "template_version": "V1.3",
                "mapping_version": MAPPING_VERSION, "available": True, "title": title,
                "recipient": (information.get("施工总承包单位") or "施工总承包单位") + "／项目部",
                "basic_information": information, "dimensions": dimensions, "issues": issues,
                "basis": basis, "limitations": limits, "summary": summary,
                "statistics": {"total": total, "counts": counts, "missing": missing},
                "checklist_count": checklist_count,
                "approval": {"opinion": "", "deadline": "", "reviewer": "", "checker": "", "issuer": ""},
                "issued_date": str(meta.get("generated_at") or "")[:10],
                "material_page_count": pages,
                "trace": {**{key: meta.get(key) for key in ("task_id", "ruleset_version", "basis_snapshot_id", "source_manifest_hash")},
                          "subject_id": subjects[0].get("subject_id"),
                          "report_policy_sha256": (reference.get("report_policy") or {}).get("sha256")}}
    visible = [item for item in items if not item.get("open_term")
               and item.get("status") in {"hit", "miss", "undecidable"}]
    machine_verdicts = {id(item): verdict for item, verdict in zip(machines, verdicts)}
    document["issue_groups"] = report_content_summary.summarize_items(
        visible, [machine_verdicts.get(id(item), "待核实") for item in visible], subjects[0].get("name"))
    document["category_summaries"] = report_content_summary.category_summaries(document["issue_groups"])
    document["blocks"] = _blocks(document)
    return document


def _blocks(document):
    """One ordered content tree for HTML, Word, PDF and text preview."""
    blocks = []
    def paragraph(text):
        blocks.append({"type": "paragraph", "text": text})
    def heading(text):
        blocks.append({"type": "heading", "text": text})
    def table(headers, rows, widths):
        blocks.append({"type": "table", "headers": headers, "rows": rows, "widths": widths})
    info = document["basic_information"]
    paragraph("致：" + document["recipient"])
    project = info.get("工程名称") or info.get("标段") or "本项目"
    if info.get("标段") and str(info["标段"]) not in project:
        project += "／" + str(info["标段"])
    page_text = "（共%s页）" % document["material_page_count"] if document["material_page_count"] is not None else ""
    date_text = "于%s" % info["报送日期"] if info.get("报送日期") else ""
    count_text = "%s项" % document["checklist_count"] if document.get("checklist_count") is not None else "待核实"
    item_text = _item_text(info)
    paragraph("根据相关法律法规和文件，对你部【%s】报送的%s%s报审材料%s实施合规性智能审查。共核查事项%s。审查情况如下：" %
              (_submitted_date(info.get("报送日期")) or "待核实", project,
               item_text, page_text, count_text))
    heading("一、报审基本情况")
    for key in ("分包单位", "分包事项"):
        if info.get(key) not in (None, ""):
            paragraph("%s：%s" % (key, info[key]))
    heading("二、审查依据")
    paragraph("《中华人民共和国建筑法》、《公路工程施工分包管理办法》、《公路工程施工分包负面清单》、《上海市公路工程施工分包管理实施细则》等，详见附件。")
    heading("三、审查情况")
    paragraph("本次按8个审查维度对报审材料核查：其中%s。" % _dimension_summary(document["dimensions"]))
    table(["序号", "审查维度", "主要依据", "初审意见"],
          [[str(index), row["title"], "；".join(row["basis"]) or (row.get("limitation") if row["verdict"] == "—" else "本次未形成专门核查依据"),
            row["verdict"]]
           for index, row in enumerate(document["dimensions"], 1)], [.07, .22, .40, .31])
    heading("四、审查发现问题")
    for row in document["issue_groups"]:
        if row["category"] in {"缺项", "不符合项"}:
            paragraph("%s：%s" % (row["theme"], row["fact"]))
    heading("五、总体审查意见")
    status_labels = (("符合", "符合项"), ("不符合", "不符合项"), ("缺项", "缺项"))
    for status, label in status_labels:
        names = [row["title"] for row in document["dimensions"] if row["verdict"] == status]
        if names:
            paragraph("%s：%s" % (label, "、".join(names)))
    paragraph("审查结论：")
    paragraph("人工审批意见：________________________________")
    paragraph("补正期限：____________________________________")
    heading("六、补充说明")
    paragraph("本报告由智能体基于报审材料及预设规则生成，仅起辅助审查作用，最终以实际审查人结论为准。")
    paragraph("建议结合原件及履约情况核对；本次材料、读取及能力边界详见附件一。")
    paragraph("附件一：审查概况与审查依据；附件二：问题清单与补正要求。")
    paragraph("审查人：________　复核人：________　签发人：________")
    paragraph("工程分包审查智能体　出具日期：" + (document["issued_date"] or UNKNOWN))
    blocks[-1]["derived_date"] = True
    blocks.append({"type": "page_break"})
    heading("附件一、审查概况与审查依据")
    heading("一、审查概况")
    for key in ("工程名称", "标段", "建设单位（业主）", "施工总承包单位", "分包单位", "分包事项",
                "拟分包金额", "材料页数", "核查基准日"):
        paragraph("%s：%s" % (key, _text(info.get(key))))
    paragraph("审查边界说明：")
    for index, text in enumerate(document["limitations"], 1):
        paragraph("%s. %s" % (index, text))
    heading("二、审查依据")
    for index, text in enumerate(document["basis"], 1):
        paragraph("%s. %s。" % (index, text))
    blocks.append({"type": "page_break"})
    heading("附件二、问题清单与补正要求")
    grouped_issues = [row for row in document["issue_groups"] if row["category"] != "符合项"]
    table(["序号", "问题类别", "问题事实", "补正要求", "责任单位"],
          [[str(index), row["theme"], row["fact"],
            row["recommendation"], row["responsible_party"]] for index, row in enumerate(grouped_issues, 1)]
          or [["—", "—", "当前无列示事项", "—", "—"]], [.06, .18, .35, .28, .13])
    paragraph("注：本清单按业务主题归并，与正文概要同源；逐条核查、原文证据及人工复核记录保留在系统中。责任单位栏表示补充说明或补正的对接角色，不作法律责任认定。")
    return blocks


def select_template(payload: dict, template_id: str) -> dict:
    """Remove frozen alternatives from legacy responses; never rebuild a history."""
    documents = payload.pop("template_documents", None) or {}
    if template_id == "legacy":
        return payload
    if template_id != TEMPLATE_ID:
        raise ValueError("template_id 仅支持 legacy / v1.3")
    document = documents.get(TEMPLATE_ID)
    if not document:
        raise LookupError("该历史版本未冻结 V1.3 模板，请使用旧模板；不会用当前材料重建历史报告。")
    if not document.get("available"):
        raise LookupError(document.get("reason") or "V1.3 模板当前不可用")
    payload["template_document"] = deepcopy(document)
    return payload
