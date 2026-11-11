"""报告导出与简化存证（W3-3，MVP-2 收口片）。

纪律：
- 导出只渲染 reporting.build_report 的聚合结果，**不重算**（与报告侧同纪律）；
- 简化存证 = 报告 canonical JSON 的 sha256 + 导出时间戳 + 证据包 zip；
  校验码是内容指纹（meta.generated_at 不参与哈希），留存在事件和证据包；
  证据包内清单写入逐文件 sha256，拿到包即可自证完整性（不依赖包外记录）；
- 报告哈希针对「不含存证块」的报告体计算：证据包里的 report.json 附带
  attestation 块，校验方对其余部分重算 canonical 哈希（generated_at 置空）
  即可比对；
- 导出动作落事件流（report.exported），哈希与时间戳随事件可查（时间戳存证）。

Word 用 python-docx；PDF 嵌入 PyMuPDF 自带的中文字体。
"""
import hashlib
import io
import json
import os
import re
import zipfile
from collections import Counter
from datetime import datetime
from types import SimpleNamespace

import fitz
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt
from sqlalchemy.orm import Session

from .extract import FIELD_LABELS
from .models import STATUS_LABELS, BsMaterial, BsReviewVersion, BsTask, BsTaskEvent
from .reference_output import reference_lines
from .reporting import page_ranges
from .report_document import summarize_report_issues, report_display_sections
from .service import add_event, event_dto, get_review_version, list_review_versions
from . import reference_visibility
from .supplement_generation import generation_metadata

_ISSUES_HEADING = "二、问题与待确认事项"
_FOLLOWUP_NOTICE = "请逐项核实上述问题及待确认事项，并记录处理意见后复核。"


def safe_filename(title: str) -> str:
    """标题 → 可安全落盘的文件名（路由层/导出层共用，故为公共符号）。"""
    out = []
    for ch in title:
        if ch in '\\/:*?"<>|' or ord(ch) < 32:
            out.append("_")
        else:
            out.append(ch)
    return ("".join(out)).strip() or "核查报告"


def _canonical_json(report: dict) -> bytes:
    return json.dumps(report, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def report_digest(report: dict) -> str:
    """报告体 canonical JSON 的 sha256 十六进制。

    边界（对外要说清）：meta.generated_at 不参与哈希——校验码 = 内容指纹，
    同一结论集在任何时间生成/导出/查看，校验码一致；导出时间另存于事件及证据包。
    """
    body = json.loads(json.dumps(report))
    body.setdefault("meta", {})["generated_at"] = ""
    # 业务正文中的报告日期由同一生成时间投影而来，不应破坏内容指纹稳定性。
    document = body.get("business_document") or {}
    if isinstance(document.get("basic_information"), dict):
        document["basic_information"]["报告日期"] = ""
    return hashlib.sha256(_canonical_json(body)).hexdigest()


# ==================== Word 导出 ====================

def _cjk(run, name="宋体"):
    run.font.name = name
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)


def _configure_word_document(doc):
    """Match the PDF exporter’s A4 portrait geometry for Word outputs."""
    for section in doc.sections:
        section.page_width = Mm(210)
        section.page_height = Mm(297)
        section.top_margin = Mm(17)
        section.bottom_margin = Mm(17)
        section.left_margin = Mm(17)
        section.right_margin = Mm(17)


def _add_para(doc, text, size=11, bold=False, indent=0.0, space_after=4, keep_with_next=False):
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.font.size = Pt(size)
    run.bold = bold
    _cjk(run)
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.keep_with_next = keep_with_next
    if indent:
        p.paragraph_format.left_indent = Pt(indent)
    return p


def _section_cover_line(meta: dict) -> str:
    """A-03（B1）封面标段行：项目/合同段/金额/签订日/基准日。

    与「档案尺度」「报告生成」同为封面基本信息行，非技术读者可见面。
    """
    amount = meta.get("contract_amount")
    if isinstance(amount, dict) and amount.get("value") is not None:
        value = amount.get("value")
        if isinstance(value, float):
            value = ("%.2f" % value).rstrip("0").rstrip(".")
        amount_text = "%s%s" % (value, amount.get("unit") or "")
    elif amount:
        amount_text = str(amount)
    else:
        amount_text = None
    parts = [
        "项目名称：%s" % (meta.get("project_name") or "未填写"),
        "合同段：%s" % (meta.get("contract_section") or "未填写"),
    ]
    tail = []
    if amount_text:
        tail.append("合同金额：%s" % amount_text)
    if meta.get("contract_sign_date"):
        tail.append("签订日期：%s" % meta["contract_sign_date"])
    tail.append("核查基准日：%s" % (meta.get("baseline_date") or "未填写"))
    return "　|　".join(parts) + "\n" + "　|　".join(tail)


def _contractor_summary_lines(report: dict) -> list[str]:
    """摘要中的承包单位三态 → 纸面统一口径。"""
    contractor = (report.get("qualification_summary") or {}).get("general_contractor")
    if not isinstance(contractor, dict):
        return []
    status = contractor.get("status")
    if status == "matched" and contractor.get("company"):
        line = "施工总承包单位：%s" % contractor["company"]
    else:
        line = "施工总承包单位：%s" % (contractor.get("label") or "承包单位待核实")
        if contractor.get("candidate_company"):
            line += "（候选：%s）" % contractor["candidate_company"]
        if contractor.get("reason_code"):
            line += "（原因：%s）" % contractor["reason_code"]
    evidence = contractor.get("evidence") or []
    locations = []
    for ref in evidence[:3]:
        if not isinstance(ref, dict):
            continue
        location = ref.get("file_name") or "材料"
        if ref.get("page"):
            location += " 第%s页" % ref["page"]
        locations.append(location)
    if locations:
        line += "；证据：%s" % "、".join(locations)
    return [line]


def _fact_value_text(f: dict) -> str:
    """字段值 → 平实中文：数值带 unit（金额万元 / 比例 %），主体/声明列表拼接。"""
    value = f.get("value")
    if isinstance(value, dict):
        text = "；".join("%s：%s" % (k, v) for k, v in value.items())
    elif isinstance(value, (list, tuple)):
        text = "、".join(str(v) for v in value)
    elif isinstance(value, (int, float)) and f.get("unit") == "万元":
        text = ("%.2f" % value).rstrip("0").rstrip(".")
    elif isinstance(value, float):
        text = ("%d" % value) if value.is_integer() else str(value)
    else:
        text = "" if value is None else str(value)
    unit = f.get("unit")
    return "%s%s" % (text, unit or "")


