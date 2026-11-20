"""考勤/任职原文候选，不计算到岗率或认定同时受聘。"""
import re

from .attachment_source import is_attachment_source
from .document_boundaries import is_document_boundary
from .personnel_evidence import identity_number_candidates, normalize_identity_number

ITEM_ID = "A2-11"
SOURCE = "在岗与项目归属辅助核对；政策解读问题3属B类且归档引文待核实，不据此自动作法定结论"
_TITLES = {"人员考勤记录": "attendance_record", "考勤记录": "attendance_record",
           "人员考勤表": "attendance_record", "考勤表": "attendance_record",
           "项目任职记录": "project_assignment", "人员任职记录": "project_assignment"}
_INDIRECT = re.compile(r"模板|示例|样例|样表|填表说明|应提交|应提供")
_LABELS = {"name": {"姓名"}, "project": {"项目名称", "工程名称"},
           "period": {"考勤日期", "考勤期间", "任职期间", "任职日期", "日期"},
           "attendance_state": {"出勤状态", "考勤状态", "任职状态"}}


def _values(text, labels):
    return list(dict.fromkeys(value.strip() for value in re.findall(
        r"(?:^|\n)(?:" + "|".join(map(re.escape, labels)) + r")\s*[:：]\s*([^\n;；|]+)", text)
        if value.strip() not in {"/", "-", "—", "待填"}))


def _record(fields, base, quote, *, numbers=None):
    values = {key: list(dict.fromkeys(values)) for key, values in fields.items()}
    names = [value for value in values.get("name", []) if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", value)]
    if len(names) != 1:
        return None
    result = {**base, "quote": quote, "name": names[0]}
    for key in _LABELS:
        candidates = values.get(key, [])
        result.update({key: candidates[0] if len(candidates) == 1 else None, key + "_candidates": candidates})
    numbers = identity_number_candidates(quote) if numbers is None else numbers
    return {**result, "id_number": numbers[0] if len(numbers) == 1 else None, "id_number_candidates": numbers}


def extract_attendance_records(materials):
    records = []
    for material in materials:
        if material.parse_state != "done" or not is_attachment_source(material) or _INDIRECT.search(material.file_name or ""):
            continue
        for page in material.pages_json or []:
            text = page.get("text") or ""
            if _INDIRECT.search(text):
                continue
            lines = text.splitlines()
            for start, line in enumerate(lines):
                title = re.sub(r"\s+", "", line).strip(" #")
                if title not in _TITLES:
                    continue
                end = next((i for i in range(start + 1, len(lines))
                            if is_document_boundary(lines[i])), len(lines))
                segment = lines[start:end]
                base = {"material_id": material.id, "subject_id": getattr(material, "subject_id", None),
                        "page": page.get("page"), "evidence_kind": _TITLES[title]}
                header_index = next((i for i, row in enumerate(segment) if "|" in row and "姓名" in row), None)
                if header_index is not None:
                    header = [cell.strip() for cell in segment[header_index].strip().strip("|").split("|")]
                    for row in segment[header_index + 1:]:
                        if "|" not in row:
                            break
                        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
                        if len(cells) != len(header):
                            continue
                        fields = {key: [cell for col, cell in zip(header, cells) if col in labels and cell]
                                  for key, labels in _LABELS.items()}
                        numbers = list(dict.fromkeys(value for col, cell in zip(header, cells)
                            if col in {"身份证号", "身份证号码", "公民身份号码"}
                            for value in [normalize_identity_number(cell)] if value))
                        record = _record(fields, base, segment[0] + "\n" + segment[header_index] + "\n" + row, numbers=numbers)
                        if record:
                            records.append(record)
                    continue
                starts = [i for i, row in enumerate(segment) if re.match(r"^\s*姓名\s*[:：]", row)]
                for first, last in zip(starts, starts[1:] + [len(segment)]):
                    local = "\n".join(segment[first:last])
                    # 只有单人文档允许使用该段明确的公共项目/期间表头。
                    fields = {key: _values("\n".join(segment) if len(starts) == 1 else local, labels)
                              for key, labels in _LABELS.items()}
                    record = _record(fields, base, "\n".join(segment) if len(starts) == 1 else segment[0] + "\n" + local)
                    if record:
                        records.append(record)
    return records
