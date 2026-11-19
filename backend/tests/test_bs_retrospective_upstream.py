import asyncio
import json

import pytest

from app.plus.blockchain_supervision.retrospective_sources import analysis_batches
from app.plus.blockchain_supervision.retrospective_upstream_contracts import (
    TOPICS, bind_extraction, bind_relationship, validate_upstream_candidate,
)


def sources():
    return [{'material_id': mid, 'file_name': name, 'role': role, 'relation': 'main',
             'total_pages': 1, 'pages': [{'page': 1, 'text': text, 'readable': True}],
             'reading_coverage': {'reading_complete': True, 'blank_pages': []}}
            for mid, name, role, text in [
                (1, '总包.pdf', 'general', '工程名称：嘉松工程。承包人：建工公司。范围：桥梁吊装。质量检测由业主指定机构完成，承包人仅负责配合。'),
                (2, '分包.pdf', 'sub', '总包工程名称：嘉松工程。发包人：建工公司。范围：桥梁吊装。质量检测由分包人委托，全部检测费用由分包人承担。')]]


def ref(mid, quote):
    return {'material_id': mid, 'page': 1, 'quote': quote, 'clause': ''}


def candidate():
    return {'kind': 'upstream_risk', 'time_scope': 'contract_text',
            'message': '总包约定由业主指定检测机构，分包却约定自行委托，同一检测对象的委托主体及费用边界尚需明确。',
            'conditions': '两侧约定涉及同一检测对象且无另行变更。', 'applicability': '同一桥梁吊装工程的检测责任。',
            'basis': [], 'evidence': [ref(1, '质量检测由业主指定机构完成，承包人仅负责配合。'),
                                     ref(2, '质量检测由分包人委托，全部检测费用由分包人承担。')]}


def test_extraction_restores_exact_source_and_retains_conditions():
    term = {'topic': 'quality', 'statement': '检测由业主指定机构完成', 'obligation_holder': 'employer',
            'transmission': 'reserved', 'conditions': '同一检测对象', 'exceptions': '另行约定除外',
            'priority': '专用条款', 'evidence': [candidate()['evidence'][0]]}
    raw = {'checked_topics': list(TOPICS), 'facts': [{'field': 'project_name', 'value': '嘉松工程', 'evidence': [ref(1, '工程名称：嘉松工程。')]}],
           'terms': [term]}
    bound = bind_extraction(raw, analysis_batches(sources()[:1])[0], sources())
    assert bound['facts'][0]['evidence'][0]['role'] == 'general'
    assert bound['terms'][0]['exceptions'] == '另行约定除外'
    assert bound['terms'][0]['term_id']
    raw['facts'][0]['value'] = '另一个工程'
    partial = bind_extraction(raw, analysis_batches(sources()[:1])[0], sources())
    assert partial['state'] == 'partial' and not partial['facts']
    assert partial['terms'] == bound['terms'] and len(partial['rejected']) == 1


def test_extraction_cannot_borrow_the_other_contract():
    rejected = bind_extraction({'checked_topics': list(TOPICS), 'facts': [], 'terms': [{'topic': 'quality', 'statement': '检测责任不同',
            'obligation_holder': 'sub', 'transmission': 'pass_through', 'conditions': '', 'exceptions': '',
            'priority': '', 'evidence': [candidate()['evidence'][1]]}]}, analysis_batches(sources()[:1])[0], sources())
    assert rejected['state'] == 'failed' and not rejected['terms']


def test_invalid_summary_quote_does_not_discard_valid_identity_or_become_evidence():
    raw = {'checked_topics': list(TOPICS), 'facts': [
        {'field': 'project_name', 'value': '嘉松工程', 'evidence': [ref(1, '工程名称：嘉松工程。')]}],
        'terms': [{'topic': 'quality', 'statement': '检测责任与配合范围', 'obligation_holder': 'employer',
                   'transmission': 'reserved', 'conditions': '', 'exceptions': '', 'priority': '',
                   'evidence': [ref(1, '质量检测由业主...承包人仅负责配合。')]}]}
    result = bind_extraction(raw, analysis_batches(sources()[:1])[0], sources())
    assert result['state'] == 'partial' and result['facts'][0]['value'] == '嘉松工程'
    assert not result['terms'] and result['rejected'][0]['kind'] == 'terms'


