"""任务模式的持久化与旧任务兼容；独立 SQLite，不连接业务库。"""
from sqlalchemy.orm import Session

from app.plus.blockchain_supervision import review_mode
from app.plus.blockchain_supervision.models import BsTask
from test_bs_report_version_http import _client


def test_task_mode_defaults_and_is_restored_by_api(tmp_path):
    client, engine, old_id = _client(tmp_path)
    base = "/api/plus/blockchain-supervision/tasks"
    with Session(engine) as db:
        old = db.get(BsTask, old_id)
        conversation_id = old.conversation_id
        old.options_snapshot_json = {"historical": True}
        db.commit()
    assert client.get(f"{base}/{old_id}").json()["review_mode"] == "admission"
    result = client.post(base, json={"conversation_id": conversation_id, "title": "倒查",
                                     "review_mode": "contract_retrospective"})
    assert result.status_code == 200, result.text
    task_id = result.json()["task_id"]
    assert result.json()["review_mode"] == "contract_retrospective"
    assert result.json()["section"]["baseline_date"] is None
    admission = client.post(base, json={"conversation_id": conversation_id, "title": "准入",
                                       "scope_a1": True})
    assert admission.status_code == 200
    assert admission.json()["section"]["baseline_date"] is not None
    assert client.get(f"{base}/{task_id}").json()["review_mode"] == "contract_retrospective"
    modes = {row["task_id"]: row["review_mode"] for row in client.get(base, params={"conversation_id": conversation_id}).json()["tasks"]}
    assert modes == {old_id: "admission", task_id: "contract_retrospective",
                     admission.json()["task_id"]: "admission"}
    started = client.post(f"{base}/{task_id}/start")
    assert started.status_code == 409
    assert "独立核查与自动报告流程" in started.json()["detail"]
    with Session(engine) as db:
        assert db.get(BsTask, old_id).options_snapshot_json == {"historical": True}
        assert review_mode.of(db.get(BsTask, task_id)) == "contract_retrospective"
        assert db.get(BsTask, task_id).state == "setup"


def test_invalid_mode_does_not_create_task(tmp_path):
    client, engine, old_id = _client(tmp_path)
    with Session(engine) as db:
        conversation_id = db.get(BsTask, old_id).conversation_id
    response = client.post("/api/plus/blockchain-supervision/tasks", json={
        "conversation_id": conversation_id, "title": "错误模式", "review_mode": "other", "scope_a1": True})
    assert response.status_code == 400
    with Session(engine) as db:
        assert db.query(BsTask).count() == 1
