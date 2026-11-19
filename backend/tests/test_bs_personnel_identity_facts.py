"""人员事实与身份关联回归；均为合成材料，不调用真实Provider。"""
from datetime import date
import json
from types import SimpleNamespace

import pytest

from app.plus.blockchain_supervision import a2_check, personnel_evidence


@pytest.mark.parametrize("period,baseline,expected", [
    ("2026年9月、2026年12月", date(2026, 11, 18), "stale"),
    ("2026年9月至2026年12月", date(2026, 11, 18), "covered"),
    ("2026-09,2026-12", date(2026, 11, 18), "stale"),
    ("2026-09至2026-10、2026-12", date(2026, 11, 18), "covered"),
    ("2026-12至2026-09", date(2026, 11, 18), "unknown"),
    ("2026-00至2026-12", date(2026, 11, 18), "unknown"),
    ("2025-12", date(2026, 1, 18), "covered"),
    ("202502-202507", date(2025, 7, 18), "covered"),
])
def test_social_period_only_covers_explicit_months_or_ranges(period, baseline, expected):
    assert a2_check.social_period_state(period, baseline) == expected


ID_A, ID_B = "110101199001010011", "110101199001010022"


def material(text, *, name="主要管理人员名单.pdf", mid=7, subject=3):
    return SimpleNamespace(id=mid, task_id=1, subject_id=subject, file_name=name,
                           role="packet", parse_state="done", degradations_json=[],
                           pages_json=[{"page": 1, "text": text}])


def test_roster_preserves_two_same_name_rows_and_identity_column():
    text = ("主要管理人员名单\n| 姓名 | 职务 | 身份证号 | 职称 | 联系电话 |\n"
            f"| 张三 | 项目经理 | {ID_A} | 工程师 | 13800138001 |\n"
            f"| 张三 | 安全员 | {ID_B} | 助理工程师 | 13800138002 |")
    facts = personnel_evidence.extract_management_roster([material(text)])
    assert [(f["name"], f["post"], f["id_number"]) for f in facts] == [
        ("张三", "项目经理", ID_A), ("张三", "安全员", ID_B)]
    assert facts[0]["professional_title"] == "工程师"
    assert all(f["subject_id"] == 3 and f["page"] == 1 for f in facts)


def test_roster_does_not_borrow_identity_from_certificate_column_or_mask():
    text = ("主要管理人员名单\n| 姓名 | 职务 | 身份证号 | 证书编号 | 联系电话 |\n"
            f"| 张三 | 项目经理 | 110101********0011 | {ID_A} | 13800138001 |")
    fact, = personnel_evidence.extract_management_roster([material(text)])
    assert fact["id_number"] is None and fact["id_number_candidates"] == []
    assert "110101********0011" in fact["quote"]


def test_social_split_row_keeps_identity_and_both_source_rows():
    text = ("社会保险参保证明\n单位名称：甲建设有限公司\n"
            "| 序号 | 姓名 | 身份证号 | 个人编号 | 缴费起止时间 | 缴费状态 |\n"
            f"| 1 | 张三 | {ID_A} | 999 | 年/月 | 年/月 |\n"
            "| 2 | | | | 202609 | 202610 |")
    fact, = personnel_evidence.extract_person_relation_evidence([material(text, name="社保证明.pdf")])
    assert fact["id_number"] == ID_A
    assert fact["period"] == "202609-202610"
    assert "张三" in fact["quote"] and ID_A in fact["quote"] and "202610" in fact["quote"]


def test_two_proofs_on_same_page_do_not_borrow_identity():
    text = (f"劳动合同\n用人单位：甲建设有限公司\n姓名：张三\n身份证号：{ID_A}\n"
            "合同期限：2026年1月至2027年1月\n"
            "劳动合同\n用人单位：乙建设有限公司\n姓名：李四\n合同期限：2026年1月至2027年1月")
    first, second = personnel_evidence.extract_person_relation_evidence([material(text, name="劳动合同.pdf")])
    assert first["id_number"] == ID_A
    assert second["id_number"] is None
    assert second["employer"] == "乙建设有限公司"
    assert ID_A not in second["quote"]


