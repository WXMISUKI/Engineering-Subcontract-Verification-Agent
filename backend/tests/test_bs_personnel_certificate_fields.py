"""人员证书字段候选的正式回归；合成输入且禁止外部调用。"""
import json
from types import SimpleNamespace

import pytest

from app.plus.blockchain_supervision import personnel_certificates as certificates, personnel_evidence

ID_A, ID_B = "110101199001010011", "110101199001010022"


def material(text, *, mid=1, subject=3, name="人员附件.pdf", role="packet"):
    return SimpleNamespace(id=mid, task_id=1, subject_id=subject, file_name=name, role=role,
                           parse_state="done", degradations_json=[], pages_json=[{"page": 1, "text": text}])


def proof(*, name="张三", number=ID_A, cert="B123", title="安全生产考核合格证书"):
    return (f"{title}\n姓名：{name}\n身份证号：{number}\n证书编号：{cert}\n"
            "证书类别：交安B\n有效期起：2025-01-01\n有效期至：2027-01-01")


def roster_text():
    return ("主要管理人员名单\n| 姓名 | 职务 | 身份证号 | 职称 | 证书类别 | 证书编号 | 有效期至 | 联系电话 |\n"
            f"| 张三 | 项目经理 | {ID_A} | 工程师 | 交安B | B123 | 2027-01-01 | 13800138001 |\n"
            f"| 张三 | 安全员 | {ID_B} | 助理工程师 | 交安C | C456 | 2028-01-01 | 13800138002 |")


def test_roster_fields_are_same_row_declarations_and_not_actual_certificates():
    source = material(roster_text())
    first, second = personnel_evidence.extract_management_roster([source])
    assert (first["professional_title"], first["certificate_number"], first["validity_end"]) == (
        "工程师", "B123", "2027-01-01")
    assert second["id_number"] == ID_B and second["certificate_number"] == "C456"
    assert certificates.extract_person_certificates([source]) == []


def test_two_certificates_on_same_page_keep_their_own_fields():
    records = certificates.extract_person_certificates([material(
        proof() + "\n特种作业操作证\n姓名：李四\n证书编号：W456\n有效期至：2028-01-01")])
    assert len(records) == 2
    assert records[0]["name"] == "张三" and records[0]["certificate_number"] == "B123"
    assert records[0]["validity_end"] == "2027-01-01" and "李四" not in records[0]["quote"]
    assert records[0]["id_number"] == ID_A and records[0]["certificate_type"] == "交安B"
    assert records[1]["name"] == "李四" and records[1]["id_number"] is None
    assert records[1]["certificate_type"] is None and records[1]["validity_start"] is None
    assert records[1]["certificate_number"] == "W456" and ID_A not in records[1]["quote"]


@pytest.mark.parametrize("tail", ["营业执照\n名称：乙公司\n有效期至：2099-01-01",
                                 "营业执照副本\n名称：乙公司\n有效期至：2099-01-01",
                                 "劳动合同\n姓名：李四\n身份证号：" + ID_B,
                                 "中华人民共和国居民身份证\n姓名：张三\n身份证号：" + ID_A])
def test_other_document_stops_personal_certificate_segment(tail):
    row, = certificates.extract_person_certificates([material("安全生产考核合格证书\n姓名：张三\n证书编号：B123\n" + tail)])
    assert row["id_number"] is None and row["validity_end"] is None
    assert tail not in row["quote"]


@pytest.mark.parametrize("source", [material(proof(), name="证书样表.pdf"),
                                    material(proof() + "\n" + "说明" * 180 + "填写示例"),
                                    material("合同约定应提供安全生产考核合格证书\n" + proof()),
                                    material(proof(), role="general"), material(proof(), role="sub")])
def test_indirect_or_nonattachment_input_is_not_actual_certificate(source):
    assert certificates.extract_person_certificates([source]) == []


def test_conflicting_fields_keep_candidates_without_selecting_one():
    row, = certificates.extract_person_certificates([material(proof() + "\n证书编号：B999")])
    assert row["certificate_number"] is None and row["certificate_number_candidates"] == ["B123", "B999"]
    assert "证书编号：B999" in row["quote"]


def test_certificate_type_value_ending_in_certificate_is_not_document_boundary():
    row, = certificates.extract_person_certificates([material(proof().replace("证书类别：交安B", "证书类别：建造师注册证书"))])
    assert row["certificate_type"] == "建造师注册证书" and row["validity_end"] == "2027-01-01"