def test_same_page_quote_may_cross_contiguous_parts_but_not_unprovided_text():
    src = sources()[:1]
    text = '1.1 工程名称：嘉松工程。\n1.2 承包人：建工公司。'
    src[0]['pages'][0]['text'] = text
    batch = analysis_batches(src)[0]
    assert len(batch['parts']) > 1
    raw = {'checked_topics': list(TOPICS), 'facts': [
        {'field': 'project_name', 'value': '嘉松工程', 'evidence': [ref(1, text)]}], 'terms': []}
    assert bind_extraction(raw, batch, src)['state'] == 'completed'
    omitted = {**batch, 'parts': batch['parts'][:1]}
    assert bind_extraction(raw, omitted, src)['state'] == 'failed'


def test_relationship_requires_all_dimensions_and_both_sides():
    dims = {'project': ('project_name', 'project_name', '嘉松工程'),
            'parties': ('contractor', 'employer', '建工公司'), 'scope': ('scope', 'scope', '桥梁吊装')}
    facts = [{'field': field, 'value': value, 'evidence': [{'role': role}]}
             for left, right, value in dims.values()
             for field, role in [(left, 'general'), (right, 'sub')]]
    checks = [{'dimension': dim, 'state': 'matched', 'reason': '同一项目主体及施工范围相衔接。',
               'evidence': [ref(1, value), ref(2, value)]} for dim, (_, _, value) in dims.items()]
    assert bind_relationship({'checks': checks}, sources(), facts)['state'] == 'matched'
    checks[1]['state'] = 'mismatch'
    assert bind_relationship({'checks': checks}, sources(), facts)['state'] == 'mismatch'
    checks[1]['evidence'] = [ref(1, '嘉松工程'), ref(2, '嘉松工程')]
    with pytest.raises(ValueError):
        bind_relationship({'checks': checks}, sources(), facts)
    checks[1]['evidence'] = checks[1]['evidence'][:1]
    with pytest.raises(ValueError):
        bind_relationship({'checks': checks}, sources(), facts)


@pytest.mark.parametrize('fault', ['one_side', 'fabricated', 'law', 'current', 'basis'])
def test_upstream_finding_rejects_unverifiable_or_wrong_semantics(fault):
    row = candidate()
    if fault == 'one_side': row['evidence'] = row['evidence'][:1] * 2
    elif fault == 'fabricated': row['evidence'][1]['quote'] = '原文没有的约定'
    elif fault == 'law': row['message'] += '该约定构成违法。'
    elif fault == 'current': row['time_scope'] = 'current'
    else: row['basis'] = [{'clause_id': '总包约定'}]
    with pytest.raises(ValueError):
        validate_upstream_candidate(row, sources())


def test_upstream_finding_binds_both_roles_without_a_fake_statute():
    row = validate_upstream_candidate(candidate(), sources())
    assert {ref['role'] for ref in row['evidence']} == {'general', 'sub'}
    assert not row['basis'] and row['finding_id']


