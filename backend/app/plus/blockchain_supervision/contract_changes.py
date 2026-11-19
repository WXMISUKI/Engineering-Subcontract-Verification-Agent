"""独立合同变更与报审原文，不证明实际变更或审批效力。"""
import re

from .attachment_source import is_attachment_source
from .document_boundaries import is_document_boundary
from .finding_text import bounded_finding_message
from .models import BsFinding, STATUS_UNDECIDABLE
from .parsing import material_page_readable

ITEM_ID = "A3-05-change"
SOURCE = "合同变更重新报审原文辅助核对；适用流程、前后原件及实际审批效力需人工核实"
_TITLES = {"合同变更协议": "change", "分包合同变更协议": "change", "合同补充协议": "change",
           "分包合同补充协议": "change", "合同变更报审表": "approval", "分包变更报审表": "approval"}
_LABELS = {"project": {"项目名称", "工程名称"}, "contract_id": {"合同编号", "原合同编号"},
           "change_id": {"变更编号", "补充协议编号"}, "old_version": {"变更前版本", "原合同版本"},
           "new_version": {"变更后版本", "本次版本"}, "content": {"变更内容", "补充内容"},
           "contractor": {"承包单位"}, "contractor_opinion": {"承包审查意见"}, "contractor_date": {"承包审查日期"},
           "supervisor": {"监理单位"}, "supervisor_opinion": {"监理书面意见", "监理审查意见"},
           "supervisor_date": {"监理签署日期"}, "owner": {"发包单位", "发包人"},
           "owner_opinion": {"发包人意见", "发包人书面意见"}, "owner_date": {"发包人签署日期"}}
_BY_LABEL = {label: key for key, labels in _LABELS.items() for label in labels}
_FIELD = re.compile(r"^\s*(" + "|".join(map(re.escape, _BY_LABEL)) + r")\s*[:：]\s*(.*)$")
_TEMPLATE = re.compile(r"^\s*(?:模板|示例|样例|样表|填写说明|填表说明|附件目录)(?:[:：]|\s*$)")
_IDENTITY = ("project", "contract_id", "change_id", "new_version")


def _title(line):
    return _TITLES.get(re.sub(r"(?:正本|副本|[（(](?:正本|副本|复印件|电子版)[）)])$", "",
        re.sub(r"\s+", "", line).strip(" #")))


def extract_change_records(materials):
    records = []
    for material in materials:
        if (getattr(material, "parse_state", None) != "done" or not is_attachment_source(material)
                or re.search(r"模板|示例|样例|样表", getattr(material, "file_name", "") or "")):
            continue
        for page in material.pages_json or []:
            if not isinstance(page, dict) or not material_page_readable(material, page.get("page")):
                continue
            lines = str(page.get("text") or "").splitlines()
            if any(_TEMPLATE.match(line.strip(" #")) for line in lines):
                continue
            for start, line in enumerate(lines):
                kind = _title(line)
                if not kind:
                    continue
                end = next((i for i in range(start + 1, len(lines)) if _title(lines[i])
                            or is_document_boundary(lines[i])), len(lines))
                segment, values, continuation = lines[start:end], {}, None
                for field_line in segment[1:]:
                    match = _FIELD.match(field_line)
                    if match:
                        key = _BY_LABEL[match.group(1)]
                        values.setdefault(key, []).append(match.group(2).strip())
                        continuation = key if key.endswith("_opinion") or key == "content" else None
                    elif continuation:
                        values[continuation][-1] += "\n" + field_line
                row = {"material_id": material.id, "subject_id": getattr(material, "subject_id", None),
                       "page": page["page"], "evidence_kind": "contract_" + kind, "quote": "\n".join(segment)}
                for key in _LABELS:
                    candidates = list(dict.fromkeys(value.strip() for value in values.get(key, [])
                        if value.strip() and not re.fullmatch(r"[_＿\s/—-]+|待填", value.strip())))
                    row.update({key: candidates[0] if len(candidates) == 1 else None, key + "_candidates": candidates})
                if any(row[key + "_candidates"] for key in (*_IDENTITY, "content",
                       "contractor_opinion", "supervisor_opinion", "owner_opinion")):
                    records.append(row)
    return records


def matching_approvals(change, approvals, *, changes):
    if (not change.get("subject_id") or any(not change.get(key) for key in _IDENTITY)
            or len(change.get("old_version_candidates", [])) > 1):
        return []
    def possible(other):
        return (other.get("subject_id") == change["subject_id"]
            and all(not other.get(key + "_candidates") or change[key] in other[key + "_candidates"] for key in _IDENTITY))
    if sum(possible(other) for other in changes) != 1:
        return []
    return [other for other in approvals if other.get("subject_id") == change["subject_id"]
            and all(other.get(key) == change[key] for key in _IDENTITY)
            and len(other.get("old_version_candidates", [])) <= 1
            and (not other.get("old_version") or not change.get("old_version")
                 or other["old_version"] == change["old_version"])]


def run_change_check(task, materials):
    records = extract_change_records(materials)
    changes = [row for row in records if row["evidence_kind"] == "contract_change"]
    approvals = [row for row in records if row["evidence_kind"] == "contract_approval"]
    groups, used = [], []
    for change in changes:
        matches = matching_approvals(change, approvals, changes=changes)
        groups.append((change, [change, *matches], "同身份报审候选" if matches else "无唯一对应的报审候选"))
        used.extend(matches)
    groups.extend((row, [row], "孤立报审记录，尚无唯一对应的变更协议") for row in approvals if row not in used)
    findings = []
    for anchor, refs, state in groups:
        details = [label + "：" + " / ".join(row[key + "_candidates"]) for row in refs
                   for key, label in (("content", "变更内容"), ("contractor_opinion", "承包原文意见"),
                       ("supervisor_opinion", "监理原文意见"), ("owner_opinion", "发包原文意见")) if row[key + "_candidates"]]
        message = "合同变更辅助核对：合同%s，变更%s，前版本%s，后版本%s；%s；%s；前后版本仅为声明，实际内容差异、适用流程、签章和实施前报审时序请核原件，不因日期或同意字样判审批完成" % (
            anchor["contract_id"] or "待核实", anchor["change_id"] or "待核实", anchor["old_version"] or "待核实",
            anchor["new_version"] or "待核实", state, "；".join(details) or "原文意见尚待核对")
        findings.append(BsFinding(task_id=task.id, item_id=ITEM_ID, kind="signature_chain", status=STATUS_UNDECIDABLE,
            finding_semantics="manual_prompt", source=SOURCE, material_id=anchor["material_id"], page=anchor["page"],
            quote=anchor["quote"], evidence_json=refs, location_mode="page", message=bounded_finding_message(message)))
    return findings
