"""分包申报审查判定内核：负面清单阈值比较 + 转包/违法分包条文族（本期五步第②步），
另含两项**后续功能**能力（分包金额占比、工程量占比，业主部署文件 二、（六）预留接口）。

三态语义（全站统一，唯一定义点见 models.STATUS_LABELS）：命中=风险情形成立 /
未命中=证据显示不成立 / 不可判定=证据不足（必须写明缺什么）。

本期内核（依据坐标见 data/review_flow.json 第②步）：
- 负面清单（《交办公路〔2024〕6号》＋《沪交行规〔2026〕8号》第九条）：触发词仅做召回，
  判定必须「抽出数值再比」——阈值抽不到数值 → 不可判定；绝不做「描述里有桥就命中」的语义判定；
- 转包 / 违法分包 / 以劳务合作名义分包（《沪交行规〔2026〕8号》第十五～十七条）：
  条文对象是**事实证据**（采购租赁发票、劳动合同与社保关系、款项流向、劳务计取范围），
  缺证据一律不可判定。

后续功能（默认关闭，见 data/config.json 的 amount_ratio／workload_ratio）：
- 金额占比：含税口径守门（任一侧未标注税额口径或两侧不一致 → 不可判定，不静默统一）；
  双口径试算（含劳务／不含劳务），结论分歧 → 不可判定（不二选一）；
  80% 阈值出处＝交委智能体口径，报告不得写「依据《实施细则》」（原文无该阈值）。
"""
import re

from .models import STATUS_HIT, STATUS_MISS, STATUS_UNDECIDABLE, BsFinding
from .parsing import describe_reading_gap, material_page_readable, material_reading_gap
from .rules import (load_config, load_negative_matchers,
                    load_province_negative_scope, load_transfer_rules,
                    negative_list_rules)

# ==================== A1-01 金额占比内核 ====================

# 阈值/出处全部来自 data/config.json（规则=数据，W4 特色规则配置演示载体）。
# 每次判定直读文件不缓存：改数据文件 → 重新核查 → 结论变化，可 diff、可回滚。


def amount_threshold() -> float:
    return float(load_config()["amount_ratio"]["threshold"])


def amount_source() -> str:
    return load_config()["amount_ratio"]["source_note"]

