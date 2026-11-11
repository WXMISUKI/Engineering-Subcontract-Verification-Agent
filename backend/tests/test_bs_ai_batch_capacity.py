from app.plus.blockchain_supervision.ai_review_batch import plan_review_batches


def test_many_findings_split_without_losing_evidence_or_identity():
    page = {'task_id': 1, 'subject_id': 2, 'material_id': 3, 'page': 5, 'text': '项目经理证书原文'}
    items = [{'finding_id': n, 'task_id': 1, 'item_id': 'A2-05', 'subject_id': 2,
              'materials': [page], 'references': [{'reference_id': 'r1', 'text': '核对证书编号'}]}
             for n in range(1, 65)]
    plan = plan_review_batches(items)
    assert not plan['limitations']
    assert len(plan['batches']) == 2
    assert all(len(batch['items']) <= 32 for batch in plan['batches'])
    assert {entry['finding_id'] for batch in plan['batches'] for entry in batch['items']} == set(range(1, 65))
    assert all(entry['material']['page'] == 5 for batch in plan['batches'] for entry in batch['items'])


def test_repeated_empty_table_rows_are_not_sent_and_offsets_remain_original():
    original = '有效前文\n' + ('|  |  |\n' * 730) + '有效后文'
    page = {'task_id': 1, 'subject_id': 2, 'material_id': 3, 'page': 23, 'text': original}
    item = {'finding_id': 1, 'task_id': 1, 'item_id': 'A2-05', 'subject_id': 2,
            'materials': [page], 'references': [{'reference_id': 'r1', 'text': '核对证书编号'}]}
    plan = plan_review_batches([item])
    entries = [entry for batch in plan['batches'] for entry in batch['items']]
    assert entries
    assert all('incomplete_material_scope' in entry and entry['incomplete_material_scope'] for entry in entries)
    assert all('材料含重复格式噪声' in ''.join(entry['limitations']) for entry in entries)
    assert all(entry['material']['text'] == original[entry['material']['text_start']:entry['material']['text_end']]
               for entry in entries)
    combined = ''.join(entry['material']['text'] for entry in entries)
    assert '有效前文' in combined and '有效后文' in combined
    assert '|  |  |' not in combined


def test_only_empty_table_rows_do_not_form_model_batch():
    page = {'task_id': 1, 'subject_id': 2, 'material_id': 3, 'page': 23, 'text': '|  |  |\n' * 10}
    item = {'finding_id': 1, 'task_id': 1, 'item_id': 'A2-05', 'subject_id': 2,
            'materials': [page], 'references': [{'reference_id': 'r1', 'text': '核对证书编号'}]}
    plan = plan_review_batches([item])
    assert not plan['batches']
    assert plan['limitations'][0]['reason'] == 'context_incomplete'


def test_nonempty_table_cells_and_isolated_blank_rows_are_preserved():
    original = '| 姓名 | 证号 |\n| --- | --- |\n|  |  |\n| 张某 | 123 |\n'
    page = {'task_id': 1, 'subject_id': 2, 'material_id': 3, 'page': 5, 'text': original}
    item = {'finding_id': 1, 'task_id': 1, 'item_id': 'A2-05', 'subject_id': 2,
            'materials': [page], 'references': [{'reference_id': 'r1', 'text': '核对证书编号'}]}
    plan = plan_review_batches([item])
    entries = [entry for batch in plan['batches'] for entry in batch['items']]
    assert len(entries) == 1
    assert entries[0]['material']['text'] == original


def test_empty_table_noise_cannot_create_a_definitive_advisory(monkeypatch):
    import asyncio
    from app.plus.blockchain_supervision import ai_review_batch
    original = '有效前文\n' + ('|  |  |\n' * 10) + '有效后文'
    page = {'task_id': 1, 'subject_id': 2, 'material_id': 3, 'page': 23, 'text': original}
    item = {'finding_id': 1, 'task_id': 1, 'item_id': 'A2-05', 'subject_id': 2,
            'materials': [page], 'references': [{'reference_id': 'r1', 'text': '核对证书编号'}]}
    batch = plan_review_batches([item])['batches'][0]
    async def proposed(*_args, **_kwargs):
        return {'proposals': [{'finding_id': 1, 'item_id': 'A2-05', 'subject_id': 2, 'status': 'miss'}]}
    monkeypatch.setattr(ai_review_batch, '_request_review_json', proposed)
    result = asyncio.run(ai_review_batch.execute_review_batch(client=object(), batch=batch))[0]
    assert result['execution_status'] == 'partial'
    assert result['proposals'] == []
    assert any('材料范围不完整' in reason for reason in result['rejection_reasons'])
