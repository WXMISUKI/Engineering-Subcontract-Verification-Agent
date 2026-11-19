import fitz
from io import BytesIO
from docx import Document

from app.plus.blockchain_supervision import report_template_render
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


def test_retrospective_report_has_eight_stable_columns_and_widths():
    report = _report()
    table = next(block for block in report["template_document"]["blocks"] if block["type"] == "table")
    assert len(table["headers"]) == 8
    assert abs(sum(table["widths"]) - 1) < 1e-9


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