def _contract_facts_lines(report: dict) -> list:
    """A-15（B1）合同要素块：逐合同材料一行（文件名 · 角色标签 · 命中要素），
    不可判定要素**不静默省略**，该材料末行统一写「本文件未能判定：<字段名>（缺：…）」。

    空 contract_facts → []（无合同材料时纸面不出现该节）。数值一律带 unit。
    """
    facts = report.get("contract_facts") or []
    if not facts:
        return []
    lines = []
    for cf in facts:
        fields = cf.get("fields") or {}
        hits = []
        misses = []
        for key, label in FIELD_LABELS.items():
            f = fields.get(key) or {}
            if f.get("status") == "hit":
                hits.append("%s：%s" % (label, _fact_value_text(f)))
            else:
                missing = "、".join(f.get("missing") or []) or "该要素"
                misses.append("%s（缺：%s）" % (label, missing))
        head = "%s · %s" % (cf.get("file_name") or "材料",
                            cf.get("role_label") or "")
        lines.append(head + ("：" + " · ".join(hits) if hits else ""))
        if misses:
            lines.append("本文件未能判定：" + "；".join(misses))
    return lines


def _evidence_line(ev: dict) -> str:
    page = "第%d页" % ev["page"] if ev.get("page") else ""
    where = ev.get("file_name") or "材料"
    loc = "%s%s" % (where, (" " + page) if page else "")
    return "    证据：%s 「%s」" % (loc, (ev.get("quote") or "").strip())


def _review_line(rv: dict) -> str:
    """人工处置回执 → 纸面一行，保留处理意见、人员与时间。"""
    line = "人工处理：%s" % rv.get("decision_label", rv.get("decision", ""))
    if rv.get("note"):
        line += "（意见：%s）" % rv["note"]
    tail = " · ".join(x for x in (rv.get("reviewer"),
                                  (rv.get("at") or "").replace("T", " ")) if x)
    if tail:
        line += " · " + tail
    return line


def _source_line(item: dict) -> str:
    """A-05（B2）：结论出处 → 纸面一行「依据：<source>」；空 source → 空串（不出现空行）。

    护栏必须有人读（AGENTS 纪律 5）：`source` 自 MVP-1 起只在数据面流转、从未上纸面，
    集团拿到的纸面报告因此看不到 A3-02 的「盖章判断仅适用本地判章口径」声明。本函数
    把 `item["source"]`（结论出处/口径声明）渲染为可读一行，Word/PDF 同源消费。
    """
    src = (item.get("source") or "").strip()
    return ("依据：%s" % src) if src else ""


def _summary_issue_keys(report: dict) -> set[tuple[str, str | None]] | None:
    """从同源摘要取纸面问题白名单；旧报告无摘要时返回 None 保持兼容。"""
    summary = report.get("qualification_summary")
    if not isinstance(summary, dict):
        return None
    sections = summary.get("sections") or {}
    keys: set[tuple[str, str | None]] = set()
    for section_name in ("non_compliant", "missing", "undecidable"):
        for item in sections.get(section_name) or []:
            if not isinstance(item, dict) or not item.get("item_id"):
                continue
            subject = item.get("subject") or {}
            subject_id = subject.get("subject_id") if isinstance(subject, dict) else None
            keys.add((str(item["item_id"]), str(subject_id) if subject_id is not None else None))
    return keys


def _report_issues(report: dict) -> list:
    """纸面整改报告消费摘要白名单；机器 sections 只提供完整证据和审计细节。"""
    summary_keys = _summary_issue_keys(report)
    issues = []
    for section in report.get("sections") or []:
        for item in section.get("items") or []:
            if item.get("status") not in ("hit", "undecidable"):
                continue
            if summary_keys is not None:
                subject = item.get("subject") or {}
                subject_id = subject.get("subject_id") if isinstance(subject, dict) else item.get("subject_id")
                key = (str(item.get("item_id")), str(subject_id) if subject_id is not None else None)
                if key not in summary_keys:
                    continue
            item = dict(item)
            item.setdefault("category", section.get("title") or "核查项")
            item.setdefault(
                "recommendation",
                "补充或核实与该项相关的原始资料，完成整改后重新核查。"
                if item.get("status") == "undecidable"
                else "请结合问题描述核对合同及相关资料，完成整改后重新核查。",
            )
            issues.append(item)
    return issues


def _issue_evidence_line(item: dict) -> str:
    refs = item.get("evidence_list") or ([item["evidence"]] if item.get("evidence") else [])
    if not refs:
        return ""
    locations = []
    for ref in refs[:3]:
        loc = ref.get("file_name") or "材料"
        version_role = ref.get("version_role")
        if version_role == "superseded":
            loc += "（已替代版）"
        elif version_role == "current":
            loc += "（当前版）"
        if ref.get("page"):
            loc += " 第%d页" % ref["page"]
        locations.append(loc)
    suffix = "等" if len(refs) > 3 else ""
    return "定位：%s%s" % ("；".join(locations), suffix)


def _invalidated_review_line(item: dict) -> str:
    """把重跑后失效的旧人工回执原样压缩成可读的纸面审计行。"""
    disposition = item.get("human_disposition") or {}
    review = item.get("review") or {}
    invalidated = disposition.get("invalidated") or review.get("invalidated")
    if not isinstance(invalidated, dict):
        return ""
    parts = ["原复核意见失效"]
    if invalidated.get("reason"):
        parts.append("原因：%s" % invalidated["reason"])
    if invalidated.get("at"):
        parts.append("时间：%s" % invalidated["at"])
    previous = invalidated.get("previous")
    if isinstance(previous, dict) and previous.get("decision"):
        parts.append("原处置：%s" % previous["decision"])
    return "；".join(parts)


