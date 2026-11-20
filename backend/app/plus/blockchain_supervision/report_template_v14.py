"""V1.4 正式审查意见投影。

结构：结论卡 + 函件式主题章节 + 问题补正表 + 8 表附件。
只读编排，复用 V1.3 的既有结论和证据，不重算核查结果，不生成审批决定。
"""
from __future__ import annotations

import re
from copy import deepcopy
from datetime import date

from . import report_template_v13 as v13

TEMPLATE_ID = "v1.4"
SCHEMA_VERSION = "bs.review-opinion-v14.v1"
TEMPLATE_VERSION = "V1.4"
MAPPING_VERSION = "2026-11-19.12"
UNKNOWN = "材料未载明"
_PLAIN_LABELS = {
    "法定签字链": "报审表法定签署",
    "集团报审签字链": "集团报审流程签署",
}
_PLAIN_SOURCES = {
    "A3-02-legal": "《上海市公路工程施工分包管理实施细则》（沪交行规〔2026〕8号）附件3/附件4 签字栏",
    "A3-02-group": "集团分包报审表签字栏（项目经理/专业监理/总监/指挥部/直属单位）",
}


def _plain_label(label):
    return _PLAIN_LABELS.get(str(label or "").strip(), label)


def _plain_source(item_id, source):
    if item_id == "A3-02-legal":
        for attachment in ("附件3", "附件4"):
            other = "附件4" if attachment == "附件3" else "附件3"
            if attachment in str(source) and other not in str(source) and "附件3/4" not in str(source):
                return _PLAIN_SOURCES[item_id].replace("附件3/附件4", attachment)
    return _PLAIN_SOURCES.get(str(item_id or "").strip(), source)


# 内部状态词不得进入正式文本（设计 2026-11-18 §7.4）：机器前缀直接去掉，
# “不可判定/无法判定”统一表述为“待核实”，“未命中”改“未见”。
_MACHINE_PREFIX_RE = re.compile(r"^\s*(?:命中|未命中|不可判定|无法判定)\s*[：:]\s*")
_PLAIN_MESSAGE_WORDS = (("无法判定", "待核实"), ("不可判定", "待核实"), ("未命中", "未见"))
_BANNED_WORDING = ("1/21", "未命中", "无法判定", "不可判定", "待补正")


def _plain_message(value):
    text = _text(value, "未载明事实")
    text = _MACHINE_PREFIX_RE.sub("", text)
    for old, new in _PLAIN_MESSAGE_WORDS:
        text = text.replace(old, new)
    return text

# 8 表附件：审查项 / 审查要点 / 归属维度 / 命中关键词 / 是否有自动内核
_SHEETS = (
    {
        "code": "legality",
        "title": "一、分包合法性审查",
        "note": "判定：分包工程不得突破《公路工程施工分包负面清单（2024年版）》；桥梁、隧道主体及关键性工程不得分包；分包须发包人书面同意。",
        "rows": (
            ("分包范围合规性", "分包内容是否落在负面清单以外；是否涉及桥梁/隧道主体及关键性工程", "compliance", ("负面清单", "分包范围"), True, ("W16-02", "NL-*")),
            ("发包人书面同意", "总承包合同是否允许分包；是否已取得发包人书面同意文件", "compliance", ("书面同意", "审批", "信息表"), True),
            ("资质等级匹配", "分包人资质是否覆盖本工程专业要求（单位工程有资质要求的须专业承包资质）", "qualification_credit", ("资质",), True),
            ("安全生产许可行业", "证照行业与合同明示施工作业是否对应；资质专业、等级、承包规模及证照有效性分别核查", "qualification_credit", ("安许", "许可行业"), True, ("A2-09",)),
            ("非整体转包/肢解", "是否存在整体转包、肢解分包迹象", "compliance", ("转包", "肢解", "整体"), True),
            ("禁止再分包", "合同是否明确禁止二次分包，是否出现分包人再次分包线索", "compliance", ("再分包", "二次分包"), True, ("W16-06",)),
            ("以劳务合作名义分包", "是否以劳务合作名义进行施工分包（计取主材/周转材、未实施有效管理）", "compliance", ("劳务合作",), True, ("W16-05",)),
        ),
    },
    {
        "code": "personnel_org",
        "title": "二、现场管理机构与人员锁定审查",
        "note": "判定：承包人、分包人均须设现场管理机构；主要人员须为本单位人员（劳动合同+社保+工资流水），人证一致。",
        "rows": (
            ("承包人现场机构", "承包人是否设现场管理机构，是否实质管理（非只收管理费）", "compliance", ("现场管理机构", "派驻"), True, ("T15-04",)),
            ("分包人现场机构", "分包人是否设立现场管理机构（项目负责人、技术、质量、安全、计量、财务）", "personnel", ("现场管理机构", "项目负责人"), True, ("A2-07",)),
            ("劳动合同与社保", "主要人员是否提供劳动合同+社保关系证明", "personnel", ("劳动合同", "社保"), True, ("A2-01",)),
            ("特种作业工种覆盖", "明确分包工法所需工种与名单、明示证书类型候选是否对应（数量仅提示）", "personnel", ("特种作业", "工种"), True, ("A2-08",)),
            ("人员证件字段与归属", "名单声明与所附证照字段逐人对照；身份字段对应仍须核对原件与岗位适用性", "personnel", ("人员证件",), True, ("A2-10",)),
            ("技术负责人职称与规模", "已提交工程技术类职称等级与本次分包规模是否对应；原件真实性、执业专业及其他岗位资格另行核对", "personnel", ("职称",), True, ("A2-14",)),
            ("执业资格与到位", "人员执业证是否齐全；是否约定到位率与更换审批", "personnel", ("执业", "证书", "到岗", "更换"), True),
            ("信用平台状态", "分包人上海市交通建设市场信用平台状态是否正常", "qualification_credit", ("信用平台", "信用档案"), False),
        ),
    },
    {
        "code": "price",
        "title": "三、价款、计量与审计审查",
        "note": "判定：计价口径清晰；计量流程含分包自检→承包人复检→监理抽检签认→发包人备案；国有资金项目最终结算以审计/财政评审为准。",
        "rows": (
            ("计价口径清晰", "单价/总价是否明确含增值税、管理费、安全生产费", "contract_clauses", ("计价", "含税", "增值税", "管理费"), False),
            ("背靠背支付条款", "是否约定业主付款后按比例支付；是否排除承包人怠于主张债权的情形", "contract_clauses", ("背靠背", "支付"), False),
            ("计量流程完整", "是否执行自检→复检→监理抽检签认流程", "contract_clauses", ("计量", "签认"), False),
            ("最终结算以审计为准", "是否约定以审计/财政评审为最终结算依据，多付可扣回", "contract_clauses", ("审计", "财政评审", "结算"), False),
            ("资金专户/财务监理", "是否执行上海资金专户与财务监理要求（市级财政项目强制）", "funds_payment", ("专户", "财务监理"), False),
        ),
    },
    {
        "code": "quality",
        "title": "四、质量、安全、环保与保修审查",
        "note": "判定：分包合同质量目标、安全标准、环保要求、缺陷责任期不得低于总承包合同；安全生产费单列专款专用。",
        "rows": (
            ("质量目标不降低", "质量目标是否不低于总承包合同及招标文件要求", "quality_safety_progress", ("质量目标",), False),
            ("质量连带责任", "是否写明分包人就分包工程与承包人向发包人承担连带责任", "contract_clauses", ("连带责任",), False),
            ("缺陷责任期对齐", "缺陷责任期是否不少于总承包合同约定", "contract_clauses", ("缺陷责任期", "保修"), False),
            ("安全生产费单列", "分包合同是否单独约定安全生产费及金额/计算方式；公路工程1.5%为工程层建筑安装造价提取标准，分包不再重复提取，实际支付与专款专用另核", "quality_safety_progress", ("安全生产费",), True, ("RC-A10-cost",)),
            ("环保与水保条款", "是否含扬尘、弃土、表土保护等环保违约条款", "quality_safety_progress", ("环保", "水保", "扬尘"), False),
            ("监理指令执行", "是否明确分包人须执行监理整改/暂停指令，逾期可上报清退", "quality_safety_progress", ("监理指令", "整改", "暂停"), False),
        ),
    },
    {
        "code": "schedule",
        "title": "五、工期与违约责任审查",
        "note": "判定：关键节点与承包人总体施组一致；违约金合理封顶；合同解除情形覆盖转包、欠薪、持续滞后。",
        "rows": (
            ("关键节点一致", "节点工期是否与承包人总体施工组织设计一致", "quality_safety_progress", ("工期", "节点"), False),
            ("违约金合理封顶", "逾期违约金是否按日计取并设上限", "contract_clauses", ("违约金",), False),
            ("合同解除情形", "是否覆盖转包、重大质量安全、群体欠薪、节点持续滞后等情形", "contract_clauses", ("合同解除", "解除"), False),
        ),
    },
    {
        "code": "wages",
        "title": "六、农民工工资与信用管理审查",
        "note": "判定：实行实名制、专用账户、总包代发；拖欠工资一经查实可从工程款直接代付并扣减信用评价分。",
        "rows": (
            ("实名制与专用账户", "是否实行农民工实名制、工资专用账户、总包代发", "wages", ("实名制", "工资专户", "专用账户", "代发"), False),
            ("工资代付机制", "是否约定发包人/承包人可从工程款中直接代付拖欠工资", "wages", ("代付", "拖欠", "工资"), False),
            ("信用档案联动", "是否约定违约行为记入上海交通建设市场信用档案", "qualification_credit", ("信用档案", "黑名单"), False),
            ("业绩备案", "是否约定合规分包业绩由发包人与承包人联合出证明并系统备案", "qualification_credit", ("业绩", "备案"), False),
        ),
    },
)

_MATERIAL_ROWS = (
    ("1", "分包合同正本（含修改对照稿）", "分包合同", "必备"),
    ("2", "发包人书面同意函（或总承包合同分包授权条款）", "书面同意", "无则否决"),
    ("3", "分包人营业执照、资质证书、安全生产许可证", "营业执照|资质|安全生产许可证", "须在有效期"),
    ("4", "主要人员执业证+劳动合同+社保记录+工资流水", "执业|劳动合同|社保", "人证一致"),
    ("5", "设备清单及权属/租赁证明、检定证书", "设备", ""),
    ("6", "安全生产费使用计划", "安全生产费", "报监理审批"),
    ("7", "农民工工资专用账户及代发协议", "工资专户|代发", ""),
    ("8", "专项施工方案（待监理审查同意）", "施工方案", ""),
    ("9", "分包管理台账（承包人建）", "台账", ""),
    ("10", "《公路工程施工分包负面清单》对照说明", "负面清单", "说明不突破"),
)


