"""BS-P3-01 规则卡目录：卡片身份单源，原文仍只存于依据库。"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from .rules import load_basis_clauses, load_report_checklist


_DATA_DIR = Path(__file__).with_name("data")
_CARD_PATH = _DATA_DIR / "cards" / "bs_rule_cards.json"


def load_rule_cards() -> dict:
    with _CARD_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def _clause_index(library: dict) -> dict:
    def walk(node: dict, parent: dict):
        yield node["clause_id"], (node, parent)
        for child in node.get("children") or []:
            yield from walk(child, parent)

    return dict(pair for parent in library.get("clauses") or [] for pair in walk(parent, parent))


def resolve_rule_cards(*, as_of=None) -> list[dict]:
    """补齐卡片的标签、来源坐标和原文；未建坐标的卡片明确返回 pending。"""
    catalog = load_rule_cards()
    checklist = {item["id"]: item for item in load_report_checklist()}
    library = load_basis_clauses(as_of=as_of) if as_of is not None else load_basis_clauses()
    index_path = _DATA_DIR / "basis_index.json"
    with index_path.open(encoding="utf-8") as handle:
        basis_index = json.load(handle).get("items") or {}
    clauses = _clause_index(library)
    docs = {doc.get("doc_id"): doc for doc in library.get("docs") or []}
    resolved = []
    for raw in catalog.get("cards") or []:
        card = deepcopy(raw)
        item = checklist.get(card.get("source_item_id"))
        coordinate = card.get("basis_ref") or basis_index.get(card.get("source_item_id")) or {}
        node, parent = clauses.get(coordinate.get("clause_id"), ({}, {}))
        doc = docs.get(coordinate.get("doc_id") or node.get("doc_id") or parent.get("doc_id"))
        selected = node
        if coordinate.get("list_no") is not None:
            selected = next((item for item in node.get("listed_materials") or []
                             if item.get("no") == coordinate.get("list_no")), {})
        card.update({
            "label": item.get("label") if item else None,
            "source": item.get("source") if item else None,
            "clause_id": coordinate.get("clause_id"),
            "doc_id": coordinate.get("doc_id"),
            "source_text": selected.get("text") or node.get("text"),
            "anchor_text": selected.get("text") or node.get("text"),
            "clause": {"page_start": parent.get("page_start"),
                       "page_end": parent.get("page_end")},
            "authority_level": doc.get("authority_level") if doc else None,
            "resolution_status": ("resolved" if node.get("text") and doc else "pending_source_coordinate"),
        })
        resolved.append(card)
    return resolved


def card_catalog_report(*, as_of=None) -> dict:
    cards = resolve_rule_cards(as_of=as_of)
    pending = [card["card_id"] for card in cards if card["resolution_status"] != "resolved"]
    return {"status": "pass" if not pending else "pending",
            "total": len(cards), "resolved": len(cards) - len(pending),
            "pending_source_coordinate": pending, "cards": cards}


__all__ = ["card_catalog_report", "load_rule_cards", "resolve_rule_cards"]
