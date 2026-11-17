"""报审表和工程量清单中的报告字段线索，不改变核查规则。"""

import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from types import SimpleNamespace

from .parsing import material_page_readable


_PARTY_LABELS = {
    "general_contractor": re.compile(r"施工总承包单位[ \t]*[:：][ \t]*([^\n|；;]{2,100})"),
    "employer": re.compile(r"(?:建设单位|业主)[ \t]*[:：][ \t]*([^\n|；;]{2,100})"),
    "subcontractor": re.compile(r"(?:分包单位名称|分包单位)[ \t]*[:：][ \t]*([^\n|；;]{2,100})"),
}
_COMPANY_END = re.compile(r"^(.+?(?:有限责任公司|股份有限公司|有限公司|集团公司))")
_REPORT_FORM = re.compile(r"(?:劳务|专业|工程)?分包报审表|分包单位认可表")
_REPORT_RECIPIENT = re.compile(r"^致\s*[:：]\s*(.+)$")
_TITLE = re.compile(r"^(.{2,120}?(?:专业|劳务)分包)工程量清单\s*$")
_PROJECT = re.compile(r"^.{2,120}(?:工程|公路|项目).{0,30}(?:标|标段)\s*$")
_SECTION = re.compile(r"(主线施工\s*[0-9一二三四五六七八九十]+\s*标)\s*$")
_AMOUNT = re.compile(r"(?:拟分包金额|分包金额)[ \t]*[:：][ \t]*([0-9]+(?:\.[0-9]+)?)[ \t]*(万元|元)")
_AMOUNT_VALUE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)[ \t]*(万元|元)$")
_ROAD_RANGE = re.compile(r"[（(]([GS]\d+公路\s*[-－—至]\s*[GS]\d+公路)[）)]", re.I)


def _plain_text(text):
    return re.sub(r"\$?\\text\s*\{\s*([^{}]+?)\s*\}\$?", r"\1", str(text or ""))


def _canonical(text):
    text = unicodedata.normalize("NFKC", str(text or ""))
    return re.sub(r"\s+", "", text).translate(str.maketrans({"—": "-", "–": "-", "－": "-", "〜": "~", "～": "~"}))


