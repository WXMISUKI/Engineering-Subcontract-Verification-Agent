import pytest

from app.plus.blockchain_supervision.qualification_summary import build_qualification_summary


def _report(contractor):
    return {
        "meta": {"task_id": 37, "state": "running"},
        "contract_party_evidence": {"general_contractor": contractor},
        "advisory_conclusion": {"code": "pending"},
        "limitations": ["OCR 部分页面尚未可靠读取"],
        "reading_coverage": [{"summary_text": "m4.pdf 第20页需人工核对"}],
        "sections": [
            {"kind": "qualification_match", "items": [
                {"item_id": "A2-04", "status": "hit", "label": "资质匹配",
                 "message": "资质类别不明确", "subject": {"subject_id": 39, "name": "乙方"},
                 "evidence": {"material_id": 54, "file_name": "m4.pdf", "page": 2,
                              "quote": "预制板梁", "location_mode": "page"}},
                {"item_id": "A2-04", "status": "miss", "label": "标点差异",
                 "message": "通过。", "subject": {"subject_id": 39, "name": "乙方"}},
            ]},
            {"kind": "completeness", "items": [
                {"item_id": "A1-01", "status": "undecidable", "material_missing": True,
                 "label": "总包合同", "message": "未提供总包合同"},
            ]},
            {"kind": "contract_semantic", "items": [
                {"item_id": "CS-01", "status": "hit", "label": "合同文字细节",
                 "message": "逗号不同"},
            ]},
        ],
    }


@pytest.mark.parametrize("status,expected_company,expected_label", [
    ("matched", "总包公司", "承包单位"),
    ("unresolved", None, "承包单位待核实"),
    ("conflict", None, "承包单位存在冲突，待核实"),
    ("missing", None, "承包单位未识别，待核实"),
])
def test_contractor_three_state_is_projected_without_guessing(status, expected_company, expected_label):
    row = {"status": status, "company": "总包公司", "candidate_company": "候选总包",
           "reason_code": "party_conflict", "evidence": [{"page": 1}]}
    summary = build_qualification_summary({"contract_party_evidence": {"general_contractor": row}})
    contractor = summary["general_contractor"]
    assert contractor["status"] == status
    assert contractor["company"] == expected_company
    assert contractor["label"] == expected_label


def test_summary_is_whitelisted_and_keeps_audit_reference():
    summary = build_qualification_summary(_report({"status": "unresolved", "candidate_company": "候选总包"}))
    assert summary["counts"] == {"hit": 1, "miss": 1, "undecidable": 1, "total": 3, "audit_items": 4}
    assert [item["item_id"] for item in summary["sections"]["non_compliant"]] == ["A2-04"]
    assert [item["item_id"] for item in summary["sections"]["missing"]] == ["A1-01"]
    assert summary["sections"]["undecidable"] == []
    assert summary["audit_ref"]["machine_sections"] == "sections"
    assert "m4.pdf 第20页需人工核对" in summary["limitations"]


def test_normal_text_noise_does_not_become_summary_issue_but_stays_in_audit_count():
    summary = build_qualification_summary(_report({"status": "missing"}))
    assert all(item["label"] != "合同文字细节" for item in summary["sections"]["non_compliant"])
    assert summary["counts"]["audit_items"] == 4
    assert summary["general_contractor"]["status"] == "missing"


def test_registered_punctuation_noise_inside_whitelist_is_filtered_and_auditable():
    report = {
        "sections": [{"kind": "qualification_match", "items": [{
            "id": 901, "item_id": "A2-NOISE", "status": "hit",
            "message": "仅逗号不同", "noise_kind": "punctuation",
            "subject": {"subject_id": 39, "name": "乙方"},
            "generation_id": "supplement-39-g-noise",
        }]}],
    }
    summary = build_qualification_summary(report)
    assert summary["counts"]["hit"] == 0
    assert summary["sections"]["non_compliant"] == []
    assert summary["audit_ref"]["noise_filtered"] == [{
        "item_id": "A2-NOISE", "finding_id": 901, "subject_id": 39,
        "generation_id": "supplement-39-g-noise", "reason": "punctuation",
    }]


def test_summary_item_audit_ref_keeps_finding_subject_and_generation_identity():
    report = {
        "sections": [{"kind": "qualification_match", "items": [{
            "id": 902, "item_id": "A2-04", "status": "hit",
            "message": "资质不匹配", "subject": {"subject_id": 39},
            "generation_id": "supplement-39-g1",
        }]}],
    }
    summary = build_qualification_summary(report)
    assert summary["sections"]["non_compliant"][0]["audit_ref"] == {
        "item_id": "A2-04", "finding_id": 902, "subject_id": 39,
        "generation_id": "supplement-39-g1",
    }
