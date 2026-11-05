from app.services.knowledge.weknora_adapter import WeKnoraAdapter


def test_weknora_pdf_locator_provides_page_span_without_claiming_visual_highlight():
    chunk = {'id': 'c1', 'content': 'synthetic', 'score': 0.8, 'knowledge_id': 'doc',
             'source_locators': [{'type': 'pdf', 'page': 4}, {'type': 'pdf', 'page': 5}]}
    row = WeKnoraAdapter._chunk_to_row(chunk, 'bs_test')
    assert row['page'] == 4 and row['page_end'] == 5
    assert row['metadata']['page_source'] == 'weknora_source_locators'
    assert 'bbox' not in row['metadata']


def test_partial_or_invalid_locators_do_not_claim_a_page():
    chunk = {'id': 'c1', 'content': 'synthetic', 'score': 0.8, 'knowledge_id': 'doc',
             'source_locators': [{'type': 'pdf', 'page': 7, 'partial': True},
                                 {'type': 'pdf', 'page': True}, {'type': 'pdf', 'page': -1},
                                 {'type': 'docx', 'page': 9}]}
    assert WeKnoraAdapter._chunk_to_row(chunk, 'bs_test')['page'] is None
