"""核查报告 v0（W2 收口，MVP-1「报告初版」）。

纪律：
- **纯聚合不重算**——结论只读已落库的 BsFinding（与 structural_check 报告
  侧同纪律：重算退化为宽松路径比留空更危险）；
- **面向非技术读者**——标题与汇总用平实中文，不出现「内核/降级/三态/
  依赖接线」等实现侧词汇；扫描件问题写「文件里没有文字内容，需人工翻看」；
- 证据条目带 file_name/page/quote/location_mode，前端据此渲染「定位到
  第 n 页」（precise 可点、page 仅跳页、unresolved 不渲染按钮）。
"""
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy.orm import Session

from . import a2_check, ai_advisory_snapshots, business_report, certificate_number_check, checklist, contract_party_evidence as contract_party_evidence_module, extract, finding_text, identity_observation, material_confirmation, material_versions, parsing, qualification_summary, reference_snapshots, report_basic_facts, report_document
from .classify import ROLE_LABELS, ROLE_PACKET, ROLE_UNKNOWN, classify_material
from . import report_contract_type
from .models import (STATUS_CODES, STATUS_LABELS, STATUS_UNDECIDABLE,
                     BsFinding, BsMaterial, BsSubject, BsTask, BsTaskEvent,
                     BsReviewVersion)
from .rules import (basis_lookup, configured_basis_lookup, load_config, load_report_checklist, load_signature_chains,
                    load_transfer_rules, negative_list_rules)
from .severity import SEVERITY_LABELS

# 核查节（顺序即报告呈现顺序；标题面向非技术读者，按业主「五步审查」命名）。
# 内部功能编号 A1/A2/A3 只作程序标识，不再出现在报告与界面文案里（FRS v2 §0.3、
# 自推内容清除表 P-04a）；后续功能分区排在五步之后，避免与本期核查项混排。
_SECTIONS = (
    ("completeness", "① 要素完整性核查 · 报审资料齐全性"),
    ("signature_chain", "① 要素完整性核查 · 签字盖章"),
    ("qualification_match", "② 资质与内容合规性审查 · 资质与承揽范围匹配"),
    ("negative_list", "② 资质与内容合规性审查 · 负面清单情形"),
    ("transfer_scope", "② 资质与内容合规性审查 · 转包情形"),
    ("illegal_subcontract", "② 资质与内容合规性审查 · 违法分包情形"),
    # R05 接线后 V16-01/L17-02 由 illegal_subcontract 结论取代（见
    # a1_check.supersede_dependency_findings）；本分区保留，历史任务的旧结论仍能归类展示。
    ("a1_dependency", "② 资质与内容合规性审查 · 监理审查与方案编制责任"),
    ("person_consistency", "③ 关键岗位人员核查 · 人员一致性与社保"),
    ("cert_validity", "④ 证件证照有效性核查 · 证照有效期"),
    ("internal_records", "⑤ 内部信用与事故材料核查"),
    # 后续功能（业主部署文件 二、（二）（六））：本期默认不产出条目；
    # 打开 config 开关或启用合同语义核查后才会出现。
    ("amount_ratio", "后续功能 · 分包合同总合同额匹配（金额占比）"),
    ("workload_ratio", "后续功能 · 分包合同总合同额匹配（工程量占比）"),
    ("contract_semantic", "后续功能 · 合同语义核查"),
)

# 第 1 步是唯一的材料完整性/缺件入口。签字盖章属于要素完整性核查，
# 因而仍可在此处说明缺什么；资质、内容、人员、证照和内部记录等后续步骤的
# `undecidable` 只能作为异常与人工核实项，不能再进入补件清单。
# 这是后端报告、意见书和待办聚合共用的唯一边界定义。
MISSING_MATERIAL_KINDS = frozenset(("completeness",))

STAGE_LABELS = {"draft": "AI 初稿（未复核）", "confirmed": "确认稿"}
DRAFT_NOTICE = "本稿为 AI 初稿，未经过人工复核，不得作为正式审查意见使用"


def mark_stage(payload: dict, stage: str) -> dict:
    """给报告/意见书正文标注稿别（draft / confirmed）。

    标注写在**正文里**并参与校验码：初稿一旦被打印或转发，纸面上必须看得出「未复核」。
    draft 不设人工处置闸门，技术完整性由 router 单独校验。确认稿的风险处置门槛
    不等于全部人工复核完成，稿别标注不能只放在查询参数里。
    """
    if stage not in STAGE_LABELS:
        raise ValueError("stage 仅支持 draft / confirmed")
    payload["review_stage"] = stage
    payload["stage_label"] = STAGE_LABELS[stage]
    if stage == "draft":
        payload["draft_notice"] = DRAFT_NOTICE
    else:
        payload.pop("draft_notice", None)
    return payload


def scope_statements(db: Session, task: BsTask) -> list:
    """本次核查范围的显式声明：把「哪些情形本次没查、为什么」写在纸面上。

    三态只描述**已纳入核查**的项判成什么，不描述「哪些项没进范围」。范围缺口若只用三态
    表达，就会把业主并未收集的资料渲染成一堆「缺件」——所以范围单独声明。
    消费方=报告 Word/PDF 与审查意见书正文（AGENTS 纪律 5：护栏必须有人读）。
    """
    statements = []
    if not (task.options_snapshot_json or {}).get(reference_snapshots.REVIEW_POINTER):
        statements.append(
            "该历史任务未留存分组运行记录，仅依据已保存结论汇总，不能证明当前材料已完成本轮核查；"
            "如需确认当前材料，请重新核查。")
    reference_snapshot = reference_snapshots.get_reference_snapshot(db, task)
    if reference_snapshot and any(
            isinstance(run, dict) and run.get("state") == "completed"
            and run.get("status") in {"undetermined", "partial"}
            for run in reference_snapshot.get("runs") or []):
        statements.append(
            "部分参考依据未取得或查询不完整；本报告保留已执行的规则结果，"
            "请查看依据详情并结合原件核实。")
    config = load_config()
    not_checked = [label for label, key in (("分包金额占比", "amount_ratio"),
                                            ("工程量占比", "workload_ratio"))
                   if not config.get(key, {}).get("enabled")]
    if not_checked:
        statements.append(
            "本次未核查%s（当前未启用）。" % "、".join(not_checked))
    flagged = [rule["title"] for rule in load_transfer_rules()["rules"]
               if rule.get("requires_general_contract")]
    task_materials = db.query(BsMaterial).filter(BsMaterial.task_id == task.id).all()
    has_general = any(material.role == "general" for material in
                      material_versions.active_input_materials(task_materials))
    if flagged and not has_general:
        statements.append(
            "未上传总包合同，未核查需该合同原文的情形：「%s」。" % "」「".join(flagged))
    statements.append(
        "本次未核查合同条款用词与文字细节（合同语义核查）。")
    if task.scope_a3:
        statements.append(
            "资质仅按工程类别文字初筛；等级与规模、同人同岗、实际到岗及证照真实性尚未自动核实。")
    if task.scope_a1:
        statements.append(
            "第五类仅核对已提供的内部信用及事故材料，未查外部网站；缺资料不代表无风险，"
            "禁入构成、适用期间及恢复准入未自动判定。")
    return statements


# 材料解析问题 → 平实中文说明（不出现「降级」「文本层」等实现词）
_DEGRADATION_TEXTS = {
    "no_text_layer": "该文件里没有文字内容（疑似扫描件），涉及文字的核查无法判断，需人工翻看原件",
    "parse_failed": "该文件解析失败，需人工检查文件是否损坏",
    "ocr_fallback": "该文件为扫描件，已通过文字识别读取内容，识别结果可能存在误差，关键结论请以原件为准",
    "parse_interrupted": "该文件上传时的解析被中断（服务重启），请删除该文件并重新上传",
    # A-15（B1）F5：合同签订日期缺失 → 用任务核查基准日兜底（降级消费坐标见 extract.py / AGENTS 纪律 6）
    "sign_date_fallback": "未在合同中找到签订日期，已按本任务核查基准日取值；涉及签订时间的比对请以原件为准",
}

# 合同字段抽取层产出中「对非技术读者可展示」的字段顺序（F1-F9；与 extract.FIELD_LABELS 同键）。
# 数值单位由字段自身的 unit 承载（金额万元 / 比例 %），呈现层不得裸值。
_CONTRACT_FACT_ORDER = (
    "contract_type", "amount", "scope", "parties", "sign_date",
    "mgmt_fee", "material_fee", "plan_author", "declarations",
)

# 仅这三类合同材料参与合同要素抽取（报审资料包/未归类不含合同条款，抽取只会产噪声）
_CONTRACT_ROLES = ("general", "sub", "labor")


def contract_facts_for_materials(materials, *, baseline_date=None) -> list[dict]:
    """从材料 DTO 可用的合同正文派生抬头事实；纯读，不改 finding 或任务状态。"""
    facts = []
    for material in materials or []:
        role = classify_material(material.file_name)
        if role not in _CONTRACT_ROLES and getattr(material, "role", None) in _CONTRACT_ROLES:
            role = material.role
        if role not in _CONTRACT_ROLES:
            continue
        result = extract.extract_contract_fields(material, baseline_date=baseline_date)
        fields = result.get("fields") or {}
        fields["contract_type"] = report_contract_type.project_contract_type(material)
        facts.append({
            "file_name": material.file_name,
            "role": role,
            "role_label": ROLE_LABELS.get(role, role),
            "project_name": extract.extract_project_name(material),
            "fields": {key: fields.get(key) for key in _CONTRACT_FACT_ORDER},
        })
    return facts

# A-13（B2）报告条目排序：严重度降序（high→medium→low→未分级），三态作次级序
# （U5 裁定）。未分级排最后，避免历史无 severity 的结论抢占表头。
# 公共符号：报告条目排序与历史回执明细共用这一份档位序（AGENTS 纪律 9）。
SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}
_STATUS_RANK = {"hit": 0, "undecidable": 1, "miss": 2}


def _item_sort_key(f: BsFinding) -> tuple:
    """条目排序键：先严重度档（未分级最后），再三态（不符合→无法核实→符合）。"""
    return (SEVERITY_RANK.get(f.severity, 3), _STATUS_RANK.get(f.status, 3), f.id)


