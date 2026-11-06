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
