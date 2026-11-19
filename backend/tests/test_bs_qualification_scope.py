# -*- coding: utf-8 -*-
"""A2-04 第二段：资质等级与承包工程范围核对（设计 5.1–5.6）。

用例只构造纯数据结构，不建库、不跑 OCR：参数抽取、等级解析、判定三件事
分开断言，确保「参数缺失/等级模糊 → 待核实」「超限 → 不符合」不是靠文案猜出来的。
"""
from types import SimpleNamespace

import pytest

from app.plus.blockchain_supervision import qualification_scope
from app.plus.blockchain_supervision.models import (
    STATUS_HIT, STATUS_MISS, STATUS_UNDECIDABLE)


def _material(pages, material_id=7):
    return SimpleNamespace(id=material_id, pages_json=pages)


def _evaluate(*, scope_text, cert_text=None, pages=None, categories=("桩基工程",),
              cert_pages=None):
    pages = pages or [{"page": 1, "text": scope_text}]
    return qualification_scope.evaluate_scope(
        task_id=1, item_id="A2-04", source="test",
        scope_categories=[{"key": key} for key in categories],
        scope_text=scope_text,
        scope_material=_material(pages),
        certificate_pages=cert_pages or [
            {"material_id": 11, "page": 3, "text": cert_text}],
    )


def test_extract_parameters_uses_label_window():
    found = qualification_scope.extract_parameters(
        "基坑开挖深度16米；单桩承受设计荷载6000千牛；桥梁单跨160米、总长1200米")
    assert found["pit_depth_m"]["value"] == 16
    assert found["single_pile_load_kn"]["value"] == 6000
    assert found["single_span_m"]["value"] == 160
    assert found["total_length_m"]["value"] == 1200
    assert "基坑" in found["pit_depth_m"]["quote"]


def test_extract_parameters_does_not_guess_missing_values():
    assert qualification_scope.extract_parameters("桩基工程（钻孔灌注桩）") == {}


def test_parse_level_reads_category_window():
    data = qualification_scope.load_qualification_scope()
    category = data["categories"][0]
    parsed = qualification_scope.parse_level(
        [{"text": "地基基础工程专业承包二级"}], category, data)
    assert parsed["level"] == "二级"
    assert parsed["ambiguous"] is False


def test_parse_level_flags_new_old_mixing():
    data = qualification_scope.load_qualification_scope()
    category = data["categories"][0]
    parsed = qualification_scope.parse_level(
        [{"text": "地基基础工程专业承包二级"},
         {"text": "地基基础工程专业承包乙级"}], category, data)
    assert parsed["ambiguous"] is True
    assert set(parsed["levels"]) == {"二级", "乙级"}


def test_foundation_secondary_with_deep_pit_is_hit():
    findings = _evaluate(
        scope_text="基坑围护工程，基坑开挖深度16米",
        pages=[{"page": 2, "text": "基坑围护工程，基坑开挖深度16米"}],
        cert_text="地基基础工程专业承包二级")
    assert len(findings) == 1
    finding = findings[0]
    assert finding.status == STATUS_HIT
    assert "16" in finding.message and "15" in finding.message
    assert finding.page == 3
    assert any(item.get("page") == 2 for item in finding.evidence_json)


def test_foundation_secondary_within_limit_is_miss():
    findings = _evaluate(
        scope_text="基坑围护工程，基坑开挖深度9米",
        pages=[{"page": 2, "text": "基坑围护工程，基坑开挖深度9米"}],
        cert_text="地基基础工程专业承包二级")
    assert findings[0].status == STATUS_MISS
    assert "未超过" in findings[0].message


def test_missing_parameter_is_undecidable_not_guess():
    findings = _evaluate(scope_text="桩基工程（钻孔灌注桩）",
                        cert_text="地基基础工程专业承包二级")
    assert findings[0].status == STATUS_UNDECIDABLE
    assert "单桩承受设计荷载" in findings[0].message
    assert "未超过" not in findings[0].message


def test_ambiguous_level_is_undecidable_with_page():
    findings = _evaluate(
        scope_text="基坑围护工程，基坑开挖深度9米",
        pages=[{"page": 2, "text": "基坑围护工程，基坑开挖深度9米"}],
        cert_pages=[{"material_id": 11, "page": 3,
                     "text": "地基基础工程专业承包二级"},
                    {"material_id": 11, "page": 5,
                     "text": "地基基础工程专业承包乙级"}])
    assert findings[0].status == STATUS_UNDECIDABLE
    assert "乙级" in findings[0].message
    assert findings[0].page == 3


