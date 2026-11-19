"""独立应用的认证、会话和分包审查入口。"""

import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import os
import secrets

import bcrypt
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from jose import jwt
from pydantic import BaseModel, Field
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .identity import Agent, ConversationMapping, User
from ..database import SessionLocal, get_db
from ..auth.cookies import SESSION_COOKIE
from ..auth.unified_user import get_current_any_user
from ..limiter import limiter
from .reference_mapping import MappingError
from ..plus.blockchain_supervision.manifest import manifest

logger = logging.getLogger(__name__)


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1000)


class ModelChoice(BaseModel):
    model_code: str = Field(min_length=1, max_length=64)


@limiter.limit('10/minute', key_func=get_remote_address)
def login(request: Request, body: Login, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == body.username))
    raw = body.password.encode('utf-8')
    valid = len(raw) <= 72 and bcrypt.checkpw(raw, (user.password_hash.encode('ascii') if user else request.app.state.dummy_hash))
    if not user or not user.is_active or not valid:
        raise HTTPException(status_code=401, detail='账号或密码错误')
    expiry = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode({'sub': str(user.id), 'exp': expiry, 'ver': user.token_version,
                        'pwd': hashlib.sha256(user.password_hash.encode('ascii')).hexdigest()},
                       request.app.state.secret_key, algorithm='HS256')
    response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=request.app.state.cookie_secure,
                        samesite='lax', max_age=8 * 60 * 60, path='/')
    return {'username': user.username}


