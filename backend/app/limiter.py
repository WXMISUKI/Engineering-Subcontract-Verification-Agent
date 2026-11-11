"""已认证账号分桶；匿名请求只使用连接地址，不信任转发头。"""
from jose import JWTError, jwt
from slowapi import Limiter
from slowapi.util import get_remote_address

from .auth.cookies import SESSION_COOKIE


def account_or_address(request):
    token = request.cookies.get(SESSION_COOKIE)
    secret = getattr(request.app.state, 'secret_key', None)
    if token and secret:
        try:
            payload = jwt.decode(token, secret, algorithms=['HS256'], options={'require_exp': True})
            subject = payload.get('sub')
            if isinstance(subject, str) and subject.isascii() and subject.isdecimal() and int(subject) > 0:
                return 'account:' + str(int(subject))
        except (JWTError, ValueError, TypeError):
            pass
    return 'address:' + get_remote_address(request)


limiter = Limiter(key_func=account_or_address, config_filename='')
