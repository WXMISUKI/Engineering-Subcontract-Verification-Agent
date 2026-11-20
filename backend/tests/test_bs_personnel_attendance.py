from types import SimpleNamespace

import pytest

from app.plus.blockchain_supervision import personnel_attendance as attendance

ID_A, ID_B = "110101199001010011", "110101199001010022"


def material(text, mid=1, subject=3):
    return SimpleNamespace(id=mid, task_id=1, subject_id=subject, file_name="人员考勤附件.pdf", role="packet",
                           parse_state="done", degradations_json=[], pages_json=[{"page": 1, "text": text}])


def record(project="甲工程", number=ID_A, state="出勤"):
    return f"人员考勤记录\n项目名称：{project}\n姓名：张三\n身份证号：{number}\n考勤日期：2026-11-01\n出勤状态：{state}"


def test_two_records_keep_project_date_state_and_do_not_borrow_identity():
    first, second = attendance.extract_attendance_records([material(record() + "\n项目任职记录\n姓名：李四\n工程名称：乙工程\n任职期间：2025-01至2025-02")])
    assert first["project"] == "甲工程" and first["attendance_state"] == "出勤"
    assert first["period"] == "2026-11-01" and "李四" not in first["quote"]
    assert second["id_number"] is None and second["project"] == "乙工程"


def test_table_preserves_each_person_and_project_without_borrowing_id_from_number_column():
    rows = attendance.extract_attendance_records([material(
        "人员考勤表\n| 姓名 | 身份证号 | 项目名称 | 日期 | 出勤状态 | 证书编号 |\n"
        f"| 张三 | {ID_A} | 甲工程 | 2026-11-01 | 出勤 | 无 |\n"
        f"| 张三 | 110101********0022 | 乙工程 | 2026-11-02 | 请假 | {ID_B} |")])
    assert len(rows) == 2 and rows[0]["id_number"] == ID_A
    assert rows[1]["id_number"] is None and rows[1]["project"] == "乙工程"
    assert rows[1]["attendance_state"] == "请假"


@pytest.mark.parametrize("text", [record() + "\n" + "说明" * 160 + "样表",
    "劳动合同约定应提供考勤表\n" + record(),
    "到岗承诺书\n姓名：张三\n承诺按时到岗", "工资发放证明\n姓名：张三\n项目名称：甲工程"])
def test_template_obligation_commitment_or_payroll_not_actual_attendance(text):
    assert attendance.extract_attendance_records([material(text)]) == []


@pytest.mark.parametrize("title", ["劳动合同", "营业执照（副本）", "安全生产考核合格证书(电子版)"])
def test_next_document_stops_fields_and_empty_table_does_not_prove_absence(title):
    row, = attendance.extract_attendance_records([material(
        "人员考勤记录\n姓名：张三\n" + title + "\n项目名称：乙工程\n考勤日期：2099-01-01")])
    assert row["project"] is None and row["period"] is None
    assert attendance.extract_attendance_records([material("考勤表\n| 姓名 | 日期 |\n| | |")]) == []


def roster(number=ID_A):
    return material("主要管理人员名单\n| 姓名 | 职务 | 身份证号 | 联系电话 |\n"
                    f"| 张三 | 项目经理 | {number} | 13800138001 |", mid=10)


def test_multiple_projects_remain_prompt_and_do_not_prove_simultaneous_employment():
    from app.plus.blockchain_supervision import a2_check, reporting
    row, = a2_check.run_person_attendance_check(SimpleNamespace(id=1), [roster(), material(record()),
        material(record(project="乙工程").replace("2026-11-01", "2025-01-01"), mid=2)])
    assert row.finding_semantics == "manual_prompt" and row.status == "undecidable"
    assert not reporting.report_gate_blocking(row)
    assert "甲工程" in row.message and "乙工程" in row.message and "实际任职时段" in row.message
    assert len(row.evidence_json) == 3


@pytest.mark.parametrize("number,subject,proof_count", [(ID_B, 3, 0), (ID_A, 4, 0), ("110101********0011", 3, 1)])
def test_identity_subject_and_mask_only_form_bounded_candidates(number, subject, proof_count):
    from app.plus.blockchain_supervision import a2_check
    row, = a2_check.run_person_attendance_check(SimpleNamespace(id=1), [roster(), material(record(number=number), subject=subject)])
    refs = [ref for ref in row.evidence_json if ref["evidence_kind"] == "attendance_record"]
    assert len(refs) == proof_count and row.finding_semantics == "manual_prompt"
    if refs:
        assert refs[0]["attendance_identity_state"] == "identity_pending"


def test_commitment_and_unreadable_record_are_not_actual_attendance():
    from app.plus.blockchain_supervision import a2_check
    actual = material(record())
    actual.degradations_json = [{"kind": "parse_incomplete", "unread_pages": [1]}]
    commitment = material(f"到岗承诺书\n姓名：张三\n身份证号：{ID_A}\n承诺按时到岗", mid=2)
    row, = a2_check.run_person_attendance_check(SimpleNamespace(id=1), [roster(), actual, commitment])
    assert "不能证明实际出勤" in row.message
    assert not any(ref["evidence_kind"] == "attendance_record" for ref in row.evidence_json)