_SIGNING_NAV = (
    ("一、分包合法性审查",
     "总包合同及招标文件、发包人书面同意、分包合同、营业执照/资质/安许、负面清单对照说明、报审信息表及监理审查",
     "禁止转包和再分包、违约责任、合同解除权",
     "未取得发包人书面同意或涉及负面清单关键性工作的，不得签订；已签订的应停工整改并解除合同。"),
    ("二、现场管理机构与人员锁定审查",
     "项目负责人、安全员、劳务员等人员名单，执业或岗位证书、劳动合同、社保缴费记录、任命文件",
     "现场管理机构、人员到岗率、人员更换审批",
     "人员未到岗、社保与证书不一致的，可能被认定为转包或挂靠，质量安全责任难以落实。"),
    ("三、价款、计量与审计审查",
     "分包计价及工程量清单、总包对应计价条款、计量支付流程",
     "计价口径（含税、管理费、安全生产费）、监理签认、审计或财政评审结论、资金专户",
     "计价与计量依据不足的，结算争议大、超付难以扣回，审计整改责任不清。"),
    ("四、质量、安全、环保与保修审查",
     "总包和分包的质量、安全、环保、保修条款，专项施工方案报审安排",
     "质量目标、缺陷责任期、安全生产费单列、环保与水保、监理指令执行",
     "质量安全责任低于总包的，缺陷修复和事故追责缺少合同依据，连带责任难以传导。"),
    ("五、工期与违约责任审查",
     "总包施工组织设计及关键节点、分包工期和节点安排",
     "节点工期、逾期违约金封顶、合同解除情形",
     "节点和违约责任缺失的，工期失控时缺少追责和退出手段。"),
    ("六、农民工工资与信用管理审查",
     "合同工资支付条款、工资专户及代发安排",
     "实名制、工资专用账户、总包代发、信用档案联动",
     "未落实工资专户和代发的，存在欠薪、行政监管和信用扣分风险。"),
    ("七、报审材料清单（开工前置核查）",
     "第 12 条五项材料、报审信息表、监理审查意见、发包人书面同意",
     "附件清单、资料移交及开工前提交安排",
     "签约前材料不齐的暂缓签订；开工前材料不齐的不得签发开工令。"),
    ("八、综合审查结论",
     "人工审批",
     "—",
     "未完成上述核验的，不得出具同意意见。"),
)


def _text(value, default=UNKNOWN):
    return str(value).strip() if value not in (None, "") else default


def _raw_items(report):
    """当前任务的逐项机器结果；排除忽略、开放条款和人工提示。"""
    rows = []
    for section in report.get("sections") or []:
        kind = section.get("kind")
        for item in section.get("items") or []:
            if (item.get("review") or {}).get("decision") == "ignored":
                continue
            if (item.get("human_disposition") or {}).get("code") == "ignored":
                continue
            if item.get("open_term") or item.get("finding_semantics") == "manual_prompt":
                continue
            # 缺项（not_included）必须进入报告附件；不适用（not_applicable）不构成本次问题。
            if item.get("status") not in {"hit", "miss", "undecidable", "not_included"}:
                continue
            rows.append({
                "kind": kind,
                "item_id": item.get("item_id") or "",
                "status": item.get("status"),
                "material_missing": item.get("material_missing") is True,
                "material_only": item.get("material_only") is True,
                "label": _plain_label(_text(item.get("label") or item.get("item_id"), "核查事项")),
                "message": _plain_message(item.get("message")),
                "source": _plain_source(item.get("item_id"), _text(item.get("source"), "")),
                "recommendation": _text(item.get("recommendation"), ""),
                "evidence": item.get("evidence") or {},
            })
    return rows


def _manual_prompt_rows(report):
    """普通人工提示可见，但不进入机器结论/签署条件。"""
    rows = []
    for section in report.get("sections") or []:
        for item in section.get("items") or []:
            if (item.get("finding_semantics") != "manual_prompt"
                    or (item.get("review") or {}).get("decision") == "ignored"
                    or (item.get("human_disposition") or {}).get("code") == "ignored"):
                continue
            evidence = item.get("evidence_list") or ([item["evidence"]] if item.get("evidence") else [])
            rows.append({"item_id": item.get("item_id"), "finding_id": item.get("finding_id"),
                "label": _plain_label(item.get("label") or item.get("item_id")),
                "message": _plain_message(item.get("message")), "source": _plain_message(item.get("source")),
                "locations": [_plain_message("%s 第%s页：%s" % (ref.get("file_name") or "材料%s" % ref.get("material_id"),
                    ref.get("page") or "待核实", ref.get("quote") or "未提供摘录"))
                    for ref in evidence if isinstance(ref, dict)]})
    return rows


def _verdict(item):
    if item["status"] == "hit" and item["material_missing"]:
        return "缺项"
    # 新内核用显式 not_included 表达缺项（材料/字段未取得），与 hit+material_missing 同档。
    if item["status"] == "not_included":
        return "缺项"
    if item["status"] == "hit":
        return "不符合"
    if item["status"] == "undecidable":
        return "待核实"
    return "符合"


def _dims(base):
    return {row.get("code"): row for row in base.get("dimensions") or []}


def _risks(base, raw):
    order = {"不符合": 0, "缺项": 1, "待核实": 2}
    rows = [item for item in raw if _verdict(item) != "符合"]
    rows.sort(key=lambda item: (order.get(_verdict(item), 9), item["label"]))
    return rows


def _suggested_conclusion(base, raw):
    """三档结论（设计 2026-11-18 §7.2）：不同意 / 原则同意，附条件通过 / 同意。"""
    dims = _dims(base)
    if (dims.get("compliance") or {}).get("verdict") == "不符合":
        return "不同意"
    if any(_verdict(item) != "符合" for item in raw):
        return "原则同意，附条件通过"
    return "同意"


def _conclusion(base, raw):
    verdicts = [_verdict(item) for item in raw]
    risks = _risks(base, raw)
    key_risks = []
    for item in risks[:5]:
        key_risks.append("%s：%s" % (item["label"], item["message"]))
    if not key_risks:
        key_risks.append("本次已核查范围内未发现不符合、缺项或待核实事项。")
    return {
        "suggested": _suggested_conclusion(base, raw),
        # 与逐项判定、签署结论同源；不引用 V1.3 的分节去重统计，避免“命中却显示 0”。
        "risk_counts": {
            "不符合": verdicts.count("不符合"),
            "缺项": verdicts.count("缺项"),
            "待核实": verdicts.count("待核实"),
        },
        "key_risks": key_risks,
        "approval_opinion": "",
        "deadline": "",
        "signatures": {"reviewer": "", "checker": "", "issuer": ""},
    }


def _section_items(raw, kinds, include=None, exclude=None):
    rows = [item for item in raw if item.get("kind") in kinds]
    if include:
        rows = [item for item in rows
                if any(word in (item["label"] + item["message"]) for word in include)]
    if exclude:
        rows = [item for item in rows
                if not any(word in (item["label"] + item["message"]) for word in exclude)]
    return rows


def _section_text(name, items, dimension):
    verdicts = [_verdict(item) for item in items]
    if "不符合" in verdicts:
        return "本节发现 %d 项不符合，见下列注意事项。" % verdicts.count("不符合")
    if "缺项" in verdicts:
        return "本节存在 %d 项材料缺项，见下列注意事项。" % verdicts.count("缺项")
    if "待核实" in verdicts:
        if "符合" in verdicts:
            return "本节已核查部分事项，其余见附件检查表。"
        return "本节已核查，相关事项见附件检查表。"
    if items:
        return "本节已核查范围内未发现不符合项。"
    if dimension and dimension.get("verdict") == "—":
        return "本节未能形成确定结论，原因见下。"
    return "本节已核查，相关事项见附件检查表。"


def _fact_text(item):
    label = item.get("label") or ""
    message = item.get("message") or ""
    fact = message if label and label in message else "%s：%s" % (label, message)
    evidence = item.get("evidence") or {}
    file_name = str(evidence.get("file_name") or "").strip()
    page = evidence.get("page")
    quote = str(evidence.get("quote") or "").strip()
    if not file_name and not page:
        return fact
    location = "见《%s》" % file_name if file_name else "原件"
    if type(page) is int and page > 0:
        location += "第%d页" % page
    if quote:
        excerpt = quote if len(quote) <= 80 else quote[:80] + "…"
        location += "：“%s”" % excerpt
    return "%s（%s）" % (fact, location)


# 影响段（设计 2026-11-18 §7.3 四段式）：按核查内核类别给出固定表述，不新增事实。
_IMPACT_BY_KIND = {
    "negative_list": "存在被认定为违法分包、合同无效并计入信用档案的风险。",
    "transfer_scope": "存在被认定为转包或违法分包的风险，质量安全责任难以落实。",
    "illegal_subcontract": "存在被认定为违法分包的风险，相关合同条款可能被认定无效。",
    "person_consistency": "人员与投标承诺或报审名单不一致，可能被认定为挂靠，质量安全责任难以落实。",
    "personnel_adequacy": "关键岗位人员不足，现场管理能力不达标，质量安全措施难以落实。",
    "cert_validity": "证照失效期间不得从事相应作业，履约与合规均存在风险。",
    "qualification_match": "资质覆盖不足，存在超资质承接与合同效力瑕疵风险。",
    "internal_records": "内部管理记录不完整，难以证明分包人实际履约及人员归属。",
}


def _impact(item):
    if item.get('item_id') == 'RC-A10-cost':
        return '影响安全生产费合同单独约定与总分包费用责任核对，不能据此认定实际支付或专款专用'
    if item.get("item_id") == "A2-14":
        return "技术负责人已提交职称等级与分包规模的对应条件未落实，需核对相应等级证明及任职安排。"
    if item.get("item_id") == "A2-10":
        return "人员证件字段与归属仍需核实，尚不能确认相应岗位的持证要求。"
    if item.get("item_id") == "A2-09":
        return "安全生产许可行业与明示施工作业的对应关系尚未确认，需核对实际证照及作业性质；资质承包范围另行核查。"
    if item.get("item_id") == "A2-08":
        return "特种作业工种或证书类型候选尚未确认，相关作业资格与安全责任条件尚不能确认。"
    return _IMPACT_BY_KIND.get(item.get("kind"), "该项不符合将影响分包合规性判断与后续结算依据。")


def _section_paragraphs(name, items, dimension):
    paragraphs = []
    if dimension:
        if dimension.get("summary"):
            paragraphs.append(dimension["summary"])
        if dimension.get("basis"):
            paragraphs.append("主要依据：" + "；".join(dimension["basis"]))
        if dimension.get("verdict") == "—" and dimension.get("limitation"):
            paragraphs.append(dimension["limitation"])
    passed = [item for item in items if _verdict(item) == "符合"]
    if passed:
        labels = "、".join(dict.fromkeys(item["label"] for item in passed))
        paragraphs.append("已核查通过：%s。" % labels)
    problems = [item for item in items if _verdict(item) != "符合"]
    for item in problems[:8]:
        verdict = _verdict(item)
        paragraphs.append("%s（%s）：%s" % (item["label"], verdict, _fact_text(item)))
    if len(problems) > 8:
        paragraphs.append("其余 %d 项见第八部分审查结论与要求。" % (len(problems) - 8))
    if not items and not dimension:
        if name == "contract":
            paragraphs.append("本次未启用合同条款语义核查；报审材料未提供完整合同条款，计价、支付、违约等条款未形成结论。")
        elif name == "safety":
            paragraphs.append("本版本未建立质量、安全、进度与工资保障的完整自动核查规则；相关事项需人工核查。")
        else:
            paragraphs.append("本次未取得可核对结果，需人工核查或补充材料后复核。")
    return paragraphs


