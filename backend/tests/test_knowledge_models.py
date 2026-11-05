from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.standalone.identity import Base, Agent
from app.models.models import KnowledgeProvider, KnowledgeBaseBinding, KnowledgeRef


def test_binding_and_reference_keep_new_environment_ids():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        agent = Agent(slug='test')
        provider = KnowledgeProvider(name='isolated', base_url='http://localhost', secret_ref='env://TEST')
        db.add_all([agent, provider])
        db.flush()
        db.add(KnowledgeBaseBinding(agent_id=agent.id, provider_id=provider.id, external_kb_id='new-kb', display_name='test'))
        db.add(KnowledgeRef(owner_type='sr_conversation', owner_key='1', run_kind='test', ref_index=1, kb_id='new-kb', knowledge_id='new-doc'))
        db.commit()
        assert db.query(KnowledgeBaseBinding).one().external_kb_id == 'new-kb'
        assert db.query(KnowledgeRef).one().knowledge_id == 'new-doc'
