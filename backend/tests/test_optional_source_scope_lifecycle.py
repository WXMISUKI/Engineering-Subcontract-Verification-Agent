from app.plus.blockchain_supervision import optional_source_scope as scope
from types import SimpleNamespace
import pytest


def _snapshot(source_state="absent", rule_ids=("T15-01", "W16-03")):
    return scope.build_scope_snapshot(
        generation_id="generation-1",
        policy=scope.POLICY_VERSION,
        ruleset_version="rules-v1",
        ruleset_fingerprint="rules-hash",
        material_fingerprint="materials-hash",
        source_state=source_state,
        source_material_ids=(31,) if source_state != "absent" else (),
        rule_ids=rule_ids,
    )


def _started(check_run_id="check-1", *, input_fingerprint="input-hash"):
    return {
        "event_type": scope.CHECK_STARTED_EVENT,
        "payload": {
            "schema_version": scope.CHECK_EVENT_SCHEMA,
            "generation_id": "generation-1",
            "group_id": "a1",
            "check_run_id": check_run_id,
            "input_fingerprint": input_fingerprint,
        },
    }


def _recorded(check_run_id="check-1", *, source_state="absent", rule_ids=("T15-01", "W16-03")):
    rules = scope.build_rule_coverage(
        generation_id="generation-1",
        group_id="a1",
        check_run_id=check_run_id,
        input_fingerprint="input-hash",
        source_state=source_state,
        source_material_ids=(31,) if source_state != "absent" else (),
        rule_ids=rule_ids,
        finding_ids_by_rule={"T15-01": [101]},
    )
    return {
        "event_type": scope.CHECK_RECORDED_EVENT,
        "payload": {
            "schema_version": scope.CHECK_EVENT_SCHEMA,
            "generation_id": "generation-1",
            "group_id": "a1",
            "check_run_id": check_run_id,
            "input_fingerprint": "input-hash",
            "policy": scope.POLICY_VERSION,
            "source_state": source_state,
            "rules": rules,
        },
    }


def _pointer(check_run_id="check-1", *, input_fingerprint="input-hash"):
    return {
        "generation_id": "generation-1",
        "group_id": "a1",
        "check_run_id": check_run_id,
        "input_fingerprint": input_fingerprint,
    }


def test_flag_defaults_off_and_freezes_policy_identity(monkeypatch):
    monkeypatch.delenv(scope.SCOPE_POLICY_ENV, raising=False)
    assert scope.scope_policy_enabled() is False
    assert scope.freeze_policy(False) == {"enabled": False, "policy": "baseline"}
    assert scope.scope_policy_enabled({scope.SCOPE_POLICY_ENV: "true"}) is True
    assert scope.freeze_policy(True) == {"enabled": True, "policy": scope.POLICY_VERSION}


def test_general_source_state_keeps_absent_processing_and_terminal_read_errors_distinct():
    classify = lambda rows: scope.classify_general_source_state(
        rows, readable=lambda item: bool(item.pages_json),
        reading_gap=lambda item: item.reading_gap,
    )

    assert classify([]) == "absent"
    assert classify([SimpleNamespace(role="general", parse_state="parsing")]) == "processing"
    assert classify([SimpleNamespace(role="general", parse_state="failed")]) == "unreadable"
    assert classify([SimpleNamespace(role="general", parse_state="done", pages_json=[], reading_gap=[])]) == "unreadable"
    assert classify([SimpleNamespace(role="general", parse_state="done", pages_json=[1], reading_gap=[2])]) == "unreadable"
    assert classify([SimpleNamespace(role="general", parse_state="done", pages_json=[1], reading_gap=None)]) == "readable"


def test_general_source_state_does_not_trust_unlinked_or_conflicting_materials():
    def classify(rows):
        return scope.classify_general_source_state(
            rows, readable=lambda _item: True, reading_gap=lambda _item: None,
            linkage_issue=lambda item: item.issue,
        )

    base = {"role": "general", "parse_state": "done", "subject_id": None, "issue": None}
    assert classify([SimpleNamespace(**{**base, "subject_id": 7})]) == "unlinked"
    assert classify([SimpleNamespace(**{**base, "issue": "subject_conflict"})]) == "conflicted"
    assert classify([SimpleNamespace(**base), SimpleNamespace(**base)]) == "conflicted"


def test_general_source_state_accepts_only_one_explicit_source_set():
    def classify(rows):
        return scope.classify_general_source_state(
            rows, readable=lambda _item: True, reading_gap=lambda _item: None,
            linkage_issue=lambda _item: None,
        )

    first = SimpleNamespace(id=1, role="general", parse_state="done", subject_id=None,
                            general_source_set_id="set-a", general_source_set_version=2)
    second = SimpleNamespace(id=2, role="general", parse_state="done", subject_id=None,
                             general_source_set_id="set-a", general_source_set_version=2)
    independent = SimpleNamespace(id=3, role="general", parse_state="done", subject_id=None,
                                   general_source_set_id=None, general_source_set_version=None)

    assert classify([first, second]) == "readable"
    assert classify([first, second, independent]) == "conflicted"
    assert classify([first, SimpleNamespace(**{**second.__dict__, "general_source_set_id": "set-b"})]) == "conflicted"


