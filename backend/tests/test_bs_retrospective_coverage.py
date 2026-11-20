import pytest
from app.plus.blockchain_supervision.retrospective_coverage import plan, summarize, validate_checks
from app.plus.blockchain_supervision.retrospective_analysis import TOPICS


def test_every_frozen_clause_has_disposition_and_only_verbatim_children_are_grouped():
    basis = [{'clause_id': 'D#1', 'doc_id': 'D', 'text': '不得转包。不得再分包。'},
             {'clause_id': 'D#1a', 'doc_id': 'D', 'text': '不得转包。'},
             {'clause_id': 'D#2', 'doc_id': 'D', 'text': '本合同工资不受结算争议影响。'},
             {'clause_id': 'X#附件1', 'doc_id': 'X', 'text': '空白认可表'}]
    p = plan(basis)
    assert len(p['clauses']) == 4 and len(p['units']) == 2
    assert p['clauses'][1]['unit_id'] == 'D#1'
    assert p['clauses'][-1]['state'] == 'not_applicable'
    assert p['clauses'][2]['state'] == 'pending'  # 无关键词仍进入计划。


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


def test_failed_scan_keeps_unit_pending_even_when_another_scan_checked():
    p = plan([{'clause_id': 'D#1', 'doc_id': 'D', 'text': '工资须支付。'}])
    result = summarize(p, [{'state': 'completed', 'basis_ids': ['D#1'], 'checks': [
        {'clause_id': 'D#1', 'state': 'checked', 'reason': '合同有约定', 'evidence': []}]},
        {'state': 'failed', 'basis_ids': ['D#1'], 'checks': []}])
    assert result['counts'] == {'checked': 0, 'not_applicable': 0, 'pending': 1}


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
    assert validate_checks(raw, units, [source], batch=batch)[0]['state'] == 'pending'


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
