"""面向业主的业务报告结构投影。

五步核查是当前系统的执行主线；PDF 样本里的十个业务模块是报告阅读主线。
本模块只做只读编排，把已有 finding、合同事实和材料清单映射到业务模块，
没有来源的模块明确标记为“本版本未覆盖”，不把缺少核查能力写成通过。
"""

from __future__ import annotations


BUSINESS_MODULES = (
    ("basic_info", "项目与分包基本信息", (), "已有任务锚点、合同要素和主体证据"),
    ("compliance", "分包合规性审查", ("negative_list", "transfer_scope", "illegal_subcontract", "signature_chain", "a1_dependency"), "负面清单、转包/违法分包和审批链"),
    ("qualification_credit", "分包人资格与信用审查", ("qualification_match", "cert_validity", "internal_records"), "资质匹配、证照有效性和已提供的内部信用材料"),
    ("personnel", "人员履约能力审查", ("person_consistency",), "关键岗位人员、劳动关系和社保一致性"),
    ("equipment", "设备与资源审查", (), "设备型号、数量、性能、自有/租赁和进场资料"),
    ("contract_clauses", "分包合同条款审查", ("contract_semantic", "amount_ratio", "workload_ratio"), "合同条款语义、金额/工程量关系和支付约定"),
    ("quality_safety_progress", "质量安全与进度审查", ("a1_dependency",), "施工方案、质量安全、环保和进度措施"),
    ("funds_payment", "资金与支付风险审查", (), "账户一致性、支付方式和低价风险"),
    ("wages", "农民工工资保障审查", ("internal_records",), "工资专户、代发、考勤、培训和特种作业资料"),
    ("conclusion", "AI 审查结论与建议", (), "建议结论、整改事项和人工复核要求"),
)

ATTACHMENT_EXPECTATIONS = (
    ("filing_form", "分包报审表/审查表", ("packet",)),
    ("contract", "拟签订的分包合同全文及工程量清单", ("sub", "labor")),
    ("qualification", "营业执照、资质证书、安全生产许可证", ("sub", "labor")),
    ("identity_authority", "银行开户证明、法定代表人身份证明、授权委托书", ("sub", "labor")),
    ("credit_performance", "类似工程业绩、信用查询及行政处罚材料", ("sub", "labor")),
    ("personnel", "主要管理人员身份证、证书、劳动合同、社保", ("sub", "labor")),
    ("labor", "劳务人员花名册、培训、工资和特种作业证", ("labor", "packet")),
    ("equipment", "拟投入机械设备清单及自有/租赁证明", ("packet",)),
    ("method_statement", "施工方案、质量安全措施、进度和应急预案", ("packet",)),
    ("payment", "安全生产费、工资保障、账户和支付佐证", ("sub", "labor", "packet")),
    ("approval", "监理人审查意见及发包人审批意见", ("packet",)),
    ("audit_trace", "AI 核验数据来源、比对规则、异常和人工复核记录", ()),
)


def _section_items(report: dict, kinds: tuple[str, ...]) -> list[dict]:
    rows = []
    for section in report.get("sections") or []:
        if section.get("kind") not in kinds:
            continue
        for item in section.get("items") or []:
            rows.append(item)
    return rows


def _status_for_items(items: list[dict], *, available: bool) -> str:
    if not available:
        return "not_covered"
    if not items:
        return "partial"
    return "available"


def _module_conclusion(items: list[dict], status: str) -> str:
    if status == "not_covered":
        return "本版本未覆盖该业务模块，不能据此判断符合或无风险。"
    counts = {"hit": 0, "miss": 0, "undecidable": 0}
    for item in items:
        key = item.get("status")
        if key in counts:
            counts[key] += 1
    if not items:
        return "已纳入报告结构，但当前没有形成可引用的逐项核查结果。"
    return "已形成 %s 项核查结果：不符合 %s 项、符合 %s 项、待核实 %s 项。" % (
        len(items), counts["hit"], counts["miss"], counts["undecidable"])


