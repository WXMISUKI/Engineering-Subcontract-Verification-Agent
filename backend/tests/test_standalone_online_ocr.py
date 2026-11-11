import hashlib

import fitz
import pytest

from app.plus.blockchain_supervision import parsing
from app.plus.structural_check import ocr_text


def pdf_file(tmp_path):
    path = tmp_path / 'synthetic.pdf'
    doc = fitz.open()
    doc.new_page()
    doc.save(path)
    doc.close()
    return path


def test_cloud_failure_records_unread_without_calling_local(monkeypatch, tmp_path):
    from app.plus.safety_review import ocr_cloud

    called = []
    monkeypatch.setattr(ocr_cloud, 'cloud_config', lambda: {})
    monkeypatch.setattr(ocr_text, '_local_ocr_engine', lambda: called.append(True))
    monkeypatch.setattr(ocr_text, '_render_pages', lambda path, pages, dpi: {})
    done = []
    pages, stats = ocr_text.ocr_pdf_pages(str(pdf_file(tmp_path)), [0], use_cache=False,
                                         allow_local=False, on_page=lambda page, source: done.append((page, source)))
    assert pages == {}
    assert stats['failed_pages'] == [0]
    assert done == [(0, 'failed')]
    assert called == []


def test_online_error_does_not_fall_back_and_material_stays_unread(monkeypatch, tmp_path):
    from app.plus.safety_review import ocr_cloud
    path = tmp_path / 'scanned.pdf'
    doc = fitz.open()
    page = doc.new_page()
    page.draw_rect(fitz.Rect(30, 30, 90, 90), fill=(0, 0, 0))
    doc.save(path)
    doc.close()
    called = []
    async def fail(*args, **kwargs):
        raise TimeoutError('synthetic timeout')
    monkeypatch.setattr(ocr_cloud, 'cloud_config', lambda: {'model': 'synthetic', 'endpoint': 'https://example.invalid', 'api_key': 'synthetic'})
    monkeypatch.setattr(ocr_cloud, 'cloud_ocr_structured', fail)
    monkeypatch.setattr(ocr_text, '_local_ocr_engine', lambda: called.append(True))
    pages, degradations = parsing.parse_material_pages(str(path))
    assert not pages
    assert called == []
    assert any(item.get('kind') == 'parse_incomplete' and 1 in item.get('unread_pages', []) for item in degradations)


def test_subcontract_ocr_uses_cloud_and_bypasses_untrusted_cache(monkeypatch, tmp_path):
    calls = []
    def fake_ocr(path, pages, **kwargs):
        calls.append(kwargs)
        return {0: 'synthetic recognized text'}, {'page_meta': {}, 'engine': 'cloud:synthetic'}
    monkeypatch.setattr(ocr_text, 'ocr_pdf_pages', fake_ocr)
    path = pdf_file(tmp_path)
    pages, _ = parsing.ocr_text_pages(str(path), page_nos=[1])
    assert pages[0]['page'] == 1
    assert calls[0]['use_cloud'] is True
    assert calls[0]['allow_local'] is False
    assert calls[0]['use_cache'] is False
    rejected, info = parsing.ocr_text_pages(str(path), page_nos=[1], use_cloud=False)
    assert rejected == [] and info is None and len(calls) == 1


def test_rotated_and_targeted_retries_use_online_service(monkeypatch, tmp_path):
    path = pdf_file(tmp_path)
    calls = []
    def fake_ocr(path, **kwargs):
        calls.append(kwargs)
        return [{'page': 1, 'text': 'synthetic readable content',
                 'ocr_meta': {'source': 'cloud', 'quality': 'ocr_ok'}}], {'engine': 'cloud:synthetic'}
    monkeypatch.setattr(parsing, 'ocr_text_pages', fake_ocr)
    rotated, _ = parsing.ocr_rotated_text_pages(str(path), [1])
    assert rotated[0]['page'] == 1
    assert rotated[0]['ocr_meta']['source'] == 'cloud_rotated'
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    parsing.prepare_targeted_page_text_candidate(str(path), [{'page': 1, 'text': 'original'}], [1], expected_sha256=digest)
    assert len(calls) == 2
    assert all(call.get('use_cloud', True) is True and call['use_cache'] is False for call in calls)


def test_rotated_page_honors_270_degree_direction(monkeypatch, tmp_path):
    path = pdf_file(tmp_path)
    observed = []

    def fake_ocr(rotated_path, **kwargs):
        with fitz.open(rotated_path) as doc:
            observed.append(doc[0].rotation)
        return [{'page': 1, 'text': 'readable rotated certificate',
                 'ocr_meta': {'source': 'cloud', 'quality': 'ocr_ok'}}], {'engine': 'cloud:synthetic'}

    monkeypatch.setattr(parsing, 'ocr_text_pages', fake_ocr)
    pages, _ = parsing.ocr_rotated_text_pages(str(path), [1], rotations={1: 270})

    assert observed == [270]
    assert pages[0]['ocr_meta']['rotation_retry'] == 270


def test_low_quality_page_remains_for_human_review_without_retry(monkeypatch, tmp_path):
    path = tmp_path / 'quality.pdf'
    doc = fitz.open()
    doc.new_page().draw_rect(fitz.Rect(30, 30, 90, 90), fill=(0, 0, 0))
    doc.save(path)
    doc.close()
    calls = []
    def fake_ocr(path, **kwargs):
        calls.append(kwargs)
        text = '短' if len(calls) == 1 else '工程分包材料内容用于审查主体名称和施工合同信息' * 3
        return [{'page': 1, 'text': text, 'ocr_meta': {'source': 'cloud'}}], {'engine': 'cloud:synthetic'}
    monkeypatch.setattr(parsing, 'ocr_text_pages', fake_ocr)
    pages, degradations = parsing.parse_material_pages(str(path))
    assert len(calls) == 1
    assert pages[0]['text'] == '短'
    incomplete = next(row for row in degradations if row['kind'] == 'parse_incomplete')
    assert incomplete['ocr_suspect_pages'] == [1]
    assert incomplete['ocr_retry_pages'] == []
    assert incomplete['rotation_retry_pages'] == []


