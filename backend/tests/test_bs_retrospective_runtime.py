import asyncio
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.plus.blockchain_supervision import ai_review_jobs, retrospective_runtime as runtime, service
from app.plus.blockchain_supervision.models import BsAiReviewJob, BsMaterial, BsReviewVersion, BsTask, BsTaskEvent
from app.plus.blockchain_supervision.retrospective_analysis import TOPICS
from app.plus.blockchain_supervision.retrospective_sources import analysis_batches, freeze_material
from test_bs_report_version_http import _client


class Client:
    model = 'synthetic-model'
    provider = 'legacy'
    base_url = 'https://synthetic.invalid/v1'
    enable_thinking = False
    timeout = 120
    max_retries = 0

    def __init__(self, *, error=None):
        self.calls = 0
        self.error = error

    async def complete(self, messages, on_usage=None):
        self.calls += 1
        if self.error:
            raise self.error
        payload = json.loads(messages[1]['content'])
        if payload.get('analysis_type') == 'basis_coverage':
            if on_usage:
                on_usage({'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30})
            part = payload['batch']['parts'][0]
            return json.dumps({'checked_topics': list(TOPICS), 'limitations': [], 'findings': [], 'checks': [
                {'clause_id': row['clause_id'], 'state': 'checked', 'reason': '已对照合同费用约定。',
                 'evidence': [{'material_id': part['material_id'], 'page': part['page'],
                               'quote': '工期延长费用均不调整。'}]} for row in payload['basis']]})
        if payload.get('analysis_type') == 'internal_consistency':
            if on_usage:
                on_usage({'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30})
            return json.dumps({'findings': []})
        if 'candidates' in payload:
            if on_usage:
                on_usage({'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30})
            return json.dumps({'decisions': [{'finding_id': row['finding_id'], 'keep': True,
                                             'reason': '相同条件费用约定直接冲突。'} for row in payload['candidates']]})
        material_id = payload['batch']['parts'][0]['material_id']
        if on_usage:
            on_usage({'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30})
        return json.dumps({'checked_topics': list(TOPICS), 'limitations': [], 'findings': [{
            'kind': 'internal_ambiguity', 'time_scope': 'contract_text',
            'message': '合同对延期费用能否调整存在不同约定，适用边界尚不明确。', 'evidence': [
                {'material_id': material_id, 'page': 1, 'quote': '工期延长的费用可另行协商。'},
                {'material_id': material_id, 'page': 1, 'quote': '工期延长费用均不调整。'},
            ]}]}, ensure_ascii=False)


def prepared(tmp_path, monkeypatch, *, text=None):
    http, engine, task_id = _client(tmp_path)
    client = Client()
    monkeypatch.setattr(runtime, 'schedule', lambda *args: False)
    monkeypatch.setattr(runtime, 'freeze_basis', lambda: [])
    monkeypatch.setattr(runtime, '_file_hash', lambda material: str(material.id).zfill(64))
    from app.services.billing import bindings
    from app.plus.startup_conditions_check import llm
    monkeypatch.setattr(bindings, 'effective_model_code_or_default', lambda *args: 'synthetic')
    monkeypatch.setattr(llm, 'client_from_env', lambda **kwargs: client)
    with Session(engine) as db:
        task = db.get(BsTask, task_id)
        task.options_snapshot_json = {'review_mode': 'contract_retrospective'}
        task.project_name = '隔离合同项目'
        material = BsMaterial(task_id=task_id, file_name='分包合同.pdf', role='sub', pages=1,
            parse_state='done', degradations_json=[], pages_json=[{'page': 1, 'text': text or
                '签订日期：2023年12月22日\n12.1 工期延长的费用可另行协商。\n12.2 工期延长费用均不调整。'}])
        db.add(material)
        db.add(BsMaterial(task_id=task_id, file_name='总包合同.pdf', role='general', pages=1,
            parse_state='done', degradations_json=[], pages_json=[{'page': 1, 'text': '工程名称：隔离合同项目。质量要求按约定执行。'}]))
        db.commit()
    accepted = http.post(f'/api/plus/blockchain-supervision/tasks/{task_id}/retrospective/start', json={})
    assert accepted.status_code == 202, accepted.text
    with Session(engine) as db:
        job_id = db.query(BsAiReviewJob).filter_by(task_id=task_id).one().id
        material_id = db.query(BsMaterial).filter_by(task_id=task_id, role='sub').one().id
    return http, engine, task_id, material_id, job_id, client, accepted.json()


def test_idempotent_accept_and_automatic_saved_report(tmp_path, monkeypatch):
    http, engine, task_id, _, job_id, client, accepted = prepared(tmp_path, monkeypatch)
    again = http.post(f'/api/plus/blockchain-supervision/tasks/{task_id}/retrospective/start', json={})
    assert again.json()['run_id'] == accepted['run_id']
    asyncio.run(runtime.run(engine, job_id))
    result = http.get(f'/api/plus/blockchain-supervision/tasks/{task_id}/retrospective').json()
    assert result['state'] == 'partial'  # 无规范库：内部歧义仍可报告。
    assert len(result['report']['rows']) == 1
    assert len(result['report']['rows'][0]['cells']) == 8
    assert result['report']['identity']['model_identity']['request_model'] == 'synthetic-model'
    assert client.calls == 4
    asyncio.run(runtime.run(engine, job_id))
    assert client.calls == 4
    with Session(engine) as db:
        assert db.query(BsReviewVersion).filter_by(task_id=task_id).count() == 1
        assert db.get(BsTask, task_id).state == 'done'
        frozen = db.query(BsTaskEvent).filter_by(task_id=task_id, event_type=runtime.INPUT).one().payload_json
        assert {s['role'] for s in frozen['sources']} == {'general', 'sub'}
        assert all(s['file_sha256'] for s in frozen['sources'])
        from app.models.models import TokenUsage
        usage = db.query(TokenUsage).filter_by(stage='contract_retrospective').all()
        assert len(usage) == client.calls
        assert len({row.dedupe_key for row in usage}) == client.calls
        assert all(len(row.dedupe_key) <= 120 for row in usage)


def test_usage_key_preserves_run_and_subrequest_identity_within_column_limit():
    batch = 'basis:' + 'a' * 64 + ':' + 'b' * 64
    identities = [('retro-' + 'c' * 32, batch + suffix)
                  for suffix in ('', ':consistency', ':review', ':final')]
    identities.append(('retro-' + 'd' * 32, batch))
    keys = [runtime.usage_dedupe_key(*identity) for identity in identities]
    assert len(set(keys)) == len(identities)
    assert all(len(key) <= 120 for key in keys)
    assert keys == [runtime.usage_dedupe_key(*identity) for identity in identities]


def test_missing_usage_record_stops_before_next_paid_request(tmp_path, monkeypatch):
    from app.services import token_service
    http, engine, task_id, _, job_id, client, _ = prepared(tmp_path, monkeypatch)
    monkeypatch.setattr(token_service, 'record', lambda **kwargs: None)
    asyncio.run(runtime.run(engine, job_id))
    result = http.get(f'/api/plus/blockchain-supervision/tasks/{task_id}/retrospective').json()
    assert client.calls == 1
    assert result['state'] == 'failed'
    assert result['report'] is None
    with Session(engine) as db:
        assert db.query(BsReviewVersion).filter_by(task_id=task_id).count() == 0


def test_basis_coverage_is_frozen_executed_and_saved_without_changing_public_template(tmp_path, monkeypatch):
    basis = [{'clause_id': 'D#1', 'doc_id': 'D', 'text': '延期费用应按双方约定处理。',
              'document': {'effective_from': '2020-01-01'}, 'human_verified': False}]
    http, engine, task_id, _, job_id, client, _ = prepared(tmp_path, monkeypatch)
    with Session(engine) as db:
        spec = db.query(BsTaskEvent).filter_by(task_id=task_id, event_type=runtime.SPEC).first()
        payload = dict(spec.payload_json)
        payload['basis'] = basis
        spec.payload_json = payload
        db.commit()
    asyncio.run(runtime.run(engine, job_id))
    report = http.get(f'/api/plus/blockchain-supervision/tasks/{task_id}/retrospective').json()['report']
    assert report['identity']['coverage_version'] == 'bs.contract-basis-coverage.v1'
    assert report['basis_coverage']['counts'] == {'checked': 1, 'not_applicable': 0, 'pending': 0}
    assert len(report['basis_coverage']['scans']) == 1
    assert '核查限制' not in str(report['template_document'])
    assert client.calls == 5


def test_unknown_response_checkpoint_is_not_rebilled(tmp_path, monkeypatch):
    http, engine, task_id, material_id, job_id, client, accepted = prepared(tmp_path, monkeypatch)
    with Session(engine) as db:
        material = db.get(BsMaterial, material_id)
        source = freeze_material(material)
        batch = analysis_batches([source])[0]
        service.add_event(db, task_id, runtime.BATCH_STARTED, stage='check', payload={
            'run_id': accepted['run_id'], 'batch_id': batch['batch_id'], 'index': 1, 'total': 1})
    asyncio.run(runtime.run(engine, job_id))
    result = http.get(f'/api/plus/blockchain-supervision/tasks/{task_id}/retrospective').json()
    assert result['state'] == 'failed' and client.calls == 0
    assert any('未自动重复付费' in message for message in result['report']['limitations'])


def test_changed_runtime_identity_stops_before_call(tmp_path, monkeypatch):
    http, engine, task_id, _, job_id, client, _ = prepared(tmp_path, monkeypatch)
    client.model = 'changed-model'
    asyncio.run(runtime.run(engine, job_id))
    assert client.calls == 0
    assert http.get(f'/api/plus/blockchain-supervision/tasks/{task_id}/retrospective').json()['state'] == 'failed'


def test_admission_mutations_cannot_change_a_retrospective_run(tmp_path, monkeypatch):
    http, engine, task_id, material_id, _, _, _ = prepared(tmp_path, monkeypatch)
    base = f'/api/plus/blockchain-supervision/tasks/{task_id}'
    for suffix in ['/material-edit', '/complete', '/rerun', '/check/semantic', '/supplement-edit']:
        response = http.post(base + suffix)
        assert response.status_code == 409, (suffix, response.text)
    assert http.delete(base + f'/materials/{material_id}').status_code == 409
    with Session(engine) as db:
        assert db.get(BsTask, task_id).state == 'running'


def test_expired_retrospective_is_scheduled_by_existing_maintenance(tmp_path, monkeypatch):
    from app.plus.blockchain_supervision import background_ai_review
    _, engine, task_id, _, job_id, _, _ = prepared(tmp_path, monkeypatch)
    seen = []
    with Session(engine) as db:
        ai_review_jobs.claim(db, job_id)
        job = db.get(BsAiReviewJob, job_id)
        job.lease_expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1)
        db.commit()
    monkeypatch.setattr(runtime, 'schedule', lambda engine, jid: seen.append(jid) or True)
    asyncio.run(background_ai_review.resume_queued_jobs(engine))
    assert seen == [job_id]


def test_ownership_is_checked_on_status_report_and_start(tmp_path, monkeypatch):
    http, _, task_id, _, _, _, _ = prepared(tmp_path, monkeypatch)
    assert http.post('/api/login', json={'username':'bob', 'password':'Bob-pass-123'}).status_code == 200
    base = f'/api/plus/blockchain-supervision/tasks/{task_id}'
    assert http.get(base + '/retrospective').status_code == 403
    assert http.get(base + '/report').status_code == 403
    assert http.post(base + '/retrospective/start', json={}).status_code == 403


def test_upload_pdf_parse_to_saved_report_isolated_from_business_storage(tmp_path, monkeypatch):
    import fitz
    monkeypatch.setenv('SECRET_KEY', 'x' * 64)
    from app.plus.blockchain_supervision import parsing
    http, engine, task_id = _client(tmp_path)
    base = f'/api/plus/blockchain-supervision/tasks/{task_id}'
    client = Client()
    from app.plus.startup_conditions_check import llm
    from app.services.billing import bindings
    monkeypatch.setattr(bindings, 'effective_model_code_or_default', lambda *args: 'synthetic')
    monkeypatch.setattr(llm, 'client_from_env', lambda **kwargs: client)
    monkeypatch.setattr(runtime, 'schedule', lambda *args: False)
    monkeypatch.setattr(runtime, 'freeze_basis', lambda: [])
    monkeypatch.setattr(parsing, 'UPLOADS_DIR', str(tmp_path / 'uploads'))
    def isolated_path(material):
        path = (Path(runtime.__file__).resolve().parents[2] / material.path).resolve()
        assert path.is_relative_to(tmp_path)
        return path
    monkeypatch.setattr(runtime, '_path', isolated_path)
    monkeypatch.setattr(parsing, 'ocr_text_pages', lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError('native-text acceptance must not call paid OCR')))
    with Session(engine) as db:
        task = db.get(BsTask, task_id)
        task.options_snapshot_json = {'review_mode': 'contract_retrospective'}
        db.commit()
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((50, 60), '工程名称：隔离测试工程\n签订日期：2023年12月22日\n12.1 工期延长的费用可另行协商。\n12.2 工期延长费用均不调整。', fontname='china-s')
        pdf_data = pdf.tobytes()
    uploaded = http.post(base + '/materials/upload', files={'file': ('分包合同.pdf', pdf_data, 'application/pdf')},
                         data={'role': 'sub'})
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()['parse_state'] == 'uploaded'
    general = http.post(base + '/materials/upload', files={'file': ('总包合同.pdf', pdf_data, 'application/pdf')},
                        data={'role': 'general'})
    assert general.status_code == 200, general.text
    accepted = http.post(base + '/retrospective/start', json={})
    assert accepted.status_code == 202, accepted.text
    with Session(engine) as db:
        job_id = db.query(BsAiReviewJob).filter_by(task_id=task_id).one().id
    asyncio.run(runtime.run(engine, job_id))
    result = http.get(base + '/retrospective').json()
    assert result['state'] == 'partial', result
    assert result['report']['project_name'] == '隔离测试工程'
    assert result['report']['materials'][0]['file_sha256']
    assert len(result['report']['rows']) == 1
    assert http.get(base + '/report/export?fmt=pdf').status_code == 200
