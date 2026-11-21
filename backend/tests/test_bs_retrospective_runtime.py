import asyncio
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from datetime import date

import pytest
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
        content = messages[1]['content']
        payload = json.loads(content[0]['text'] if isinstance(content, list) else content)
        if payload.get('analysis_type') == 'citation_visual':
            if on_usage:
                on_usage({'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30})
            return json.dumps({'checks': [{'reference_id': key, 'readable': True,
                'observed_quote': ref['quote'], 'reason': '模拟原页逐字一致。'}
                for key, ref in payload['references'].items()]})
        if payload.get('analysis_type') == 'upstream_extract':
            if on_usage:
                on_usage({'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30})
            return json.dumps({'checked_topics': payload['topic_ids'], 'facts': [], 'terms': [],
                'skipped_evidence': [{'evidence_id': key, 'reason': '此模拟不生成上游条款。'}
                    for key in payload['evidence_catalog']]})
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


def prepared(tmp_path, monkeypatch, *, text=None, baseline_date=None, baseline_source=None,
             include_general=True, extra_sub_pages=(), model_client=None):
    http, engine, task_id = _client(tmp_path)
    client = model_client if model_client is not None else Client()
    monkeypatch.setattr(runtime, 'schedule', lambda *args: False)
    monkeypatch.setattr(runtime, 'freeze_basis', lambda: [])
    monkeypatch.setattr(runtime, '_file_hash', lambda material: str(material.id).zfill(64))
    monkeypatch.setattr(runtime, '_render_citation_page', lambda *args: b'\x89PNG\r\n\x1a\nsynthetic')
    from app.services.billing import bindings
    from app.plus.startup_conditions_check import llm
    monkeypatch.setattr(bindings, 'effective_model_code_or_default', lambda *args: 'synthetic')
    monkeypatch.setattr(llm, 'client_from_env', lambda **kwargs: client)
    with Session(engine) as db:
        task = db.get(BsTask, task_id)
        task.options_snapshot_json = {'review_mode': 'contract_retrospective'}
        task.baseline_date = baseline_date
        task.options_snapshot_json['baseline_date_source'] = baseline_source or 'automatic'
        task.project_name = '隔离合同项目'
        material = BsMaterial(task_id=task_id, file_name='分包合同.pdf', role='sub', pages=1 + len(extra_sub_pages),
            parse_state='done', degradations_json=[], pages_json=[{'page': 1, 'text': text or
                '签订日期：2023年12月22日\n12.1 工期延长的费用可另行协商。\n12.2 工期延长费用均不调整。'}] + [
                    {'page': page, 'text': content} for page, content in enumerate(extra_sub_pages, 2)])
        db.add(material)
        if include_general:
            db.add(BsMaterial(task_id=task_id, file_name='总包合同.pdf', role='general', pages=1,
                parse_state='done', degradations_json=[], pages_json=[{'page': 1, 'text': '工程名称：隔离合同项目。质量要求按约定执行。'}]))
        db.commit()
    accepted = http.post(f'/api/plus/blockchain-supervision/tasks/{task_id}/retrospective/start', json={})
    assert accepted.status_code == 202, accepted.text
    with Session(engine) as db:
        spec = db.query(BsTaskEvent).filter_by(task_id=task_id, event_type=runtime.SPEC).one()
        assert spec.payload_json['baseline_date'] == (
            baseline_date.isoformat() if baseline_date else None)
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
    headings = [block['text'] for block in result['report']['template_document']['blocks'] if block['type'] == 'heading']
    assert '分包合同条款' in headings
    assert '分包合同条款与总包衔接' not in headings
    assert result['report']['identity']['prompt_version'] == 'bs.contract-retrospective-prompt.v26'
    assert result['report']['identity']['model_identity']['request_model'] == 'synthetic-model'
    assert client.calls == 7
    assert result['report']['identity']['baseline_date'] == '2023-12-22'
    assert result['report']['identity']['baseline_date_source'] == 'subcontract_signing_date'
    asyncio.run(runtime.run(engine, job_id))
    assert client.calls == 7
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


def test_single_subcontract_runs_and_report_explains_comparison_is_outside_scope(tmp_path, monkeypatch):
    http, engine, task_id, material_id, job_id, _, _ = prepared(tmp_path, monkeypatch, include_general=False)
    asyncio.run(runtime.run(engine, job_id))
    report = http.get(f'/api/plus/blockchain-supervision/tasks/{task_id}/retrospective').json()['report']
    assert [row['material_id'] for row in report['materials']] == [material_id]
    assert report['upstream']['state'] == 'not_applicable'
    assert len(report['rows']) == 1
    assert '未提供总包合同' in str(report['template_document'])
    with Session(engine) as db:
        batches = db.query(BsTaskEvent).filter_by(task_id=task_id, event_type=runtime.BATCH_STARTED).all()
        assert batches and not any(row.payload_json['batch_id'].startswith('upstream:') for row in batches)


def test_default_historical_baseline_uses_unique_subcontract_signing_date(tmp_path, monkeypatch):
    text = ('合同协议书\n签订日期：2023年12月22日\n'
            '12.1 工期延长的费用可另行协商。\n12.2 工期延长费用均不调整。')
    http, engine, task_id, _, job_id, _, _ = prepared(
        tmp_path, monkeypatch, text=text,
        extra_sub_pages=('第二部分 通用合同条款\n治安防范、消防安全责任书（分包）\n签订日期：2022年1月2日',))
    with Session(engine) as db:
        event = db.query(BsTaskEvent).filter_by(task_id=task_id, event_type=runtime.SPEC).one()
        assert event.payload_json['baseline_date'] is None
        assert event.payload_json['baseline_date_source'] == 'automatic'
        assert event.payload_json['current_notice_date']
    asyncio.run(runtime.run(engine, job_id))
    report = http.get(f'/api/plus/blockchain-supervision/tasks/{task_id}/retrospective').json()['report']
    assert report['identity']['contract_date'] == '2023-12-22'
    assert report['identity']['baseline_date'] == '2023-12-22'
    assert report['identity']['baseline_date_source'] == 'subcontract_signing_date'
    assert report['identity']['current_notice_date']


def test_unknown_signing_date_does_not_fall_back_to_task_creation_date(tmp_path, monkeypatch):
    text = '合同协议书\n工程项目：隔离测试工程。12.1 工期延长的费用可另行协商。'
    http, engine, task_id, _, job_id, _, _ = prepared(
        tmp_path, monkeypatch, text=text, extra_sub_pages=('安全生产合同\n签订日期：2022年1月2日',))
    asyncio.run(runtime.run(engine, job_id))
    report = http.get(f'/api/plus/blockchain-supervision/tasks/{task_id}/retrospective').json()['report']
    assert report['identity']['contract_date'] is None
    assert report['identity']['baseline_date'] is None
    assert report['identity']['baseline_date_source'] == 'unknown'
    assert any('历史适用性不作确定性判断' in row for row in report['analysis_notes'])


def test_manual_historical_baseline_overrides_extracted_signing_date(tmp_path, monkeypatch):
    from datetime import date
    http, engine, task_id, _, job_id, _, _ = prepared(
        tmp_path, monkeypatch, baseline_date=date(2024, 1, 1), baseline_source='manual')
    asyncio.run(runtime.run(engine, job_id))
    report = http.get(f'/api/plus/blockchain-supervision/tasks/{task_id}/retrospective').json()['report']
    assert report['identity']['contract_date'] == '2023-12-22'
    assert report['identity']['baseline_date'] == '2024-01-01'
    assert report['identity']['baseline_date_source'] == 'manual'


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


@pytest.mark.parametrize('checkpoint_state', [
    'normal', 'missing', 'scan_saved', 'review_saved', 'review_missing', 'review_mismatch',
])
def test_basis_coverage_is_frozen_executed_and_saved_without_changing_public_template(
        tmp_path, monkeypatch, checkpoint_state):
    from app.plus.blockchain_supervision.retrospective_analysis import validate_candidate
    from app.plus.blockchain_supervision.retrospective_coverage import VERSION
    from app.plus.blockchain_supervision.retrospective_sources import digest, original_citation

    basis = [{'clause_id': 'D#1', 'doc_id': 'D', 'text': '延期费用应按双方约定处理。',
              'document': {'effective_from': '2020-01-01'}, 'human_verified': False}]
    http, engine, task_id, material_id, job_id, client, accepted = prepared(tmp_path, monkeypatch)
    with Session(engine) as db:
        spec = db.query(BsTaskEvent).filter_by(task_id=task_id, event_type=runtime.SPEC).first()
        payload = dict(spec.payload_json)
        payload['basis'] = basis
        spec.payload_json = payload
        if checkpoint_state != 'normal':
            source = freeze_material(db.get(BsMaterial, material_id))
            batch = analysis_batches([source])[0]
            key = 'basis:' + digest({'version': VERSION, 'basis': basis, 'batch': batch['batch_id']})

            def store_checkpoint(batch_key, result):
                service.add_event(db, task_id, runtime.CHECKPOINT, stage='check', payload={
                    'run_id': accepted['run_id'], 'batch_id': batch_key, 'attempt': 1,
                    'result': {'state': 'completed', **result}}, commit=False)

            store_checkpoint(key, {'checks': [], 'findings': []})
            if checkpoint_state != 'missing':
                citations = [{'material_id': material_id, 'page': 1, 'quote': quote} for quote in (
                    '工期延长的费用可另行协商。', '工期延长费用均不调整。')]
                check = {'clause_id': 'D#1', 'state': 'checked', 'reason': '已对照合同费用约定。',
                         'evidence': [original_citation(citations[1], [source])]}
                candidates = []
                if checkpoint_state.startswith('review_'):
                    candidates = [validate_candidate({'kind': 'internal_ambiguity', 'time_scope': 'contract_text',
                        'message': '合同对延期费用能否调整存在不同约定。', 'evidence': citations},
                        [source], basis, {})]
                store_checkpoint(key + ':scan', {'checks': [check], 'rejected': [], 'candidates': candidates})
                if checkpoint_state in {'review_saved', 'review_mismatch'}:
                    store_checkpoint(key + ':review', {'findings': candidates, 'automatic_review': [
                        {'finding_id': row['finding_id'] if checkpoint_state == 'review_saved' else 'other',
                         'keep': True, 'reason': '确有两处相反约定。'}
                        for row in candidates]})
        db.commit()
    asyncio.run(runtime.run(engine, job_id))
    result = http.get(f'/api/plus/blockchain-supervision/tasks/{task_id}/retrospective').json()
    report = result['report']
    if checkpoint_state != 'normal':
        with Session(engine) as db:
            starts = db.query(BsTaskEvent).filter_by(task_id=task_id, event_type=runtime.BATCH_STARTED).all()
            assert not any(event.payload_json['batch_id'].startswith('basis:') for event in starts)
    if checkpoint_state in {'missing', 'review_missing', 'review_mismatch'}:
        assert report is None and result['failure']['phase'] == 'coverage'
        assert not result['can_resume']
        assert result['failure']['batch_id'] == key
        assert '缺少可核对的扫描或复核明细' in result['failure']['reason']
        with Session(engine) as db:
            assert db.query(BsReviewVersion).filter_by(task_id=task_id).count() == 0
        return
    assert report['identity']['coverage_version'] == 'bs.contract-basis-coverage.v10'
    assert report['basis_coverage']['counts'] == {'checked': 1, 'not_applicable': 0, 'pending': 0}
    [scan] = report['basis_coverage']['scans']
    assert '核查限制' not in str(report['template_document'])
    if checkpoint_state == 'normal':
        assert client.calls == 8
    else:
        expected = [key + ':scan', key + ':review'] if checkpoint_state == 'review_saved' else [key + ':scan']
        assert scan['restored_from'] == expected
        assert client.calls == 7
        with Session(engine) as db:
            checkpoints = db.query(BsTaskEvent).filter_by(task_id=task_id, event_type=runtime.CHECKPOINT).all()
            saved = [event.payload_json for event in checkpoints if event.payload_json['batch_id'] == key]
            assert len(saved) == 2
            assert saved[0]['result']['checks'] == []
            assert saved[-1]['result']['restored_from'] == expected
            assert saved[-1]['phase'] == 'coverage' and '复用' in saved[-1]['label']


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
    assert result['report'] is None
    assert '未自动重复付费' in result['error']
    assert result['can_resume'] is False
    response = http.post(f'/api/plus/blockchain-supervision/tasks/{task_id}/retrospective/resume')
    assert response.status_code == 409
    assert client.calls == 0


def test_structured_output_truncation_reports_batch_and_preserves_usage(tmp_path, monkeypatch, caplog):
    import httpx
    import openai
    from app.models.models import TokenUsage
    from app.plus.startup_conditions_check.llm import OpenAICompatibleClient

    model = OpenAICompatibleClient('https://synthetic.invalid/v1', 'synthetic-key', 'qwen3.7-flash',
                                   timeout=120, max_retries=0, json_output=True)
    sdk = openai.AsyncOpenAI
    calls = []

    async def response(request):
        body = json.loads(request.content)
        assert body['response_format']['json_schema']['strict'] is True
        calls.append(request.url.host)
        return httpx.Response(200, headers={'x-request-id': 'mock-truncated-1'}, json={
            'id': 'completion', 'object': 'chat.completion', 'created': 0, 'model': model.model,
            'choices': [{'index': 0, 'finish_reason': 'length',
                         'message': {'role': 'assistant', 'content': '{"findings":['}}],
            'usage': {'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30},
        })

    def local_sdk(**kwargs):
        assert kwargs['max_retries'] == 0
        kwargs['http_client'] = httpx.AsyncClient(transport=httpx.MockTransport(response), trust_env=False)
        return sdk(**kwargs)

    monkeypatch.setattr(openai, 'AsyncOpenAI', local_sdk)
    http, engine, task_id, _, job_id, _, _ = prepared(
        tmp_path, monkeypatch, include_general=False, model_client=model)
    asyncio.run(runtime.run(engine, job_id))
    base = f'/api/plus/blockchain-supervision/tasks/{task_id}/retrospective'
    result = http.get(base).json()
    assert result['report'] is None and not result['can_resume']
    assert result['failure']['phase'] == 'contract_check'
    assert result['failure']['batch_id'].endswith(':contract')
    assert '模型输出达到长度限制，响应被截断' in result['failure']['reason']
    assert 'provider_request_id=mock-truncated-1' in caplog.text
    assert '{"findings":[' not in caplog.text
    assert http.post(f'{base}/resume').status_code == 409
    assert calls == ['synthetic.invalid']
    with Session(engine) as db:
        assert db.query(TokenUsage).filter_by(source='plus:blockchain-supervision').count() == 1
        assert db.query(BsReviewVersion).filter_by(task_id=task_id).count() == 0


def test_known_batch_failure_resumes_without_publishing_interim_report(tmp_path, monkeypatch):
    http, engine, task_id, _, job_id, client, accepted = prepared(tmp_path, monkeypatch, include_general=False)
    original = client.complete
    calls = []

    async def fail_consistency_once(messages, **kwargs):
        content = messages[1]['content']
        payload = json.loads(content[0]['text'] if isinstance(content, list) else content)
        phase = payload.get('analysis_type', 'review' if 'candidates' in payload else 'contract')
        calls.append(phase)
        if phase == 'internal_consistency' and calls.count(phase) == 1:
            kwargs['on_usage']({'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20})
            return '{}'
        return await original(messages, **kwargs)

    client.complete = fail_consistency_once
    asyncio.run(runtime.run(engine, job_id))
    base = f'/api/plus/blockchain-supervision/tasks/{task_id}/retrospective'
    failed = http.get(base).json()
    assert failed['state'] == 'failed'
    assert failed['report'] is None and failed['can_resume']
    assert failed['failure']['phase'] == 'contract_check'
    with Session(engine) as db:
        assert db.query(BsReviewVersion).filter_by(task_id=task_id).count() == 0
    for _ in range(2):
        resumed = http.post(f'{base}/resume')
        assert resumed.status_code == 202, resumed.text
        assert resumed.json()['run_id'] == accepted['run_id']
        assert resumed.json()['state'] == 'queued'
    asyncio.run(runtime.run(engine, job_id))
    result = http.get(base).json()
    assert result['report'] and result['version_no'] == 1, result
    assert result['report']['findings']
    assert result['report']['required_stages_completed']
    assert '尚未完成的核查范围' not in result['report']['summary']
    assert '分包合同条款与总包衔接' not in str(result['report']['template_document'])
    assert calls.count('contract') == 1
    assert calls.count('internal_consistency') == 2
    with Session(engine) as db:
        assert db.query(BsReviewVersion).filter_by(task_id=task_id).count() == 1
        assert db.query(BsTaskEvent).filter_by(task_id=task_id, event_type='retrospective.resumed').count() == 1
        from app.models.models import TokenUsage
        usages = db.query(TokenUsage).filter_by(source='plus:blockchain-supervision').all()
        assert len({row.dedupe_key for row in usages}) == len(calls)


def test_skipped_timing_rule_explains_failure_and_resumes_same_run(tmp_path, monkeypatch):
    timing = '开始当天不计入，从次日开始计算'
    text = ('签订日期：2023年12月22日\n12.1 工期延长的费用可另行协商。\n12.2 工期延长费用均不调整。'
            '\n12.3 合同中按天计算时间的，' + timing + '。')
    http, engine, task_id, _, job_id, client, accepted = prepared(tmp_path, monkeypatch, text=text)
    original = client.complete
    attempts = []
    primary_calls = []

    async def repair_timing_on_second_attempt(messages, **kwargs):
        content = messages[1]['content']
        payload = json.loads(content[0]['text'] if isinstance(content, list) else content)
        raw = await original(messages, **kwargs)
        if 'batch' in payload and not payload.get('analysis_type'):
            primary_calls.append(payload['batch']['batch_id'])
        if payload.get('analysis_type') != 'upstream_extract' or payload['role'] != 'sub':
            return raw
        attempts.append(payload['batch_id'])
        if len(attempts) != 2:
            return raw
        response = json.loads(raw)
        [key] = [key for key, ref in payload['evidence_catalog'].items() if timing in ref['quote']]
        response['skipped_evidence'] = [item for item in response['skipped_evidence']
                                        if item['evidence_id'] != key]
        response['terms'] = [{'topic': 'schedule', 'statement': '合同期限从次日开始计算。',
                              'obligation_holder': 'both', 'transmission': 'uncertain',
                              'conditions': '按天计算时间', 'exceptions': '', 'priority': '',
                              'evidence_ids': [key]}]
        return json.dumps(response, ensure_ascii=False)

    client.complete = repair_timing_on_second_attempt
    base = f'/api/plus/blockchain-supervision/tasks/{task_id}/retrospective'
    asyncio.run(runtime.run(engine, job_id))
    failed = http.get(base).json()
    assert failed['report'] is None and failed['can_resume']
    assert failed['failure']['phase'] == 'upstream_extract'
    assert '合同日期或期限计算约定被略过' in failed['failure']['reason']
    export_path = base.removesuffix('/retrospective') + '/report/export?fmt=docx'
    assert http.get(export_path).status_code == 409
    with Session(engine) as db:
        assert db.query(BsReviewVersion).filter_by(task_id=task_id).count() == 0
        event = db.query(BsTaskEvent).filter_by(task_id=task_id, event_type=runtime.FAILED).one()
        assert service.event_dto(event)['message'] == failed['failure']['reason']
    resumed = http.post(f'{base}/resume')
    assert resumed.status_code == 202 and resumed.json()['run_id'] == accepted['run_id']
    asyncio.run(runtime.run(engine, job_id))
    completed = http.get(base).json()
    assert completed['version_no'] == 1 and completed['report']['required_stages_completed']
    assert len(primary_calls) == 1 and len(attempts) == 2 and attempts[0] == attempts[1]
    assert completed['report']['upstream']['coverage']['batches_completed'] == 2
    assert http.get(export_path).status_code == 200


@pytest.mark.parametrize('include_general', [False, True])
def test_long_contract_resumes_past_per_attempt_batch_limit(tmp_path, monkeypatch, include_general):
    from app.plus.blockchain_supervision import retrospective_analysis, retrospective_upstream

    monkeypatch.setattr(retrospective_analysis, 'MAX_BATCHES', 1)
    monkeypatch.setattr(retrospective_upstream, 'MAX_EXTRACTION_BATCHES', 1)
    http, engine, task_id, _, job_id, client, accepted = prepared(
        tmp_path, monkeypatch, include_general=include_general,
        extra_sub_pages=('分包人负责设备维护及记录。' * 430, '总承包人办理设备进场手续。' * 430))
    original = client.complete
    calls = {'contract_check': [], 'upstream_extract': []}

    async def record_attempt(messages, **kwargs):
        payload = json.loads(messages[1]['content'])
        if payload.get('analysis_type') == 'upstream_extract':
            calls['upstream_extract'].append(payload['batch_id'])
        elif 'batch' in payload and 'analysis_type' not in payload:
            calls['contract_check'].append(payload['batch']['batch_id'])
            kwargs['on_usage']({'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20})
            return json.dumps({'checked_topics': list(TOPICS), 'limitations': [], 'findings': []})
        return await original(messages, **kwargs)

    client.complete = record_attempt
    base = f'/api/plus/blockchain-supervision/tasks/{task_id}/retrospective'
    pauses = set()
    for _ in range(16):
        before = {key: len(values) for key, values in calls.items()}
        asyncio.run(runtime.run(engine, job_id))
        assert all(len(values) - before[key] <= 1 for key, values in calls.items())
        result = http.get(base).json()
        if result['report']:
            break
        assert result['can_resume'], result.get('failure') or result.get('error')
        failure = result['failure']
        pauses.add(failure['phase'])
        assert '单轮上限' in failure['reason'] and '下一批' in failure['reason']
        with Session(engine) as db:
            assert db.query(BsReviewVersion).filter_by(task_id=task_id).count() == 0
            starts = db.query(BsTaskEvent).filter_by(task_id=task_id, event_type=runtime.BATCH_STARTED).all()
            assert failure['batch_id'] not in {event.payload_json['batch_id'] for event in starts}
        resumed = http.post(f'{base}/resume')
        assert resumed.status_code == 202 and resumed.json()['run_id'] == accepted['run_id']
    else:
        pytest.fail('相同输入续跑后仍未生成报告')

    report = result['report']
    assert report['required_stages_completed'] and result['version_no'] == 1
    assert len(calls['contract_check']) == len(set(calls['contract_check'])) == report['coverage']['batches_total'] > 1
    assert {page for batch in report['coverage']['batches'] for page in batch['pages']} == {1, 2, 3}
    assert pauses == ({'contract_check', 'upstream_extract'} if include_general else {'contract_check'})
    if include_general:
        assert len(calls['upstream_extract']) == len(set(calls['upstream_extract']))
        assert len(calls['upstream_extract']) == report['upstream']['coverage']['batches_total'] > 1
    else:
        assert not calls['upstream_extract']
    with Session(engine) as db:
        assert db.query(BsReviewVersion).filter_by(task_id=task_id).count() == 1


def test_resume_rejects_changed_frozen_file_before_enqueuing(tmp_path, monkeypatch):
    http, engine, task_id, _, job_id, client, _ = prepared(tmp_path, monkeypatch, include_general=False)
    client.model = 'changed-model'
    asyncio.run(runtime.run(engine, job_id))
    assert client.calls == 0
    monkeypatch.setattr(runtime, '_file_hash', lambda _: 'f' * 64)
    base = f'/api/plus/blockchain-supervision/tasks/{task_id}/retrospective'
    response = http.post(f'{base}/resume')
    assert response.status_code == 409
    assert '原件、归属或版本已变化' in response.json()['detail']
    assert http.get(base).json()['report'] is None
    with Session(engine) as db:
        assert db.get(BsAiReviewJob, job_id).state == 'failed'


def test_changed_runtime_identity_stops_before_call(tmp_path, monkeypatch):
    http, engine, task_id, _, job_id, client, _ = prepared(tmp_path, monkeypatch)
    client.model = 'changed-model'
    asyncio.run(runtime.run(engine, job_id))
    assert client.calls == 0
    assert http.get(f'/api/plus/blockchain-supervision/tasks/{task_id}/retrospective').json()['state'] == 'failed'


def test_cached_numbered_ocr_loop_blocks_model_calls_and_report(tmp_path, monkeypatch):
    http, engine, task_id, material_id, job_id, client, _ = prepared(tmp_path, monkeypatch, include_general=False)
    with Session(engine) as db:
        material = db.get(BsMaterial, material_id)
        material.pages = 2
        loop = '\n'.join(f'19.1.{index} 完工验收程序' for index in range(1, 40))
        material.pages_json = material.pages_json + [{'page': 2, 'text': '实际合同内容。\n' + loop}]
        db.commit()
    asyncio.run(runtime.run(engine, job_id))
    result = http.get(f'/api/plus/blockchain-supervision/tasks/{task_id}/retrospective').json()
    assert client.calls == 0
    assert result['state'] == 'failed' and result['report'] is None
    assert result['failure']['phase'] == 'parse' and '第2页' in result['error']


def test_cached_empty_markdown_rows_block_model_calls_and_report(tmp_path, monkeypatch):
    http, engine, task_id, material_id, job_id, client, _ = prepared(tmp_path, monkeypatch, include_general=False)
    with Session(engine) as db:
        material = db.get(BsMaterial, material_id)
        material.pages = 2
        text = '工程量清单\n| 项目 | 数量 |\n| --- | --- |\n' + '|   |   |\n' * 100
        material.pages_json = material.pages_json + [{'page': 2, 'text': text}]
        material.degradations_json = [
            {'kind': 'reading_quality_review', 'outcome': 'readable', 'pages': [2]},
        ]
        db.commit()
    asyncio.run(runtime.run(engine, job_id))
    result = http.get(f'/api/plus/blockchain-supervision/tasks/{task_id}/retrospective').json()
    assert client.calls == 0
    assert result['state'] == 'failed' and result['report'] is None
    assert result['failure']['phase'] == 'parse' and '第2页' in result['error'] and '重复空表格行' in result['error']


def test_legacy_accepted_run_does_not_gain_upstream_calls_or_new_identity(tmp_path, monkeypatch):
    http, engine, task_id, _, job_id, client, _ = prepared(tmp_path, monkeypatch)
    with Session(engine) as db:
        event = db.query(BsTaskEvent).filter_by(event_type=runtime.SPEC).one()
        payload = dict(event.payload_json)
        payload.pop('completion_policy')
        payload.pop('upstream_version')
        payload.pop('visual_version')
        event.payload_json = payload
        db.commit()
    asyncio.run(runtime.run(engine, job_id))
    report = http.get(f'/api/plus/blockchain-supervision/tasks/{task_id}/retrospective').json()['report']
    assert client.calls == 4
    assert report['identity'].get('upstream_version') is None
    assert 'upstream' not in report


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
