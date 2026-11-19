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
    assert checked[0]['state'] == 'checked'


def test_failed_scan_keeps_unit_pending_even_when_another_scan_checked():
    p = plan([{'clause_id': 'D#1', 'doc_id': 'D', 'text': '工资须支付。'}])
    result = summarize(p, [{'state': 'completed', 'basis_ids': ['D#1'], 'checks': [
        {'clause_id': 'D#1', 'state': 'checked', 'reason': '合同有约定', 'evidence': []}]},
        {'state': 'failed', 'basis_ids': ['D#1'], 'checks': []}])
    assert result['counts'] == {'checked': 0, 'not_applicable': 0, 'pending': 1}


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
