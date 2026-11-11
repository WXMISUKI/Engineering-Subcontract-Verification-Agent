"""仅供报告展示的合同类型投影，不改变核查规则或保存的结论。"""
import re

from .models import STATUS_HIT, STATUS_UNDECIDABLE
from .rules import load_extract_matchers


_GENERIC_TYPE_KEYWORDS = {"施工合同"}


def project_contract_type(material) -> dict:
    """优先匹配具体合同名称；泛称和互相矛盾的线索保持待核实。"""
    matchers = load_extract_matchers()
    name = getattr(material, "file_name", "") or ""
    pages = getattr(material, "pages_json", None) or []
    flat = "\n".join(re.sub(r"\s+", "", p.get("text", "") or "")
                     for p in pages if isinstance(p, dict))

    def matches(text):
        hits = []
        for key, keywords in matchers["types"].items():
            for keyword in keywords:
                if keyword in {"分包", "劳务"}:
                    continue
                for match in re.finditer(re.escape(keyword), text):
                    hits.append((match.start(), match.end(), key, keyword))
        return [hit for hit in hits if not any(
            other[2] != hit[2] and other[0] <= hit[0] and other[1] >= hit[1]
            and other[1] - other[0] > hit[1] - hit[0] for other in hits)]

    def select(hits):
        """具体合同类型优先，泛称仅在没有具体线索时参与判定。"""
        specific = [hit for hit in hits if hit[3] not in _GENERIC_TYPE_KEYWORDS]
        relevant = specific or hits
        kinds = {hit[2] for hit in relevant}
        if len(kinds) != 1:
            return None, None
        key = next(iter(kinds))
        return key, max((hit for hit in relevant if hit[2] == key), key=lambda hit: len(hit[3]))

    for source, is_name in ((name, True),):
        hits = matches(source)
        key, best = select(hits)
        if hits and key is None:
            return {"value": None, "unit": None, "status": STATUS_UNDECIDABLE,
                    "missing": ["合同类型存在多种明确线索，请人工核实"], "quote": None, "page": None}
        if best:
            value = "劳务分包" if key == "labor" and best[3] == "劳务分包" else matchers["type_labels"].get(key, key)
            return {"value": value, "unit": None, "status": STATUS_HIT,
                    "missing": [], "quote": name if is_name else best[3], "page": None}

    page_hits = []
    for index, page in enumerate(pages, start=1):
        if not isinstance(page, dict):
            continue
        page_text = re.sub(r"\s+", "", page.get("text", "") or "")
        page_number = page.get("page") or index
        page_hits.extend((*hit, page_number) for hit in matches(page_text))
    key, best = select(page_hits)
    if page_hits and key is None:
        return {"value": None, "unit": None, "status": STATUS_UNDECIDABLE,
                "missing": ["合同类型存在多种明确线索，请人工核实"], "quote": None, "page": None}
    if best:
        value = "劳务分包" if key == "labor" and best[3] == "劳务分包" else matchers["type_labels"].get(key, key)
        return {"value": value, "unit": None, "status": STATUS_HIT,
                "missing": [], "quote": best[3], "page": best[4]}
    return {"value": None, "unit": None, "status": STATUS_UNDECIDABLE,
            "missing": ["合同类型（总包/专业分包/劳务合作）关键词"], "quote": None, "page": None}
