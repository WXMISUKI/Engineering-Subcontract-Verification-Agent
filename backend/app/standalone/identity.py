"""独立应用账号和会话；调用方负责选择隔离数据库并初始化表。"""

import bcrypt
from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, select
from sqlalchemy.orm import DeclarativeBase, Session
from ..plus.blockchain_supervision.manifest import manifest


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = 'standalone_users'
    id = Column(Integer, primary_key=True)
    username = Column(String(100), nullable=False, unique=True, index=True)
    password_hash = Column(String(100), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    token_version = Column(Integer, nullable=False, default=0)

    @property
    def user_type(self):
        return 'web'


class Agent(Base):
    __tablename__ = 'agents'
    id = Column(Integer, primary_key=True)
    slug = Column(String(100), nullable=False, unique=True)


class ConversationMapping(Base):
    __tablename__ = 'conversation_mappings'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('standalone_users.id'), nullable=False, index=True)
    user_type = Column(String(20), nullable=False, default='web')
    agent_id = Column(Integer, ForeignKey('agents.id'), nullable=False)
    is_deleted = Column(Boolean, nullable=False, default=False)


def create_account(engine, username: str, password: str) -> None:
    username = username.strip()
    raw = password.encode('utf-8')
    if not username or len(username) > 100 or len(password) < 12 or len(raw) > 72:
        raise ValueError('账号或密码不符合要求')
    password_hash = bcrypt.hashpw(raw, bcrypt.gensalt()).decode('ascii')
    with Session(engine) as session:
        if not session.scalar(select(Agent).where(Agent.slug == manifest['code'])):
            session.add(Agent(slug=manifest['code']))
        session.add(User(username=username, password_hash=password_hash))
        session.commit()
