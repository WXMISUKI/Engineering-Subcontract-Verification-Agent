from types import SimpleNamespace

from app.plus.blockchain_supervision.rule_source_coverage import build_rule_source_coverage


def _material(material_id=1, role="sub", parse_state="done", gaps=None):
    return SimpleNamespace(
        id=material_id, role=role, parse_state=parse_state,
        pages_json=[{"page": 1, "text": "合成材料正文"}],
        degradations_json=gaps or [],
    )


def _rule(coverage, rule_id):
    return next(row for row in coverage["rules"] if row["rule_id"] == rule_id)


def _requirement(coverage, rule_id, source_key):
    return next(row for row in _rule(coverage, rule_id)["requirements"]
                if row["source_key"] == source_key)


def test_no_materials_marks_required_sources_uncovered():
    coverage = build_rule_source_coverage([])
    assert _rule(coverage, "RC-A02")["state"] == "uncovered"
    assert _rule(coverage, "RC-A02")["reason_codes"] == ["source_not_provided"]
    assert _requirement(coverage, "NL-B-01", "subcontract")["state"] == "uncovered"


def test_readable_subcontract_covers_applicable_rules():
    coverage = build_rule_source_coverage([_material()])
    assert _rule(coverage, "RC-A02")["state"] == "covered"
    assert _requirement(coverage, "NL-B-01", "subcontract")["state"] == "covered"
    # 技术参数/适用上下文没有可靠分类器，整条规则保持 unknown。
    assert _requirement(coverage, "NL-B-01", "technical_context")["state"] == "unknown"
    assert _rule(coverage, "NL-B-01")["state"] == "unknown"
    # 没有逐项分类器的清单项保持 unknown，不冒充已覆盖。
    assert _rule(coverage, "RC-A01")["state"] == "unknown"
    assert _rule(coverage, "RC-A01")["reason_codes"] == ["source_classifier_unavailable"]


def test_pending_and_unreadable_sources_are_unknown_not_missing():
    pending = build_rule_source_coverage([_material(parse_state="parsing")])
    assert _rule(pending, "RC-A02")["state"] == "unknown"
    assert _rule(pending, "RC-A02")["reason_codes"] == ["source_reading_pending"]

    unreadable = build_rule_source_coverage([
        _material(gaps=[{"kind": "parse_incomplete", "total_pages": 2,
                         "read_pages": [1], "unread_pages": [2],
                         "ocr_suspect_pages": [], "ocr_failed_pages": []}])
    ])
    assert _rule(unreadable, "RC-A02")["state"] == "unknown"
    assert _rule(unreadable, "RC-A02")["reason_codes"] == ["source_unreadable"]
