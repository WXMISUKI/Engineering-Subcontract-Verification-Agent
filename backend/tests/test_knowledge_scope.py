from app.services.knowledge.binding_service import authorized_file_scope


def test_malformed_scope_never_expands_authorization():
    assert authorized_file_scope(None) is None
    assert authorized_file_scope('not-json') == ()
    assert authorized_file_scope('["a", "a", "b"]') == ('a', 'b')
    assert authorized_file_scope('["a", null]') == ()
