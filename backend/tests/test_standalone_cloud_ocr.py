import pytest

from app.plus.safety_review.ocr_cloud import cloud_config, cloud_ocr_structured


def test_cloud_ocr_requires_explicit_configuration(monkeypatch):
    for key in ('OCR_CLOUD_MODEL', 'OCR_CLOUD_ENDPOINT', 'OCR_CLOUD_API_KEY'):
        monkeypatch.delenv(key, raising=False)
    assert cloud_config() == {}
    monkeypatch.setenv('OCR_CLOUD_MODEL', 'test-model')
    monkeypatch.setenv('OCR_CLOUD_ENDPOINT', 'https://ocr.invalid/api')
    monkeypatch.setenv('OCR_CLOUD_API_KEY', 'synthetic')
    assert cloud_config()['model'] == 'test-model'


@pytest.mark.asyncio
async def test_cloud_ocr_preserves_structured_text(monkeypatch):
    import httpx

    def handler(request):
        assert request.headers['authorization'] == 'Bearer synthetic'
        return httpx.Response(200, json={'output': {'choices': [{'message': {'content': [{'text': '合成测试文本'}]}}]}})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    result = await cloud_ocr_structured(b'image', model='test', endpoint='https://ocr.invalid', api_key='synthetic')
    assert result['markdown'] == '合成测试文本'