def create_app(engine, secret_key: str, *, cookie_secure: bool = True, include_subcontract: bool = True,
               runtime_lock_dir=None) -> FastAPI:
    if len(secret_key.encode('utf-8')) < 32:
        raise ValueError('secret key must contain at least 32 bytes')
    @asynccontextmanager
    async def lifespan(app):
        if runtime_lock_dir is None:
            yield
            return
        from . import runtime
        from ..plus.blockchain_supervision.service import recover_interrupted_ingests
        from ..plus.blockchain_supervision import background_ai_review
        with runtime.exclusive_instance(runtime_lock_dir):
            with Session(engine) as db:
                recover_interrupted_ingests(db)
            # AI 核查任务表是调度事实源：启动时接管过期租约并续跑排队任务，
            # 运行期由巡检覆盖进程内崩溃/长任务租约过期。
            await background_ai_review.resume_queued_jobs(engine)
            maintenance = asyncio.create_task(background_ai_review.maintenance_loop(engine))
            try:
                yield
            finally:
                maintenance.cancel()
                with suppress(asyncio.CancelledError):
                    await maintenance

    app = FastAPI(title='工程分包审查智能体', lifespan=lifespan)
    SessionLocal.configure(bind=engine)
    app.state.secret_key = secret_key
    app.state.cookie_secure = cookie_secure
    app.state.dummy_hash = bcrypt.hashpw(secrets.token_bytes(32), bcrypt.gensalt())
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    @app.exception_handler(MappingError)
    async def mapping_error_handler(request, exc):
        cause_type = type(exc.__cause__).__name__ if exc.__cause__ is not None else "none"
        logger.error("[bs] 知识库依据映射检查失败 code=%s reason=%s context=%s cause_type=%s",
                     exc.code, str(exc), exc.context, cause_type)
        return JSONResponse(status_code=503, content={'detail': '知识库依据无法核实，请联系管理员完成知识初始化'})

    app.add_api_route('/api/login', login, methods=['POST'])
    # 独立版沿用智能体广场的用户侧账单适配器；默认 shadow，只记录用量与预估，不扣余额。
    from ..routers.billing_user import router as billing_user_router
    app.include_router(billing_user_router)
    if include_subcontract:
        from ..plus.blockchain_supervision.router import router as subcontract_router
        app.include_router(subcontract_router, prefix='/api/plus/blockchain-supervision')

    @app.get('/api/health')
    def health():
        try:
            with engine.connect() as connection:
                connection.execute(text('SELECT 1'))
        except Exception:
            raise HTTPException(status_code=503, detail='数据库不可用') from None
        return {'status': 'ok', 'scope': 'identity'}

    @app.post('/api/logout', status_code=204)
    def logout(response: Response, user: User = Depends(get_current_any_user), db: Session = Depends(get_db)):
        user.token_version += 1
        db.commit()
        response.delete_cookie(SESSION_COOKIE, path='/', secure=cookie_secure, httponly=True, samesite='lax')

    @app.get('/api/session')
    def session(user: User = Depends(get_current_any_user)):
        return {'username': user.username}

    def _current_model_code(db: Session) -> str:
        """目录默认模型优先；旧库/未初始化计费目录时回退环境变量。"""
        try:
            from ..services.billing.registry import get_default_model
            model = get_default_model(db)
            if model is not None:
                return model.code
        except Exception:  # noqa: BLE001 —— 只读展示，目录不可用不阻断页面
            logger.warning('读取模型目录失败，回退 LLM_MODEL', exc_info=True)
        return os.environ.get('LLM_MODEL', '').strip()

    @app.get('/api/model')
    def model_status(db: Session = Depends(get_db), user: User = Depends(get_current_any_user)):
        return {'model': _current_model_code(db)}

    @app.get('/api/models')
    def list_models(db: Session = Depends(get_db), user: User = Depends(get_current_any_user)):
        """当前部署可切换的对话模型；只读目录，不触发任何模型调用。"""
        from ..services.billing import bindings
        try:
            models = bindings.available_models(db)
        except Exception:  # noqa: BLE001 —— 旧库无计费目录时保持可打开
            logger.warning('读取可选模型目录失败', exc_info=True)
            models = []
        return {
            'current': _current_model_code(db),
            'models': [{'code': row['code'], 'display_name': row['display_name']} for row in models],
        }

    @app.put('/api/model')
    def switch_model(body: ModelChoice, db: Session = Depends(get_db),
                     user: User = Depends(get_current_any_user)):
        """切换部署级默认模型；只接受目录内可选、启用且已定价的对话模型。"""
        from ..services.billing import billing_settings, bindings
        from ..services.billing.registry import get_model
        available = {row['code'] for row in bindings.available_models(db)}
        if body.model_code not in available:
            raise HTTPException(status_code=409, detail='该模型当前不可选：未启用、未开放或尚未配置价格')
        model = get_model(db, body.model_code)
        if model is None:
            raise HTTPException(status_code=404, detail='模型不在目录中')
        billing_settings.update_settings(db, {'default_model_id': model.id}, operator_id=user.id)
        db.commit()
        return {'model': model.code}

    @app.post('/api/conversations')
    def create_conversation(user: User = Depends(get_current_any_user), db: Session = Depends(get_db)):
        agent = db.scalar(select(Agent).where(Agent.slug == manifest['code']))
        if agent is None:
            raise HTTPException(status_code=503, detail='应用尚未初始化')
        conversation = ConversationMapping(user_id=user.id, user_type='web', agent_id=agent.id)
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        return {'id': conversation.id}

    @app.get('/api/conversations')
    def list_conversations(user: User = Depends(get_current_any_user), db: Session = Depends(get_db)):
        rows = db.scalars(select(ConversationMapping).where(
            ConversationMapping.user_id == user.id, ConversationMapping.user_type == 'web',
            ConversationMapping.is_deleted.is_(False)).order_by(ConversationMapping.id.desc())).all()
        titles = {}
        if rows and include_subcontract:
            from ..plus.blockchain_supervision.models import BsTask
            tasks = db.scalars(select(BsTask).where(
                BsTask.conversation_id.in_([row.id for row in rows]),
                BsTask.created_by == user.id, BsTask.created_by_type == 'web').order_by(BsTask.id.desc())).all()
            for task in tasks:
                titles.setdefault(task.conversation_id, task.title)
        return [{'id': row.id, 'title': titles.get(row.id) or '新建审查'} for row in rows]

    @app.get('/api/conversations/{conversation_id}')
    def get_conversation(conversation_id: int, user: User = Depends(get_current_any_user), db: Session = Depends(get_db)):
        row = db.scalar(select(ConversationMapping).where(
            ConversationMapping.id == conversation_id, ConversationMapping.user_id == user.id,
            ConversationMapping.user_type == 'web', ConversationMapping.is_deleted.is_(False)))
        if row is None:
            raise HTTPException(status_code=404, detail='会话不存在')
        return {'id': row.id}

    @app.delete('/api/conversations/{conversation_id}', status_code=204)
    def delete_conversation(conversation_id: int, user: User = Depends(get_current_any_user),
                            db: Session = Depends(get_db)):
        row = db.scalar(select(ConversationMapping).where(
            ConversationMapping.id == conversation_id, ConversationMapping.user_id == user.id,
            ConversationMapping.user_type == 'web', ConversationMapping.is_deleted.is_(False)
        ).with_for_update())
        if row is None:
            raise HTTPException(status_code=404, detail='会话不存在')
        if include_subcontract:
            from ..plus.blockchain_supervision.models import BsTask, BsMaterial
            from ..plus.blockchain_supervision import service, reference_snapshots
            tasks = db.scalars(select(BsTask).where(BsTask.conversation_id == row.id).order_by(BsTask.id).with_for_update()).all()
            for task in tasks:
                snapshot = reference_snapshots.get_reference_snapshot(db, task) or {}
                processing = db.scalar(select(BsMaterial.id).where(
                    BsMaterial.task_id == task.id, BsMaterial.parse_state.in_(('uploading', 'parsing'))).limit(1))
                active_reference_run = any(
                    (run or {}).get('state') in ('running', 'preparing')
                    for run in (snapshot.get('runs') or [])
                )
                if (task.state == 'preparing' or processing is not None
                        or active_reference_run or service.current_ai_advisory_pending(db, task)):
                    raise HTTPException(
                        status_code=409,
                        detail='任务正在处理：材料解析、AI 或核查运行仍未完成，请等待后再删除',
                    )
        row.is_deleted = True
        db.commit()
        return Response(status_code=204)

    return app
