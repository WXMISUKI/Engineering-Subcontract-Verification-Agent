"""独立入口的合成材料业务流；不连接外部模型或知识服务。"""
import json
import time
from pathlib import Path

import fitz
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.plus.blockchain_supervision.models import BsMaterial

from app.standalone.identity import Base, create_account
from app.standalone.main import create_app
from app.plus.blockchain_supervision import parsing, service, reference_snapshots, negative_candidate_plan


def test_uploaded_pdf_starts_and_runs_checks_with_unavailable_knowledge(tmp_path, monkeypatch):
    monkeypatch.setenv('SECRET_KEY', 'synthetic-secret-key-0123456789-abcdefgh')
    reference = Path(__file__).parent.parent / 'app/plus/blockchain_supervision/data/reference_package.json'
    sources = json.loads(reference.read_text(encoding='utf-8'))['sources']
    mapping = {'version': 1, 'documents': [
        {'sha256': digest, 'kb_id': 'synthetic-kb', 'knowledge_id': 'synthetic-' + digest,
         'collection': 'road_national_basis'}
        for digest in {row['original_sha256'] for row in sources}
    ]}
    mapping_path = tmp_path / 'mapping.json'
    mapping_path.write_text(json.dumps(mapping), encoding='utf-8')
    monkeypatch.setenv('BS_REFERENCE_MAPPING_FILE', str(mapping_path))
    monkeypatch.setattr(parsing, 'UPLOADS_DIR', str(tmp_path / 'uploads'))
    engine = create_engine('sqlite:///' + (tmp_path / 'business.sqlite').as_posix(),
                           connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    create_account(engine, 'alice', 'Alice-pass-123')
    client = TestClient(create_app(engine, 'x' * 64, cookie_secure=False))
    assert client.post('/api/login', json={'username': 'alice', 'password': 'Alice-pass-123'}).status_code == 200
    conversation = client.post('/api/conversations').json()['id']
    base = '/api/plus/blockchain-supervision'
    created = client.post(f'{base}/tasks', json={'conversation_id': conversation, 'title': 'synthetic',
                                                  'scope_a1': True, 'scope_a3': True})
    assert created.status_code == 200, created.text
    task_id = created.json()['task_id']
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), 'SYNTHETIC CONTRACT ' * 8)
    pdf = doc.tobytes()
    doc.close()
    uploaded = client.post(f'{base}/tasks/{task_id}/materials/upload',
                           files={'file': ('synthetic.pdf', pdf, 'application/pdf')}, data={'role': 'labor'})
    assert uploaded.status_code == 200, uploaded.text
    detail = client.get(f'{base}/tasks/{task_id}').json()
    subjects = client.get(f'{base}/tasks/{task_id}/subjects').json()['subjects']
    assert subjects
    confirmed = client.patch(f"{base}/tasks/{task_id}/subjects/{subjects[0]['subject_id']}/type",
                             json={'subject_type': 'labor'})
    assert confirmed.status_code == 200, confirmed.text
    named = client.patch(f"{base}/tasks/{task_id}/subjects/{subjects[0]['subject_id']}/name",
                         json={'name': '合成测试单位'})
    assert named.status_code == 200, named.text
    started = client.post(f'{base}/tasks/{task_id}/start')
    assert started.status_code in (200, 202), started.text
    for _ in range(200):
        detail = client.get(f'{base}/tasks/{task_id}').json()
        if detail['state'] != 'preparing':
            break
        time.sleep(.05)
    assert detail['state'] == 'running', detail
    with Session(engine) as db:
        original = db.query(BsMaterial).filter_by(task_id=task_id).one()
        assert original.parse_state == 'done', original.degradations_json
        assert 'SYNTHETIC CONTRACT' in original.pages_json[0]['text']
    endpoints = {'completeness': 'check', 'signature_chain': 'check/a3-signature',
                 'a1': 'check/a1', 'a2': 'check/a2', 'internal_records': 'check/internal-records'}
    for suffix in endpoints.values():
        response = client.post(f'{base}/tasks/{task_id}/{suffix}')
        assert response.status_code == 200, (suffix, response.text)
    findings = client.get(f'{base}/tasks/{task_id}/findings')
    assert findings.status_code == 200, findings.text
    rows = findings.json()['findings']
    assert rows and any(row['status'] == 'undecidable' for row in rows)
    candidate = next(row for row in rows if row['status'] == 'undecidable')
    reviewed = client.patch(f"{base}/findings/{candidate['id']}/review",
                            json={'decision': 'accepted', 'note': 'synthetic review'})
    assert reviewed.status_code == 200, reviewed.text
    report = client.get(f'{base}/tasks/{task_id}/report?stage=draft')
    assert report.status_code == 200, report.text
    assert report.json()['sections']
    exported = client.get(f'{base}/tasks/{task_id}/report/export?fmt=docx&stage=draft')
    assert exported.status_code == 200, exported.text
    assert exported.content.startswith(b'PK')
    confirmed_report = client.get(f'{base}/tasks/{task_id}/report?stage=confirmed')
    assert confirmed_report.status_code == 409, confirmed_report.text
    assert confirmed_report.json()['detail']['code'] == 'pending_undecidable_review'
    completed = client.post(f'{base}/tasks/{task_id}/complete')
    assert completed.status_code == 409, completed.text
    plan = client.post(f'{base}/tasks/{task_id}/supplement-plan', json={
        'generation_id': 'synthetic-supplement', 'category': 'submission_packet',
        'item_ids': ['RC-A01'], 'subject_ids': [subjects[0]['subject_id']],
    })
    assert plan.status_code == 200, plan.text
    assert plan.json()['supplement_plan']['status'] == 'planned'
    edit = client.post(f'{base}/tasks/{task_id}/supplement-edit')
    assert edit.status_code == 200 and edit.json()['findings_preserved'] is True, edit.text
    supplement = client.post(f'{base}/tasks/{task_id}/materials/upload',
                             files={'file': ('supplement.pdf', pdf, 'application/pdf')},
                             data={'role': 'packet', 'subject_id': str(subjects[0]['subject_id']),
                                   'supplement_generation_id': 'synthetic-supplement'})
    assert supplement.status_code == 200, supplement.text
    activated = client.post(f'{base}/tasks/{task_id}/supplement-rerun')
    assert activated.status_code in (200, 202), activated.text
    for _ in range(200):
        state = client.get(f'{base}/tasks/{task_id}').json()['state']
        if state != 'preparing':
            break
        time.sleep(.05)
    with Session(engine) as db:
        materials = [(m.file_name, m.path, m.parse_state, m.degradations_json) for m in db.query(BsMaterial).all()]
    assert state == 'running', str(materials)
    assert all(material[2] == 'done' for material in materials), str(materials)
    current_plan = client.get(f'{base}/tasks/{task_id}/supplement-plan')
    assert current_plan.json()['supplement_plan']['status'] == 'running'
    groups = current_plan.json()['supplement_plan'].get('execution_groups')
    assert groups, current_plan.json()
    for suffix in (endpoints[group] for group in groups):
        response = client.post(f'{base}/tasks/{task_id}/{suffix}')
        assert response.status_code == 200, (suffix, response.text)
    rows = client.get(f'{base}/tasks/{task_id}/findings').json()['findings']
    monkeypatch.setattr(negative_candidate_plan, 'negative_list_rules', lambda: [])
    monkeypatch.setattr(service, 'effective_model_code_or_default', lambda *_: 'synthetic-model')

    class SyntheticClient:
        async def complete(self, *_args, **_kwargs):
            return json.dumps({'proposals': []})

    monkeypatch.setattr(service, 'client_from_env', lambda **_kwargs: SyntheticClient())
    with Session(engine) as db:
        from app.plus.blockchain_supervision.models import BsTask
        snapshot = reference_snapshots.get_reference_snapshot(db, db.get(BsTask, task_id))
    for group in snapshot['ai_auto_groups']:
        url = f'{base}/tasks/{task_id}/check/{group}/ai-advisory-batch'
        status = client.get(url)
        assert status.status_code == 200, status.text
        if status.json()['counts']['not_started']:
            response = client.post(url, json={'run_id': status.json()['run_id']})
            assert response.status_code == 200, response.text
    for row in rows:
        if row['status'] in ('hit', 'undecidable'):
            response = client.patch(f"{base}/findings/{row['id']}/review",
                                    json={'decision': 'accepted', 'note': 'synthetic review'})
            assert response.status_code == 200, (row['item_id'], response.text)
    completed = client.post(f'{base}/tasks/{task_id}/complete')
    assert completed.status_code == 200, json.dumps(completed.json(), ensure_ascii=True)
    assert completed.json()['state'] == 'done'
    assert client.get(f'{base}/tasks/{task_id}/supplement-plan').json()['supplement_plan']['status'] == 'completed'
    confirmed = client.get(f'{base}/tasks/{task_id}/report/export?fmt=docx&stage=confirmed')
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.content.startswith(b'PK')
