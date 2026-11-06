from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, delete, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base
from app.plus.blockchain_supervision.models import BsMaterial, BsMaterialFact, BsTask


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
    assert by_field["certificate.number"]["extractor_version"] == "material-facts.v2"


def _facts(text):
    from app.plus.blockchain_supervision.material_facts import extract_material_facts

    return {fact["field_path"]: fact for fact in extract_material_facts(_material(text))}


def test_extract_person_name_accepts_spaced_label():
    # 真实证照原文写作「姓 名: 陶瑜」（扫描件 p28），标签中间有空格。
    facts = _facts("姓 名: 陶瑜\n身份证号：360124199901027518")
    assert facts["person.name"]["raw_value"] == "陶瑜"


def test_extract_person_name_stops_at_inline_field_label():
    # 「姓名:鄢明志性别:男」连排时不能把「性别」并入姓名。
    facts = _facts("姓名:鄢明志性别:男\n证书编号: 沪交安C(22)G00822")
    assert facts["person.name"]["raw_value"] == "鄢明志"


def test_extract_person_name_stops_at_series_label():
    facts = _facts("姓名陶瑜系列工程系列\n性别 男")
    assert facts["person.name"]["raw_value"] == "陶瑜"


def test_extract_person_name_ignores_roster_table_header():
    # 名单表头「| 序号 | 姓名 | 岗位 |」的下一列不是姓名，不得臆造 person.name。
    facts = _facts("| 序号 | 姓名 | 岗位 | 证书 |\n| 1 | 陶瑜 | 项目经理 | 一级建造师 |")
    assert "person.name" not in facts


def test_extract_certificate_number_ignores_id_card_label():
    # 身份证号排在证书编号之前，不能被「证号」子串命中。
    facts = _facts("姓 名: 陶瑜\n身份证号：360124199901027518\n证书编号：沪交安B25G00683")
    number = facts["certificate.number"]["raw_value"]
    assert number == "沪交安B25G00683"
    assert "360124199901027518" not in number


def test_extract_certificate_number_requires_colon():
    # 名单表头「持证以及证号」不是编号标签，无冒号不得产出证书编号。
    facts = _facts("持证以及证号\n交安 B 证\n沪交安 B（25）G00206")
    assert "certificate.number" not in facts
    assert facts["certificate.type"]["raw_value"] == "B"


def test_extract_company_from_employment_unit_label():
    facts = _facts("受聘单位：中铁二十四局集团上海铁建工程有限公司\n证书编号：沪交安B25G00683")
    assert facts["company.name"]["raw_value"] == "中铁二十四局集团上海铁建工程有限公司"


def test_extract_company_does_not_swallow_following_certificate_number():
    facts = _facts("企业名称: 武汉龙辰建设有限公司 证书编号: 鄂交安A25G01398")
    assert facts["company.name"]["raw_value"] == "武汉龙辰建设有限公司"
    assert facts["certificate.number"]["raw_value"] == "鄂交安A25G01398"


def test_extract_company_strips_latex_page_decoration():
    # 扫描件文本层是 LaTeX 包裹：$\text { 单位名称:武汉龙辰建设有限公司 }$
    facts = _facts("$\\text { 单位名称:武汉龙辰建设有限公司 }$")
    assert facts["company.name"]["raw_value"] == "武汉龙辰建设有限公司"


def test_extract_certificate_number_strips_latex_page_decoration():
    facts = _facts("$\\text { 证书编号: } 0311811293118000239$")
    assert facts["certificate.number"]["raw_value"] == "0311811293118000239"


def test_extract_certificate_number_drops_trailing_date_column():
    # 表格里编号后面紧跟日期列，日期不是编号的一部分。
    facts = _facts("证书编号:102591202105000857 2021 06 21")
    assert facts["certificate.number"]["raw_value"] == "102591202105000857"


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


def _fact_row(*, material_id=7, task_id=3, field_path="person.name", page=1,
              batch="b1", raw="陶瑜", **kwargs):
    return BsMaterialFact(
        task_id=task_id,
        material_id=material_id,
        field_path=field_path,
        raw_value=raw,
        normalized_value=raw,
        page=page,
        quote=raw,
        quality="readable",
        source_kind="embedded_text_layer",
        parse_batch_id=batch,
        schema_version="material-facts.v1",
        extractor_version="material-facts.v1",
        **kwargs,
    )


