import fitz
import pytest
from io import BytesIO
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.plus.blockchain_supervision import report_template_render
from app.plus.blockchain_supervision.retrospective_contracts import REPORT_DIMENSIONS, report_dimension_id
from app.plus.blockchain_supervision.retrospective_report import build_report
from app.plus.blockchain_supervision.retrospective_sources import original_citation


def _report():
    return build_report({"schema_version": "bs.contract-retrospective.v1", "identity": {"run_id": "r1"},
        "execution_status": "completed", "summary": "未发现可列示问题。", "findings": [],
        "limitations": [], "coverage": {"batches_total": 1, "batches_completed": 1, "batches": []},
        "scope_note": "范围说明"}, project_name="项目", sources=[])


def test_retrospective_export_uses_landscape_for_pdf_and_word():
    report = _report()
    pdf_data = report_template_render.pdf_bytes(report, "digest", "2026-11-19")
    with fitz.open(stream=pdf_data, filetype="pdf") as pdf:
        assert pdf[0].rect.width > pdf[0].rect.height
        assert '内容指纹' not in ''.join(page.get_text() for page in pdf)

    word_data = report_template_render.word_bytes(report, "digest", "2026-11-19")
    assert word_data[:2] == b"PK"
    document = Document(BytesIO(word_data))
    assert document.sections[0].page_width > document.sections[0].page_height
    assert len(document.tables[0].columns) == 8
    assert not any('内容指纹' in p.text for p in document.paragraphs)


def test_retrospective_exports_appendix_heading_before_detail_table():
    report = _report()
    pdf_data = report_template_render.pdf_bytes(report, "digest", "2026-11-19")
    with fitz.open(stream=pdf_data, filetype="pdf") as pdf:
        pdf_text = "\n".join(page.get_text() for page in pdf)
    assert pdf_text.index("附件一：合同问题与风险明细表") < pdf_text.index("合同文件")

    word_data = report_template_render.word_bytes(report, "digest", "2026-11-19")
    document = Document(BytesIO(word_data))
    body_text = []
    for element in document.element.body.iterchildren():
        if element.tag.endswith("}p"):
            body_text.append(Paragraph(element, document).text)
        elif element.tag.endswith("}tbl"):
            body_text.append("\n".join(cell.text for row in Table(element, document).rows for cell in row.cells))
    appendix_index = next(i for i, text in enumerate(body_text) if "附件一：合同问题与风险明细表" in text)
    table_index = next(i for i, text in enumerate(body_text) if "合同文件" in text)
    assert appendix_index < table_index


def test_retrospective_report_has_eight_stable_columns_and_widths():
    report = _report()
    table = next(block for block in report["template_document"]["blocks"] if block["type"] == "table")
    assert len(table["headers"]) == 8
    assert abs(sum(table["widths"]) - 1) < 1e-9


def test_report_exports_use_manual_baseline_separately_from_actual_signing_date():
    raw = _report()
    raw['identity'].update(contract_date='2023-12-22', baseline_date='2024-01-01', baseline_date_source='manual')
    report = build_report(raw, project_name='测试项目', sources=[])
    overview = report['template_document']['blocks'][1]['text']
    assert '合同签订日期：2023-12-22' in overview
    assert '人工选定的核查基准日为2024-01-01' in overview
    word = Document(BytesIO(report_template_render.word_bytes(report, 'digest', '2026-11-19')))
    assert any(overview in p.text for p in word.paragraphs)
    with fitz.open(stream=report_template_render.pdf_bytes(report, 'digest', '2026-11-19'), filetype='pdf') as pdf:
        assert overview in ''.join(''.join(page.get_text().split()) for page in pdf)


@pytest.mark.parametrize(('baseline', 'source', 'notice'), [
    ('2023-12-22', 'subcontract_signing_date', '核查基准日采用上述分包合同签订日'),
    (None, 'unknown', '核查基准日尚未确定'),
])
def test_overview_explains_signing_based_or_unknown_baseline(baseline, source, notice):
    raw = _report()
    raw['identity'].update(contract_date=baseline, baseline_date=baseline, baseline_date_source=source)
    report = build_report(raw, project_name='测试项目', sources=[])
    assert notice in report['template_document']['blocks'][1]['text']


