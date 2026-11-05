from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.standalone.identity import Base, create_account
from app.standalone.main import create_app
from app.plus.blockchain_supervision import access
from app.plus.blockchain_supervision.models import BsTask, BsMaterial, BsFinding
from sqlalchemy.orm import Session
from fastapi import HTTPException
import pytest


def client_and_engine():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    create_account(engine, 'alice', 'Alice-pass-123')
    create_account(engine, 'bob', 'Bob-password-456')
    return TestClient(create_app(engine, 'x' * 64, cookie_secure=False)), engine


def test_login_logout_and_no_registration():
    client, _ = client_and_engine()
    assert client.get('/api/session').status_code == 401
    assert client.post('/api/register', json={'username': 'mallory', 'password': 'guess'}).status_code == 404
    assert client.post('/api/login', json={'username': 'alice', 'password': 'wrong'}).status_code == 401
    response = client.post('/api/login', json={'username': 'alice', 'password': 'Alice-pass-123'})
    assert response.status_code == 200
    assert 'HttpOnly' in response.headers['set-cookie']
    assert client.get('/api/session').json()['username'] == 'alice'
    old_token = client.cookies.get('access_token')
    assert client.post('/api/logout').status_code == 204
    assert client.get('/api/session').status_code == 401
    client.cookies.set('access_token', old_token)
    assert client.get('/api/session').status_code == 401


def test_conversations_are_private_to_the_account():
    client, _ = client_and_engine()
    client.post('/api/login', json={'username': 'alice', 'password': 'Alice-pass-123'})
    created = client.post('/api/conversations').json()['id']
    assert [row['id'] for row in client.get('/api/conversations').json()] == [created]
    client.post('/api/logout')
    client.post('/api/login', json={'username': 'bob', 'password': 'Bob-password-456'})
    assert client.get('/api/conversations').json() == []
    assert client.get(f'/api/conversations/{created}').status_code == 404


def test_existing_bs_guard_accepts_only_owner():
    client, engine = client_and_engine()
    client.post('/api/login', json={'username': 'alice', 'password': 'Alice-pass-123'})
    conversation_id = client.post('/api/conversations').json()['id']
    with Session(engine) as db:
        task = BsTask(conversation_id=conversation_id, title='synthetic', created_by=1, created_by_type='web')
        db.add(task)
        db.commit()
        assert access.require_bs_task(db, task.id, 1, 'web').id == task.id
        with pytest.raises(HTTPException) as exc:
            access.require_bs_task(db, task.id, 2, 'web')
        assert exc.value.status_code == 403


def test_worker_session_uses_application_database():
    from app.database import SessionLocal
    client, _ = client_and_engine()
    with SessionLocal() as db:
        assert db.query(BsTask).count() == 0


def test_original_router_identity_dependency_uses_same_login():
    from fastapi import Depends
    from app.auth.unified_user import get_current_any_user
    client, engine = client_and_engine()
    app = create_app(engine, 'x' * 64, cookie_secure=False)

    @app.get('/protected')
    def protected(user=Depends(get_current_any_user)):
        return {'id': user.id, 'user_type': user.user_type}

    client = TestClient(app)
    assert client.get('/protected').status_code == 401
    client.post('/api/login', json={'username': 'alice', 'password': 'Alice-pass-123'})
    assert client.get('/protected').json() == {'id': 1, 'user_type': 'web'}


def test_login_rate_limit_blocks_repeated_failures():
    from app.limiter import limiter
    limiter.reset()
    client, _ = client_and_engine()
    for _ in range(10):
        assert client.post('/api/login', json={'username': 'unknown', 'password': 'wrong'}).status_code == 401
    assert client.post('/api/login', json={'username': 'unknown', 'password': 'wrong'}).status_code == 429
    limiter.reset()


def test_authenticated_rate_limits_are_separate_for_accounts_behind_one_proxy():
    from app.limiter import limiter
    limiter.reset()
    client, _ = client_and_engine()
    assert client.post('/api/login', json={'username': 'alice', 'password': 'Alice-pass-123'}).status_code == 200
    path = '/api/plus/blockchain-supervision/tasks/99999/check'
    for _ in range(6):
        assert client.post(path).status_code == 404
    assert client.post(path).status_code == 429
    assert client.post('/api/login', json={'username': 'bob', 'password': 'Bob-password-456'}).status_code == 200
    assert client.post(path).status_code == 404


def test_login_limit_remains_shared_even_with_authenticated_cookie():
    from app.limiter import limiter
    limiter.reset()
    client, _ = client_and_engine()
    assert client.post('/api/login', json={'username': 'alice', 'password': 'Alice-pass-123'}).status_code == 200
    for _ in range(9):
        assert client.post('/api/login', json={'username': 'nobody', 'password': 'incorrect'}).status_code == 401
    assert client.post('/api/login', json={'username': 'bob', 'password': 'Bob-password-456'}).status_code == 429


