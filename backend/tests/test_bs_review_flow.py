"""Regression contract for admission flow entries, close groups and report sections."""
from importlib import import_module

from app.plus.blockchain_supervision import reference_snapshots, reporting, rules


EXPECTED_ENTRIES = {
    "completeness": ("a3_check.run_completeness_check",),
    "signature_chain": ("signature_chain.run_signature_chain_checks",),
    "qualification_match": ("a2_check.run_qualification_match_check",),
    "negative_list": ("a1_check.run_negative_list_check",),
    "transfer_scope": ("a1_check.run_transfer_scope_check",),
    "illegal_subcontract": ("a1_check.run_illegal_subcontract_check",),
    "person_consistency": ("a2_check.run_person_consistency_check",),
    "personnel_adequacy": (
        "a2_check.run_personnel_staffing_check",
        "a2_check.run_technical_title_check",
    ),
    "cert_validity": ("a2_check.run_cert_validity_check",),
    "internal_records": ("internal_records.run_internal_records_check",),
}


def test_admission_flow_entries_are_callable_and_match_runtime_contracts():
    flow = rules.load_review_flow()
    entries_by_kind = {}
    seen_step_ids = set()
    orders = []

    for step in flow["steps"]:
        assert step["id"] not in seen_step_ids
        seen_step_ids.add(step["id"])
        orders.append(step["order"])
        for kernel in step.get("kernels") or []:
            entries_by_kind.setdefault(kernel["kind"], []).append(kernel["entry"])
            module_name, function_name = kernel["entry"].rsplit(".", 1)
            module = import_module(f"app.plus.blockchain_supervision.{module_name}")
            assert callable(getattr(module, function_name, None)), kernel["entry"]

    assert orders == sorted(orders)
    assert {kind: tuple(entries) for kind, entries in entries_by_kind.items()} == EXPECTED_ENTRIES

    flow_kinds = set(entries_by_kind)
    close_group_kinds = {
        kind
        for group in reference_snapshots.CHECK_GROUPS.values()
        for kind in group["kinds"]
    }
    report_section_kinds = {kind for kind, _title in reporting._SECTIONS}

    assert set(rules.in_scope_kinds()) == flow_kinds
    assert flow_kinds <= close_group_kinds
    assert flow_kinds <= report_section_kinds