def test_upstream_scope_names_unconfirmed_party_and_locates_counterpart():
    raw = _report()
    raw['upstream'] = {'state': 'partial',
        'relationship': {'state': 'uncertain', 'checks': [
            {'dimension': 'project', 'state': 'matched'},
            {'dimension': 'parties', 'state': 'uncertain', 'reason': '模型内部字段提示'},
            {'dimension': 'scope', 'state': 'matched'},
        ]},
        'facts': [{'field': 'employer', 'value': '沪上施工公司', 'evidence': [
            {'material_id': 2, 'page': 2, 'quote': '发包方：沪上施工公司。'},
        ]}]}
    sources = [
        {'material_id': 1, 'file_name': '总包合同.pdf', 'role': 'general', 'total_pages': 1,
         'pages': [{'page': 1, 'readable': True, 'text': '项目名称：测试工程。'}],
         'reading_coverage': {'reading_complete': True}},
        {'material_id': 2, 'file_name': '分包合同.pdf', 'role': 'sub', 'total_pages': 2,
         'pages': [{'page': 1, 'readable': True, 'text': '项目名称：测试工程。'},
                   {'page': 2, 'readable': True, 'text': '发包方：沪上施工公司。'}],
         'reading_coverage': {'reading_complete': True}},
    ]
    report = build_report(raw, project_name='测试项目', sources=sources)
    overview = report['template_document']['blocks'][1]['text']
    assert '总包承包人尚未核实' in overview
    assert '分包发包方原文见《分包合同.pdf》PDF第2页' in overview
    assert '本次未开展总分包条款比对' in overview
    assert '分包合同按其文本和纳入范围的依据核查' in overview
    assert '模型内部字段提示' not in overview
    assert report['scope_note'] in overview
    assert len(report['rows']) == 0
    word = Document(BytesIO(report_template_render.word_bytes(report, 'digest', '2026-11-19')))
    assert any(overview in p.text for p in word.paragraphs)
    with fitz.open(stream=report_template_render.pdf_bytes(report, 'digest', '2026-11-19'), filetype='pdf') as pdf:
        assert overview in ''.join(''.join(page.get_text().split()) for page in pdf)


def test_matched_contracts_without_extracted_upstream_terms_disclose_comparison_scope():
    raw = _report()
    raw['upstream'] = {'state': 'partial', 'relationship': {'state': 'matched', 'checks': []},
                      'coverage': {'terms_total': 0, 'comparison_batches': []}}
    report = build_report(raw, project_name='测试项目', sources=[])
    overview = report['template_document']['blocks'][1]['text']
    assert '未取得可用于比对的总包条款' in overview
    assert '本次未开展总分包条款比对' in overview


@pytest.mark.parametrize(('field', 'label', 'first', 'second'), [
    ('project_name', '项目', '高架道路新建工程', '滨河桥梁改建工程'),
    ('contract_section', '标段', 'C01标段', 'C03标段'),
])
def test_report_scope_explains_confirmed_project_mismatch_with_original_pages(field, label, first, second):
    raw = _report()
    sources, facts = [], []
    for mid, (side, role, value) in enumerate([('总包', 'general', first), ('分包', 'labor', second)], 1):
        file = f'{side}合同.pdf'
        quote = f'{label}：{value}'
        source = {'material_id': mid, 'file_name': file, 'role': role, 'total_pages': mid,
                  'pages': [{'page': mid, 'readable': True, 'text': quote}],
                  'reading_coverage': {'reading_complete': False}}
        sources.append(source)
        citation = original_citation({'material_id': mid, 'page': mid, 'quote': quote}, [source])
        facts.append({'field': field, 'value': value, 'evidence': [{**citation, 'role': role}]})
    raw['upstream'] = {'state': 'mismatch', 'facts': facts,
        'relationship': {'state': 'mismatch', 'checks': [
            {'dimension': 'project', 'state': 'mismatch', 'evidence': [fact['evidence'][0] for fact in facts]},
            {'dimension': 'parties', 'state': 'matched'}, {'dimension': 'scope', 'state': 'matched'},
        ]}}
    report = build_report(raw, project_name=None, sources=sources)
    scope = report['scope_note']
    assert f'{label}不一致' in scope
    assert f'总包{label}为「{first}」（《总包合同.pdf》PDF第1页）' in scope
    assert f'分包{label}为「{second}」（《分包合同.pdf》PDF第2页）' in scope
    assert '本次未开展总分包条款比对' in scope
    assert '项目或标段对应关系待核实' not in scope


