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
MAPPING_VERSION = "2026-11-17.1"
UNKNOWN = "材料未载明"
_PLAIN_LABELS = {
    "法定签字链": "监理及发包人签署",
    "集团报审签字链": "集团报审流程签署",
}
_PLAIN_SOURCES = {
    "A3-02-legal": "《上海市公路工程施工分包管理实施细则》（沪交行规〔2026〕8号）附件3/附件4 签字栏",
    "A3-02-group": "集团分包报审表签字栏（项目经理/专业监理/总监/指挥部/直属单位）",
}


def _plain_label(label):
    return _PLAIN_LABELS.get(str(label or "").strip(), label)


def _plain_source(item_id, source):
    return _PLAIN_SOURCES.get(str(item_id or "").strip(), source)

# 8 表附件：审查项 / 审查要点 / 归属维度 / 命中关键词 / 是否有自动内核
_SHEETS = (
    {
        "code": "legality",
        "title": "一、分包合法性审查",
        "note": "判定：分包工程不得突破《公路工程施工分包负面清单（2024年版）》；桥梁、隧道主体及关键性工程不得分包；分包须发包人书面同意。",
        "rows": (
            ("分包范围合规性", "分包内容是否落在负面清单以外；是否涉及桥梁/隧道主体及关键性工程", "compliance", ("负面清单", "分包范围"), True),
            ("发包人书面同意", "总承包合同是否允许分包；是否已取得发包人书面同意文件", "compliance", ("书面同意", "审批", "信息表"), True),
            ("资质等级匹配", "分包人资质是否覆盖本工程专业要求（单位工程有资质要求的须专业承包资质）", "qualification_credit", ("资质",), True),
            ("非整体转包/肢解", "是否存在整体转包、肢解分包迹象", "compliance", ("转包", "肢解", "整体"), True),
            ("禁止再分包", "合同是否明确禁止二次分包、以劳务合作变相施工分包", "compliance", ("再分包", "二次分包", "劳务合作"), True),
        ),
    },
    {
        "code": "personnel_org",
        "title": "二、现场管理机构与人员锁定审查",
        "note": "判定：承包人、分包人均须设现场管理机构；主要人员须为本单位人员（劳动合同+社保+工资流水），人证一致。",
        "rows": (
            ("承包人现场机构", "承包人是否设现场管理机构，是否实质管理（非只收管理费）", "compliance", ("现场管理机构", "派驻"), True),
            ("分包人现场机构", "分包人是否设立现场管理机构（项目负责人、技术、质量、安全、计量、财务）", "personnel", ("现场管理机构", "项目负责人"), True),
            ("劳动合同与社保", "主要人员是否提供劳动合同+社保关系证明", "personnel", ("劳动合同", "社保"), True),
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
            ("安全生产费单列", "是否按不低于分包建筑安装工程费 1.5% 单列、专款专用", "quality_safety_progress", ("安全生产费",), False),
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
            if item.get("status") not in {"hit", "miss", "undecidable"}:
                continue
            rows.append({
                "kind": kind,
                "item_id": item.get("item_id") or "",
                "status": item.get("status"),
                "material_missing": item.get("material_missing") is True,
                "material_only": item.get("material_only") is True,
                "label": _plain_label(_text(item.get("label") or item.get("item_id"), "核查事项")),
                "message": _text(item.get("message"), "未载明事实"),
                "source": _plain_source(item.get("item_id"), _text(item.get("source"), "")),
                "recommendation": _text(item.get("recommendation"), ""),
            })
    return rows


def _verdict(item):
    if item["status"] == "hit" and item["material_missing"]:
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
    dims = _dims(base)
    if (dims.get("compliance") or {}).get("verdict") == "不符合":
        return "不同意"
    if any(_verdict(item) in {"不符合", "缺项"} for item in raw):
        return "附条件同意"
    if any(_verdict(item) == "待核实" for item in raw):
        return "待补正"
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
            "待补正": verdicts.count("待核实"),
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
    return message if label and label in message else "%s：%s" % (label, message)


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
    ("personnel", "3. 人员配置与持证", ("person_consistency", "cert_validity"),
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
        if negative:
            match = re.search(r"已评估\s*(\d+)\s*/\s*(\d+)\s*条", coverage["message"]) if coverage else None
            ratio_incomplete = bool(match) and int(match.group(1)) < int(match.group(2))
            incomplete = ratio_incomplete or (bool(coverage) and any(
                word in coverage["message"] for word in ("覆盖不足", "不足以确认", "未完成或仍需核实", "未召回")))
            if match and ratio_incomplete:
                lines.append("负面清单仅完成 %s/%s 项核对，未覆盖部分不能认定未命中。" % match.groups())
            elif incomplete:
                lines.append("负面清单核查覆盖不足，未覆盖部分不能认定未命中。")
            else:
                lines.append("已对负面清单关键性工作进行核对，现有材料未形成明确命中结论。")
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


def _personnel_summary_lines(facts):
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
    if not rows:
        return ["未取得主要管理人员名单的实填姓名和岗位，需补充或人工核对。"]
    return ["已提取主要管理人员：" + "、".join(rows[:8]) + "。",
            "人员与社保、劳动合同、证书的对应关系仍需核对。"]


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
        return "人员材料已纳入核对，社保缴费记录和证书对应关系尚未形成一致结论。"
    if code == "legality":
        coverage = next((item for item in items if item["item_id"] == "W16-02"), None)
        match = re.search(r"已评估\s*(\d+)\s*/\s*(\d+)\s*条", coverage["message"]) if coverage else None
        ratio_incomplete = bool(match) and int(match.group(1)) < int(match.group(2))
        incomplete = ratio_incomplete or (bool(coverage) and any(
            word in coverage["message"] for word in ("覆盖不足", "不足以确认", "未完成或仍需核实", "未召回")))
        if match and ratio_incomplete:
            return ("负面清单仅完成 %s/%s 项核对，未覆盖部分不能认定未命中；"
                    "转包、违法分包需结合材料核对。" % match.groups())
        if incomplete:
            return "负面清单核查覆盖不足，未覆盖部分不能认定未命中；转包、违法分包需结合材料核对。"
        return "已核对部分分包范围，未发现负面清单明确命中；转包、违法分包需结合材料核对。"
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


def _sections(base, raw, contract_facts=None, personnel_facts=None):
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
        if code == "contract" and contract_facts and contract_facts.get("pages"):
            status = "待核实"
            summary_lines = _contract_summary_lines(contract_facts)
            summary_conclusion = _contract_summary_conclusion(contract_facts)
            conclusion_text = summary_conclusion
            bullets = []
        if code == "personnel" and personnel_facts:
            summary_lines = _personnel_summary_lines(personnel_facts)
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
    if item.get("kind") in {"qualification_match", "person_consistency", "cert_validity", "internal_records"}:
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


def _dimension_result(dimension, keywords, items, covered):
    matched = [item for item in items
               if any(word in (item["label"] + item["message"]) for word in keywords)]
    if matched:
        verdicts = [_verdict(item) for item in matched]
        for verdict in ("不符合", "缺项", "待核实"):
            if verdict in verdicts:
                first = next(item for item in matched if _verdict(item) == verdict)
                reason = first["source"] or first["message"]
                if verdict == "缺项":
                    reason = "未提供%s" % first["label"]
                return verdict, reason, first["recommendation"] or (
                    "补交" + first["label"] if verdict == "缺项" else "人工核实")
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
        for item, point, dimension_code, keywords, covered in sheet["rows"]:
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
                    "本版本未建立该审查项的自动核查规则",
                    point + "；请核对合同、证照原件或现场资料",
                    responsibility,
                ])
                continue
            verdict, reason, fix = _dimension_result(
                dims.get(dimension_code), keywords, raw, covered)
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
        for item, point, dimension_code, keywords, covered in sheet["rows"]:
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
                dims.get(dimension_code), keywords, raw, covered)
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


def _condition_for_item(item, facts=None):
    facts = facts or {}
    license_facts = facts.get("business_license") or {}
    qualification = facts.get("qualification") or {}
    safety = facts.get("safety_license") or {}
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


def _signing_conditions(raw, facts=None):
    rows = []
    for item in raw:
        if _verdict(item) == "符合" or item.get("material_only"):
            continue
        text = _condition_for_item(item, facts)
        if not text:
            continue
        rows.append((0 if _verdict(item) in {"不符合", "缺项"} else 1,
                     _condition_priority(text), item["item_id"], text))
    conditions = []
    for *_order, text in sorted(rows):
        if text not in conditions:
            conditions.append(text)
    return _merge_negative_conditions(_merge_personnel_conditions(conditions))[:5]


def _signing_conclusion(raw, facts=None):
    red = _red_line_hit(raw)
    if red:
        return {
            "text": "不同意签订该分包合同。",
            "conditions": [],
            "red_line": True,
            "blocking_count": 1,
        }
    conditions = _signing_conditions(raw, facts)
    if conditions:
        return {
            "text": "暂缓签订该分包合同，待下列事项补正后重新报审：",
            "conditions": conditions,
            "red_line": False,
            "blocking_count": len(conditions),
        }
    blocking = [item for item in raw
                if _verdict(item) != "符合" and not item.get("material_only")]
    if blocking:
        return {
            "text": "暂缓签订该分包合同，待补充材料后重新报审：",
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


def _section1_conclusion(document, section, is_labor):
    if section["counts"]["不符合"] or section["counts"]["缺项"]:
        return section["conclusion"]
    facts = document.get("certificate_facts") or {}
    qualification = facts.get("qualification") or {}
    safety = facts.get("safety_license") or {}
    baseline = _baseline_date(document)
    missing = []
    qualification_title = str(qualification.get("title") or "")
    labor_certificate = is_labor and "施工劳务" in qualification_title \
        and bool(qualification.get("valid_until"))
    if not labor_certificate and not (qualification.get("category") or qualification.get("level")):
        missing.append("施工劳务备案证书" if is_labor else "资质类别/等级")
    if not safety.get("valid_until"):
        missing.append("安许有效期")
    expired = []
    if _expired(qualification.get("valid_until"), baseline):
        expired.append("资质证书有效期至%s" % qualification["valid_until"])
    if _expired(safety.get("valid_until"), baseline):
        expired.append("安全生产许可证有效期至%s" % safety["valid_until"])
    if expired:
        return "主体资格材料已核对；%s，早于核查基准日，需提交续期证书后确认。" % "；".join(expired)
    if missing:
        return "主体资格材料已核对；%s未识别，需核对证书原件后确认。" % "、".join(missing)
    return "主体资格材料已核对。"


def _section_fact_lines(document, info, is_labor):
    """第一章的已核实事实：只写证照页/合同里读到的字段，不反推结论。"""
    facts = document.get("certificate_facts") or {}
    license_facts = facts.get("business_license") or {}
    qualification = facts.get("qualification") or {}
    safety = facts.get("safety_license") or {}
    lines = []
    if license_facts:
        parts = []
        if license_facts.get("name"):
            parts.append("分包单位" + license_facts["name"])
        if license_facts.get("uscc"):
            parts.append("统一社会信用代码" + license_facts["uscc"])
        if license_facts.get("legal_rep"):
            parts.append("法定代表人" + license_facts["legal_rep"])
        if license_facts.get("registered_capital"):
            parts.append("注册资本" + license_facts["registered_capital"])
        scope = _scope_keywords(license_facts.get("business_scope"))
        if scope:
            parts.append("经营范围含" + scope)
        if parts:
            lines.append("，".join(parts) + "。")
    if qualification:
        title = qualification.get("title") or "建筑业企业资质证书"
        details = []
        category = qualification.get("category") or qualification.get("level")
        if category:
            details.append(category)
        if qualification.get("cert_no"):
            details.append("证书编号" + qualification["cert_no"])
        if qualification.get("valid_until"):
            details.append("有效期至" + qualification["valid_until"])
        if details:
            lines.append("持有%s（%s）。" % (title, "，".join(details)))
        else:
            lines.append("持有%s；资质类别/等级未识别，需核对证书原件。" % title)
    if safety:
        details = []
        if safety.get("cert_no"):
            details.append("证书号" + safety["cert_no"])
        if safety.get("scope"):
            details.append("许可范围“%s”" % safety["scope"])
        if safety.get("valid_until"):
            details.append("有效期至" + safety["valid_until"])
        line = "持有安全生产许可证"
        if details:
            line += "（" + "，".join(details) + "）"
        lines.append(line + "。")
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

    paragraph("以下依据《上海市公路工程施工分包管理实施细则》（2026）、"
              "《公路工程施工分包管理办法》（交公路规〔2024〕2号）、"
              "《上海市建筑市场管理条例》及报审材料，出具审查意见。")
    heading("一、建设单位审查意见（正式表述）")
    paragraph("工程名称：%s" % _text(info.get("工程名称")))
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

    heading("7. 结论")
    signing = document.get("signing_conclusion") or {}
    conditions = signing.get("conditions") or []
    paragraph(signing.get("text") or "暂缓签订该分包合同。", bold=True)
    for index, text in enumerate(conditions, 1):
        paragraph("%d. %s。" % (index, text), indent=True)
    if signing.get("red_line"):
        paragraph("红线事项整改完成前，不得签订或实施该分包合同。")
    paragraph("工资专户、专项施工方案、安全生产费使用计划、设备检定等，"
              "合同中应约定于开工前提交，不作为本次签约缺件。")
    paragraph("指挥部/建设单位（章）：____________________")
    paragraph("日期：____年__月__日")

    blocks.append({"type": "page_break"})
    appendix = document.get("appendix") or {}
    heading("附件一：本次自动核查结果")
    paragraph("本表只列系统已形成确定结论的事项；同一材料组的多项材料合并为一行，"
              "行数不等于正文核查项数；待核实、未覆盖事项见附件二、附件三。")
    if appendix.get("auto_rows"):
        table(appendix["auto_headers"], appendix["auto_rows"], appendix["auto_widths"])
    else:
        paragraph("本次自动核查未形成确定性结论，相关事项见附件二。")

    heading("附件二：8 表签约前核查导航")
    paragraph("本表说明八张检查表在签约前需要提交或核验的材料、合同应约定的条款，"
              "以及未落实时对签约和开工的影响；工资专户、专项方案、安全费计划、设备检定等"
              "开工前事项，签约时只核对合同是否约定。")
    if appendix.get("nav_rows"):
        table(appendix["nav_headers"], appendix["nav_rows"], appendix["nav_widths"])

    heading("附件三：需补充材料/人工核查事项")
    paragraph("本表按类别归并，具体明细见系统核查工作底稿。")
    if appendix.get("manual_rows"):
        table(appendix["manual_headers"], appendix["manual_rows"], appendix["manual_widths"])
    paragraph(appendix.get("uncovered_text") or "本次未覆盖：无。")
    paragraph("八张检查表作为工作底稿保存在系统中，可按需下钻，不作为正式意见的逐项结论。")
    return blocks


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
        "signing_conclusion": _signing_conclusion(raw, facts),
        "sections": _sections(base, raw, contract_facts, personnel_facts),
        "issues": _issue_rows(raw),
        "pending": _pending_rows(raw),
        "pending_total": sum(1 for item in raw if _verdict(item) == "待核实"),
        "attachments": _attachments(base, raw, contract_facts),
        "appendix": _appendix(base, raw, contract_facts),
        "certificate_facts": facts,
        "contract_clauses": contract_facts,
        "personnel_roster": personnel_facts,
        "equipment_facts": equipment_facts,
        "basis": deepcopy(base["basis"]),
        "limitations": deepcopy(base["limitations"]),
        "issued_date": base.get("issued_date") or "",
        "statistics": deepcopy(base.get("statistics") or {}),
        "trace": deepcopy(base.get("trace") or {}),
    }
    document["blocks"] = _blocks(document)
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
