from copy import deepcopy

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from types import SimpleNamespace

from app.plus.blockchain_supervision import negative_candidate_plan as plan
from app.plus.blockchain_supervision import service
from app.plus.blockchain_supervision import ai_advisory_snapshots as snapshots
from app.plus.blockchain_supervision import reporting
from app.plus.blockchain_supervision.models import BsFinding, BsTask
from app.database import Base


def inputs():
    candidate = {
        'candidate_id': 'nc-test', 'task_id': 1, 'run_id': 'run-1', 'review_run_id': 'review-1',
        'material_fingerprint': 'materials-1', 'rule_fingerprint': 'rules-1',
        'subject_id': 7, 'item_id': 'A1-01', 'contract_ids': [8],
        'reference_ids': ['ref-1'], 'material_pages': [],
        'unavailable_reason': 'contract_scope_unlocated', 'incomplete_material_scope': True,
    }
    result = {'candidates': [{**candidate, 'execution_status': 'not_run', 'batches_total': 0,
                              'batches_completed': 0, 'proposals': []}]}
    run = {'task_id': 1, 'run_id': 'run-1', 'review_run_id': 'review-1',
           'material_fingerprint': 'materials-1', 'rule_fingerprint': 'rules-1',
           'references': [{'reference_id': 'ref-1', 'quote': '经授权的规则原文'}]}
    return {'candidates': [candidate], 'items': []}, result, run


def test_scope_unlocated_becomes_auditable_undecidable_without_invented_evidence():
    prepared, result, run = inputs()
    records = plan.make_unlocated_scope_findings(task_id=1, prepared=prepared, result=result, run=run)
    assert len(records) == 1
    key, finding, rule_refs = records[0]
    assert key.startswith('ncf-')
    assert (finding.task_id, finding.subject_id, finding.item_id) == (1, 7, 'A1-01')
    assert (finding.kind, finding.status) == ('negative_candidate', 'undecidable')
    assert not finding.material_id and not finding.page and not finding.quote and not finding.evidence_json
    assert '分包范围' in finding.message and '人工核实' in finding.message
    assert reporting.report_review_blocking(finding)
    assert rule_refs == [{'reference_id': 'ref-1', 'quote': '经授权的规则原文'}]
    assert plan.make_unlocated_scope_findings(task_id=1, prepared=prepared, result=result, run=run)[0][0] == key


@pytest.mark.parametrize('change', ['identity', 'missing_reference', 'model_failed'])
def test_unlocated_scope_never_accepts_tampered_identity_or_missing_authority(change):
    prepared, result, run = inputs()
    if change == 'identity':
        result['candidates'][0]['subject_id'] = 99
    elif change == 'missing_reference':
        run['references'] = []
    else:
        result['candidates'][0]['execution_status'] = 'failed'
    assert plan.make_unlocated_scope_findings(task_id=1, prepared=prepared, result=result, run=run) == []


def test_other_unavailable_reasons_are_not_auto_reviewable():
    prepared, result, run = inputs()
    prepared = deepcopy(prepared)
    prepared['candidates'][0]['unavailable_reason'] = 'reference_missing'
    result['candidates'][0]['unavailable_reason'] = 'reference_missing'
    assert plan.make_unlocated_scope_findings(task_id=1, prepared=prepared, result=result, run=run) == []


def test_scope_records_are_persisted_idempotently_and_preserve_human_receipt():
    prepared, result, run = inputs()
    unlocated = plan.make_unlocated_scope_findings(task_id=1, prepared=prepared, result=result, run=run)
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        task = SimpleNamespace(id=1)
        records = service.sync_candidate_review_records(db, task, [], [], [], unlocated=unlocated)
        db.commit()
        first = db.get(BsFinding, records[0]['finding_id'])
        first.review_json = {'decision': 'accepted', 'reviewer': 'tester'}
        db.commit()
        again = service.sync_candidate_review_records(db, task, [], records, [], unlocated=unlocated)
        db.commit()
        assert again == records
        assert db.get(BsFinding, records[0]['finding_id']).review_json['decision'] == 'accepted'
        assert db.query(BsFinding).count() == 1
        with pytest.raises(service.AiContextChanged):
            service.sync_candidate_review_records(db, task, [], records, [], unlocated=[])
        db.rollback()
        assert db.get(BsFinding, records[0]['finding_id']).review_json['decision'] == 'accepted'


def test_reviewed_candidate_projects_note_and_ignored_stays_out_of_report(monkeypatch):
    """验证候选复核落库后，完整报告投影保留采纳备注并排除忽略项。"""
    from app.plus.blockchain_supervision.report_document import build_report_document, report_display_sections
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        task = BsTask(id=1, conversation_id=1, title='合成测试', state='running',
                      created_by=1, created_by_type='web', scope_a1=True)
        finding = BsFinding(task_id=1, subject_id=7, kind='negative_candidate', item_id='A1-01',
                            status='undecidable', message='候选范围需人工核对', source='合成规则原文')
        db.add_all([task, finding])
        db.commit()
        # 本测试聚焦处置与报告投影；候选授权有效性的守卫由独立测试覆盖。
        monkeypatch.setattr(reporting.ai_advisory_snapshots, 'filter_current_candidate_findings',
                            lambda _db, _task, _user, findings: findings)
        service.review_finding(db, finding, reviewer='测试人员', decision='accepted', note='已联系核对')
        accepted = reporting.build_report(db, task)
        accepted_text = str(report_display_sections(build_report_document(accepted)))
        assert '已联系核对' in accepted_text
        assert '候选范围需人工核对' in accepted_text
        service.review_finding(db, finding, reviewer='测试人员', decision='ignored', note='忽略原因')
        ignored = reporting.build_report(db, task)
        ignored_text = str(report_display_sections(build_report_document(ignored)))
        assert '候选范围需人工核对' not in ignored_text
        assert '忽略原因' not in ignored_text
        assert db.get(BsFinding, finding.id).review_json['note'] == '忽略原因'


