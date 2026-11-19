import asyncio
from copy import deepcopy
import json

import pytest

from app.plus.blockchain_supervision import retrospective_visual as visual
from app.plus.blockchain_supervision.retrospective_analysis import ExecutionBoundaryError
from app.plus.blockchain_supervision.semantic_review import InputBudgetExceeded, request_json

PNG = b'\x89PNG\r\n\x1a\nsynthetic'


def sample():
    refs = [{'material_id': 1, 'page': 39, 'quote': '因发包人原因导致工期延长。', 'clause': '12.1'},
            {'material_id': 2, 'page': 31, 'quote': '报价不包含检测费用。', 'clause': '(19)'}]
    return {'identity': {'visual_version': visual.VERSION, 'model_code': 'test', 'model_identity': {}},
            'execution_status': 'completed', 'summary': '原摘要', 'limitations': [],
            'findings': [{'finding_id': 'A', 'evidence': [refs[0]]},
                         {'finding_id': 'B', 'evidence': refs}]}, [
            {'material_id': i, 'file_sha256': str(i) * 64} for i in (1, 2)]


class Client:
    supports_json_schema = True

    def __init__(self, *, state='matches', error=None):
        self.calls = []
        self.state = state
        self.error = error

    async def complete(self, messages, on_usage=None, response_schema=None):
        assert response_schema['additionalProperties'] is False
        content = messages[1]['content']
        assert content[1]['image_url']['url'].startswith('data:image/png;base64,')
        payload = json.loads(content[0]['text'])
        self.calls.append(payload)
        if self.error:
            raise self.error
        if on_usage:
            on_usage({'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30})
        return json.dumps({'checks': [{'reference_id': key, 'readable': self.state != 'unreadable',
            'observed_quote': (ref['quote'].replace('发包人', '分包人') if self.state == 'differs'
                               else ref['quote']), 'reason': '独立读取原页相应文字。'}
            for key, ref in payload['references'].items()]})


def run(result=None, sources=None, client=None, **kwargs):
    default, default_sources = sample()
    return asyncio.run(visual.verify_findings(result or default, sources or default_sources,
        client=client or Client(), render_page=kwargs.pop('render_page', lambda *args: PNG), **kwargs))


def test_dedup_shared_pages_and_both_contract_sides_with_stable_resume():
    client, cache, usage, started = Client(), {}, [], []
    report = run(client=client, load_checkpoint=cache.get,
                 save_checkpoint=lambda key, row: cache.update({key: row}),
                 before_batch=lambda *args: started.append(args), on_usage=usage.append)
    assert len(client.calls) == len(usage) == len(cache) == 2
    assert {p['material_id'] for p in client.calls} == {1, 2}
    assert all(len(p['references']) == 1 for p in client.calls)
    assert report['citation_visual']['state'] == 'completed'
    assert len(report['findings']) == 2
    replay = run(client=client, load_checkpoint=cache.get, on_usage=usage.append)
    assert replay == report and len(client.calls) == len(usage) == 2
    assert all(row[0].startswith('visual:') for row in started)


def test_responsibility_misread_excludes_only_affected_findings_and_keeps_audit():
    original, sources = sample()
    original['findings'].append({'finding_id': 'C', 'evidence': [original['findings'][1]['evidence'][1]]})
    before = deepcopy(original)
    report = run(original, sources, Client(state='differs'))
    # 服务器从原页观察逐字比较，模型不负责选择匹配状态。
    assert [row['finding_id'] for row in report['findings']] == ['C']
    assert report['execution_status'] == 'partial'
    assert report['citation_visual']['findings_excluded'] == 2
    assert report['citation_visual']['pages'][0]['checks'][0]['observed_quote'] == '因分包人原因导致工期延长。'
    assert report['citation_visual']['pages'][1]['checks'][0]['state'] == 'matches'
    assert original == before
    assert report['excluded_findings'][0]['finding'] == before['findings'][0]


def test_unaffected_finding_is_retained_when_other_page_differs():
    class Mixed(Client):
        async def complete(self, messages, **kwargs):
            payload = json.loads(messages[1]['content'][0]['text'])
            self.state = 'differs' if payload['material_id'] == 1 else 'matches'
            return await super().complete(messages, **kwargs)
    original, sources = sample()
    original['findings'].append({'finding_id': 'C', 'evidence': [original['findings'][1]['evidence'][1]]})
    report = run(original, sources, Mixed())
    assert [f['finding_id'] for f in report['findings']] == ['C']


@pytest.mark.parametrize('checks', [[], [
    {'reference_id': 'V001', 'readable': True, 'observed_quote': '合同原文。', 'reason': '核对。'}] * 2,
    [{'reference_id': 'unknown', 'readable': True, 'observed_quote': '合同原文。', 'reason': '核对。'}]])
def test_missing_duplicate_or_unknown_reference_cannot_pass(checks):
    with pytest.raises(ValueError):
        visual.validate_page({'checks': checks}, {'V001': {'quote': '合同原文。'}})


def test_match_requires_observed_exact_text_only_whitespace_is_ignored():
    refs = {'V001': {'quote': '分包人承担责任。'}}
    def check(text):
        return visual.validate_page({'checks': [{'reference_id': 'V001', 'readable': True,
            'observed_quote': text, 'reason': '原页读取。'}]}, refs)[0]['state']
    assert check('分包人\n承担责任。') == 'matches'
    assert check('发包人承担责任。') == 'differs'


def test_provider_failure_stops_without_retry_and_does_not_confirm_remaining_pages():
    client, cache = Client(error=TimeoutError()), {}
    report = run(client=client, save_checkpoint=lambda key, row: cache.update({key: row}))
    assert len(client.calls) == len(cache) == 1
    assert report['citation_visual']['pages_total'] == 2
    assert report['citation_visual']['pages_checked'] == 0
    assert not report['findings']
    run(client=client, load_checkpoint=cache.get)
    assert len(client.calls) == 1


def test_unknown_started_request_is_not_rebilled():
    client = Client()
    report = run(client=client, load_checkpoint=lambda key: {'state': 'failed', 'checks': []})
    assert client.calls == [] and not report['findings']


def test_accounting_failure_stops_all_following_requests():
    client = Client()
    def fail(_raw):
        raise RuntimeError('ledger unavailable')
    with pytest.raises(ExecutionBoundaryError):
        run(client=client, on_usage=fail)
    assert len(client.calls) == 1


def test_original_change_stops_before_provider():
    client = Client()
    def changed(*args):
        raise ValueError('original changed')
    with pytest.raises(ValueError, match='original changed'):
        run(client=client, render_page=changed)
    assert client.calls == []


def test_version_and_cached_binding_cannot_be_mixed():
    result, sources = sample()
    result['identity']['visual_version'] = 'old'
    with pytest.raises(ValueError, match='版本'):
        run(result, sources)
    with pytest.raises(ValueError, match='身份'):
        run(load_checkpoint=lambda key: {'state': 'completed', 'binding_sha256': 'changed', 'checks': []})


def test_image_budget_rejected_before_provider_and_images_are_not_logged(caplog):
    client = Client()
    with pytest.raises(InputBudgetExceeded):
        asyncio.run(request_json(client, 'system', {}, images=[b'not png']))
    assert not client.calls
    run(client=client)
    assert 'data:image' not in caplog.text and 'base64' not in caplog.text


def test_no_findings_does_not_render_or_call():
    result, sources = sample()
    result['findings'] = []
    client = Client()
    report = run(result, sources, client, render_page=lambda *args: pytest.fail('unexpected render'))
    assert report['citation_visual']['pages_total'] == 0
    assert client.calls == []