class UpstreamClient:
    supports_json_schema = True

    def __init__(self, *, mismatch=False, uncertain=False, no_risk=False):
        self.calls = []
        self.mismatch, self.uncertain, self.no_risk = mismatch, uncertain, no_risk

    async def complete(self, messages, on_usage=None, response_schema=None):
        assert response_schema and response_schema['additionalProperties'] is False
        payload = json.loads(messages[1]['content'])
        self.calls.append(payload)
        if on_usage:
            on_usage({'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30})
        phase = payload.get('analysis_type')
        if phase == 'upstream_extract':
            general = payload['role'] == 'general'
            mid = 1 if general else 2
            value = {'checked_topics': list(TOPICS), 'facts': [
                {'field': 'project_name', 'value': '嘉松工程', 'evidence': [ref(mid, '嘉松工程')]},
                {'field': 'contractor' if general else 'employer', 'value': '建工公司', 'evidence': [ref(mid, '建工公司')]},
                {'field': 'scope', 'value': '桥梁吊装', 'evidence': [ref(mid, '桥梁吊装')]},
            ], 'terms': [{'topic': 'quality', 'statement': '工程检测的责任与费用约定',
                'obligation_holder': 'employer' if general else 'sub',
                'transmission': 'reserved' if general else 'pass_through',
                'conditions': '同一检测对象', 'exceptions': '', 'priority': '专用条款',
                'evidence': [candidate()['evidence'][0 if general else 1]]}]}
        elif phase == 'upstream_relationship':
            value = {'checks': [{'dimension': dim,
                'state': 'mismatch' if self.mismatch else 'uncertain' if self.uncertain else 'matched',
                'reason': '双侧身份事实及范围已分别对照。', 'evidence': [ref(1, text), ref(2, text)]}
                for dim, text in [('project', '嘉松工程'), ('parties', '建工公司'), ('scope', '桥梁吊装')]]}
        elif phase == 'upstream_compare':
            value = {'checks': [{'term_id': term['term_id'], 'state': 'aligned' if self.no_risk else 'risk',
                'reason': candidate()['message'], 'applicability': candidate()['applicability'],
                'conditions': candidate()['conditions'], 'evidence': candidate()['evidence']}
                for term in payload['upstream_terms']]}
        elif 'candidates' in payload:
            value = {'decisions': [{'finding_id': row['finding_id'], 'keep': True, 'reason': '双侧原文支撑同对象的约定差异。',
                                   'message': row['message']} for row in payload['candidates']]}
        else:
            raise AssertionError(phase)
        return json.dumps(value, ensure_ascii=False)


def test_compare_executes_every_term_and_restores_without_provider_calls():
    from app.plus.blockchain_supervision.retrospective_upstream import compare
    client, saved, started, usage = UpstreamClient(), {}, [], []
    def run():
        return asyncio.run(compare(sources(), {'baseline_date': '2026-11-19'}, client=client,
            load_checkpoint=saved.get, before_batch=lambda key, i, n: started.append(key),
            save_checkpoint=lambda key, value: saved.update({key: value}), on_usage=usage.append))
    result = run()
    assert result['state'] == 'completed'
    assert result['coverage']['batches_completed'] == 2
    assert result['coverage']['counts']['risk'] == 1
    assert len(result['findings']) == 1
    assert len(client.calls) == len(set(started)) == len(usage) == 5
    assert run() == result and len(client.calls) == 5


@pytest.mark.parametrize('state', ['mismatch', 'uncertain'])
def test_wrong_or_unknown_relationship_never_enters_normal_comparison(state):
    from app.plus.blockchain_supervision.retrospective_upstream import compare
    client = UpstreamClient(**{state: True})
    result = asyncio.run(compare(sources(), {}, client=client))
    assert result['relationship']['state'] == state
    assert not result['findings'] and not result['checks']
    assert all(payload.get('analysis_type') != 'upstream_compare' for payload in client.calls)


def test_comparison_must_account_for_every_upstream_term():
    from app.plus.blockchain_supervision.retrospective_upstream import bind_comparison
    with pytest.raises(ValueError):
        bind_comparison({'checks': []}, [{'term_id': 'one'}], [], [], sources())


def test_usage_failure_stops_before_next_upstream_call():
    from app.plus.blockchain_supervision.retrospective_upstream import compare
    from app.plus.blockchain_supervision.retrospective_analysis import ExecutionBoundaryError
    client = UpstreamClient()
    def fail(raw):
        raise RuntimeError('synthetic accounting failure')
    with pytest.raises(ExecutionBoundaryError):
        asyncio.run(compare(sources(), {}, client=client, on_usage=fail))
    assert len(client.calls) == 1


def test_provider_timeout_stops_new_upstream_requests_without_claiming_coverage():
    from app.plus.blockchain_supervision.retrospective_upstream import compare
    class TimeoutClient:
        calls = 0
        async def complete(self, *args, **kwargs):
            self.calls += 1
            raise TimeoutError('synthetic provider timeout')
    client = TimeoutClient()
    result = asyncio.run(compare(sources(), {}, client=client))
    assert client.calls == 1 and result['state'] == 'partial'
    assert result['coverage']['batches_completed'] == 0
    assert result['extraction'][1]['state'] == 'not_executed'


def test_interrupted_extraction_is_not_called_again_or_reported_complete():
    from app.plus.blockchain_supervision.retrospective_upstream import compare
    client = UpstreamClient()
    result = asyncio.run(compare(sources(), {}, client=client,
        load_checkpoint=lambda key: {'state': 'failed', 'findings': [], 'limitations': ['已受理请求中断']}))
    assert client.calls == [] and result['state'] == 'partial'
    assert result['coverage']['batches_completed'] == 0
    assert not result['findings']


def test_no_risk_does_not_require_a_minimum_finding_count():
    from app.plus.blockchain_supervision.retrospective_upstream import compare
    client = UpstreamClient(no_risk=True)
    result = asyncio.run(compare(sources(), {}, client=client))
    assert result['state'] == 'completed' and not result['findings']
    assert result['coverage']['counts']['aligned'] == 1


def test_automatic_rejection_is_retained_as_a_decision_without_report_risk():
    from app.plus.blockchain_supervision.retrospective_upstream import compare
    class Reject(UpstreamClient):
        async def complete(self, messages, on_usage=None, response_schema=None):
            payload = json.loads(messages[1]['content'])
            if 'candidates' in payload:
                assert payload['upstream_context']['relationship']['state'] == 'matched'
                return json.dumps({'decisions': [{'finding_id': row['finding_id'], 'keep': False,
                    'reason': '引用涉及不同的检测范围，不能确认同一对象的约定冲突。', 'message': ''}
                    for row in payload['candidates']]})
            return await super().complete(messages, on_usage, response_schema)
    result = asyncio.run(compare(sources(), {'baseline_date': '2026-11-19'}, client=Reject()))
    assert result['state'] == 'completed' and not result['findings']
    assert result['coverage']['counts']['dismissed'] == 1
    assert result['automatic_review']['automatic_review'][0]['keep'] is False


def test_pending_comparison_never_becomes_an_omission_finding():
    from app.plus.blockchain_supervision.retrospective_upstream import compare
    class MissingCounterpart(UpstreamClient):
        async def complete(self, messages, on_usage=None, response_schema=None):
            payload = json.loads(messages[1]['content'])
            if payload.get('analysis_type') == 'upstream_compare':
                return json.dumps({'checks': [{'term_id': term['term_id'], 'state': 'pending',
                    'reason': '尚未找到同一检测对象的对应条款，无法确认是否适用。',
                    'applicability': '', 'conditions': '', 'evidence': []}
                    for term in payload['upstream_terms']]})
            return await super().complete(messages, on_usage, response_schema)
    result = asyncio.run(compare(sources(), {}, client=MissingCounterpart()))
    assert result['state'] == 'partial' and not result['findings']
    assert result['coverage']['counts']['pending'] == 1


def test_comparison_cannot_use_unprovided_counterpart_or_call_law_a_contract():
    from app.plus.blockchain_supervision.retrospective_upstream import bind_comparison
    src = sources()
    term = {'term_id': 'quality', 'evidence': [candidate()['evidence'][0]]}
    check = {'term_id': 'quality', 'state': 'risk', 'reason': candidate()['message'],
             'conditions': candidate()['conditions'], 'applicability': candidate()['applicability'],
             'evidence': candidate()['evidence']}
    with pytest.raises(ValueError, match='未提供'):
        bind_comparison({'checks': [check]}, [term], [], [], src)


def test_payment_percentage_and_reserved_insurance_can_close_without_risk():
    """验证合理排除可持久化收口；模拟模型不证明真实Provider语义准确率。"""
    from app.plus.blockchain_supervision.retrospective_upstream import bind_comparison
    src = sources()
    src[0]['pages'][0]['text'] += '进度款支付85%。业主办理建筑工程一切险。'
    src[1]['pages'][0]['text'] += '进度款支付80%。分包人另行办理雇主责任险。'
    for topic, left, right, state in [
            ('payment', '进度款支付85%。', '进度款支付80%。', 'aligned'),
            ('insurance', '业主办理建筑工程一切险。', '分包人另行办理雇主责任险。', 'not_applicable')]:
        term = {'term_id': topic, 'evidence': [ref(1, left)]}
        sub = {'term_id': topic + '-sub', 'evidence': [ref(2, right)]}
        result = bind_comparison({'checks': [{'term_id': topic, 'state': state,
            'reason': '两侧合同的具体付款对象或保险标的不同，单凭比例或险种差异不认定风险。',
            'applicability': '核对合同对象及费用边界。', 'conditions': '未见相反的同对象强制传导约定。',
            'evidence': [ref(1, left), ref(2, right)]}]}, [term], [sub], [], src)
        assert not result['findings'] and result['checks'][0]['state'] == state


def test_real_runtime_persists_dual_evidence_and_same_report_exports(tmp_path, monkeypatch):
    import io
    import fitz
    from docx import Document
    from sqlalchemy.orm import Session
    from app.plus.blockchain_supervision import retrospective_runtime as runtime
    from app.plus.blockchain_supervision.models import BsTask, BsMaterial, BsTaskEvent, BsAiReviewJob
    from app.plus.startup_conditions_check import llm
    from app.services.billing import bindings
    from test_bs_report_version_http import _client
    from test_bs_retrospective_runtime import Client

    class Composite(UpstreamClient, Client):
        def __init__(self):
            UpstreamClient.__init__(self)
            self.internal = Client()

        async def complete(self, messages, on_usage=None, response_schema=None):
            payload = json.loads(messages[1]['content'])
            if payload.get('analysis_type', '').startswith('upstream_') or any(
                    row['kind'] == 'upstream_risk' for row in payload.get('candidates', [])):
                return await UpstreamClient.complete(self, messages, on_usage, response_schema)
            return await self.internal.complete(messages, on_usage)

    http, engine, tid = _client(tmp_path)
    client = Composite()
    monkeypatch.setattr(runtime, 'schedule', lambda *args: False)
    monkeypatch.setattr(runtime, 'freeze_basis', lambda: [])
    monkeypatch.setattr(runtime, '_file_hash', lambda m: str(m.id).zfill(64))
    monkeypatch.setattr(bindings, 'effective_model_code_or_default', lambda *args: 'synthetic')
    monkeypatch.setattr(llm, 'client_from_env', lambda **kwargs: client)
    with Session(engine) as db:
        task = db.get(BsTask, tid)
        task.options_snapshot_json = {'review_mode': 'contract_retrospective'}
        for source in sources():
            text = source['pages'][0]['text']
            if source['role'] == 'sub':
                text += '\n工期延长的费用可另行协商。工期延长费用均不调整。'
            db.add(BsMaterial(task_id=tid, file_name=source['file_name'], role=source['role'],
                parse_state='done', pages=1, degradations_json=[], pages_json=[{'page': 1, 'text': text}]))
        db.commit()
    base = f'/api/plus/blockchain-supervision/tasks/{tid}'
    assert http.post(base + '/retrospective/start', json={}).status_code == 202
    with Session(engine) as db:
        job_id = db.query(BsAiReviewJob).one().id
    asyncio.run(runtime.run(engine, job_id))
    result = http.get(base + '/retrospective').json()
    report = result['report']
    assert report, result
    assert report['identity']['upstream_version'] == 'bs.contract-upstream.v2'
    assert report['upstream']['state'] == 'completed'
    assert {row['kind'] for row in report['rows']} == {'internal_ambiguity', 'upstream_risk'}
    row = next(row for row in report['rows'] if row['kind'] == 'upstream_risk')
    assert row['cells'][1] == '总包／分包'
    assert '总包《总包.pdf》' in row['cells'][4] and '分包《分包.pdf》' in row['cells'][4]
    assert row['cells'][6] == '上游合同约定（合同约束）'
    assert {ref['role'] for ref in row['evidence']} == {'general', 'sub'}
    calls = len(client.calls) + client.internal.calls
    asyncio.run(runtime.run(engine, job_id))
    assert calls == len(client.calls) + client.internal.calls
    with Session(engine) as db:
        from app.models.models import TokenUsage
        assert db.query(TokenUsage).count() == calls
        assert db.query(BsTaskEvent).filter_by(event_type=runtime.CHECKPOINT).count() >= 5
    word = http.get(base + '/report/export?fmt=docx')
    pdf = http.get(base + '/report/export?fmt=pdf')
    assert word.status_code == pdf.status_code == 200
    document = Document(io.BytesIO(word.content))
    texts = '\n'.join(cell.text for table in document.tables for cells in table.rows for cell in cells.cells)
    assert candidate()['evidence'][0]['quote'] in texts and candidate()['evidence'][1]['quote'] in texts
    with fitz.open(stream=pdf.content, filetype='pdf') as doc:
        assert all(page.rect.width > page.rect.height for page in doc)
    assert http.get(base + '/retrospective').json()['report'] == report
