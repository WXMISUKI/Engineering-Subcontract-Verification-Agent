from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.plus.blockchain_supervision import page_text_candidates, parsing, reference_snapshots, service
from app.plus.blockchain_supervision.models import BsMaterial, BsPageTextCandidate, BsTask


@pytest.fixture(autouse=True)
def _secret_key(monkeypatch):
    # 候选消费序列化需要签名密钥；测试必须显式提供，不能依赖外部环境。
    monkeypatch.setenv('SECRET_KEY', 'synthetic-test-key-01234567890123456789')


def make_session():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    return engine


def seed_task(db, *, unread=(1, 2)):
    task = BsTask(id=1, conversation_id=1, title='synthetic', state='running',
                  scope_a1=True, created_by=1, created_by_type='web',
                  options_snapshot_json={reference_snapshots.REVIEW_POINTER: 'review-1'})
    db.add(task)
    db.flush()
    material = BsMaterial(
        id=5, task_id=1, file_name='证书.pdf', role='packet', parse_state='done', pages=2,
        pages_json=[{'page': 1, 'text': '第一页'}, {'page': 2, 'text': '第二页'}],
        degradations_json=[{'kind': 'parse_incomplete', 'unread_pages': list(unread)}],
    )
    db.add(material)
    db.commit()
    return task, material


def seed_candidate(db, page_no, *, status='pending_review', version=1, text=None, material_id=5):
    row = BsPageTextCandidate(
        id=f'candidate-{page_no}', task_id=1, material_id=material_id, page_no=page_no,
        status=status, candidate_version=version, review_run_id='review-1',
        source_sha256='a' * 64, input_material_fingerprint='material-fp',
        input_rule_fingerprint='rule-fp', candidate_text=text or f'机器候选{page_no}',
        candidate_meta_json={'quality_suspect': False, 'page': {'page': page_no,
                                                                'text': text or f'机器候选{page_no}'}},
    )
    db.add(row)
    db.commit()
    return row


def test_candidate_lifecycle_hides_text_until_pending_review():
    engine = make_session()
    with Session(engine) as db:
        seed_task(db)
        row = seed_candidate(db, 1, status='requested')
        assert 'candidate_text' not in page_text_candidates.candidate_payload(row)

        claimed = page_text_candidates.claim_candidate(db, candidate_id=row.id)
        assert claimed['status'] == 'preparing'
        assert page_text_candidates.store_pending_candidate(
            db, candidate_id=row.id, lease_token=claimed['lease_token'],
            text='可核对正文', meta={'page': {'page': 1}}, signature='sig') is True
        payload = page_text_candidates.candidate_payload(
            page_text_candidates.get_candidate(db, candidate_id=row.id, task_id=1), include_text=True)
        assert payload['status'] == 'pending_review'
        assert payload['candidate_text'] == '可核对正文'


def test_confirm_requires_current_version_and_pending_status():
    engine = make_session()
    with Session(engine) as db:
        task, _ = seed_task(db)
        seed_candidate(db, 1)
        with pytest.raises(ValueError, match='版本'):
            service.confirm_page_text_candidates(
                db, task, [{'candidate_id': 'candidate-1', 'expected_candidate_version': 2}],
                operator=SimpleNamespace(id=1, user_type='web'))
        db.rollback()
        row = db.get(BsPageTextCandidate, 'candidate-1')
        assert row.status == 'pending_review'


