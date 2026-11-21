from copy import deepcopy
from datetime import date
from types import SimpleNamespace

from app.plus.blockchain_supervision import retrospective_knowledge as knowledge
from app.plus.blockchain_supervision.retrospective_sources import freeze_basis, freeze_material


def test_native_reference_retrieval_receives_contract_scope_and_excludes_admission(monkeypatch):
    sources = [freeze_material(SimpleNamespace(id=1, file_name='分包.pdf', role='sub', pages=1,
        parse_state='done', degradations_json=[], pages_json=[{'page': 1, 'text':
            '4. 分包工程承包范围和内容：预制立柱、盖梁、箱梁、板梁等构件安装。专业分包。'}]))]
    template = {'doc_id': 'MOT-NL-2024', 'clause_id': 'MOT-NL-2024#一、（三）6',
                'kb_id': 'authorized-kb', 'knowledge_id': 'registered-file', 'original_sha256': 'a' * 64,
                'item_id': 'NL-B-08', 'negative_parts': True}
    before = deepcopy(template)
    monkeypatch.setattr(knowledge.knowledge_runs, 'load_reference_package', lambda **kw: {'sources': [template]})
    def native(db, task, run, **kwargs):
        assert kwargs['as_of'] == date(2023, 12, 22)
        planned = run['basis_sources']
        assert all(row['doc_id'] == 'MOT-NL-2024' for row in planned)
        leaf = next(row for row in planned if row['clause_id'].endswith('（三）6'))
        assert leaf['negative_parts'] is True and leaf['original_sha256'] == 'a' * 64
        assert '箱梁' in leaf['query_text']
        return {'status': 'ok', 'references': [{'clause_id': leaf['clause_id'], 'quote': '整孔预制安装的预制梁'}],
                'queries': [{'status': 'hit'}], 'authorized_scope': [{'kb_id': 'authorized-kb'}]}
    monkeypatch.setattr(knowledge.knowledge_runs, 'retrieve_reference_run', native)
    result = knowledge.retrieve(None, SimpleNamespace(baseline_date=date(2026, 11, 19)),
                                sources, freeze_basis(), contract_date='2023-12-22',
                                baseline_date='2023-12-22')
    assert result['status'] == 'ok' and result['references']
    assert template == before


def test_missing_mapping_is_explicit_local_mode_without_network(monkeypatch):
    def unavailable(**kw):
        raise ValueError('mapping unavailable')
    monkeypatch.setattr(knowledge.knowledge_runs, 'load_reference_package', unavailable)
    result = knowledge.retrieve(None, SimpleNamespace(baseline_date=None), [], [])
    assert result['status'] == 'local_only'
    assert result['references'] == [] and '不按当前日期筛选' in result['limitations'][0]


def test_old_contract_keeps_shanghai_online_comparison_and_temporal_notice(monkeypatch):
    from app.plus.blockchain_supervision.rules import shanghai_comparison_notice

    as_of = date(2023, 12, 22)
    selected_ids = {'SHJT-2026-8#第十六条（一）', 'SHJT-2026-8#第十二条'}
    basis = [row for row in freeze_basis() if row['clause_id'] in selected_ids]
    assert len(basis) == 2
    notice = shanghai_comparison_notice(basis[0]['document'], as_of)
    assert notice
    template = {'doc_id': 'SHJT-2026-8', 'clause_id': 'SHJT-2026-8#第十六条（一）',
                'kb_id': 'authorized-kb', 'knowledge_id': 'registered-file',
                'original_sha256': 'a' * 64, 'temporal_notice': notice}

    def load_package(**kwargs):
        assert kwargs == {'as_of': as_of, 'compare_shanghai': True}
        return {'sources': [template]}

    def native(db, task, run, **kwargs):
        assert kwargs['as_of'] == as_of
        assert {row['clause_id'] for row in run['basis_sources']} == selected_ids
        refs = []
        for row in run['basis_sources']:
            assert row['temporal_notice'] == notice
            doc, clause = knowledge.knowledge_runs._clause(row, as_of=as_of)
            assert doc['doc_id'] == template['doc_id']
            refs.append({'clause_id': clause['clause_id'], 'quote': clause['text'],
                         'physical_page': row['physical_page']})
        verified = [ref for ref in refs if ref['clause_id'] == template['clause_id']]
        return {'status': 'partial', 'references': verified,
                'queries': [{'clause_id': clause_id, 'status': 'hit'} for clause_id in sorted(selected_ids)],
                'authorized_scope': []}

    monkeypatch.setattr(knowledge.knowledge_runs, 'load_reference_package', load_package)
    monkeypatch.setattr(knowledge.knowledge_runs, 'retrieve_reference_run', native)
    result = knowledge.retrieve(None, SimpleNamespace(baseline_date=None), [], basis, baseline_date=as_of)
    assert result['status'] == 'partial'
    [ref] = result['references']
    assert ref['clause_id'] == template['clause_id'] and ref['physical_page'] == 9
    assert ref['temporal_notice'] == notice
    assert {row['clause_id']: row['reference_verified'] for row in result['queries']} == {
        clause_id: clause_id == ref['clause_id'] for clause_id in selected_ids}
    assert '包含 2 条' in result['limitations'][0] and '其中 1 条' in result['limitations'][0]
