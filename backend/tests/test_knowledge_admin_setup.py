import pytest

from app.standalone.knowledge_admin_setup import register_admin, prepare_embedding, prepare_query_key, prepare_vision
from app.standalone.setup_journal import SetupJournalError


class FakeAdmin:
    def __init__(self):
        self.registrations = 0
        self.models = 0
        self.keys = 0
        self.vision = 0

    def register(self, username, email, password):
        self.registrations += 1

    def login(self, email, password):
        pass

    def create_embedding_model(self, name, base_url, api_key, dimension):
        self.models += 1
        return 'new-model-id'

    def create_retrieval_key(self, kb_ids):
        self.keys += 1
        return 'key-id', 'synthetic-query-secret'

    def create_vision_model(self, name, base_url, api_key):
        self.vision += 1
        return 'vision-id'


def test_admin_and_model_setup_is_repeatable_and_hides_secrets(tmp_path):
    admin = FakeAdmin()
    for _ in range(2):
        register_admin(admin, tmp_path, 'admin', 'test@example.invalid', 'synthetic-password')
        model_id = prepare_embedding(admin, tmp_path, 'model', 'https://example.invalid/v1', 'synthetic-key', 1024)
        assert model_id == 'new-model-id'
    assert admin.registrations == admin.models == 1
    assert 'synthetic-password' not in (tmp_path / 'setup-journal.json').read_text()
    assert 'synthetic-key' not in (tmp_path / 'setup-journal.json').read_text()


def test_model_configuration_change_never_reuses_old_model_id(tmp_path):
    admin = FakeAdmin()
    prepare_embedding(admin, tmp_path, 'model', 'https://example.invalid/v1', 'synthetic-key', 1024)
    with pytest.raises(SetupJournalError, match='模型配置'):
        prepare_embedding(admin, tmp_path, 'model', 'https://example.invalid/v1', 'synthetic-key', 512)
    assert admin.models == 1


def test_read_only_key_is_not_logged_and_is_not_created_twice(tmp_path):
    admin = FakeAdmin()
    prepare_query_key(admin, tmp_path, ['kb-one'])
    prepare_query_key(admin, tmp_path, ['kb-one'])
    assert admin.keys == 1
    assert (tmp_path / 'query-key').read_text() == 'synthetic-query-secret'
    assert 'synthetic-query-secret' not in (tmp_path / 'setup-journal.json').read_text()
    with pytest.raises(SetupJournalError, match='范围'):
        prepare_query_key(admin, tmp_path, ['kb-other'])
    assert admin.keys == 1


def test_vision_model_is_created_once_and_does_not_log_secret(tmp_path):
    admin = FakeAdmin()
    for _ in range(2):
        assert prepare_vision(admin, tmp_path, 'ocr', 'https://example.invalid/v1', 'synthetic-ocr-secret') == 'vision-id'
    assert admin.vision == 1
    assert 'synthetic-ocr-secret' not in (tmp_path / 'setup-journal.json').read_text()
