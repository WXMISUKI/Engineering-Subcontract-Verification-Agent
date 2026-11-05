import hashlib
import json

import pytest
import fitz

from app.standalone.knowledge_seed_setup import initialize_seed, validate_seed_inputs
from app.standalone.setup_journal import SetupJournal, SetupJournalError


class FakeAdmin:
    def __init__(self):
        self.created = []
        self.uploaded = []

    def create_knowledge_base(self, name, model_id, vision_model_id=None):
        self.created.append(name)
        return 'kb-' + name

    def upload_document(self, kb_id, path, enable_multimodal=False):
        self.uploaded.append(path.name)
        return 'doc-' + path.stem

    def document_status(self, knowledge_id):
        return 'completed'


def make_seed(tmp_path):
    root = tmp_path / 'source'
    root.mkdir()
    content = b'synthetic content'
    (root / 'sample.txt').write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'version': 1, 'documents': [
        {'path': 'sample.txt', 'sha256': digest, 'collection': 'road_national_basis'}]}), encoding='utf-8')
    package = {'sources': [{'original_sha256': digest, 'kb_id': 'old', 'knowledge_id': 'old'}]}
    return root, manifest, package


def test_seed_setup_is_repeatable_without_duplicate_remote_writes(tmp_path):
    root, manifest, package = make_seed(tmp_path)
    admin = FakeAdmin()
    output = tmp_path / 'config' / 'reference-mapping.json'
    initialize_seed(admin, 'model-id', manifest, root, output, package)
    initialize_seed(admin, 'model-id', manifest, root, output, package)
    assert admin.created == ['bs_road_national_basis']
    assert admin.uploaded == ['sample.txt']
    record = json.loads(output.read_text(encoding='utf-8'))['documents'][0]
    assert record['kb_id'] == 'kb-bs_road_national_basis'
    assert record['knowledge_id'] == 'doc-sample'
    assert record['collection'] == 'road_national_basis'


def test_uncertain_upload_is_not_retried(tmp_path):
    root, manifest, package = make_seed(tmp_path)
    digest = package['sources'][0]['original_sha256']
    output = tmp_path / 'config' / 'reference-mapping.json'
    journal = SetupJournal(output.parent / 'setup-journal.json')
    journal.begin('document:' + digest)
    admin = FakeAdmin()
    with pytest.raises(SetupJournalError, match='未确认'):
        initialize_seed(admin, 'model-id', manifest, root, output, package)
    assert not admin.uploaded
    assert not output.exists()


def test_changed_model_is_rejected_without_remote_mutation(tmp_path):
    root, manifest, package = make_seed(tmp_path)
    admin = FakeAdmin()
    output = tmp_path / 'config' / 'reference-mapping.json'
    initialize_seed(admin, 'model-id', manifest, root, output, package)
    before = output.read_bytes()
    with pytest.raises(SetupJournalError, match='模型已变化'):
        initialize_seed(admin, 'other-model', manifest, root, output, package)
    assert len(admin.created) == len(admin.uploaded) == 1
    assert output.read_bytes() == before


def test_failed_parse_does_not_publish_mapping(tmp_path):
    root, manifest, package = make_seed(tmp_path)
    admin = FakeAdmin()
    admin.document_status = lambda _id: 'failed'
    output = tmp_path / 'config' / 'reference-mapping.json'
    with pytest.raises(SetupJournalError, match='解析未成功'):
        initialize_seed(admin, 'model-id', manifest, root, output, package)
    assert not output.exists()


def test_reference_package_missing_a_seed_fails_before_remote_calls(tmp_path):
    root, manifest, package = make_seed(tmp_path)
    package['sources'][0]['original_sha256'] = 'f' * 64
    admin = FakeAdmin()
    with pytest.raises(SetupJournalError, match='种子缺口'):
        validate_seed_inputs(manifest, root, package)
    with pytest.raises(SetupJournalError, match='种子缺口'):
        initialize_seed(admin, 'model-id', manifest, root, tmp_path / 'map.json', package)
    assert not admin.created and not admin.uploaded


def test_scanned_pdf_requires_configured_vision_before_creating_libraries(tmp_path):
    root = tmp_path / 'seeds'; root.mkdir()
    pdf = root / 'scan.pdf'
    doc = fitz.open(); doc.new_page(); doc.save(pdf); doc.close()
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'version': 1, 'documents': [
        {'path': 'scan.pdf', 'sha256': digest, 'collection': 'road_national_basis'}]}))
    admin = FakeAdmin()
    with pytest.raises(SetupJournalError, match='OCR'):
        initialize_seed(admin, 'model-id', manifest, root, tmp_path / 'map.json', {'sources': []})
    assert not admin.created and not admin.uploaded
