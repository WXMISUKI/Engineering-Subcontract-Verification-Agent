from types import SimpleNamespace
from datetime import date

import pytest

from app.plus.blockchain_supervision import a2_check, safety_permit_scope as permit, rule_item_catalog


def material(text, *, mid=1, role="packet", more=None):
    return SimpleNamespace(id=mid, task_id=1, subject_id=3, role=role, file_name="合成附件.pdf",
        parse_state="done", degradations_json=[], pages_json=[{"page": 1, "text": text}] + (more or []))


def certificate(scope="建筑施工", company="甲建设有限公司"):
    return "安全生产许可证\n企业名称：%s\n许可证编号：（沪）JZ安许证字[2026]123\n许可范围：%s\n有效期至：2028年1月1日" % (company, scope)


def check(materials):
    finding, = a2_check.run_safety_permit_scope_check(SimpleNamespace(id=1, baseline_date=date(2026, 11, 19)), materials,
                                                     expected_company="甲建设有限公司")
    return finding


def contract():
    return material("专业分包合同\n分包人：甲建设有限公司\n分包工程承包范围和内容：桥梁施工", mid=9, role="sub")


def test_construction_industry_correspondence_is_limited_and_keeps_both_sources():
    finding = check([material(certificate()), contract()])
    assert finding.item_id == "A2-09" and finding.status == "miss"
    assert "仅确认许可行业" in finding.message and "资质专业、等级及承包规模" in finding.message
    assert "不证明证照真实性或实际安全生产条件" in finding.message
    assert {r["material_id"] for r in finding.evidence_json} == {1, 9}
    assert finding.evidence_json[0]["scope"] == "建筑施工"


def test_no_permit_and_no_scope_are_missing_only_when_reading_complete():
    assert check([contract()]).status == "not_included"
    m = material(certificate().replace("许可范围：建筑施工\n", ""))
    assert check([m, contract()]).status == "not_included"
    m.degradations_json = [{"kind": "parse_incomplete", "ocr_suspect_pages": [1]}]
    assert check([m, contract()]).status == "undecidable"


def test_other_company_and_multiple_scopes_are_pending_not_selected_best():
    assert check([material(certificate(company="乙公司")), contract()]).status == "undecidable"
    result = check([material(certificate()), material(certificate("危险化学品生产"), mid=2), contract()])
    assert result.status == "undecidable" and "多份范围候选" in result.message


def test_other_page_or_business_license_cannot_complete_safety_scope():
    m = material(certificate().replace("许可范围：建筑施工\n", ""), more=[{
        "page": 2, "text": "营业执照\n企业名称：甲建设有限公司\n统一社会信用代码：913100000000000000\n经营范围：建筑施工"}])
    result = check([m, contract()])
    assert result.status == "not_included"
    assert result.evidence_json[0]["scope"] == ""


def test_templates_and_obligation_lists_are_not_permits():
    assert check([material("填写示例\n" + certificate()), contract()]).status == "not_included"
    assert check([material("附件清单\n应提供安全生产许可证；许可范围：建筑施工"), contract()]).status == "not_included"


def test_same_page_business_license_cannot_supply_safety_scope_or_company():
    safety = certificate().replace("许可范围：建筑施工\n", "")
    business = "营业执照\n企业名称：乙公司\n统一社会信用代码：913100000000000000\n经营范围：建筑施工"
    for text in (safety + "\n" + business, business + "\n" + safety):
        result = check([material(text), contract()])
        assert result.status == "not_included"
        record = next(r for r in result.evidence_json if r["evidence_kind"] == "safety_permit_scope")
        assert record["scope"] == "" and record["name"] == "甲建设有限公司"


def test_business_license_title_variants_and_business_scope_never_fill_permit():
    for title in ("营业执照副本", "营 业 执 照（副本）", "营业执照 正本"):
        text = certificate().replace("许可范围：建筑施工\n", "") + "\n" + title + "\n企业名称：乙公司\n统一社会信用代码：123456\n经营范围：建筑施工"
        finding = check([material(text), contract()])
        record = next(r for r in finding.evidence_json if r["evidence_kind"] == "safety_permit_scope")
        assert record["scope"] == record["uscc"] == ""
        assert title not in record["quote"]
        assert finding.status == "not_included"
    # 即使没有可识别的第二证照标题，也不能把经营范围当作许可范围。
    text = certificate().replace("许可范围：建筑施工", "经营范围：建筑施工")
    assert check([material(text), contract()]).status == "not_included"


