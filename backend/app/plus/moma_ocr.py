"""基于 MoMA 文档中图片理解 Chat Completions 协议的页面转写。"""
import base64

import httpx

from app.plus.moma_security import require_https_endpoint
from app.plus.request_errors import ModelHTTPError

_MAX_BASE64_BYTES = 4_000_000
_DEFAULT_PROMPT = '请完整转写图片中的文字，保留原有顺序，不要总结或猜测。'


async def recognize_page(png_bytes, *, model, endpoint, api_key, prompt=None, on_usage=None):
    require_https_endpoint(endpoint)
    if 4 * ((len(png_bytes) + 2) // 3) >= _MAX_BASE64_BYTES:
        raise ValueError('页面图片超过 MoMA 接口的编码大小上限，需人工核实')
    encoded = base64.b64encode(png_bytes).decode('ascii')
    body = {'model': model, 'stream': False, 'messages': [{'role': 'user', 'content': [
        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + encoded}},
        {'type': 'text', 'text': prompt or _DEFAULT_PROMPT},
    ]}]}
    try:
        async with httpx.AsyncClient(timeout=180.0, follow_redirects=False) as client:
            response = await client.post(endpoint, json=body, headers={'Authorization': f'Bearer {api_key}'})
    except httpx.TimeoutException:
        raise TimeoutError('MoMA OCR 请求超时') from None
    except httpx.HTTPError:
        raise RuntimeError('MoMA OCR 连接失败') from None
    if response.status_code != 200:
        raise ModelHTTPError(response.status_code)
    try:
        data = response.json()
        if on_usage is not None and isinstance(data.get('usage'), dict):
            usage = data['usage']
            on_usage({key: usage.get(key, 0) or 0 for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')})
        choice = data['choices'][0]
        content = choice['message']['content']
        if choice.get('finish_reason') != 'stop' or not isinstance(content, str) or not content.strip():
            raise ValueError
    except (KeyError, IndexError, TypeError, ValueError):
        raise RuntimeError('MoMA OCR 响应为空、不完整或格式异常，需人工核实') from None
    return {'markdown': content.strip(), 'words': []}
