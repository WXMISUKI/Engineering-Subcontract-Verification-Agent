"""真实合同OCR退化形态回归；不截断或改写原始识别文本。"""
from app.plus.blockchain_supervision.parsing import _ocr_quality_suspect


def test_incrementing_numbers_cannot_hide_repeated_ocr_title():
    text = '15.2 其它工程的成品保护\n' + '\n'.join(
        f'17.2.{i} 完工验收程序' for i in range(1, 325))
    assert _ocr_quality_suspect(text)


def test_distinct_contract_clauses_and_short_repeated_headings_are_not_rejected():
    assert not _ocr_quality_suspect('\n'.join(
        f'17.2.{i} 第{i}项验收程序另有具体约定' for i in range(1, 50)))
    assert not _ocr_quality_suspect('\n'.join(f'17.2.{i} 完工验收程序' for i in range(1, 6)))
    # 清单行中的相同标题、不同规格不是短标题退化输出。
    assert not _ocr_quality_suspect('\n'.join(
        f'17.2.{i} 完工验收程序应按专用合同约定的第{i}项详细技术要求和程序组织实施。' for i in range(1, 50)))
