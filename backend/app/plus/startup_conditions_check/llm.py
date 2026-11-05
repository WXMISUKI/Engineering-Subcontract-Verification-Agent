"""分包审查使用的 OpenAI 兼容模型适配器，不依赖广场模型目录。"""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
import os

_required_model = ContextVar('bs_required_model', default=False)


def model_required() -> bool:
    return _required_model.get()


@contextmanager
def require_model():
    token = _required_model.set(True)
    try:
        yield
    finally:
        _required_model.reset(token)


class OpenAICompatibleClient:
    def __init__(self, base_url, api_key, model, enable_thinking=False, prompt_cache_key=None, timeout=None):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.enable_thinking = enable_thinking
        self.prompt_cache_key = prompt_cache_key
        self.timeout = timeout

    def _extra(self):
        body = {'enable_thinking': self.enable_thinking}
        if self.prompt_cache_key:
            body['prompt_cache_key'] = self.prompt_cache_key
        return {'extra_body': body}

    async def complete(self, messages, on_usage=None):
        from openai import AsyncOpenAI
        client = AsyncOpenAI(base_url=self.base_url, api_key=self.api_key, timeout=self.timeout)
        try:
            response = await client.chat.completions.create(
                model=self.model, messages=messages, stream=False, temperature=0.2, **self._extra())
            if on_usage is not None:
                on_usage(response.usage.model_dump() if response.usage is not None else {})
            return response.choices[0].message.content or ''
        finally:
            await client.close()


def llm_configured(model_code=None):
    model = os.environ.get('LLM_MODEL', '').strip()
    return bool(model and os.environ.get('LLM_API_BASE', '').strip() and os.environ.get('LLM_API_KEY', '').strip()
                and (model_code is None or model_code == model))


def client_from_env(enable_thinking=False, prompt_cache_key=None, model_code=None):
    if not llm_configured(model_code):
        raise RuntimeError('模型配置不完整或所选模型不可用')
    return OpenAICompatibleClient(
        base_url=os.environ['LLM_API_BASE'].strip(), api_key=os.environ['LLM_API_KEY'].strip(),
        model=os.environ['LLM_MODEL'].strip(), enable_thinking=enable_thinking,
        prompt_cache_key=prompt_cache_key, timeout=float(os.environ.get('LLM_TIMEOUT_SECONDS', '120')))


def run_sync(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        from concurrent.futures import ThreadPoolExecutor
        ctx = copy_context()
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(ctx.run, asyncio.run, coro).result()
    return asyncio.run(coro)