_SECTIONS = (
    ("qualification", "1. 资质与承接能力",
     ("qualification_match", "internal_records", "cert_validity"),
     "qualification_credit",
     "本次未取得营业执照、资质证书或信用材料，无法核对资质等级和承接范围。",
     None, ("安全生产许可证", "安许", "建造师", "交安", "特种作业", "身份证", "社保", "劳动合同")),
    ("safety_permit", "2. 安全生产许可", ("cert_validity",), None,
     "本次未取得安全生产许可证，无法核对证号、许可范围和有效期。",
     ("安全生产许可证", "安许"), None),
    ("personnel", "3. 人员配置与持证", ("person_consistency", "personnel_adequacy", "cert_validity"),
     "personnel",
     "本次未取得人员证书、劳动合同或社保记录，无法核对人员配置。",
     None, ("安全生产许可证", "安许")),
    ("legality", "4. 分包范围与负面清单",
     ("negative_list", "transfer_scope", "illegal_subcontract"),
     "compliance",
     "本次材料未涉及负面清单、转包或违法分包情形。",
     None, None),
    ("contract", "5. 合同与支付条款",
     ("contract_semantic", "amount_ratio", "workload_ratio"),
     "contract_clauses",
     "本次未取得完整合同文本，无法核对计价、支付、违约条款。",
     None, None),
    ("procedure", "6. 报审程序与资料补正",
     ("completeness", "signature_chain", "a1_dependency"),
     None,
     "本次未取得完整的报审材料或签字链结果，无法核对审批程序。",
     None, None),
)


_CATEGORY_RULES = (
    ("企业资格与业绩资料", ("营业执照", "资质证书", "安全生产许可证", "安许", "诚信手册", "业绩", "注册资本")),
    ("人员及履约资料", ("人员", "社保", "劳动合同", "身份证", "建造师", "交安", "特种作业", "证书")),
    ("合同与支付资料", ("合同", "计价", "支付", "结算", "违约", "工资")),
    ("信用与事故资料", ("信用", "事故", "警示名单")),
    ("报审程序资料", ("签字", "盖章", "信息表", "报审", "审批", "授权", "清单")),
)


def _item_category(item):
    text = item["label"] + item["message"]
    for name, words in _CATEGORY_RULES:
        if any(word in text for word in words):
            return name
    return "其他报审资料"


def _short_text(text, limit=120):
    text = str(text or "").strip().rstrip("。；;")
    if len(text) <= limit:
        return text + "。"
    for separator in ("。", "；", ";"):
        if separator in text:
            first = text.split(separator, 1)[0].strip()
            if first and len(first) <= limit:
                return first + "。"
    head = text[:limit]
    if "，" in head:
        head = head.rsplit("，", 1)[0]
    return head + "等，需人工核实。"


def _strip_subject(text):
    return re.sub(r"^主体\s*\d+（[^）]*）：", "", str(text or "")).strip()


def _unique_labels(items, verdict=None):
    labels = []
    for item in items:
        if verdict and _verdict(item) != verdict:
            continue
        label = item.get("label") or ""
        if label and label not in labels:
            labels.append(label)
    return labels


def _pending_label(item):
    """待核实项优先写实际缺失证件，不用整条材料要求标签冒充缺失范围。"""
    message = item.get("message") or ""
    match = re.search(r"尚不能确认以下证件与其对应：(.+?)(?:；|。|$)", message)
    if match:
        head = str(item.get("label") or "").split("（", 1)[0]
        return "%s（%s）" % (head, match.group(1).strip())
    return item.get("label") or ""


def _section_summary_lines(code, items):
    """每章的具体内容摘要；不写“需核实”前缀，也不逐条铺开。"""
    if code == "safety_permit":
        safety_items = [item for item in items
                        if "安全生产许可证" in (item["label"] + item["message"])]
        if not safety_items:
            return ["本次未取得安全生产许可证的可核对字段。"]
        expiry = next((item for item in safety_items if "早于基准日" in item["message"]), None)
        if expiry:
            match = re.search(r"有效期至\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", expiry["message"])
            when = "（有效期至%s）" % match.group(1) if match else ""
            return ["安全生产许可证已随报审资料提供%s。" % when,
                    "核对提示：证照标注有效期早于核查基准日，不能认定在有效期内；需提交续期后的证书。"]
        return ["安全生产许可证已提供；许可范围、证书号、有效期需核对证书原件。"]
    if code == "personnel":
        labels = _unique_labels(items)
        lines = ["人员材料已纳入核对：主要管理人员名单、社保资料、执业/岗位证书。"]
        if labels:
            lines.append("待核对：" + "、".join(labels[:6]) + "。")
        return lines
    if code == "legality":
        negative = [item for item in items if item["kind"] == "negative_list"]
        transfer = [item for item in items if item["kind"] == "transfer_scope"]
        illegal = [item for item in items if item["kind"] == "illegal_subcontract"]
        coverage = next((item for item in items if item["item_id"] == "W16-02"), None)
        lines = []
        if coverage:
            verdict = _verdict(coverage)
            if verdict == "不符合":
                lines.append("负面清单：发现问题，见下列注意事项。")
            elif verdict == "待核实":
                lines.append("负面清单：有需补充确认的事项。")
            else:
                lines.append("负面清单：通过。")
        elif negative:
            lines.append("负面清单：通过。")
        if transfer or illegal:
            lines.append("转包、违法分包类事项需结合总包合同和现场资料核对。")
        return lines
    if code == "procedure":
        pending = []
        for item in items:
            if _verdict(item) != "待核实":
                continue
            label = _pending_label(item)
            if label and label not in pending:
                pending.append(label)
        if pending:
            return ["尚未确认：" + "、".join(pending[:6]) + "。"]
        return []
    return []


_CONTRACT_CLAUSE_LABELS = (
    ("pricing", "计价"),
    ("measurement", "计量"),
    ("payment", "支付"),
    ("settlement", "结算/审计"),
    ("breach", "违约"),
    ("termination", "解除"),
    ("quality", "质量"),
    ("safety", "安全"),
    ("wages", "农民工工资"),
    ("plan", "专项施工方案审批"),
    ("change", "合同变更"),
    ("warranty", "缺陷责任期/保修"),
    ("environment", "环保与水保"),
)


_CONTRACT_ITEM_KEYS = {
    "计价口径清晰": "pricing",
    "背靠背支付条款": "payment",
    "计量流程完整": "measurement",
    "最终结算以审计为准": "settlement",
    "质量目标不降低": "quality",
    "质量连带责任": "quality",
    "缺陷责任期对齐": "warranty",
    "安全生产费单列": "safety",
    "环保与水保条款": "environment",
    "违约金合理封顶": "breach",
    "合同解除情形": "termination",
    "实名制与专用账户": "wages",
    "工资代付机制": "wages",
}


def _contract_hint(item, contract_facts):
    key = _CONTRACT_ITEM_KEYS.get(str(item or "").strip())
    clause = ((contract_facts or {}).get("clauses") or {}).get(key) if key else None
    page = clause.get("page") if isinstance(clause, dict) else None
    if not isinstance(page, int) or isinstance(page, bool) or page <= 0:
        return None
    label = dict(_CONTRACT_CLAUSE_LABELS).get(key, "合同")
    return page, label


def _contract_page_range(facts):
    pages = sorted({page for page in facts.get("pages") or []
                    if isinstance(page, int) and not isinstance(page, bool) and page > 0})
    if not pages:
        return ""
    if len(pages) == 1:
        return "第%d页" % pages[0]
    return "第%d–%d页" % (pages[0], pages[-1])


def _contract_summary_lines(facts):
    if not facts or not facts.get("pages"):
        return ["未取得可核对的合同正文页，计价、支付、违约等条款需人工核对。"]
    lines = ["合同文本已随报审资料提供（%s）。" % _contract_page_range(facts)]
    clauses = facts.get("clauses") or {}
    seen = []
    missing = []
    for key, label in _CONTRACT_CLAUSE_LABELS:
        item = clauses.get(key) or {}
        if item.get("page"):
            seen.append("%s（第%s页）" % (label, item["page"]))
        else:
            missing.append(label)
    if seen:
        lines.append("已定位主要条款：" + "、".join(seen) + "。")
    if missing:
        lines.append("未定位条款：" + "、".join(missing) + "，需核对合同原文。")
    return lines


def _contract_summary_conclusion(facts):
    if facts and facts.get("pages"):
        return "合同主要条款已在文本中定位；条款是否低于总承包合同、是否公平可执行，仍需人工核对。"
    return "未取得完整合同条款，计价、支付、违约等条款需人工核对。"


def _personnel_summary_lines(facts, social=None, items=None):
    rows = []
    seen = set()
    for fact in facts or []:
        if not isinstance(fact, dict):
            continue
        name = str(fact.get("name") or "").strip()
        post = str(fact.get("post") or "").strip()
        if not name or not post or name in seen:
            continue
        seen.add(name)
        rows.append("%s（%s）" % (name, post))
    lines = (["已提取主要管理人员：" + "、".join(rows[:8]) + "。"] if rows
             else ["未取得主要管理人员名单的实填姓名和岗位，需补充或人工核对。"])
    social_periods, employer = {}, ""
    for fact in social or []:
        if not isinstance(fact, dict):
            continue
        name = str(fact.get("name") or "").strip()
        period = str(fact.get("period") or "").strip()
        if name and period:
            social_periods.setdefault(name, period)
        if not employer and fact.get("employer"):
            employer = str(fact["employer"]).strip()
    if social_periods:
        detail = "、".join("%s（%s）" % (name, period)
                          for name, period in list(social_periods.items())[:6])
        lines.append("社保缴费记录已覆盖：%s%s。" % (
            detail, "，参保单位" + employer if employer else ""))
    else:
        lines.append("社保缴费记录未识别到人员与期间，需补充或人工核对。")
    cert_gaps, labor_gap = [], ""
    for item in items or []:
        message = item.get("message") or ""
        match = re.search(r"尚不能确认以下证件与其对应：(.+?)(?:；|。|$)", message)
        if match:
            head = str(item.get("label") or "").split("（", 1)[0]
            cert_gaps.append("%s（%s）" % (head, match.group(1).strip()))
        labor = re.search(r"劳动合同关系未证明(?:（缺([^）]*)）)?", message)
        if labor and not labor_gap:
            labor_gap = labor.group(1) or ""
    if cert_gaps:
        lines.append("证件核对：" + "；".join(cert_gaps) + "。")
    if labor_gap:
        lines.append("劳动合同关系未证明：%s，需补交劳动合同。" % labor_gap)
    elif any("劳动合同" in (item.get("message") or "") for item in items or []):
        lines.append("劳动合同关系尚未证明，需补交主要人员劳动合同。")
    lines.append("要求：项目负责人、技术负责人、安全负责人须为本单位人员并常驻现场，"
                 "未经建设单位同意不得更换。")
    return lines


