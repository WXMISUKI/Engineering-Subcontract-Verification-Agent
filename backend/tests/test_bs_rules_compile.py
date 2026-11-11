from app.plus.blockchain_supervision.cards import resolve_rule_cards
from app.plus.blockchain_supervision.rules_compile import compile_card, compile_catalog


def _card(**overrides):
    card = {
        "card_id": "floor-1",
        "source_text": "项目必须提交完整材料",
        "anchor_text": "必须提交完整材料",
        "clause_id": "DOC#1",
        "clause": {"page_start": 1, "page_end": 1},
        "statuses": ["hit", "miss", "undecidable"],
        "items": ["contract", "roster"],
    }
    card.update(overrides)
    return card


def test_compile_card_passes_all_gates():
    result = compile_card(_card())
    assert result["status"] == "accepted"
    assert len(result["gates"]) == 7
    assert all(gate["status"] == "pass" for gate in result["gates"])


def test_compile_card_rejects_bad_anchor_and_duplicate():
    result = compile_card(_card(anchor_text="不存在", card_id="floor-1"),
                          existing_cards=[_card(card_id="floor-1")])
    assert result["status"] == "rejected"
    assert {"anchor_not_found", "card_id_duplicate"} <= set(result["errors"])


def test_compile_card_requires_human_compare_for_unknown_floor_relation():
    candidate = _card(card_id="override-1", text_posture="may")
    floor = _card(card_id="floor-1", text_posture="must")
    result = compile_card(candidate, floor_card=floor)
    assert result["status"] == "pending_human_compare"
    assert result["relation_to_floor"]["action"] == "pending_human_compare"


def test_compile_card_rejects_below_floor_numeric_value():
    floor = _card(threshold={"value": 30, "direction": "min", "unit": "%"})
    candidate = _card(card_id="override-1", threshold={"value": 20, "direction": "min", "unit": "%"})
    result = compile_card(candidate, floor_card=floor)
    assert result["status"] == "rejected"
    assert result["relation_to_floor"]["relation"] == "below_floor"


def test_compile_catalog_uses_current_source_cards():
    result = compile_catalog(resolve_rule_cards())
    assert result["total"] == 40
    assert result["counts"]["accepted"] == 40
    assert result["counts"].get("rejected", 0) == 0


def test_compile_catalog_rejects_duplicate_card_ids():
    result = compile_catalog([_card(card_id="duplicate-1"), _card(card_id="duplicate-1")])

    assert result["status"] == "fail"
    assert result["counts"]["rejected"] == 2
    assert all("card_id_duplicate" in report["errors"] for report in result["reports"])


def test_compile_card_keeps_unprovable_floor_value_pending_for_human_compare():
    floor = _card(card_id="floor-1", threshold={"value": 30, "direction": "min", "unit": "%"})
    candidate = _card(card_id="candidate-1", threshold={"value": 30, "direction": "min", "unit": "人"})

    result = compile_card(candidate, floor_card=floor)

    assert result["status"] == "pending_human_compare"
    assert result["relation_to_floor"]["relation"] == "unknown"
    assert "value_comparison_unknown" in result["relation_to_floor"]["reasons"]
