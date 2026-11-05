"""规则数据文件装载（规则=数据，MVP 阶段不做录入界面）。

数据文件逐字取自原文 docx；AD-5 纪律：任何阈值/条目都必须能指回这里的原文 text 字段。
"""
import json
from datetime import date, datetime
from pathlib import Path

_DATA_DIR = Path(__file__).parent / "data"
ATTACHMENT_SEAL_ITEM = "ZHB-2025-SEAL"


def load_negative_list() -> dict:
    """《公路工程施工分包负面清单（2024年版）》21 条叶子。"""
    with open(_DATA_DIR / "negative_list_2024.json", encoding="utf-8") as f:
        return json.load(f)


def negative_list_rules() -> list:
    return load_negative_list()["rules"]


def load_province_negative_scope() -> dict:
    """省级负面清单增补口径（对应负面清单第三条的省级自行确定事项）。

    只登记**有书面依据**的省级口径；未登记地区不得默认「未增补」，一律按
    「未见省级书面清单」判不可判定。每次直读不缓存——补一条口径 → 重新核查即生效。
    """
    with open(_DATA_DIR / "province_negative_scope.json", encoding="utf-8") as f:
        return json.load(f)


def load_report_checklist() -> list:
    """A3-01 报审资料完整性核查条目（出处见文件 meta.source_note）。"""
    with open(_DATA_DIR / "report_checklist.json", encoding="utf-8") as f:
        document = json.load(f)
    items = [dict(item) for item in document["items"]]
    by_id = {item["id"]: item for item in items}
    # 同一逻辑义务可由多份文件支撑；来源叠加不复制条目或放大分母。
    for overlay in document.get("meta", {}).get("source_overlays") or []:
        for item_id in overlay.get("item_ids") or []:
            item = by_id.get(item_id)
            if item and overlay.get("source") not in item.get("source", ""):
                item["source"] = "%s；%s" % (item["source"], overlay["source"])
    return items


def load_negative_matchers() -> dict:
    """负面清单匹配器：召回触发词 + 阈值抽取模式（工程配置，不与原文混放）。

    返回 {"extractors": {阈值key: 正则}, "matchers": {规则id: {...}}}，
    正则含 NUM 占位符（数字捕获组），由 a1_check 引擎编译时替换。
    """
    with open(_DATA_DIR / "negative_list_matchers.json", encoding="utf-8") as f:
        return json.load(f)


def load_extract_matchers() -> dict:
    """A-15 合同字段统一抽取层匹配器（字段正则/词表外置为数据；纯正则/词表，零 LLM）。

    与 load_negative_matchers 同构：只读数据文件、不缓存——改词表 → 重新核查 → 抽取结果变化。
    """
    with open(_DATA_DIR / "extract_matchers.json", encoding="utf-8") as f:
        return json.load(f)


def load_severity_matrix() -> dict:
    """A-13 严重度/置信档位矩阵（BS 专属分包合规语境；词表外置为数据、零 LLM）。

    与 load_extract_matchers 同构：只读数据文件、不缓存——改词表 → 重新核查 → 档位变化。
    """
    with open(_DATA_DIR / "severity_matrix.json", encoding="utf-8") as f:
        return json.load(f)


def load_missing_material_map() -> dict:
    """缺件 key → 材料类归并映射（BS-P1-12；P1-06 待办面板的归并依据）。

    与 load_severity_matrix 同构：直读不缓存。**中文标签与归并关系都在本文件**，
    库里只存 key ⇒ 改文案不会让已落库结论的缺件信息失效（这正是 P1-12 要解决的）。
    """
    with open(_DATA_DIR / "missing_material_map.json", encoding="utf-8") as f:
        return json.load(f)


def load_signature_chains() -> list:
    """A3-02 签字链双轨定义（法定链/集团链，出处见文件 meta.source_note）。"""
    with open(_DATA_DIR / "signature_chains.json", encoding="utf-8") as f:
        return json.load(f)["chains"]


