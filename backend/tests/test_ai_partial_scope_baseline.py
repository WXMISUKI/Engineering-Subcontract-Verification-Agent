"""部分模型上下文与整包缺件结论的边界。"""
import pytest
import asyncio

from app.plus.blockchain_supervision.ai_evidence import validate_ai_proposal
from app.plus.blockchain_supervision import ai_review_batch
from app.plus.blockchain_supervision.ai_review_batch import plan_review_batches, to_wire_batch


def proposal(message, status='undecidable'):
    return {'item_id': 'SIG-1', 'subject_id': 2, 'status': status, 'message': message,
            'material_evidence': [{'material_id': 3, 'page': 1, 'quote': '合同已经签署'}],
            'rule_evidence': [{'reference_id': 'r1', 'quote': '应当核对签署情况'}]}


def evaluate(value, *, partial=True):
    return validate_ai_proposal(value, task_id=1, subject_id=2, item_id='SIG-1', kind='signature_chain',
        incomplete_material_scope=partial,
        materials=[{'task_id': 1, 'subject_id': 2, 'material_id': 3, 'page': 1, 'text': '合同已经签署'}],
        references=[{'reference_id': 'r1', 'text': '应当核对签署情况'}])


@pytest.mark.parametrize('text', [
    '已提供的文本片段中未见签字，请核实是否缺失。',
    '本次输入的文本片段中未见签章，请核对原件。',
    '请核实是否未提供签署资料。',
])
def test_partial_input_accepts_limited_uncertainty_with_verified_evidence(text):
    assert evaluate(proposal(text)).accepted


@pytest.mark.parametrize('text', ['报审资料未提供签署文件。', '合同缺少签章。', '材料中未见签名。'])
def test_partial_input_rejects_unverified_package_absence(text):
    result = evaluate(proposal(text))
    assert not result.accepted
    assert '缺件表述无法证实' in result.reason


def test_partial_input_cannot_establish_definitive_hit_or_compliance():
    assert not evaluate(proposal('材料存在签章问题', status='hit')).accepted
    assert not evaluate(proposal('材料符合要求', status='miss')).accepted
    assert not evaluate(proposal('材料符合要求', status='miss'), partial=False).accepted


def test_uncertainty_requires_actual_matching_material_and_rule():
    value = proposal('已提供的文本片段中未见签字，请核实是否缺失。')
    value['material_evidence'][0]['quote'] = '伪造的签署文字'
    assert not evaluate(value).accepted


def test_cannot_cite_another_items_reference_even_when_quote_matches():
    value = proposal('请核对签章原件。')
    value['rule_evidence'][0]['reference_id'] = 'r2'
    assert not evaluate(value).accepted


def test_batch_split_marks_model_context_partial_without_asserting_package_missing():
    item = {'finding_id': 1, 'task_id': 1, 'item_id': 'SIG-1', 'subject_id': 2,
            'kind': 'signature_chain', 'materials': [
                {'task_id': 1, 'subject_id': 2, 'material_id': 3, 'page': i, 'text': '合同已经签署'}
                for i in range(1, 14)],
            'references': [{'reference_id': 'r1', 'text': '应当核对签署情况'}]}
    plan = plan_review_batches([item])
    assert len(plan['batches']) > 1
    assert all(entry['incomplete_material_scope'] for batch in plan['batches'] for entry in batch['items'])
    assert all('部分材料或片段' in entry['材料范围说明']
               for batch in plan['batches'] for entry in to_wire_batch(batch)['items'])


def test_model_prompt_distinguishes_packet_and_current_batch_and_groups_identical_facts(monkeypatch):
    item = {'finding_id': 1, 'task_id': 1, 'item_id': 'SIG-1', 'subject_id': 2,
            'kind': 'signature_chain', 'materials': [
                {'task_id': 1, 'subject_id': 2, 'material_id': 3, 'page': i, 'text': '合同已经签署'}
                for i in range(1, 14)],
            'references': [{'reference_id': 'r1', 'text': '应当核对签署情况'}]}
    batch = plan_review_batches([item])['batches'][0]
    captured = {}

    async def response(_client, system, payload, **_kwargs):
        captured.update(system=system, payload=payload)
        return {'proposals': []}

    monkeypatch.setattr(ai_review_batch, '_request_review_json', response)
    asyncio.run(ai_review_batch.execute_review_batch(client=object(), batch=batch))
    assert '当前批次' in captured['system'] and '完整报审资料' in captured['system']
    assert '同一核查项的同一事实' in captured['system']
    assert '已提供的文本片段中未见相关信息，请核对原件' in captured['system']
    assert all('当前模型输入' in row['材料范围说明'] for row in captured['payload']['items'])
    assert all(row['allowed_reference_ids'] == ['r1'] for row in captured['payload']['items'])
    assert 'allowed_reference_ids' in captured['system']


