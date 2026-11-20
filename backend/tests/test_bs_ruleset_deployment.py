import json

import pytest

from app.plus.blockchain_supervision import ruleset


def test_deployment_fingerprint_accepts_current_ruleset():
    current = ruleset.manifest()
    result = ruleset.verify_deployment_fingerprint(
        expected_version=current["version"],
        expected_manifest_hash=current["raw_manifest_hash"])
    assert result["ok"] is True
    assert result["version"] == current["version"]
    assert {row["name"] for row in result["checks"]} == {"version", "raw_manifest_hash"}


def test_deployment_fingerprint_blocks_mismatched_version_or_hash():
    current = ruleset.manifest()
    version_mismatch = ruleset.verify_deployment_fingerprint(expected_version="stale-version")
    assert version_mismatch["ok"] is False
    assert version_mismatch["checks"][0]["actual"] == current["version"]

    hash_mismatch = ruleset.verify_deployment_fingerprint(expected_manifest_hash="0" * 64)
    assert hash_mismatch["ok"] is False
    assert hash_mismatch["checks"][0]["name"] == "raw_manifest_hash"


def test_deployment_fingerprint_without_expectation_is_read_only_snapshot():
    result = ruleset.verify_deployment_fingerprint()
    assert result["ok"] is True
    assert result["checks"] == []
    assert result["version"]
    assert len(result["raw_manifest_hash"]) == 64


@pytest.mark.parametrize('name', [
    'personnel_adequacy.json', 'negative_list_applicability.json', 'special_operation_catalog.json',
    'safety_permit_scope.json',
    'basis_clauses.json', 'basis_index.json',
])
def test_decision_data_changes_invalidate_frozen_ruleset(tmp_path, monkeypatch, name):
    source = ruleset._DATA_DIR
    for filename in set(ruleset.RULESET_FILES) | {ruleset.RULESET_META_FILE, name}:
        (tmp_path / filename).write_bytes((source / filename).read_bytes())
    monkeypatch.setattr(ruleset, '_DATA_DIR', tmp_path)
    frozen_version = ruleset.current_version()
    data = json.loads((tmp_path / name).read_text(encoding='utf-8'))
    if name == 'personnel_adequacy.json':
        data['legal_safety_minimum']['thresholds'][0]['min'] += 1
    elif name == 'negative_list_applicability.json':
        data['keyword_map']['domains']['bridge'].append('synthetic-bridge-term')
    elif name == 'special_operation_catalog.json':
        data['work_types']['mobile_crane_driver']['accepted_cert_types'].append('synthetic-certificate')
    elif name == 'safety_permit_scope.json':
        data['accepted_industry_scopes'].append('synthetic-industry')
    elif name == 'basis_clauses.json':
        data['clauses'][0]['text'] += 'synthetic-clause-change'
    else:
        data['items']['A2-09']['clause_id'] = 'synthetic-clause-id'
    (tmp_path / name).write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

    result = ruleset.verify_deployment_fingerprint(expected_version=frozen_version)
    assert result['ok'] is False, f'{name} changed without invalidating the frozen version'
    assert result['version'] != frozen_version