def _label_maps() -> tuple:
    """条目 id → 展示名（负面清单=条款号+原文情形；完整性=清单条目名；签字链=链名；
    证件有效期=A2 条目名）。"""
    neg = {r["id"]: ("%s %s" % (r.get("article", ""), r.get("text", ""))).strip()
           for r in negative_list_rules()}
    rc = {i["id"]: i["label"] for i in load_report_checklist()}
    sc = {"A3-02-%s" % c["id"]: c["name"] for c in load_signature_chains()}
    sc["A3-02-timing"] = "先报批后实施时序"
    val = {"A2-02": "证件有效期", "A2-01": "社保资料覆盖管理人员",
           "A2-03": "证件注册单位一致性",
           certificate_number_check.ITEM_ID: "项目经理交安 B 证编号一致性",
           identity_observation.ITEM_ID: "同名身份证资料原文对照",
           "A2-04": "资质与承揽范围匹配",
           "BS-INT-01": "内部信用与事故材料核查"}
    # 与核查规则同源，避免报告把内部条目编号当作面向用户的问题名称。
    val.update({rule["id"]: rule["title"] for rule in load_transfer_rules()["rules"]
                if rule.get("id") and rule.get("title")})
    # R-3（2026-10-06）：A2-01-P 已并入 A2-01（覆盖口径），历史 finding 标签兜底显示新名。
    val["A2-01-P"] = val["A2-01"]
    return neg, rc, sc, val


def _item_label(item_id: str, neg_map: dict, rc_map: dict, sc_map: dict, val_map: dict) -> str:
    return (neg_map.get(item_id) or rc_map.get(item_id) or sc_map.get(item_id)
            or val_map.get(item_id) or item_id)


def item_label(item_id: str) -> str:
    """条目 id → 展示名（报告条目 `label` 的同口径公共入口）。

    旧回执明细若需显示项名，也必须复用同一归并逻辑；`_item_label` 仍是唯一定义点，
    这里只负责取映射表（AGENTS 纪律 9）。
    """
    return _item_label(item_id, *_label_maps())


def status_counts(items_or_statuses) -> dict:
    """三态 counts 的唯一产出点：英文键、键序固定为 `models.STATUS_CODES`。

    入参可以是 `BsFinding` 行、报告/意见书条目 dict，或状态字符串本身。报告
    `summary.counts`、意见书 `conclusion.counts`、五个检查端点都调这一处，
    不得各处手写 key（BS-P1-15 / 设计 100 §2.2）。
    """
    counts = {code: 0 for code in STATUS_CODES}
    for entry in items_or_statuses or []:
        if isinstance(entry, str):
            status = entry
        elif isinstance(entry, dict):
            status = entry.get("status")
        else:
            status = getattr(entry, "status", None)
        if status in counts:
            counts[status] += 1
    return counts


def status_label(status: str, open_term: bool = False) -> str:
    """状态码 → 对外展示词（唯一取词点；`open_term` 例外标「仅登记」）。

    `open_term=True` 的兜底条款不是三态之一：它仅登记备查、不计 counts，
    不得显示成「无法核实」（BS-P1-11 语义不变）。
    """
    return OPEN_TERM_STATUS_LABEL if open_term else STATUS_LABELS.get(status, status)


def _evidence(f: BsFinding, file_names: dict, version_facts: dict | None = None) -> dict | None:
    """保留可定位的页码线索；无坐标的计算类结论不产 evidence。"""
    page_only = (f.location_mode == "page" and f.material_id is not None
                 and type(f.page) is int and f.page > 0)
    if not (f.quote or "").strip() and not page_only:
        return None
    fact = (version_facts or {}).get(f.material_id) or {}
    evidence = {
        "material_id": f.material_id,
        "file_name": file_names.get(f.material_id),
        "revision_no": fact.get("revision_no"),
        "version_role": fact.get("version_role", "legacy_unknown"),
        "replacement_target_id": fact.get("replacement_target_id"),
        "page": f.page,
        "quote": f.quote,
        "location_mode": f.location_mode,
    }
    for ref in f.evidence_json or []:
        if not isinstance(ref, dict):
            continue
        ref_material = ref.get("material_id")
        if ref_material is not None and f.material_id is not None and str(ref_material) != str(f.material_id):
            continue
        ref_page = ref.get("page")
        if ref_page is not None and f.page is not None and str(ref_page) != str(f.page):
            continue
        anchor = ref.get("anchor")
        if isinstance(anchor, dict):
            evidence["anchor"] = dict(anchor)
            break
    return evidence


def report_review_blocking(finding) -> bool:
    """同名身份证识别差异只作可追溯参考，不强迫处置后才能查看报告。"""
    if is_open_term(finding) or checklist.is_material_only_finding(finding):
        return False
    if (finding.item_id != identity_observation.ITEM_ID or finding.status != STATUS_UNDECIDABLE):
        return True
    refs = finding.evidence_json or []
    return not (isinstance(refs, list) and len(refs) == 2 and all(isinstance(ref, dict) and ref.get("material_id")
                and isinstance(ref.get("page"), int) and ref["page"] > 0
                and isinstance(ref.get("anchor"), dict) and ref["anchor"].get("status") == "precise" for ref in refs)
                and (refs[0]["material_id"], refs[0]["page"]) != (refs[1]["material_id"], refs[1]["page"]))


def decision_trace(f: BsFinding, basis: dict | None = None) -> dict:
    """结构化呈现判定链，不暴露或臆造模型内部思维过程。

    复核页需要回答「依据什么、证据在哪里、缺什么」，而不是展示一段无法审计的
    自由生成解释。该投影只消费已落库 finding、依据坐标和结构化缺件。
    """
    missing = finding_missing(f)
    status = status_label(f.status, is_open_term(f))
    location = f.location_mode
    if location == "precise" and f.material_id and f.page:
        evidence_status = "precise"
        evidence_summary = "已关联材料并取得可验证的第 %s 页引文。" % f.page
    elif location == "page" and f.material_id and f.page:
        evidence_status = "page_only"
        evidence_summary = "已关联材料和页码，但引文尚未完成定位校验。"
    elif f.material_id:
        evidence_status = "material_without_location"
        evidence_summary = "已关联材料，但没有可验证的页码或引文。"
    elif f.status == STATUS_UNDECIDABLE:
        evidence_status = "missing"
        evidence_summary = "当前没有绑定可核实的材料证据，因此不能形成确定结论。"
    else:
        evidence_status = "rule_only"
        evidence_summary = "本条没有单页材料引文，按已落库规则结果展示。"
    basis_summary = "已建立规则依据坐标。" if basis else "该条目尚未建立规则依据坐标。"
    missing_summary = "；缺口：%s" % "、".join(missing.get("labels") or []) if missing else ""
    return {
        "status": f.status,
        "status_label": status,
        "evidence_status": evidence_status,
        "basis_available": bool(basis),
        "summary": "%s%s%s" % (basis_summary, evidence_summary, missing_summary),
        "missing": missing,
    }


def _headline(counts: dict, total: int) -> str:
    if total == 0:
        return "该任务尚未开始核查。"
    parts = []
    if counts["hit"]:
        parts.append("发现 %d 项风险问题" % counts["hit"])
    if counts["miss"]:
        parts.append("%d 项经核查无问题" % counts["miss"])
    if counts["undecidable"]:
        # 状态词只从 STATUS_LABELS 取（设计 100 §2.1 单一措辞源），不在汇总句另抄一份。
        parts.append("%d 项%s，具体原因见各项说明，需人工确认"
                     % (counts["undecidable"], STATUS_LABELS["undecidable"]))
    return "本次核查%s。" % ("；".join(parts) if parts else "未产生结论")


# ==================== 开放条款（兜底条款）口径（BS-P1-11 / 设计 77 §1） ====================
# 法规末尾「法律法规规定的其他……行为」这类条款（目录 `open_term: true`）本身没有可机械
# 执行的构成要件，其必需证据 `additional_rule_text`（其他情形明文）与
# `deterministic_criterion`（确定性认定标准）**在权威口径补充前并不存在**，故内核恒判
# 不可判定（`a1_check._open_term_finding`）。
#
# 矛盾点（BS-P1-11 要修的就是这个）：内核文案自称「仅登记，不参与命中/未命中统计」，
# 但报告统计把全部 undecidable 一并计入 counts，而意见书「只要存在 undecidable 就不出
# 结论」⇒ **材料再齐全也给不出「同意」**（实测见设计 77 §7 验证 1/2）。
#
# 口径（用户 2026-10-22 确认方案 A）：报告**照常逐条列出**这 4 条并标「仅登记」，
# 但**不计入 counts.undecidable 与 counts.total**——与内核已写下的语义一致，
# 属「修正统计实现」而非「改判定口径」。`open_term` 是数据字段：将来权威口径明确某项，
# 把它改为 false 并补判据即自动恢复计数，本文件零改动。
OPEN_TERM_STATUS_LABEL = "仅登记"
OPEN_TERM_NOTICE = ("本次另有 %d 项为法律法规兜底条款（「法律法规规定的其他……行为」），"
                    "该类条款无可机械执行的构成要件，仅登记备查、不参与结论；"
                    "如需据此认定，应有新的法规明文并补充确定性认定标准。")


def open_term_item_ids() -> set:
    """目录中 `open_term=true` 的条目 id（数据驱动，不硬编码 ID 清单）。"""
    return {rule["id"] for rule in load_transfer_rules()["rules"] if rule.get("open_term")}


def is_open_term(f) -> bool:
    """该结论是否为开放条款登记项（按目录判定，不看 status/文案）。"""
    return getattr(f, "item_id", None) in open_term_item_ids()


def _countable(findings: list) -> list:
    """参与三态统计的结论：剔除开放条款（它们仅登记，不构成结论）。

    剔除后 counts 三项与 total 同分母，避免「hit+miss+undecidable ≠ total」。
    """
    return [f for f in findings if not is_open_term(f)]


# ==================== 结构化缺件（BS-P1-12 / 设计 77 §2） ====================
# 缺件此前只存在于 `message` 文案的「（缺：…）」段里 ⇒ 面板只能靠正则拆，而实测
# `全部工程覆盖（肢解/分别发包）明文` 这类嵌套中文括号必被截断（朴素 `[^）]*` 会停在
# 第一个右括号）。更致命的是 `BS-P1-02` 已经改过一次缺件文案 ⇒ 面板依赖文案则每改一次
# 坏一次。故：内核落 `missing_json`（**只存 key**），中文标签与 key→材料类归并都在
# `data/missing_material_map.json`，现读现算。
#
# 历史结论 `missing_json` 为 NULL（不回填——回填只能靠拆旧文案，正是要避免的事）。
# 消费方必须走 `finding_missing()`：它先看结构化字段，没有才回落文案，且回落失败返回
# 空列表而不是猜。


