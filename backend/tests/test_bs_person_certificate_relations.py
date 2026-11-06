"""S3 定向用例：人员名单与证书的三态关系。

覆盖 matched / conflict / unresolved 三态的判定边界、同名多证书互斥、
多页同一证件的合并、主体冲突，以及 append-only 落库的幂等与版本追加。
不触发 OCR：事实与页面均由用例直接构造。
"""
import os
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.plus.blockchain_supervision.models import BsEvidenceRelation, BsTask
from app.plus.blockchain_supervision.relation_resolution import (
    STATUS_CONFLICT, STATUS_MATCHED, STATUS_UNRESOLVED,
    build_person_certificate_relations, latest_relations, persist_relations,
)

COMPANY = "中铁二十四局集团上海铁建工程有限公司"
ROSTER_TEXT = (
    "管理人员名单\n"
    "| 序号 | 姓名 | 岗位 | 联系方式 | 持证以及证号 |\n"
    "| --- | --- | --- | --- | --- |\n"
    "| 1 | 陶瑜 | 项目经理 | 13800138000 | 沪交安B25G00206 |\n"
)
CERT_TEXT = (
    "姓名：陶瑜\n企业名称：%s\n证书编号：沪交安B25G00206" % COMPANY
)


def _box(x0, y0, x1, y1):
    return [x0, y0, x1, y1]


def _roster_page(with_layout=True, page=1):
    meta = {"quality": "ocr_ok"} if not with_layout else {
        "quality": "ocr_ok",
        "layout": [
            {"text": "姓名", "bbox": _box(10, 10, 60, 25)},
            {"text": "职务", "bbox": _box(100, 10, 160, 25)},
            {"text": "持证以及证号", "bbox": _box(200, 10, 330, 25)},
            {"text": "陶瑜", "bbox": _box(15, 40, 55, 55)},
            {"text": "项目经理", "bbox": _box(105, 40, 155, 55)},
            {"text": "沪交安B25G00206", "bbox": _box(205, 40, 325, 55)},
        ],
    }
    return {"page": page, "text": ROSTER_TEXT, "ocr_meta": meta}


def _material(mid, *, subject_id=7, role="packet", file_name="交安B证.pdf", pages=None):
    return SimpleNamespace(id=mid, task_id=1, subject_id=subject_id, role=role,
                           file_name=file_name, parse_state="done",
                           pages_json=pages if pages is not None else [])


def _fact(fid, material_id, page, path, raw, normalized=None, quality="readable"):
    return SimpleNamespace(id=fid, material_id=material_id, page=page, field_path=path,
                           raw_value=raw, normalized_value=normalized, quality=quality,
                           quote=raw)


def _cert_facts(mid, *, page=1, name="陶瑜", company=COMPANY, ctype="B",
                number="沪交安B25G00206", quality="readable", base_id=100):
    facts = []
    if name is not None:
        facts.append(_fact(base_id, mid, page, "person.name", name))
    if company is not None:
        facts.append(_fact(base_id + 1, mid, page, "company.name", company))
    if ctype is not None:
        facts.append(_fact(base_id + 2, mid, page, "certificate.type", ctype,
                           normalized=ctype, quality=quality))
    if number is not None:
        facts.append(_fact(base_id + 3, mid, page, "certificate.number", number,
                           quality=quality))
    return facts


def _build(materials, facts_by_material, subject_names=None):
    return build_person_certificate_relations(
        materials, facts_by_material,
        subject_names=subject_names if subject_names is not None else {7: COMPANY},
    )


def test_matched_when_name_subject_type_and_number_all_agree():
    roster = _material(1, file_name="管理人员名单.pdf", pages=[_roster_page()])
    cert = _material(2, pages=[{"page": 1, "text": CERT_TEXT}])
    relations = _build([roster, cert], {1: [], 2: _cert_facts(2)})
    assert len(relations) == 1
    relation = relations[0]
    assert relation["status"] == STATUS_MATCHED
    assert relation["person_key"] == "陶瑜"
    assert relation["post"] == "项目经理"
    assert relation["left"]["page"] == 1 and relation["right"]["page"] == 1
    assert {d["field"] for d in relation["dimensions"]} >= {
        "person.name", "company.name", "certificate.type", "certificate.number"}


def test_conflict_when_certificate_type_is_c_for_project_manager():
    roster = _material(1, file_name="管理人员名单.pdf", pages=[_roster_page()])
    cert = _material(2, pages=[{"page": 1, "text": CERT_TEXT}])
    relations = _build([roster, cert], {1: [], 2: _cert_facts(2, ctype="C")})
    relation = relations[0]
    assert relation["status"] == STATUS_CONFLICT
    type_dim = next(d for d in relation["dimensions"] if d["field"] == "certificate.type")
    assert type_dim["result"] == "conflict"
    assert type_dim["expected"] == "B" and type_dim["actual"] == "C"


