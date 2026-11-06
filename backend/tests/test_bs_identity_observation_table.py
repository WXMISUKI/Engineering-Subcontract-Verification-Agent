from types import SimpleNamespace

from app.plus.blockchain_supervision.identity_observation import run_identity_observations


def test_table_identity_and_certificate_disagreement_is_a_locatable_review_clue():
    material = SimpleNamespace(
        id=1, role='packet', parse_state='done', file_name='报审.pdf', degradations_json=[],
        pages_json=[
            {'page': 19, 'text': '| 姓名 | 张三 |\n| 身份证号码 | 110101199001010011 |\n居民身份证'},
            {'page': 20, 'text': '安全生产考核合格证书\n姓名：张三\n身份证号：110101199001010022'},
        ],
    )
    findings = run_identity_observations(SimpleNamespace(id=1), [material])
    assert len(findings) == 1 and findings[0].status == 'undecidable'
    assert {ref['page'] for ref in findings[0].evidence_json} == {19, 20}
    assert all(ref['location_mode'] == 'page' for ref in findings[0].evidence_json)
    assert '张三' in findings[0].message and '19' in findings[0].message and '20' in findings[0].message
    assert '110101199001010011' not in findings[0].message


def test_blank_table_name_cannot_borrow_next_row():
    material = SimpleNamespace(
        id=1, role='packet', parse_state='done', file_name='报审.pdf', degradations_json=[],
        pages_json=[
            {'page': 19, 'text': '| 姓名 |  |\n| 地址 | 张三 |\n| 身份证号码 | 110101199001010011 |\n居民身份证'},
            {'page': 20, 'text': '安全生产考核合格证书\n姓名：张三\n身份证号：110101199001010022'},
        ],
    )
    assert run_identity_observations(SimpleNamespace(id=1), [material]) == []
