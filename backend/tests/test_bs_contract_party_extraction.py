from types import SimpleNamespace

from app.plus.blockchain_supervision.contract_party_evidence import (
    ROLE_EMPLOYER,
    ROLE_GENERAL_CONTRACTOR,
    ROLE_SUBCONTRACTOR,
    resolve_contract_party_evidence,
)
from app.plus.blockchain_supervision.extract import (
    extract_material_subject_name,
    extract_subcontractor_name,
)


def _material(role, pages, *, material_id=1):
    return SimpleNamespace(
        id=material_id,
        role=role,
        generation_id=None,
        file_name="合同.pdf",
        parse_state="done",
        degradations_json=[],
        pages_json=[{"page": number, "text": text} for number, text in pages],
    )


def _task12_contract():
    return _material("sub", [
        (2, "建设工程施工专业分包合同\n"
            "发包人（全称）：上海建工集团股份有限公司\n"
            "分包人（全称）：上海兴友工程机械有限公司\n"
            "总包工程名称：嘉松公路越江新建工程\n"
            "分包工程名称：嘉松公路越江新建工程预制构件安装工程\n"
            "分包工程承包范围和内容：预制立柱、盖梁、箱梁、板梁、挡墙等构件安装相关的工程内容\n"
            "签约合同价（含税）：4557598.71元\n"
            "不含增值税的签约合同价：4181283.22元\n"
            "税率：9%"),
        (55, "附属协议\n"
             "甲方：上海兴友工程机械有限公司\n"
             "乙方：上海建工集团股份有限公司"),
        (56, "乙方：专业公司"),
    ])


def test_full_name_labels_extract_unique_subcontractor():
    material = _task12_contract()
    assert extract_subcontractor_name(material) == "上海兴友工程机械有限公司"
    assert extract_material_subject_name(material) == "上海兴友工程机械有限公司"


def test_reversed_supplement_agreement_does_not_conflict_with_main_contract():
    material = _task12_contract()
    resolved = resolve_contract_party_evidence([material])
    assert resolved[ROLE_SUBCONTRACTOR]["status"] == "matched"
    assert resolved[ROLE_SUBCONTRACTOR]["company"] == "上海兴友工程机械有限公司"
    contractor = resolved[ROLE_GENERAL_CONTRACTOR]
    assert contractor["status"] == "unresolved"
    assert contractor["candidate_company"] == "上海建工集团股份有限公司"


def test_general_contract_parties_keep_their_roles():
    material = _material("general", [
        (1, "施工合同\n"
            "发包人（全称）：上海城投公路建设有限公司\n"
            "承包人（全称）：上海建工集团股份有限公司"),
    ])
    resolved = resolve_contract_party_evidence([material])
    assert resolved["employer"]["company"] == "上海城投公路建设有限公司"
    assert resolved[ROLE_GENERAL_CONTRACTOR]["company"] == "上海建工集团股份有限公司"


def test_generic_party_b_and_placeholder_are_not_subcontractor_evidence():
    assert extract_subcontractor_name(_material("sub", [(1, "乙方：某建设有限公司")])) is None
    assert extract_subcontractor_name(_material("sub", [(1, "乙方：专业公司")])) is None


def test_appendix_protocol_with_reversed_roles_is_excluded():
    material = _material("sub", [
        (2, "发包人（全称）：上海建工集团股份有限公司\n"
            "分包人（全称）：上海兴友工程机械有限公司"),
        (55, "附件 1:\n委托代发工资协议书\n"
             "甲方: 上海兴友工程机械有限公司\n"
             "乙方: 上海建工集团股份有限公司"),
    ])
    resolved = resolve_contract_party_evidence([material])
    assert resolved[ROLE_SUBCONTRACTOR]["company"] == "上海兴友工程机械有限公司"
    assert resolved[ROLE_GENERAL_CONTRACTOR]["candidate_company"] == "上海建工集团股份有限公司"


def test_signature_page_colon_is_not_part_of_party_name():
    material = _material("general", [
        (1, "发包人:上海公路投资建设发展有限公司\n承包人:上海建工集团股份有限公司"),
        (17, "本页为签署页\n甲方：\n法定代表人或委托代理人：公投有限\n有限公司"),
    ])
    resolved = resolve_contract_party_evidence([material])
    names = {row["company"] for row in resolved[ROLE_EMPLOYER]["evidence"]}
    assert "法定代表人或委托代理人：公投有限公司" not in names
    assert resolved[ROLE_EMPLOYER]["company"] == "上海公路投资建设发展有限公司"


