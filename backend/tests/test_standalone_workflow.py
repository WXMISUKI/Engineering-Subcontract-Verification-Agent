"""独立入口的合成材料业务流；不连接外部模型或知识服务。"""
import json
import asyncio
import time
import io
import zipfile
from pathlib import Path

import fitz
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.plus.blockchain_supervision.models import BsMaterial

from app.standalone.identity import Base, create_account
from app.standalone.main import create_app
from app.plus.blockchain_supervision import (
    parsing, service, reference_snapshots, negative_candidate_plan, optional_source_scope,
)


def test_uploaded_pdf_starts_and_runs_checks_with_unavailable_knowledge(tmp_path, monkeypatch):
    monkeypatch.setenv('SECRET_KEY', 'synthetic-secret-key-0123456789-abcdefgh')
    monkeypatch.setenv(optional_source_scope.SCOPE_POLICY_ENV, 'true')
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
    parent_scope_response = client.get(f'{base}/tasks/{task_id}/todo')
    assert parent_scope_response.status_code == 200, parent_scope_response.text
    parent_scope = parent_scope_response.json()['scope_coverage']
    assert parent_scope['policy'] == optional_source_scope.POLICY_VERSION
    assert parent_scope['state'] == 'partial'
    assert {item['source_state'] for item in parent_scope['limitations']} == {'absent'}
    assert set(optional_source_scope.OPTIONAL_SOURCE_RULE_IDS) <= {
        item['rule_id'] for item in parent_scope['limitations']
    }
    parent_generation_id = parent_scope['generation_id']
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
        'generation_id': 'synthetic-supplement', 'category': 'general_contract',
        'item_ids': ['T15-01'],
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
    assert previous['scope_coverage'] == parent_scope
    edit_scope = client.get(f'{base}/tasks/{task_id}/todo').json()['scope_coverage']
    assert edit_scope['state'] == 'legacy_unknown'
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
                             files={'file': ('supplement-general.pdf', pdf, 'application/pdf')},
                             data={'role': 'general',
                                   'supplement_generation_id': 'synthetic-supplement'})
    assert supplement.status_code == 200, supplement.text
    with Session(engine) as db:
        general_material = db.get(BsMaterial, supplement.json()['material_id'])
        assert general_material.role == 'general'
        general_material.parse_state = 'parsing'
        db.commit()
    scope_during_upload = client.get(f'{base}/tasks/{task_id}/todo').json()['scope_coverage']
    assert scope_during_upload == edit_scope
    assert previous['scope_coverage'] == parent_scope
    pending_activation = client.post(f'{base}/tasks/{task_id}/supplement-rerun')
    assert pending_activation.status_code == 409
    assert pending_activation.json()['detail']['reason'] == 'parsing_pending'
    assert client.get(f'{base}/tasks/{task_id}/todo').json()['scope_coverage'] == edit_scope
    with Session(engine) as db:
        general_material = db.get(BsMaterial, supplement.json()['material_id'])
        general_material.parse_state = 'uploaded'
        db.commit()
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
    new_scope_before_a1 = client.get(f'{base}/tasks/{task_id}/todo').json()['scope_coverage']
    assert new_scope_before_a1['generation_id'] != parent_generation_id
    assert new_scope_before_a1['state'] == 'incomplete'
    assert new_scope_before_a1['limitations'] == []
    duplicate_activation = client.post(f'{base}/tasks/{task_id}/supplement-rerun')
    assert duplicate_activation.status_code == 200
    assert duplicate_activation.json().get('already_running') is True
    current = client.get(f'{base}/tasks/{task_id}').json()
    assert current['options_snapshot']['rerun']['carry'] == []
    assert client.get(f'{base}/tasks/{task_id}/report?stage=draft').status_code == 409
    assert client.get(f'{base}/tasks/{task_id}/findings').json()['publication_status'] == 'preparing'
    historical_during_candidate = client.get(
        f'{base}/tasks/{task_id}/report?stage=draft&version_no={previous_version}')
    assert historical_during_candidate.status_code == 200, historical_during_candidate.text
    frozen = client.get(f'{base}/tasks/{task_id}/versions/{previous_version}').json()['snapshot']
    assert [m['file_name'] for m in frozen['materials']] == ['synthetic.pdf']
    assert frozen['scope_coverage'] == parent_scope
    frozen_business_document = frozen['report']['business_document']
    assert historical_during_candidate.json()['business_document'] == frozen_business_document
    groups = current_plan.json()['supplement_plan'].get('execution_groups')
    assert groups, current_plan.json()
    for suffix in (endpoints[group] for group in groups):
        response = client.post(f'{base}/tasks/{task_id}/{suffix}')
        assert response.status_code == 200, (suffix, response.text)
    new_scope_after_checks = client.get(f'{base}/tasks/{task_id}/todo').json()['scope_coverage']
    assert new_scope_after_checks['generation_id'] == new_scope_before_a1['generation_id']
    assert new_scope_after_checks['state'] == 'available', [
        (item['rule_id'], item['source_state'], item['reason_code'])
        for item in new_scope_after_checks.get('limitations', [])
    ]
    assert new_scope_after_checks['limitations'] == []
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
    assert published['publication_state'] == 'candidate'
    assert published['baseline_version_no'] == previous_version
    diff = client.get(f'{base}/tasks/{task_id}/versions/diff', params={'a': previous_version, 'b': 'current'})
    assert diff.status_code == 200, diff.text
    draft_after_supplement = client.get(f'{base}/tasks/{task_id}/report?stage=draft')
    assert draft_after_supplement.status_code == 409
    assert draft_after_supplement.json()['detail']['code'] == 'supplement_candidate_unpublished'
    opinion_during_candidate = client.get(f'{base}/tasks/{task_id}/opinion?stage=draft')
    assert opinion_during_candidate.status_code == 409
    assert opinion_during_candidate.json()['detail']['code'] == 'supplement_candidate_unpublished'
    opinion_preview_during_candidate = client.get(f'{base}/tasks/{task_id}/opinion/preview-markdown?stage=draft')
    assert opinion_preview_during_candidate.status_code == 200, opinion_preview_during_candidate.text
    opinion_pdf_preview_during_candidate = client.get(f'{base}/tasks/{task_id}/opinion/preview?stage=draft')
    assert opinion_pdf_preview_during_candidate.status_code == 200, opinion_pdf_preview_during_candidate.text
    report_export_during_candidate = client.get(f'{base}/tasks/{task_id}/report/export?fmt=docx&stage=draft')
    assert report_export_during_candidate.status_code == 409
    opinion_export_during_candidate = client.get(f'{base}/tasks/{task_id}/opinion/export?fmt=docx&stage=draft')
    assert opinion_export_during_candidate.status_code == 409
    ignored_id = next(row['id'] for row in rows if row['status'] in ('hit', 'undecidable'))
    for row in rows:
        if row['status'] in ('hit', 'undecidable'):
            response = client.patch(f"{base}/findings/{row['id']}/review",
                                    json={'decision': 'ignored' if row['id'] == ignored_id else 'accepted',
                                          'note': 'synthetic ignored reason' if row['id'] == ignored_id else 'synthetic review'})
            assert response.status_code == 200, (row['item_id'], response.text)
    completed = client.post(f'{base}/tasks/{task_id}/complete')
    assert completed.status_code == 200, json.dumps(completed.json(), ensure_ascii=True)
    assert completed.json()['state'] == 'done'
    assert client.get(f'{base}/tasks/{task_id}/supplement-plan').json()['supplement_plan']['status'] == 'completed'
    after_complete = client.get(f'{base}/tasks/{task_id}/findings').json()
    assert after_complete['publication_state'] == 'published'
    assert after_complete['publication_generation_id'] == 'synthetic-supplement'
    report_after_complete = client.get(f'{base}/tasks/{task_id}/report?stage=draft')
    assert report_after_complete.status_code == 200
    assert report_after_complete.json()['meta']['publication_generation_id'] == 'synthetic-supplement'
    assert report_after_complete.json()['meta']['publication_state'] == 'published'
    opinion_after_complete = client.get(f'{base}/tasks/{task_id}/opinion?stage=draft')
    assert opinion_after_complete.status_code == 200
    assert opinion_after_complete.json()['meta']['publication_generation_id'] == 'synthetic-supplement'
    assert opinion_after_complete.json()['meta']['publication_state'] == 'published'
    published_lineage = [
        after_complete,
        report_after_complete.json()['meta'],
        opinion_after_complete.json()['meta'],
    ]
    assert {row['publication_state'] for row in published_lineage} == {'published'}
    assert {row['publication_generation_id'] for row in published_lineage} == {'synthetic-supplement'}
    assert {row['review_policy'] for row in published_lineage} == {'current_generation'}
    confirmed_report = client.get(f'{base}/tasks/{task_id}/report?stage=confirmed')
    assert confirmed_report.status_code == 200, confirmed_report.text
    confirmed_opinion = client.get(f'{base}/tasks/{task_id}/opinion?stage=confirmed')
    assert confirmed_opinion.status_code == 200, confirmed_opinion.text
    confirmed = client.get(f'{base}/tasks/{task_id}/report/export?fmt=docx&stage=confirmed')
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.content.startswith(b'PK')
    confirmed_opinion_export = client.get(f'{base}/tasks/{task_id}/opinion/export?fmt=docx&stage=confirmed')
    assert confirmed_opinion_export.status_code == 200, confirmed_opinion_export.text
    assert confirmed_opinion_export.content.startswith(b'PK')
    evidence_bundle = client.get(f'{base}/tasks/{task_id}/report/evidence-bundle?stage=confirmed')
    assert evidence_bundle.status_code == 200, evidence_bundle.text
    assert evidence_bundle.content.startswith(b'PK')
    with zipfile.ZipFile(io.BytesIO(evidence_bundle.content)) as archive:
        audit_report = json.loads(archive.read('report.json'))
        ignored = [item for section in audit_report['sections'] for item in section['items']
                   if item.get('finding_id') == ignored_id]
        assert ignored[0]['human_disposition']['code'] == 'ignored'
        assert ignored[0]['human_disposition']['note'] == 'synthetic ignored reason'
        assert all(ignored_id not in item['finding_ids'] for item in audit_report['business_document']['issues'])
    second = client.post(f'{base}/tasks/{task_id}/supplement-plan', json={
        'generation_id': 'second-supplement', 'category': 'general_contract',
        'item_ids': ['T15-01'],
    })
    assert second.status_code == 200
    second_edit = client.post(f'{base}/tasks/{task_id}/supplement-edit')
    assert second_edit.status_code == 200
    prior = client.get(f"{base}/tasks/{task_id}/versions/{second_edit.json()['previous_version_no']}").json()['snapshot']
    assert any(finding['finding_id'] == ignored_id and finding['review']['decision'] == 'ignored'
               for finding in prior['findings'])
    assert all(ignored_id not in item['finding_ids'] for item in prior['report']['business_document']['issues'])
    assert prior['task']['options_snapshot']['supplement_plan']['generation_id'] == 'synthetic-supplement'
    assert 'a1' in second.json()['supplement_plan']['impact']['affected_groups']
    old_supplement_id = supplement.json()['material_id']
    broken = client.post(f'{base}/tasks/{task_id}/materials/upload',
                         files={'file': ('broken.pdf', b'%PDF-broken', 'application/pdf')},
                         data={'role': 'general', 'replaces_material_id': str(old_supplement_id),
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
    failed_publication = client.get(f'{base}/tasks/{task_id}/findings').json()
    assert failed_publication['publication_state'] == 'failed'
    assert failed_publication['publication_generation_id'] == 'synthetic-supplement'
    assert failed_publication['candidate_generation_id'] == 'second-supplement'
    failed_scope = client.get(f'{base}/tasks/{task_id}/todo').json()['scope_coverage']
    assert failed_scope['state'] == 'legacy_unknown'
    assert prior['scope_coverage'] == new_scope_after_checks
    current_finding_id = failed_publication['findings'][0]['id']
    assert client.patch(f"{base}/findings/{current_finding_id}/review",
                        json={'decision': 'accepted', 'note': 'must remain baseline-only'}).status_code == 409
    assert client.get(f'{base}/tasks/{task_id}/report?stage=draft').status_code == 409
    failed_opinion = client.get(f'{base}/tasks/{task_id}/opinion?stage=draft')
    assert failed_opinion.status_code == 409
    assert failed_opinion.json()['detail']['code'] == 'supplement_failed'
    failed_confirmed_report = client.get(f'{base}/tasks/{task_id}/report?stage=confirmed')
    assert failed_confirmed_report.status_code == 409
    failed_bundle = client.get(f'{base}/tasks/{task_id}/report/evidence-bundle?stage=confirmed')
    assert failed_bundle.status_code == 409
    historical_after_failure = client.get(
        f'{base}/tasks/{task_id}/report?stage=draft&version_no={previous_version}')
    assert historical_after_failure.status_code == 200, historical_after_failure.text
    assert historical_after_failure.json()['business_document'] == frozen_business_document