class _HTMLTables(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables, self.rows, self.row, self.cell = [], None, None, None

    def handle_starttag(self, tag, attrs):
        if tag == "table": self.rows = []
        elif tag == "tr" and self.rows is not None: self.row = []
        elif tag in {"td", "th"} and self.row is not None: self.cell = []

    def handle_data(self, data):
        if self.cell is not None: self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.cell is not None:
            self.row.append("".join(self.cell).strip())
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None
        elif tag == "table" and self.rows is not None:
            self.tables.append(self.rows)
            self.rows = None


def _tables(text):
    parser = _HTMLTables()
    parser.feed(text)
    tables, rows = parser.tables, []
    for line in text.splitlines() + [""]:
        if "|" in line:
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if not all(re.fullmatch(r"[:\- ]*", cell) for cell in cells): rows.append(cells)
        elif rows:
            tables.append(rows)
            rows = []
    return tables


def _table_facts(text, base, found):
    for rows in _tables(text):
        if not rows: continue
        headers = [_canonical(cell) for cell in rows[0]]
        for cells in rows[1:]:
            if len(cells) != len(headers): continue
            quote = " | ".join(cells)
            for header, cell in zip(headers, cells):
                if not cell: continue
                if header in {"拟分包工程内容", "分包事项", "拟分包内容"}:
                    found["subcontract_item_name"].append({**base, "value": cell, "quote": quote})
                if header in {"拟分包范围", "分包范围"}:
                    found["subcontract_scope"].append({**base, "value": cell, "quote": quote})
                amount_header = re.fullmatch(r"(?:拟分包金额|分包金额)(?:\((万元|元)\))?", header)
                total_header = re.fullmatch(r"(?:合价|合计金额)\((万元|元)\)", header)
                if total_header and not any("合计" in value for value in cells): continue
                match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*(万元|元)?", cell.replace(",", ""))
                unit = (match.group(2) or (amount_header or total_header).group(1)) if match and (amount_header or total_header) else None
                if unit:
                    found["subcontract_amount"].append({**base, "value": match.group(1), "unit": unit, "quote": quote})


def current_contract_materials(materials, project_fact):
    """Scope report party evidence to contracts carrying the current project name.

    Packet PDFs may include historical performance contracts. These page views are
    transient objects; ORM materials and original page text are never modified.
    """
    project = _canonical((project_fact or {}).get("value"))
    if not project or (project_fact or {}).get("status") != "matched":
        return materials
    output = []
    for material in materials:
        pages = getattr(material, "pages_json", None) or []
        starts = [index for index, page in enumerate(pages)
                  if re.search(r"(?:劳务|专业|工程|施工)分包合同", _plain_text(page.get("text"))[:250])
                  and "工程名称" in _plain_text(page.get("text"))]
        if not starts:
            output.append(material)
            continue
        selected = []
        for index, start in enumerate(starts):
            end = starts[index + 1] if index + 1 < len(starts) else len(pages)
            text = _canonical(_plain_text(pages[start].get("text")))
            if project in text:
                selected.extend(pages[start:end])
        output.append(SimpleNamespace(id=material.id, file_name=material.file_name, role=material.role,
                                     generation_id=getattr(material, "generation_id", None), pages_json=selected,
                                     material_category=getattr(material, "material_category", None)))
    return output


def _amount_from_layout(page):
    layout = (page.get("ocr_meta") or {}).get("layout") or []
    blocks = []
    for row in layout:
        if not isinstance(row, dict):
            continue
        box = row.get("bbox")
        if not isinstance(box, (list, tuple)) or len(box) != 4 or not all(type(v) in (int, float) for v in box):
            continue
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        blocks.append((str(row.get("text") or "").strip(), box))
    headers = [(text, box) for text, box in blocks if text in {"分包金额", "拟分包金额"}]
    if len(headers) != 1:
        return None
    _, head = headers[0]
    candidates = []
    for text, box in blocks:
        match = _AMOUNT_VALUE.fullmatch(text)
        center = (box[0] + box[2]) / 2
        if match and head[0] <= center <= head[2] and 0 < box[1] - head[3] <= 150:
            candidates.append((match, text))
    return candidates[0] if len(candidates) == 1 else None


def _resolve(rows):
    unique = {_canonical(row["value"]) for row in rows}
    if len(unique) == 1:
        return {"status": "matched", "value": rows[0]["value"], "evidence": rows}
    return {"status": "conflict" if unique else "unresolved", "value": None, "evidence": rows}


def _resolve_submitted_dates(rows):
    parsed = []
    for row in rows:
        value = str(row.get("value") or "").strip()
        try:
            parsed_date = date.fromisoformat(value[:10])
            parsed.append((parsed_date.year, parsed_date.month, parsed_date.day, row))
            continue
        except ValueError:
            pass
        match = re.fullmatch(r"(\d{1,2})月(\d{1,2})日", value)
        if not match:
            return _resolve(rows)
        month, day = map(int, match.groups())
        try:
            date(2000, month, day)
        except ValueError:
            return _resolve(rows)
        parsed.append((None, month, day, row))

    month_days = {(month, day) for _, month, day, _ in parsed}
    years = {year for year, _, _, _ in parsed if year is not None}
    if len(month_days) != 1 or len(years) > 1:
        return {"status": "conflict" if rows else "unresolved", "value": None, "evidence": rows}
    year, month, day, _ = next((item for item in parsed if item[0] is not None), parsed[0])
    value = date(year, month, day).isoformat() if year is not None else "%s月%s日" % (month, day)
    return {"status": "matched", "value": value, "evidence": rows}


def project_task_fact(task_value, material_fact, *, project_name=False):
    """Keep material candidates distinct from an independently supplied task value."""
    material_fact = material_fact or {}
    candidates = material_fact.get("evidence") or []
    if not task_value:
        return {**material_fact, "source": "packet" if material_fact.get("value") else None}
    normalized = _canonical(task_value)
    def equivalent(value):
        value = _canonical(value)
        if value == normalized:
            return True
        if not project_name or _ROAD_RANGE.sub("", value) != _ROAD_RANGE.sub("", normalized):
            return False
        ranges = {_canonical(match.group(1)).upper() for text in (value, normalized) for match in _ROAD_RANGE.finditer(text)}
        return len(ranges) <= 1
    matching = [row for row in candidates if equivalent(row.get("value"))]
    different = any(not equivalent(row.get("value")) for row in candidates)
    return {"value": task_value, "source": "task", "status": "conflict" if different else "matched", "evidence": matching,
            "material_candidates": candidates}


def _amount_in_yuan(row):
    if row.get("unit") not in {"元", "万元"}:
        return None
    try:
        value = Decimal(str(row.get("value")))
    except InvalidOperation:
        return None
    if not value.is_finite() or value < 0:
        return None
    if row["unit"] == "万元":
        sign, digits, exponent = value.as_tuple()
        value = Decimal((sign, digits, exponent + 4))
    return value


def merge_amount_facts(packet_amount, contract_amount):
    """Compare explicit amounts without replacing either source's precision."""
    if packet_amount.get("value") is not None and contract_amount.get("value") is not None:
        packet_value, contract_value = _amount_in_yuan(packet_amount), _amount_in_yuan(contract_amount)
        if packet_value is not None and packet_value == contract_value:
            return packet_amount
        return {"status": "conflict", "value": None, "evidence": packet_amount.get("evidence") or [],
                "contract_candidate": contract_amount}
    return packet_amount if packet_amount.get("status") in {"matched", "conflict"} else contract_amount


def extract_report_basic_facts(materials):
    """只从可读的明确标签和清单标题生成可回指候选。"""
    found = {"general_contractor": [], "employer": [], "subcontractor": [], "subcontract_scope": [],
             "project_name": [], "contract_section": [], "subcontract_amount": [],
             "subcontract_item_name": [], "submitted_date": []}
    recipient_candidates = []
    scope_titles = []
    for material in materials or []:
        if getattr(material, "parse_state", None) != "done":
            continue
        for page in getattr(material, "pages_json", None) or []:
            if not isinstance(page, dict):
                continue
            number = page.get("page")
            if type(number) is not int or number < 1 or not material_page_readable(material, number):
                continue
            text = _plain_text(page.get("text"))
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            base = {"material_id": getattr(material, "id", None),
                    "file_name": getattr(material, "file_name", None), "page": number}
            if getattr(material, "role", None) in {"packet", "sub", "labor"}:
                _table_facts(text, base, found)
            if any(_REPORT_FORM.search(line) for line in lines):
                for line in lines:
                    match = _REPORT_RECIPIENT.match(line)
                    company = _COMPANY_END.match(match.group(1).strip()) if match else None
                    if company:
                        recipient_candidates.append({**base, "value": company.group(1), "quote": line})
            if getattr(material, "role", None) == "packet":
                layout_amount = _amount_from_layout(page)
                if layout_amount:
                    match, quote = layout_amount
                    found["subcontract_amount"].append({**base, "value": match.group(1),
                                                         "unit": match.group(2), "quote": quote})
            for index, line in enumerate(lines):
                submitted = re.search(r"报送日期\s*[:：]\s*(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})日?", line)
                if submitted:
                    try:
                        value = date(*map(int, submitted.groups())).isoformat()
                    except ValueError:
                        value = None
                    if value: found["submitted_date"].append({**base, "value": value, "quote": line})
                elif getattr(material, "role", None) == "packet":
                    contextual = re.search(
                        r"(?:你部|贵部)?[【\[]\s*(?:(\d{4})[年/-])?(\d{1,2})月(\d{1,2})日\s*[】\]]\s*报送的",
                        line,
                    )
                    if contextual:
                        year, month, day = contextual.groups()
                        try:
                            parsed = date(int(year) if year else 2000, int(month), int(day))
                        except ValueError:
                            parsed = None
                        if parsed:
                            value = parsed.isoformat() if year else "%s月%s日" % (parsed.month, parsed.day)
                            found["submitted_date"].append({**base, "value": value, "quote": line})
                amount = _AMOUNT.search(line) if getattr(material, "role", None) in {"packet", "sub", "labor"} else None
                if amount:
                    found["subcontract_amount"].append({**base, "value": amount.group(1),
                                                         "unit": amount.group(2), "quote": amount.group(0)})
                project_line = re.sub(r"^(?:工程名称|项目名称)[ \t]*[:：][ \t]*", "", line)
                if _PROJECT.fullmatch(project_line):
                    found["project_name"].append({**base, "value": project_line, "quote": line})
                    section_match = _SECTION.search(project_line)
                    if section_match:
                        section = re.sub(r"\s+", "", section_match.group(1))
                        found["contract_section"].append({**base, "value": section,
                                                           "quote": line})
                for key, pattern in _PARTY_LABELS.items():
                    match = pattern.search(line)
                    company = _COMPANY_END.match(match.group(1).strip()) if match else None
                    if company:
                        found[key].append({**base, "value": company.group(1), "quote": match.group(0)})
                match = _TITLE.fullmatch(line)
                if match:
                    prefix = lines[index - 1] if index and _PROJECT.fullmatch(lines[index - 1]) else ""
                    value = re.sub(r"\s+", "", prefix + match.group(1))
                    scope_titles.append({**base, "value": value,
                                         "quote": "\n".join(filter(None, [prefix, line]))})
    if not found["subcontract_scope"]:
        found["subcontract_scope"] = scope_titles
    result = {key: (_resolve_submitted_dates(rows) if key == "submitted_date" else _resolve(rows))
              for key, rows in found.items()}
    result["employer_recipient_candidates"] = recipient_candidates
    projects = found["project_name"]
    if projects:
        normalized = {_ROAD_RANGE.sub("", _canonical(row["value"])) for row in projects}
        ranges = {_canonical(match.group(1)).upper()
                  for row in projects for match in _ROAD_RANGE.finditer(_canonical(row["value"]))}
        if len(normalized) == 1 and len(ranges) <= 1:
            result["project_name"] = {"status": "matched", "value": max(projects, key=lambda row: len(row["value"]))["value"],
                                      "evidence": projects}
    amounts = found["subcontract_amount"]
    if amounts:
        values_in_yuan = {_amount_in_yuan(row) for row in amounts}
        if len(values_in_yuan) > 1:
            result["subcontract_amount"].update(status="conflict", value=None)
        else:
            result["subcontract_amount"].update(status="matched", value=amounts[0]["value"],
                                                 unit=amounts[0]["unit"])
    return result


def merge_party_facts(contract_evidence, packet_facts):
    """合并显式报审角色与合同角色证据；显式冲突不以首个来源覆盖。"""
    output = dict(contract_evidence or {})
    packet_facts = packet_facts or {}
    for role, key in (("general_contractor", "general_contractor"),
                      ("employer", "employer"),
                      ("subcontractor", "subcontractor")):
        contract = (contract_evidence or {}).get(role) or {}
        packet = packet_facts.get(key) or {}
        contract_status = contract.get("status")
        packet_status = packet.get("status")
        contract_name = contract.get("company") if contract_status == "matched" else None
        packet_name = packet.get("value") if packet_status == "matched" else None
        contract_candidate = contract.get("candidate_company")
        evidence = list(contract.get("evidence") or []) + list(packet.get("evidence") or [])
        if contract_status == "conflict" or packet_status == "conflict" or (
                contract_name and packet_name
                and re.sub(r"\s+", "", contract_name) != re.sub(r"\s+", "", packet_name)) or (
                contract_candidate and packet_name
                and re.sub(r"\s+", "", contract_candidate) != re.sub(r"\s+", "", packet_name)):
            output[role] = {"status": "conflict", "company": None, "evidence": evidence}
        elif packet_name and (not contract_name or contract_name == packet_name):
            output[role] = {"status": "matched", "company": packet_name, "evidence": evidence}
        elif contract_name:
            output[role] = {"status": "matched", "company": contract_name, "evidence": evidence}
        elif contract_candidate:
            output[role] = {"status": "unresolved", "company": None,
                            "candidate_company": contract_candidate, "evidence": evidence}
        elif packet_name:
            output[role] = {"status": "matched", "company": packet_name, "evidence": evidence}
        else:
            output[role] = {"status": "unresolved", "company": None, "evidence": evidence}
    recipients = packet_facts.get("employer_recipient_candidates") or []
    employer = output.get("employer") or {}
    if recipients:
        employer["recipient_candidates"] = recipients
        values = {re.sub(r"\s+", "", str(row.get("value") or "")) for row in recipients if row.get("value")}
        if employer.get("status") == "unresolved" and len(values) == 1 and not employer.get("candidate_company"):
            employer["candidate_company"] = recipients[0]["value"]
        output["employer"] = employer
    return output


def apply_employer_confirmation(party, confirmation):
    """Apply a prior human confirmation only while its exact source remains current."""
    party = dict(party or {})
    if party.get("status") != "unresolved" or not isinstance(confirmation, dict):
        return party
    keys = ("material_id", "page", "value", "quote")
    candidate = next((row for row in party.get("recipient_candidates") or []
                      if all(row.get(key) == confirmation.get(key) for key in keys)), None)
    if not candidate:
        return party
    # Other independently identified candidates must not be silently overridden.
    other = party.get("candidate_company")
    if other and re.sub(r"\s+", "", other) != re.sub(r"\s+", "", candidate["value"]):
        return party
    return {**party, "status": "matched", "company": candidate["value"],
            "source": "human_confirmation", "evidence": [candidate]}


def latest_employer_confirmation(db, task_id):
    from .models import BsTaskEvent
    event = (db.query(BsTaskEvent).filter(BsTaskEvent.task_id == task_id,
             BsTaskEvent.event_type == "report.employer_confirmed")
             .order_by(BsTaskEvent.sequence.desc()).first())
    return (event.payload_json or {}).get("candidate") if event else None