def test_general_source_set_aggregation_waits_for_processing_and_rejects_unreadable_member():
    def classify(rows):
        return scope.classify_general_source_state(
            rows, readable=lambda item: item.readable, reading_gap=lambda _item: None,
            linkage_issue=lambda _item: None,
        )

    base = {"role": "general", "subject_id": None,
            "general_source_set_id": "set-a", "general_source_set_version": 1}
    readable = SimpleNamespace(**{**base, "id": 1, "parse_state": "done", "readable": True})
    processing = SimpleNamespace(**{**base, "id": 2, "parse_state": "parsing", "readable": False})
    unreadable = SimpleNamespace(**{**base, "id": 3, "parse_state": "failed", "readable": False})

    assert classify([readable, processing]) == "processing"
    assert classify([readable, unreadable]) == "unreadable"


def test_scope_snapshot_records_source_identity_without_document_content():
    snapshot = scope.build_scope_snapshot(
        generation_id="generation-1",
        policy=scope.POLICY_VERSION,
        ruleset_version="rules-v1",
        ruleset_fingerprint="rules-hash",
        material_fingerprint="materials-hash",
        source_state="unreadable",
        source_material_ids=(31, 32),
        source_sets=[{"source_set_id": "set-a", "relation_version": 2,
                      "member_material_ids": [31, 32]}],
        rule_ids=("T15-01", "W16-03"),
    )

    assert snapshot["schema_version"] == scope.SCOPE_SNAPSHOT_SCHEMA
    assert snapshot["generation_id"] == "generation-1"
    assert snapshot["source"] == {
        "source_role": "general",
        "source_material_ids": [31, 32],
        "source_state": "unreadable",
        "source_sets": [{"source_set_id": "set-a", "relation_version": 2,
                         "member_material_ids": [31, 32]}],
    }
    assert {rule["rule_id"] for rule in snapshot["rules"]} == {"T15-01", "W16-03"}
    assert "text" not in str(snapshot).lower()


def test_legacy_generation_is_explicitly_unknown():
    result = scope.project_scope_coverage(
        generation_id="generation-legacy",
        policy=None,
        scope_snapshots=[],
        current_pointer=None,
        started_events=[],
        terminal_events=[],
    )

    assert result["state"] == "legacy_unknown"
    assert result["limitations"] == []


def test_enabled_generation_without_unique_scope_snapshot_fails_closed():
    result = scope.project_scope_coverage(
        generation_id="generation-1",
        policy={"enabled": True, "policy": scope.POLICY_VERSION},
        scope_snapshots=[_snapshot(), _snapshot()],
        current_pointer=None,
        started_events=[],
        terminal_events=[],
    )

    assert result["state"] == "unavailable"
    assert result["limitations"] == []


def test_generation_with_no_applicable_rules_has_explicit_empty_coverage():
    result = scope.project_scope_coverage(
        generation_id="generation-1",
        policy={"enabled": True, "policy": scope.POLICY_VERSION},
        scope_snapshots=[_snapshot(rule_ids=())],
        current_pointer=None,
        started_events=[],
        terminal_events=[],
    )

    assert result["state"] == "available"
    assert result["rules"] == []
    assert result["limitations"] == []


def test_scope_projection_is_incomplete_until_current_check_starts():
    snapshot = _snapshot()
    result = scope.project_scope_coverage(
        generation_id="generation-1",
        policy={"enabled": True, "policy": scope.POLICY_VERSION},
        scope_snapshots=[snapshot],
        current_pointer=None,
        started_events=[],
        terminal_events=[],
    )

    assert result["state"] == "incomplete"
    assert result["rules"] == snapshot["rules"]


def test_projection_uses_exact_current_run_and_reports_running():
    result = scope.project_scope_coverage(
        generation_id="generation-1",
        policy={"enabled": True, "policy": scope.POLICY_VERSION},
        scope_snapshots=[_snapshot()],
        current_pointer=_pointer("check-2"),
        started_events=[_started("check-1"), _started("check-2")],
        terminal_events=[_recorded("check-1")],
    )

    assert result["state"] == "running"
    assert result["limitations"] == []


def test_completed_projection_separates_scope_limitations_from_findings():
    result = scope.project_scope_coverage(
        generation_id="generation-1",
        policy={"enabled": True, "policy": scope.POLICY_VERSION},
        scope_snapshots=[_snapshot()],
        current_pointer=_pointer(),
        started_events=[_started()],
        terminal_events=[_recorded()],
    )

    assert result["state"] == "partial"
    assert {item["rule_id"] for item in result["limitations"]} == {"T15-01", "W16-03"}
    assert all(item["counts_as_finding"] is False for item in result["limitations"])
    assert all(item["counts_as_pass"] is False for item in result["limitations"])
    assert all(item["blocks_report"] is False for item in result["limitations"])
    assert {rule["rule_id"]: rule["finding_ids"] for rule in result["rules"]}["T15-01"] == [101]


def test_conflicting_duplicate_terminal_events_fail_closed():
    first = _recorded()
    second = _recorded()
    second["payload"]["rules"][0]["finding_ids"] = [202]
    result = scope.project_scope_coverage(
        generation_id="generation-1",
        policy={"enabled": True, "policy": scope.POLICY_VERSION},
        scope_snapshots=[_snapshot()],
        current_pointer=_pointer(),
        started_events=[_started()],
        terminal_events=[first, second],
    )

    assert result["state"] == "unavailable"
    assert result["limitations"] == []


def test_coverage_terminal_must_match_frozen_source_state_and_identity():
    result = scope.project_scope_coverage(
        generation_id="generation-1",
        policy={"enabled": True, "policy": scope.POLICY_VERSION},
        scope_snapshots=[_snapshot(source_state="absent")],
        current_pointer=_pointer(),
        started_events=[_started()],
        terminal_events=[_recorded(source_state="readable")],
    )

    assert result["state"] == "unavailable"
    assert result["limitations"] == []