def _section_summary_conclusion(code, items, fallback):
    counts = {status: sum(1 for item in items if _verdict(item) == status)
              for status in ("符合", "不符合", "缺项", "待核实")}
    if counts["不符合"] or counts["缺项"]:
        return fallback
    if code == "safety_permit":
        expiry = next((item for item in items if "早于基准日" in item["message"]), None)
        if expiry:
            match = re.search(r"有效期至\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", expiry["message"])
            when = "（有效期至%s）" % match.group(1) if match else ""
            return "安全生产许可证已提供%s，但已早于核查基准日，需提交续期后的证书后重新报审。" % when
        return "安许证书已提供；许可范围、证书号、有效期需核对证书原件。"
    if code == "personnel":
        if any("劳动合同" in (item.get("message") or "") for item in items):
            return "人员配置已核对；劳动合同关系未证明，需补交主要人员劳动合同后确认。"
        return "人员配置与社保、证书对应关系已核对；未在册证件需核对原件。"
    if code == "legality":
        coverage = next((item for item in items if item["item_id"] == "W16-02"), None)
        if coverage:
            verdict = _verdict(coverage)
            if verdict == "不符合":
                return "负面清单：发现问题；转包、违法分包需结合材料核对。"
            if verdict == "待核实":
                return "负面清单：有需补充确认的事项；转包、违法分包需结合材料核对。"
        return "负面清单：通过；转包、违法分包需结合材料核对。"
    if code == "procedure":
        return "已核对 %d 项材料；未确认事项见上，需核对原件。" % counts["符合"]
    return fallback


def _section_bullets(items, empty_text):
    bullets = []
    passed = [item for item in items
              if _verdict(item) == "符合"
              and item.get("kind") not in {"negative_list", "transfer_scope", "illegal_subcontract", "a1_dependency"}
              and len(item["label"]) <= 30]
    if passed:
        labels = "、".join(dict.fromkeys(item["label"] for item in passed))
        bullets.append({"text": "已核对材料：%s。" % labels, "bold": False})
    problems = [item for item in items if _verdict(item) != "符合"]
    missing = [item for item in problems if _verdict(item) == "缺项"]
    grouped = {}
    for item in missing:
        grouped.setdefault(_item_category(item), []).append(item["label"])
    for category, labels in grouped.items():
        bullets.append({"text": "注意：%s缺项：%s。" % (category, "、".join(dict.fromkeys(labels))),
                        "bold": True})
    nonconforming = [item for item in problems if _verdict(item) == "不符合"]
    for item in nonconforming[:5]:
        bullets.append({"text": "注意：" + _short_text(_fact_text(item)), "bold": True})
    if not items and empty_text:
        bullets.append({"text": empty_text, "bold": False})
    return bullets


def _section_status(items):
    verdicts = [_verdict(item) for item in items]
    if "不符合" in verdicts:
        return "不通过"
    if "缺项" in verdicts:
        return "缺项"
    if "待核实" in verdicts:
        return "待核实"
    if items:
        return "通过"
    return "未核查"


def _sections(base, raw, contract_facts=None, personnel_facts=None, certificate_facts=None,
              baseline=None, personnel_social=None):
    dims = _dims(base)
    rows = []
    for code, title, kinds, dimension_code, empty_text, include, exclude in _SECTIONS:
        items = _section_items(raw, kinds, include=include, exclude=exclude)
        dimension = dims.get(dimension_code) if dimension_code else None
        counts = {status: sum(1 for item in items if _verdict(item) == status)
                  for status in ("符合", "不符合", "缺项", "待核实")}
        conclusion_text = _section_text(code, items, dimension)
        summary_lines = _section_summary_lines(code, items)
        summary_conclusion = _section_summary_conclusion(code, items, conclusion_text)
        status = _section_status(items)
        bullets = _section_bullets(items, empty_text)
        if code == "qualification" and certificate_facts and (
                certificate_facts.get("qualification") or certificate_facts.get("business_license")):
            if status == "未核查":
                status = "待核实"
        if code == "safety_permit" and certificate_facts and certificate_facts.get("safety_license"):
            safety = certificate_facts["safety_license"]
            summary_lines = _safety_summary_lines(safety, baseline)
            summary_conclusion = _safety_summary_conclusion(safety, baseline)
            conclusion_text = summary_conclusion
            bullets = []
            if status == "未核查":
                status = "待核实"
        if code == "safety_permit" and any(item.get("item_id") == "A2-09" for item in items):
            summary_lines += [item["message"] for item in items if item.get("item_id") == "A2-09"]
            scope_verdicts = list(dict.fromkeys(_verdict(item) for item in items if item.get("item_id") == "A2-09"))
            summary_conclusion = (_section_text(code, items, dimension)
                                  + "许可行业核查结果：%s；资质专业、等级、承包规模及证照有效性分别核查。"
                                  % "、".join(scope_verdicts))
            conclusion_text = summary_conclusion
        if code == "contract" and contract_facts and contract_facts.get("pages"):
            status = "待核实"
            summary_lines = _contract_summary_lines(contract_facts)
            summary_conclusion = _contract_summary_conclusion(contract_facts)
            conclusion_text = summary_conclusion
            bullets = []
        if code == "personnel" and personnel_facts:
            summary_lines = _personnel_summary_lines(personnel_facts, personnel_social, items)
            if status == "未核查":
                status = "待核实"
        rows.append({
            "code": code,
            "title": title,
            "status": status,
            "counts": counts,
            "conclusion": conclusion_text,
            "summary_conclusion": summary_conclusion,
            "summary_lines": summary_lines,
            "paragraphs": _section_paragraphs(code, items, dimension) or ([empty_text] if empty_text else []),
            "bullets": bullets,
        })
    return rows


def _responsible(item):
    if item.get("kind") in {"qualification_match", "person_consistency", "personnel_adequacy",
                            "cert_validity", "internal_records"}:
        return "分包人"
    text = item.get("label", "") + item.get("message", "")
    if any(word in text for word in ("营业执照", "资质", "安全生产许可证", "安许", "人员",
                                     "社保", "劳动合同", "证书", "特种作业")):
        return "分包人"
    return "承包人"


def _issue_rows(raw):
    rows = []
    seen = set()
    for item in _risks(None, raw):
        verdict = _verdict(item)
        if verdict not in {"不符合", "缺项"}:
            continue
        key = (verdict, item["label"], item["message"])
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "number": len(rows) + 1,
            "category": verdict,
            "fact": _fact_text(item),
            "reason": item["source"] or "见核查记录",
            "impact": _impact(item),
            "recommendation": item["recommendation"] or ("补交" + item["label"] if verdict == "缺项" else "人工核实"),
            "responsible_party": _responsible(item),
        })
    return rows


def _pending_rows(raw, limit=10):
    rows = []
    seen = set()
    for item in _risks(None, raw):
        if _verdict(item) != "待核实":
            continue
        key = (item["label"], item["message"])
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "number": len(rows) + 1,
            "fact": _fact_text(item),
            "reason": item["source"] or "现有材料未形成确定结论",
            "recommendation": item["recommendation"] or "人工核实或补充材料后复核",
        })
        if len(rows) >= limit:
            break
    return rows


def _matches_item_scope(item, item_ids):
    item_id = item.get("item_id") or ""
    for scope in item_ids:
        if scope.endswith("*"):
            if item_id.startswith(scope[:-1]):
                return True
        elif item_id == scope:
            return True
    return False


def _dimension_result(dimension, keywords, items, covered, item_ids=None):
    # 条目白名单优先：避免“再分包”行抓到“以劳务合作名义”的 finding 后引用错条款。
    if item_ids:
        matched = [item for item in items if _matches_item_scope(item, item_ids)]
    else:
        matched = [item for item in items
                   if any(word in (item["label"] + item["message"]) for word in keywords)]
    if matched:
        verdicts = [_verdict(item) for item in matched]
        for verdict in ("不符合", "缺项", "待核实"):
            if verdict in verdicts:
                first = next(item for item in matched if _verdict(item) == verdict)
                reason = first["source"] or first["message"]
                if first['item_id'] in {'RC-A10-cost', 'A2-14'}:
                    reason = '；'.join(value for value in (first['message'], first['source']) if value)
                if verdict == "缺项":
                    reason = "未提供%s" % first["label"]
                return verdict, reason, first["recommendation"] or (
                    "补交" + first["label"] if verdict == "缺项" else "人工核实")
        if all(item['item_id'] in {'RC-A10-cost', 'A2-14'} for item in matched):
            return '符合', '；'.join(item['message'] for item in matched), '—'
        return "符合", "已核查，未发现异常", "—"
    if not covered:
        return "—", "本版本未覆盖该审查项", "人工核查或补充材料后复核"
    if dimension and dimension.get("verdict") in {"符合", "不符合", "缺项"}:
        return "—", "本次未形成独立结论，本维度整体状态为%s" % dimension["verdict"], "人工核实"
    if dimension and dimension.get("verdict") == "—":
        return "—", "本次未形成该审查项的可核对结论", "人工核查或补充材料后复核"
    return "—", "本次未形成该审查项的可核对结论", "人工核查或补充材料后复核"


def _responsibility(sheet_code, item):
    if "发包人" in item or "建设单位" in item:
        return "建设单位"
    if "监理" in item:
        return "监理"
    if "承包人" in item:
        return "承包人"
    if sheet_code in {"personnel_org", "quality"}:
        return "承包人/分包人"
    return "承包人"


