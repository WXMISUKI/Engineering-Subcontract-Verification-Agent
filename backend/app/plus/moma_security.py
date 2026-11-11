"""MoMA 端点的基本传输安全校验；网络白名单和服务资质由部署方负责。"""
from urllib.parse import urlsplit


def require_https_endpoint(endpoint: str) -> None:
    try:
        parsed = urlsplit(endpoint)
        valid = (parsed.scheme.lower() == 'https' and bool(parsed.hostname)
                 and parsed.username is None and parsed.password is None
                 and not parsed.query and not parsed.fragment and parsed.port != 0)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('MoMA 端点必须使用不含凭据或查询参数的 HTTPS 地址')
