"""Non-mutating structured evidence shadow projection for eligible A1 rules."""

from __future__ import annotations

import hashlib
import json
import re

from . import a1_check, rules


_SHADOW_STRATEGY = "t15-05-evidence-graph-shadow-v1"
_EVIDENCE_ROLES = {"general", "sub", "labor", "packet"}


def _fallback(reason: str, pages: list[dict], rule_fingerprint: str | None = None) -> dict:
    return {
        "rule_id": "T15-05",
        "strategy_version": _SHADOW_STRATEGY,
        "rule_fingerprint": rule_fingerprint,
        "narrowing_eligible": False,
        "fallback_reason": reason,
        "input_fingerprint": _fingerprint(pages, [], rule_fingerprint),
        "occurrences": [],
        "positive_scope_relation_count": 0,
        "counter_scope_relation_count": 0,
    }


def _fingerprint(pages: list[dict], role_rows: list[dict], rule_fingerprint: str | None = None) -> str:
    identities = []
    for page in pages:
        if not isinstance(page, dict):
            identities.append({"invalid_page_type": type(page).__name__})
            continue
        text = page.get("text")
        identities.append({
            "task_id": page.get("task_id"),
            "subject_id": page.get("subject_id"),
            "material_id": page.get("material_id"),
            "page": page.get("page"),
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()
            if isinstance(text, str) else None,
        })
    payload = {
        "strategy": _SHADOW_STRATEGY,
        "rule_fingerprint": rule_fingerprint,
        "pages": sorted(identities, key=lambda row: (
            str(row.get("task_id")), str(row.get("subject_id")),
            str(row.get("material_id")), str(row.get("page")),
            str(row.get("invalid_page_type")))),
        "roles": sorted(role_rows, key=lambda row: row["material_id"]),
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _page_text_index(materials: list) -> dict[tuple[int, int], str]:
    result = {}
    for material in materials:
        for page_no, text in a1_check._norm_pages(material):
            key = (material.id, page_no)
            if key in result:
                raise ValueError("duplicate_readable_page")
            result[key] = text
    return result


def _word_occurrences(text: str, words: list[str]):
    for word_index, word in enumerate(words):
        if not isinstance(word, str) or not word:
            continue
        start = 0
        while True:
            start = text.find(word, start)
            if start < 0:
                break
            yield word_index, start, start + len(word)
            start += max(1, len(word))


def _pattern_occurrences(text: str, patterns: list[str]):
    for pattern_index, source in enumerate(patterns):
        if not isinstance(source, str) or not source:
            continue
        pattern = re.compile(source)
        for match_index, match in enumerate(pattern.finditer(text)):
            yield pattern_index, match_index, match.start(), match.end()


def build_t15_05_shadow_graph(*, eligible_pages: list[dict], materials: list) -> dict:
    """Enumerate rule-defined T15-05 evidence locators without changing AI input.

    All page text must be the exact readable page supplied by the current context.
    The result intentionally remains shadow-only until rule linkage is reviewed.
    """
    pages = list(eligible_pages or [])
    if not pages:
        return _fallback("empty_eligible_context", pages)

    identities = set()
    for page in pages:
        if (not isinstance(page, dict)
                or type(page.get("task_id")) is not int
                or (page.get("subject_id") is not None and type(page.get("subject_id")) is not int)
                or type(page.get("material_id")) is not int
                or type(page.get("page")) is not int or page["page"] < 1
                or not isinstance(page.get("text"), str) or not page["text"].strip()):
            return _fallback("invalid_eligible_page", pages)
        identity = (page["task_id"], page.get("subject_id"), page["material_id"], page["page"])
        if identity in identities:
            return _fallback("duplicate_page_identity", pages)
        identities.add(identity)

    by_material = {}
    for material in materials:
        if type(getattr(material, "id", None)) is not int:
            continue
        by_material.setdefault(material.id, []).append(material)
    resolved, role_rows = {}, []
    for page in pages:
        matches = by_material.get(page["material_id"], [])
        if len(matches) != 1:
            return _fallback("page_source_unresolved", pages)
        material = matches[0]
        if (getattr(material, "parse_state", None) != "done"
                or getattr(material, "role", None) not in _EVIDENCE_ROLES
                or getattr(material, "task_id", page["task_id"]) != page["task_id"]
                or getattr(material, "subject_id", page.get("subject_id")) != page.get("subject_id")):
            return _fallback("page_source_identity_mismatch", pages)
        resolved.setdefault(material.id, material)
        role_rows.append({"material_id": material.id, "role": material.role,
                          "subject_id": page.get("subject_id")})

    try:
        normalized = _page_text_index(list(resolved.values()))
    except (TypeError, ValueError):
        return _fallback("page_source_unreadable", pages)

    ruleset = rules.load_transfer_rules()
    rule_matches = [row for row in ruleset.get("rules", []) if row.get("id") == "T15-05"]
    if len(rule_matches) != 1:
        return _fallback("rule_definition_unavailable", pages)
    rule = rule_matches[0]
    detection = rule.get("relations_detection") or {}
    rule_identity = json.dumps({
        "id": rule.get("id"), "required_evidence": rule.get("required_evidence"),
        "relations_detection": detection,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    rule_fingerprint = hashlib.sha256(rule_identity.encode("utf-8")).hexdigest()
    required_categories = set(rule.get("required_evidence") or [])
    if (required_categories != {"labor_scope", "whole_project_scope", "management_fee", "whole_project_payment"}
            or any(not detection.get(key) for key in (
                "labor_subject_words", "labor_scope_patterns", "labor_whole_scope_patterns",
                "whole_scope_patterns", "whole_scope_negation_patterns", "management_fee_words",
                "whole_payment_patterns", "labor_payment_patterns"))):
        return _fallback("rule_evidence_contract_incomplete", pages, rule_fingerprint)

    for page in pages:
        source_text = normalized.get((page["material_id"], page["page"]))
        expected_text = a1_check._normalize_amount_text(
            re.sub(r"\s+", "", page["text"]))
        if source_text is None or source_text != expected_text:
            return _fallback("page_text_does_not_match_source", pages, rule_fingerprint)

    occurrences = []
    selectors = (
        ("labor_scope", "words", detection.get("labor_subject_words"), {"general"}, "supporting"),
        ("labor_scope", "patterns", detection.get("labor_scope_patterns"), {"general"}, "supporting"),
        ("labor_whole_scope", "patterns", detection.get("labor_whole_scope_patterns"),
         {"general"}, "supporting"),
        ("whole_project_scope", "patterns", detection.get("whole_scope_patterns"),
         {"general"}, "supporting"),
        ("whole_scope_negation", "patterns", detection.get("whole_scope_negation_patterns"),
         {"general"}, "counter"),
        ("management_fee", "words", detection.get("management_fee_words"),
         _EVIDENCE_ROLES, "supporting"),
        ("whole_project_payment", "patterns", detection.get("whole_payment_patterns"),
         _EVIDENCE_ROLES, "supporting"),
        ("labor_payment", "patterns", detection.get("labor_payment_patterns"),
         _EVIDENCE_ROLES, "supporting"),
    )
    eligible_keys = {(page["material_id"], page["page"]): page for page in pages}
    try:
        for relation, selector_kind, selectors_for_relation, allowed_roles, polarity in selectors:
            selectors_for_relation = selectors_for_relation or []
            for (material_id, page_no), page in eligible_keys.items():
                material = resolved[material_id]
                if material.role not in allowed_roles:
                    continue
                text = normalized[(material_id, page_no)]
                if selector_kind == "words":
                    matches = ((selector_index, 0, start, end)
                               for selector_index, start, end in _word_occurrences(text, selectors_for_relation))
                else:
                    matches = _pattern_occurrences(text, selectors_for_relation)
                for selector_index, match_index, start, end in matches:
                    occurrences.append({
                        "relation": relation,
                        "polarity": polarity,
                        "selector_kind": selector_kind,
                        "selector_index": selector_index,
                        "match_index": match_index,
                        "task_id": page["task_id"],
                        "subject_id": page.get("subject_id"),
                        "material_id": material_id,
                        "material_role": material.role,
                        "page": page_no,
                        "start": start,
                        "end": end,
                    })
    except (re.error, TypeError, ValueError):
        return _fallback("rule_selector_error", pages, rule_fingerprint)
    occurrences.sort(key=lambda row: (
        row["material_id"], row["page"], row["start"], row["end"], row["relation"],
        row["selector_index"], row["match_index"],
    ))
    source_roles = tuple(sorted({
        material.role for material in materials
        if getattr(material, "task_id", None) in {page["task_id"] for page in pages}
        and getattr(material, "role", None) in _EVIDENCE_ROLES
    }))
    readable_roles = tuple(sorted({row["role"] for row in role_rows}))
    missing_source_roles = tuple(role for role in ("general",) if role not in source_roles)
    missing_readable_roles = tuple(role for role in ("general",) if role not in readable_roles)
    return {
        "rule_id": "T15-05",
        "strategy_version": _SHADOW_STRATEGY,
        "rule_fingerprint": rule_fingerprint,
        "narrowing_eligible": False,
        "fallback_reason": "shadow_only_not_reviewed",
        "input_fingerprint": _fingerprint(pages, role_rows, rule_fingerprint),
        "role_coverage": {
            "present_source_roles": source_roles,
            "missing_source_roles": missing_source_roles,
            "readable_page_roles": readable_roles,
            "missing_readable_page_roles": missing_readable_roles,
            "complete": not missing_source_roles and not missing_readable_roles,
        },
        "occurrences": occurrences,
        "positive_scope_relation_count": sum(
            row["relation"] == "labor_whole_scope" for row in occurrences),
        "counter_scope_relation_count": sum(
            row["relation"] == "whole_scope_negation" for row in occurrences),
    }
