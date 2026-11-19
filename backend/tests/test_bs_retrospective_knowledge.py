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
    def native(db, task, run):
        planned = run['basis_sources']
        assert all(row['doc_id'] == 'MOT-NL-2024' for row in planned)
        leaf = next(row for row in planned if row['clause_id'].endswith('（三）6'))
        assert leaf['negative_parts'] is True and leaf['original_sha256'] == 'a' * 64
        assert '箱梁' in leaf['query_text']
        return {'status': 'ok', 'references': [{'clause_id': leaf['clause_id'], 'quote': '整孔预制安装的预制梁'}],
                'queries': [{'status': 'hit'}], 'authorized_scope': [{'kb_id': 'authorized-kb'}]}
    monkeypatch.setattr(knowledge.knowledge_runs, 'retrieve_reference_run', native)
    result = knowledge.retrieve(None, SimpleNamespace(baseline_date=date(2026, 11, 19)),
                                sources, freeze_basis(), contract_date='2023-12-22')
    assert result['status'] == 'ok' and result['references']
    assert template == before


def test_missing_mapping_is_explicit_local_mode_without_network(monkeypatch):
    def unavailable(**kw):
        raise ValueError('mapping unavailable')
    monkeypatch.setattr(knowledge.knowledge_runs, 'load_reference_package', unavailable)
    result = knowledge.retrieve(None, SimpleNamespace(baseline_date=None), [], [])
    assert result['status'] == 'unavailable'
    assert result['references'] == [] and '本地规范' in result['limitations'][0]
