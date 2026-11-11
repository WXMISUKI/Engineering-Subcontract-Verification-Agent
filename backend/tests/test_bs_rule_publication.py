from app.plus.blockchain_supervision.rules_compile import (
    compile_catalog,
    prepare_publication_candidate,
)


def _catalog():
    return compile_catalog([
        {
            "card_id": "floor-1",
            "source_text": "项目必须提交完整材料",
            "anchor_text": "必须提交完整材料",
            "clause_id": "DOC#1",
            "clause": {"page_start": 1, "page_end": 1},
            "statuses": ["hit", "miss", "undecidable"],
            "items": ["contract"],
        },
    ])


def _identity():
    return {
        "basis_snapshot_id": "basis-road-r3",
        "ruleset_version": "bs-rules-r8",
        "profile_code": "road_default",
        "source_manifest_hash": "a" * 64,
    }


def test_publication_candidate_is_immutable_human_gate():
    result = prepare_publication_candidate(_catalog(), **_identity())

    assert result["status"] == "pending_human"
    assert result["published"] is False
    assert result["writeback_performed"] is False
    assert result["candidate_snapshot_id"].startswith("bs-rule-candidate-")
    assert result["card_count"] == 1


def test_publication_candidate_rejects_missing_identity():
    identity = _identity()
    identity["profile_code"] = ""
    result = prepare_publication_candidate(_catalog(), **identity)

    assert result["status"] == "rejected"
    assert result["reason_code"] == "publication_identity_incomplete"
    assert result["candidate_snapshot_id"] is None


def test_publication_candidate_reports_all_missing_identity_fields():
    result = prepare_publication_candidate(
        _catalog(),
        basis_snapshot_id="",
        ruleset_version="",
        profile_code="",
        source_manifest_hash="",
    )

    assert result["status"] == "rejected"
    assert result["reason_code"] == "publication_identity_incomplete"
    assert result["missing_identity"] == [
        "basis_snapshot_id",
        "ruleset_version",
        "profile_code",
        "source_manifest_hash",
    ]


def test_publication_candidate_rejects_catalog_that_fails_real_compilation():
    bad_card = {
        "card_id": "broken-1",
        "source_text": "项目必须提交完整材料",
        "anchor_text": "不存在的原文",
        "clause_id": "DOC#1",
        "clause": {"page_start": 1, "page_end": 1},
        "statuses": ["hit", "miss", "undecidable"],
        "items": ["contract"],
    }
    catalog = compile_catalog([bad_card])

    result = prepare_publication_candidate(catalog, **_identity())

    assert catalog["status"] == "fail"
    assert result["status"] == "rejected"
    assert result["reason_code"] == "compile_gate_not_passed"


def test_publication_candidate_rejects_unresolved_catalog():
    catalog = {"status": "fail", "reports": [{"status": "rejected", "card": {}}]}
    result = prepare_publication_candidate(catalog, **_identity())

    assert result["status"] == "rejected"
    assert result["reason_code"] == "compile_gate_not_passed"
    assert result["published"] is not True


def test_new_rule_card_is_data_only_extension_until_human_publish():
    card = {
        "card_id": "NEW-DATA-ONLY-01",
        "source_text": "新增依据要求提交现场核验记录",
        "anchor_text": "提交现场核验记录",
        "clause_id": "NEW-DOC#3",
        "clause": {"page_start": 3, "page_end": 3},
        "statuses": ["hit", "miss", "undecidable"],
        "items": ["field_record"],
    }
    catalog = compile_catalog([card])
    result = prepare_publication_candidate(catalog, **_identity())

    assert catalog["status"] == "pass"
    assert result["status"] == "pending_human"
    assert result["card_count"] == 1
    assert result["published"] is False


def test_publication_candidate_returns_readable_error_for_malformed_reports():
    result = prepare_publication_candidate(
        {"status": "pass", "reports": ["bad-entry"]},
        **_identity(),
    )

    assert result["status"] == "rejected"
    assert result["reason_code"] == "catalog_report_entry_invalid"
    assert result["published"] is False