def load_qualification_matchers() -> dict:
    """A2-04 资质匹配词表（分包施工内容类别 × 经营范围标签，出处见 meta.source_note）。"""
    with open(_DATA_DIR / "qualification_matchers.json", encoding="utf-8") as f:
        return json.load(f)


def load_config() -> dict:
    """可配置核查参数（金额占比阈值等）。每次调用直读文件不缓存——
    「改数据文件 → 重新核查 → 结论变化」是 W4 特色规则配置演示的核心形态。"""
    with open(_DATA_DIR / "config.json", encoding="utf-8") as f:
        return json.load(f)


def load_transfer_rules() -> dict:
    """A1-03/A1-04/A1-05 条款目录与必需证据字段。

    该目录只定义规则身份、来源和证据契约，不承担法律评价或三态计算。
    """
    with open(_DATA_DIR / "transfer_rules.json", encoding="utf-8") as f:
        return json.load(f)


def load_review_flow() -> dict:
    """审查流程与对应依据的唯一定义点（步骤 → 内核 → 条款坐标）。

    由 `backend/scripts/render_review_flow.py` 渲染成《审查流程与依据对照表》与流程图；
    `tests/test_bs_review_flow.py` 把它的声明钉在源码坐标上（内核函数、报告分区、条款库），
    因此新增或改名内核后跑测试即知图是否需要同步。
    """
    with open(_DATA_DIR / "review_flow.json", encoding="utf-8") as f:
        return json.load(f)


def in_scope_kinds() -> list:
    """本期必查的结论类型（＝流程图中五步主线登记的内核 kind，去重保序）。

    单一真源：收口闸（`service.complete_review` 的必查分组）与界面/报告分组都以此为准，
    避免出现第二份「必查清单」过期。**预留能力（deferred 里的占比类、合同语义核查）
    不在此列**——把它们算成必查会让任务永远收不了口（2026-10-14 实际踩过）。
    """
    kinds = []
    for step in load_review_flow()["steps"]:
        for kernel in step.get("kernels") or []:
            if kernel["kind"] not in kinds:
                kinds.append(kernel["kind"])
    return kinds


def _as_of_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        raise ValueError("规则基准日格式无效，应为 YYYY-MM-DD")


def _document_is_effective(document: dict, as_of: date) -> bool:
    """缺少有效期的历史材料不猜排除；有边界时按闭区间判断。"""
    start = document.get("effective_from")
    end = document.get("effective_to")
    if start and as_of < date.fromisoformat(str(start)[:10]):
        return False
    if end and as_of > date.fromisoformat(str(end)[:10]):
        return False
    return True


def load_basis_clauses(as_of=None) -> dict:
    """依据条款库（条款原文 + 跨页页范围 + 校对状态）。

    由 `backend/scripts/ingest_basis_clauses.py` 从依据原文生成。`human_verified=false`
    表示该依据来自 OCR 自动识别、**尚未人工校对**，引用时必须如实标注，不得当作已校对。
    """
    with open(_DATA_DIR / "basis_clauses.json", encoding="utf-8") as f:
        library = json.load(f)
    effective_date = _as_of_date(as_of)
    if effective_date is None:
        return library
    active_docs = [doc for doc in library.get("docs") or []
                   if _document_is_effective(doc, effective_date)]
    active_doc_ids = {doc.get("doc_id") for doc in active_docs}

    def keep(node: dict) -> dict | None:
        doc_id = node.get("doc_id")
        if doc_id and doc_id not in active_doc_ids:
            return None
        children = [child for child in (keep(item) for item in node.get("children") or [])
                    if child is not None]
        copied = dict(node)
        if "children" in node:
            copied["children"] = children
        return copied

    return {
        **library,
        "docs": active_docs,
        "clauses": [item for item in (keep(node) for node in library.get("clauses") or [])
                    if item is not None],
        "as_of": effective_date.isoformat(),
    }


