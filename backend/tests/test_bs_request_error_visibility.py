"""请求错误须安全地从模型调用传递到任务时间线。"""
from types import SimpleNamespace
import json
from pathlib import Path
import time

import fitz
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.plus.blockchain_supervision.ai_review_batch import execute_review_batch
from app.plus.blockchain_supervision.service import event_dto
from app.plus.blockchain_supervision import parsing
from app.plus.blockchain_supervision import background_ai_review
from app.plus.structural_check import ocr_text
from app.plus.blockchain_supervision.models import BsTaskEvent
from app.standalone.identity import Base, create_account
from app.standalone.main import create_app
from test_standalone_model_failure import _batch


@pytest.mark.asyncio
@pytest.mark.parametrize('status,category', [(429, 'rate_limit'), (401, 'authentication'), (403, 'authentication'), (503, 'service_error')])
async def test_batch_preserves_safe_http_failure_category(status, category):
    class Error(Exception):
        status_code = status

    class Client:
        async def complete(self, *_args, **_kwargs):
            raise Error('private-token private-material')

    result = (await execute_review_batch(client=Client(), batch=_batch(), max_attempts=1))[0]
    assert result['execution_status'] == 'failed'
    assert result['failure_category'] == category
    assert 'private-token' not in str(result)
    assert 'private-material' not in str(result)


def test_ai_timeline_displays_confirmed_429_without_raw_payload():
    ev = SimpleNamespace(sequence=1, event_type='check.ai_batch_progress', stage='check', created_at=None,
                         payload_json={'group_id': 'a1', 'batch_index': 3, 'batch_total': 12,
                                       'status': 'failed', 'failure_categories': ['rate_limit'],
                                       'private': 'private-token'})
    dto = event_dto(ev)
    assert '429' in dto['message'] and '3/12' in dto['message']
    assert 'private-token' not in str(dto)


def test_background_failure_keeps_429_category_after_restart():
    error = background_ai_review.BackgroundReviewIncomplete(['rate_limit'])
    ev = SimpleNamespace(event_type=background_ai_review.FAILED, payload_json={
        'job_id': 'job', 'review_run_id': 'review', 'failure_categories': error.categories})
    result = background_ai_review.project_job_state(ev, active_job_ids=set(), instance_id='other')
    assert result['state'] == 'failed'
    assert result['failure_categories'] == ['rate_limit']


def test_timeout_is_not_misreported_as_rate_limit():
    ev = SimpleNamespace(sequence=1, event_type='check.ai_batch_progress', stage='check', created_at=None,
                         payload_json={'group_id': 'a1', 'batch_index': 1, 'batch_total': 1,
                                       'status': 'failed', 'failure_categories': ['timeout']})
    message = event_dto(ev)['message']
    assert '超时' in message and '429' not in message


@pytest.mark.parametrize(('status', 'expected'), [
    ('timeout_split', '模型响应超时，正在拆分后继续核查'),
    ('invalid_retry', '模型返回格式无效，正在重试该部分'),
])
def test_ai_recovery_progress_is_visible_in_timeline(status, expected):
    ev = SimpleNamespace(sequence=1, event_type='check.ai_batch_progress', stage='check', created_at=None,
                         payload_json={'group_id': 'signature_chain', 'batch_index': 3, 'batch_total': 5,
                                       'status': status})
    dto = event_dto(ev)
    assert '3/5' in dto['message'] and expected in dto['message']
    assert dto['diagnostics']['status'] == status


def test_ocr_timeline_shows_failed_pages_without_raw_error():
    ev = SimpleNamespace(sequence=1, event_type='material.parsed', stage='check', created_at=None,
                         payload_json={'file_name': 'test.pdf', 'degraded': True,
                                       'request_errors': [{'category': 'rate_limit', 'pages': [2, 4]}]})
    dto = event_dto(ev)
    assert '429' in dto['message'] and '2' in dto['message'] and '4' in dto['message']


def test_ocr_http_429_keeps_page_unread_and_preserves_safe_category(monkeypatch, tmp_path, caplog):
    path = tmp_path / 'scan.pdf'
    doc = fitz.open()
    doc.new_page().draw_rect(fitz.Rect(30, 30, 90, 90), fill=(0, 0, 0))
    doc.save(path)
    doc.close()
    monkeypatch.setenv('OCR_CLOUD_MODEL', 'synthetic')
    monkeypatch.setenv('OCR_CLOUD_ENDPOINT', 'https://ocr.invalid/api')
    monkeypatch.setenv('OCR_CLOUD_API_KEY', 'synthetic-secret')
    real_client = httpx.AsyncClient
    calls = []
    def response(request):
        calls.append(request)
        return httpx.Response(429, text='synthetic-secret')
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real_client(
        transport=httpx.MockTransport(response), **kwargs))
    monkeypatch.setattr(ocr_text, '_local_ocr_engine', lambda: pytest.fail('local OCR used'))
    pages, degradations = parsing.parse_material_pages(str(path))
    assert not pages
    assert next(d for d in degradations if d['kind'] == 'ocr_request_errors')['request_errors'] == [
        {'page': 1, 'category': 'rate_limit'}]
    assert next(d for d in degradations if d['kind'] == 'parse_incomplete')['ocr_failed_pages'] == [1]
    assert len(calls) == 2
    assert 'synthetic-secret' not in str(degradations) + caplog.text