@pytest.mark.parametrize(('baseline', 'notice'), [
    ('2023-12-22', '核查基准日早于细则施行日，具体适用性请人工判别'),
    (None, '核查基准日尚未确定，具体适用性请人工判别'),
    ('2026-08-08', None),
])
def test_shanghai_comparison_finding_keeps_evidence_and_explains_temporal_limit(baseline, notice):
    raw = _report()
    raw['identity']['baseline_date'] = baseline
    raw['findings'] = [{
        'finding_id': 'f-shanghai', 'kind': 'conditional_risk', 'kind_label': '条件性风险',
        'time_scope': 'current', 'message': '合同所载的审批责任与依据要求存在差异。',
        'evidence': [{'material_id': 1, 'page': 2, 'file_name': '分包.pdf',
                      'quote': '审批责任原文。', 'clause': '2.1'}],
        'basis': [{'doc_id': 'SHJT-2026-8', 'marker': '第十二条', 'quote': '依据原文。',
                   'document': {'doc_title': '上海市公路工程施工分包管理实施细则',
                                'effective_from': '2026-08-08'}}],
    }]
    report = build_report(raw, project_name='测试项目', sources=[])
    row, = report['rows']
    texts = [block['text'] for block in report['template_document']['blocks'] if 'text' in block]
    if notice:
        assert notice in row['cells'][5]
        assert any(notice in text for text in texts)
    else:
        assert '具体适用性请人工判别' not in row['cells'][5]
    assert row['evidence'] == raw['findings'][0]['evidence']


def test_report_groups_only_findings_and_keeps_detail_table_as_final_appendix():
    raw = _report()
    raw['findings'] = [{
        'finding_id': 'f1', 'kind': 'internal_ambiguity', 'review_dimension': 'commercial_terms_and_insurance',
        'kind_label': '内部歧义', 'time_scope': 'contract_text', 'message': '两处计价约定的适用边界未说明。',
        'evidence': [{'material_id': 1, 'page': 1, 'file_name': '分包合同.pdf',
                      'quote': '计价约定原文。', 'clause': '12.1'}],
        'basis': [],
    }]
    source = {'material_id': 1, 'file_name': '分包合同.pdf', 'role': 'sub', 'total_pages': 1,
              'pages': [{'page': 1, 'readable': True, 'text': '计价约定原文。'}],
              'reading_coverage': {'reading_complete': True, 'blank_pages': []}}
    report = build_report(raw, project_name='测试项目', sources=[source])
    blocks = report['template_document']['blocks']
    headings = [block['text'] for block in blocks if block['type'] == 'heading']
    assert '计量计价、支付、担保与保险' in headings
    assert all(label not in headings for key, label in REPORT_DIMENSIONS.items()
               if key != 'commercial_terms_and_insurance')
    assert headings[:2] == ['一、核查概况', '二、重点问题']
    assert headings[-2:] == ['三、综合核查意见', '附件一：合同问题与风险明细表']
    assert blocks[-1]['type'] == 'table' and len(blocks[-1]['headers']) == 8


