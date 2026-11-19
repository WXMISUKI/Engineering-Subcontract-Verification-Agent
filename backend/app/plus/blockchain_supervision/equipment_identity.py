"""设备原文记录与同编号体系候选对应，不判特种设备适用性。"""
import re

from .attachment_source import is_attachment_source
from .document_boundaries import is_document_boundary
from .parsing import material_page_readable

ITEM_ID = "A2-12"
SOURCE = "设备清单与所附检验、登记、权属或租赁原文辅助核对；设备适用条件及法定周期仍需核实"
_TITLES = {
    "设备清单": "inventory", "施工设备清单": "inventory", "机械设备清单": "inventory",
    "施工机具清单": "inventory", "拟投入设备清单": "inventory",
    "设备检验报告": "inspection", "特种设备定期检验报告": "inspection", "起重机械检验报告": "inspection",
    "特种设备使用登记证": "registration", "设备使用登记证": "registration",
    "设备权属证明": "ownership", "设备租赁合同": "lease", "机械设备租赁合同": "lease",
}
_LABELS = {"name": {"设备名称", "机械名称"}, "model": {"规格型号", "设备型号"},
    "internal_id": {"设备编号"}, "factory_id": {"出厂编号", "产品编号", "设备出厂编号"},
    "registration_id": {"使用登记证编号", "使用登记编号"}, "owner": {"所有权人", "产权单位", "出租方"},
    "user": {"使用单位", "承租方"}, "inspection_date": {"检验日期"},
    "next_inspection": {"下次检验日期", "下次检验时间"}, "period": {"租赁期间", "租赁期限"},
    "inspection_result": {"检验结论"}}
_INDIRECT = re.compile(r"模板|示例|样表|填写说明|应提交|应提供")


def _title(line):
    plain = re.sub(r"\s+", "", line).strip(" #")
    plain = re.sub(r"(?:正本|副本|[（(](?:正本|副本|复印件|电子版)[）)])$", "", plain)
    return _TITLES.get(plain)


def _record(values, base, quote):
    record = {**base, "quote": quote}
    for field in _LABELS:
        candidates = list(dict.fromkeys(value.strip() for value in values.get(field, [])
                          if value.strip() and value.strip() not in {"/", "-", "—", "待填"}))
        record.update({field: candidates[0] if len(candidates) == 1 else None, field + "_candidates": candidates})
    return record if record.get("name") else None


def extract_equipment_records(materials):
    records = []
    for material in materials:
        if material.parse_state != "done" or not is_attachment_source(material) or _INDIRECT.search(material.file_name or ""):
            continue
        for page in material.pages_json or []:
            if not material_page_readable(material, page.get("page")):
                continue
            text = page.get("text") or ""
            if _INDIRECT.search(text):
                continue
            lines = text.splitlines()
            for start, line in enumerate(lines):
                kind = _title(line)
                if kind is None:
                    continue
                end = next((i for i in range(start + 1, len(lines)) if _title(lines[i]) or is_document_boundary(lines[i])), len(lines))
                segment = lines[start:end]
                base = {"material_id": material.id, "subject_id": getattr(material, "subject_id", None),
                        "page": page.get("page"), "evidence_kind": "equipment_" + kind}
                header_index = next((i for i, row in enumerate(segment) if "|" in row and
                    any(cell.strip() in _LABELS["name"] for cell in row.strip().strip("|").split("|"))), None)
                if header_index is not None:
                    header = [cell.strip() for cell in segment[header_index].strip().strip("|").split("|")]
                    for row in segment[header_index + 1:]:
                        if "|" not in row:
                            break
                        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
                        if len(cells) != len(header):
                            continue
                        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                            continue
                        values = {field: [cell for col, cell in zip(header, cells) if col in labels]
                                  for field, labels in _LABELS.items()}
                        record = _record(values, base, segment[0] + "\n" + segment[header_index] + "\n" + row)
                        if record:
                            records.append(record)
                    continue
                starts = [i for i, row in enumerate(segment) if re.match(r"^\s*(?:设备名称|机械名称)\s*[:：]", row)]
                for first, last in zip(starts, starts[1:] + [len(segment)]):
                    local = "\n".join(segment if len(starts) == 1 else [segment[0], *segment[first:last]])
                    values = {field: re.findall(r"(?:^|\n)(?:" + "|".join(map(re.escape, labels)) +
                                               r")\s*[:：]\s*([^\n;；|]+)", local)
                              for field, labels in _LABELS.items()}
                    record = _record(values, base, local)
                    if record:
                        records.append(record)
    return records


def matching_proofs(equipment, proofs, *, inventories=()):
    """只按同主体、同编号体系唯一明示值对应；不按设备名猜身份。"""
    fields = ("internal_id", "factory_id", "registration_id")
    matched = []
    normalize = lambda value: re.sub(r"\s+", "", value).upper()
    for field in fields:
        if equipment.get(field) and sum(row.get("subject_id") == equipment.get("subject_id")
                and bool(row.get(field)) and normalize(row[field]) == normalize(equipment[field])
                for row in inventories) > 1:
            return []
    for proof in proofs:
        if proof.get("subject_id") != equipment.get("subject_id"):
            continue
        shared = [field for field in fields if equipment.get(field) and proof.get(field)]
        if not shared:
            continue
        if any(normalize(equipment[field]) != normalize(proof[field]) for field in shared):
            continue
        if any(len(equipment.get(field + "_candidates", [])) > 1
               or len(proof.get(field + "_candidates", [])) > 1 for field in fields):
            continue
        matched.append(proof)
    return matched
