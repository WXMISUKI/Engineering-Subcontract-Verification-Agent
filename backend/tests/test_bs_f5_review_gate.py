"""F5 人工复核回执与报告门禁的最小 HTTP 契约。"""

from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from app.plus.blockchain_supervision.models import BsFinding, BsTask
from app.plus.blockchain_supervision import reporting
from app.standalone.identity import Base, create_account
from app.standalone.main import create_app


def _client(tmp_path):
    engine = create_engine(
        "sqlite:///" + (tmp_path / "f5-review.sqlite").as_posix(),
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    create_account(engine, "reviewer", "Reviewer-pass-123")
    client = TestClient(create_app(engine, "x" * 64, cookie_secure=False))
    assert client.post(
        "/api/login",
        json={"username": "reviewer", "password": "Reviewer-pass-123"},
    ).status_code == 200
    conversation_id = client.post("/api/conversations").json()["id"]
    task_id = client.post(
        "/api/plus/blockchain-supervision/tasks",
        json={"conversation_id": conversation_id, "title": "f5-review-gate", "scope_a1": True},
    ).json()["task_id"]
    with Session(engine) as db:
        task = db.get(BsTask, task_id)
        task.state = "running"
        finding = BsFinding(
            task_id=task_id,
            item_id="T15-01",
            kind="completeness",
            status="undecidable",
            message="需要人工核对证据。",
            quote="合同范围",
            page=1,
            location_mode="precise",
            evidence_json=[{"material_id": 1, "page": 1, "anchor": {"status": "precise"}}],
            source="测试依据",
        )
        db.add(finding)
        db.commit()
        finding_id = finding.id
    return client, engine, finding_id


def test_review_requires_reason_for_ignore_and_keeps_auditable_decision(tmp_path):
    client, engine, finding_id = _client(tmp_path)
    endpoint = f"/api/plus/blockchain-supervision/findings/{finding_id}/review"

    missing_reason = client.patch(endpoint, json={"decision": "ignored"})
    assert missing_reason.status_code == 400
    with Session(engine) as db:
        assert db.get(BsFinding, finding_id).review_json is None

    accepted = client.patch(
        endpoint,
        json={"decision": "confirm", "note": "已核对原件，采纳系统结论"},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["review"]["decision"] == "accepted"
    assert accepted.json()["review"]["note"] == "已核对原件，采纳系统结论"

    ignored = client.patch(
        endpoint,
        json={"decision": "ignored", "note": "证据不足，暂不采纳"},
    )
    assert ignored.status_code == 200, ignored.text
    assert ignored.json()["review"]["decision"] == "ignored"
    with Session(engine) as db:
        review = db.get(BsFinding, finding_id).review_json
        assert review["decision"] == "ignored"
        assert review["note"] == "证据不足，暂不采纳"


def test_invalidated_review_is_unprocessed_but_retains_reason():
    review = {
        "invalidated": {"reason": "supplement_affected", "previous": {"decision": "accepted"}},
    }
    dto = reporting.review_dto(review)
    disposition = reporting.human_disposition(review)
    assert dto["decision"] is None
    assert dto["invalidated"]["reason"] == "supplement_affected"
    assert disposition["code"] == "unprocessed"
    assert disposition["invalidated"]["reason"] == "supplement_affected"


def test_report_review_blocking_excludes_open_term_but_blocks_unresolved_finding():
    open_term = SimpleNamespace(item_id="T15-10", status="undecidable", evidence_json=[])
    unresolved = SimpleNamespace(item_id="T15-01", status="undecidable", evidence_json=[])
    assert reporting.report_review_blocking(open_term) is False
    assert reporting.report_review_blocking(unresolved) is True
