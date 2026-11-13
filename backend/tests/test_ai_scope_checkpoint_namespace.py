from app.plus.blockchain_supervision.service import ai_scope_checkpoint_namespace
from app.plus.blockchain_supervision.ai_guarded_review import _review_batch_checkpoint_key
from app.plus.blockchain_supervision.ai_review_batch import plan_review_batches, to_wire_batch


def test_legacy_or_missing_scope_strategy_uses_baseline_namespace():
    assert ai_scope_checkpoint_namespace(None) == "scope:baseline:disabled"
    assert ai_scope_checkpoint_namespace({}) == "scope:baseline:disabled"
    assert ai_scope_checkpoint_namespace({"scope_strategy": {}}) == "scope:baseline:disabled"


def test_candidate_scope_strategy_is_distinct_from_baseline():
    assert ai_scope_checkpoint_namespace({
        "scope_strategy": {"enabled": True, "version": "category-adjacency-v1"},
    }) == "scope:category-adjacency-v1:enabled"


def test_checkpoint_key_binds_strategy_and_selected_input_fingerprint():
    first = {"items": [{"finding_id": 1, "scope_selection_fingerprint": "a" * 64}]}
    changed_pages = {"items": [{"finding_id": 1, "scope_selection_fingerprint": "b" * 64}]}

    key = _review_batch_checkpoint_key("scope:category-adjacency-v1:enabled", first)

    assert key != _review_batch_checkpoint_key("scope:baseline:disabled", first)
    assert key != _review_batch_checkpoint_key("scope:category-adjacency-v2:enabled", first)
    assert key != _review_batch_checkpoint_key("scope:category-adjacency-v1:enabled", changed_pages)
    assert key == _review_batch_checkpoint_key("scope:category-adjacency-v1:enabled", first)


def test_scope_fingerprint_is_checkpoint_only_and_not_sent_to_model():
    item = {
        "finding_id": 1, "task_id": 1, "item_id": "T15-01", "kind": "transfer_scope",
        "subject_id": None, "limitations": [], "incomplete_material_scope": True,
        "scope_selection": {
            "input_fingerprint": "a" * 64,
            "shadow_evidence": {"occurrences": [{"relation": "labor_scope", "material_id": 2, "page": 1}]},
        },
        "materials": [{"task_id": 1, "subject_id": None, "material_id": 2,
                       "page": 1, "text": "authorized page"}],
        "references": [{"reference_id": "r1", "text": "rule excerpt"}],
    }

    batch = plan_review_batches([item])["batches"][0]
    wire = to_wire_batch(batch)

    assert batch["items"][0]["scope_selection_fingerprint"] == "a" * 64
    assert "scope_selection_fingerprint" not in wire["items"][0]
    assert "scope_selection" not in wire["items"][0]
    assert "shadow_evidence" not in wire["items"][0]
