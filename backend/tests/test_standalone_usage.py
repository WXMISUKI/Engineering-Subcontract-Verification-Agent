from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.standalone.identity import Base
from app.services.token_service import TokenUsage, record
from app.services.billing.bindings import effective_model_code_or_default
from app.services.billing.model_context import current_model_code, use_model


def test_usage_is_persisted_without_platform_billing(monkeypatch):
    monkeypatch.setenv('LLM_MODEL', 'configured')
    monkeypatch.setenv('SECRET_KEY', 'synthetic-test-secret-key-0123456789')
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        assert effective_model_code_or_default(db, 'bs_task', 1) == 'configured'
        with use_model('configured'):
            assert current_model_code() == 'configured'
            record(db=db, user_id=1, user_type='web', conversation_id=2,
                   input_count=9, output_count=0, token_count=9,
                   raw_usage={'prompt_tokens': 9}, model_code='configured',
                   source='subcontract', stage='review', scope_type='bs_task', scope_id='1')
            db.commit()
        assert current_model_code() is None
        row = db.scalar(select(TokenUsage))
        assert row.raw_usage_json == {'prompt_tokens': 9}
        assert row.billing_scope_id == '1'
