from types import SimpleNamespace

from app.plus.blockchain_supervision import service


def _task(options):
    return SimpleNamespace(options_snapshot_json=options)


def test_publication_state_defaults_to_baseline():
    state = service.supplement_publication(_task({}))

    assert state == {
        "state": "baseline",
        "baseline_version_no": None,
        "candidate_generation_id": None,
        "published_generation_id": None,
        "published_version_no": None,
        "review_policy": "current_generation",
    }


def test_publication_state_exposes_running_candidate_and_frozen_baseline():
    state = service.supplement_publication(_task({
        "supplement_plan": {"status": "running", "generation_id": "g2"},
        "supplement_edit": {"generation_id": "g2", "previous_version_no": 4},
    }))

    assert state["state"] == "candidate"
    assert state["baseline_version_no"] == 4
    assert state["candidate_generation_id"] == "g2"
    assert state["published_generation_id"] is None


def test_publication_state_keeps_published_generation_after_completion():
    state = service.supplement_publication(_task({
        "supplement_plan": {
            "status": "completed",
            "generation_id": "g2",
            "completed_version_no": 5,
        },
        "supplement_edit": {"generation_id": "g2", "previous_version_no": 4},
    }))

    assert state["state"] == "published"
    assert state["baseline_version_no"] == 4
    assert state["published_generation_id"] == "g2"
    assert state["published_version_no"] == 5


def test_publication_state_exposes_failed_candidate_without_losing_baseline():
    state = service.supplement_publication(_task({
        "supplement_publication": {
            "state": "failed",
            "baseline_version_no": 4,
            "candidate_generation_id": "g3",
            "last_failure": {"reason": "parse_failed"},
        },
    }))

    assert state["state"] == "failed"
    assert state["baseline_version_no"] == 4
    assert state["candidate_generation_id"] == "g3"
    assert state["last_failure"]["reason"] == "parse_failed"


def test_publication_state_promotes_candidate_when_plan_is_already_completed():
    state = service.supplement_publication(_task({
        "supplement_plan": {
            "status": "completed",
            "generation_id": "g4",
            "completed_version_no": 8,
        },
        "supplement_edit": {"generation_id": "g4", "previous_version_no": 7},
        "supplement_publication": {
            "state": "candidate",
            "baseline_version_no": 7,
            "candidate_generation_id": "g4",
        },
    }))

    assert state["state"] == "published"
    assert state["published_generation_id"] == "g4"
    assert state["published_version_no"] == 8


def test_restart_recovery_marks_interrupted_candidate_failed():
    task = _task({
        "supplement_plan": {"status": "running", "generation_id": "g5"},
        "supplement_edit": {"generation_id": "g5", "previous_version_no": 9},
        "supplement_publication": {
            "state": "candidate",
            "baseline_version_no": 9,
            "candidate_generation_id": "g5",
        },
    })
    task.state = "preparing"

    state = service.recover_supplement_candidate(task)

    assert state["state"] == "failed"
    assert state["baseline_version_no"] == 9
    assert state["candidate_generation_id"] == "g5"
    assert state["last_failure"]["reason"] == "service_restart"


def test_publication_state_exposes_review_policy_for_human_decisions():
    candidate = service.supplement_publication(_task({
        "supplement_plan": {"status": "running", "generation_id": "g6"},
        "supplement_edit": {"generation_id": "g6", "previous_version_no": 10},
    }))
    published = service.supplement_publication(_task({
        "supplement_plan": {"status": "completed", "generation_id": "g6", "completed_version_no": 11},
        "supplement_edit": {"generation_id": "g6", "previous_version_no": 10},
    }))

    assert candidate["review_policy"] == "candidate_requires_review"
    assert published["review_policy"] == "current_generation"


def test_repeated_activation_during_running_candidate_is_idempotent():
    class LockedDb:
        def rollback(self):
            pass

        def refresh(self, task, with_for_update=False):
            pass

    task = _task({
        "supplement_plan": {"status": "planned", "generation_id": "g-running"},
        "supplement_publication": {
            "state": "candidate",
            "candidate_generation_id": "g-running",
            "baseline_version_no": 3,
            "review_policy": "candidate_requires_review",
        },
    })
    task.state = "running"

    result = service.activate_supplement_plan(LockedDb(), task)

    assert result["ok"] is True
    assert result["already_running"] is True
    assert result["generation_id"] == "g-running"


def test_activation_state_race_does_not_mark_publication_failed():
    class LockedDb:
        def rollback(self):
            pass

        def refresh(self, task, with_for_update=False):
            pass

    task = _task({
        "supplement_plan": {"status": "planned", "generation_id": "g-race"},
        "supplement_publication": {
            "state": "baseline",
            "baseline_version_no": 3,
            "review_policy": "baseline_only",
        },
    })
    task.state = "review"

    result = service.activate_supplement_plan(LockedDb(), task)

    assert result["ok"] is False
    assert result["reason"] == "state"
    assert service.supplement_publication(task)["state"] == "baseline"
