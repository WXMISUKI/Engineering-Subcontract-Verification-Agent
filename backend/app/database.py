"""独立应用的共享 ORM 基类；引擎由部署入口显式提供。"""
from sqlalchemy.orm import sessionmaker

from .standalone.identity import Base

SessionLocal = sessionmaker(autocommit=False, autoflush=False)


def get_db():
    with SessionLocal() as db:
        yield db
