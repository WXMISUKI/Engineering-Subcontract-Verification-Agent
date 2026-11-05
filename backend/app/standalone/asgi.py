"""显式配置的独立 ASGI 入口；不自动建表或创建账号。"""
import os

from sqlalchemy import create_engine

from .main import create_app


database_url = os.environ['DATABASE_URL']
secret_key = os.environ['SECRET_KEY']
cookie_setting = os.environ.get('COOKIE_SECURE', 'true').lower()
if cookie_setting not in ('true', 'false'):
    raise ValueError('COOKIE_SECURE must be true or false')
engine = create_engine(database_url, pool_pre_ping=True)
app = create_app(engine, secret_key, cookie_secure=cookie_setting == 'true',
                 runtime_lock_dir='/app/app/uploads')
