"""分包认证及知识引用需要的最小兼容模型。"""
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, JSON, Numeric, String, Text, UniqueConstraint, func, text
from ..database import Base
from ..standalone.identity import Agent, ConversationMapping


class KnowledgeProvider(Base):
    __tablename__ = 'knowledge_providers'
    __table_args__ = (UniqueConstraint('name', name='uq_knowledge_provider_name'),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    provider_type = Column(String(32), nullable=False, default='weknora')
    base_url = Column(String(500), nullable=False)
    tenant_ref = Column(String(100), nullable=True)
    secret_ref = Column(String(255), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    status = Column(String(32), nullable=False, default='unknown')
    last_check_at = Column(DateTime, nullable=True)
    last_check_result = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class KnowledgeBaseBinding(Base):
    __tablename__ = 'knowledge_base_bindings'
    __table_args__ = (
        UniqueConstraint('agent_id', 'provider_id', 'external_kb_id', name='uq_agent_knowledge_binding'),
        Index('ix_knowledge_binding_agent_enabled', 'agent_id', 'is_enabled'),
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, ForeignKey('agents.id', ondelete='CASCADE'), nullable=False)
    provider_id = Column(Integer, ForeignKey('knowledge_providers.id', ondelete='CASCADE'), nullable=False)
    external_kb_id = Column(String(100), nullable=False)
    display_name = Column(String(200), nullable=False, default='')
    purpose = Column(Text, nullable=True)
    is_enabled = Column(Boolean, nullable=False, default=True)
    priority = Column(Integer, nullable=False, default=100)
    retrieval_weight = Column(Float, nullable=False, default=1.0)
    allow_query = Column(Boolean, nullable=False, default=True)
    allow_ingest = Column(Boolean, nullable=False, default=False)
    allow_manage = Column(Boolean, nullable=False, default=False)
    selected_knowledge_ids = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default='unknown')
    last_verified_at = Column(DateTime, nullable=True)
    last_verified_status = Column(String(32), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class KnowledgeRef(Base):
    __tablename__ = 'knowledge_ref'
    __table_args__ = (Index('ix_knowledge_ref_owner_run', 'owner_type', 'owner_key', 'run_kind', 'ref_index'),)
    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_type = Column(String(32), nullable=False)
    owner_key = Column(String(128), nullable=False)
    run_kind = Column(String(64), nullable=False)
    ref_index = Column(Integer, nullable=False)
    provider_id = Column(Integer, nullable=True)
    kb_id = Column(String(100), nullable=True)
    knowledge_id = Column(String(100), nullable=True)
    doc_title = Column(String(500), nullable=True)
    chunk_index = Column(Integer, nullable=True)
    snippet = Column(Text, nullable=True)
    score = Column(Float, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class TokenUsage(Base):
    """统一模型用量台账；None 表示未知，0 表示已知零。"""
    __tablename__ = 'token_usage'
    __table_args__ = (
        Index('idx_token_usage_user_date', 'user_id', 'created_at'),
        Index('idx_token_usage_status', 'status'),
        Index('idx_token_usage_billing_status', 'billing_status'),
        Index('idx_token_usage_unit_type', 'unit_type'),
        Index('idx_token_usage_scope', 'billing_scope_type', 'billing_scope_id'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    user_type = Column(String(20), default='web', nullable=False, index=True)
    conversation_id = Column(Integer, ForeignKey('conversation_mappings.id', ondelete='SET NULL'), nullable=True)
    chat_id = Column(String(100), nullable=True)
    dedupe_key = Column(String(120), nullable=True, unique=True)
    input_count = Column(Integer, nullable=True)
    output_count = Column(Integer, nullable=True)
    token_count = Column(Integer, nullable=True)
    source = Column(String(32), default='chat', nullable=False)
    stage = Column(String(64), nullable=True)
    as_session_id = Column(String(36), nullable=True)
    unit_type = Column(String(16), nullable=False, default='token', server_default=text("'token'"))
    unit_scale = Column(Integer, nullable=False, default=1, server_default=text('1'))
    billable_quantity = Column(Numeric(18, 3), nullable=True)
    billable = Column(Boolean, nullable=False, default=True, server_default=text('1'))
    status = Column(String(16), nullable=False, default='completed', server_default=text("'completed'"))
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    model_code = Column(String(64), nullable=True)
    provider_code = Column(String(32), nullable=True)
    cache_hit_input_count = Column(Integer, nullable=True)
    cache_miss_input_count = Column(Integer, nullable=True)
    cache_write_input_count = Column(Integer, nullable=True)
    cache_reported = Column(Boolean, nullable=True)
    usage_status = Column(String(16), nullable=True)
    raw_usage_json = Column(JSON, nullable=True)
    request_id = Column(String(64), nullable=True)
    billing_status = Column(String(16), nullable=True)
    billing_scene = Column(String(32), nullable=True)
    agent_id = Column(Integer, nullable=True)
    billing_scope_type = Column(String(32), nullable=True)
    billing_scope_id = Column(String(64), nullable=True)
    created_at = Column(DateTime, server_default=func.now())


# Register billing-domain tables with the standalone metadata when installed.
from . import billing_models as _billing_models  # noqa: E402,F401
