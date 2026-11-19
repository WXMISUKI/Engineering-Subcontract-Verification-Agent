# -*- coding: utf-8 -*-
"""DG/TJ 08-2225 人员配置判定契约测试。"""
from app.plus.blockchain_supervision import personnel_adequacy as pa
from app.plus.blockchain_supervision.rules import load_personnel_adequacy
import pytest

SMALL = {
    "project_manager": 1, "technical_lead": 1, "construction_officer": 1,
    "quality_officer": 1, "safety_officer": 1, "standard_officer": 1,
    "material_officer": 1, "machinery_officer": 1, "labor_officer": 1,
    "document_officer": 1,
}
MEDIUM = {
    "project_manager": 1, "technical_lead": 1, "construction_officer": 2,
    "quality_officer": 2, "safety_officer": 2, "standard_officer": 1,
    "material_officer": 1, "machinery_officer": 1, "labor_officer": 1,
    "document_officer": 1,
}
LARGE = {
    "project_manager": 1, "technical_lead": 1, "construction_officer": 3,
    "quality_officer": 3, "safety_officer": 3, "standard_officer": 1,
    "material_officer": 2, "machinery_officer": 2, "labor_officer": 2,
    "document_officer": 2,
}


def _roster(required, *, overrides=None):
    overrides = overrides or {}
    rows, index = [], 0
    for post, count in required.items():
        count = overrides.get(post, count)
        for _ in range(int(count)):
            index += 1
            rows.append({"name": "人员%d" % index, "post": post})
    return rows


def test_data_shape_and_source():
    data = load_personnel_adequacy()
    assert data["meta"]["standard"] == "DG/TJ 08-2225-2017"
    assert data["professional"]["by_scale"]["large"]["safety_officer"] == 3
    assert data["professional"]["part_time_allowed"]["medium"] == [
        "standard_officer", "labor_officer", "document_officer"]
    assert data["labor"]["bands"][2]["safety_ratio_permille"] == 5
    assert "SHJT-2026-8#第七条" in data["meta"]["basis_ids"]


def test_normalize_post_aliases():
    assert pa.normalize_post("项目经理") == "project_manager"
    assert pa.normalize_post("项目总工") == "technical_lead"
    assert pa.normalize_post("专职安全员") == "safety_officer"
    assert pa.normalize_post("质量负责人") == "quality_officer"
    assert pa.normalize_post("project_manager") == "project_manager"
    assert pa.normalize_post("财务负责人") is None


def test_determine_scale_any_condition_and_interpretation():
    assert pa.determine_scale("bridge", {"span_m": 60})["scale"] == "large"
    assert pa.determine_scale("bridge", {"span_m": 30, "bridge_length_m": 100,
                                         "contract_amount_yi": 0.1})["scale"] == "medium"
    assert pa.determine_scale("bridge", {"span_m": 10, "bridge_length_m": 20,
                                         "contract_amount_yi": 0.1})["scale"] == "small"
    assert pa.determine_scale("highway_first_class", {"roadbed_km": 12})["scale"] == "large"
    assert pa.determine_scale("highway_first_class", {"roadbed_km": 6, "contract_amount_yi": 1})["scale"] == "large"
    assert pa.determine_scale("highway_expressway")["scale"] == "large"
    secondary = pa.determine_scale("highway_secondary")
    assert secondary["scale"] is None
    assert pa.determine_scale("tunnel")["scale"] == "large"
    assert pa.determine_scale("bridge", {})["scale"] is None
    assert pa.determine_scale("unknown")["scale"] is None


@pytest.mark.parametrize("category,params,expected", [
    ("highway_first_class", {"roadbed_km": 5}, "large"),
    ("highway_first_class", {"pavement_area_wan_m2": 15}, "large"),
    ("highway_first_class", {"roadbed_km": 4.9, "pavement_area_wan_m2": 14.9,
                              "contract_amount_yi": 0.1}, "medium"),
    ("highway_secondary", {"roadbed_km": 10}, "large"),
    ("highway_secondary", {"roadbed_km": 6, "pavement_area_wan_m2": 20,
                           "contract_amount_yi": 0.1}, "medium"),
    ("highway_secondary", {"roadbed_km": 4.9, "pavement_area_wan_m2": 14.9,
                           "contract_amount_yi": 0.49}, "small"),
])
def test_original_appendix_a_rows(category, params, expected):
    result = pa.determine_scale(category, params)
    assert result["scale"] == expected and result["complete"] is True
    assert result["source_pages"] == [28]


