"""报告一致性后校验（设计 2.6）：结论/分节/清单/附件冲突即阻断导出。"""
from app.plus.blockchain_supervision import report_template_v14 as v14


def _raw(*statuses):
    return [{"status": status, "material_missing": False, "label": "事项%d" % index}
            for index, status in enumerate(statuses)]


def _document(**overrides):
    document = {
        "conclusion": {"suggested": "同意",
                       "risk_counts": {"不符合": 0, "缺项": 0, "待核实": 0}},
        "pending_total": 0,
        "issues": [],
        "pending": [],
        "sections": [],
        "attachments": [],
        "appendix": {"auto_rows": []},
    }
    document.update(overrides)
    return document


def test_consistent_document_passes():
    assert v14.consistency_violations(_document(), _raw("miss", "miss")) == []


def test_conclusion_counts_must_match_item_verdicts():
    problems = v14.consistency_violations(_document(), _raw("hit"))
    assert any("结论计数" in problem for problem in problems)
    assert any("同意" in problem for problem in problems)


def test_pending_total_must_match_undecidable_count():
    problems = v14.consistency_violations(_document(), _raw("undecidable"))
    assert any("待核实合计" in problem for problem in problems)


def test_issue_table_must_list_every_non_conforming_verdict():
    document = _document(
        conclusion={"suggested": "原则同意，附条件通过",
                    "risk_counts": {"不符合": 1, "缺项": 0, "待核实": 0}})
    problems = v14.consistency_violations(document, _raw("hit"))
    assert any("问题表未列出" in problem for problem in problems)


def test_issue_rows_require_four_part_content():
    document = _document(
        conclusion={"suggested": "原则同意，附条件通过",
                    "risk_counts": {"不符合": 1, "缺项": 0, "待核实": 0}},
        issues=[{"number": 1, "category": "不符合", "fact": "事实", "reason": "理由",
                 "impact": "", "recommendation": "建议", "responsible_party": "分包人"}])
    problems = v14.consistency_violations(document, _raw("hit"))
    assert any("四段式" in problem for problem in problems)


def test_section_passed_must_not_carry_risks():
    document = _document(sections=[{"title": "① 要素完整性核查", "status": "通过",
                                    "counts": {"不符合": 1}}])
    problems = v14.consistency_violations(document, _raw("hit"))
    assert any("分节" in problem for problem in problems)


def test_appendix_one_must_match_appendix_two_state():
    document = _document(
        attachments=[{"auto_rows": [["分包范围合规性", "符合"]]}],
        appendix={"auto_rows": [["分包范围合规性", "不符合"]]})
    problems = v14.consistency_violations(document, _raw("miss"))
    assert any("附件一" in problem for problem in problems)
