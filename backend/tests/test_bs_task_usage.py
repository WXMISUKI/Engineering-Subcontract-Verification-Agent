from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.models import TokenUsage
from app.plus.blockchain_supervision.models import BsTask
from app.standalone.identity import Base, create_account
from app.standalone.main import create_app


def test_task_usage_endpoint_is_scoped_and_preserves_unknown_tokens(tmp_path):
    engine = create_engine(
        "sqlite:///" + (tmp_path / "task-usage.sqlite").as_posix(),
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    create_account(engine, "alice", "Alice-pass-123")
    client = TestClient(create_app(engine, "x" * 64, cookie_secure=False))
    assert client.post("/api/login", json={"username": "alice", "password": "Alice-pass-123"}).status_code == 200
    conversation_id = client.post("/api/conversations").json()["id"]
    task_id = client.post("/api/plus/blockchain-supervision/tasks", json={
        "conversation_id": conversation_id,
        "title": "task-usage",
        "scope_a1": True,
    }).json()["task_id"]
    with Session(engine) as db:
        db.add_all([
            TokenUsage(
                user_id=1, user_type="web", conversation_id=conversation_id,
                raw_usage_json={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                input_count=10, output_count=5, token_count=15,
                source="plus:blockchain-supervision", stage="agent_review",
                billing_scope_type="bs_task", billing_scope_id=str(task_id),
                usage_status="complete", billing_status="pending",
            ),
            TokenUsage(
                user_id=1, user_type="web", conversation_id=conversation_id,
                raw_usage_json={}, input_count=None, output_count=None, token_count=None,
                source="plus:blockchain-supervision", stage="agent_review",
                billing_scope_type="bs_task", billing_scope_id=str(task_id),
                usage_status="unknown", billing_status="unknown",
            ),
            TokenUsage(
                user_id=1, user_type="web", conversation_id=conversation_id,
                raw_usage_json={"total_tokens": 999}, input_count=None, output_count=None, token_count=999,
                source="plus:blockchain-supervision", stage="other",
                billing_scope_type="other", billing_scope_id="foreign",
            ),
        ])
        db.commit()

    response = client.get(f"/api/plus/blockchain-supervision/tasks/{task_id}/usage")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["billing_mode"] == "shadow"
    assert payload["calls"] == 2
    assert payload["unknown_calls"] == 1
    assert payload["input_tokens"] == 10
    assert payload["output_tokens"] == 5
    assert payload["total_tokens"] == 15
    assert payload["billing"]["settlement"]["usage_rows"] == 2
    assert len(payload["items"]) == 2