def test_processing_source_does_not_become_final_scope_limitation():
    result = scope.project_scope_coverage(
        generation_id="generation-1",
        policy={"enabled": True, "policy": scope.POLICY_VERSION},
        scope_snapshots=[_snapshot(source_state="processing")],
        current_pointer=_pointer(),
        started_events=[_started()],
        terminal_events=[_recorded(source_state="processing")],
    )

    assert result["state"] == "incomplete"
    assert result["limitations"] == []


def test_coverage_event_identity_conflict_is_not_idempotent():
    original = _started()
    retry = _started()
    assert scope.same_event_payload(original["payload"], retry["payload"]) is True

    retry["payload"]["input_fingerprint"] = "different-input"
    assert scope.same_event_payload(original["payload"], retry["payload"]) is False


def _db_task(db, *, scope_a1=True):
    from app.plus.blockchain_supervision.models import BsTask

    task = BsTask(conversation_id=101, title="可选来源范围测试", created_by=1,
                  created_by_type="tester", options_snapshot_json={}, scope_a1=scope_a1)
    db.add(task)
    db.flush()
    return task


def _check_plan(db, task, generation_id):
    from app.plus.blockchain_supervision import knowledge_runs

    return {
        "group_id": "a1",
        "review_run_id": generation_id,
        "run_id": "reference-run",
        "material_fingerprint": knowledge_runs.material_fingerprint(db, task),
        "rule_fingerprint": knowledge_runs.rule_fingerprint(),
        "dependency_fingerprint": knowledge_runs.dependency_fingerprint(db, "a1", task.id),
    }


def test_review_generation_freezes_flag_and_records_one_scope_snapshot(db_session, monkeypatch):
    from app.plus.blockchain_supervision import reference_snapshots, service
    from app.plus.blockchain_supervision.models import BsTaskEvent

    monkeypatch.setenv(scope.SCOPE_POLICY_ENV, "true")
    task = _db_task(db_session)
    seed = service.initialize_reference_review(db_session, task)
    monkeypatch.setenv(scope.SCOPE_POLICY_ENV, "false")

    assert seed["optional_source_scope"] == {"enabled": True, "policy": scope.POLICY_VERSION}
    events = db_session.query(BsTaskEvent).filter_by(
        task_id=task.id, event_type=scope.SCOPE_SNAPSHOT_EVENT).all()
    assert len(events) == 1
    assert events[0].payload_json["generation_id"] == seed["review_run_id"]
    assert events[0].payload_json["source"]["source_state"] == "absent"
    assert {row["rule_id"] for row in events[0].payload_json["rules"]} == set(scope.OPTIONAL_SOURCE_RULE_IDS)
    assert reference_snapshots.get_reference_snapshot(db_session, task)["optional_source_scope"]["enabled"] is True


def test_default_off_generation_has_no_scope_event(db_session, monkeypatch):
    from app.plus.blockchain_supervision import service
    from app.plus.blockchain_supervision.models import BsTaskEvent

    monkeypatch.delenv(scope.SCOPE_POLICY_ENV, raising=False)
    task = _db_task(db_session)
    seed = service.initialize_reference_review(db_session, task)

    assert seed["optional_source_scope"] == {"enabled": False, "policy": "baseline"}
    assert db_session.query(BsTaskEvent).filter_by(
        task_id=task.id, event_type=scope.SCOPE_SNAPSHOT_EVENT).count() == 0


@pytest.mark.parametrize("selector_enabled, scope_enabled", [
    (False, False),
    (True, False),
    (False, True),
    (True, True),
])
def test_review_generation_freezes_selector_and_business_scope_flags_independently(
        db_session, monkeypatch, selector_enabled, scope_enabled):
    from app.plus.blockchain_supervision import ai_scope_selection, service
    from app.plus.blockchain_supervision.models import BsTaskEvent

    monkeypatch.setenv(ai_scope_selection.SCOPE_SELECTOR_ENV,
                       "true" if selector_enabled else "false")
    monkeypatch.setenv(scope.SCOPE_POLICY_ENV, "true" if scope_enabled else "false")
    task = _db_task(db_session)

    seed = service.initialize_reference_review(db_session, task)
    coverage = service.optional_scope_coverage(db_session, task)
    scope_events = db_session.query(BsTaskEvent).filter_by(
        task_id=task.id, event_type=scope.SCOPE_SNAPSHOT_EVENT).all()

    assert seed["scope_strategy"] == (
        {"enabled": True, "version": ai_scope_selection.SCOPE_SELECTOR_STRATEGY}
        if selector_enabled else {"enabled": False, "version": "baseline"}
    )
    assert seed["optional_source_scope"] == scope.freeze_policy(scope_enabled)
    assert len(scope_events) == int(scope_enabled)
    assert coverage["state"] == ("incomplete" if scope_enabled else "legacy_unknown")