def _legacy_missing_from_message(message: str) -> list:
    """历史数据回落：从文案「（缺：…）」段拆出中文标签（不还原 key）。

    括号配平（不用 `[^）]*`）：标签里自身含中文括号，简单正则会截断。找不到返回 []。
    """
    text = message or ""
    marker = "（缺："
    start = text.find(marker)
    if start < 0:
        return []
    i = start + len(marker)
    depth = 0
    buf = []
    while i < len(text):
        ch = text[i]
        if ch in "（(":
            depth += 1
        elif ch in "）)":
            if depth == 0:
                break
            depth -= 1
        buf.append(ch)
        i += 1
    labels = [part.strip() for part in "".join(buf).split("、")]
    return [lb for lb in labels if lb]


def _label_to_key(label: str, mapping: dict) -> str | None:
    """中文标签 → key（回落用；同一标签可能对应多个 key，取映射里第一个）。

    `mapping` 是 `_LABEL_TO_KEY()` 产出的**标签→key**表（方向与函数名一致）。
    此前这里按 `{key: label}` 遍历（`for key, lab in mapping.items(): if lab == label`），
    而传进来的恰恰是反向表 ⇒ 永远匹配不上、`keys` 恒为空，文案回落的 key 还原从未生效。
    """
    return mapping.get(label)


_LABEL_TO_KEY_CACHE: dict = {}


def _EVIDENCE_LABEL(key: str) -> str:
    """缺件 key → 中文标签（标签表在 a1_check，此处引用而不复制一份）。

    `a1_check` 不 import `reporting`（单向依赖），故此处导入不成环。
    """
    from .a1_check import _EVIDENCE_LABELS
    return _EVIDENCE_LABELS.get(key, key)


def missing_material_map() -> dict:
    """映射文件 + 内部 mtime 戳（缓存失效用，不对外暴露 `_stamp`）。

    直读文件（与 `load_severity_matrix` 同纪律：改数据 → 下次出报告即生效）；
    缓存的只是「标签→key 反查表」这一份派生结果，按 mtime 失效。
    """
    import os

    from .rules import _DATA_DIR, load_missing_material_map
    try:
        stamp = os.path.getmtime(_DATA_DIR / "missing_material_map.json")
    except OSError:        # 数据文件缺失不该让报告整体崩，降级为不缓存
        stamp = None
    doc = dict(load_missing_material_map())
    doc["_stamp"] = stamp
    return doc


def _LABEL_TO_KEY() -> dict:
    """标签→key 反查表（按文件 mtime 失效，避免进程内吃到旧映射）。"""
    doc = missing_material_map()
    stamp = doc.get("_stamp")
    cached = _LABEL_TO_KEY_CACHE.get("v")
    if cached and cached.get("stamp") == stamp:
        return cached["map"]
    rev = {}
    for key in (doc.get("evidence_to_category") or {}):
        rev.setdefault(_EVIDENCE_LABEL(key), key)
    _LABEL_TO_KEY_CACHE["v"] = {"stamp": stamp, "map": rev}
    return rev


def finding_missing(f) -> dict:
    """单条结论的结构化缺件视图（key / 标签 / 材料类）。

    返回 `{"keys": [...], "labels": [...], "categories": [...], "source": "json"|"message"|None}`：
    - `source="json"`：本行有结构化字段（BS-P1-12 之后的新结论）；
    - `source="message"`：历史行，从文案回落（key 尽力还原，还原不了就只给标签）；
    - `source=None`：真的没有缺件（命中/未命中/冲突型不可判定）。

    **不猜材料类**：key 未登记归并关系时 `categories` 里不出现猜测值，由面板按「其他」
    单列并原样显示中文标签（归因纪律：指错方向比少报更糟）。

    另给 `by_category`（材料类 → 该类下的标签）：一条结论常同时缺多类材料（如既缺总包
    合同又缺分包合同），`labels` 是全部、`by_category` 才是「这一类各缺什么」。缺这份
    映射时面板只能把全部标签塞进每个类，读者会在「总包合同」下看到「分包合同范围」。
    """
    keys = list(getattr(f, "missing_json", None) or [])
    mapping_doc = missing_material_map()
    evidence_map = mapping_doc.get("evidence_to_category") or {}
    if keys:
        labels = [checklist.missing_label(key) or _EVIDENCE_LABEL(key) for key in keys]
        source = "json"
    else:
        labels = _legacy_missing_from_message(getattr(f, "message", "") or "")
        keys = [k for k in (_label_to_key(lb, _LABEL_TO_KEY()) for lb in labels) if k]
        source = "message" if labels else None
    categories = []
    by_category: dict = {}
    for key, label in zip(keys, labels):
        cat = ("submission_packet" if str(key).startswith("checklist:")
               else evidence_map.get(key))
        if not cat:
            continue
        if cat not in categories:
            categories.append(cat)
        bucket = by_category.setdefault(cat, [])
        if label not in bucket:
            bucket.append(label)
    # 文案回落时 key 可能还原不出来（历史标签不在映射里）——这些标签仍要能显示，
    # 归到 `None` 桶由面板单列，不能因为没 key 就丢掉。
    for label in labels:
        if label not in [lb for group in by_category.values() for lb in group]:
            bucket = by_category.setdefault(None, [])
            if label not in bucket:
                bucket.append(label)
    return {"keys": keys, "labels": labels, "categories": categories,
            "by_category": by_category, "source": source}


def missing_category_labels() -> dict:
    """材料类 → 中文标签/提示（面板与报告共用，不各写一份）。"""
    return ((missing_material_map().get("material_categories")) or {})


def is_explicit_material_missing(finding: BsFinding) -> bool:
    """是否为可直接进入补件清单的明确缺件。

    新目录规则以 ``checklist.is_material_missing_finding`` 为单一判据；历史完整性
    结论可能没有目录条目，兼容其 ``hit + 结构化/可还原缺件标签``。不可判定无论
    是否带候选缺项都不算缺件，只能进入人工核实。
    """
    if checklist.is_material_missing_finding(finding):
        return True
    return bool(getattr(finding, "kind", None) in MISSING_MATERIAL_KINDS
                and getattr(finding, "status", None) == "hit"
                and finding_missing(finding)["labels"])


def project_compound_material_findings(findings: list, checklist_summary: dict | None) -> list:
    """只读投影复合材料当前确认状态，保留原始规则结论。"""
    compound = {(row.get("subject_id"), item.get("item_id")): item
                for row in (checklist_summary or {}).get("subjects", [])
                for item in row.get("checklist_items", []) if item.get("subitems")}
    projected = []
    for finding in findings:
        item = compound.get((getattr(finding, "subject_id", None), getattr(finding, "item_id", None)))
        if not item:
            projected.append(finding)
            continue
        state = item.get("material_state")
        projected.append(SimpleNamespace(
            id=getattr(finding, "id", None), item_id=finding.item_id, kind=finding.kind,
            subject_id=finding.subject_id, material_id=finding.material_id,
            message={"provided": "合同包各子材料已识别或经人工确认。",
                     "missing": "合同包存在需补充的子材料，详见材料清单。",
                     "verification": "合同包部分子材料待核实，详见材料清单。"}.get(state, finding.message),
            status={"provided": "miss", "verification": "undecidable", "missing": "hit"}.get(state, finding.status),
            missing_json=["checklist:%s:%s" % (finding.item_id, child["key"])
                          for child in item["subitems"] if child.get("material_state") == "missing"]))
    return projected


def missing_summary(findings: list, checklist_summary: dict | None = None) -> dict:
    """按材料类归并缺件（BS-P1-06 待办面板的聚合依据）。

    带 `counts` 而不只是列表：实测**一份总包合同可消 39 条缺件**，面板必须能一眼说出
    「补这一份能解决 39 项」，否则用户不知道先补哪个。
    开放条款排除在外（它们缺的不是材料，见 `is_open_term`）。

    P1-06 面板契约（纯加法，既有键语义不变）：
    - 每个材料类带 `roles` / `actionable`：roles 取自 `data/missing_material_map.json`
      的 `material_categories`（唯一定义点，这里不另抄），`actionable = bool(roles)`。
      `statutory_authority`（法规明文）与 `review_output`（审查产出/人工作判）的 roles
      为空 ⇒ 不可补件，面板只展示、不给「去补件」动作。
    - `without_missing_count`：明确材料缺失、非兜底条款、但 `finding_missing` 拿不到标签的
      历史结论条数。这些条目进不了任何材料类；不显式给出的话，
      「total_items 与各类之和」的差会被读者误当成漏统计。
      口径说明：`total_items` 数**结论条数**，各类 `count` 数**类内结论条数**，
      一条结论可缺多类材料 ⇒ 各类之和可 ≥ total_items（同一结论在每类各计一次）。
    """
    projected = project_compound_material_findings(findings, checklist_summary)
    cats = missing_category_labels()
    buckets: dict = {}
    total_items = 0
    without_missing_count = 0
    for f in projected:
        # “缺件”只接受材料目录规则的明确命中。不可判定表示证据不足，
        # 即使发生在完整性步骤也只能进入人工核实，不能反向推断文件不存在。
        material_hit = is_explicit_material_missing(f)
        if not material_hit or is_open_term(f):
            continue
        total_items += 1
        info = finding_missing(f)
        if not info["labels"]:
            without_missing_count += 1
            continue
        # 一条结论可能同时缺多类材料：每类只收「属于该类」的标签（`by_category`），
        # 不能把全部标签塞进每个 bucket —— 否则「总包合同」下会出现「分包合同范围」。
        groups = info.get("by_category") or {}
        if not groups:
            groups = {None: list(info["labels"])}
        for cat, group_labels in groups.items():
            bucket = buckets.setdefault(cat, {"category": cat,
                                              "label": (cats.get(cat) or {}).get("label")
                                                       or "其他未归类材料",
                                              "hint": (cats.get(cat) or {}).get("hint"),
                                              "item_ids": [], "labels": [], "_identities": []})
            subject_id = getattr(f, "subject_id", None)
            material_id = getattr(f, "material_id", None)
            bucket.setdefault("_subject_ids", set())
            bucket.setdefault("_material_ids", set())
            bucket.setdefault("_subject_unresolved", False)
            if subject_id is not None:
                bucket["_subject_ids"].add(subject_id)
            else:
                bucket["_subject_unresolved"] = True
            if material_id is not None:
                bucket["_material_ids"].add(material_id)
            identity = (getattr(f, "subject_id", None), f.item_id)
            if identity not in bucket["_identities"]:
                bucket["_identities"].append(identity)
                bucket["item_ids"].append(f.item_id)
            for lb in group_labels:
                if lb not in bucket["labels"]:
                    bucket["labels"].append(lb)
    out = sorted(buckets.values(), key=lambda b: (-len(b["item_ids"]), b["label"]))
    for bucket in out:
        identities = bucket.pop("_identities")
        bucket["count"] = len(identities)
        # 保留聚合前的真实配对，避免前端选择某一主体时把其他主体的核查项带入。
        bucket["subject_items"] = [{"subject_id": subject_id, "item_id": item_id}
                                   for subject_id, item_id in identities if subject_id is not None]
        bucket["subject_ids"] = sorted(bucket.pop("_subject_ids", set()))
        bucket["material_ids"] = sorted(bucket.pop("_material_ids", set()))
        bucket["subject_mapping_reliable"] = not bucket.pop("_subject_unresolved", False)
        # 角色/可补件标记只从映射文件派生（未归类桶 category=None 也走这里 → roles=[]、
        # actionable=False：归不到材料类的缺件本就不该被当作「补一份材料即可消」）。
        roles = list((cats.get(bucket["category"]) or {}).get("roles") or [])
        bucket["roles"] = roles
        bucket["actionable"] = bool(roles)
    return {"total_items": total_items,
            "without_missing_count": without_missing_count,
            "categories": out}


