import fitz
from io import BytesIO
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.plus.blockchain_supervision import report_template_render
from app.plus.blockchain_supervision.retrospective_contracts import REPORT_DIMENSIONS, report_dimension_id
from app.plus.blockchain_supervision.retrospective_report import build_report


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