_AMT_RE = re.compile(
    r"(合同总金额|签约合同价|合同总价|合同金额|合同价|承包金额|分包金额|合同价款|劳务报酬|金额|总价)"
    # 合同扫描件常见“签约合同价（含税）为人民币 … 元”写法；
    # 允许标签后的税额括注和“为人民币”等结构，但仍要求紧邻金额单位，避免从无关段落猜金额。
    r"(?:[（(][^）)]{0,20}[）)])?[^0-9]{0,24}(?:人民币|￥|¥)?"
    r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(亿元|万元|元)")
_STRONG_KEYWORDS = ("合同总金额", "签约合同价", "合同总价", "合同金额", "合同价",
                    "承包金额", "分包金额", "合同价款")
_UNIT_TO_WAN = {"亿元": 10000.0, "万元": 1.0, "元": 0.0001}
_EXCL_TAX_HINTS = ("不含税", "不含增值税", "税前", "除税")   # 先查不含税：「不含税」包含「含税」
_INCL_TAX_HINTS = ("含税", "含增值税", "税后", "税率", "价内税")
_TAX_LABELS = {"incl": "含税", "excl": "不含税", "unknown": "未标注"}

_WS_RE = re.compile(r"\s+")
_TEX_TEXT_RE = re.compile(r"\\text\s*\{")


def _normalize_amount_text(text: str) -> str:
    """去除 OCR 文本中的 TeX 包装，保留合同金额语义。

    部分 PDF/OCR 产物会把同一行拆成 ``\\text{...}862...\\text{元}``；
    金额解析只需要移除包装符，不能让渲染标记阻断金额与单位的配对。
    """
    text = _TEX_TEXT_RE.sub("", text or "")
    return text.replace("$", "").replace("{", "").replace("}", "")


def _readable_pages(material) -> list:
    """仅将可追溯且通过读取质量检查的页用于规则取证。"""
    return [p for p in (material.pages_json or [])
            if isinstance(p, dict) and type(p.get("page")) is int and p["page"] > 0
            and material_page_readable(material, p["page"])]


def _norm_pages(material) -> list:
    return [(p.get("page"), _normalize_amount_text(_WS_RE.sub("", p.get("text", ""))))
            for p in _readable_pages(material)]


def _material_text(material) -> str:
    return "\n".join(p.get("text", "") for p in _readable_pages(material))


def _optional_general_source_readable(materials: list, *, scope_policy_enabled: bool,
                                     general_source_state: str | None) -> bool:
    """Keep optional-source coverage decisions explicit and fail closed on mismatches."""
    if not scope_policy_enabled:
        return True
    if general_source_state is not None and general_source_state != "readable":
        return False
    return any(m.role == "general" and m.parse_state == "done"
               and _material_text(m).strip() for m in materials)


def _detect_tax(window: str) -> str:
    if any(h in window for h in _EXCL_TAX_HINTS):
        return "excl"
    if any(h in window for h in _INCL_TAX_HINTS):
        return "incl"
    return "unknown"


def _detect_tax_for_amount(text: str, match) -> str:
    """Bind a tax label to the amount it qualifies.

    A clause can list inclusive, exclusive and tax values on one line. A
    symmetric window would make the first amount inherit a later ``不含税``
    label, so prefer the label before the amount and only inspect a short suffix
    for forms such as ``金额 100 元（含税）``.
    """
    detected = _detect_tax(match.group(0))
    if detected != "unknown":
        return detected
    prefix = text[max(0, match.start() - 40):match.start()]
    detected = _detect_tax(prefix)
    if detected != "unknown":
        return detected
    suffix = text[match.end():min(len(text), match.end() + 16)]
    return _detect_tax(suffix)


def extract_contract_amount(material) -> dict:
    """从一份合同文本层抽取主金额：优先强关键词，回退泛关键词。

    返回 {found, value_wan, tax, quote, page}；金额单位统一换算为万元。
    """
    candidates = []
    for page_no, text in _norm_pages(material):
        for m in _AMT_RE.finditer(text):
            value = float(m.group(2).replace(",", ""))
            value_wan = value * _UNIT_TO_WAN[m.group(3)]
            # 允许“含税”出现在金额前，或紧跟单位之后；窗口不跨到下一条金额，
            # 避免首个含税总价被后续“不含税金额”反向覆盖。
            candidates.append({
                "strong": m.group(1) in _STRONG_KEYWORDS,
                "value_wan": value_wan,
                "tax": _detect_tax_for_amount(text, m),
                "quote": text[max(0, m.start() - 20):m.end() + 20],
                "page": page_no,
            })
    if not candidates:
        return {"found": False}
    strong = [c for c in candidates if c["strong"]]
    chosen = strong[0] if strong else candidates[0]
    chosen = dict(chosen)
    chosen["found"] = True
    return chosen


def _verdict_of_ratio(ratio: float) -> str:
    # 已冻结：达到阈值即预警（comparison=greater_or_equal，config.json）。
    return STATUS_HIT if ratio >= amount_threshold() else STATUS_MISS


def _undecidable_finding(task, item_id: str, kind: str, message: str,
                         source: str, material_id=None, missing: list | None = None) -> BsFinding:
    """不可判定结论。`missing` 是结构化缺件 **key** 列表（BS-P1-12），落 `missing_json`。

    只落 key、不落中文标签：文案改过一次（P1-02），把中文存进库等于让历史结论跟着
    文案版本走；key 稳定、可归并、可统计（P1-06 面板按材料类汇总用）。标签与
    key→材料类归并在 `data/missing_material_map.json`，现读现算。

    调用方传 `missing=missing` 即可；未传时 `missing_json` 为 NULL，凡消费方都必须
    按「历史数据 → 从 message 文案回落」处理，不得当成"没有缺件"。
    """
    return BsFinding(task_id=task.id, material_id=material_id, item_id=item_id,
                     kind=kind, status=STATUS_UNDECIDABLE, message=message,
                     quote=None, page=None, source=source,
                     missing_json=list(missing) if missing else None)


def _fmt_wan(v: float) -> str:
    return format(v, ",.2f").rstrip("0").rstrip(".") + "万元"


def run_amount_ratio_check(task, materials: list, *, scope_policy_enabled: bool = False,
                            general_source_state: str | None = None) -> list:
    """A1-01：分母=总包合同金额（已确认口径），分子=分包合同累计，双口径试算。"""
    if not _optional_general_source_readable(
            materials, scope_policy_enabled=scope_policy_enabled,
            general_source_state=general_source_state):
        return []
    general = [m for m in materials if m.role == "general" and m.parse_state == "done"]
    subs = [m for m in materials if m.role == "sub" and m.parse_state == "done"]
    labors = [m for m in materials if m.role == "labor" and m.parse_state == "done"]

    if not general:
        return [_undecidable_finding(task, "A1-01", "amount_ratio",
                                     "不可判定：无已解析完成的总包合同（缺：总包合同金额）",
                                     amount_source())]
    if not subs:
        return [_undecidable_finding(task, "A1-01", "amount_ratio",
                                     "不可判定：无已解析完成的分包合同（缺：分包合同金额）",
                                     amount_source())]

    denom = extract_contract_amount(general[0])
    if not denom["found"]:
        return [_undecidable_finding(
            task, "A1-01", "amount_ratio",
            "不可判定：总包合同《%s》未抽取到合同金额（缺：合同金额）" % general[0].file_name,
            amount_source(), general[0].id)]
    if denom["tax"] == "unknown":
        return [_undecidable_finding(
            task, "A1-01", "amount_ratio",
            "不可判定：总包合同《%s》金额未标注税额口径（缺：含税/不含税标注）" % general[0].file_name,
            amount_source(), general[0].id)]

    amounts = []      # [(material, amount)]
    for m in subs + labors:
        amt = extract_contract_amount(m)
        if not amt["found"]:
            return [_undecidable_finding(
                task, "A1-01", "amount_ratio",
                "不可判定：%s《%s》未抽取到合同金额（缺：合同金额）" % (
                    "分包合同" if m.role == "sub" else "劳务合作合同", m.file_name),
                amount_source(), m.id)]
        if amt["tax"] == "unknown":
            return [_undecidable_finding(
                task, "A1-01", "amount_ratio",
                "不可判定：%s《%s》金额未标注税额口径（缺：含税/不含税标注）" % (
                    "分包合同" if m.role == "sub" else "劳务合作合同", m.file_name),
                amount_source(), m.id)]
        if amt["tax"] != denom["tax"]:
            return [_undecidable_finding(
                task, "A1-01", "amount_ratio",
                "不可判定：%s《%s》与总包合同税额口径不一致（%s vs %s），不静默统一" % (
                    "分包合同" if m.role == "sub" else "劳务合作合同",
                    m.file_name, _TAX_LABELS[amt["tax"]], _TAX_LABELS[denom["tax"]]),
                amount_source(), m.id)]
        amounts.append((m, amt))

    sub_sum = sum(amt["value_wan"] for m, amt in amounts if m.role == "sub")
    labor_sum = sum(amt["value_wan"] for m, amt in amounts if m.role == "labor")
    ratio_no_labor = sub_sum / denom["value_wan"]
    verdicts = {"不含劳务分包": _verdict_of_ratio(ratio_no_labor)}

    if labors:  # 有劳务合同（金额已过守门，能进到这里必然抽到了）
        ratio_all = (sub_sum + labor_sum) / denom["value_wan"]
        verdicts["含劳务分包"] = _verdict_of_ratio(ratio_all)
    # 未上传劳务合同时两口径分子相同（软缺口已在启动校验明示），按可用口径出结论

    values = set(verdicts.values())
    th_pct = "%.0f%%" % (amount_threshold() * 100)   # 阈值来自 config.json，文案随配置走
    tax_txt = "%s口径，阈值%s（达到即预警）" % (_TAX_LABELS[denom["tax"]], th_pct)
    if len(values) > 1:
        detail = "；".join("%s=%.1f%%→%s" % (k, (sub_sum if k == "不含劳务分包"
                        else sub_sum + labor_sum) / denom["value_wan"] * 100, v)
                        for k, v in verdicts.items())
        return [BsFinding(
            task_id=task.id, material_id=general[0].id, item_id="A1-01",
            kind="amount_ratio", status=STATUS_UNDECIDABLE,
            message="不可判定：%s阈值结论随分子累计口径变化（%s）。%s" % (th_pct, detail, tax_txt),
            quote=denom["quote"], page=denom["page"], source=amount_source())]

    status = values.pop()
    base = ratio_no_labor
    ratio_txt = "；".join("%s=%.1f%%" % (k, (sub_sum if k == "不含劳务分包"
                          else sub_sum + labor_sum) / denom["value_wan"] * 100)
                          for k in verdicts)
    if status == STATUS_HIT:
        # T11-D-A §三 Task1：不得用「转包」措辞暗示金额占比结论等同转包识别
        # （转包情形识别属 A1-03，本包未实现）。仅陈述金额占比事实与人工复核建议，
        # 金额比例判定本身不变。
        message = ("命中：分包合同金额累计占比达到%s及以上（%s，分母=总包合同%s，%s），"
                   "请人工复核分包范围与合同规定的允许分包范围是否一致"
                   % (th_pct, ratio_txt, _fmt_wan(denom["value_wan"]), tax_txt))
    else:
        message = "未命中：分包合同金额累计占比低于%s（%s，分母=总包合同%s，%s）" % (
            th_pct, ratio_txt, _fmt_wan(denom["value_wan"]), tax_txt)
    return [BsFinding(
        task_id=task.id, material_id=general[0].id, item_id="A1-01",
        kind="amount_ratio", status=status, message=message,
        quote=denom["quote"], page=denom["page"], source=amount_source())]


# ==================== A1-02 负面清单阈值比较内核 ====================

_NUM = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
_UNIT_SCALE = {"千米": 1000.0, "米": 1.0, "m": 1.0, "M": 1.0,
               "万吨": 10000.0, "吨": 1.0, "t": 1.0, "T": 1.0,
               "千克": 0.001, "kg": 0.001, "公斤": 0.001,
               "平方米": 1.0, "㎡": 1.0, "m2": 1.0, "m²": 1.0, "平方千米": 1e6}
_OPS = {">=": lambda a, b: a >= b, ">": lambda a, b: a > b}


def _compile_extractors(raw: dict) -> dict:
    compiled = {}
    for key, pattern in raw.items():
        assert "NUM" in pattern, "extractor %s 缺 NUM 占位符" % key
        compiled[key] = re.compile(pattern.replace("NUM", _NUM))
    return compiled


_EXTRACTORS = _compile_extractors(load_negative_matchers()["extractors"])
_MATCHERS = load_negative_matchers()["matchers"]
_NEGATIVE_SOURCE = "《公路工程施工分包负面清单（2024年版）》"


def _extract_threshold(key: str, page_text: str, material, *, fallback_text=None) -> tuple:
    """抽取某阈值 key 的数值：先当前页，回退整份材料。返回 (值, 匹配文本) 或 (None, None)。"""
    pat = _EXTRACTORS[key]
    m = pat.search(page_text)
    source_text = page_text
    if m is None:
        full = (_WS_RE.sub("", _material_text(material))
                if fallback_text is None else fallback_text)
        m = pat.search(full)
        source_text = full
    if m is None:
        return None, None
    value = float(m.group(1).replace(",", "")) * _UNIT_SCALE[m.group(2)]
    return value, source_text[max(0, m.start() - 20):m.end() + 20]


def _compare_rule_thresholds(rule: dict, page_text: str, material, *, fallback_text=None) -> tuple:
    """确定性比较。返回 (status, quote, page, missing_keys, detail)。

    - all 逻辑：任一已抽值不满足 → 未命中；全部抽出且都满足 → 命中；有缺 → 不可判定
    - any 逻辑：任一已抽值满足 → 命中；全部抽出且都不满足 → 未命中；缺值 → 不可判定
    """
    logic = rule.get("threshold_logic", "all")
    extracted = []   # [(key, op, threshold, value, quote_text)]
    missing = []
    for th in rule.get("thresholds", []):
        value, quote_text = _extract_threshold(th["key"], page_text, material,
                                              fallback_text=fallback_text)
        if value is None:
            missing.append(th["key"])
        else:
            extracted.append((th["key"], th["op"], th["value"], value, quote_text))
    detail = "；".join("%s=%s%s%s" % (k, v, op, t) for k, op, t, v, _ in extracted)
    quote_text = extracted[0][4] if extracted else None

    if not extracted:
        return (STATUS_UNDECIDABLE, None, None, missing, "阈值数值均未抽取到")
    if logic == "any":
        if any(_OPS[op](v, t) for _, op, t, v, _ in extracted):
            return (STATUS_HIT, quote_text, None, missing, detail)
        if missing:
            return (STATUS_UNDECIDABLE, quote_text, None, missing, detail)
        return (STATUS_MISS, quote_text, None, missing, detail)
    # all 逻辑
    if any(not _OPS[op](v, t) for _, op, t, v, _ in extracted):
        return (STATUS_MISS, quote_text, None, missing, detail)
    if missing:
        return (STATUS_UNDECIDABLE, quote_text, None, missing, detail)
    return (STATUS_HIT, quote_text, None, missing, detail)


def _trigger_hit(matcher: dict, text: str):
    """触发词召回。返回 (trigger, match_span_text) 或 None。"""
    for trigger in matcher.get("triggers", []):
        m = re.search(re.escape(trigger), text)
        if m:
            return trigger, text[max(0, m.start() - 20):m.end() + 20]
    return None


_NEGATIVE_QUOTED_RE = re.compile(
    r"(?:规范|制度|规则|办法|条款|清单)(?:引用|规定|要求|示例|说明|摘录|原文)"
    r"|(?:引用|引文|条文摘录)[:：]|《[^》]+》(?:规定|要求)"
)
_NEGATIVE_UNCERTAIN_RE = re.compile(
    r"(?:^|[:：，,])(?:若|如|如果|假如|倘若|一旦)"
    r"|未来可能|将来可能|可能采用|尚待确认|尚未确定|范围待定"
)
_NEGATIVE_DENIED_RE = re.compile(
    r"(?:(?:不|未|没有|并非)(?:采用|使用|涉及|包括|包含)|不含|禁止|严禁|不得|不应|无需).{0,24}$"
)


def _negative_fact_limit(clause: str, trigger: str = "") -> str | None:
    """词面召回不能把被否定、条件或规范引用内容当成实际分包范围。"""
    end = clause.find(trigger) + len(trigger) if trigger and trigger in clause else len(clause)
    context = clause[:end]
    # 后面的变更条件不支配已经明示的范围；只检查该命中之前及其自身的语境。
    if _NEGATIVE_QUOTED_RE.search(context) or _NEGATIVE_UNCERTAIN_RE.search(context):
        return "仅见规范引用、条件或待确认表述，尚无足够分包范围事实，请核对合同及实际范围"
    prefix = clause[:clause.find(trigger)] if trigger and trigger in clause else clause
    if _NEGATIVE_DENIED_RE.search(prefix):
        return "仅见否定或禁止表述，尚不能据此确认分包范围事实，请核对原件及实际范围"
    return None


def _negative_clauses(text: str) -> list:
    return [clause for clause in re.split(r"[。；;！？!?]", text) if clause.strip()]


def _province_delegated_finding(task, rule: dict) -> BsFinding:
    """负面清单第三条：是否增补由省级交通运输主管部门自行确定。

    三种书面状态分开判，**不合并、不默认**：
    - 省级已明文「暂不增补」→ 未触发，并原文引用该条（上海：沪交行规〔2026〕8号第九条）；
    - 省级已增补但清单未入库 → 不可判定（缺：省级增补清单）；
    - 未登记或省级未就此表述 → 不可判定（缺：省级书面口径）。
    只有拿到省级书面表述才判未触发；没有书面依据时不得默认「未增补」。
    """
    scope = load_province_negative_scope()
    region = scope.get("default_region")
    record = (scope.get("regions") or {}).get(region) or {}
    state = record.get("status") or "unknown"
    base = "「%s」授权省级交通运输主管部门确定是否增补" % rule["article"]

    if state == "not_augmented":
        doc = "《%s》（%s）%s" % (record.get("doc_title", ""), record.get("doc_no", ""),
                                 record.get("article_label", ""))
        tail = "" if record.get("human_verified") else "；该条款文本为 OCR 识别，待人工校对"
        return BsFinding(
            task_id=task.id, material_id=None, item_id=rule["id"],
            kind="negative_list", status=STATUS_MISS,
            message="未触发：%s，%s已明文暂不增补——“%s”，故不构成本条负面清单情形%s"
                    % (base, doc, record.get("quote", ""), tail),
            quote=None, page=None, source=doc)
    if state == "augmented":
        return BsFinding(
            task_id=task.id, material_id=None, item_id=rule["id"],
            kind="negative_list", status=STATUS_UNDECIDABLE,
            message="不可判定：%s，%s已公布增补清单但清单未入库（缺：省级增补清单）"
                    % (base, region or "本省"), quote=None, page=None,
            source="%s%s" % (_NEGATIVE_SOURCE, rule["article"]))
    return BsFinding(
        task_id=task.id, material_id=None, item_id=rule["id"],
        kind="negative_list", status=STATUS_UNDECIDABLE,
        message="不可判定：%s，%s增补口径未见书面清单（缺：省级增补清单）"
                % (base, region or "本省"), quote=None, page=None,
        source="%s%s" % (_NEGATIVE_SOURCE, rule["article"]))


def run_negative_list_check(task, materials: list) -> list:
    """A1-02：分包/劳务合同范围 vs 负面清单 21 叶。

    只对「触发词召回」的条目出结论；未召回条目不逐条刷屏（报告汇总里说明覆盖面）。
    同一规则跨材料聚合：命中 > 不可判定 > 未命中。
    """
    contracts = [m for m in materials if m.role in ("sub", "labor")]
    gaps = [(m, material_reading_gap(m)) for m in contracts]
    gaps = [(m, pages) for m, pages in gaps if pages is not None]
    incomplete = bool(gaps)
    scoped = [m for m in contracts
              if m.role in ("sub", "labor") and m.parse_state == "done"
              and _material_text(m).strip()]
    rules = negative_list_rules()
    findings = []
    prepared = []
    for material in scoped:
        candidates = [(page_no, clause) for page_no, page_text in _norm_pages(material)
                      for clause in _negative_clauses(page_text)]
        # 同一合同的可靠页面及事实片段对所有条目相同，只读取和切分一次。
        factual_text = "。".join(
            fragment.group() for _, clause in candidates
            for fragment in re.finditer(r"[^，,]+", clause)
            if _negative_fact_limit(clause[:fragment.end()]) is None
        )
        prepared.append((material, candidates, factual_text))

    for rule in rules:
        if rule.get("kind") == "province_delegated":
            findings.append(_province_delegated_finding(task, rule))
            continue
        matcher = _MATCHERS.get(rule["id"])
        if not matcher or not matcher.get("triggers"):
            continue

        context_re = re.compile(matcher["context"]) if matcher.get("context") else None
        best = None   # (nonfactual_only, status_rank, finding)
        for m, candidates, factual_text in prepared:
            for page_no, text in candidates:
                hit = _trigger_hit(matcher, text)
                if hit is None:
                    continue
                trigger, trig_quote = hit
                fact_limit = _negative_fact_limit(text, trigger)
                if fact_limit:
                    status, quote, detail, missing, page = (
                        STATUS_UNDECIDABLE, trig_quote, fact_limit, [], page_no)
                elif context_re is not None and not context_re.search(text):
                    # 触发词在场但缺上下文限定：不硬判，标不可判定（缺什么写清）
                    status, quote, detail = STATUS_UNDECIDABLE, trig_quote, \
                        "触发词「%s」在场但未见上下文限定（缺：%s）" % (trigger, matcher["context"])
                    missing, page = [], page_no
                elif not rule.get("thresholds"):
                    status, quote, detail, missing, page = \
                        STATUS_HIT, trig_quote, "工法/对象类条目，触发词「%s」确定性命中" % trigger, [], page_no
                else:
                    status, quote, _pg, missing, detail = \
                        _compare_rule_thresholds(rule, text, m, fallback_text=factual_text)
                    page = page_no
                    if status == STATUS_UNDECIDABLE and missing:
                        detail = "阈值未抽取到（缺：%s）" % "、".join(missing)
                    elif status == STATUS_UNDECIDABLE:
                        detail = detail or "阈值结论不一致"
                # 例外情形只提示不翻案（人工终审）
                exceptions = [e for e in rule.get("exceptions", []) if e in text]
                if exceptions and status == STATUS_HIT:
                    detail += "；注意例外情形「%s」（人工终审确认）" % "、".join(exceptions)
                rule_text = rule["text"].rstrip("。；;")
                if status == STATUS_HIT:
                    message = "命中：分包范围涉及负面清单%s「%s」——%s（%s）" % (
                        rule["article"], rule_text, detail, "阈值抽取比对成立")
                elif status == STATUS_MISS:
                    message = "未命中：负面清单%s「%s」——%s" % (rule["article"], rule_text, detail)
                else:
                    message = "不可判定：负面清单%s「%s」——%s" % (rule["article"], rule_text, detail)
                finding = BsFinding(
                    task_id=task.id, material_id=m.id, item_id=rule["id"],
                    kind="negative_list", status=status, message=message[:500],
                    quote=quote, page=page,
                    source="%s%s" % (_NEGATIVE_SOURCE, rule["article"]))
                rank = {STATUS_HIT: 0, STATUS_UNDECIDABLE: 1, STATUS_MISS: 2}[status]
                # 制度/否定提示只能在没有实际范围证据时兜底，不能盖过其余实证。
                score = (bool(fact_limit), rank)
                if best is None or score < best[:2]:
                    best = (*score, finding)
        if best is not None:
            finding = best[2]
            if incomplete and finding.status == STATUS_MISS:
                descriptions = []
                for material, pages in gaps[:3]:
                    page_text = describe_reading_gap(material, pages, limit=8)
                    descriptions.append("「%s」%s" % (getattr(material, "file_name", None) or "合同", page_text))
                if len(gaps) > 3:
                    descriptions.append("另有%d份合同未读" % (len(gaps) - 3))
                finding.status = STATUS_UNDECIDABLE
                finding.message = ("不可判定：%s；已识别范围不能代表全部范围；%s"
                                   % ("；".join(descriptions), finding.message))[:500]
            findings.append(finding)
    return findings


# ==================== A1←A3 依赖判定（V16-01 / L17-02） ====================
# 03-需求基线 §7.5 硬约束：这两项的判定证据在 A3 报审材料侧，A1 读证据出结论，
# 不重复执行 A3-01 的对表；A3 未跑或证据不足时自动判「不可判定」。
# 口径已拍板（2026-09-20）：V16-01 或逻辑从严；L17-02 劳务合同+报审包双通道。

_DEPENDENCY_KIND = "a1_dependency"
_V16_01_SOURCE = ("《实施细则》第十六条视为（一）；取数：A3-01 RC-09（总监签章）"
                  "——A1 证据取自 A3，不自行重判")
_L17_02_SOURCE = ("《实施细则》第十七条（二）、第十四条；取数：劳务合同责任条款 + 报审包方案署名双通道")

# L17-02 责任方向判定（normalized 文本）；窗口类排除句界（。；;，,），防跨分句误命中：
# 命中=责任派给劳务侧；未命中=明确派给承包人/总包侧；都没有=证据不足
_L17_HIT_RES = (
    re.compile(r"(劳务|作业)[^。；;，,]{0,12}(负责|承担)[^。；;，,]{0,12}(施工方案|专项方案|方案编制|试验检测|测量|档案|质安|质量安全)"),
    re.compile(r"(施工方案|专项方案|方案编制|试验检测|测量|档案|质安)[^。；;，,]{0,12}由[^。；;，,]{0,10}(劳务|作业)"),
)
_L17_MISS_RES = (
    re.compile(r"(承包人|总承包|总包|项目部)[^。；;，,]{0,10}(负责|承担|编制)[^。；;，,]{0,12}(施工方案|专项方案|方案编制|试验检测|测量|档案|质安)"),
    re.compile(r"(施工方案|专项方案|方案编制|试验检测|测量|档案|质安)[^。；;，,]{0,12}由[^。；;，,]{0,10}(承包人|总承包|总包|项目部)[^。；;，,]{0,6}(负责|编制|承担)"),
)
_L17_LABOR_SIGN_RE = re.compile(r"编制(?:单位|人)?[：:][^。；;，,]{0,24}劳务")


def _match_first(patterns, text: str):
    for pat in patterns:
        m = pat.search(text)
        if m:
            return text[max(0, m.start() - 20):m.end() + 20]
    return None


def _check_v16_01(task, a3_findings: list) -> list:
    """V16-01：分包合同未经监理审查或未报发包人书面同意（或逻辑从严）。"""
    rc09 = next((f for f in a3_findings if f.item_id == "RC-09"), None)
    if rc09 is None:
        return [_undecidable_finding(
            task, "V16-01", _DEPENDENCY_KIND,
            "不可判定：A3-01 对表无总监签章结论（缺：A3-01 对表未执行，或任务未选 A3 范围）",
            _V16_01_SOURCE)]
    if rc09.status == STATUS_HIT:
        return [BsFinding(
            task_id=task.id, material_id=rc09.material_id, item_id="V16-01",
            kind=_DEPENDENCY_KIND, status=STATUS_HIT,
            message=("命中：报审表未见总监理工程师签章（取自 A3-01 RC-09），"
                     "「未经监理审查」侧风险成立；「未报发包人书面同意」侧无 A3 条目，请人工复核"),
            quote=rc09.quote, page=rc09.page, source=_V16_01_SOURCE)]
    if rc09.status == STATUS_MISS:
        return [_undecidable_finding(
            task, "V16-01", _DEPENDENCY_KIND,
            ("不可判定：总监理工程师签章在场（A3-01 RC-09 未命中），但「未报发包人书面同意」侧"
             "无 A3 结论条目（缺：发包人书面同意），或逻辑下无法排除风险"),
            _V16_01_SOURCE)]
    return [_undecidable_finding(
        task, "V16-01", _DEPENDENCY_KIND,
        "不可判定：总监签章侧证据不足（A3-01 RC-09 判不可判定）；发包人书面同意侧亦无 A3 条目",
        _V16_01_SOURCE)]


def _check_l17_02(task, materials: list) -> list:
    """L17-02：由劳务企业自行负责方案编制/试验检测/测量/档案/质安（双通道）。"""
    labor = [m for m in materials if m.role == "labor" and m.parse_state == "done"]
    packet = [m for m in materials if m.role == "packet" and m.parse_state == "done"]

    hit_quote = hit_mat = hit_page = None
    miss_quote = miss_mat = miss_page = None
    for m in labor:
        for page_no, text in _norm_pages(m):
            q = _match_first(_L17_HIT_RES, text)
            if q and hit_quote is None:
                hit_quote, hit_mat, hit_page = q, m, page_no
            q = _match_first(_L17_MISS_RES, text)
            if q and miss_quote is None:
                miss_quote, miss_mat, miss_page = q, m, page_no
    sign_quote = sign_mat = sign_page = None
    for m in packet:
        for page_no, text in _norm_pages(m):
            mm = _L17_LABOR_SIGN_RE.search(text)
            if mm:
                sign_quote = text[max(0, mm.start() - 20):mm.end() + 20]
                sign_mat, sign_page = m, page_no
                break
        if sign_quote:
            break

    if hit_quote:  # 劳务合同把方案/检测等责任派给劳务侧，或报审包方案署名为劳务方
        return [BsFinding(
            task_id=task.id, material_id=hit_mat.id, item_id="L17-02",
            kind=_DEPENDENCY_KIND, status=STATUS_HIT,
            message="命中：劳务合同约定方案编制/试验检测/测量/档案/质安责任由劳务方承担（第十七条（二）情形）",
            quote=hit_quote, page=hit_page, source=_L17_02_SOURCE)]
    if sign_quote:
        return [BsFinding(
            task_id=task.id, material_id=sign_mat.id, item_id="L17-02",
            kind=_DEPENDENCY_KIND, status=STATUS_HIT,
            message="命中：报审包方案文件编制署名为劳务方（第十四条「分包人应当自行编制施工方案」反向证据）",
            quote=sign_quote, page=sign_page, source=_L17_02_SOURCE)]
    if miss_quote:
        return [BsFinding(
            task_id=task.id, material_id=miss_mat.id, item_id="L17-02",
            kind=_DEPENDENCY_KIND, status=STATUS_MISS,
            message="未命中：劳务合同明确方案编制/检测等责任由承包人（总包）侧承担",
            quote=miss_quote, page=miss_page, source=_L17_02_SOURCE)]
    if not labor and not packet:
        return [_undecidable_finding(
            task, "L17-02", _DEPENDENCY_KIND,
            "不可判定：无劳务合作合同且无报审资料包（缺：责任条款与编制署名双证据）",
            _L17_02_SOURCE)]
    return [_undecidable_finding(
        task, "L17-02", _DEPENDENCY_KIND,
        "不可判定：劳务合同未见方案编制责任条款，报审包亦未见劳务方署名（缺：责任条款或编制主体署名）",
        _L17_02_SOURCE)]


def run_a3_dependent_checks(task, a3_findings: list, materials: list) -> list:
    """A1←A3 依赖两项编排：V16-01 读 A3 结论，L17-02 读报审侧材料。"""
    return _check_v16_01(task, a3_findings) + _check_l17_02(task, materials)


# ==================== 工程量占比内核（内部 ID A1-X-WORKLOAD，30% 预警线，config.workload_ratio） ====================
# T11-D-A §二.1/2：`A1-03` 已归还业主语义「转包情形识别」（本包不实现其算法，不生成假 finding）；
# 30% 工程量占比改用**内部 ID `A1-X-WORKLOAD`**，不占用业主功能编号 A1-01..A1-06。
# 增量澄清（A1-RULE-01）：第十五条「全部工程发包给他人 / 全部工程肢解后分别发包」情形以
#   内部稳定 ID `T15-01` / `T15-02` 实现（见本文件末 A1-03 分节）；`A1-03` 编号仍归还业主
#   「转包情形识别」、不被占用，故 A1-03（业主编号）与 T15-xx（内部规则 id）并存不冲突。
# 口径（config.json source_note 同步）：与 80% 金额占比独立并行、互不换算；
# 证据=合同文本明示的工程量占比数值，未明示判不可判定（与负面清单「抽数值再比」同纪律）。

_WORKLOAD_ITEM_ID = "A1-X-WORKLOAD"
_WORKLOAD_KIND = "workload_ratio"
_WORKLOAD_SOURCE = "30%工程量预警线（出处：集团/业主管理口径，详见核查参数文件；与80%金额占比为两条独立并行口径，互不换算）"

# 召回：工「程量」语境（排除「金额」句）；抽取：明示百分比数值
_WORKLOAD_TRIGGER_RE = re.compile(r"(工程量|作业量|工作量)")
_WORKLOAD_PCT_RES = (
    re.compile(r"(?:占总?工程量|工程量比例|工程量占比|劳务作业量占比|作业量占比|工作量占比)"
               r"[^0-9%。；;]{0,10}(\d{1,3}(?:\.\d+)?)\s*%"),
    re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%[^。；;]{0,6}(?:的?)(?:总?工程量|作业量|工作量)"),
)


def _workload_pct(materials: list) -> tuple:
    """在分包/劳务合同文本层找明示的工程量占比数值。返回 (最大占比, 引文, 页码, material)。"""
    best = None   # (pct, quote, page, material)
    for m in materials:
        for page_no, text in _norm_pages(m):
            if not _WORKLOAD_TRIGGER_RE.search(text):
                continue
            for pat in _WORKLOAD_PCT_RES:
                mm = pat.search(text)
                if mm:
                    pct = float(mm.group(1))
                    if best is None or pct > best[0]:
                        best = (pct, text[max(0, mm.start() - 20):mm.end() + 20], page_no, m)
    return best


def run_workload_ratio_check(task, materials: list) -> list:
    """工程量占比 vs 30% 预警线（阈值/出处走 config.json，可改可回滚）。

    内部 ID `A1-X-WORKLOAD`（非业主 A1-03；见文件头 T11-D-A 说明）。
    scope 守卫在 service.run_a1_checks（与 A1-01/A1-02 同构，内核不重复查）。
    """
    scoped = [m for m in materials
              if m.role in ("sub", "labor") and m.parse_state == "done"
              and _material_text(m).strip()]
    if not scoped:
        return [_undecidable_finding(
            task, _WORKLOAD_ITEM_ID, _WORKLOAD_KIND,
            "不可判定：无可用分包/劳务合同（缺：合同文本）",
            _WORKLOAD_SOURCE)]

    best = _workload_pct(scoped)
    if best is None:
        return [_undecidable_finding(
            task, _WORKLOAD_ITEM_ID, _WORKLOAD_KIND,
            "不可判定：合同未明示工程量占比数值（缺：文本中写明的工程量/作业量占比）。"
            "工程量与金额是两条独立口径，本条不做金额换算推断",
            _WORKLOAD_SOURCE)]

    pct, quote, page, mat = best
    ratio = pct / 100.0
    th = float(load_config()["workload_ratio"]["threshold"])
    th_pct = "%.0f%%" % (th * 100)
    if ratio > th:
        return [BsFinding(
            task_id=task.id, material_id=mat.id, item_id=_WORKLOAD_ITEM_ID,
            kind=_WORKLOAD_KIND, status=STATUS_HIT,
            message="命中：合同明示工程量占比 %g%% 超过 %s 预警线（超过才预警），"
                    "请人工复核是否以劳务/分包形式承担主要工程量" % (pct, th_pct),
            quote=quote, page=page, source=_WORKLOAD_SOURCE)]
    return [BsFinding(
        task_id=task.id, material_id=mat.id, item_id=_WORKLOAD_ITEM_ID,
        kind=_WORKLOAD_KIND, status=STATUS_MISS,
        message="未命中：合同明示工程量占比 %g%% 未超过 %s 预警线" % (pct, th_pct),
        quote=quote, page=page, source=_WORKLOAD_SOURCE)]


# ==================== A1-03 第十五条（一）（二）合同范围核查内核（A1-RULE-01） ====================
# T15-01「全部工程发包给他人」/ T15-02「全部工程肢解后分别发包」纯规则内核。
# 本内核由 service.run_a1_checks 接线（service.py:1627，R05 已落地）；识别词、
# 交付/肢解动词、保留模式、工程对象词与标段锚点词全部来自 data/transfer_rules.json
# （规则=数据，改数据 → 重新核查 → 结论变化，可 diff 可回滚），本文件不硬编码任何词表。
# 三态语义（AGENTS 纪律 4）：仅「明文完整转移」或「明文肢解/分别发包」才可能命中；范围相似度
# 只降级为不可判定 + 人工提示，绝不直接命中；不引入语义相似度/向量/embedding/LLM（非目标）。

_TRANSFER_KIND = "transfer_scope"
_TRANSFER_REG_NAME = "《上海市公路工程施工分包管理实施细则》"
_CLAUSE_SPLIT_RE = re.compile(r"[。；;]")

# 证据侧 key → 中文标签（不可判定「缺什么」文案用，与目录 required_evidence 同名）。
_EVIDENCE_LABELS = {
    "general_scope": "总承包范围",
    "sub_scope": "分包范围",
    "contract_parties": "合同双方",
    "whole_project_transfer": "全部工程交付关系",
    "subcontract_scopes": "分包合同范围",
    "section_anchor": "标段锚点",
    "scope_coverage": "全部工程覆盖（肢解/分别发包）明文",
}


def _transfer_rule(rule_id: str) -> dict:
    """按内部稳定 ID 取规则目录条目（含 scope_detection 词表）。"""
    for rule in load_transfer_rules()["rules"]:
        if rule["id"] == rule_id:
            return rule
    raise KeyError("transfer rule not found: %s" % rule_id)


def _rule_source(rule: dict) -> str:
    """规则出处：法规名 + 条款 + PDF 页码 + 规则目录 id（页码读目录，不另写一份）。"""
    src = rule["source"]
    return "%s%s；PDF 第%d页（规则目录 %s）" % (
        _TRANSFER_REG_NAME, src["article"], src["page_start"], rule["id"])


def _missing_suffix(keys: list) -> str:
    return "（缺：%s）" % "、".join(_EVIDENCE_LABELS[k] for k in keys)


def _find_first(words: list, text: str):
    """返回 (命中词, 前后约 40 字引文切片) 或 None。"""
    for w in words:
        idx = text.find(w)
        if idx >= 0:
            return w, text[max(0, idx - 20):idx + len(w) + 20]
    return None


def _page_match(materials: list, words: list):
    """在材料页文本层找第一个命中词。返回 (material, page, quote, word) 或 None。"""
    for m in materials:
        for page_no, text in _norm_pages(m):
            hit = _find_first(words, text)
            if hit:
                word, quote = hit
                return (m, page_no, quote, word)
    return None


def _material_has(material, words: list) -> bool:
    for _, text in _norm_pages(material):
        if any(w in text for w in words):
            return True
    return False


def _first_parties(materials: list, words: list):
    """合同双方证据：某一页文本同时出现 ≥2 个主体词（发包人/承包人/…）才算识别到双方。

    只出现单侧主体（或全无）→ 未识别到合同双方，返回 None（主体不清）。
    """
    for m in materials:
        for page_no, text in _norm_pages(m):
            present = [w for w in words if w in text]
            if len(present) >= 2:
                idx = text.find(present[0])
                return (m, page_no, text[max(0, idx - 20):idx + 40], present)
    return None


def _first_scope_verb(clause: str, scopes: list, verbs: list, connectors: list):
    """子句内第一个「范围词紧跟（其间只允许连接词片段）交付/肢解动词」的出现。

    邻接判据：范围词必须是动词的**直接宾语** —— 范围词之后紧邻 `connector + verb`
    （connector 取空串或白名单之一，即二者之间只允许白名单片段、可为空）。由此把
    「全部工程量/质量/工期/进度/款…」这类「范围词 + 名词后缀」的**开放名词族**排除：
    其后紧邻的是名词而非动词。返回 (index, scope, verb) 或 None；
    范围词的每次出现都遍历（`find` 递进），全部不成立才试下一个范围词。
    """
    for scope in scopes:
        start = 0
        while True:
            i = clause.find(scope, start)
            if i < 0:
                break
            pos = i + len(scope)
            hit_verb = None
            for connector in [""] + connectors:
                for verb in verbs:
                    if clause.startswith(connector + verb, pos):
                        hit_verb = verb
                        break
                if hit_verb:
                    break
            if hit_verb:
                return (i, scope, hit_verb)
            start = i + len(scope)
    return None


def _composite_match(scopes: list, verbs: list, text: str, connectors: list):
    """同一子句（以 。；; 切分）内「范围词 × 交付/肢解动词」且二者**邻接**才算明文命中。

    - 范围词必须是动词的直接宾语（其间只允许连接词白名单片段，可为空）——见 `_first_scope_verb`；
    - 跨子句不得命中（同子句要求不变）；同子句内范围词的每次出现都遍历。
    返回 (quote, scope, verb) 或 None。
    """
    for clause in _CLAUSE_SPLIT_RE.split(text):
        hit = _first_scope_verb(clause, scopes, verbs, connectors)
        if hit is None:
            continue
        i, scope, verb = hit
        return (clause[max(0, i - 20):i + len(scope) + 40], scope, verb)
    return None


def _composite_over(materials: list, scopes: list, verbs: list, connectors: list):
    """跨材料页找第一个明文复合命中。返回 (material, page, quote, scope, verb) 或 None。"""
    for m in materials:
        for page_no, text in _norm_pages(m):
            hit = _composite_match(scopes, verbs, text, connectors)
            if hit:
                quote, scope, verb = hit
                return (m, page_no, quote, scope, verb)
    return None


def _first_head_tail(text: str, heads: list, tails: list):
    """文本内第一个「head 后**紧邻** tail」的出现。返回 (index, head, tail) 或 None。

    与 `_first_scope_verb` 同款邻接写法：head 每次出现都遍历（`find` 递进），
    其后必须紧跟一个 tail 条目才算成立，否则作废并继续找下一次出现。
    """
    for head in heads:
        start = 0
        while True:
            i = text.find(head, start)
            if i < 0:
                break
            pos = i + len(head)
            tail_hit = next((t for t in tails if text.startswith(t, pos)), None)
            if tail_hit is not None:
                return (i, head, tail_hit)
            start = i + len(head)
    return None


def _head_tail_over(materials: list, heads: list, tails: list):
    """跨材料页找第一个「head 后紧邻 tail」的命中。返回 (material, page, quote, head, tail) 或 None。"""
    for m in materials:
        for page_no, text in _norm_pages(m):
            hit = _first_head_tail(text, heads, tails)
            if hit:
                i, head, tail = hit
                return (m, page_no, text[max(0, i - 20):i + len(head) + 40], head, tail)
    return None


def _object_set(materials: list, objects: list) -> set:
    found = set()
    for m in materials:
        for _, text in _norm_pages(m):
            for o in objects:
                if o in text:
                    found.add(o)
    return found


def _similarity_high(general: list, subs: list, objects: list) -> bool:
    """确定性关键词重叠（非语义模型）：分包范围工程对象词非空且全部落在总包范围词集合内。

    命中此判据只用于**降级提示**（undecidable + 建议人工确认），绝不据此直接命中。
    """
    gen = _object_set(general, objects)
    sub = _object_set(subs, objects)
    if not gen or not sub:
        return False
    return sub <= gen


def _anchor_present(task, materials: list, anchor_words: list) -> bool:
    """标段锚点：任务标段/项目名非空，或合同文本命中标段锚点词。

    task 用 getattr 兼容旧夹具（SimpleNamespace(id=1) 无 contract_section/project_name）。
    """
    if getattr(task, "contract_section", None) or getattr(task, "project_name", None):
        return True
    for m in materials:
        if _material_has(m, anchor_words):
            return True
    return False


def _run_t15_01(task, general: list, subs: list, rule: dict) -> BsFinding:
    """T15-01：全部工程发包给他人（跨材料聚合为 1 条）。"""
    det = rule["scope_detection"]
    ek = det["evidence_keywords"]
    source = _rule_source(rule)

    transfer = _composite_over(general + subs, det["transfer_scopes"],
                               ek["whole_project_transfer"],
                               det["scope_verb_connectors"])
    general_scope = _page_match(general, ek["general_scope"])
    sub_scope = _page_match(subs, ek["sub_scope"])
    parties = _first_parties(general + subs, ek["contract_parties"])

    if transfer is not None:
        # 明文完整转移成立：四侧齐全 → 命中；任一侧缺失 → 不可判定并列明缺项。
        missing = []
        if general_scope is None:
            missing.append("general_scope")
        if sub_scope is None:
            missing.append("sub_scope")
        if parties is None:
            missing.append("contract_parties")
        mat, page, quote, scope, verb = transfer
        if not missing:
            return BsFinding(
                task_id=task.id, material_id=mat.id, item_id="T15-01",
                kind=_TRANSFER_KIND, status=STATUS_HIT,
                message="命中：合同出现「%s」完整转移明文（交付动词「%s」），请人工复核是否属"
                        "全部工程交由他人施工的线索" % (scope, verb),
                quote=quote, page=page, source=source)
        return _undecidable_finding(
            task, "T15-01", _TRANSFER_KIND,
            "不可判定：合同出现「%s」完整转移明文，但范围/主体证据不齐，不能据此判命中%s"
            % (scope, _missing_suffix(missing)),
            source, mat.id)

    retention = _page_match(general, det["retention_patterns"])
    if retention is not None:
        # 明文由总包保留施工组织且无完整转移明文 → 未命中。
        mat, page, quote, word = retention
        return BsFinding(
            task_id=task.id, material_id=mat.id, item_id="T15-01",
            kind=_TRANSFER_KIND, status=STATUS_MISS,
            message="未命中：合同明文由承包人（总包）自行保留施工组织（保留模式「%s」），"
                    "未见全部工程完整转移明文" % word,
            quote=quote, page=page, source=source)

    missing = []
    if general_scope is None:
        missing.append("general_scope")
    if sub_scope is None:
        missing.append("sub_scope")
    if parties is None:
        missing.append("contract_parties")
    missing.append("whole_project_transfer")
    base = "无法判断：现有材料未能确认分包范围是否覆盖总承包的全部工程"
    if _similarity_high(general, subs, det["similarity_objects"]):
        base += "；范围高度相似，建议人工确认是否整体转移"
    base += _missing_suffix(missing)
    if general_scope is None:
        base += "；请补充或核对载明总承包范围的总包合同及工程交付关系资料"
    return _undecidable_finding(task, "T15-01", _TRANSFER_KIND, base, source,
                                missing=missing)


def _run_t15_02(task, general: list, subs: list, rule: dict) -> BsFinding:
    """T15-02：全部工程肢解后分别发包（跨材料聚合为 1 条）。"""
    det = rule["scope_detection"]
    ek = det["evidence_keywords"]
    source = _rule_source(rule)

    general_scope = _page_match(general, ek["general_scope"])
    sub_with_scope = [m for m in subs if _material_has(m, ek["subcontract_scopes"])]
    anchor = _anchor_present(task, general + subs, det["section_anchors"])
    coverage = _composite_over(
        general + subs, det["dismember_scopes"],
        det["dismember_verbs"] + det["separate_award_verbs"],
        det["scope_verb_connectors"])
    retention = _page_match(general, det["retention_patterns"])
    partial = _head_tail_over(sub_with_scope, det["partial_scope_words"],
                              det["partial_scope_tails"])

    missing = []
    if general_scope is None:
        missing.append("general_scope")
    if len(sub_with_scope) < 2:
        missing.append("subcontract_scopes")
    if not anchor:
        missing.append("section_anchor")
    if coverage is None:
        missing.append("scope_coverage")

    if not missing:
        mat, page, quote, scope, verb = coverage
        return BsFinding(
            task_id=task.id, material_id=mat.id, item_id="T15-02",
            kind=_TRANSFER_KIND, status=STATUS_HIT,
            message="命中：合同出现「%s」肢解/分别发包明文（%s），且命中至少 2 份分包合同与标段锚点，"
                    "请人工复核是否属全部工程肢解后分别发包的线索" % (scope, verb),
            quote=quote, page=page, source=source)

    if coverage is None and retention is not None and partial is not None:
        # 明文由总包保留施工组织、分包仅承担部分范围，且无肢解/分别发包明文 → 未命中。
        mat, page, quote, word = retention
        return BsFinding(
            task_id=task.id, material_id=mat.id, item_id="T15-02",
            kind=_TRANSFER_KIND, status=STATUS_MISS,
            message="未命中：合同明文由承包人（总包）自行保留施工组织（保留模式「%s」），"
                    "分包仅承担部分范围，未见肢解或分别发包明文" % word,
            quote=quote, page=page, source=source)

    base = "不可判定：现有材料不足，无法判断全部工程是否被拆分后分别发包"
    if len(sub_with_scope) >= 2:
        base += "；仅有多份分包合同不足以证明肢解"
    base += _missing_suffix(missing)
    advice = []
    if "general_scope" in missing:
        advice.append("核对载明总承包范围的总包合同")
    if "subcontract_scopes" in missing:
        advice.append("如有其他分包合同，请一并提供以核对各自范围")
    if "section_anchor" in missing:
        advice.append("补充项目及合同段信息")
    if "scope_coverage" in missing:
        advice.append("核对工程范围及发包关系的相关资料")
    if advice:
        base += "；建议" + "；".join(advice)
    material_id = coverage[0].id if coverage is not None else None
    return _undecidable_finding(task, "T15-02", _TRANSFER_KIND, base, source, material_id,
                                missing=missing)


def run_transfer_scope_check(task, materials: list, *, scope_policy_enabled: bool = False,
                             general_source_state: str | None = None) -> list:
    """A1-RULE-01：第十五条（一）（二）合同范围核查纯内核。

    默认固定返回恰好 2 条 finding，顺序 T15-01 → T15-02；启用可选来源范围策略且总包
    不可用时，仅跳过依赖总包的规则。由 `service.run_a1_checks` 接线。

    材料筛选口径与既有内核一致：role ∈ {general, sub}、parse_state == "done"、文本非空。
    """
    if not _optional_general_source_readable(
            materials, scope_policy_enabled=scope_policy_enabled,
            general_source_state=general_source_state):
        return []
    general = [m for m in materials
               if m.role == "general" and m.parse_state == "done"
               and _material_text(m).strip()]
    subs = [m for m in materials
            if m.role == "sub" and m.parse_state == "done"
            and _material_text(m).strip()]
    r15_01 = _transfer_rule("T15-01")
    r15_02 = _transfer_rule("T15-02")
    findings = [_run_t15_01(task, general, subs, r15_01),
                _run_t15_02(task, general, subs, r15_02)]
    # 劳务合同中可定位的范围只作材料线索，不能直接等同于工程承包范围。
    # 仅一份合同有线索时提供页码，避免多主体或多合同间猜测配对。
    scope_words = [word for word in r15_01["scope_detection"]["evidence_keywords"]["sub_scope"]
                   if "范围" in word or "内容" in word]
    labor_clues = [next((clue for word in scope_words if (clue := _page_match([m], [word]))), None)
                   for m in materials if m.role == "labor" and m.parse_state == "done"]
    labor_clues = [clue for clue in labor_clues if clue is not None]
    if not subs and len(labor_clues) == 1:
        mat, page, quote, _ = labor_clues[0]
        for finding in findings:
            if finding.status != STATUS_UNDECIDABLE:
                continue
            finding.message = ("已定位劳务合同范围线索，需核对其与工程分包范围的关系；"
                               + finding.message.replace("分包范围", "可用于工程对比的分包范围"))[:500]
            if finding.material_id is None:
                finding.material_id, finding.page, finding.quote = mat.id, page, quote
            else:
                finding.evidence_json = [*(finding.evidence_json or []), {
                    "material_id": mat.id, "page": page, "quote": quote,
                    "location_mode": "page", "evidence_kind": "labor_scope_clue"}]
    # 劳务合同同样是本任务的合同材料；读取不完整时不能据此给出排除性结论。
    # 劳务作业范围是否等同于工程承包范围仍由既有规则和人工核实处理。
    gaps = [(m, material_reading_gap(m)) for m in materials
            if m.role in ("general", "sub", "labor")]
    gaps = [(m, pages) for m, pages in gaps if pages is not None]
    if gaps:
        detail = "；".join(
            f"{getattr(m, 'file_name', None) or '合同材料'}"
            + (f"第{','.join(map(str, pages[:8]))}页" if pages else "部分页面")
            for m, pages in gaps
        )
        for finding in findings:
            if finding.status == STATUS_MISS:
                finding.status = STATUS_UNDECIDABLE
                evidence = (finding.message or "").removeprefix("未命中：")
                finding.message = (f"不可判定：合同材料读取不完整（{detail}），请核对原件；"
                                   f"可读内容线索：{evidence}")[:500]
    return findings


# ==================== A1-03 第十五条（三）（四）采购租赁与现场/劳动关系核查内核（A1-RULE-02） ====================
# T15-03「采购租赁由其他单位或个人完成」/ T15-04「现场管理机构、劳动关系或项目负责人管理缺失」
# 纯规则内核。与 T15-01/T15-02 同 kind=transfer_scope（R05 一次登记四个条目）；本内核由
# service.run_a1_checks 接线（service.py:1628，R05 已落地）。采购/租赁责任明文模式、各证据侧
# 识别词、自购自租/他人采购模式、主体指向模式与社保统计期间正则全部来自 data/transfer_rules.json
# 的 materials_detection（规则=数据，改数据 → 重新核查 → 结论变化），本文件不硬编码词表。
# 冻结口径（A1-RULE-02 任务包 / 2026-10-08 冻结补充）：
# - T15-03 未命中=合同明文由承包人负责采购/租赁 且 全部采购/租赁证据主体均为承包人自身
#   （自购自租，与他人无涉）；T15-04 未命中=六侧证据齐全 且 现场/劳动/社保证据全部指向
#   承包人自身人员；
# - 证据不齐一律不可判定，不得落未命中（缺失项写进 message 的「缺：xxx」，结构化
#   missing_evidence 字段留给 R05 在 DTO/报告层统一落地，本包不用未命中掩盖材料缺失）；
# - 「不能合理解释并提供相应证明」不由规则层评价合理性：任何涉及解释是否合理的情形只能输出
#   不可判定 + 人工复核提示，不得因材料出现解释文本就判命中或未命中；
# - 社保材料「在场」看**证明材料形态**（缴纳证明/参保明细/缴费记录/名单/明细表头），
#   义务条款措辞（「社会保险由承包人缴纳」「工资支付」）只算提及；在场后还必须识别到
#   统计期间（年月区间确定性正则），识别不到 → 不可判定并列「缺：社保统计期间」；
# - 「合同明确由承包人负责采购/租赁」的责任明文只在总包件（role=general）检索；分包件/报审包
#   出现类似字样不得作责任归属证据（R01 归因错配教训）。

# A1-RULE-02：为 T15-03/T15-04 各证据侧补齐中文标签（沿用 _EVIDENCE_LABELS 机制，
# 「缺什么」文案逐字取自该映射；social_security_period 为任务包冻结的社保期间虚拟侧）。
_EVIDENCE_LABELS.update({
    "procurement_responsibility": "采购租赁责任明文",
    "procurement_or_lease_contract": "采购/租赁合同",
    "invoice_or_explanation": "发票或合理解释",
    "supplier_subject": "供应商主体",
    "site_management_org": "现场管理机构",
    "deployed_personnel": "派驻人员",
    "manager_roster": "主要管理人员名单",
    "labor_contract": "劳动合同",
    "social_security_or_wage_relation": "授权人员社保证明（缴纳记录/截图）",
    "project_manager_management": "项目负责人组织管理",
    "social_security_period": "社保统计期间",
    # 以下 5 个 key 在 data/transfer_rules.json 的 required_evidence 里出现，
    # 但此前**本表没有对应标签** ⇒ 一旦某条规则命中缺件分支，`_EVIDENCE_LABELS[k]`
    # 会 KeyError，或（走 .get 的路径）把英文 key 直接印到报告里。补齐以免日后踩。
    "illegal_subcontract_exclusion": "违法分包排除结论",
    "contractor_subject": "承包人主体",
    "employer_subject": "发包人主体",
    "subcontract_authority": "分包授权依据",
    "technical_quality_safety_equipment_guidance": "技术质量安全设备指导责任",
})


def _page_match_patterns(materials: list, patterns: list):
    """正则版 _page_match：在材料页文本层找第一个正则命中。

    模式来自 transfer_rules.json（规则=数据）。返回 (material, page, quote, matched) 或 None。
    """
    compiled = [re.compile(p) for p in patterns]
    for m in materials:
        for page_no, text in _norm_pages(m):
            for pat in compiled:
                mm = pat.search(text)
                if mm:
                    return (m, page_no,
                            text[max(0, mm.start() - 20):mm.end() + 20],
                            mm.group(0))
    return None


def _has_any_word(materials: list, words: list) -> bool:
    return any(_material_has(m, words) for m in materials)


def _materials_with(materials: list, words: list) -> list:
    """筛出文本中含任一识别词的材料（社保统计期间只在含社保证据的材料里检索）。"""
    return [m for m in materials if _material_has(m, words)]


def _run_t15_03(task, general: list, evidence: list, rule: dict) -> BsFinding:
    """T15-03：主要材料/构配件/设备/施工机械的采购租赁由其他单位或个人完成（跨材料聚合 1 条）。

    - 责任明文只在总包件（general）检索；分包件/报审包类似字样不作责任归属证据；
    - 命中=责任明文 + 四侧配套证据齐全 + 由其他单位/个人采购租赁的明示；
    - 未命中=责任明文 + 四侧齐全（发票在场，解释不算）+ 自购自租明示 + 无他人采购证据；
    - 「合理解释」不由机器裁定合理性：发票缺失仅解释在场时只能不可判定 + 人工复核提示。
    """
    det = rule["materials_detection"]
    source = _rule_source(rule)

    responsibility = _page_match(general, det["procurement_responsibility_patterns"])
    contract = _page_match(evidence, det["procurement_contract_words"])
    invoice = _page_match(evidence, det["invoice_words"])
    supplier = _page_match(evidence, det["supplier_subject_words"])
    third_party = _page_match_patterns(evidence, det["third_party_procurement_patterns"])
    self_proc = _page_match_patterns(evidence, det["self_procurement_patterns"])
    explanation = _has_any_word(evidence, det["explanation_words"])

    missing = []
    if responsibility is None:
        missing.append("procurement_responsibility")
    if contract is None:
        missing.append("procurement_or_lease_contract")
    if invoice is None and not explanation:
        # 「发票或合理解释」侧：解释在场仅满足「不缺侧」（不因此列缺项），但**不足以支撑
        # 命中/未命中**——发票缺失仅解释在场时落不可判定 + 人工复核提示（见下方分支）。
        missing.append("invoice_or_explanation")
    if supplier is None:
        missing.append("supplier_subject")

    if third_party is not None:
        if responsibility is not None and invoice is not None and not missing:
            # 命中必须发票在场：解释文本不能顶替发票（冻结口径：解释合理性不由机器裁定，
            # 仅解释在场时只能不可判定 + 人工复核提示，与 miss 分支同纪律）。
            mat, page, quote, word = third_party
            return BsFinding(
                task_id=task.id, material_id=mat.id, item_id="T15-03",
                kind=_TRANSFER_KIND, status=STATUS_HIT,
                message="命中：合同明文采购/租赁责任在承包人，同时证据显示采购租赁由其他单位/"
                        "个人完成（「%s」），请人工复核是否属第十五条（三）情形" % word,
                quote=quote, page=page, source=source)
        base = ("不可判定：出现由其他单位/个人采购租赁的证据线索，"
                "但责任归属或配套证据不齐，不能据此判命中")
        if explanation and invoice is None:
            base += "；材料含解释性说明，是否构成合理解释并提供相应证明需人工复核"
        if missing:
            base += _missing_suffix(missing)
        return _undecidable_finding(task, "T15-03", _TRANSFER_KIND, base, source,
                                    third_party[0].id, missing=missing)

    if (responsibility is not None and self_proc is not None
            and invoice is not None and not missing):
        # 未命中（自购自租）：责任明文 + 四侧齐全（发票在场）+ 自购自租明示 + 无他人采购证据。
        mat, page, quote, word = self_proc
        return BsFinding(
            task_id=task.id, material_id=mat.id, item_id="T15-03",
            kind=_TRANSFER_KIND, status=STATUS_MISS,
            message="未命中：合同明文由承包人负责采购/租赁，且采购/租赁证据主体均为承包人自身"
                    "（自购自租「%s」），未见由其他单位/个人采购租赁的证据" % word,
            quote=quote, page=page, source=source)

    base = ("不可判定：无法证明采购/租赁由其他单位或个人全部完成，"
            "也无法证明全部由承包人自身完成")
    if (responsibility is not None and contract is not None
            and invoice is not None and supplier is not None and self_proc is None):
        base += "；证据在场但未见采购/租赁主体指向的明示"
    if explanation and invoice is None:
        base += "；材料含解释性说明，是否构成合理解释并提供相应证明需人工复核"
    if missing:
        base += _missing_suffix(missing)
    anchor = self_proc or contract or invoice or supplier or responsibility
    material_id = anchor[0].id if anchor is not None else None
    return _undecidable_finding(task, "T15-03", _TRANSFER_KIND, base, source, material_id,
                                missing=missing)


def _run_t15_04(task, evidence: list, rule: dict) -> BsFinding:
    """T15-04：现场管理机构、派驻人员、主要管理人员、劳动合同、社保缴纳证明与项目负责人
    组织管理证据（第十五条（四），跨材料聚合 1 条）。

    - 未命中=六侧证据齐全且现场/劳动/社保证据全部指向承包人自身人员；
    - 命中=六侧齐全且指向其他单位；两侧指向冲突或无指向明示 → 不可判定；
    - 社保侧在场＝**证明材料形态**（缴纳证明/参保明细/缴费记录/名单/明细表头）；合同里的
      义务表述不算在场，缺件提示因此指向「缺社保证明」而非「缺统计期间」（BS-P1-02）；
    - 社保证明在场后必须识别到统计期间（年月区间确定性正则），识别不到 → 不可判定并列
      「缺：社保统计期间」（冻结口径，不用「出现期间关键词就算有」的宽松口径）。
    """
    det = rule["materials_detection"]
    source = _rule_source(rule)

    # 社保证明材料「在场」= 证明材料形态（缴纳证明/参保明细/缴费记录/名单/明细表头）。
    # 义务条款措辞（「社会保险由承包人缴纳」「工资支付」）只算**提及**，不构成材料在场：
    # 否则合同里的义务表述会被当成"社保材料已在场"，缺件提示指向「缺统计期间」而不是
    # 「缺社保证明」，用户会去补错东西（台账 BS-P1-02，2026-10-22 口径）。
    social_proof = _page_match_patterns(
        evidence, det.get("social_security_proof_patterns") or [])
    social_mention = _page_match(evidence, det["social_security_words"])

    side_hits = {
        "site_management_org": _page_match(evidence, det["site_management_org_words"]),
        "deployed_personnel": _page_match(evidence, det["deployed_personnel_words"]),
        "manager_roster": _page_match(evidence, det["manager_roster_words"]),
        "labor_contract": _page_match(evidence, det["labor_contract_words"]),
        "social_security_or_wage_relation": social_proof,
        "project_manager_management": _page_match(evidence, det["project_manager_words"]),
    }
    missing = [k for k in rule["required_evidence"] if side_hits[k] is None]

    # 社保统计期间：只有在社保证明材料在场时才有意义，且必须在含社保证据的材料里识别到年月区间。
    period_missing = False
    if social_proof is not None:
        social_materials = _materials_with(evidence, det["social_security_words"])
        period = _page_match_patterns(social_materials, [det["social_period_pattern"]])
        period_missing = period is None
    if period_missing:
        missing.append("social_security_period")

    if missing:
        base = "不可判定：现场/劳动关系证据不足，无法证明现场机构、劳动关系或项目负责人管理缺位"
        if "social_security_or_wage_relation" in missing:
            if social_mention is not None:
                base += ("；材料中仅见社保/工资相关表述（「%s」），未见社保缴纳证明、"
                         "参保人员明细或缴费记录" % social_mention[3])
            else:
                base += "；未见社保缴纳证明、参保人员明细或缴费记录"
        if period_missing:
            base += "；社保材料在场但未识别到统计期间（年月区间）"
        base += _missing_suffix(missing)
        return _undecidable_finding(task, "T15-04", _TRANSFER_KIND, base, source,
                                    missing=missing)

    third_dir = _page_match_patterns(evidence, det["third_party_direction_patterns"])
    self_dir = _page_match_patterns(evidence, det["self_direction_patterns"])
    if third_dir is not None and self_dir is not None:
        return _undecidable_finding(
            task, "T15-04", _TRANSFER_KIND,
            "不可判定：现场/劳动/社保证据同时出现指向承包人自身与其他单位的表述，主体指向冲突",
            source)
    if third_dir is not None:
        mat, page, quote, word = third_dir
        return BsFinding(
            task_id=task.id, material_id=mat.id, item_id="T15-04",
            kind=_TRANSFER_KIND, status=STATUS_HIT,
            message="命中：现场/劳动/社保等证据显示派驻、劳动关系或社保工资关系指向其他单位"
                    "（「%s」），请人工复核是否属第十五条（四）情形" % word,
            quote=quote, page=page, source=source)
    if self_dir is not None:
        mat, page, quote, word = self_dir
        return BsFinding(
            task_id=task.id, material_id=mat.id, item_id="T15-04",
            kind=_TRANSFER_KIND, status=STATUS_MISS,
            message="未命中：六侧证据齐全且现场/劳动/社保关系均指向承包人自身人员（「%s」）" % word,
            quote=quote, page=page, source=source)
    return _undecidable_finding(
        task, "T15-04", _TRANSFER_KIND,
        "不可判定：六侧证据在场但未见主体指向明示，无法确定现场/劳动/社保关系"
        "指向承包人自身还是其他单位（缺：主体指向明示）",
        source)


def run_transfer_materials_check(task, materials: list, *, scope_policy_enabled: bool = False,
                                 general_source_state: str | None = None) -> list:
    """A1-RULE-02：第十五条（三）（四）采购租赁与现场/劳动关系核查纯内核。

    默认固定返回恰好 2 条 finding，顺序 T15-03 → T15-04；启用可选来源范围策略时，
    缺少可用总包只跳过 T15-03，T15-04 仍按原有材料集合运行。
    （与 run_transfer_scope_check 同构）。kind 仍为 transfer_scope（与 T15-01/T15-02 同
    kind，R05 一次登记四个条目）。由 `service.run_a1_checks` 接线（service.py:1628）——
    金样里的 T15-03/T15-04 结论就是这条路径产出的。

    材料筛选口径：责任明文只在 role=general 检索；其余证据（采购/租赁合同、发票、供应商主体、
    现场机构、派驻人员、名册、劳动合同、社保工资）在 role ∈ {general, sub, labor, packet}、
    parse_state == "done" 且文本非空的材料中检索。
    """
    general = [m for m in materials
               if m.role == "general" and m.parse_state == "done"
               and _material_text(m).strip()]
    evidence = [m for m in materials
                if m.role in ("general", "sub", "labor", "packet")
                and m.parse_state == "done" and _material_text(m).strip()]
    r15_03 = _transfer_rule("T15-03")
    r15_04 = _transfer_rule("T15-04")
    findings = []
    if _optional_general_source_readable(
            materials, scope_policy_enabled=scope_policy_enabled,
            general_source_state=general_source_state):
        findings.append(_run_t15_03(task, general, evidence, r15_03))
    findings.append(_run_t15_04(task, evidence, r15_04))
    return findings


# ==================== A1-03 第十五条（五）-（九）主体关系与资金流核查内核（A1-RULE-03） ====================
# T15-05「劳务企业承包全部工程并计取全部工程价款」/ T15-06「合作联营个人承包等形式转移全部工程」/
# T15-07「分包发包单位不是承包人且不属于违法分包」（依赖占位）/ T15-08「工程款无收付关系或
# 扣费后全部转拨」/ T15-09「联合体一方不施工不管理并收取管理费」纯规则内核。与 T15-01..T15-04
# 同 kind=transfer_scope；本内核由 service.run_a1_checks 接线（service.py:1629，R05 已落地）。
# 词表/正则/依赖关系全部来自 data/transfer_rules.json 的 relations_detection / depends_on 字段
# （规则=数据，改数据 → 重新核查 → 结论变化），本文件不硬编码任何词表。
# 冻结口径（A1-RULE-03 任务包）：
# - T15-05：「全部工程」与「全部工程价款」是子串关系——范围侧词表必须尾排除（价款/款/费/量/
#   质量/进度/工期），「计取全部工程价款」类句式不得点亮范围侧；劳务/范围类明文只在总包件
#   （role=general）检索（同 T15-03 归因纪律），价款/管理费证据在证据池检索；
#   R03-HOTFIX H1：hit 的范围侧/价款侧必须是关系级联合模式（labor_whole_scope_patterns /
#   labor_payment_patterns）——同一语句内建立「劳务主体↔全部工程/价款」施受关系；裸主体词、
#   裸「收取全部工程价款」句式只作在场与缺项判定，不得单独支撑命中；
# - T15-06：合作/联营/个人承包关键词绝不能单独命中（仅关键词在场必须不可判定）；全部工程
#   转移=「全部工程」+转移动词邻接（禁裸词、禁「全部工程款」子串误亮）；
# - T15-07：依赖占位——需先取得 W16/V16（违法分包排除）核查结果方可判定，恒不可判定
#   （依赖关系记录在目录 depends_on 字段，尚未接入结论判定）；
# - T15-08：资金流统计期间用独立字段 fund_period_pattern（不与 T15-04 社保期间共用，
#   任务包冻结：统计期间独立记录）；R03-HOTFIX H2：期间只在与转拨/收付证据（pass_through/
#   payee 命中）同一材料的文本里检索，无关材料不得跨材料补期间，且含「其他项目」等排除
#   声明（period_exclusion_patterns）的分句不计；资金流材料明示表格不完整
#   （incomplete_table_patterns）→ 不可判定并列缺失声明，优先级在命中之前；
#   R03-HOTFIX H3：否定前缀（negation_prefixes）作用下的转拨命中作废，不作为肯定证据，
#   肯定与否定转拨同时在场 → 不可判定（指向冲突）；
#   缺期间/缺表/主体无法对应一律不可判定，不得用未命中替代；payment_relationship 侧语义=
#   收付维度说明在场（收款方/付款方），不是「存在正常收付」——否则「无收付关系」情形
#   永远到不了命中；
# - T15-09：联合体协议、分工、不施工不管理明示、管理费四类证据缺一不可（任务包原文）；
#   R03-HOTFIX H1：分句同主体验证（same_clause_required）——不施工、不管理、管理费三证
#   必须同分句（以 。；; 切分）齐备，跨分句不得拼接为同一主体；
# - 多证据 finding：主证据锚 material_id/page/quote，关联证据写进 message
#   （「关联证据：《文件名》第N页（侧标签）」）；每条 hit 至少 1 主 + 1 关联。

# A1-RULE-03：为 T15-05..T15-09 各证据侧补齐中文标签（沿用 _EVIDENCE_LABELS 机制，
# 「缺什么」文案逐字取自该映射；key 与目录 required_evidence 同名）。
_EVIDENCE_LABELS.update({
    "labor_scope": "劳务企业承包范围",
    "whole_project_scope": "全部工程范围明示",
    "management_fee": "管理费约定",
    "whole_project_payment": "全部工程价款计取",
    "cooperation_form": "合作/联营/个人承包形式",
    "actual_builder": "实际施工主体明示",
    "management_responsibility": "施工组织管理责任明示",
    "payment_relationship": "收付关系说明",
    "management_fee_deduction": "管理费扣除约定",
    "pass_through_payment": "扣费转拨或无收付明示",
    "fund_flow_statement": "资金流表",
    "coverage_period": "资金流统计期间",
    "joint_venture_agreement": "联合体协议",
    "joint_work_division": "联合体分工",
    "actual_construction": "实际施工归属明示",
    "actual_management": "实际管理归属明示",
})


def _related_refs(side_matches, main):
    """hit finding 的关联证据引用（主证据之外的其余在场侧），供 message 拼接。

    side_matches 为 [(侧标签, 侧命中) ...]；main 为主证据命中（同对象引用，跳过）。
    返回 "；关联证据：《文件名》第N页（标签）..." 或空串。
    """
    refs = []
    for label, match in side_matches:
        if match is None or match is main:
            continue
        mat, page, _quote, _word = match
        refs.append("关联证据：《%s》第%d页（%s）" % (mat.file_name, page, label))
    return ("；" + "；".join(refs)) if refs else ""


def _materials_matching(materials: list, patterns: list) -> list:
    """正则版 _materials_with：筛出文本任一页命中任一模式的材料。"""
    compiled = [re.compile(p) for p in patterns]
    return [m for m in materials
            if any(pat.search(text) for _, text in _norm_pages(m)
                   for pat in compiled)]


def _collect_pattern_matches(materials: list, patterns: list) -> list:
    """收集全部正则命中词（去重保序），用于缺失声明罗列。"""
    compiled = [re.compile(p) for p in patterns]
    seen, words = set(), []
    for m in materials:
        for _, text in _norm_pages(m):
            for pat in compiled:
                for mm in pat.finditer(text):
                    if mm.group(0) not in seen:
                        seen.add(mm.group(0))
                        words.append(mm.group(0))
    return words


def _page_match_patterns_excluding(materials: list, patterns: list,
                                   exclusion_patterns: list):
    """分句版正则检索：以 。；; 切分分句，跳过含排除模式的分句后检索首个命中。

    返回 (material, page, 分句文本, matched) 或 None。用于资金流统计期间的纵深防御：
    「其他项目统计期间…」分句不得为当前资金表补期间（R03-HOTFIX H2）。
    """
    compiled = [re.compile(p) for p in patterns]
    excl = [re.compile(p) for p in exclusion_patterns]
    for m in materials:
        for page_no, text in _norm_pages(m):
            for clause in re.split(r"[。；;]", text):
                if any(p.search(clause) for p in excl):
                    continue
                for pat in compiled:
                    mm = pat.search(clause)
                    if mm:
                        return (m, page_no, clause, mm.group(0))
    return None


def _pass_through_scan(materials: list, patterns: list, negation_prefixes: list):
    """转拨类正则扫描 + 否定极性作废（R03-HOTFIX H3）。

    对每个正则命中检查**命中起点前 2 字窗口**内是否出现否定前缀（否定词长度不定，
    lookbehind 不可行，用命中位置前缀检查）；被作废命中记录其跨度，凡完整落在已作废
    跨度内的子串命中一并作废——「未扣除管理费后全额转付」中的「全额转付」子串不得
    逃逸为肯定证据。返回 (positive, negated)：positive 为首个肯定命中
    (material, page, quote, word) 或 None；negated 为全部作废命中列表（同形）。
    """
    compiled = [re.compile(p) for p in patterns]
    found = []  # (material, page, start, end, quote, word, is_negated)
    for m in materials:
        for page_no, text in _norm_pages(m):
            for pat in compiled:
                for mm in pat.finditer(text):
                    window = text[max(0, mm.start() - 2):mm.start()]
                    found.append((m, page_no, mm.start(), mm.end(),
                                  text[max(0, mm.start() - 20):mm.end() + 20],
                                  mm.group(0),
                                  any(pfx in window for pfx in negation_prefixes)))
    dead_spans = [(s, e) for (_m, _p, s, e, _q, _w, neg) in found if neg]
    positive = None
    negated = []
    for (m, page_no, s, e, quote, word, is_neg) in found:
        if is_neg or any(ds <= s and e <= de for ds, de in dead_spans):
            negated.append((m, page_no, quote, word))
        elif positive is None:
            positive = (m, page_no, quote, word)
    return positive, negated


def _same_clause_triple(materials: list, det: dict):
    """T15-09 分句同主体验证（R03-HOTFIX H1）：同一分句内「不施工 + 不管理 + 管理费」
    三证齐，才近似为同一主语的「不施工、不管理并收取管理费」；跨分句不得拼接。

    分句 = 以 。；; 切分。返回 (material, page, 分句文本, word) 或 None。
    """
    nc_res = [re.compile(p) for p in det["no_construction_patterns"]]
    nm_res = [re.compile(p) for p in det["no_management_patterns"]]
    fee_res = [re.compile(p) for p in det.get("fee_only_patterns", [])]
    fee_words = det.get("management_fee_words", [])
    for m in materials:
        for page_no, text in _norm_pages(m):
            for clause in re.split(r"[。；;]", text):
                if not any(p.search(clause) for p in nc_res):
                    continue
                if not any(p.search(clause) for p in nm_res):
                    continue
                word = None
                for p in fee_res:
                    mm = p.search(clause)
                    if mm:
                        word = mm.group(0)
                        break
                if word is None:
                    word = next((w for w in fee_words if w in clause), None)
                if word is not None:
                    return (m, page_no, clause, word)
    return None


def _run_t15_05(task, general: list, evidence: list, rule: dict) -> BsFinding:
    """T15-05：劳务企业承包全部工程并计取全部工程价款（跨材料聚合 1 条）。

    - 劳务/范围类明文只在总包件（general）检索；价款/管理费证据在证据池检索；
    - 侧在场（labor_scope/whole_scope/fee/payment）只作缺项判定：裸 labor_subject_words、
      裸「收取全部工程价款」等关键词各自为政不能证明业务关系（R03-HOTFIX H1）；
    - 命中=四侧齐 + 关系级联合模式双亮：labor_whole_scope_patterns（同一语句内劳务主体
      承包/负责/分包全部工程，或全部工程发包/交由劳务主体）+ labor_payment_patterns
      （同一语句内劳务主体计取/收取/结算全部工程价款，或全部工程价款由劳务主体计取）；
    - 四侧齐但关系模式不成立 → 不可判定（施受关系未建立）；
    - 未命中=范围否定明示在场 + 无全部工程范围明示 + 劳务/价款语境在场；
    - 范围明示与范围否定同时在场 → 不可判定（指向冲突）；其余按缺失侧列明。
    """
    det = rule["relations_detection"]
    source = _rule_source(rule)

    labor_scope = (_page_match(general, det["labor_subject_words"])
                   or _page_match_patterns(general, det["labor_scope_patterns"]))
    # 范围侧词表自带尾排除（价/款/费/量/质量/进度/工期）：「计取全部工程价款」等
    # 价款句式不得点亮范围侧（子串回归纪律，见任务包）。
    whole_scope = _page_match_patterns(general, det["whole_scope_patterns"])
    fee = _page_match(evidence, det["management_fee_words"])
    payment = _page_match_patterns(evidence, det["whole_payment_patterns"])
    negation = _page_match_patterns(general, det["whole_scope_negation_patterns"])
    # R03-HOTFIX H1：关系级联合模式——hit 必须由同一语句内的劳务主体↔全部工程/价款
    # 施受关系点亮；裸词命中只进上面的侧在场与缺项判定。
    labor_whole_scope = _page_match_patterns(
        general, det.get("labor_whole_scope_patterns", []))
    labor_payment = _page_match_patterns(
        evidence, det.get("labor_payment_patterns", []))

    sides = {"labor_scope": labor_scope, "whole_project_scope": whole_scope,
             "management_fee": fee, "whole_project_payment": payment}
    missing = [k for k in rule["required_evidence"] if sides[k] is None]

    if whole_scope is not None and negation is not None:
        return _undecidable_finding(
            task, "T15-05", _TRANSFER_KIND,
            "不可判定：总包件同时出现全部工程范围明示与范围否定表述，范围证据指向冲突，"
            "不能据此判命中或未命中",
            source, whole_scope[0].id)

    if not missing:
        if labor_whole_scope is None or labor_payment is None:
            anchor = labor_scope or whole_scope or fee or payment
            return _undecidable_finding(
                task, "T15-05", _TRANSFER_KIND,
                "不可判定：劳务主体、全部工程范围、价款与管理费关键词虽在场，但未能在"
                "同一语句建立「劳务主体承包全部工程」「劳务主体计取全部工程价款」的"
                "施受关系，不能证明同一劳务企业承包全部工程并计取全部工程价款",
                source, anchor[0].id if anchor is not None else None)
        main = labor_payment or labor_whole_scope
        mat, page, quote, word = main
        related = [("劳务主体", labor_scope), ("全部工程范围明示", whole_scope),
                   ("管理费约定", fee), ("全部工程价款计取", payment)]
        return BsFinding(
            task_id=task.id, material_id=mat.id, item_id="T15-05",
            kind=_TRANSFER_KIND, status=STATUS_HIT,
            message=("命中：同一语句明示劳务主体承包全部工程并计取全部工程价款，证据同时"
                     "显示管理费约定（「%s」），请人工复核是否属第十五条（五）情形%s"
                     % (word, _related_refs(related, main))),
            quote=quote, page=page, source=source)

    labor_context = (_has_any_word(evidence, det["labor_subject_words"])
                     or fee is not None or payment is not None)
    if negation is not None and whole_scope is None and labor_context:
        mat, page, quote, word = negation
        return BsFinding(
            task_id=task.id, material_id=mat.id, item_id="T15-05",
            kind=_TRANSFER_KIND, status=STATUS_MISS,
            message=("未命中：材料明示劳务方不承包全部工程、仅承担部分劳务作业（「%s」），"
                     "未见全部工程范围明示" % word),
            quote=quote, page=page, source=source)

    base = "不可判定：无法证明劳务企业承包全部工程并计取全部工程价款"
    if negation is not None:
        base += "；范围否定表述在场但劳务/价款语境不足"
    base += _missing_suffix(missing)
    anchor = labor_scope or whole_scope or fee or payment or negation
    material_id = anchor[0].id if anchor is not None else None
    return _undecidable_finding(task, "T15-05", _TRANSFER_KIND, base, source, material_id,
                                missing=missing)


def _run_t15_06(task, evidence: list, rule: dict) -> BsFinding:
    """T15-06：合作联营个人承包等形式转移全部工程（跨材料聚合 1 条）。

    - 合作/联营/个人承包关键词绝不能单独命中：仅关键词在场必须不可判定；
    - 命中=合作形式 + 全部工程转移明文（「全部工程」+转移动词邻接，禁裸词/子串误亮）
      + 实际施工主体明示 + 施工组织管理责任明示，四侧齐且同向；
    - 未命中=合作形式在场 + 转移否定明示 + 无转移/实际施工明示；
    - 转移明文与转移否定同时在场 → 不可判定（指向冲突）。
    """
    det = rule["relations_detection"]
    source = _rule_source(rule)

    coop = _page_match(evidence, det["cooperation_form_words"])
    transfer = _page_match_patterns(evidence, det["whole_transfer_patterns"])
    builder = _page_match_patterns(evidence, det["actual_builder_patterns"])
    mgmt = _page_match(evidence, det["management_responsibility_words"])
    negation = _page_match_patterns(evidence, det["transfer_negation_patterns"])

    sides = {"cooperation_form": coop, "actual_builder": builder,
             "whole_project_transfer": transfer, "management_responsibility": mgmt}
    missing = [k for k in rule["required_evidence"] if sides[k] is None]

    if transfer is not None and negation is not None:
        return _undecidable_finding(
            task, "T15-06", _TRANSFER_KIND,
            "不可判定：材料同时出现全部工程转移明文与转移否定表述，转移证据指向冲突，"
            "不能据此判命中或未命中",
            source, transfer[0].id)

    if not missing:
        mat, page, quote, word = transfer
        related = [("合作/联营/个人承包形式", coop), ("实际施工主体", builder),
                   ("施工组织管理责任", mgmt), ("全部工程交付关系", transfer)]
        return BsFinding(
            task_id=task.id, material_id=mat.id, item_id="T15-06",
            kind=_TRANSFER_KIND, status=STATUS_HIT,
            message=("命中：证据显示以合作/联营/个人承包形式将全部工程转由他人施工"
                     "（「%s」），请人工复核是否属第十五条（六）情形%s"
                     % (word, _related_refs(related, transfer))),
            quote=quote, page=page, source=source)

    if coop is not None and negation is not None \
            and transfer is None and builder is None:
        mat, page, quote, word = negation
        return BsFinding(
            task_id=task.id, material_id=mat.id, item_id="T15-06",
            kind=_TRANSFER_KIND, status=STATUS_MISS,
            message=("未命中：材料显示存在合作/联营形式，但明文不转移全部工程、各方各自"
                     "承担各自范围（「%s」），未见全部工程转移或实际施工明示" % word),
            quote=quote, page=page, source=source)

    base = "不可判定：合作/联营/个人承包关键词不能单独作为全部工程转移的判定依据"
    if coop is not None:
        base += "；合作/联营字样在场但实际施工主体或施工组织管理责任等配套证据不齐"
    base += _missing_suffix(missing)
    anchor = transfer or builder or coop or mgmt or negation
    material_id = anchor[0].id if anchor is not None else None
    return _undecidable_finding(task, "T15-06", _TRANSFER_KIND, base, source, material_id,
                                missing=missing)


def _run_t15_07(task, rule: dict) -> BsFinding:
    """T15-07：分包发包单位不是承包人且不属于违法分包（依赖占位，恒不可判定）。

    判定前提是先取得 W16/V16（违法分包排除）核查结果；依赖不足时只能不可判定，
    不得静默判通过或未命中。依赖关系记录在目录 depends_on 字段（规则=数据）；
    当前尚未实现依赖结论与主体关系的联合判断。
    """
    return _undecidable_finding(
        task, "T15-07", _TRANSFER_KIND,
        "不可判定：系统尚不能自动完成分包发包单位与承包人关系及违法分包排除核验，"
        "请人工核对相关主体及材料；（缺：违法分包排除结论）",
        _rule_source(rule), missing=["illegal_subcontract_exclusion"])


def _run_t15_08(task, evidence: list, rule: dict) -> BsFinding:
    """T15-08：工程款无收付关系或扣费后全部转拨（跨材料聚合 1 条）。

    - 资金流统计期间独立识别（fund_period_pattern，不与 T15-04 社保期间共用）；
      R03-HOTFIX H2：期间只在与转拨/收付证据（pass_through/payee 命中）**同一材料**
      的文本里检索，无关材料不得跨材料补期间；含「其他项目」等排除声明
      （period_exclusion_patterns）的分句不计（纵深防御）；缺期间 → 不可判定并列
      「缺：资金流统计期间」，不得用未命中替代；
    - 主体可对应（确定性）：资金流表材料须同时含承包人侧与分包侧主体字样，
      任一缺失 → 不可判定「主体无法对应」；
    - R03-HOTFIX H2：资金流材料明示表格不完整（incomplete_table_patterns）→ 不可判定
      并列识别到的缺失声明，优先级在命中之前；
    - R03-HOTFIX H3 极性纪律：否定前缀（negation_prefixes）作用下的转拨命中作废，
      不作为肯定证据；肯定与否定的转拨表述同时在场 → 不可判定（指向冲突）；
      仅否定转拨在场且无分包收款明示 → 不可判定（转拨极性不明）；
    - payment_relationship 侧=收付维度说明在场（收款方/付款方），不是「存在正常收付」；
    - 命中=资金流表在场 + 期间齐 + 主体可对应 + 扣费转拨/无收付明示（肯定极性）；
    - 未命中=前三者齐 + 分包方收款记录明示 + 无转拨/无收付明示；
    - 未上传资金流表属证据缺口（任务仍可运行），缺表时不可判定并列明缺项。
    """
    det = rule["relations_detection"]
    source = _rule_source(rule)

    flow = _page_match(evidence, det["fund_flow_words"])
    pay_rel = _page_match(evidence, det["payment_relationship_words"])
    pass_through, pass_through_negated = _pass_through_scan(
        evidence, det["pass_through_patterns"], det.get("negation_prefixes", []))
    deduction = _page_match(evidence, det["deduction_words"])
    payee = _page_match_patterns(evidence, det["payee_patterns"])

    period = None
    subject_ok = False
    flow_materials = []
    if flow is not None:
        # 统计期间只在与转拨/收付证据同一材料的文本里检索（H2：无关材料不得补齐期间），
        # 含排除声明（其他项目等）的分句不计（纵深防御，字段仍独立于 T15-04 社保期间）。
        fund_evidence_materials = _materials_matching(
            evidence, det["pass_through_patterns"] + det["payee_patterns"])
        period = _page_match_patterns_excluding(
            fund_evidence_materials, [det["fund_period_pattern"]],
            det.get("period_exclusion_patterns", []))
        subject_ok = (_material_has(flow[0], det["contractor_subject_words"])
                      and _material_has(flow[0], det["subcontractor_subject_words"]))
        flow_materials = _materials_with(evidence, det["fund_flow_words"])

    if flow is None:
        side_map = {"fund_flow_statement": flow, "payment_relationship": pay_rel,
                    "pass_through_payment": pass_through,
                    "management_fee_deduction": deduction}
        missing = [k for k in side_map if side_map[k] is None]
        return _undecidable_finding(
            task, "T15-08", _TRANSFER_KIND,
            "不可判定：未见资金流/收付明细类材料，无法核验工程款收付与转拨"
            "（资金流/发票流缺失属证据缺口，任务仍可运行）" + _missing_suffix(missing),
            source, missing=missing)

    if period is None:
        return _undecidable_finding(
            task, "T15-08", _TRANSFER_KIND,
            "不可判定：资金流材料在场但未识别到统计期间（年月区间），期间缺失时不得用"
            "未命中替代（缺：资金流统计期间）",
            source, flow[0].id, missing=["coverage_period"])
    if not subject_ok:
        return _undecidable_finding(
            task, "T15-08", _TRANSFER_KIND,
            "不可判定：资金流材料主体无法对应（缺：承包人/总包侧或分包侧主体字样），"
            "收付关系无法对应到本任务主体",
            source, flow[0].id)

    # R03-HOTFIX H2：明示表格不完整 → 不可判定（优先级在命中之前），并列识别到的缺失声明。
    incomplete = _collect_pattern_matches(
        flow_materials, det.get("incomplete_table_patterns", []))
    if incomplete:
        return _undecidable_finding(
            task, "T15-08", _TRANSFER_KIND,
            "不可判定：资金流材料明示表格不完整（识别到缺失声明：%s），材料不完整时"
            "不能证明收付与转拨关系，需补齐完整资金往来明细后人工复核"
            % "、".join("「%s」" % w for w in incomplete),
            source, flow[0].id)

    # R03-HOTFIX H3：肯定与否定的转拨表述同时在场 → 不可判定（指向冲突）。
    if pass_through is not None and pass_through_negated:
        return _undecidable_finding(
            task, "T15-08", _TRANSFER_KIND,
            "不可判定：资金流材料同时出现肯定与否定的转拨表述（肯定「%s」、否定「%s」），"
            "转拨证据指向冲突，不能据此判命中或未命中"
            % (pass_through[3], pass_through_negated[0][3]),
            source, pass_through[0].id)

    if pass_through is not None:
        main = pass_through
        mat, page, quote, word = main
        related = [("资金流表", flow), ("资金流统计期间", period),
                   ("收付关系说明", pay_rel), ("管理费扣除约定", deduction)]
        return BsFinding(
            task_id=task.id, material_id=mat.id, item_id="T15-08",
            kind=_TRANSFER_KIND, status=STATUS_HIT,
            message=("命中：资金流材料显示扣费后转拨或无收付关系情形（「%s」），"
                     "请人工复核是否属第十五条（八）情形%s"
                     % (word, _related_refs(related, main))),
            quote=quote, page=page, source=source)

    if payee is not None:
        mat, page, quote, word = payee
        return BsFinding(
            task_id=task.id, material_id=mat.id, item_id="T15-08",
            kind=_TRANSFER_KIND, status=STATUS_MISS,
            message=("未命中：资金流材料显示分包方实际收款（「%s」），未见扣费转拨或"
                     "无收付关系明示" % word),
            quote=quote, page=page, source=source)

    if pass_through_negated:
        # 否定转拨作废后无肯定证据、也无分包收款明示 → 极性不明，不可判定。
        return _undecidable_finding(
            task, "T15-08", _TRANSFER_KIND,
            "不可判定：资金流材料存在否定性转拨表述（「%s」），转拨极性不明，且未见"
            "分包方收款明示，不能判命中或未命中" % pass_through_negated[0][3],
            source, pass_through_negated[0][0].id)

    missing = []
    if pay_rel is None:
        missing.append("payment_relationship")
    if pass_through is None:
        missing.append("pass_through_payment")
    if deduction is None:
        missing.append("management_fee_deduction")
    base = ("不可判定：资金流表在场、期间与主体可对应，但未见扣费转拨、无收付关系或"
            "分包方收款明示")
    base += _missing_suffix(missing)
    return _undecidable_finding(task, "T15-08", _TRANSFER_KIND, base, source, flow[0].id,
                                missing=missing)


def _run_t15_09(task, evidence: list, rule: dict) -> BsFinding:
    """T15-09：联合体一方不施工不管理并收取管理费（跨材料聚合 1 条）。

    - 命中核心明示：某一方「不承担/不进行施工」「不参与/不负责管理」并收取管理费；
    - R03-HOTFIX H1 分句同主体验证（same_clause_required）：不施工、不管理、管理费
      三证必须出现在**同一分句**（以 。；; 切分）内，跨分句不得拼接为同一主体；
    - 命中=联合体协议 + 分工 + 同一分句三证齐（四类证据缺一不可）；
    - 各侧在场但无任一分句三证齐 → 不可判定（同主体归属不明）；
    - 未命中=协议 + 分工在场 + 各方实际承担施工/管理明示 + 无「仅收取管理费」明示；
    - 不施工/不管理与各方实际承担施工同时在场 → 不可判定（指向冲突）。
    """
    det = rule["relations_detection"]
    source = _rule_source(rule)

    agreement = _page_match(evidence, det["agreement_words"])
    division = _page_match(evidence, det["division_words"])
    no_construction = _page_match_patterns(evidence, det["no_construction_patterns"])
    no_management = _page_match_patterns(evidence, det["no_management_patterns"])
    fee = _page_match(evidence, det["management_fee_words"])
    fee_only = _page_match_patterns(evidence, det["fee_only_patterns"])
    actual_work = _page_match_patterns(evidence, det["actual_work_patterns"])
    # R03-HOTFIX H1：分句同主体验证——三证同分句才算同一主体的明示。
    triple = (_same_clause_triple(evidence, det)
              if det.get("same_clause_required") else None)

    sides = {"joint_venture_agreement": agreement, "joint_work_division": division,
             "actual_construction": no_construction, "actual_management": no_management,
             "management_fee": fee}
    missing = [k for k in rule["required_evidence"] if sides[k] is None]

    if (no_construction is not None or no_management is not None) \
            and actual_work is not None:
        anchor = no_construction or no_management
        return _undecidable_finding(
            task, "T15-09", _TRANSFER_KIND,
            "不可判定：材料同时出现一方不施工/不管理与各方实际承担施工/管理的表述，"
            "施工管理归属证据指向冲突",
            source, anchor[0].id)

    if agreement is not None and division is not None and triple is not None:
        mat, page, quote, word = triple
        related = [("联合体协议", agreement), ("联合体分工", division),
                   ("不参与施工明示", no_construction), ("不参与管理明示", no_management),
                   ("管理费约定", fee)]
        return BsFinding(
            task_id=task.id, material_id=mat.id, item_id="T15-09",
            kind=_TRANSFER_KIND, status=STATUS_HIT,
            message=("命中：联合体协议与分工在场，且同一分句内明示一方不施工、不参与"
                     "管理并涉及管理费（「%s」），请人工复核是否属第十五条（九）情形%s"
                     % (word, _related_refs(related, triple))),
            quote=quote, page=page, source=source)

    if not missing and triple is None:
        anchor = no_construction or no_management or fee or agreement or division
        return _undecidable_finding(
            task, "T15-09", _TRANSFER_KIND,
            "不可判定：不施工、不管理与收取管理费未见于同一分句（跨分句不得拼接为"
            "同一主体），无法证明同一联合体方不施工且不管理并收取管理费",
            source, anchor[0].id if anchor is not None else None)

    if agreement is not None and division is not None \
            and actual_work is not None and fee_only is None \
            and no_construction is None and no_management is None:
        mat, page, quote, word = actual_work
        return BsFinding(
            task_id=task.id, material_id=mat.id, item_id="T15-09",
            kind=_TRANSFER_KIND, status=STATUS_MISS,
            message=("未命中：联合体各方明文实际承担施工与管理（「%s」），未见"
                     "仅收取管理费明示" % word),
            quote=quote, page=page, source=source)

    base = "不可判定：联合体情形四类证据（协议、分工、不施工不管理明示、管理费）缺一不可"
    base += _missing_suffix(missing)
    anchor = fee_only or no_construction or no_management or agreement or division or fee
    material_id = anchor[0].id if anchor is not None else None
    return _undecidable_finding(task, "T15-09", _TRANSFER_KIND, base, source, material_id,
                                missing=missing)


def run_transfer_relations_check(task, materials: list, *, scope_policy_enabled: bool = False,
                                 general_source_state: str | None = None) -> list:
    """A1-RULE-03：第十五条（五）-（九）主体关系与资金流核查纯内核。

    默认固定返回恰好 5 条 finding，顺序 T15-05 → T15-06 → T15-07 → T15-08 → T15-09；
    启用可选来源范围策略时，缺少可用总包只跳过 T15-05；
    每条规则跨材料聚合为 1 条，kind 均为 transfer_scope（与 T15-01..T15-04 同 kind）。
    由 `service.run_a1_checks` 接线（service.py:1629，R05 已落地）。

    材料筛选口径：T15-05 范围/责任类明文只在 role=general 检索（同 T15-03 归因纪律），
    价款/管理费证据在 role ∈ {general, sub, labor, packet} 证据池检索；T15-06/08/09
    全部侧在证据池检索；parse_state == "done" 且文本非空。
    """
    general = [m for m in materials
               if m.role == "general" and m.parse_state == "done"
               and _material_text(m).strip()]
    evidence = [m for m in materials
                if m.role in ("general", "sub", "labor", "packet")
                and m.parse_state == "done" and _material_text(m).strip()]
    findings = []
    if _optional_general_source_readable(
            materials, scope_policy_enabled=scope_policy_enabled,
            general_source_state=general_source_state):
        findings.append(_run_t15_05(task, general, evidence, _transfer_rule("T15-05")))
    findings.extend([
        _run_t15_06(task, evidence, _transfer_rule("T15-06")),
        _run_t15_07(task, _transfer_rule("T15-07")),
        _run_t15_08(task, evidence, _transfer_rule("T15-08")),
        _run_t15_09(task, evidence, _transfer_rule("T15-09")),
    ])
    return findings


# ==================== 第十六条/十七条 违法分包与劳务名义分包核查内核（A1-RULE-04） ====================
# W16-01..07 / V16-01..04 / L17-01..03 纯规则内核。独立 kind=illegal_subcontract；
# 本内核由 service.run_a1_checks 接线（service.py:1630，R05 已落地）。
# 词表/正则全部来自 data/transfer_rules.json 的 illegal_detection 字段（规则=数据，
# 改数据 → 重新核查 → 结论变化）；开放条款由目录 open_term 字段驱动，本文件不硬编码
# 任何开放条款 ID 清单。
# 冻结口径（A1-RULE-04 任务包 / 记账红线）：
# - 旧依赖检查段（_check_v16_01/_check_l17_02/run_a3_dependent_checks，L358-473）逐字
#   不动、旧入口保持旧行为；本节 V16-01 采用「监理审查/发包人书面同意两侧独立、缺任一侧
#   不可判定」新口径（比旧「或逻辑从严」更保守）；
# - a3_findings/negative_list_findings 为已计算结论**显式传入**，不调数据库、不重复执行
#   A3/负面清单；qualification_evidence 为可选 dict（键 business_license/qualification/
#   safety_permit，任意真值=合格在场）；
# - 否定句内子串不作肯定证据（R03 dead_spans 同型纪律）；双重否定机器不裁 → 不可判定；
# - W16-04 禁「挂靠」结论：message 只用「以他人名义承揽线索」语气（禁词由测试扫描把关）；
# - L17-01 禁裸词「主要材料费」：命中必须由施受邻接关系模式点亮，百分比计费条款
#   （管理费按主要材料费的X%计取）不构成计取材料款证据；
# - L17-02 责任方向判定语义与旧 _L17_HIT_RES/_L17_MISS_RES/_L17_LABOR_SIGN_RE 一致，
#   但正则迁移进 json illegal_detection，并修复跨行拼接缺陷（换行先归一为句界）；
# - W16-05×L17 重叠去重走 illegal_subcontract_relations 纯函数（可测试中间结构，
#   不写数据库、不加模型字段；R05 负责映射持久化/接口/报告去重）；
# - hit 一律线索语气 + 「请人工复核」；缺证据 → 不可判定列缺项，禁静默判通过。

_ILLEGAL_KIND = "illegal_subcontract"

# A1-RULE-04：为 14 条规则各证据侧补齐中文标签（沿用 _EVIDENCE_LABELS 机制，
# 「缺什么」文案逐字取自该映射；key 与目录 required_evidence 同名）。
_EVIDENCE_LABELS.update({
    "subcontractor_subject": "分包主体",
    "qualification": "资质",
    "license": "证照",
    "safety_permit": "安全生产许可证",
    "required_condition": "相应条件",
    "subcontract_scope": "分包范围",
    "negative_list_match": "负面清单核查结论",
    "threshold_values": "负面清单阈值数值",
    "no_subcontract_clause": "不得分包条款",
    "employer_written_consent": "发包人书面同意",
    "consent_date": "书面同意日期",
    "subcontractor_identity": "分包人身份",
    "named_subject": "名义承揽明示",
    "contract_and_material_subjects": "合同与实施主体对照",
    "construction_scope": "施工范围明示",
    "labor_payment_terms": "劳务计费条款",
    "l17_related_evidence": "第十七条关联证据",
    "contract_chain": "合同链条（再分包明示）",
    "downstream_subject": "下游主体",
    "actual_implementation": "实际实施证据",
    "supervision_review": "监理审查",
    "general_contract": "总包合同",
    "subcontract": "分包合同",
    "principle_comparison": "合同原则对照",
    "equipment_facility_explanation": "设备设施说明",
    "management_evidence": "管理证据",
    "labor_fee": "劳务费用约定",
    "main_material_fee": "主要材料费计取",
    "main_turnover_material_fee": "周转材料费计取",
    "construction_plan_owner": "方案编制主体",
    "testing_owner": "试验检测主体",
    "survey_owner": "测量主体",
    "archive_owner": "档案主体",
    "quality_safety_owner": "质量安全主体",
    "additional_rule_text": "法律法规其他情形明文",
    "deterministic_criterion": "确定性认定标准",
})


def _illegal_rules() -> list:
    """第十六条/十七条 14 条目录条目：article 字段驱动（16/17），顺序=目录顺序。

    不按 ID 清单筛选；开放条款（W16-07/V16-04/L17-03）由条目 open_term 字段驱动。
    """
    return [r for r in load_transfer_rules()["rules"]
            if r.get("article") in ("16", "17")]


def _compile_res(patterns: list) -> list:
    return [re.compile(p) for p in patterns]


def _norm_pages_breaks(material) -> list:
    """换行视作句界的页归一化（A1-RULE-04 反跨行拼接纪律）。

    先把 \r\n 归一为「。」再去除其余空白，使跨行文本保留句界标记——同句/关系级
    模式在分句匹配时不会把上一行行尾与下一行行首拼成一句（L17-02 已知缺陷
    「跨行拼接误命中合规样本」的同型预防，手法落此；见 json line_break_as_clause）。
    """
    out = []
    for p in _readable_pages(material):
        text = re.sub(r"[\r\n]+", "。", p.get("text", ""))
        out.append((p.get("page"), _WS_RE.sub("", text)))
    return out


def _page_match_breaks(materials: list, patterns: list):
    """正则检索（换行=句界归一化）。返回 (material, page, quote, matched) 或 None。"""
    compiled = _compile_res(patterns)
    for m in materials:
        for page_no, text in _norm_pages_breaks(m):
            for pat in compiled:
                mm = pat.search(text)
                if mm:
                    return (m, page_no,
                            text[max(0, mm.start() - 20):mm.end() + 20],
                            mm.group(0))
    return None


def _clause_match_breaks(materials: list, patterns: list,
                         exclusion_patterns: list = None):
    """分句级正则检索（换行=句界；跳过含排除模式的分句，如百分比条款）。

    返回 (material, page, 分句文本, matched) 或 None。
    """
    compiled = _compile_res(patterns)
    excl = _compile_res(exclusion_patterns or [])
    for m in materials:
        for page_no, text in _norm_pages_breaks(m):
            for clause in re.split(r"[。；;]", text):
                if not clause or any(p.search(clause) for p in excl):
                    continue
                for pat in compiled:
                    mm = pat.search(clause)
                    if mm:
                        return (m, page_no, clause, mm.group(0))
    return None


def _same_clause_groups(materials: list, groups: list,
                        exclusion_patterns: list = None):
    """同一分句内每组模式各至少一个命中（换行=句界，跨行不得拼接为同一句）。

    groups 为模式串列表的列表；返回 (material, page, 分句文本, quote) 或 None。
    """
    compiled_groups = [_compile_res(g) for g in groups]
    excl = _compile_res(exclusion_patterns or [])
    for m in materials:
        for page_no, text in _norm_pages_breaks(m):
            for clause in re.split(r"[。；;]", text):
                if not clause or any(p.search(clause) for p in excl):
                    continue
                if all(any(p.search(clause) for p in g) for g in compiled_groups):
                    return (m, page_no, clause, clause[:60])
    return None


# ---- A1-RULE-04-HOTFIX：极性守卫 / 对象标识（数据层词表驱动，内核消费）----
# 命中位置前 N 字窗口出现否定/条件/制度前缀 → 该命中是「被否定的非事实」，作废或降级；
# 证据必须对应同一主体/项目/合同对象；对象 token 从材料文本或证据引文抽取。

_GUARD_CATEGORIES = ("institutional", "condition", "negation")
_GUARD_KEYS = {"institutional": "institutional_prefixes",
               "condition": "condition_prefixes",
               "negation": "negation_prefixes"}


def _guard_lists(guard: dict) -> dict:
    """编译 guard 词表（缺字段=空，向后兼容旧调用）。"""
    guard = guard or {}
    return {k: _compile_res(guard.get(_GUARD_KEYS[k], [])) for k in _GUARD_CATEGORIES}


def _guard_hit(clause: str, word: str, guard_c: dict, window: int = 8,
               cats=None):
    """命中子串是否被否定/条件/制度前缀支配；返回类别或 None。

    clause=完整分句（已按句界切分），word=命中子串。窗口=命中起点前 window 字，
    且不跨越句界；命中子串自身也纳入扫描（如「劳务企业不得计取…」中「不得」在
    命中串内）。制度前缀优先于条件、条件优先于否定（F02/F03/F08 同型）。

    cats：参与判定的类别子集；**本身即否定/禁止形态的风险模式**（如 V16-01 的
    「未经监理审查」、W16-03 的「不得分包」）只套 institutional/condition，不套
    negation，否则会命中自身否定而误杀。
    """
    if not word:
        return None
    idx = clause.find(word)
    if idx < 0:
        return None
    start = max(0, idx - window)
    for sep in ("。", "；", ";", "\n"):
        cut = clause.rfind(sep, 0, idx)
        if cut >= 0:
            start = max(start, cut + 1)
    region = clause[start: idx + len(word)]
    for cat in (cats or _GUARD_CATEGORIES):
        if any(p.search(region) for p in guard_c.get(cat, ())):
            return cat
    return None


_RISK_GUARD_CATS = ("institutional", "condition")   # 风险/禁止形态：不套否定


def _clause_word(patterns: list, clause: str):
    """在分句内取首个命中子串（用于对已知分句再做极性窗口定位）。"""
    for p in _compile_res(patterns):
        m = p.search(clause)
        if m:
            return m.group(0)
    return None


_OBJECT_TOKEN_RE = re.compile(r"([甲乙丙丁A-Z])(?:项目|标段|公司|企业|集团|单位|工程|合同|中心|局|院|队|班组)")


def _object_tokens(text: str, pattern: str = None) -> set:
    """从文本抽取项目/主体标识 token（如「甲项目/乙项目」「X公司」）；无则空集。

    纯文本层可抽取、不依赖外部模型/数据库；空集=未标注对象，按「无法证明不同对象」
    处理（保守：不为空集凭空造出差异）。
    """
    if not text:
        return set()
    if pattern:
        return set(re.findall(pattern, text))
    return set(_OBJECT_TOKEN_RE.findall(text))


def _same_object_tokens(a: set, b: set) -> bool:
    """两侧对象是否可认定同一：任一侧为空→不可证明不同（True）；两侧非空且无交集→False。"""
    if a and b and not (a & b):
        return False
    return True


def _clause_containing(entry, word):
    """证据条目 (mat, page, quote, word) 中，包含 word 的完整分句（换行=句界）。"""
    if entry is None or not word:
        return None
    mat, page_no = entry[0], entry[1]
    for pno, text in _norm_pages_breaks(mat):
        if pno != page_no:
            continue
        for clause in re.split(r"[。；;]", text):
            if word in clause:
                return clause
    return None


def _has_plan_prefix(clause: str, d: tuple, plan_prefixes: list) -> bool:
    """日期 d 前 4 字窗口是否含计划/拟类前缀（计划类日期不作事件日期）。"""
    if not plan_prefixes:
        return False
    for mm in re.finditer(r"\d{4}年\d{1,2}月\d{1,2}日", clause or ""):
        g = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", mm.group(0))
        if g and tuple(int(x) for x in g.groups()) == d:
            pre = clause[max(0, mm.start() - 4):mm.start()]
            if any(p in pre for p in plan_prefixes):
                return True
    return False


def _event_date(clause, date_pattern: str, plan_prefixes: list):
    """事件关联句窗口内的有效事件日期：计划/拟前缀日期不作事件日期；多个取最早。"""
    if not clause:
        return None
    cands = [d for d in _date_tuples(clause, date_pattern)
             if not _has_plan_prefix(clause, d, plan_prefixes)]
    return min(cands) if cands else None


def _consent_is_positive(clause: str, word: str, det: dict) -> bool:
    """同意侧极性：否定同意（「未经/未报/无…同意」）不是合规依据（F11）。"""
    if not clause or not word:
        return False
    guard_c = _guard_lists(det.get("clause_polarity_guard"))
    gneg = {"institutional": (), "condition": (),
            "negation": guard_c.get("negation", ())}
    if _guard_hit(clause, word, gneg) is not None:
        return False
    idx = clause.find(word)
    if idx < 0:
        return False
    pre = clause[max(0, idx - 4):idx]
    return not any(tok in pre for tok in det.get("consent_negation_patterns", []))


def _extract_no_subcontract_scope(clause: str, det: dict) -> str:
    """从不得分包条款抽取被禁对象短语（如「主体工程」「桥梁主体」）；无则空串。"""
    for p in _compile_res(det.get("no_subcontract_scope_patterns", [])):
        m = p.search(clause or "")
        if m:
            g = m.group(1).strip()
            for _ in range(2):      # 反复剥离引语式引导词（「总包合同约定：」等）
                g2 = re.sub(r"^(?:总包合同约定|总包合同|总承包合同|施工总承包|合同约定|"
                            r"本工程|本合同|工程|约定|合同)[：:，,、]?", "", g)
                if g2 == g:
                    break
                g = g2
            return g
    return ""


def _extract_subcontract_scope(entry, det: dict) -> str:
    """抽取实际分包范围短语（分包范围/分包内容 后的短语）；无则回退该分句。"""
    clause = _clause_containing(entry, entry[3]) if entry is not None else None
    if not clause:
        return ""
    for p in _compile_res(det.get("sub_scope_extract_patterns", [])):
        m = p.search(clause)
        if m:
            return m.group(0)
    return clause


def _scope_is_general(scope: str, det: dict) -> bool:
    """禁止对象是否为「主体工程/全部工程」等兜底范围（此时任意分包均触发）。"""
    return bool(scope) and any(k in scope for k in det.get("general_main_scope_keywords", []))


def _scope_work_types(text: str, det: dict) -> set:
    return {k for k in det.get("work_type_keywords", []) if k and k in (text or "")}


def _has_negated_construction(det: dict, pool: list) -> bool:
    """pool 中是否存在被否定/条件/制度前缀支配的「承担施工」表述（F07 冲突依据）。

    仅当分句中同时出现「劳务主体」+「施工范围」两句式、且施工范围命中被否定/条件
    前缀支配时才为冲突（避免把「承包人负责施工」等非劳务语境误判为冲突）。
    """
    guard_c = _guard_lists(det.get("clause_polarity_guard"))
    scope_pats = _compile_res(det.get("construction_scope_patterns", []))
    subj_pats = _compile_res(["(?:%s)" % "|".join(det.get("labor_subject_patterns", []))])
    for m in pool:
        for _page_no, text in _norm_pages_breaks(m):
            for clause in re.split(r"[。；;]", text):
                if not any(p.search(clause) for p in subj_pats):
                    continue
                for p in scope_pats:
                    mm = p.search(clause)
                    if mm and _guard_hit(clause, mm.group(0), guard_c) is not None:
                        return True
    return False


def _hit_finding(task, item_id: str, mat, page, quote, message: str,
                 source: str) -> BsFinding:
    return BsFinding(task_id=task.id,
                     material_id=mat.id if mat is not None else None,
                     item_id=item_id, kind=_ILLEGAL_KIND, status=STATUS_HIT,
                     message=message, quote=quote, page=page, source=source)


def _miss_finding(task, item_id: str, mat, page, quote, message: str,
                  source: str) -> BsFinding:
    return BsFinding(task_id=task.id,
                     material_id=mat.id if mat is not None else None,
                     item_id=item_id, kind=_ILLEGAL_KIND, status=STATUS_MISS,
                     message=message, quote=quote, page=page, source=source)


def _open_term_kind(rule: dict) -> str:
    """兜底条款归哪个 kind：按条文归属推导（article 15=转包 → transfer_scope；
    16/17=违法分包与劳务名义 → illegal_subcontract）。

    原实现一律写 `_ILLEGAL_KIND`，但第十五条（十）T15-10 属**转包**条款——若沿用
    违法分包 kind，报告会把它归到「违法分包情形」分区，读者看到的归类是错的。
    按 article 字段推导（数据驱动），不硬编码条目 ID。
    """
    return _TRANSFER_KIND if str(rule.get("article")) == "15" else _ILLEGAL_KIND


def _open_term_finding(task, rule: dict) -> BsFinding:
    """开放条款：恒不可判定仅登记（open_term 字段驱动，不参与命中/未命中统计）。"""
    missing = "、".join(_EVIDENCE_LABELS.get(k, k) for k in rule["required_evidence"])
    return _undecidable_finding(
        task, rule["id"], _open_term_kind(rule),
        "不可判定：「%s」为开放条款，仅登记，不参与命中/未命中统计（缺：%s）；"
        "认定标准待有权口径补充后启用，请人工复核" % (rule["title"], missing),
        _rule_source(rule), missing=list(rule["required_evidence"]))


# 资格证据侧「同义异名」归一（BS-P1-12）：`qualification_labels` 是历史文案字典，
# 键取自早年实现（`business_license`）；规则目录 required_evidence 用的是 `license`。
# 结构化缺件必须落规则目录的 key，否则同一缺件按两个 key 各计一次、材料类归并翻倍。
# 只做 key 归一，不改任何文案。
_QE_KEY_ALIAS = {"business_license": "license"}


def _run_w16_01(task, pool: list, qe, rule: dict) -> BsFinding:
    """W16-01：分包给个人或不具备相应条件企业。

    - 命中=分包关系在场 +（明示分包给个人/包工头，或同句明示分包主体不具备资质/
      未取得许可的否定性表述）；双重否定（「不存在不具备资质情形」类）机器不裁：
      资格证据不齐时落不可判定；
    - 未命中=分包关系在场 + 三侧资格证据（证照/资质/安全生产许可证）全合格 +
      无否定性明示；
    - 不可判定=资格证据缺字段（逐字列缺哪个）或无资格证据且无否定明示。
    """
    det = rule["illegal_detection"]
    source = _rule_source(rule)
    labels = det["qualification_labels"]
    guard_c = _guard_lists(det.get("clause_polarity_guard"))

    relation = _page_match_breaks(pool, det["subcontract_relation_patterns"])
    individual = _clause_match_breaks(pool, det["individual_award_patterns"])
    neg_qual = _same_clause_groups(
        pool, [det["no_qualification_patterns"],
               ["(?:%s)" % "|".join(det["qualification_subject_words"])]])
    dual_neg = _page_match_breaks(pool, det["dual_negation_patterns"])

    # 极性守卫：命中子串被否定/条件/制度前缀支配（如「不得分包给个人」「禁止…」）
    # → 作废该命中，不作为既成分包事实（F01 规则）。
    individual_guarded = False
    if individual is not None:
        if _guard_hit(individual[2], individual[3], guard_c) is not None:
            individual_guarded = True
            individual = None
    neg_qual_guarded = False
    if neg_qual is not None:
        # 否定性资格表述本身即风险信号，不套否定前缀（否则误杀「未取得许可证」正向命中）；
        # 仅制度/条件前缀（「制度规定：未取得资质不得分包」类引用）作废。
        gq = {"institutional": guard_c.get("institutional", ()),
              "condition": guard_c.get("condition", ()), "negation": ()}
        w2 = _clause_word(det["no_qualification_patterns"], neg_qual[2])
        if _guard_hit(neg_qual[2], w2, gq) is not None:
            neg_qual_guarded = True
            neg_qual = None

    qe = qe or {}
    # 文案照旧取 qualification_labels 的中文值（保持既有措辞不变）；
    # 结构化缺件另算一份、按规则目录 required_evidence 的 key 落库（BS-P1-12）：
    # 二者同义异名（`business_license` ↔ `license`），若直接拿 labels 的 key 落库，
    # 同一缺件会在 `license` / `business_license` 两个 key 下各计一次。
    qe_missing = [label for key, label in labels.items() if not qe.get(key)]
    qe_missing_keys = [_QE_KEY_ALIAS.get(key, key)
                       for key in labels if not qe.get(key)]
    qe_full = qe is not None and not qe_missing
    if qe_full:
        # 三证齐备时双重否定表述（「不存在不具备资质情形」）与资格证据同向，
        # 按无否定性明示处理，不再触发极性不可判定
        dual_neg = None

    if dual_neg is not None:
        return _undecidable_finding(
            task, rule["id"], _ILLEGAL_KIND,
            "不可判定：材料存在双重否定表述（「%s」），机器无法可靠判定资质极性，"
            "不作肯定或否定证据；资格证据不齐（缺：%s），请人工复核"
            % (dual_neg[3], "、".join(qe_missing) or "证照、资质、安全生产许可证"),
            source, dual_neg[0].id)

    if individual is not None:
        mat, page, quote, word = individual
        return _hit_finding(
            task, rule["id"], mat, page, quote,
            "命中：材料明示分包给个人/包工头的线索（「%s」），"
            "请人工复核是否属第十六条（一）情形" % word, source)
    if neg_qual is not None:
        mat, page, clause, _q = neg_qual
        return _hit_finding(
            task, rule["id"], mat, page, clause[:60],
            "命中：材料同句明示分包主体不具备相应资质/未取得许可的否定性表述"
            "（「%s」），请人工复核是否属第十六条（一）情形" % clause[:40], source)

    if relation is None:
        missing_txt = "；资格证据亦不齐（缺：%s）" % "、".join(qe_missing) if qe_missing else ""
        guard_txt = "；否定/条件/制度前缀下的表述不作为既成分包事实" if (
            individual_guarded or neg_qual_guarded) else ""
        # 缺件 key：分包关系侧取规则目录的 subcontractor_subject；资格侧按 key 别名归一。
        return _undecidable_finding(
            task, rule["id"], _ILLEGAL_KIND,
            "不可判定：未见分包关系明示（缺：分包主体/分包关系）" + missing_txt + guard_txt,
            source, missing=["subcontractor_subject"] + qe_missing_keys)

    if qe_missing:
        guard_txt = "；否定/条件/制度前缀下的表述不作为既成分包事实" if (
            individual_guarded or neg_qual_guarded) else ""
        return _undecidable_finding(
            task, rule["id"], _ILLEGAL_KIND,
            "不可判定：分包关系在场，未见分包给个人或不具备相应条件的否定性明示，"
            "但资格证据不齐（缺：%s），不能据此判未命中%s" % ("、".join(qe_missing), guard_txt),
            source, relation[0].id, missing=qe_missing_keys)

    mat, page, quote, _w = relation
    return _miss_finding(
        task, rule["id"], mat, page, quote,
        "未命中：分包关系在场，%s三侧资格证据均合格，且未见分包给个人或"
        "不具备相应条件的否定性明示" % "、".join(labels.values()), source)


def _run_w16_02(task, nl_findings, rule: dict) -> BsFinding:
    """W16-02：分包负面清单主体或关键性工作（复用已计算的负面清单核查结论）。

    - 有命中条目 → 命中（message 列条目号与事由，关联规则 id 进去重元数据；
      并存不可判定条目时在 message 中提示条数，不翻案——命中依据已充分）；
    - 无命中但存在不可判定条目 → 不可判定（无法排除）；
    - 当前目录全部条目均有未命中结论 → 未命中；无结论或覆盖不足 → 不可判定。
      上游只输出触发词召回项，省级不增补不能替代国家条目的实际核查覆盖。
    """
    source = _rule_source(rule)
    missing_evidence = ["negative_list_match"]
    if not nl_findings:
        return _undecidable_finding(
            task, rule["id"], _ILLEGAL_KIND,
            "不可判定：负面清单核查未执行或无结论条目（缺：负面清单核查结论）——"
            "「跑过且全部未命中」与「未跑」是两种不同证据状态，本条按未处理",
            source, missing=missing_evidence)
    expected = {entry["id"] for entry in negative_list_rules()}
    registered = [finding for finding in nl_findings if finding.item_id in expected]
    evaluated = {finding.item_id for finding in registered
                 if finding.status in (STATUS_HIT, STATUS_MISS, STATUS_UNDECIDABLE)}
    missing = sorted(expected - evaluated)
    coverage = "已评估%d/%d条（当前负面清单目录，含省级增补状态）" % (len(evaluated), len(expected))
    hits = [f for f in registered if f.status == STATUS_HIT]
    undecided = [f for f in registered if f.status == STATUS_UNDECIDABLE]
    if hits:
        ids = "、".join(f.item_id for f in hits)
        first = hits[0]
        reasons = "；".join("%s：%s" % (f.item_id, (f.message or "")[:40])
                            for f in hits[:3])
        extra = ("；另有 %d 条负面清单条目不可判定，无法排除" % len(undecided)
                 if undecided else "") + "；" + coverage
        if missing:
            extra += "，其余条目覆盖不足，未召回不等于已排除"
        return BsFinding(
            task_id=task.id, material_id=first.material_id, item_id=rule["id"],
            kind=_ILLEGAL_KIND, status=STATUS_HIT,
            message="命中：分包范围涉及负面清单条目（%s），关联结论：%s%s，"
                    "请人工复核是否属第十六条（二）情形" % (ids, reasons, extra),
            quote=first.quote, page=first.page, source=source)
    if undecided:
        return _undecidable_finding(
            task, rule["id"], _ILLEGAL_KIND,
            "不可判定：负面清单核查存在不可判定条目（%s），负面清单侧无法排除"
            "第十六条（二）情形（缺：负面清单确定性结论）；%s"
            % ("、".join(f.item_id for f in undecided[:5]), coverage),
            source, missing=missing_evidence)
    if not expected or missing:
        return _undecidable_finding(
            task, rule["id"], _ILLEGAL_KIND,
            "不可判定：负面清单核查覆盖不足，%s；未召回或未取得有效结论的条目（%s）"
            "不能按未命中处理，未召回不等于明确不适用或已排除（缺：完整负面清单核查结论）；"
            "需人工逐条核实国家条目，此为核查覆盖缺口，不要求企业另补一份证明"
            % (coverage, "、".join(missing[:5]) or "当前目录未登记条目"),
            source, missing=missing_evidence)
    return _miss_finding(
        task, rule["id"], None, None, None,
        "未命中：负面清单核查已执行且全部条目未命中，%s，未见分包负面清单"
        "主体或关键性工作的结论线索" % coverage,
        source)


def _date_tuples(text: str, date_pattern: str) -> list:
    """抽取文本中全部「YYYY年M月D日」形态日期（模式来自 json date_pattern），
    返回 (年, 月, 日) 元组列表。"""
    out = []
    for mm in re.finditer(date_pattern, text):
        d = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", mm.group(0))
        if d:
            out.append(tuple(int(g) for g in d.groups()))
    return out


def _run_w16_03(task, general: list, subs_labor: list, pool: list,
                rule: dict) -> BsFinding:
    """W16-03：分包合同明确不得分包的工程。

    - 命中=总包合同明文「某某工程不得分包」条款 + 分包发生（同一对象）+ 无发包人
      书面同意明示；禁止范围必须与实际分包范围对照：禁止对象为「主体工程/全部工程
      等兜底」→ 任意分包均命中；禁止对象为具体分项工程（桥梁/路基…）→ 仅当该范围
      与分包范围同类对照一致才命中，否则不可判定（F10 桥梁禁令 vs 绿化分包）；
    - 未命中=条款+分包（同一对象）+ 书面同意在场且**同意日期与签订日期各自绑定事件**
      可抽取、同意不晚于签订（两日期都比到才判）；
    - 不可判定=缺条款/缺分包/跨对象/范围无对照/同意在场但日期不可比或晚于签订
      （事后同意效力不由机器裁定）。
    日期绑定（HOTFIX）：签订日期只在「分包合同…签订」关联句窗口抽、同意日期只在
    「发包人书面同意」关联句窗口抽，否定同意（「未经…同意」）不算合规且其日期不作
    同意日期；计划/拟类前缀日期与其他项目日期不作事件日期。
    """
    det = rule["illegal_detection"]
    source = _rule_source(rule)
    guard_c = _guard_lists(det.get("clause_polarity_guard"))
    clause = _page_match_breaks(general, det["no_subcontract_patterns"])
    subcontract = _page_match_breaks(subs_labor, det["subcontract_contract_patterns"])
    consent = _page_match_breaks(pool, det["consent_patterns"])
    sign = _page_match_breaks(subs_labor, det.get("subcontract_sign_patterns", []))

    # 极性守卫：制度禁令引用（「制度规定：…不得分包」）不是既成分包事实（F08 同型）。
    if clause is not None and _guard_hit(clause[2], clause[3], guard_c, cats=_RISK_GUARD_CATS) is not None:
        clause = None
    if sign is not None and _guard_hit(sign[2], sign[3], guard_c, cats=_RISK_GUARD_CATS) is not None:
        sign = None

    # 同意侧对象归属：同意证据若标注了项目/主体标识（乙），而本项目条款/分包侧
    # 均无该标识（或另有明确标识）→ 该同意属其他项目，不作本项目同意依据（日期反例2）。
    _obj_pat = det.get("object_token_pattern")

    if clause is None or subcontract is None:
        missing = [k for k, side in (("no_subcontract_clause", clause),
                                     ("subcontract_scope", subcontract)) if side is None]
        return _undecidable_finding(
            task, rule["id"], _ILLEGAL_KIND,
            "不可判定：无法对照不得分包条款与分包发生关系%s"
            % ("（缺：%s）" % "、".join(_EVIDENCE_LABELS[k] for k in missing)),
            source, (clause or subcontract)[0].id if (clause or subcontract) else None,
            missing=missing)

    # 对象对齐：总包条款侧与分包侧若各自标注不同项目/主体（甲/乙）→ 非同一对象。
    clause_tk = _object_tokens(_material_text(clause[0]), _obj_pat)
    sub_tk = _object_tokens(_material_text(subcontract[0]), _obj_pat)
    if clause_tk and sub_tk and not (clause_tk & sub_tk):
        return _undecidable_finding(
            task, rule["id"], _ILLEGAL_KIND,
            "不可判定：不得分包条款侧（%s）与分包侧（%s）分属不同审查对象，"
            "无法证明同一审查对象，不能据此判命中或未命中"
            % ("、".join(sorted(clause_tk)), "、".join(sorted(sub_tk))),
            source, clause[0].id)

    # 同意侧对象归属：同意证据若标注了项目/主体标识（乙），而本项目条款/分包侧均无
    # 该标识 → 该同意属其他项目，不作本项目同意依据（日期反例2，不把其他项目当本项目）。
    if consent is not None and _obj_pat:
        con_tk = _object_tokens(_material_text(consent[0]), _obj_pat)
        local_tk = _object_tokens(_material_text(clause[0]), _obj_pat) \
            | _object_tokens(_material_text(subcontract[0]), _obj_pat)
        if con_tk and not (con_tk & local_tk):
            consent = None

    # 禁止范围 ↔ 实际分包范围对照
    general_scope = _extract_no_subcontract_scope(clause[2], det)
    sub_scope = _extract_subcontract_scope(subcontract, det)
    if general_scope and not _scope_is_general(general_scope, det):
        banned_types = _scope_work_types(general_scope, det)
        sub_types = _scope_work_types(sub_scope, det)
        if banned_types and sub_types and not (banned_types & sub_types):
            return _undecidable_finding(
                task, rule["id"], _ILLEGAL_KIND,
                "不可判定：不得分包条款禁止范围为「%s」，与实际分包范围「%s」不对应，"
                "无法证明同一范围，不能据此判命中或未命中"
                % ("/".join(sorted(banned_types)), "/".join(sorted(sub_types))),
                source, clause[0].id)

    if consent is None:
        # 资料缺同意属证据缺口：不把「未上传」当「实际未同意」——但禁止条款+同一对象
        # 分包发生已成立，按第十六条（三）情形线索命中（人工复核）。
        mat, page, quote, word = clause
        return _hit_finding(
            task, rule["id"], mat, page, quote,
            "命中：总包合同明文「%s」条款在场，且同一审查对象下分包发生（分包合同在场），"
            "未见发包人书面同意明示，请人工复核是否属第十六条（三）情形" % word, source)

    # 同意侧极性：否定同意（「未经发包人书面同意」）不是合规依据（F11）。
    consent_ok = _consent_is_positive(consent[2], consent[3], det)
    if not consent_ok:
        mat, page, quote, word = clause
        return _hit_finding(
            task, rule["id"], mat, page, quote,
            "命中：总包合同明文「%s」条款在场，分包同对象发生，且材料中发包人书面同意"
            "为否定表述（「%s」），否定同意不作合规依据，请人工复核是否属第十六条（三）情形"
            % (word, consent[3]), source)

    # 日期绑定：签订日期只在「分包合同…签订」关联句抽；同意日期只在肯定同意关联句抽。
    plan_pref = det.get("plan_prefixes", [])
    cw = _clause_containing(consent, consent[3])
    cd = _event_date(cw, det["date_pattern"], plan_pref)
    if cd is None:
        return _undecidable_finding(
            task, rule["id"], _ILLEGAL_KIND,
            "不可判定：不得分包条款、分包与发包人书面同意明示均在场，但同意日期不可比"
            "（缺：发包人书面同意日期或分包签订日期），不能据此判未命中",
            source, consent[0].id)
    sw = _clause_containing(sign, sign[3]) if sign is not None else None
    sd = _event_date(sw, det["date_pattern"], plan_pref)
    if sd is None:
        return _undecidable_finding(
            task, rule["id"], _ILLEGAL_KIND,
            "不可判定：不得分包条款、分包与发包人书面同意明示均在场，但分包签订日期"
            "不可比（缺：分包签订日期），不能据此判未命中",
            source, consent[0].id)
    if cd <= sd:
        return _miss_finding(
            task, rule["id"], consent[0], consent[1], consent[2],
            "未命中：不得分包条款与分包均在场，但发包人书面同意日期（%04d-%02d-%02d）"
            "不晚于分包签订日期（%04d-%02d-%02d），同意在先" % (cd + sd), source)
    return _undecidable_finding(
        task, rule["id"], _ILLEGAL_KIND,
        "不可判定：发包人书面同意日期（%04d-%02d-%02d）晚于分包签订日期"
        "（%04d-%02d-%02d），事后同意的效力不由机器裁定，请人工复核"
        % (cd + sd), source, consent[0].id)


def _run_w16_04(task, pool: list, rule: dict) -> BsFinding:
    """W16-04：分包人以他人名义承揽分包工程。

    - 命中=同句明示「以他人/其他单位/第三方名义承揽」+ 合同签订方与实际实施主体
      不一致（同句对照）；**禁「挂靠」结论**：message 只用「以他人名义承揽线索」
      语气（禁词由测试扫描把关）；
    - 未命中=合同主体与实施主体一致明示（同一主体表述）；
    - 不可判定=仅主体不一致线索无名义承揽明示（主体不一致线索请人工复核）。
    """
    det = rule["illegal_detection"]
    source = _rule_source(rule)
    guard_c = _guard_lists(det.get("clause_polarity_guard"))
    nominee = _page_match_breaks(pool, det["nominee_patterns"])
    mismatch = _page_match_breaks(pool, det["subject_mismatch_patterns"])
    match_stmt = _page_match_breaks(pool, det["subject_match_patterns"])
    both = _same_clause_groups(pool, [det["nominee_patterns"],
                                      det["subject_mismatch_patterns"]])
    # 极性守卫：命中被否定/条件/制度前缀支配（「不得以他人名义…」「制度规定禁止…」）→ 作废。
    if both is not None and _guard_hit(both[2], both[3], guard_c, cats=_RISK_GUARD_CATS) is not None:
        both = None
    if nominee is not None and _guard_hit(nominee[2], nominee[3], guard_c, cats=_RISK_GUARD_CATS) is not None:
        nominee = None

    if both is not None:
        mat, page, clause, quote = both
        return _hit_finding(
            task, rule["id"], mat, page, quote,
            "命中：发现以他人名义承揽分包工程的线索（同句明示合同签订方与实际"
            "实施主体不一致：「%s」），请人工复核是否属第十六条（四）情形"
            % clause[:40], source)
    if match_stmt is not None and mismatch is None and nominee is None:
        mat, page, quote, word = match_stmt
        return _miss_finding(
            task, rule["id"], mat, page, quote,
            "未命中：材料明示合同主体与实际实施主体一致（「%s」），"
            "未见名义承揽或主体不一致表述" % word, source)
    if mismatch is not None:
        mat, page, quote, word = mismatch
        return _undecidable_finding(
            task, rule["id"], _ILLEGAL_KIND,
            "不可判定：存在合同签订方与实际实施主体不一致线索（「%s」），但同句"
            "未见以他人名义承揽明示——主体不一致线索，请人工复核"
            "（缺：名义承揽明示）" % word, source, mat.id,
            missing=["named_subject"])
    return _undecidable_finding(
        task, rule["id"], _ILLEGAL_KIND,
        "不可判定：未见以他人名义承揽明示，也未见主体对照表述"
        "（缺：名义承揽明示、合同与实施主体对照）", source,
        missing=["named_subject", "contract_and_material_subjects"])


def _run_w16_05(task, pool: list, rule: dict) -> BsFinding:
    """W16-05：以劳务合作名义进行施工分包。

    - 命中=劳务合同在场 + 同句明示劳务主体承担施工范围 + 计费条款指向工程施工/
      工程量而非纯劳务；**范围侧与计费侧必须同材料、同主体对象**（禁止全 pool 任取
      一侧拼接，F06 跨项目/跨主体拼接）；**冲突/否定判断必须先于 hit 返回**（F07：
      否定施工 + 纯劳务 + 施工计费冲突时不得先 hit；条件/制度引用不作既成事实）；
    - 未命中=劳务合同 + 范围明示仅劳务作业 + 按劳务工作量/报酬计费 + 无施工范围
      明示（施工范围与纯劳务范围同在场 → 指向冲突，不可判定）；
    - 不可判定=缺任一侧或对象无法对齐（列缺什么）；与 L17-01/L17-02 的重叠去重由
      illegal_subcontract_relations 承载（本函数不管）。
    """
    det = rule["illegal_detection"]
    source = _rule_source(rule)
    guard_c = _guard_lists(det.get("clause_polarity_guard"))
    labor_subject_re = ["(?:%s)" % "|".join(det["labor_subject_patterns"])]
    obj_pat = det.get("object_token_pattern")
    labor_contract = _labor_subcontract_clue(pool, det["labor_contract_patterns"])
    construction_scope = _same_clause_groups(
        pool, [labor_subject_re, det["construction_scope_patterns"]])
    construction_payment = _page_match_breaks(pool, det["construction_payment_patterns"])
    labor_only = _page_match_breaks(pool, det["labor_only_scope_patterns"])
    labor_payment = _page_match_breaks(pool, det["labor_payment_terms_patterns"])

    if labor_contract is None:
        return _undecidable_finding(
            task, rule["id"], _ILLEGAL_KIND,
            "不可判定：未发现企业间劳务合作合同线索，无法判断该劳务情形是否适用；"
            "如有相关合同，请提供或人工核实", source,
            missing=["labor_contract"])

    # 极性守卫：施工范围命中被否定/条件/制度前缀支配（「不承担工程施工」「若…」）→ 作废。
    scope_guarded = False
    if construction_scope is not None:
        sw = _clause_word(det["construction_scope_patterns"], construction_scope[2])
        if _guard_hit(construction_scope[2], sw, guard_c) is not None:
            scope_guarded = True
            construction_scope = None
    payment_guarded = False
    if construction_payment is not None:
        if _guard_hit(construction_payment[2], construction_payment[3], guard_c, cats=_RISK_GUARD_CATS) is not None:
            payment_guarded = True
            construction_payment = None

    # 对象对齐：施工范围侧与计费侧必须同材料；各自标注不同项目/主体 → 非同一对象。
    if construction_scope is not None and construction_payment is not None:
        if construction_payment[0].id != construction_scope[0].id:
            return _undecidable_finding(
                task, rule["id"], _ILLEGAL_KIND,
                "不可判定：施工范围侧与施工计费侧分属不同材料，无法证明同一主体/项目/"
                "合同语境（禁止跨材料拼接），请人工复核"
                "（缺：同一材料内的施工范围与施工计费对照）", source,
                construction_scope[0].id)
        stk = _object_tokens(_material_text(construction_scope[0]), obj_pat)
        ptk = _object_tokens(_material_text(construction_payment[0]), obj_pat)
        if stk and ptk and not (stk & ptk):
            return _undecidable_finding(
                task, rule["id"], _ILLEGAL_KIND,
                "不可判定：施工范围侧（%s）与施工计费侧（%s）分属不同主体/项目，"
                "无法证明同一审查对象，不能据此判命中"
                % ("、".join(sorted(stk)), "、".join(sorted(ptk))), source,
                construction_scope[0].id)

    # 冲突判断先于 hit：范围指向冲突、或否定施工与施工计费并存 → 不可判定（F07）。
    conflict = labor_only is not None or _has_negated_construction(det, pool)
    if conflict and (construction_scope is not None or construction_payment is not None):
        anchor = construction_scope or construction_payment or labor_only
        return _undecidable_finding(
            task, rule["id"], _ILLEGAL_KIND,
            "不可判定：劳务合同同时出现承担施工范围与「不承担施工/仅劳务作业」等"
            "冲突表述，范围证据指向冲突，不能据此判命中或未命中", source, anchor[0].id)

    if labor_contract is not None and construction_scope is not None \
            and construction_payment is not None:
        mat, page, clause, quote = construction_scope
        pay_word = construction_payment[3]
        return _hit_finding(
            task, rule["id"], mat, page, quote,
            "命中：劳务合同在场且同句明示劳务主体承担施工范围（「%s」），同一材料内"
            "计费条款指向工程施工/工程量而非纯劳务（「%s」），请人工复核是否属以劳务"
            "合作名义进行施工分包（第十六条（五）情形线索）" % (clause[:40], pay_word),
            source)

    if construction_scope is not None or construction_payment is not None:
        if labor_only is not None:
            anchor = construction_scope or construction_payment
            return _undecidable_finding(
                task, rule["id"], _ILLEGAL_KIND,
                "不可判定：施工范围与纯劳务范围表述同在场，范围证据指向冲突，"
                "不能据此判命中或未命中", source, anchor[0].id)
        guard_txt = "；否定/条件/制度前缀下的表述不作为既成事实" if (
            scope_guarded or payment_guarded) else ""
        return _undecidable_finding(
            task, rule["id"], _ILLEGAL_KIND,
            "不可判定：施工范围或施工计费线索在场，但施受对照或配套证据不齐"
            "（缺：施工范围明示或劳务计费条款），请人工复核%s" % guard_txt,
            source, (construction_scope or construction_payment)[0].id)

    if labor_only is not None and labor_payment is not None:
        mat, page, quote, word = labor_only
        return _miss_finding(
            task, rule["id"], mat, page, quote,
            "未命中：劳务合同在场，范围明示仅劳务作业（「%s」）且按劳务报酬/工作量"
            "计费（「%s」），未见施工范围明示" % (word, labor_payment[3]), source)

    guard_txt = "；否定/条件/制度前缀下的表述不作为既成事实" if (
        scope_guarded or payment_guarded) else ""
    missing = [k for k, side in (("labor_contract", labor_contract),
                                 ("construction_scope", construction_scope),
                                 ("labor_payment_terms", labor_payment)) if side is None]
    return _undecidable_finding(
        task, rule["id"], _ILLEGAL_KIND,
        "不可判定：以劳务合作名义进行施工分包的证据侧不齐（缺：%s）%s"
        % ("、".join(_EVIDENCE_LABELS[k] for k in missing), guard_txt),
        source, labor_contract[0].id, missing=missing)


def _run_w16_06(task, pool: list, rule: dict) -> BsFinding:
    """W16-06：再分包或劳务作业转交他人。

    - 命中=中间分包主体再分包/转交明示（含下游主体词）+ 实际实施证据同分句；
    - 未命中=明示工程自行实施/不再分包；局部设计或资料义务不作排除证据；
    - 不可判定=缺合同链条或实施证据。
    """
    det = rule["illegal_detection"]
    source = _rule_source(rule)
    guard_c = _guard_lists(det.get("clause_polarity_guard"))
    downstream_re = ["(?:%s)" % "|".join(det["downstream_subject_words"])]
    resub_and_impl = _same_clause_groups(
        pool, [det["resubcontract_patterns"], downstream_re,
               det["implementation_patterns"]])
    resub = _page_match_breaks(pool, det["resubcontract_patterns"])
    # “自行完成”须明确指向工程施工或本工程整体，不能把深化设计/资料整理等局部义务
    # 当成工程实际实施或不再分包的排除证据。这里仅收紧证据充分性，不裁定 BD-06 构成。
    completion_scope = (r"自行完成(?:(?:本|该|分包|全部|所有)?工程(?:的)?施工(?:任务|工作)?"
                        r"|(?:本|该|分包|全部|所有)工程)(?=[。；;，,]|$)")
    self_patterns = [completion_scope if pattern == "自行完成" else pattern
                     for pattern in det["self_implementation_patterns"]]
    self_impl = _page_match_breaks(pool, self_patterns)
    local_completion = _clause_match_breaks(pool, ["自行完成"])
    # 极性守卫：命中被否定/条件/制度前缀支配（「不得再次分包」「禁止转包」）→ 作废。
    if resub_and_impl is not None and _guard_hit(
            resub_and_impl[2], resub_and_impl[3], guard_c) is not None:
        resub_and_impl = None
    if resub is not None and _guard_hit(resub[2], resub[3], guard_c, cats=_RISK_GUARD_CATS) is not None:
        resub = None
    if self_impl is not None and _guard_hit(self_impl[2], self_impl[3], guard_c, cats=_RISK_GUARD_CATS) is not None:
        self_impl = None

    if resub_and_impl is not None:
        mat, page, clause, quote = resub_and_impl
        return _hit_finding(
            task, rule["id"], mat, page, quote,
            "命中：中间分包主体存在再分包/转交线索（「%s」）且同句见实际实施证据，"
            "请人工复核是否属第十六条（六）情形" % clause[:40], source)
    if self_impl is not None and resub is None:
        mat, page, quote, word = self_impl
        return _miss_finding(
            task, rule["id"], mat, page, quote,
            "未命中：材料明示自行实施/不再分包（「%s」），未见再分包或转交明示" % word,
            source)
    if local_completion is not None and resub is None:
        # BS-P1-03（设计 98 §7）：局部义务句（「自行完成深化设计/资料整理」等）既不作
        # 排除证据，也不作结论引文——不锚定该句，也不拿别句冒充证据；材料可回查。
        return _undecidable_finding(
            task, rule["id"], _ILLEGAL_KIND,
            "不可判定：仅见“自行完成深化设计/资料整理”等局部义务表述，不能证明工程"
            "实际实施或不再分包；缺工程实际实施与合同链条证据，请人工核实",
            source, local_completion[0].id,
            missing=["contract_chain", "actual_implementation"])
    missing = [k for k, side in (("contract_chain", resub),
                                 ("actual_implementation", resub_and_impl)) if side is None]
    if self_impl is not None and resub is not None:
        return _undecidable_finding(
            task, rule["id"], _ILLEGAL_KIND,
            "不可判定：自行实施与再分包表述同在场，指向冲突，不能据此判命中或未命中",
            source, resub[0].id)
    anchor = resub or self_impl
    return _undecidable_finding(
        task, rule["id"], _ILLEGAL_KIND,
        "不可判定：再分包/转交与实际实施的证据链不齐（缺：%s）"
        % "、".join(_EVIDENCE_LABELS[k] for k in missing),
        source, anchor[0].id if anchor is not None else None, missing=missing)


def _side_polarity_scan(evidence: list, risk_patterns: list, ok_patterns: list,
                        guard: dict = None):
    """侧内极性扫描（V16-01 两侧共用）：分句内风险优先——「未经发包人书面同意」
    包含子串「发包人书面同意」、「未经监理审查」包含「经监理审查」，若风险与合规
    模式各自独立扫描会同句双亮（子串互染）；故逐分句先试风险、风险在场该分句不计
    合规。跨分句肯定与否定并存 → 指向冲突（R03 极性纪律同型）。

    guard：极性守卫词表；命中子串被否定/条件/制度前缀支配（「制度规定：未经…不得…」
    类制度引用）→ 该命中作废，不点亮风险或合规（F08）。

    返回 ("risk"|"legal"|"conflict"|None, anchor)；anchor 为 (m, page, quote, word)。
    """
    guard_c = _guard_lists(guard)
    risk_c = _compile_res(risk_patterns)
    ok_c = _compile_res(ok_patterns)
    risk_anchor = ok_anchor = None
    for m in evidence:
        for page_no, text in _norm_pages_breaks(m):
            for clause in re.split(r"[。；;]", text):
                if not clause:
                    continue
                r = next((mm for mm in (p.search(clause) for p in risk_c) if mm), None)
                o = next((mm for mm in (p.search(clause) for p in ok_c) if mm), None)
                if r is not None:
                    if _guard_hit(clause, r.group(0), guard_c, cats=_RISK_GUARD_CATS) is not None:
                        continue           # 被否定/条件/制度前缀支配 → 不作风险证据
                    if risk_anchor is None:
                        risk_anchor = (m, page_no, clause[:60], r.group(0))
                elif o is not None:
                    if _guard_hit(clause, o.group(0), guard_c) is not None:
                        continue           # 合规表述同样受守卫（条件/制度引用不作合规依据）
                    if ok_anchor is None:
                        ok_anchor = (m, page_no, clause[:60], o.group(0))
    if risk_anchor is not None and ok_anchor is not None:
        return "conflict", risk_anchor
    if risk_anchor is not None:
        return "risk", risk_anchor
    if ok_anchor is not None:
        return "legal", ok_anchor
    return None, None


def _v16_01_supervision_side(a3_findings, evidence: list, det: dict) -> tuple:
    """监理侧判定：A3-01 RC-09 结论优先，材料文本明示兜底。

    返回 ("risk"|"legal"|"insufficient", anchor)；文本同侧肯定与否定并存 → 指向
    冲突 → 证据不足。
    """
    rc09 = next((f for f in (a3_findings or [])
                 if getattr(f, "item_id", None) == "RC-09"), None)
    if rc09 is not None and rc09.status == STATUS_HIT:
        return "risk", ("a3", rc09)
    if rc09 is not None and rc09.status == STATUS_MISS:
        return "legal", ("a3", rc09)
    verdict, anchor = _side_polarity_scan(
        evidence, det["supervision_risk_patterns"], det["supervision_ok_patterns"],
        det.get("clause_polarity_guard"))
    if verdict == "risk":
        return "risk", ("text", anchor)
    if verdict == "legal":
        return "legal", ("text", anchor)
    if verdict == "conflict":
        return "insufficient", ("conflict", anchor)
    return "insufficient", (None,)


def _v16_01_consent_side(evidence: list, det: dict) -> tuple:
    """发包人书面同意侧判定（材料文本明示，无 A3 条目）。返回形态同监理侧。"""
    verdict, anchor = _side_polarity_scan(
        evidence, det["consent_risk_patterns"], det["consent_ok_patterns"],
        det.get("clause_polarity_guard"))
    if verdict == "risk":
        return "risk", ("text", anchor)
    if verdict == "legal":
        return "legal", ("text", anchor)
    if verdict == "conflict":
        return "insufficient", ("conflict", anchor)
    return "insufficient", (None,)


def _side_anchor(anchor: tuple):
    """侧证据锚 → (material_id, page, quote) 或 (None, None, None)。"""
    kind = anchor[0]
    if kind == "a3":
        f = anchor[1]
        return f.material_id, f.page, f.quote
    if kind == "text":
        m, page, quote, _w = anchor[1]
        return m.id, page, quote
    if kind == "conflict":
        m, page, quote, _w = anchor[1]
        return m.id, page, quote
    return None, None, None


def _side_word(anchor: tuple):
    if anchor[0] in ("text", "conflict"):
        return anchor[1][3]
    if anchor[0] == "a3":
        return (anchor[1].quote or "")[:40]
    return ""


def _run_v16_01(task, evidence: list, a3_findings, rule: dict) -> BsFinding:
    """V16-01（新口径）：未经监理审查或未报发包人书面同意——两侧独立、缺任一侧
    不可判定（比旧 _check_v16_01「或逻辑从严」保守；旧入口保持旧行为不动）。

    - 监理侧：A3-01 RC-09 结论优先（hit→风险 / miss→合法 / undecidable 或缺→
      落材料文本明示兜底）；材料文本明示「未经监理审查/总监未签章」→风险、
      「监理审查/总监签章」在场→合法；同在→指向冲突→证据不足；
    - 同意侧：材料文本明示；无 A3 条目；
    - 笛卡尔分支：risk/risk→命中；legal/legal→未命中；risk/legal 与 legal/risk
      （混合态）→不可判定并明确说明哪侧风险哪侧合规、命中语义待人工裁决（R05
      发布前关闭），**不得输出空缺项、不得伪造缺证据**；任一侧 insufficient
      （真缺证据）→不可判定逐字列缺哪侧；
    - 两侧证据须属同一审查对象（同一材料/项目标识）；分属不同项目（甲/乙）→
      不可判定（F09），不拼成 hit。
    """
    det = rule["illegal_detection"]
    source = _rule_source(rule)
    obj_pat = det.get("object_token_pattern")
    sup, sup_anchor = _v16_01_supervision_side(a3_findings, evidence, det)
    con, con_anchor = _v16_01_consent_side(evidence, det)

    # 证据对象对齐：两侧均取到文本锚且各标不同项目/主体 → 非同一审查对象（F09）。
    obj_mismatch_txt = None
    if sup_anchor[0] in ("text", "conflict") and con_anchor[0] in ("text", "conflict"):
        sup_tk = _object_tokens(_material_text(sup_anchor[1][0]), obj_pat)
        con_tk = _object_tokens(_material_text(con_anchor[1][0]), obj_pat)
        if sup_tk and con_tk and not (sup_tk & con_tk):
            obj_mismatch_txt = ("监理侧材料对象（%s）与同意侧材料对象（%s）分属不同审查对象"
                                % ("、".join(sorted(sup_tk)), "、".join(sorted(con_tk))))

    if obj_mismatch_txt is not None:
        mid, page, quote = _side_anchor(sup_anchor)
        return _undecidable_finding(
            task, rule["id"], _ILLEGAL_KIND,
            "不可判定：%s，无法证明同一审查对象（同一材料/同一项目标识），"
            "不能据此判命中或未命中（缺：两证据侧属同一审查对象的证明）"
            % obj_mismatch_txt, source, mid)

    if sup == "risk" and con == "risk":
        mid, page, quote = _side_anchor(sup_anchor)
        message = ("命中：监理审查侧与发包人书面同意侧均显示风险（监理侧线索「%s」；"
                   "同意侧线索「%s」），两侧独立证据齐备，请人工复核是否属第十六条"
                   "视为（一）情形" % (_side_word(sup_anchor), _side_word(con_anchor)))
        return BsFinding(
            task_id=task.id, material_id=mid, item_id=rule["id"],
            kind=_ILLEGAL_KIND, status=STATUS_HIT, message=message,
            quote=quote, page=page, source=source)
    if sup == "legal" and con == "legal":
        mid, page, quote = _side_anchor(sup_anchor)
        message = ("未命中：监理审查与发包人书面同意两侧均显示合规（监理侧「%s」；"
                   "同意侧「%s」）" % (_side_word(sup_anchor), _side_word(con_anchor)))
        return BsFinding(
            task_id=task.id, material_id=mid, item_id=rule["id"],
            kind=_ILLEGAL_KIND, status=STATUS_MISS, message=message,
            quote=quote, page=page, source=source)

    # 混合态（一侧风险、一侧合规）：明确状态 + 可理解说明，命中语义待人工裁决。
    if sup in ("risk", "legal") and con in ("risk", "legal"):
        risk_side = "监理审查侧" if sup == "risk" else "发包人书面同意侧"
        legal_side = "发包人书面同意侧" if sup == "risk" else "监理审查侧"
        risk_anchor = sup_anchor if sup == "risk" else con_anchor
        mid, page, quote = _side_anchor(risk_anchor)
        return _undecidable_finding(
            task, rule["id"], _ILLEGAL_KIND,
            "不可判定：混合态（%s风险、%s合规），两侧均已有明确表述；"
            "命中语义待人工裁决（R05 发布前关闭），不能据此判命中或未命中——"
            "本条不属「缺证据」"
            % (risk_side, legal_side), source, mid)

    missing = []
    detail = []
    if sup == "insufficient":
        missing.append("supervision_review")
        if sup_anchor[0] == "conflict":
            detail.append("监理侧材料同时出现审查与未审查表述（指向冲突）")
        else:
            detail.append("监理侧证据不足（A3-01 RC-09 无结论且材料文本无明示）")
    if con == "insufficient":
        missing.append("employer_written_consent")
        if con_anchor[0] == "conflict":
            detail.append("同意侧材料同时出现同意与未同意表述（指向冲突）")
        else:
            detail.append("发包人书面同意侧证据不足（材料文本无明示）")
    anchor_mats = [a for a in (sup_anchor, con_anchor) if a[0] in ("text", "conflict")]
    mid = anchor_mats[0][1][0].id if anchor_mats else None
    return _undecidable_finding(
        task, rule["id"], _ILLEGAL_KIND,
        "不可判定：监理审查与发包人书面同意为两个独立证据侧，任一侧证据不足即"
        "不可判定（缺：%s）——%s" % ("、".join(_EVIDENCE_LABELS[k] for k in missing),
                                    "；".join(detail)),
        source, mid, missing=missing)


def _run_v16_02(task, general: list, subs_labor: list, pool: list,
                rule: dict) -> BsFinding:
    """V16-02：未依法签订或未遵循承包合同原则要求。

    - 命中=总包合同与分包合同在场 + 明示违背总包合同原则（或主要机械设备/设施由
      分包/劳务侧承担）+ 无设备设施说明在场；
    - 未命中=对照在场 + 原则一致明示（违背与一致同在场 → 指向冲突，不可判定）；
    - 不可判定=缺合同对照或现场材料。
    """
    det = rule["illegal_detection"]
    source = _rule_source(rule)
    guard_c = _guard_lists(det.get("clause_polarity_guard"))
    gc = _page_match_breaks(general, det["general_contract_patterns"])
    sc = _page_match_breaks(subs_labor, det["subcontract_patterns"])
    violation = _page_match_breaks(pool, det["violation_patterns"])
    consistency = _page_match_breaks(pool, det["consistency_patterns"])
    explanation = _page_match_breaks(pool, det["explanation_patterns"])

    if gc is None or sc is None:
        missing = [k for k, side in (("general_contract", gc),
                                     ("subcontract", sc)) if side is None]
        return _undecidable_finding(
            task, rule["id"], _ILLEGAL_KIND,
            "不可判定：缺合同对照，无法比对分包与总包合同原则%s"
            % ("（缺：%s）" % "、".join(_EVIDENCE_LABELS[k] for k in missing)),
            source, (gc or sc)[0].id if (gc or sc) else None, missing=missing)
    # 极性守卫：违背表述被否定/条件/制度前缀支配 → 不作既成事实。
    if violation is not None and _guard_hit(violation[2], violation[3], guard_c) is not None:
        violation = None
    if violation is not None and consistency is not None:
        return _undecidable_finding(
            task, rule["id"], _ILLEGAL_KIND,
            "不可判定：违背总包合同原则与符合总包合同原则的表述同在场，对照证据"
            "指向冲突", source, violation[0].id)
    if violation is not None and explanation is None:
        mat, page, quote, word = violation
        return _hit_finding(
            task, rule["id"], mat, page, quote,
            "命中：总包合同与分包合同在场，明示违背总包合同原则（「%s」），且无"
            "设备设施说明在场，请人工复核是否属第十六条视为（二）情形" % word, source)
    if consistency is not None:
        mat, page, quote, word = consistency
        return _miss_finding(
            task, rule["id"], mat, page, quote,
            "未命中：合同对照在场且明示符合总包合同原则（「%s」），未见违背明示" % word,
            source)
    missing = [k for k, side in (("principle_comparison", violation),
                                 ("equipment_facility_explanation", explanation))
               if side is None]
    return _undecidable_finding(
        task, rule["id"], _ILLEGAL_KIND,
        "不可判定：合同对照在场但未见原则违背或一致明示，现场材料不足"
        "（缺：%s）" % "、".join(_EVIDENCE_LABELS[k] for k in missing),
        source, (violation or sc)[0].id if violation else sc[0].id, missing=missing)


def _run_v16_03(task, subs_labor: list, pool: list, rule: dict) -> BsFinding:
    """V16-03：未设现场管理机构和派驻人员有效管理。

    - 命中=分包范围在场 + 明示未设现场管理机构/未派驻人员（否定性表述，含极性
      冲突守卫）+ 无有效管理证据；
    - 未命中=管理机构+派驻人员+管理证据齐且指向分包侧自身（同主体明示）；
    - 不可判定=缺任一侧；词表用本条 illegal_detection 独立字段，与 T15-04
      （materials_detection）互不引用。
    """
    det = rule["illegal_detection"]
    source = _rule_source(rule)
    scope = _page_match_breaks(subs_labor, det["subcontract_scope_patterns"])
    no_mgmt = _clause_match_breaks(pool, det["no_management_patterns"],
                                   det.get("affirm_conflict_patterns"))
    dual_neg = _page_match_breaks(pool, det.get("dual_negation_patterns", []))
    org = _page_match(pool, det["management_org_words"])
    deployed = _page_match(pool, det["deployed_words"])
    mgmt_evidence = _page_match(pool, det["management_evidence_words"])
    self_dir = _page_match_breaks(pool, det["self_direction_patterns"])

    if scope is None:
        return _undecidable_finding(
            task, rule["id"], _ILLEGAL_KIND,
            "不可判定：未见分包范围明示（缺：分包范围）", source)
    if dual_neg is not None:
        # 双重否定（「并非未设立现场管理机构」）机器不裁：不作肯定或否定证据（F05）。
        return _undecidable_finding(
            task, rule["id"], _ILLEGAL_KIND,
            "不可判定：材料存在双重否定表述（「%s」），机器无法可靠判定未设机构/未派驻"
            "的极性，不作肯定或否定证据，请人工复核" % dual_neg[3],
            source, dual_neg[0].id)
    if no_mgmt is not None and mgmt_evidence is not None:
        return _undecidable_finding(
            task, rule["id"], _ILLEGAL_KIND,
            "不可判定：未设机构/未派驻的否定性表述与管理证据同在场，管理归属指向"
            "冲突，不能据此判命中或未命中", source, no_mgmt[0].id)
    if no_mgmt is not None:
        mat, page, clause, word = no_mgmt
        return _hit_finding(
            task, rule["id"], mat, page, clause[:60],
            "命中：分包范围在场且明示未设现场管理机构/未派驻人员（「%s」），未见"
            "有效管理证据，请人工复核是否属第十六条视为（三）情形" % word, source)
    if org is not None and deployed is not None and mgmt_evidence is not None \
            and self_dir is not None:
        mat, page, quote, word = self_dir
        return _miss_finding(
            task, rule["id"], mat, page, quote,
            "未命中：管理机构、派驻人员与管理证据齐备且指向分包侧自身（「%s」），"
            "未见未设机构/未派驻的否定性明示" % word, source)
    missing = [k for k, side in (("site_management_org", org),
                                 ("deployed_personnel", deployed),
                                 ("management_evidence", mgmt_evidence)) if side is None]
    base = ("不可判定：分包范围在场但未见未设机构/未派驻的否定性明示，也无法证明"
            "管理机构、派驻人员与管理证据齐备且指向分包侧自身（缺：%s）"
            % "、".join(_EVIDENCE_LABELS[k] for k in missing))
    if no_mgmt is not None and self_dir is not None:
        base = ("不可判定：否定性表述与分包侧自管表述同在场，管理归属指向冲突"
                "（不能据此判命中或未命中）")
    anchor = no_mgmt or org or deployed or mgmt_evidence or self_dir or scope
    return _undecidable_finding(task, rule["id"], _ILLEGAL_KIND, base, source,
                                anchor[0].id, missing=missing)


def _labor_subcontract_clue(materials: list, patterns: list):
    """排除与人员签订的劳务合同，不把用工合同当作企业间分包合同。"""
    compiled = _compile_res(patterns)
    employment = re.compile(r"(?:劳务人员|务工人员|劳动者|员工|工人)[^。；;]{0,16}(?:签订|签署|订立)[^。；;]{0,6}$")
    for material in materials:
        for page_no, text in _norm_pages_breaks(material):
            for pattern in compiled:
                for match in pattern.finditer(text):
                    prefix = text[max(0, match.start() - 40):match.start()]
                    if match.group(0) == "劳务合同" and employment.search(prefix):
                        continue
                    return (material, page_no, text[max(0, match.start() - 20):match.end() + 20], match.group(0))
    return None


def _run_l17_01(task, pool: list, rule: dict) -> BsFinding:
    """L17-01：劳务企业计取主要材料或周转材料款。

    - 命中=劳务主体 + 计取动词 + 主要材料(费|款)/周转材料(费|款) 施受邻接
      （同分句，两种语序都进 json）；**禁裸词**：「管理费按主要材料费的3%计取」
      类百分比条款的宾语是管理费，不得点亮（百分比排除模式 + 施受结构双防线）；
    - 未命中=劳务合同 + 费用明示仅为劳务费/劳务报酬 + 无材料费计取明示；
    - 不可判定=缺劳务合同或缺费用侧。
    """
    det = rule["illegal_detection"]
    source = _rule_source(rule)
    guard_c = _guard_lists(det.get("clause_polarity_guard"))
    labor_contract = _labor_subcontract_clue(pool, det["labor_contract_words"])
    hit_rel = _clause_match_breaks(pool, det["hit_relation_patterns"],
                                   det.get("percentage_exclusion_patterns"))
    fee_only = _page_match_breaks(pool, det["labor_fee_only_patterns"])

    if labor_contract is None:
        return _undecidable_finding(
            task, rule["id"], _ILLEGAL_KIND,
            "不可判定：未发现企业间劳务合作合同线索，无法判断该劳务情形是否适用；"
            "如有相关合同，请提供或人工核实", source, missing=["labor_contract"])
    # 极性守卫：命中子串被否定/条件前缀支配（「不得计取…」「若…则应纠正」）→ 不作
    # 既成事实；命中串自身含否定前缀（「不…计取」）同样作废（F02/F03）。
    hit_guarded = False
    if hit_rel is not None:
        if _guard_hit(hit_rel[2], hit_rel[3], guard_c) is not None:
            hit_guarded = True
            hit_rel = None
    if hit_rel is not None:
        mat, page, clause, word = hit_rel
        return _hit_finding(
            task, rule["id"], mat, page, clause[:60],
            "命中：劳务主体计取主要材料/周转材料款项的施受关系明示（「%s」），"
            "请人工复核是否属第十七条（一）情形" % clause[:40], source)
    if fee_only is not None:
        mat, page, quote, word = fee_only
        return _miss_finding(
            task, rule["id"], mat, page, quote,
            "未命中：劳务合同在场且费用明示仅为劳务费/劳务报酬（「%s」），"
            "未见主要材料/周转材料款计取明示" % word, source)
    guard_txt = "；否定/条件/制度前缀下的表述不作为既成事实" if hit_guarded else ""
    return _undecidable_finding(
        task, rule["id"], _ILLEGAL_KIND,
        "不可判定：劳务合同在场但费用侧不明，未见劳务主体计取材料款的施受关系，"
        "也未见纯劳务计费明示（缺：主要材料费计取、周转材料费计取或劳务费用约定）；"
        "按材料费百分比计取管理费类条款不构成计取材料款证据，请人工复核%s" % guard_txt,
        source, missing=list(rule["required_evidence"]))


def _run_l17_02(task, labor: list, packet: list, rule: dict, *, bundled_contracts=()) -> BsFinding:
    """L17-02：劳务企业自行承担方案检测测量档案及质量安全管理（双通道）。

    责任方向判定语义与旧 _L17_HIT_RES/_L17_MISS_RES/_L17_LABOR_SIGN_RE 一致，
    但正则迁移进 json illegal_detection（旧函数硬编码不动，本实现读 json）；
    修复跨行拼接缺陷：页文本换行先归一为句界（_norm_pages_breaks），窗口类
    [^。；;，,] 不再吞并换行。分支：劳务侧责任→命中；署名通道→命中；
    承包人侧责任→未命中；双通道都无→不可判定。
    """
    det = rule["illegal_detection"]
    source = _rule_source(rule)
    guard_c = _guard_lists(det.get("clause_polarity_guard"))

    hit_quote = hit_mat = hit_page = None
    hit_guarded = False
    miss_quote = miss_mat = miss_page = None
    for m in labor:
        for page_no, text in _norm_pages_breaks(m):
            for clause in re.split(r"[。；;]", text):
                if not clause:
                    continue
                hw = _clause_word(det["labor_responsibility_patterns"], clause)
                if hw:
                    # 极性守卫：劳务侧责任被否定/条件前缀支配（「不负责施工方案编制」）→ 作废。
                    if _guard_hit(clause, hw, guard_c) is not None:
                        hit_guarded = True
                    elif hit_quote is None:
                        hit_quote, hit_mat, hit_page = clause[:40], m, page_no
                mw = _clause_word(det["contractor_responsibility_patterns"], clause)
                if mw and miss_quote is None and _guard_hit(clause, mw, guard_c) is None:
                    miss_quote, miss_mat, miss_page = clause[:40], m, page_no
    # 分包合同可能是包含附件的整册；仅将其用于查找署名，不改变合同分类。
    sign = _page_match_breaks(packet + list(bundled_contracts), det["labor_sign_patterns"])

    if hit_quote:
        return _hit_finding(
            task, rule["id"], hit_mat, hit_page, hit_quote,
            "命中：劳务合同约定方案编制/试验检测/测量/档案/质安责任由劳务方承担"
            "（第十七条（二）情形线索），请人工复核", source)
    if sign is not None:
        return _hit_finding(
            task, rule["id"], sign[0], sign[1], sign[2],
            "命中：报审包方案文件编制署名为劳务方（第十四条「分包人应当自行编制"
            "施工方案」反向证据线索），请人工复核", source)
    if miss_quote:
        return _miss_finding(
            task, rule["id"], miss_mat, miss_page, miss_quote,
            "未命中：劳务合同明确方案编制/检测等责任由承包人（总包）侧承担", source)
    if not labor and not packet and not bundled_contracts:
        return _undecidable_finding(
            task, rule["id"], _ILLEGAL_KIND,
            "不可判定：无劳务合作合同且无报审资料包（缺：责任条款与编制署名双证据）",
            source,
            # 缺的是「双证据同时缺」：责任条款在劳务合同、编制署名在报审包
            missing=["management_responsibility", "construction_plan_owner"])
    guard_txt = "；否定/条件前缀下的表述不作为既成事实" if hit_guarded else ""
    return _undecidable_finding(
        task, rule["id"], _ILLEGAL_KIND,
        "不可判定：现有材料未找到可核实的劳务方方案编制责任条款或署名"
        "（缺：责任条款或编制主体署名）%s" % guard_txt, source,
        missing=["management_responsibility", "construction_plan_owner"])


_ILLEGAL_HANDLERS = {
    "W16-01": lambda t, g, sl, p, l, pk, a3, nl, qe, r: _run_w16_01(t, p, qe, r),
    "W16-02": lambda t, g, sl, p, l, pk, a3, nl, qe, r: _run_w16_02(t, nl, r),
    "W16-03": lambda t, g, sl, p, l, pk, a3, nl, qe, r: _run_w16_03(t, g, sl, p, r),
    "W16-04": lambda t, g, sl, p, l, pk, a3, nl, qe, r: _run_w16_04(t, p, r),
    "W16-05": lambda t, g, sl, p, l, pk, a3, nl, qe, r: _run_w16_05(t, p, r),
    "W16-06": lambda t, g, sl, p, l, pk, a3, nl, qe, r: _run_w16_06(t, p, r),
    "V16-01": lambda t, g, sl, p, l, pk, a3, nl, qe, r: _run_v16_01(t, p, a3, r),
    "V16-02": lambda t, g, sl, p, l, pk, a3, nl, qe, r: _run_v16_02(t, g, sl, p, r),
    "V16-03": lambda t, g, sl, p, l, pk, a3, nl, qe, r: _run_v16_03(t, sl, p, r),
    "L17-01": lambda t, g, sl, p, l, pk, a3, nl, qe, r: _run_l17_01(t, p, r),
    "L17-02": lambda t, g, sl, p, l, pk, a3, nl, qe, r: _run_l17_02(
        t, l, pk, r, bundled_contracts=[m for m in sl if m.role == "sub"]),
}


def run_illegal_subcontract_check(task, materials: list, a3_findings: list = None,
                                  negative_list_findings: list = None,
                                  qualification_evidence: dict = None, *,
                                  scope_policy_enabled: bool = False,
                                  general_source_state: str | None = None) -> list:
    """A1-RULE-04：第十六条七类 + 第十六条视为四类 + 第十七条三类 违法分包纯内核。

    总包合同在场时固定返回**恰好 14 条** finding，顺序 W16-01→W16-07、V16-01→V16-04、
    L17-01→L17-03（article 字段驱动，顺序=目录顺序）；kind 统一 illegal_subcontract。
    由 `service.run_a1_checks` 接线（service.py:1630）。

    证据范围闸（口径修正）：`requires_general_contract` 的规则（W16-03「分包合同明确不得
    分包的工程」、V16-02「未依法签订或未遵循承包合同原则要求」）证据只能来自**总包合同**。
    任务未上传总包合同时这些规则**整体不在本次范围**（不产出 finding，改由
    `reporting.scope_statements` 声明一句），否则会把业主并未收集的资料渲染成一堆「缺件」；
    总包合同在场时按原口径逐条判定。

    a3_findings / negative_list_findings 为已计算的 BsFinding 列表（显式传入，
    不调数据库、不重复执行 A3/负面清单）；qualification_evidence 为可选 dict
    （键 business_license/qualification/safety_permit，任意真值=合格在场）。
    开放条款（目录 open_term=true）恒不可判定仅登记，不参与命中/未命中统计。

    材料筛选口径与既有内核一致：parse_state == "done" 且文本非空；不得分包条款
    只在总包件（role=general）检索（R01/R02 归因纪律），其余侧在证据池检索。
    """
    general = [m for m in materials if m.role == "general"
               and m.parse_state == "done" and _material_text(m).strip()]
    if not _optional_general_source_readable(
            materials, scope_policy_enabled=scope_policy_enabled,
            general_source_state=general_source_state):
        general = []
    subs = [m for m in materials if m.role == "sub"
            and m.parse_state == "done" and _material_text(m).strip()]
    labor = [m for m in materials if m.role == "labor"
             and m.parse_state == "done" and _material_text(m).strip()]
    packet = [m for m in materials if m.role == "packet"
              and m.parse_state == "done" and _material_text(m).strip()]
    pool = general + subs + labor + packet
    subs_labor = subs + labor

    findings = []
    for rule in _illegal_rules():
        # 证据范围闸：未上传总包合同 → 证据只能来自总包原文的规则整体不进本次范围
        # （不产出 finding；范围由 reporting.scope_statements 在报告/意见书上声明一句）。
        if rule.get("requires_general_contract") and not general:
            continue
        if rule.get("open_term"):
            findings.append(_open_term_finding(task, rule))
            continue
        handler = _ILLEGAL_HANDLERS[rule["id"]]
        findings.append(handler(task, general, subs_labor, pool, labor, packet,
                                a3_findings, negative_list_findings,
                                qualification_evidence, rule))
    return findings


def _finding_object_tokens(f) -> set:
    """从 finding 自身可测字段推导对象 token（自包含，无需外部 metadata）。

    取 quote（引文）与 message 中可直接抽取的项目/主体标识 token；两者均无标注时
    返回空集=未标注对象（按「无法证明不同对象」保守处理，不隐藏风险）。
    """
    text = "%s\n%s" % (getattr(f, "quote", "") or "", getattr(f, "message", "") or "")
    return _object_tokens(text)


def illegal_subcontract_relations(findings: list,
                                  negative_list_ids: list = None) -> dict:
    """违法分包 14 条去重元数据（公共纯函数；不写数据库、不加模型字段）。

    返回 {item_id: {"primary": bool, "related_rule_ids": list[str],
    "dedup_owner": str | None}}：
    - W16-05 与 L17-01/L17-02 同为命中**且同对象**时：主 finding=W16-05
      （primary=True，related_rule_ids=实际命中的 L17 条目）；命中的 L17 条目
      primary=False、dedup_owner="W16-05"、related_rule_ids=["W16-05"]；
      对象不可认定同一（finding 各自标注不同项目/主体，如甲项目 vs 乙项目）→
      **不去重**，L17 保持 primary=True、不隐藏风险（F13）；
    - W16-05 非命中时 L17 各自独立（primary=True，dedup_owner=None）；
    - W16-02 命中时 related_rule_ids=negative_list_ids（显式传入的负面清单条目
      id 列表）；
    - 其余条目 primary=True、related_rule_ids=[]、dedup_owner=None；
    - 只含 findings 中出现的 item_id。关联规则清单读目录
      W16-05 illegal_detection.dedup_related（规则=数据）。
    对象 token 从 finding.quote（+message）纯文本层抽取（`_finding_object_tokens`）；
    任一侧空集=未标注对象→按可认定同一处理（保守不隐藏）；两侧非空且无交集=不同对象。
    R05 负责映射持久化/接口/报告去重，本函数不做任何落库。
    """
    by_id = {}
    for f in findings:
        by_id[getattr(f, "item_id", None)] = f
    w16_05 = by_id.get("W16-05")
    w16_05_hit = w16_05 is not None and w16_05.status == STATUS_HIT
    w16_05_tk = _finding_object_tokens(w16_05) if w16_05_hit else set()
    related_l17 = []
    if w16_05_hit:
        det = _transfer_rule("W16-05").get("illegal_detection", {})
        related_l17 = [rid for rid in det.get("dedup_related", [])
                       if rid in by_id and by_id[rid].status == STATUS_HIT
                       and _same_object_tokens(w16_05_tk, _finding_object_tokens(by_id[rid]))]

    result = {}
    for item_id, f in by_id.items():
        if item_id is None:
            continue
        if item_id == "W16-05":
            result[item_id] = {"primary": True,
                               "related_rule_ids": list(related_l17),
                               "dedup_owner": None}
        elif item_id in ("L17-01", "L17-02"):
            if w16_05_hit and f.status == STATUS_HIT \
                    and _same_object_tokens(w16_05_tk, _finding_object_tokens(f)):
                result[item_id] = {"primary": False,
                                   "related_rule_ids": ["W16-05"],
                                   "dedup_owner": "W16-05"}
            else:
                result[item_id] = {"primary": True, "related_rule_ids": [],
                                   "dedup_owner": None}
        elif item_id == "W16-02" and f.status == STATUS_HIT:
            result[item_id] = {"primary": True,
                               "related_rule_ids": list(negative_list_ids or []),
                               "dedup_owner": None}
        else:
            result[item_id] = {"primary": True, "related_rule_ids": [],
                               "dedup_owner": None}
    return result


# ==================== R05 统一接线：旧/新同名结论归并 ====================


def run_open_term_registrations(task, findings: list) -> list:
    """补登记「目录标记 open_term 但无内核承载」的兜底条款（BS-P1-11）。

    背景：第十六/十七条的兜底条款由 `run_illegal_subcontract_check` 的
    `open_term` 分支登记，但**第十五条（十）T15-10 无人承载**——转包范围内核固定
    产 2 条、转包关系内核固定产 5 条，都不遍历它（`test_bs_a1_wiring_r05.py` 曾把
    这个缺口写成断言「T15-10 not in ids」）。结果报告里少一条，与对外所说
    「4 条兜底条款仅登记」不一致。

    为什么不塞进两个既有内核：它们各有「恰好 N 条」的形状锁测试，插入会连带改动
    契约；补登记是**纯追加**语义，独立成函数即可零破坏。

    判定依据全来自目录（`open_term` 字段 + 已产出 item_id 集合），不硬编码 ID 清单：
    任何已存在的 item_id 跳过，将来新增兜底条款亦自动纳入。
    """
    existing = {getattr(f, "item_id", None) for f in findings}
    registered = []
    for rule in load_transfer_rules()["rules"]:
        if not rule.get("open_term") or rule["id"] in existing:
            continue
        registered.append(_open_term_finding(task, rule))
    return registered


def supersede_dependency_findings(findings: list) -> tuple:
    """同一 item_id 上，旧 `a1_dependency` 结论由新 `illegal_subcontract` 结论取代。

    为什么归并在本模块内：kind 常量（`_DEPENDENCY_KIND`/`_ILLEGAL_KIND`）只在本文件定义，
    编排层引用它们会变成跨模块私有符号引用（AGENTS 红线），故以公共函数暴露。

    取代关系**结构推导**（同 item_id 两类内核都产出 → 新口径生效），不硬编码 ID 清单，
    与「规则=数据」一致；依据见本文件 A1-RULE-04 记账节：新 V16-01 用「两侧独立、
    缺任一侧不可判定」比旧「或逻辑从严」更保守，新 L17-02 已修复跨行拼接缺陷。

    返回 `(保留列表, 取代条数)`；取代条数由编排写入事件 payload，不静默丢弃。
    """
    illegal_ids = {getattr(f, "item_id", None) for f in findings
                   if getattr(f, "kind", None) == _ILLEGAL_KIND}
    kept = [f for f in findings
            if not (getattr(f, "kind", None) == _DEPENDENCY_KIND
                    and getattr(f, "item_id", None) in illegal_ids)]
    return kept, len(findings) - len(kept)
