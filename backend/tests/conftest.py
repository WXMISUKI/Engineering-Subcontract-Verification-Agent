import socket
import json
from pathlib import Path
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.standalone.identity import Base, Agent, ConversationMapping, User
from app.plus.blockchain_supervision import models as _bs_models
from app.models import models as _knowledge_models
from app.limiter import limiter


@pytest.fixture(autouse=True)
def reset_rate_limits():
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def db_session(monkeypatch, tmp_path):
    monkeypatch.setenv('SECRET_KEY', 'synthetic-test-key-01234567890123456789')
    reference = Path(__file__).parent.parent / 'app/plus/blockchain_supervision/data/reference_package.json'
    source = json.loads(reference.read_text(encoding='utf-8'))
    digests = {row['original_sha256'] for row in source['sources']}
    mapping = {'version': 1, 'documents': [{'sha256': digest, 'kb_id': 'test-kb', 'knowledge_id': 'test-' + digest,
                                            'collection': 'road_national_basis'} for digest in digests]}
    mapping_path = tmp_path / 'mapping.json'
    mapping_path.write_text(json.dumps(mapping), encoding='utf-8')
    monkeypatch.setenv('BS_REFERENCE_MAPPING_FILE', str(mapping_path))
    engine = create_engine('sqlite://')

    @event.listens_for(engine, 'connect')
    def enable_fk(connection, _record):
        connection.execute('PRAGMA foreign_keys=ON')

    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([User(id=1, username='test1', password_hash='synthetic'),
                    User(id=2, username='test2', password_hash='synthetic'),
                    Agent(id=1, slug='blockchain-supervision')])
        db.flush()
        db.add_all([ConversationMapping(id=101, user_id=1, user_type='tester', agent_id=1),
                    ConversationMapping(id=102, user_id=2, user_type='tester', agent_id=1)])
        db.commit()
        yield db
    engine.dispose()


@pytest.fixture(autouse=True)
def block_external_connections(monkeypatch):
    import httpx
    original_connect = socket.socket.connect
    original_send = httpx.Client.send
    original_async_send = httpx.AsyncClient.send

    def checked_connect(sock, address):
        if isinstance(address, tuple) and address[0] not in ('localhost', '127.0.0.1', '::1'):
            raise OSError('tests must not access external services')
        return original_connect(sock, address)

    monkeypatch.setattr(socket.socket, 'connect', checked_connect)

    def check_http(client, request):
        transport = getattr(client, '_transport', None)
        if isinstance(transport, (httpx.MockTransport, httpx.ASGITransport, httpx.WSGITransport)):
            return
        if request.url.host not in ('localhost', '127.0.0.1', '::1', 'testserver'):
            raise OSError('tests must not access external services')

    def send(client, request, *args, **kwargs):
        check_http(client, request)
        return original_send(client, request, *args, **kwargs)

    async def async_send(client, request, *args, **kwargs):
        check_http(client, request)
        return await original_async_send(client, request, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, 'send', send)
    monkeypatch.setattr(httpx.AsyncClient, 'send', async_send)
