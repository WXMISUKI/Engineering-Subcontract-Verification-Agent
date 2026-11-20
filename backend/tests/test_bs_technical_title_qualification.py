"""技术负责人职称与分包规模：正式合成回归，不访问业务库或外部服务。"""
from types import SimpleNamespace

import pytest

from app.plus.blockchain_supervision import a2_check

ID_A = '110101199001010011'


def material(text, mid, *, subject=3, role='packet'):
    return SimpleNamespace(id=mid, task_id=1, subject_id=subject, role=role,
        file_name='人员附件.pdf', parse_state='done', degradations_json=[],
        pages_json=[{'page': 1, 'text': text}])


def inputs(title='高级工程师', *, scope='一级公路路基工程，路基长度6km', number=ID_A, subject=3,
           doc='职称证书', cert='Z123'):
    return [material('专业分包合同\n工程内容：'+scope+'\n签约合同价（含税）：1000万元', 1, role='sub'),
        material('主要管理人员名单\n| 姓名 | 职务 | 身份证号 | 职称 |\n'
                 f'| 张三 | 技术负责人 | {ID_A} | 高级工程师 |', 2),
        material(f'{doc}\n姓名：张三\n身份证号：{number}\n职称：{title}\n证书编号：{cert}',
                 3, subject=subject)]


def run(sources):
    return a2_check.run_technical_title_check(SimpleNamespace(id=1), sources)


@pytest.mark.parametrize('title,scope,status', [
    ('高级工程师', '一级公路路基工程，路基长度6km', 'miss'),
    ('工程师', '一级公路路基工程，路基长度6km', 'hit'),
    ('助理工程师', '一级公路路基工程，路基长度6km', 'hit'),
    ('工程师', '一级公路，本次仅承担路基工程，路基长度3km', 'miss'),
    ('助理工程师', '一级公路，本次仅承担路基工程，路基长度3km', 'hit'),
    ('助理工程师', '二级公路，本次仅承担路基工程，路基长度3km', 'miss'),
])
def test_grade_matches_confirmed_subcontract_scale(title, scope, status):
    row, = run(inputs(title, scope=scope))
    assert row.item_id == 'A2-14' and row.kind == 'personnel_adequacy' and row.status == status
    assert '职称' in row.message and 'PDF物理第15页' in row.source
    assert {ref['material_id'] for ref in row.evidence_json} == {1, 2, 3}
    if status == 'hit':
        assert '已提交' in row.message and '违法' not in row.message


@pytest.mark.parametrize('change', ['roster_only', 'name_only', 'other_subject', 'unread',
    'unknown_title', 'wrong_document', 'conflict', 'missing_number', 'scale_pending'])
def test_evidence_gaps_do_not_become_proven_qualification(change):
    sources = inputs()
    if change == 'roster_only':
        sources.pop()
    elif change == 'name_only':
        sources[2].pages_json[0]['text'] = sources[2].pages_json[0]['text'].replace(ID_A, '110101********0011')
    elif change == 'other_subject':
        sources[2].subject_id = 4
    elif change == 'unread':
        sources[2].degradations_json = [{'kind': 'parse_incomplete', 'unread_pages': [1]}]
    elif change == 'unknown_title':
        sources = inputs('高级经济师')
    elif change == 'wrong_document':
        sources = inputs(doc='建造师注册证书')
    elif change == 'conflict':
        sources[2].pages_json[0]['text'] += '\n职称：助理工程师'
    elif change == 'missing_number':
        sources = inputs(cert='待填')
    elif change == 'scale_pending':
        sources = inputs(scope='桥梁工程施工')
    row, = run(sources)
    assert row.status == 'undecidable' and '需' in row.message


def test_same_certificate_number_grade_conflict_cannot_use_highest():
    sources = inputs('助理工程师')
    sources.append(material('职称证书\n姓名：张三\n身份证号：'+ID_A+
        '\n职称：高级工程师\n证书编号：Z123', 4))
    row, = run(sources)
    assert row.status == 'undecidable' and '冲突' in row.message


def test_conflicting_fields_on_one_copy_cannot_be_hidden_by_another_copy():
    sources = inputs()
    sources[2].pages_json[0]['text'] += '\n职称：助理工程师'
    sources.append(material('职称证书\n姓名：张三\n身份证号：'+ID_A+
        '\n职称：高级工程师\n证书编号：Z123', 4))
    row, = run(sources)
    assert row.status == 'undecidable' and '冲突' in row.message


def test_independent_higher_title_can_cover_scale():
    sources = inputs('助理工程师')
    sources.append(material('职称证书\n姓名：张三\n身份证号：'+ID_A+
        '\n职称：高级工程师\n证书编号：Z999', 4))
    row, = run(sources)
    assert row.status == 'miss'


def test_labor_contract_does_not_invent_professional_technical_lead_requirement():
    sources = inputs()
    sources[0].role = 'labor'
    sources[0].pages_json[0]['text'] = sources[0].pages_json[0]['text'].replace('专业分包', '劳务分包')
    assert run(sources) == []


@pytest.mark.parametrize('text,role', [
    ('专业分包合同与劳务分包合同', 'sub'),
    ('专业分包合同', 'labor'),
])
def test_contract_type_conflict_is_not_overridden_by_material_role(text, role):
    sources = inputs()
    sources[0].role = role
    sources[0].pages_json[0]['text'] = sources[0].pages_json[0]['text'].replace('专业分包合同', text)
    row, = run(sources)
    assert row.status == 'undecidable' and '分包类型未唯一识别' in row.message


