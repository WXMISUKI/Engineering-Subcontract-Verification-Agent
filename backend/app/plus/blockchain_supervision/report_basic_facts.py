"""报审表和工程量清单中的报告字段线索，不改变核查规则。"""

import re
import unicodedata
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from types import SimpleNamespace

from .a2_check import norm_company
from .extract import party_scoped_pages
from .parsing import material_page_readable


# 主体标签必须按材料角色解释：总包合同里的「发包人」是建设单位，分包合同里的
# 「发包人」才是施工总承包单位；泛化的「乙方」不参与分包人判定。
_ROLE_PARTY_LABELS = {
    "general": {
        "employer": {"发包人", "发包单位", "建设单位", "业主", "甲方"},
        "general_contractor": {"承包人", "承包单位", "施工总承包单位", "总承包单位", "乙方"},
    },
    "sub": {
        "general_contractor": {"发包人", "发包单位", "甲方"},
        "subcontractor": {"分包人", "分包单位", "分包单位名称"},
    },
    "labor": {
        "general_contractor": {"发包人", "发包单位", "甲方"},
        "subcontractor": {"分包人", "分包单位", "分包单位名称"},
    },
    "packet": {
        "general_contractor": {"施工总承包单位", "总承包单位"},
        "employer": {"建设单位", "业主"},
        "subcontractor": {"分包单位名称", "分包单位"},
    },
}
_PARTY_LINE = re.compile(
    r"(?P<label>施工总承包单位|总承包单位|发包单位|发包人|建设单位|业主|"
    r"分包单位名称|分包单位|分包人|承包单位|承包人|甲方|乙方)"
    r"(?:名称)?(?:[（(]全称[）)])?\s*[:：]\s*(?P<name>[^\n|；;：:]{2,100})"
)
_COMPANY_END = re.compile(r"^(.+?(?:有限责任公司|股份有限公司|有限公司|集团公司))")
_REPORT_FORM = re.compile(r"(?:劳务|专业|工程)?分包报审表|分包单位认可表")
_REPORT_RECIPIENT = re.compile(r"^致\s*[:：]\s*(.+)$")
_TITLE = re.compile(r"^(.{2,120}?(?:专业|劳务)分包)工程量清单\s*$")
_PROJECT = re.compile(r"^.{2,120}(?:工程|公路|项目).{0,30}(?:标|标段)\s*$")
_SECTION = re.compile(r"(主线施工\s*[0-9一二三四五六七八九十]+\s*标)\s*$")
_AMOUNT = re.compile(r"(?:拟分包金额|分包金额)[ \t]*[:：][ \t]*([0-9]+(?:\.[0-9]+)?)[ \t]*(万元|元)")
_AMOUNT_VALUE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)[ \t]*(万元|元)$")
_ROAD_RANGE = re.compile(r"[（(]([GS]\d+公路\s*[-－—至]\s*[GS]\d+公路)[）)]", re.I)
_ITEM_NAME = re.compile(r"(?:分包工程名称|专业分包工程名称|劳务分包工程名称)\s*[:：]\s*([^\n|；;]{2,200})")
_SCOPE_LABEL = re.compile(r"(?:分包工程承包范围和内容|分包工程范围和内容|分包工程内容)\s*[:：]\s*([^\n|；;]{2,300})")
_TOTAL_PROJECT = re.compile(r"(?:总包工程名称|总承包工程名称)\s*[:：]\s*([^\n|；;]{2,200})")
_CONTRACT_AMOUNT_INCL = re.compile(
    r"(?<!不含增值税的)(?<!不含增值税)(?<!不含税的)(?<!不含税)"
    r"签约合同价(?:款)?"
    r"(?![（(]?\s*不含(?:增值)?税)"
    r"(?:[（(]含税[）)])?\s*(?:为)?\s*[:：]?[^\d¥￥；;。\n]{0,120}[¥￥]?\s*"
    r"([0-9][0-9,，]*(?:\.[0-9]+)?)\s*(万元|元)")
_CONTRACT_AMOUNT_EXCL = re.compile(
    r"(?:不含增值税的?签约合同价(?:款)?|不含税(?:的)?签约合同价(?:款)?)"
    r"\s*(?:为)?\s*[:：]?[^\d¥￥；;。\n]{0,120}[¥￥]?\s*"
    r"([0-9][0-9,，]*(?:\.[0-9]+)?)\s*(万元|元)")
