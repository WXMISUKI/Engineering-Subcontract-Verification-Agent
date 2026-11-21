import pytest
from app.plus.blockchain_supervision.retrospective_coverage import plan, summarize, validate_checks
from app.plus.blockchain_supervision.retrospective_analysis import TOPICS


def test_every_obligation_has_its_own_disposition_even_when_parent_contains_its_text():
    basis = [{'clause_id': 'D#1', 'doc_id': 'D', 'text': '不得转包。不得再分包。'},
             {'clause_id': 'D#1a', 'doc_id': 'D', 'text': '不得转包。'},
             {'clause_id': 'D#2', 'doc_id': 'D', 'text': '本合同工资不受结算争议影响。'},
             {'clause_id': 'X#附件1', 'doc_id': 'X', 'text': '空白认可表'}]
    p = plan(basis)
    assert len(p['clauses']) == 4 and len(p['units']) == 3
    assert p['clauses'][1]['unit_id'] == 'D#1a'
    assert p['clauses'][-1]['state'] == 'not_applicable'
    assert p['clauses'][2]['state'] == 'pending'  # 无关键词仍进入计划。
    report = summarize(p, [{'state': 'completed', 'basis_ids': ['D#1'], 'checks': [
        {'clause_id': 'D#1', 'state': 'checked', 'reason': '只发现不得再分包的约定。', 'evidence': []}]}])
    states = {row['clause_id']: row['state'] for row in report['clauses']}
    assert states['D#1'] == 'checked' and states['D#1a'] == 'pending'


def test_missing_reply_or_unproven_nonapplicability_cannot_be_coverage():
    units = [{'clause_id': 'D#1'}]
    with pytest.raises(ValueError):
        validate_checks({'checks': []}, units, [])
    checks = validate_checks({'checks': [{'clause_id': 'D#1', 'state': 'not_applicable', 'reason': '没有关键词'}]}, units, [])
    assert checks[0]['state'] == 'pending'
    checked = validate_checks({'checks': [{'clause_id': 'D#1', 'state': 'checked',
        'reason': '本批已检查，未出现相关合同约定。', 'evidence': []}]}, units, [])
    assert checked[0]['state'] == 'pending'
    observed = validate_checks({'checks': [{'clause_id': 'D#1', 'state': 'not_observed',
        'reason': '本批未见对应约定，仅记录本批观察。', 'evidence': []}]}, units, [])
    assert observed[0]['state'] == 'not_observed'


def test_strict_scan_distinguishes_an_unsupported_conclusion_from_invalid_evidence():
    units = [{'clause_id': 'D#1'}]
    raw = {'checks': [{'clause_id': 'D#1', 'state': 'not_applicable',
                       'reason': '合同内没有对应事项', 'evidence_ids': []}]}
    [check] = validate_checks(raw, units, [], catalog={}, strict=True)
    assert check['state'] == 'pending'
    assert check['evidence'] == []
    assert '未提供支持该判断的合同原文' in check['reason']
    raw['checks'][0]['evidence_ids'] = ['E999']
    with pytest.raises(ValueError, match='引用编号重复或不属于本批'):
        validate_checks(raw, units, [], catalog={}, strict=True)


@pytest.mark.asyncio
async def test_native_scan_rejects_duplicate_json_keys_before_covering_checks():
    from app.plus.blockchain_supervision.retrospective_coverage import _scan_response_schema
    from app.plus.blockchain_supervision.semantic_review import InvalidModelResponse, request_json

    class Client:
        supports_json_schema = True

        async def complete(self, *args, **kwargs):
            return '{"checks":{"B01":{},"B01":{}}}'

    with pytest.raises(InvalidModelResponse, match='重复字段'):
        await request_json(Client(), '返回JSON', {}, response_schema=_scan_response_schema({'B01': 'D#1'}, ['E001']))