def test_reform_level_without_basis_is_undecidable():
    findings = _evaluate(scope_text="基坑围护工程，基坑开挖深度9米",
                        cert_text="地基基础工程专业承包乙级")
    assert findings[0].status == STATUS_UNDECIDABLE
    assert "待核实" in findings[0].message


def test_tunnel_missing_cross_section_is_undecidable():
    findings = _evaluate(scope_text="隧道工程，单洞长度800米",
                        pages=[{"page": 2, "text": "隧道工程，单洞长度800米"}],
                        cert_text="隧道工程专业承包二级",
                        categories=("隧道工程",))
    assert findings[0].status == STATUS_UNDECIDABLE
    assert "断面" in findings[0].message


@pytest.mark.parametrize("level,area,length,status", [
    ("二级", 60, 1000, STATUS_MISS),
    ("二级", 60.1, 900, STATUS_HIT),
    ("二级", 50, 1000.1, STATUS_HIT),
    ("三级", 40, 500, STATUS_MISS),
    ("三级", 40.1, 400, STATUS_HIT),
    ("三级", 30, 500.1, STATUS_HIT),
])
def test_tunnel_requires_both_original_scope_limits(level, area, length, status):
    findings = _evaluate(
        scope_text=f"隧道工程，断面面积{area}平方米，单洞长度{length}米",
        cert_text=f"隧道工程专业承包{level}", categories=("隧道工程",))
    assert findings[0].status == status
    assert "20.4" in findings[0].message


def test_tunnel_missing_length_is_undecidable():
    findings = _evaluate(scope_text="隧道工程，断面面积30平方米",
                        cert_text="隧道工程专业承包三级", categories=("隧道工程",))
    assert findings[0].status == STATUS_UNDECIDABLE
    assert "单洞长度" in findings[0].message


@pytest.mark.parametrize("height,status", [(70, STATUS_MISS), (70.1, STATUS_HIT)])
def test_foundation_third_grade_structure_height_limit(height, status):
    findings = _evaluate(scope_text=f"构筑物地基基础工程，构筑物高度{height}米",
                        cert_text="地基基础工程专业承包三级")
    assert findings[0].status == status


@pytest.mark.parametrize("category", ["路基工程", "路面工程"])
@pytest.mark.parametrize("level,road", [("二级", "一级公路"), ("三级", "二级公路")])
def test_road_grade_below_includes_the_stated_grade(category, level, road):
    findings = _evaluate(scope_text=road + category,
                        cert_text=f"公路{category}专业承包{level}", categories=(category,))
    assert findings[0].status == STATUS_MISS


def test_unverified_reform_has_no_invented_effective_date():
    data = qualification_scope.load_qualification_scope()
    pending = next(row for row in data["basis"] if row["basis_id"] == "JS-REFORM-2026")
    assert pending["effective_from"] is None
    assert data["reform_effective_from"] is None
    assert pending["verified"] is False


@pytest.mark.parametrize("pages", [
    [{"page": 2, "text": "隧道工程断面面积30平方米、单洞长度400米；另项断面面积80平方米。"}],
    [{"page": 2, "text": "隧道工程断面面积30平方米、单洞长度400米。"},
     {"page": 4, "text": "另项隧道工程断面面积80平方米。"}],
])
def test_conflicting_scale_values_do_not_select_the_first_smaller_one(pages):
    findings = _evaluate(scope_text="隧道工程", pages=pages,
                        cert_text="隧道工程专业承包二级", categories=("隧道工程",))
    assert findings[0].status == STATUS_UNDECIDABLE
    assert "多个" in findings[0].message
    assert {ref["page"] for ref in findings[0].evidence_json} >= {row["page"] for row in pages}


def test_conflicting_road_grades_do_not_choose_the_first_grade():
    findings = _evaluate(scope_text="公路路基工程",
                        pages=[{"page": 2, "text": "本次路基为二级公路；另一处为高速公路。"}],
                        cert_text="公路路基工程专业承包二级", categories=("路基工程",))
    assert findings[0].status == STATUS_UNDECIDABLE
    assert "多个" in findings[0].message


