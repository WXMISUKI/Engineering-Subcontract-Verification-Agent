"""合成回放：人员证据关系经过 A2-05 投影后进入业务报告。"""

from types import SimpleNamespace
from io import BytesIO

import fitz
import pytest
from docx import Document
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.plus.blockchain_supervision import export, reporting
from app.plus.blockchain_supervision.models import BsTask
from app.plus.blockchain_supervision.models import BsFinding, BsReviewVersion, BsSubject
from app.plus.blockchain_supervision.evidence_projection import project_person_certificate_relations
from app.plus.blockchain_supervision.report_document import build_report_document, _point_verdict
from app.plus.blockchain_supervision.reporting import report_gate_blocking
from app.plus.blockchain_supervision import report_policy
from app.plus.blockchain_supervision.relation_resolution import persist_relations
from app.plus.blockchain_supervision.service import _replace_findings
from app.plus.blockchain_supervision import a2_check, signature_chain
from datetime import date
from app.plus.blockchain_supervision.router import _require_no_pending_hits
from fastapi import HTTPException
from test_bs_report_version_http import _client, _seed_version
from test_bs_person_certificate_relations import COMPANY, _build, _cert_facts, _cert_page, _material, _roster_page
from test_report_document import _report


def test_structured_ocr_date_and_form_clues_reach_both_exports():
    form = '劳务分包报审表\n总监理工程师意见：\n意见文字\n指挥部意见：\n意见文字'
    cert = ('```json\n{"证照标题":"<576,302,57,299,90>,安全生产许可证",'
            '"营业期限":"<554,576,194,20,0>,2023年03月31日至2026年03月31日"}\n```')
    material = SimpleNamespace(id=5, file_name='报审包.pdf', role='packet', parse_state='done',
        pages_json=[{'page': 1, 'text': form}, {'page': 5, 'text': cert}],
        seal_json=[], subject_id=1)
    task = SimpleNamespace(id=1)
    signature = next(f for f in signature_chain.run_signature_chain_checks(task, [material])
                     if f.item_id == 'A3-02-group')
    validity = next(f for f in a2_check.run_cert_validity_check(task, [material],
                    baseline=date(2026, 11, 11)) if f.page == 5)
    def project(f, label):
        return {'kind': f.kind, 'item_id': f.item_id, 'status': f.status, 'label': label,
                'message': f.message, 'source': f.source, 'subject_id': f.subject_id,
                'evidence_list': f.evidence_json or [], 'human_disposition': None}
    report = _report([project(signature, '签字盖章及审批资料'), project(validity, '证件有效期')],
                     selected=['signature_chain', 'a2'], completed=['signature_chain', 'a2'])
    report['meta'].update({'title': '局部核查验证', 'scope': {}})
    report['business_document'] = build_report_document(report)
    word = Document(BytesIO(export.word_bytes(report)))
    word_text = '\n'.join(cell.text for table in word.tables for row in table.rows for cell in row.cells)
    pdf = fitz.open(stream=export.pdf_bytes(report), filetype='pdf')
    pdf_text = '\n'.join(page.get_text() for page in pdf)
    for text in (word_text, pdf_text):
        compact = ''.join(text.split())
        assert '总监理工程师意见' in compact and '指挥部意见' in compact
        assert '2026-03-31' in compact and '核对最新证照' in compact


def test_candidate_report_projection_uses_original_source_kind(tmp_path, monkeypatch):
    _, engine, task_id = _client(tmp_path)
    monkeypatch.setattr(reporting.ai_advisory_snapshots, 'filter_current_candidate_findings',
                        lambda _db, _task, _user, rows: rows)
    with Session(engine) as db:
        task = db.get(BsTask, task_id)
        db.add(BsFinding(task_id=task_id, item_id='NL-B-08', kind='negative_candidate',
            status='undecidable', message='模型推测了未核实的施工工艺', source='负面清单辅助核查',
            evidence_json=[{'material_id': 1, 'page': 2, 'quote': '预制板梁劳务分包工程量清单'}],
            review_json={'decision': 'accepted', 'note': '请核对工艺资料'}))
        db.commit()
        report = reporting.build_report(db, task)
    item = next(item for section in report['sections'] for item in section['items']
                if item['item_id'] == 'NL-B-08')
    assert item['source_kind'] == 'negative_candidate'
    point = next(p for p in report['business_document']['sections'][1]['key_points'] if p['label'] == '负面清单')
    assert '预制板梁劳务分包工程量清单' in point['summary']
    assert '未核实的施工工艺' not in point['summary']
    assert '请核对工艺资料' in point['summary']
    word = Document(BytesIO(export.word_bytes(report)))
    word_text = ''.join(cell.text for table in word.tables for row in table.rows for cell in row.cells)
    pdf = fitz.open(stream=export.pdf_bytes(report), filetype='pdf')
    pdf_text = ''.join(''.join(page.get_text().split()) for page in pdf)
    for text in (word_text, pdf_text):
        assert '预制板梁劳务分包工程量清单' in text and '请核对工艺资料' in text
        assert '未核实的施工工艺' not in text


