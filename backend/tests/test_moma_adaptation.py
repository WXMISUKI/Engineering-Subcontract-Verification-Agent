import json

import httpx
import openai
import pytest

from app.plus.safety_review.ocr_cloud import cloud_config, cloud_ocr_image, cloud_ocr_structured
from app.plus.startup_conditions_check.llm import client_from_env


def configure_ocr(monkeypatch):
    monkeypatch.setenv('OCR_CLOUD_PROVIDER', 'moma')
    monkeypatch.setenv('OCR_CLOUD_MODEL', 'synthetic-vision')
    monkeypatch.setenv('OCR_CLOUD_ENDPOINT', 'https://vision.example.invalid/v1/chat/completions')
    monkeypatch.setenv('OCR_CLOUD_API_KEY', 'synthetic-key')


@pytest.mark.asyncio
async def test_moma_image_request_and_text_only_result(monkeypatch):
    configure_ocr(monkeypatch)
    usage = []

    def handler(request):
        body = json.loads(request.content)
        assert request.headers['authorization'] == 'Bearer synthetic-key'
        assert body['model'] == 'synthetic-vision'
        assert body['messages'][0]['content'][0]['image_url']['url'].startswith('data:image/png;base64,')
        assert 'input' not in body
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': '合成页面文字'}}],
                                         'usage': {'prompt_tokens': 4, 'completion_tokens': 3, 'total_tokens': 7}})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    cfg = cloud_config()
    result = await cloud_ocr_structured(b'synthetic image', model=cfg['model'], endpoint=cfg['endpoint'],
                                        api_key=cfg['api_key'], on_usage=usage.append)
    assert result == {'markdown': '合成页面文字', 'words': []}
    assert usage == [{'prompt_tokens': 4, 'completion_tokens': 3, 'total_tokens': 7}]


