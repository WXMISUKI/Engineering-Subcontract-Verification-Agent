from app.plus.blockchain_supervision.ai_scope_selection import (
    SCOPE_SELECTOR_ENV,
    resolved_scope_strategy,
    select_scope,
    scope_selector_enabled,
)
from app.plus.blockchain_supervision.ai_scope_mappings import rule_scope_mapping
from app.plus.blockchain_supervision.rules import load_transfer_rules


def page(material_id, page_no, text, subject_id=None):
    return {
        "task_id": 1,
        "subject_id": subject_id,
        "material_id": material_id,
        "page": page_no,
        "text": text,
    }


def test_selector_is_default_off_and_reads_only_explicit_server_flag():
    assert scope_selector_enabled({}) is False
    assert scope_selector_enabled({SCOPE_SELECTOR_ENV: "true"}) is True
    assert scope_selector_enabled({SCOPE_SELECTOR_ENV: "TRUE"}) is True
    assert scope_selector_enabled({SCOPE_SELECTOR_ENV: "unexpected"}) is False


def test_selector_requires_every_category_and_falls_back_when_mapping_is_missing():
    pages = [page(1, 1, "范围和主体"), page(1, 2, "支付信息")]
    result = select_scope(
        pages,
        required_categories=["scope", "payment"],
        category_selectors={"scope": ["范围"]},
    )
    assert result.pages == tuple(pages)
    assert result.narrowed is False
    assert result.fallback_reason == "category_mapping_missing"


def test_selector_falls_back_when_required_category_has_no_hit():
    pages = [page(1, 1, "范围和主体"), page(1, 2, "无关页面")]
    result = select_scope(
        pages,
        required_categories=["scope", "payment"],
        category_selectors={"scope": ["范围"], "payment": ["付款"]},
    )
    assert result.pages == tuple(pages)
    assert result.fallback_reason == "required_category_no_hit"


def test_selector_keeps_same_material_adjacency_and_never_crosses_subject_or_material():
    pages = [
        page(1, 1, "前页", subject_id=10),
        page(1, 2, "范围证据", subject_id=10),
        page(1, 3, "续页", subject_id=10),
        page(1, 2, "另一个主体同页", subject_id=20),
        page(2, 1, "另一个材料", subject_id=10),
    ]
    result = select_scope(
        pages,
        required_categories=["scope"],
        category_selectors={"scope": ["范围证据"]},
    )
    assert [(row["material_id"], row["subject_id"], row["page"]) for row in result.pages] == [
        (1, 10, 1), (1, 10, 2), (1, 10, 3)
    ]
    assert result.scope_incomplete is True
    assert result.excluded_page_count == 2
    assert result.selected_page_ids == ((1, 10, 1), (1, 10, 2), (1, 10, 3))
    assert result.excluded_page_ids == ((1, 20, 2), (2, 10, 1))


def test_selector_keeps_full_cross_page_fact_span_and_bounded_context():
    pages = [
        page(1, 1, "合同主体与工程范围", subject_id=10),
        page(1, 2, "事实链第一段", subject_id=10),
        page(1, 3, "事实链第二段", subject_id=10),
        page(1, 4, "事实链第三段", subject_id=10),
        page(1, 5, "事实链第四段", subject_id=10),
        page(1, 6, "付款关系", subject_id=10),
        page(1, 7, "末页邻接说明", subject_id=10),
        page(1, 8, "无关附件一", subject_id=10),
        page(1, 9, "无关附件二", subject_id=10),
    ]

    result = select_scope(
        pages,
        required_categories=["scope", "payment"],
        category_selectors={"scope": ["工程范围"], "payment": ["付款关系"]},
    )

    assert [row["page"] for row in result.pages] == list(range(1, 8))
    assert result.narrowed is True


def test_selector_keeps_table_continuation_between_category_hits():
    pages = [
        page(1, 1, "范围表格：合同主体", subject_id=10),
        page(1, 2, "表格續表：计量项目", subject_id=10),
        page(1, 3, "表格续页：支付关系", subject_id=10),
        page(1, 4, "表格续页：结算说明", subject_id=10),
        page(1, 5, "无关附件", subject_id=10),
    ]

    result = select_scope(
        pages,
        required_categories=["scope", "payment"],
        category_selectors={"scope": ["合同主体"], "payment": ["支付关系"]},
    )

    assert [row["page"] for row in result.pages] == [1, 2, 3, 4]
    assert result.narrowed is True


def test_selector_preserves_anchor_pages_inside_the_authorized_context():
    pages = [page(1, 1, "命中"), page(1, 2, "锚点"), page(1, 3, "其他")]
    result = select_scope(
        pages,
        required_categories=["scope"],
        category_selectors={"scope": ["命中"]},
        anchor_pages=[(1, 2)],
    )
    assert [(row["page"]) for row in result.pages] == [1, 2, 3]
    assert result.fallback_reason == "candidate_not_narrower"


def test_subject_qualified_anchor_never_imports_another_subject_page():
    pages = [
        page(1, 1, "范围证据", subject_id=10),
        page(1, 1, "另一主体锚点", subject_id=20),
        page(1, 2, "另一主体邻页", subject_id=20),
    ]

    result = select_scope(
        pages,
        required_categories=["scope"],
        category_selectors={"scope": ["范围证据"]},
        anchor_pages=[(1, 1, 20)],
    )

    assert [(row["subject_id"], row["page"]) for row in result.pages] == [(10, 1), (20, 1), (20, 2)]


def test_selector_falls_back_for_malformed_anchor_identity():
    pages = [page(1, 1, "范围证据"), page(1, 2, "其他信息")]

    result = select_scope(
        pages,
        required_categories=["scope"],
        category_selectors={"scope": ["范围证据"]},
        anchor_pages=[(1, "2")],
    )

    assert result.pages == tuple(pages)
    assert result.fallback_reason == "invalid_anchor"


def test_selector_falls_back_for_unreadable_or_duplicate_pages():
    unreadable = select_scope(
        [page(1, 1, "")],
        required_categories=["scope"],
        category_selectors={"scope": ["范围"]},
    )
    assert unreadable.fallback_reason == "unreadable_page"

    duplicate = select_scope(
        [page(1, 1, "范围"), page(1, 1, "范围二")],
        required_categories=["scope"],
        category_selectors={"scope": ["范围"]},
    )
    assert duplicate.fallback_reason == "duplicate_page_identity"


def test_selector_fingerprint_changes_with_selected_text_and_strategy():
    pages = [page(1, 1, "范围"), page(1, 2, "付款")]
    first = select_scope(
        pages,
        required_categories=["scope"],
        category_selectors={"scope": ["范围"]},
        strategy_version="v1",
    )
    second = select_scope(
        pages,
        required_categories=["scope"],
        category_selectors={"scope": ["范围"]},
        strategy_version="v2",
    )
    assert first.input_fingerprint != second.input_fingerprint


def test_all_catalog_rules_stay_baseline_until_kernel_and_linkage_review():
    rules = load_transfer_rules()["rules"]

    assert rules
    assert all(rule_scope_mapping(rule["id"]) is None for rule in rules)


def test_resolved_scope_strategy_is_frozen_as_baseline_or_explicit_candidate():
    assert resolved_scope_strategy({}) == {"enabled": False, "version": "baseline"}
    assert resolved_scope_strategy({SCOPE_SELECTOR_ENV: "true"}) == {
        "enabled": True,
        "version": "category-adjacency-v1",
    }