def _recommendation(f: BsFinding, label: str) -> str:
    """给整改报告的统一短建议；不替代规则结论，只说明下一步动作。"""
    if is_open_term(f):
        # 兜底条款不该让人去「补材料」——它缺的不是材料，是法规明文与认定标准。
        return "该条为法律法规兜底条款，仅登记备查；如需据此认定，应有新的法规明文与确定性认定标准。"
    if f.status == "undecidable":
        return "补充或核实与该项相关的原始资料，完成确认后重新核查。"
    return "请结合问题描述核对合同及相关资料，完成整改后重新核查。"


# 复核处置：旧 confirm/reject 输入统一映射为 accepted/ignored，历史回执继续可读。
REVIEW_DECISION_ALIASES = {
    "confirm": "accepted", "accepted": "accepted",
    "reject": "ignored", "ignored": "ignored",
}
# 当前候选人工处置词表：只有采纳/忽略。依据库中的 human_verified 是原文校对事实，
# 不属于问题项人工处置，不得混入此映射。
REVIEW_LABELS = {"accepted": "已采纳", "ignored": "已忽略"}
UNPROCESSED_REVIEW_LABEL = "未经人工处理，保留系统核查结果"
UNPROCESSED_HIT_LABEL = "AI 定性（未经人工处置）"


def canonical_review_decision(decision: str | None) -> str | None:
    """处置词归一化（旧 confirm/reject 兼容）。

    未登记的取值原样返回；路由层会拒绝新的未知处置词。历史快照中的
    `verified` 只允许以停用的历史回执形式读取，不重新生成。
    """
    return REVIEW_DECISION_ALIASES.get(decision, decision)


def is_ignored_review(review: dict | None) -> bool:
    return canonical_review_decision((review or {}).get("decision")) == "ignored"


def review_dto(rv: dict | None) -> dict | None:
    """复核回执统一展示形态；未复核（NULL）原样透 None。

    BS-P1-13（设计 98 §4.4）：重跑后结论变了 → `review_json` 变为
    `{"invalidated": {...}}`（无 decision）⇒ 该条回到「未复核」待重新处置，
    展示层据 `invalidated` 显示「原复核意见失效」；`decision` 语义不变。
    """
    if not rv:
        return None
    decision = canonical_review_decision(rv.get("decision"))
    legacy_decision = decision if decision not in (None, "accepted", "ignored") else None
    if legacy_decision:
        # 历史 verified 不能继续作为当前业务状态，但保留可审计的原始取值。
        decision = None
    return {
        "decision": decision,
        "decision_label": (REVIEW_LABELS.get(decision)
                            if decision else ("历史人工回执（已停用）" if legacy_decision else None)),
        "legacy_decision": legacy_decision,
        "reviewer": rv.get("reviewer"),
        "note": rv.get("note"),
        "at": rv.get("at"),
        "invalidated": rv.get("invalidated"),
    }


def human_disposition(rv: dict | None, status: str | None = None) -> dict:
    """将人工处置投影为报告可读事实，不引入第三种业务状态。

    ``unprocessed`` 是由没有当前 accepted/ignored 回执推导出的展示代码，
    不是可提交的数据库状态；历史 verified 也回落到该投影，同时保留原始取值。
    """
    review = rv or {}
    decision = canonical_review_decision(review.get("decision"))
    if decision in REVIEW_LABELS:
        return {
            "code": decision,
            "label": REVIEW_LABELS[decision],
            "note": review.get("note"),
            "reviewer": review.get("reviewer"),
            "at": review.get("at"),
            "invalidated": review.get("invalidated"),
        }
    return {
        "code": "unprocessed",
        "label": UNPROCESSED_HIT_LABEL if status == "hit" else UNPROCESSED_REVIEW_LABEL,
        "note": None,
        "reviewer": None,
        "at": None,
        "invalidated": review.get("invalidated"),
        "legacy_decision": decision if decision else None,
    }


# ==================== 历史快照展示归一化（BS-P1-15 / 设计 100 §2.3） ====================
# 已落库 review_version.snapshot 是 append-only：**不重写**、不迁移。旧快照里的
# counts 中文键与旧状态词只影响展示，故在读取路径（router._output_payload）返回前
# 做一次纯派生归一化——值不变、只换键/换词；report_digest 在归一化之后计算，
# 与当前报告同口径。
_LEGACY_COUNT_KEYS = {"命中": "hit", "未命中": "miss", "不可判定": "undecidable"}


def normalize_status_counts(counts: dict | None) -> dict:
    """历史快照 counts → 英文键（值不变；已是英文键的原样保留）。

    只认三态键；缺失的键补 0，保证键集合恒为 `{hit, miss, undecidable}`。
    """
    out = {code: 0 for code in STATUS_CODES}
    for key, value in (counts or {}).items():
        code = _LEGACY_COUNT_KEYS.get(key, key)
        if code in out:
            out[code] = value
    return out


def _normalize_items(items) -> None:
    """就地归一化条目列表的展示字段（只动返回值副本，调用方先 deepcopy）。"""
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if "status" in item:
            item["status_label"] = status_label(item.get("status"),
                                                bool(item.get("open_term")))
        if isinstance(item.get("review"), dict):
            item["review"] = review_dto(item["review"])
        if "human_disposition" not in item:
            item["human_disposition"] = human_disposition(item.get("review"), item.get("status"))


def normalize_output_payload(payload: dict, kind: str) -> dict:
    """历史报告/意见书载荷 → 当前展示口径（只改返回值，不写库、不改快照本体）。

    - `counts`：中文旧键 → 英文键（值不变）；
    - 每个 item 的 `status_label`：按 status 重取（open_term 仍走「仅登记」例外）；
    - `review.decision_label`：按 decision 重取；没有当前处置时补充
      `human_disposition=unprocessed`。
    """
    if not isinstance(payload, dict):
        return payload
    if kind == "report":
        summary = payload.get("summary")
        if isinstance(summary, dict) and "counts" in summary:
            summary["counts"] = normalize_status_counts(summary["counts"])
        for section in payload.get("sections") or []:
            if isinstance(section, dict):
                _normalize_items(section.get("items"))
    elif kind == "opinion":
        conclusion = payload.get("conclusion")
        if isinstance(conclusion, dict) and "counts" in conclusion:
            conclusion["counts"] = normalize_status_counts(conclusion["counts"])
        _normalize_items(payload.get("items"))
        _normalize_items(payload.get("missing_materials"))
        _normalize_items(payload.get("open_term_items"))
    return payload


# ==================== 未读页披露（BS-P1-09 / 设计 98 §8） ====================
# 文案单一来源：报告的 `reading_coverage[].summary_text` 与意见书的限制项共用同一段文字，
# 前端/纸面不得另写一份措辞（AGENTS 纪律 9）。
_READING_SUGGESTION_TEXT = ("建议将材料分册上传，或补传清晰扫描件后重新核查；"
                            "未读页可能影响相关核查结果，请结合原件人工核实。")


def page_ranges(pages: list) -> str:
    """页码列表 → 可读范围（连续段合并）：[3,4,5,9] → "3–5、9"。"""
    parts = []
    start = prev = None
    for page in sorted({p for p in pages or []
                        if isinstance(p, int) and not isinstance(p, bool)}):
        if start is None:
            start = prev = page
            continue
        if page == prev + 1:
            prev = page
            continue
        parts.append(str(start) if start == prev else "%d–%d" % (start, prev))
        start = prev = page
    if start is not None:
        parts.append(str(start) if start == prev else "%d–%d" % (start, prev))
    return "、".join(parts)


def reading_summary(file_name: str, coverage: dict, affected: int) -> str:
    """未读页「结论层」说明：未读页范围 + 受影响结论数（不改判定，只披露）。"""
    unread = coverage.get("unread_pages") or []
    total = coverage.get("total_pages")
    head = "《%s》" % (file_name or "材料")
    if total:
        head += "共 %d 页，" % total
    if unread:
        text = "%s未取得可读正文的页码：第 %s 页" % (head, page_ranges(unread))
        over_limit = coverage.get("over_limit_pages") or []
        if over_limit:
            text += "（其中第 %s 页超出本次文字识别页数上限）" % page_ranges(over_limit)
        blank_pages = coverage.get("blank_pages") or []
        if blank_pages:
            text += ("（其中第 %s 页经识别为空白页，无正文、无图像、无图形，无需人工翻看）"
                     % page_ranges(blank_pages))
        suspect_pages = coverage.get("suspect_pages") or []
        if suspect_pages:
            text += "（其中第 %s 页文字质量可疑，需人工核对原件）" % page_ranges(suspect_pages)
        return text + "；涉及 %d 条结论。" % affected
    # 整体读取失败/无文本层：页码本身未知（不猜），只如实说明没读到正文。
    return "%s未能读取正文；涉及 %d 条结论。" % (head, affected)


