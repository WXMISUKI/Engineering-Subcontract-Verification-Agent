from types import SimpleNamespace

import pytest

from app.plus.blockchain_supervision import equipment_identity as equipment


def material(text, mid=1, subject=3):
    return SimpleNamespace(id=mid, task_id=1, subject_id=subject, file_name="设备附件.pdf", role="packet",
                           parse_state="done", degradations_json=[], pages_json=[{"page": 1, "text": text}])


def inventory():
    return material("设备清单\n| 设备名称 | 设备编号 | 出厂编号 |\n| 汽车吊 | E1 | F1 |\n| 汽车吊 | E2 | F2 |")


@pytest.mark.parametrize("name_label", ["设备名称", "机械名称"])
def test_equipment_table_header_aliases_and_markdown_separator(name_label):
    rows = equipment.extract_equipment_records([material(
        f"机械设备清单\n| {name_label} | 出厂编号 |\n| :--- | ---: |\n| 塔吊 | F-1 |")])
    assert len(rows) == 1
    assert rows[0]["name"] == "塔吊" and rows[0]["factory_id"] == "F-1"


def proof(number="F1", label="出厂编号", title="设备检验报告"):
    return f"{title}\n设备名称：汽车吊\n{label}：{number}\n检验日期：2026-01-01\n下次检验日期：2027-01-01\n检验结论：合格"


def test_same_page_equipment_records_do_not_borrow_fields():
    rows = equipment.extract_equipment_records([material(proof() + "\n设备检验报告\n设备名称：履带吊\n出厂编号：F2")])
    assert len(rows) == 2 and rows[0]["factory_id"] == "F1" and rows[0]["next_inspection"] == "2027-01-01"
    assert rows[1]["factory_id"] == "F2" and rows[1]["next_inspection"] is None
    assert "F1" not in rows[1]["quote"]


@pytest.mark.parametrize("source,expected", [(material(proof(), mid=2), 1),
    (material(proof("E1"), mid=2), 0),  # 内部编号E1不能与出厂编号E1混比
    (material(proof(), mid=2, subject=4), 0),
    (material(proof() + "\n设备编号：E9", mid=2), 0)])
def test_only_same_subject_and_same_number_namespace_correspond(source, expected):
    first, second = equipment.extract_equipment_records([inventory()])
    proofs = equipment.extract_equipment_records([source])
    assert len(equipment.matching_proofs(first, proofs)) == expected
    assert equipment.matching_proofs(second, proofs) == []


def test_same_name_without_identifier_and_indirect_inventory_claim_do_not_supply_proof():
    first, = equipment.extract_equipment_records([material("设备清单\n设备名称：汽车吊\n检验结论：合格")])
    assert equipment.matching_proofs(first, equipment.extract_equipment_records([material(proof(), mid=2)])) == []
    assert first["evidence_kind"] == "equipment_inventory"


@pytest.mark.parametrize("text", [proof() + "\n" + "说明" * 160 + "模板", "合同约定应提供设备检验报告\n" + proof()])
def test_templates_and_obligations_do_not_become_equipment_proof(text):
    assert equipment.extract_equipment_records([material(text)]) == []


def test_multiple_devices_in_one_report_and_next_certificate_are_isolated():
    rows = equipment.extract_equipment_records([material("设备检验报告\n设备名称：汽车吊\n出厂编号：F1\n"
        "设备名称：汽车吊\n出厂编号：F2\n下次检验日期：2028-01-01\n营业执照（副本）\n产权单位：错误公司")])
    assert len(rows) == 2 and rows[0]["next_inspection"] is None
    assert rows[1]["next_inspection"] == "2028-01-01" and rows[1]["owner"] is None


def test_duplicate_inventory_identifier_and_unreadable_report_do_not_lend_proof():
    first, second = equipment.extract_equipment_records([inventory()])
    second["factory_id"] = "F1"
    source = material(proof(), mid=2)
    proofs = equipment.extract_equipment_records([source])
    assert equipment.matching_proofs(first, proofs, inventories=[first, second]) == []
    source.degradations_json = [{"kind": "parse_incomplete", "unread_pages": [1]}]
    assert equipment.extract_equipment_records([source]) == []


def test_service_fact_batches_report_and_number_namespaces(db_session):
    import json
    from app.plus.blockchain_supervision import material_facts, reporting, report_template_v14 as v14, rule_item_catalog, service
    from app.plus.blockchain_supervision.models import BsMaterialFact
    task = service.create_task(db_session, conversation_id=101, title="合成逐台设备", scope_a1=False, scope_a3=True, created_by=1)
    subject = service.create_subject(db_session, task, name="甲建设有限公司", subject_type="professional")
    for name, text in (("设备清单.pdf", inventory().pages_json[0]["text"]), ("设备检验.pdf", proof())):
        source = service.add_material(db_session, task, file_name=name, role="packet", subject_id=subject.id)
        source.pages_json, source.parse_state = [{"page": 1, "text": text}], "done"
        db_session.commit()
        for _ in range(2):
            material_facts.persist_material_facts(db_session, source)
            db_session.commit()
    facts = [row for row in material_facts.latest_material_facts(db_session, source.id) if row.field_path == "equipment.records"]
    assert len(facts) == 1 and json.loads(facts[0].normalized_value)[0]["factory_id"] == "F1"
    assert db_session.query(BsMaterialFact).filter_by(material_id=source.id, field_path="equipment.records").count() == 1
    assert rule_item_catalog.item_group_catalog()["A2-12"] == "a2"
    for _ in range(2):
        rows = [row for row in service.run_personnel_staffing(db_session, task) if row.item_id == "A2-12"]
        assert len(rows) == 2 and all(row.finding_semantics == "manual_prompt" and not reporting.report_gate_blocking(row) for row in rows)
        assert len(rows[0].evidence_json) == 2 and len(rows[1].evidence_json) == 1
    report = reporting.build_report(db_session, task)
    assert report["equipment_facts"]["records"] and report["equipment_facts"]["rows"][0]["material_id"]
    assert not any(row["item_id"] == "A2-12" for row in v14._raw_items(report))
    prompts = [row for row in v14._manual_prompt_rows(report) if row["item_id"] == "A2-12"]
    assert len(prompts) == 2 and prompts[0]["locations"]
    assert "下次检验2027-01-01" in prompts[0]["message"]
    assert "清单声明不能替代检验报告" in prompts[1]["message"]
