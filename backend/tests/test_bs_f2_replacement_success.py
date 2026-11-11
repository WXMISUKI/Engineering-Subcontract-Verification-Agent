"""F2：同角色替换完成解析后，CAS 正确收口并保留旧版本。"""

import json
import time
from pathlib import Path

import fitz
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.plus.blockchain_supervision.models import BsMaterial, BsTask
from app.plus.blockchain_supervision import parsing
from app.standalone.identity import Base, create_account
from app.standalone.main import create_app


def _pdf(text: str) -> bytes:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    payload = doc.tobytes()
    doc.close()
    return payload


def test_same_role_replacement_commits_superseded_after_parse(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "synthetic-secret-key-0123456789-abcdefgh")
    reference = Path(__file__).parent.parent / "app/plus/blockchain_supervision/data/reference_package.json"
    sources = json.loads(reference.read_text(encoding="utf-8"))["sources"]
    mapping = {
        "version": 1,
        "documents": [
            {"sha256": digest, "kb_id": "synthetic-kb", "knowledge_id": "synthetic-" + digest,
             "collection": "road_national_basis"}
            for digest in {row["original_sha256"] for row in sources}
        ],
    }
    mapping_path = tmp_path / "reference-mapping.json"
    mapping_path.write_text(json.dumps(mapping), encoding="utf-8")
    monkeypatch.setenv("BS_REFERENCE_MAPPING_FILE", str(mapping_path))
    monkeypatch.setattr(parsing, "UPLOADS_DIR", str(tmp_path / "uploads"))

    engine = create_engine("sqlite:///" + (tmp_path / "replacement-success.sqlite").as_posix(),
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    create_account(engine, "alice", "Alice-pass-123")
    client = TestClient(create_app(engine, "x" * 64, cookie_secure=False))
    assert client.post("/api/login", json={"username": "alice", "password": "Alice-pass-123"}).status_code == 200
    conversation_id = client.post("/api/conversations").json()["id"]
    base = "/api/plus/blockchain-supervision"
    task_id = client.post(f"{base}/tasks", json={
        "conversation_id": conversation_id,
        "title": "f2-replacement-success",
        "scope_a1": True,
    }).json()["task_id"]

    baseline = client.post(
        f"{base}/tasks/{task_id}/materials/upload",
        files={"file": ("baseline.pdf", _pdf("BASELINE CONTRACT"), "application/pdf")},
        data={"role": "labor"},
    )
    assert baseline.status_code == 200, baseline.text
    old_id = baseline.json()["material_id"]
    subject_id = client.get(f"{base}/tasks/{task_id}/subjects").json()["subjects"][0]["subject_id"]
    assert client.patch(f"{base}/tasks/{task_id}/subjects/{subject_id}/type",
                        json={"subject_type": "labor"}).status_code == 200

    replacement = client.post(
        f"{base}/tasks/{task_id}/materials/upload",
        files={"file": ("replacement.pdf", _pdf("REPLACEMENT CONTRACT"), "application/pdf")},
        data={"role": "labor", "replaces_material_id": str(old_id)},
    )
    assert replacement.status_code == 200, replacement.text
    replacement_id = replacement.json()["material_id"]

    started = client.post(f"{base}/tasks/{task_id}/start")
    assert started.status_code in (200, 202), started.text
    for _ in range(200):
        with Session(engine) as db:
            old = db.get(BsMaterial, old_id)
            current = db.get(BsMaterial, replacement_id)
            state = db.get(BsTask, task_id).state
            if current.parse_state in {"done", "failed"} and state != "preparing":
                break
        time.sleep(0.05)

    with Session(engine) as db:
        old = db.get(BsMaterial, old_id)
        current = db.get(BsMaterial, replacement_id)
        assert current.parse_state == "done", current.degradations_json
        assert old.superseded_by_id == replacement_id
        assert current.replacement_target_id is None


def test_replacement_candidate_cannot_change_subject(monkeypatch, tmp_path):
    """替换候选一旦建立，不能通过 setup 改派到另一主体。"""
    monkeypatch.setenv("SECRET_KEY", "synthetic-secret-key-0123456789-abcdefgh")
    engine = create_engine("sqlite:///" + (tmp_path / "replacement-subject.sqlite").as_posix(),
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    create_account(engine, "alice", "Alice-pass-123")
    client = TestClient(create_app(engine, "x" * 64, cookie_secure=False))
    assert client.post("/api/login", json={"username": "alice", "password": "Alice-pass-123"}).status_code == 200
    conversation_id = client.post("/api/conversations").json()["id"]
    base = "/api/plus/blockchain-supervision"
    task_id = client.post(f"{base}/tasks", json={
        "conversation_id": conversation_id,
        "title": "f2-replacement-subject",
        "scope_a1": True,
    }).json()["task_id"]

    baseline = client.post(
        f"{base}/tasks/{task_id}/materials/upload",
        files={"file": ("baseline.pdf", _pdf("BASELINE CONTRACT"), "application/pdf")},
        data={"role": "labor"},
    )
    assert baseline.status_code == 200, baseline.text
    old_id = baseline.json()["material_id"]
    subjects = client.get(f"{base}/tasks/{task_id}/subjects").json()["subjects"]
    original_subject_id = subjects[0]["subject_id"]
    second = client.post(f"{base}/tasks/{task_id}/subjects", json={
        "subject_type": "professional",
        "name": "主体2",
    })
    assert second.status_code == 200, second.text
    second_subject_id = second.json()["subject_id"]

    candidate = client.post(
        f"{base}/tasks/{task_id}/materials/upload",
        files={"file": ("replacement.pdf", _pdf("REPLACEMENT CONTRACT"), "application/pdf")},
        data={"role": "labor", "replaces_material_id": str(old_id)},
    )
    assert candidate.status_code == 200, candidate.text
    candidate_id = candidate.json()["material_id"]

    reassigned = client.post(
        f"{base}/tasks/{task_id}/materials/{candidate_id}/subject",
        json={"subject_id": second_subject_id},
    )
    assert reassigned.status_code == 409, reassigned.text
    assert reassigned.json()["detail"] == "替换候选必须保持原材料主体"

    detail = client.get(f"{base}/tasks/{task_id}").json()
    row = next(item for item in detail["materials"] if item["material_id"] == candidate_id)
    assert row["subject_id"] == original_subject_id
