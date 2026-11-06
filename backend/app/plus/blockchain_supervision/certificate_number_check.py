"""同一主体内人员名单和实际交安 B 证编号对照。"""
import re
import unicodedata

from . import pinning
from .attachment_source import is_attachment_source
from .models import BsFinding, STATUS_HIT, STATUS_UNDECIDABLE
from .parsing import material_reading_gap, reading_coverage
from .personnel_evidence import extract_management_roster

ITEM_ID = "A2-05"
SOURCE = "对照报审材料中的人员名单与交安 B 证编号原文"
_NUMBER = re.compile(r"[\u4e00-\u9fff]交安\s*B\s*[(（]?\s*\d{2}\s*[)）]?\s*G\s*\d{5}(?![A-Za-z0-9])", re.I)
_ROLE = {"项目经理", "项目总工", "安全员", "材料员", "资料员", "标准员", "质量员", "施工员", "劳务员", "机械员"}


def normalize_b_number(value):
    text = unicodedata.normalize("NFKC", str(value or ""))
    match = _NUMBER.fullmatch(text.strip())
    return re.sub(r"[\s()]", "", match.group()).upper() if match else None


def _numbers(text):
    return [(normalize_b_number(m.group()), m.group()) for m in _NUMBER.finditer(text or "")]


def _blocks(page):
    meta = page.get("ocr_meta") or {}
    if not isinstance(meta, dict) or meta.get("quality") != "ocr_ok":
        return []
    result = []
    for block in meta.get("layout") or []:
        if not isinstance(block, dict) or not isinstance(block.get("text"), str):
            continue
        box = block.get("bbox")
        if (not isinstance(box, (list, tuple)) or len(box) != 4
                or any(type(v) not in (int, float) for v in box)
                or box[2] <= box[0] or box[3] <= box[1]):
            continue
        result.append((block["text"].strip(), box))
    return result


def _page_readable(material, page):
    gap = material_reading_gap(material)
    if gap is None:
        return True
    if not gap:
        return False
    coverage = reading_coverage(material)
    failed = {number for record in getattr(material, "degradations_json", None) or []
              if isinstance(record, dict) and record.get("kind") == "parse_incomplete"
              for number in record.get("ocr_failed_pages") or [] if type(number) is int}
    return page.get("page") not in set(gap) | set(coverage["suspect_pages"]) | failed


def _roster_candidates(material):
    rows = []
    for page in material.pages_json or []:
        if not _page_readable(material, page):
            continue
        text = page.get("text") or ""
        if "名单" not in text:
            continue
        blocks = _blocks(page)
        headers = {name: [b for t, b in blocks if t == name] for name in ("姓名", "职务")}
        cert_headers = [b for t, b in blocks if "持证" in t and "证号" in t]
        if any(len(v) != 1 for v in headers.values()) or len(cert_headers) != 1:
            continue
        name_head, role_head, cert_head = headers["姓名"][0], headers["职务"][0], cert_headers[0]
        if not (name_head[2] < role_head[0] and role_head[2] < cert_head[0]):
            continue
        role_blocks = sorted([(t, b) for t, b in blocks if t in _ROLE and b[1] > role_head[3]
                              and name_head[2] < (b[0] + b[2]) / 2 < cert_head[0]],
                             key=lambda v: (v[1][1] + v[1][3]) / 2)
        for index, (role, box) in enumerate(role_blocks):
            if role != "项目经理":
                continue
            y = (box[1] + box[3]) / 2
            upper = ((y + (role_blocks[index + 1][1][1] + role_blocks[index + 1][1][3]) / 2) / 2
                     if index + 1 < len(role_blocks) else box[3])
            name_hits = [(t, b) for t, b in blocks if re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", t)
                         and name_head[0] <= (b[0] + b[2]) / 2 <= name_head[2]
                         and min(b[3], box[3]) - max(b[1], box[1]) > 0]
            number_hits = [(num, raw) for t, b in blocks
                           if cert_head[0] - (cert_head[2] - cert_head[0]) <= b[0] <= cert_head[2]
                           and box[1] <= (b[1] + b[3]) / 2 < upper
                           for num, raw in _numbers(t)]
            if len(name_hits) == len(number_hits) == 1:
                name = name_hits[0][0]
                num, raw = number_hits[0]
                if name in text and unicodedata.normalize("NFKC", raw) in unicodedata.normalize("NFKC", text):
                    rows.append({"name": name, "number": num, "quote": raw, "material_id": material.id, "page": page.get("page")})
    return rows


def roster_b_entries(materials: list) -> list[dict]:
    """名单中可唯一对应到本人的 B 证编号证据（关系层复用，不代替本模块判定）。

    优先取 OCR 布局线索（姓名/岗位/证号同行且唯一）；布局不可用时退回文本表格
    续行，仍要求姓名+岗位唯一对应且只命中一个 B 证编号。取不到就返回空，不猜。
    ``number_source`` 标明编号来自布局还是文本表格，便于复核。
    """
    rows = [{**row, "number_source": "layout"}
            for material in materials for row in _roster_candidates(material)]
    by_material = {material.id: material for material in materials}
    for fact in extract_management_roster(materials):
        material = by_material.get(fact.get("material_id"))
        if material is None:
            continue
        page = next((p for p in material.pages_json or []
                     if isinstance(p, dict) and p.get("page") == fact.get("page")), None)
        if page is None or not _page_readable(material, page):
            continue
        numbers = {}
        for number, raw in _roster_text_numbers(page, fact):
            numbers.setdefault(number, raw)
        if len(numbers) != 1:
            continue
        number, raw = next(iter(numbers.items()))
        rows.append({"name": fact.get("name"), "number": number, "quote": raw,
                     "material_id": fact.get("material_id"), "page": fact.get("page"),
                     "number_source": "text_table"})
    # 同一材料同一人若出现多个互斥编号，宁可判无法核实，也不择一采信。
    grouped: dict = {}
    for row in rows:
        key = (row.get("material_id"),
               re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(row.get("name") or ""))))
        grouped.setdefault(key, []).append(row)
    return [items[0] for items in grouped.values()
            if items[0].get("number") and len({item.get("number") for item in items}) == 1]

def _roster_text_numbers(page, fact):
    numbers = _numbers(fact.get("quote") or "")
    if numbers:
        return numbers
    # 识别表格可能把持证信息拆成空姓名续行，且列发生偏移；只能作为人工线索。
    lines = (page.get("text") or "").splitlines()
    starts = []
    for index, line in enumerate(lines):
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 3 and cells[1:3] == [fact["name"], fact["post"]]:
            starts.append(index)
    if len(starts) != 1:
        return []
    continuation = []
    for line in lines[starts[0] + 1:]:
        if "|" not in line:
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3 or cells[0] or cells[1]:
            break
        continuation.append(line)
    text = "\n".join(continuation)
    return _numbers(text) if re.search(r"交安\s*B\s*证", text, re.I) else []


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
