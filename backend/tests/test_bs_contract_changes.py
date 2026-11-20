from types import SimpleNamespace

import pytest

from app.plus.blockchain_supervision import contract_changes as changes


def material(text, mid=1, subject=3, role="packet"):
    return SimpleNamespace(id=mid, task_id=1, subject_id=subject, role=role, file_name="变更附件.pdf",
        parse_state="done", pages_json=[{"page": 1, "text": text}], degradations_json=[])


def document(title="分包合同变更协议", suffix="", new="V2"):
    return f"{title}\n项目名称：甲工程\n合同编号：C-1\n变更编号：CH-1\n变更前版本：V1\n变更后版本：{new}\n{suffix}"


def test_raw_negative_opinion_and_multiline_content_are_kept():
    row, = changes.extract_change_records([material(document(suffix="变更内容：增加工程范围；\n合同金额待复核\n"
        "监理书面意见：不同意；退回修改\n应提供计算书后重新报审\n发包人意见：尚未审查"))])
    assert row["content"] == "增加工程范围；\n合同金额待复核"
    assert row["supervisor_opinion"] == "不同意；退回修改\n应提供计算书后重新报审"
    assert row["owner_opinion"] == "尚未审查"


@pytest.mark.parametrize("subject,new,expected", [(3, "V2", 1), (4, "V2", 0), (3, "V3", 0)])
def test_only_unique_exact_subject_identity_corresponds(subject, new, expected):
    original = changes.extract_change_records([material(document())])
    approvals = changes.extract_change_records([material(document("合同变更报审表", new=new), 2, subject)])
    assert len(changes.matching_approvals(original[0], approvals, changes=original)) == expected
    assert changes.matching_approvals(original[0], approvals, changes=original + original) == []


def test_overlapping_conflicting_identity_blocks_correspondence_and_orphans_remain():
    original = changes.extract_change_records([material(document())])
    conflict = changes.extract_change_records([material(document(suffix="变更后版本：V3"), 2)])
    approvals = changes.extract_change_records([material(document("合同变更报审表"), 3)])
    assert changes.matching_approvals(original[0], approvals, changes=original + conflict) == []
    rows = changes.run_change_check(SimpleNamespace(id=1), [material(document("合同变更报审表", suffix="监理书面意见：不同意"))])
    assert len(rows) == 1 and "孤立" in rows[0].message and "不同意" in rows[0].message


@pytest.mark.parametrize("title", ["身份证", "营业执照（副本）", "监理通知单", "施工方案报审表", "履约保函", "保险单", "工资支付凭证"])
def test_independent_following_document_cannot_lend_approval(title):
    row, = changes.extract_change_records([material(document(suffix="变更内容：待核对") +
        "\n" + title + "\n监理书面意见：同意\n发包人签署日期：2026-01-01")])
    assert row["supervisor_opinion"] is row["owner_date"] is None and title not in row["quote"]


@pytest.mark.parametrize("change", ["sub", "general", "unread", "template", "template_heading", "pending"])
def test_template_contract_obligations_and_unread_pages_are_not_actual_change(change):
    source = material(document())
    if change in {"sub", "general"}:
        source.role = change
    elif change == "unread":
        source.degradations_json = [{"kind": "parse_incomplete", "unread_pages": [1]}]
    elif change == "template":
        source.file_name = "变更协议模板.pdf"
    elif change == "template_heading":
        source.pages_json[0]["text"] += "\n监理书面意见：按要求填写\n填写说明：补全样表"
    else:
        source.parse_state = "pending"
    assert changes.extract_change_records([source]) == []


def test_same_page_copy_titles_and_versions_do_not_lend_fields():
    first, second = changes.extract_change_records([material(document("分包合同变更协议（副本）", suffix="变更内容：范围调整") +
        "\n" + document(new="V3"))])
    assert first["new_version"] == "V2" and second["new_version"] == "V3" and second["content"] is None


def test_explicit_old_version_conflict_cannot_borrow_approval():
    first = changes.extract_change_records([material(document())])
    approval = changes.extract_change_records([material(document("合同变更报审表").replace("变更前版本：V1", "变更前版本：V0"), 2)])
    assert changes.matching_approvals(first[0], approval, changes=first) == []