def test_legacy_finding_dimensions_project_into_nine_current_report_sections():
    assert len(REPORT_DIMENSIONS) == 9
    assert report_dimension_id('subcontract_scope') == 'scope_and_qualification'
    assert report_dimension_id('price_and_settlement') == 'commercial_terms_and_insurance'
    assert report_dimension_id('other_contract_terms') == 'contract_terms_and_upstream'

    raw = _report()
    raw['findings'] = [{
        'finding_id': 'legacy-1', 'kind': 'text_nonconformity',
        'review_dimension': 'subcontract_scope', 'kind_label': '文本不符合',
        'time_scope': 'historical', 'message': '合同范围约定与适用依据存在差异。',
        'evidence': [{'material_id': 1, 'page': 1, 'file_name': '分包合同.pdf',
                     'quote': '合同范围约定原文。', 'clause': '2.1'}],
        'basis': [],
    }]
    report = build_report(raw, project_name='测试项目', sources=[])
    headings = [block['text'] for block in report['template_document']['blocks']
                if block['type'] == 'heading']
    assert '分包范围与主体资格' in headings
    assert report['rows'][0]['review_dimension'] == 'scope_and_qualification'


def test_checkpoint_noise_is_audited_without_becoming_a_report_finding():
    raw = _report()
    raw['summary'] = '本次已覆盖条款范围内列示1项合同文本问题或风险提示。'
    raw['findings'] = [{'finding_id': 'noise', 'kind': 'internal_ambiguity', 'evidence': [
        {'quote': '\n'.join(f'17.2.{i} 完工验收程序' for i in range(100, 111))}]}]
    report = build_report(raw, project_name='项目', sources=[])
    assert not report['findings'] and not report['rows']
    assert report['execution_status'] == 'partial'
    assert '未形成可列示的问题' in report['summary']
    assert report['excluded_findings'][0]['finding'] == raw['findings'][0]
    assert report['excluded_findings'][0]['reason_code'] == 'repetitive_heading_evidence'
    assert len(raw['findings']) == 1  # 原检查点与保存版本不回写。
    assert '完工验收程序' not in str(report['template_document'])


def test_public_limits_use_execution_coverage_without_promoting_model_claims_to_facts():
    raw = _report()
    raw['limitations'] = ['第5页材料未提供社保，human_verified=false，请7日内补正。']
    raw['coverage'] = {'batches_total': 2, 'batches_completed': 1, 'batches': [
        {'state': 'completed', 'pages': [1, 2]}, {'state': 'failed', 'pages': [3, 4]}]}
    raw['execution_status'] = 'partial'
    report = build_report(raw, project_name='项目', sources=[])
    assert report['analysis_notes'] == raw['limitations']
    public = '\n'.join(report['limitations'])
    assert 'PDF第3、4页' in public and '未完成' in public
    assert not any(word in public for word in ('社保', 'human_verified', '7日'))
    visible = str(report['template_document']['blocks'])
    assert '核查限制' not in visible and 'PDF第3、4页' not in visible


def test_long_retrospective_table_repeats_headers_and_keeps_last_row():
    report = _report()
    table = next(block for block in report['template_document']['blocks'] if block['type'] == 'table')
    table['rows'] = [['测试工程', '分包', '合同.pdf', f'第{i+1}页 12.1', '合同原文摘录。' * 20,
                      '内部约定边界存在歧义。' * 15, '合同内部约定', '两处同页原文对照。' * 15]
                     for i in range(8)]
    table['rows'][-1][5] = '末行核验标识'
    with fitz.open(stream=report_template_render.pdf_bytes(report, 'digest'), filetype='pdf') as pdf:
        assert len(pdf) > 1
        text = ''.join(page.get_text() for page in pdf)
        assert '末行核验标识' in text
        assert text.count('合同文件') > 1
        for page in pdf:
            assert all(word[0] >= 0 and word[2] <= page.rect.width + 1 and word[3] <= page.rect.height + 1
                       for word in page.get_text('words'))


def test_retrospective_first_page_has_data_even_when_first_row_needs_the_next_page():
    report = _report()
    table = next(block for block in report['template_document']['blocks'] if block['type'] == 'table')
    table['rows'] = [['首行项目', '分包', '合同.pdf', '第39页', '合同原文摘录。',
                      '内部约定边界存在歧义。', '合同内部约定', '\n'.join(['两处原文对照。'] * 32)]]
    with fitz.open(stream=report_template_render.pdf_bytes(report, 'digest'), filetype='pdf') as pdf:
        assert len(pdf) > 1
        assert '首行项目' in pdf[0].get_text()
        assert '合同文件' in pdf[1].get_text()
