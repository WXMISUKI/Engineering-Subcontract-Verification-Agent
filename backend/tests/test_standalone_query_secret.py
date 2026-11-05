import pytest

from app.services.knowledge.provider_client import SecretResolver, SecretResolutionError


def test_query_key_is_read_from_explicit_secret_file(monkeypatch, tmp_path):
    path = tmp_path / 'query-key'
    path.write_text('synthetic-read-only-key\n', encoding='utf-8')
    monkeypatch.delenv('WEKNORA_API_KEY', raising=False)
    monkeypatch.setenv('WEKNORA_API_KEY_FILE', str(path))
    assert SecretResolver().resolve('env://WEKNORA_API_KEY') == 'synthetic-read-only-key'


def test_missing_secret_file_fails_without_revealing_content(monkeypatch, tmp_path):
    monkeypatch.delenv('WEKNORA_API_KEY', raising=False)
    monkeypatch.setenv('WEKNORA_API_KEY_FILE', str(tmp_path / 'missing'))
    with pytest.raises(SecretResolutionError):
        SecretResolver().resolve('env://WEKNORA_API_KEY')


def test_explicit_secret_file_takes_precedence_over_old_environment_key(monkeypatch, tmp_path):
    path = tmp_path / 'query-key'
    path.write_text('new-key', encoding='utf-8')
    monkeypatch.setenv('WEKNORA_API_KEY', 'old-key')
    monkeypatch.setenv('WEKNORA_API_KEY_FILE', str(path))
    assert SecretResolver().resolve('env://WEKNORA_API_KEY') == 'new-key'
