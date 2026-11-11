"""同一主体内人员名单和实际交安 B 证编号对照（A2-05 专用视图）。

通用 A/B/C 提取已下沉到 ``roster_certificate_evidence``；本模块只保留
A2-05 的业务范围：项目经理 + 交安 B 证。
"""
import re

from . import pinning
from .attachment_source import is_attachment_source
from .models import BsFinding, STATUS_HIT, STATUS_UNDECIDABLE
from .personnel_evidence import extract_management_roster
from .roster_certificate_evidence import (
    extract_numbers,
    layout_certificate_entries,
    normalize_number,
    page_readable,
    roster_certificate_entries,
    text_numbers_for_fact,
)

ITEM_ID = "A2-05"
SOURCE = "对照报审材料中的人员名单与交安 B 证编号原文"


def normalize_b_number(value):
    number, certificate_type = normalize_number(value)
    return number if certificate_type == "B" else None


def _numbers(text):
    return [(number, raw) for number, raw, certificate_type in extract_numbers(text)
            if certificate_type == "B"]


def _page_readable(material, page):
    return page_readable(material, page)


def _roster_candidates(material):
    return [row for row in layout_certificate_entries([material])
            if row.get("certificate_type") == "B" and row.get("post") == "项目经理"]


def roster_b_entries(materials: list) -> list[dict]:
    """A2-05 兼容视图：只保留项目经理 + B 证，且同一材料同一人编号唯一。

    通用 A/B/C 证据请使用 ``roster_certificate_evidence.roster_certificate_entries``。
    """
    rows = [row for row in roster_certificate_entries(materials)
            if row.get("certificate_type") == "B" and row.get("post") == "项目经理"]
    grouped: dict = {}
    for row in rows:
        key = (row.get("material_id"), re.sub(r"\s+", "", str(row.get("name") or "")))
        grouped.setdefault(key, []).append(row)
    return [items[0] for items in grouped.values()
            if items[0].get("number")
            and len({item.get("number") for item in items}) == 1]


def _roster_text_numbers(page, fact):
    return [(number, raw) for number, raw, certificate_type, _quote
            in text_numbers_for_fact(page, fact)
            if certificate_type == "B"]


def _text_roster_comparison(task, materials):
    """布局不可用时保留可定位的候选差异，不以同名代替身份核实。"""
    by_id = {material.id: material for material in materials}
    rows = []
    for fact in extract_management_roster(materials):
        if fact.get("post") != "项目经理":
            continue
        material = by_id.get(fact.get("material_id"))
        page = next((p for p in material.pages_json or [] if p.get("page") == fact.get("page")), None) if material else None
        numbers = _roster_text_numbers(page, fact) if page else []
        if page and _page_readable(material, page) and len(numbers) == 1:
            rows.append({**fact, "number": numbers[0][0], "raw": numbers[0][1]})
    if len(rows) != 1:
        return None
    row = rows[0]
    certs = []
    for material in materials:
        for page in material.pages_json or []:
            if not _page_readable(material, page) or (material.id == row["material_id"] and page.get("page") == row["page"]):
                continue
            text = page.get("text") or ""
            holders = re.findall(r"姓\s*名\s*[:：]\s*([\u4e00-\u9fa5]{2,4})", text)
            numbers = _numbers(text)
            if "考核" in text and "证书" in text and holders == [row["name"]] and len(numbers) == 1:
                certs.append({"material_id": material.id, "page": page.get("page"),
                              "number": numbers[0][0], "raw": numbers[0][1]})
    if len(certs) != 1 or row["number"] == certs[0]["number"]:
        return None
    cert = certs[0]
    refs = []
    for side in (row, cert):
        ref = {"material_id": side["material_id"], "page": side["page"], "quote": side["raw"]}
        probe = BsFinding(task_id=task.id, material_id=ref["material_id"], page=ref["page"],
                          quote=ref["quote"], evidence_json=[ref])
        pinning.pin_findings([probe], materials)
        if probe.location_mode != "precise" or probe.page != ref["page"]:
            return None
        pinned = probe.evidence_json[0]
        refs.append({**pinned, "location_mode": pinning.reference_location_mode(pinned)})
    return BsFinding(task_id=task.id, item_id=ITEM_ID, kind="cert_validity", status=STATUS_UNDECIDABLE,
                     source=SOURCE, material_id=row["material_id"], page=row["page"], quote=row["raw"],
                     evidence_json=refs,
                     message="项目经理%s的人员名单第%s页有编号线索%s，同名证书第%s页编号为%s；请核实名单对应关系及正确编号"
                     % (row["name"], row["page"], row["raw"], cert["page"], cert["raw"]))