@pytest.mark.parametrize("title", ["人员考勤记录", "安全生产考核合格证书"])
def test_commitment_cannot_borrow_identity_or_company_from_next_document(title):
    from app.plus.blockchain_supervision import personnel_evidence
    text = ("到岗承诺书\n姓名：张三\n承诺到岗\n" + title +
            f"\n身份证号：{ID_A}\n单位名称：错误公司\n项目名称：错误工程")
    row, = [row for row in personnel_evidence.extract_person_supplement_evidence([material(text)])
            if row["evidence_kind"] == "attendance_commitment"]
    assert row["id_number"] is None and row["employer"] is None
    assert title not in row["quote"]


def test_service_persistence_report_and_gate_keep_prompt_separate(db_session):
    import json
    from app.plus.blockchain_supervision import material_facts, report_template_v14 as v14, reporting, rule_item_catalog, service
    task = service.create_task(db_session, conversation_id=101, title="合成在岗提示", scope_a1=False, scope_a3=True, created_by=1)
    subject = service.create_subject(db_session, task, name="甲建设有限公司", subject_type="professional")
    for name, text in (("主要管理人员名单.pdf", roster().pages_json[0]["text"]), ("考勤记录.pdf", record())):
        source = service.add_material(db_session, task, file_name=name, role="packet", subject_id=subject.id)
        source.pages_json, source.parse_state = [{"page": 1, "text": text}], "done"
        db_session.commit()
        material_facts.persist_material_facts(db_session, source)
        db_session.commit()
    records = [json.loads(row.normalized_value) for row in material_facts.latest_material_facts(db_session, source.id)
               if row.field_path == "personnel.records"]
    assert records[0][0]["project"] == "甲工程"
    assert rule_item_catalog.item_group_catalog()["A2-11"] == "a2"
    for _ in range(2):
        prompts = [row for row in service.run_person_consistency(db_session, task) if row.item_id == "A2-11"]
        assert len(prompts) == 1 and prompts[0].subject_id == subject.id
        assert prompts[0].finding_semantics == "manual_prompt" and not reporting.report_gate_blocking(prompts[0])
    report = reporting.build_report(db_session, task)
    assert report["summary"]["manual_prompt"]["count"] == 1
    assert not any(row["item_id"] == "A2-11" for row in v14._raw_items(report))
    rows = v14._manual_prompt_rows(report)
    assert len(rows) == 1 and "甲工程" in rows[0]["message"] and rows[0]["locations"]
    report["sections"] = [{"kind": "person_consistency", "items": [row for section in report["sections"]
        for row in section.get("items", []) if row.get("item_id") == "A2-11"]}]
    raw = v14._raw_items(report)
    assert raw == [] and v14._issue_rows(raw) == [] and v14._pending_rows(raw) == []
    assert not v14._signing_conclusion(raw, {}, {}).get("conditions")


def test_attendance_and_staffing_service_calls_do_not_erase_each_other(db_session):
    from app.plus.blockchain_supervision import service
    from app.plus.blockchain_supervision.models import BsFinding
    task = service.create_task(db_session, conversation_id=101, title="合成核查组隔离", scope_a1=False, scope_a3=True, created_by=1)
    subject = service.create_subject(db_session, task, name="甲建设有限公司", subject_type="professional")
    source = service.add_material(db_session, task, file_name="主要管理人员名单.pdf", role="packet", subject_id=subject.id)
    source.pages_json, source.parse_state = roster().pages_json, "done"
    db_session.commit()
    service.run_personnel_staffing(db_session, task)
    staffing_ids = {row.id for row in db_session.query(BsFinding).filter_by(task_id=task.id, kind="personnel_adequacy")}
    assert staffing_ids
    service.run_person_consistency(db_session, task)
    assert staffing_ids == {row.id for row in db_session.query(BsFinding).filter_by(task_id=task.id, kind="personnel_adequacy")}
    service.run_personnel_staffing(db_session, task)
    assert db_session.query(BsFinding).filter_by(task_id=task.id, item_id="A2-11").count() == 1


def test_v14_manual_prompt_is_visible_without_changing_machine_conclusion_or_signed_conditions():
    from copy import deepcopy
    from test_report_template_v14 import sample_report
    from app.plus.blockchain_supervision import report_template_v14 as v14
    report = sample_report()
    before = v14.build_document(report)
    report["sections"].append({"kind": "person_consistency", "items": [{
        "item_id": "A2-11", "finding_id": 123, "status": "undecidable", "finding_semantics": "manual_prompt",
        "label": "在岗辅助提示", "message": "张三考勤记录需核对实际任职时段", "source": attendance.SOURCE,
        "evidence_list": [{"file_name": "考勤.pdf", "page": 1, "quote": "姓名：张三"}],
    }]})
    frozen = deepcopy(report)
    document = v14.build_document(report)
    assert report == frozen
    assert document["conclusion"] == before["conclusion"]
    assert document["signing_conclusion"] == before["signing_conclusion"]
    assert document["pending_total"] == before["pending_total"]
    assert document["attachments"] == before["attachments"]
    text = "\n".join(block.get("text", "") for block in document["blocks"])
    assert "履约核对提示" in text and "张三考勤记录需核对实际任职时段" in text and "考勤.pdf 第1页" in text