@pytest.mark.asyncio
@pytest.mark.parametrize('content,reason', [('', 'stop'), ('不完整', 'length'), ('拒绝', 'content_filter')])
async def test_moma_rejects_incomplete_or_empty_result(monkeypatch, content, reason):
    configure_ocr(monkeypatch)
    real_client = httpx.AsyncClient
    handler = lambda request: httpx.Response(200, json={'choices': [{'finish_reason': reason, 'message': {'content': content}}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(RuntimeError):
        await cloud_ocr_structured(b'image', model='synthetic', endpoint='https://vision.example.invalid', api_key='synthetic')


def test_provider_validation_and_tls(monkeypatch):
    configure_ocr(monkeypatch)
    monkeypatch.setenv('OCR_CLOUD_ENDPOINT', 'http://vision.example.invalid')
    with pytest.raises(ValueError):
        cloud_config()
    monkeypatch.setenv('OCR_CLOUD_PROVIDER', 'unknown')
    with pytest.raises(ValueError):
        cloud_config()
    monkeypatch.setenv('LLM_PROVIDER', 'moma')
    monkeypatch.setenv('LLM_MODEL', 'synthetic')
    monkeypatch.setenv('LLM_API_BASE', 'http://text.example.invalid/v1')
    monkeypatch.setenv('LLM_API_KEY', 'synthetic')
    with pytest.raises(ValueError):
        client_from_env()


def test_moma_text_omits_vendor_extensions(monkeypatch):
    monkeypatch.setenv('LLM_PROVIDER', 'moma')
    monkeypatch.setenv('LLM_MODEL', 'synthetic')
    monkeypatch.setenv('LLM_API_BASE', 'https://text.example.invalid/v1')
    monkeypatch.setenv('LLM_API_KEY', 'synthetic')
    client = client_from_env(enable_thinking=True, prompt_cache_key='synthetic')
    assert client._extra() == {}


def test_moma_does_not_reuse_legacy_provider_checkpoint(monkeypatch):
    from types import SimpleNamespace
    from app.plus.blockchain_supervision import service
    from app.plus.startup_conditions_check.llm import OpenAICompatibleClient
    monkeypatch.setattr(service, 'get_settings', lambda: SimpleNamespace(SECRET_KEY='synthetic-secret'))
    legacy = OpenAICompatibleClient('https://example.invalid/v1', 'synthetic', 'model')
    moma = OpenAICompatibleClient('https://example.invalid/v1', 'synthetic', 'model', provider='moma')
    assert service.ai_checkpoint_client_fingerprint(legacy) != service.ai_checkpoint_client_fingerprint(moma)


@pytest.mark.asyncio
async def test_moma_text_request_and_usage(monkeypatch):
    monkeypatch.setenv('LLM_PROVIDER', 'moma')
    monkeypatch.setenv('LLM_MODEL', 'synthetic-text')
    monkeypatch.setenv('LLM_API_BASE', 'https://text.example.invalid/v1')
    monkeypatch.setenv('LLM_API_KEY', 'synthetic')
    usage = []

    def handler(request):
        body = json.loads(request.content)
        assert request.url.path == '/v1/chat/completions'
        assert request.headers['authorization'] == 'Bearer synthetic'
        assert 'enable_thinking' not in body and 'prompt_cache_key' not in body
        return httpx.Response(200, json={'id': 'synthetic', 'object': 'chat.completion', 'created': 1,
            'model': 'synthetic-text', 'choices': [{'index': 0, 'finish_reason': 'stop',
            'message': {'role': 'assistant', 'content': '合成答复'}}],
            'usage': {'prompt_tokens': 2, 'completion_tokens': 3, 'total_tokens': 5}})

    class MockClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(httpx, 'AsyncClient', MockClient)
    result = await client_from_env().complete([{'role': 'user', 'content': '合成问题'}], on_usage=usage.append)
    assert result == '合成答复'
    assert usage[0]['total_tokens'] == 5


@pytest.mark.asyncio
async def test_moma_text_does_not_follow_redirect(monkeypatch):
    monkeypatch.setenv('LLM_PROVIDER', 'moma')
    monkeypatch.setenv('LLM_MODEL', 'synthetic')
    monkeypatch.setenv('LLM_API_BASE', 'https://text.example.invalid/v1')
    monkeypatch.setenv('LLM_API_KEY', 'synthetic')
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={'Location': 'http://other.example.invalid/collect'})

    class MockClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(httpx, 'AsyncClient', MockClient)
    with pytest.raises(Exception):
        await client_from_env(max_retries=0).complete([{'role': 'user', 'content': '合成问题'}])
    assert requests == ['https://text.example.invalid/v1/chat/completions']


@pytest.mark.asyncio
@pytest.mark.parametrize('content,reason', [('', 'stop'), ('incomplete', 'length')])
async def test_moma_text_rejects_incomplete_response(monkeypatch, content, reason):
    monkeypatch.setenv('LLM_PROVIDER', 'moma')
    monkeypatch.setenv('LLM_MODEL', 'synthetic')
    monkeypatch.setenv('LLM_API_BASE', 'https://text.example.invalid/v1')
    monkeypatch.setenv('LLM_API_KEY', 'synthetic')
    def handler(request):
        return httpx.Response(200, json={'id': 'synthetic', 'object': 'chat.completion', 'created': 1,
            'model': 'synthetic', 'choices': [{'index': 0, 'finish_reason': reason,
            'message': {'role': 'assistant', 'content': content}}]})
    class MockClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(httpx, 'AsyncClient', MockClient)
    with pytest.raises(RuntimeError):
        await client_from_env().complete([{'role': 'user', 'content': '合成问题'}])


@pytest.mark.asyncio
async def test_moma_rejects_oversized_image_before_network(monkeypatch):
    configure_ocr(monkeypatch)
    with pytest.raises(ValueError):
        await cloud_ocr_structured(b'x' * 3_000_000, model='synthetic',
                                   endpoint='https://vision.example.invalid', api_key='synthetic')


@pytest.mark.asyncio
async def test_word_box_only_ocr_cannot_fall_back_to_legacy_provider(monkeypatch):
    configure_ocr(monkeypatch)
    with pytest.raises(RuntimeError):
        await cloud_ocr_image(b'image', model='synthetic', endpoint='https://vision.example.invalid', api_key='synthetic')


def test_moma_page_failure_stays_unread(monkeypatch, tmp_path):
    import pymupdf as fitz
    from app.plus.blockchain_supervision.parsing import parse_material_pages

    configure_ocr(monkeypatch)
    pdf = tmp_path / 'synthetic.pdf'
    document = fitz.open()
    document.new_page().draw_rect(fitz.Rect(30, 30, 90, 90), fill=(0, 0, 0))
    document.save(pdf)
    document.close()
    real_client = httpx.AsyncClient
    handler = lambda request: httpx.Response(200, json={'choices': [
        {'finish_reason': 'length', 'message': {'content': '不完整'}}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    pages, degradations = parse_material_pages(str(pdf))
    assert not pages
    assert any(item.get('kind') == 'parse_incomplete' and 1 in item.get('unread_pages', [])
               for item in degradations)


def test_moma_page_text_keeps_physical_page_without_invented_boxes(monkeypatch, tmp_path):
    import pymupdf as fitz
    from app.plus.blockchain_supervision.parsing import parse_material_pages

    configure_ocr(monkeypatch)
    pdf = tmp_path / 'synthetic.pdf'
    document = fitz.open()
    document.new_page().draw_rect(fitz.Rect(30, 30, 90, 90), fill=(0, 0, 0))
    document.save(pdf)
    document.close()
    real_client = httpx.AsyncClient
    handler = lambda request: httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
        'message': {'content': '这是一份用于测试的工程资料，包含主体名称和材料编号。' * 4}}]})
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    pages, _ = parse_material_pages(str(pdf))
    assert len(pages) == 1 and pages[0]['page'] == 1
    assert '用于测试的工程资料' in pages[0]['text']
    assert not (pages[0].get('ocr_meta') or {}).get('layout')