def load_basis_index() -> dict:
    """条目 ID → 依据条款坐标。

    由 `backend/scripts/check_basis_alignment.py` 生成，坐标按**逐字**比对条款库后才写入
    （解析不到或指向不存在条款的坐标不入索引）。只存坐标、不复制原文：同一条款原文
    只在 `basis_clauses.json` 定义一次。
    """
    with open(_DATA_DIR / "basis_index.json", encoding="utf-8") as f:
        return json.load(f)


def _basis_view(node: dict, doc: dict, parent: dict) -> dict:
    """条款视图：文档级信息（文号/名称/校对状态）取自归属文档，页范围取自所属条款。"""
    doc = doc or {}
    return {
        "clause_id": node["clause_id"],
        "doc_id": node.get("doc_id") or parent.get("doc_id"),
        "doc_no": doc.get("doc_no"),
        "doc_title": doc.get("title"),
        "authority_level": doc.get("authority_level"),
        "marker": node.get("marker"),
        "text": node.get("text"),
        "page_start": parent.get("page_start"),
        "page_end": parent.get("page_end"),
        "human_verified": bool(doc.get("human_verified")),
        "source_kind": doc.get("source_kind"),
    }


def basis_lookup(as_of=None) -> dict:
    """一次性解析全部条目 → {条目 ID: 依据条款视图}（单次读盘，供意见书批量引用）。

    坐标在条款库中找不到的一律不入表：意见书宁愿不显示依据，也不能引用到错误位置。
    """
    library = load_basis_clauses(as_of=as_of)
    docs = {d["doc_id"]: d for d in library.get("docs") or []}
    by_clause_id = {}
    for clause in library.get("clauses") or []:
        by_clause_id[clause["clause_id"]] = _basis_view(clause, docs.get(clause["doc_id"]), clause)
        for child in clause.get("children") or []:
            by_clause_id[child["clause_id"]] = _basis_view(child, docs.get(clause["doc_id"]), clause)
    out = {}
    for item_id, coord in (load_basis_index().get("items") or {}).items():
        view = by_clause_id.get(coord["clause_id"])
        if view:
            out[item_id] = view
    return out


