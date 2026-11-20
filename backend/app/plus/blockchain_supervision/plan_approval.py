"""独立施工方案身份及审批原文候选，不认证审批效力。"""
import re

from ..startup_conditions_check.validity import parse_date_text
from .attachment_source import is_attachment_source
from .document_boundaries import is_document_boundary
from .parsing import material_page_readable
from .finding_text import bounded_finding_message
from .models import BsFinding, STATUS_UNDECIDABLE

ITEM_ID = "A3-03-plan"
SOURCE = "《沪交行规〔2026〕8号》第十四条；仅辅助核对方案编制、承包审查和监理书面意见原文"
_TITLES = {"施工方案": "plan", "专项施工方案": "plan", "施工方案报审表": "approval",
           "专项施工方案报审表": "approval", "施工方案审批表": "approval"}
_LABELS = {"project": {"项目名称", "工程名称"}, "name": {"方案名称"},
           "plan_id": {"方案编号"}, "version": {"方案版本", "版本号"},
           "compiler": {"分包编制单位", "编制单位"}, "compile_date": {"编制日期"},
           "contractor": {"承包审查单位", "承包单位"}, "contractor_opinion": {"承包审查意见", "承包人审查意见"},
           "contractor_date": {"承包审查日期", "承包人审查日期"},
           "supervisor": {"监理单位"}, "supervisor_opinion": {"监理书面意见", "监理审查意见"},
           "supervisor_date": {"监理签署日期", "监理审查日期"}}
_INDIRECT = re.compile(r"模板|示例|样表|填写说明|应提交|应提供")
_FIELD_BY_LABEL = {label: field for field, labels in _LABELS.items() for label in labels}
_FIELD_LINE = re.compile(r"^\s*(" + "|".join(map(re.escape, _FIELD_BY_LABEL)) + r")\s*[:：]\s*(.*)$")


def _title(line):
    title = re.sub(r"\s+", "", line).strip(" #")
    title = re.sub(r"(?:正本|副本|[（(](?:正本|副本|复印件|电子版)[）)])$", "", title)
    return _TITLES.get(title)


def _fields(lines):
    values, outside, opinion = {}, [], None
    for line in lines:
        if re.match(r"^\s*(?:填写说明\s*[:：]|(?:模板|示例|样表)\s*(?:[:：].*)?$)", line):
            outside.append(line)
            opinion = None
            continue
        match = _FIELD_LINE.match(line)
        if match:
            field = _FIELD_BY_LABEL[match.group(1)]
            values.setdefault(field, []).append(match.group(2).strip())
            opinion = field if field.endswith("_opinion") else None
            if not opinion:
                outside.append(line)
        elif opinion:
            values[opinion][-1] += "\n" + line
        else:
            outside.append(line)
    return values, "\n".join(outside)


def extract_plan_records(materials):
    records = []
    for material in materials:
        if material.parse_state != "done" or not is_attachment_source(material) or _INDIRECT.search(material.file_name or ""):
            continue
        for page in material.pages_json or []:
            if not material_page_readable(material, page.get("page")):
                continue
            lines = (page.get("text") or "").splitlines()
            for start, line in enumerate(lines):
                kind = _title(line)
                if kind is None:
                    continue
                end = next((i for i in range(start + 1, len(lines))
                            if _title(lines[i]) or is_document_boundary(lines[i])), len(lines))
                quote = "\n".join(lines[start:end])
                values, outside = _fields(lines[start:end])
                if _INDIRECT.search(outside):
                    continue
                record = {"material_id": material.id, "subject_id": getattr(material, "subject_id", None),
                          "page": page.get("page"), "quote": quote, "evidence_kind": "plan_" + kind}
                for field in _LABELS:
                    candidates = list(dict.fromkeys(value.strip() for value in values.get(field, [])
                        if value.strip() and not re.fullmatch(r"[_＿\s/—-]+|待填", value.strip())))
                    record.update({field: candidates[0] if len(candidates) == 1 else None,
                                   field + "_candidates": candidates})
                if record["name"] or record["plan_id"]:
                    records.append(record)
    return records


def matching_approvals(plan, records, *, plans):
    fields = ("subject_id", "project", "plan_id", "version")
    conflicted = lambda row: any(len(row.get(field + "_candidates", [])) > 1
                                for field in ("project", "name", "plan_id", "version"))
    if conflicted(plan) or any(not plan.get(field) for field in fields):
        return []
    same = lambda row: all(row.get(field) == plan[field] for field in fields)
    def possible(row):
        if row.get("subject_id") != plan["subject_id"]:
            return False
        for field in ("project", "plan_id", "version"):
            candidates = row.get(field + "_candidates") or ([row[field]] if row.get(field) else [])
            if candidates and plan[field] not in candidates:
                return False
        return True
    if sum(possible(row) for row in plans) != 1:
        return []
    return [row for row in records if same(row) and not conflicted(row)
            and (not row.get("name") or not plan.get("name") or row["name"] == plan["name"])]


def date_observation(records):
    dates = []
    for field in ("compile_date", "contractor_date", "supervisor_date"):
        values = {value for row in records for value in row.get(field + "_candidates", [])}
        if len(values) != 1:
            return "三侧日期缺失或存在冲突，尚不比较顺序"
        value = next(iter(values))
        if not re.fullmatch(r"\d{4}(?:-\d{1,2}-\d{1,2}|/\d{1,2}/\d{1,2}|年\d{1,2}月\d{1,2}日)", value):
            return "日期不能唯一解析，尚不比较顺序"
        parsed = parse_date_text(value)
        if parsed is None:
            return "日期不能唯一解析，尚不比较顺序"
        dates.append(parsed)
    return ("原文日期按编制、承包审查、监理签署顺序排列" if dates[0] <= dates[1] <= dates[2]
            else "原文日期未按编制、承包审查、监理签署顺序排列")


def run_plan_approval_check(task, materials):
    records = extract_plan_records(materials)
    plans = [row for row in records if row["evidence_kind"] == "plan_plan"]
    approvals = [row for row in records if row["evidence_kind"] == "plan_approval"]
    groups, used = [], []
    for item in plans:
        matched = matching_approvals(item, approvals, plans=plans)
        groups.append((item, [item, *matched], "方案与对应候选" if matched else "方案尚无可唯一对应的独立报审记录"))
        used.extend(matched)
    groups.extend((row, [row], "报审记录尚无可唯一对应的方案") for row in approvals if row not in used)
    findings = []
    for item, refs, state in groups:
        details = []
        for row in refs:
            values = [label + "：" + "、".join(row.get(field + "_candidates", []))
                      for field, label in (("compiler", "编制单位"), ("contractor", "承包审查单位"),
                          ("contractor_opinion", "承包原文意见"), ("supervisor", "监理单位"),
                          ("supervisor_opinion", "监理原文意见")) if row.get(field + "_candidates")]
            details.extend(values)
        message = "方案辅助核对：%s（编号%s，版本%s）；%s；%s；%s；适用性、实际签章及意见效力请核原件，日期排列不证明审批完成或实施合法" % (
            item.get("name") or "名称待核实", item.get("plan_id") or "待核实", item.get("version") or "待核实",
            state, "；".join(details) or "三侧单位和原文意见尚待核对", date_observation(refs))
        findings.append(BsFinding(task_id=task.id, item_id=ITEM_ID, kind="signature_chain", status=STATUS_UNDECIDABLE,
            finding_semantics="manual_prompt", source=SOURCE, message=bounded_finding_message(message),
            material_id=item["material_id"], page=item["page"], quote=item["quote"], location_mode="page", evidence_json=refs))
    return findings
