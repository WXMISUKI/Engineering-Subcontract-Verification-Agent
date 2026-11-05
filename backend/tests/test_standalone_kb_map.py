import pytest
import json

from app.plus.blockchain_supervision.knowledge_config import parse_bs_kb_map


def test_standalone_knowledge_map_accepts_only_subcontract_collections():
    assert parse_bs_kb_map('{"bs_road_national_basis":"new-kb"}') == {
        'bs_road_national_basis': 'new-kb'
    }
    with pytest.raises(ValueError):
        parse_bs_kb_map('{"sc_structural-check_basis":"other-kb"}')


def test_runtime_uses_seed_mapping_for_collection_routing(monkeypatch, tmp_path):
    from app.plus.blockchain_supervision.knowledge import _configured_kb_ids
    mapping = tmp_path / 'mapping.json'
    mapping.write_text(json.dumps({'version': 1, 'documents': [
        {'sha256': 'a' * 64, 'collection': 'road_national_basis', 'kb_id': 'national-kb', 'knowledge_id': 'a'},
        {'sha256': 'b' * 64, 'collection': 'road_group_basis', 'kb_id': 'group-kb', 'knowledge_id': 'b'}]}))
    monkeypatch.setenv('BS_WEKNORA_KB_MAP', '')
    monkeypatch.setenv('BS_REFERENCE_MAPPING_FILE', str(mapping))
    assert _configured_kb_ids(('road_national_basis',)) == {'national-kb'}
    assert _configured_kb_ids(('road_group_basis',)) == {'group-kb'}


def test_default_route_does_not_expand_when_only_group_library_is_mapped(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from app.plus.blockchain_supervision.knowledge import _route_targets
    mapping = tmp_path / 'mapping.json'
    mapping.write_text(json.dumps({'version': 1, 'documents': [
        {'sha256': 'a' * 64, 'collection': 'road_group_basis', 'kb_id': 'group-kb', 'knowledge_id': 'doc'}]}))
    monkeypatch.setenv('BS_WEKNORA_KB_MAP', '')
    monkeypatch.setenv('BS_REFERENCE_MAPPING_FILE', str(mapping))
    assert _route_targets([SimpleNamespace(kb_id='other-kb', kb_name='other')], 'road_default') == []
