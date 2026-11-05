"""规则卡七道校验闸门（纯函数，不写库、不上传）。"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from ..admission import relation_to_floor, validate_card_shape


GATES = (
    "source_text_anchor",
    "unit",
    "three_state",
    "numeric_direction",
    "clause_coordinate",
    "duplicate",
    "floor_relation",
)


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _gate(name: str, errors: list[str] | None = None, *, status: str = "pass") -> dict:
    return {"name": name, "status": status, "errors": list(errors or [])}


def compile_card(card: Mapping, *, existing_cards: Sequence[Mapping] = (),
                 floor_card: Mapping | None = None) -> dict:
    """编译一张候选卡并返回每道闸门的可审计结果。"""
    shape_errors = validate_card_shape(card)
    source = _compact(card.get("source_text"))
    anchor = _compact(card.get("anchor_text") or card.get("source_text"))
    gates = []
    gates.append(_gate("source_text_anchor", ["anchor_not_found"]
                       if source and anchor not in source else []))

    threshold = card.get("threshold")
    gates.append(_gate("unit", ["unit_missing"] if isinstance(threshold, Mapping)
                       and not str(threshold.get("unit") or "").strip() else []))
    statuses = set(card.get("statuses") or [])
    gates.append(_gate("three_state", ["three_state_incomplete"]
                       if statuses != {"hit", "miss", "undecidable"} else []))
    numeric_errors = []
    if isinstance(threshold, Mapping):
        if not isinstance(threshold.get("value"), (int, float)) or isinstance(threshold.get("value"), bool):
            numeric_errors.append("numeric_value_missing")
        if threshold.get("direction") not in {"min", "max", "eq"}:
            numeric_errors.append("direction_missing")
    gates.append(_gate("numeric_direction", numeric_errors))

    coordinate = card.get("clause") or {}
    start, end = coordinate.get("page_start"), coordinate.get("page_end")
    coordinate_errors = []
    if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
        coordinate_errors.append("clause_coordinate_invalid")
    gates.append(_gate("clause_coordinate", coordinate_errors))

    ids = {str(item.get("card_id")) for item in existing_cards if isinstance(item, Mapping)}
    gates.append(_gate("duplicate", ["card_id_duplicate"]
                       if str(card.get("card_id")) in ids else []))

    relation = None
    if floor_card is None:
        floor_gate = _gate("floor_relation")
    else:
        relation = relation_to_floor(floor_card, card)
        floor_gate = _gate(
            "floor_relation",
            relation.get("reasons") or [],
            status=("fail" if relation["relation"] == "below_floor"
                    else "pending" if relation["relation"] == "unknown" else "pass"),
        )
    gates.append(floor_gate)

    errors = [error for gate in gates if gate["status"] != "pending"
              for error in gate["errors"]]
    errors.extend(shape_errors)
    if any(gate["status"] == "fail" for gate in gates) or errors:
        status = "rejected"
    elif any(gate["status"] == "pending" for gate in gates):
        status = "pending_human_compare"
    else:
        status = "accepted"
    return {"status": status, "card": dict(card), "gates": gates,
            "errors": sorted(set(errors)), "relation_to_floor": relation}


def compile_catalog(cards: Sequence[Mapping], *, floor_cards: Sequence[Mapping] = ()) -> dict:
    """批量编译卡片，重复卡片和未解析来源在同一报告中显式列出。"""
    cards = list(cards or [])
    floor_by_id = {str(card.get("card_id")): card for card in floor_cards
                   if isinstance(card, Mapping)}
    reports = []
    for card in cards:
        floor = floor_by_id.get(str(card.get("floor_card_id")))
        reports.append(compile_card(
            card,
            existing_cards=[other for other in cards if other is not card],
            floor_card=floor,
        ))
    counts = {}
    for report in reports:
        counts[report["status"]] = counts.get(report["status"], 0) + 1
    return {"status": "pass" if counts.get("rejected", 0) == 0 else "fail",
            "total": len(reports), "counts": counts, "reports": reports}


__all__ = ["GATES", "compile_card", "compile_catalog"]
