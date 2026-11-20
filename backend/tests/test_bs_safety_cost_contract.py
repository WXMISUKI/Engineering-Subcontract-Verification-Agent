"""财资136号合同单独约定维度；合成文本进入实际抽取与服务。"""
from types import SimpleNamespace

import pytest

from app.plus.blockchain_supervision import contract_responsibility as cr


def material(body, *, mid=1, subject=3, role='sub'):
    return SimpleNamespace(id=mid, task_id=1, subject_id=subject, role=role, file_name='合同.pdf',
        parse_state='done', degradations_json=[], pages_json=[{'page': 1, 'text':
            '专业分包合同\n甲方：甲公司\n乙方：乙公司\n'+body}])


def run(body):
    row, = cr.run_responsibility_check(SimpleNamespace(id=1), [material(body)], topic='safety_cost')
    return row


@pytest.mark.parametrize('body,status', [
    ('安全生产费单列15万元，税率9%', 'miss'),
    ('单独约定安全生产费150000元', 'miss'),
    ('安全生产费按建筑安装工程造价的1.5%单列，税率9%', 'miss'),
    ('安全生产费按分包合同额的1%单列', 'miss'),
    ('安全生产费不单列，包含在综合单价内', 'hit'),
    ('安全生产费不单独约定', 'hit'),
    ('分包单位须自行按1.5%提取安全生产费', 'hit'),
    ('安全生产费单列，分包单位不再重复提取', 'undecidable'),
    ('安全生产费单列；税率9%，合同价100万元', 'undecidable'),
    ('安全文明施工费单列15万元', 'undecidable'),
    ('安全生产费按1.5%单列', 'undecidable'),
    ('安全生产费单列0元', 'undecidable'),
    ('安全生产费暂定单列15万元', 'undecidable'),
    ('安全生产费单列15万元；安全生产费单列20万元', 'undecidable'),
    ('安全生产费单列15万元；安全生产费不单列', 'undecidable'),
    ('不单列安全生产费15万元', 'hit'),
    ('安全生产费单列15万元，分包单位无须重复提取安全生产费', 'miss'),
    ('安全生产费单列15万元，不得要求分包单位自行提取安全生产费', 'miss'),
    ('安全生产费单列15万元，分包单位须自行提取其他费用', 'miss'),
    ('安全生产费单列15万元左右', 'undecidable'),
])
def test_contract_dimension_and_source_boundaries(body, status):
    row = run(body)
    assert row.status == status and row.finding_semantics == 'machine_conclusion'
    assert '第十七、十八条' in row.source and '物理第12—13页' in row.source
    assert '实际支付及专款专用另核' in row.message and '分包不再重复提取' in row.message
    result = row.evidence_json[0]['safety_cost_assessment']
    assert result['statutory_rate_verified'] is False and result['performance_verified'] is False
    if status == 'undecidable':
        assert '需' in row.message


def test_tax_rate_and_contract_amount_are_not_assigned_to_cost():
    row = run('安全生产费单列15万元，税率9%，合同金额1000万元')
    result = row.evidence_json[0]['safety_cost_assessment']
    assert result['amounts_yuan'] == ['150000'] and result['rates'] == []
    assert row.evidence_json[0]['percentage_mentions'] == ['9%']


def test_single_contract_amount_does_not_prove_fee_rate_or_statutory_base():
    row = run('安全生产费按分包合同额的1%单列')
    assert row.status == 'miss' and '仅该合同约定维度符合' in row.message
    assert row.evidence_json[0]['safety_cost_assessment']['rates'] == [
        {'rate_percent': '1', 'base': '分包合同额'}]
    assert '低于' not in row.message


@pytest.mark.parametrize('change', ['unread', 'general', 'packet', 'annex'])
def test_document_boundary_and_reading_gate(change):
    source = material('安全生产费单列15万元')
    if change == 'unread':
        source.pages_json.append({'page': 2, 'text': ''})
        source.degradations_json = [{'kind': 'parse_incomplete', 'unread_pages': [2]}]
        row, = cr.run_responsibility_check(SimpleNamespace(id=1), [source], topic='safety_cost')
        assert row.status == 'undecidable' and '未确认页' in row.message
    else:
        if change == 'annex':
            source.pages_json[0]['text'] = source.pages_json[0]['text'].replace(
                '安全生产费单列', '普通条款\n银行回单\n安全生产费单列')
        else:
            source.role = change
        assert cr.run_responsibility_check(SimpleNamespace(id=1), [source], topic='safety_cost') == []


def test_service_rerun_report_history_and_fact_batches(db_session):
    import copy
    import json
    from app.plus.blockchain_supervision import service, reporting, material_facts as mf, report_template_v14 as v14
    from app.plus.blockchain_supervision.models import BsFinding, BsMaterialFact, BsReviewVersion
    db = db_session
    task = service.create_task(db, conversation_id=101, title='合成安全费约定', scope_a1=False, scope_a3=True, created_by=1)
    subject = service.create_subject(db, task, name='乙建设有限公司', subject_type='professional')
    source = service.add_material(db, task, file_name='分包合同.pdf', role='sub', subject_id=subject.id)
    source.pages_json, source.parse_state = material('安全生产费不单列').pages_json, 'done'
    old = BsMaterialFact(task_id=task.id, material_id=source.id, field_path='contract.responsibility_records',
        raw_value='historical-v14', normalized_value='historical-v14', page=1, quote='historical', quality='readable',
        source_kind='embedded_text_layer', parse_batch_id='legacy-v14', schema_version='material-facts.v1',
        extractor_version='material-facts.v14')
    db.add(old)
    db.commit()
    for _ in range(2):
        mf.persist_material_facts(db, source)
        db.commit()
        row, = [r for r in service.run_completeness(db, task) if r.item_id == 'RC-A10-cost']
        assert row.status == 'hit' and row.subject_id == subject.id
        assert row.finding_semantics == 'machine_conclusion'
        assert not reporting.is_explicit_material_missing(row)
        assert db.query(BsFinding).filter_by(task_id=task.id, item_id='RC-A10-cost').count() == 1
    current, = [r for r in mf.latest_material_facts(db, source.id) if r.field_path == 'contract.responsibility_records']
    assert current.extractor_version == 'material-facts.v15'
    assert json.loads(current.raw_value)[0]['safety_cost_terms'][0]['separate'] == 'no'
    report = reporting.build_report(db, task)
    raw, = [r for r in v14._raw_items(report) if r['item_id'] == 'RC-A10-cost']
    assert v14._verdict(raw) == '不符合'
    document = v14.build_document(report)
    row, = [r for sheet in document['attachments'] for r in sheet.get('auto_rows', []) if r[0] == '安全生产费单列']
    assert row[1] == '不符合' and '不单独约定' in row[2]
    assert '分包建筑安装工程费 1.5%' not in json.dumps(document, ensure_ascii=False)
    assert '不再重复提取' in v14._condition_for_item(raw)
    frozen = {'report': copy.deepcopy(report)}
    version = BsReviewVersion(task_id=task.id, version_no=1, trigger='synthetic', snapshot_json=frozen)
    db.add(version)
    db.commit()
    source.pages_json = material('安全生产费单列15万元').pages_json
    db.commit()
    service.initialize_reference_review(db, task)
    db.commit()
    row, = [r for r in service.run_completeness(db, task) if r.item_id == 'RC-A10-cost']
    assert row.status == 'miss'
    db.expire_all()
    assert version.snapshot_json == frozen and old.raw_value == 'historical-v14'
