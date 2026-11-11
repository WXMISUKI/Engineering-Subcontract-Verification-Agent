"""仅对独立测试数据库运行，验证 MySQL 下的报告策略并发切换。"""

import copy
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.plus.blockchain_supervision import reference_snapshots, report_policy, service
from app.plus.blockchain_supervision.models import BsTask, BsTaskEvent
from app.standalone.identity import Agent, Base, ConversationMapping, User


@pytest.mark.skipif(not os.environ.get("BS_POLICY_TEST_MYSQL_URL"), reason="需要独立 MySQL 测试库")
def test_concurrent_policy_activation_accepts_only_one_request(monkeypatch):
    engine = create_engine(os.environ["BS_POLICY_TEST_MYSQL_URL"], pool_pre_ping=True)
    Base.metadata.create_all(engine)
    old_policy = report_policy.freeze_policy(report_policy.load_current_policy())
    new_content = copy.deepcopy(old_policy["content"])
    new_content["version"] = "1.0.1"
    target = report_policy.freeze_policy(new_content)
    monkeypatch.setattr(report_policy, "load_current_policy", lambda: copy.deepcopy(new_content))
    with Session(engine) as db:
        user = User(username="isolated-test", password_hash="unused")
        agent = Agent(slug="isolated-test")
        db.add_all([user, agent])
        db.flush()
        conversation = ConversationMapping(user_id=user.id, agent_id=agent.id, user_type="web")
        db.add(conversation)
        db.flush()
        task = BsTask(conversation_id=conversation.id, title="隔离并发测试", created_by=user.id,
                      created_by_type="web", options_snapshot_json={reference_snapshots.REVIEW_POINTER: "test-review"})
        db.add(task)
        db.flush()
        db.add(BsTaskEvent(task_id=task.id, sequence=1, event_type=reference_snapshots.REVIEW_EVENT,
            payload_json={"review_run_id": "test-review", "expected_groups": [], "steps": [], "report_policy": old_policy}))
        task_id = task.id
        db.commit()
    barrier = Barrier(2)

    def activate():
        with Session(engine) as db:
            task = db.get(BsTask, task_id)  # 两个请求均先读取旧状态，再竞争锁。
            barrier.wait(timeout=10)
            try:
                service.activate_report_policy(db, task, expected_review_id="test-review",
                    expected_previous_sha256=old_policy["sha256"], expected_target_sha256=target["sha256"],
                    operator_id=1, operator_type="web")
                db.commit()
                return "activated"
            except ValueError:
                db.rollback()
                return "stale"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: activate(), range(2)))
    assert sorted(outcomes) == ["activated", "stale"]
    with Session(engine) as db:
        rows = db.query(BsTaskEvent).filter_by(task_id=task_id, event_type="report.policy_activated").all()
        assert len(rows) == 1
    engine.dispose()
