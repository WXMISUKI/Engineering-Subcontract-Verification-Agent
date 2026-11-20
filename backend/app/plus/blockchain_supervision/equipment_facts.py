# -*- coding: utf-8 -*-
"""设备与辅助设施事实抽取。

只定位报审资料中的设备清单/权属/租赁/检定页，不判断设备数量是否满足施工需要；
该类判断缺少业务阈值，报告只写“已见/未见，需人工核对”。
"""
from __future__ import annotations

import re

_EQUIPMENT_PAGE_RE = re.compile(r"设备|机械")
_EQUIPMENT_HINT_RE = re.compile(r"设备清单|机械设备|设备名称|规格型号|租赁|权属|检定|设备数量")


def extract_equipment_facts(materials) -> dict:
    pages = set()
    rows = []
    for material in materials or []:
        if getattr(material, "parse_state", None) != "done":
            continue
        for page in getattr(material, "pages_json", None) or []:
            if not isinstance(page, dict):
                continue
            text = page.get("text") or ""
            page_no = page.get("page")
            if not isinstance(text, str) or not _EQUIPMENT_PAGE_RE.search(text):
                continue
            if not _EQUIPMENT_HINT_RE.search(text):
                continue
            if not isinstance(page_no, int) or isinstance(page_no, bool) or page_no <= 0:
                continue
            pages.add(page_no)
            if len(rows) < 10:
                line = next((item.strip() for item in text.splitlines()
                             if _EQUIPMENT_HINT_RE.search(item)), "")
                if line:
                    rows.append({"material_id": material.id, "subject_id": getattr(material, "subject_id", None),
                                 "page": page_no, "quote": re.sub(r"\s+", " ", line)[:120]})
    from .equipment_identity import extract_equipment_records
    return {"pages": sorted(pages), "rows": rows, "records": extract_equipment_records(materials or [])}