def test_repeated_identical_scale_values_remain_comparable():
    text = "隧道工程断面面积60平方米、单洞长度1000米。"
    findings = _evaluate(scope_text="隧道工程",
                        pages=[{"page": 2, "text": text}, {"page": 4, "text": text}],
                        cert_text="隧道工程专业承包二级", categories=("隧道工程",))
    assert findings[0].status == STATUS_MISS


def test_conflicting_road_grades_on_different_pages_are_preserved():
    findings = _evaluate(scope_text="路面工程",
                        pages=[{"page": 2, "text": "本项目二级公路路面工程。"},
                               {"page": 4, "text": "另项高速公路路面工程。"}],
                        cert_text="公路路面工程专业承包二级", categories=("路面工程",))
    assert findings[0].status == STATUS_UNDECIDABLE
    assert {ref["page"] for ref in findings[0].evidence_json} >= {2, 4}


def test_road_grade_over_limit_is_hit():
    findings = _evaluate(scope_text="高速公路路基工程",
                        pages=[{"page": 2, "text": "本项目为高速公路路基工程"}],
                        cert_text="公路路基工程专业承包二级",
                        categories=("路基工程",))
    assert findings[0].status == STATUS_HIT
    assert "高速公路" in findings[0].message


def test_road_grade_within_limit_is_miss():
    findings = _evaluate(scope_text="二级公路路面工程",
                        pages=[{"page": 2, "text": "本项目为二级公路路面工程"}],
                        cert_text="公路路面工程专业承包二级",
                        categories=("路面工程",))
    assert findings[0].status == STATUS_MISS


def test_bridge_secondary_span_over_limit_is_hit():
    findings = _evaluate(
        scope_text="桥梁工程，单跨160米，桥梁总长300米",
        pages=[{"page": 2, "text": "桥梁工程，单跨160米，桥梁总长300米"}],
        cert_text="桥梁工程专业承包二级",
        categories=("桥梁工程",))
    assert findings[0].status == STATUS_HIT
    assert "150" in findings[0].message


def test_unlimited_level_is_miss():
    findings = _evaluate(scope_text="桥梁工程", cert_text="桥梁工程专业承包一级",
                        categories=("桥梁工程",))
    assert findings[0].status == STATUS_MISS
    assert "不受规模限制" in findings[0].message


def test_labor_category_is_not_scope_checked():
    """劳务分包不分等级：第二段不介入，保留第一段「符合」口径（设计 5.5）。"""
    findings = _evaluate(scope_text="劳务分包", cert_text="施工劳务不分等级",
                        categories=("劳务分包",))
    assert findings == []


def test_category_not_on_certificate_is_left_to_first_segment():
    findings = _evaluate(scope_text="桥梁工程", cert_text="公路路基工程专业承包二级",
                        categories=("桥梁工程",))
    assert findings == []


def test_uncovered_category_stays_undecidable_instead_of_passing():
    """未录入承包范围依据的类别：类别匹配不算资质符合，必须输出待核实。"""
    findings = _evaluate(scope_text="钢结构工程", cert_text="钢结构工程专业承包二级",
                        categories=("钢结构工程",))
    assert len(findings) == 1
    assert findings[0].status == STATUS_UNDECIDABLE
    assert "承包工程范围依据尚未录入" in findings[0].message


def _point_item(*, status, source_kind, item_id="A2-04", kind="qualification_match"):
    return {
        "kind": kind, "item_id": item_id, "status": status, "source": "依据",
        "evidence_list": [{"material_id": 11, "page": 3, "quote": "证书/合同原文",
                           "source_kind": source_kind}],
    }


def test_report_point_verdict_follows_quantified_scope_result():
    from app.plus.blockchain_supervision import report_document

    assert report_document._point_verdict(
        [_point_item(status=STATUS_MISS, source_kind="qualification_certificate")],
        complete=True, reading_incomplete=False) == "符合"
    assert report_document._point_verdict(
        [_point_item(status=STATUS_HIT, source_kind="qualification_certificate")],
        complete=True, reading_incomplete=False) == "不符合"
    # 只有类别词表比对（无等级段证据）时仍保守待核实。
    assert report_document._point_verdict(
        [_point_item(status=STATUS_MISS, source_kind="certificate_text")],
        complete=True, reading_incomplete=False) == "待核实"
    assert report_document._point_verdict(
        [_point_item(status=STATUS_UNDECIDABLE, source_kind="qualification_certificate")],
        complete=True, reading_incomplete=False) == "待核实"
