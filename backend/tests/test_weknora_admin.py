import httpx
import pytest
import hashlib

from app.standalone.weknora_admin import KnowledgeSetupError, WeKnoraAdmin


def test_admin_login_and_new_kb_use_returned_token():
    def handler(request):
        if request.url.path.endswith('/auth/login'):
            return httpx.Response(200, json={'success': True, 'token': 'synthetic-token'})
        assert request.headers['Authorization'] == 'Bearer synthetic-token'
        return httpx.Response(201, json={'success': True, 'data': {'id': 'new-kb'}})
    with httpx.Client(base_url='http://weknora:8080', transport=httpx.MockTransport(handler)) as client:
        admin = WeKnoraAdmin(client)
        admin.login('test@example.invalid', 'synthetic-password')
        assert admin.create_knowledge_base('bs-test', 'model-id') == 'new-kb'


def test_admin_failure_does_not_expose_server_response():
    transport = httpx.MockTransport(lambda request: httpx.Response(403, text='synthetic-secret'))
    with httpx.Client(base_url='http://weknora:8080', transport=transport) as client:
        with pytest.raises(KnowledgeSetupError) as exc:
            WeKnoraAdmin(client).login('test@example.invalid', 'synthetic-password')
        assert 'synthetic-secret' not in str(exc.value)


def test_duplicate_upload_is_not_silently_accepted(tmp_path):
    path = tmp_path / 'sample.txt'
    path.write_text('synthetic', encoding='utf-8')
    transport = httpx.MockTransport(lambda request: httpx.Response(
        409, json={'success': False, 'data': {'id': 'unverified-existing-file'}}))
    with httpx.Client(base_url='http://weknora:8080', transport=transport) as client:
        with pytest.raises(KnowledgeSetupError, match='HTTP 409'):
            WeKnoraAdmin(client).upload_document('kb', path)


def test_query_key_is_scoped_to_the_requested_libraries():
    import json
    def handler(request):
        if request.url.path.endswith('/auth/me'):
            return httpx.Response(200, json={'success': True, 'data': {'tenant': {'id': 8}}})
        assert request.url.path == '/api/v1/tenants/8/api-keys'
        assert json.loads(request.content) == {
            'name': 'bs-query', 'capabilities': ['retrieve'], 'knowledge_base_ids': ['kb-one'], 'full_access': False}
        return httpx.Response(201, json={'success': True, 'data': {'id': 12, 'token': 'synthetic-query-key'}})
    with httpx.Client(base_url='http://weknora:8080', transport=httpx.MockTransport(handler)) as client:
        assert WeKnoraAdmin(client).create_retrieval_key(['kb-one']) == ('12', 'synthetic-query-key')


def test_vision_model_is_attached_to_new_knowledge_base():
    import json
    def handler(request):
        body = json.loads(request.content)
        if request.url.path == '/api/v1/models':
            assert body['type'] == 'VLLM' and body['parameters']['base_url'] == 'https://example.invalid/v1'
            return httpx.Response(201, json={'success': True, 'data': {'id': 'vlm-id'}})
        assert body['vlm_config'] == {'enabled': True, 'model_id': 'vlm-id'}
        return httpx.Response(201, json={'success': True, 'data': {'id': 'kb-id'}})
    with httpx.Client(base_url='http://weknora:8080', transport=httpx.MockTransport(handler)) as client:
        admin = WeKnoraAdmin(client)
        vision = admin.create_vision_model('ocr', 'https://example.invalid/v1', 'synthetic-key')
        assert admin.create_knowledge_base('bs-test', 'embedding-id', vision) == 'kb-id'


def test_recovery_requires_unique_completed_file_and_downloaded_sha256(tmp_path):
    path = tmp_path / 'sample.txt'
    path.write_bytes(b'synthetic source')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    record = {'id': 'doc-id', 'knowledge_base_id': 'kb-id', 'file_name': path.name,
              'file_size': path.stat().st_size, 'embedding_model_id': 'model-id', 'parse_status': 'completed'}
    records = [record]
    content = [path.read_bytes()]

    def handler(request):
        if request.url.path.endswith('/knowledge-bases/kb-id/knowledge'):
            return httpx.Response(200, json={'success': True, 'data': records, 'total': len(records),
                                             'page': 1, 'page_size': 100})
        if request.url.path.endswith('/knowledge/doc-id/download'):
            return httpx.Response(200, content=content[0])
        return httpx.Response(200, json={'success': True, 'data': record})

    with httpx.Client(base_url='http://weknora:8080', transport=httpx.MockTransport(handler)) as client:
        admin = WeKnoraAdmin(client)
        assert admin.find_verified_document('kb-id', path, digest, 'model-id') == 'doc-id'
        records.append({**record, 'id': 'second-id'})
        assert admin.find_verified_document('kb-id', path, digest, 'model-id') is None
        records.pop()
        content[0] = b'wrong'
        assert admin.find_verified_document('kb-id', path, digest, 'model-id') is None
        content[0] = path.read_bytes()
        record['knowledge_base_id'] = 'other-kb'
        assert admin.find_verified_document('kb-id', path, digest, 'model-id') is None


@pytest.mark.parametrize('response', [
    {'success': True, 'data': [], 'total': 1, 'page': 1, 'page_size': 100},
    {'success': True, 'data': [], 'total': 0, 'page': 2, 'page_size': 100},
    {'success': True, 'data': [], 'page': 1, 'page_size': 100},
])
def test_recovery_rejects_incomplete_or_inconsistent_file_list(tmp_path, response):
    path = tmp_path / 'sample.txt'
    path.write_bytes(b'synthetic')
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=response))
    with httpx.Client(base_url='http://weknora:8080', transport=transport) as client:
        with pytest.raises(KnowledgeSetupError, match='列表'):
            WeKnoraAdmin(client).find_verified_document('kb-id', path, hashlib.sha256(path.read_bytes()).hexdigest(), 'model-id')