def build_coverage(db: Session, task: BsTask, *, finding_count: int | None = None) -> dict:
    """构造报告的可读性/覆盖说明（纯读，不改变收口判据）。

    ``service.review_output_readiness`` 继续服务于“完成复核/收口”这条严格链路；
    报告读取则允许已启动任务先展示中间结果。这里把缺组、运行中、运行失败和
    “尚无逐项结论”都投影成报告内的限制说明，避免把空结果误写成“全部符合”。
    """
    if finding_count is None:
        finding_count = (db.query(BsFinding)
                         .filter(BsFinding.task_id == task.id)
                         .count())
    selected = [group_id for group_id, group in reference_snapshots.CHECK_GROUPS.items()
                if (task.scope_a1 if group["scope"] == "a1" else task.scope_a3)]
    snapshot = reference_snapshots.get_reference_snapshot(db, task)
    limitations = []
    completed, pending, missing = [], [], []
    if snapshot:
        completed = [run.get("group_id") for run in snapshot.get("runs") or []
                     if run.get("state") == "completed" and run.get("group_id") in selected]
        pending = [group for group in snapshot.get("pending_groups") or [] if group in selected]
        missing = [group for group in snapshot.get("missing_groups") or [] if group in selected]
        limitations.extend(snapshot.get("limitations") or [])
        for run in snapshot.get("runs") or []:
            if run.get("state") != "completed":
                limitations.extend(run.get("limitations") or [])
    elif finding_count:
        missing = list(selected)
        limitations.append({
            "code": "run_record_missing",
            "message": "本任务已有核查结论，但未留存本代分组运行记录；以下内容仅作中间报告，不能证明全部范围已完成核查。",
        })

    # 去重但保留原始顺序，避免同一失败原因在报告中重复出现。
    unique_limitations = []
    seen = set()
    for limitation in limitations:
        if not isinstance(limitation, dict):
            continue
        key = (limitation.get("code"), limitation.get("message"), limitation.get("group_id"))
        if key in seen:
            continue
        seen.add(key)
        unique_limitations.append(dict(limitation))
    limitations = unique_limitations

    if pending:
        limitations.append({
            "code": "groups_pending",
            "message": "以下核查分组仍在处理中：%s。当前报告为中间结果。" %
                       "、".join(reference_snapshots.CHECK_GROUPS[group]["label"]
                                 for group in pending),
        })
    if missing:
        limitations.append({
            "code": "groups_incomplete",
            "message": "以下核查分组尚未完成：%s。当前报告为中间结果。" %
                       "、".join(reference_snapshots.CHECK_GROUPS[group]["label"]
                                 for group in missing),
        })
    # 完整度由选定分组的终态回执决定；零问题但全部分组 completed 也是完整结果。
    complete = bool(selected) and not pending and not missing \
        and set(completed) >= set(selected)
    # 已有全部分组的 completed 回执且确实没有问题时，这是一个完整的“未发现问题”
    # 结果；只有运行尚未完成/失败/无逐项结论时，空 finding 才是中间限制。
    if finding_count == 0 and not complete:
        limitations.append({
            "code": "no_findings",
            "message": "当前尚无逐项核查结论，不能视为已核查无问题或全部符合。",
        })
    status = "complete" if complete else "intermediate"
    return {
        "complete": complete,
        "status": status,
        "status_label": "完整结果" if complete else "中间结果",
        "selected_groups": selected,
        "completed_groups": list(dict.fromkeys(completed)),
        "pending_groups": list(dict.fromkeys(pending)),
        "missing_groups": list(dict.fromkeys(missing)),
        "finding_count": finding_count,
        "summary": {
            "completed": len(set(completed) & set(selected)),
            "selected": len(selected),
            "finding_count": finding_count,
        },
        "limitations": limitations,
    }


def build_run_lineage(db: Session, task: BsTask) -> dict:
    """Project parse/reference/rerun receipts into one optional report field.

    The event stream remains the source of truth. This projection deliberately keeps
    only stable lineage fields so old report consumers can ignore it safely.
    """
    events = (db.query(BsTaskEvent)
              .filter(BsTaskEvent.task_id == task.id)
              .order_by(BsTaskEvent.sequence, BsTaskEvent.id)
              .all())
    material_runs = []
    reference_runs = []
    reruns = []
    for event in events:
        payload = event.payload_json if isinstance(event.payload_json, dict) else {}
        if event.event_type in {"material.parsed", "material.parse_failed", "material.subject_conflict", "material.subject_unresolved"}:
            run_id = payload.get("run_id")
            if run_id:
                material_runs.append({
                    "run_id": run_id,
                    "event_type": event.event_type,
                    "material_id": payload.get("material_id"),
                    "file_name": payload.get("file_name"),
                    "input_sha256": payload.get("input_sha256"),
                    "pages": payload.get("pages"),
                    "ocr_recovered": payload.get("ocr_recovered"),
                    "layout_pages": payload.get("layout_pages"),
                    "degraded": payload.get("degraded"),
                    "failure_reason": payload.get("reason") or payload.get("message"),
                    "sequence": event.sequence,
                })
        elif event.event_type == "knowledge.reference_completed":
            run_id = payload.get("run_id")
            if run_id:
                reference_runs.append({
                    "run_id": run_id,
                    "review_run_id": payload.get("review_run_id"),
                    "group_id": payload.get("group_id"),
                    "step_id": payload.get("step_id"),
                    "status": payload.get("status") or payload.get("state"),
                    "purpose": payload.get("purpose"),
                    "previous_run_id": payload.get("previous_run_id"),
                    "rule_fingerprint": payload.get("rule_fingerprint"),
                    "basis_snapshot_id": payload.get("basis_snapshot_id"),
                    "used_for_decision": any(
                        bool(query.get("used_for_decision"))
                        for query in (payload.get("queries") or [])
                        if isinstance(query, dict)
                    ),
                    "finding_count": len(payload.get("finding_ids") or []),
                    "limitations": payload.get("limitations") or [],
                    "sequence": event.sequence,
                })
        elif event.event_type == "task.rerun":
            reruns.append({
                "scope": payload.get("scope"),
                "requested_scope": payload.get("requested_scope"),
                "rerun_groups": payload.get("rerun_groups") or [],
                "reused": payload.get("reused", 0),
                "retried": payload.get("retried", 0),
                "carry_count": payload.get("carry_count", 0),
                "fallback_reason": payload.get("fallback_reason"),
                "ruleset_version": payload.get("ruleset_version"),
                "previous_ruleset_version": payload.get("previous_ruleset_version"),
                "superseded_version_no": payload.get("superseded_version_no"),
                "sequence": event.sequence,
            })
    versions = (db.query(BsReviewVersion.version_no)
                .filter(BsReviewVersion.task_id == task.id)
                .order_by(BsReviewVersion.version_no.desc())
                .first())
    latest_review_version_no = versions[0] if versions else None
    latest_review_run_id = None
    if reference_runs:
        latest_review_run_id = reference_runs[-1].get("review_run_id")
    return {
        "schema_version": "bs.run-lineage.v1",
        "material_runs": material_runs,
        "reference_runs": reference_runs,
        "reruns": reruns,
        "latest_review_run_id": latest_review_run_id,
        "latest_review_version_no": latest_review_version_no,
    }


def _attach_qualification_summary(report: dict) -> dict:
    """在保留机器全量报告的前提下附加纯读业主摘要投影。"""
    report["qualification_summary"] = qualification_summary.build_qualification_summary(report)
    return report


