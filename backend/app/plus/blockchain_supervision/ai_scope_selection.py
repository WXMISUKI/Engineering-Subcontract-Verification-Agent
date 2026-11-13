"""Opt-in, fail-closed page selection for MoMA advisory context.

This module deliberately knows nothing about database sessions or model calls. The
caller supplies an already authorized/readable page set and an explicit mapping
from required evidence categories to selector terms. Missing mappings fall back to
the caller's existing context instead of guessing from rule field names.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from typing import Any, Mapping, Sequence


SCOPE_SELECTOR_ENV = "BS_MOMA_SCOPE_SELECTOR_ENABLED"
SCOPE_SELECTOR_STRATEGY = "category-adjacency-v1"
_TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ScopeSelection:
    """The selector result and non-content audit facts."""

    pages: tuple[dict, ...]
    narrowed: bool
    scope_incomplete: bool
    strategy_version: str
    matched_categories: tuple[str, ...]
    fallback_reason: str | None
    excluded_page_count: int
    input_fingerprint: str
    selected_page_ids: tuple[tuple[int | None, int | None, int], ...]
    excluded_page_ids: tuple[tuple[int | None, int | None, int], ...]


def scope_selector_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Read the server-side opt-in flag; missing/invalid values stay disabled."""

    source = os.environ if env is None else env
    return str(source.get(SCOPE_SELECTOR_ENV, "")).strip().lower() in _TRUE_VALUES


