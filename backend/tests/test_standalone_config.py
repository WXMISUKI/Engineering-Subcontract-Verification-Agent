import pytest
from app.config import get_settings


def test_secret_is_required_and_upload_limit_is_configurable(monkeypatch):
    monkeypatch.delenv('SECRET_KEY', raising=False)
    with pytest.raises(RuntimeError):
        get_settings()
    monkeypatch.setenv('SECRET_KEY', 'synthetic-secret-for-tests-only-12345')
    monkeypatch.setenv('BLOCKCHAIN_SUPERVISION_UPLOAD_MAX_MB', '42')
    assert get_settings().BLOCKCHAIN_SUPERVISION_UPLOAD_MAX_MB == 42