def _issue_lines(item: dict, index: int) -> list:
    """每个问题用标题和一段要点呈现，避免一项拆成多段。"""
    title = "%d　[%s] %s（风险等级：%s）" % (
        index,
        item.get("category") or "核查项",
        item.get("label") or item.get("item_id") or "问题",
        item.get("severity_label") or "待确认",
    )
    details = ["问题描述：%s" % (item.get("message") or "需人工核实")]
    if item.get("source"):
        details.append("依据：%s" % item["source"])
    details.append("整改建议：%s" % (item.get("recommendation") or "补充或核实相关资料后重新核查。"))
    disposition = item.get("human_disposition") or {}
    review = item.get("review") or {}
    if disposition.get("code") in ("accepted", "ignored"):
        review_line = "人工处理：%s" % (disposition.get("label") or "已处理")
        if disposition.get("note"):
            review_line += "（意见：%s）" % disposition["note"]
        details.append(review_line)
    else:
        # 未发生 accepted/ignored 时也必须上纸面，避免读者把机器结论当成人工结论。
        details.append(disposition.get("label") or "未经人工处理，保留系统核查结果")
    invalidated_line = _invalidated_review_line(item)
    if invalidated_line:
        details.append(invalidated_line)
    evidence = _issue_evidence_line(item)
    if evidence:
        details.append(evidence)
    return [title, "；".join(details)]


def _material_reading_lines(report: dict) -> list:
    """材料读取限制独立上纸面，不依赖某一结论是否命中或不可判定。"""
    return ["《%s》：%s" % (item.get("file_name") or "材料", item["message"])
            for item in report.get("material_issues") or [] if item.get("message")]


def _coverage_lines(payload: dict) -> list:
    """报告/意见书共用覆盖说明；中间结果不得被纸面写成“全部符合”。"""
    coverage = payload.get("coverage") or {}
    limitations = payload.get("limitations") or coverage.get("limitations") or []
    if not coverage and not limitations:
        return []
    lines = ["当前报告状态：%s" %
             (coverage.get("status_label") or ("完整结果" if coverage.get("complete") else "中间结果"))]
    if not coverage.get("complete"):
        lines.append("当前报告可先查看，但未完成范围不能理解为已核查无问题。")
    lines.extend(item.get("message") or str(item) for item in limitations
                 if isinstance(item, dict) and item.get("message"))
    return lines


def _business_information_rows(document: dict) -> list[tuple[str, str]]:
    statuses = document.get("basic_information_status") or {}
    rows = []
    for label, value in (document.get("basic_information") or {}).items():
        value = str(value) if value is not None and value != "" else "待核实"
        if statuses.get(label) in {"conflict", "unresolved"} and value != "待核实":
            value += "（待核实）"
        rows.append((label, value))
    return rows


def _business_summary_rows(document: dict) -> tuple[list[dict], list[dict]]:
    pending = document.get("pending_summaries")
    notes = document.get("human_notes")
    if pending is None or notes is None:
        fallback_pending, fallback_notes = summarize_report_issues(document.get("issues") or [])
        pending = fallback_pending if pending is None else pending
        notes = fallback_notes if notes is None else notes
    return pending, notes


def _business_key_points(section: dict) -> list[dict]:
    return section.get("display_points", section.get("key_points", section.get("fields"))) or []


def _pending_summary_text(row: dict) -> str:
    text = str(row.get("text") or "")
    for prefix, suffix in (("待核实：", "需要核实。"), ("需关注：", "需要关注。"), ("明确缺少材料：", "材料缺失。")):
        if text.startswith(prefix):
            return text[len(prefix):].rstrip("。") + suffix
    return text


def _business_document_lines(report: dict) -> list[tuple[str, str, bool]]:
    """将新的业务报告投影转换为简单的标题/正文行序列。"""
    document = report.get("business_document")
    if not isinstance(document, dict):
        return []
    lines: list[tuple[str, str, bool]] = [("heading", "一、项目与分包基本信息", True)]
    for label, value in _business_information_rows(document):
        lines.append(("field", "%s：%s" % (label, value), False))
    lines.append(("heading", "二、核查摘要", True))
    for index, section in enumerate(report_display_sections(document), 1):
        lines.append(("subheading", "%s. %s" % (index, section.get("title") or "核查"), True))
        for field in _business_key_points(section):
            lines.append(("text", "%s：%s" % (field.get("label"), field.get("summary")), False))
    return lines


def _add_business_document_word(doc, report: dict) -> bool:
    document = report.get("business_document")
    if not isinstance(document, dict):
        return False

    def table(headers, rows, proportions=None):
        result = doc.add_table(rows=1, cols=len(headers))
        result.style = "Table Grid"
        if proportions:
            result.autofit = False
            section = doc.sections[-1]
            width = section.page_width - section.left_margin - section.right_margin
            for column, proportion in zip(result.columns, proportions):
                column.width = int(width * proportion)
        repeat_header = OxmlElement("w:tblHeader")
        repeat_header.set(qn("w:val"), "true")
        result.rows[0]._tr.get_or_add_trPr().append(repeat_header)
        for cell, title in zip(result.rows[0].cells, headers):
            cell.text = title
            cell.paragraphs[0].paragraph_format.keep_with_next = True
        for row in rows:
            cells = result.add_row().cells
            for cell, value in zip(cells, row):
                if isinstance(value, tuple):
                    label, basis = value
                    cell.text = str(label or "")
                    para = cell.add_paragraph()
                    para.add_run("主要依据：" + ("；".join(basis) if basis else "暂无可展示依据"))
                else:
                    cell.text = str(value or "待核实")
        for row in result.rows:
            if proportions:
                for cell, proportion in zip(row.cells, proportions):
                    cell.width = int(width * proportion)
            for cell in row.cells:
                for para in cell.paragraphs:
                    for run in para.runs:
                        _cjk(run)
                        run.font.size = Pt(9)
        for row in result.rows[1:]:
            if len(headers) == 3 and len(row.cells[0].paragraphs) > 1:
                for run in row.cells[0].paragraphs[1].runs:
                    run.font.size = Pt(7.5)
        return result

    _add_para(doc, "一、项目与分包基本信息", size=13, bold=True, keep_with_next=True)
    table(("字段", "内容"), _business_information_rows(document), (0.30, 0.70))
    _add_para(doc, "二、核查摘要", size=13, bold=True, keep_with_next=True)
    for index, section in enumerate(report_display_sections(document), 1):
        _add_para(doc, "%s. %s" % (index, section.get("title") or "核查"), size=11, bold=True, keep_with_next=True)
        rows = [((row.get("label"), row.get("basis") or []), row.get("summary"), row.get("verdict") or "待核实")
                for row in _business_key_points(section)]
        if rows:
            table(("审查要点及主要依据", "核查情况", "判定"), rows, (0.30, 0.58, 0.12))
    return True