def test_partial_amount_preserves_preliminary_scale_and_missing_dimensions():
    result = pa.determine_scale("bridge", {"contract_amount_yi": 0.1})
    assert result["scale"] is None and result["preliminary_scale"] == "small"
    assert result["complete"] is False
    assert set(result["missing_fields"]) == {"span_m", "bridge_length_m"}
    assert "按合同额初判" in result["reason"] and "待核" in result["reason"]
    evaluated = pa.evaluate_staffing(subcontract_type="professional", category="bridge",
        params={"contract_amount_yi": 0.1}, roster=_roster(SMALL))
    assert evaluated["verdict"] == "待核实"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, True])
def test_invalid_scale_value_does_not_prove_a_grade(value):
    result = pa.determine_scale("bridge", {"span_m": value})
    assert result["scale"] is None


def _staff_material(text, *, mid=1, role="sub", page=1):
    from types import SimpleNamespace
    return SimpleNamespace(id=mid, role=role, subject_id=3, task_id=1,
        file_name="分包合同.pdf", parse_state="done", degradations_json=[],
        pages_json=[{"page": page, "text": text}])


def _staff_roster():
    from types import SimpleNamespace
    return [{"name": "人员%d" % i, "post": post, "material_id": 2,
             "page": 1, "quote": post, "subject_id": 3}
            for i, post in enumerate(["项目经理", "项目总工", "质量负责人", "安全员"])]


@pytest.mark.parametrize("scope,expected", [
    ("一级公路路基工程，路基长度6km", "大型"),
    ("二级公路路基工程，路基长度12000米", "大型"),
    ("一级公路路面工程，路面面积150000平方米", "大型"),
    ("桥梁工程，" + "本次专业承包范围说明。" * 12 + "单跨60米，桥梁总长200米", "大型"),
])
def test_staffing_entry_uses_full_scope_dimensions_and_contract_page(monkeypatch, scope, expected):
    from types import SimpleNamespace
    from app.plus.blockchain_supervision import a2_check
    monkeypatch.setattr(a2_check, "extract_management_roster", lambda materials: _staff_roster())
    contract = _staff_material("专业分包合同\n工程内容：" + scope + "\n签约合同价（含税）：1000万元")
    roster = _staff_material("名单", mid=2, role="packet")
    row, = a2_check.run_personnel_staffing_check(SimpleNamespace(id=1), [contract, roster])
    assert expected in row.message and "PDF物理第" in row.source
    assert any(ref["material_id"] == 1 and ref["page"] == 1 and scope in ref["quote"]
               for ref in row.evidence_json)
    assert any(ref.get("staffing_scale") == "large" for ref in row.evidence_json)


def test_staffing_entry_partial_amount_does_not_claim_all_scale_conditions_known(monkeypatch):
    from types import SimpleNamespace
    from app.plus.blockchain_supervision import a2_check
    monkeypatch.setattr(a2_check, "extract_management_roster", lambda materials: _staff_roster())
    contract = _staff_material("专业分包合同\n工程内容：桥梁工程施工\n签约合同价（含税）：1000万元")
    row, = a2_check.run_personnel_staffing_check(SimpleNamespace(id=1),
        [contract, _staff_material("名单", mid=2, role="packet")])
    assert row.status == "undecidable" and "按合同额初判" in row.message
    assert "单跨" in row.message and "桥梁总长" in row.message


@pytest.mark.parametrize("scope,expected,required", [
    ("一级公路，本次仅承担路基工程，路基长度3km", "medium", {"roadbed_km", "contract_amount_yi"}),
    ("二级公路，本次仅承担路面工程，路面面积10万平方米", "small", {"pavement_area_wan_m2", "contract_amount_yi"}),
    ("一级公路路基工程，路基长度3km", None, {"roadbed_km", "pavement_area_wan_m2", "contract_amount_yi"}),
    ("一级公路，本次仅承担路基工程及路面工程，路基长度3km", None,
     {"roadbed_km", "pavement_area_wan_m2", "contract_amount_yi"}),
])
def test_explicit_single_road_scope_uses_only_applicable_dimensions(monkeypatch, scope, expected, required):
    from types import SimpleNamespace
    from app.plus.blockchain_supervision import a2_check
    monkeypatch.setattr(a2_check, "extract_management_roster", lambda materials: _staff_roster())
    contract = _staff_material("专业分包合同\n工程内容：" + scope + "\n签约合同价（含税）：1000万元")
    row, = a2_check.run_personnel_staffing_check(SimpleNamespace(id=1),
        [contract, _staff_material("名单", mid=2, role="packet")])
    ref = next(ref for ref in row.evidence_json if ref.get("evidence_kind") == "subcontract_scope")
    assert ref["staffing_scale"] == expected
    assert set(ref["staffing_scale_required_fields"]) == required
    if expected is None:
        assert row.status == "undecidable" and "路面面积" in row.message
    else:
        assert ref["staffing_scale_complete"] is True


