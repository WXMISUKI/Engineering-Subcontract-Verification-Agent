"""Safe, structured classification of online model request failures."""
import httpx
from openai import APITimeoutError


class ModelHTTPError(RuntimeError):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"Model HTTP {status_code}")


def classify_request_error(exc: Exception) -> str:
    status = getattr(exc, 'status_code', None)
    if type(status) is int:
        if status == 429:
            return 'rate_limit'
        if status in (401, 403):
            return 'authentication'
        if status >= 500:
            return 'service_error'
    if isinstance(exc, (TimeoutError, httpx.TimeoutException, APITimeoutError)):
        return 'timeout'
    return 'model_call_error'


ERROR_MESSAGES = {
    'rate_limit': '服务请求受限（HTTP 429），请稍后重试',
    'authentication': '服务认证、权限或额度异常，请联系管理员检查',
    'service_error': '服务暂时不可用，请稍后重试',
    'timeout': '服务响应超时，请稍后重试',
    'model_call_error': '服务请求未完成，请稍后重试或联系管理员',
}
