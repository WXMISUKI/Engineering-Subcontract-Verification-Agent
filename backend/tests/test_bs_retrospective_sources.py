from types import SimpleNamespace

from app.plus.blockchain_supervision.retrospective_sources import (
    analysis_batches, clause_index, freeze_material, infer_contract_date, original_citation, reading_limitations,
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


def test_dates_are_not_guessed_from_project_schedule_or_conflicting_labels():
    source = freeze_material(material(["开工日期：2023年1月1日\n签订日期：2023年12月22日"]))
    assert infer_contract_date([source]) == ("2023-12-22", [])
    source = freeze_material(material(["签订日期：2023年12月22日", "签署日期：2024年1月2日"]))
    assert infer_contract_date([source])[0] is None
    source = freeze_material(material(["计划开工日期：2023年12月22日"]))
    assert infer_contract_date([source])[0] is None
