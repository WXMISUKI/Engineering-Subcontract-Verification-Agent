from types import SimpleNamespace

import pytest

from app.plus.blockchain_supervision.personnel_evidence import contract_roster_evidence, extract_management_roster


def material(text, role="sub"):
    return SimpleNamespace(id=14, task_id=12, subject_id=3, file_name="专业合同.pdf", role=role,
        parse_state="done", pages_json=[{"page": 45, "text": text}], degradations_json=[])


def test_compact_contract_roster_without_phone_preserves_declared_qualification():
    source = material("分包人主要项目管理人员表\n项目名称：甲工程\n岗位姓名职称/资格联系方式备注\n"
        "项目经理张三二级建造师\n技术负责人李四一级建造师\n安全负责人王五安全员\n施工员赵六施工员")
    rows = extract_management_roster([source])
    assert [(row["post"], row["name"]) for row in rows] == [
        ("项目经理", "张三"), ("技术负责人", "李四"), ("安全负责人", "王五"), ("施工员", "赵六")]
    assert rows[0]["qualification_declaration"] == "二级建造师"
    assert all(row["evidence_kind"] == "contract_roster" and row["subject_id"] == 3 and row["page"] == 45 for row in rows)
    assert all(not row.get("id_number") and not row.get("certificate_number") for row in rows)
    assert contract_roster_evidence([source])[2] == 45


def test_headquarters_never_contribute_site_roles_and_next_document_is_not_borrowed():
    source = material("分包人主要项目管理人员表\n名称姓名职务职称主要资历、经验及承担过的项目\n"
        "一、总部人员\n项目经理张三\n二、现场人员\n项目经理李四项目经理\n技术负责人王五\n"
        "监理通知单\n施工员赵六")
    rows = extract_management_roster([source])
    assert [(row["post"], row["name"]) for row in rows] == [("项目经理", "李四"), ("技术负责人", "王五")]
    assert "张三" not in "".join(row["quote"] for row in rows)


@pytest.mark.parametrize("text,role", [
    ("分包人主要项目管理人员表\n岗位姓名职称\n项目经理张三", "general"),
    ("合同联系人\n岗位姓名职称\n项目经理张三", "sub"),
    ("分包人主要项目管理人员表\n岗位姓名职称\n应提供人员名单\n项目经理张三", "sub"),
    ("分包人主要项目管理人员表\n岗位姓名职称\n项目经理张三未知资格文字", "sub"),
    ("分包人主要项目管理人员表\n岗位姓名职称\n项目经理待定", "sub"),
])
def test_contract_roster_scope_and_ambiguous_rows_do_not_guess(text, role):
    assert extract_management_roster([material(text, role)]) == []


def test_contract_roster_does_not_turn_other_contract_pages_into_certificates():
    from app.plus.blockchain_supervision.personnel_certificates import extract_person_certificates
    source = material("分包人主要项目管理人员表\n岗位姓名职称\n项目经理张三二级建造师\n"
        "安全生产考核合格证书\n姓名：张三\n证书编号：B123")
    assert extract_person_certificates([source]) == []


def test_fact_batch_and_a2_fields_keep_declaration_distinct_from_proof(db_session):
    import json
    from app.plus.blockchain_supervision import material_facts, service
    from app.plus.blockchain_supervision.models import BsMaterialFact
    task = service.create_task(db_session, conversation_id=101, title="合成合同名单", scope_a1=False, scope_a3=True, created_by=1)
    subject = service.create_subject(db_session, task, name="甲建设有限公司", subject_type="professional")
    source = service.add_material(db_session, task, file_name="合同.pdf", role="sub", subject_id=subject.id)
    source.pages_json, source.parse_state = [{"page": 45, "text": "分包人主要项目管理人员表\n岗位姓名职称/资格\n项目经理张三二级建造师"}], "done"
    db_session.commit()
    for _ in range(2):
        material_facts.persist_material_facts(db_session, source)
        db_session.commit()
    row = db_session.query(BsMaterialFact).filter_by(material_id=source.id, field_path="personnel.records").one()
    declaration, = json.loads(row.normalized_value)
    assert declaration["evidence_kind"] == "contract_roster" and declaration["qualification_declaration"] == "二级建造师"
    for _ in range(2):
        rows = [row for row in service.run_cert_validity(db_session, task) if row.item_id == "A2-10"]
        assert len(rows) == 1 and rows[0].status == "undecidable"
        assert "尚无可对应的人员证书字段" in rows[0].message
        assert rows[0].evidence_json[0]["qualification_declaration"] == "二级建造师"
        assert not any(row.get("evidence_kind") == "person_certificate" for row in rows[0].evidence_json)


def test_unread_contract_roster_and_multiple_names_are_not_guessed():
    source = material("分包人主要项目管理人员表\n岗位姓名职称\n项目经理张三 李四")
    assert extract_management_roster([source]) == []
    source.pages_json[0]["text"] = source.pages_json[0]["text"].replace("张三 李四", "张三")
    source.degradations_json = [{"kind": "parse_incomplete", "unread_pages": [45]}]
    assert extract_management_roster([source]) == []


def test_repeated_same_page_roster_titles_do_not_borrow_or_duplicate_rows():
    source = material("分包人主要项目管理人员表\n岗位姓名职称\n项目经理张三\n"
        "分包人主要项目管理人员表\n名称姓名职务职称\n技术负责人李四")
    rows = extract_management_roster([source])
    assert [(row["post"], row["name"]) for row in rows] == [("项目经理", "张三"), ("技术负责人", "李四")]
    assert "名称姓名职务" in rows[1]["quote"] and "岗位姓名" not in rows[1]["quote"]


def test_template_filename_does_not_become_contract_roster_fact():
    source = material("分包人主要项目管理人员表\n岗位姓名职称\n项目经理张三")
    source.file_name = "人员表模板.pdf"
    assert extract_management_roster([source]) == []