def test_new_a1_check_run_supersedes_old_pointer_and_rejects_late_write(db_session, monkeypatch):
    from app.plus.blockchain_supervision import knowledge_runs, service
    from app.plus.blockchain_supervision.models import BsFinding, BsTaskEvent
    from app.plus.blockchain_supervision.knowledge_runs import ReferenceRunSuperseded

    monkeypatch.setenv(scope.SCOPE_POLICY_ENV, "true")
    task = _db_task(db_session)
    seed = service.initialize_reference_review(db_session, task)
    db_session.commit()
    plan = _check_plan(db_session, task, seed["review_run_id"])
    first = service._begin_optional_scope_check_run(db_session, task, plan)
    second = service._begin_optional_scope_check_run(db_session, task, plan)

    assert first["check_run_id"] != second["check_run_id"]
    with pytest.raises(ReferenceRunSuperseded):
        service._assert_optional_scope_check_current(db_session, task, first)
    service._assert_optional_scope_check_current(db_session, task, second)

    monkeypatch.setattr(knowledge_runs, "retrieve_reference_run", lambda *_args: {
        "run_id": plan["run_id"], "review_run_id": seed["review_run_id"],
        "group_id": "a1", "state": "completed", "references": [], "limitations": [],
    })
    monkeypatch.setattr(knowledge_runs, "assert_reference_run_current", lambda *_args, **_kwargs: None)
    stale_finding = BsFinding(task_id=task.id, item_id="T15-01", kind="transfer_scope",
                              status="miss", message="旧运行结果")
    with pytest.raises(ReferenceRunSuperseded):
        service._persist_reference_group(db_session, task, plan, [stale_finding], scope_run=first)

    assert db_session.query(BsFinding).filter_by(task_id=task.id, item_id="T15-01").count() == 0
    assert db_session.query(BsTaskEvent).filter_by(
        task_id=task.id, event_type=scope.CHECK_RECORDED_EVENT).count() == 0


def test_scope_event_same_key_retry_is_idempotent_and_conflict_fails_closed(db_session):
    from app.plus.blockchain_supervision import service
    from app.plus.blockchain_supervision.models import BsTaskEvent

    task = _db_task(db_session)
    payload = {
        "schema_version": scope.CHECK_EVENT_SCHEMA,
        "generation_id": "generation-1",
        "group_id": "a1",
        "check_run_id": "check-1",
        "input_fingerprint": "input-hash",
    }
    first = service._record_optional_scope_event(
        db_session, task, scope.CHECK_STARTED_EVENT, payload, commit=True)
    retry = service._record_optional_scope_event(
        db_session, task, scope.CHECK_STARTED_EVENT, dict(payload), commit=True)
    assert first == retry
    assert db_session.query(BsTaskEvent).filter_by(
        task_id=task.id, event_type=scope.CHECK_STARTED_EVENT).count() == 1

    conflicting = {**payload, "input_fingerprint": "other-input"}
    with pytest.raises(ValueError, match="身份冲突"):
        service._record_optional_scope_event(
            db_session, task, scope.CHECK_STARTED_EVENT, conflicting, commit=True)


def test_scope_event_timeline_hides_structured_internal_payload():
    from datetime import datetime
    from app.plus.blockchain_supervision import service
    from app.plus.blockchain_supervision.models import BsTaskEvent

    event = BsTaskEvent(task_id=1, sequence=1, event_type=scope.SCOPE_SNAPSHOT_EVENT,
                        stage="check", payload_json={"generation_id": "internal-generation",
                        "source_material_ids": [31]}, created_at=datetime.now())
    dto = service.event_dto(event)

    assert dto["label"] == "本轮规则来源范围已冻结"
    assert dto["payload"] == {}


def test_scope_failure_timeline_hides_internal_payload_but_keeps_normal_error_message():
    from datetime import datetime
    from app.plus.blockchain_supervision import service
    from app.plus.blockchain_supervision.models import BsTaskEvent

    scope_failure = BsTaskEvent(
        task_id=1, sequence=1, event_type=scope.CHECK_FAILED_EVENT, stage="check",
        payload_json={"schema_version": scope.CHECK_EVENT_SCHEMA,
                      "generation_id": "internal-generation", "check_run_id": "internal-run",
                      "input_fingerprint": "internal-fingerprint", "reason_code": "execution_error"},
        created_at=datetime.now(),
    )
    ordinary_failure = BsTaskEvent(
        task_id=1, sequence=2, event_type="check.failed", stage="check",
        payload_json={"message": "规则核查失败，请重新发起"}, created_at=datetime.now(),
    )

    assert service.event_dto(scope_failure)["payload"] == {}
    assert service.event_dto(ordinary_failure)["payload"] == {"message": "规则核查失败，请重新发起"}


