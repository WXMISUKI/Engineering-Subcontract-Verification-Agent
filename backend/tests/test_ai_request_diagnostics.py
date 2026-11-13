import asyncio
import json
from types import SimpleNamespace

import pytest

from app.plus.blockchain_supervision import ai_guarded_review, ai_review_batch, semantic_review


@pytest.mark.asyncio
@pytest.mark.parametrize(('raw', 'reason'), [
    ('private response {', 'json_parse_error'),
    ('["private response"]', 'top_level_not_object'),
])
async def test_invalid_response_logs_safe_parse_reason_and_correlation(raw, reason, caplog):
    class Client:
        async def complete(self, *_args, **_kwargs):
            return raw

    caplog.set_level('WARNING', logger='app.plus.blockchain_supervision.semantic_review')
    with pytest.raises((json.JSONDecodeError, semantic_review.InvalidModelResponse)):
        await semantic_review.request_json(
            Client(), 'secret prompt', {'task_id': 7, 'items': []},
            diagnostic_context={'job_id': 'job-1', 'request_id': 'request-1', 'recovery_part': 'split_2'})
    for field in (f'reason={reason}', 'job_id=job-1', 'request_id=request-1', 'recovery_part=split_2',
                  f'output_chars={len(raw)}'):
        assert field in caplog.text
    assert 'private response' not in caplog.text
    assert 'secret prompt' not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(('proposals', 'reason'), [
    ('private value', 'proposals_not_list'),
    ([{}] * 201, 'proposals_limit_exceeded'),
])
async def test_invalid_batch_shape_logs_safe_reason(monkeypatch, caplog, proposals, reason):
    async def fake_request(*_args, **_kwargs):
        return {'proposals': proposals}

    monkeypatch.setattr(ai_review_batch, 'request_json', fake_request)
    caplog.set_level('WARNING', logger='app.plus.blockchain_supervision.ai_review_batch')
    with pytest.raises(ai_review_batch.InvalidBatchResponse):
        await ai_review_batch._request_review_json(
            object(), 'secret prompt', {'task_id': 7, 'items': []}, max_attempts=1,
            diagnostic_context={'job_id': 'job-1', 'recovery_part': 'split_2'})
    assert f'reason={reason}' in caplog.text
    assert 'job_id=job-1' in caplog.text
    assert 'recovery_part=split_2' in caplog.text
    assert 'private value' not in caplog.text
    assert 'secret prompt' not in caplog.text


@pytest.mark.asyncio
async def test_request_timeout_logs_safe_request_diagnostics(monkeypatch, caplog):
    class SlowClient:
        model = 'synthetic-model'

        async def complete(self, *_args, **_kwargs):
            await asyncio.sleep(0.02)
            return '{"proposals": []}'

    monkeypatch.setattr(semantic_review, 'MODEL_REQUEST_TIMEOUT_SECONDS', 0.001)
    caplog.set_level('WARNING', logger='app.plus.blockchain_supervision.semantic_review')
    with pytest.raises(TimeoutError):
        await semantic_review.request_json(
            SlowClient(), 'synthetic system', {'items': [{'text': 'private material'}]})
    message = caplog.text
    assert 'AI model request failed' in message
    assert 'model=synthetic-model' in message
    assert 'entries=1' in message
    assert 'exception_type=TimeoutError' in message
    assert 'timeout_phase=application_deadline' in message
    assert 'private material' not in message


@pytest.mark.asyncio
async def test_request_logs_provider_transport_timeout_phase(caplog):
    import httpx
    from openai import APITimeoutError

    class Client:
        model = 'synthetic-model'

        async def complete(self, *_args, **_kwargs):
            error = APITimeoutError(request=httpx.Request('POST', 'https://example.invalid'))
            error.__cause__ = httpx.ReadTimeout('private transport details')
            raise error

    caplog.set_level('WARNING', logger='app.plus.blockchain_supervision.semantic_review')
    with pytest.raises(APITimeoutError):
        await semantic_review.request_json(Client(), 'system', {'items': []})
    assert 'timeout_phase=read_timeout' in caplog.text
    assert 'private transport details' not in caplog.text


@pytest.mark.asyncio
async def test_request_keeps_clients_without_usage_callback_compatible():
    class Client:
        async def complete(self, _messages):
            return '{"proposals": []}'

    assert await semantic_review.request_json(Client(), 'system', {'items': []}) == {'proposals': []}


@pytest.mark.asyncio
async def test_invalid_diagnostic_identifiers_are_not_logged(caplog):
    class Client:
        model = 'private material\nforged log entry'

        async def complete(self, *_args, **_kwargs):
            raise RuntimeError('private exception')

    caplog.set_level('WARNING', logger='app.plus.blockchain_supervision.semantic_review')
    with pytest.raises(RuntimeError):
        await semantic_review.request_json(
            Client(), 'system', {'task_id': 'private task text', 'items': []},
            diagnostic_context={'model_code': 'private model text'})
    assert 'model=unknown' in caplog.text
    assert 'private' not in caplog.text
    assert 'forged log entry' not in caplog.text


