from types import SimpleNamespace

import pytest

from app.plus.blockchain_supervision import plan_approval as plan


def material(text, mid=1, subject=3, role="packet"):
    return SimpleNamespace(id=mid, task_id=1, subject_id=subject, role=role, file_name="方案附件.pdf",
        parse_state="done", pages_json=[{"page": 1, "text": text}], degradations_json=[])


def document(title="施工方案", version="V1", suffix=""):
    return f"{title}\n项目名称：甲工程\n方案名称：吊装方案\n方案编号：P-1\n方案版本：{version}\n{suffix}"


def test_raw_three_role_fields_and_rejection_are_preserved():
    text = document(suffix="编制单位：甲分包\n编制日期：2026-01-01\n承包审查单位：乙总包\n"
        "承包审查意见：附条件同意\n承包审查日期：2026-01-02\n监理单位：丙监理\n"
        "监理书面意见：不同意，退回修改\n监理签署日期：2026-01-03")
    row, = plan.extract_plan_records([material(text)])
    assert row["supervisor_opinion"] == "不同意，退回修改"
    assert row["contractor_opinion"] == "附条件同意"
    assert "按编制" in plan.date_observation([row])


@pytest.mark.parametrize("version,subject,expected", [("V1", 3, 1), ("V2", 3, 0), ("V1", 4, 0)])
def test_only_same_subject_project_identifier_and_version_correspond(version, subject, expected):
    first, = plan.extract_plan_records([material(document())])
    approval = plan.extract_plan_records([material(document("施工方案报审表", version), 2, subject)])
    assert len(plan.matching_approvals(first, approval, plans=[first])) == expected
    assert plan.matching_approvals(first, approval, plans=[first, dict(first)]) == []
    first["version"] = None
    assert plan.matching_approvals(first, approval, plans=[first]) == []


def test_same_page_versions_and_next_certificate_do_not_lend_fields():
    rows = plan.extract_plan_records([material(document(suffix="编制日期：2026-01-01") + "\n" +
        document(version="V2", suffix="监理书面意见：尚未审查") + "\n营业执照（副本）\n监理签署日期：2026-01-03")])
    assert len(rows) == 2 and rows[1]["compile_date"] is None and rows[1]["supervisor_date"] is None
    assert "V1" not in rows[1]["quote"]


@pytest.mark.parametrize("source", [material(document(), role="sub"), material(document(suffix="应提供监理批准")),
    material("施工方案报审表\n方案名称：____\n方案编号：____\n方案版本：____")])
def test_obligations_contracts_and_templates_do_not_become_approval(source):
    assert plan.extract_plan_records([source]) == []


@pytest.mark.parametrize("dates", [("2026-01-02", "2026-01-01", "2026-01-03"),
    ("2026-01-01", "2026-99-01", "2026-01-03"), ("2026-01-01", "待核实", "2026-01-03")])
def test_reversed_and_invalid_dates_do_not_claim_procedure_compliant(dates):
    row = {field + "_candidates": [value] for field, value in zip(
        ("compile_date", "contractor_date", "supervisor_date"), dates)}
    observation = plan.date_observation([row])
    assert "未按" in observation or "不比较" in observation


def test_unread_page_and_conflicting_dates_are_not_compared():
    source = material(document())
    source.degradations_json = [{"kind": "parse_incomplete", "unread_pages": [1]}]
    assert plan.extract_plan_records([source]) == []
    assert "不比较" in plan.date_observation([{"compile_date_candidates": ["2026-01-01", "2026-01-02"]}])


def test_conflicting_name_and_mismatching_project_do_not_lend_approval():
    first, = plan.extract_plan_records([material(document())])
    approval, = plan.extract_plan_records([material(document("施工方案报审表", suffix="方案名称：支护方案"), 2)])
    assert plan.matching_approvals(first, [approval], plans=[first]) == []
    approval["name_candidates"], approval["name"] = ["吊装方案"], "吊装方案"
    approval["project"] = "乙工程"
    assert plan.matching_approvals(first, [approval], plans=[first]) == []


def test_another_plan_with_ambiguous_overlapping_identity_blocks_unique_correspondence():
    first, = plan.extract_plan_records([material(document())])
    other, = plan.extract_plan_records([material(document(suffix="项目名称：乙工程"), 2)])
    approval = plan.extract_plan_records([material(document("施工方案报审表"), 3)])
    assert other["project"] is None
    assert plan.matching_approvals(first, approval, plans=[first, other]) == []


def test_copy_title_is_supported_without_reading_next_document_fields():
    row, = plan.extract_plan_records([material(document("施工方案报审表（复印件）", suffix="监理书面意见：不同意"))])
    assert row["evidence_kind"] == "plan_approval" and row["supervisor_opinion"] == "不同意"


def test_semicolon_opinion_is_preserved():
    row, = plan.extract_plan_records([material(document(suffix="监理书面意见：不同意；退回修改；补签后另报"))])
    assert row["supervisor_opinion"] == "不同意；退回修改；补签后另报"


