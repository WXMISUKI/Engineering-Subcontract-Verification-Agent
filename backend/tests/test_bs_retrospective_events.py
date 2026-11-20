from datetime import datetime
from types import SimpleNamespace

from app.plus.blockchain_supervision import service


def test_retrospective_events_only_expose_progress_not_frozen_text_or_model_results():
    raw = {"run_id": "r1", "phase": "upstream", "label": "核对总分包合同约定及适用条件",
           "index": 2, "total": 8, "batch_id": "b2", "state": "failed",
           "result": {"findings": ["private finding"]}, "sources": ["private text"],
           "model_identity": {"provider": "private"}, "reason": "private failure"}
    for kind in ["accepted", "input_frozen", "batch_started", "batch_completed", "completed", "failed"]:
        dto = service.event_dto(SimpleNamespace(event_type=f"retrospective.{kind}", payload_json=raw,
            sequence=4, stage="check", created_at=datetime(2026, 11, 19)))
        assert dto["run_id"] == "r1"
        assert dto["label"] != "任务动态"
        assert "private" not in str(dto)
        assert dto["payload"]["state"] == "failed"
        assert dto["message"] == "2/8批"