def _page_state_history_lines(payload: dict) -> list:
    """将报告中的页级状态历史压缩成纸面可读摘要；完整链路仍在 report.json 中保留。"""
    lines = []
    for row in payload.get("reading_coverage") or []:
        history = row.get("page_state_history") or []
        if not isinstance(history, list) or not history:
            continue
        parts = []
        for item in history[:6]:
            if not isinstance(item, dict) or not item.get("page"):
                continue
            transitions = item.get("transitions") or []
            states = [step.get("state") for step in transitions
                      if isinstance(step, dict) and step.get("state")]
            if states:
                parts.append("第%s页 %s" % (item["page"], "→".join(states)))
        if parts:
            suffix = "；其余页详见证据包" if len(history) > 6 else ""
            lines.append("《%s》页级状态流转：%s%s" % (
                row.get("file_name") or "材料", "；".join(parts), suffix))
    return lines


def _ai_advisory_lines(report: dict) -> list[str]:
    items = report.get("ai_advisories") or []
    coverage = report.get("ai_coverage")
    if not items and not coverage:
        return []
    findings = {item.get("finding_id", item.get("id")): item
                for section in report.get("sections") or [] for item in section.get("items") or []}
    lines = ["以下为辅助建议，需结合材料和规则由人工核实；不改变系统结论。"]
    if coverage:
        lines.append("本次 AI 辅助核查范围（未执行或依据不足的项目需人工核实）：")
        if coverage.get("state") == "unavailable":
            lines.append("核查范围不可用，请重新核查。")
        for group in coverage.get("groups") or []:
            if group.get("state") != "available":
                lines.append("%s：状态不可用" % group.get("label", "核查步骤"))
                continue
            counts = group.get("counts") or {}
            lines.append("%s：完成 %s 项，未开始 %s 项，进行中 %s 项，部分完成 %s 项，失败 %s 项，未执行 %s 项，无关联依据 %s 项" % (
                group.get("label", "核查步骤"), counts.get("completed", 0), counts.get("not_started", 0),
                counts.get("pending", 0), counts.get("partial", 0), counts.get("failed", 0),
                counts.get("not_run", 0), group.get("skipped", 0)))
    for advisory in items:
        finding = findings.get(advisory.get("finding_id")) or {}
        lines.append(finding.get("label") or finding.get("message") or "相关核查项")
        if advisory.get("limitation"):
            lines.append(advisory["limitation"])
        for proposal in advisory.get("proposals") or []:
            lines.append(proposal.get("message") or "")
            for ref in proposal.get("material_evidence") or []:
                lines.append("材料第 %s 页：%s" % (ref.get("page"), ref.get("quote") or ""))
            for ref in proposal.get("rule_evidence") or []:
                lines.append("依据原文：%s" % (ref.get("quote") or ""))
        lines.extend(advisory.get("limitations") or [])
    return lines


def word_bytes(report: dict, digest: str = None, exported_at: str = None) -> bytes:
    """report dict → Word (docx) 字节流；digest/exported_at 缺省时按当前内容自算。"""
    doc = Document()
    _configure_word_document(doc)
    meta = report["meta"]
    _add_para(doc, "%s——核查报告" % meta["title"], size=16, bold=True, space_after=8)
    if report.get("draft_notice"):
        # 稿别标注进纸面：初稿被打印/转发后，必须一眼看得出「未复核」
        _add_para(doc, "【%s】%s" % (report.get("stage_label", ""), report["draft_notice"]),
                  size=10, bold=True, space_after=6)

    if _business_document_lines(report):
        _add_business_document_word(doc, report)
        bio = io.BytesIO()
        doc.save(bio)
        return bio.getvalue()

    scope = "、".join(k for k, on in meta["scope"].items() if on) or "未选择"
    _add_para(doc, "核查范围：%s　|　档案尺度：%s　|　报告生成：%s"
              % (scope, meta.get("scale") == "standard" and "标准档" or meta.get("scale", ""),
                 meta.get("generated_at", "")), size=9, space_after=2)
    # A-03（B1）：封面追加标段锚点行（空值 → 「未填写」）
    _add_para(doc, _section_cover_line(meta), size=9,
              space_after=4 if report.get("scope_statements") else 8)
    for line in _contractor_summary_lines(report):
        _add_para(doc, line, size=9, space_after=3)
    fact_lines = _contract_facts_lines(report)
    if fact_lines:
        _add_para(doc, "合同要素事实", size=11, bold=True, space_after=4)
        for line in fact_lines:
            _add_para(doc, line, size=9, space_after=2)
    # 范围说明：本期未纳入核查的情形要写在纸面上（护栏必须有人读），不能只留在后台日志
    for statement in report.get("scope_statements") or []:
        _add_para(doc, "范围说明：%s" % statement, size=9, space_after=2)
    if report.get("scope_statements"):
        _add_para(doc, "", size=6, space_after=2)
    reading_lines = _material_reading_lines(report)
    if reading_lines:
        _add_para(doc, "材料读取提示", size=11, bold=True, space_after=4)
        for line in reading_lines:
            _add_para(doc, line, size=9, space_after=3)
    # BS-P1-09（设计 98 §8）：未读页范围 + 受影响结论数必须上纸面（护栏必须有人读）；
    # 文案取报告同一 summary_text，层级为次级说明（建议单独一行）。
    reading_coverage = report.get("reading_coverage") or []
    if reading_coverage:
        _add_para(doc, "未读页范围与受影响结论", size=11, bold=True, space_after=4)
        for row in reading_coverage:
            _add_para(doc, row.get("summary_text") or "", size=9, space_after=2)
            if row.get("suggestion_text"):
                _add_para(doc, "建议：%s" % row["suggestion_text"], size=9, space_after=3)
        history_lines = _page_state_history_lines(report)
        if history_lines:
            _add_para(doc, "页级状态流转", size=11, bold=True, space_after=4)
            for line in history_lines:
                _add_para(doc, line, size=9, space_after=2)
    coverage_lines = _coverage_lines(report)
    if coverage_lines:
        _add_para(doc, "核查覆盖说明", size=11, bold=True, space_after=4)
        for line in coverage_lines:
            _add_para(doc, line, size=9, space_after=2)
    _add_para(doc, "一、核查总体情况与统计概览", size=13, bold=True, space_after=5)
    _add_para(doc, report["summary"]["headline"], size=10.5, space_after=10)
    issues = _report_issues(report)
    _add_para(doc, _ISSUES_HEADING, size=13, bold=True, space_after=5)
    if not issues:
        _add_para(doc, "本次未发现需要整改的问题。" if (not report.get("coverage") or report["coverage"].get("complete"))
                  else "当前尚无逐项核查结论，不能视为全部符合。",
                  size=10.5, indent=12, space_after=8)
    for index, item in enumerate(issues, 1):
        for line_no, line in enumerate(_issue_lines(item, index)):
            _add_para(doc, line, size=10.5 if line_no == 0 else 9.5,
                      bold=line_no == 0, indent=12, space_after=2 if line_no < 4 else 6)
    if issues:
        _add_para(doc, "三、整改建议", size=13, bold=True, space_after=5)
        _add_para(doc, _FOLLOWUP_NOTICE, size=10.5, space_after=3)

    _add_para(doc, "参考依据（五步快照）", size=13, bold=True, space_after=5)
    for line in reference_lines(report.get("reference_snapshot")):
        _add_para(doc, line, size=9, space_after=3)
    advisory_lines = _ai_advisory_lines(report)
    if advisory_lines:
        _add_para(doc, "AI 辅助意见", size=13, bold=True, space_after=5)
        for line in advisory_lines:
            _add_para(doc, line, size=9, space_after=3)

    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()


