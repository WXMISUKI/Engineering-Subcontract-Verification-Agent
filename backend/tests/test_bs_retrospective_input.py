"""双合同门槛的HTTP回归；独立SQLite与模拟模型，不触发付费调用。"""
import asyncio

import pytest
from sqlalchemy.orm import Session

from app.plus.blockchain_supervision import retrospective_runtime as runtime
from app.plus.blockchain_supervision.models import BsAiReviewJob, BsMaterial, BsTask, BsTaskEvent
from test_bs_report_version_http import _client
from test_bs_retrospective_runtime import Client


def setup(tmp_path, monkeypatch, roles):
    http, engine, task_id = _client(tmp_path)
    from app.plus.startup_conditions_check import llm
    from app.services.billing import bindings
    client = Client()
    monkeypatch.setattr(runtime, 'schedule', lambda *args: False)
    monkeypatch.setattr(runtime, 'freeze_basis', lambda: [])
    monkeypatch.setattr(runtime, '_file_hash', lambda m: str(m.id).zfill(64))
    monkeypatch.setattr(bindings, 'effective_model_code_or_default', lambda *args: 'synthetic')
    monkeypatch.setattr(llm, 'client_from_env', lambda **kwargs: client)
    with Session(engine) as db:
        db.get(BsTask, task_id).options_snapshot_json = {'review_mode': 'contract_retrospective'}
        for role in roles:
            db.add(BsMaterial(task_id=task_id, file_name=role + '.pdf', role=role, pages=1,
                parse_state='done', degradations_json=[], pages_json=[{'page': 1,
                    'text': '签订日期：2023年12月22日。工期延长的费用可另行协商。工期延长费用均不调整。'}]))
        db.commit()
        ids = [m.id for m in db.query(BsMaterial).filter_by(task_id=task_id).order_by(BsMaterial.id)]
    return http, engine, task_id, ids, client


@pytest.mark.parametrize('roles,missing', [([], '总包'), (['sub'], '总包'), (['general'], '分包'),
                                         (['sub', 'sub'], '总包'), (['general', 'general'], '分包')])
def test_missing_side_cannot_be_bypassed_and_does_not_create_job(tmp_path, monkeypatch, roles, missing):
    http, engine, tid, _, model = setup(tmp_path, monkeypatch, roles)
    base = f'/api/plus/blockchain-supervision/tasks/{tid}'
    validation = http.get(base).json()['validation']
    assert validation['hard_ok'] is False
    assert any(missing in gap for gap in validation['hard_gaps'])
    response = http.post(base + '/retrospective/start', json={})
    assert response.status_code == 409 and missing in response.text
    with Session(engine) as db:
        assert db.get(BsTask, tid).state == 'setup'
        assert db.query(BsAiReviewJob).count() == 0
        assert db.query(BsTaskEvent).filter_by(event_type=runtime.SPEC).count() == 0
    assert model.calls == 0


@pytest.mark.parametrize('state', ['failed', 'parsing', 'uploading', 'stored'])
def test_unready_general_blocks_start(tmp_path, monkeypatch, state):
    http, engine, tid, ids, _ = setup(tmp_path, monkeypatch, ['general', 'labor'])
    with Session(engine) as db:
        db.get(BsMaterial, ids[0]).parse_state = state
        db.commit()
    assert http.post(f'/api/plus/blockchain-supervision/tasks/{tid}/retrospective/start', json={}).status_code == 409


def test_relations_persist_and_source_set_uses_existing_endpoints(tmp_path, monkeypatch):
    http, engine, tid, ids, _ = setup(tmp_path, monkeypatch, ['general', 'general', 'sub', 'sub'])
    base = f'/api/plus/blockchain-supervision/tasks/{tid}'
    relations = [{'material_id': mid, 'relation': 'main' if i in (0, 2) else 'attachment'}
                 for i, mid in enumerate(ids)]
    assert http.patch(base + '/retrospective/contracts', json={'relations': relations}).status_code == 200
    assert not http.get(base).json()['validation']['hard_ok']  # 总包尚未关联
    assert http.post(base + '/general-source-sets', json={'material_ids': ids[:2]}).status_code == 200
    detail = http.get(base).json()
    assert detail['validation']['hard_ok']
    assert detail['validation']['contract_relations'] == relations
    accepted = http.post(base + '/retrospective/start', json={})
    assert accepted.status_code == 202, accepted.text
    with Session(engine) as db:
        spec = db.query(BsTaskEvent).filter_by(task_id=tid, event_type=runtime.SPEC).one().payload_json
        assert len(spec['materials']) == 4
        assert {m['role'] for m in spec['materials']} == {'general', 'sub'}
        assert spec['materials'][0]['general_source_set_id']
        assert spec['materials'][0]['general_source_set_version'] == 1
        assert spec['relations'] == relations
        assert all(m['file_sha256'] and 'revision_no' in m for m in spec['materials'])
    assert http.patch(base + '/retrospective/contracts', json={'relations': relations}).status_code == 409
    assert http.post(base + f'/materials/{ids[0]}/role', json={'role': 'sub'}).status_code == 409
    assert http.delete(base + f'/materials/{ids[0]}/general-source-set').status_code == 409


def test_role_correction_and_invalid_relationships(tmp_path, monkeypatch):
    http, _, tid, ids, _ = setup(tmp_path, monkeypatch, ['sub', 'sub'])
    base = f'/api/plus/blockchain-supervision/tasks/{tid}'
    assert http.post(base + f'/materials/{ids[0]}/role', json={'role': 'general'}).status_code == 200
    assert http.get(base).json()['validation']['hard_ok']
    assert http.post(base + f'/materials/{ids[0]}/role', json={'role': 'packet'}).status_code == 400
    for relations in ([{'material_id': ids[0], 'relation': 'main'}] * 2,
                      [{'material_id': 999999, 'relation': 'main'}],
                      [{'material_id': ids[0], 'relation': 'unknown'}]):
        assert http.patch(base + '/retrospective/contracts', json={'relations': relations}).status_code == 409
        assert http.post(base + '/retrospective/start', json={'relations': relations}).status_code == 409


def test_accepted_dual_sources_are_frozen_and_mutation_stops_before_provider(tmp_path, monkeypatch):
    http, engine, tid, ids, model = setup(tmp_path, monkeypatch, ['general', 'sub'])
    base = f'/api/plus/blockchain-supervision/tasks/{tid}'
    accepted = http.post(base + '/retrospective/start', json={})
    assert accepted.status_code == 202
    with Session(engine) as db:
        job_id = db.query(BsAiReviewJob).one().id
        db.get(BsMaterial, ids[0]).revision_no = 9
        db.commit()
    asyncio.run(runtime.run(engine, job_id))
    assert model.calls == 0
    assert http.get(base + '/retrospective').json()['state'] == 'failed'


def test_relation_write_requires_owner(tmp_path, monkeypatch):
    http, _, tid, ids, _ = setup(tmp_path, monkeypatch, ['general', 'sub'])
    http.post('/api/login', json={'username': 'bob', 'password': 'Bob-pass-123'})
    assert http.patch(f'/api/plus/blockchain-supervision/tasks/{tid}/retrospective/contracts',
                      json={'relations': [{'material_id': ids[0], 'relation': 'main'}]}).status_code == 403
