"""合同主体证据必须从任务详情接口透传，供工作台抬头在报告未读时使用。"""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from app.plus.blockchain_supervision.models import BsMaterial, BsSubject, BsTask
from app.standalone.identity import Base, ConversationMapping, create_account
from app.standalone.main import create_app


def test_task_detail_includes_contract_party_evidence(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "x" * 64)
    engine = create_engine("sqlite:///" + (tmp_path / "party-router.sqlite").as_posix(),
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    create_account(engine, "alice", "Alice-pass-123")
    client = TestClient(create_app(engine, "x" * 64, cookie_secure=False))
    assert client.post("/api/login", json={"username": "alice", "password": "Alice-pass-123"}).status_code == 200
    conversation_id = client.post("/api/conversations").json()["id"]
    task_id = client.post("/api/plus/blockchain-supervision/tasks", json={
        "conversation_id": conversation_id,
        "title": "contract-party-detail",
        "scope_a1": True,
    }).json()["task_id"]
    with Session(engine) as db:
        task = db.get(BsTask, task_id)
        subject = BsSubject(task_id=task_id, stable_code="S1", name="主体一", subject_type="labor")
        db.add(subject)
        db.flush()
        db.add(BsMaterial(
            task_id=task_id,
            subject_id=subject.id,
            material_category="general",
            file_name="general.pdf",
            role="general",
            role_confirmed=True,
            via="fixture",
            size_kb=1,
            pages=1,
            pages_json=[{"page": 2, "text": "发包人：建设单位公司；承包人：总承包单位公司。"}],
            parse_state="done",
            degradations_json=[],
            seal_json=[],
        ))
        db.commit()
        material = db.query(BsMaterial).filter_by(task_id=task_id).one()
    response = client.get(f"/api/plus/blockchain-supervision/tasks/{task_id}")
    assert response.status_code == 200, response.text
    party = response.json()["contract_party_evidence"]["general_contractor"]
    assert party["status"] == "matched", response.json()
    assert party["company"] == "总承包单位公司"
    assert party["evidence"][0]["page"] == 2