def test_batch_confirm_marks_confirmed_and_launches_single_consumption(monkeypatch):
    engine = make_session()
    launched = []
    monkeypatch.setattr(service, '_launch_page_text_candidate_consume',
                        lambda task_id, ids, bind: launched.append((task_id, tuple(ids))))
    with Session(engine) as db:
        task, _ = seed_task(db)
        seed_candidate(db, 1)
        seed_candidate(db, 2)
        result = service.confirm_page_text_candidates(
            db, task,
            [{'candidate_id': 'candidate-1', 'expected_candidate_version': 1,
              'corrected_text': '人工校对一页'},
             {'candidate_id': 'candidate-2', 'expected_candidate_version': 1}],
            operator=SimpleNamespace(id=1, user_type='web'))
        assert result['accepted'] is True
        rows = db.query(BsPageTextCandidate).order_by(BsPageTextCandidate.page_no).all()
        assert [row.status for row in rows] == ['confirmed', 'confirmed']
        assert rows[0].reviewed_text == '人工校对一页'
        assert rows[1].candidate_text == '机器候选2'
    assert launched == [(1, ('candidate-1', 'candidate-2'))]


def test_consumption_uses_reviewed_text_and_marks_consumed(monkeypatch):
    engine = make_session()
    monkeypatch.setattr(service, 'rerun_task', lambda *a, **k: {'ok': True, 'superseded_version_no': 9})
    with Session(engine) as db:
        seed_task(db)
        first = seed_candidate(db, 1)
        second = seed_candidate(db, 2)
        first.status = second.status = 'confirmed'
        first.reviewed_text = '人工校对后的正文'
        db.commit()
        payload = service._page_candidate_rerun_payload(
            db.get(BsTask, 1), [first, second])
        assert payload['pages'][0]['text'] == '人工校对后的正文'
        assert payload['pages'][1]['text'] == '机器候选2'
        assert payload['signature']

        service._run_page_text_candidate_consume(1, ['candidate-1', 'candidate-2'], engine)
        db.expire_all()
        rows = db.query(BsPageTextCandidate).all()
        assert [row.status for row in rows] == ['consumed', 'consumed']
        assert [row.rerun_version_no for row in rows] == [9, 9]


def test_consumption_failure_invalidates_candidates(monkeypatch):
    engine = make_session()
    monkeypatch.setattr(service, 'rerun_task', lambda *a, **k: {
        'ok': False, 'detail': '任务输入或核查代已变化'})
    with Session(engine) as db:
        seed_task(db)
        row = seed_candidate(db, 1)
        row.status = 'confirmed'
        db.commit()
        service._run_page_text_candidate_consume(1, ['candidate-1'], engine)
        db.expire_all()
        refreshed = db.get(BsPageTextCandidate, 'candidate-1')
        assert refreshed.status == 'invalidated'
        assert '已变化' in refreshed.reason


def test_start_registers_candidate_and_launches_preparation(monkeypatch):
    engine = make_session()
    launched = []
    monkeypatch.setattr(service, '_page_text_candidate_context', lambda *a: {
        'task_id': 1, 'material_id': 5, 'page_no': 1, 'review_run_id': 'review-1',
        'source_sha256': 'b' * 64, 'input_material_fingerprint': 'material-fp',
        'input_rule_fingerprint': 'rule-fp'})
    monkeypatch.setattr(service, '_launch_page_text_candidate_preparation',
                        lambda *a: launched.append(a))
    with Session(engine) as db:
        task, _ = seed_task(db)
        result = service.start_page_text_candidate_preparation(db, task, 5, 1)
        assert result['accepted'] is True
        assert result['candidate']['status'] == 'preparing'
    assert len(launched) == 1
    assert launched[0][:3] == (1, 5, 1)


def test_expire_candidates_only_touches_expired_active_rows():
    engine = make_session()
    with Session(engine) as db:
        seed_task(db)
        seed_candidate(db, 1)
        seed_candidate(db, 2)
        db.get(BsPageTextCandidate, 'candidate-1').expires_at = page_text_candidates.utc_naive_now()
        db.get(BsPageTextCandidate, 'candidate-2').expires_at = None
        db.commit()
        assert page_text_candidates.expire_candidates(db) == 1
        assert db.get(BsPageTextCandidate, 'candidate-1').status == 'expired'
        assert db.get(BsPageTextCandidate, 'candidate-2').status == 'pending_review'