def test_same_page_records_survive_persistence_replay_and_new_batch():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.database import Base
    from app.plus.blockchain_supervision.material_facts import latest_material_facts, persist_material_facts
    from app.plus.blockchain_supervision.models import BsMaterial, BsMaterialFact

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    text = ("主要管理人员名单\n| 姓名 | 职务 | 身份证号 | 联系电话 |\n"
            f"| 张三 | 项目经理 | {ID_A} | 13800138001 |\n"
            f"| 张三 | 安全员 | {ID_B} | 13800138002 |")
    with Session(engine) as db:
        source = BsMaterial(task_id=1, file_name="主要管理人员名单.pdf", role="packet",
                            path="synthetic.pdf", parse_state="done", revision_no=1,
                            pages_json=[{"page": 1, "text": text}])
        db.add(source)
        db.flush()
        first = persist_material_facts(db, source)
        db.commit()
        records, = [f for f in first if f.field_path == "personnel.records"]
        assert [r["id_number"] for r in json.loads(records.raw_value)] == [ID_A, ID_B]
        assert records.page == 1 and records.quality == "readable"
        assert ID_A in records.quote and ID_B in records.quote
        assert persist_material_facts(db, source) == []
        old_value = records.raw_value
        source.pages_json = [{"page": 1, "text": text.replace(ID_B, "110101********0022")}]
        persist_material_facts(db, source)
        db.commit()
        current, = [f for f in latest_material_facts(db, source.id) if f.field_path == "personnel.records"]
        assert [r["id_number"] for r in json.loads(current.raw_value)] == [ID_A, None]
        assert db.get(BsMaterialFact, records.id).raw_value == old_value
        assert current.parse_batch_id != records.parse_batch_id


def relation(kind, number=ID_A, *, subject=3, employer="甲建设有限公司"):
    return {"name": "张三", "subject_id": subject, "evidence_kind": kind,
            "id_number": number, "employer": employer, "period": "2026-10至2026-11"}


def evaluate(roster, relations):
    return a2_check.evaluate_person_relations(
        [r["name"] for r in roster], relations, expected_company="甲建设有限公司",
        baseline_date=date(2026, 11, 18), roster=roster)


def test_same_name_people_use_their_own_contract_social_and_post():
    roster = [{"name": "张三", "id_number": number, "subject_id": 3, "post": post}
              for number, post in [(ID_A, "项目经理"), (ID_B, "安全员")]]
    results = evaluate(roster, [relation(kind, number, employer=employer)
                       for number, employer in [(ID_A, "甲建设有限公司"), (ID_B, "乙建设有限公司")]
                       for kind in ("labor_contract", "social_record")])
    assert [r["status"] for r in results] == ["miss", "hit"]
    assert [r["post"] for r in results] == ["项目经理", "安全员"]
    assert len({r["person_key"] for r in results}) == 2
    assert all(r["identity_basis"] == "id_number" and ID_A not in r["person_key"] for r in results)


def test_wrong_identity_does_not_become_wrong_employer_violation():
    results = evaluate([{"name": "张三", "id_number": ID_A, "subject_id": 3}], [
        relation("labor_contract", ID_B, employer="乙建设有限公司"),
        relation("social_record", ID_B, employer="乙建设有限公司")])
    assert results[0]["status"] == "undecidable"
    assert "身份证号不一致" in results[0]["message"]
    assert "单位「乙建设有限公司」" not in results[0]["message"]


def test_no_roster_identity_cannot_merge_contract_and_social_with_different_ids():
    result, = evaluate([{"name": "张三", "subject_id": 3}], [
        relation("labor_contract", ID_A), relation("social_record", ID_B)])
    assert result["status"] == "undecidable"


def test_other_subject_cannot_supply_contract_or_social_even_with_same_id():
    result, = evaluate([{"name": "张三", "id_number": ID_A, "subject_id": 3}], [
        relation("labor_contract", subject=4), relation("social_record", subject=4)])
    assert result["status"] == "not_included"
    assert "缺劳动合同" in result["message"] and "缺社保" in result["message"]


