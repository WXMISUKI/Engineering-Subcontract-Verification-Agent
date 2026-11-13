from copy import deepcopy
from types import SimpleNamespace

from app.plus.blockchain_supervision import a1_check
from app.plus.blockchain_supervision.ai_scope_evidence_graph import (
    build_t15_05_shadow_graph,
)
from app.plus.blockchain_supervision.models import STATUS_HIT, STATUS_MISS, STATUS_UNDECIDABLE
from app.plus.blockchain_supervision import rules


def _material(material_id, role, pages, *, subject_id=None):
    return SimpleNamespace(
        id=material_id, task_id=1, subject_id=subject_id,
        file_name=f"material-{material_id}.pdf", role=role,
        parse_state="done", pages_json=pages,
        degradations_json=[],
    )


def _eligible(material_id, page, text, *, task_id=1, subject_id=None):
    return {
        "task_id": task_id, "subject_id": subject_id,
        "material_id": material_id, "page": page, "text": text,
    }


def test_t15_05_shadow_graph_enumerates_all_positive_and_counter_evidence():
    general_text = (
        "全部工程由劳务公司承包。劳务公司仅承担路基劳务作业。"
        "承包人将全部工程发包给另一劳务企业。"
    )
    labor_text = "劳务企业计取全部工程价款，另按2%计取管理费。"
    materials = [
        _material(10, "general", [{"page": 1, "text": general_text}]),
        _material(20, "labor", [{"page": 3, "text": labor_text}]),
    ]
    pages = [
        _eligible(10, 1, general_text),
        _eligible(20, 3, labor_text),
    ]

    graph = build_t15_05_shadow_graph(eligible_pages=pages, materials=materials)

    assert graph["rule_id"] == "T15-05"
    assert graph["narrowing_eligible"] is False
    assert graph["fallback_reason"] == "shadow_only_not_reviewed"
    assert graph["input_fingerprint"]
    occurrences = graph["occurrences"]
    assert occurrences
    assert all("text" not in occurrence and "quote" not in occurrence for occurrence in occurrences)
    assert any(row["relation"] == "labor_whole_scope" and row["material_id"] == 10
               for row in occurrences)
    assert any(row["relation"] == "whole_scope_negation" and row["material_id"] == 10
               for row in occurrences)
    assert any(row["relation"] == "labor_payment" and row["material_id"] == 20
               for row in occurrences)
    assert any(row["relation"] == "management_fee" and row["material_id"] == 20
               for row in occurrences)
    assert graph["positive_scope_relation_count"] >= 2
    assert graph["counter_scope_relation_count"] >= 1


def test_t15_05_shadow_graph_rejects_page_without_unique_source_role():
    graph = build_t15_05_shadow_graph(
        eligible_pages=[_eligible(99, 1, "劳务企业计取全部工程价款")],
        materials=[],
    )

    assert graph["narrowing_eligible"] is False
    assert graph["fallback_reason"] == "page_source_unresolved"
    assert graph["occurrences"] == []


def test_t15_05_shadow_graph_reports_missing_general_source_without_inferring_no_fact():
    text = "劳务企业计取全部工程价款，另按2%计取管理费。"
    material = _material(20, "labor", [{"page": 3, "text": text}])

    graph = build_t15_05_shadow_graph(
        eligible_pages=[_eligible(20, 3, text)], materials=[material])

    assert graph["role_coverage"]["present_source_roles"] == ("labor",)
    assert graph["role_coverage"]["missing_source_roles"] == ("general",)
    assert graph["role_coverage"]["missing_readable_page_roles"] == ("general",)
    assert graph["role_coverage"]["complete"] is False
    assert graph["narrowing_eligible"] is False
    assert graph["fallback_reason"] == "shadow_only_not_reviewed"


def test_t15_05_shadow_graph_distinguishes_unreadable_general_source_from_missing_source():
    labor_text = "劳务企业计取全部工程价款，另按2%计取管理费。"
    materials = [
        _material(10, "general", [{"page": 1, "text": "总包正文未读"}]),
        _material(20, "labor", [{"page": 3, "text": labor_text}]),
    ]
    materials[0].degradations_json = [{"kind": "no_text_layer"}]
    eligible = [_eligible(20, 3, labor_text)]

    graph = build_t15_05_shadow_graph(eligible_pages=eligible, materials=materials)

    coverage = graph["role_coverage"]
    assert coverage["present_source_roles"] == ("general", "labor")
    assert coverage["missing_source_roles"] == ()
    assert coverage["readable_page_roles"] == ("labor",)
    assert coverage["missing_readable_page_roles"] == ("general",)
    assert coverage["complete"] is False


