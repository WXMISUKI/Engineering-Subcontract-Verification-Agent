from types import SimpleNamespace

from app.plus.blockchain_supervision.contract_party_evidence import (
    ROLE_EMPLOYER,
    ROLE_GENERAL_CONTRACTOR,
    ROLE_SUBCONTRACTOR,
    extract_contract_party_evidence,
    resolve_contract_party_evidence,
)


def _material(role, text, material_id=1, page=1):
    return SimpleNamespace(
        id=material_id,
        role=role,
        generation_id=None,
        pages_json=[{"page": page, "text": text}],
    )


def test_general_contract_extracts_employer_and_general_contractor():
    material = _material("general", "发包人：甲方公司；承包人：乙方公司。")
    resolved = resolve_contract_party_evidence([material])
    assert resolved[ROLE_EMPLOYER]["status"] == "matched"
    assert resolved[ROLE_EMPLOYER]["company"] == "甲方公司"
    assert resolved[ROLE_GENERAL_CONTRACTOR]["status"] == "matched"
    assert resolved[ROLE_GENERAL_CONTRACTOR]["company"] == "乙方公司"
    assert resolved[ROLE_GENERAL_CONTRACTOR]["evidence"][0]["page"] == 1


def test_subcontract_party_a_is_only_unresolved_contractor_candidate():
    material = _material("sub", "甲方：总包公司；乙方：分包公司。")
    resolved = resolve_contract_party_evidence([material])
    assert resolved[ROLE_SUBCONTRACTOR]["status"] == "matched"
    assert resolved[ROLE_SUBCONTRACTOR]["company"] == "分包公司"
    contractor = resolved[ROLE_GENERAL_CONTRACTOR]
    assert contractor["status"] == "unresolved"
    assert contractor["company"] is None
    assert contractor["candidate_company"] == "总包公司"
    assert contractor["reason_code"] == "contractor_candidate_requires_confirmation"


def test_conflicting_general_contractors_clear_company_and_keep_evidence():
    materials = [
        _material("general", "发包人：甲方公司；承包人：乙方公司。", 1),
        _material("general", "发包人：甲方公司；承包人：丙方公司。", 2),
    ]
    resolved = resolve_contract_party_evidence(materials)
    contractor = resolved[ROLE_GENERAL_CONTRACTOR]
    assert contractor["status"] == "conflict"
    assert contractor["company"] is None
    assert contractor["reason_code"] == "party_conflict"
    assert len(contractor["evidence"]) == 2


def test_role_labels_win_over_party_position_when_roles_are_swapped():
    material = _material("general", "承包人：总承包公司；发包人：建设单位公司。")
    resolved = resolve_contract_party_evidence([material])
    assert resolved[ROLE_GENERAL_CONTRACTOR]["company"] == "总承包公司"
    assert resolved[ROLE_EMPLOYER]["company"] == "建设单位公司"


def test_party_evidence_does_not_cross_contaminate_material_ids():
    first = _material("general", "发包人：甲方公司；承包人：乙方公司。", material_id=10)
    second = _material("general", "发包人：丙方公司；承包人：丁方公司。", material_id=20)
    resolved = resolve_contract_party_evidence([first, second])
    evidence = resolved[ROLE_GENERAL_CONTRACTOR]["evidence"]
    assert {item["material_id"] for item in evidence} == {10, 20}
    assert resolved[ROLE_GENERAL_CONTRACTOR]["status"] == "conflict"


def test_missing_general_contract_is_unresolved_with_reason():
    resolved = resolve_contract_party_evidence([_material("labor", "乙方：劳务公司。")])
    contractor = resolved[ROLE_GENERAL_CONTRACTOR]
    assert contractor["status"] == "unresolved"
    assert contractor["reason_code"] == "party_role_missing"
    assert resolved[ROLE_EMPLOYER]["reason_code"] == "party_role_missing"


def test_non_contract_material_produces_no_party_evidence():
    material = _material("packet", "发包人：不应读取为合同主体。")
    assert extract_contract_party_evidence(material) == []
