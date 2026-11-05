import pytest

from app.plus.startup_conditions_check.llm import client_from_env, llm_configured, run_sync


def test_model_requires_all_explicit_settings(monkeypatch):
    for key in ('LLM_MODEL', 'LLM_API_BASE', 'LLM_API_KEY'):
        monkeypatch.delenv(key, raising=False)
    assert not llm_configured()
    with pytest.raises(RuntimeError):
        client_from_env()
    monkeypatch.setenv('LLM_MODEL', 'synthetic-model')
    monkeypatch.setenv('LLM_API_BASE', 'https://model.invalid/v1')
    monkeypatch.setenv('LLM_API_KEY', 'synthetic')
    assert llm_configured()
    assert client_from_env().model == 'synthetic-model'
    assert not llm_configured('other-model')
    with pytest.raises(RuntimeError):
        client_from_env(model_code='other-model')


@pytest.mark.asyncio
async def test_run_sync_inside_running_loop():
    async def work():
        return 'ok'
    assert run_sync(work()) == 'ok'
