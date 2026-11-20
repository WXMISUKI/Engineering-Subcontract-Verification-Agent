from types import SimpleNamespace

import pytest

from app.plus.blockchain_supervision import training_records as training

ID_A, ID_B = "110101199001010011", "110101199001010022"


def material(text, mid=1, subject=3, role="packet"):
    return SimpleNamespace(id=mid, task_id=1, subject_id=subject, role=role, file_name="培训附件.pdf",
        parse_state="done", pages_json=[{"page": 1, "text": text}], degradations_json=[])


def record(name="张三", suffix=""):
    return f"岗前培训记录\n姓名：{name}\n身份证号：{ID_A}\n工种：焊工\n项目名称：甲工程\n培训日期：2026-11-01\n培训内容：安全操作\n考核结果：未合格；需补训{suffix}"


def test_explicit_record_preserves_negative_result_and_all_sources():
    row, = training.extract_training_records([material(record())])
    assert row["name"] == "张三" and row["result"] == "未合格；需补训"
    assert row["course"] == "安全操作" and row["id_number"] == ID_A and row["quote"] == record()
    assert row["subject_id"] == 3 and row["material_id"] == 1


def test_same_table_two_people_do_not_lend_results_or_identification():
    text = ("培训签到表\n项目名称：甲工程\n培训日期：2026-11-01\n培训课程：安全操作\n"
        "| 姓名 | 身份证号 | 工种 | 原文结果 | 证书编号 |\n| --- | --- | --- | --- | --- |\n"
        f"| 张三 | {ID_A} | 焊工 | 已签到 | 无 |\n| 李四 | 110101********0022 | 司机 | 未签到 | {ID_B} |")
    first, second = training.extract_training_records([material(text)])
    assert first["record_type"] == second["record_type"] == "signin"
    assert first["result"] == "已签到" and second["result"] == "未签到"
    assert first["id_number"] == ID_A and second["id_number"] is None
    assert first["course"] == second["course"] == "安全操作" and "李四" not in first["quote"]


def test_repeated_table_header_is_not_a_person_and_cannot_lend_other_table_fields():
    text = "培训签到表\n项目名称：甲工程\n| 姓名 | 工种 | 原文结果 |\n| 张三 | 焊工 | 已签到 |\n| 姓名 | 工种 | 原文结果 |\n| 李四 | 司机 | 未签到 |"
    rows = training.extract_training_records([material(text)])
    assert [row["name"] for row in rows] == ["张三"]


@pytest.mark.parametrize("title", ["岗前培训记录", "岗前培训记录（复印件）", "身份证", "营业执照（副本）"])
def test_same_page_next_independent_record_does_not_supply_fields(title):
    rows = training.extract_training_records([material("岗前培训记录\n姓名：张三\n" + title +
        f"\n姓名：李四\n身份证号：{ID_B}\n项目名称：乙工程")])
    assert rows[0]["name"] == "张三" and rows[0]["id_number"] is None and rows[0]["project"] is None


@pytest.mark.parametrize("change", ["sub", "general", "unread", "template", "template_heading", "pending"])
def test_contracts_templates_and_unread_pages_are_not_training(change):
    source = material(record())
    if change in {"sub", "general"}:
        source.role = change
    elif change == "unread":
        source.degradations_json = [{"kind": "parse_incomplete", "unread_pages": [1]}]
    elif change == "template":
        source.file_name = "培训记录样表.pdf"
    elif change == "template_heading":
        source.pages_json[0]["text"] += "\n填写说明：按要求填写"
    else:
        source.parse_state = "pending"
    assert training.extract_training_records([source]) == []


def test_obligation_empty_table_and_multi_name_labels_do_not_prove_training():
    for text in ("劳动合同约定应培训后上岗", "培训签到表\n| 姓名 | 签到情况 |\n| | |",
                 "岗前培训记录\n姓名：张三\n姓名：李四\n考核结果：合格"):
        assert training.extract_training_records([material(text)]) == []
    assert training.run_training_check(SimpleNamespace(id=1), [material("未识别布局")]) == []


