import json
import pytest
from app.standalone.reference_mapping import MappingError, apply_reference_mapping


def test_mapping_replaces_every_old_id_by_document_hash(tmp_path):
    path = tmp_path / 'mapping.json'
    path.write_text(json.dumps({'version': 1, 'documents': [
        {'sha256': 'a' * 64, 'kb_id': 'new-kb', 'knowledge_id': 'new-doc'}]}), encoding='utf-8')
    original = {'sources': [{'original_sha256': 'a' * 64, 'kb_id': 'old', 'knowledge_id': 'old-doc'}]}
    result = apply_reference_mapping(original, path)
    assert result['sources'][0]['kb_id'] == 'new-kb'
    assert result['sources'][0]['knowledge_id'] == 'new-doc'
    assert original['sources'][0]['kb_id'] == 'old'


def test_missing_or_conflicting_mapping_fails_closed(tmp_path):
    package = {'sources': [{'original_sha256': 'a' * 64, 'kb_id': 'old', 'knowledge_id': 'old-doc'}]}
    with pytest.raises(MappingError):
        apply_reference_mapping(package, tmp_path / 'missing.json')
    path = tmp_path / 'mapping.json'
    path.write_text(json.dumps({'version': 1, 'documents': [
        {'sha256': 'a' * 64, 'kb_id': 'one', 'knowledge_id': 'one'},
        {'sha256': 'a' * 64, 'kb_id': 'two', 'knowledge_id': 'two'}]}), encoding='utf-8')
    with pytest.raises(MappingError):
        apply_reference_mapping(package, path)


def test_runtime_reference_package_never_uses_old_ids(monkeypatch, tmp_path):
    from app.plus.blockchain_supervision.knowledge_runs import load_reference_package
    monkeypatch.delenv('BS_REFERENCE_MAPPING_FILE', raising=False)
    with pytest.raises(MappingError):
        load_reference_package()
    from pathlib import Path
    source = json.loads((Path(__file__).parent.parent / 'app/plus/blockchain_supervision/data/reference_package.json').read_text(encoding='utf-8'))
    hashes = {row['original_sha256'] for row in source['sources']}
    mapping = {'version': 1, 'documents': [{'sha256': digest, 'kb_id': 'target-kb', 'knowledge_id': 'target-' + digest} for digest in hashes]}
    path = tmp_path / 'mapping.json'
    path.write_text(json.dumps(mapping), encoding='utf-8')
    monkeypatch.setenv('BS_REFERENCE_MAPPING_FILE', str(path))
    package = load_reference_package()
    assert package['sources'] and all(row['kb_id'] == 'target-kb' for row in package['sources'])


def test_group_selection_uses_target_mapping_not_historical_id(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from app.plus.blockchain_supervision import knowledge
    entry = {'document_no': '沪城公路〔2025〕118号', 'selection': 'selected',
             'target_collection': 'road_group_basis', 'sha256': 'a' * 64,
             'ingest_record': {'kb_id': 'old-kb', 'knowledge_id': 'old-doc'}}
    monkeypatch.setattr(knowledge.source_manifest, 'load_registered_manifest', lambda: {'files': [entry]})
    monkeypatch.setattr(knowledge, '_route_targets', lambda targets, profile: targets)
    path = tmp_path / 'mapping.json'
    path.write_text(json.dumps({'version': 1, 'documents': [
        {'sha256': 'a' * 64, 'kb_id': 'new-kb', 'knowledge_id': 'new-doc'}]}), encoding='utf-8')
    monkeypatch.setenv('BS_REFERENCE_MAPPING_FILE', str(path))
    target = SimpleNamespace(kb_id='new-kb', knowledge_ids=['new-doc'])
    result = knowledge._confirmed_group_file_targets([target], {'kb_ids': ['new-kb'], 'knowledge_ids': ['new-doc']})
    assert result == [target]
    assert knowledge._confirmed_group_file_targets([target], {'kb_ids': ['old-kb'], 'knowledge_ids': ['old-doc']}) == []