def configured_basis_lookup(as_of=None) -> dict:
    """返回面向 finding 展示的依据投影。

    固定依据索引仍由 :func:`basis_lookup` 单独维护；这里仅补充业务规则卡片、
    签字链和证照有效期等配置型规则，明确标记 ``source_kind=configured_rule``，
    不把它们伪装成法规或业主原文。
    """
    out = dict(basis_lookup(as_of=as_of))
    from .cards import resolve_rule_cards

    for card in resolve_rule_cards(as_of=as_of):
        item_id = card.get("source_item_id") or card.get("card_id")
        if not item_id or item_id in out:
            continue
        clause = card.get("clause") or {}
        out[item_id] = {
            "clause_id": card.get("clause_id") or f"configured:{item_id}",
            "doc_id": card.get("doc_id"),
            "doc_no": None,
            "doc_title": card.get("source"),
            "authority_level": card.get("authority_level"),
            "marker": None,
            "text": card.get("source_text") or card.get("label") or card.get("source"),
            "page_start": clause.get("page_start"),
            "page_end": clause.get("page_end"),
            "human_verified": card.get("resolution_status") == "resolved",
            "source_kind": "configured_rule",
        }

    chains = {row.get("id"): row for row in load_signature_chains()}
    for item_id, chain_id in (
        ("A3-02-legal", "legal"),
        ("A3-02-group", "group"),
    ):
        chain = chains.get(chain_id)
        if chain and item_id not in out:
            out[item_id] = {
                "clause_id": f"configured:signature_chain:{chain_id}",
                "doc_id": None,
                "doc_no": None,
                "doc_title": chain.get("name"),
                "authority_level": "configured_rule",
                "marker": None,
                "text": chain.get("source") or chain.get("name"),
                "page_start": None,
                "page_end": None,
                "human_verified": False,
                "source_kind": "configured_rule",
            }
    out.setdefault("A3-02-timing", {
        "clause_id": "configured:signature_chain:timing",
        "doc_id": None,
        "doc_no": None,
        "doc_title": "分包报批与合同开工日期时序规则",
        "authority_level": "configured_rule",
        "marker": None,
        "text": "分包报批与合同开工日期的先后时序核对规则",
        "page_start": None,
        "page_end": None,
        "human_verified": False,
        "source_kind": "configured_rule",
    })
    out.setdefault("A2-02", {
        "clause_id": "configured:certificate_validity",
        "doc_id": None,
        "doc_no": None,
        "doc_title": "证照有效期核对规则",
        "authority_level": "configured_rule",
        "marker": None,
        "text": "证照有效期应相对于核查基准日核对；材料声明不能替代实际证照核验",
        "page_start": None,
        "page_end": None,
        "human_verified": False,
        "source_kind": "configured_rule",
    })
    # A2 与内部记录核查的依据来自代码中已冻结的规则源说明，仍显式保持配置规则类型。
    from .a2_check import SOURCE_R, SOURCE_Q
    from .certificate_number_check import ITEM_ID as CERT_NUMBER_ITEM_ID, SOURCE as CERT_NUMBER_SOURCE
    from .identity_observation import ITEM_ID as IDENTITY_ITEM_ID, SOURCE as IDENTITY_SOURCE
    from .internal_records import SOURCE as INTERNAL_SOURCE
    out.setdefault("A2-01", {
        "clause_id": "configured:social_security_coverage",
        "doc_id": None,
        "doc_no": None,
        "doc_title": "社保资料覆盖管理人员规则",
        "authority_level": "configured_rule",
        "marker": None,
        "text": "按主要管理人员名单核对社保资料人员覆盖；合同缴费义务不能替代社保缴费记录（2026-10-06配置口径）",
        "page_start": None,
        "page_end": None,
        "human_verified": False,
        "source_kind": "configured_rule",
    })
    out.setdefault("A2-03", {
        "clause_id": "configured:certificate_registered_to",
        "doc_id": None,
        "doc_no": None,
        "doc_title": "证件注册单位一致性规则",
        "authority_level": "configured_rule",
        "marker": None,
        "text": SOURCE_R,
        "page_start": None,
        "page_end": None,
        "human_verified": False,
        "source_kind": "configured_rule",
    })
    out.setdefault("A2-04", {
        "clause_id": "configured:qualification_match",
        "doc_id": None,
        "doc_no": None,
        "doc_title": "资质与承揽范围匹配规则",
        "authority_level": "configured_rule",
        "marker": None,
        "text": SOURCE_Q,
        "page_start": None,
        "page_end": None,
        "human_verified": False,
        "source_kind": "configured_rule",
    })
    out.setdefault(CERT_NUMBER_ITEM_ID, {
        "clause_id": "configured:manager_b_certificate_number",
        "doc_id": None,
        "doc_no": None,
        "doc_title": "人员证书编号一致性核对规则",
        "authority_level": "configured_rule",
        "marker": None,
        "text": CERT_NUMBER_SOURCE,
        "page_start": None,
        "page_end": None,
        "human_verified": False,
        "source_kind": "configured_rule",
    })
    out.setdefault(IDENTITY_ITEM_ID, {
        "clause_id": "configured:identity_document_observation",
        "doc_id": None, "doc_no": None, "doc_title": "身份证资料原文对照",
        "authority_level": "configured_rule", "marker": None, "text": IDENTITY_SOURCE,
        "page_start": None, "page_end": None, "human_verified": False, "source_kind": "configured_rule",
    })
    out.setdefault("BS-INT-01", {
        "clause_id": "configured:internal_records",
        "doc_id": None,
        "doc_no": None,
        "doc_title": "业主内部信用与事故材料核查规则",
        "authority_level": "configured_rule",
        "marker": None,
        "text": INTERNAL_SOURCE,
        "page_start": None,
        "page_end": None,
        "human_verified": False,
        "source_kind": "configured_rule",
    })
    return out