@pytest.mark.parametrize("scope", ["桥梁工程，单跨10米，单跨60米", "桥梁及隧道工程施工"])
def test_staffing_conflicting_scale_or_category_keeps_candidates(monkeypatch, scope):
    from types import SimpleNamespace
    from app.plus.blockchain_supervision import a2_check
    monkeypatch.setattr(a2_check, "extract_management_roster", lambda materials: _staff_roster())
    contract = _staff_material("专业分包合同\n工程内容：" + scope + "\n签约合同价（含税）：1000万元")
    row, = a2_check.run_personnel_staffing_check(SimpleNamespace(id=1),
        [contract, _staff_material("名单", mid=2, role="packet")])
    assert row.status == "undecidable" and "冲突" in row.message
    assert any(scope in ref.get("quote", "") for ref in row.evidence_json)


def test_missing_scale_does_not_hide_independent_legal_safety_shortfall():
    result = pa.evaluate_staffing(subcontract_type="professional", category="bridge",
        params={"contract_amount_yi": 0.6}, contract_amount_wan=6000, roster=_roster(SMALL))
    assert result["verdict"] == "不符合" and result["legal_safety_minimum"] == 2
    assert result["scale"] is None and "初判" in result["reason"]


@pytest.mark.parametrize("scope", ["桥梁工程，单跨不超过60米", "桥梁工程，单跨约60米",
                                   "桥梁工程，单跨60米，不包括本次分包"])
def test_nonexact_or_negated_scale_is_not_a_confirmed_grade(monkeypatch, scope):
    from types import SimpleNamespace
    from app.plus.blockchain_supervision import a2_check
    monkeypatch.setattr(a2_check, "extract_management_roster", lambda materials: _staff_roster())
    contract = _staff_material("专业分包合同\n工程内容：" + scope + "\n签约合同价（含税）：1000万元")
    row, = a2_check.run_personnel_staffing_check(SimpleNamespace(id=1),
        [contract, _staff_material("名单", mid=2, role="packet")])
    assert row.status == "undecidable"
    assert not any(ref.get("staffing_scale") for ref in row.evidence_json)


def test_actual_roster_and_contract_scale_survive_service_rerun_and_report(db_session):
    from app.plus.blockchain_supervision import service, reporting, report_template_v14
    task = service.create_task(db_session, conversation_id=101, title="合成规模链路",
                               scope_a1=False, scope_a3=True, created_by=1)
    subject = service.create_subject(db_session, task, name="甲建设有限公司", subject_type="professional")
    contract = service.add_material(db_session, task, file_name="分包合同.pdf", role="sub", subject_id=subject.id)
    contract.pages_json = [{"page": 1, "text": "专业分包合同\n工程内容：一级公路路基工程，路基长度6km\n签约合同价（含税）：1000万元"}]
    contract.parse_state = "done"
    roster = service.add_material(db_session, task, file_name="主要管理人员名单.pdf", role="packet", subject_id=subject.id)
    roster.pages_json = [{"page": 1, "text": "主要管理人员名单\n| 姓名 | 职务 | 联系电话 |\n"
        "| 张三 | 项目经理 | 13800000001 |\n| 李四 | 项目总工 | 13800000002 |\n"
        "| 王五 | 质量负责人 | 13800000003 |\n| 赵六 | 安全员 | 13800000004 |"}]
    roster.parse_state = "done"
    db_session.commit()
    for _ in range(2):
        rows = [row for row in service.run_personnel_staffing(db_session, task) if row.item_id == "A2-07"]
        assert len(rows) == 1 and "大型" in rows[0].message
        assert any(ref.get("staffing_scale") == "large" for ref in rows[0].evidence_json)
    report = reporting.build_report(db_session, task)
    raw = [row for row in report_template_v14._raw_items(report) if row["item_id"] == "A2-07"]
    assert len(raw) == 1 and "大型" in raw[0]["message"]


def test_legal_safety_minimum():
    assert pa.legal_safety_minimum(None) is None
    assert pa.legal_safety_minimum(455.76) == 1
    assert pa.legal_safety_minimum(6171.0) == 2
    assert pa.legal_safety_minimum(20000) == 5
    # 档位边界：不足 5000 万 1 人；5000 万~2 亿每 5000 万 1 人；2 亿以上 5 人
    assert pa.legal_safety_minimum(4999) == 1
    assert pa.legal_safety_minimum(5000) == 1
    assert pa.legal_safety_minimum(12000) == 3
    assert pa.legal_safety_minimum(19999) == 4


def test_professional_small_complete():
    result = pa.evaluate_staffing(
        subcontract_type="professional", scale="small", roster=_roster(SMALL))
    assert result["verdict"] == "符合"
    assert result["missing_key_posts"] == []
    assert result["advisories"] == []


