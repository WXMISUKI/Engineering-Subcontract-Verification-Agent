"""合同字段双路候选抽取：规则保底 + schema 约束 LLM 第二路。

设计边界（已与业主确认的路线）：
- **规则路**继续由 `report_basic_facts.extract_report_basic_facts` 提供，保证离线可复现；
- **LLM 路**只输出候选，不是结论：字段值必须带 `material_id/page/quote`，且 quote 必须
  能在原页文本中逐字命中，否则丢弃并记 ``evidence_unverifiable``；
- **裁决路**：两路一致才自动采纳；仅 LLM 命中 → ``provisional``（需人工确认）；
  仅规则命中 → ``matched`` 但标记 ``single_source``；两路冲突 → ``conflict`` 且不选边。
- **可复现**：prompt 版本、schema 版本、模型编码由调用方冻结并随结果返回；同一材料
  重跑必须使用同一组合。

本模块不写数据库、不改任务状态，也不在报告生成时隐式调用模型；接入流水线时必须
显式传入 client，并把版本指纹落到事实行/事件。
"""
from __future__ import annotations

import os
import re
from decimal import Decimal, InvalidOperation

from .a2_check import norm_company
from .extract import is_placeholder_party_name
from .semantic_review import request_json, validate_evidence

SCHEMA_VERSION = "contract-extraction.schema.v1"
PROMPT_VERSION = "contract-extraction.prompt.v1"
RESOLVER_VERSION = "contract-dual.v1"

FIELDS = (
    "contract_type", "employer", "general_contractor", "subcontractor",
    "project_name", "item_name", "scope", "amount_incl_wan",
    "amount_excl_wan", "tax_rate_pct", "sign_date",
)
PARTY_FIELDS = frozenset({"employer", "general_contractor", "subcontractor"})

# 合同关键字段通常集中在首页与专门条款页；先按关键词选页，控制单次输入预算，
# 同时保留前 3 页兜底（封面/当事人/金额页常在前面）。选择结果可复现，不做随机抽样。
_FIELD_KEYWORDS = {
    "employer": ("发包人", "建设单位", "甲方"),
    "general_contractor": ("承包人", "总承包单位", "乙方"),
    "subcontractor": ("分包人", "分包单位"),
    "project_name": ("总包工程名称", "工程名称", "项目名称"),
    "item_name": ("分包工程名称", "分包内容"),
    "scope": ("承包范围", "分包范围", "工程内容", "工作内容"),
    "amount_incl_wan": ("签约合同价", "合同金额", "合同总价"),
    "amount_excl_wan": ("不含税", "不含增值税"),
    "tax_rate_pct": ("税率",),
    "sign_date": ("签订日期", "签约日期", "签订时间"),
    "contract_type": ("分包合同", "施工合同", "劳务合同"),
}

_SYSTEM = (
    "你是工程分包合同字段抽取器。只依据给定页面原文抽取字段，不推断、不补全、"
    "不引用外部知识。每个字段必须给出原文证据 material_id/page/quote，quote 必须"
    "逐字出现在该页原文中；找不到就返回 null。金额单位为万元，税率单位为百分数。"
    "只输出 JSON 对象。"
)

_FIELD_SPEC = {
    "contract_type": "合同类型（总包/专业分包/劳务合作）",
    "employer": "建设单位（总包合同的发包人）",
    "general_contractor": "施工总承包单位（分包合同的发包人/承包人）",
    "subcontractor": "分包单位（分包合同的承包人/分包人）",
    "project_name": "总包工程名称",
    "item_name": "分包工程名称",
    "scope": "分包工程承包范围和内容",
    "amount_incl_wan": "含税签约合同价，单位万元",
    "amount_excl_wan": "不含增值税签约合同价，单位万元",
    "tax_rate_pct": "税率，单位百分数（只填数字）",
    "sign_date": "合同签订日期 YYYY-MM-DD",
}


def version_fingerprint(model_code: str | None = None) -> dict:
    """冻结可复现身份：prompt/schema/裁决器版本 + 实际模型编码。"""
    return {"prompt_version": PROMPT_VERSION, "schema_version": SCHEMA_VERSION,
            "resolver_version": RESOLVER_VERSION, "model_code": model_code}


def _page_text(page: dict) -> str:
    return str(page.get("text") or "")


