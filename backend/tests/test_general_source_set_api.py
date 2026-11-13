import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.plus.blockchain_supervision.models import BsMaterial, BsTask, BsTaskEvent
from app.standalone.identity import Base, create_account
from app.standalone.main import create_app


def test_general_source_set_api_exposes_relation_and_locks_after_start(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "synthetic-source-set-test-key-0123456789")
    reference = Path(__file__).parent.parent / "app/plus/blockchain_supervision/data/reference_package.json"
    sources = json.loads(reference.read_text(encoding="utf-8"))["sources"]
    mapping_path = tmp_path / "reference-mapping.json"
    mapping_path.write_text(json.dumps({"version": 1, "documents": [
        {"sha256": digest, "kb_id": "synthetic-kb", "knowledge_id": "synthetic-" + digest,
         "collection": "road_national_basis"}
        for digest in {row["original_sha256"] for row in sources}
    ]}), encoding="utf-8")
    monkeypatch.setenv("BS_REFERENCE_MAPPING_FILE", str(mapping_path))
    engine = create_engine(
        "sqlite:///" + (tmp_path / "general-source-set.sqlite").as_posix(),
        connect_args={"check_same_thread": False},
    )
    try:
        Base.metadata.create_all(engine)
        create_account(engine, "alice", "Alice-pass-123")
        client = TestClient(create_app(engine, "x" * 64, cookie_secure=False))
        assert client.post(
            "/api/login", json={"username": "alice", "password": "Alice-pass-123"}
        ).status_code == 200
        conversation_id = client.post("/api/conversations").json()["id"]
        task_response = client.post("/api/plus/blockchain-supervision/tasks", json={
            "conversation_id": conversation_id,
            "title": "source set api test",
            "scope_a1": True,
        })
        assert task_response.status_code == 200, task_response.text
        task_id = task_response.json()["task_id"]
        with Session(engine) as db:
            materials = [
                BsMaterial(
                    task_id=task_id,
                    file_name=f"general-{index}.pdf",
                    role="general",
                    role_confirmed=True,
                    parse_state="done",
                    pages_json=[{"page": 1, "text": "contract text"}],
                    degradations_json=[],
                )
                for index in range(1, 4)
            ]
            labor = BsMaterial(
                task_id=task_id,
                file_name="labor.pdf",
                role="labor",
                role_confirmed=True,
                parse_state="done",
                pages_json=[{"page": 1, "text": "labor contract"}],
                degradations_json=[],
            )
            db.add_all([*materials, labor])
            db.commit()
            material_ids = [material.id for material in materials]

        base = f"/api/plus/blockchain-supervision/tasks/{task_id}"
        initial_detail = client.get(base)
        assert initial_detail.status_code == 200, initial_detail.text
        assert initial_detail.json()["validation"]["hard_ok"] is False
        associated = client.post(
            f"{base}/general-source-sets",
            json={"material_ids": material_ids},
        )
        assert associated.status_code == 200, associated.text
        association = associated.json()
        assert association["material_ids"] == material_ids
        assert association["relation_version"] == 1

        detail = client.get(base)
        assert detail.status_code == 200, detail.text
        assert detail.json()["validation"]["hard_ok"] is True
        material_dtos = {row["id"]: row for row in detail.json()["materials"]}
        assert {material_dtos[material_id]["general_source_set_id"] for material_id in material_ids} == {
            association["source_set_id"]
        }
        assert {material_dtos[material_id]["general_source_set_version"] for material_id in material_ids} == {1}

        removed = client.delete(f"{base}/materials/{material_ids[0]}/general-source-set")
        assert removed.status_code == 200, removed.text
        assert removed.json()["material_ids"] == material_ids[1:]
        detail = client.get(base)
        material_dtos = {row["id"]: row for row in detail.json()["materials"]}
        assert detail.json()["validation"]["hard_ok"] is False
        assert material_dtos[material_ids[0]]["general_source_set_id"] is None
        assert material_dtos[material_ids[1]]["general_source_set_id"] == association["source_set_id"]
        assert material_dtos[material_ids[1]]["general_source_set_version"] == 2

        relinked = client.post(f"{base}/general-source-sets", json={"material_ids": material_ids})
        assert relinked.status_code == 200, relinked.text
        assert relinked.json()["relation_version"] == 3
        detail = client.get(base)
        assert detail.json()["validation"]["hard_ok"] is True

        started = client.post(f"{base}/start")
        assert started.status_code == 200, started.text
        assert client.get(base).json()["state"] == "running"

        with Session(engine) as db:
            task = db.get(BsTask, task_id)
            association_event = db.query(BsTaskEvent).filter_by(
                task_id=task_id, event_type="material.general_source_set_changed"
            ).order_by(BsTaskEvent.sequence).first()
            assert association_event.payload_json["operator"] == task.created_by
        rejected = client.delete(f"{base}/materials/{material_ids[1]}/general-source-set")
        assert rejected.status_code == 409
    finally:
        engine.dispose()
