from types import SimpleNamespace

from app.plus.blockchain_supervision.ai_review_context import build_review_context, scope_task_level_materials


def _page(material_id, page):
    return {"task_id": 1, "subject_id": None, "material_id": material_id,
            "page": page, "text": f"material-{material_id}-page-{page}"}


def test_task_level_finding_scopes_material_context_to_anchor_and_adjacent_pages():
    finding = SimpleNamespace(
        task_id=1,
        subject_id=None,
        kind="transfer_scope",
        material_id=2,
        page=5,
        evidence_json=[],
    )
    materials = [_page(2, page) for page in range(1, 9)] + [_page(3, 1)]

    scoped, incomplete = scope_task_level_materials(finding, materials)

    assert [(row["material_id"], row["page"]) for row in scoped] == [(2, 4), (2, 5), (2, 6)]
    assert incomplete is True


def test_task_level_finding_without_anchor_keeps_full_context():
    finding = SimpleNamespace(
        task_id=1,
        subject_id=None,
        kind="transfer_scope",
        material_id=None,
        page=None,
        evidence_json=[],
    )
    materials = [_page(2, 1), _page(3, 1)]

    scoped, incomplete = scope_task_level_materials(finding, materials)

    assert scoped == materials
    assert incomplete is False


def test_enabled_scope_strategy_keeps_t1501_on_baseline_until_mapping_is_reviewed():
    finding = SimpleNamespace(
        id=7,
        task_id=1,
        subject_id=None,
        kind="transfer_scope",
        item_id="T15-01",
        material_id=None,
        page=None,
        evidence_json=[],
    )
    material = SimpleNamespace(
        id=1,
        task_id=1,
        subject_id=None,
        role="general",
        parse_state="done",
        degradations_json=[],
        pages_json=[
            {"page": 1, "text": "总承包范围 发包人"},
            {"page": 2, "text": "分包范围 承包人"},
            {"page": 3, "text": "全部工程 发包给"},
                {"page": 4, "text": "邻接说明"},
                {"page": 5, "text": "无关说明"},
                {"page": 6, "text": "更远无关说明"},
        ],
    )
    run = {
        "state": "completed",
        "task_id": 1,
        "scope_strategy": {"enabled": True, "version": "category-adjacency-v1"},
        "references": [{"reference_id": "r1", "quote": "应核对全部工程转移"}],
        "finding_links": [{
            "finding_id": 7, "subject_id": None, "item_id": "T15-01",
            "kind": "transfer_scope", "reference_ids": ["r1"],
        }],
        "limitations": [],
    }

    context = build_review_context(finding=finding, materials=[material], run=run)

    assert [page["page"] for page in context["materials"]] == [1, 2, 3, 4, 5, 6]
    assert "scope_selection" not in context


def test_enabled_scope_strategy_runs_t1505_shadow_graph_without_narrowing_context():
    finding = SimpleNamespace(
        id=8, task_id=1, subject_id=None, kind="transfer_scope", item_id="T15-05",
        material_id=1, page=1, evidence_json=[],
    )
    material = SimpleNamespace(
        id=1, task_id=1, subject_id=None, role="general", parse_state="done",
        degradations_json=[], pages_json=[{"page": 1, "text": "劳务企业 全部工程"}],
    )
    run = {
        "state": "completed", "task_id": 1,
        "scope_strategy": {"enabled": True, "version": "category-adjacency-v1"},
        "references": [{"reference_id": "r1", "quote": "应核对劳务范围"}],
        "finding_links": [{"finding_id": 8, "subject_id": None, "item_id": "T15-05",
                            "kind": "transfer_scope", "reference_ids": ["r1"]}],
        "limitations": [],
    }

    context = build_review_context(finding=finding, materials=[material], run=run)

    assert context["scope_selection"]["narrowed"] is False
    assert context["scope_selection"]["scope_incomplete"] is False
    assert context["scope_selection"]["fallback_reason"] == "shadow_only_not_reviewed"
    assert context["scope_selection"]["shadow_evidence"]["rule_fingerprint"]
    assert "occurrences" not in context["scope_selection"]["shadow_evidence"]
    assert context["scope_selection"]["shadow_evidence"]["relation_counts"]
    assert context["scope_selection"]["shadow_evidence"]["source_role_coverage"]["complete"] is True
    assert [page["page"] for page in context["materials"]] == [1]


