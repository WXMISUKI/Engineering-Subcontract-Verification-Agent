from types import SimpleNamespace

from app.plus.blockchain_supervision.retrospective_sources import (
    analysis_batches, clause_index, freeze_material, infer_contract_date, infer_project_name, original_citation, reading_limitations,
)


def material(texts, **kwargs):
    return SimpleNamespace(id=1, file_name="合同.pdf", role="sub", parse_state="done",
        pages=len(texts), pages_json=[{"page": i + 1, "text": text} for i, text in enumerate(texts)],
        degradations_json=kwargs.get("degradations", []))


def test_cross_page_clause_preserves_physical_spans_and_original_whitespace():
    source = freeze_material(material(["12.1 工期\n延长工期费用另行协商。", "乙方承担全部损失。\n12.2 质量\n质量合格。" ]))
    index = clause_index(source)
    assert [span["page"] for span in index[0]["spans"]] == [1, 2]
    ref = original_citation({"material_id": 1, "page": 1, "quote": "12.1工期延长工期费用另行协商。"}, [source])
    assert ref["quote"] == "12.1 工期\n延长工期费用另行协商。"
    assert ref["clause"].startswith("12.1")
    assert not original_citation({"material_id": 1, "page": 2, "quote": "延长工期费用另行协商。"}, [source])
    assert not original_citation({"material_id": 2, "page": 1, "quote": ref["quote"]}, [source])


def test_chunking_covers_long_pages_and_keeps_context_bounded():
    source = freeze_material(material(["原文内容" * 120, "12.1 工期\n" + "工期费用约定" * 100]))
    batches = analysis_batches([source], max_chars=200, overlap_chars=40)
    actual = "".join(part["text"] for batch in batches for part in batch["parts"])
    assert actual == "".join(page["text"] for page in source["pages"])
    assert all(sum(len(part["text"]) for part in batch["context"]) <= 40 for batch in batches)
    assert len({row["batch_id"] for row in batches}) == len(batches)


def test_unread_and_suspect_page_is_not_usable_or_silently_omitted():
    source = freeze_material(material(["条款正文足够完整", "错误OCR原文待核实", "尾页有效正文"], degradations=[
        {"kind": "parse_incomplete", "total_pages": 3, "read_pages": [1, 3],
         "unread_pages": [], "ocr_suspect_pages": [2]}]))
    assert not original_citation({"material_id": 1, "page": 2, "quote": "错误OCR原文"}, [source])
    assert reading_limitations([source]) and "2" in reading_limitations([source])[0]
    assert {part["page"] for batch in analysis_batches([source]) for part in batch["parts"]} == {1, 3}


def test_confirmed_blank_is_accounted_for_but_never_used_as_contract_evidence():
    source = freeze_material(material(["分包合同的质量验收约定。", ""], degradations=[
        {"kind": "parse_incomplete", "total_pages": 2, "read_pages": [1],
         "unread_pages": [2], "ocr_blank_pages": [2]}]))
    assert not reading_limitations([source])
    assert {part["page"] for batch in analysis_batches([source]) for part in batch["parts"]} == {1}
    source["reading_coverage"]["suspect_pages"] = [2]
    assert reading_limitations([source])


def test_dates_are_not_guessed_from_project_schedule_or_conflicting_labels():
    source = freeze_material(material(["开工日期：2023年1月1日\n签订日期：2023年12月22日"]))
    assert infer_contract_date([source]) == ("2023-12-22", [])
    source = freeze_material(material(["签订日期：2023年12月22日", "签署日期：2024年1月2日"]))
    assert infer_contract_date([source])[0] is None


def test_signing_date_uses_main_agreement_before_attached_contracts():
    main_heading = r'$\text { 第一部分 合同协议书 }$'
    before_agreement = '签订日期：2022年5月22日\n' + main_heading
    terms = '# 第二部分 通用合同条款\n本节约定合同具体条件。'
    attachment = '治安防范、消防安全责任书（分包）\n签订日期：2022年5月22日'
    main_date = '| 签订日期：2023 年 5 月 22 日 | 签订日期：2023 年 5 月 22 日 |'
    source = freeze_material(material([before_agreement, main_date, terms, attachment]))
    assert infer_contract_date([source]) == ('2023-05-22', [])

    source = freeze_material(material([main_heading, terms, attachment]))
    assert infer_contract_date([source])[0] is None

    source = freeze_material(material([main_heading, main_date, '签署日期：2023年6月1日', terms, attachment]))
    assert infer_contract_date([source])[0] is None


def test_signing_date_rejects_other_contract_labels_inside_the_main_agreement():
    text = ('总包合同签订日期：2023年5月13日\n'
            '劳动合同签约日期：2024年8月13日\n'
            '| 本分包合同签订日期：2023 年 12 月 22 日 |')
    source = freeze_material(material([text]))
    assert infer_contract_date([source]) == ('2023-12-22', [])
    source = freeze_material(material([text.split('|')[0]]))
    assert infer_contract_date([source])[0] is None


def test_project_name_distinguishes_overall_project_from_subcontract_scope():
    source = freeze_material(material(['总包工程名称：某公路新建工程。\n分包工程名称：某公路新建工程吊装分包。']))
    source['relation'] = 'main'
    assert infer_project_name([source]) == '某公路新建工程'
    source['pages'].append({'page': 2, 'readable': True, 'text': '总包工程名称：另一个公路工程。'})
    assert infer_project_name([source]) is None


def test_historical_official_sources_are_traceable_without_faking_human_review():
    from app.plus.blockchain_supervision.retrospective_sources import _historical_basis
    rows = _historical_basis()
    assert len(rows) == 6
    wages = next(row for row in rows if row['clause_id'] == 'SC-724-2019-RETRO#35')
    assert '施工总承包单位也不得因争议不按照规定代发工资' in wages['text']
    assert wages['document']['effective_from'] == '2020-05-01'
    assert wages['document']['source_url'].startswith('https://www.gov.cn/gongbao/')
    assert all(not row['human_verified'] and not row['document']['human_verified'] for row in rows)
    safety = next(row for row in rows if row['clause_id'] == 'SC-393-2003-RETRO#24')
    assert '承担连带责任' in safety['text']


def test_historical_source_or_excerpt_tampering_is_rejected(tmp_path):
    import json
    import shutil
    import pytest
    from pathlib import Path
    from app.plus.blockchain_supervision import retrospective_sources
    root = Path(retrospective_sources.__file__).with_name('data') / 'retrospective_basis'
    copy = tmp_path / 'basis'
    shutil.copytree(root, copy)
    original = (copy / 'wages.html').read_bytes()
    (copy / 'wages.html').write_bytes(original + b'changed')
    with pytest.raises(ValueError, match='指纹'):
        retrospective_sources._historical_basis(copy)
    (copy / 'wages.html').write_bytes(original)
    manifest = json.loads((copy / 'manifest.json').read_text(encoding='utf-8'))
    manifest['clauses'][0]['text'] = '伪造的规范原文不得进入报告'
    (copy / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
    with pytest.raises(ValueError, match='选段'):
        retrospective_sources._historical_basis(copy)
    source = freeze_material(material(["计划开工日期：2023年12月22日"]))
    assert infer_contract_date([source])[0] is None