def test_unrecognized_next_document_title_is_boundary_not_proof_of_safety_owner():
    safety = certificate().replace("企业名称：甲建设有限公司\n", "").replace("许可范围：建筑施工\n", "")
    safety += "\n发证机关：合成主管部门"
    for title in ("电子营业执照", "中华人民共和国营业执照", "企业电子资质证书"):
        text = safety + "\n" + title + "\n企业名称：甲建设有限公司\n统一社会信用代码：123456\n经营范围：建筑施工"
        finding = check([material(text), contract()])
        record = next(r for r in finding.evidence_json if r["evidence_kind"] == "safety_permit_scope")
        assert record["name"] == record["uscc"] == record["scope"] == ""
        assert title not in record["quote"]
        assert finding.status == "undecidable" and "企业名称缺失" in finding.message


def test_wrapped_certificate_number_label_is_not_next_document_heading():
    text = certificate().replace("许可证编号：", "安全生产许可证编号\n")
    m = material(text)
    m.file_name = "安全生产许可证.pdf"
    finding = check([m, contract()])
    record = next(r for r in finding.evidence_json if r["evidence_kind"] == "safety_permit_scope")
    assert record["scope"] == "建筑施工" and record["name"] == "甲建设有限公司"
    assert "安全生产许可证编号" in record["quote"]
    assert finding.status == "miss" and "仅确认许可行业" in finding.message


def test_scope_rule_is_registered_for_existing_a2_rerun():
    assert rule_item_catalog.item_group_catalog()[permit.ITEM_ID] == "a2"
    plan = rule_item_catalog.plan_rerun(expected_groups=("a2",), scope="items", item_ids=[permit.ITEM_ID], changed_item_ids=[])
    assert plan["item_ids"] == [permit.ITEM_ID]


def test_service_and_report_keep_scope_result_owned_and_idempotent(db_session):
    from app.plus.blockchain_supervision import service, reporting, report_template_v14

    task = service.create_task(db_session, conversation_id=101, title="合成安许范围",
                               scope_a1=False, scope_a3=True, created_by=1)
    subject = service.create_subject(db_session, task, name="甲建设有限公司", subject_type="professional")
    for name, role, text in (("分包合同.pdf", "sub", contract().pages_json[0]["text"]),
                             ("安全生产许可证.pdf", "packet", certificate())):
        m = service.add_material(db_session, task, file_name=name, role=role, subject_id=subject.id)
        m.pages_json = [{"page": 1, "text": text}]
        m.parse_state = "done"
    db_session.commit()
    for _ in range(2):
        rows = service.run_cert_validity(db_session, task)
        scope, = [row for row in rows if row.item_id == permit.ITEM_ID]
        assert scope.subject_id == subject.id and scope.status == "miss"
        assert {row["evidence_kind"] for row in scope.evidence_json} == {"safety_permit_scope", "subcontract_scope"}
    report = reporting.build_report(db_session, task)
    item, = [item for section in report["sections"] for item in section["items"] if item["item_id"] == permit.ITEM_ID]
    assert item["status"] == "miss" and "许可行业" in item["label"]
    assert item["evidence_list"]
    raw = report_template_v14._raw_items(report)
    section = next(row for row in report_template_v14._sections({}, raw) if row["code"] == "safety_permit")
    assert section["status"] == "通过" and "许可行业核查结果：符合" in section["summary_conclusion"]
    assert "资质专业、等级、承包规模及证照有效性分别核查" in section["summary_conclusion"]
    assert any("仅确认许可行业" in line for line in section["summary_lines"])


@pytest.mark.parametrize("activity", [
    "土木工程施工", "建筑工程施工", "公路工程施工", "桥梁施工及路面施工",
    "设备安装工程施工", "装修工程施工", "预制构件安装", "建筑拆除施工",
])
def test_explicit_construction_activities_only_establish_permit_industry(activity):
    m = contract()
    m.pages_json[0]["text"] = m.pages_json[0]["text"].replace("桥梁施工", activity)
    result = check([material(certificate()), m])
    assert result.status == "miss" and "仅确认许可行业" in result.message