def test_each_entry_lists_only_its_own_authorized_references():
    base = {'task_id': 1, 'subject_id': 2, 'kind': 'signature_chain',
            'materials': [{'task_id': 1, 'subject_id': 2, 'material_id': 3, 'page': 1, 'text': '合同已经签署'}]}
    first = {**base, 'finding_id': 1, 'item_id': 'SIG-1',
             'references': [{'reference_id': 'r1', 'text': '应核对签署'}]}
    second = {**base, 'finding_id': 2, 'item_id': 'SIG-2',
              'references': [{'reference_id': 'r2', 'text': '应核对盖章'}]}
    batch = plan_review_batches([first, second])['batches'][0]
    wire = to_wire_batch(batch)
    assert {row['finding_id']: row['allowed_reference_ids'] for row in wire['items']} == {1: ['r1'], 2: ['r2']}
    assert all([wire['references'][i]['reference_id'] for i in row['reference_indexes']] == row['allowed_reference_ids']
               for row in wire['items'])


def test_batch_budget_includes_per_item_reference_ids_without_losing_pages():
    from app.plus.blockchain_supervision.ai_review_batch import _size
    item = {'finding_id': 1, 'task_id': 1, 'item_id': 'SIG-1', 'subject_id': 2,
            'kind': 'signature_chain', 'materials': [
                {'task_id': 1, 'subject_id': 2, 'material_id': 3, 'page': p, 'text': '合同签署材料' * 250}
                for p in (1, 2)],
            'references': [{'reference_id': 'reference-' + str(i) * 100, 'text': '应当核对签署情况'}
                           for i in range(3)]}
    plan = plan_review_batches([item], max_chars=4000)
    assert len(plan['batches']) >= 2 and not plan['limitations']
    assert all(_size(batch) <= 4000 for batch in plan['batches'])
    assert {entry['material']['page'] for batch in plan['batches'] for entry in batch['items']} == {1, 2}


@pytest.mark.parametrize('message', [
    '在已提供的材料片段中未见签字，建议核对原件。',
    '在本次提供的材料片段中未见签章，请核对原件。',
])
def test_equivalent_explicit_partial_scope_wording_is_not_rejected(message):
    assert evaluate(proposal(message)).accepted


@pytest.mark.parametrize('message', [
    '已提供的文本片段中可见签发机关，但未见注册单位信息，请核对原件。',
    '已提供的文本片段中仅见操作证信息，未见社保缴费记录，请核对原件。',
])
def test_fragment_scoped_observed_and_unobserved_fields_are_allowed(message):
    assert evaluate(proposal(message)).accepted


@pytest.mark.parametrize('message', [
    '已提供的文本片段中可见签发机关，但未见注册单位；报审资料缺失证书。',
    '已提供的文本片段中可见签发机关，但未见注册单位，说明报审资料缺件。',
    '已提供的文本片段中可见签发机关，整包未见注册单位，请核对。',
    '已提供的文本片段中可见证书，报审资料未提供证明，但未见注册单位。',
])
def test_fragment_scoped_comparison_does_not_excuse_package_absence(message):
    assert not evaluate(proposal(message)).accepted


def test_scoped_uncertainty_does_not_excuse_a_separate_absence_claim():
    value = proposal('在已提供的材料片段中未见签字，报审资料缺失签章。')
    assert not evaluate(value).accepted


@pytest.mark.parametrize('claim', ['因此整包未签字。', '因此报审资料未盖章。',
                                    '因此整包未签署。', '因此整包未加盖公章。', '因此报审资料无签章。'])
def test_scoped_uncertainty_cannot_excuse_signature_absence_claim(claim):
    assert not evaluate(proposal('在已提供的材料片段中未见签字，' + claim)).accepted


def test_identical_validated_suggestions_are_deduplicated_without_losing_distinct_evidence(monkeypatch):
    pages = [{'task_id': 1, 'subject_id': 2, 'material_id': 3, 'page': i, 'text': '合同已经签署'} for i in (1, 2)]
    item = {'finding_id': 1, 'task_id': 1, 'item_id': 'SIG-1', 'subject_id': 2,
            'kind': 'signature_chain', 'materials': pages,
            'references': [{'reference_id': 'r1', 'text': '应当核对签署情况'}]}
    batch = plan_review_batches([item])['batches'][0]
    first = {'finding_id': 1, **proposal('请核对签章原件。')}
    other = {'finding_id': 1, **proposal('请核对签章原件。')}
    other['material_evidence'][0]['page'] = 2

    async def response(*_args, **_kwargs):
        return {'proposals': [first, first.copy(), other]}

    monkeypatch.setattr(ai_review_batch, '_request_review_json', response)
    result = asyncio.run(ai_review_batch.execute_review_batch(client=object(), batch=batch))[0]
    assert result['model_proposal_count'] == 3
    assert len(result['proposals']) == 2
    assert {row['material_evidence'][0]['page'] for row in result['proposals']} == {1, 2}
