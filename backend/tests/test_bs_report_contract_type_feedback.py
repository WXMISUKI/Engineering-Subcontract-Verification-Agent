from types import SimpleNamespace
from io import BytesIO

import pytest
from docx import Document

from app.plus.blockchain_supervision import export, reporting, report_contract_type


@pytest.mark.parametrize(('name', 'text', 'expected'), [
    ('劳务分包合同.pdf', '', '劳务分包'),
    ('专业分包合同.pdf', '', '专业分包'),
    ('总包合同.pdf', '', '总包'),
    ('分包合同.pdf', '劳务分包合同', '劳务分包'),
])
def test_contract_type_prefers_specific_evidence(name, text, expected):
    result = report_contract_type.project_contract_type(SimpleNamespace(file_name=name, pages_json=[{'text': text}]))
    assert result['status'] == 'hit'
    assert result['value'] == expected


def test_contract_type_text_evidence_preserves_source_page():
    result = report_contract_type.project_contract_type(SimpleNamespace(
        file_name='合同扫描件-预制梁.pdf',
        pages_json=[
            {'page': 1, 'text': '预制板梁工程劳务分包'},
            {'page': 2, 'text': '双方就劳务承包事项协商达成一致'},
        ],
    ))
    assert result['status'] == 'hit'
    assert result['value'] == '劳务分包'
    assert result['page'] == 1


def test_contract_type_specific_text_overrides_generic_construction_contract():
    result = report_contract_type.project_contract_type(SimpleNamespace(
        file_name='合同扫描件-预制梁.pdf',
        pages_json=[
            {'page': 1, 'text': '预制板梁工程劳务分包'},
            {'page': 12, 'text': '施工合同通用条款'},
        ],
    ))
    assert result['status'] == 'hit'
    assert result['value'] == '劳务分包'
    assert result['page'] == 1


@pytest.mark.parametrize(('name', 'text'), [
    ('分包合同.pdf', ''),
    ('专业分包与劳务分包合同.pdf', ''),
    ('合同.pdf', '专业分包合同与劳务分包合同'),
    ('合同.pdf', ''),
])
def test_contract_type_ambiguous_evidence_is_undecidable(name, text):
    result = report_contract_type.project_contract_type(SimpleNamespace(file_name=name, pages_json=[{'text': text}]))
    assert result['status'] == 'undecidable'
    assert result['value'] is None
    assert result['missing']


def _opinion_with_contracts(monkeypatch, contracts, subjects=None):
    facts = []
    for role, filename in contracts:
        field = report_contract_type.project_contract_type(SimpleNamespace(file_name=filename))
        facts.append({'role': role, 'file_name': filename, 'fields': {'contract_type': field}})
    report = {'meta': {}, 'sections': [], 'contract_facts': facts,
              'summary': {'missing_by_category': []}, 'coverage': {'complete': False},
              'checklist_summary': {'subjects': subjects or []}, 'reference_snapshot': {'steps': []}}
    monkeypatch.setattr(reporting, 'build_report', lambda *_: report)
    return reporting.build_opinion(None, SimpleNamespace(baseline_date=None))


def test_labor_contract_type_reaches_opinion_and_markdown(monkeypatch):
    opinion = _opinion_with_contracts(monkeypatch, [('labor', '劳务分包合同.pdf')],
                                      [{'status': 'ready', 'subject_type': 'labor', 'name': '某公司'}])
    assert opinion['subject']['分包类型'] == '劳务分包'
    assert '劳务分包' in export.opinion_markdown(opinion, 'synthetic')
    document = Document(BytesIO(export.opinion_word_bytes(opinion, 'synthetic')))
    assert any('分包类型：劳务分包' in paragraph.text for paragraph in document.paragraphs)


def test_multiple_contract_types_do_not_silently_select_professional(monkeypatch):
    opinion = _opinion_with_contracts(monkeypatch, [
        ('sub', '专业分包合同.pdf'), ('labor', '劳务分包合同.pdf'),
    ])
    assert opinion['subject']['分包类型'] == '主体类型待核实'


def test_unidentified_contract_type_does_not_disappear_from_summary(monkeypatch):
    opinion = _opinion_with_contracts(monkeypatch, [
        ('labor', '劳务分包合同.pdf'), ('sub', '分包合同.pdf'),
    ])
    assert opinion['subject']['分包类型'] == '主体类型待核实'


def test_multiple_contracts_with_same_role_preserve_type_conflict(monkeypatch):
    opinion = _opinion_with_contracts(monkeypatch, [
        ('sub', '专业分包合同.pdf'), ('sub', '劳务分包合同.pdf'),
    ])
    assert opinion['subject']['分包类型'] == '主体类型待核实'


def test_confirmed_labor_subject_and_contract_disagreement_is_visible(monkeypatch):
    opinion = _opinion_with_contracts(monkeypatch, [('sub', '专业分包合同.pdf')],
                                      [{'status': 'ready', 'subject_type': 'labor', 'name': '某公司'}])
    assert opinion['subject']['分包类型'] == '劳务分包（合同类型不一致，需核实）'
    assert '劳务分包（合同类型不一致，需核实）' in export.opinion_markdown(opinion, 'synthetic')


def test_unconfirmed_subject_type_is_not_derived_from_contract(monkeypatch):
    opinion = _opinion_with_contracts(monkeypatch, [('labor', '劳务分包合同.pdf')],
                                      [{'status': 'type_pending', 'subject_type': 'labor'}])
    assert opinion['subject']['分包类型'] == '主体类型待核实'


def test_confirmed_labor_subject_with_multiple_contract_types_requires_review(monkeypatch):
    opinion = _opinion_with_contracts(monkeypatch, [
        ('sub', '专业分包合同.pdf'), ('labor', '劳务分包合同.pdf'),
    ], [{'status': 'ready', 'subject_type': 'labor', 'name': '某公司'}])
    assert opinion['subject']['分包类型'] == '劳务分包（合同类型需分别核实）'


def test_report_contract_projection_preserves_rule_extractor():
    from app.plus.blockchain_supervision import extract
    material = SimpleNamespace(id=1, file_name='劳务分包合同.pdf', role='labor', pages_json=[])
    original = extract.extract_contract_fields(material)['fields']['contract_type']
    fields = reporting.contract_facts_for_materials([material])[0]['fields']
    assert original['value'] == '专业分包'
    assert fields['contract_type']['value'] == '劳务分包'
