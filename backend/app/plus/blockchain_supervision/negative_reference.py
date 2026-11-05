"""Match a prohibition against already-authorized, same-file source chunks.

The caller must filter chunk type and verify current authorization and original
fingerprint before calling. This module does not establish legal applicability.
"""
from __future__ import annotations


def _span(text: str, expected: str, *, after: int = 0) -> tuple[int, int] | None:
    positions = [i for i, ch in enumerate(text) if not ch.isspace()]
    compact = "".join(text[i] for i in positions)
    needle = "".join(expected.split())
    start = compact.find(needle) if needle else -1
    if start < 0 or compact.find(needle, start + 1) >= 0:
        return None
    begin, end = positions[start], positions[start + len(needle) - 1] + 1
    return (begin, end) if begin >= after else None


def match_negative_fragments(rows: list[dict], rule: dict, preamble: str) -> list[dict] | None:
    if rule.get("kind") != "prohibited" or not isinstance(preamble, str) or not preamble:
        return None
    if not rows:
        return None
    keys = ("provider_id", "binding_id", "kb_id", "knowledge_id")
    identity = tuple(rows[0].get(key) for key in keys)
    if any(value is None or value == "" for value in identity):
        return None
    if any(tuple(row.get(key) for key in keys) != identity or not row.get("id")
           or not isinstance(row.get("document"), str) for row in rows):
        return None
    parent, leaf = rule.get("parent_context") or "", rule.get("text") or ""
    if not isinstance(parent, str) or not isinstance(leaf, str) or not leaf:
        return None
    if any(not isinstance(exception, str) or not exception or exception not in (parent + leaf)
           for exception in rule.get("exceptions") or []):
        return None
    prefix = None
    item = None
    parent_part = None
    for row in rows:
        text = row["document"]
        pspan = _span(text, preamble)
        if pspan and prefix is None:
            prefix = {"role": "preamble", "chunk_id": row["id"], "quote": text[pspan[0]:pspan[1]]}
        parent_span = _span(text, parent) if parent else None
        if parent and not parent_span:
            continue
        leaf_span = _span(text, leaf, after=parent_span[1] if parent_span else 0)
        if leaf_span and item is None:
            if parent_span:
                parent_part = {"role": "parent", "chunk_id": row["id"],
                               "quote": text[parent_span[0]:parent_span[1]]}
            item = {"role": "item", "chunk_id": row["id"], "quote": text[leaf_span[0]:leaf_span[1]]}
    return [prefix, *([parent_part] if parent_part else []), item] if prefix and item else None