def test_same_name_records_and_subjects_remain_independent_manual_prompts():
    rows = training.run_training_check(SimpleNamespace(id=1), [material(record()), material(record(), 2, 4)])
    assert len(rows) == 2
    assert {row.evidence_json[0]["subject_id"] for row in rows} == {3, 4}
    assert all(row.status == "undecidable" and row.finding_semantics == "manual_prompt" for row in rows)
    assert all("未合格；需补训" in row.message and "不替代" in row.message for row in rows)


def test_template_heading_before_record_does_not_disappear_outside_segment():
    assert training.extract_training_records([material("模板\n" + record())]) == []


def test_service_fact_history_report_and_subject_snapshot_gate(db_session):
    import json
    from app.plus.blockchain_supervision import material_facts, reporting, report_template_v14 as v14, rule_item_catalog, service
    from app.plus.blockchain_supervision.models import BsFinding, BsMaterialFact
    task = service.create_task(db_session, conversation_id=101, title="合成培训核对", scope_a1=False, scope_a3=True, created_by=1)
    subject = service.create_subject(db_session, task, name="甲建设有限公司", subject_type="labor")
    source = service.add_material(db_session, task, file_name="培训.pdf", role="packet", subject_id=subject.id)
    source.pages_json, source.parse_state = [{"page": 1, "text": record()}], "done"
    db_session.commit()
    db_session.add(BsMaterialFact(task_id=task.id, material_id=source.id, field_path="training.records",
        raw_value="historical", normalized_value="historical", page=1, quote="historical", quality="review_required",
        source_kind="embedded_text_layer", parse_batch_id="historical-v10", extractor_version="material-facts.v10", schema_version="1"))
    db_session.commit()
    for _ in range(2):
        material_facts.persist_material_facts(db_session, source)
        db_session.commit()
    rows = [row for row in material_facts.latest_material_facts(db_session, source.id) if row.field_path == "training.records"]
    assert len(rows) == 1 and rows[0].extractor_version == material_facts.EXTRACTOR_VERSION
    assert json.loads(rows[0].normalized_value)[0]["result"] == "未合格；需补训"
    assert db_session.query(BsMaterialFact).filter_by(parse_batch_id="historical-v10").one().quote == "historical"
    assert rule_item_catalog.item_group_catalog()[training.ITEM_ID] == "a2"
    for _ in range(2):
        rows = [row for row in service.run_a2_checks(db_session, task) if row.item_id == training.ITEM_ID]
        assert len(rows) == 1 and rows[0].subject_id == subject.id and not reporting.report_gate_blocking(rows[0])
        assert rows[0].finding_semantics == "manual_prompt"
        assert db_session.query(BsFinding).filter_by(task_id=task.id, item_id=training.ITEM_ID).count() == 1
    report = reporting.build_report(db_session, task)
    prompts = [row for row in v14._manual_prompt_rows(report) if row["item_id"] == training.ITEM_ID]
    assert len(prompts) == 1 and "未合格；需补训" in prompts[0]["message"] and prompts[0]["locations"]
    assert not any(row["item_id"] == training.ITEM_ID for row in v14._raw_items(report))
    subject.type_confirmed = False
    db_session.commit()
    from app.plus.blockchain_supervision.knowledge_runs import ReferenceRunSuperseded
    with pytest.raises(ReferenceRunSuperseded):
        service.run_a2_checks(db_session, task)
    service.initialize_reference_review(db_session, task)
    db_session.commit()
    rows = [row for row in service.run_a2_checks(db_session, task) if row.item_id == training.ITEM_ID]
    assert len(rows) == 1 and rows[0].finding_semantics != "manual_prompt" and reporting.report_gate_blocking(rows[0])
