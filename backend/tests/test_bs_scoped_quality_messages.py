from types import SimpleNamespace
from app.plus.blockchain_supervision.a2_check import _certificate_reading_limit
from app.plus.blockchain_supervision.signature_chain import _seal_note


def test_certificate_quality_limit_does_not_attribute_unrelated_pages():
    mat = SimpleNamespace(file_name='报审.pdf', parse_state='done',
                          pages_json=[{'page': 2, 'text': '工程量'}, {'page': 10, 'text': '执照'}],
                          degradations_json=[{'kind': 'parse_incomplete', 'ocr_suspect_pages': [2, 10]}])
    message = _certificate_reading_limit(mat)
    assert '人工' in message and '第2' not in message and '第10' not in message
    assert '识别质量' not in message


def test_many_seals_do_not_claim_all_pages_are_signing_pages():
    mat = SimpleNamespace(seal_json=[{'page_idx': n} for n in range(1, 30)])
    text = _seal_note(mat)
    assert '人工核对' in text and '第1页' not in text and '第29页' not in text
