import pytest

from app.standalone.knowledge_seed_cli import read_environment, read_bootstrap_environment, read_vision_environment, validate_database_url


def test_seed_cli_requires_explicit_independent_admin_and_model():
    with pytest.raises(ValueError, match='WEKNORA_ADMIN_EMAIL'):
        read_environment({})
    with pytest.raises(ValueError, match='WEKNORA_EMBEDDING_MODEL_ID'):
        read_environment({'WEKNORA_ADMIN_EMAIL': 'test@example.invalid',
                          'WEKNORA_ADMIN_PASSWORD': 'synthetic'})


def test_seed_cli_uses_explicit_values_without_reporting_password():
    values = {'WEKNORA_ADMIN_EMAIL': 'test@example.invalid',
              'WEKNORA_ADMIN_PASSWORD': 'synthetic-secret',
              'WEKNORA_EMBEDDING_MODEL_ID': 'model-id'}
    result = read_environment(values)
    assert result == ('test@example.invalid', 'synthetic-secret', 'model-id')


def test_bootstrap_requires_embedding_service_and_valid_dimension():
    values = {'WEKNORA_ADMIN_USERNAME': 'admin', 'WEKNORA_ADMIN_EMAIL': 'test@example.invalid',
              'WEKNORA_ADMIN_PASSWORD': 'synthetic', 'EMBEDDING_MODEL': 'model',
              'EMBEDDING_API_BASE': 'https://example.invalid/v1', 'EMBEDDING_API_KEY': 'synthetic-key'}
    with pytest.raises(ValueError, match='EMBEDDING_DIMENSION'):
        read_bootstrap_environment(values)
    values['EMBEDDING_DIMENSION'] = '1024'
    assert read_bootstrap_environment(values)[-1] == 1024


def test_initialization_rejects_original_or_external_database():
    assert validate_database_url('mysql+pymysql://bs_review:test@mysql/bs_review')
    for url in ('mysql+pymysql://u:p@remote.example/bs_review', 'sqlite:///existing.db',
                'mysql+pymysql://u:p@mysql/other_database'):
        with pytest.raises(ValueError):
            validate_database_url(url)


def test_scanned_material_requires_explicit_vision_configuration():
    with pytest.raises(ValueError, match='KNOWLEDGE_VLM_MODEL'):
        read_vision_environment({})
    values = {'KNOWLEDGE_VLM_MODEL': 'ocr', 'KNOWLEDGE_VLM_API_BASE': 'https://example.invalid/v1',
              'KNOWLEDGE_VLM_API_KEY': 'synthetic-secret'}
    assert read_vision_environment(values) == ('ocr', 'https://example.invalid/v1', 'synthetic-secret')