def test_professional_medium_missing_quality_is_missing_item():
    roster = _roster(MEDIUM, overrides={"quality_officer": 0})
    result = pa.evaluate_staffing(
        subcontract_type="professional", scale="medium", roster=roster)
    assert result["verdict"] == "缺项"
    assert "quality_officer" in result["missing_key_posts"]


def test_professional_large_shortfall_is_advisory():
    roster = _roster(LARGE, overrides={"safety_officer": 2})
    result = pa.evaluate_staffing(
        subcontract_type="professional", scale="large", roster=roster)
    assert result["verdict"] == "符合"
    assert result["shortfalls"]["safety_officer"] == {"required": 3, "actual": 2}
    assert any(row["post"] == "safety_officer" for row in result["advisories"])


def test_labor_band_shortfall_and_missing_key():
    roster = _roster({"project_manager": 1, "safety_officer": 1, "labor_officer": 1})
    short = pa.evaluate_staffing(
        subcontract_type="labor", roster=roster, worker_count=120)
    assert short["verdict"] == "符合"
    assert short["shortfalls"]["safety_officer"] == {"required": 2, "actual": 1}

    missing = pa.evaluate_staffing(
        subcontract_type="labor", roster=_roster({"project_manager": 1, "labor_officer": 1}),
        worker_count=120)
    assert missing["verdict"] == "缺项"
    assert "safety_officer" in missing["missing_key_posts"]


def test_labor_without_worker_count_is_undecidable():
    result = pa.evaluate_staffing(
        subcontract_type="labor", roster=_roster({"project_manager": 1, "safety_officer": 1}))
    assert result["verdict"] == "待核实"
    assert "缺作业人员人数" in result["reason"]


def test_legal_safety_overrides_table_minimum():
    result = pa.evaluate_staffing(
        subcontract_type="professional", scale="small", roster=_roster(SMALL),
        contract_amount_wan=6000)
    assert result["legal_safety_minimum"] == 2
    assert result["verdict"] == "不符合"
    assert result["severity"] == "阻断"


def test_empty_roster_is_missing_item():
    result = pa.evaluate_staffing(subcontract_type="professional", scale="small", roster=[])
    assert result["verdict"] == "缺项"


def test_machinery_exempt_without_special_equipment():
    roster = _roster({k: v for k, v in SMALL.items() if k != "machinery_officer"})
    result = pa.evaluate_staffing(
        subcontract_type="professional", scale="small", roster=roster,
        has_special_equipment=False)
    assert result["verdict"] == "符合"
    assert "machinery_officer" not in result["required"]


def test_part_time_declared_and_undeclared():
    base = _roster({k: v for k, v in SMALL.items()
                    if k not in {"standard_officer", "document_officer"}})
    declared = base + [{"name": "兼职人员", "posts": ["标准员", "资料员"], "part_time": True}]
    result = pa.evaluate_staffing(
        subcontract_type="professional", scale="small", roster=declared)
    assert result["verdict"] == "符合"
    assert result["advisories"] == []

    undeclared = base + [{"name": "兼职人员", "posts": ["标准员", "资料员"]}]
    result = pa.evaluate_staffing(
        subcontract_type="professional", scale="small", roster=undeclared)
    assert result["verdict"] == "符合"
    assert any("未见兼职声明" in row["message"] for row in result["advisories"])


def test_safety_officer_business_decisions_locked():
    decision = load_personnel_adequacy()["business_decisions"]["safety_officer"]
    assert decision["calculation_base"] == "subcontract_contract_amount"
    assert decision["scope"] == "subcontractor_separate_no_sharing"
    assert decision["conflict_rule"] == "max_of_legal_and_recommended"
    assert decision["by_specialty"] == "subcontract_specialty"
    assert decision["version"] == "2026-11-18.1"


def test_social_insurance_business_decisions_locked():
    decision = load_personnel_adequacy()["business_decisions"]["social_insurance"]
    assert decision["payment_unit_must_match"] == "subcontractor"
    assert decision["branch_office"] == "recognized_as_same_unit_with_proof"
    assert decision["subsidiary_parent_hr_agency_individual"] == "not_recognized"
    assert decision["retired_person"]["default"] == "not_recognized"
    # 与同份已确认notes一致：关键岗位不自动适用退休返聘例外。
    assert decision["retired_person"]["key_posts"] == "not_allowed"
    assert decision["labor_dispatch"] == "not_recognized_unless_contract_or_owner_explicit"
    assert decision["salary_flow"] == "supplementary_evidence"
    assert decision["period"] == "benchmark_month_or_previous_natural_month"
    assert decision["version"] == "2026-11-18.2"