def select_contract_pages(material, *, max_chars: int = 16000) -> tuple[list[dict], bool]:
    """按字段关键词选页；返回 (pages, truncated)。

    前 3 页必选，其余页按关键词命中数降序；预算用尽即停止，并显式标记截断，
    不以“已经读完”伪装。
    """
    pages = [page for page in getattr(material, "pages_json", None) or []
             if isinstance(page, dict) and type(page.get("page")) is int
             and page["page"] > 0 and _page_text(page).strip()]
    if not pages:
        return [], False
    head = [page for page in pages if page["page"] <= 3]
    rest = [page for page in pages if page["page"] > 3]

    def score(page):
        text = _page_text(page)
        return sum(text.count(keyword) for keywords in _FIELD_KEYWORDS.values()
                   for keyword in keywords)

    ranked = sorted(rest, key=lambda page: (-score(page), page["page"]))
    selected, used, truncated = list(head), sum(len(_page_text(page)) for page in head), False
    for page in ranked:
        size = len(_page_text(page))
        if used + size > max_chars:
            truncated = True
            continue
        selected.append(page)
        used += size
    selected.sort(key=lambda page: page["page"])
    return selected, truncated


def build_payload(material, pages: list[dict]) -> dict:
    """构造单材料抽取请求；页面文本是不可信数据，由 system prompt 明确隔离。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "task": "contract_field_extraction",
        "fields": _FIELD_SPEC,
        "material": {
            "material_id": getattr(material, "id", None),
            "file_name": getattr(material, "file_name", None),
            "role": getattr(material, "role", None),
            "pages": [{"page": page["page"], "text": _page_text(page)} for page in pages],
        },
    }


def _clean_value(field: str, value):
    if value is None:
        return None
    if field in {"amount_incl_wan", "amount_excl_wan", "tax_rate_pct"}:
        try:
            number = Decimal(str(value).replace(",", "").replace("，", ""))
        except InvalidOperation:
            return None
        if not number.is_finite() or number < 0:
            return None
        text = format(number, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    text = re.sub(r"\s+", " ", str(value)).strip(" ：:;；，,。")
    if not text:
        return None
    if field in PARTY_FIELDS:
        text = norm_company(text)
        if not text or is_placeholder_party_name(text):
            return None
    return text or None


def normalize_candidates(result: dict, materials: list) -> dict:
    """校验模型输出：字段白名单、证据逐字回验、占位词过滤。

    返回 ``{"fields": {...}, "dropped": [...]}``；任何无法回指原文的字段都不进入
    候选，绝不把模型猜测当事实。
    """
    fields_out, dropped = {}, []
    fields = (result or {}).get("fields") if isinstance(result, dict) else None
    if not isinstance(fields, dict):
        # 模型常直接返回顶层字段（无 fields 包装），两种形态都接受；
        # 证据既支持嵌套 evidence，也支持 material_id/page/quote 平铺。
        fields = {key: value for key, value in (result or {}).items() if key in FIELDS}
    if not isinstance(fields, dict):
        return {"fields": {}, "dropped": [{"field": None, "reason": "invalid_fields"}]}
    for field, payload in fields.items():
        if field not in FIELDS or not isinstance(payload, dict):
            dropped.append({"field": field, "reason": "field_not_allowed"})
            continue
        value = _clean_value(field, payload.get("value"))
        if value is None:
            if payload.get("value") not in (None, ""):
                dropped.append({"field": field, "reason": "value_rejected"})
            continue
        evidence = payload.get("evidence")
        if not isinstance(evidence, dict):
            evidence = {key: payload.get(key) for key in ("material_id", "page", "quote")}
        if not validate_evidence(evidence, materials):
            dropped.append({"field": field, "reason": "evidence_unverifiable"})
            continue
        confidence = payload.get("confidence")
        if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            confidence = None
        fields_out[field] = {
            "value": value,
            "confidence": confidence,
            "evidence": {"material_id": evidence["material_id"],
                         "page": evidence["page"],
                         "quote": evidence["quote"]},
        }
    return {"fields": fields_out, "dropped": dropped}


async def extract_llm_candidates(material, *, client=None, max_chars: int = 16000) -> dict:
    """调用一次 LLM 抽取（按选页预算）；未配置 client 时返回不可用状态，不猜结果。"""
    if client is None:
        return {"available": False, "reason": "model_not_configured",
                "fields": {}, "dropped": [], "version": version_fingerprint()}
    pages, truncated = select_contract_pages(material, max_chars=max_chars)
    if not pages:
        return {"available": False, "reason": "no_readable_pages",
                "fields": {}, "dropped": [], "version": version_fingerprint()}
    result = await request_json(client, _SYSTEM, build_payload(material, pages),
                                diagnostic_context={"scope": "contract_extraction"})
    normalized = normalize_candidates(result, [material])
    return {"available": True, "truncated": truncated,
            "fields": normalized["fields"], "dropped": normalized["dropped"],
            "version": version_fingerprint(
                getattr(client, "model_code", None) or os.environ.get("LLM_MODEL"))}


# ==================== 双路裁决 ====================

_RULE_FACT_MAP = {
    "subcontractor": "subcontractor",
    "general_contractor": "general_contractor",
    "employer": "employer",
    "subcontract_scope": "scope",
    "subcontract_item_name": "item_name",
    "subcontract_amount": "amount_incl_wan",
    "subcontract_amount_excl_tax": "amount_excl_wan",
    "subcontract_tax_rate": "tax_rate_pct",
}


def rule_candidates(report_facts: dict) -> dict:
    """把报告基础事实投影成与 LLM 路相同的字段名；只取 matched。"""
    total_project = report_facts.get("total_project_name") or {}
    project = total_project if total_project.get("status") == "matched" else (
        report_facts.get("project_name") or {})
    output = {}
    if project.get("status") == "matched" and project.get("value"):
        output["project_name"] = {"value": str(project["value"]), "confidence": None,
                                  "evidence": (project.get("evidence") or [None])[0]}
    for source_key, field in _RULE_FACT_MAP.items():
        fact = report_facts.get(source_key) or {}
        if fact.get("status") != "matched" or fact.get("value") in (None, ""):
            continue
        output[field] = {"value": str(fact["value"]), "confidence": None,
                         "evidence": (fact.get("evidence") or [None])[0]}
    return output


def _canonical(value) -> str:
    return re.sub(r"[\s（）()\[\]【】]", "", str(value or "")).translate(
        str.maketrans({"—": "-", "–": "-", "－": "-", "〜": "~", "～": "~"}))


def _equivalent(field: str, left, right) -> bool:
    if field in {"amount_incl_wan", "amount_excl_wan", "tax_rate_pct"}:
        try:
            return Decimal(str(left)) == Decimal(str(right))
        except InvalidOperation:
            return False
    if field in PARTY_FIELDS:
        return norm_company(str(left)) == norm_company(str(right))
    return _canonical(left) == _canonical(right)


def merge_candidates(rule: dict, llm: dict) -> dict:
    """双路裁决；不一致保留冲突，不静默选边。"""
    output = {}
    for field in FIELDS:
        rule_row = (rule or {}).get(field)
        llm_row = (llm or {}).get(field)
        if rule_row and llm_row:
            if _equivalent(field, rule_row["value"], llm_row["value"]):
                output[field] = {"status": "matched", "value": rule_row["value"],
                                 "source": "rule+llm",
                                 "confidence": llm_row.get("confidence"),
                                 "evidence": [rule_row.get("evidence"), llm_row.get("evidence")]}
            else:
                output[field] = {"status": "conflict", "value": None,
                                 "source": "rule+llm",
                                 "rule_value": rule_row["value"],
                                 "llm_value": llm_row["value"],
                                 "evidence": [rule_row.get("evidence"), llm_row.get("evidence")]}
        elif rule_row:
            output[field] = {"status": "matched", "value": rule_row["value"],
                             "source": "rule", "single_source": True,
                             "confidence": None, "evidence": [rule_row.get("evidence")]}
        elif llm_row:
            output[field] = {"status": "provisional", "value": llm_row["value"],
                             "source": "llm", "needs_review": True,
                             "confidence": llm_row.get("confidence"),
                             "evidence": [llm_row.get("evidence")]}
    return output


__all__ = [
    "FIELDS", "PROMPT_VERSION", "RESOLVER_VERSION", "SCHEMA_VERSION",
    "build_payload", "extract_llm_candidates", "merge_candidates",
    "normalize_candidates", "rule_candidates", "select_contract_pages",
    "version_fingerprint",
]