def test_incomplete_overlapping_change_is_retained_and_blocks_unique_claim():
    complete = changes.extract_change_records([material(document())])
    partial = changes.extract_change_records([material("合同变更协议\n合同编号：C-1\n变更内容：范围待核对", 2)])
    assert len(partial) == 1 and partial[0]["change_id"] is None
    approval = changes.extract_change_records([material(document("合同变更报审表"), 3)])
    assert changes.matching_approvals(complete[0], approval, changes=complete + partial) == []


@pytest.mark.parametrize("sentence", ["双方签订合同补充协议", "本次变更形成合同补充协议", "双方应签订合同补充协议"])
def test_agreement_predicate_sentence_does_not_hide_later_rejection(sentence):
    row, = changes.extract_change_records([material(document(suffix="变更内容：增加工程范围\n" + sentence +
        "\n监理书面意见：不同意；退回修改"))])
    assert sentence in row["content"] and row["supervisor_opinion"] == "不同意；退回修改"


def test_service_fact_history_report_and_subject_snapshot_gate(db_session):
    import json
    from app.plus.blockchain_supervision import material_facts, reporting, report_template_v14 as v14, rule_item_catalog, service
    from app.plus.blockchain_supervision.models import BsFinding, BsMaterialFact
    task = service.create_task(db_session, conversation_id=101, title="合成变更", scope_a1=False, scope_a3=True, created_by=1)
    subject = service.create_subject(db_session, task, name="甲建设有限公司", subject_type="professional")
    for title in ("分包合同变更协议", "合同变更报审表"):
        source = service.add_material(db_session, task, file_name=title + ".pdf", role="packet", subject_id=subject.id)
        source.pages_json, source.parse_state = [{"page": 1, "text": document(title, suffix="监理书面意见：不同意；退回修改")}], "done"
        db_session.commit()
        if title == "分包合同变更协议":
            db_session.add(BsMaterialFact(task_id=task.id, material_id=source.id, field_path="change.records",
                raw_value="historical", normalized_value="historical", page=1, quote="historical", quality="review_required",
                source_kind="embedded_text_layer", parse_batch_id="historical-v12", extractor_version="material-facts.v12", schema_version="1"))
            db_session.commit()
        for _ in range(2):
            material_facts.persist_material_facts(db_session, source)
            db_session.commit()
        row, = [row for row in material_facts.latest_material_facts(db_session, source.id) if row.field_path == "change.records"]
        assert row.extractor_version == material_facts.EXTRACTOR_VERSION and json.loads(row.normalized_value)[0]["new_version"] == "V2"
    assert db_session.query(BsMaterialFact).filter_by(parse_batch_id="historical-v12").one().quote == "historical"
    assert rule_item_catalog.item_group_catalog()[changes.ITEM_ID] == "signature_chain"
    for _ in range(2):
        rows = [row for row in service.run_signature_chain(db_session, task) if row.item_id == changes.ITEM_ID]
        assert len(rows) == 1 and rows[0].subject_id == subject.id and not reporting.report_gate_blocking(rows[0])
        assert rows[0].finding_semantics == "manual_prompt" and "不同意；退回修改" in rows[0].message
        assert len(rows[0].evidence_json) == 2
        assert db_session.query(BsFinding).filter_by(task_id=task.id, item_id=changes.ITEM_ID).count() == 1
    report = reporting.build_report(db_session, task)
    prompt, = [row for row in v14._manual_prompt_rows(report) if row["item_id"] == changes.ITEM_ID]
    assert len(prompt["locations"]) == 2 and "辅助提示" in prompt["label"]
    assert not any(row["item_id"] == changes.ITEM_ID for row in v14._raw_items(report))
    subject.type_confirmed = False
    db_session.commit()
    from app.plus.blockchain_supervision.knowledge_runs import ReferenceRunSuperseded
    with pytest.raises(ReferenceRunSuperseded):
        service.run_signature_chain(db_session, task)
    service.initialize_reference_review(db_session, task)
    db_session.commit()
    row, = [row for row in service.run_signature_chain(db_session, task) if row.item_id == changes.ITEM_ID]
    assert row.finding_semantics != "manual_prompt" and reporting.report_gate_blocking(row)
