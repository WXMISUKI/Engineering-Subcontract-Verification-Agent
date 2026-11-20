from types import SimpleNamespace

from app.plus.blockchain_supervision import a2_check, special_operation_coverage as coverage


def test_explicit_mobile_crane_requires_driver_and_signal_only():
    result = coverage.required_work_types("汽车吊吊装预制梁，不含焊接")
    assert {row["work_type"] for row in result["required"]} == {"mobile_crane_driver", "signal_slinger"}
    assert result["gaps"] == []


def test_generic_lifting_and_height_do_not_guess_actual_equipment_or_operation():
    lifting = coverage.required_work_types("预制构件吊装")
    assert {row["work_type"] for row in lifting["required"]} == {"signal_slinger"}
    assert any("设备类型" in gap for gap in lifting["gaps"])
    assert coverage.required_work_types("高处作业")["gaps"]
    assert coverage.required_work_types("路面施工")["gaps"]


def person(post, certificate_type=None):
    return {"name": "张三", "post": post, "certificate_type": certificate_type,
            "material_id": 1, "page": 2, "quote": "合成名单行"}


def test_crane_wrong_type_is_pending_missing_trade_is_missing_not_violation():
    rows = [person("汽车吊司机", "建筑起重机械司机（塔式）"), person("信号工", "起重指挥Q1")]
    result = coverage.evaluate_coverage("汽车吊吊装", rows)
    assert result["status"] == "undecidable"
    assert [row["status"] for row in result["details"]] == ["undecidable", "miss"]
    assert coverage.evaluate_coverage("汽车吊吊装", rows[:1])["status"] == "not_included"
    assert coverage.evaluate_coverage("汽车吊吊装", [], reading_complete=False)["status"] == "undecidable"


def test_q2_requires_mobile_scope_and_q12_is_not_q1():
    rows = [person("汽车吊司机", "特种设备Q2（流动式起重机）"), person("信号工", "Q1")]
    assert coverage.evaluate_coverage("履带吊吊装", rows)["status"] == "miss"
    rows[0]["certificate_type"] = "Q2"
    assert coverage.evaluate_coverage("履带吊吊装", rows)["status"] == "undecidable"
    rows[0]["certificate_type"] = "Q2流动式起重机"
    rows[1]["certificate_type"] = "Q12"
    assert coverage.evaluate_coverage("履带吊吊装", rows)["status"] == "undecidable"


def test_welder_dual_acceptance_and_height_matches_actual_type():
    for cert in ("熔化焊接与热切割作业", "建筑焊工"):
        result = coverage.evaluate_coverage("钢筋焊接", [person("焊工", cert)])
        assert result["status"] == "miss" and "人数仅记录" in result["quantity_notice"]
    assert coverage.evaluate_coverage("脚手架搭设", [person("架子工", "高处作业吊篮安装拆卸工")])["status"] == "undecidable"
    assert coverage.evaluate_coverage("脚手架搭设", [person("架子工", "登高架设作业")])["status"] == "miss"


def test_mixed_certificate_types_cannot_borrow_another_persons_match():
    rows = [dict(person("焊工", cert), name=name) for name, cert in (
        ("张三", "熔化焊接与热切割作业"), ("李四", "塔式起重机司机"), ("王五", None))]
    result = coverage.evaluate_coverage("钢筋焊接", rows)
    detail, = result["details"]
    assert result["status"] == detail["status"] == "undecidable"
    assert [row["name"] for row in detail["accepted_people"]] == ["张三"]
    assert [row["name"] for row in detail["invalid_people"]] == ["李四"]
    assert [row["name"] for row in detail["missing_type_people"]] == ["王五"]
    assert "1条类型缺失、1条类型不匹配" in detail["message"]
    assert coverage.evaluate_coverage("钢筋焊接", rows[:2])["status"] == "undecidable"
    assert coverage.evaluate_coverage("钢筋焊接", [rows[0], rows[2]])["status"] == "undecidable"
    rows[1]["certificate_type"] = rows[2]["certificate_type"] = "建筑焊工"
    assert coverage.evaluate_coverage("钢筋焊接", rows)["status"] == "miss"


def material(mid, role, text):
    return SimpleNamespace(id=mid, task_id=1, subject_id=3, role=role, file_name="合成.pdf",
                           parse_state="done", degradations_json=[], pages_json=[{"page": 1, "text": text}])


def test_kernel_reads_subcontract_scope_and_roster_and_ignores_general_scope():
    contract = material(1, "sub", "专业分包合同\n分包工程承包范围和内容：汽车吊吊装预制梁")
    roster = material(2, "packet", "特种作业人员名单\n| 序号 | 姓名 | 工种 | 证书类型 |\n"
                      "| 1 | 张三 | 汽车吊司机 | Q2流动式起重机 |\n"
                      "| 2 | 李四 | 信号工 | 起重指挥Q1 |")
    task = SimpleNamespace(id=1)
    finding, = a2_check.run_special_operation_coverage_check(task, [contract, roster])
    assert finding.status == "miss" and finding.item_id == "A2-08"
    assert {ref["material_id"] for ref in finding.evidence_json} == {1, 2}
    assert "归属、有效期和真实性另行核对" in finding.message
    contract.role = "general"
    finding, = a2_check.run_special_operation_coverage_check(task, [contract, roster])
    assert finding.status == "undecidable"


def test_staffing_missing_verdict_stays_not_included(monkeypatch):
    from app.plus.blockchain_supervision import personnel_adequacy

    monkeypatch.setattr(personnel_adequacy, "evaluate_staffing", lambda **kwargs: {
        "verdict": "缺项", "details": [{"verdict": "缺项", "message": "关键岗位未配"}]})
    finding, = a2_check.run_personnel_staffing_check(SimpleNamespace(id=1), [material(1, "sub", "专业分包合同")])
    assert finding.status == "not_included"


def test_new_and_existing_staffing_items_are_registered_for_rerun():
    from app.plus.blockchain_supervision.rule_item_catalog import item_group_catalog, plan_rerun

    assert {key: value for key, value in item_group_catalog().items() if key in {"A2-07", "A2-08"}} == {
        "A2-07": "a2", "A2-08": "a2"}
    plan = plan_rerun(expected_groups=("a2",), scope="items", item_ids=["A2-08"], changed_item_ids=[])
    assert plan["scope"] == "items" and plan["item_ids"] == ["A2-08"]