def test_t15_05_shadow_graph_locators_cover_synthetic_kernel_hit_sides_without_narrowing():
    general_text = "总承包范围：路基工程。承包人将全部工程发包给某劳务公司。"
    labor_text = "劳务企业计取全部工程价款，另按2%计取管理费。"
    general = _material(10, "general", [{"page": 1, "text": general_text}])
    labor = _material(20, "labor", [{"page": 3, "text": labor_text}])
    materials = [general, labor]
    task = SimpleNamespace(id=1)

    finding = next(row for row in a1_check.run_transfer_relations_check(task, materials)
                   if row.item_id == "T15-05")
    pages = [
        _eligible(10, 1, general_text),
        _eligible(20, 3, labor_text),
    ]
    graph = build_t15_05_shadow_graph(eligible_pages=pages, materials=materials)

    assert finding.status == STATUS_HIT
    assert graph["role_coverage"]["complete"] is True
    assert {row["relation"] for row in graph["occurrences"]} >= {
        "labor_whole_scope", "labor_payment", "management_fee",
    }
    assert {row["material_id"] for row in graph["occurrences"]
            if row["relation"] == "labor_whole_scope"} == {10}
    assert {row["material_id"] for row in graph["occurrences"]
            if row["relation"] in {"labor_payment", "management_fee"}} == {20}
    assert graph["narrowing_eligible"] is False
    assert len(pages) == 2


def test_t15_05_shadow_graph_tracks_kernel_miss_counterevidence_without_narrowing():
    text = "总承包范围：路基工程。劳务公司仅承担路基劳务作业。"
    material = _material(10, "general", [{"page": 1, "text": text}])
    materials = [material]
    task = SimpleNamespace(id=1)

    finding = next(row for row in a1_check.run_transfer_relations_check(task, materials)
                   if row.item_id == "T15-05")
    graph = build_t15_05_shadow_graph(
        eligible_pages=[_eligible(10, 1, text)], materials=materials)

    assert finding.status == STATUS_MISS
    assert any(row["relation"] == "whole_scope_negation" for row in graph["occurrences"])
    assert graph["narrowing_eligible"] is False
    assert len(graph["occurrences"]) >= 1


def test_t15_05_shadow_graph_tracks_kernel_conflict_as_both_sides_without_narrowing():
    general_text = (
        "总承包范围：路基工程。承包人将全部工程发包给某劳务公司。"
        "劳务公司仅承担路基劳务作业。"
    )
    labor_text = "劳务企业计取全部工程价款，另按2%计取管理费。"
    materials = [
        _material(10, "general", [{"page": 1, "text": general_text}]),
        _material(20, "labor", [{"page": 3, "text": labor_text}]),
    ]
    task = SimpleNamespace(id=1)
    finding = next(row for row in a1_check.run_transfer_relations_check(task, materials)
                   if row.item_id == "T15-05")
    graph = build_t15_05_shadow_graph(eligible_pages=[
        _eligible(10, 1, general_text), _eligible(20, 3, labor_text),
    ], materials=materials)

    assert finding.status == STATUS_UNDECIDABLE
    relations = {row["relation"] for row in graph["occurrences"]}
    assert "labor_whole_scope" in relations
    assert "whole_scope_negation" in relations
    assert "labor_payment" in relations
    assert graph["narrowing_eligible"] is False


def test_t15_05_shadow_graph_rejects_duplicate_material_page_identity():
    material = _material(10, "general", [{"page": 1, "text": "全部工程由劳务公司承包"}])
    page = _eligible(10, 1, "全部工程由劳务公司承包")

    graph = build_t15_05_shadow_graph(eligible_pages=[page, page], materials=[material])

    assert graph["narrowing_eligible"] is False
    assert graph["fallback_reason"] == "duplicate_page_identity"
    assert graph["occurrences"] == []


def test_t15_05_shadow_graph_invalid_page_shape_fails_closed():
    graph = build_t15_05_shadow_graph(eligible_pages=[None], materials=[])

    assert graph["narrowing_eligible"] is False
    assert graph["fallback_reason"] == "invalid_eligible_page"
    assert graph["occurrences"] == []


def test_t15_05_shadow_graph_requires_exact_source_page_text():
    material = _material(10, "general", [{"page": 1, "text": "总包来源页文本"}])

    graph = build_t15_05_shadow_graph(
        eligible_pages=[_eligible(10, 1, "伪造的影子页文本")], materials=[material])

    assert graph["narrowing_eligible"] is False
    assert graph["fallback_reason"] == "page_text_does_not_match_source"
    assert graph["occurrences"] == []


def test_t15_05_shadow_graph_fingerprint_includes_rule_definition(monkeypatch):
    text = "全部工程由劳务公司承包。"
    material = _material(10, "general", [{"page": 1, "text": text}])
    page = _eligible(10, 1, text)
    original_loader = rules.load_transfer_rules
    original = original_loader()
    first = build_t15_05_shadow_graph(eligible_pages=[page], materials=[material])
    changed = deepcopy(original)
    rule = next(row for row in changed["rules"] if row["id"] == "T15-05")
    rule["relations_detection"]["labor_whole_scope_patterns"].append("synthetic-shadow-pattern")
    monkeypatch.setattr(rules, "load_transfer_rules", lambda: changed)

    second = build_t15_05_shadow_graph(eligible_pages=[page], materials=[material])

    assert first["rule_fingerprint"] != second["rule_fingerprint"]
    assert first["input_fingerprint"] != second["input_fingerprint"]