def test_t1505_shadow_graph_keeps_ocr_gap_disclosure_and_baseline_pages(monkeypatch):
    import app.plus.blockchain_supervision.ai_review_context as review_context

    monkeypatch.setattr(review_context, "material_reading_gap", lambda _material: [2])
    monkeypatch.setattr(review_context, "reading_coverage", lambda _material: {
        "reading_complete": False, "unread_pages": [2], "suspect_pages": [],
    })
    monkeypatch.setattr(review_context, "reviewed_readable_pages", lambda _material: {1})
    finding = SimpleNamespace(
        id=81, task_id=1, subject_id=None, kind="transfer_scope", item_id="T15-05",
        material_id=None, page=None, evidence_json=[], status="hit",
    )
    material = SimpleNamespace(
        id=11, task_id=1, subject_id=None, role="general", parse_state="done",
        degradations_json=[], pages_json=[
            {"page": 1, "text": "全部工程由劳务公司承包"},
            {"page": 2, "text": "劳务公司仅承担部分劳务"},
        ],
    )
    run = {
        "state": "completed", "task_id": 1,
        "scope_strategy": {"enabled": True, "version": "category-adjacency-v1"},
        "references": [{"reference_id": "r1", "quote": "规则原文"}],
        "finding_links": [{"finding_id": 81, "subject_id": None, "item_id": "T15-05",
                            "kind": "transfer_scope", "reference_ids": ["r1"]}],
        "limitations": [],
    }

    context = review_context.build_review_context(finding=finding, materials=[material], run=run)

    assert [page["page"] for page in context["materials"]] == [1]
    assert context["incomplete_material_scope"] is True
    assert context["scope_selection"]["narrowed"] is False
    assert context["scope_selection"]["scope_incomplete"] is True


def test_enabled_scope_strategy_does_not_narrow_context_with_unreadable_pages(monkeypatch):
    import app.plus.blockchain_supervision.ai_review_context as review_context

    monkeypatch.setattr(review_context, "rule_scope_mapping", lambda _item: (("scope",), {"scope": ("范围",)}))
    finding = SimpleNamespace(
        id=9, task_id=1, subject_id=None, kind="transfer_scope", item_id="T15-01",
        material_id=None, page=None, evidence_json=[],
    )
    material = SimpleNamespace(
        id=1, task_id=1, subject_id=None, role="general", parse_state="done",
        degradations_json=[], pages_json=[
            {"page": 1, "text": "范围命中"},
            {"page": 2, "text": "其他有效信息"},
            {"page": 3, "text": ""},
        ],
    )
    run = {
        "state": "completed", "task_id": 1,
        "scope_strategy": {"enabled": True, "version": "category-adjacency-v1"},
        "references": [{"reference_id": "r1", "quote": "应核对范围"}],
        "finding_links": [{"finding_id": 9, "subject_id": None, "item_id": "T15-01",
                            "kind": "transfer_scope", "reference_ids": ["r1"]}],
        "limitations": [],
    }

    context = review_context.build_review_context(finding=finding, materials=[material], run=run)

    assert [page["page"] for page in context["materials"]] == [1, 2]
    assert context["scope_selection"]["narrowed"] is False
    assert context["scope_selection"]["fallback_reason"] == "preexisting_incomplete_context"


def test_enabled_scope_strategy_keeps_undecidable_conflict_on_baseline_context(monkeypatch):
    import app.plus.blockchain_supervision.ai_review_context as review_context

    monkeypatch.setattr(review_context, "rule_scope_mapping", lambda _item: (("scope",), {"scope": ("范围",)}))
    finding = SimpleNamespace(
        id=10, task_id=1, subject_id=None, kind="transfer_scope", item_id="T15-01",
        status="undecidable", material_id=None, page=None, evidence_json=[],
    )
    material = SimpleNamespace(
        id=1, task_id=1, subject_id=None, role="general", parse_state="done",
        degradations_json=[], pages_json=[
            {"page": 1, "text": "范围命中"},
            {"page": 2, "text": "无关信息"},
            {"page": 3, "text": "更远无关信息"},
        ],
    )
    run = {
        "state": "completed", "task_id": 1,
        "scope_strategy": {"enabled": True, "version": "category-adjacency-v1"},
        "references": [{"reference_id": "r1", "quote": "应核对范围"}],
        "finding_links": [{"finding_id": 10, "subject_id": None, "item_id": "T15-01",
                            "kind": "transfer_scope", "reference_ids": ["r1"]}],
        "limitations": [],
    }

    context = review_context.build_review_context(finding=finding, materials=[material], run=run)

    assert [page["page"] for page in context["materials"]] == [1, 2, 3]
    assert context["scope_selection"]["narrowed"] is False
    assert context["scope_selection"]["fallback_reason"] == "undecidable_finding"