def build_report(db: Session, task: BsTask) -> dict:
    """聚合任务全部已落库结论为报告 dict（纯读，不写库）。"""
    reference_snapshot = reference_snapshots.get_reference_snapshot(db, task)
    ai_coverage = ai_advisory_snapshots.collect_current_coverage(db, task, reference_snapshot, report_review_blocking)
    timeout_ids = {finding_id for group in (ai_coverage or {}).get("groups", [])
                   for finding_id in group.get("reviewable_timeout_finding_ids", [])}
    findings = (db.query(BsFinding)
                .filter(BsFinding.task_id == task.id)
                .order_by(BsFinding.id)
                .all())
    findings = ai_advisory_snapshots.filter_current_candidate_findings(
        db, task, SimpleNamespace(id=task.created_by, user_type=task.created_by_type), findings)
    # 忽略项仍保留在报告明细和导出中，统计分母单独排除忽略项。
    # 这样纸面能证明“系统提出过什么、人工为何忽略”，同时不把忽略项计入风险总数。
    report_findings = findings
    negative_coverage = (ai_advisory_snapshots.collect_current_negative_list_coverage(
        db, task, SimpleNamespace(id=task.created_by, user_type=task.created_by_type))
        if any(f.item_id == "W16-02" for f in findings) else None)
    countable_findings = [finding for finding in findings
                          if not is_ignored_review(finding.review_json)]
    materials = db.query(BsMaterial).filter(BsMaterial.task_id == task.id).all()
    material_rows, material_warnings = material_versions.project_material_rows(materials)
    version_facts = {row["material_id"]: row for row in material_rows}
    input_materials = material_versions.active_input_materials(materials)
    file_names = {m.id: m.file_name for m in materials}
    active_ids = checklist.active_subject_ids(db, task)
    subjects = (db.query(BsSubject)
                .filter(BsSubject.task_id == task.id, BsSubject.id.in_(active_ids))
                .order_by(BsSubject.id).all()) if active_ids else []
    checklist_summary = material_confirmation.attach_confirmations(
        db, task, checklist.build_summary(task, subjects, findings), findings)
    compound_projection = {f.id: projected for f, projected in zip(
        findings, project_compound_material_findings(findings, checklist_summary)) if projected is not f}
    subjects_by_id = {subject.id: subject for subject in subjects}
    neg_map, rc_map, sc_map, val_map = _label_maps()
    basis_map = configured_basis_lookup(as_of=task.baseline_date)

    by_kind: dict = {}
    for f in report_findings:
        by_kind.setdefault("negative_list" if f.kind == "negative_candidate" else f.kind, []).append(f)
    seen_kinds = {"negative_list" if f.kind == "negative_candidate" else f.kind for f in findings}

    # BS-P1-09（设计 98 §8）：未读页影响面要在逐条渲染前已知（条目带 `reading_limited`），
    # 故先做一次纯派生遍历（只读 degradations_json/pages_json，无 IO、无重算）；
    # 材料问题/合同要素仍复用后面那一次 materials 遍历。
    coverage_by_material = {}
    for material in materials:
        coverage = parsing.reading_coverage(material)
        if coverage["degraded"]:
            coverage_by_material[material.id] = coverage
    affected_by_material: dict = {}
    for f in report_findings:
        if f.material_id in coverage_by_material:
            affected_by_material[f.material_id] = affected_by_material.get(f.material_id, 0) + 1
    reading_coverage = [{
        # material_id 供前端精确匹配材料行；同名材料（重传同名文件是常态）只按文件名
        # 匹配会把未读页徽标挂到错误的行上（BS-P1-09 实现时发现的真坑）。
        "material_id": material.id,
        "file_name": material.file_name,
        "total_pages": coverage_by_material[material.id]["total_pages"],
        "unread_pages": coverage_by_material[material.id]["unread_pages"],
        "over_limit_pages": coverage_by_material[material.id]["over_limit_pages"],
        "blank_pages": coverage_by_material[material.id]["blank_pages"],
        "suspect_pages": coverage_by_material[material.id]["suspect_pages"],
        "page_states": coverage_by_material[material.id].get("page_states", []),
        "page_state_history": coverage_by_material[material.id].get("page_state_history", []),
        "page_state_counts": {
            state: sum(1 for item in coverage_by_material[material.id].get("page_states", [])
                       if item.get("state") == state)
            for state in {item.get("state") for item in coverage_by_material[material.id].get("page_states", [])
                          if item.get("state")}
        },
        "affected_findings": affected_by_material.get(material.id, 0),
        "summary_text": reading_summary(
            material.file_name, coverage_by_material[material.id],
            affected_by_material.get(material.id, 0)),
        "suggestion_text": _READING_SUGGESTION_TEXT,
    } for material in materials if material.id in coverage_by_material]

    sections = []
    for kind, title in _SECTIONS:
        items = []
        for f in sorted(by_kind.get(kind, []), key=_item_sort_key):
            open_term = is_open_term(f)
            display_finding = compound_projection.get(f.id, f)
            missing_info = finding_missing(display_finding)
            subject = subjects_by_id.get(f.subject_id)
            original_trace = decision_trace(f, basis_map.get(f.item_id))
            display_trace = original_trace
            if display_finding is not f:
                display_trace = {**original_trace, "status": display_finding.status,
                    "status_label": status_label(display_finding.status, open_term), "missing": missing_info,
                    "summary": "原规则结果与当前材料确认分别保留；" + display_finding.message}
            items.append({
                "finding_id": f.id,
                "item_id": f.item_id,
                "category": title,
                "label": _item_label(f.item_id, neg_map, rc_map, sc_map, val_map),
                "subject_id": f.subject_id,
                "subject": ({"subject_id": subject.id, "stable_code": subject.stable_code,
                             "name": subject.name, "subject_type": subject.subject_type}
                            if subject is not None else None),
                "status": display_finding.status,
                **({"machine_status": f.status, "machine_message": f.message,
                    "machine_decision_trace": original_trace} if display_finding is not f else {}),
                "report_review_blocking": report_review_blocking(f),
                "material_only": display_finding is not f or checklist.is_material_only_finding(f),
                # 意见书和缺件汇总共用同一判据，避免一个说“明确缺件”、
                # 另一个把“无法核实”也列进补件清单。
                "material_missing": is_explicit_material_missing(display_finding),
                # 开放条款单独给「仅登记」标签：它与「缺材料」是两件事，混在一起会让人
                # 去找一份并不存在的材料（BS-P1-11）。取词走 status_label 单点。
                "status_label": status_label(display_finding.status, open_term),
                "open_term": open_term,
                # BS-P1-12：结构化缺件（key/标签/材料类）。面板与补件清单都读这里，
                # 不再靠正则拆 message——文案改一次不会让面板坏一次。
                "missing": missing_info,
                # A-13（B2）：严重度/置信档位（确定性矩阵判定，落库于 _replace_findings）
                "severity": f.severity,
                "severity_label": SEVERITY_LABELS.get(f.severity, f.severity),
                "confidence": f.confidence,
                "message": finding_text.display_finding_message(display_finding, negative_coverage, f.id in timeout_ids),
                "recommendation": _recommendation(f, _item_label(f.item_id, neg_map, rc_map, sc_map, val_map)),
                "source": f.source,
                "evidence": _evidence(f, file_names, version_facts),
                "decision_trace": display_trace,
                "evidence_list": [
                    dict(ref,
                         file_name=file_names.get(ref.get("material_id")),
                         revision_no=(version_facts.get(ref.get("material_id")) or {}).get("revision_no"),
                         version_role=(version_facts.get(ref.get("material_id")) or {}).get(
                             "version_role", "legacy_unknown"),
                         replacement_target_id=(version_facts.get(ref.get("material_id")) or {}).get(
                             "replacement_target_id"))
                    for ref in (f.evidence_json or [])
                ],
                "review": review_dto(f.review_json),
                "human_disposition": human_disposition(f.review_json),
                # BS-P1-09（设计 98 §8）：该条结论引用的材料是否有未读页（不改判定，
                # 只让读者知道「这条结论的证据材料没读全」）。
                "reading_limited": f.material_id in coverage_by_material,
            })
        sections.append({
            "kind": kind,
            "title": title,
            "checked": kind in seen_kinds,
            "items": items,
        })

    # 材料问题 + 合同要素：同一次 materials 遍历（不新增第二次遍历）。
    # - 材料解析降级记录（口径不变）逐条汇入 degradations；
    # - 仅三类合同材料调 extract_contract_fields（F5 基准日兜底降级在此并入同一列表，
    #   消费链：extract 生产者 → 报告 material_issues → 前端/Word/PDF 展示，纪律 5）。
    degradations = []
    contract_facts = []
    for m in input_materials:
        for d in m.degradations_json or []:
            degradations.append({
                "file_name": m.file_name,
                "message": _DEGRADATION_TEXTS.get(d.get("kind"), d.get("message", "")),
            })
        role = classify_material(m.file_name)
        if role not in _CONTRACT_ROLES and getattr(m, "role", None) in _CONTRACT_ROLES:
            role = m.role
        if role not in _CONTRACT_ROLES:
            continue
        res = extract.extract_contract_fields(m, baseline_date=task.baseline_date)
        for d in res.get("degradations") or []:
            degradations.append({
                "file_name": m.file_name,
                "message": _DEGRADATION_TEXTS.get(d.get("kind"), d.get("message", "")),
            })
        fields = res.get("fields") or {}
        fields["contract_type"] = report_contract_type.project_contract_type(m)
        contract_facts.append({
            "file_name": m.file_name,
            "role": role,
            "role_label": ROLE_LABELS.get(role, role),
            "project_name": extract.extract_project_name(m),
            "fields": {k: fields.get(k) for k in _CONTRACT_FACT_ORDER},
        })
    # F1-2：合同主体角色证据独立于专业/劳务 BsSubject；缺失和冲突保持三态，
    # 不为了填充报告抬头而猜测施工总承包单位。
    contract_party_evidence = contract_party_evidence_module.resolve_contract_party_evidence(input_materials)
    packet_facts = report_basic_facts.extract_report_basic_facts(input_materials)
    contract_party_evidence = report_basic_facts.merge_party_facts(
        contract_party_evidence, packet_facts)
    contract_party_evidence["employer"] = report_basic_facts.apply_employer_confirmation(
        contract_party_evidence.get("employer"), report_basic_facts.latest_employer_confirmation(db, task.id))

    # 报告封面与工作台共用合同事实：历史任务可能没有显式填写 task.project_name，
    # 但合同材料已经抽取出工程名称/金额/签订日期时，不能在报告封面退回“未填写”。
    # 只消费命中字段，不从不可判定字段猜值；合同事实明细仍完整保留在 contract_facts。
    primary_contract_fact = next(
        (fact for fact in contract_facts
         if fact.get("project_name") or (fact.get("fields") or {}).get("amount", {}).get("status") == "hit"),
        contract_facts[0] if contract_facts else None,
    )
    primary_fields = (primary_contract_fact or {}).get("fields") or {}
    primary_amount = primary_fields.get("amount") or {}
    primary_sign_date = primary_fields.get("sign_date") or {}

    # 三态计数只统计「构成结论」的条目：开放条款仅登记，不计入（BS-P1-11 / 设计 77 §1）。
    # 剔除后 hit+miss+undecidable == total，读者不会因「三项之和小于明细条数」而困惑。
    # 键固定英文（BS-P1-15 / 设计 100 §2.2），取数走 status_counts 单点。
    countable = _countable([compound_projection.get(f.id, f) for f in countable_findings])
    counts = status_counts(countable)
    open_term_count = sum(1 for finding in countable_findings if is_open_term(finding))
    coverage = build_coverage(db, task, finding_count=len(findings))
    headline = _headline(counts, len(countable))
    if coverage["complete"] and not countable:
        # 所有选定分组均已完成且没有构成问题的条目（包括仅登记开放条款）
        # 是完整的“未发现需要整改的问题”，不能再沿用空任务提示。
        headline = "本次未发现需要整改的问题。"
    elif not coverage["complete"]:
        # 空任务仍保留“尚未开始核查”的前置提示；一旦已有逐项 finding，
        # 中间报告既要显式说明覆盖未完成，也要保留风险/待确认摘要，避免
        # 用户只能看到一条技术性门槛文案而不知道当前已发现什么。
        if findings and headline != "该任务尚未开始核查。":
            headline = "本轮核查尚未形成完整结论，当前报告为中间结果；%s" % headline
        elif findings:
            headline = "本轮核查尚未形成完整结论，当前报告为中间结果。"
        else:
            headline = "该任务尚未开始核查。"
    basic_project = packet_facts.get("project_name") or {}
    basic_section = packet_facts.get("contract_section") or {}
    basic_scope = packet_facts.get("subcontract_scope") or {}
    sub_contract_fact = next((fact for fact in contract_facts
                              if fact.get("role") in {"sub", "labor"}), None)
    sub_contract_fields = (sub_contract_fact or {}).get("fields") or {}
    basic_amount = sub_contract_fields.get("amount") or {}
    subject_rows = checklist_summary.get("subjects") or []
    subject_ready = len(subject_rows) == 1 and subject_rows[0].get("status") == "ready"
    packet_amount = packet_facts.get("subcontract_amount") or {}
    contract_amount = {"value": basic_amount.get("value") if basic_amount.get("status") == "hit" else None,
                       "unit": basic_amount.get("unit"), "status": basic_amount.get("status", "unresolved"),
                       "source": sub_contract_fact.get("file_name") if sub_contract_fact else None}
    amount_result = report_basic_facts.merge_amount_facts(packet_amount, contract_amount)
    report_basic_information = {
        "project_name": report_basic_facts.project_task_fact(task.project_name, basic_project, project_name=True),
        "contract_section": report_basic_facts.project_task_fact(task.contract_section, basic_section),
        "general_contractor": contract_party_evidence.get("general_contractor") or {},
        "employer": contract_party_evidence.get("employer") or {},
        "subcontractor": (contract_party_evidence.get("subcontractor") or {}) if len(subjects) != 1 else {
            "company": subjects[0].name if subject_ready else None,
            "status": "matched" if subject_ready else "unresolved"},
        "subcontract_type": _opinion_subject_type(checklist_summary.get("subjects") or [], contract_facts),
        # 旧合同范围是关键词窗口，可能混入表格邻列；未能定位可靠标题时保持待核实。
        "subcontract_scope": basic_scope,
        "subcontract_amount": amount_result,
        "baseline_date": task.baseline_date.isoformat() if task.baseline_date else None,
    }
    report = {
        "meta": {
            "task_id": task.id,
            "title": task.title,
            "scale": task.scale,
            "scope": {"A1": task.scope_a1, "A3": task.scope_a3},
            "state": task.state,
            # A-03（B1）标段锚点：空值 → None（呈现层按「未填写」占位）；属报告内容，
            # 参与内容指纹（report_digest 只排除 generated_at，见 export.py）。
            "project_name": task.project_name or (primary_contract_fact or {}).get("project_name") or None,
            "contract_section": task.contract_section or None,
            "contract_amount": (
                {"value": primary_amount.get("value"), "unit": primary_amount.get("unit")}
                if primary_amount.get("status") == "hit" and primary_amount.get("value") is not None
                else None
            ),
            "contract_sign_date": (
                primary_sign_date.get("value")
                if primary_sign_date.get("status") == "hit" and primary_sign_date.get("value")
                else None
            ),
            "baseline_date": task.baseline_date.isoformat() if task.baseline_date else None,
            # 依据身份来自本代只读快照；历史任务没有快照时保持 None，不补造版本。
            "basis_snapshot_id": (reference_snapshot or {}).get("basis_snapshot_id"),
            "source_manifest_hash": (reference_snapshot or {}).get("source_manifest_hash"),
            "profile_code": (reference_snapshot or {}).get("profile_code") or task.profile_code,
            "ruleset_version": (reference_snapshot or {}).get("ruleset_version") or task.ruleset_version,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        },
        "summary": {
            "counts": dict(counts),
            "total": len(countable),
            # 仅登记、不参与结论的兜底条款条数（纸面必须让读者看得见，不能静默少几条）
            "open_term": {"count": open_term_count,
                          "notice": OPEN_TERM_NOTICE % open_term_count if open_term_count else None},
            "headline": headline,
            # BS-P1-12：缺件按材料类归并（P1-06 待办面板的主入口数据）。
            # 带 count 的原因：实测一份总包合同可消 39 条缺件，面板必须能说出
            # 「补这一份能解决 39 项」，否则用户不知道先补哪个。
            "missing_by_category": missing_summary(countable_findings, checklist_summary),
        },
        "advisory_conclusion": {
            "code": derive_advisory_conclusion(counts)[0],
            "text": derive_advisory_conclusion(counts)[1],
        },
        "sections": sections,
        "checklist_summary": checklist_summary,
        "reference_snapshot": reference_snapshot,
        "ai_coverage": ai_coverage,
        "negative_list_coverage": negative_coverage,
        "ai_advisories": ai_advisory_snapshots.redact_unavailable_advisories(
            db, task, ai_advisory_snapshots.collect_current_advisories(db, task)),
        # Q3：页面、报告与证据包共用同一运行回执投影；历史报告没有该字段时保持兼容。
        "run_lineage": build_run_lineage(db, task),
        "material_issues": degradations,
        # 页面、报告、Word/PDF 和证据包共用的材料版本事实；旧材料仍保留在清单中，
        # 其历史 finding 可继续回链，但新规则输入只消费 active_input_materials。
        "materials": material_rows,
        "material_version_warnings": material_warnings,
        # BS-P1-09（设计 98 §8）：未读页范围 + 受影响结论数（只列有缺口的材料）。
        # 这是结论层披露：缺读页**不改判定**，报告/意见书/前端共用同一 summary_text。
        "reading_coverage": reading_coverage,
        # 本次核查范围声明（未纳入的情形 + 原因）；消费方=报告与意见书正文
        "scope_statements": scope_statements(db, task) + (["部分 AI 辅助核查因超时未完成，相关问题已转人工核实，不代表 AI 已完成核查。"] if timeout_ids else []),
        # A-15（B1）合同要素事实认定：三类合同材料逐份 9 字段三态结果
        # （extract 层消费方；空则 []，与 meta/summary/sections/material_issues 键并存不互斥）。
        "contract_facts": contract_facts,
        "contract_party_evidence": contract_party_evidence,
        "basic_information": report_basic_information,
        "ignored_items": [
            item
            for section in sections
            for item in section["items"]
            if (item.get("review") or {}).get("decision") == "ignored"
        ],
        # 报告资格与人工处置解耦：未完成分组、运行失败和零结论都在正文披露。
        "coverage": coverage,
        "limitations": coverage["limitations"],
    }
    report = _attach_qualification_summary(report)
    report["business_report"] = business_report.build_business_report(report)
    report["business_document"] = report_document.build_report_document(report)
    return report


