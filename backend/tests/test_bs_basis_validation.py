"""依据治理回归：文档角色、主体范围、条款错配与报告行映射。"""
from __future__ import annotations

import copy

import pytest

from app.plus.blockchain_supervision import basis_validation as bv
from app.plus.blockchain_supervision.report_template_v14 import (
    _SHEETS, _dimension_result,)


def _errors(issues):
    return [issue for issue in issues if issue["severity"] == "error"]


def test_registry_has_no_errors():
    assert _errors(bv.validate_registry()) == []


def test_basis_index_and_review_flow_are_bound():
    assert _errors(bv.validate_basis_index()) == []
    assert _errors(bv.validate_review_flow()) == []
    assert _errors(bv.validate_attachment_pairs()) == []


def test_review_flow_step3_registers_personnel_basis():
    flow = bv._load("review_flow.json")
    step = next(row for row in flow["steps"] if row["id"] == "s3")
    clause_ids = {ref["clause_id"] for ref in step["basis"]}
    assert "SHJT-2026-8#第七条" in clause_ids
    assert "SHJT-2026-8#第十条" in clause_ids
    assert "MOT-2024-2#第二十三条" in clause_ids


def test_permit_industry_basis_is_bound_and_preserves_old_online_gap():
    from app.plus.blockchain_supervision.rules import basis_lookup

    binding = bv.assert_binding("A2-09", subject="subcontractor", as_of="2026-11-19")
    assert binding["clause_id"] == "MOHURD-128-2015#第二条"
    view = basis_lookup(as_of="2026-11-19")["A2-09"]
    assert "本规定所称建筑施工企业" in view["text"]
    assert view["page_start"] is None
    assert view["human_verified"] is False
    assert "A2-09" not in basis_lookup(as_of="2015-01-21")
    old = next(row for row in bv.load_review_basis()["external_documents"]
               if row["basis_id"] == "SAFETY-PERMIT-397")
    assert old["quote"] is None
    assert old["quote_status"] == "pending_verification"


def test_permit_official_text_and_identity_are_frozen_in_review_seed():
    from datetime import date
    from types import SimpleNamespace
    from app.plus.blockchain_supervision.reference_snapshots import build_review_seed

    task = SimpleNamespace(baseline_date=date(2026, 11, 19), scope_a1=True,
                           scope_a3=True, profile_code="road", jurisdiction="shanghai")
    seed = build_review_seed(task, review_run_id="permit-regression",
                             material_fingerprint="materials", rule_fingerprint="rules",
                             package={"version": "test", "sources": []})
    rows = next(row for row in seed["steps"] if row["step_id"] == "s2")["fixed_basis"]
    permits = [row for row in rows if row["doc_id"] in {"SC-397-2014", "MOHURD-128-2015"}]
    assert len(permits) == 2
    for row in permits:
        assert row["available"] is True
        assert row["text"].startswith("第二条")
        assert row["human_verified"] is False
        assert row["page_start"] is None
        document = row["document"]
        assert document["verification"] == "official_local_text_reviewed"
        assert document["online_knowledge_verified"] is False
        assert document["knowledge_id"] is None
        assert document["source_url"].startswith("https://")
        assert len(document["source_text_sha256"]) == 64


def test_qualification_original_scope_is_frozen_with_physical_pages():
    from datetime import date
    from types import SimpleNamespace
    from app.plus.blockchain_supervision.reference_snapshots import build_review_seed

    task = SimpleNamespace(baseline_date=date(2026, 11, 19), scope_a1=True,
                           scope_a3=True, profile_code="road", jurisdiction="shanghai")
    seed = build_review_seed(task, review_run_id="qualification-regression",
                             material_fingerprint="materials", rule_fingerprint="rules",
                             package={"version": "test", "sources": []})
    rows = next(row for row in seed["steps"] if row["step_id"] == "s2")["fixed_basis"]
    originals = [row for row in rows if row.get("doc_id") == "JS-2014-159-ARCHIVE"]
    assert len(originals) == 6
    tunnel = next(row for row in originals if row["marker"] == "20.4")
    assert "断面60平方米以下且单洞长度1000米以下" in "".join(tunnel["text"].split())
    assert tunnel["page_start"] == tunnel["page_end"] == 111
    assert tunnel["document"]["online_knowledge_verified"] is False
    assert tunnel["human_verified"] is False
    assert tunnel["document"]["source_sha256"] == (
        "2d3dcf4484c604eb9e81a8308f32b81d19dea39318301380590b22d7a32ffc16")