def _attachments(base, raw, contract_facts=None):
    dims = _dims(base)
    sheets = []
    for sheet in _SHEETS:
        auto_rows = []
        manual_rows = []
        for item, point, dimension_code, keywords, covered, *rest in sheet["rows"]:
            item_ids = rest[0] if rest else None
            responsibility = _responsibility(sheet["code"], item)
            if not covered:
                hint = _contract_hint(item, contract_facts)
                if hint:
                    page, label = hint
                    auto_rows.append([
                        item,
                        "待核实",
                        "合同第%d页已定位%s条款；需核对原文并判断是否满足要求。\n审查要点：%s"
                        % (page, label, point),
                        "人工核对合同条款",
                        responsibility,
                    ])
                    continue
                manual_rows.append([
                    item,
                    "本版本未建立该审查项自动规则；报审材料中未定位到可直接核对的条款或字段",
                    point + "；请核对合同、证照原件或现场资料",
                    responsibility,
                ])
                continue
            verdict, reason, fix = _dimension_result(
                dims.get(dimension_code), keywords, raw, covered, item_ids)
            if verdict == "—":
                manual_rows.append([
                    item,
                    reason,
                    point + "；请核对合同、证照原件或现场资料",
                    responsibility,
                ])
                continue
            auto_rows.append([
                item,
                verdict,
                "%s\n审查要点：%s" % (reason, point),
                fix,
                responsibility,
            ])
        sheets.append({
            "code": sheet["code"],
            "title": sheet["title"],
            "note": sheet["note"],
            "kind": "table",
            "auto_headers": ["审查项", "核查结果", "核查说明", "补正要求", "责任单位"],
            "auto_widths": [.18, .09, .43, .20, .10],
            "auto_rows": auto_rows,
            "manual_headers": ["审查项", "未自动核查原因", "建议人工核查内容", "责任单位"],
            "manual_widths": [.20, .30, .38, .12],
            "manual_rows": manual_rows,
        })
    material_rows = []
    for number, name, keywords, note in _MATERIAL_ROWS:
        words = keywords.split("|")
        matched = [item for item in raw if any(word in (item["label"] + item["message"]) for word in words)]
        if matched:
            verdicts = [_verdict(item) for item in matched]
            if "缺项" in verdicts:
                state, reason = "否", "未提供%s" % name
            elif "不符合" in verdicts:
                state, reason = "否", "已提供但核查不符合：" + matched[0]["message"]
            elif "待核实" in verdicts:
                state, reason = "无法核实", "待核实：" + matched[0]["message"]
            else:
                state, reason = "是", note or "已提供"
        else:
            state, reason = "无法核实", "报审材料清单未载明该项，需人工确认"
        material_rows.append([number, name, state, reason, "承包人"])
    sheets.append({
        "code": "materials",
        "title": "七、报审材料清单（开工前置核查）",
        "note": "勾选“是否齐全”，不齐全的不得签发开工令。",
        "kind": "table",
        "auto_headers": ["序号", "材料名称", "是否齐全", "说明", "责任单位"],
        "auto_widths": [.07, .35, .12, .36, .10],
        "auto_rows": material_rows,
        "manual_headers": [],
        "manual_widths": [],
        "manual_rows": [],
    })
    sheets.append({
        "code": "conclusion",
        "title": "八、综合审查结论",
        "note": "",
        "kind": "key_value",
        "rows": [
            ("审查结论", "□ 通过　□ 修改后通过　□ 不通过"),
            ("重大风险事项", "□ 突破负面清单　□ 未取得发包人书面同意　□ 人员无社保/挂靠　□ 无安全生产费　□ 无农民工工资专户"),
            ("整改期限", "分包合同须在 ____ 年 __ 月 __ 日前修改完成并重新报审"),
            ("责任人", "承包人合约负责人：________　监理总监：________　建设单位：________"),
        ],
    })
    return sheets


def _appendix(base, raw, contract_facts=None):
    """正式意见附件：只保留确定结论、需人工核查汇总、未覆盖范围。

    8 张检查表仍保留在 `attachments` 作为系统工作底稿，不进入正式意见正文。
    """
    dims = _dims(base)
    auto_rows = []
    manual = {}
    uncovered_items = []
    uncovered_sheets = []
    for sheet in _SHEETS:
        covered_flags = [row[4] for row in sheet["rows"]]
        uncovered_rows = [row for row in sheet["rows"] if not row[4]]
        hinted_rows = [(row, _contract_hint(row[0], contract_facts)) for row in uncovered_rows]
        unhinted_rows = [row for row, hint in hinted_rows if hint is None]
        if not any(covered_flags):
            if unhinted_rows:
                uncovered_sheets.append(sheet["title"])
        elif uncovered_rows:
            uncovered_items.extend(row[0] for row in unhinted_rows)
        for item, point, dimension_code, keywords, covered, *rest in sheet["rows"]:
            item_ids = rest[0] if rest else None
            responsibility = _responsibility(sheet["code"], item)
            if not covered:
                hint = _contract_hint(item, contract_facts)
                if hint:
                    page, _label = hint
                    group = manual.setdefault(sheet["title"] + "·合同条款已定位", {
                        "items": [], "status": "待核实",
                        "reason": "合同已定位相关条款，需人工核对是否满足要求",
                        "responsibilities": [],
                    })
                    group["items"].append("%s（合同第%d页）" % (item, page))
                else:
                    group = manual.setdefault(sheet["title"], {
                        "items": [], "status": "未覆盖",
                        "reason": "本版本未建立自动核查规则", "responsibilities": [],
                    })
                    group["items"].append(item)
                if responsibility not in group["responsibilities"]:
                    group["responsibilities"].append(responsibility)
                continue
            verdict, reason, fix = _dimension_result(
                dims.get(dimension_code), keywords, raw, covered, item_ids)
            if verdict in {"符合", "不符合", "缺项"}:
                auto_rows.append([
                    item, verdict,
                    "%s\n审查要点：%s" % (reason, point),
                    fix, responsibility,
                ])
                continue
            group = manual.setdefault(sheet["title"], {
                "items": [], "status": "待核实",
                "reason": "现有材料未形成确定结论",
                "responsibilities": [],
            })
            group["items"].append(item)
            if responsibility not in group["responsibilities"]:
                group["responsibilities"].append(responsibility)

    material_group = {
        "items": [], "status": "待核实",
        "reason": "报审材料清单未形成确定结论", "responsibilities": ["承包人"],
    }
    for _number, name, keywords, note in _MATERIAL_ROWS:
        words = keywords.split("|")
        matched = [item for item in raw
                   if any(word in (item["label"] + item["message"]) for word in words)]
        if not matched:
            material_group["items"].append(name)
            continue
        verdicts = [_verdict(item) for item in matched]
        if "缺项" in verdicts:
            auto_rows.append([name, "缺项", "未提供%s" % name, "补交" + name, "承包人"])
        elif "不符合" in verdicts:
            auto_rows.append([
                name, "不符合", matched[0]["message"],
                matched[0]["recommendation"] or "人工核实", "承包人",
            ])
        elif "符合" in verdicts:
            reason = "已随报审资料上传。"
            if any(word in name for word in ("营业执照", "资质证书", "安全生产许可证",
                                             "主要人员", "社保")):
                reason = "已随报审资料上传；证照有效性、人员一致性另见附件二。"
            auto_rows.append([name, "通过", reason, "—", "承包人"])
        else:
            material_group["items"].append(name)
    if material_group["items"]:
        manual["七、报审材料清单（开工前置核查）"] = material_group

    manual_rows = []
    for title, group in manual.items():
        labels = list(dict.fromkeys(group["items"]))
        shown = "、".join(labels[:6])
        if len(labels) > 6:
            shown += "等 %d 项" % len(labels)
        manual_rows.append([
            title, group["status"], group["reason"],
            shown + "；请核对合同、证照原件或现场资料。",
            "、".join(group["responsibilities"]),
        ])

    uncovered_parts = []
    if uncovered_items:
        uncovered_parts.append("单项：" + "、".join(dict.fromkeys(uncovered_items)))
    if uncovered_sheets:
        uncovered_parts.append("整类：" + "、".join(dict.fromkeys(uncovered_sheets)))
    if uncovered_parts:
        uncovered_text = "本次未覆盖：%s。本版本未建立对应自动核查规则，需人工核查。" \
                         % "；".join(uncovered_parts)
    else:
        uncovered_text = "本次未覆盖：无。"
    return {
        "auto_headers": ["审查项", "核查结果", "核查说明", "补正要求", "责任单位"],
        "auto_widths": [.18, .09, .43, .20, .10],
        "auto_rows": auto_rows,
        "manual_headers": ["事项类别", "当前状态", "原因", "建议动作", "责任单位"],
        "manual_widths": [.16, .10, .24, .38, .12],
        "manual_rows": manual_rows,
        "nav_headers": ["检查表", "签约前应提交/核验", "合同应约定", "未落实的后果"],
        "nav_widths": [.16, .32, .28, .24],
        "nav_rows": [list(row) for row in _SIGNING_NAV],
        "uncovered_text": uncovered_text,
    }


def _condition_text(row):
    text = (row.get("fact") or "") + (row.get("recommendation") or "")
    if "安全生产许可证" in text or "安许" in text:
        return "补交有效期内的安全生产许可证"
    if "合同" in text:
        return "补交完整分包合同（含计价、支付、质量、安全、农民工工资条款）"
    if "社保" in text:
        return "补交主要管理人员近一个月社保缴费证明"
    if "资质" in text:
        return "补交覆盖分包范围的资质证书"
    if "营业执照" in text:
        return "补交营业执照"
    if "书面同意" in text or "签字" in text:
        return "补齐监理审查意见和发包人书面同意"
    if "负面清单" in text or "关键工序" in text:
        return "出具是否涉及负面清单关键工序的书面说明"
    return ""


def _conditions(issues):
    rows = []
    for row in issues:
        text = _condition_text(row)
        if text and text not in rows:
            rows.append(text)
    return rows[:5]


_RED_LINE_KINDS = {"negative_list", "transfer_scope", "illegal_subcontract"}


def _red_line_hit(raw):
    return next((item for item in raw
                 if item["kind"] in _RED_LINE_KINDS and item["status"] == "hit"), None)


