import hashlib
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.models import KnowledgeBaseBinding, KnowledgeProvider
from app.standalone.identity import Base
from app.standalone.knowledge_binding_setup import bind_seed_knowledge, BindingSetupError
from app.standalone.knowledge_binding_finish import finish_seed_setup
from app.standalone.seed_manifest import validate_seed_manifest


def fixture_data(tmp_path):
    source = tmp_path / 'seeds'; source.mkdir()
    data = b'synthetic'
    (source / 'sample.txt').write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'version': 1, 'documents': [
        {'path': 'sample.txt', 'sha256': digest, 'collection': 'road_national_basis'}]}), encoding='utf-8')
    mapping = tmp_path / 'mapping.json'
    mapping.write_text(json.dumps({'version': 1, 'documents': [
        {'sha256': digest, 'collection': 'road_national_basis', 'kb_id': 'new-kb', 'knowledge_id': 'new-doc'}]}), encoding='utf-8')
    return validate_seed_manifest(manifest, source), mapping


def test_bindings_are_limited_to_seed_documents_and_repeatable(tmp_path):
    documents, mapping = fixture_data(tmp_path)
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    bind_seed_knowledge(engine, documents, mapping)
    bind_seed_knowledge(engine, documents, mapping)
    with Session(engine) as db:
        provider = db.scalar(select(KnowledgeProvider))
        rows = db.scalars(select(KnowledgeBaseBinding)).all()
        assert provider.base_url == 'http://weknora:8080/api/v1'
        assert provider.secret_ref == 'env://WEKNORA_API_KEY'
        assert len(rows) == 1 and rows[0].external_kb_id == 'new-kb'
        assert json.loads(rows[0].selected_knowledge_ids) == ['new-doc']
        assert rows[0].allow_query and not rows[0].allow_manage and not rows[0].allow_ingest


def test_tampered_seed_mapping_is_rejected_before_database_write(tmp_path):
    documents, mapping = fixture_data(tmp_path)
    body = json.loads(mapping.read_text())
    body['documents'][0]['collection'] = 'road_group_basis'
    mapping.write_text(json.dumps(body))
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with pytest.raises(BindingSetupError):
        bind_seed_knowledge(engine, documents, mapping)
    with Session(engine) as db:
        assert db.scalar(select(KnowledgeProvider)) is None


def test_existing_broader_binding_is_not_overwritten(tmp_path):
    documents, mapping = fixture_data(tmp_path)
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    bind_seed_knowledge(engine, documents, mapping)
    with Session(engine) as db:
        row = db.scalar(select(KnowledgeBaseBinding))
        row.selected_knowledge_ids = None
        db.commit()
    with pytest.raises(BindingSetupError):
        bind_seed_knowledge(engine, documents, mapping)
    with Session(engine) as db:
        assert db.scalar(select(KnowledgeBaseBinding)).selected_knowledge_ids is None


def test_mapping_publishes_only_after_read_only_key_and_database_binding(tmp_path):
    documents, staged = fixture_data(tmp_path)
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    class Admin:
        def create_retrieval_key(self, kb_ids):
            assert kb_ids == ['new-kb']
            return 'key-id', 'synthetic-query-key'
    final = tmp_path / 'reference-mapping.json'
    finish_seed_setup(engine, Admin(), documents, staged, final)
    assert final.exists() and not staged.exists()
    assert (tmp_path / 'query-key').read_text() == 'synthetic-query-key'
    with Session(engine) as db:
        assert db.scalar(select(KnowledgeBaseBinding)).external_kb_id == 'new-kb'
