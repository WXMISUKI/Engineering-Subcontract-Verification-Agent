"""独立入口的合成材料业务流；不连接外部模型或知识服务。"""
import json
import asyncio
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
    prior_report = client.get(f'{base}/tasks/{task_id}/report?stage=draft')
    assert prior_report.status_code == 200
    assert prior_report.json()['meta'].get('generation_id') != 'synthetic-supplement'
    edit = client.post(f'{base}/tasks/{task_id}/supplement-edit')
    assert edit.status_code == 200 and edit.json()['findings_preserved'] is True, edit.text
    # The published baseline must be frozen before the supplemental file exists.
    previous_version = edit.json()['previous_version_no']
    previous = client.get(f'{base}/tasks/{task_id}/versions/{previous_version}').json()['snapshot']
    assert [m['file_name'] for m in previous['materials']] == ['synthetic.pdf']
    assert previous['task']['options_snapshot'].get('supplement_plan') is None
    assert any(f.get('review', {}).get('note') == 'synthetic review' for f in previous['findings'] if f.get('review'))
    create_account(engine, 'bob', 'Bob-password-12345')
    assert client.post('/api/login', json={'username': 'bob', 'password': 'Bob-password-12345'}).status_code == 200
    assert client.get(f'{base}/tasks/{task_id}/versions/{previous_version}').status_code == 403
    assert client.post('/api/login', json={'username': 'alice', 'password': 'Alice-pass-123'}).status_code == 200
    repeated = client.post(f'{base}/tasks/{task_id}/supplement-edit')
    assert repeated.status_code == 200 and repeated.json()['previous_version_no'] == previous_version
    assert len(client.get(f'{base}/tasks/{task_id}/versions').json()['versions']) == 1
    competing = client.post(f'{base}/tasks/{task_id}/supplement-plan', json={
        'generation_id': 'competing-supplement', 'category': 'submission_packet',
        'item_ids': ['RC-A01'], 'subject_ids': [subjects[0]['subject_id']],
    })
    assert competing.status_code == 409
    # Old findings are historical during supplement preparation, not a new report.
    assert client.get(f'{base}/tasks/{task_id}/report?stage=draft').status_code == 409
    assert client.get(f'{base}/tasks/{task_id}/versions/diff', params={'a': previous_version, 'b': 'current'}).status_code == 409
    assert client.patch(f"{base}/findings/{candidate['id']}/review",
                        json={'decision': 'accepted', 'note': 'should not replace baseline'}).status_code == 409
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
    assert current_plan.json()['supplement_plan']['superseded_version_no'] == previous_version
    current = client.get(f'{base}/tasks/{task_id}').json()
    assert current['options_snapshot']['rerun']['carry'] == []
    assert client.get(f'{base}/tasks/{task_id}/report?stage=draft').status_code == 409
    assert client.get(f'{base}/tasks/{task_id}/findings').json()['publication_status'] == 'preparing'
    frozen = client.get(f'{base}/tasks/{task_id}/versions/{previous_version}').json()['snapshot']
    assert [m['file_name'] for m in frozen['materials']] == ['synthetic.pdf']
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
    with monkeypatch.context() as pending:
        pending.setattr(service, 'current_ai_advisory_pending', lambda *_: True)
        assert client.get(f'{base}/tasks/{task_id}/findings').json()['publication_status'] == 'preparing'
    from app.plus.blockchain_supervision import background_ai_review
    with monkeypatch.context() as pending:
        pending.setattr(background_ai_review, 'job_status', lambda *_: {'state': 'not_started'})
        assert client.get(f'{base}/tasks/{task_id}/findings').json()['publication_status'] == 'preparing'
    asyncio.run(background_ai_review._run_background(engine, task_id, 1, 'web', snapshot['review_run_id'],
                                                     'synthetic-background-job', snapshot['ai_auto_groups'], False))
    published = client.get(f'{base}/tasks/{task_id}/findings').json()
    assert published['publication_status'] == 'ready'
    assert published['publication_generation_id'] == 'synthetic-supplement'
    diff = client.get(f'{base}/tasks/{task_id}/versions/diff', params={'a': previous_version, 'b': 'current'})
    assert diff.status_code == 200, diff.text
    draft_after_supplement = client.get(f'{base}/tasks/{task_id}/report?stage=draft')
    assert draft_after_supplement.status_code == 200
    assert draft_after_supplement.json()['meta']['review_version_no'] is None
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
    second = client.post(f'{base}/tasks/{task_id}/supplement-plan', json={
        'generation_id': 'second-supplement', 'category': 'submission_packet',
        'item_ids': ['RC-A01'], 'subject_ids': [subjects[0]['subject_id']],
    })
    assert second.status_code == 200
    second_edit = client.post(f'{base}/tasks/{task_id}/supplement-edit')
    assert second_edit.status_code == 200
    prior = client.get(f"{base}/tasks/{task_id}/versions/{second_edit.json()['previous_version_no']}").json()['snapshot']
    assert prior['task']['options_snapshot']['supplement_plan']['generation_id'] == 'synthetic-supplement'
    old_supplement_id = supplement.json()['material_id']
    broken = client.post(f'{base}/tasks/{task_id}/materials/upload',
                         files={'file': ('broken.pdf', b'%PDF-broken', 'application/pdf')},
                         data={'role': 'packet', 'replaces_material_id': str(old_supplement_id),
                               'supplement_generation_id': 'second-supplement'})
    assert broken.status_code == 200, broken.text
    attempted = client.post(f'{base}/tasks/{task_id}/supplement-rerun')
    assert attempted.status_code in (202, 409), attempted.text
    for _ in range(200):
        detail = client.get(f'{base}/tasks/{task_id}').json()
        if detail['state'] != 'preparing':
            break
        time.sleep(.05)
    with Session(engine) as db:
        old = db.get(BsMaterial, old_supplement_id)
        replacement = db.get(BsMaterial, broken.json()['material_id'])
        assert replacement.parse_state == 'failed'
        assert old.superseded_by_id is None
    assert detail['state'] == 'setup'
    assert client.get(f'{base}/tasks/{task_id}/report?stage=draft').status_code == 409
