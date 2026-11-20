# -*- coding: utf-8 -*-
"""特种作业证书类型矩阵形状锁。"""
from app.plus.blockchain_supervision.rules import load_special_operation_catalog


def test_catalog_shape_and_version():
    data = load_special_operation_catalog()
    assert data["schema_version"] == "bs-special-operation-catalog.v1"
    assert data["meta"]["version"] == "2026-11-18.3"
    assert "JTG-F90-2015#5.6.2" in data["meta"]["basis_ids"]
    assert set(data["work_types"]) == {
        "mobile_crane_driver", "signal_slinger", "welder",
        "work_at_height", "crane_installer",
    }


def test_mobile_crane_driver_only_q2():
    row = load_special_operation_catalog()["work_types"]["mobile_crane_driver"]
    assert row["accepted_cert_types"] == ["特种设备作业人员证Q2（限流动式起重机）"]
    assert "建筑起重机械司机（塔式/门式等）" in row["excluded_cert_types"]


def test_signal_slinger_dual_acceptance():
    row = load_special_operation_catalog()["work_types"]["signal_slinger"]
    assert set(row["accepted_cert_types"]) == {"起重指挥（Q1）", "建筑起重信号司索工"}
    assert "含义相同" in row["equivalence_note"]


def test_welder_dual_acceptance():
    row = load_special_operation_catalog()["work_types"]["welder"]
    assert set(row["accepted_cert_types"]) == {"熔化焊接与热切割作业", "建筑焊工"}
    assert row["preferred"] == "熔化焊接与热切割作业"


def test_work_at_height_matches_actual_type():
    row = load_special_operation_catalog()["work_types"]["work_at_height"]
    assert set(row["accepted_cert_types"]) == {"登高架设作业", "高处作业吊篮安装拆卸工"}
    assert row["match_rule"] == "by_actual_work_type"
