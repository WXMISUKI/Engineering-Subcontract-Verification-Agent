from types import SimpleNamespace

import pytest

from app.plus.blockchain_supervision import contract_responsibility as responsibility


def material(text, mid=1, subject=3, role="sub"):
    return SimpleNamespace(id=mid, task_id=1, subject_id=subject, role=role, file_name="合同.pdf",
        parse_state="done", pages_json=[{"page": 1, "text": text}], degradations_json=[])


def contract(body):
    return "专业分包合同\n甲方：甲公司\n乙方：乙公司\n" + body


def test_multiple_rates_negative_opinions_and_responsibilities_remain_raw():
    text = contract("安全生产费不单列，含税金额100万元、税率9%，另按不含税金额的1.5%计取。\n"
                    "总包应开设工资专户并代发，分包应提供实名制名单；本合同不证明账户已经开立。")
    cost, wages = responsibility.extract_responsibility_records([material(text)])
    assert cost["percentage_mentions"] == ["9%", "1.5%"]
    assert cost["amount_mentions"] == ["100万元"]
    assert cost["quote"] == wages["quote"] == text
    assert cost["mentions_are_unassigned"] is True
    assert not wages["performance_verified"]


def test_subject_material_and_each_page_sources_are_not_collapsed():
    first = material(contract("安全生产费按1.5%约定"))
    first.pages_json.append({"page": 2, "text": "工资由总包代发，分包不自行开户"})
    second = material(contract("安全生产费按2%约定"), 2, 4)
    rows = responsibility.extract_responsibility_records([first, second])
    assert [(row["material_id"], row["subject_id"], row["page"]) for row in rows] == [(1, 3, 1), (1, 3, 2), (2, 4, 1)]


@pytest.mark.parametrize("title", ["工资专户证明", "工资支付承诺书", "工程量清单", "专业分包报审表"])
def test_next_independent_document_does_not_lend_contract_obligations(title):
    rows = responsibility.extract_responsibility_records([material(contract("安全生产费按原约定") +
        "\n" + title + "\n甲方：另一公司\n乙方：另一单位\n工资已支付20万元")])
    assert len(rows) == 1 and rows[0]["topic"] == "safety_cost"
    assert title not in rows[0]["quote"] and "20万元" not in rows[0]["quote"]


@pytest.mark.parametrize("title", ["银行回单", "工资支付凭证", "工资发放表", "实名制登记表"])
def test_actual_financial_record_is_not_misrepresented_as_contract_responsibility(title):
    rows = responsibility.extract_responsibility_records([material(contract("普通条款") +
        "\n" + title + "\n工资已支付20万元")])
    assert rows == []


def test_missing_page_breaks_contract_continuity():
    source = material(contract("普通条款"))
    source.pages_json.append({"page": 3, "text": "工资已支付"})
    assert responsibility.extract_responsibility_records([source]) == []


@pytest.mark.parametrize("change", ["general", "packet", "unread", "template", "undone"])
def test_general_contracts_unread_templates_and_wrong_roles_are_not_proof(change):
    source = material(contract("安全生产费按1.5%计取，工资应代发"))
    if change in {"general", "packet"}:
        source.role = change
    elif change == "unread":
        source.degradations_json = [{"kind": "parse_incomplete", "unread_pages": [1]}]
    elif change == "template":
        source.file_name = "分包合同模板.pdf"
    else:
        source.parse_state = "pending"
    assert responsibility.extract_responsibility_records([source]) == []


def test_no_contract_origin_no_cross_material_continuity_and_unread_resets():
    source = material(contract("合同条款"))
    source.pages_json.extend([{"page": 2, "text": "独立不可读页"}, {"page": 3, "text": "工资已支付"}])
    source.degradations_json = [{"kind": "parse_incomplete", "unread_pages": [2]}]
    assert responsibility.extract_responsibility_records([source, material("工资已支付", 2)]) == []


def test_multiple_independent_documents_cannot_restart_contract_from_party_fields():
    text = contract("普通合同条款") + "\n工资支付证明\n甲方：假公司\n乙方：假单位\n工资已支付\n身份证\n身份证号码：待填"
    assert responsibility.extract_responsibility_records([material(text)]) == []


def test_contract_obligation_ending_in_roster_is_not_an_independent_attachment():
    row, = responsibility.extract_responsibility_records([material(contract("分包应提供实名制名单"))])
    assert row["topic"] == "wages" and "应提供" in row["quote"]


@pytest.mark.parametrize("prefix", ["本合同约定", "合同约定", "施工总承包人"])
def test_contract_obligation_subject_is_not_limited_to_party_short_names(prefix):
    row, = responsibility.extract_responsibility_records([material(contract(prefix + "应提供工资支付证明"))])
    assert row["topic"] == "wages" and "应提供" in row["quote"]


