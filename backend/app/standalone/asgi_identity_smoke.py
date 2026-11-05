"""仅供认证切片容器冒烟，不是交付入口。"""
import os
from sqlalchemy import create_engine
from .main import create_app

engine = create_engine(os.environ['DATABASE_URL'], pool_pre_ping=True)
app = create_app(engine, os.environ['SECRET_KEY'], cookie_secure=True, include_subcontract=False)