@pytest.mark.asyncio
async def test_request_logs_safe_usage_and_preserves_billing_callback(caplog):
    usage = {
        'prompt_tokens': 17,
        'completion_tokens': 8,
        'total_tokens': 25,
        'private_field': 'must not be logged',
    }
    forwarded = []

    class Client:
        model = 'synthetic-model'
        timeout = 120

        async def complete(self, *_args, on_usage=None, **_kwargs):
            on_usage(usage)
            return '{"proposals": []}'

    caplog.set_level('INFO', logger='app.plus.blockchain_supervision.semantic_review')
    await semantic_review.request_json(
        Client(), 'system', {'items': []}, on_usage=forwarded.append,
        diagnostic_context={'model_code': 'qwen3.7flash'})
    assert forwarded == [usage]
    assert 'model_code=qwen3.7flash' in caplog.text
    assert 'client_timeout_seconds=120' in caplog.text
    assert 'input_tokens=17' in caplog.text
    assert 'output_tokens=8' in caplog.text
    assert 'total_tokens=25' in caplog.text
    assert 'must not be logged' not in caplog.text


@pytest.mark.asyncio
async def test_request_rate_limit_logs_batch_correlation_and_safe_provider_headers(caplog):
    class RateLimited(Exception):
        status_code = 429
        response = SimpleNamespace(headers={
            'x-request-id': 'provider-req-42',
            'retry-after': '3',
            'authorization': 'private-token',
        })

    class Client:
        model = 'synthetic-model'

        async def complete(self, *_args, **_kwargs):
            raise RateLimited('private material and token')

    caplog.set_level('WARNING', logger='app.plus.blockchain_supervision.semantic_review')
    with pytest.raises(RateLimited):
        await semantic_review.request_json(
            Client(), 'synthetic system', {'task_id': 7, 'items': [{}, {}]},
            diagnostic_context={
                'job_id': 'job-1', 'request_id': 'request-1', 'group_id': 'a2',
                'model_code': 'qwen3.7flash',
                'batch_index': 4, 'batch_total': 12, 'recovery_part': 'split_1',
                'prompt': 'must not enter diagnostics',
            })
    message = caplog.text
    for field in ('task_id=7', 'job_id=job-1', 'request_id=request-1', 'group_id=a2',
                  'model_code=qwen3.7flash',
                  'batch_index=4', 'batch_total=12', 'recovery_part=split_1',
                  'status_code=429', 'provider_request_id=provider-req-42', 'retry_after_seconds=3'):
        assert field in message
    assert 'private-token' not in message
    assert 'private material' not in message
    assert 'must not enter diagnostics' not in message


@pytest.mark.asyncio
@pytest.mark.parametrize(('code', 'expected'), [
    ('AccessDenied.Unpurchased', 'provider_error_code=AccessDenied.Unpurchased'),
    ('private-business-token', 'provider_error_code=None'),
    ({'unexpected': 'private-business-token'}, 'provider_error_code=None'),
])
async def test_model_denial_logs_only_allowlisted_provider_error_code(caplog, code, expected):
    class Denied(Exception):
        status_code = 403

    class Client:
        async def complete(self, *_args, **_kwargs):
            error = Denied('private response body')
            error.code = code
            raise error

    caplog.set_level('WARNING', logger='app.plus.blockchain_supervision.semantic_review')
    with pytest.raises(Denied):
        await semantic_review.request_json(Client(), 'private prompt', {'items': []})
    assert expected in caplog.text
    assert 'private-business-token' not in caplog.text
    assert 'private response body' not in caplog.text
    assert 'private prompt' not in caplog.text


@pytest.mark.asyncio
async def test_split_recovery_keeps_batch_correlation_outside_model_payload(monkeypatch):
    calls = []

    async def fake_request_json(_client, _system, payload, *, diagnostic_context=None, **_kwargs):
        calls.append((payload, dict(diagnostic_context or {})))
        if diagnostic_context['recovery_part'] == 'initial':
            raise TimeoutError('synthetic timeout')
        return {'proposals': []}

    monkeypatch.setattr(ai_review_batch, 'request_json', fake_request_json)
    batch = {
        'task_id': 7,
        'subject_id': 1,
        'items': [
            {
                'finding_id': finding_id,
                'item_id': f'RULE-{finding_id}',
                'subject_id': 1,
                'references': [{'reference_id': 'ref-1', 'text': 'synthetic rule'}],
                'limitations': [],
                'material': {
                    'task_id': 7, 'subject_id': 1, 'material_id': 9,
                    'page': 1, 'text': f'synthetic fact {finding_id}',
                    'text_start': 0, 'text_end': len(f'synthetic fact {finding_id}'),
                },
            }
            for finding_id in (11, 12)
        ],
    }
    output = await ai_guarded_review._execute_with_timeout_recovery(
        batch, client=object(), diagnostic_context={
            'job_id': 'job-1', 'request_id': 'request-1', 'group_id': 'a2',
            'batch_index': 4, 'batch_total': 12,
        })
    assert len(calls) == 3
    assert [context['recovery_part'] for _, context in calls] == ['initial', 'split_1', 'split_2']
    for payload, context in calls:
        assert context['job_id'] == 'job-1'
        assert context['request_id'] == 'request-1'
        assert context['group_id'] == 'a2'
        assert context['batch_index'] == 4 and context['batch_total'] == 12
        assert 'job_id' not in payload and 'request_id' not in payload
    assert len(output) == 2
    assert all(row['execution_status'] == 'completed' for row in output)


@pytest.mark.asyncio
async def test_model_batch_scheduler_keeps_concurrency_bounded(monkeypatch):
    monkeypatch.setattr(ai_guarded_review, '_MODEL_BATCH_CONCURRENCY', 2)
    active = 0
    maximum = 0

    async def execute(_index, _batch):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return _index

    result = await ai_guarded_review._run_bounded_batches([{}, {}, {}, {}], execute)
    assert result == [1, 2, 3, 4]
    assert maximum == 2