def test_report_basic_facts_use_contract_roles_and_tax_inclusive_amount():
    from app.plus.blockchain_supervision.report_basic_facts import extract_report_basic_facts
    facts = extract_report_basic_facts([_task12_contract()])
    assert facts["subcontractor"]["value"] == "上海兴友工程机械有限公司"
    assert facts["general_contractor"]["value"] == "上海建工集团股份有限公司"
    assert facts["subcontract_item_name"]["value"] == "嘉松公路越江新建工程预制构件安装工程"
    assert facts["subcontract_scope"]["value"].startswith("预制立柱")
    assert facts["total_project_name"]["value"] == "嘉松公路越江新建工程"
    assert (facts["subcontract_amount"]["value"], facts["subcontract_amount"]["unit"]) == (
        "455.759871", "万元")
    assert facts["subcontract_amount"]["tax_basis"] == "含税"
    assert (facts["subcontract_amount_excl_tax"]["value"],
            facts["subcontract_amount_excl_tax"]["unit"]) == ("418.128322", "万元")
    assert (facts["subcontract_tax_rate"]["value"],
            facts["subcontract_tax_rate"]["unit"]) == ("9", "%")


def test_ocr_uppercase_amount_and_tax_rate_format_are_supported():
    from app.plus.blockchain_supervision.report_basic_facts import extract_report_basic_facts
    material = _material("sub", [(2, (
        "四、分包合同价格\n"
        "1. 签约合同价为:人民币(大写)肆佰伍拾伍万柒仟伍佰玖拾捌元柒角壹分(¥4557598.71元);"
        "不含增值税的签约合同价为:大写:肆佰壹拾捌万壹仟贰佰捌拾叁元贰角贰分(¥4181283.22元)。\n"
        "增值税税率/征收率=9%。\n"
        "4. 分包工程承包范围和内容：预制立柱、盖梁、箱梁、板梁、挡墙等构件安装相关的工程内容。"))])
    facts = extract_report_basic_facts([material])
    assert (facts["subcontract_amount"]["value"], facts["subcontract_amount"]["unit"]) == (
        "455.759871", "万元")
    assert (facts["subcontract_amount_excl_tax"]["value"],
            facts["subcontract_amount_excl_tax"]["unit"]) == ("418.128322", "万元")
    assert facts["subcontract_tax_rate"]["value"] == "9"
    assert facts["subcontract_scope"]["value"].endswith("工程内容")


def _task_with_subject(db, *, name="上海建工集团股份有限公司"):
    from app.plus.blockchain_supervision.models import BsMaterial, BsSubject, BsTask
    task = BsTask(conversation_id=101, title="测试任务", state="setup",
                  created_by=1, created_by_type="web")
    db.add(task)
    db.flush()
    subject = BsSubject(task_id=task.id, stable_code="专业分包1", name=name,
                        subject_type="professional", origin="material_auto")
    db.add(subject)
    db.flush()
    pages = [{"page": row["page"], "text": row["text"]}
             for row in _task12_contract().pages_json]
    material = BsMaterial(task_id=task.id, subject_id=subject.id, file_name="合同.pdf",
                          role="sub", parse_state="done", material_category="sub",
                          pages_json=pages)
    db.add(material)
    db.commit()
    return task, subject, material


def test_startup_repair_fixes_auto_subject_name(db_session):
    from app.plus.blockchain_supervision import service
    task, subject, material = _task_with_subject(db_session)
    service.add_event(db_session, task.id, "material.subject_assigned", payload={
        "material_id": material.id, "subject_id": subject.id, "match": "exact_body_name"})
    repaired = service.repair_auto_subject_names(db_session, task)
    assert repaired == [{"subject_id": subject.id, "name": "上海兴友工程机械有限公司"}]
    db_session.refresh(subject)
    assert subject.name == "上海兴友工程机械有限公司"


def test_startup_repair_never_overrides_human_confirmation(db_session):
    from app.plus.blockchain_supervision import service
    task, subject, material = _task_with_subject(db_session)
    service.add_event(db_session, task.id, "material.subject_assigned", payload={
        "material_id": material.id, "subject_id": subject.id, "match": "exact_body_name"})
    service.add_event(db_session, task.id, "subject.name_confirmed", payload={
        "subject_id": subject.id, "name": subject.name})
    assert service.repair_auto_subject_names(db_session, task) == []
    db_session.refresh(subject)
    assert subject.name == "上海建工集团股份有限公司"
