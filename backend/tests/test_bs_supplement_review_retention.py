"""补件重核只保留未受影响的问题处置；旧意见保存在补件前快照。"""
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.standalone.identity import Base
from app.plus.blockchain_supervision.models import BsFinding, BsTask
from app.plus.blockchain_supervision.service import _replace_findings


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