@pytest.mark.parametrize("activity", [
    "桥梁工程", "专业分包", "物资供应", "预制构件制造", "汽车吊租赁",
    "桥梁施工及材料供应", "桥梁施工人员派遣", "不承担桥梁施工",
    "除桥梁施工以外的辅助工作", "桥梁施工安装指导", "爆破及桥梁施工",
    "如需桥梁施工另签合同", "桥梁施工由甲方负责", "现取消桥梁施工",
    "桥梁施工（仅提供租赁机械）", "非建筑工程施工", "桥梁施工。上述施工不由乙方承担",
])
def test_nonconstruction_mixed_negative_or_conditional_scope_is_not_auto_passed(activity):
    m = contract()
    m.pages_json[0]["text"] = m.pages_json[0]["text"].replace("桥梁施工", activity)
    result = check([material(certificate()), m])
    assert result.status == "undecidable"


def test_scope_preview_cannot_hide_later_restrictions_or_mixed_work():
    m = contract()
    m.pages_json[0]["text"] += "、" + "路面施工、" * 15 + "物资供应"
    result = check([material(certificate()), m])
    assert result.status == "undecidable"
    assert "物资供应" in next(ref["quote"] for ref in result.evidence_json if ref["evidence_kind"] == "subcontract_scope")
    m.pages_json[0]["text"] = "专业分包合同\n分包人：甲建设有限公司\n工程范围：桥梁施工\n另包含预制构件制造"
    assert check([material(certificate()), m]).status == "undecidable"


def test_multiple_scope_declarations_must_all_be_resolved_and_incomplete_pages_do_not_pass():
    m = contract()
    m.pages_json[0]["text"] += "\n工程内容：汽车吊租赁"
    assert check([material(certificate()), m]).status == "undecidable"
    m = contract()
    m.pages = 2
    m.pages_json.append({"page": 2, "text": "本页另有其他合同内容"})
    assert check([material(certificate()), m]).status == "undecidable"


def test_full_scope_stops_at_next_contract_field_and_unread_material_blocks_positive():
    m = contract()
    m.pages_json[0]["text"] += "\n合同金额：100万元\n双方不得转包"
    assert check([material(certificate()), m]).status == "miss"
    m.degradations_json = [{"kind": "parse_incomplete", "ocr_suspect_pages": [1]}]
    assert check([material(certificate()), m]).status == "undecidable"


@pytest.mark.parametrize("scope", ["建筑施工、民用爆炸物品生产", "建筑施工（不含桥梁）", "非建筑施工", "危险化学品生产"])
def test_other_or_restricted_permit_industry_is_pending_not_illegal(scope):
    result = check([material(certificate(scope)), contract()])
    assert result.status == "undecidable"
    assert "限制/状态说明" in result.message or "不据其他行业证照认定未取得" in result.message


@pytest.mark.parametrize("baseline", [None, date(2014, 1, 1), "unreadable"])
def test_unknown_or_earlier_baseline_does_not_use_current_industry_definition(baseline):
    result, = a2_check.run_safety_permit_scope_check(SimpleNamespace(id=1, baseline_date=baseline),
        [material(certificate()), contract()], expected_company="甲建设有限公司")
    assert result.status == "undecidable" and "依据版本" in result.message


def test_official_local_quotes_remain_separate_from_old_online_document():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    policy = json.loads((root / "backend/app/plus/blockchain_supervision/data/safety_permit_scope.json").read_text(encoding="utf-8"))
    for basis in policy["basis"]:
        assert basis["online_knowledge_verified"] is False
    registry = json.loads((root / "backend/app/plus/blockchain_supervision/data/review_basis.json").read_text(encoding="utf-8"))
    old, = [row for row in registry["external_documents"] if row["basis_id"] == "SAFETY-PERMIT-397"]
    assert old["quote_status"] == "pending_verification" and old["quote"] is None


@pytest.mark.parametrize("suffix", [
    "\n备注：本证不含桥梁施工", "\n许可范围：矿山开采", "\n企业名称：乙公司",
    "\n备注：证照已注销",
])
def test_later_certificate_restriction_or_duplicate_identity_cannot_hide_behind_first_field(suffix):
    assert check([material(certificate() + suffix), contract()]).status == "undecidable"


@pytest.mark.parametrize("continuation", ["\n\n另包括汽车吊租赁", "\n2.爆破施工"])
def test_blank_or_numbered_scope_continuation_is_not_proof_of_next_section(continuation):
    m = contract()
    m.pages_json[0]["text"] += continuation
    assert check([material(certificate()), m]).status == "undecidable"


def test_unreported_missing_page_numbers_cannot_support_industry_pass():
    m = material(certificate())
    m.pages = 3
    m.pages_json.append({"page": 3, "text": "附件末页"})
    assert check([m, contract()]).status == "undecidable"
