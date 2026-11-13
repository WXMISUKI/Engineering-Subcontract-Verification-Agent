from app.plus.blockchain_supervision.reporting import reading_summary


def test_distinguishes_unread_suspect_and_blank_pages_without_losing_scope():
    text = reading_summary('资料.pdf', {'total_pages': 8, 'unread_pages': [2, 3, 4, 5],
        'suspect_pages': [3, 4], 'blank_pages': [5], 'over_limit_pages': [2]}, 6)
    assert '第 2 页未取得可用正文' in text
    assert '第 3–4 页已取得文字但质量可疑' in text
    assert '第 5 页经识别为空白页' in text
    assert '第 2 页超出本次文字识别页数上限' in text
    assert '涉及 6 条结论' in text
    assert '未取得可用正文的页码：第 2–5 页' not in text


def test_suspect_only_does_not_imply_entire_material_unread():
    text = reading_summary('资料.pdf', {'total_pages': 3, 'unread_pages': [], 'suspect_pages': [2]}, 1)
    assert '第 2 页已取得文字但质量可疑' in text
    assert '未能读取正文' not in text


def test_unknown_whole_material_failure_remains_explicit():
    text = reading_summary('资料.pdf', {'total_pages': None, 'unread_pages': []}, 1)
    assert '未能读取正文' in text
