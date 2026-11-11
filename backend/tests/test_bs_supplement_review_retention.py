"""补件重核只保留未受影响的问题处置；旧意见保存在补件前快照。"""
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.standalone.identity import Base
from app.plus.blockchain_supervision.models import BsFinding, BsSubject, BsTask
from app.plus.blockchain_supervision.service import _replace_findings
from app.plus.blockchain_supervision.reporting import review_dto, human_disposition


def test_scoped_recheck_clears_affected_review_but_preserves_other_review():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        task = BsTask(id=1, conversation_id=1, title='test', created_by=1, created_by_type='web',
                      options_snapshot_json={'rerun': {'scope': 'items', 'item_ids': ['A2-05'], 'carry': []}})
        db.add(task)
        db.add_all([
            BsFinding(task_id=1, item_id='A2-05', kind='certificate', status='undecidable',
                      message='待核对', review_json={'decision': 'accepted'}),
            BsFinding(task_id=1, item_id='A2-06', kind='certificate', status='undecidable',
                      message='另一个待核对项', review_json={'decision': 'ignored', 'note': '测试意见'}),
        ])
        db.commit()
        new = BsFinding(task_id=1, item_id='A2-05', kind='certificate', status='undecidable', message='待核对')
        _replace_findings(db, task, [new])
        result = {row.item_id: row for row in db.query(BsFinding).filter_by(task_id=1).all()}
        assert result['A2-05'].review_json is None
        assert result['A2-06'].review_json == {'decision': 'ignored', 'note': '测试意见'}


def test_changed_conclusion_invalidates_old_review_and_keeps_history():
    """结论变化后旧意见只能留痕，不能继续作为当前处置。"""
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        task = BsTask(id=2, conversation_id=2, title='changed', created_by=1, created_by_type='web',
                      options_snapshot_json={'rerun': {'scope': 'items', 'item_ids': ['A2-05'], 'carry': [{
                          'subject_id': 7,
                          'item_id': 'A2-05',
                          'material_id': 10,
                          'page': 3,
                          'status': 'undecidable',
                          'message': '旧结论',
                          'quote': '旧原文',
                          'source': '旧依据',
                          'evidence_json': [{'material_id': 10, 'page': 3, 'quote': '旧原文'}],
                          'review_json': {'reviewer': '张三', 'decision': 'accepted', 'note': '旧意见'},
                      }]}})
        db.add(task)
        db.add(BsSubject(id=7, task_id=2, stable_code='subject-7', name='主体7', subject_type='labor'))
        db.commit()
        new = BsFinding(task_id=2, subject_id=7, item_id='A2-05', material_id=10, page=3,
                        kind='certificate', status='hit', message='新结论', quote='新原文',
                        source='新依据', evidence_json=[{'material_id': 10, 'page': 3, 'quote': '新原文'}])
        _replace_findings(db, task, [new])
        row = db.query(BsFinding).filter_by(task_id=2, item_id='A2-05').one()
        assert row.review_json and row.review_json.get('invalidated')
        assert row.review_json['invalidated']['reason'] == 'conclusion_changed'
        assert row.review_json['invalidated']['previous']['decision'] == 'accepted'
        assert review_dto(row.review_json)['decision'] is None
        assert review_dto(row.review_json)['invalidated']['previous']['note'] == '旧意见'
        assert human_disposition(row.review_json)['code'] == 'unprocessed'
