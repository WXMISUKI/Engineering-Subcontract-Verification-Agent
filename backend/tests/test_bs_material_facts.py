from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.plus.blockchain_supervision.models import BsMaterial, BsMaterialFact


def _material(text, *, file_name="交安B证.pdf", degradations=None, ocr_source=None):
    page = {"page": 1, "text": text}
    if ocr_source:
        page["ocr_meta"] = {"source": ocr_source}
    return SimpleNamespace(
        id=7,
        task_id=3,
        file_name=file_name,
        pages_json=[page],
        degradations_json=degradations or [],
    )


def test_extract_material_facts_preserves_raw_normalized_and_grounding():
    from app.plus.blockchain_supervision.material_facts import extract_material_facts

    material = _material(
        "姓名：陶瑜\n企业名称：中铁二十四局集团上海铁建工程有限公司\n"
        "证书编号：沪交安 B（25）G00206\n有效期：2025年1月1日至2028年1月1日"
    )

    facts = extract_material_facts(material)

    by_field = {fact["field_path"]: fact for fact in facts}
    assert by_field["person.name"]["raw_value"] == "陶瑜"
    assert by_field["person.name"]["normalized_value"] == "陶瑜"
    assert by_field["certificate.number"]["raw_value"] == "沪交安 B（25）G00206"
    assert by_field["certificate.number"]["normalized_value"] == "沪交安B25G00206"
    assert by_field["certificate.number"]["page"] == 1
    assert by_field["certificate.number"]["quote"]
    # 无 ocr_meta 的页面是文本层，不能一律记成线上 OCR
    assert by_field["certificate.number"]["source_kind"] == "embedded_text_layer"
    assert by_field["certificate.number"]["extractor_version"] == "material-facts.v1"


def test_extract_material_facts_records_actual_page_source():
    from app.plus.blockchain_supervision.material_facts import extract_material_facts

    cloud = extract_material_facts(_material("证书编号：沪交安B25G00206", ocr_source="cloud"))
    local = extract_material_facts(_material("证书编号：沪交安B25G00206", ocr_source="local"))

    assert {fact["source_kind"] for fact in cloud} == {"online_ocr_text"}
    assert {fact["source_kind"] for fact in local} == {"local_ocr_text"}


def test_extract_material_facts_marks_quality_suspect_pages_for_review_without_reocr():
    from app.plus.blockchain_supervision.material_facts import extract_material_facts

    material = _material(
        "姓名：\n企业名称：\n证书编号：沪交安C(",
        degradations=[{"kind": "parse_incomplete", "message": "第1页文字识别不完整"}],
    )

    facts = extract_material_facts(material)

    assert facts
    assert {fact["quality"] for fact in facts} == {"review_required"}
    assert not any(fact.get("reprocess") for fact in facts)
    assert not any(fact["field_path"] == "person.name" and fact["normalized_value"] for fact in facts)


def _seed_materials(db):
    material = BsMaterial(
        id=7,
        task_id=3,
        file_name="交安B证.pdf",
        role="packet",
        path="uploads/cert.pdf",
        pages_json=[{"page": 1, "text": "姓名：陶瑜\n证书编号：沪交安B25G00206"}],
        parse_state="done",
        revision_no=2,
    )
    other = BsMaterial(
        id=8,
        task_id=3,
        file_name="其他材料.pdf",
        role="packet",
        path="uploads/other.pdf",
        pages_json=[{"page": 1, "text": "证书编号：沪交安C25G00001"}],
        parse_state="done",
    )
    db.add_all([material, other])
    db.commit()
    return material


def test_persist_material_facts_appends_batches_and_keeps_history():
    from app.plus.blockchain_supervision.material_facts import (
        latest_material_facts, material_fact_batches, persist_material_facts,
    )

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        material = _seed_materials(db)

        first = persist_material_facts(db, material, generation_id="g1")
        db.commit()
        assert first
        first_batch = first[0].parse_batch_id
        first_count = len(first)

        second = persist_material_facts(db, material, generation_id="g2",
                                        parse_batch_id="g2-batch")
        db.commit()
        assert second
        assert {row.parse_batch_id for row in second} == {"g2-batch"}

        history = db.query(BsMaterialFact).order_by(BsMaterialFact.id).all()
        # append-only：g1 行仍在，且内容未被 g2 覆盖
        assert {row.generation_id for row in history} == {"g1", "g2"}
        assert sum(1 for row in history if row.parse_batch_id == first_batch) == first_count
        assert {row.material_id for row in history} == {7}
        assert material_fact_batches(db, 7) == [first_batch, "g2-batch"]
        assert {row.parse_batch_id for row in latest_material_facts(db, 7)} == {"g2-batch"}

        # 同批次重放幂等，不重复插入
        replay = persist_material_facts(db, material, generation_id="g2",
                                        parse_batch_id="g2-batch")
        db.commit()
        assert replay == []
        assert db.query(BsMaterialFact).count() == len(history)


def test_persist_material_facts_records_version_fingerprint_and_whitelisted_fields():
    from app.plus.blockchain_supervision.material_facts import (
        EXTRACTOR_VERSION, QUALITY_STATES, SOURCE_KINDS, persist_material_facts,
    )

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        material = _seed_materials(db)
        rows = persist_material_facts(db, material, generation_id="g1")
        db.commit()

        assert rows
        for row in rows:
            assert len(row.content_sha256) == 64
            assert row.material_version == 2
            assert row.schema_version == "material-facts.v1"
            assert row.extractor_version == EXTRACTOR_VERSION
            assert row.source_kind in SOURCE_KINDS
            assert row.quality in QUALITY_STATES
            assert row.quote


def test_persist_material_facts_is_idempotent_by_content_and_extractor():
    from app.plus.blockchain_supervision.material_facts import persist_material_facts

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        material = _seed_materials(db)

        first = persist_material_facts(db, material, generation_id="g1")
        db.commit()
        # 相同材料对象重复解析（未指定批次）落回同一批次
        again = persist_material_facts(db, material, generation_id="g1")
        db.commit()

        assert first
        assert again == []
        assert db.query(BsMaterialFact).count() == len(first)