def _condition_for_item(item, facts=None, contract_facts=None):
    if item.get('item_id') == 'RC-A10-cost':
        return '核对安全生产费单独约定及金额/计算方式，区分工程层计提与总包向分包支付责任；分包不再重复提取，实际付款和使用需另附凭证'
    if item.get("item_id") == "A2-14":
        return "逐人核对技术负责人的工程技术类职称证书、身份字段与分包规模；等级不足时补充对应等级证明或调整人员，名单声明不代替证书"
    if item.get("item_id") == "A2-10":
        return "逐人核对名单与所附证照的姓名、身份号、职称、类别、编号及有效期；未提取字段请对照原页确认，再按岗位核验适用要求"
    facts = facts or {}
    license_facts = facts.get("business_license") or {}
    qualification = facts.get("qualification") or {}
    safety = facts.get("safety_license") or {}
    if item.get("item_id") == "A2-09":
        if "未取得可核对的安全生产许可证原页" in item.get("message", ""):
            return "补交本分包单位的安全生产许可证原页，包含企业名称、证号、许可范围及有效期"
        if "许可范围字段未提取" in item.get("message", ""):
            return "已见安全生产许可证，请补充可读的许可范围字段或核对证照原页"
        return "核对安全生产许可证原页的企业名称、许可范围及实际作业性质的对应关系；许可行业不替代资质专业、等级和承包规模核查"
    # 历史结果仍写“资格证据不齐”时，以已提取到的三上证照事实为准，不重复要求补交。
    if (item.get("item_id") == "W16-01"
            and license_facts and qualification and safety):
        return ""
    # 劳务分包已提供施工劳务备案/资质证书，不再套用专业承包类别匹配条件。
    if (item.get("kind") == "qualification_match" and qualification
            and "施工劳务" in str(qualification.get("title") or "")):
        return ""
    text = item["label"] + item["message"]
    # 证照已覆盖基准日、仅需核对原件的，是人工核验动作，不是补正材料。
    if "覆盖基准日" in text and "需核对原件" in text:
        return ""
    if "安全生产许可证" in text or "安许" in text:
        if "早于基准日" in text:
            match = re.search(r"有效期至\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", text)
            when = "（有效期至%s）" % match.group(1) if match else ""
            return "安全生产许可证已提供%s，但已早于核查基准日到期，请提交续期后的证书" % when
        return "请提交安全生产许可证原件核对许可范围和有效期"
    if "营业执照" in text:
        return "补交营业执照" if any(word in text for word in ("未提供", "缺", "未查见")) \
            else "请提交营业执照原件核对登记信息"
    if "施工劳务备案" in text:
        return "已提供施工劳务备案证书；请核对证书原件及有效期"
    if "身份证" in text and any(word in text for word in ("超过有效期", "已过期", "已届满", "过期")):
        role = re.search(r"「([^」]{1,20})」", text)
        expiry = re.search(r"有效期至\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", text)
        who = role.group(1) if role else "相关人员"
        when = "（现证有效期至%s）" % expiry.group(1) if expiry else ""
        return "提交%s更新后的身份证，确保有效期覆盖核查基准日%s" % (who, when)
    if item.get("item_id") == "A2-08":
        return "确认分包工法及设备类型，补充缺失工种的实填名单和对应证书；人数仅提示，证件归属、有效期和真实性另行核对"
    if item.get("kind") == "personnel_adequacy":
        return "按分包合同及人员配置表补齐缺失的关键岗位人员，并附岗位证书、劳动合同及社保记录"
    if item.get("item_id") == "A2-01" or item.get("kind") == "person_consistency":
        if any(word in text for word in ("未能识别到社保", "未识别到社保", "社保信息里未提取到人员姓名")):
            return "补交主要人员社保缴费证明、劳动合同及证书"
        if "劳动合同" in text:
            return "补交主要人员劳动合同及证书；社保记录已提供，请核对覆盖人员和缴费期间"
    if "资质" in text:
        if any(word in text for word in ("未提供", "未取得", "缺")):
            return "补交资质证书，说明资质类别/等级覆盖分包范围"
        return "已提供资质证书；请补充说明资质类别/等级覆盖分包范围"
    if any(word in text for word in ("社保", "劳动合同", "人员", "证书")):
        return "补交主要人员社保缴费证明、劳动合同及证书"
    if any(word in text for word in ("书面同意", "签字", "监理审查")):
        return "补齐监理审查意见、发包人书面同意及签字日期"
    if any(word in text for word in ("负面清单", "关键性工作", "关键工序")):
        if "需补充确认" in text:
            return "补充负面清单相关参数及工作内容说明后重新核对"
        if "发现" in text and "问题" in text:
            return "按负面清单问题清单调整分包范围或补充说明"
        match = re.search(r"已评估\s*(\d+)\s*/\s*(\d+)\s*条", text)
        ratio_incomplete = bool(match) and int(match.group(1)) < int(match.group(2))
        incomplete = ratio_incomplete or any(
            word in text for word in ("覆盖不足", "不足以确认", "未完成或仍需核实", "未召回"))
        if ratio_incomplete:
            return ("补充负面清单逐条对照说明及人工核查结论（当前仅完成 %s/%s 项）"
                    % match.groups())
        if incomplete:
            return "补充负面清单逐条对照说明及人工核查结论（当前覆盖不足）"
        return "出具不涉及负面清单关键性工作的书面说明"
    if any(word in text for word in ("转包", "违法分包", "再分包")):
        return "补充总包合同、现场管理及资金流资料，说明不存在转包、违法分包"
    if "合同" in text:
        # 合同正文已在本次材料中定位时，不再要求「补交完整分包合同」。
        if (contract_facts or {}).get("pages"):
            return ""
        return "补交完整分包合同（计价、支付、质量、安全、农民工工资条款）"
    if "设备" in text:
        return "补交设备清单及权属/租赁证明"
    if any(word in text for word in ("信用", "事故")):
        return "补充内部信用或事故核查材料"
    return "补充或核实相关材料后重新报审"


def _condition_priority(text):
    if "安全生产许可证" in text:
        return 0
    if "资质" in text or "营业执照" in text:
        return 1
    if "社保" in text or "劳动合同" in text:
        return 2
    if "书面同意" in text or "签字" in text:
        return 3
    if "负面清单" in text or "关键性工作" in text:
        return 4
    if "转包" in text or "违法分包" in text:
        return 5
    if "合同" in text:
        return 6
    return 7


_PERSONNEL_CONDITION_MARKERS = ("社保", "劳动合同", "主要人员", "岗位证书")


def _is_personnel_condition(text):
    return any(word in text for word in _PERSONNEL_CONDITION_MARKERS)


def _merge_personnel_conditions(conditions):
    """同一组人员材料只留一条补正，避免“社保已提供”与“补交社保”同时出现。"""
    personnel = [text for text in conditions if _is_personnel_condition(text)]
    if len(personnel) < 2:
        return conditions
    social_ready = any("社保记录已提供" in text or "社保缴费记录已在册" in text
                       for text in personnel)
    canonical = ("补交主要人员劳动合同及所缺岗位证书；社保缴费记录已在册，请核对覆盖人员和缴费期间"
                 if social_ready else personnel[0])
    merged, inserted = [], False
    for text in conditions:
        if not _is_personnel_condition(text):
            merged.append(text)
        elif not inserted:
            merged.append(canonical)
            inserted = True
    return merged


def _merge_negative_conditions(conditions):
    """负面清单多条候选只留一条；有覆盖比例时优先，避免退化成“出具书面说明”。"""
    negatives = [text for text in conditions if "负面清单" in text]
    if len(negatives) < 2:
        return conditions
    preferred = next((text for text in negatives if "仅完成" in text or "覆盖不足" in text),
                     negatives[0])
    merged, inserted = [], False
    for text in conditions:
        if "负面清单" not in text:
            merged.append(text)
        elif not inserted:
            merged.append(preferred)
            inserted = True
    return merged


def _signing_conditions(raw, facts=None, contract_facts=None):
    rows = []
    for item in raw:
        if _verdict(item) == "符合" or item.get("material_only"):
            continue
        text = _condition_for_item(item, facts, contract_facts)
        if not text:
            continue
        rows.append((0 if _verdict(item) in {"不符合", "缺项"} else 1,
                     _condition_priority(text), item["item_id"], text))
    conditions = []
    for *_order, text in sorted(rows):
        if text not in conditions:
            conditions.append(text)
    return _merge_negative_conditions(_merge_personnel_conditions(conditions))[:5]


def _signing_conclusion(raw, facts=None, contract_facts=None):
    red = _red_line_hit(raw)
    if red:
        return {
            "text": "不同意签订该分包合同。",
            "conditions": [],
            "red_line": True,
            "blocking_count": 1,
        }
    conditions = _signing_conditions(raw, facts, contract_facts)
    if conditions:
        return {
            "text": "原则同意，附条件通过。下列事项补正并复核后方可签订该分包合同：",
            "conditions": conditions,
            "red_line": False,
            "blocking_count": len(conditions),
        }
    blocking = [item for item in raw
                if _verdict(item) != "符合" and not item.get("material_only")]
    if blocking:
        return {
            "text": "原则同意，附条件通过。请补充或核实下列材料后签订该分包合同：",
            "conditions": ["补充或核实相关材料后重新报审"],
            "red_line": False,
            "blocking_count": len(blocking),
        }
    return {
        "text": "同意签订该分包合同。",
        "conditions": [],
        "red_line": False,
        "blocking_count": 0,
    }


_SCOPE_WORDS = ("建筑劳务分包", "施工劳务", "建设工程施工", "施工专业作业",
                "市政公用", "公路工程", "地基基础", "桥梁工程", "隧道工程", "机电工程")


def _scope_keywords(scope):
    return "、".join(word for word in _SCOPE_WORDS if word in str(scope or ""))


# 资质类别 → 可覆盖的分包内容关键词；只做保守映射，命中不了就转人工。
_QUALIFICATION_SCOPE_MAP = (
    ("地基基础", ("桩基", "基坑", "地基处理", "地基基础")),
    ("桥梁", ("桥梁", "桥面", "梁体", "预制梁", "吊装", "支座")),
    ("隧道", ("隧道", "洞身", "盾构", "明挖")),
    ("公路", ("公路", "路基", "路面", "匝道", "涵洞", "交通工程")),
    ("市政", ("市政", "道路", "排水", "管网", "给水")),
    ("机电", ("机电", "电气", "照明", "通信", "监控")),
    ("建筑", ("房建", "建筑", "装饰", "幕墙", "钢结构")),
    ("环保", ("环保", "绿化", "水保", "噪声")),
)


def _qualification_entries(facts):
    entries = [row for row in (facts.get("qualifications") or []) if isinstance(row, dict)]
    primary = facts.get("qualification") or {}
    identity = (primary.get("cert_no"), primary.get("category"), primary.get("level"))
    if primary and not any(
            (row.get("cert_no"), row.get("category"), row.get("level")) == identity for row in entries):
        entries.insert(0, primary)
    return entries


def _qualification_label(entry):
    category = str(entry.get("category") or "").strip()
    level = str(entry.get("level") or "").strip()
    if category and level and level not in category:
        return category + level
    return category or str(entry.get("title") or "").strip() or "资质证书"


def _scope_match(category, content):
    category, content = str(category or ""), str(content or "")
    for keyword, aliases in _QUALIFICATION_SCOPE_MAP:
        if keyword in category:
            return any(alias in content for alias in aliases) or keyword in content
    parts = [part.strip() for part in re.split(r"[、,，/；;]", category) if part.strip()]
    return bool(parts) and any(part in content for part in parts)