def test_rate_limit_rejects_forged_or_expired_account_identity():
    from datetime import datetime, timedelta, timezone
    from jose import jwt
    from starlette.requests import Request
    from app.limiter import account_or_address
    from types import SimpleNamespace

    app = SimpleNamespace(state=SimpleNamespace(secret_key='x' * 64))
    def key(token):
        scope = {'type': 'http', 'app': app, 'client': ('127.0.0.1', 1234),
                 'headers': [(b'cookie', ('access_token=' + token).encode())]}
        return account_or_address(Request(scope))

    valid = jwt.encode({'sub': '2', 'exp': datetime.now(timezone.utc) + timedelta(minutes=1)}, 'x' * 64)
    forged = jwt.encode({'sub': '2', 'exp': datetime.now(timezone.utc) + timedelta(minutes=1)}, 'wrong-key')
    expired = jwt.encode({'sub': '2', 'exp': datetime.now(timezone.utc) - timedelta(minutes=1)}, 'x' * 64)
    assert key(valid) == 'account:2'
    assert key(forged) == key(expired) == 'address:127.0.0.1'


def test_subcontract_task_api_enforces_account_ownership():
    client, _ = client_and_engine()
    client.post('/api/login', json={'username': 'alice', 'password': 'Alice-pass-123'})
    conversation_id = client.post('/api/conversations').json()['id']
    path = '/api/plus/blockchain-supervision/tasks'
    created = client.post(path, json={'conversation_id': conversation_id, 'title': '合成任务', 'scope_a1': True, 'scope_a3': True})
    assert created.status_code == 200, created.text
    task_id = created.json()['task_id']
    assert client.get('/api/conversations').json()[0]['title'] == '合成任务'
    assert client.get(f'{path}/{task_id}').status_code == 200
    client.post('/api/logout')
    client.post('/api/login', json={'username': 'bob', 'password': 'Bob-password-456'})
    assert client.get(f'{path}/{task_id}').status_code == 403


def test_cross_account_cannot_read_or_modify_task_evidence():
    client, engine = client_and_engine()
    client.post('/api/login', json={'username': 'alice', 'password': 'Alice-pass-123'})
    conversation_id = client.post('/api/conversations').json()['id']
    base = '/api/plus/blockchain-supervision'
    task_id = client.post(f'{base}/tasks', json={
        'conversation_id': conversation_id, 'title': '合成任务', 'scope_a1': True,
    }).json()['task_id']
    with Session(engine) as db:
        material = BsMaterial(task_id=task_id, file_name='synthetic.pdf',
                              pages_json=[{'page': 1, 'text': 'private synthetic text'}])
        db.add(material)
        db.flush()
        finding = BsFinding(task_id=task_id, material_id=material.id, item_id='test',
                            kind='completeness', status='hit', message='synthetic')
        db.add(finding)
        db.commit()
        material_id, finding_id = material.id, finding.id
    assert client.get(f'{base}/materials/{material_id}/pages/1').status_code == 200
    client.post('/api/logout')
    client.post('/api/login', json={'username': 'bob', 'password': 'Bob-password-456'})
    paths = [
        f'{base}/tasks?conversation_id={conversation_id}',
        f'{base}/tasks/{task_id}', f'{base}/tasks/{task_id}/subjects',
        f'{base}/tasks/{task_id}/findings', f'{base}/tasks/{task_id}/events',
        f'{base}/tasks/{task_id}/report', f'{base}/tasks/{task_id}/report/export',
        f'{base}/tasks/{task_id}/report/evidence-bundle',
        f'{base}/tasks/{task_id}/opinion', f'{base}/tasks/{task_id}/opinion/preview',
        f'{base}/tasks/{task_id}/opinion/preview-markdown', f'{base}/tasks/{task_id}/opinion/export',
        f'{base}/tasks/{task_id}/versions', f'{base}/tasks/{task_id}/versions/1',
        f'{base}/tasks/{task_id}/versions/diff?a=1&b=2',
        f'{base}/tasks/{task_id}/versions/1/materials/{material_id}/pages/1',
        f'{base}/tasks/{task_id}/materials/{material_id}/history',
        f'{base}/tasks/{task_id}/references/example/file',
        f'{base}/materials/{material_id}/pages/1', f'{base}/materials/{material_id}/file',
    ]
    for path in paths:
        assert client.get(path).status_code == 403, path
    assert client.patch(f'{base}/findings/{finding_id}/review', json={'decision': 'accepted'}).status_code == 403
    assert client.delete(f'{base}/tasks/{task_id}').status_code == 403
    with Session(engine) as db:
        assert db.get(BsFinding, finding_id).review_json is None
        assert db.get(BsTask, task_id) is not None


def test_model_status_does_not_expose_key(monkeypatch):
    monkeypatch.setenv('LLM_MODEL', 'example')
    monkeypatch.setenv('LLM_API_KEY', 'synthetic-secret')
    client, _ = client_and_engine()
    assert client.get('/api/model').status_code == 401
    client.post('/api/login', json={'username': 'alice', 'password': 'Alice-pass-123'})
    response = client.get('/api/model')
    assert response.json() == {'model': 'example'}
    assert 'synthetic-secret' not in response.text


def test_uninitialized_knowledge_is_reported_before_start(monkeypatch):
    monkeypatch.delenv('BS_REFERENCE_MAPPING_FILE', raising=False)
    client, _ = client_and_engine()
    client.post('/api/login', json={'username': 'alice', 'password': 'Alice-pass-123'})
    conversation_id = client.post('/api/conversations').json()['id']
    path = '/api/plus/blockchain-supervision/tasks'
    task_id = client.post(path, json={'conversation_id': conversation_id, 'title': 'synthetic', 'scope_a1': True}).json()['task_id']
    response = client.post(f'{path}/{task_id}/start')
    assert response.status_code == 503
    assert '知识库依据无法核实' in response.json()['detail']
    assert client.get(f'{path}/{task_id}').json()['state'] == 'setup'