def test_same_name_people_only_receive_their_own_certificate_fields():
    from app.plus.blockchain_supervision import a2_check
    task = SimpleNamespace(id=1)
    rows = a2_check.run_person_certificate_fields_check(task, [material(roster_text()),
        material(proof() + "\n" + proof(number=ID_B, cert="C456"), mid=2)])
    assert len(rows) == 2 and all(row.status == "undecidable" for row in rows)
    for finding, number in zip(rows, (ID_A, ID_B)):
        proofs = [ref for ref in finding.evidence_json if ref["evidence_kind"] == "person_certificate"]
        assert len(proofs) == 1 and proofs[0]["id_number"] == number
        assert proofs[0]["certificate_identity_state"] == "identity_fields_correspond"
        assert "不代表证件真实性已验证" in finding.message


@pytest.mark.parametrize("source,reason", [(material(proof(number=ID_B), mid=2), "身份证号不一致"),
    (material(proof(name="李四"), mid=2), "姓名不一致"),
    (material(proof(), mid=2, subject=4), "尚无可对应")])
def test_wrong_identity_name_or_subject_does_not_lend_certificate(source, reason):
    from app.plus.blockchain_supervision import a2_check
    source_roster = material(roster_text().splitlines()[0] + "\n" + "\n".join(roster_text().splitlines()[1:3]))
    row, = a2_check.run_person_certificate_fields_check(SimpleNamespace(id=1), [source_roster, source])
    assert row.status == "undecidable" and reason in row.message
    assert all(ref["evidence_kind"] != "person_certificate" for ref in row.evidence_json)


def test_no_id_certificate_is_only_a_candidate_and_unreadable_page_is_not_borrowed():
    from app.plus.blockchain_supervision import a2_check
    source_roster = material("\n".join(roster_text().splitlines()[:3]))
    cert = material(proof(number="110101********0011"), mid=2)
    row, = a2_check.run_person_certificate_fields_check(SimpleNamespace(id=1), [source_roster, cert])
    assert "仅保留姓名候选" in row.message
    assert all(ref["certificate_identity_state"] == "identity_pending" for ref in row.evidence_json)
    cert.degradations_json = [{"kind": "parse_incomplete", "unread_pages": [1]}]
    row, = a2_check.run_person_certificate_fields_check(SimpleNamespace(id=1), [source_roster, cert])
    assert len(row.evidence_json) == 1


def test_personnel_certificate_facts_are_append_only_and_service_report_are_idempotent(db_session):
    from copy import deepcopy
    from app.plus.blockchain_supervision import material_facts, reporting, report_template_v14, service, rule_item_catalog
    from app.plus.blockchain_supervision.models import BsMaterialFact
    task = service.create_task(db_session, conversation_id=101, title="合成人员证件",
                               scope_a1=False, scope_a3=True, created_by=1)
    subject = service.create_subject(db_session, task, name="甲建设有限公司", subject_type="professional")
    roster = service.add_material(db_session, task, file_name="主要管理人员名单.pdf", role="packet", subject_id=subject.id)
    roster.pages_json, roster.parse_state = [{"page": 1, "text": roster_text()}], "done"
    cert = service.add_material(db_session, task, file_name="人员证书.pdf", role="packet", subject_id=subject.id)
    cert.pages_json, cert.parse_state = [{"page": 1, "text": proof()}], "done"
    db_session.commit()
    old = material_facts.persist_material_facts(db_session, cert, parse_batch_id="legacy-person-fields")
    db_session.commit()
    frozen = [(row.id, deepcopy(row.raw_value)) for row in old]
    for _ in range(2):
        material_facts.persist_material_facts(db_session, cert)
        db_session.commit()
    batches = material_facts.material_fact_batches(db_session, cert.id)
    assert len(batches) == 2
    assert all(db_session.get(BsMaterialFact, fid).raw_value == raw for fid, raw in frozen)
    record_fact, = [row for row in material_facts.latest_material_facts(db_session, cert.id)
                    if row.field_path == "personnel.records"]
    record, = json.loads(record_fact.normalized_value)
    assert record["certificate_number"] == "B123" and record["validity_end"] == "2027-01-01"
    assert rule_item_catalog.item_group_catalog()["A2-10"] == "a2"
    for _ in range(2):
        rows = [row for row in service.run_cert_validity(db_session, task) if row.item_id == "A2-10"]
        assert len(rows) == 2 and all(row.subject_id == subject.id for row in rows)
    report = reporting.build_report(db_session, task)
    raw = [row for row in report_template_v14._raw_items(report) if row["item_id"] == "A2-10"]
    assert len(raw) == 2 and all(report_template_v14._verdict(row) == "待核实" for row in raw)
    assert report_template_v14._issue_rows(raw) == []
    assert "证照失效" not in report_template_v14._impact(raw[0])