# ==================== PDF 导出 ====================

def _text_width(text: str, size: float) -> float:
    # fitz 的内置 china-s / china-ss 在 insert_text 中连 ASCII 也按全角推进。
    return len(text) * size


def _wrap(text: str, size: float, max_width: float) -> list:
    lines, cur, cur_w = [], [], 0.0
    for c in text:
        w = size
        if cur and cur_w + w > max_width:
            lines.append("".join(cur))
            cur, cur_w = [], 0.0
        cur.append(c)
        cur_w += w
    if cur:
        lines.append("".join(cur))
    return lines or [""]


def _pdf_insert_text(page, point, text, fontsize):
    """Embed the available CJK font so readers do not depend on local fonts."""
    if not any(font[4] == "EmbeddedCJK" for font in page.get_fonts()):
        page.insert_font(fontname="EmbeddedCJK", fontbuffer=fitz.Font("cjk").buffer)
    page.insert_text(point, text, fontname="EmbeddedCJK", fontsize=fontsize)


def _draw_business_pdf(doc, page, y, document):
    """Render business tables, splitting oversized rows and repeating their header."""
    margin, page_w, page_h = 48.0, 595.0, 842.0
    right = page_w - margin
    size, line_height, padding = 9.5, 14.0, 5.0

    def new_page():
        return doc.new_page(width=page_w, height=page_h), margin

    def heading(text):
        nonlocal page, y
        # Leave room for the following table header and a short first row.
        if y + 130 > page_h - margin:
            page, y = new_page()
        y += 6
        _pdf_insert_text(page, (margin, y + 13), text, fontsize=13)
        y += 21

    def table(headers, rows):
        nonlocal page, y
        widths = ([0.30, 0.58, 0.12] if len(headers) == 3 else [135 / (right - margin), 1 - 135 / (right - margin)])
        edges = [margin]
        for width in widths:
            edges.append(edges[-1] + width * (right - margin))

        def draw_cells(columns, header=False):
            nonlocal y
            height = max(map(len, columns)) * line_height + 2 * padding
            for x0, x1, lines in zip(edges, edges[1:], columns):
                page.draw_rect(fitz.Rect(x0, y, x1, y + height), color=(0.35, 0.35, 0.35), width=0.5,
                               fill=(0.94, 0.94, 0.94) if header else None)
                for index, (line, font_size) in enumerate(lines):
                    _pdf_insert_text(page, (x0 + padding, y + padding + size + index * line_height), line,
                                     fontsize=font_size)
            y += height

        def header():
            draw_cells([[(label, size)] for label in headers], True)

        if y + 2 * (line_height + 2 * padding) > page_h - margin:
            page, y = new_page()
        header()
        for row in rows:
            columns = []
            for value, x0, x1 in zip(row, edges, edges[1:]):
                lines = []
                values = [(str(value or "待核实"), size)]
                if isinstance(value, tuple):
                    label, basis = value
                    values = [(str(label or ""), size), ("主要依据：" + ("；".join(basis) if basis else "暂无可展示依据"), 7.5)]
                for content, font_size in values:
                    for raw in content.splitlines() or [""]:
                        lines.extend((line, font_size) for line in _wrap(raw, font_size, x1 - x0 - 2 * padding))
                columns.append(lines)
            offset = 0
            total = max(map(len, columns))
            row_height = total * line_height + 2 * padding
            header_height = line_height + 2 * padding
            if (row_height <= page_h - 2 * margin - header_height
                    and y + row_height > page_h - margin):
                page, y = new_page()
                header()
            while offset < total:
                available = int((page_h - margin - y - 2 * padding) // line_height)
                if available < 1:
                    page, y = new_page()
                    header()
                    continue
                end = min(total, offset + available)
                draw_cells([column[offset:end] for column in columns])
                offset = end
        y += 4

    heading("一、项目与分包基本信息")
    table(("字段", "内容"), _business_information_rows(document))
    heading("二、核查摘要")
    for index, section in enumerate(report_display_sections(document), 1):
        heading("%s. %s" % (index, section.get("title") or "核查"))
        rows = [((row.get("label"), row.get("basis") or []), row.get("summary"), row.get("verdict") or "待核实")
                for row in _business_key_points(section)]
        if rows:
            table(("审查要点及主要依据", "核查情况", "判定"), rows)
    return page, y


def pdf_bytes(report: dict, digest: str = None, exported_at: str = None) -> bytes:
    """report dict → PDF 字节流（fitz 内置简体中文字体 china-s）。"""
    margin, size_body, size_h1, size_h2 = 48.0, 10.5, 15.0, 12.5
    page_w, page_h = 595.0, 842.0
    max_w = page_w - margin * 2
    line_gap = 1.45

    doc = fitz.open()
    page = doc.new_page(width=page_w, height=page_h)
    y = margin

    def new_page():
        nonlocal page, y
        page = doc.new_page(width=page_w, height=page_h)
        y = margin

    def write(text, size, bold=False):
        nonlocal y
        lh = size * line_gap
        # 原文中的显式换行也占一行，避免insert_text自行换行后与下一段重叠。
        for raw_line in text.splitlines() or [""]:
            for ln in _wrap(raw_line, size, max_w):
                if y + lh > page_h - margin:
                    new_page()
                _pdf_insert_text(page, fitz.Point(margin, y + size), ln, fontsize=size)
                y += lh

    meta = report["meta"]
    write("%s——核查报告" % meta["title"], size_h1, bold=True)
    if report.get("draft_notice"):
        write("【%s】%s" % (report.get("stage_label", ""), report["draft_notice"]), 9.5, bold=True)
    business_lines = _business_document_lines(report)
    if business_lines:
        page, y = _draw_business_pdf(doc, page, y, report["business_document"])
        bio = io.BytesIO()
        doc.save(bio, deflate=True)
        doc.close()
        return bio.getvalue()
    scope = "、".join(k for k, on in meta["scope"].items() if on) or "未选择"
    write("核查范围：%s | 档案尺度：%s | 报告生成：%s"
          % (scope, meta.get("scale", ""), meta.get("generated_at", "")), 9)
    # A-03（B1）：封面追加标段锚点行（空值 → 「未填写」）
    write(_section_cover_line(meta), 9)
    for line in _contractor_summary_lines(report):
        write(line, 9)
    fact_lines = _contract_facts_lines(report)
    if fact_lines:
        write("合同要素事实", 11, bold=True)
        for line in fact_lines:
            write(line, 9)
    for statement in report.get("scope_statements") or []:
        write("范围说明：%s" % statement, 9)
    reading_lines = _material_reading_lines(report)
    if reading_lines:
        write("材料读取提示", 11, bold=True)
        for line in reading_lines:
            write(line, 9)
    coverage_lines = _coverage_lines(report)
    if coverage_lines:
        write("核查覆盖说明", 11, bold=True)
        for line in coverage_lines:
            write(line, 9)
    history_lines = _page_state_history_lines(report)
    if history_lines:
        write("页级状态流转", 11, bold=True)
        for line in history_lines:
            write(line, 9)
    write("一、核查总体情况与统计概览", size_h2, bold=True)
    write(report["summary"]["headline"], 9.5)
    issues = _report_issues(report)
    write(_ISSUES_HEADING, size_h2, bold=True)
    if not issues:
        write("本次未发现需要整改的问题。" if (not report.get("coverage") or report["coverage"].get("complete"))
              else "当前尚无逐项核查结论，不能视为全部符合。", 9.5)
    for index, item in enumerate(issues, 1):
        for line_no, line in enumerate(_issue_lines(item, index)):
            write(line, size_body if line_no == 0 else 9.2, bold=line_no == 0)
    if issues:
        write("三、整改建议", size_h2, bold=True)
        write(_FOLLOWUP_NOTICE, 9.5)

    write("参考依据（五步快照）", size_h2, bold=True)
    for line in reference_lines(report.get("reference_snapshot")):
        write(line, 9)
    advisory_lines = _ai_advisory_lines(report)
    if advisory_lines:
        write("AI 辅助意见", size_h2, bold=True)
        for line in advisory_lines:
            write(line, 9)

    bio = io.BytesIO()
    doc.save(bio, deflate=True)
    doc.close()
    return bio.getvalue()


# ==================== 简化存证：证据包 ====================

def _material_fs_path(mat: BsMaterial) -> str | None:
    if not mat.path:
        return None
    fs_path = os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "..", mat.path))
    return fs_path if os.path.isfile(fs_path) else None