def test_a2_consumes_identity_fields_and_keeps_person_evidence_groups():
    roster = material("主要管理人员名单\n| 姓名 | 职务 | 身份证号 | 联系电话 |\n"
                      f"| 张三 | 项目经理 | {ID_A} | 13800138001 |\n"
                      f"| 张三 | 安全员 | {ID_B} | 13800138002 |")
    proofs = []
    for mid, title, unit_label, period_label in [
        (8, "劳动合同", "用人单位", "合同期限"),
        (9, "社会保险缴费证明", "参保单位", "缴费期间"),
    ]:
        text = "\n".join(f"{title}\n姓名：张三\n身份证号：{number}\n"
                         f"{unit_label}：{unit}\n{period_label}：2026-10至2026-11"
                         for number, unit in [(ID_A, "甲建设有限公司"), (ID_B, "乙建设有限公司")])
        proofs.append(material(text, name=title + ".pdf", mid=mid))
    task = SimpleNamespace(id=1, baseline_date=date(2026, 11, 18))
    finding, = a2_check.run_person_consistency_check(task, [roster, *proofs], expected_company="甲建设有限公司")
    assert finding.status == "hit"
    assert "张三（安全员）" in finding.message
    assert "同名尚不能证明" not in finding.message
    refs = finding.evidence_json
    assert len({ref["person_key"] for ref in refs}) == 2
    assert {ref["post"] for ref in refs if ref["id_number"] == ID_A} == {"项目经理"}
    assert {ref["post"] for ref in refs if ref["id_number"] == ID_B} == {"安全员"}


def test_unknown_identity_cannot_lend_one_contract_to_two_same_name_people():
    roster = [{"name": "张三", "id_number": number, "subject_id": 3} for number in (ID_A, ID_B)]
    results = evaluate(roster, [relation("labor_contract", None), relation("social_record", None)])
    assert [r["status"] for r in results] == ["undecidable", "undecidable"]


@pytest.mark.parametrize("number", [ID_A, None])
def test_single_person_missing_identity_in_proofs_stays_candidate(number):
    result, = evaluate([{"name": "张三", "id_number": number, "subject_id": 3}], [
        relation("labor_contract", None), relation("social_record", None)])
    assert result["status"] == "undecidable"
    assert result["identity_basis"] == "name_company_candidate"


def test_no_identity_does_not_turn_other_company_clue_into_violation():
    result, = evaluate([{"name": "张三", "subject_id": 3}], [
        relation("labor_contract", None), relation("social_record", None, employer="乙建设有限公司")])
    assert result["status"] == "undecidable"


def test_start_contract_backfill_does_not_reextract_historical_personnel():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from app.database import Base
    from app.plus.blockchain_supervision import service
    from app.plus.blockchain_supervision.material_facts import latest_material_facts
    from app.plus.blockchain_supervision.models import BsMaterial, BsMaterialFact

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        source = BsMaterial(task_id=1, role="sub", file_name="合成合同.pdf", path="synthetic.pdf",
                            parse_state="done", pages_json=[{"page": 1, "text":
                            "建设工程施工专业分包合同\n分包人（全称）：甲建设有限公司\n"
                            f"主要管理人员名单\n姓名：张三\n身份证号：{ID_A}\n联系电话：13800138001"}])
        db.add(source)
        db.flush()
        old = BsMaterialFact(task_id=1, material_id=source.id, parse_batch_id="historical-v3",
                             field_path="person.name", raw_value="张三", normalized_value="张三",
                             page=1, quote="姓名：张三", quality="readable", source_kind="embedded_text_layer",
                             schema_version="material-facts.v1", extractor_version="material-facts.v3")
        db.add(old)
        db.commit()
        from app.plus.blockchain_supervision.material_facts import persist_contract_candidates
        candidates = persist_contract_candidates(db, source, {
            "available": True, "version": {"model_code": "synthetic"},
            "fields": {"subcontractor": {"value": "甲建设有限公司", "evidence": {
                "page": 1, "quote": "分包人（全称）：甲建设有限公司"}}}})
        db.commit()
        assert candidates and latest_material_facts(db, source.id)[0].parse_batch_id == "historical-v3"
        assert service.backfill_contract_facts(db, SimpleNamespace(id=1)) > 0
        db.commit()
        rows = latest_material_facts(db, source.id)
        assert {r.parse_batch_id for r in rows} == {"historical-v3"}
        assert {r.field_path for r in rows} >= {"person.name", "contract.party.subcontractor"}
        assert all(r.field_path != "personnel.records" for r in rows)
        assert db.get(BsMaterialFact, old.id).extractor_version == "material-facts.v3"
        assert service.backfill_contract_facts(db, SimpleNamespace(id=1)) == 0
        assert db.get(BsMaterialFact, candidates[0].id).parse_batch_id != "historical-v3"
        assert all(not row.field_path.endswith(".candidate") for row in latest_material_facts(db, source.id))
