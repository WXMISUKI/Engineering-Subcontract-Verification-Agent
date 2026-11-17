"""读取质量复核：只解除已核实页的疑点，并可写回核对转写。"""
from app.plus.blockchain_supervision import parsing, service
from app.plus.blockchain_supervision.models import BsMaterial, BsTask


def _material(db):
    task = BsTask(id=9, conversation_id=101, title='读取质量复核', state='running', created_by=1,
                  created_by_type='tester', scale='standard', scope_a1=True, scope_a3=True)
    db.add(task)
    db.flush()
    material = BsMaterial(
        task_id=task.id, file_name='m.pdf', parse_state='done', path='m.pdf',
        pages_json=[{'page': 1, 'text': '正常正文'}, {'page': 2, 'text': '1. 2. 3.'},
                    {'page': 3, 'text': ''}],
        degradations_json=[{'kind': 'parse_incomplete', 'total_pages': 3, 'read_pages': [1],
                            'unread_pages': [2, 3], 'ocr_suspect_pages': [2]}])
    db.add(material)
    db.commit()
    return task, material


def test_review_clears_only_requested_page_and_keeps_transcript(db_session):
    task, material = _material(db_session)
    result = service.record_reading_quality_review(
        db_session, task, material.id, [2], reviewer='user:1', note='按页图核对',
        transcripts={2: '于文祥 身份证 371329199912221514 交安C证 鄂交安C25G01400'})

    assert result['pages'] == [2]
    assert result['transcript_pages'] == [2]
    db_session.expire_all()
    material = db_session.get(BsMaterial, material.id)
    coverage = parsing.reading_coverage(material)
    assert 2 in coverage['read_pages']
    assert 2 not in coverage['suspect_pages']
    assert 3 in coverage['unread_pages']
    assert next(page for page in material.pages_json if page['page'] == 2)['text'].startswith('于文祥')
    assert any(record.get('kind') == 'reading_quality_review' and record.get('pages') == [2]
               for record in material.degradations_json)
    events = [event.event_type for event in task_events(db_session, task.id)]
    assert 'material.reading_quality_reviewed' in events


def task_events(db, task_id):
    from app.plus.blockchain_supervision.models import BsTaskEvent
    return db.query(BsTaskEvent).filter_by(task_id=task_id).all()