def resolved_scope_strategy(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Return the strategy identity to freeze into a review seed."""

    enabled = scope_selector_enabled(env)
    return {
        "enabled": enabled,
        "version": SCOPE_SELECTOR_STRATEGY if enabled else "baseline",
    }


def _page_key(page: Mapping[str, Any]) -> tuple[int, int, int | None]:
    return (page.get("material_id"), page.get("page"), page.get("subject_id"))


def _fingerprint(pages: Sequence[Mapping[str, Any]], strategy_version: str) -> str:
    identity = [
        {
            "task_id": page.get("task_id"),
            "subject_id": page.get("subject_id"),
            "material_id": page.get("material_id"),
            "page": page.get("page"),
            "text_sha256": hashlib.sha256(
                str(page.get("text", "")).encode("utf-8")
            ).hexdigest(),
        }
        for page in pages
    ]
    payload = {"strategy_version": strategy_version, "pages": identity}
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _page_ids(pages: Sequence[Mapping[str, Any]]) -> tuple[tuple[int | None, int | None, int], ...]:
    return tuple(
        (page.get("material_id"), page.get("subject_id"), page.get("page"))
        for page in pages
        if type(page.get("page")) is int
    )


def _fallback(
    pages: Sequence[dict],
    reason: str,
    *,
    strategy_version: str,
    matched_categories: Sequence[str] = (),
) -> ScopeSelection:
    frozen = tuple(pages)
    return ScopeSelection(
        pages=frozen,
        narrowed=False,
        scope_incomplete=False,
        strategy_version=strategy_version,
        matched_categories=tuple(sorted(set(matched_categories))),
        fallback_reason=reason,
        excluded_page_count=0,
        input_fingerprint=_fingerprint(frozen, strategy_version),
        selected_page_ids=_page_ids(frozen),
        excluded_page_ids=(),
    )


def fallback_scope(
    pages: Sequence[dict],
    *,
    reason: str,
    strategy_version: str = SCOPE_SELECTOR_STRATEGY,
) -> ScopeSelection:
    """Keep the caller's current page set when it detects uncertain linkage."""

    return _fallback(pages, reason, strategy_version=strategy_version)


def _validate_pages(pages: Sequence[dict]) -> str | None:
    seen = set()
    for page in pages:
        if not isinstance(page, dict):
            return "invalid_page"
        if (type(page.get("material_id")) is not int or page["material_id"] < 1
                or type(page.get("page")) is not int or page["page"] < 1
                or (page.get("subject_id") is not None and
                    (type(page.get("subject_id")) is not int or page["subject_id"] < 1))):
            return "invalid_page_identity"
        if not isinstance(page.get("text"), str) or not page["text"].strip():
            return "unreadable_page"
        key = _page_key(page)
        if key in seen:
            return "duplicate_page_identity"
        seen.add(key)
    return None


def select_scope(
    pages: Sequence[dict],
    *,
    required_categories: Sequence[str],
    category_selectors: Mapping[str, Sequence[str]],
    anchor_pages: Sequence[tuple[int, int]] = (),
    strategy_version: str = SCOPE_SELECTOR_STRATEGY,
    adjacency: int = 1,
) -> ScopeSelection:
    """Select a conservative subset of an already authorized page set.

    ``category_selectors`` is an explicit, reviewed mapping. Terms are matched
    literally after whitespace removal. Every required category must have at least
    one non-empty selector term and at least one hit; otherwise the original page
    set is returned with a fallback reason. Page adjacency never crosses material
    identity and never adds a page outside ``pages``.
    """

    original = list(pages)
    if not original:
        return _fallback(original, "empty_context", strategy_version=strategy_version)
    invalid = _validate_pages(original)
    if invalid:
        return _fallback(original, invalid, strategy_version=strategy_version)
    if type(adjacency) is not int or adjacency < 0 or adjacency > 3:
        return _fallback(original, "invalid_adjacency", strategy_version=strategy_version)
    anchors = list(anchor_pages)
    if any(
        not isinstance(anchor, (tuple, list)) or len(anchor) not in {2, 3}
        or type(anchor[0]) is not int or anchor[0] < 1
        or type(anchor[1]) is not int or anchor[1] < 1
        or (len(anchor) == 3 and anchor[2] is not None
            and (type(anchor[2]) is not int or anchor[2] < 1))
        for anchor in anchors
    ):
        return _fallback(original, "invalid_anchor", strategy_version=strategy_version)

    categories = tuple(dict.fromkeys(str(value) for value in required_categories if str(value).strip()))
    if not categories:
        return _fallback(original, "no_required_categories", strategy_version=strategy_version)

    normalized_text = {
        _page_key(page): "".join(page["text"].split()) for page in original
    }
    hits_by_category: dict[str, set[tuple[int, int, int | None]]] = {}
    for category in categories:
        terms = {
            "".join(str(term).split())
            for term in (category_selectors.get(category) or ())
            if str(term).strip()
        }
        if not terms:
            return _fallback(original, "category_mapping_missing", strategy_version=strategy_version)
        hits = {
            key for key, text in normalized_text.items()
            if any(term in text for term in terms)
        }
        if not hits:
            return _fallback(
                original,
                "required_category_no_hit",
                strategy_version=strategy_version,
                matched_categories=hits_by_category,
            )
        hits_by_category[category] = hits

    original_by_key = {_page_key(page): page for page in original}
    selected_keys = set().union(*hits_by_category.values())
    selected_keys.update(
        key for key in (_page_key(page) for page in original)
        if any(
            key[0] == anchor[0]
            and abs(key[1] - anchor[1]) <= adjacency
            and (len(anchor) == 2 or key[2] == anchor[2])
            for anchor in anchors
        )
    )
    # Keep the complete readable span between evidence-category hits. This
    # preserves multi-page relations and table continuations without guessing.
    pages_by_material_subject: dict[tuple[int, int | None], list[int]] = {}
    for material_id, page_no, subject_id in selected_keys:
        pages_by_material_subject.setdefault((material_id, subject_id), []).append(page_no)
    for (material_id, subject_id), page_numbers in pages_by_material_subject.items():
        if len(page_numbers) < 2:
            continue
        low, high = min(page_numbers), max(page_numbers)
        selected_keys.update(
            key for key in original_by_key
            if key[0] == material_id and key[2] == subject_id and low <= key[1] <= high
        )
    for material_id, page_no, subject_id in list(selected_keys):
        for key in original_by_key:
            if key[0] == material_id and key[2] == subject_id and abs(key[1] - page_no) <= adjacency:
                selected_keys.add(key)

    selected = [page for page in original if _page_key(page) in selected_keys]
    if not selected or len(selected) == len(original):
        return _fallback(
            original,
            "candidate_not_narrower" if selected else "candidate_empty",
            strategy_version=strategy_version,
            matched_categories=hits_by_category,
        )
    frozen = tuple(selected)
    selected_id_set = set(_page_ids(frozen))
    excluded = tuple(page for page in original
                     if (page.get("material_id"), page.get("subject_id"), page.get("page"))
                     not in selected_id_set)
    return ScopeSelection(
        pages=frozen,
        narrowed=True,
        scope_incomplete=True,
        strategy_version=strategy_version,
        matched_categories=tuple(sorted(hits_by_category)),
        fallback_reason=None,
        excluded_page_count=len(original) - len(selected),
        input_fingerprint=_fingerprint(frozen, strategy_version),
        selected_page_ids=_page_ids(frozen),
        excluded_page_ids=_page_ids(excluded),
    )
