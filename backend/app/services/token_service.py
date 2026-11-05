"""记录可用的模型用量，不推算或声称费用。"""
from sqlalchemy import JSON, Column, DateTime, Integer, String, func
from ..database import Base


class TokenUsage(Base):
    __tablename__ = 'standalone_token_usage'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=False, index=True)
    user_type = Column(String(20), nullable=False)
    conversation_id = Column(Integer, nullable=True)
    model_code = Column(String(160), nullable=True)
    source = Column(String(100), nullable=True)
    stage = Column(String(100), nullable=True)
    scope_type = Column(String(50), nullable=True)
    scope_id = Column(String(100), nullable=True)
    raw_usage = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)


def record(*, db, user_id, user_type, conversation_id=None, raw_usage=None,
           model_code=None, source=None, stage=None, scope_type=None, scope_id=None, **kwargs):
    row = TokenUsage(user_id=user_id, user_type=user_type, conversation_id=conversation_id,
                     raw_usage=raw_usage if isinstance(raw_usage, dict) else {}, model_code=model_code,
                     source=source, stage=stage, scope_type=scope_type, scope_id=scope_id)
    db.add(row)
    return row
