"""S2 定向用例：材料解析收口写入事实批次。

覆盖 D-1（事实与 material.parsed 同事务）、D-2（抽取失败降级、解析失败不产事实、
质量可疑页只产候选事实）、S2-D1（代次唯一才归属）与 ③A（主体冲突仍保留候选事实）。
不触发真实 OCR：解析入口由用例注入。
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.plus.blockchain_supervision import parsing, service
from app.plus.blockchain_supervision.models import (
    BsMaterial, BsMaterialFact, BsSubject, BsTask, BsTaskEvent,
)

APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(parsing.__file__), "..", ".."))
CERT_TEXT = (
    "姓名：陶瑜\n企业名称：中铁二十四局集团上海铁建工程有限公司\n"
    "证书编号：沪交安B25G00206\n有效期：2025年1月1日至2028年1月1日"
)


def _engine(tmp_path):
    # 收口走独立会话（SessionLocal(bind=...)），内存库跨会话不共享，必须落文件。
    return create_engine(
        "sqlite:///" + (tmp_path / "ingest.sqlite").as_posix(),
        connect_args={"check_same_thread": False},
    )


def _seed(tmp_path, *, role="packet", generation="g-2", file_name="交安B证.pdf",
          subject_name=None):
    engine = _engine(tmp_path)
    Base.metadata.create_all(engine)
    file_path = tmp_path / file_name
    file_path.write_bytes(b"%PDF-1.4 synthetic certificate")
    with Session(engine) as db:
        db.add(BsTask(id=1, conversation_id=1, title="补件任务", state="running",
                      created_by=1, created_by_type="web"))
        db.commit()
        subject_id = None
        if subject_name:
            subject = BsSubject(task_id=1, stable_code="S-1", name=subject_name)
            db.add(subject)
            db.commit()
            subject_id = subject.id
        material = BsMaterial(task_id=1, file_name=file_name, role=role,
                              path=os.path.relpath(file_path, APP_ROOT),
                              subject_id=subject_id, parse_state="parsing")
        db.add(material)
        db.commit()
        if generation:
            service.add_event(db, 1, "material.supplement_linked",
                              payload={"generation_id": generation, "material_id": material.id},
                              commit=False)
            db.commit()
        return engine, material.id


def _pages(monkeypatch, text=CERT_TEXT, degradations=None, error=None):
    calls = []

    def fake_parse(abs_path, progress=None):
        calls.append(abs_path)
        if error is not None:
            raise error
        return ([{"page": 1, "text": text}],
                degradations or [{"kind": "total_pages", "total_pages": 1}])

    monkeypatch.setattr(parsing, "parse_material_pages", fake_parse)
    return calls


def _facts(db, material_id):
    return db.query(BsMaterialFact).filter_by(material_id=material_id).order_by(BsMaterialFact.id).all()


def _event_types(db):
    return [row.event_type for row in db.query(BsTaskEvent).filter_by(task_id=1).all()]


def test_ingest_writes_facts_with_parsed_event_in_one_close(tmp_path, monkeypatch):
    engine, material_id = _seed(tmp_path)
    _pages(monkeypatch)

    service._finish_ingest(1, material_id, engine)

    with Session(engine) as db:
        material = db.get(BsMaterial, material_id)
        facts = _facts(db, material_id)
        parsed = db.query(BsTaskEvent).filter_by(task_id=1, event_type="material.parsed").all()
        assert material.parse_state == "done"
        assert {fact.field_path for fact in facts} >= {"person.name", "certificate.number"}
        assert all(fact.generation_id == "g-2" for fact in facts)
        assert all(fact.page == 1 and fact.quote for fact in facts)
        assert len(parsed) == 1
        # 收口事件与事实同一次提交：事件里能对上本次真正新增的行数
        assert parsed[0].payload_json["facts_inserted"] == len(facts)


def test_fact_extraction_failure_degrades_without_losing_parse(tmp_path, monkeypatch):
    engine, material_id = _seed(tmp_path)
    _pages(monkeypatch)

    def boom(*args, **kwargs):
        raise RuntimeError("抽取器崩了")

    monkeypatch.setattr(service, "persist_material_facts", boom)

    service._finish_ingest(1, material_id, engine)

    with Session(engine) as db:
        material = db.get(BsMaterial, material_id)
        assert material.parse_state == "done"
        assert "facts_failed" in [item.get("kind") for item in material.degradations_json or []]
        assert _facts(db, material_id) == []
        events = _event_types(db)
        assert "material.parsed" in events and "material.parse_failed" not in events
        parsed = db.query(BsTaskEvent).filter_by(task_id=1, event_type="material.parsed").one()
        assert parsed.payload_json["facts_inserted"] is None


def test_parse_failure_writes_no_facts(tmp_path, monkeypatch):
    engine, material_id = _seed(tmp_path)
    _pages(monkeypatch, error=ValueError("文件损坏"))

    service._finish_ingest(1, material_id, engine)

    with Session(engine) as db:
        material = db.get(BsMaterial, material_id)
        assert material.parse_state == "failed"
        assert _facts(db, material_id) == []
        events = _event_types(db)
        assert "material.parse_failed" in events and "material.parsed" not in events


def test_repeated_ingest_reuses_batch_without_duplicate_facts(tmp_path, monkeypatch):
    engine, material_id = _seed(tmp_path)
    _pages(monkeypatch)

    service._finish_ingest(1, material_id, engine)
    with Session(engine) as db:
        first_count = len(_facts(db, material_id))

    service._finish_ingest(1, material_id, engine)

    with Session(engine) as db:
        facts = _facts(db, material_id)
        payloads = [row.payload_json for row in
                    db.query(BsTaskEvent).filter_by(task_id=1, event_type="material.parsed").all()]
        assert len(facts) == first_count
        assert len({fact.parse_batch_id for fact in facts}) == 1
        assert payloads[-1]["facts_inserted"] == 0
        assert first_count > 0


def test_material_without_supplement_link_keeps_generation_unknown(tmp_path, monkeypatch):
    engine, material_id = _seed(tmp_path, generation=None)
    _pages(monkeypatch)

    service._finish_ingest(1, material_id, engine)

    with Session(engine) as db:
        facts = _facts(db, material_id)
        assert facts
        assert all(fact.generation_id is None for fact in facts)


def test_conflicting_supplement_generations_leave_generation_unknown(tmp_path, monkeypatch):
    engine, material_id = _seed(tmp_path, generation="g-2")
    with Session(engine) as db:
        service.add_event(db, 1, "material.supplement_linked",
                          payload={"generation_id": "g-3", "material_id": material_id},
                          commit=False)
        db.commit()
    _pages(monkeypatch)

    service._finish_ingest(1, material_id, engine)

    with Session(engine) as db:
        facts = _facts(db, material_id)
        assert facts
        assert all(fact.generation_id is None for fact in facts)


def test_quality_suspect_page_yields_review_required_facts_without_reparse(tmp_path, monkeypatch):
    engine, material_id = _seed(tmp_path)
    calls = _pages(monkeypatch, text="姓名：陶瑜\n证书编号：沪交安B25G00206",
                   degradations=[{"kind": "ocr_quality", "message": "第1页文字识别不完整"}])

    service._finish_ingest(1, material_id, engine)

    with Session(engine) as db:
        material = db.get(BsMaterial, material_id)
        facts = _facts(db, material_id)
        assert material.parse_state == "done"
        assert facts and {fact.quality for fact in facts} == {"review_required"}
        assert len(calls) == 1  # 可疑页不触发自动重读


def test_subject_conflict_keeps_candidate_facts_but_blocks_clean_pass(tmp_path, monkeypatch):
    engine, material_id = _seed(
        tmp_path, file_name="营业执照.pdf",
        subject_name="中铁二十四局集团上海铁建工程有限公司",
    )
    _pages(monkeypatch, text="企业名称：上海示例建设有限公司")

    service._finish_ingest(1, material_id, engine)

    with Session(engine) as db:
        material = db.get(BsMaterial, material_id)
        kinds = [item.get("kind") for item in material.degradations_json or []]
        assert material.parse_state == "done"
        assert "subject_conflict" in kinds
        assert "material.subject_conflict" in _event_types(db)
        # ③A：读取成果保留为候选事实，但冲突记录使其不构成有效通过证据
        assert _facts(db, material_id)
