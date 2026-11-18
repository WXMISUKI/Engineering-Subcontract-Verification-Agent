"""旧库的缺列错误必须明确诊断；请求不能自动改表或掩盖其他数据库错误。"""
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.plus.blockchain_supervision.models import BsTask
from app.standalone.identity import Base, create_account
from app.standalone.main import create_app


def test_legacy_schema_returns_actionable_503_without_ddl(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "legacy.sqlite").as_posix(),
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    create_account(engine, "alice", "Alice-pass-123")
    client = TestClient(create_app(engine, "x" * 64, cookie_secure=False), raise_server_exceptions=False)
    with client:
        assert client.post("/api/login", json={"username": "alice", "password": "Alice-pass-123"}).status_code == 200
        conversation_id = client.post("/api/conversations").json()["id"]
        with Session(engine) as db:
            task = BsTask(conversation_id=conversation_id, title="旧库合成任务", created_by=1,
                          created_by_type="web", state="running")
            db.add(task)
            db.commit()
            task_id = task.id
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE bs_finding DROP COLUMN finding_semantics"))
        response = client.get(f"/api/plus/blockchain-supervision/tasks/{task_id}/findings")
        assert response.status_code == 503, (response.status_code, response.headers.get("content-type"))
        assert response.json()["detail"]["code"] == "bs_finding_schema_not_ready"
        assert "finding_semantics" not in {column["name"] for column in inspect(engine).get_columns("bs_finding")}
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE bs_task DROP COLUMN title"))
        other_error = client.get(f"/api/plus/blockchain-supervision/tasks/{task_id}/findings")
        assert other_error.status_code == 500
    engine.dispose()
