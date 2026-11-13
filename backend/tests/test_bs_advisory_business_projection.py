from copy import deepcopy

from app.plus.blockchain_supervision import ai_advisory_snapshots


def test_fragment_absence_does_not_become_business_conclusion_and_evidence_is_preserved():
    advisory = {
        "finding_id": 1,
        "proposals": [
            {"message": "可见报审表签章，但已提供的文本片段中未见发包人书面同意，请核对原件。",
             "material_evidence": [{"page": 1, "quote": "监理工程师签章"}],
             "rule_evidence": [{"quote": "发包人书面同意"}]},
            {"message": "请核对签章日期与报审日期。", "material_evidence": [{"page": 2, "quote": "签章日期"}]},
        ],
    }
    original = deepcopy(advisory)
    result = ai_advisory_snapshots.project_advisory_for_business(advisory)
    assert advisory == original
    assert "未见" not in result["proposals"][0]["message"]
    assert "完整报审材料" in result["proposals"][0]["message"]
    assert result["proposals"][0]["requires_full_scope_review"] is True
    assert result["proposals"][0]["material_evidence"] == original["proposals"][0]["material_evidence"]
    assert result["proposals"][0]["rule_evidence"] == original["proposals"][0]["rule_evidence"]
    assert result["proposals"][1] == original["proposals"][1]
