"""Public template endpoints retain frozen history, authorization and gates."""
from copy import deepcopy

from sqlalchemy.orm import Session

from app.plus.blockchain_supervision.models import BsMaterial, BsReviewVersion
from app.plus.blockchain_supervision import report_template_v13
from test_bs_report_version_http import _client, _seed_version
from test_report_template_v13 import sample_report


def test_current_builders_and_new_review_snapshot_freeze_same_projection(tmp_path):
    from app.plus.blockchain_supervision import reporting, service
    from app.plus.blockchain_supervision.models import BsSubject, BsTask
    _, engine, task_id = _client(tmp_path)
    with Session(engine) as db:
        task = db.get(BsTask, task_id)
        db.add(BsSubject(task_id=task_id, stable_code="v13-one", name="隔离测试主体",
                         subject_type="professional", type_confirmed=True))
        db.commit()
        legacy = reporting.build_report(db, task)
        current = reporting.build_report(db, task, include_templates=True)
        opinion = reporting.build_opinion(db, task, include_templates=True)
        assert "template_documents" not in legacy
        assert current["template_documents"]["v1.3"]["available"] is True
        assert current["template_documents"]["v1.3"]["statistics"] == opinion["template_documents"]["v1.3"]["statistics"]
        snapshot = service._review_version_snapshot(db, task, trigger="completed")
        assert snapshot["report"]["template_documents"]["v1.3"]["available"] is True
        assert snapshot["opinion"]["template_documents"]["v1.3"]["available"] is True


def test_old_snapshot_stays_legacy_and_template_does_not_bypass_gate(tmp_path):
    client, engine, task_id = _client(tmp_path)
    _seed_version(engine, task_id)
    root = f"/api/plus/blockchain-supervision/tasks/{task_id}"
    legacy = client.get(root + "/report?version_no=1")
    assert legacy.status_code == 200
    assert "template_document" not in legacy.json()
    unavailable = client.get(root + "/report?version_no=1&template_id=v1.3")
    assert unavailable.status_code == 409 and "未冻结" in unavailable.text
    assert client.get(root + "/report?version_no=1&template_id=other").status_code == 400
    for path in ("/report?stage=confirmed", "/report/export?stage=confirmed", "/opinion?stage=confirmed",
                 "/opinion/export?stage=confirmed", "/opinion/preview?stage=confirmed"):
        assert client.get(root + path + "&template_id=v1.3").status_code == 409
    assert client.post("/api/login", json={"username": "bob", "password": "Bob-pass-123"}).status_code == 200
    assert client.get(root + "/report?version_no=1&template_id=v1.3").status_code in (403, 404)


def test_new_frozen_template_round_trip_preview_export_and_digest(tmp_path):
    client, engine, task_id = _client(tmp_path)
    material_id = _seed_version(engine, task_id)
    frozen = sample_report()
    document = report_template_v13.build_document(frozen)
    with Session(engine) as db:
        version = db.query(BsReviewVersion).filter_by(task_id=task_id, version_no=1).one()
        snapshot = deepcopy(version.snapshot_json)
        for kind in ("report", "opinion"):
            snapshot[kind] = {"meta": frozen["meta"], "business_document": frozen["business_document"],
                              "template_documents": {"v1.3": document}}
        version.snapshot_json = snapshot
        db.get(BsMaterial, material_id).file_name = "后来修改的材料.pdf"
        db.commit()
    root = f"/api/plus/blockchain-supervision/tasks/{task_id}"
    query = "version_no=1&stage=confirmed&template_id=v1.3"
    report = client.get(root + "/report?" + query)
    opinion = client.get(root + "/opinion?" + query)
    assert report.status_code == opinion.status_code == 200
    assert report.json()["template_document"] == opinion.json()["template_document"] == document
    assert "后来修改的材料" not in str(report.json()["template_document"])
    digest = opinion.json()["opinion_digest"]
    markdown = client.get(root + "/opinion/preview-markdown?" + query)
    assert markdown.status_code == 200 and markdown.json()["opinion_digest"] == digest
    preview = client.get(root + "/opinion/preview?" + query)
    assert preview.status_code == 200 and preview.headers["X-Opinion-Digest"] == digest
    for kind in ("report", "opinion"):
        for fmt, signature in (("docx", b"PK"), ("pdf", b"%PDF")):
            response = client.get(root + f"/{kind}/export?{query}&fmt={fmt}&expected_digest={digest}")
            assert response.status_code == 200, response.text if response.status_code != 200 else ""
            assert response.content.startswith(signature)
    assert client.get(root + "/opinion/export?" + query + "&expected_digest=stale").status_code == 409
    legacy = client.get(root + "/report?version_no=1")
    assert legacy.status_code == 200 and "template_document" not in legacy.json() and "template_documents" not in legacy.json()
    with Session(engine) as db:
        assert db.query(BsReviewVersion).filter_by(task_id=task_id, version_no=1).one().snapshot_json == snapshot
