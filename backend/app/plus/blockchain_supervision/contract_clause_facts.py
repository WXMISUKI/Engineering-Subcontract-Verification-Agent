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
}


def _is_contract_page(text: str) -> bool:
    return bool(_CONTRACT_PAGE_RE.search(text) and _CONTRACT_BODY_RE.search(text))


def _is_next_document(text: str) -> bool:
    head = text[:80]
    return bool(re.search(r"安全生产文明施工协议书|工程量清单|承诺书|授权委托书|报审表", head))


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
