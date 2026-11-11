from app.plus.blockchain_supervision.export import _contractor_summary_lines, _report_issues


def test_export_issue_selection_uses_summary_whitelist_but_keeps_machine_detail():
    report = {
        "qualification_summary": {
            "sections": {
                "non_compliant": [{"item_id": "A2-04", "subject": {"subject_id": 39}}],
                "missing": [], "undecidable": [],
            },
        },
        "sections": [
            {"kind": "qualification_match", "title": "资质", "items": [
                {"item_id": "A2-04", "subject": {"subject_id": 39}, "status": "hit",
                 "message": "资质不符", "evidence_list": [{"file_name": "a.pdf", "page": 2}]},
            ]},
            {"kind": "contract_semantic", "title": "合同文字", "items": [
                {"item_id": "CS-01", "subject": {"subject_id": 39}, "status": "hit",
                 "message": "普通标点差异"},
            ]},
        ],
    }
    issues = _report_issues(report)
    assert [item["item_id"] for item in issues] == ["A2-04"]
    assert issues[0]["evidence_list"][0]["page"] == 2


def test_export_contractor_summary_preserves_unresolved_state_and_evidence():
    report = {"qualification_summary": {"general_contractor": {
        "status": "unresolved", "label": "承包单位待核实", "candidate_company": "候选总包",
        "reason_code": "party_unresolved", "evidence": [{"file_name": "合同.pdf", "page": 3}],
    }}}
    lines = _contractor_summary_lines(report)
    assert lines == ["施工总承包单位：承包单位待核实（候选：候选总包）（原因：party_unresolved）；证据：合同.pdf 第3页"]
