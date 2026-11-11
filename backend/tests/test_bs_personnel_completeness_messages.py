from types import SimpleNamespace

from app.plus.blockchain_supervision.a3_check import run_completeness_check
from app.plus.blockchain_supervision.rules import load_report_checklist
from app.plus.blockchain_supervision.models import BsFinding, BsMaterial, BsTask
from app.plus.blockchain_supervision import pinning, reporting, service
from app.plus.blockchain_supervision.router import _finding_dto


def test_qualification_missing_requires_readable_packet_without_certificate_clues():
    for item_id in ('RC-A02', 'RC-A03', 'RC-A04'):
        item = next(item for item in load_report_checklist() if item['id'] == item_id)
        material = SimpleNamespace(
            id=1, role='packet', file_name='报审.pdf', parse_state='done',
            pages_json=[{'page': 1, 'text': '专业分包报审表'}, {'page': 2, 'text': '工程量表'}],
            degradations_json=[])
        task = SimpleNamespace(id=1)
        assert run_completeness_check(task, [material], items=[item])[0].status == 'hit'
        material.degradations_json = [{'kind': 'parse_incomplete', 'ocr_suspect_pages': [2]}]
        assert run_completeness_check(task, [material], items=[item])[0].status == 'undecidable'
        material.degradations_json = []
        material.pages_json[1]['text'] = '证照目录：' + item['keywords'][0] + '（请核对原件）'
        assert run_completeness_check(task, [material], items=[item])[0].status == 'undecidable'


def test_personnel_uncertainty_does_not_list_unrelated_unread_pages():
    item = next(item for item in load_report_checklist() if item['id'] == 'RC-A07')
    material = SimpleNamespace(
        id=1, role='packet', file_name='报审.pdf', parse_state='done',
        pages_json=[{'page': 1, 'text': '专业分包报审表'},
                    {'page': 2, 'text': '工程量表'},
                    {'page': 16, 'text': '企业负责人：身份证、交安A证'}],
        degradations_json=[{'kind': 'parse_incomplete', 'ocr_suspect_pages': [2]}],
    )
    finding = run_completeness_check(SimpleNamespace(id=1), [material], items=[item])[0]
    assert finding.status == 'undecidable'
    assert '企业负责人' in finding.message and '身份证' in finding.message and '交安A证' in finding.message
    assert '第2页' not in finding.message and '识别质量' not in finding.message


def test_enterprise_head_identity_disagreement_identifies_pages_and_person():
    item = next(item for item in load_report_checklist() if item['id'] == 'RC-A07')
    material = SimpleNamespace(
        id=1, role='packet', file_name='报审.pdf', parse_state='done', degradations_json=[],
        pages_json=[
            {'page': 1, 'text': '专业分包报审表'},
            {'page': 16, 'text': '企业负责人：身份证、交安A证\n关于张三同志任职的通知\n经决定：张三同志任示范公司总经理。'},
            {'page': 19, 'text': '| 姓名 | 张三 |\n| 身份证号码 | 110101199001010011 |\n居民身份证'},
            {'page': 20, 'text': '安全生产考核合格证书\n姓名：张三\n身份证号：110101199001010022'},
        ],
    )
    finding = run_completeness_check(SimpleNamespace(id=1), [material], items=[item])[0]
    assert finding.status == 'undecidable'
    assert all(part in finding.message for part in ['张三', '19', '20', '身份证号码'])
    assert {ref['page'] for ref in finding.evidence_json} == {16, 19, 20}
    assert '110101199001010011' not in finding.message


def test_evidence_survives_persistence_api_and_draft_report(db_session):
    item = next(item for item in load_report_checklist() if item['id'] == 'RC-A07')
    task = BsTask(conversation_id=101, title='隔离测试', created_by=1, scope_a3=True)
    db_session.add(task)
    db_session.flush()
    material = BsMaterial(task_id=task.id, role='packet', file_name='报审.pdf', parse_state='done',
                          pages_json=[
                              {'page': 1, 'text': '专业分包报审表'},
                              {'page': 16, 'text': '企业负责人：身份证、交安A证\n关于张三同志任职的通知\n张三同志任示范公司总经理。'},
                              {'page': 19, 'text': '| 姓名 | 张三 |\n| 身份证号码 | 110101199001010011 |\n居民身份证'},
                              {'page': 20, 'text': '安全生产考核合格证书\n姓名：张三\n身份证号：110101199001010022'},
                          ], degradations_json=[])
    db_session.add(material)
    db_session.flush()
    findings = run_completeness_check(task, [material], items=[item])
    pinning.pin_findings(findings, [material])
    service._replace_findings(db_session, task, findings)
    db_session.expire_all()
    saved = db_session.query(BsFinding).filter_by(task_id=task.id, item_id='RC-A07').one()
    assert {ref['page'] for ref in _finding_dto(saved)['evidence']} == {16, 19, 20}
    report = reporting.build_report(db_session, task)
    row = next(row for section in report['sections'] for row in section['items'] if row['item_id'] == 'RC-A07')
    assert {ref['page'] for ref in row['evidence_list']} == {16, 19, 20}


