"""阅读门禁（设计 2.5）：未读/可疑页不得支撑「符合」结论。"""
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.standalone.identity import Base
from app.plus.blockchain_supervision import reading_gate
from app.plus.blockchain_supervision.models import (
    STATUS_HIT, STATUS_MISS, STATUS_NOT_APPLICABLE, STATUS_NOT_INCLUDED,
    BsFinding, BsMaterial, BsTask)
from app.plus.blockchain_supervision.service import _replace_findings

READABLE_PAGES = [{"page": 1, "text": "合同正文"}, {"page": 2, "text": "签署页"}]
UNREAD_RECORD = [{"kind": "parse_incomplete", "total_pages": 2,
                  "read_pages": [1], "unread_pages": [2]}]


def _material(material_id=1, *, pages=None, degradations=None, parse_state="done",
              file_name="分包合同.pdf"):
    return SimpleNamespace(id=material_id, file_name=file_name, parse_state=parse_state,
                           pages_json=READABLE_PAGES if pages is None else pages,
                           degradations_json=degradations or [])


def _finding(**kwargs):
    values = {"status": STATUS_MISS, "material_id": 1, "page": 1,
              "evidence_json": [], "message": "未发现整体转包"}
    values.update(kwargs)
    return SimpleNamespace(**values)


def test_readable_page_keeps_conforming_finding():
    finding = _finding()
    assert reading_gate.gate_reading_findings([finding], [_material()]) == []
    assert finding.status == STATUS_MISS


def test_unread_page_downgrades_conforming_finding_to_missing():
    finding = _finding(page=2)
    downgraded = reading_gate.gate_reading_findings(
        [finding], [_material(degradations=UNREAD_RECORD)])
    assert downgraded == [finding]
    assert finding.status == STATUS_NOT_INCLUDED
    assert "第2页未读" in finding.message
    assert "缺项" in finding.message


def test_unread_evidence_page_downgrades_material_level_finding():
    finding = _finding(page=None, evidence_json=[{"material_id": 1, "page": 2, "quote": "原文"}])
    reading_gate.gate_reading_findings([finding], [_material(degradations=UNREAD_RECORD)])
    assert finding.status == STATUS_NOT_INCLUDED


def test_whole_material_unreadable_downgrades_material_level_finding():
    finding = _finding(page=None, material_id=2)
    reading_gate.gate_reading_findings(
        [finding], [_material(2, pages=[], degradations=[{"kind": "no_text_layer"}])])
    assert finding.status == STATUS_NOT_INCLUDED
    assert "整份材料读取不完整" in finding.message


def test_non_conforming_and_not_applicable_findings_are_untouched():
    hit = _finding(status=STATUS_HIT, page=2)
    not_applicable = _finding(status=STATUS_NOT_APPLICABLE, page=2)
    reading_gate.gate_reading_findings(
        [hit, not_applicable], [_material(degradations=UNREAD_RECORD)])
    assert hit.status == STATUS_HIT
    assert not_applicable.status == STATUS_NOT_APPLICABLE


def test_unknown_material_reference_is_skipped():
    finding = _finding(material_id=99, page=2)
    assert reading_gate.gate_reading_findings([finding], [_material()]) == []
    assert finding.status == STATUS_MISS


def test_replace_findings_applies_reading_gate_before_persisting():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        task = BsTask(id=3, conversation_id=3, title="阅读门禁", created_by=1,
                      created_by_type="web", options_snapshot_json={})
        db.add(task)
        db.add(BsMaterial(id=11, task_id=3, file_name="分包合同.pdf", role="sub",
                          parse_state="done",
                          pages_json=[{"page": 1, "text": "合同正文"}, {"page": 2, "text": ""}],
                          degradations_json=UNREAD_RECORD))
        db.commit()
        finding = BsFinding(task_id=3, item_id="W16-01", kind="illegal_subcontract",
                            status=STATUS_MISS, material_id=11, page=2,
                            message="未发现整体转包")
        _replace_findings(db, task, [finding])
        row = db.query(BsFinding).filter_by(task_id=3).one()
        assert row.status == STATUS_NOT_INCLUDED
        assert "第2页未读" in row.message