def test_material_429_is_saved_in_timeline_through_real_upload(tmp_path, monkeypatch):
    from app.plus.safety_review import ocr_cloud
    from app.plus.request_errors import ModelHTTPError
    monkeypatch.setenv('SECRET_KEY', 'synthetic-secret-key-0123456789-abcdefgh')
    reference = Path(__file__).parent.parent / 'app/plus/blockchain_supervision/data/reference_package.json'
    sources = json.loads(reference.read_text(encoding='utf-8'))['sources']
    mapping = {'version': 1, 'documents': [
        {'sha256': digest, 'kb_id': 'synthetic-kb', 'knowledge_id': 'synthetic-' + digest,
         'collection': 'road_national_basis'} for digest in {row['original_sha256'] for row in sources}]}
    mapping_path = tmp_path / 'mapping.json'
    mapping_path.write_text(json.dumps(mapping), encoding='utf-8')
    monkeypatch.setenv('BS_REFERENCE_MAPPING_FILE', str(mapping_path))
    monkeypatch.setattr(parsing, 'UPLOADS_DIR', str(tmp_path / 'uploads'))
    monkeypatch.setattr(ocr_cloud, 'cloud_config', lambda: {'model': 'mock', 'endpoint': 'mock', 'api_key': 'secret'})
    async def reject(*args, **kwargs):
        raise ModelHTTPError(429)
    monkeypatch.setattr(ocr_cloud, 'cloud_ocr_structured', reject)
    engine = create_engine('sqlite:///' + (tmp_path / 'events.sqlite').as_posix(), connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    create_account(engine, 'alice', 'Alice-password-123')
    doc = fitz.open()
    doc.new_page().draw_rect(fitz.Rect(30, 30, 90, 90), fill=(0, 0, 0))
    pdf = doc.tobytes()
    doc.close()
    with TestClient(create_app(engine, 's' * 64, cookie_secure=False)) as client:
        assert client.post('/api/login', json={'username': 'alice', 'password': 'Alice-password-123'}).status_code == 200
        conversation = client.post('/api/conversations').json()['id']
        base = '/api/plus/blockchain-supervision'
        task = client.post(base + '/tasks', json={'conversation_id': conversation, 'title': 'synthetic', 'scope_a1': True}).json()['task_id']
        assert client.post(f'{base}/tasks/{task}/materials/upload', files={'file': ('scan.pdf', pdf, 'application/pdf')}, data={'role': 'labor'}).status_code == 200
        subject = client.get(f'{base}/tasks/{task}/subjects').json()['subjects'][0]['subject_id']
        assert client.patch(f'{base}/tasks/{task}/subjects/{subject}/type', json={'subject_type': 'labor'}).status_code == 200
        assert client.patch(f'{base}/tasks/{task}/subjects/{subject}/name', json={'name': '合成单位'}).status_code == 200
        assert client.post(f'{base}/tasks/{task}/start').status_code in (200, 202)
        for _ in range(200):
            with Session(engine) as db:
                event = db.query(BsTaskEvent).filter_by(task_id=task, event_type='material.parsed').first()
                if event:
                    result = event_dto(event)
                    break
            time.sleep(.05)
        else:
            pytest.fail('material parsed event was not saved')
        assert '429' in result['message'] and '第1页' in result['message']
        assert 'secret' not in str(result)
    restarted_engine = create_engine('sqlite:///' + (tmp_path / 'events.sqlite').as_posix())
    with Session(restarted_engine) as db:
        persisted = db.query(BsTaskEvent).filter_by(task_id=task, event_type='material.parsed').first()
        assert '429' in event_dto(persisted)['message']
    restarted_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('statuses,expected_calls', [([429, 200], 2), ([401], 1)])
async def test_ocr_retry_is_bounded_and_only_for_transient_statuses(monkeypatch, statuses, expected_calls):
    from app.plus.safety_review.ocr_cloud import _post_ocr
    from app.plus.request_errors import ModelHTTPError
    import asyncio
    async def no_sleep(_delay):
        pass
    monkeypatch.setattr(asyncio, 'sleep', no_sleep)
    calls = []
    def handler(request):
        status = statuses[len(calls)]
        calls.append(status)
        return httpx.Response(status, json={'output': {'choices': [{'message': {'content': []}}]}})
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    if statuses[-1] == 200:
        assert await _post_ocr(b'image', model='mock', endpoint='https://ocr.invalid', api_key='secret') == []
    else:
        with pytest.raises(ModelHTTPError):
            await _post_ocr(b'image', model='mock', endpoint='https://ocr.invalid', api_key='secret')
    assert len(calls) == expected_calls