@pytest.mark.parametrize(
    ("number", "quality", "name", "company", "cert_subject", "expected_status", "expected_verdict"),
    [("沪交安B25G00999", "readable", "陶瑜", COMPANY, 7, "hit", "不符合"),
     (None, "review_required", "陶瑜", COMPANY, 7, "undecidable", "待核实"),
     ("沪交安B25G00999", "readable", None, COMPANY, 7, "undecidable", "待核实"),
     ("沪交安B25G00999", "readable", "陶瑜", None, 7, "hit", "待核实"),
     ("沪交安B25G00999", "readable", "陶瑜", COMPANY, 8, "hit", "待核实")],
)
def test_supplement_certificate_relation_to_report(tmp_path, number, quality, name, company, cert_subject,
                                                    expected_status, expected_verdict):
    original = _material(1, file_name="原报审材料.pdf", pages=[_roster_page(page=1)])
    page = _cert_page(number or "未读出", page=2)
    if name is None:
        page["text"] = "持证人姓名被遮挡\n证书编号：" + number
    elif company is None:
        page["text"] = "姓名：陶瑜\n企业名称被遮挡\n证书编号：" + number
    page["ocr_meta"]["quality"] = "ocr_ok" if quality == "readable" else "review_required"
    supplement = _material(2, subject_id=cert_subject, file_name="补件证书.pdf", pages=[page])
    relation = _build(
        [original, supplement], {1: [], 2: _cert_facts(2, page=2, name=name, company=company,
                                                       number=number, quality=quality)},
        generation_lookup=lambda material: {"generation_id": "g-2", "sequence": 10} if material.id == 2 else None,
    )[0]
    engine = create_engine("sqlite:///" + (tmp_path / "replay.sqlite").as_posix())
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(BsTask(id=1, conversation_id=1, title="合成回放", state="running",
                      created_by=1, created_by_type="web"))
        db.commit()
        rows = persist_relations(db, [relation])
        db.commit()
        finding = project_person_certificate_relations(db, SimpleNamespace(id=1),
                                                        [original, supplement], relations=rows)[0]
    assert finding.status == expected_status
    assert report_gate_blocking(finding) is (expected_status == "undecidable")
    if expected_verdict == "不符合":
        assert {ref["role"] for ref in finding.evidence_json} == {"support", "contradiction"}
        assert {ref["material_id"] for ref in finding.evidence_json} == {1, 2}
    item = {"kind": finding.kind, "item_id": finding.item_id, "subject_id": finding.subject_id,
            "label": "项目经理交安B证编号一致性", "status": finding.status,
            "message": finding.message, "source": finding.source,
            "evidence_list": finding.evidence_json, "human_disposition": None}
    report = _report([item], selected=["a2"], completed=["a2"])
    report["reference_snapshot"] = {"report_policy": report_policy.freeze_policy(report_policy.load_current_policy())}
    report["checklist_summary"] = {"subjects": [{"subject_id": 7, "status": "ready", "type_confirmed": True,
                                                    "subject_type": "labor", "checklist_items": []}]}
    report["meta"].update({"title": "合成补件回放", "scope": {}})
    report["business_document"] = build_report_document(report)
    point = next(row for row in report["business_document"]["sections"][2]["key_points"]
                 if row["label"] == "人员与证件对应")
    assert point["verdict"] == expected_verdict
    assert finding.source in point["basis"]
    word = Document(BytesIO(export.word_bytes(report)))
    word_text = "\n".join(cell.text for table in word.tables for row in table.rows for cell in row.cells)
    pdf = fitz.open(stream=export.pdf_bytes(report), filetype="pdf")
    pdf_text = "\n".join(page.get_text() for page in pdf)
    assert expected_verdict in word_text and expected_verdict in pdf_text
    assert "人员与证件对应" in word_text and "人员与证件对应" in pdf_text


def test_number_conflict_requires_frozen_policy_and_both_locatable_sides():
    policy = report_policy.load_current_policy()
    old = {**policy, "rules": [rule for rule in policy["rules"] if rule["item_id"] != "A2-05"]}
    support = {"role": "support", "conflict_fields": ["certificate.number"], "material_id": 1,
               "page": 1, "location_mode": "page", "quote": "名单证号"}
    contradiction = {"role": "contradiction", "conflict_fields": ["certificate.number"], "material_id": 2,
                     "page": 2, "location_mode": "page", "quote": "证书证号"}
    item = {"kind": "cert_validity", "item_id": "A2-05", "status": "hit", "subject_id": 7,
            "source": "证号一致性核查", "evidence_list": [support, contradiction]}
    unknown = {"kind": "cert_validity", "item_id": "A2-05", "status": "undecidable"}
    check = lambda rows, rules, types={7: "labor"}: _point_verdict(rows, True, False, policy=rules,
                                                                   subject_types=types)
    assert check([item, unknown], policy) == "不符合"
    assert _point_verdict([item, unknown], True, True, policy=policy, subject_types={7: "labor"}) == "不符合"
    assert check([{**item, "reading_limited": True}], policy) == "待核实"
    assert check([item], old) == "待核实"
    assert check([item], policy, {}) == "待核实"
    assert check([{**item, "evidence_list": [support]}], policy) == "待核实"
    assert check([{**item, "evidence_list": [support, {**contradiction, "quality": "review_required"}]}],
                 policy) == "待核实"
    assert check([{**item, "human_disposition": {"code": "accepted", "note": "已补充说明"}}],
                 policy) == "待核实"