def test_run_a1_checks_passes_frozen_scope_source_state_to_rule_kernels(db_session, monkeypatch):
    from app.plus.blockchain_supervision import a1_check, service

    monkeypatch.setenv(scope.SCOPE_POLICY_ENV, "true")
    task = _db_task(db_session)
    plan = {"group_id": "a1"}
    scope_run = {
        "generation_id": "generation-1", "group_id": "a1", "check_run_id": "check-1",
        "input_fingerprint": "input-hash", "policy": scope.POLICY_VERSION,
        "source_state": "absent", "source_material_ids": [],
        "rule_ids": list(scope.OPTIONAL_SOURCE_RULE_IDS),
    }
    captured = {}

    monkeypatch.setattr(service, "_prepare_reference_group", lambda *_args: plan)
    monkeypatch.setattr(service, "_begin_optional_scope_check_run", lambda *_args: scope_run)
    monkeypatch.setattr(service, "_rule_materials", lambda *_args: [])
    monkeypatch.setattr(service, "load_config", lambda: {"amount_ratio": {"enabled": True}})
    monkeypatch.setattr(a1_check, "run_negative_list_check", lambda *_args: [])
    monkeypatch.setattr(a1_check, "run_a3_dependent_checks", lambda *_args: [])

    def capture(name):
        def run(*_args, **kwargs):
            captured[name] = kwargs
            return []
        return run

    monkeypatch.setattr(a1_check, "run_amount_ratio_check", capture("amount_ratio"))
    monkeypatch.setattr(a1_check, "run_transfer_scope_check", capture("transfer_scope"))
    monkeypatch.setattr(a1_check, "run_transfer_materials_check", capture("transfer_materials"))
    monkeypatch.setattr(a1_check, "run_transfer_relations_check", capture("transfer_relations"))
    monkeypatch.setattr(a1_check, "run_illegal_subcontract_check", capture("illegal_subcontract"))
    monkeypatch.setattr(a1_check, "run_open_term_registrations", lambda _task, findings: findings)
    monkeypatch.setattr(a1_check, "supersede_dependency_findings", lambda findings: (findings, []))
    monkeypatch.setattr(service.pinning, "pin_findings", lambda *_args: None)
    monkeypatch.setattr(service, "_persist_reference_group", lambda *_args, **kwargs: kwargs["scope_run"])

    result = service.run_a1_checks(db_session, task)

    assert result is scope_run
    assert set(captured) == {
        "amount_ratio", "transfer_scope", "transfer_materials", "transfer_relations", "illegal_subcontract"
    }
    assert all(values.get("scope_policy_enabled") is True
               and values.get("general_source_state") == "absent"
               for values in captured.values())


def test_stale_optional_scope_failure_does_not_poison_current_check_run(db_session, monkeypatch):
    from app.plus.blockchain_supervision import service
    from app.plus.blockchain_supervision.models import BsTaskEvent

    monkeypatch.setenv(scope.SCOPE_POLICY_ENV, "true")
    task = _db_task(db_session)
    seed = service.initialize_reference_review(db_session, task)
    db_session.commit()
    plan = _check_plan(db_session, task, seed["review_run_id"])
    stale = service._begin_optional_scope_check_run(db_session, task, plan)
    current = service._begin_optional_scope_check_run(db_session, task, plan)

    service._record_optional_scope_check_failure(db_session, task, stale)
    assert db_session.query(BsTaskEvent).filter_by(
        task_id=task.id, event_type=scope.CHECK_FAILED_EVENT).count() == 0

    service._record_optional_scope_check_failure(db_session, task, current)
    failure = db_session.query(BsTaskEvent).filter_by(
        task_id=task.id, event_type=scope.CHECK_FAILED_EVENT).one()
    coverage = service.optional_scope_coverage(db_session, task)

    assert coverage["state"] == "failed"
    assert service.event_dto(failure)["payload"] == {}


