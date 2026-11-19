from types import SimpleNamespace

import pytest

from app.plus.blockchain_supervision import performance_security as security


def material(text, mid=1, subject=3, role="packet"):
    return SimpleNamespace(id=mid, task_id=1, subject_id=subject, role=role, file_name="担保附件.pdf",
        parse_state="done", pages_json=[{"page": 1, "text": text}], degradations_json=[])


def guarantee(title="履约保函"):
    return title + "\n项目名称：甲工程\n合同编号：C-1\n保函编号：G-1\n申请人：甲公司\n受益人：乙公司\n担保人：丙银行\n担保金额：100万元\n担保比例：5%\n保函有效期：2026-01-01至2026-12-31\n原文状态：已失效，待重新出函"


def test_guarantee_deposit_insurance_keep_distinct_amounts_parties_and_sources():
    text = guarantee() + "\n履约保证金凭证\n凭证编号：D-1\n缴款人：丁公司\n缴纳金额：20万元\n" + \
        "建筑工程一切险保险单\n保单号：I-1\n投保人：戊公司\n被保险人：己公司\n保险金额：500万元\n保费：2万元"
    first, second, third = security.extract_security_records([material(text)])
    assert first["state"] == "已失效，待重新出函" and first["guaranteed_amount"] == "100万元"
    assert second["guarantor"] is None and second["guaranteed_amount"] == "20万元"
    assert third["insured_amount"] == "500万元" and third["premium"] == "2万元" and third["guaranteed_amount"] is None
    assert "I-1" not in first["quote"] and third["policyholder"] == "戊公司"


@pytest.mark.parametrize("title", ["履约保函（副本）", "银行履约保函", "履约保函正本"])
def test_title_copies_and_conflicting_values_remain_candidates(title):
    row, = security.extract_security_records([material(guarantee(title) + "\n担保金额：200万元")])
    assert row["guaranteed_amount"] is None and row["guaranteed_amount_candidates"] == ["100万元", "200万元"]


@pytest.mark.parametrize("change", ["sub", "general", "unread", "template", "template_heading", "pending"])
def test_templates_contract_obligations_and_unread_material_do_not_prove_submission(change):
    source = material(guarantee())
    if change in {"sub", "general"}:
        source.role = change
    elif change == "unread":
        source.degradations_json = [{"kind": "parse_incomplete", "unread_pages": [1]}]
    elif change == "template":
        source.file_name = "履约保函模板.pdf"
    elif change == "template_heading":
        source.pages_json[0]["text"] = "附件目录\n" + guarantee()
    else:
        source.parse_state = "pending"
    assert security.extract_security_records([source]) == []


@pytest.mark.parametrize("title", ["身份证", "工资支付凭证", "银行回单", "保费收据", "投标保函", "预付款保函"])
def test_next_document_never_supplies_guarantee_fields(title):
    row, = security.extract_security_records([material("履约保函\n保函编号：G-1\n" + title +
        "\n项目名称：错误项目\n担保金额：999万元")])
    assert row["project"] is row["guaranteed_amount"] is None and title not in row["quote"]


def test_empty_template_obligation_and_zero_result_do_not_prove_absence():
    for text in ("合同约定应提供履约保函", "履约保函\n保函编号：____\n担保金额：____", "保险单\n保费：2万元"):
        assert security.extract_security_records([material(text)]) == []
    assert security.run_security_check(SimpleNamespace(id=1), []) == []


@pytest.mark.parametrize("sentence", ["本保函不替代保险单", "申请人应另行提供保险单", "本次付款不构成保证金缴纳凭证"])
def test_explanatory_or_obligation_sentence_does_not_hide_later_expired_state(sentence):
    row, = security.extract_security_records([material("履约保函\n保函编号：G-1\n" + sentence + "\n原文状态：已失效")])
    assert row["state"] == "已失效" and sentence in row["quote"]


def test_service_fact_history_report_and_subject_snapshot_gate(db_session):
    import json
    from app.plus.blockchain_supervision import material_facts, reporting, report_template_v14 as v14, rule_item_catalog, service
    from app.plus.blockchain_supervision.models import BsFinding, BsMaterialFact
    task = service.create_task(db_session, conversation_id=101, title="合成担保", scope_a1=False, scope_a3=True, created_by=1)
    subject = service.create_subject(db_session, task, name="甲建设有限公司", subject_type="professional")
    source = service.add_material(db_session, task, file_name="保函.pdf", role="packet", subject_id=subject.id)
    source.pages_json, source.parse_state = [{"page": 1, "text": guarantee()}], "done"
    db_session.commit()
    db_session.add(BsMaterialFact(task_id=task.id, material_id=source.id, field_path="security.records",
        raw_value="historical", normalized_value="historical", page=1, quote="historical", quality="review_required",
        source_kind="embedded_text_layer", parse_batch_id="historical-v11", extractor_version="material-facts.v11", schema_version="1"))
    db_session.commit()
    for _ in range(2):
        material_facts.persist_material_facts(db_session, source)
        db_session.commit()
    rows = [row for row in material_facts.latest_material_facts(db_session, source.id) if row.field_path == "security.records"]
    assert len(rows) == 1 and rows[0].extractor_version == material_facts.EXTRACTOR_VERSION
    assert json.loads(rows[0].normalized_value)[0]["state"] == "已失效，待重新出函"
    assert db_session.query(BsMaterialFact).filter_by(parse_batch_id="historical-v11").one().quote == "historical"
    assert rule_item_catalog.item_group_catalog()[security.ITEM_ID] == "signature_chain"
    for _ in range(2):
        rows = [row for row in service.run_signature_chain(db_session, task) if row.item_id == security.ITEM_ID]
        assert len(rows) == 1 and rows[0].subject_id == subject.id and not reporting.report_gate_blocking(rows[0])
        assert rows[0].finding_semantics == "manual_prompt" and "已失效" in rows[0].message
        assert db_session.query(BsFinding).filter_by(task_id=task.id, item_id=security.ITEM_ID).count() == 1
    report = reporting.build_report(db_session, task)
    prompts = [row for row in v14._manual_prompt_rows(report) if row["item_id"] == security.ITEM_ID]
    assert len(prompts) == 1 and prompts[0]["locations"] and "100万元" in prompts[0]["message"]
    assert not any(row["item_id"] == security.ITEM_ID for row in v14._raw_items(report))
    subject.type_confirmed = False
    db_session.commit()
    from app.plus.blockchain_supervision.knowledge_runs import ReferenceRunSuperseded
    with pytest.raises(ReferenceRunSuperseded):
        service.run_signature_chain(db_session, task)
    service.initialize_reference_review(db_session, task)
    db_session.commit()
    row, = [row for row in service.run_signature_chain(db_session, task) if row.item_id == security.ITEM_ID]
    assert row.finding_semantics != "manual_prompt" and reporting.report_gate_blocking(row)
