import time

from app.services.knowledge.binding_service import authorized_file_scope
from app.services.knowledge.multi_kb_query import QueryTarget, apply_target_selection, run_multi_kb_query


def test_malformed_scope_never_expands_authorization():
    assert authorized_file_scope(None) is None
    assert authorized_file_scope('not-json') == ()
    assert authorized_file_scope('[]') == ()
    assert authorized_file_scope('["a", "a", "b"]') == ('a', 'b')
    assert authorized_file_scope('["a", null]') == ()


def test_selecting_a_library_does_not_expand_file_scope():
    target = QueryTarget(kb_id='library', kb_name='test', base_url='http://test', secret_ref='env://TEST',
                         weight=1, priority=1, knowledge_ids=('authorized',))
    selected = apply_target_selection([target], {'kb_ids': ['library']})
    assert len(selected) == 1
    assert selected[0].knowledge_ids == ('authorized',)
    narrowed = apply_target_selection([target], {'kb_ids': ['library'], 'knowledge_ids': ['other']})
    assert narrowed[0].knowledge_ids == ()


def test_empty_binding_never_queries_the_provider():
    target = QueryTarget(kb_id='library', kb_name='test', base_url='http://test', secret_ref='env://TEST',
                         weight=1, priority=1, knowledge_ids=authorized_file_scope('[]'), provider_id=1)
    calls = []
    result = run_multi_kb_query([target], 'query', search_fn=lambda *args, **kwargs: calls.append(args))
    assert calls == []
    assert result.status == 'undetermined'


def test_one_slow_library_isolated_as_partial_without_erasing_fast_results():
    targets = [
        QueryTarget(kb_id='fast', kb_name='fast', base_url='http://fast', secret_ref='env://FAST',
                    weight=1, priority=1),
        QueryTarget(kb_id='slow', kb_name='slow', base_url='http://slow', secret_ref='env://SLOW',
                    weight=1, priority=2),
    ]

    def search(target, _query, _top_k, *, timeout_seconds):
        if target.kb_id == 'slow':
            time.sleep(0.05)
        return [{'id': target.kb_id, 'document': target.kb_id, 'score': 1.0,
                 'knowledge_id': target.kb_id, 'source': target.kb_id}]

    result = run_multi_kb_query(
        targets, 'query', search_fn=search,
        per_kb_timeout_seconds=0.01, overall_timeout_seconds=0.03,
    )

    assert result.status == 'partial'
    assert result.succeeded_targets == ['fast']
    assert result.failed_targets == ['slow']
    assert any(row['error_code'] == 'deadline_exceeded' for row in result.per_kb if row['kb_id'] == 'slow')
    assert [item['kb_id'] for item in result.items] == ['fast']


def test_scope_mismatch_is_failed_and_does_not_enter_aggregate():
    target = QueryTarget(kb_id='authorized-kb', kb_name='authorized', base_url='http://test',
                         secret_ref='env://TEST', weight=1, priority=1,
                         knowledge_ids=('authorized-file',), provider_id=1)

    def search(_target, _query, _top_k, *, timeout_seconds):
        return [{'id': 'wrong', 'document': 'wrong', 'score': 1.0,
                 'metadata': {'kb_id': 'other-kb', 'knowledge_id': 'other-file'}}]

    result = run_multi_kb_query([target], 'query', search_fn=search)

    assert result.status == 'undetermined'
    assert result.items == []
    assert result.failed_kbs == ['authorized-kb']
    assert result.per_kb[0]['error_code'] == 'returned_scope_mismatch'