@pytest.mark.parametrize("obligation", ["本合同约定需提供工资支付证明", "本合同约定乙方应按要求提供工资支付证明"])
def test_modified_obligation_auxiliaries_are_not_document_titles(obligation):
    row, = responsibility.extract_responsibility_records([material(contract(obligation))])
    assert row["topic"] == "wages" and obligation in row["quote"]


@pytest.mark.parametrize("suffix", ["（副本）", "（复印件）", "正本", "（电子版）"])
def test_contract_copy_title_retains_contiguous_page_origin(suffix):
    source = material(contract("普通条款").replace("专业分包合同", "专业分包合同" + suffix))
    source.pages_json.append({"page": 2, "text": "总包应开设工资专户并代发"})
    row, = responsibility.extract_responsibility_records([source])
    assert row["topic"] == "wages" and row["page"] == 2


@pytest.mark.parametrize("heading", ["模板", "填写说明：条款应填写完整", "样表"])
def test_explicit_template_headings_reject_whole_page(heading):
    assert responsibility.extract_responsibility_records([material(contract("安全生产费按1.5%计取") + "\n" + heading)]) == []


def test_service_fact_history_report_prompts_and_subject_gate(db_session):
    import json
    from app.plus.blockchain_supervision import material_facts, reporting, report_template_v14 as v14, rule_item_catalog, service
    from app.plus.blockchain_supervision.models import BsFinding, BsMaterialFact
    task = service.create_task(db_session, conversation_id=101, title="合成责任事实", scope_a1=False, scope_a3=True, created_by=1)
    subjects = [service.create_subject(db_session, task, name=name, subject_type="professional")
                for name in ("甲建设有限公司", "乙建设有限公司")]
    for index, subject in enumerate(subjects, 1):
        source = service.add_material(db_session, task, file_name=f"合同{index}.pdf", role="sub", subject_id=subject.id)
        text = contract(f"安全生产费按{index}%另计；含税金额100万元\n总包应开工资专户代发，分包应提供实名制名单")
        source.pages_json, source.parse_state = [{"page": 1, "text": text}], "done"
        db_session.commit()
        db_session.add(BsMaterialFact(task_id=task.id, material_id=source.id, field_path="contract.responsibility_records",
            raw_value="historical", normalized_value="historical", page=1, quote="historical",
            quality="review_required", source_kind="embedded_text_layer", parse_batch_id=f"historical-{index}",
            extractor_version="material-facts.v9", schema_version="1"))
        db_session.commit()
        for _ in range(2):
            material_facts.persist_material_facts(db_session, source)
            db_session.commit()
        rows = [row for row in material_facts.latest_material_facts(db_session, source.id)
                if row.field_path == "contract.responsibility_records"]
        assert len(rows) == 1 and rows[0].extractor_version == material_facts.EXTRACTOR_VERSION
        assert {row["topic"] for row in json.loads(rows[0].normalized_value)} == {"safety_cost", "wages"}
        assert db_session.query(BsMaterialFact).filter_by(parse_batch_id=f"historical-{index}").one().raw_value == "historical"
    assert {rule_item_catalog.item_group_catalog()[item] for item in responsibility.ITEMS.values()} == {"completeness"}
    for _ in range(2):
        findings = service.run_completeness(db_session, task)
        rows = [row for row in findings if row.item_id in responsibility.ITEMS.values()]
        assert len(rows) == 4 and {row.subject_id for row in rows} == {subject.id for subject in subjects}
        for row in rows:
            assert row.status == 'undecidable'
            if row.item_id == responsibility.ITEMS['wages']:
                assert row.finding_semantics == 'manual_prompt' and not reporting.report_gate_blocking(row)
            else:
                assert row.finding_semantics == 'machine_conclusion' and reporting.report_gate_blocking(row)
            assert all(ref["subject_id"] == row.subject_id for ref in row.evidence_json)
        assert db_session.query(BsFinding).filter(BsFinding.task_id == task.id,
            BsFinding.item_id.in_(responsibility.ITEMS.values())).count() == 4
    report = reporting.build_report(db_session, task)
    prompts = [row for row in v14._manual_prompt_rows(report) if row["item_id"] in responsibility.ITEMS.values()]
    assert len(prompts) == 2 and all(row["locations"] for row in prompts)
    assert all("辅助提示" in row["label"] for row in prompts)
    assert {row['item_id'] for row in v14._raw_items(report) if row['item_id'] in responsibility.ITEMS.values()} == {'RC-A10-cost'}
    subjects[0].type_confirmed = False
    db_session.commit()
    from app.plus.blockchain_supervision.knowledge_runs import ReferenceRunSuperseded
    with pytest.raises(ReferenceRunSuperseded):
        service.run_completeness(db_session, task)
    service.initialize_reference_review(db_session, task)
    db_session.commit()
    rows = [row for row in service.run_completeness(db_session, task)
            if row.item_id in responsibility.ITEMS.values() and row.subject_id is None]
    assert rows and all(row.finding_semantics != "manual_prompt" for row in rows)
    assert all(reporting.report_gate_blocking(row) for row in rows)
