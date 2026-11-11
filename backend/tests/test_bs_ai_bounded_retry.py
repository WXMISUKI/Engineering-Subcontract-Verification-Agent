import asyncio
import json

import httpx
import openai
import pytest

from app.plus.blockchain_supervision import ai_guarded_review, ai_review_batch
from app.plus.startup_conditions_check.llm import OpenAICompatibleClient


def batch():
    return {'task_id': 7, 'subject_id': 1, 'items': [
        {'finding_id': i, 'item_id': f'RULE-{i}', 'subject_id': 1,
         'references': [{'reference_id': 'ref', 'text': 'synthetic'}], 'limitations': [],
         'material': {'task_id': 7, 'subject_id': 1, 'material_id': 9, 'page': 1,
                      'text': 'synthetic fact', 'text_start': 0, 'text_end': 14}}
        for i in (1, 2)
    ]}


@pytest.mark.asyncio
@pytest.mark.parametrize(('scenario', 'expected_calls', 'expected_status'), [
    ('transient_429', 2, 'completed'),
    ('transient_500', 2, 'completed'),
    ('persistent_429', 2, 'failed'),
    ('long_retry_after', 1, 'failed'),
    ('provider_forbids_retry', 1, 'failed'),
    ('authentication', 1, 'failed'),
    ('persistent_timeout', 3, 'failed'),
    ('large_timeout', 3, 'completed'),
    ('partial_split', 3, 'partial'),
])
async def test_batch_recovery_bounds_http_requests(monkeypatch, scenario, expected_calls, expected_status):
    calls = []

    def handler(request):
        body = json.loads(request.content)
        items = json.loads(body['messages'][1]['content'])['items']
        calls.append(len(items))
        if scenario == 'persistent_timeout' or (scenario in ('large_timeout', 'partial_split') and len(items) > 1):
            raise httpx.ReadTimeout('synthetic', request=request)
        if scenario == 'partial_split' and len(calls) == 3:
            raise httpx.ReadTimeout('synthetic', request=request)
        if scenario == 'authentication':
            return httpx.Response(401, json={'error': {'message': 'synthetic'}})
        if scenario == 'provider_forbids_retry':
            return httpx.Response(429, headers={'x-should-retry': 'false'}, json={'error': {'message': 'synthetic'}})
        if scenario in ('persistent_429', 'long_retry_after') or (scenario == 'transient_429' and len(calls) == 1):
            return httpx.Response(429, headers={'retry-after': '100' if scenario == 'long_retry_after' else '0'},
                                  json={'error': {'message': 'synthetic'}})
        if scenario == 'transient_500' and len(calls) == 1:
            return httpx.Response(500, json={'error': {'message': 'synthetic'}})
        return httpx.Response(200, json={
            'id': 'synthetic', 'object': 'chat.completion', 'created': 0, 'model': 'synthetic',
            'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': '{"proposals": []}'}}],
        })

    original = openai.AsyncOpenAI

    def create_client(**kwargs):
        return original(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), **kwargs)

    async def no_wait(_delay):
        pass

    monkeypatch.setattr(openai, 'AsyncOpenAI', create_client)
    monkeypatch.setattr(ai_review_batch, '_sleep_before_retry', no_wait)
    client = OpenAICompatibleClient('https://example.invalid/v1', 'synthetic', 'synthetic', timeout=1, max_retries=0)
    rows = await ai_guarded_review._execute_with_timeout_recovery(batch(), client=client)
    assert len(calls) == expected_calls
    if scenario == 'partial_split':
        assert [row['execution_status'] for row in rows] == ['completed', 'failed']
        assert not ai_guarded_review._batch_checkpointable(rows)
    else:
        assert all(row['execution_status'] == expected_status for row in rows)
    if scenario == 'large_timeout':
        assert calls == [2, 1, 1]


