from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.plus.blockchain_supervision.router import _task_timing_dto


def test_task_timing_marks_naive_database_timestamps_as_utc():
    task = SimpleNamespace(
        started_at=datetime(2026, 11, 12, 20, 52, 9),
        completed_at=None,
        elapsed_seconds=None,
    )

    assert _task_timing_dto(task) == {
        "started_at": "2026-11-12T20:52:09+00:00",
        "completed_at": None,
        "elapsed_seconds": None,
    }


def test_task_timing_normalizes_aware_timestamps_to_utc():
    task = SimpleNamespace(
        started_at=datetime(2026, 11, 13, 4, 52, 9, tzinfo=timezone(timedelta(hours=8))),
        completed_at=None,
        elapsed_seconds=None,
    )

    assert _task_timing_dto(task)["started_at"] == "2026-11-12T20:52:09+00:00"
