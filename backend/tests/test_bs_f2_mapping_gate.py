"""F2：知识映射缺失时，替换候选不能覆盖旧基线。"""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from app.plus.blockchain_supervision.models import BsMaterial, BsTask
from app.standalone.identity import Base, create_account
from app.standalone.main import create_app


def _pdf_bytes():
    return b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n"


def test_missing_reference_mapping_keeps_replacement_baseline(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "x" * 64)
    monkeypatch.setenv("BLOCKCHAIN_SUPERVISION_UPLOAD_MAX_MB", "200")
    monkeypatch.setenv("BS_REFERENCE_MAPPING_FILE", str(tmp_path / "missing-reference-mapping.json"))
    engine = create_engine("sqlite:///" + (tmp_path / "mapping-gate.sqlite").as_posix(),
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    create_account(engine, "alice", "Alice-pass-123")
    client = TestClient(create_app(engine, "x" * 64, cookie_secure=False))
    assert client.post("/api/login", json={"username": "alice", "password": "Alice-pass-123"}).status_code == 200
    conversation_id = client.post("/api/conversations").json()["id"]
    task_id = client.post("/api/plus/blockchain-supervision/tasks", json={
        "conversation_id": conversation_id,
        "title": "f2-mapping-gate",
        "scope_a1": True,
    }).json()["task_id"]
    base = f"/api/plus/blockchain-supervision/tasks/{task_id}"
    uploaded = client.post(
        f"{base}/materials/upload",
        files={"file": ("baseline.pdf", _pdf_bytes(), "application/pdf")},
        data={"role": "labor"},
    )
    assert uploaded.status_code == 200, uploaded.text
    old_id = uploaded.json()["material_id"]
    subject_id = client.get(f"{base}/subjects").json()["subjects"][0]["subject_id"]
    assert client.patch(f"{base}/subjects/{subject_id}/type", json={"subject_type": "labor"}).status_code == 200
    candidate = client.post(
        f"{base}/materials/upload",
        files={"file": ("replacement.pdf", _pdf_bytes(), "application/pdf")},
        data={"role": "labor", "replaces_material_id": str(old_id)},
    )
    assert candidate.status_code == 200, candidate.text
    candidate_id = candidate.json()["material_id"]

    started = client.post(f"{base}/start")
    assert started.status_code == 503, started.text
    assert started.json()["detail"] == "知识库依据无法核实，请联系管理员完成知识初始化"
    with Session(engine) as db:
        task = db.get(BsTask, task_id)
        old = db.get(BsMaterial, old_id)
        replacement = db.get(BsMaterial, candidate_id)
        assert task.state == "setup"
        assert old.superseded_by_id is None
        assert replacement.replacement_target_id == old_id
