"""为分包路由提供独立账号的认证依赖。"""
import hashlib
import secrets

from fastapi import Depends, HTTPException, Request
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from .cookies import SESSION_COOKIE
from ..database import get_db
from ..standalone.identity import User

# 独立版只有一种 web 用户；保留广场账单路由所需的统一类型名，避免复制整套身份体系。
UnifiedUser = User


def get_current_any_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    try:
        if not token:
            raise JWTError('missing token')
        payload = jwt.decode(token, request.app.state.secret_key, algorithms=['HS256'])
        user_id = int(payload['sub'])
        fingerprint = payload['pwd']
        token_version = int(payload['ver'])
    except (JWTError, KeyError, ValueError, TypeError):
        raise HTTPException(status_code=401, detail='请先登录') from None
    user = db.get(User, user_id)
    if not user or not user.is_active or user.token_version != token_version or not secrets.compare_digest(
        hashlib.sha256(user.password_hash.encode('ascii')).hexdigest(), str(fingerprint)
    ):
        raise HTTPException(status_code=401, detail='请先登录')
    return user
