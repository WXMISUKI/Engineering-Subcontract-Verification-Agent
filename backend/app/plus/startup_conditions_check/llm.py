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


class StructuredOutputTruncated(RuntimeError):
    def __init__(self, request_id=None):
        super().__init__('模型输出达到长度限制，响应被截断')
        self.request_id = request_id


class OpenAICompatibleClient:
    def __init__(self, base_url, api_key, model, enable_thinking=False, prompt_cache_key=None, timeout=None,
                 max_retries=None, provider='legacy', json_output=False):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.enable_thinking = enable_thinking
        self.prompt_cache_key = prompt_cache_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.provider = provider
        self.json_output = json_output

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
        return {'extra_body': body, **({'response_format': {'type': 'json_object'}} if self.json_output else {})}

    @property
    def supports_json_schema(self):
        # 百炼官方结构化输出支持清单；其他兼容端点不推测支持，也不失败后自动降级。
        return self.provider == 'legacy' and (
            self.model == 'qwen4-max' or self.model.startswith((
                'qwen3.7-flash', 'qwen3.7-plus', 'qwen3.7-max', 'qwen3.8-flash', 'qwen3.8-max')))

    async def complete(self, messages, on_usage=None, *, response_schema=None):
        from openai import AsyncOpenAI
        retry_options = {'max_retries': self.max_retries} if self.max_retries is not None else {}
        if self.provider == 'moma':
            import httpx
            retry_options['http_client'] = httpx.AsyncClient(follow_redirects=False, timeout=self.timeout)
        client = AsyncOpenAI(base_url=self.base_url, api_key=self.api_key, timeout=self.timeout, **retry_options)
        try:
            options = self._extra()
            if response_schema is not None:
                if not self.supports_json_schema:
                    raise ValueError('该模型尚未确认支持严格JSON Schema')
                options['response_format'] = {'type': 'json_schema', 'json_schema': {
                    'name': 'contract_retrospective', 'strict': True, 'schema': response_schema}}
            response = await client.chat.completions.create(
                model=self.model, messages=messages, stream=False, temperature=0.2, **options)
            if on_usage is not None:
                on_usage(response.usage.model_dump() if response.usage is not None else {})
            if response_schema is not None:
                finish_reason = response.choices[0].finish_reason if response.choices else None
                if finish_reason == 'length':
                    raise StructuredOutputTruncated(getattr(response, '_request_id', None))
                if finish_reason != 'stop':
                    raise RuntimeError('结构化响应未完整结束，不能作为完成结果')
            if self.provider == 'moma' and (not response.choices or response.choices[0].finish_reason != 'stop'
                                             or not (response.choices[0].message.content or '').strip()):
                raise RuntimeError('模型响应为空或未完整结束，需人工核实')
            return response.choices[0].message.content or ''
        finally:
            await client.close()


def _runtime_params(model_code=None):
    """按目录 code 解析请求模型/端点/Key；无 code 时返回 (None, None) 走环境变量。"""
    from app.services.billing.model_context import current_model_code
    from app.services.billing.model_keys import resolve_client_params
    code = model_code or current_model_code()
    if not code:
        return None, None
    params = resolve_client_params(
        code,
        default_model=os.environ.get('LLM_MODEL', '').strip(),
        default_base_url=os.environ.get('LLM_API_BASE', '').strip(),
    )
    return code, params


def llm_configured(model_code=None):
    code, params = _runtime_params(model_code)
    if code is not None:
        return bool(params and params.get('available'))
    model = os.environ.get('LLM_MODEL', '').strip()
    return bool(model and os.environ.get('LLM_API_BASE', '').strip() and os.environ.get('LLM_API_KEY', '').strip())


def client_from_env(enable_thinking=False, prompt_cache_key=None, model_code=None, max_retries=None, json_output=False):
    provider = os.environ.get('LLM_PROVIDER', 'legacy').strip().lower()
    if provider not in ('legacy', 'moma'):
        raise ValueError('不支持的业务模型 provider')
    code, params = _runtime_params(model_code)
    if code is not None:
        if not params or not params.get('available'):
            raise RuntimeError('所选模型不可用：模型目录、Key 或端点未配置')
        base_url = params.get('base_url')
        api_key = params.get('api_key')
        request_model = params.get('model') or code
    else:
        if not llm_configured():
            raise RuntimeError('模型配置不完整或所选模型不可用')
        base_url = os.environ['LLM_API_BASE'].strip()
        api_key = os.environ['LLM_API_KEY'].strip()
        request_model = os.environ['LLM_MODEL'].strip()
    if provider == 'moma':
        require_https_endpoint(base_url)
    return OpenAICompatibleClient(
        base_url=base_url, api_key=api_key,
        model=request_model, enable_thinking=enable_thinking,
        prompt_cache_key=prompt_cache_key, timeout=float(os.environ.get('LLM_TIMEOUT_SECONDS', '120')),
        max_retries=max_retries, provider=provider, json_output=json_output and provider == 'legacy')


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
