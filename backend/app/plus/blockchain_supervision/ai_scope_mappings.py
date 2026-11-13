"""Explicit, reviewed rule-to-category mappings for MoMA scope selection."""

from __future__ import annotations

_EVIDENCE_KEYWORD_RULES: set[str] = set()


def rule_scope_mapping(item_id: str) -> tuple[tuple[str, ...], dict[str, tuple[str, ...]]] | None:
    """Return an explicit mapping for the small approved initial slice.

    No rule has completed deterministic-kernel and source-linkage review yet.
    Keep every rule on the baseline path until its complete evidence contract is
    explicitly approved; field presence alone is not sufficient.
    """

    if item_id not in _EVIDENCE_KEYWORD_RULES:
        return None
    from .rules import load_transfer_rules
    rule = next((row for row in load_transfer_rules().get("rules", []) if row.get("id") == item_id), None)
    if not rule:
        return None
    required = tuple(rule.get("required_evidence") or ())
    selectors = ((rule.get("scope_detection") or {}).get("evidence_keywords") or {})
    mapping = {
        category: tuple(term for term in selectors.get(category, ()) if isinstance(term, str) and term.strip())
        for category in required
    }
    if not required or any(not mapping.get(category) for category in required):
        return None
    return required, mapping
