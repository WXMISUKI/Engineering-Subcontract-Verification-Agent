"""合成模型失败时不生成误导性的已完成辅助核查结论。"""
import socket

import pytest

from app.plus.blockchain_supervision.ai_review_batch import plan_review_batches, execute_review_batch
from app.plus.startup_conditions_check.llm import OpenAICompatibleClient


def _batch():
    item = {'finding_id': 1, 'task_id': 1, 'subject_id': 1, 'item_id': 'synthetic-rule',
            'materials': [{'task_id': 1, 'subject_id': 1, 'material_id': 1, 'page': 1,
                           'text': 'synthetic private material'}],
            'references': [{'reference_id': 'synthetic-reference', 'text': 'synthetic rule text'}]}
    plan = plan_review_batches([item])
    assert len(plan['batches']) == 1 and not plan['limitations']
    return plan['batches'][0]


@pytest.mark.asyncio
@pytest.mark.parametrize('error,category', [
    (TimeoutError('synthetic-secret-timeout'), 'timeout'),
    (ConnectionError('synthetic-secret-connection'), 'model_call_error'),
])
async def test_model_failure_is_not_reported_as_completed(error, category, caplog):
    class FailingClient:
        calls = 0

        async def complete(self, *_args, **_kwargs):
            self.calls += 1
            raise error

    client = FailingClient()
    results = await execute_review_batch(client=client, batch=_batch(), max_attempts=1)
    assert client.calls == 1
    assert len(results) == 1
    assert results[0]['execution_status'] == 'failed'
    assert results[0]['failure_category'] == category
    assert results[0]['proposals'] == []
    assert results[0]['limitations']
    assert 'synthetic-secret' not in str(results) + caplog.text
    assert 'synthetic private material' not in caplog.text


@pytest.mark.asyncio
async def test_real_client_connection_failure_is_not_reported_as_success(caplog):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
        client = OpenAICompatibleClient(f'http://127.0.0.1:{port}/v1', 'synthetic-secret', 'synthetic-model', timeout=1)
        result = (await execute_review_batch(client=client, batch=_batch(), max_attempts=1))[0]
    assert result['execution_status'] == 'failed'
    # A bound but non-listening socket may be rejected or time out on Windows.
    assert result['failure_category'] in {'model_call_error', 'timeout'}
    assert result['proposals'] == [] and result['limitations']
    assert 'synthetic-secret' not in str(result) + caplog.text