def test_reviewed_purpose_clause_stays_in_inventory_but_modified_text_is_scanned():
    from app.plus.blockchain_supervision.retrospective_sources import freeze_basis

    reviewed = next(row for row in freeze_basis() if row['clause_id'] == 'SHJT-2026-8#第一条')
    [context] = plan([reviewed])['clauses']
    assert context['unit_id'] is None
    assert '制定目的及依据' in context['reason']
    changed = {**reviewed, 'text': reviewed['text'] + '分包单位应提交专项施工方案。'}
    [updated] = plan([changed])['clauses']
    assert updated['unit_id'] == reviewed['clause_id']


@pytest.mark.asyncio
async def test_scan_compares_later_rule_text_without_using_dates_to_exclude_it():
    import json
    from types import SimpleNamespace
    from app.plus.blockchain_supervision.retrospective_analysis import TOPICS
    from app.plus.blockchain_supervision.retrospective_coverage import scan
    from app.plus.blockchain_supervision.retrospective_sources import freeze_material

    original = '分包单位须按照合同约定办理分包报审。'
    source = freeze_material(SimpleNamespace(id=1, file_name='合同.pdf', role='sub', pages=1,
        parse_state='done', degradations_json=[], pages_json=[{'page': 1, 'text': original}]))
    clause = {'clause_id': 'SHJT-2026-8#例', 'text': '有关单位应办理分包报审。',
              'scope_context': '本市交通建设工程', 'document': {'doc_title': '上海交通建设细则',
                  'effective_from': '2026-08-08', 'effective_to': '2031-08-07'}}

    class Client:
        supports_json_schema = True

        async def complete(self, messages, *, response_schema):
            payload = json.loads(messages[1]['content'])
            assert 'baseline_date' not in payload and 'contract_date' not in payload
            [rule] = payload['basis']
            assert rule['text'] == clause['text'] and rule['scope_context'] == clause['scope_context']
            assert '2026-08-08' not in messages[1]['content']
            assert '发布、签约、施行日期和历史适用性均不能作为跳过规范正文的理由' in messages[0]['content']
            schema = response_schema['properties']['checks']
            assert schema['required'] == [rule['check_key']]
            assert set(schema['properties']) == set(schema['required'])
            check = response_schema['$defs']['BasisCheck']
            evidence_id = payload['batch']['parts'][0]['evidence_id']
            assert check['properties']['evidence_ids']['items']['enum'] == [evidence_id]
            assert 'clause_id' not in check['properties']
            return json.dumps({'checked_topics': list(TOPICS), 'findings': [], 'limitations': [], 'checks': {
                rule['check_key']: {'state': 'checked', 'reason': '本批明确约定办理分包报审。',
                                   'evidence_ids': [evidence_id]}}})

    saved = {}
    result, _ = await scan([source], [clause], {'baseline_date': '2023-12-22', 'contract_date': '2023-12-22'},
                           client=Client(), load_checkpoint=saved.get, save_checkpoint=saved.__setitem__,
                           checkpoint_subrequests=True)
    assert result['execution']['state'] == 'completed', saved
    assert result['counts']['checked'] == 1


def test_failed_scan_keeps_unit_pending_even_when_another_scan_checked():
    p = plan([{'clause_id': 'D#1', 'doc_id': 'D', 'text': '工资须支付。'}])
    result = summarize(p, [{'state': 'completed', 'basis_ids': ['D#1'], 'checks': [
        {'clause_id': 'D#1', 'state': 'checked', 'reason': '合同有约定', 'evidence': []}]},
        {'state': 'failed', 'basis_ids': ['D#1'], 'checks': []}])
    assert result['counts'] == {'checked': 0, 'not_applicable': 0, 'pending': 1}