def test_personnel_original_tables_are_frozen_without_claiming_online_repair():
    from datetime import date
    from types import SimpleNamespace
    from app.plus.blockchain_supervision.reference_snapshots import build_review_seed
    task = SimpleNamespace(baseline_date=date(2026, 11, 19), scope_a1=True,
                           scope_a3=True, profile_code="road", jurisdiction="shanghai")
    seed = build_review_seed(task, review_run_id="personnel-scale-regression",
                            material_fingerprint="materials", rule_fingerprint="rules",
                            package={"version": "test", "sources": []})
    rows = next(row for row in seed["steps"] if row["step_id"] == "s3")["fixed_basis"]
    originals = [row for row in rows if row.get("doc_id") == "DG-TJ08-2225-ARCHIVE"]
    assert len(originals) == 4
    title = next(row for row in originals if row['marker'] == '4.2.2')
    assert (title['page_start'], title['page_end']) == (15, 15)
    assert '技术负责人应取得工程技术类职称' in title['text']
    assert '除最大规模外' in title['text']
    assert title['human_verified'] is False
    assert title['document']['online_knowledge_verified'] is False
    binding = bv.assert_binding('A2-14', subject='subcontractor', as_of='2026-11-19')
    assert binding['clause_id'] == 'DG-TJ08-2225-ARCHIVE#4.2.2'
    appendix = next(row for row in originals if row["marker"] == "附录A")
    assert (appendix["page_start"], appendix["page_end"]) == (28, 32)
    assert "任一项达到" in appendix["text"]
    assert appendix["document"]["source_sha256"] == (
        "3ae2ff0dda06215ec32d44849f80fdc6fbd66200c10d8fa23eae84c7b3b6e905")
    assert appendix["human_verified"] is False
    assert appendix["document"]["online_knowledge_verified"] is False
    old = next(row for row in bv.load_review_basis()["external_documents"]
               if row["basis_id"] == "DG/TJ08-2225-2017")
    assert old["effective_from"] == "2017-06-01"
    assert old["quote"] is None and old["quote_status"] == "pending_verification"


def test_item_clause_mismatch_is_rejected():
    # 再分包必须引第十六条（六），不能沿用（五）“以劳务合作名义”。
    with pytest.raises(bv.BasisBindingError) as exc:
        bv.assert_binding("W16-06", "SHJT-2026-8#第十六条（五）")
    assert exc.value.code == "basis_item_clause_mismatch"


def test_safety_cost_original_contract_boundary_and_transition_are_frozen():
    from datetime import date
    from types import SimpleNamespace
    from app.plus.blockchain_supervision.reference_snapshots import build_review_seed
    task = SimpleNamespace(baseline_date=date(2026, 11, 19), scope_a1=True,
                           scope_a3=True, profile_code='road', jurisdiction='shanghai')
    seed = build_review_seed(task, review_run_id='safety-cost-regression',
        material_fingerprint='materials', rule_fingerprint='rules', package={'version':'test','sources':[]})
    rows = next(row for row in seed['steps'] if row['step_id']=='s1')['fixed_basis']
    originals = [row for row in rows if row.get('doc_id')=='MOF-2022-136-ARCHIVE']
    assert len(originals) == 2
    extraction = next(row for row in originals if row['marker']=='第十七条')
    payment = next(row for row in originals if row['marker']=='第十八条')
    assert (payment['page_start'],payment['page_end']) == (12,13)
    assert '分包单位不再重复提取' in ''.join(payment['text'].split())
    assert '建筑安装工程造价' in ''.join(extraction['text'].split())
    assert '按照原规定提取标准执行' in ''.join(extraction['text'].split())
    assert payment['human_verified'] is False and payment['document']['online_knowledge_verified'] is False
    assert payment['document']['effective_from'] == '2022-11-21'
    assert bv.assert_binding('RC-A10-cost',subject='subcontractor')['clause_id'].endswith('#第十八条')
    old = next(row for row in bv.load_review_basis()['external_documents'] if row['basis_id']=='MOF-2022-136')
    assert old['quote'] is None and old['quote_status']=='pending_verification'


