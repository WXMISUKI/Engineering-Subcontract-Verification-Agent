"""独立培训记录原文候选，不据签到/考核文字判准予上岗。"""
import re

from .attachment_source import is_attachment_source
from .document_boundaries import is_document_boundary
from .finding_text import bounded_finding_message
from .models import BsFinding, STATUS_UNDECIDABLE
from .parsing import material_page_readable
from .personnel_evidence import identity_number_candidates, normalize_identity_number

ITEM_ID = "A2-13"
SOURCE = "劳务培训原文辅助核对（SHJT-2026-8第四条、MOT-2024-2第二十五条归档待人工校对），不自动判上岗合规"
_TITLES = {"劳务人员培训记录", "岗前培训记录", "培训记录", "培训签到表", "岗前培训签到表",
           "培训考核记录", "岗前培训考核记录", "安全教育培训记录", "安全教育培训签到表",
           "安全技术交底记录", "岗前交底记录"}
_LABELS = {"name": {"姓名", "人员姓名"}, "post": {"工种", "岗位"}, "project": {"项目名称", "工程名称"},
           "course": {"培训内容", "培训课程", "课程名称", "交底内容"},
           "period": {"培训日期", "培训时间", "交底日期", "考核日期"},
           "organizer": {"培训单位", "组织单位", "交底单位"}, "result": {"考核结果", "培训结果", "签到情况", "原文结果"}}
_TEMPLATE = re.compile(r"模板|示例|样例|样表|填写说明|填表说明")


def _plain_title(line):
    return re.sub(r"(?:正本|副本|[（(](?:正本|副本|复印件|电子版)[）)])$", "",
                  re.sub(r"\s+", "", line).strip(" #"))


def _fields(text):
    return {key: list(dict.fromkeys(value.strip() for value in re.findall(
        r"(?:^|\n)\s*(?:" + "|".join(map(re.escape, labels)) + r")\s*[:：]\s*([^\n]+)", text)
        if value.strip() not in {"/", "—", "-", "待填", "____"})) for key, labels in _LABELS.items()}


def _record(fields, base, quote, numbers):
    if len(fields["name"]) != 1 or not re.fullmatch(r"[\u4e00-\u9fff]{2,4}", fields["name"][0]):
        return None
    row = {**base, "quote": quote, "id_number_candidates": numbers,
           "id_number": numbers[0] if len(numbers) == 1 else None}
    for key, values in fields.items():
        values = list(dict.fromkeys(values))
        row.update({key: values[0] if len(values) == 1 else None, key + "_candidates": values})
    return row


def extract_training_records(materials):
    records = []
    for material in materials:
        if (getattr(material, "parse_state", None) != "done" or not is_attachment_source(material)
                or _TEMPLATE.search(getattr(material, "file_name", "") or "")):
            continue
        for page in material.pages_json or []:
            if not isinstance(page, dict) or not material_page_readable(material, page.get("page")):
                continue
            lines = str(page.get("text") or "").splitlines()
            if any(_TEMPLATE.match(item.strip(" #")) for item in lines):
                continue
            for start, line in enumerate(lines):
                if _plain_title(line) not in _TITLES:
                    continue
                end = next((i for i in range(start + 1, len(lines)) if _plain_title(lines[i]) in _TITLES
                            or is_document_boundary(lines[i])), len(lines))
                segment = lines[start:end]
                if any(_TEMPLATE.match(item.strip(" #")) for item in segment):
                    continue
                base = {"material_id": material.id, "subject_id": getattr(material, "subject_id", None),
                        "page": page["page"], "evidence_kind": "training_record", "document_title": line.strip(),
                        "record_type": "signin" if "签到" in line else "briefing" if "交底" in line else "training"}
                header_i = next((i for i, line in enumerate(segment) if "|" in line
                    and set(cell.strip() for cell in line.strip().strip("|").split("|")) & _LABELS["name"]), None)
                if header_i is not None:
                    header = [cell.strip() for cell in segment[header_i].strip().strip("|").split("|")]
                    common = _fields("\n".join(segment[:header_i]))
                    for line in segment[header_i + 1:]:
                        if "|" not in line:
                            break
                        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
                        if any(cell in _LABELS["name"] for cell in cells):
                            break
                        if len(cells) != len(header):
                            continue
                        fields = {key: [cell for col, cell in zip(header, cells) if col in labels and cell]
                                  or (common[key] if key not in {"name", "post", "result"} else [])
                                  for key, labels in _LABELS.items()}
                        numbers = list(dict.fromkeys(value for col, cell in zip(header, cells)
                            if col in {"身份证号", "身份证号码", "公民身份号码"}
                            for value in [normalize_identity_number(cell)] if value))
                        row = _record(fields, base, "\n".join(segment[:header_i + 1] + [line]), numbers)
                        if row:
                            records.append(row)
                else:
                    text = "\n".join(segment)
                    row = _record(_fields(text), base, text, identity_number_candidates(text))
                    if row:
                        records.append(row)
    return records


def run_training_check(task, materials):
    return [BsFinding(task_id=task.id, item_id=ITEM_ID, kind="personnel_adequacy", status=STATUS_UNDECIDABLE,
        finding_semantics="manual_prompt", source=SOURCE, material_id=row["material_id"], page=row["page"],
        quote=row["quote"], location_mode="page", evidence_json=[row],
        message=bounded_finding_message("培训辅助核对：%s；项目%s，工种%s，日期%s，课程%s，原文结果%s；"
            "身份、实际签字、培训内容与作业适配需核原件，签到不证明考核合格，培训不替代特种作业证或准予上岗" % (
                row["name"], row["project"] or "待核实", row["post"] or "待核实", row["period"] or "待核实",
                row["course"] or "待核实", row["result"] or "待核实"))) for row in extract_training_records(materials)]
