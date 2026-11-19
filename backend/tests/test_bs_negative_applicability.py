# -*- coding: utf-8 -*-
"""负面清单判定契约测试：只有 通过 / 发现问题。"""
from app.plus.blockchain_supervision import negative_applicability as na
from app.plus.blockchain_supervision.rules import (
    load_negative_applicability, load_negative_list,
)


def test_all_21_rules_have_applicability():
    rules = load_negative_list()["rules"]
    apps = load_negative_applicability()["rules"]
    assert len(rules) == 21
    assert {row["id"] for row in rules} == set(apps)


def test_classify_bridge_foundation():
    profile = na.classify_scope(
        "桩基工程、基坑围护、地基处理",
        "G15公路嘉金段（G1503公路—S32公路）改扩建工程")
    assert profile["domains"] == ["bridge"]
    assert "foundation" in profile["structure_parts"]
    assert "superstructure" not in profile["structure_parts"]


def test_classify_bridge_superstructure():
    profile = na.classify_scope("预制构件安装工程", "嘉松公路越江新建工程")
    assert profile["domains"] == ["bridge"]
    assert "superstructure" in profile["structure_parts"]


def test_classify_tunnel_and_roadbed():
    tunnel = na.classify_scope("盾构法隧道工程")
    assert tunnel["domains"] == ["tunnel"]
    assert "tunnel" in tunnel["structure_parts"]

    road = na.classify_scope("路基、路面工程")
    assert set(road["domains"]) == {"roadbed", "pavement"}
    assert {"roadbed", "pavement"}.issubset(set(road["structure_parts"]))


def test_classify_traffic_safety_and_unknown():
    traffic = na.classify_scope("交通安全设施工程")
    assert traffic["domains"] == ["traffic_safety"]
    unknown = na.classify_scope("房建工程")
    assert unknown["known"] is False


def test_foundation_scope_passes_without_listing_items():
    result = na.evaluate_scope("桩基工程、基坑围护、地基处理", "G15公路改扩建工程")
    assert result["status"] == "pass"
    assert result["summary"] == "通过"
    assert result["problems"] == []


def test_precast_broad_passes_but_marks_scope_quality():
    result = na.evaluate_scope("预制构件安装工程", "嘉松公路越江新建工程")
    assert result["status"] == "pass"
    assert result["summary"] == "通过"
    assert result["scope_quality"] == "broad"


def test_precast_beam_hits_negative_list():
    result = na.evaluate_scope(
        "箱梁预制安装工程", "嘉松公路越江新建工程", params={"unit_weight_t": 220})
    assert result["status"] == "problem"
    assert result["summary"] == "发现 2 项问题"
    assert {row["rule_id"] for row in result["problems"]} == {"NL-B-08", "NL-B-09"}
    assert all(row["verdict"] == "违反" for row in result["problems"])


def test_precast_beam_without_weight_needs_confirmation():
    result = na.evaluate_scope("箱梁预制安装工程", "嘉松公路越江新建工程")
    verdicts = {row["rule_id"]: row["verdict"] for row in result["problems"]}
    assert verdicts == {"NL-B-08": "违反", "NL-B-09": "需确认"}


def test_shield_tunnel_hits_negative_list():
    result = na.evaluate_scope("盾构法隧道工程")
    assert result["status"] == "problem"
    assert result["problems"][0]["rule_id"] == "NL-T-02"
    assert result["problems"][0]["verdict"] == "违反"


def test_segment_precast_exception_needs_confirmation():
    result = na.evaluate_scope("盾构隧道管片预制")
    assert result["status"] == "problem"
    assert result["problems"][0]["rule_id"] == "NL-T-02"
    assert result["problems"][0]["verdict"] == "除外待确认"


def test_roadbed_passes_and_records_checked():
    result = na.evaluate_scope("路基、路面工程")
    assert result["status"] == "pass"
    assert result["summary"] == "通过"
    assert any(row["rule_id"] == "NL-R-01" for row in result["audit"]["checked"])


def test_bridge_substructure_passes():
    result = na.evaluate_scope("桥梁下部结构工程", "某高速公路")
    assert result["status"] == "pass"
    assert result["problems"] == []


def test_cast_in_place_without_method_passes_but_marks_scope_quality():
    result = na.evaluate_scope("现浇梁工程", "某高速公路")
    assert result["status"] == "pass"
    assert result["scope_quality"] == "broad"


def test_cast_in_place_with_method_and_height():
    hit = na.evaluate_scope(
        "落地式支撑架现浇梁工程", "某高速公路", params={"height_m": 25})
    assert hit["status"] == "problem"
    assert hit["problems"][0]["rule_id"] == "NL-B-03"
    assert hit["problems"][0]["verdict"] == "违反"

    below = na.evaluate_scope(
        "落地式支撑架现浇梁工程", "某高速公路", params={"height_m": 15})
    assert below["status"] == "pass"


def test_unknown_scope_passes_with_scope_missing():
    result = na.evaluate_scope("房建工程")
    assert result["status"] == "pass"
    assert result["summary"] == "通过"
    assert result["scope_missing"] is True
    assert result["problems"] == []


def test_traffic_safety_passes():
    result = na.evaluate_scope("交通安全设施工程")
    assert result["status"] == "pass"
    assert result["problems"] == []


def test_broad_bridge_passes_and_marks_scope_quality():
    result = na.evaluate_scope("桥梁工程", "某高速公路")
    assert result["status"] == "pass"
    assert result["scope_quality"] == "broad"


def test_broad_tunnel_passes_and_marks_scope_quality():
    result = na.evaluate_scope("隧道工程")
    assert result["status"] == "pass"
    assert result["scope_quality"] == "broad"


def test_structure_classification_locked():
    data = load_negative_applicability()["structure_classification"]
    assert data["version"] == "2026-11-18.4"
    rules = data["rules"]
    assert rules["pile_foundation"]["negative_list_status"] == "not_listed_in_shanghai"
    assert rules["pile_foundation"]["cross_province_check"] is True
    assert rules["substructure"]["negative_list_status"] == "not_listed"
    assert rules["precast_components"]["part"] == "split_by_component"
    assert rules["bridge_deck_ancillary"]["part"] == "ancillary"
    assert "西藏" in data["province_notes"]["other_provinces"]
