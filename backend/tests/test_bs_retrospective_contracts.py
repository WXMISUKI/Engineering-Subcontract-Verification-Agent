"""倒查结果语义回归：不通过人工状态来伪造自动报告。"""
import pytest
from pydantic import ValidationError

from app.plus.blockchain_supervision.retrospective_contracts import (
    COLUMNS, FindingCandidate, RunIdentity, execution_status, summary,
)


def candidate(**overrides):
    return {"kind": "internal_ambiguity", "time_scope": "contract_text",
            "message": "两处费用承担约定的适用边界存在歧义。",
            "evidence": [{"material_id": 1, "page": 2, "quote": "延长工期费用另行协商"},
                         {"material_id": 1, "page": 3, "quote": "延长工期费用均不调整"}],
            **overrides}


def test_internal_ambiguity_uses_contract_sources_without_fabricated_statute():
    value = FindingCandidate.model_validate(candidate())
    assert not value.basis and len(value.evidence) == 2
    assert len(COLUMNS) == 8


@pytest.mark.parametrize("overrides", [
    {"evidence": [{"material_id": 1, "page": 2, "quote": "仅有一侧实际约定"}]},
    {"kind": "text_nonconformity", "time_scope": "historical"},
    {"kind": "current_notice", "time_scope": "historical",
     "basis": [{"clause_id": "law#1", "quote": "某已核验的原文条款"}]},
    {"kind": "conditional_risk", "basis": [{"clause_id": "law#1", "quote": "某已核验的原文条款"}]},
    {"evidence": [{"material_id": True, "page": 2, "quote": "不能用布尔冒充材料"}]},
])
def test_invalid_or_misclassified_candidates_are_rejected(overrides):
    with pytest.raises(ValidationError):
        FindingCandidate.model_validate(candidate(**overrides))


def test_zero_findings_and_no_execution_are_different():
    done = execution_status(batches_total=3, batches_completed=3, limitations=[])
    assert done == "completed" and "未发现可列示问题" in summary(done, 0)
    failed = execution_status(batches_total=3, batches_completed=0, limitations=["模型失败"])
    assert failed == "failed" and "未发现" not in summary(failed, 0)
    assert execution_status(batches_total=3, batches_completed=2, limitations=[]) == "partial"


def test_run_identity_serializes_with_frozen_model_and_dates():
    identity = RunIdentity(run_id="retro-1", task_id=1, model_code="model-a",
                           material_fingerprint="a" * 64, basis_fingerprint="b" * 64,
                           baseline_date="2026-11-19", contract_date="2023-12-22")
    saved = identity.model_dump(mode="json")
    assert RunIdentity.model_validate(saved) == identity
    assert saved["model_code"] == "model-a" and saved["contract_date"] != saved["baseline_date"]