def evidence_bundle_bytes(db: Session, task: BsTask, stage: str = "draft", *, user=None) -> tuple:
    """证据包 zip：报告、意见书、版本/事件链和材料原件副本。

    清单自证：report.json 的 attestation.files 逐文件记录 sha256/size，
    报告哈希 = canonical(report minus attestation) 的 sha256。
    `opinion.json`、`review-versions.json` 和 `events.json` 让压缩包在脱离数据库后
    仍能回放“结论—版本—事件”链；`manifest.json` 是包内入口清单。
    返回 (zip_bytes, report_sha256, exported_at)。
    """
    from . import reporting  # 局部 import 避免 service↔reporting 环

    user = user or SimpleNamespace(id=task.created_by, user_type=task.created_by_type)
    report = reporting.mark_stage(reporting.build_report(db, task), stage)
    if "reference_snapshot" in report:
        report["reference_snapshot"] = reference_visibility.visible_snapshot(db, task, report["reference_snapshot"], user)
    # 证据包不经过 router._attach_generation_identity，需在这里同步当前代身份，
    # 确保 report.json 与 PDF/Word 以及 review-versions.json 指向同一最新版本。
    review_versions = list_review_versions(db, task)
    latest_version = (max(review_versions, key=lambda row: row.get("version_no", 0))
                      if review_versions else None)
    meta = report.setdefault("meta", {})
    meta["review_version_no"] = latest_version.get("version_no") if latest_version else None
    from .supplement_generation import output_generation_plan
    meta.update(generation_metadata(output_generation_plan(task.options_snapshot_json)))
    exported_at = datetime.now().isoformat(timespec="seconds")
    digest = report_digest(report)

    files = []
    material_facts = {
        row.get("material_id"): row for row in report.get("materials") or []
    }
    materials = db.query(BsMaterial).filter(BsMaterial.task_id == task.id).all()
    blobs = []
    used_names = set()
    for m in materials:
        fs_path = _material_fs_path(m)
        if fs_path is None:
            continue
        with open(fs_path, "rb") as f:
            data = f.read()
        base = safe_filename(os.path.splitext(m.file_name)[0]) or "材料"
        name = "%s%s" % (base, os.path.splitext(m.file_name)[1] or ".pdf")
        i = 2
        while name in used_names:
            name = "%s(%d)%s" % (base, i, os.path.splitext(m.file_name)[1] or ".pdf")
            i += 1
        used_names.add(name)
        blobs.append(("materials/" + name, data))
        fact = material_facts.get(m.id) or {}
        files.append({"material_id": m.id, "file_name": name,
                      "revision_no": fact.get("revision_no"),
                      "version_role": fact.get("version_role", "legacy_unknown"),
                      "replacement_target_id": fact.get("replacement_target_id"),
                      "sha256": hashlib.sha256(data).hexdigest(),
                      "size": len(data)})

    payload = dict(report)
    payload["attestation"] = {
        "kind": "简化存证（本地版）",
        "report_sha256": digest,
        "exported_at": exported_at,
        "note": "report_sha256 为本文件除 attestation 外内容 canonical JSON 的 sha256"
                "（meta.generated_at 不参与计算，保证同一内容校验码稳定）；"
                "materials/ 下文件哈希见 files 清单；历史原件保留并通过 material_id/version_role 标识。",
        "files": files,
    }
    opinion = reporting.build_opinion(db, task)
    if "reference_snapshot" in opinion:
        opinion["reference_snapshot"] = reference_visibility.visible_snapshot(db, task, opinion["reference_snapshot"], user)
    opinion_payload = {
        "schema_version": "bs.opinion.v1",
        "task_id": task.id,
        "report_sha256": digest,
        "exported_at": exported_at,
        "opinion": opinion,
    }
    versions = (db.query(BsReviewVersion)
                .filter(BsReviewVersion.task_id == task.id)
                .order_by(BsReviewVersion.version_no).all())
    version_payload = [{
        "id": row.id,
        "version_no": row.version_no,
        "trigger": row.trigger,
        "snapshot": reference_visibility.visible_version(
            db, task, get_review_version(db, task, row.version_no), user)["snapshot"],
    } for row in versions]
    events = (db.query(BsTaskEvent)
              .filter(BsTaskEvent.task_id == task.id)
              .order_by(BsTaskEvent.sequence, BsTaskEvent.id).all())
    event_payload = [{
        "id": row.id,
        "sequence": row.sequence,
        "event_type": row.event_type,
        "stage": row.stage,
        "payload": event_dto(row)["payload"],
        "created_at": row.created_at.isoformat() if row.created_at else None,
    } for row in events]
    manifest = {
        "schema_version": "bs.evidence-bundle.v2",
        "task_id": task.id,
        "stage": stage,
        "report_sha256": digest,
        "entries": ["report.json", "opinion.json", "review-versions.json",
                    "events.json", "manifest.json"] + [name for name, _ in blobs],
        "chain": {
            "report": "report.json",
            "opinion": "opinion.json",
            "review_versions": "review-versions.json",
            "events": "events.json",
            "materials": "materials/",
        },
    }
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("report.json", json.dumps(payload, ensure_ascii=False,
                                              indent=2, sort_keys=True))
        zf.writestr("opinion.json", json.dumps(opinion_payload, ensure_ascii=False,
                                               indent=2, sort_keys=True))
        zf.writestr("review-versions.json", json.dumps(version_payload,
                                                       ensure_ascii=False,
                                                       indent=2, sort_keys=True))
        zf.writestr("events.json", json.dumps(event_payload, ensure_ascii=False,
                                              indent=2, sort_keys=True))
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False,
                                                indent=2, sort_keys=True))
        for name, data in blobs:
            zf.writestr(name, data)
    return bio.getvalue(), digest, exported_at