def test_subject_mismatch_is_rejected():
    # T15-04 是承包人现场机构义务，不能套到分包人主体。
    with pytest.raises(bv.BasisBindingError) as exc:
        bv.assert_binding("T15-04", subject="subcontractor")
    assert exc.value.code == "basis_subject_mismatch"


def test_notice_only_document_cannot_bind():
    basis = copy.deepcopy(bv.load_review_basis())
    basis["documents"]["SHJT-2026-8"]["document_role"] = "notice_only"
    issues = _errors(bv.validate_basis_index(basis=basis))
    assert any(issue["code"] == "basis_role_not_binding" for issue in issues)
    with pytest.raises(bv.BasisBindingError) as exc:
        bv.assert_binding("W16-06", basis=basis)
    assert exc.value.code == "basis_role_not_binding"


def test_scanned_document_requires_ocr_companion():
    basis = copy.deepcopy(bv.load_review_basis())
    basis["documents"]["SHJT-2026-8"]["ocr_companion"] = None
    issues = _errors(bv.validate_registry(basis=basis))
    assert any(issue["code"] == "basis_ocr_companion_missing" for issue in issues)


def test_attachment_parent_must_exist():
    issues = bv.validate_attachment_pairs({"10a": "10", "12a": "12"})
    assert len(_errors(issues)) == 2
    assert {issue["code"] for issue in issues} == {"basis_attachment_parent_missing"}


def test_report_rows_use_item_scope():
    def row(title):
        for sheet in _SHEETS:
            for entry in sheet["rows"]:
                if entry[0] == title:
                    return entry
        raise AssertionError(title)

    assert row("禁止再分包")[5] == ("W16-06",)
    assert row("以劳务合作名义分包")[5] == ("W16-05",)
    assert row("分包人现场机构")[5] == ("A2-07",)
    assert row("承包人现场机构")[5] == ("T15-04",)


def test_repeated_subcontract_row_ignores_labor_cooperation_finding():
    # finding 文案里含“再分包”，但条目是 W16-05，不得被“禁止再分包”行引用。
    items = [{
        "item_id": "W16-05",
        "label": "以劳务合作名义进行施工分包",
        "message": "合同出现再分包表述但未计取主材",
        "source": "上海细则第十六条（五）",
        "status": "miss",
        "recommendation": "",
    }]
    verdict, reason, _ = _dimension_result(
        None, ("再分包", "二次分包"), items, True, ("W16-06",))
    assert verdict == "—"
    assert "第十六条（五）" not in reason


def test_ruleset_gate_passes_on_current_data():
    assert bv.ruleset_gate() is None


def test_finding_gate_rejects_broken_clause_binding():
    class _Finding:
        item_id = "W16-06"

    index = copy.deepcopy(bv.load_basis_index())
    index["items"]["W16-06"]["clause_id"] = "SHJT-2026-8#不存在的条款"
    with pytest.raises(bv.BasisBindingError) as exc:
        bv.gate_findings([_Finding()], index=index)
    assert exc.value.code == "basis_clause_missing"


def test_finding_gate_skips_unregistered_items():
    class _Finding:
        item_id = "DERIVED-NOT-REGISTERED"

    assert bv.validate_findings([_Finding()]) == []
