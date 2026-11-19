# -*- coding: utf-8 -*-
"""合同条款事实抽取。

只读取已落库的逐页文本，定位合同正文和主要条款所在页；
不判断条款是否公平、是否低于总包合同，结论由报告写“需人工核对”。
"""
from __future__ import annotations

import re

_CONTRACT_PAGE_RE = re.compile(r"合同编号|甲方\s*[:：]|乙方\s*[:：]|劳务分包合同|分包合同")
_CONTRACT_BODY_RE = re.compile(r"甲方|乙方|发包人|承包人|合同编号")

_CLAUSE_PATTERNS = {
    "pricing": ("合同价款", "暂定价款", "计价", "单价", "总价", "合同价格"),
    "measurement": ("计量", "验工计价", "工程量确认"),
    "payment": ("支付", "付款", "进度款"),
    "settlement": ("结算", "审计", "财政评审"),
    "breach": ("违约", "违约金"),
    "termination": ("解除", "终止"),
    "quality": ("质量",),
    "safety": ("安全",),
    "wages": ("农民工工资", "工资支付", "工资"),
    "plan": ("专项施工方案", "施工方案", "方案审批"),
    "change": ("合同变更", "补充协议", "变更"),
    "warranty": ("缺陷责任期", "保修"),
    "environment": ("环保", "水保", "扬尘"),
}


def _is_contract_page(text: str) -> bool:
    return bool(_CONTRACT_PAGE_RE.search(text) and _CONTRACT_BODY_RE.search(text))


def _is_next_document(text: str) -> bool:
    head = text[:80]
    return bool(re.search(r"安全生产文明施工协议书|工程量清单|承诺书|授权委托书|报审表", head))


def iter_contract_sections(materials, *, allowed_roles=('sub', 'labor')):
    """逐页给出有明确起点的分包合同段；不改变旧报告首次定位口径。"""
    from .document_boundaries import is_document_boundary
    from .parsing import material_page_readable

    template = re.compile(r"模板|示例|样例|样表|填写说明")
    contract_title = re.compile(r"^[^:：|，；。]{0,60}(?:分包合同(?:协议)?书?|合同协议书)$")
    for material in materials or []:
        if (getattr(material, "parse_state", None) != "done"
                or getattr(material, "role", None) not in allowed_roles
                or template.search(getattr(material, "file_name", "") or "")):
            continue
        in_contract, previous_page = False, None
        for page in getattr(material, "pages_json", None) or []:
            if not isinstance(page, dict):
                in_contract, previous_page = False, None
                continue
            page_no = page.get("page")
            if type(page_no) is not int or previous_page is None or page_no != previous_page + 1:
                in_contract = False
            previous_page = page_no if type(page_no) is int else None
            text = page.get("text")
            if not isinstance(text, str) or not material_page_readable(material, page.get("page")):
                in_contract = False
                continue
            lines, start = text.splitlines(), 0
            if any(re.fullmatch(r"(?:模板|示例|样例|样表|填写说明)(?:[:：].*)?", line.strip(" #"))
                   for line in lines):
                in_contract = False
                continue
            for index, line in enumerate(lines):
                title = re.sub(r"\s+", "", line).strip(" #")
                # 合同义务句可以以“名单/证明”结尾，不能据此切成独立附件。
                if re.search(r"[，；。]", title) or re.search(
                        r"(?:应当?|须|需要?|必须|不得)[^:：|]{0,8}"
                        r"(?:提供|提交|开设|支付|承担|配合|建立|报送|出具|开具)|负责|承担", title):
                    continue
                is_contract_title = bool(contract_title.fullmatch(re.sub(
                    r"(?:[（(](?:正本|副本|复印件|电子版)[）)]|正本|副本)$", "", title)))
                boundary = (is_document_boundary(line) or _is_next_document(title)
                            or is_contract_title
                            or bool(re.fullmatch(r"[^:：|]{0,50}(?:凭证|回单|流水|支付表|发放表|实名制登记表)", title))
                            or bool(re.fullmatch(r"(?:模板|示例|样例|样表|填写说明)[:：]?", title)))
                if not boundary:
                    continue
                previous = "\n".join(lines[start:index])
                if previous.strip() and (in_contract or (start == 0 and _is_contract_page(previous))):
                    yield material, page, previous
                in_contract = is_contract_title
                start = index if in_contract else index + 1
            segment = "\n".join(lines[start:])
            # 文档切段后仅明确合同起点可以重启，证明中的甲乙方不是合同。
            if start == 0 and _is_contract_page(segment):
                in_contract = True
            if in_contract and segment.strip():
                yield material, page, segment


def extract_contract_clauses(materials) -> dict:
    """返回合同页范围和主要条款首次出现的页码/摘录。"""
    pages = set()
    clauses = {}
    for material in materials or []:
        if getattr(material, "parse_state", None) != "done":
            continue
        in_contract = False
        for page in getattr(material, "pages_json", None) or []:
            if not isinstance(page, dict):
                continue
            text = page.get("text") or ""
            page_no = page.get("page")
            if not isinstance(text, str) or not text.strip():
                continue
            if _is_contract_page(text):
                in_contract = True
            elif in_contract and _is_next_document(text):
                in_contract = False
            if not in_contract:
                continue
            if isinstance(page_no, int) and not isinstance(page_no, bool) and page_no > 0:
                pages.add(page_no)
            for key, words in _CLAUSE_PATTERNS.items():
                if key in clauses:
                    continue
                for word in words:
                    index = text.find(word)
                    if index < 0:
                        continue
                    start = max(0, index - 30)
                    clauses[key] = {
                        "page": page_no,
                        "keyword": word,
                        "quote": re.sub(r"\s+", " ", text[start:index + len(word) + 50])[:120],
                    }
                    break
    return {"pages": sorted(pages), "clauses": clauses}