def _qualification_lines(document, info, is_labor):
    """第一章资质承接能力：主资质匹配 + 营业执照/法人 + 配套资质；安许不在此章。"""
    facts = document.get("certificate_facts") or {}
    license_facts = facts.get("business_license") or {}
    entries = _qualification_entries(facts)
    content = str(info.get("分包事项") or info.get("拟分包范围") or "")
    primary = entries[0] if entries else {}
    lines, conclusion = [], ""
    if is_labor and (primary.get("category") == "施工劳务备案"
                     or "施工劳务" in str(primary.get("title") or "")):
        label = str(primary.get("title") or "").strip() or _qualification_label(primary)
        details = []
        if primary.get("cert_no"):
            details.append("证书编号" + primary["cert_no"])
        if primary.get("valid_until"):
            details.append("有效期至" + primary["valid_until"])
        lines.append("持有%s（%s）。" % (label, "，".join(details) if details else "证书编号/有效期未载明"))
        lines.append("施工劳务不分等级，可承接本工程劳务作业。")
        conclusion = "资质与承接能力已核对；施工劳务备案不区分等级，可承接本工程劳务作业。"
    elif not entries:
        lines.append("报审材料未载明资质类别/等级，无法判断是否覆盖分包内容，需人工核实。")
        conclusion = "主体资格材料已核对；资质类别/等级未识别，需核对证书原件后确认。"
    else:
        label = _qualification_label(primary)
        matched = _scope_match(primary.get("category") or label, content)
        details = []
        if primary.get("cert_no"):
            details.append("证书编号" + primary["cert_no"])
        if primary.get("valid_until"):
            details.append("有效期至" + primary["valid_until"])
        line = "分包单位具备%s" % label
        if details:
            line += "（%s）" % "，".join(details)
        if matched:
            line += "，与%s分包内容匹配，符合单位工程资质要求。" % (content or "本工程")
        else:
            line += "；与%s分包内容的对应关系需人工核实（现有证书未载明可覆盖该内容的类别/等级）。" % (
                content or "本工程")
        lines.append(line)
        conclusion = "资质与承接能力已核对；%s与分包内容%s。" % (
            label, "匹配" if matched else "对应关系需人工核实")
        supporting = []
        for entry in entries[1:]:
            item = _qualification_label(entry)
            if item and item not in supporting:
                supporting.append(item)
        if supporting:
            lines.append("另持有%s等配套资质，可覆盖相应专业施工需要；是否覆盖本分包内容需结合证书原件核对。"
                         % "、".join(supporting[:4]))
    license_parts = []
    if license_facts.get("name"):
        license_parts.append("营业执照登记名称" + license_facts["name"])
    if license_facts.get("uscc"):
        license_parts.append("统一社会信用代码" + license_facts["uscc"])
    if license_facts.get("legal_rep"):
        license_parts.append("法定代表人" + license_facts["legal_rep"])
    if license_facts.get("registered_capital"):
        license_parts.append("注册资本" + license_facts["registered_capital"])
    scope = _scope_keywords(license_facts.get("business_scope"))
    if scope:
        license_parts.append("经营范围含" + scope)
    if license_parts:
        lines.append("，".join(license_parts) + "，具备法人资格和相应履约能力。")
    else:
        lines.append("报审材料未载明营业执照/法人信息，需人工核实。")
    return lines, conclusion


def _parse_date(value):
    match = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", str(value or ""))
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _baseline_date(document):
    return _parse_date((document.get("basic_information") or {}).get("核查基准日"))


def _expired(valid_until, baseline):
    valid = _parse_date(valid_until)
    return bool(valid and baseline and valid < baseline)


def _safety_summary_lines(safety, baseline):
    cert_no = str(safety.get("cert_no") or "").strip()
    scope = str(safety.get("scope") or "").strip()
    valid = str(safety.get("valid_until") or "").strip()
    if not (cert_no or scope or valid):
        # 只在合同条款里出现的“安全生产许可证”不算提供了证照页，不能写成“持有”。
        return ["报审材料提及安全生产许可证，但未识别证号与有效期，需核对原件。"]
    details = []
    if cert_no:
        details.append("证书号" + cert_no)
    if scope:
        details.append("许可范围“%s”" % scope)
    line = "持有安全生产许可证"
    if details:
        line += "（%s）" % "，".join(details)
    lines = [line + "。"]
    if valid:
        lines.append("有效期至%s。" % valid)
        if _expired(valid, baseline):
            lines.append("注意：该有效期已早于核查基准日，当前不能认定安许有效；"
                         "须补充续期后的有效证件，否则不得进场施工。")
        else:
            lines.append("有效期覆盖核查基准日；证书号、许可范围需核对原件。")
    else:
        lines.append("有效期未载明，需核对证书原件。")
    return lines


def _safety_summary_conclusion(safety, baseline):
    valid = str(safety.get("valid_until") or "").strip()
    if valid and _expired(valid, baseline):
        return ("安全生产许可证已提供，但有效期至%s，早于核查基准日，暂不能认定有效；"
                "需提交续期后的证书后重新报审。" % valid)
    if valid:
        return "安全生产许可证在有效期内；许可范围和证书号需核对原件。"
    return "未取得可核对的安全许可证有效期，需补充证书后核实。"


def _section1_conclusion(document, section, is_labor):
    if section["counts"]["不符合"] or section["counts"]["缺项"]:
        return section["conclusion"]
    facts = document.get("certificate_facts") or {}
    qualification = facts.get("qualification") or {}
    baseline = _baseline_date(document)
    _, conclusion = _qualification_lines(document, document.get("basic_information") or {}, is_labor)
    if _expired(qualification.get("valid_until"), baseline):
        return "主体资格材料已核对；资质证书有效期至%s，早于核查基准日，需提交续期证书后确认。" % (
            qualification["valid_until"])
    return conclusion


def _section_fact_lines(document, info, is_labor):
    """第一章的已核实事实：资质匹配 + 营业执照/法人 + 配套资质；安许归第二章。"""
    lines, _ = _qualification_lines(document, info, is_labor)
    equipment = document.get("equipment_facts") or {}
    equipment_pages = sorted({page for page in equipment.get("pages") or []
                              if isinstance(page, int) and not isinstance(page, bool) and page > 0})
    if equipment_pages:
        pages = ("第%d页" % equipment_pages[0]) if len(equipment_pages) == 1 \
            else "第%d–%d页" % (equipment_pages[0], equipment_pages[-1])
        lines.append("报审资料含设备清单或机械配置（%s）；设备权属、租赁及检定证明需核对。" % pages)
    content = info.get("分包事项") or info.get("拟分包范围")
    amount = info.get("拟分包金额")
    if content:
        line = "合同约定分包内容为%s" % content
        if amount:
            line += "，金额%s" % amount
        lines.append(line + "。")
    if not lines:
        lines.append("本次未取得营业执照、资质证书或安全生产许可证的可核对字段，需补充材料后核对。")
    return lines


def _blocks(document):
    blocks = []

    def paragraph(text, *, bold=False, align=None, indent=False):
        item = {"type": "paragraph", "text": text}
        if bold:
            item["bold"] = True
        if align:
            item["align"] = align
        if indent:
            item["indent"] = True
        blocks.append(item)

    def heading(text):
        blocks.append({"type": "heading", "text": text})

    def table(headers, rows, widths):
        blocks.append({"type": "table", "headers": headers, "rows": rows, "widths": widths})

    info = document["basic_information"]
    is_labor = "劳务" in str(info.get("分包类型") or "")
    unit_label = "分包单位"
    content_label = "分包内容"
    amount_label = "分包金额"

    paragraph("以下依据《公路工程施工分包管理办法》、《公路工程施工分包负面清单》、"
              "《上海市公路工程施工分包管理实施细则》及报审材料，出具审查意见。")
    if document.get("reading_statement"):
        paragraph(document["reading_statement"])
    heading("一、建设单位审查意见（正式表述）")
    paragraph("工程名称：%s" % _text(info.get("工程名称")))
    paragraph("建设单位：%s" % _text(info.get("建设单位") or info.get("建设单位（业主）")))
    paragraph("总包单位：%s" % _text(info.get("总包单位") or info.get("施工总承包单位")))
    paragraph("%s：%s" % (unit_label, _text(info.get("分包单位"))))
    paragraph("%s：%s" % (content_label, _text(info.get("分包事项") or info.get("拟分包范围"))))
    paragraph("%s：%s" % (amount_label, _text(info.get("拟分包金额"))))
    paragraph("经核对分包报审资料、资质证书、安许、人员证书及社保记录，建设单位意见如下：")

    sections = [row for row in document["sections"] if row["status"] != "未核查"]
    skipped = [row["title"].split(". ", 1)[-1] for row in document["sections"] if row["status"] == "未核查"]
    if skipped:
        paragraph("本次未涉及：%s。" % "、".join(skipped))

    for section in sections:
        heading(section["title"])
        if section["title"].startswith("1."):
            fact_lines = _section_fact_lines(document, info, is_labor)
            conclusion_text = _section1_conclusion(document, section, is_labor)
        else:
            fact_lines = section.get("summary_lines") or []
            conclusion_text = section.get("summary_conclusion") or section["conclusion"]
        for line in fact_lines:
            paragraph(line, indent=True)
        paragraph("结论：" + conclusion_text, bold=True)
        bullets = section.get("bullets") or []
        if fact_lines and section["title"].startswith("1."):
            bullets = [bullet for bullet in bullets
                       if not bullet["text"].startswith("已核对材料：")]
        for bullet in bullets:
            paragraph("· " + bullet["text"], bold=bullet.get("bold"), indent=True)

    if document.get("manual_prompts"):
        heading("履约核对提示")
        paragraph("以下事项供人工结合履约材料核对，不作为本次机器不符合或签署附加条件。")
        for row in document["manual_prompts"]:
            paragraph("· " + row["message"], indent=True)
            if row["source"]:
                paragraph("核对说明：" + row["source"], indent=True)
            for location in row["locations"]:
                paragraph(location, indent=True)

    heading("7. 结论")
    signing = document.get("signing_conclusion") or {}
    conditions = signing.get("conditions") or []
    paragraph(signing.get("text") or "原则同意，附条件通过。", bold=True)
    for index, text in enumerate(conditions, 1):
        paragraph("%d. %s。" % (index, text), indent=True)
    if signing.get("red_line"):
        paragraph("红线事项整改完成前，不得签订或实施该分包合同。")
    paragraph("工资专户、专项施工方案、安全生产费使用计划、设备检定等，"
              "合同中应约定于开工前提交，不作为本次签约缺件。")
    paragraph("指挥部/建设单位（章）：____________________")
    paragraph("日期：____年__月__日")

    limitations = [str(text).strip() for text in document.get("limitations") or []
                   if str(text).strip()]
    if limitations:
        heading("8. 本次核查范围与限制")
        paragraph("下列情形本轮未纳入核查或受材料限制，不能解释为已核查通过：")
        for text in dict.fromkeys(limitations):
            paragraph("· " + text, indent=True)

    blocks.append({"type": "page_break"})
    appendix = document.get("appendix") or {}
    heading("附件一：本次自动核查结果")
    paragraph("本表只列系统已形成确定结论的事项；同一材料组的多项材料合并为一行，"
              "行数不等于正文核查项数；待核实、未覆盖事项见附件二。")
    if appendix.get("auto_rows"):
        table(appendix["auto_headers"], appendix["auto_rows"], appendix["auto_widths"])
    else:
        paragraph("本次自动核查未形成确定性结论，相关事项见附件二。")

    heading("附件二：8 表签约前核查明细")
    paragraph("本附件全量展示 8 张检查表。每张表先列已形成自动结论的事项，再列未自动核查事项及原因；"
              "工资专户、专项方案、安全费计划、设备检定等开工前事项，签约时只核对合同是否约定。")
    for sheet in document.get("attachments") or []:
        paragraph(sheet.get("title") or "", bold=True)
        if sheet.get("note"):
            paragraph(sheet["note"], indent=True)
        if sheet.get("kind") == "key_value":
            rows = [list(row) for row in sheet.get("rows") or []]
            if rows:
                table(["项目", "内容"], rows, [.25, .75])
            continue
        if sheet.get("auto_rows"):
            table(sheet.get("auto_headers") or [], sheet["auto_rows"],
                  sheet.get("auto_widths") or [])
        if sheet.get("manual_rows"):
            paragraph("未自动核查事项及原因：", indent=True)
            table(sheet.get("manual_headers") or [], sheet["manual_rows"],
                  sheet.get("manual_widths") or [])

    return blocks


