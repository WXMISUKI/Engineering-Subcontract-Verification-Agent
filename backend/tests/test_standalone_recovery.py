from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.standalone.identity import Base, Agent, ConversationMapping, User
from app.standalone.main import create_app
from app.plus.blockchain_supervision.models import BsMaterial, BsTask, BsTaskEvent


def make_engine():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([User(id=1, username='synthetic', password_hash='unused'),
                    Agent(id=1, slug='blockchain-supervision')])
        db.flush()
        db.add(ConversationMapping(id=1, user_id=1, user_type='web', agent_id=1))
        db.flush()
        db.add(BsTask(id=1, conversation_id=1, title='synthetic', state='preparing',
                      created_by=1, created_by_type='web'))
        db.flush()
        db.add_all([BsMaterial(id=1, task_id=1, file_name='interrupted.pdf', parse_state='parsing'),
                    BsMaterial(id=2, task_id=1, file_name='ready.pdf', parse_state='done')])
        db.commit()
    return engine


def test_startup_recovers_interrupted_work_once_while_lock_is_held(monkeypatch, tmp_path):
    from app.standalone import runtime
    held = []

    @contextmanager
    def lock(path):
        held.append('acquired')
        yield
        held.append('released')

    monkeypatch.setattr(runtime, 'exclusive_instance', lock)
    engine = make_engine()
    app = create_app(engine, 'x' * 64, runtime_lock_dir=tmp_path)
    for _ in range(2):
        with TestClient(app):
            assert held[-1] == 'acquired'
            with Session(engine) as db:
                assert db.get(BsTask, 1).state == 'setup'
                assert db.get(BsMaterial, 1).parse_state == 'failed'
                assert db.get(BsMaterial, 2).parse_state == 'done'
                assert db.query(BsTaskEvent).filter_by(event_type='material.parse_failed').count() == 1
                assert db.query(BsTaskEvent).filter_by(event_type='zip.preparation_recovered').count() == 1
        assert held[-1] == 'released'


def test_lock_failure_prevents_recovery(monkeypatch, tmp_path):
    from app.standalone import runtime

    @contextmanager
    def locked(path):
        raise RuntimeError('another instance owns the lock')
        yield

    monkeypatch.setattr(runtime, 'exclusive_instance', locked)
    engine = make_engine()
    with pytest.raises(RuntimeError, match='another instance'):
        with TestClient(create_app(engine, 'x' * 64, runtime_lock_dir=tmp_path)):
            pass
    with Session(engine) as db:
        assert db.get(BsTask, 1).state == 'preparing'
        assert db.get(BsMaterial, 1).parse_state == 'parsing'


def test_startup_marks_interrupted_supplement_candidate_failed(monkeypatch, tmp_path):
    from app.standalone import runtime

    @contextmanager
    def lock(path):
        yield

    monkeypatch.setattr(runtime, 'exclusive_instance', lock)
    engine = make_engine()
    with Session(engine) as db:
        task = db.get(BsTask, 1)
        task.options_snapshot_json = {
            'supplement_plan': {'status': 'running', 'generation_id': 'restart-g1'},
            'supplement_edit': {'generation_id': 'restart-g1', 'previous_version_no': 4},
            'supplement_publication': {
                'state': 'candidate', 'baseline_version_no': 4,
                'candidate_generation_id': 'restart-g1',
            },
        }
        db.commit()

    with TestClient(create_app(engine, 'x' * 64, runtime_lock_dir=tmp_path)):
        pass

    with Session(engine) as db:
        task = db.get(BsTask, 1)
        publication = task.options_snapshot_json['supplement_publication']
        assert publication['state'] == 'failed'
        assert publication['baseline_version_no'] == 4
        assert publication['candidate_generation_id'] == 'restart-g1'
        assert publication['last_failure']['reason'] == 'service_restart'
        assert db.query(BsTaskEvent).filter_by(event_type='task.supplement_failed').count() == 1