# ==================== 审查意见书导出（对齐 118 号文附件 1《分包单位认可表》） ====================

# 字段顺序沿用附件1（工程名称 → 施工总承包单位 → 致直属单位 → 承诺语 → 附件清单 →
# 项目经理 → 分包单位名称 → 表列 → 总监理工程师意见 → 直属单位意见 → 负责人签字），
# 使直属单位拿到纸面即可与原件逐项对照。
_OPINION_SUBJECT_ORDER = ("工程名称", "标段", "施工总承包单位", "分包单位名称",
                          "分包类型", "拟分包金额", "拟分包范围")

_SEAL_PAGES = re.compile(r"图像检测在((?:第[1-9]\d*页、)+第[1-9]\d*页)检出盖章痕迹")


def _compact_seal_pages(message: str) -> str:
    """仅压缩可完整识别的签章页列表；其余历史文本保持原样。"""
    def compact(match):
        pages = [int(page) for page in re.findall(r"第([1-9]\d*)页", match.group(1))]
        value = "图像检测在第%s页检出盖章痕迹" % page_ranges(pages)
        return value if len(value) < len(match.group(0)) else match.group(0)
    return _SEAL_PAGES.sub(compact, message)


def _opinion_summary_lines(opinion: dict) -> list[tuple[str, str]]:
    """预览与导出共用的简版正文，只列人工采纳的问题。"""
    from .rules import load_review_flow

    business_lines = _business_document_lines(opinion)
    if business_lines:
        lines = [("title", "分包报审智能审查意见书")]
        if opinion.get("draft_notice"):
            lines.append(("text", "【%s】%s" % (opinion.get("stage_label", ""), opinion["draft_notice"])))
        lines.extend((kind if kind in {"heading", "subheading"} else "text", text)
                     for kind, text, _bold in business_lines)
        return lines
    lines = [("title", "分包报审智能审查意见书")]
    if opinion.get("draft_notice"):
        lines.append(("text", "【%s】%s" % (opinion.get("stage_label", ""), opinion["draft_notice"])))
    lines.append(("heading", "一、项目及主体信息"))
    for key in _OPINION_SUBJECT_ORDER:
        value = (opinion.get("subject") or {}).get(key)
        if isinstance(value, dict):
            value = "；".join("%s：%s" % (k, v) for k, v in value.items() if v)
        elif isinstance(value, list):
            value = "；".join(str(v) for v in value if v)
        lines.append(("text", "%s：%s" % (key, value or "未填写")))
    lines.append(("text", "核查基准日：%s" % ((opinion.get("meta") or {}).get("baseline_date") or "未填写")))
    for conflict in opinion.get("subject_identity_conflicts") or []:
        lines.append(("text", "主体名称待核对：任务主体为%s；%s识别的分包单位为%s，请核对合同原件。" % (
            conflict.get("subject_name") or "未确认", conflict.get("file_name") or "合同材料",
            conflict.get("contract_name") or "未识别")))
    accepted = [item for item in opinion.get("items") or []
                if (item.get("human_disposition") or {}).get("code") == "accepted"]
    def display_key(item):
        return (item.get("kind"), item.get("label") or item.get("item_id"),
                item.get("status"), " ".join(str(item.get("message") or "").split()))
    duplicates = Counter(display_key(item) for item in accepted)
    lines.extend([("heading", "二、核查摘要"), ("text", "本意见书列示人工采纳的 %d 项核查问题。" % len(accepted))])
    for statement in opinion.get("scope_statements") or []:
        lines.append(("text", "核查范围：%s" % statement))
    coverage = opinion.get("coverage") or {}
    if coverage and not coverage.get("complete"):
        lines.append(("text", "部分核查尚未完成，不能据此认定未完成范围无问题。"))
    if opinion.get("reading_limitations"):
        lines.append(("text", "部分材料可能因模糊或遮挡识别不清，或尚未完成读取，请结合原件核实。"))
    snapshot_steps = (opinion.get("reference_snapshot") or {}).get("steps") or []
    steps = snapshot_steps if snapshot_steps and all("kinds" in step for step in snapshot_steps) else [
        {"title": step["title"], "kinds": [kernel["kind"] for kernel in step.get("kernels", [])]}
        for step in load_review_flow()["steps"]]
    seen = set()

    def add_item(item):
        seen.add(id(item))
        status = "无法核实" if item.get("status") == "undecidable" else "发现问题"
        label = item.get("label") or item.get("item_id") or "核查项"
        message = " ".join(str(item.get("message") or "").split())
        message = _compact_seal_pages(message)
        # 只去除与前置状态和标题完全相同的重复词，不截断缺口、页码或建议。
        if item.get("status") == "undecidable":
            for prefix in ("不可判定：", "无法判断：", "无法核实："):
                if message.startswith(prefix):
                    message = message[len(prefix):].lstrip()
                    break
        repeated_label = "「%s」" % label
        if message.startswith(repeated_label):
            message = message[len(repeated_label):].lstrip()
        evidence = item.get("evidence") or {}
        page, name = evidence.get("page"), evidence.get("file_name")
        source = ("（%s，第 %s 页）" % (name, page)
                  if duplicates[display_key(item)] > 1 and name and type(page) is int and page > 0
                  and evidence.get("location_mode") in ("page", "precise") else "")
        lines.append(("text", "【%s】%s%s%s" % (status, label, "：%s" % message if message else "", source)))
        note = (item.get("human_disposition") or {}).get("note")
        if note and str(note).strip():
            lines.append(("text", "人工补充原文：%s（人工）" % str(note).strip()))

    for step in steps:
        group = [item for item in accepted if item.get("kind") in (step.get("kinds") or [])
                 and id(item) not in seen]
        if group:
            lines.append(("subheading", step.get("title") or "核查问题"))
            for item in group:
                add_item(item)
    remaining = [item for item in accepted if id(item) not in seen]
    if remaining:
        lines.append(("subheading", "其他核查项"))
        for item in remaining:
            add_item(item)
    if not accepted:
        lines.append(("text", "当前没有已采纳的问题。"))
    material_gaps = opinion.get("material_gaps") or []
    if material_gaps:
        lines.append(("heading", "三、材料待核实及补充"))
        for gap in material_gaps:
            state = "需补件" if gap.get("state") == "missing" else "待核实"
            details = "：" + "、".join(gap.get("missing_labels") or []) if gap.get("missing_labels") else ""
            lines.append(("text", "%s：%s（%s）%s" % (gap.get("subject_name") or "主体待确认",
                                                       gap.get("label") or "材料要求", state, details)))
            if gap.get("pending_labels"):
                lines.append(("text", "待核实：" + "、".join(gap["pending_labels"])))
    return lines


