import hashlib
import json

import pytest

from app.standalone.seed_manifest import SeedManifestError, validate_seed_manifest


def manifest_for(data: bytes, path: str = 'source.pdf') -> dict:
    return {'version': 1, 'documents': [{'path': path, 'sha256': hashlib.sha256(data).hexdigest(), 'collection': 'road'}]}


def test_valid_seed_is_checked_without_modifying_files(tmp_path):
    content = b'synthetic document'
    document = tmp_path / 'source.pdf'
    document.write_bytes(content)
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps(manifest_for(content)), encoding='utf-8')
    result = validate_seed_manifest(manifest, tmp_path)
    assert len(result) == 1
    assert result[0].path == document
    assert document.read_bytes() == content


def test_changed_document_is_rejected(tmp_path):
    (tmp_path / 'source.pdf').write_bytes(b'changed')
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps(manifest_for(b'original')), encoding='utf-8')
    with pytest.raises(SeedManifestError, match='sha256'):
        validate_seed_manifest(manifest, tmp_path)


@pytest.mark.parametrize('relative', ['../outside.pdf', '/absolute.pdf', 'nested/../../outside.pdf', 'C:outside.pdf', 'bad\u0000file'])
def test_paths_cannot_escape_seed_root(tmp_path, relative):
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps(manifest_for(b'data', relative)), encoding='utf-8')
    with pytest.raises(SeedManifestError):
        validate_seed_manifest(manifest, tmp_path)


def test_duplicate_document_is_rejected(tmp_path):
    (tmp_path / 'source.pdf').write_bytes(b'data')
    payload = manifest_for(b'data')
    payload['documents'].append(payload['documents'][0].copy())
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps(payload), encoding='utf-8')
    with pytest.raises(SeedManifestError, match='duplicate'):
        validate_seed_manifest(manifest, tmp_path)