@pytest.mark.parametrize('fault', ['missing_scan', 'duplicate_scan', 'missing_clause'])
def test_coverage_requires_every_planned_scan_and_clause(fault):
    p = plan([{'clause_id': clause, 'doc_id': 'D', 'text': '合同义务应逐项审查。'}
              for clause in ('D#1', 'D#2')])
    clause_ids = ['D#1', 'D#2']
    expected = {key: clause_ids for key in ('scan-one', 'scan-two')}
    scans = [
        {'checkpoint_id': key, 'state': 'completed', 'basis_ids': clause_ids,
         'checks': [{'clause_id': clause, 'state': 'checked', 'reason': '本批有相应合同约定。', 'evidence': []}
                    for clause in clause_ids]}
        for key in expected
    ]
    if fault == 'missing_scan':
        scans.pop()
    elif fault == 'duplicate_scan':
        scans[1] = scans[0]
    else:
        scans[1]['checks'].pop()
    result = summarize(p, scans, planned_scans=expected)
    assert result['execution']['state'] == 'incomplete'
    assert result['execution']['batches_completed'] < result['execution']['batches_total'] == 2
    assert result['counts'] == {'checked': 0, 'not_applicable': 0, 'pending': 2}


@pytest.mark.parametrize('other', ['not_observed', 'not_applicable'])
def test_actual_checked_clause_and_other_scanned_batches_are_not_lost(other):
    p = plan([{'clause_id': 'D#1', 'doc_id': 'D', 'text': '工资须支付。'}])
    def scan(state, batch):
        return {'state': 'completed', 'batch_id': batch, 'basis_ids': ['D#1'], 'checks': [
            {'clause_id': 'D#1', 'state': state, 'reason': '本批具体核对记录', 'evidence': []}]}
    result = summarize(p, [scan('checked', 'one'), scan(other, 'two')])
    assert result['counts'] == {'checked': 1, 'not_applicable': 0, 'pending': 0}
    result = summarize(p, [scan('not_observed', 'one'), scan('not_observed', 'two')])
    assert result['counts']['pending'] == 1
    assert '尚不能' in result['clauses'][0]['reason']
    result = summarize(p, [scan('checked', 'one'), scan('pending', 'two')])
    assert result['counts']['pending'] == 1


def test_coverage_evidence_cannot_borrow_another_batch_or_conflict_with_no_observation():
    from types import SimpleNamespace
    from app.plus.blockchain_supervision.retrospective_sources import freeze_material, analysis_batches
    source = freeze_material(SimpleNamespace(id=1, file_name='合同.pdf', role='sub', pages=1,
        parse_state='done', degradations_json=[], pages_json=[{'page': 1, 'text':
            '1.1 工资应按月足额支付。\n1.2 安全费用应专款专用。'}]))
    batch = analysis_batches([source])[0]
    ref = {'material_id': 1, 'page': 1, 'quote': '安全费用应专款专用。'}
    units = [{'clause_id': 'D#1'}]
    raw = {'checks': [{'clause_id': 'D#1', 'state': 'checked', 'reason': '有合同原文支撑', 'evidence': [ref]}]}
    assert validate_checks(raw, units, [source], batch=batch)[0]['state'] == 'checked'
    assert validate_checks(raw, units, [source], batch={**batch, 'parts': batch['parts'][:1]})[0]['state'] == 'pending'
    raw['checks'][0]['state'] = 'not_observed'
    [pending] = validate_checks(raw, units, [source], batch=batch, strict=True)
    assert pending['state'] == 'pending'
    assert pending['evidence'][0]['quote'] == ref['quote']


@pytest.mark.asyncio
async def test_coverage_scans_unmatched_rule_and_replays_without_new_call():
    import json
    from types import SimpleNamespace
    from app.plus.blockchain_supervision.retrospective_sources import freeze_material
    from app.plus.blockchain_supervision.retrospective_coverage import scan
    source = freeze_material(SimpleNamespace(id=1, file_name='合同.pdf', role='sub', pages=1,
        parse_state='done', degradations_json=[], pages_json=[{'page': 1, 'text': '合同价款由双方协商确定。'}]))
    basis = [{'clause_id': 'D#1', 'doc_id': 'D', 'text': '不得阻挠申请仲裁。'}]
    class Client:
        calls = 0
        async def complete(self, messages, **kwargs):
            self.calls += 1
            payload = json.loads(messages[1]['content'])
            assert payload['basis'][0]['clause_id'] == 'D#1'
            return json.dumps({'checked_topics': list(TOPICS), 'findings': [], 'limitations': [], 'checks': [{'clause_id': 'D#1', 'state': 'pending',
                'reason': '未找到争议解决方式，不能判断适用。', 'evidence': []}]})
    client, saved = Client(), {}
    first = await scan([source], basis, {'baseline_date': '2026-11-19'}, client=client,
                       save_checkpoint=lambda key, row: saved.__setitem__(key, row))
    second = await scan([source], basis, {'baseline_date': '2026-11-19'}, client=client,
                        load_checkpoint=saved.get)
    assert first == second and client.calls == 1
    assert first[0]['counts']['pending'] == 1