def opinion_word_bytes(opinion: dict, digest: str = None, exported_at: str = None) -> bytes:
    """Word 与页面、PDF 使用同一份正文。"""
    doc = Document()
    _configure_word_document(doc)
    if opinion.get("business_document"):
        _add_para(doc, "分包报审智能审查意见书", size=16, bold=True, space_after=8)
        if opinion.get("draft_notice"):
            _add_para(doc, "【%s】%s" % (opinion.get("stage_label", ""), opinion["draft_notice"]),
                      size=10, bold=True, space_after=6)
        _add_business_document_word(doc, opinion)
    else:
        for kind, text in _opinion_summary_lines(opinion):
            size = {"title": 16, "heading": 13, "subheading": 11}.get(kind, 10)
            _add_para(doc, text, size=size, bold=kind != "text", space_after=5)
    digest = digest or report_digest(opinion)
    exported_at = exported_at or datetime.now().isoformat(timespec="seconds")
    _add_para(doc, "意见书校验码：%s（sha256 前 16 位，完整值见证据包） | 导出时间：%s" %
              (digest[:16], exported_at), size=8.5, space_after=0)
    output = io.BytesIO()
    doc.save(output)
    return output.getvalue()


def opinion_markdown(opinion: dict, digest: str) -> str:
    """材料文字转义后呈现，避免改变 Markdown 结构。"""
    result = []
    for kind, text in _opinion_summary_lines(opinion):
        value = str(text).replace("\\", "\\\\")
        for char in chr(96) + "*_{}[]<>#|":
            value = value.replace(char, "\\" + char)
        value = value.replace("\n", "  \n")
        result.append(("# " if kind == "title" else "## " if kind == "heading" else
                       "### " if kind == "subheading" else "") + value)
    return "\n\n".join(result)


def opinion_pdf_bytes(opinion: dict, digest: str = None, exported_at: str = None) -> bytes:
    """PDF 与 Word、页面预览使用同一份正文。"""
    margin, page_w, page_h = 48.0, 595.0, 842.0
    doc = fitz.open()
    page = doc.new_page(width=page_w, height=page_h)
    y = margin

    def write(text, size, bold):
        nonlocal page, y
        for raw in str(text or "").splitlines() or [""]:
            for line in _wrap(raw, size, page_w - margin * 2):
                if y + size * 1.45 > page_h - margin:
                    page = doc.new_page(width=page_w, height=page_h)
                    y = margin
                _pdf_insert_text(page, fitz.Point(margin, y + size), line, fontsize=size)
                y += size * 1.45
        y += 3

    if isinstance(opinion.get("business_document"), dict):
        write("分包报审智能审查意见书", 16, True)
        if opinion.get("draft_notice"):
            write("【%s】%s" % (opinion.get("stage_label", ""), opinion["draft_notice"]), 10, True)
        page, y = _draw_business_pdf(doc, page, y, opinion["business_document"])
    else:
        for kind, text in _opinion_summary_lines(opinion):
            write(text, {"title": 16, "heading": 13, "subheading": 11}.get(kind, 10), kind != "text")
    digest = digest or report_digest(opinion)
    exported_at = exported_at or datetime.now().isoformat(timespec="seconds")
    write("意见书校验码：%s（sha256 前 16 位，完整值见证据包） | 导出时间：%s" %
          (digest[:16], exported_at), 8.5, False)
    return doc.tobytes()


def record_export(db: Session, task: BsTask, kind: str, digest: str,
                  exported_at: str) -> None:
    """导出动作落事件流（时间戳存证的本地载体）。"""
    add_event(db, task.id, "report.exported", payload={
        "format": kind, "report_sha256": digest, "exported_at": exported_at,
    })
