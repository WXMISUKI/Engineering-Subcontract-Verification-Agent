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
