import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.plus.blockchain_supervision import ai_guarded_review as guarded
from app.plus.blockchain_supervision import service
from app.plus.blockchain_supervision.ai_review_batch import plan_review_batches
from app.plus.blockchain_supervision.models import BsFinding, BsSubject, BsTask


def _part(finding_id, status='completed'):
    return {'finding_id': finding_id, 'execution_status': status, 'proposals': [],
            'limitations': [], 'assessment_incomplete': False}


def _setup(monkeypatch):
    task = SimpleNamespace(id=1)
    findings = {i: SimpleNamespace(id=i, task_id=1, item_id=f'item-{i}', kind='a1', subject_id=2)
                for i in (10, 11)}
    run = {'run_id': 'run-1', 'finding_ids': [10, 11]}
    context = {'materials': [{'task_id': 1, 'subject_id': 2, 'material_id': 3, 'page': 1, 'text': 'source'}],
               'references': [{'reference_id': 'r1', 'text': 'rule'}], 'limitations': []}

    class Query:
        def filter(self, *_args):
            return self
        def all(self):
            return []

    class FakeDB:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            pass
        def get(self, model, key):
            return task if model is BsTask and key == 1 else findings.get(key) if model is BsFinding else None
        def query(self, model):
            assert model is BsSubject
            return Query()
        def commit(self):
            pass

    monkeypatch.setattr(guarded.knowledge_runs, 'get_reference_run', lambda *_args, **_kwargs: run)
    monkeypatch.setattr(guarded, 'assert_ai_context_current', lambda *_args, **_kwargs: 'unchanged')
    monkeypatch.setattr(guarded, '_lock_save_inputs', lambda *_args: task)
    monkeypatch.setattr(guarded, 'build_review_context', lambda **_kwargs: context)
    items = [{'finding_id': i, 'task_id': 1, 'item_id': f'item-{i}', 'kind': 'a1', 'subject_id': 2, **context}
             for i in (10, 11)]
    batch = plan_review_batches(items)['batches'][0]
    key = hashlib.sha256(json.dumps({'namespace': 'model-v1', 'batch': batch}, ensure_ascii=False,
                                     sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
    return FakeDB, batch, key


def _run(factory, **kwargs):
    return asyncio.run(guarded.run_guarded_batch_review(
        session_factory=factory, task_id=1, group_id='a1', finding_ids=[11],
        planning_finding_ids=[10, 11], user=SimpleNamespace(id=1), client=object(),
        material_loader=lambda *_: [], checkpoint_namespace='model-v1', **kwargs))


def test_resume_uses_original_checkpoint_without_replacing_completed_finding(monkeypatch):
    factory, batch, key = _setup(monkeypatch)
    saved, progress = [], []
    async def must_not_call(*_args, **_kwargs):
        raise AssertionError('matching checkpoint must avoid model call')
    monkeypatch.setattr(guarded, '_execute_with_timeout_recovery', must_not_call)
    output = _run(factory, load_checkpoint=lambda _run, actual_key: [_part(10), _part(11)] if actual_key == key else None,
                  save_result=lambda _db, _task, _run, results: saved.extend(results),
                  on_batch_progress=lambda *_args: progress.append(_args[-2]))
    assert [entry['finding_id'] for entry in batch['items']] == [10, 11]
    assert [row['finding_id'] for row in output] == [11]
    assert [row['finding_id'] for row in saved] == [11]
    assert output[0]['execution_status'] == 'completed'
    assert progress == ['reused']


def test_resume_missing_checkpoint_runs_original_batch_but_saves_only_retry_items(monkeypatch):
    factory, batch, _key = _setup(monkeypatch)
    calls, saved, checkpoints = [], [], []
    async def fake_model(actual_batch, **_kwargs):
        calls.append(actual_batch)
        return [_part(10), _part(11)]
    monkeypatch.setattr(guarded, '_execute_with_timeout_recovery', fake_model)
    output = _run(factory, load_checkpoint=lambda *_args: None,
                  save_checkpoint=lambda _db, _task, _run, _key, parts: checkpoints.extend(parts),
                  save_result=lambda _db, _task, _run, results: saved.extend(results))
    assert calls == [batch]
    assert [row['finding_id'] for row in checkpoints] == [10, 11]
    assert [row['finding_id'] for row in saved] == [11]
    assert output[0]['execution_status'] == 'completed'


def test_resume_rejects_incomplete_checkpoint_without_model_call(monkeypatch):
    factory, _batch, _key = _setup(monkeypatch)
    async def must_not_call(*_args, **_kwargs):
        raise AssertionError('invalid checkpoint must not cause automatic model call')
    monkeypatch.setattr(guarded, '_execute_with_timeout_recovery', must_not_call)
    with pytest.raises(guarded.AiContextChanged):
        _run(factory, load_checkpoint=lambda *_args: [_part(11)])


def test_resume_rejects_failed_checkpoint_without_model_call(monkeypatch):
    factory, _batch, _key = _setup(monkeypatch)
    async def must_not_call(*_args, **_kwargs):
        raise AssertionError('invalid checkpoint must not cause automatic model call')
    monkeypatch.setattr(guarded, '_execute_with_timeout_recovery', must_not_call)
    with pytest.raises(guarded.AiContextChanged):
        _run(factory, load_checkpoint=lambda *_args: [_part(10), _part(11, 'failed')])


def test_resume_rejects_context_change_before_result_is_saved(monkeypatch):
    factory, _batch, _key = _setup(monkeypatch)
    observed, saved = [], []
    def context(*_args, **_kwargs):
        observed.append(None)
        return 'changed' if len(observed) >= 3 else 'original'
    monkeypatch.setattr(guarded, 'assert_ai_context_current', context)
    with pytest.raises(guarded.AiContextChanged):
        _run(factory, load_checkpoint=lambda *_args: [_part(10), _part(11)],
             save_result=lambda *_args: saved.append(True))
    assert saved == []


def test_service_passes_original_ids_to_retry_and_preserves_completed_result(monkeypatch):
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    run = {'run_id': 'run-1', 'finding_ids': [10, 11]}
    cached_event = SimpleNamespace(event_type='check.ai_advisory_completed', payload_json={
        'finding_id': 10, 'model_code': 'model-v1', 'validation_version': guarded.CHECKPOINT_SCHEMA_VERSION,
        'execution_status': 'completed', 'proposals': [{'marker': 'saved'}]})
    seen, client_options = {}, []
    monkeypatch.setattr(service, 'effective_model_code_or_default', lambda *_args: 'model-v1')
    monkeypatch.setattr(service.knowledge_runs, 'get_reference_run', lambda *_args, **_kwargs: run)
    monkeypatch.setattr(service, 'assert_ai_context_current', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, '_latest_ai_advisory_event',
                        lambda _db, _task, _run, finding_id: cached_event if finding_id == 10 else None)
    monkeypatch.setattr(service, 'add_event', lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, 'ai_checkpoint_client_fingerprint', lambda _client: 'client-fingerprint')
    def make_client(**kwargs):
        client_options.append(kwargs)
        return object()
    monkeypatch.setattr(service, 'client_from_env', make_client)
    async def fake_guarded(**kwargs):
        seen.update(kwargs)
        return [_part(11)]
    monkeypatch.setattr(guarded, 'run_guarded_batch_review', fake_guarded)
    with Session(engine) as db:
        task = BsTask(id=1, conversation_id=1, title='synthetic', state='running', created_by=3,
                      created_by_type='web')
        db.add(task)
        db.commit()
        output = asyncio.run(service.run_linked_batch_ai_advisory(
            db, task, SimpleNamespace(id=3, user_type='web'), group_id='a1', finding_ids=[10, 11],
            resume_partial=True, expected_run_id='run-1'))
    assert seen['finding_ids'] == [11]
    assert seen['planning_finding_ids'] == [10, 11]
    assert seen['diagnostic_context']['model_code'] == 'model-v1'
    assert client_options == [{'model_code': 'model-v1', 'max_retries': 0}]
    assert output[0]['proposals'] == [{'marker': 'saved'}]
    assert output[1]['finding_id'] == 11