def test_conflict_when_certificate_number_differs_from_roster():
    roster = _material(1, file_name="管理人员名单.pdf", pages=[_roster_page()])
    cert = _material(2, pages=[{"page": 1, "text": CERT_TEXT}])
    relations = _build([roster, cert], {1: [], 2: _cert_facts(2, number="沪交安B25G00999")})
    relation = relations[0]
    assert relation["status"] == STATUS_CONFLICT
    number_dim = next(d for d in relation["dimensions"] if d["field"] == "certificate.number")
    assert number_dim["result"] == "conflict"


def test_unresolved_when_certificate_holder_name_is_unreadable():
    roster = _material(1, file_name="管理人员名单.pdf", pages=[_roster_page()])
    cert = _material(2, pages=[{"page": 1, "text": "证书编号：沪交安C25G00301"}])
    relations = _build([roster, cert], {1: [], 2: _cert_facts(2, name=None, ctype="C",
                                                              number="沪交安C25G00301")})
    relation = relations[0]
    assert relation["status"] == STATUS_UNRESOLVED
    assert relation["right"] == {}
    assert "证书页" in relation["reason"]


def test_unresolved_when_two_certificates_for_same_person_disagree():
    roster = _material(1, file_name="管理人员名单.pdf", pages=[_roster_page()])
    cert = _material(2, pages=[{"page": 1, "text": CERT_TEXT}, {"page": 2, "text": CERT_TEXT}])
    facts = _cert_facts(2, page=1) + _cert_facts(2, page=2, base_id=200,
                                                 number="沪交安B25G00999")
    relations = _build([roster, cert], {1: [], 2: facts})
    relation = relations[0]
    assert relation["status"] == STATUS_UNRESOLVED
    assert "互斥" in relation["reason"]


def test_multiple_pages_of_same_certificate_merge_into_one_relation():
    roster = _material(1, file_name="管理人员名单.pdf", pages=[_roster_page()])
    cert = _material(2, pages=[{"page": 1, "text": CERT_TEXT}, {"page": 2, "text": CERT_TEXT}])
    facts = _cert_facts(2, page=1) + _cert_facts(2, page=2, base_id=200)
    relations = _build([roster, cert], {1: [], 2: facts})
    assert len(relations) == 1
    relation = relations[0]
    assert relation["status"] == STATUS_MATCHED
    assert relation["right"]["pages"] == [1, 2]
    assert len(relation["fact_ids"]) == 8


def test_unresolved_when_roster_has_no_comparable_certificate_number():
    # 无 OCR 布局 ⇒ 取不到名单侧证号，只能给人工核对线索，不得判 matched
    roster = _material(1, file_name="管理人员名单.pdf", pages=[_roster_page(with_layout=False)])
    cert = _material(2, pages=[{"page": 1, "text": CERT_TEXT}])
    relations = _build([roster, cert], {1: [], 2: _cert_facts(2)})
    relation = relations[0]
    assert relation["status"] == STATUS_UNRESOLVED
    number_dim = next(d for d in relation["dimensions"] if d["field"] == "certificate.number")
    assert number_dim["result"] == "unresolved"


def test_conflict_when_certificate_material_belongs_to_another_subject():
    roster = _material(1, file_name="管理人员名单.pdf", subject_id=7, pages=[_roster_page()])
    cert = _material(2, subject_id=8, pages=[{"page": 1, "text": CERT_TEXT}])
    relations = _build([roster, cert], {1: [], 2: _cert_facts(2)})
    relation = relations[0]
    subject_dim = next(d for d in relation["dimensions"] if d["field"] == "subject")
    assert subject_dim["result"] == "conflict"
    assert relation["status"] == STATUS_CONFLICT


def test_roster_page_itself_is_not_treated_as_certificate_page():
    roster = _material(1, file_name="管理人员名单.pdf", pages=[_roster_page()])
    relations = _build([roster], {1: _cert_facts(1)})
    assert relations and relations[0]["status"] == STATUS_UNRESOLVED
    assert relations[0]["right"] == {}


def test_no_relation_without_roster_record():
    cert = _material(2, pages=[{"page": 1, "text": CERT_TEXT}])
    assert _build([cert], {2: _cert_facts(2)}) == []


def test_unresolved_when_same_name_spans_two_subjects_in_roster():
    # 同名不等于同人：名单同名记录跨主体时不得择一，也不得继续字段比较
    first = _material(1, subject_id=7, file_name="管理人员名单.pdf", pages=[_roster_page()])
    second = _material(3, subject_id=8, file_name="管理人员名单2.pdf", pages=[_roster_page()])
    cert = _material(2, subject_id=7, pages=[{"page": 1, "text": CERT_TEXT}])
    relations = _build([first, second, cert], {1: [], 3: [], 2: _cert_facts(2)},
                       subject_names={7: COMPANY, 8: "另一家施工单位"})
    assert len(relations) == 1
    relation = relations[0]
    assert relation["status"] == STATUS_UNRESOLVED
    identity = next(d for d in relation["dimensions"] if d["field"] == "person.identity")
    assert identity["result"] == "unresolved"
    assert "同名" in identity["reason"]


