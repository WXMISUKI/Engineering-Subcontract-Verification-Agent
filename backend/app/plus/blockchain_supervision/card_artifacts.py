"""P3-01 卡片与现有流程/依据产物的只读生成对账。"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import re

from .cards import resolve_rule_cards
from .rules import load_report_checklist, load_review_flow


def build_card_source_alignment(*, cards: Sequence[Mapping] | None = None,
                                checklist: Sequence[Mapping] | None = None) -> dict:
    cards = list(cards if cards is not None else resolve_rule_cards())
    checklist = list(checklist if checklist is not None else load_report_checklist())
    card_ids = {str(card.get("card_id")) for card in cards}
    checklist_ids = {str(item.get("id")) for item in checklist}
    return {
        "checklist_count": len(checklist_ids),
        "card_count": len(card_ids),
        "missing_cards": sorted(checklist_ids - card_ids),
        "orphan_cards": sorted(card_ids - checklist_ids),
        "pending_source_coordinate": sorted(
            str(card.get("card_id")) for card in cards
            if card.get("resolution_status") != "resolved"
        ),
    }


def build_review_flow_overlay(*, cards: Sequence[Mapping] | None = None,
                              review_flow: Mapping | None = None) -> dict:
    """按条款坐标反向生成步骤→卡片引用，不复制原文。"""
    cards = list(cards if cards is not None else resolve_rule_cards())
    review_flow = review_flow or load_review_flow()
    by_clause: dict[str, list[str]] = {}
    for card in cards:
        clause_id = card.get("clause_id")
        if clause_id:
            text = str(clause_id)
            keys = {text, re.sub(r"\d+$", "", text)}
            by_clause.setdefault(text, []).append(str(card["card_id"]))
            for key in keys - {text}:
                by_clause.setdefault(key, []).append(str(card["card_id"]))
    steps = []
    for step in review_flow.get("steps") or []:
        refs = []
        for basis in step.get("basis") or []:
            ids = by_clause.get(str(basis.get("clause_id")), [])
            refs.extend(ids)
        steps.append({"step_id": step.get("id"),
                      "card_ids": sorted(set(refs))})
    return {"schema_version": "bs-card-review-flow-overlay.v1", "steps": steps}


def build_artifact_report() -> dict:
    cards = resolve_rule_cards()
    alignment = build_card_source_alignment(cards=cards)
    overlay = build_review_flow_overlay(cards=cards)
    return {"status": "pass" if not alignment["missing_cards"] else "fail",
            "alignment": alignment, "review_flow_overlay": overlay}


__all__ = ["build_artifact_report", "build_card_source_alignment", "build_review_flow_overlay"]