def test_only_valid_persisted_scope_candidate_is_reviewable_in_coverage():
    prepared, result, run = inputs()
    run.update({'group_id': 'a1', 'state': 'completed', 'finding_ids': [], 'basis_sources': []})
    event = SimpleNamespace(event_type='check.negative_candidates_finished', payload_json={
        'run_id': run['run_id'], 'validation_version': snapshots.ai_review_batch.CHECKPOINT_SCHEMA_VERSION,
        'result': result,
    })
    snapshot = {'ai_auto_enabled': True, 'ai_auto_groups': ['a1']}
    blocked = snapshots.summarize_coverage(snapshot, [run], [event])['groups'][0]
    assert blocked['counts']['not_run'] == 1 and blocked['reviewable_not_run'] == 0
    allowed = snapshots.summarize_coverage(snapshot, [run], [event],
                                           scope_reviewable_ids={'nc-test'})['groups'][0]
    assert allowed['counts']['not_run'] == 1 and allowed['reviewable_not_run'] == 1


def test_non_reviewable_contract_missing_is_exposed_as_material_scope_category():
    prepared, result, run = inputs()
    prepared['candidates'][0]['unavailable_reason'] = 'contract_missing'
    result['candidates'][0]['unavailable_reason'] = 'contract_missing'
    run.update({'group_id': 'a1', 'state': 'completed', 'finding_ids': [], 'basis_sources': []})
    event = SimpleNamespace(event_type='check.negative_candidates_finished', payload_json={
        'run_id': run['run_id'], 'validation_version': snapshots.ai_review_batch.CHECKPOINT_SCHEMA_VERSION,
        'result': result,
    })
    row = snapshots.summarize_coverage({'ai_auto_enabled': True, 'ai_auto_groups': ['a1']},
                                       [run], [event])['groups'][0]
    assert row['counts']['not_run'] == 1
    assert row['reviewable_not_run'] == 0
    assert row['failure_categories'] == ['incomplete_material_scope']


def test_scope_coverage_requires_matching_active_persisted_finding(monkeypatch):
    prepared, result, run = inputs()
    run['group_id'] = 'a1'
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        task = SimpleNamespace(id=1)
        entries = plan.make_unlocated_scope_findings(task_id=1, prepared=prepared, result=result, run=run)
        records = service.sync_candidate_review_records(db, task, [], [], [], unlocated=entries)
        db.commit()
        event = SimpleNamespace(event_type='check.negative_candidates_finished', payload_json={
            'run_id': run['run_id'], 'validation_version': snapshots.ai_review_batch.CHECKPOINT_SCHEMA_VERSION,
            'result': result, 'finding_records': records,
        })
        monkeypatch.setattr(snapshots, 'active_candidate_finding_ids', lambda *_: {records[0]['finding_id']})
        assert snapshots.unlocated_scope_reviewable_ids(db, task, None, run, [event]) == {'nc-test'}
        finding = db.get(BsFinding, records[0]['finding_id'])
        finding.subject_id = 99
        db.flush()
        assert snapshots.unlocated_scope_reviewable_ids(db, task, None, run, [event]) == set()
        finding.subject_id = 7
        db.flush()
        monkeypatch.setattr(snapshots, 'active_candidate_finding_ids', lambda *_: set())
        assert snapshots.unlocated_scope_reviewable_ids(db, task, None, run, [event]) == set()

def test_a2_05_supplement_unreadable_is_report_blocking_with_reason_label():
    from app.plus.blockchain_supervision.router import _finding_dto
    finding = BsFinding(
        task_id=1, subject_id=7, item_id="A2-05", kind="cert_validity",
        status="undecidable",
        message="项目经理李强无法确认：补件证书编号未识别，不能回退原证书判通过",
        evidence_json=[{
            "material_id": 2, "page": 1, "quote": "交安 B",
            "reason_code": "supplement_unreadable",
            "certificate_evidence": [
                {"material_id": 1, "page": 2, "role": "superseded"},
                {"material_id": 2, "page": 1, "role": "authoritative"},
            ],
        }],
    )
    assert reporting.report_review_blocking(finding)
    assert reporting.finding_reason_code(finding) == "supplement_unreadable"
    assert reporting.reason_code_label("supplement_unreadable") == "补件证书不可读或缺号"
    trace = reporting.decision_trace(finding)
    assert trace["reason_code"] == "supplement_unreadable"
    assert trace["reason_label"] == "补件证书不可读或缺号"
    dto = _finding_dto(finding)
    assert dto["reason_code"] == "supplement_unreadable"
    assert dto["reason_label"] == "补件证书不可读或缺号"