def test_unresolved_when_same_name_certificate_pages_belong_to_different_subjects():
    roster = _material(1, subject_id=7, file_name="管理人员名单.pdf", pages=[_roster_page()])
    cert_a = _material(2, subject_id=7, pages=[{"page": 1, "text": CERT_TEXT}])
    cert_b = _material(3, subject_id=8, pages=[{"page": 1, "text": CERT_TEXT}])
    relations = _build(
        [roster, cert_a, cert_b],
        {1: [], 2: _cert_facts(2), 3: _cert_facts(3, base_id=200)},
        subject_names={7: COMPANY, 8: "另一家施工单位"},
    )
    assert len(relations) == 1
    relation = relations[0]
    assert relation["status"] == STATUS_UNRESOLVED
    assert any(d["field"] == "person.identity" for d in relation["dimensions"])


def test_unresolved_when_certificate_page_quality_is_review_required():
    # 字段一致但页面质量存疑：只能是待人工确认，不得直接 matched
    roster = _material(1, file_name="管理人员名单.pdf", pages=[_roster_page()])
    cert = _material(2, pages=[{"page": 1, "text": CERT_TEXT}])
    relations = _build([roster, cert], {1: [], 2: _cert_facts(2, quality="review_required")})
    relation = relations[0]
    assert relation["status"] == STATUS_UNRESOLVED
    quality = next(d for d in relation["dimensions"] if d["field"] == "evidence.quality")
    assert quality["result"] == "unresolved"
    assert relation["right"]["quality"] == "review_required"


def test_generation_lookup_is_attached_to_relation():
    roster = _material(1, file_name="管理人员名单.pdf", pages=[_roster_page()])
    cert = _material(2, pages=[{"page": 1, "text": CERT_TEXT}])
    relations = build_person_certificate_relations(
        [roster, cert], {1: [], 2: _cert_facts(2)}, subject_names={7: COMPANY},
        generation_lookup=lambda material: "g-%s" % material.id,
    )
    assert relations[0]["generation_id"] == "g-2"


def _engine(tmp_path):
    return create_engine("sqlite:///" + (tmp_path / "relations.sqlite").as_posix())


def _relation_dict(**overrides):
    base = {
        "task_id": 1, "subject_id": 7, "generation_id": "g-2",
        "relation_type": "person_certificate", "person_key": "陶瑜", "person_name": "陶瑜",
        "post": "项目经理", "status": STATUS_UNRESOLVED, "reason": "待核对",
        "dimensions": [], "left": {"material_id": 1, "page": 1},
        "right": {"material_id": 2, "page": 1}, "fact_ids": [101, 102],
        "resolver_version": "person-cert-relation.v1",
    }
    base.update(overrides)
    return base


def test_persist_is_idempotent_and_appends_new_version(tmp_path):
    engine = _engine(tmp_path)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(BsTask(id=1, conversation_id=1, title="关系任务", state="running",
                      created_by=1, created_by_type="web"))
        db.commit()
        first = persist_relations(db, [_relation_dict()])
        db.commit()
        again = persist_relations(db, [_relation_dict()])
        db.commit()
        assert len(first) == 1 and again == []
        assert db.query(BsEvidenceRelation).count() == 1
        # 事实行变化 ⇒ 指纹变化 ⇒ 追加新版本，旧行保留
        newer = persist_relations(db, [_relation_dict(fact_ids=[201, 202],
                                                      status=STATUS_MATCHED)])
        db.commit()
        assert len(newer) == 1
        rows = db.query(BsEvidenceRelation).order_by(BsEvidenceRelation.id).all()
        assert [row.status for row in rows] == [STATUS_UNRESOLVED, STATUS_MATCHED]
        assert rows[0].generation_id == "g-2" and rows[1].generation_id == "g-2"


def test_latest_relations_returns_last_row_per_person(tmp_path):
    engine = _engine(tmp_path)
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(BsTask(id=1, conversation_id=1, title="关系任务", state="running",
                      created_by=1, created_by_type="web"))
        db.commit()
        persist_relations(db, [_relation_dict(),
                               _relation_dict(fact_ids=[301], status=STATUS_MATCHED,
                                              person_key="李四", person_name="李四")])
        persist_relations(db, [_relation_dict(fact_ids=[401], status=STATUS_CONFLICT)])
        db.commit()
        latest = {row.person_key: row.status for row in latest_relations(db, 1)}
        assert latest == {"陶瑜": STATUS_CONFLICT, "李四": STATUS_MATCHED}