def test_t15_05_shadow_graph_invalid_rule_pattern_fails_closed(monkeypatch):
    original = rules.load_transfer_rules()
    changed = deepcopy(original)
    rule = next(row for row in changed["rules"] if row["id"] == "T15-05")
    rule["relations_detection"]["labor_whole_scope_patterns"] = ["("]
    monkeypatch.setattr(rules, "load_transfer_rules", lambda: changed)
    text = "全部工程由劳务公司承包。"
    material = _material(10, "general", [{"page": 1, "text": text}])

    graph = build_t15_05_shadow_graph(
        eligible_pages=[_eligible(10, 1, text)], materials=[material])

    assert graph["narrowing_eligible"] is False
    assert graph["fallback_reason"] == "rule_selector_error"
    assert graph["rule_fingerprint"]
    assert graph["occurrences"] == []


def test_t15_05_shadow_graph_never_reads_evidence_pool_for_general_only_scope():
    general = _material(10, "general", [{"page": 1, "text": "总包范围文本"}])
    labor = _material(20, "labor", [{"page": 1, "text": "全部工程由劳务公司承包"}])
    pages = [_eligible(10, 1, "总包范围文本"), _eligible(20, 1, "全部工程由劳务公司承包")]

    graph = build_t15_05_shadow_graph(eligible_pages=pages, materials=[general, labor])

    assert not any(row["relation"] == "labor_whole_scope" and row["material_id"] == 20
                   for row in graph["occurrences"])


def test_t15_05_shadow_graph_keeps_subject_identity_separate():
    text = "全部工程由劳务公司承包。"
    materials = [
        _material(10, "general", [{"page": 1, "text": text}], subject_id=7),
        _material(20, "general", [{"page": 1, "text": text}], subject_id=8),
    ]
    pages = [_eligible(10, 1, text, subject_id=7), _eligible(20, 1, text, subject_id=8)]

    graph = build_t15_05_shadow_graph(eligible_pages=pages, materials=materials)

    relations = [row for row in graph["occurrences"] if row["relation"] == "labor_whole_scope"]
    assert {(row["material_id"], row["subject_id"]) for row in relations} == {(10, 7), (20, 8)}


def test_t15_05_shadow_graph_preserves_cross_page_positive_and_counterevidence_locations():
    pages = [
        (1, "全部工程由劳务公司承包。"),
        (2, "劳务公司仅承担路基劳务作业。"),
        (3, "劳务企业计取全部工程价款，另按2%计取管理费。"),
    ]
    material = _material(10, "general", [
        {"page": page_no, "text": text} for page_no, text in pages
    ])
    eligible = [_eligible(10, page_no, text) for page_no, text in pages]

    graph = build_t15_05_shadow_graph(eligible_pages=eligible, materials=[material])

    positive_pages = {row["page"] for row in graph["occurrences"]
                      if row["relation"] == "labor_whole_scope"}
    counter_pages = {row["page"] for row in graph["occurrences"]
                     if row["relation"] == "whole_scope_negation"}
    payment_pages = {row["page"] for row in graph["occurrences"]
                     if row["relation"] == "labor_payment"}
    assert 1 in positive_pages
    assert 2 in counter_pages
    assert 3 in payment_pages


def test_t15_05_shadow_graph_no_keyword_hits_still_keeps_full_baseline_context():
    text = "本页为普通技术说明，没有规则证据词项。"
    material = _material(10, "general", [{"page": 1, "text": text}])
    page = _eligible(10, 1, text)

    graph = build_t15_05_shadow_graph(eligible_pages=[page], materials=[material])

    assert graph["occurrences"] == []
    assert graph["narrowing_eligible"] is False
    assert graph["fallback_reason"] == "shadow_only_not_reviewed"
    assert graph["input_fingerprint"]


def test_t15_05_shadow_graph_keeps_both_sides_of_scope_conflict():
    text = "全部工程由劳务公司承包；劳务公司仅承担路基劳务作业。"
    material = _material(10, "general", [{"page": 2, "text": text}])
    page = _eligible(10, 2, text)

    graph = build_t15_05_shadow_graph(eligible_pages=[page], materials=[material])

    positive = [row for row in graph["occurrences"] if row["relation"] == "labor_whole_scope"]
    counter = [row for row in graph["occurrences"] if row["relation"] == "whole_scope_negation"]
    assert positive and counter
    assert {row["page"] for row in positive + counter} == {2}
    assert graph["narrowing_eligible"] is False
    assert graph["fallback_reason"] == "shadow_only_not_reviewed"
