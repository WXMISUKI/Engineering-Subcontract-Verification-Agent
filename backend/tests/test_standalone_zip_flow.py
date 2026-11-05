"""独立 HTTP 入口的 ZIP 准备验收，全部资料均为合成内容。"""
import io
import json
import time
import zipfile
from pathlib import Path

import fitz
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.plus.blockchain_supervision import parsing
from app.plus.blockchain_supervision.models import BsMaterial
from app.standalone.identity import Base, create_account
from app.standalone.main import create_app


@pytest.mark.parametrize('entry,success', [('folder/packet.pdf', True), ('../outside.pdf', False)])
def test_zip_preparation_from_authenticated_entry(tmp_path, monkeypatch, entry, success):
    monkeypatch.setenv('SECRET_KEY', 'synthetic-secret-key-0123456789-abcdefgh')
    reference = Path(__file__).parent.parent / 'app/plus/blockchain_supervision/data/reference_package.json'
    sources = json.loads(reference.read_text(encoding='utf-8'))['sources']
    mapping = {'version': 1, 'documents': [
        {'sha256': digest, 'kb_id': 'synthetic-kb', 'knowledge_id': 'synthetic-' + digest,
         'collection': 'road_national_basis'}
        for digest in {row['original_sha256'] for row in sources}
    ]}
    mapping_path = tmp_path / 'mapping.json'
    mapping_path.write_text(json.dumps(mapping), encoding='utf-8')
    monkeypatch.setenv('BS_REFERENCE_MAPPING_FILE', str(mapping_path))
    monkeypatch.setattr(parsing, 'UPLOADS_DIR', str(tmp_path / 'uploads'))
    engine = create_engine('sqlite:///' + (tmp_path / 'business.sqlite').as_posix(),
                           connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    create_account(engine, 'alice', 'Alice-pass-123')
    client = TestClient(create_app(engine, 'x' * 64, cookie_secure=False))
    assert client.post('/api/login', json={'username': 'alice', 'password': 'Alice-pass-123'}).status_code == 200
    conversation = client.post('/api/conversations').json()['id']
    base = '/api/plus/blockchain-supervision'
    task = client.post(f'{base}/tasks', json={'conversation_id': conversation, 'title': 'synthetic',
                                               'scope_a1': True}).json()['task_id']
    document = fitz.open()
    document.new_page().insert_text((72, 72), 'SYNTHETIC CONTRACT ' * 8)
    pdf = document.tobytes()
    document.close()
    upload = f'{base}/tasks/{task}/materials/upload'
    assert client.post(upload, files={'file': ('contract.pdf', pdf, 'application/pdf')},
                       data={'role': 'labor'}).status_code == 200
    subjects = client.get(f'{base}/tasks/{task}/subjects').json()['subjects']
    assert subjects
    subject = subjects[0]['subject_id']
    assert client.patch(f'{base}/tasks/{task}/subjects/{subject}/type',
                        json={'subject_type': 'labor'}).status_code == 200
    assert client.patch(f'{base}/tasks/{task}/subjects/{subject}/name',
                        json={'name': '合成测试单位'}).status_code == 200
    packed = io.BytesIO()
    with zipfile.ZipFile(packed, 'w') as archive:
        archive.writestr(entry, pdf)
        archive.writestr('readme.txt', 'not a PDF')
    result = client.post(upload, files={'file': ('packet.zip', packed.getvalue(), 'application/zip')},
                         data={'role': 'packet', 'subject_id': str(subject)})
    assert result.status_code == 200, result.text
    container_id = result.json()['material_id']
    with Session(engine) as db:
        assert db.get(BsMaterial, container_id).parse_state == 'stored'
        assert db.query(BsMaterial).filter_by(container_material_id=container_id).count() == 0
    response = client.post(f'{base}/tasks/{task}/start')
    assert response.status_code == 202, response.text
    for _ in range(300):
        state = client.get(f'{base}/tasks/{task}').json()['state']
        if state != 'preparing':
            break
        time.sleep(.05)
    with Session(engine) as db:
        container = db.get(BsMaterial, container_id)
        children = db.query(BsMaterial).filter_by(container_material_id=container_id).all()
        if success:
            assert state == 'running'
            assert container.parse_state == 'done'
            assert len(children) == 1 and children[0].container_entry_path == entry
            assert children[0].parse_state == 'done'
            assert 'SYNTHETIC CONTRACT' in children[0].pages_json[0]['text']
        else:
            assert state == 'setup'
            assert container.parse_state == 'failed'
            assert not children
            assert not (tmp_path / 'outside.pdf').exists()
            assert client.post(f'{base}/tasks/{task}/start').status_code == 400