@pytest.mark.parametrize('direction', [90, 270])
def test_certificate_orientation_signal_triggers_one_bounded_rotation_retry(monkeypatch, tmp_path, direction):
    path = tmp_path / 'rotated-certificate.pdf'
    doc = fitz.open()
    doc.new_page().draw_rect(fitz.Rect(30, 30, 90, 90), fill=(0, 0, 0))
    doc.save(path)
    doc.close()

    calls = []

    def fake_ocr(path, **kwargs):
        return ([{
            'page': 1,
            'text': f'<290,422,375,155,{direction}>,公路水运工程施工企业主要负责人和安全生产管理人员安全生产考核合格证书\n姓名：于文祥',
            'ocr_meta': {'source': 'cloud'},
        }], {'engine': 'cloud:synthetic'})

    def fake_rotated(path, page_nos, *, rotations=None):
        calls.append(list(page_nos))
        assert rotations == {1: direction}
        return ([{
            'page': 1,
            'text': '公路水运工程施工企业主要负责人和安全生产管理人员安全生产考核合格证书\n'
                    '姓名：于文祥\n企业名称：武汉龙辰建设有限公司\n证书编号：鄂交安C25G01400\n'
                    '有效期：2025-06-10 至 2028-06-10',
            'ocr_meta': {'source': 'cloud_rotated', 'quality': 'ocr_ok', 'rotation_retry': direction},
        }], {'engine': 'cloud:synthetic'})

    monkeypatch.setattr(parsing, 'ocr_text_pages', fake_ocr)
    monkeypatch.setattr(parsing, 'ocr_rotated_text_pages', fake_rotated)
    pages, degradations = parsing.parse_material_pages(str(path))

    assert calls == [[1]]
    assert pages[0]['text'].endswith('有效期：2025-06-10 至 2028-06-10')
    fallback = next(row for row in degradations if row['kind'] == 'ocr_fallback')
    assert fallback['rotation_retry_pages'] == [1]
    assert fallback['rotation_retry_degrees'] == {'1': direction}
    assert fallback['rotation_retry_recovered_pages'] == [1]
    assert fallback['page_states'][0]['state'] == 'ocr_ok'
    assert any(item['state'] == 'retrying' and item['attempt'] == 1
               for item in fallback['page_state_history'][0]['transitions'])


def test_missing_identity_fields_go_to_review_without_second_ocr(monkeypatch, tmp_path):
    path = tmp_path / 'identity.pdf'
    doc = fitz.open()
    for _ in range(2):
        doc.new_page().draw_rect(fitz.Rect(30, 30, 90, 90), fill=(0, 0, 0))
    doc.save(path)
    doc.close()
    calls = []
    def fake_ocr(path, **kwargs):
        calls.append(kwargs)
        return [
            {'page': 1, 'text': '安全生产考核合格证书\n姓名：张三\n身份证号码：110101199001010011'},
            {'page': 2, 'text': '居民身份证\n签发机关：上海市公安局\n有效期限：2020.01.01-2040.01.01'},
        ], {'engine': 'cloud:synthetic'}
    monkeypatch.setattr(parsing, 'ocr_text_pages', fake_ocr)
    pages, degradations = parsing.parse_material_pages(str(path))
    assert len(calls) == 1
    incomplete = next(row for row in degradations if row['kind'] == 'parse_incomplete')
    assert 2 in incomplete['ocr_suspect_pages']
    assert pages[1]['text'].startswith('居民身份证')


def test_real_online_adapter_handles_normal_and_rotated_pages(monkeypatch, tmp_path):
    import httpx
    path = pdf_file(tmp_path)
    for key, value in [('OCR_CLOUD_MODEL', 'synthetic'), ('OCR_CLOUD_ENDPOINT', 'https://ocr.invalid/api'),
                       ('OCR_CLOUD_API_KEY', 'synthetic')]:
        monkeypatch.setenv(key, value)
    calls = []
    def handler(request):
        calls.append(request.url.host)
        return httpx.Response(200, json={'output': {'choices': [{'message': {'content': [{'text': '合成线上识别材料内容'}]}}]}})
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr(ocr_text, '_local_ocr_engine', lambda: (_ for _ in ()).throw(AssertionError('local OCR called')))
    pages, info = parsing.ocr_text_pages(str(path), page_nos=[1])
    rotated, rotated_info = parsing.ocr_rotated_text_pages(str(path), [1])
    assert pages[0]['text'] == '合成线上识别材料内容'
    assert rotated[0]['text'] == pages[0]['text'] and rotated[0]['page'] == 1
    assert rotated[0]['ocr_meta']['source'] == 'cloud_rotated'
    assert info['engine'].startswith('cloud:') and rotated_info['engine'].startswith('cloud:')
    assert calls == ['ocr.invalid', 'ocr.invalid']


def test_online_vertical_layout_can_trigger_rotation_review():
    blocks = [{'text': '工程分包施工单位信息内容', 'bbox': [i * 20, 0, i * 20 + 10, 100]} for i in range(3)]
    assert parsing._ocr_sideways_layout({'ocr_meta': {'source': 'cloud', 'layout': blocks}})