# ==================== 审查意见书（对齐 118 号文附件 1《分包单位认可表》） ====================

# 结论措辞**逐字**取自《沪城公路〔2025〕118号》附件1「直属单位意见」的两条预置结论。
# 取证：docs/plus/blockchain-supervision/evidence/依据原文-20261014/
#       沪城公路2025-118号-文本层部分-附件1附件2.txt:58-59（文本层，已人工校对）
OPINION_CONCLUSION_PASS = "经审查，该分包报审流程符合相关规定，同意监理审核意见。"
OPINION_CONCLUSION_FAIL = "经审查，该分包不符合相关规定，不同意监理审核意见。"
# 存在无法核实项时**不出结论**：AGENTS 纪律 4「判不了就写清缺什么，严禁静默判通过」
OPINION_CONCLUSION_PENDING = ("本次审查存在无法核实项，暂不足以出具结论；"
                              "请完成人工核实后复核。")
OPINION_CONCLUSION_INTERMEDIATE = ("本轮核查尚未形成完整结论，当前意见书为中间结果；"
                                   "请完成未完成核查范围后复核。")
OPINION_MACHINE_NOTICE = ("本结论由智能体依据已上传材料自动生成，仅为提示，"
                          "不替代监理与直属单位的审查意见，须经人工复核确认后生效。")


def derive_opinion_conclusion(counts: dict) -> tuple:
    """三态计数 → 建议结论。从严：命中即建议不符合；存在不可判定则不出结论。"""
    if counts.get("hit"):
        return "fail", OPINION_CONCLUSION_FAIL
    if counts.get("undecidable"):
        return "pending", OPINION_CONCLUSION_PENDING
    return "pass", OPINION_CONCLUSION_PASS


def derive_advisory_conclusion(counts: dict) -> tuple:
    """供审批参考的单点建议结论，保留无法核实的谨慎语义。"""
    if counts.get("undecidable"):
        return "pending", "暂不予通过（供审批参考）"
    if counts.get("hit"):
        return "fail", OPINION_CONCLUSION_FAIL
    return "pass", OPINION_CONCLUSION_PASS


def _fact_value(fact: dict, key: str) -> str | None:
    """合同要素字段 → 展示文本（值 + 单位）；未命中或无值 → None（不臆造）。"""
    field = ((fact or {}).get("fields") or {}).get(key) or {}
    if field.get("status") != "hit" or field.get("value") in (None, ""):
        return None
    unit = field.get("unit") or ""
    value = field["value"]
    if isinstance(value, (int, float)):
        return "%s%s" % (format(value, ",.4g"), unit)
    return "%s%s" % (value, unit)


def _subcontract_type_value(contract_facts: list[dict]) -> str | None:
    """汇总所有分包合同的类型，避免按角色或材料顺序掩盖冲突。"""
    values = []
    for fact in contract_facts:
        if fact.get("role") not in {"sub", "labor"}:
            continue
        value = _fact_value(fact, "contract_type")
        if not value:
            return None
        values.append(value)
    distinct = set(values)
    if len(distinct) > 1:
        return "多种分包类型，需分别核实"
    return values[0] if values else None


def _opinion_subject_type(subjects: list[dict], contract_facts: list[dict]) -> str | None:
    """主体类型以已确认的审查对象为准，合同线索冲突单独提示。"""
    if not subjects:
        return "主体类型待核实"
    if any(row.get("status") != "ready" or row.get("subject_type") not in {"labor", "professional"}
           for row in subjects):
        return "主体类型待核实"
    kinds = {row["subject_type"] for row in subjects}
    if len(kinds) > 1:
        return "多种分包类型，需分别核实"
    kind = next(iter(kinds))
    label = {"labor": "劳务分包", "professional": "专业分包"}[kind]
    contract_type = _subcontract_type_value(contract_facts)
    if any(row.get("type_conflict") for row in subjects):
        return label + "（材料类型存在冲突，需核实）"
    if contract_type == "多种分包类型，需分别核实":
        return label + "（合同类型需分别核实）"
    if not contract_type:
        return label + "（合同类型待核实）"
    contract_kind = {"劳务分包": "labor", "劳务合作": "labor", "专业分包": "professional"}.get(contract_type)
    if contract_kind != kind:
        return label + "（合同类型不一致，需核实）"
    return label


