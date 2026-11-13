"""分包审查使用的 OpenAI 兼容模型适配器，不依赖广场模型目录。"""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
import os

from app.plus.moma_security import require_https_endpoint

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
    def __init__(self, base_url, api_key, model, enable_thinking=False, prompt_cache_key=None, timeout=None,
                 max_retries=None, provider='legacy'):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.enable_thinking = enable_thinking
        self.prompt_cache_key = prompt_cache_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.provider = provider

    @property
    def checkpoint_request_mode(self):
        if self.provider == 'moma' and self.model == 'deepseek-v4.1-flash':
            return 'moma-deepseek-v41-thinking-disabled-v1'
        return None

    def _extra(self):
        if self.provider == 'moma':
            if self.checkpoint_request_mode:
                return {'extra_body': {'thinking': {'type': 'disabled'}}}
            return {}
        body = {'enable_thinking': self.enable_thinking}
        if self.prompt_cache_key:
            body['prompt_cache_key'] = self.prompt_cache_key
        return {'extra_body': body}

    async def complete(self, messages, on_usage=None):
        from openai import AsyncOpenAI
        retry_options = {'max_retries': self.max_retries} if self.max_retries is not None else {}
        if self.provider == 'moma':
            import httpx
            retry_options['http_client'] = httpx.AsyncClient(follow_redirects=False, timeout=self.timeout)
        client = AsyncOpenAI(base_url=self.base_url, api_key=self.api_key, timeout=self.timeout, **retry_options)
        try:
            response = await client.chat.completions.create(
                model=self.model, messages=messages, stream=False, temperature=0.2, **self._extra())
            if on_usage is not None:
                on_usage(response.usage.model_dump() if response.usage is not None else {})
            if self.provider == 'moma' and (not response.choices or response.choices[0].finish_reason != 'stop'
                                             or not (response.choices[0].message.content or '').strip()):
                raise RuntimeError('模型响应为空或未完整结束，需人工核实')
            return response.choices[0].message.content or ''
        finally:
            await client.close()


def llm_configured(model_code=None):
    model = os.environ.get('LLM_MODEL', '').strip()
    return bool(model and os.environ.get('LLM_API_BASE', '').strip() and os.environ.get('LLM_API_KEY', '').strip()
                and (model_code is None or model_code == model))


def client_from_env(enable_thinking=False, prompt_cache_key=None, model_code=None, max_retries=None):
    provider = os.environ.get('LLM_PROVIDER', 'legacy').strip().lower()
    if provider not in ('legacy', 'moma'):
        raise ValueError('不支持的业务模型 provider')
    if not llm_configured(model_code):
        raise RuntimeError('模型配置不完整或所选模型不可用')
    if provider == 'moma':
        require_https_endpoint(os.environ['LLM_API_BASE'].strip())
    return OpenAICompatibleClient(
        base_url=os.environ['LLM_API_BASE'].strip(), api_key=os.environ['LLM_API_KEY'].strip(),
        model=os.environ['LLM_MODEL'].strip(), enable_thinking=enable_thinking,
        prompt_cache_key=prompt_cache_key, timeout=float(os.environ.get('LLM_TIMEOUT_SECONDS', '120')),
        max_retries=max_retries, provider=provider)


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
