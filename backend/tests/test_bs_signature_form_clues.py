from types import SimpleNamespace

from app.plus.blockchain_supervision import signature_chain


def test_form_headings_are_page_clues_not_evidence_of_consent():
    text = ('劳务分包报审表\n总监理工程师意见：\n经核查有关材料\n'
            '总监：某某 2025年08月25日\n指挥部意见：\n指挥长：某某')
    material = SimpleNamespace(id=5, file_name='报审包.pdf', role='packet', parse_state='done',
        pages_json=[{'page': 1, 'text': text}], seal_json=[], subject_id=1)
    findings = signature_chain.run_signature_chain_checks(SimpleNamespace(id=6), [material])
    result = next(f for f in findings if f.item_id == 'A3-02-group')
    assert result.status == 'undecidable'
    assert result.page == 1 and result.quote in text
    assert '总监理工程师意见' in result.message and '指挥部意见' in result.message
    assert '原件核对' in result.message
    assert '已同意' not in result.message
    assert {ref['quote'] for ref in result.evidence_json} == {'总监理工程师意见', '指挥部意见'}


def test_unreadable_or_template_form_never_becomes_opinion_clue():
    text = '劳务分包报审表\n总监理工程师意见：\n指挥部意见：'
    material = SimpleNamespace(id=5, file_name='报审包.pdf', role='packet', parse_state='done',
        pages_json=[{'page': 1, 'text': text}], seal_json=[], subject_id=1,
        degradations_json=[{'kind': 'parse_incomplete', 'ocr_suspect_pages': [1]}])
    assert signature_chain._form_opinion_clue([material]) is None
    material.degradations_json = []
    material.pages_json[0]['text'] = '填写示例\n' + text
    assert signature_chain._form_opinion_clue([material]) is None
