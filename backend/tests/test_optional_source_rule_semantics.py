from types import SimpleNamespace

import pytest

from app.plus.blockchain_supervision import a1_check
from app.plus.blockchain_supervision.models import STATUS_UNDECIDABLE


def _task():
    return SimpleNamespace(id=1)


def _mat(mid, role, text="", *, parse_state="done", **attrs):
    pages = [{"page": 1, "text": text}] if text else []
    return SimpleNamespace(
        id=mid,
        task_id=1,
        file_name=f"{role}-{mid}.pdf",
        role=role,
        parse_state=parse_state,
        pages_json=pages,
        **attrs,
    )


def _finding_projection(findings, item_ids):
    return [
        (finding.item_id, finding.status, finding.message, finding.material_id,
         finding.page, finding.quote, finding.missing_json)
        for finding in findings if finding.item_id in item_ids
    ]


@pytest.mark.parametrize(
    "source_state",
    ["absent", "processing", "unreadable", "required_pages_unreadable", "unlinked", "conflicted"],
)
def test_optional_general_source_states_skip_only_dependent_transfer_rules(source_state):
    materials = [_mat(10, "sub", "分包范围：路基工程。")]

    scope = a1_check.run_transfer_scope_check(
        _task(), materials, scope_policy_enabled=True, general_source_state=source_state
    )
    transfer_materials = a1_check.run_transfer_materials_check(
        _task(), materials, scope_policy_enabled=True, general_source_state=source_state
    )
    relations = a1_check.run_transfer_relations_check(
        _task(), materials, scope_policy_enabled=True, general_source_state=source_state
    )

    assert scope == []
    assert "T15-03" not in {finding.item_id for finding in transfer_materials}
    assert "T15-04" in {finding.item_id for finding in transfer_materials}
    assert "T15-05" not in {finding.item_id for finding in relations}
    assert {"T15-06", "T15-07", "T15-08", "T15-09"} <= {
        finding.item_id for finding in relations
    }
    assert _finding_projection(transfer_materials, {"T15-04"}) == _finding_projection(
        a1_check.run_transfer_materials_check(_task(), materials), {"T15-04"}
    )
    assert _finding_projection(relations, {"T15-06", "T15-07", "T15-08", "T15-09"}) == (
        _finding_projection(a1_check.run_transfer_relations_check(_task(), materials),
                            {"T15-06", "T15-07", "T15-08", "T15-09"})
    )


@pytest.mark.parametrize(
    "source_state",
    ["absent", "processing", "unreadable", "required_pages_unreadable", "unlinked", "conflicted"],
)
def test_optional_general_source_states_skip_only_general_only_rules(source_state):
    materials = [
        _mat(1, "general", "总包合同约定：主体工程不得分包。"),
        _mat(2, "sub", "分包合同：分包范围：附属工程。"),
    ]

    baseline = a1_check.run_illegal_subcontract_check(_task(), materials)
    findings = a1_check.run_illegal_subcontract_check(
        _task(), materials, scope_policy_enabled=True, general_source_state=source_state
    )
    ids = {finding.item_id for finding in findings}

    assert "W16-03" not in ids
    assert "V16-02" not in ids
    assert "W16-01" in ids
    assert "W16-02" in ids
    unrelated = ids - {"W16-03", "V16-02"}
    assert _finding_projection(findings, unrelated) == _finding_projection(baseline, unrelated)


def test_readable_optional_general_source_keeps_general_dependent_rules():
    materials = [
        _mat(1, "general", "总包合同约定：主体工程不得分包。"),
        _mat(2, "sub", "分包合同：分包范围：附属工程。"),
    ]

    findings = a1_check.run_illegal_subcontract_check(
        _task(), materials, scope_policy_enabled=True, general_source_state="readable"
    )

    assert {"W16-03", "V16-02"} <= {finding.item_id for finding in findings}


def test_optional_amount_ratio_scope_does_not_hide_real_subcontract_gap():
    missing_general = a1_check.run_amount_ratio_check(
        _task(), [_mat(2, "sub", "分包合同：合同金额人民币100万元。")],
        scope_policy_enabled=True,
        general_source_state="absent",
    )
    assert missing_general == []

    readable_general_missing_sub = a1_check.run_amount_ratio_check(
        _task(), [_mat(1, "general", "总包合同金额为人民币1000万元。")],
        scope_policy_enabled=True,
        general_source_state="readable",
    )
    assert len(readable_general_missing_sub) == 1
    assert readable_general_missing_sub[0].status == STATUS_UNDECIDABLE
    assert "分包合同金额" in readable_general_missing_sub[0].message
    assert "总包合同" not in readable_general_missing_sub[0].message
    assert "general_contract" not in str(readable_general_missing_sub[0].missing_json)


def test_default_kernel_behavior_remains_compatible_without_scope_policy():
    materials = [_mat(2, "sub", "分包范围：路基工程。")]

    findings = a1_check.run_transfer_scope_check(_task(), materials)

    assert [finding.item_id for finding in findings] == ["T15-01", "T15-02"]
    assert all(finding.status == STATUS_UNDECIDABLE for finding in findings)