def run_b_certificate_number_check(task, materials):
    """独立证据不足时不推断一致或冲突；仅比较同页能唯一识别持有人的证书。"""
    materials = [m for m in materials if is_attachment_source(m) and m.parse_state == "done"]
    roster = [row for material in materials for row in _roster_candidates(material)]
    if not roster:
        candidate = _text_roster_comparison(task, materials)
        if candidate:
            return [candidate]
        if any("项目经理" in (page.get("text") or "") and _numbers(page.get("text") or "")
               for material in materials for page in material.pages_json or []):
            return [BsFinding(task_id=task.id, item_id=ITEM_ID, kind="cert_validity", status=STATUS_UNDECIDABLE, source=SOURCE,
                              message="项目经理名单或证书编号无法唯一对应，请人工核对")]
        return []
    if len(roster) != 1:
        return [BsFinding(task_id=task.id, item_id=ITEM_ID, kind="cert_validity", status=STATUS_UNDECIDABLE, source=SOURCE,
                          message="项目经理名单或证书编号无法唯一对应，请人工核对")]
    row = roster[0]
    certs = []
    for material in materials:
        for page in material.pages_json or []:
            if not _page_readable(material, page):
                continue
            text = page.get("text") or ""
            if page.get("page") == row["page"] and material.id == row["material_id"]:
                continue
            holder = re.search(r"姓\s*名\s*[:：]\s*([\u4e00-\u9fa5]{2,4})", text)
            if "考核" not in text or "证书" not in text or not holder or holder.group(1) != row["name"]:
                continue
            numbers = _numbers(text)
            if len(numbers) == 1:
                num, raw = numbers[0]
                certs.append({"number": num, "quote": raw, "material_id": material.id, "page": page.get("page")})
    if len(certs) != 1:
        return [BsFinding(task_id=task.id, item_id=ITEM_ID, kind="cert_validity", status=STATUS_UNDECIDABLE, source=SOURCE,
                          message="项目经理名单与交安 B 证不能唯一对应，请人工核对",
                          material_id=row["material_id"], page=row["page"], quote=row["quote"])]
    cert = certs[0]
    if row["number"] == cert["number"]:
        return []
    refs = []
    for side in (row, cert):
        ref = {key: side[key] for key in ("material_id", "page", "quote")}
        probe = BsFinding(task_id=task.id, material_id=ref["material_id"], page=ref["page"],
                          quote=ref["quote"], evidence_json=[ref])
        pinning.pin_findings([probe], materials)
        if probe.location_mode != "precise" or probe.page != ref["page"]:
            return [BsFinding(task_id=task.id, item_id=ITEM_ID, kind="cert_validity", status=STATUS_UNDECIDABLE,
                              source=SOURCE, message="名单或证书编号原文无法在对应页核实，请人工核对")]
        pinned = probe.evidence_json[0]
        refs.append({**pinned, "location_mode": pinning.reference_location_mode(pinned)})
    return [BsFinding(task_id=task.id, item_id=ITEM_ID, kind="cert_validity", status=STATUS_HIT, source=SOURCE,
                      message="项目经理交安 B 证编号不一致：名单 %s，证书 %s，请核对原件" % (row["number"], cert["number"]),
                      material_id=row["material_id"], page=row["page"], quote=row["quote"],
                      evidence_json=refs)]
