"""已确认工资/分支/退休口径的合成回归。"""
from datetime import date
from types import SimpleNamespace

from app.plus.blockchain_supervision import a2_check, personnel_evidence


ID = "110101199001010011"
COMPANY = "甲建设有限公司"
BRANCH = COMPANY + "上海分公司"


def material(text, *, subject=3):
    return SimpleNamespace(id=7, task_id=1, subject_id=subject, role="packet",
                           file_name="合成证明.pdf", parse_state="done", degradations_json=[],
                           pages_json=[{"page": 1, "text": text}])


def proof(kind, **fields):
    return {"name": "张三", "id_number": ID, "subject_id": 3, "evidence_kind": kind, **fields}


def normal_relations(unit=COMPANY):
    return [proof(kind, employer=unit, period="2026-10至2026-11")
            for kind in ("labor_contract", "social_record")]


def evaluate(relations, post="资料员"):
    return a2_check.evaluate_person_relations(
        ["张三"], relations, expected_company=COMPANY, baseline_date=date(2026, 11, 18),
        roster=[{"name": "张三", "id_number": ID, "subject_id": 3, "post": post}])[0]


def branch_proofs(subject=3):
    return [{"subject_id": subject, "evidence_kind": kind, "branch_name": BRANCH,
             "parent_company": COMPANY, "branch_candidates": [BRANCH],
             "parent_candidates": [COMPANY], "non_independent": True}
            for kind in ("branch_registration", "branch_affiliation")]


def test_extracts_actual_salary_and_separate_retirement_proofs():
    text = (f"工资流水\n姓名：张三\n身份证号：{ID}\n发放单位：{COMPANY}\n工资期间：2026-10\n"
            f"退休证\n姓名：张三\n身份证号：{ID}\n"
            f"退休人员返聘协议\n姓名：张三\n身份证号：{ID}\n返聘单位：{COMPANY}")
    facts = personnel_evidence.extract_person_supplement_evidence([material(text)])
    assert [f["evidence_kind"] for f in facts] == ["salary_record", "retirement_certificate", "reemployment_agreement"]
    assert all(f["id_number"] == ID and f["subject_id"] == 3 and f["page"] == 1 for f in facts)
    assert facts[0]["employer"] == COMPANY and facts[0]["period"] == "2026-10"


def test_submission_obligation_template_and_list_are_not_actual_proofs():
    for text in ["应提交退休证和返聘协议", "退休证\n模板\n姓名：张三", "附件清单：工资流水、退休证"]:
        assert personnel_evidence.extract_person_supplement_evidence([material(text)]) == []


def test_branch_proofs_are_explicit_not_from_name_alone():
    text = (f"营业执照\n名称：{BRANCH}\n类型：有限责任公司分公司\n"
            f"隶属关系证明\n分公司名称：{BRANCH}\n上级法人：{COMPANY}")
    registration, affiliation = personnel_evidence.extract_person_supplement_evidence([material(text)])
    assert registration["branch_name"] == BRANCH and registration["non_independent"]
    assert affiliation["parent_company"] == COMPANY


def test_branch_requires_registration_and_affiliation_for_same_subject():
    rows = normal_relations(BRANCH)
    assert evaluate(rows)["status"] == "not_included"
    assert evaluate(rows + branch_proofs()[:1])["status"] == "not_included"
    assert evaluate(rows + branch_proofs(subject=4))["status"] == "not_included"
    assert evaluate(rows + branch_proofs())["status"] == "miss"


def test_branch_proof_conflict_is_pending_not_employer_violation():
    rows = branch_proofs()
    rows[1]["parent_company"] = "乙建设有限公司"
    rows[1]["parent_candidates"] = ["乙建设有限公司"]
    result = evaluate(normal_relations(BRANCH) + rows)
    assert result["status"] == "undecidable"
    assert "证明存在冲突" in result["message"]