def test_changed_supplement_invalidates_review_without_rewriting_history(tmp_path):
    client, engine, task_id = _client(tmp_path)
    _seed_version(engine, task_id)
    with Session(engine) as db:
        task = db.get(BsTask, task_id)
        db.add(BsSubject(id=7, task_id=task_id, stable_code="synthetic", name="合成主体", subject_type="labor"))
        snapshot = db.query(BsReviewVersion).filter_by(task_id=task_id, version_no=1).one()
        saved = {"meta": {"title": "历史工程", "scope": {}},
                 "business_document": {"basic_information": {"工程名称": "历史工程"}, "sections": [],
                 "issues": [{"category": "人员核查", "label": "历史意见", "status": "hit",
                             "human_disposition": {"code": "accepted", "note": "历史备注"}}]}}
        snapshot.snapshot_json = {**snapshot.snapshot_json, "report": saved}
        task.options_snapshot_json = {"rerun": {"scope": "items", "item_ids": ["A2-05"], "carry": [{
            "subject_id": 7, "item_id": "A2-05", "material_id": 10, "page": 3,
            "status": "hit", "message": "旧结论", "quote": "旧原文", "source": "旧依据",
            "evidence_json": [{"material_id": 10, "page": 3, "quote": "旧原文"}],
            "review_json": {"decision": "accepted", "note": "历史备注"}}]}}
        db.add(BsFinding(task_id=task_id, subject_id=7, item_id="A2-06", kind="cert_validity",
                         status="undecidable", message="无关事项", review_json={"decision": "ignored", "note": "保留"}))
        db.commit()
        new = BsFinding(task_id=task_id, subject_id=7, item_id="A2-05", kind="cert_validity",
                        material_id=10, page=3, status="undecidable", message="补件证号无法核实",
                        quote="补件原文", source="证号一致性核查")
        _replace_findings(db, task, [new])
        current = db.query(BsFinding).filter_by(task_id=task_id, item_id="A2-05").one()
        other = db.query(BsFinding).filter_by(task_id=task_id, item_id="A2-06").one()
        assert current.review_json["invalidated"]["previous"]["note"] == "历史备注"
        assert other.review_json["note"] == "保留"
        with pytest.raises(HTTPException) as blocked:
            _require_no_pending_hits(db, task_id, "confirmed")
        assert blocked.value.status_code == 409
        assert db.query(BsReviewVersion).filter_by(task_id=task_id, version_no=1).one().snapshot_json["report"] == saved
        task.options_snapshot_json = {**task.options_snapshot_json, "supplement_publication": {
            "state": "candidate", "candidate_generation_id": "synthetic-g2", "baseline_version_no": 1}}
        db.commit()
    current_url = f"/api/plus/blockchain-supervision/tasks/{task_id}/report"
    candidate = client.get(current_url)
    assert candidate.status_code == 409 and candidate.json()["detail"]["code"] == "supplement_candidate_unpublished"
    with Session(engine) as db:
        task = db.get(BsTask, task_id)
        task.options_snapshot_json = {**task.options_snapshot_json, "supplement_publication": {
            "state": "failed", "candidate_generation_id": "synthetic-g2", "baseline_version_no": 1}}
        db.commit()
    failed = client.get(current_url)
    assert failed.status_code == 409 and failed.json()["detail"]["code"] == "supplement_failed"
    url = f"/api/plus/blockchain-supervision/tasks/{task_id}/report/export"
    for fmt in ("docx", "pdf"):
        response = client.get(url, params={"version_no": 1, "fmt": fmt})
        assert response.status_code == 200, response.text
        if fmt == "docx":
            doc = Document(BytesIO(response.content))
            text = "\n".join([p.text for p in doc.paragraphs] + [c.text for t in doc.tables for r in t.rows for c in r.cells])
        else:
            pdf = fitz.open(stream=response.content, filetype="pdf")
            text = "\n".join(page.get_text() for page in pdf)
        assert "历史备注" in text and "补件证号无法核实" not in text
    assert client.post("/api/login", json={"username": "bob", "password": "Bob-pass-123"}).status_code == 200
    assert client.get(url, params={"version_no": 1, "fmt": "pdf"}).status_code in (403, 404)