@pytest.mark.asyncio
async def test_non_observation_trace_disambiguates_same_page_number_across_files():
    import json
    from types import SimpleNamespace
    from app.plus.blockchain_supervision.retrospective_sources import freeze_material
    from app.plus.blockchain_supervision.retrospective_coverage import scan

    sources = [freeze_material(SimpleNamespace(id=mid, file_name=f'合同{mid}.pdf', role='sub', pages=1,
        parse_state='done', degradations_json=[], pages_json=[{'page': 1, 'text': '合同价款由双方协商确定。'}]))
        for mid in (11, 22)]
    basis = [{'clause_id': 'D#1', 'doc_id': 'D', 'text': '不得阻挠申请仲裁。'}]

    class Client:
        async def complete(self, messages, **kwargs):
            return json.dumps({'checked_topics': list(TOPICS), 'findings': [], 'limitations': [], 'checks': [
                {'clause_id': 'D#1', 'state': 'not_observed', 'reason': '本批未见争议解决约定。', 'evidence_ids': []}]})

    coverage, _ = await scan(sources, basis, {}, client=Client())
    assert coverage['execution']['state'] == 'completed'
    [clause] = coverage['clauses']
    assert clause['state'] == 'pending'
    scans = {row['checkpoint_id']: row for row in coverage['scans']}
    assert {tuple(scans[row['checkpoint_id']]['material_ids']) for row in clause['checks']} == {(11,), (22,)}
    assert all(scans[row['checkpoint_id']]['pages'] == [1] and not row['evidence'] for row in clause['checks'])


@pytest.mark.asyncio
async def test_coverage_selects_in_batch_evidence_by_id_and_keeps_bad_ids_pending():
    import json
    from types import SimpleNamespace
    from app.plus.blockchain_supervision.retrospective_sources import freeze_material
    from app.plus.blockchain_supervision.retrospective_coverage import scan

    text = '1.1 工资应按月足额支付。'
    source = freeze_material(SimpleNamespace(id=1, file_name='合同.pdf', role='sub', pages=1,
        parse_state='done', degradations_json=[], pages_json=[{'page': 1, 'text': text}]))
    basis = [{'clause_id': 'D#1', 'doc_id': 'D', 'text': '应当足额支付工资。'},
             {'clause_id': 'D#2', 'doc_id': 'D', 'text': '应当保证安全费用。'}]

    class Client:
        async def complete(self, messages, **kwargs):
            part = json.loads(messages[1]['content'])['batch']['parts'][0]
            return json.dumps({'checked_topics': list(TOPICS), 'findings': [], 'limitations': [],
                'checks': [{'clause_id': clause, 'state': 'checked', 'reason': '有合同约定。',
                            'evidence_ids': [key]} for clause, key in (
                    ('D#1', part['evidence_id']), ('D#2', 'not-in-this-batch'))]})

    result, _ = await scan([source], basis, {}, client=Client())
    assert result['execution'] == {'state': 'completed', 'batches_total': 1, 'batches_completed': 1}
    assert result['counts'] == {'checked': 1, 'not_applicable': 0, 'pending': 1}
    check, = result['clauses'][0]['checks']
    assert check['evidence'][0]['quote'] == text