_TAX_RATE = re.compile(
    r"(?:增值税)?税率(?:/征收率)?\s*[:：=＝]?\s*([0-9]+(?:\.[0-9]+)?)\s*[%％]")


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
                if header in {"拟分包工程内容", "分包事项", "拟分包内容", "分包工程名称"}:
                    found["subcontract_item_name"].append({**base, "value": cell, "quote": quote})
                if header in {"拟分包范围", "分包范围", "分包工程承包范围和内容",
                              "分包工程范围和内容", "分包工程内容"}:
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


def _amount_text(value):
    """金额文本保留合同精度，不输出浮点尾差。"""
    try:
        # 万元口径最多保留 6 位小数（元→万元换算），先定点再归一化，
        # 避免 float 尾差（455.75987100000003）进入报告。
        number = Decimal(str(value)).quantize(Decimal("0.000001")).normalize()
    except InvalidOperation:
        return str(value)
    text = format(number, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def merge_amount_facts(packet_amount, contract_amount):
    """Compare explicit amounts without replacing either source's precision."""
    if packet_amount.get("value") is not None and contract_amount.get("value") is not None:
        packet_value, contract_value = _amount_in_yuan(packet_amount), _amount_in_yuan(contract_amount)
        same = (packet_value is not None and contract_value is not None
                and packet_value.quantize(Decimal("0.01")) == contract_value.quantize(Decimal("0.01")))
        if same:
            # 报审表可能以「元」记账，合同以「万元」记账；相等时优先返回万元口径，
            # 避免报告出现 4557598.71 元而丢失合同精度与含税说明。
            if packet_amount.get("unit") == "万元":
                return packet_amount
            if contract_amount.get("unit") == "万元":
                return {**contract_amount, "status": "matched",
                        "value": _amount_text(contract_amount["value"]),
                        "source": "contract+packet"}
            return packet_amount
        return {"status": "conflict", "value": None, "evidence": packet_amount.get("evidence") or [],
                "contract_candidate": contract_amount}
    return packet_amount if packet_amount.get("status") in {"matched", "conflict"} else contract_amount


def _clean_label_value(value):
    """显式标签值只去掉边界标点，不改写正文。"""
    return str(value or "").strip(" ：:;；，,。")


def _company_core(name: str) -> str:
    return re.sub(r"(?:有限责任公司|股份有限公司|集团有限公司|有限公司|集团公司)$",
                  "", norm_company(name))


def _majority_employer_resolution(evidence: list[dict]):
    """建设单位名称多数一致且少数仅为 OCR 缺字时，按多数完整名确认。

    只在「同一核心名称、短名是长名前缀、差异不超过 4 字、长名至少出现两处」
    时生效；真正的不同公司仍保留冲突。
    """
    values = []
    for item in evidence:
        value = item.get("company_display") or item.get("company") or item.get("value")
        if value:
            values.append(str(value))
    counts = Counter(norm_company(value) for value in values)
    if len(counts) < 2:
        return None
    dominant, count = counts.most_common(1)[0]
    if count < 2:
        return None
    core = _company_core(dominant)
    for other in counts:
        if other == dominant:
            continue
        other_core = _company_core(other)
        if not (other_core and core.startswith(other_core)
                and 0 < len(core) - len(other_core) <= 4):
            return None
    display = next((value for value in values if norm_company(value) == dominant), dominant)
    return {"status": "matched", "company": dominant, "company_display": display,
            "source": "majority_evidence",
            "resolution_note": "多页一致证据（%d 处）" % count,
            "evidence": evidence}


def _amount_wan_text(row):
    """金额统一换算为万元文本；无效行返回 None，不猜补单位。"""
    value = _amount_in_yuan(row)
    if value is None:
        return None
    text = format(value / Decimal(10000), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def extract_report_basic_facts(materials):
    """只从可读的明确标签和清单标题生成可回指候选。"""
    found = {"general_contractor": [], "employer": [], "subcontractor": [], "subcontract_scope": [],
             "project_name": [], "contract_section": [], "subcontract_amount": [],
             "subcontract_item_name": [], "submitted_date": [], "total_project_name": [],
             "subcontract_amount_excl_tax": [], "subcontract_tax_rate": []}
    recipient_candidates = []
    scope_titles = []
    for material in materials or []:
        if getattr(material, "parse_state", None) != "done":
            continue
        material_role = getattr(material, "role", None)
        # 附属/补充协议常反转甲乙方；主体标签只取主合同页，金额/范围仍按全文读取。
        party_pages = ({number for number, _text in party_scoped_pages(material)}
                       if material_role in {"general", "sub", "labor"} else None)
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
                if material_role in _ROLE_PARTY_LABELS and (party_pages is None or number in party_pages):
                    labels = _ROLE_PARTY_LABELS.get(material_role) or {}
                    for match in _PARTY_LINE.finditer(line):
                        key = next((role for role, words in labels.items()
                                    if match.group("label") in words), None)
                        company = _COMPANY_END.match(match.group("name").strip()) if key else None
                        if company:
                            found[key].append({**base, "value": company.group(1), "quote": match.group(0)})
                if material_role in {"sub", "labor"}:
                    item = _ITEM_NAME.search(line)
                    if item:
                        found["subcontract_item_name"].append(
                            {**base, "value": _clean_label_value(item.group(1)),
                             "quote": item.group(0)})
                    total = _TOTAL_PROJECT.search(line)
                    if total:
                        found["total_project_name"].append(
                            {**base, "value": _clean_label_value(total.group(1)),
                             "quote": total.group(0)})
                    scope = _SCOPE_LABEL.search(line)
                    if scope:
                        found["subcontract_scope"].append(
                            {**base, "value": _clean_label_value(scope.group(1)),
                             "quote": scope.group(0)})
                    amount_incl = _CONTRACT_AMOUNT_INCL.search(line)
                    if amount_incl:
                        found["subcontract_amount"].append(
                            {**base, "value": amount_incl.group(1).replace(",", "").replace("，", ""),
                             "unit": amount_incl.group(2), "quote": amount_incl.group(0),
                             "tax_basis": "含税"})
                    amount_excl = _CONTRACT_AMOUNT_EXCL.search(line)
                    if amount_excl:
                        found["subcontract_amount_excl_tax"].append(
                            {**base, "value": amount_excl.group(1).replace(",", "").replace("，", ""),
                             "unit": amount_excl.group(2), "quote": amount_excl.group(0),
                             "tax_basis": "不含税"})
                    tax_rate = _TAX_RATE.search(line)
                    if tax_rate:
                        found["subcontract_tax_rate"].append(
                            {**base, "value": tax_rate.group(1), "unit": "%",
                             "quote": tax_rate.group(0)})
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
    for key in ("subcontract_amount", "subcontract_amount_excl_tax"):
        rows = found[key]
        if not rows:
            continue
        values = {value for value in (_amount_in_yuan(row) for row in rows) if value is not None}
        if len(values) > 1:
            result[key].update(status="conflict", value=None, unit=None)
        elif len(values) == 1:
            result[key].update(status="matched", value=_amount_wan_text(rows[0]), unit="万元",
                               tax_basis=rows[0].get("tax_basis"))
    rates = found["subcontract_tax_rate"]
    if rates:
        values = {_canonical(row["value"]) for row in rates}
        if len(values) > 1:
            result["subcontract_tax_rate"].update(status="conflict", value=None, unit=None)
        else:
            result["subcontract_tax_rate"].update(
                status="matched", value=rates[0]["value"], unit="%")
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
        contract_display = (contract.get("company_display") or contract_name) if contract_status == "matched" else None
        packet_name = packet.get("value") if packet_status == "matched" else None
        contract_candidate = contract.get("candidate_company")
        evidence = list(contract.get("evidence") or []) + list(packet.get("evidence") or [])
        if contract_status == "conflict" or packet_status == "conflict" or (
                contract_name and packet_name
                and norm_company(contract_name) != norm_company(packet_name)) or (
                contract_candidate and packet_name
                and norm_company(contract_candidate) != norm_company(packet_name)):
            resolved = _majority_employer_resolution(evidence) if role == "employer" else None
            output[role] = resolved or {"status": "conflict", "company": None, "evidence": evidence}
        elif packet_name and (not contract_name or norm_company(contract_name) == norm_company(packet_name)):
            output[role] = {"status": "matched", "company": packet_name,
                            "company_display": packet_name, "evidence": evidence}
        elif contract_name:
            output[role] = {"status": "matched", "company": contract_name,
                            "company_display": contract_display, "evidence": evidence}
        elif contract_candidate:
            output[role] = {"status": "unresolved", "company": None,
                            "candidate_company": contract_candidate,
                            "candidate_company_display": (contract.get("candidate_company_display")
                                                          or contract_candidate),
                            "evidence": evidence}
        elif packet_name:
            output[role] = {"status": "matched", "company": packet_name,
                            "company_display": packet_name, "evidence": evidence}
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
