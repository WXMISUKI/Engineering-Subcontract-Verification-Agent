from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from app.models.models import TokenUsage
from app.services.billing.registry import bootstrap
from app.standalone.identity import Base, create_account
from app.standalone.main import create_app


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "x" * 64)
    engine = create_engine(
        "sqlite:///" + (tmp_path / "billing.sqlite").as_posix(),
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        bootstrap(db)
    create_account(engine, "alice", "Alice-pass-123")
    client = TestClient(create_app(engine, "x" * 64, cookie_secure=False))
    assert client.post("/api/login", json={"username": "alice", "password": "Alice-pass-123"}).status_code == 200
    return client, engine


def test_standalone_billing_adapter_exposes_wallet_usage_and_task_summary(tmp_path, monkeypatch):
    client, engine = _client(tmp_path, monkeypatch)
    conversation_id = client.post("/api/conversations").json()["id"]
    task_id = client.post(
        "/api/plus/blockchain-supervision/tasks",
        json={"conversation_id": conversation_id, "title": "billing-task", "scope_a1": True},
    ).json()["task_id"]
    with Session(engine) as db:
        db.add(TokenUsage(
            user_id=1,
            user_type="web",
            conversation_id=conversation_id,
            input_count=12,
            output_count=4,
            token_count=16,
            source="plus:blockchain-supervision",
            stage="ai_advisory",
            billing_scope_type="bs_task",
            billing_scope_id=str(task_id),
            billing_scene="subcontract_review",
            usage_status="complete",
            billing_status="pending",
        ))
        db.commit()

    wallet = client.get("/api/user/wallet")
    usage = client.get("/api/user/usage?group_by=scene")
    task = client.get(f"/api/user/billing/task?scope_type=bs_task&scope_id={task_id}")
    task_usage = client.get(f"/api/plus/blockchain-supervision/tasks/{task_id}/usage")
    assert wallet.status_code == 200
    assert usage.status_code == 200
    assert task.status_code == 200
    assert task_usage.status_code == 200
    assert task_usage.json()["billing_mode"] == "shadow"
    assert task_usage.json()["total_tokens"] == 16
    assert task.json()["settlement"]["usage_rows"] == 1