def _attachment_register(report: dict) -> list[dict]:
    materials = report.get("materials") or []
    active = [m for m in materials if m.get("version_role") != "superseded"]
    by_role: dict[str, list[dict]] = {}
    for material in active:
        by_role.setdefault(material.get("role") or "unknown", []).append(material)
    rows = []
    for code, label, roles in ATTACHMENT_EXPECTATIONS:
        matched = [m for role in roles for m in by_role.get(role, [])]
        if matched:
            state = "provided"
            note = "已接收材料，是否满足该类资料要求仍以逐项核查结果为准。"
        elif code == "audit_trace":
            state = "generated"
            note = "由系统报告、运行链路和人工复核记录提供。"
        else:
            state = "not_identified"
            note = "当前材料台账没有明确归入该类附件；不能据此认定缺失或合格。"
        rows.append({
            "code": code,
            "label": label,
            "state": state,
            "material_ids": [m.get("material_id") for m in matched if m.get("material_id") is not None],
            "file_names": [m.get("file_name") for m in matched if m.get("file_name")],
            "note": note,
        })
    return rows


def build_business_report(report: dict) -> dict:
    meta = report.get("meta") or {}
    facts = report.get("contract_facts") or []
    party = report.get("contract_party_evidence") or {}
    summary = report.get("summary") or {}
    sections = []
    for code, label, kinds, scope in BUSINESS_MODULES:
        if code == "basic_info":
            items = []
            available = bool(meta.get("project_name") or facts or party)
        elif code == "conclusion":
            items = []
            available = bool(summary or report.get("advisory_conclusion"))
        else:
            items = _section_items(report, kinds)
            available = bool(items)
            if code == "contract_clauses" and facts:
                available = True
        status = _status_for_items(items, available=available)
        limitations = []
        if status == "not_covered":
            limitations.append("当前五步核查没有对应的自动核查项或可验证来源。")
        if code == "basic_info":
            fields = {
                "项目名称": meta.get("project_name"),
                "合同段": meta.get("contract_section"),
                "承包单位": (party.get("general_contractor") or {}).get("company"),
                "核查基准日": meta.get("baseline_date"),
            }
        elif code == "conclusion":
            fields = {
                "建议结论": (report.get("advisory_conclusion") or {}).get("text"),
                "核查摘要": summary.get("headline"),
            }
        else:
            fields = {}
        sections.append({
            "code": code,
            "title": label,
            "status": status,
            "status_label": {"available": "已形成核查结果", "partial": "部分覆盖", "not_covered": "本版本未覆盖"}[status],
            "scope": scope,
            "source_kinds": list(kinds),
            "fields": fields,
            "items": items,
            "conclusion": _module_conclusion(items, status),
            "limitations": limitations,
        })
    statuses = {row["status"] for row in sections}
    overall = "complete" if statuses == {"available"} else "partial"
    return {
        "schema_version": "bs.business-report.v1",
        "overall_status": overall,
        "overall_status_label": "业务报告模块已完整覆盖" if overall == "complete" else "业务报告模块部分覆盖",
        "notice": "业务模块按当前已执行核查和材料证据投影；标记为未覆盖的模块不代表符合或无风险。",
        "sections": sections,
        "attachment_register": _attachment_register(report),
        "process_trace": {
            "source_manifest_hash": (meta.get("source_manifest_hash")),
            "basis_snapshot_id": meta.get("basis_snapshot_id"),
            "ruleset_version": meta.get("ruleset_version"),
            "generated_at": meta.get("generated_at"),
            "review_stage": report.get("review_stage"),
            "run_lineage_available": bool(report.get("run_lineage")),
            "human_review_records": sum(
                1 for section in report.get("sections") or []
                for item in section.get("items") or []
                if item.get("human_disposition")
            ),
        },
    }
