from app.plus.blockchain_supervision.business_report import build_business_report


def _report():
    return {
        "meta": {
            "project_name": "XX 高速公路",
            "contract_section": "S1",
            "baseline_date": "2026-11-09",
            "source_manifest_hash": "manifest-sha",
            "basis_snapshot_id": "basis-1",
            "ruleset_version": "rules-1",
            "generated_at": "2026-11-09T10:00:00",
        },
        "contract_party_evidence": {"general_contractor": {"status": "matched", "company": "总包公司"}},
        "contract_facts": [{"role": "labor", "file_name": "劳务合同.pdf", "fields": {}}],
        "summary": {"headline": "存在待核实项"},
        "advisory_conclusion": {"text": "暂不予通过（供审批参考）"},
        "sections": [
            {"kind": "qualification_match", "items": [{"item_id": "A2-01", "status": "miss", "label": "资质匹配"}]},
            {"kind": "person_consistency", "items": [{"item_id": "A2-02", "status": "undecidable", "label": "人员一致性"}]},
        ],
        "materials": [{"material_id": 1, "file_name": "劳务合同.pdf", "role": "labor", "version_role": "current"}],
        "run_lineage": {"schema_version": "bs.run-lineage.v1"},
        "review_stage": "draft",
    }


def test_business_report_preserves_uncovered_modules_and_process_trace():
    result = build_business_report(_report())
    assert result["schema_version"] == "bs.business-report.v1"
    assert result["overall_status"] == "partial"
    modules = {row["code"]: row for row in result["sections"]}
    assert modules["qualification_credit"]["status"] == "available"
    assert modules["personnel"]["status"] == "available"
    assert modules["equipment"]["status"] == "not_covered"
    assert modules["equipment"]["conclusion"].startswith("本版本未覆盖")
    assert result["process_trace"]["source_manifest_hash"] == "manifest-sha"
    assert result["attachment_register"][1]["state"] == "provided"