def test_extract_material_facts_records_missing_page_as_zero():
    from app.plus.blockchain_supervision.material_facts import extract_material_facts

    material = SimpleNamespace(
        id=7,
        task_id=3,
        pages_json=[{"text": "姓名：陶瑜"}, {"page": None, "text": "证书编号：沪交安B25G00206"}],
        degradations_json=[],
    )

    facts = extract_material_facts(material)

    assert facts
    assert {fact["page"] for fact in facts} == {0}


def test_material_fact_page_column_is_not_null_with_zero_default():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)

    column = BsMaterialFact.__table__.c.page
    assert column.nullable is False
    assert column.server_default is not None

    with Session(engine) as db:
        _seed_materials(db)
        row = BsMaterialFact(
            task_id=3,
            material_id=7,
            field_path="person.name",
            raw_value="陶瑜",
            quote="陶瑜",
            quality="readable",
            source_kind="embedded_text_layer",
            parse_batch_id="b1",
            schema_version="material-facts.v1",
            extractor_version="material-facts.v1",
        )
        db.add(row)
        db.commit()
        assert row.page == 0


def test_material_fact_unique_index_rejects_duplicate_batch_key():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        _seed_materials(db)
        db.add(_fact_row())
        db.commit()

        db.add(_fact_row())
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        assert db.query(BsMaterialFact).count() == 1


def test_material_fact_same_field_and_page_allowed_across_batches():
    from app.plus.blockchain_supervision.material_facts import persist_material_facts

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        material = _seed_materials(db)
        first = persist_material_facts(db, material, parse_batch_id="b1")
        db.commit()
        second = persist_material_facts(db, material, parse_batch_id="b2")
        db.commit()

        assert first and second
        assert (db.query(BsMaterialFact)
                .filter(BsMaterialFact.field_path == "person.name",
                        BsMaterialFact.page == 1)
                .count()) == 2


def test_persist_material_facts_treats_unique_conflict_as_duplicate_without_poisoning_session():
    from app.plus.blockchain_supervision.material_facts import (
        default_parse_batch_id, material_content_sha256, persist_material_facts,
    )

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        material = _seed_materials(db)
        content_sha256 = material_content_sha256(material)
        batch_id = default_parse_batch_id(content_sha256)
        # 模拟并发写入方已抢先落库同名键，persist 的写入前查重会漏掉这条。
        db.add(_fact_row(field_path="person.name", page=1, batch=batch_id))
        db.commit()

        inserted = persist_material_facts(db, material, parse_batch_id=batch_id)
        db.commit()  # 唯一冲突按重复处理，Session 不应被标记为失败

        assert all(row.field_path != "person.name" for row in inserted)
        assert (db.query(BsMaterialFact)
                .filter(BsMaterialFact.field_path == "person.name")
                .count()) == 1
        assert db.query(BsMaterial).count() == 2


def test_deleting_task_cascades_material_facts():
    from app.plus.blockchain_supervision.material_facts import persist_material_facts

    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        # bs_task 的父链（账号/代理/会话）先满足外键，才能验证级联本身。
        conn.exec_driver_sql("INSERT INTO agents (id, slug) VALUES (1, 'bs-test')")
        conn.exec_driver_sql(
            "INSERT INTO standalone_users (id, username, password_hash, is_active, token_version) "
            "VALUES (1, 'cascade-test', 'x', 1, 0)"
        )
        conn.exec_driver_sql(
            "INSERT INTO conversation_mappings (id, user_id, user_type, agent_id, is_deleted) "
            "VALUES (1, 1, 'web', 1, 0)"
        )

    with Session(engine) as db:
        db.add(BsTask(id=1, conversation_id=1, title="级联验证", created_by=1,
                      created_by_type="web"))
        db.commit()
        material = BsMaterial(
            id=7,
            task_id=1,
            file_name="交安B证.pdf",
            role="packet",
            path="uploads/cert.pdf",
            pages_json=[{"page": 1, "text": "姓名：陶瑜\n证书编号：沪交安B25G00206"}],
            parse_state="done",
        )
        db.add(material)
        db.commit()

        rows = persist_material_facts(db, material)
        db.commit()
        assert rows

        db.execute(delete(BsTask).where(BsTask.id == 1))
        db.commit()
        # D-3：任务删除视为授权清除，事实随任务/材料级联消失。
        assert db.query(BsMaterialFact).count() == 0
        assert db.query(BsMaterial).count() == 0