@pytest.mark.parametrize("opinion", ["不同意，应提供计算书后重新报审", "退回修改，修改后应提交监理复审",
    "附条件同意；\n应提供计算书；按模板补充附件", "附条件同意\n应提供设备检验报告"])
def test_actual_opinion_requirements_are_preserved_instead_of_treated_as_template(opinion):
    row, = plan.extract_plan_records([material(document("施工方案报审表", suffix="监理书面意见：" + opinion))])
    assert row["supervisor_opinion"] == opinion


@pytest.mark.parametrize("heading", ["填写说明：应提供计算书并按模板补充", "模板", "示例", "样表"])
def test_explicit_template_instruction_after_opinion_is_not_hidden_as_continuation(heading):
    assert plan.extract_plan_records([material(document("施工方案报审表",
        suffix="监理书面意见：填写意见\n" + heading))]) == []


@pytest.mark.parametrize("title", ["监理通知单", "整改通知单", "设备检验报告"])
def test_next_independent_document_cannot_supply_approval(title):
    row, = plan.extract_plan_records([material(document(suffix="编制日期：2026-01-01") +
        f"\n{title}\n监理单位：错误单位\n监理书面意见：同意\n监理签署日期：2026-01-03")])
    assert row["supervisor"] is row["supervisor_opinion"] is row["supervisor_date"] is None
    assert title not in row["quote"]


def test_service_fact_history_report_prompts_and_subject_gate(db_session):
    import json
    from app.plus.blockchain_supervision import material_facts, reporting, report_template_v14 as v14, rule_item_catalog, service
    from app.plus.blockchain_supervision.models import BsFinding, BsMaterialFact
    task = service.create_task(db_session, conversation_id=101, title="合成方案审批", scope_a1=False, scope_a3=True, created_by=1)
    subject = service.create_subject(db_session, task, name="甲建设有限公司", subject_type="professional")
    for name, text in (("方案.pdf", document(suffix="编制单位：甲建设有限公司\n编制日期：2026-01-01")),
                       ("报审.pdf", document("施工方案报审表", suffix="承包审查日期：2026-01-02\n"
                        "承包审查意见：同意\n监理签署日期：2026-01-03\n监理书面意见：不同意，退回修改"))):
        source = service.add_material(db_session, task, file_name=name, role="packet", subject_id=subject.id)
        source.pages_json, source.parse_state = [{"page": 1, "text": text}], "done"
        db_session.commit()
        if name == "方案.pdf":
            db_session.add(BsMaterialFact(task_id=task.id, material_id=source.id, field_path="plan.records",
                raw_value="historical", normalized_value="historical", page=1, quote="historical",
                quality="review_required", source_kind="embedded_text_layer", parse_batch_id="historical-v7",
                extractor_version="material-facts.v7", schema_version="1"))
            db_session.commit()
        for _ in range(2):
            material_facts.persist_material_facts(db_session, source)
            db_session.commit()
        rows = [row for row in material_facts.latest_material_facts(db_session, source.id) if row.field_path == "plan.records"]
        assert len(rows) == 1 and rows[0].extractor_version == material_facts.EXTRACTOR_VERSION
        assert json.loads(rows[0].normalized_value)[0]["plan_id"] == "P-1"
    old = db_session.query(BsMaterialFact).filter_by(parse_batch_id="historical-v7").one()
    assert old.raw_value == old.normalized_value == old.quote == "historical"
    assert rule_item_catalog.item_group_catalog()[plan.ITEM_ID] == "signature_chain"
    for _ in range(2):
        findings = service.run_signature_chain(db_session, task)
        rows = [row for row in findings if row.item_id == plan.ITEM_ID]
        assert len(rows) == 1 and rows[0].finding_semantics == "manual_prompt"
        assert rows[0].subject_id == subject.id and len(rows[0].evidence_json) == 2
        assert "不同意，退回修改" in rows[0].message and "按编制" in rows[0].message
        assert not reporting.report_gate_blocking(rows[0])
        assert db_session.query(BsFinding).filter_by(task_id=task.id, item_id=plan.ITEM_ID).count() == 1
    report = reporting.build_report(db_session, task)
    prompts = [row for row in v14._manual_prompt_rows(report) if row["item_id"] == plan.ITEM_ID]
    assert len(prompts) == 1 and len(prompts[0]["locations"]) == 2
    assert not any(row["item_id"] == plan.ITEM_ID for row in v14._raw_items(report))
    subject.type_confirmed = False
    db_session.commit()
    from app.plus.blockchain_supervision.knowledge_runs import ReferenceRunSuperseded
    with pytest.raises(ReferenceRunSuperseded):
        service.run_signature_chain(db_session, task)
    service.initialize_reference_review(db_session, task)
    db_session.commit()
    rows = [row for row in service.run_signature_chain(db_session, task) if row.item_id == plan.ITEM_ID]
    assert len(rows) == 1 and rows[0].finding_semantics != "manual_prompt"
    assert "未确认" in rows[0].message and reporting.report_gate_blocking(rows[0])