def test_branch_and_parent_relationship_is_symmetric_after_proof():
    rows = normal_relations()
    rows[0]["employer"] = BRANCH
    assert evaluate(rows + branch_proofs())["status"] == "miss"
    rows[1]["employer"], rows[0]["employer"] = BRANCH, COMPANY
    assert evaluate(rows + branch_proofs())["status"] == "miss"


def test_retired_default_requires_exception_documents_even_with_normal_social():
    result = evaluate(normal_relations() + [proof("retirement_certificate")])
    assert result["status"] == "not_included"
    assert "返聘协议" in result["message"] and "业主书面允许" in result["message"]


def retirement_proofs():
    return [proof("retirement_certificate"), proof("reemployment_agreement", employer=COMPANY),
            proof("attendance_commitment"), proof("owner_approval", approval_state="candidate")]


def test_retired_non_key_complete_is_review_candidate_never_auto_pass():
    result = evaluate(retirement_proofs())
    assert result["status"] == "undecidable"
    assert "候选齐备" in result["message"] and "签章" in result["message"]
    assert "缺劳动合同" not in result["message"] and "缺社保" not in result["message"]


def test_retired_key_or_unknown_post_cannot_use_non_key_exception():
    for post in ("项目经理", "技术负责人", "质量员", "安全员"):
        result = evaluate(retirement_proofs(), post=post)
        assert result["status"] == "not_included" and "关键岗位" in result["message"]
    assert evaluate(retirement_proofs(), post=None)["status"] == "undecidable"


def test_retirement_proof_for_other_identity_does_not_change_regular_person():
    rows = normal_relations() + [proof("retirement_certificate", id_number="110101199001010022")]
    assert evaluate(rows)["status"] == "miss"


def test_rejected_owner_letter_is_not_accepted_as_approval():
    rows = retirement_proofs()
    rows[-1]["approval_state"] = "rejected"
    result = evaluate(rows)
    assert result["status"] == "undecidable" and "存在拒绝" in result["message"]
    text = f"业主同意返聘函\n姓名：张三\n身份证号：{ID}\n审批意见：不同意返聘"
    fact, = personnel_evidence.extract_person_supplement_evidence([material(text)])
    assert fact["approval_state"] == "rejected"


def test_salary_missing_or_other_unit_only_adds_advisory():
    assert evaluate(normal_relations())["status"] == "miss"
    result = evaluate(normal_relations() + [proof("salary_record", employer="乙建设有限公司")])
    assert result["status"] == "miss" and result["advisories"]
    assert "劳动合同、社保单位一致" in result["message"]


def test_ambiguous_branch_proof_is_pending():
    rows = branch_proofs()
    rows[1]["branch_name"] = None
    rows[1]["branch_candidates"] = [BRANCH, "其他分公司"]
    assert evaluate(normal_relations(BRANCH) + rows)["status"] == "undecidable"


def test_branch_trade_name_is_recognized_from_proofs_not_name_substring():
    branch = "沪北工程上海分公司"
    proofs = branch_proofs()
    for row in proofs:
        row["branch_name"] = branch
        row["branch_candidates"] = [branch]
    assert evaluate(normal_relations(branch) + proofs)["status"] == "miss"
    assert evaluate(normal_relations(branch))["status"] == "not_included"


def test_pending_negative_approval_is_never_allowed_candidate():
    for opinion in ("尚未同意返聘", "未批准返聘", "不予同意返聘"):
        text = f"业主同意返聘函\n姓名：张三\n身份证号：{ID}\n审批意见：{opinion}"
        fact, = personnel_evidence.extract_person_supplement_evidence([material(text)])
        assert fact["approval_state"] == "rejected"


def test_salary_without_identity_remains_unlinked_advisory():
    result = evaluate(normal_relations() + [proof("salary_record", id_number=None, employer=COMPANY)])
    assert result["status"] == "miss"
    assert any("身份尚未交叉核对" in message for message in result["advisories"])
    assert all("单位线索一致" not in message for message in result["advisories"])