def test_phone_optional_roster_stays_in_explicit_attachment_table():
    from app.plus.blockchain_supervision.personnel_evidence import extract_management_roster
    sources = inputs()
    assert len(extract_management_roster([sources[1]])) == 1
    table = sources[1].pages_json[0]['text']
    for text, role in [(table.replace('主要管理人员名单\n', ''), 'packet'), (table, 'sub'),
                       (table.replace('主要管理人员名单\n', '主要管理人员名单\n营业执照\n'), 'packet')]:
        assert extract_management_roster([material(text, 5, role=role)]) == []


def _seed_service(db):
    from app.plus.blockchain_supervision import service
    task = service.create_task(db, conversation_id=101, title='合成职称链路',
                               scope_a1=False, scope_a3=True, created_by=1)
    subject = service.create_subject(db, task, name='甲建设有限公司', subject_type='professional')
    sources = []
    for source in inputs('工程师'):
        row = service.add_material(db, task, file_name=source.file_name,
                                   role=source.role, subject_id=subject.id)
        row.pages_json, row.parse_state = source.pages_json, 'done'
        sources.append(row)
    db.commit()
    return task, subject, sources


def test_real_extraction_service_rerun_report_and_history(db_session):
    import copy
    from app.plus.blockchain_supervision import service, reporting, report_template_v14
    from app.plus.blockchain_supervision.models import BsFinding, BsReviewVersion
    db = db_session
    task, subject, sources = _seed_service(db)
    for _ in range(2):
        row, = [r for r in service.run_personnel_staffing(db, task) if r.item_id == 'A2-14']
        assert row.status == 'hit' and row.subject_id == subject.id
        assert {ref['material_id'] for ref in row.evidence_json} == {r.id for r in sources}
        assert db.query(BsFinding).filter_by(task_id=task.id, item_id='A2-14').count() == 1
    report = reporting.build_report(db, task)
    raw, = [r for r in report_template_v14._raw_items(report) if r['item_id'] == 'A2-14']
    assert raw['status'] == 'hit' and '已提交' in raw['message'] and '职称' in raw['message']
    document = report_template_v14.build_document(report)
    title_row, = [row for sheet in document['attachments'] for row in sheet.get('auto_rows', [])
                  if row[0] == '技术负责人职称与规模']
    assert title_row[1] == '不符合' and '已提交' in title_row[2]
    assert '等级未覆盖' in title_row[2]
    assert '违法' not in title_row[2]
    assert '职称' in report_template_v14._condition_for_item(raw)
    frozen = {'report': copy.deepcopy(report)}
    version = BsReviewVersion(task_id=task.id, version_no=1, trigger='synthetic', snapshot_json=frozen)
    db.add(version)
    db.commit()
    sources[2].pages_json = [{'page': 1, 'text': sources[2].pages_json[0]['text'].replace(
        '职称：工程师', '职称：高级工程师')}]
    db.commit()
    row, = [r for r in service.run_personnel_staffing(db, task) if r.item_id == 'A2-14']
    assert row.status == 'miss'
    db.expire(version)
    assert version.snapshot_json == frozen


@pytest.mark.parametrize('change', ['unconfirmed', 'other_subject', 'unassigned'])
def test_service_keeps_subject_boundary(db_session, change):
    from app.plus.blockchain_supervision import service
    task, subject, sources = _seed_service(db_session)
    sources[2].pages_json = inputs()[2].pages_json
    if change == 'unconfirmed':
        subject.type_confirmed = False
    elif change == 'other_subject':
        other = service.create_subject(db_session, task, name='乙建设有限公司', subject_type='professional')
        sources[2].subject_id = other.id
    else:
        sources[2].subject_id = None
    db_session.commit()
    rows = [r for r in service.run_personnel_staffing(db_session, task) if r.item_id == 'A2-14']
    assert rows and all(r.status == 'undecidable' for r in rows)
    assert not any(ref.get('material_id') == sources[2].id for row in rows
                   if row.subject_id == subject.id for ref in row.evidence_json or [])


def test_v14_roster_facts_append_without_rewriting_legacy_batch(db_session):
    import json
    from app.plus.blockchain_supervision import material_facts as mf
    from app.plus.blockchain_supervision.models import BsMaterialFact
    task, _, sources = _seed_service(db_session)
    roster = sources[1]
    legacy = BsMaterialFact(task_id=task.id, material_id=roster.id, field_path='personnel.records',
        raw_value='[]', normalized_value='[]', page=1, quote='历史未识别名单', quality='readable',
        source_kind='embedded_text_layer', parse_batch_id='legacy-v13', schema_version='material-facts.v1',
        extractor_version='material-facts.v13')
    db_session.add(legacy)
    db_session.commit()
    rows = mf.persist_material_facts(db_session, roster)
    db_session.commit()
    current, = [r for r in rows if r.field_path == 'personnel.records']
    assert current.extractor_version == mf.EXTRACTOR_VERSION
    assert json.loads(current.raw_value)[0]['name'] == '张三'
    db_session.expire(legacy)
    assert legacy.raw_value == '[]' and legacy.extractor_version == 'material-facts.v13'
    assert mf.persist_material_facts(db_session, roster) == []