def _reading_statement(report):
    """阅读覆盖说明（设计 2026-11-18 §7.5）：未读页必须让签署人看见。

    ``reading_coverage`` 行与报告 payload 同源：页字段是页码列表（不是计数）。
    """
    rows = [row for row in (report.get("reading_coverage") or []) if isinstance(row, dict)]
    if not rows:
        return "本次提交材料未发现未读页、空白页或低质扫描页；结论基于可读取文本与附件交叉核对。"

    def _pages(row, key):
        value = row.get(key) or []
        return len(value) if isinstance(value, (list, tuple, set)) else 0

    unread = sum(_pages(row, "unread_pages") for row in rows)
    over = sum(_pages(row, "over_limit_pages") for row in rows)
    blank = sum(_pages(row, "blank_pages") for row in rows)
    suspect = sum(_pages(row, "suspect_pages") for row in rows)
    names = "、".join(dict.fromkeys(str(row.get("file_name") or "材料") for row in rows))
    return ("本次共 %d 份材料存在阅读受限（%s）：未读 %d 页、超限 %d 页、空白 %d 页、疑似低质 %d 页；"
            "已读页结论不受影响，受限页涉及事项已在正文标注需人工核对原件。"
            % (len(rows), names, unread, over, blank, suspect))


# 结论态跨表归一：附件一/附件二/正文用词不同（通过/是/否/无法核实），
# 但语义必须一致；「否」在附件二同时承载不符合与缺项，故映射为二值集合。
_STATE_ALIASES = {
    "符合": frozenset(("符合",)), "通过": frozenset(("符合",)), "是": frozenset(("符合",)),
    "不符合": frozenset(("不符合",)), "否": frozenset(("不符合", "缺项")),
    "缺项": frozenset(("缺项",)),
    "待核实": frozenset(("待核实",)), "无法核实": frozenset(("待核实",)),
}


def _states(value):
    return _STATE_ALIASES.get(str(value or "").strip(), frozenset())


def consistency_violations(document, raw):
    """报告一致性后校验（设计 2026-11-18 §2.6）：返回冲突描述列表。

    比对四个消费面：结论计数、正文分节状态、问题/待核实清单、附件一/附件二。
    任何一面与逐项判定（`raw`）冲突即拒绝导出——纸面自相矛盾比缺少结论更危险。
    """
    problems = []
    verdicts = [_verdict(item) for item in raw]
    counts = {verdict: verdicts.count(verdict)
              for verdict in ("符合", "不符合", "缺项", "待核实")}
    conclusion = document.get("conclusion") or {}

    risk_counts = conclusion.get("risk_counts") or {}
    for verdict in ("不符合", "缺项", "待核实"):
        if risk_counts.get(verdict) != counts[verdict]:
            problems.append("结论计数「%s」为 %s，逐项判定为 %d"
                            % (verdict, risk_counts.get(verdict), counts[verdict]))
    if document.get("pending_total") != counts["待核实"]:
        problems.append("待核实合计为 %s，逐项判定为 %d"
                        % (document.get("pending_total"), counts["待核实"]))

    has_risk = any(counts[verdict] for verdict in ("不符合", "缺项", "待核实"))
    if conclusion.get("suggested") == "同意" and has_risk:
        problems.append("结论为「同意」但存在风险事项")
    if conclusion.get("suggested") == "原则同意，附条件通过" and not has_risk:
        problems.append("结论为「附条件通过」但没有任何风险事项")

    issues = document.get("issues") or []
    categories = {row.get("category") for row in issues}
    for verdict in ("不符合", "缺项"):
        if counts[verdict] and verdict not in categories:
            problems.append("存在 %d 条「%s」但问题表未列出" % (counts[verdict], verdict))
    if len(issues) > counts["不符合"] + counts["缺项"]:
        problems.append("问题表 %d 行，多于不符合+缺项 %d 条"
                        % (len(issues), counts["不符合"] + counts["缺项"]))
    for row in issues:
        if row.get("category") not in {"不符合", "缺项"}:
            problems.append("问题表第 %s 行结论档位非法：%s"
                            % (row.get("number"), row.get("category")))
        if not all(str(row.get(key) or "").strip()
                   for key in ("fact", "reason", "impact", "recommendation", "responsible_party")):
            problems.append("问题表第 %s 行四段式字段不完整" % row.get("number"))

    pending = document.get("pending") or []
    if counts["待核实"] and not pending:
        problems.append("存在 %d 条待核实但待核实清单为空" % counts["待核实"])
    if len(pending) > counts["待核实"]:
        problems.append("待核实清单 %d 行，多于待核实 %d 条" % (len(pending), counts["待核实"]))
    for row in pending:
        if not all(str(row.get(key) or "").strip()
                   for key in ("fact", "reason", "recommendation")):
            problems.append("待核实清单第 %s 行字段不完整" % row.get("number"))

    for section in document.get("sections") or []:
        if section.get("status") != "通过":
            continue
        section_counts = section.get("counts") or {}
        bad = [verdict for verdict in ("不符合", "缺项", "待核实") if section_counts.get(verdict)]
        if bad:
            problems.append("分节「%s」标为通过但含%s"
                            % (section.get("title"), "、".join(bad)))

    attachment_states = {}
    for sheet in document.get("attachments") or []:
        for row in sheet.get("auto_rows") or []:
            if len(row) >= 2:
                attachment_states.setdefault(str(row[0]), set()).update(_states(row[1]))
    for row in (document.get("appendix") or {}).get("auto_rows") or []:
        states = _states(row[1]) if len(row) >= 2 else frozenset()
        other = attachment_states.get(str(row[0]))
        if states and other is not None and not (states & other):
            problems.append("附件一「%s」为「%s」，附件二同项为「%s」"
                            % (row[0], row[1], "、".join(sorted(other))))
    return problems


def wording_violations(node, path=""):
    """报告文本后校验（设计 2026-11-18 §7.4）：返回 [(禁用词, 位置)]。"""
    hits = []
    if isinstance(node, str):
        for word in _BANNED_WORDING:
            if word in node:
                hits.append((word, path or "text"))
    elif isinstance(node, dict):
        for key, value in node.items():
            hits.extend(wording_violations(value, "%s.%s" % (path, key)))
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            hits.extend(wording_violations(value, "%s[%d]" % (path, index)))
    return hits


def build_document(report: dict, base: dict | None = None) -> dict:
    """Build V1.4 from the same frozen facts as V1.3."""
    base = base or v13.build_document(report)
    if base.get("available") is False:
        return {"template_id": TEMPLATE_ID, "template_version": TEMPLATE_VERSION,
                "available": False, "reason": base.get("reason") or "V1.4 当前不可用"}
    raw = _raw_items(report)
    conclusion = _conclusion(base, raw)
    facts = deepcopy(report.get("certificate_facts") or {})
    contract_facts = deepcopy(report.get("contract_clauses") or {})
    personnel_facts = deepcopy(report.get("personnel_roster") or [])
    personnel_social = deepcopy(report.get("personnel_social") or [])
    equipment_facts = deepcopy(report.get("equipment_facts") or {})
    document = {
        "schema_version": SCHEMA_VERSION,
        "template_id": TEMPLATE_ID,
        "template_version": TEMPLATE_VERSION,
        "mapping_version": MAPPING_VERSION,
        "available": True,
        "title": base["title"].replace("（AI初审）", ""),
        "recipient": base["recipient"],
        "basic_information": deepcopy(base["basic_information"]),
        "conclusion": conclusion,
        "signing_conclusion": _signing_conclusion(raw, facts, contract_facts),
        "sections": _sections(base, raw, contract_facts, personnel_facts, facts,
                              _baseline_date({"basic_information": base.get("basic_information") or {}}),
                              personnel_social),
        "issues": _issue_rows(raw),
        "pending": _pending_rows(raw),
        "manual_prompts": _manual_prompt_rows(report),
        "pending_total": sum(1 for item in raw if _verdict(item) == "待核实"),
        "attachments": _attachments(base, raw, contract_facts),
        "appendix": _appendix(base, raw, contract_facts),
        "certificate_facts": facts,
        "contract_clauses": contract_facts,
        "personnel_roster": personnel_facts,
        "personnel_social": personnel_social,
        "equipment_facts": equipment_facts,
        "basis": deepcopy(base["basis"]),
        "limitations": deepcopy(base["limitations"]),
        "reading_coverage": deepcopy(report.get("reading_coverage") or []),
        "reading_statement": _reading_statement(report),
        "issued_date": base.get("issued_date") or "",
        "statistics": deepcopy(base.get("statistics") or {}),
        "trace": deepcopy(base.get("trace") or {}),
    }
    document["blocks"] = _blocks(document)
    # 一致性校验（fail-closed，设计 2.6）：结论计数/分节/清单/附件必须同源。
    conflicts = consistency_violations(document, raw)
    if conflicts:
        raise ValueError("报告内部不一致，已阻断导出：%s" % "；".join(conflicts[:3]))
    # 后校验（fail-closed）：内部状态词与机器计数不得进入正式文本。
    violations = wording_violations({
        "blocks": document["blocks"],
        "issues": document["issues"],
        "pending": document["pending"],
        "signing_conclusion": document["signing_conclusion"],
        "reading_statement": document["reading_statement"],
    })
    if violations:
        word, where = violations[0]
        raise ValueError("报告文本含禁用词“%s”（%s），已阻断生成" % (word, where))
    return document


def select_template(payload: dict, template_id: str) -> dict:
    """Remove frozen alternatives from legacy responses; never rebuild a history."""
    documents = payload.pop("template_documents", None) or {}
    if template_id == "legacy":
        return payload
    if template_id != TEMPLATE_ID:
        raise ValueError("template_id 仅支持 legacy / v1.3 / v1.4")
    document = documents.get(TEMPLATE_ID)
    if not document:
        raise LookupError("该历史版本未冻结 V1.4 模板，请使用 V1.3 或旧模板；不会用当前材料重建历史报告。")
    if not document.get("available"):
        raise LookupError(document.get("reason") or "V1.4 模板当前不可用")
    payload["template_document"] = deepcopy(document)
    return payload
