"""中断恢复必须核对远端原件，不把同名文件当作同一资料。"""
import hashlib
import json

import pytest

from app.standalone.knowledge_seed_setup import initialize_seed
from app.standalone.setup_journal import SetupJournal, SetupJournalError
from app.standalone.knowledge_admin_setup import register_admin, prepare_embedding
from app.standalone.knowledge_seed_cli import recover_bootstrap_upload
from test_knowledge_seed_setup import FakeAdmin, make_seed


class LostUploadAdmin(FakeAdmin):
    def __init__(self):
        super().__init__()
        self.remote_id = None
        self.fail_once = True

    def register(self, username, email, password):
        pass

    def login(self, email, password):
        pass

    def create_embedding_model(self, name, base_url, api_key, dimension):
        return 'new-model-id'

    def upload_document(self, kb_id, path, enable_multimodal=False):
        self.remote_id = super().upload_document(kb_id, path, enable_multimodal)
        if self.fail_once:
            self.fail_once = False
            raise TimeoutError('synthetic response lost')
        return self.remote_id

    def find_verified_document(self, kb_id, path, sha256, model_id):
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sha256
        assert model_id == 'model-id'
        return self.remote_id


def test_response_lost_can_recover_only_verified_document_without_upload_again(tmp_path):
    from app.standalone.knowledge_upload_recovery import recover_pending_upload
    root, manifest, package = make_seed(tmp_path)
    admin = LostUploadAdmin()
    output = tmp_path / 'config' / 'mapping.json'
    with pytest.raises(TimeoutError):
        initialize_seed(admin, 'model-id', manifest, root, output, package)
    recover_pending_upload(admin, 'model-id', manifest, root, output.parent, package)
    initialize_seed(admin, 'model-id', manifest, root, output, package)
    assert admin.uploaded == ['sample.txt']
    assert json.loads(output.read_text(encoding='utf-8'))['documents'][0]['knowledge_id'] == admin.remote_id


def test_unconfirmed_document_or_changed_model_never_mutates_journal(tmp_path):
    from app.standalone.knowledge_upload_recovery import recover_pending_upload
    root, manifest, package = make_seed(tmp_path)
    admin = LostUploadAdmin()
    output = tmp_path / 'config' / 'mapping.json'
    with pytest.raises(TimeoutError):
        initialize_seed(admin, 'model-id', manifest, root, output, package)
    journal = output.parent / 'setup-journal.json'
    before = journal.read_bytes()
    with pytest.raises(SetupJournalError):
        recover_pending_upload(admin, 'other-model', manifest, root, output.parent, package)
    assert journal.read_bytes() == before
    admin.find_verified_document = lambda *args: None
    with pytest.raises(SetupJournalError):
        recover_pending_upload(admin, 'model-id', manifest, root, output.parent, package)
    assert journal.read_bytes() == before
    assert len(admin.uploaded) == 1


def test_bootstrap_recovers_pending_upload_before_normal_setup(tmp_path):
    root, manifest, package = make_seed(tmp_path)
    admin = LostUploadAdmin()
    output = tmp_path / 'config' / 'mapping.json'
    register_admin(admin, output.parent, 'admin', 'admin@example.invalid', 'synthetic-password')
    prepare_embedding(admin, output.parent, 'model', 'https://example.invalid', 'synthetic-key', 1024)
    with pytest.raises(TimeoutError):
        initialize_seed(admin, 'new-model-id', manifest, root, output, package)
    admin.find_verified_document = lambda kb, path, sha, model: admin.remote_id if model == 'new-model-id' else None
    kwargs = dict(username='admin', email='admin@example.invalid', password='synthetic-password',
                  model='model', base_url='https://example.invalid', dimension=1024)
    journal_path = output.parent / 'setup-journal.json'
    before = journal_path.read_bytes()
    with pytest.raises(SetupJournalError):
        recover_bootstrap_upload(admin, output.parent, manifest, root, package, **{**kwargs, 'dimension': 512})
    assert journal_path.read_bytes() == before
    assert recover_bootstrap_upload(admin, output.parent, manifest, root, package, **kwargs)
    initialize_seed(admin, 'new-model-id', manifest, root, output, package)
    assert len(admin.uploaded) == 1
