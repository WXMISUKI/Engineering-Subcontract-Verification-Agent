"""分包认证及知识引用需要的最小兼容模型。"""
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
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
