"""F1：材料归属角色必须使用统一白名单。"""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
import pytest

from app.plus.blockchain_supervision.classify import validate_role
from app.plus.blockchain_supervision.models import BsMaterial
from app.plus.blockchain_supervision import service
from app.standalone.identity import Base, create_account
from app.standalone.main import create_app


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch):
    """HTTP 上传路由会读取独立配置；每个测试显式提供合成密钥。"""
    monkeypatch.setenv("SECRET_KEY", "x" * 64)
    monkeypatch.setenv("BLOCKCHAIN_SUPERVISION_UPLOAD_MAX_MB", "200")


def _client(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "role.sqlite").as_posix(),
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    create_account(engine, "alice", "Alice-pass-123")
    client = TestClient(create_app(engine, "x" * 64, cookie_secure=False))
    assert client.post("/api/login", json={"username": "alice", "password": "Alice-pass-123"}).status_code == 200
    conversation = client.post("/api/conversations").json()["id"]
    task = client.post("/api/plus/blockchain-supervision/tasks", json={
        "conversation_id": conversation, "title": "role-whitelist", "scope_a1": True,
    })
    assert task.status_code == 200, task.text
    return client, engine, task.json()["task_id"]


def test_validate_role_allows_auto_classification_and_known_roles():
    assert validate_role(None) is None
    assert validate_role("labor") == "labor"
    assert validate_role("unknown") == "unknown"


def test_validate_role_rejects_unknown_role():
    with pytest.raises(ValueError, match="未知归属类型"):
        validate_role("bogus")


@pytest.mark.parametrize("operation", ["add", "ingest", "zip"])
def test_service_paths_reject_unknown_role_before_persistence(operation):
    with pytest.raises(ValueError, match="未知归属类型"):
        if operation == "add":
            service.add_material(None, None, file_name="x.pdf", role="bogus")
        elif operation == "ingest":
            service.ingest_material(None, None, file_name="x.pdf", content=b"", role="bogus")
        else:
            service.store_zip_container(None, None, file_name="x.zip", content=b"", role="bogus")


def test_upload_unknown_role_returns_400_without_material_row(tmp_path):
    client, engine, task_id = _client(tmp_path)
    response = client.post(
        f"/api/plus/blockchain-supervision/tasks/{task_id}/materials/upload",
        files={"file": ("fake.pdf", b"%PDF-1.4", "application/pdf")},
        data={"role": "bogus"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "未知归属类型"
    with Session(engine) as db:
        assert db.query(BsMaterial).filter_by(task_id=task_id).count() == 0


def test_upload_fake_pdf_header_returns_400_without_material_row(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "x" * 64)
    client, engine, task_id = _client(tmp_path)
    response = client.post(
        f"/api/plus/blockchain-supervision/tasks/{task_id}/materials/upload",
        files={"file": ("fake.pdf", b"plain text", "application/pdf")},
    )
    assert response.status_code == 400
    assert "PDF" in response.json()["detail"]
    with Session(engine) as db:
        assert db.query(BsMaterial).filter_by(task_id=task_id).count() == 0


def test_upload_empty_file_returns_400_without_material_row(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "x" * 64)
    client, engine, task_id = _client(tmp_path)
    response = client.post(
        f"/api/plus/blockchain-supervision/tasks/{task_id}/materials/upload",
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "空文件"
    with Session(engine) as db:
        assert db.query(BsMaterial).filter_by(task_id=task_id).count() == 0


def test_upload_invalid_zip_returns_400_without_material_row(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "x" * 64)
    client, engine, task_id = _client(tmp_path)
    response = client.post(
        f"/api/plus/blockchain-supervision/tasks/{task_id}/materials/upload",
        files={"file": ("broken.zip", b"not a zip", "application/zip")},
        data={"role": "packet"},
    )
    assert response.status_code == 400
    assert "ZIP" in response.json()["detail"]
    with Session(engine) as db:
        assert db.query(BsMaterial).filter_by(task_id=task_id).count() == 0


def _pdf_bytes():
    return b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n"


def test_upload_cross_task_subject_binding_returns_404(tmp_path):
    client, _engine, task_id = _client(tmp_path)
    first = client.post(
        f"/api/plus/blockchain-supervision/tasks/{task_id}/materials/upload",
        files={"file": ("first.pdf", _pdf_bytes(), "application/pdf")},
        data={"role": "labor"},
    )
    assert first.status_code == 200, first.text
    subject_id = client.get(f"/api/plus/blockchain-supervision/tasks/{task_id}/subjects").json()["subjects"][0]["subject_id"]
    conversation = client.post("/api/conversations").json()["id"]
    second_task = client.post("/api/plus/blockchain-supervision/tasks", json={
        "conversation_id": conversation, "title": "cross-task", "scope_a1": True,
    }).json()["task_id"]
    cross = client.post(
        f"/api/plus/blockchain-supervision/tasks/{second_task}/materials/upload",
        files={"file": ("cross.pdf", _pdf_bytes(), "application/pdf")},
        data={"role": "labor", "subject_id": str(subject_id)},
    )
    assert cross.status_code == 404


def test_replacement_role_drift_returns_409_and_keeps_old_material(tmp_path):
    client, engine, task_id = _client(tmp_path)
    uploaded = client.post(
        f"/api/plus/blockchain-supervision/tasks/{task_id}/materials/upload",
        files={"file": ("old.pdf", _pdf_bytes(), "application/pdf")},
        data={"role": "labor"},
    )
    assert uploaded.status_code == 200, uploaded.text
    old_id = uploaded.json()["material_id"]
    drift = client.post(
        f"/api/plus/blockchain-supervision/tasks/{task_id}/materials/upload",
        files={"file": ("new.pdf", _pdf_bytes(), "application/pdf")},
        data={"role": "sub", "replaces_material_id": str(old_id)},
    )
    assert drift.status_code == 409
    with Session(engine) as db:
        old = db.get(BsMaterial, old_id)
        assert old is not None and old.superseded_by_id is None