def test_unknown_certificate_clues_identify_the_unique_roster_person():
    item = next(item for item in load_report_checklist() if item['id'] == 'RC-P12')
    material = SimpleNamespace(
        id=1, role='packet', file_name='报审.pdf', parse_state='done', subject_id=1,
        pages_json=[{'page': 1, 'text': '专业分包报审表'},
                    {'page': 5, 'text': '主要管理人员名单\n| 序号 | 姓名 | 职务 | 电话 |\n'
                                      '| --- | --- | --- | --- |\n| 1 | 张三 | 项目总工 | 13800000001 |'},
                    {'page': 8, 'text': '其他材料'}],
        degradations_json=[{'kind': 'parse_incomplete', 'ocr_suspect_pages': [8]}],
    )
    finding = run_completeness_check(SimpleNamespace(id=1), [material], items=[item])[0]
    assert finding.status == 'undecidable'
    assert '张三' in finding.message and '项目总工' in finding.message
    assert '身份证' in finding.message


def test_enterprise_identity_clue_does_not_guess_role_without_appointment():
    item = next(item for item in load_report_checklist() if item['id'] == 'RC-A07')
    material = SimpleNamespace(
        id=1, role='packet', file_name='报审.pdf', parse_state='done', degradations_json=[],
        pages_json=[{'page': 1, 'text': '专业分包报审表'},
                    {'page': 16, 'text': '企业负责人：身份证、交安A证'},
                    {'page': 19, 'text': '| 姓名 | 张三 |\n| 身份证号码 | 110101199001010011 |\n居民身份证'},
                    {'page': 20, 'text': '安全生产考核合格证书\n姓名：张三\n身份证号：110101199001010022'}],
    )
    finding = run_completeness_check(SimpleNamespace(id=1), [material], items=[item])[0]
    assert finding.status == 'undecidable'
    assert '张三' not in finding.message


def test_suspect_identity_page_is_presented_for_manual_review():
    item = next(item for item in load_report_checklist() if item['id'] == 'RC-A07')
    material = SimpleNamespace(
        id=1, role='packet', file_name='报审.pdf', parse_state='done',
        degradations_json=[{'kind': 'parse_incomplete', 'ocr_suspect_pages': [19]}],
        pages_json=[{'page': 1, 'text': '专业分包报审表'},
                    {'page': 16, 'text': '企业负责人：身份证、交安A证\n关于张三同志任职的通知\n张三同志任示范公司总经理。'},
                    {'page': 19, 'text': '| 姓名 | 张三 |\n| 身份证号码 | 110101199001010011 |\n居民身份证'}],
    )
    finding = run_completeness_check(SimpleNamespace(id=1), [material], items=[item])[0]
    assert finding.status == 'undecidable'
    assert all(value in finding.message for value in ('张三', '19', '身份证'))
    assert {ref['page'] for ref in finding.evidence_json} == {16, 19}
    assert '遮挡' not in finding.message and '模糊' not in finding.message


def test_certificate_clue_excludes_unrelated_unread_pages():
    item = next(item for item in load_report_checklist() if item['id'] == 'RC-A02')
    material = SimpleNamespace(
        id=1, role='packet', file_name='报审.pdf', parse_state='done',
        degradations_json=[{'kind': 'parse_incomplete', 'ocr_suspect_pages': [2, 3]}],
        pages_json=[{'page': 1, 'text': '专业分包报审表'},
                    {'page': 2, 'text': '工程量表'},
                    {'page': 3, 'text': '营业执照\n统一社会信用代码\n登记机关'}],
    )
    finding = run_completeness_check(SimpleNamespace(id=1), [material], items=[item])[0]
    assert finding.status == 'undecidable' and finding.page == 3
    assert '第3页' in finding.message and '第2' not in finding.message
    assert '识别质量' not in finding.message