@pytest.mark.asyncio
async def test_shared_budget_spans_retry_and_split(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={'retry-after': '0'}, json={'error': {'message': 'synthetic'}})
        raise httpx.ReadTimeout('synthetic', request=request)

    original = openai.AsyncOpenAI
    monkeypatch.setattr(openai, 'AsyncOpenAI', lambda **kwargs: original(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), **kwargs))
    async def no_wait(_delay):
        pass
    monkeypatch.setattr(ai_review_batch, '_sleep_before_retry', no_wait)
    client = OpenAICompatibleClient('https://example.invalid/v1', 'synthetic', 'synthetic', timeout=1, max_retries=0)
    rows = await ai_guarded_review._execute_with_timeout_recovery(batch(), client=client)
    assert len(calls) == 4
    assert all(row['execution_status'] == 'failed' for row in rows)


@pytest.mark.asyncio
async def test_batch_deadline_caps_request_time_and_prevents_split(monkeypatch):
    calls = []
    async def slow_request(_client, _system, _payload, **kwargs):
        calls.append(kwargs['timeout_seconds'])
        await asyncio.sleep(kwargs['timeout_seconds'])
        raise TimeoutError('synthetic')
    monkeypatch.setattr(ai_review_batch, 'request_json', slow_request)
    original_budget = ai_review_batch.BatchRequestBudget
    monkeypatch.setattr(ai_review_batch, 'BatchRequestBudget', lambda: original_budget(max_seconds=0.02))
    rows = await ai_guarded_review._execute_with_timeout_recovery(batch(), client=object())
    assert len(calls) == 1
    assert 0 < calls[0] <= 0.02
    assert all(row['execution_status'] == 'failed' for row in rows)


@pytest.mark.asyncio
async def test_cancelled_batch_does_not_split_or_retry(monkeypatch):
    calls = []
    async def cancelled(*_args, **_kwargs):
        calls.append(1)
        raise asyncio.CancelledError()
    monkeypatch.setattr(ai_review_batch, 'request_json', cancelled)
    with pytest.raises(asyncio.CancelledError):
        await ai_guarded_review._execute_with_timeout_recovery(batch(), client=object())
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_retry_after_is_honored_without_changing_billing_callback(monkeypatch):
    waits, usages, calls = [], [], []
    class RateLimited(Exception):
        status_code = 429
        response = httpx.Response(429, headers={'retry-after': '2'})
    async def request(_client, _system, _payload, *, on_usage=None, **_kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RateLimited()
        on_usage({'prompt_tokens': 8})
        return {'proposals': []}
    async def capture_wait(delay):
        waits.append(delay)
    monkeypatch.setattr(ai_review_batch, 'request_json', request)
    monkeypatch.setattr(ai_review_batch, '_sleep_before_retry', capture_wait)
    rows = await ai_guarded_review._execute_with_timeout_recovery(batch(), client=object(), on_usage=usages.append)
    assert len(waits) == 1 and 2.5 <= waits[0] <= 3.0
    assert usages == [{'prompt_tokens': 8}]
    assert all(row['execution_status'] == 'completed' for row in rows)


@pytest.mark.asyncio
async def test_sdk_retry_setting_is_scoped_to_batch_client(monkeypatch):
    retry_settings = []
    class FakeSdk:
        def __init__(self, **kwargs):
            retry_settings.append(kwargs.get('max_retries', 'sdk-default'))
            self.chat = type('Chat', (), {'completions': self})()
        async def create(self, **_kwargs):
            return type('Response', (), {'choices': [type('Choice', (), {
                'message': type('Message', (), {'content': '{}'})()})()]})()
        async def close(self):
            pass
    monkeypatch.setattr(openai, 'AsyncOpenAI', FakeSdk)
    await OpenAICompatibleClient('https://example.invalid/v1', 'synthetic', 'synthetic').complete([])
    await OpenAICompatibleClient('https://example.invalid/v1', 'synthetic', 'synthetic', max_retries=0).complete([])
    assert retry_settings == ['sdk-default', 0]