def build_opinion(db: Session, task: BsTask) -> dict:
    """已落库结论 → 审查意见书内容（纯读：不重算、不写库）。

    与 `build_report` 的分工：报告回答「要改什么」（只铺命中/不可判定项），
    意见书回答「这一单能不能过」（抬头 + 结论 + 明细 + 补件 + 依据 + 签署区）。
    """
    report = build_report(db, task)
    material_gaps = []
    material_finding_ids = [item.get("finding_id")
        for subject in (report.get("checklist_summary") or {}).get("subjects", [])
        for item in subject.get("checklist_items", []) if item.get("finding_id")]
    material_findings = {f.id: f for f in db.query(BsFinding).filter(
        BsFinding.task_id == task.id, BsFinding.id.in_(material_finding_ids)).all()} if material_finding_ids else {}
    for subject_row in (report.get("checklist_summary") or {}).get("subjects", []):
        for item in subject_row.get("checklist_items", []):
            if not item.get("finding_id") or not checklist.is_material_item(item.get("item_id")):
                continue
            state = item.get("material_state")
            if not state:
                state = "missing" if item.get("missing") else "verification" if item.get("status") == "undecidable" else None
            if state in {"missing", "verification"}:
                finding = material_findings.get(item.get("finding_id"))
                prefix = "checklist:%s:" % item.get("item_id")
                missing_labels = []
                pending_labels = []
                if item.get("subitems"):
                    missing_labels = [child["label"] for child in item["subitems"]
                                      if child.get("material_state") == "missing"]
                    pending_labels = [child["label"] for child in item["subitems"]
                                      if child.get("material_state") == "verification"]
                elif state == "missing" and finding and finding.status == "hit":
                    for key in finding.missing_json or []:
                        if isinstance(key, str) and key.startswith(prefix):
                            label = checklist.missing_child_label(key)
                            if label and label not in missing_labels:
                                missing_labels.append(label)
                material_gaps.append({"subject_id": subject_row.get("subject_id"),
                                      "subject_name": subject_row.get("name"),
                                      "item_id": item.get("item_id"), "label": item.get("label"),
                                      "state": state, "missing_labels": missing_labels,
                                      "pending_labels": pending_labels})
    # BS-P1-09（设计 98 §8）：意见书限制项直接复用报告同一 summary_text（不另写措辞）。
    reading_limitations = [{"file_name": row["file_name"], "summary_text": row["summary_text"]}
                           for row in report.get("reading_coverage") or []]
    items = []
    compound_states = {(subject.get("subject_id"), item.get("item_id")): item
        for subject in (report.get("checklist_summary") or {}).get("subjects", [])
        for item in subject.get("checklist_items", []) if item.get("subitems")}
    for section in report["sections"]:
        for item in section["items"]:
            projected = dict(item, category=section["title"], kind=section["kind"])
            items.append(projected)
    # 结论只由「构成结论」的条目推导：开放条款仅登记，不参与（BS-P1-11 / 设计 77 §1）。
    # 否则材料再齐全也恒为 pending——产品永远给不出「同意」。
    countable = [i for i in items if not i.get("open_term")]
    counts = status_counts(countable)
    # 明确缺件与不可判定必须在数据结构上分开：前者进入补件清单，
    # 后者（包括完整性步骤）只进入人工核实项。该列表同时供结论文案、意见书和导出使用，避免
    # “存在不可判定”被重复解释成“都要补材料”。
    missing = [{"item_id": i["item_id"], "label": i["label"], "category": i["category"],
                "message": i["message"], "missing": i["missing"]} for i in items
               if i.get("material_missing") and not i.get("open_term")
               and (i.get("subject_id"), i.get("item_id")) not in compound_states]
    manual_review = [{"item_id": i["item_id"], "label": i["label"],
                      "category": i["category"], "kind": i["kind"],
                      "message": i["message"],
                      "human_disposition": i.get("human_disposition")}
                     for i in items
                     if (i["status"] == "undecidable" and not i.get("open_term") and not i.get("material_only"))]
    advisory_code, advisory_text = derive_advisory_conclusion(counts)
    if (report.get("coverage") or {}).get("complete") is False:
        code, conclusion = "pending", OPINION_CONCLUSION_INTERMEDIATE
        if counts.get("undecidable"):
            if missing or material_gaps:
                conclusion += "材料待核实或需补充事项见材料清单。"
            else:
                conclusion += "当前无材料缺件，相关异常需人工核实。"
    else:
        code, conclusion = derive_opinion_conclusion(counts)
        if code == "pending":
            if missing or material_gaps:
                conclusion += "材料待核实或需补充事项见材料清单；其余异常需人工核实。"
            else:
                conclusion += "当前无材料缺件，相关异常需人工核实。"

    facts = {f["role"]: f for f in report.get("contract_facts") or []}
    # 已确认主体是任务的审查对象；合同中甲方和乙方不能整体当作分包单位名称。
    confirmed_subjects = list(dict.fromkeys(
        row["name"].strip() for row in (report.get("checklist_summary") or {}).get("subjects", [])
        if row.get("status") == "ready" and isinstance(row.get("name"), str) and row["name"].strip()
    ))
    contract_subjects = []
    for fact in report.get("contract_facts") or []:
        if fact.get("role") not in {"sub", "labor"}:
            continue
        field = (fact.get("fields") or {}).get("parties") or {}
        parties = field.get("value") if field.get("status") == "hit" else None
        if not isinstance(parties, dict):
            continue
        names = {name.strip() for role, name in parties.items()
                 if role in {"乙方", "分包人", "分包单位"} and isinstance(name, str) and name.strip()}
        if len(names) == 1:
            contract_subjects.append({"name": names.pop(), "file_name": fact.get("file_name")})
    subcontractor = (confirmed_subjects[0] if len(confirmed_subjects) == 1 else confirmed_subjects) if confirmed_subjects else None
    if not subcontractor and len(contract_subjects) == 1:
        subcontractor = contract_subjects[0]["name"]
    subject_identity_conflicts = []
    if len(confirmed_subjects) == len(contract_subjects) == 1:
        contract_subject = contract_subjects[0]
        if a2_check.norm_company(confirmed_subjects[0]) != a2_check.norm_company(contract_subject["name"]):
            subject_identity_conflicts.append({"subject_name": confirmed_subjects[0],
                                               "contract_name": contract_subject["name"],
                                               "file_name": contract_subject["file_name"]})
    sub_fact = facts.get("sub") or facts.get("labor") or {}
    contractor_summary = (report.get("qualification_summary") or {}).get("general_contractor")
    if isinstance(contractor_summary, dict):
        contractor_status = contractor_summary.get("status") or "missing"
        contractor_name = (contractor_summary.get("company")
                           if contractor_status == "matched" else None)
        contractor_label = contractor_summary.get("label") or "承包单位未识别，待核实"
    else:
        # 兼容没有摘要字段的历史 report dict；当前 build_report 始终提供摘要。
        contractor_status = "legacy"
        contractor_name = _fact_value(facts.get("general") or {}, "parties")
        contractor_label = "施工总承包单位"
    subject = {
        "工程名称": report["meta"].get("project_name"),
        "标段": report["meta"].get("contract_section"),
        "施工总承包单位": contractor_name,
        "施工总承包单位状态": contractor_status,
        "施工总承包单位说明": contractor_label,
        "施工总承包单位证据": (list((contractor_summary or {}).get("evidence") or [])
                               if isinstance(contractor_summary, dict) else []),
        "分包单位名称": subcontractor,
        "分包类型": _opinion_subject_type((report.get("checklist_summary") or {}).get("subjects") or [],
                                           report.get("contract_facts") or []),
        "拟分包金额": _fact_value(sub_fact, "amount"),
        "拟分包范围": _fact_value(sub_fact, "scope"),
    }

    # 补件清单只收「缺证据」的条目；开放条款缺的不是材料，是法规明文与认定标准，
    # 列进补件清单会让人去找一份并不存在的材料（BS-P1-11）。它们单独成栏。
    # BS-P1-12：每行带结构化 key 与材料类（`missing` 子对象），补件面板按材料类归并
    # 才能说出「补这一份能解决 N 项」，不用再拆文案。列表在结论计算前统一构造。
    open_term_items = [{"item_id": i["item_id"], "label": i["label"], "category": i["category"],
                        "message": i["message"]} for i in items if i.get("open_term")]
    field_missing = []
    for fact in report.get("contract_facts") or []:
        for key, field in (fact.get("fields") or {}).items():
            for miss in (field or {}).get("missing") or []:
                field_missing.append({
                    "file_name": fact["file_name"],
                    "field_label": extract.FIELD_LABELS.get(key, key),
                    "missing": miss,
                })

    basis, seen = [], set()
    reference_snapshot = report.get("reference_snapshot")
    if reference_snapshot is not None:
        for step in reference_snapshot.get("steps") or []:
            if step.get("state") == "out_of_scope":
                continue
            for view in step.get("fixed_basis") or []:
                identity = (view["clause_id"], view.get("list_no"))
                if identity not in seen:
                    seen.add(identity)
                    basis.append(dict(view))
    else:
        lookup = configured_basis_lookup(as_of=task.baseline_date)
        for item in items:
            view = lookup.get(item["item_id"])
            if not view or view["clause_id"] in seen:
                continue
            seen.add(view["clause_id"])
            basis.append(dict(view, item_id=item["item_id"], label=item["label"]))

    return {
        "meta": dict(report["meta"], opinion_conclusion=code,
                     advisory_conclusion={"code": advisory_code, "text": advisory_text},
                     machine_notice=OPINION_MACHINE_NOTICE),
        "business_document": deepcopy(report.get("business_document")),
        "subject": subject,
        "conclusion": {
            "code": code,
            "text": conclusion,
            "counts": dict(counts),
            # total 与 counts 同分母（只算构成结论的条目）；open_term 单独交代，
            # 避免读者把「明细 19 条、结论说 16 条」当成漏项。
            "total": len(countable),
            "open_term": {"count": len(open_term_items),
                          "notice": (OPEN_TERM_NOTICE % len(open_term_items)
                                     if open_term_items else None)},
            # BS-P1-12：按材料类归并的缺件汇总（与报告 summary 同源同函数）。
            # 纸面上用户是拿着这份清单去补件的，逐条看不出「先补哪个」；实测一份总包
            # 合同能消掉近半数缺件，这个数字必须能出现在意见书与 Word 里。
            "missing_by_category": report["summary"]["missing_by_category"],
            "machine_notice": OPINION_MACHINE_NOTICE,
            "advisory_conclusion": {"code": advisory_code, "text": advisory_text},
        },
        "items": items,
        "subject_identity_conflicts": subject_identity_conflicts,
        "missing_materials": missing,
        "material_gaps": material_gaps,
        "manual_review_items": manual_review,
        # 兜底条款单列一栏（不进补件清单）：仅登记、需法规明文才可能认定
        "open_term_items": open_term_items,
        "field_missing": field_missing,
        # 范围说明与报告同源（同一函数产出，不各存一份副本）
        "scope_statements": report.get("scope_statements") or [],
        # BS-P1-09：材料未读页限制项（与报告 reading_coverage 同一文案，不另写一份）
        "reading_limitations": reading_limitations,
        "basis": basis,
        "reference_snapshot": report.get("reference_snapshot"),
        # 引用依据里有多少条来自「尚未人工校对」的文本 —— 纸面必须如实标注
        "basis_unverified": sum(1 for b in basis if not b.get("human_verified")),
        "signature": {
            "项目经理": "（签字、执业章、项目章）",
            "总监理工程师意见": "（盖章）",
            "直属单位意见": "（盖章）",
            "直属单位负责人": "（签字）",
            "preset_options": [OPINION_CONCLUSION_PASS, OPINION_CONCLUSION_FAIL],
            "suggested_option": conclusion if code in ("pass", "fail") else None,
        },
        "coverage": report.get("coverage") or {},
        "limitations": report.get("limitations") or [],
    }