def test_completed_scope_coverage_and_findings_commit_together(db_session, monkeypatch):
    from app.plus.blockchain_supervision import knowledge_runs, service
    from app.plus.blockchain_supervision.models import BsFinding, BsTaskEvent

    monkeypatch.setenv(scope.SCOPE_POLICY_ENV, "true")
    task = _db_task(db_session)
    seed = service.initialize_reference_review(db_session, task)
    db_session.commit()
    plan = _check_plan(db_session, task, seed["review_run_id"])
    scope_run = service._begin_optional_scope_check_run(db_session, task, plan)
    monkeypatch.setattr(knowledge_runs, "retrieve_reference_run", lambda _db, _task, _plan: {
        "run_id": plan["run_id"], "review_run_id": seed["review_run_id"],
        "group_id": "a1", "state": "completed", "references": [], "limitations": [],
    })
    monkeypatch.setattr(knowledge_runs, "assert_reference_run_current", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(knowledge_runs, "link_reference_findings", lambda *_args, **_kwargs: None)
    finding = BsFinding(task_id=task.id, item_id="T15-04", kind="transfer_scope",
                        status="miss", message="未命中：独立于总包的规则结果")

    service._finish_reference_run(db_session, task, plan, [finding], scope_run=scope_run)

    recorded = db_session.query(BsTaskEvent).filter_by(
        task_id=task.id, event_type=scope.CHECK_RECORDED_EVENT).one()
    saved_finding = db_session.query(BsFinding).filter_by(
        task_id=task.id, item_id="T15-04").one()
    assert recorded.payload_json["check_run_id"] == scope_run["check_run_id"]
    assert recorded.payload_json["rules"][0]["finding_ids"] == []
    assert saved_finding.id is not None


def test_review_version_freezes_scope_coverage_and_legacy_version_stays_unknown(db_session, monkeypatch):
    from app.plus.blockchain_supervision import service
    from app.plus.blockchain_supervision.models import BsReviewVersion

    monkeypatch.setenv(scope.SCOPE_POLICY_ENV, "true")
    task = _db_task(db_session)
    seed = service.initialize_reference_review(db_session, task)
    db_session.commit()
    plan = _check_plan(db_session, task, seed["review_run_id"])
    current = service._begin_optional_scope_check_run(db_session, task, plan)
    from app.plus.blockchain_supervision import knowledge_runs
    monkeypatch.setattr(knowledge_runs, "retrieve_reference_run", lambda _db, _task, _plan: {
        "run_id": plan["run_id"], "review_run_id": seed["review_run_id"],
        "group_id": "a1", "state": "completed", "references": [], "limitations": [],
    })
    monkeypatch.setattr(knowledge_runs, "assert_reference_run_current", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(knowledge_runs, "link_reference_findings", lambda *_args, **_kwargs: None)
    service._finish_reference_run(db_session, task, plan, [], scope_run=current)
    coverage = service.optional_scope_coverage(db_session, task)
    version = service.create_review_version(db_session, task, trigger="completed")

    assert version.snapshot_json["scope_coverage"] == coverage
    assert version.snapshot_json["report"]["scope_coverage"] == coverage
    assert version.snapshot_json["opinion"]["scope_coverage"] == coverage
    legacy = BsReviewVersion(task_id=task.id, version_no=99, trigger="completed",
                             snapshot_json={"schema_version": "bs-review-version.v1"})
    db_session.add(legacy)
    db_session.flush()
    assert legacy.snapshot_json.get("scope_coverage") is None


def test_todo_endpoint_returns_scope_coverage_as_a_separate_projection(db_session, monkeypatch):
    from app.plus.blockchain_supervision import router, service

    monkeypatch.setenv(scope.SCOPE_POLICY_ENV, "true")
    task = _db_task(db_session)
    seed = service.initialize_reference_review(db_session, task)
    db_session.commit()
    monkeypatch.setattr(router, "_get_task", lambda _db, _task_id, _user: task)

    todo = router.get_todo(task.id, db_session, user=object())

    assert todo["scope_coverage"]["generation_id"] == seed["review_run_id"]
    assert todo["scope_coverage"]["state"] == "incomplete"
    assert todo["scope_coverage"]["limitations"] == []
    assert todo["total_undecidable"] == 0


def test_scope_coverage_readiness_allows_only_final_generation_projection():
    from app.plus.blockchain_supervision import reference_snapshots, service

    task = SimpleNamespace(options_snapshot_json={
        reference_snapshots.REVIEW_POINTER: "generation-1",
    })
    base = {
        "schema_version": "bs-scope-coverage.v1",
        "generation_id": "generation-1",
        "policy": scope.POLICY_VERSION,
        "limitations": [],
    }

    assert service._scope_coverage_readiness_error(
        task, {**base, "state": "available"}) is None
    assert service._scope_coverage_readiness_error(
        task, {**base, "state": "partial", "limitations": [{
            "rule_id": "T15-01", "counts_as_finding": False,
            "counts_as_pass": False, "blocks_report": False,
        }]}) is None
    assert service._scope_coverage_readiness_error(
        task, {**base, "state": "legacy_unknown", "policy": "baseline"}) is None

    for state in ("running", "incomplete", "failed", "unavailable"):
        result = service._scope_coverage_readiness_error(task, {**base, "state": state})
        assert result["code"] == "scope_coverage_%s" % state

    mismatch = service._scope_coverage_readiness_error(
        task, {**base, "generation_id": "generation-old", "state": "partial"})
    assert mismatch["code"] == "scope_coverage_unavailable"

    no_current_generation = SimpleNamespace(options_snapshot_json={})
    mismatch = service._scope_coverage_readiness_error(
        no_current_generation, {**base, "state": "partial"})
    assert mismatch["code"] == "scope_coverage_unavailable"


def test_unsupported_enabled_scope_policy_blocks_readiness():
    from app.plus.blockchain_supervision import reference_snapshots, service

    task = SimpleNamespace(options_snapshot_json={
        reference_snapshots.REVIEW_POINTER: "generation-1",
    })
    coverage = scope.project_scope_coverage(
        generation_id="generation-1",
        policy={"enabled": True, "policy": "optional-source-scope.v2"},
        scope_snapshots=[], current_pointer=None, started_events=[], terminal_events=[],
    )

    assert coverage["state"] == "unavailable"
    error = service._scope_coverage_readiness_error(task, coverage)
    assert error["code"] == "scope_coverage_unavailable"


def test_unknown_rule_in_terminal_coverage_fails_closed():
    snapshot = _snapshot(rule_ids=("T15-01",))
    terminal = _recorded(rule_ids=("T15-01",))
    terminal["payload"]["rules"][0]["rule_id"] = "UNKNOWN"

    coverage = scope.project_scope_coverage(
        generation_id="generation-1",
        policy={"enabled": True, "policy": scope.POLICY_VERSION},
        scope_snapshots=[snapshot], current_pointer=_pointer(),
        started_events=[_started()], terminal_events=[terminal],
    )

    assert coverage["state"] == "unavailable"


def test_report_output_readiness_returns_projection_and_blocks_incomplete_scope(monkeypatch):
    from app.plus.blockchain_supervision import reference_snapshots, service

    task = SimpleNamespace(options_snapshot_json={
        reference_snapshots.REVIEW_POINTER: "generation-1",
    })
    coverage = {
        "schema_version": scope.SCOPE_COVERAGE_SCHEMA,
        "generation_id": "generation-1", "policy": scope.POLICY_VERSION,
        "state": "partial", "limitations": [{
            "rule_id": "T15-01", "counts_as_finding": False,
            "counts_as_pass": False, "blocks_report": False,
        }],
    }
    monkeypatch.setattr(service, "_report_output_readiness_base", lambda *_args, **_kwargs: {
        "ok": True, "missing_groups": [], "pending_groups": [],
    })
    monkeypatch.setattr(service, "optional_scope_coverage", lambda *_args: coverage)

    result = service.report_output_readiness(None, task)
    assert result["ok"] is True
    assert result["scope_coverage"] is coverage

    coverage["state"] = "incomplete"
    result = service.report_output_readiness(None, task)
    assert result["ok"] is False
    assert result["code"] == "scope_coverage_incomplete"
    assert result["scope_coverage"] is coverage


def test_current_report_and_opinion_payloads_reuse_readiness_projection(monkeypatch):
    from app.plus.blockchain_supervision import knowledge_runs, reference_snapshots, router

    coverage = {
        "generation_id": "generation-1", "policy": scope.POLICY_VERSION,
        "state": "partial", "limitations": [{
            "rule_id": "T15-01", "counts_as_finding": False,
            "counts_as_pass": False, "blocks_report": False,
        }],
    }
    task = SimpleNamespace(options_snapshot_json={})
    captured = []

    def build(_db, _task, *, scope_coverage=None):
        captured.append(scope_coverage)
        return {"meta": {}, "scope_coverage": scope_coverage}

    monkeypatch.setattr(router, "_require_current_output_ready", lambda *_args, **_kwargs: {
        "ok": True, "scope_coverage": coverage,
    })
    monkeypatch.setattr(router.reference_visibility, "snapshot_restricted", lambda *_args: False)
    monkeypatch.setattr(router.reference_visibility, "run_restricted", lambda *_args: False)
    monkeypatch.setattr(reference_snapshots, "get_reference_snapshot", lambda *_args: None)
    monkeypatch.setattr(knowledge_runs, "get_reference_run", lambda *_args: None)
    monkeypatch.setattr(router.reporting, "build_report", build)
    monkeypatch.setattr(router.reporting, "build_opinion", build)
    monkeypatch.setattr(router, "_mark_stage", lambda output, _stage: output)
    monkeypatch.setattr(router, "_attach_generation_identity", lambda output, **_kwargs: output)

    for kind in ("report", "opinion"):
        output = router._output_payload(None, task, kind, "draft", None, user=object())
        assert output["scope_coverage"] is coverage
    assert captured == [coverage, coverage]


def test_generation_and_review_version_freeze_source_set_members(db_session, monkeypatch):
    from app.plus.blockchain_supervision import service
    from app.plus.blockchain_supervision.models import BsMaterial, BsTaskEvent

    monkeypatch.setenv(scope.SCOPE_POLICY_ENV, "true")
    task = _db_task(db_session)
    for name in ("总包主册.pdf", "总包补充协议.pdf"):
        db_session.add(BsMaterial(
            task_id=task.id, file_name=name, role="general", role_confirmed=True,
            general_source_set_id="set-a", general_source_set_version=3,
            parse_state="done", pages_json=[{"page": 1, "text": "总包合同正文"}],
            degradations_json=[],
        ))
    db_session.commit()
    material_ids = [row.id for row in db_session.query(BsMaterial)
                    .filter_by(task_id=task.id).order_by(BsMaterial.id).all()]

    service.initialize_reference_review(db_session, task)
    event = db_session.query(BsTaskEvent).filter_by(
        task_id=task.id, event_type=scope.SCOPE_SNAPSHOT_EVENT).one()
    expected = [{"source_set_id": "set-a", "relation_version": 3,
                 "member_material_ids": material_ids}]

    assert event.payload_json["source"]["source_sets"] == expected
    assert service._review_version_snapshot(db_session, task, trigger="completed")[
        "scope_coverage"]["source"]["source_sets"] == expected


def test_legacy_v1_scope_snapshot_without_source_sets_remains_readable():
    snapshot = scope.build_scope_snapshot(
        generation_id="review-v1",
        policy=scope.POLICY_VERSION,
        ruleset_version="rules-v1",
        ruleset_fingerprint="rules-fingerprint",
        material_fingerprint="materials-fingerprint",
        source_state="absent",
        source_material_ids=[],
        rule_ids=["T15-01"],
    )
    snapshot["schema_version"] = scope.LEGACY_SCOPE_SNAPSHOT_SCHEMA
    snapshot["source"].pop("source_sets")

    assert scope._valid_snapshot(snapshot, "review-v1")
    coverage = scope.project_scope_coverage(
        generation_id="review-v1",
        policy={"enabled": True, "policy": scope.POLICY_VERSION},
        scope_snapshots=[snapshot],
        current_pointer=None,
        started_events=[],
        terminal_events=[],
    )

    assert coverage["state"] == "incomplete"
    assert coverage["source"]["source_sets"] == []


def test_source_set_association_is_setup_scoped_audited_and_versioned(db_session):
    from app.plus.blockchain_supervision import service
    from app.plus.blockchain_supervision.models import BsMaterial, BsTaskEvent

    task = _db_task(db_session)
    materials = [BsMaterial(task_id=task.id, file_name=f"general-{index}.pdf",
                            role="general", role_confirmed=True, parse_state="done",
                            pages_json=[{"page": 1, "text": "contract text"}], degradations_json=[])
                 for index in range(1, 4)]
    labor = BsMaterial(task_id=task.id, file_name="labor.pdf", role="labor", role_confirmed=True,
                       parse_state="done", pages_json=[{"page": 1, "text": "labor contract"}],
                       degradations_json=[])
    db_session.add_all([*materials, labor])
    db_session.commit()
    ids = [item.id for item in materials]

    first = service.associate_general_source_set(db_session, task, ids[:2], operator=17)
    assert not service.validate_task_materials(db_session, task)["hard_ok"]
    assert first["relation_version"] == 1
    assert first["material_ids"] == ids[:2]

    with pytest.raises(ValueError, match="全部成员"):
        service.associate_general_source_set(db_session, task, [ids[0], ids[2]], operator=17)

    extended = service.associate_general_source_set(db_session, task, ids, operator=17)
    assert extended["source_set_id"] == first["source_set_id"]
    assert extended["relation_version"] == 2
    assert service.validate_task_materials(db_session, task)["hard_ok"]

    service.remove_general_source_set_member(db_session, task, ids[2], operator=17)
    assert not service.validate_task_materials(db_session, task)["hard_ok"]
    rows = db_session.query(BsMaterial).filter(BsMaterial.id.in_(ids)).order_by(BsMaterial.id).all()
    assert [row.general_source_set_id for row in rows] == [first["source_set_id"]] * 2 + [None]
    assert [row.general_source_set_version for row in rows] == [3, 3, None]

    service.remove_general_source_set_member(db_session, task, ids[0], operator=17)
    assert not service.validate_task_materials(db_session, task)["hard_ok"]
    rows = db_session.query(BsMaterial).filter(BsMaterial.id.in_(ids)).order_by(BsMaterial.id).all()
    assert all(row.general_source_set_id is None and row.general_source_set_version is None for row in rows)

    events = db_session.query(BsTaskEvent).filter_by(
        task_id=task.id, event_type="material.general_source_set_changed").order_by(BsTaskEvent.sequence).all()
    assert len(events) == 4
    assert events[0].payload_json["operator"] == 17
    assert events[0].payload_json["material_ids"] == ids[:2]
    assert events[1].payload_json["relation_version"] == 2


def test_role_change_and_delete_cannot_leave_or_restore_single_member_source_sets(db_session):
    from app.plus.blockchain_supervision import service
    from app.plus.blockchain_supervision.models import BsMaterial, BsTaskEvent

    task = _db_task(db_session)
    materials = [BsMaterial(task_id=task.id, file_name=f"general-{index}.pdf",
                            role="general", role_confirmed=True, parse_state="done",
                            pages_json=[{"page": 1, "text": "contract text"}], degradations_json=[])
                 for index in range(1, 5)]
    db_session.add_all(materials)
    db_session.commit()
    ids = [item.id for item in materials]
    first = service.associate_general_source_set(db_session, task, ids[:2], operator=17)
    second = service.associate_general_source_set(db_session, task, ids[2:], operator=17)

    service.set_material_role(db_session, task, ids[0], "unknown", operator=17)
    service.delete_material(db_session, task, ids[2], operator=17)

    rows = db_session.query(BsMaterial).filter(BsMaterial.id.in_([ids[1], ids[3]])).all()
    assert all(row.general_source_set_id is None and row.general_source_set_version is None for row in rows)
    changes = db_session.query(BsTaskEvent).filter_by(
        task_id=task.id, event_type="material.general_source_set_changed").all()
    assert len(changes) == 4
    assert all(row.payload_json["operator"] == 17 for row in changes)
    assert {row.payload_json["source_set_id"] for row in changes} == {
        first["source_set_id"], second["source_set_id"],
    }


def test_replacing_source_set_member_requires_explicit_reassociation(db_session):
    from app.plus.blockchain_supervision import service
    from app.plus.blockchain_supervision.models import BsMaterial

    task = _db_task(db_session)
    original = BsMaterial(
        task_id=task.id, file_name="general-original.pdf", role="general", role_confirmed=True,
        material_category="general", parse_state="done", pages_json=[{"page": 1, "text": "contract"}],
        degradations_json=[], general_source_set_id="set-a", general_source_set_version=1,
        revision_no=1,
    )
    sibling = BsMaterial(
        task_id=task.id, file_name="general-appendix.pdf", role="general", role_confirmed=True,
        material_category="general", parse_state="done", pages_json=[{"page": 1, "text": "appendix"}],
        degradations_json=[], general_source_set_id="set-a", general_source_set_version=1,
    )
    db_session.add_all([original, sibling])
    db_session.flush()
    replacement = BsMaterial(
        task_id=task.id, file_name="general-replacement.pdf", role="general", role_confirmed=True,
        material_category="general", parse_state="done", pages_json=[{"page": 1, "text": "contract"}],
        degradations_json=[], replacement_target_id=original.id,
    )
    db_session.add(replacement)
    db_session.commit()

    service.activate_material_replacement(db_session, task, original.id, replacement.id, operator=17)
    current_general = [material for material in service._rule_materials(db_session, task)
                       if material.role == "general"]
    classify = lambda rows: scope.classify_general_source_state(
        rows, readable=lambda _material: True, reading_gap=lambda _material: None,
    )

    assert {material.id for material in current_general} == {sibling.id, replacement.id}
    assert replacement.general_source_set_id is None
    assert classify(current_general) == "conflicted"

    linked = service.associate_general_source_set(
        db_session, task, [sibling.id, replacement.id], operator=17,
    )
    current_general = [material for material in service._rule_materials(db_session, task)
                       if material.role == "general"]

    assert linked["source_set_id"] == "set-a"
    assert linked["relation_version"] == 2
    assert classify(current_general) == "readable"
    db_session.refresh(original)
    assert original.general_source_set_id == "set-a"
    assert original.superseded_by_id == replacement.id


def test_standalone_schema_adds_source_set_columns_idempotently_to_legacy_sqlite():
    from sqlalchemy import create_engine, inspect, text
    from app.standalone.schema_migrations import ensure_standalone_schema

    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE bs_material (id INTEGER PRIMARY KEY)"))

        ensure_standalone_schema(engine)
        ensure_standalone_schema(engine)

        columns = {column["name"] for column in inspect(engine).get_columns("bs_material")}
        assert {"general_source_set_id", "general_source_set_version"} <= columns
    finally:
        engine.dispose()
