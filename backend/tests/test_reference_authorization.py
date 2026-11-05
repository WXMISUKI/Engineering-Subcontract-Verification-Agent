from types import SimpleNamespace
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.standalone.identity import Base, create_account, Agent, ConversationMapping
from app.models.models import KnowledgeBaseBinding, KnowledgeProvider, KnowledgeRef
from app.services.knowledge.reference_access import authorized_reference_provider


def test_original_access_requires_owner_and_current_file_binding():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    create_account(engine, 'alice', 'synthetic-pass-123')
    create_account(engine, 'bob', 'synthetic-pass-456')
    with Session(engine) as db:
        agent = db.query(Agent).one()
        conversation = ConversationMapping(user_id=1, user_type='web', agent_id=agent.id)
        provider = KnowledgeProvider(name='isolated', base_url='http://localhost', secret_ref='env://KEY')
        db.add_all([conversation, provider])
        db.flush()
        binding = KnowledgeBaseBinding(agent_id=agent.id, provider_id=provider.id, external_kb_id='kb',
                                       selected_knowledge_ids='["doc"]')
        ref = KnowledgeRef(owner_type='sr_conversation', owner_key=str(conversation.id), run_kind='x',
                           ref_index=1, provider_id=provider.id, kb_id='kb', knowledge_id='doc')
        db.add_all([binding, ref])
        db.commit()
        assert authorized_reference_provider(db, ref, SimpleNamespace(id=1, user_type='web')) is not None
        assert authorized_reference_provider(db, ref, SimpleNamespace(id=2, user_type='web')) is None
        binding.selected_knowledge_ids = 'corrupt'
        db.flush()
        assert authorized_reference_provider(db, ref, SimpleNamespace(id=1, user_type='web')) is None
