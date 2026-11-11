import copy
import json

import pytest

from app.plus.blockchain_supervision import knowledge_runs, report_policy


def _policy():
    return {"schema_version": "bs-report-policy.v1", "policy_id": "baseline", "version": "1.0.0",
            "rules": [{"id": "certificate-A02", "kind": "completeness", "item_id": "RC-A02",
                       "point": "主体资料", "subject_types": ["professional", "labor"],
                       "predicate": "required_certificate_missing", "verdict": "不符合",
                       "basis": "报审资料附件要求", "approved": True}]}


def test_policy_validation_is_strict_and_freezes_detached_canonical_content():
    value = _policy()
    frozen = report_policy.freeze_policy(value)
    value["rules"][0]["item_id"] = "RC-A03"
    assert frozen["content"]["rules"][0]["item_id"] == "RC-A02"
    assert frozen["sha256"] == report_policy.policy_digest(frozen["content"])
    assert report_policy.policy_digest(json.loads(json.dumps(frozen["content"], ensure_ascii=False, indent=2))) == frozen["sha256"]
    assert report_policy.policy_digest(value) != frozen["sha256"]


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(extra="unexpected"),
    lambda p: p.pop("version"),
    lambda p: p.update(version="bad version"),
    lambda p: p["rules"][0].update(predicate="eval"),
    lambda p: p["rules"][0].update(verdict="基本符合"),
    lambda p: p["rules"][0].update(approved=False),
    lambda p: p["rules"][0].update(subject_types=[{}]),
    lambda p: p["rules"].append(copy.deepcopy(p["rules"][0])),
    lambda p: p["rules"].append({**p["rules"][0], "id": "overlapping"}),
])
def test_invalid_unapproved_or_conflicting_policies_are_rejected(mutation):
    policy = _policy()
    mutation(policy)
    with pytest.raises(report_policy.InvalidReportPolicy):
        report_policy.freeze_policy(policy)


def test_default_policy_has_three_confirmed_certificates_and_no_basic_pass():
    policy = report_policy.load_current_policy()
    assert {r["item_id"] for r in policy["rules"]} == {"RC-A02", "RC-A03", "RC-A04"}
    assert all(r["verdict"] != "基本符合" for r in policy["rules"])


def test_policy_json_rejects_duplicate_fields_instead_of_silently_overwriting(tmp_path, monkeypatch):
    monkeypatch.setattr(report_policy, "POLICY_DIR", tmp_path)
    (tmp_path / "policy.json").write_text('{"schema_version":"bs-report-policy.v1",'
        '"policy_id":"one","policy_id":"two","version":"1.0.0","rules":[]}', encoding="utf-8")
    with pytest.raises(report_policy.InvalidReportPolicy):
        report_policy.load_current_policy()


def test_current_policy_cannot_omit_bindings_to_bypass_coverage_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(report_policy, "POLICY_DIR", tmp_path)
    (tmp_path / "policy.json").write_text(json.dumps(_policy(), ensure_ascii=False), encoding="utf-8")
    with pytest.raises(report_policy.InvalidReportPolicy):
        report_policy.load_current_policy()


def test_report_policy_files_do_not_change_fact_rules_fingerprint(tmp_path, monkeypatch):
    before = knowledge_runs.rule_fingerprint()
    current = report_policy.load_current_policy()
    monkeypatch.setattr(report_policy, "POLICY_DIR", tmp_path)
    (tmp_path / "policy.json").write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
    report_policy.load_current_policy()
    assert knowledge_runs.rule_fingerprint() == before


def test_review_generation_freezes_policy_without_changing_old_generations(db_session, monkeypatch):
    from app.plus.blockchain_supervision import reference_snapshots, service
    from app.plus.blockchain_supervision.models import BsTask, BsTaskEvent

    task = BsTask(conversation_id=101, title="规则快照", created_by=1, created_by_type="tester",
                  options_snapshot_json={}, scope_a1=True)
    db_session.add(task)
    db_session.flush()
    policy = _policy()
    monkeypatch.setattr(report_policy, "load_current_policy", lambda: copy.deepcopy(policy))
    monkeypatch.setattr(knowledge_runs, "load_reference_package", lambda **kwargs: {"version": "test"})
    monkeypatch.setattr(reference_snapshots, "build_review_seed", lambda *args, **kwargs: {
        "review_run_id": kwargs["review_run_id"], "expected_groups": []})
    first = service.initialize_reference_review(db_session, task)
    db_session.flush()
    original_digest = first["report_policy"]["sha256"]
    policy["version"] = "1.0.1"
    second = service.initialize_reference_review(db_session, task)
    db_session.flush()
    assert second["report_policy"]["sha256"] != original_digest
    saved = db_session.query(BsTaskEvent).filter_by(task_id=task.id,
        event_type=reference_snapshots.REVIEW_EVENT).order_by(BsTaskEvent.sequence).all()
    assert saved[0].payload_json["report_policy"]["sha256"] == original_digest
    assert saved[0].payload_json["report_policy"]["content"]["version"] == "1.0.0"


def test_real_review_seed_exposes_same_frozen_policy_in_snapshot(db_session):
    from app.plus.blockchain_supervision import reference_snapshots, service
    from app.plus.blockchain_supervision.models import BsTask

    task = BsTask(conversation_id=101, title="真实审查种子", created_by=1, created_by_type="tester",
                  options_snapshot_json={}, scope_a1=True)
    db_session.add(task)
    db_session.flush()
    seed = service.initialize_reference_review(db_session, task)
    db_session.flush()
    snapshot = reference_snapshots.get_reference_snapshot(db_session, task)
    assert snapshot["report_policy"] == seed["report_policy"]
    assert report_policy.read_frozen_policy(snapshot["report_policy"])["policy_id"] == "confirmed-certificate-baseline"


def test_actual_report_build_uses_review_seed_policy_after_current_changes(db_session, monkeypatch):
    from app.plus.blockchain_supervision import reporting, service
    from app.plus.blockchain_supervision.models import BsTask
    policy = _policy()
    monkeypatch.setattr(report_policy, "load_current_policy", lambda: copy.deepcopy(policy))
    task = BsTask(conversation_id=101, title="冻结规则报告", created_by=1, created_by_type="tester",
                  options_snapshot_json={}, scope_a1=True)
    db_session.add(task)
    db_session.flush()
    seed = service.initialize_reference_review(db_session, task)
    db_session.flush()
    policy["version"] = "1.0.1"
    report = reporting.build_report(db_session, task)
    assert report["reference_snapshot"]["report_policy"] == seed["report_policy"]
    assert report["reference_snapshot"]["report_policy"]["content"]["version"] == "1.0.0"
    assert len(report["business_document"]["sections"]) == 5


def test_evidence_bundle_keeps_policy_version_and_activation_audit(db_session, monkeypatch):
    from io import BytesIO
    from zipfile import ZipFile
    from app.plus.blockchain_supervision import export, service
    from app.plus.blockchain_supervision.models import BsTask
    policy = _policy()
    monkeypatch.setattr(report_policy, "load_current_policy", lambda: copy.deepcopy(policy))
    task = BsTask(conversation_id=101, title="规则证据包", created_by=1, created_by_type="tester",
                  options_snapshot_json={}, scope_a1=True)
    db_session.add(task)
    db_session.flush()
    seed = service.initialize_reference_review(db_session, task)
    db_session.flush()
    policy["version"] = "1.0.1"
    target = report_policy.freeze_policy(policy)
    service.activate_report_policy(db_session, task, expected_review_id=seed["review_run_id"],
        expected_previous_sha256=seed["report_policy"]["sha256"], expected_target_sha256=target["sha256"],
        operator_id=1, operator_type="tester")
    db_session.flush()
    raw, _digest, _time = export.evidence_bundle_bytes(db_session, task)
    with ZipFile(BytesIO(raw)) as bundle:
        report = json.loads(bundle.read("report.json"))
        opinion = json.loads(bundle.read("opinion.json"))
        events = json.loads(bundle.read("events.json"))
    assert report["reference_snapshot"]["report_policy"]["sha256"] == target["sha256"]
    assert target["sha256"] in json.dumps(opinion, ensure_ascii=False)
    assert target["sha256"] in json.dumps(events, ensure_ascii=False)


def test_invalid_policy_does_not_clear_existing_review_pointer(db_session, monkeypatch):
    from app.plus.blockchain_supervision import reference_snapshots, service
    from app.plus.blockchain_supervision.models import BsTask
    task = BsTask(conversation_id=101, title="保留旧审查", created_by=1, created_by_type="tester",
                  options_snapshot_json={reference_snapshots.REVIEW_POINTER: "existing-review"}, scope_a1=True)
    db_session.add(task)
    db_session.flush()
    bad = _policy()
    bad["rules"][0]["verdict"] = "基本符合"
    monkeypatch.setattr(report_policy, "load_current_policy", lambda: bad)
    with pytest.raises(report_policy.InvalidReportPolicy):
        service.initialize_reference_review(db_session, task)
    assert task.options_snapshot_json[reference_snapshots.REVIEW_POINTER] == "existing-review"


def test_invalid_policy_rolls_back_actual_start_gate_transaction(db_session, monkeypatch):
    from app.plus.blockchain_supervision import service
    from app.plus.blockchain_supervision.models import BsTask, BsTaskEvent
    task = BsTask(conversation_id=101, title="配置无效的启动", created_by=1, created_by_type="tester",
                  options_snapshot_json={}, state="setup", scope_a1=True)
    db_session.add(task)
    db_session.commit()
    task_id = task.id
    bad = _policy()
    bad["rules"][0]["verdict"] = "基本符合"
    monkeypatch.setattr(report_policy, "load_current_policy", lambda: bad)
    monkeypatch.setattr(service, "_subject_start_gate", lambda *args: ([], None))
    monkeypatch.setattr(service, "_new_frozen_ruleset_inputs", lambda: ({"version": "test"}, "test-fingerprint"))
    monkeypatch.setattr(service, "_missing_section_suggestions", lambda *args: {})
    with pytest.raises(report_policy.InvalidReportPolicy):
        service._apply_start_gates(db_session, task, check={"composition": [], "soft_gaps": []})
    db_session.refresh(task)
    assert task.state == "setup" and task.options_snapshot_json == {}
    assert db_session.query(BsTaskEvent).filter_by(task_id=task_id).count() == 0


def test_background_preparation_recovers_with_policy_specific_message(db_session, monkeypatch):
    from app.plus.blockchain_supervision import service
    from app.plus.blockchain_supervision.models import BsTask, BsTaskEvent
    task = BsTask(conversation_id=101, title="后台配置异常", created_by=1, created_by_type="tester",
                  options_snapshot_json={}, state="preparing", scope_a1=True)
    db_session.add(task)
    db_session.commit()
    task_id = task.id
    monkeypatch.setattr(service, "pending_pdf_materials", lambda *args: [])
    monkeypatch.setattr(service, "zip_containers", lambda *args: [])
    monkeypatch.setattr(service, "_settle_children", lambda *args, **kwargs: None)
    def invalid_finish(*args, **kwargs):
        raise report_policy.InvalidReportPolicy("模拟配置失效")
    monkeypatch.setattr(service, "_finish_preparation", invalid_finish)
    service._prepare_zip_background(task_id, db_session.get_bind(), operator=1)
    db_session.expire_all()
    assert db_session.get(BsTask, task_id).state == "setup"
    event = db_session.query(BsTaskEvent).filter_by(task_id=task_id, event_type="zip.preparation_failed").one()
    assert event.payload_json["reason"] == "report_policy_invalid"
    assert "报告规则" in service.event_dto(event)["message"]


def test_explicit_report_policy_activation_keeps_review_inputs_and_audits_version(db_session, monkeypatch):
    from app.plus.blockchain_supervision import reference_snapshots, service
    from app.plus.blockchain_supervision.models import BsTask, BsTaskEvent, BsReviewVersion
    task = BsTask(conversation_id=101, title="显式切换报告规则", created_by=1, created_by_type="tester",
                  options_snapshot_json={}, scope_a1=True)
    db_session.add(task)
    db_session.flush()
    policy = _policy()
    monkeypatch.setattr(report_policy, "load_current_policy", lambda: copy.deepcopy(policy))
    seed = service.initialize_reference_review(db_session, task)
    db_session.flush()
    historical = BsReviewVersion(task_id=task.id, version_no=1, trigger="completed",
        snapshot_json={"reference_snapshot": {"report_policy": copy.deepcopy(seed["report_policy"])}})
    db_session.add(historical)
    db_session.flush()
    material_before = knowledge_runs.material_fingerprint(db_session, task)
    rule_before = knowledge_runs.rule_fingerprint()
    policy["version"] = "1.0.1"
    target = report_policy.freeze_policy(policy)
    result = service.activate_report_policy(db_session, task, expected_review_id=seed["review_run_id"],
        expected_previous_sha256=seed["report_policy"]["sha256"], expected_target_sha256=target["sha256"],
        operator_id=1, operator_type="tester")
    db_session.flush()
    assert result["sha256"] == target["sha256"]
    assert reference_snapshots.get_reference_snapshot(db_session, task)["report_policy"] == target
    assert historical.snapshot_json["reference_snapshot"]["report_policy"] == seed["report_policy"]
    assert knowledge_runs.material_fingerprint(db_session, task) == material_before
    assert knowledge_runs.rule_fingerprint() == rule_before
    events = db_session.query(BsTaskEvent).filter_by(task_id=task.id, event_type="report.policy_activated").all()
    assert len(events) == 1 and events[0].payload_json["previous_sha256"] == seed["report_policy"]["sha256"]
    with pytest.raises(ValueError):
        service.activate_report_policy(db_session, task, expected_review_id=seed["review_run_id"],
            expected_previous_sha256=seed["report_policy"]["sha256"], expected_target_sha256=target["sha256"],
            operator_id=1, operator_type="tester")


def test_saved_report_version_keeps_its_policy_when_current_policy_is_activated(db_session, monkeypatch):
    from io import BytesIO
    from zipfile import ZipFile
    from app.plus.blockchain_supervision import export, reporting, service
    from app.plus.blockchain_supervision.router import _attach_generation_identity
    from app.plus.blockchain_supervision.models import BsTask
    policy = _policy()
    monkeypatch.setattr(report_policy, "load_current_policy", lambda: copy.deepcopy(policy))
    task = BsTask(conversation_id=101, title="历史报告规则", created_by=1, created_by_type="tester",
                  options_snapshot_json={}, scope_a1=True)
    db_session.add(task)
    db_session.flush()
    seed = service.initialize_reference_review(db_session, task)
    db_session.flush()
    saved = service.create_review_version(db_session, task, trigger="completed")
    old_report = copy.deepcopy(saved.snapshot_json["report"])
    assert old_report["reference_snapshot"]["report_policy"]["sha256"] == seed["report_policy"]["sha256"]
    before = _attach_generation_identity(reporting.build_report(db_session, task), task=task, version_no=None, db=db_session)
    assert before["meta"]["review_version_no"] == saved.version_no
    policy["version"] = "1.0.1"
    target = report_policy.freeze_policy(policy)
    service.activate_report_policy(db_session, task, expected_review_id=seed["review_run_id"],
        expected_previous_sha256=seed["report_policy"]["sha256"], expected_target_sha256=target["sha256"],
        operator_id=1, operator_type="tester")
    db_session.flush()
    current = reporting.build_report(db_session, task)
    assert current["reference_snapshot"]["report_policy"]["sha256"] == target["sha256"]
    current = _attach_generation_identity(current, task=task, version_no=None, db=db_session)
    assert current["meta"]["review_version_no"] is None
    assert current["meta"]["report_policy_sha256"] == target["sha256"]
    raw, _, _ = export.evidence_bundle_bytes(db_session, task)
    with ZipFile(BytesIO(raw)) as bundle:
        bundled = json.loads(bundle.read("report.json"))
        bundled_opinion = json.loads(bundle.read("opinion.json"))["opinion"]
    assert bundled["meta"]["review_version_no"] is None
    assert bundled["meta"]["report_policy_sha256"] == target["sha256"]
    assert bundled_opinion["meta"]["review_version_no"] is None
    assert bundled_opinion["meta"]["report_policy_sha256"] == target["sha256"]
    assert saved.snapshot_json["report"] == old_report
def test_frozen_policy_rejects_tampering_and_never_reloads_current_policy():
    frozen = report_policy.freeze_policy(_policy())
    assert report_policy.read_frozen_policy(frozen)["version"] == "1.0.0"
    frozen["content"]["version"] = "tampered"
    with pytest.raises(report_policy.InvalidReportPolicy):
        report_policy.read_frozen_policy(frozen)


def test_report_uses_frozen_policy_and_confirmed_subject_not_current_file(monkeypatch):
    from app.plus.blockchain_supervision.report_document import build_report_document
    item = {"item_id": "RC-A02", "subject_id": 7, "label": "营业执照", "status": "hit",
            "material_missing": True, "source": "附件要求", "message": "报审资料缺营业执照"}
    report = {"sections": [{"kind": "completeness", "items": [item]}],
              "coverage": {"selected_groups": ["completeness"], "completed_groups": ["completeness"]},
              "checklist_summary": {"subjects": [{"subject_id": 7, "status": "ready",
                  "subject_type": "labor", "type_confirmed": True, "type_conflict": False,
                  "checklist_items": []}]},
              "reference_snapshot": {"report_policy": report_policy.freeze_policy(_policy())}}
    monkeypatch.setattr(report_policy, "load_current_policy", lambda: (_ for _ in ()).throw(AssertionError("读取当前策略")))
    point = lambda: next(p for p in build_report_document(report)["sections"][0]["key_points"] if p["label"] == "主体资料")
    assert point()["verdict"] == "不符合"
    report["checklist_summary"]["subjects"][0]["type_confirmed"] = False
    assert point()["verdict"] == "待核实"
    report["checklist_summary"]["subjects"][0]["type_confirmed"] = True
    report["reference_snapshot"]["report_policy"]["content"]["rules"] = []
    with pytest.raises(report_policy.InvalidReportPolicy):
        point()


def test_empty_frozen_policy_does_not_inherit_current_hardcoded_certificate_rule():
    from app.plus.blockchain_supervision.report_document import build_report_document
    policy = _policy()
    policy["rules"] = []
    item = {"item_id": "RC-A02", "subject_id": 7, "label": "营业执照", "status": "hit",
            "material_missing": True, "source": "附件要求", "message": "报审资料缺营业执照"}
    report = {"sections": [{"kind": "completeness", "items": [item]}],
              "coverage": {"selected_groups": ["completeness"], "completed_groups": ["completeness"]},
              "checklist_summary": {"subjects": [{"subject_id": 7, "status": "ready",
                  "subject_type": "labor", "type_confirmed": True, "type_conflict": False,
                  "checklist_items": []}]},
              "reference_snapshot": {"report_policy": report_policy.freeze_policy(policy)}}
    point = next(p for p in build_report_document(report)["sections"][0]["key_points"] if p["label"] == "主体资料")
    assert point["verdict"] == "待核实"


def test_new_approved_document_rule_is_incremental_when_existing_fact_is_supported():
    """假设配置仅验证机制，不认定任何真实资料的适用性或严重程度。"""
    from app.plus.blockchain_supervision.report_document import build_report_document
    policy = _policy()
    policy["rules"].append({**policy["rules"][0], "id": "synthetic-A05", "item_id": "RC-A05",
        "predicate": "required_document_missing", "basis": "仅用于合成测试的假设依据"})
    item = {"item_id": "RC-A05", "subject_id": 7, "label": "假设资料", "status": "hit",
            "material_missing": True, "source": "假设依据", "message": "缺少假设资料"}
    report = {"sections": [{"kind": "completeness", "items": [item]}],
              "coverage": {"selected_groups": ["completeness"], "completed_groups": ["completeness"]},
              "checklist_summary": {"subjects": [{"subject_id": 7, "status": "ready",
                  "subject_type": "labor", "type_confirmed": True, "type_conflict": False,
                  "checklist_items": []}]},
              "reference_snapshot": {"report_policy": report_policy.freeze_policy(policy)}}
    point = next(p for p in build_report_document(report)["sections"][0]["key_points"] if p["label"] == "主体资料")
    assert point["verdict"] == "不符合"
    policy["rules"].pop()
    report["reference_snapshot"]["report_policy"] = report_policy.freeze_policy(policy)
    assert next(p for p in build_report_document(report)["sections"][0]["key_points"] if p["label"] == "主体资料")["verdict"] == "待核实"


def test_configured_effective_dates_require_a_matching_baseline():
    policy = _policy()
    rule = policy["rules"][0]
    rule.update(effective_from="2027-01-01", effective_to="2027-12-31")
    report_policy.freeze_policy(policy)
    assert report_policy.rule_applies_on(rule, "2027-01-01")
    assert report_policy.rule_applies_on(rule, "2027-12-31")
    assert not report_policy.rule_applies_on(rule, "2026-12-31")
    assert not report_policy.rule_applies_on(rule, None)
    rule["effective_to"] = "2026-01-01"
    with pytest.raises(report_policy.InvalidReportPolicy):
        report_policy.freeze_policy(policy)


def test_report_does_not_apply_a_future_rule_before_baseline():
    from app.plus.blockchain_supervision.report_document import build_report_document
    policy = _policy()
    policy["rules"][0]["effective_from"] = "2027-01-01"
    item = {"item_id": "RC-A02", "subject_id": 7, "label": "营业执照", "status": "hit",
            "material_missing": True, "source": "附件要求", "message": "报审资料缺营业执照"}
    report = {"meta": {"baseline_date": "2026-11-10"},
              "sections": [{"kind": "completeness", "items": [item]}],
              "coverage": {"selected_groups": ["completeness"], "completed_groups": ["completeness"]},
              "checklist_summary": {"subjects": [{"subject_id": 7, "status": "ready",
                  "subject_type": "labor", "type_confirmed": True, "type_conflict": False,
                  "checklist_items": []}]},
              "reference_snapshot": {"report_policy": report_policy.freeze_policy(policy)}}
    point = lambda: next(p for p in build_report_document(report)["sections"][0]["key_points"] if p["label"] == "主体资料")
    assert point()["verdict"] == "待核实"
    report["meta"]["baseline_date"] = "2027-01-01"
    assert point()["verdict"] == "不符合"


def test_frozen_bindings_control_supported_point_mapping_without_changing_legacy():
    from app.plus.blockchain_supervision.report_document import _field_matches
    policy = report_policy.load_current_policy()
    changed = copy.deepcopy(policy)
    original = next(row for row in changed["bindings"] if row["kind"] == "completeness" and row["field"] == "主体资料")
    original["item_ids"].remove("RC-A05")
    next(row for row in changed["bindings"] if row["kind"] == "completeness" and row["field"] == "合同及清单")["item_ids"].append("RC-A05")
    frozen = report_policy.read_frozen_policy(report_policy.freeze_policy(changed))
    item = {"kind": "completeness", "item_id": "RC-A05"}
    assert _field_matches("主体资料", item)
    assert _field_matches("主体资料", item, policy)
    assert not _field_matches("主体资料", item, frozen)
    assert _field_matches("合同及清单", item, frozen)


def test_binding_conflict_or_rule_without_matching_point_is_rejected():
    policy = report_policy.load_current_policy()
    broken = copy.deepcopy(policy)
    broken["bindings"].append({"kind": "completeness", "item_ids": ["RC-A02"], "field": "报审资料"})
    with pytest.raises(report_policy.InvalidReportPolicy):
        report_policy.freeze_policy(broken)
    broken = copy.deepcopy(policy)
    next(row for row in broken["bindings"] if row["kind"] == "completeness" and row["field"] == "主体资料")["item_ids"].remove("RC-A02")
    with pytest.raises(report_policy.InvalidReportPolicy):
        report_policy.freeze_policy(broken)
    broken = copy.deepcopy(policy)
    broken["bindings"] = None
    with pytest.raises(report_policy.InvalidReportPolicy):
        report_policy.freeze_policy(broken)


def test_policy_cannot_drop_existing_checklist_mapping_even_if_rule_removed():
    policy = report_policy.load_current_policy()
    subject_binding = next(row for row in policy["bindings"] if row["kind"] == "completeness" and row["field"] == "主体资料")
    subject_binding["item_ids"].remove("RC-A03")
    policy["rules"] = [row for row in policy["rules"] if row["item_id"] != "RC-A03"]
    with pytest.raises(report_policy.InvalidReportPolicy):
        report_policy.freeze_policy(policy)


def test_legacy_incomplete_bindings_snapshot_remains_readable():
    policy = report_policy.load_current_policy()
    for binding in policy["bindings"]:
        if binding["kind"] == "completeness" and "RC-A03" in binding["item_ids"]:
            binding["item_ids"].remove("RC-A03")
    policy["rules"] = [rule for rule in policy["rules"] if rule["item_id"] != "RC-A03"]
    snapshot = {"content": policy, "sha256": report_policy.policy_digest(policy)}
    assert report_policy.read_frozen_policy(snapshot) == policy
    with pytest.raises(report_policy.InvalidReportPolicy):
        report_policy.freeze_policy(policy)


def test_completed_task_cannot_switch_policy_without_a_new_review_generation(db_session, monkeypatch):
    from app.plus.blockchain_supervision import service
    from app.plus.blockchain_supervision.models import BsTask, BsTaskEvent
    task = BsTask(conversation_id=101, title="已完成审查", created_by=1, created_by_type="tester",
                  options_snapshot_json={}, state="done", scope_a1=True)
    db_session.add(task)
    db_session.flush()
    policy = _policy()
    monkeypatch.setattr(report_policy, "load_current_policy", lambda: copy.deepcopy(policy))
    seed = service.initialize_reference_review(db_session, task)
    db_session.flush()
    policy["version"] = "1.0.1"
    target = report_policy.freeze_policy(policy)
    with pytest.raises(ValueError, match="已完成"):
        service.activate_report_policy(db_session, task, expected_review_id=seed["review_run_id"],
            expected_previous_sha256=seed["report_policy"]["sha256"], expected_target_sha256=target["sha256"],
            operator_id=1, operator_type="tester")
    assert db_session.query(BsTaskEvent).filter_by(task_id=task.id, event_type="report.policy_activated").count() == 0


def test_default_bindings_match_existing_point_mapping_for_catalog_and_rule_kinds():
    from app.plus.blockchain_supervision.report_document import _CATEGORIES, _field_matches
    from app.plus.blockchain_supervision.rules import load_report_checklist
    policy = report_policy.load_current_policy()
    fields = {field for _title, _kinds, names in _CATEGORIES for field in names}
    items = [{"kind": "completeness", "item_id": row["id"]} for row in load_report_checklist()]
    items += [{"kind": kind, "item_id": "sample"} for kind in (
        "signature_chain", "qualification_match", "negative_list", "transfer_scope",
        "illegal_subcontract", "a1_dependency", "person_consistency", "internal_records")]
    items += [{"kind": "cert_validity", "item_id": item_id} for item_id in ("A2-03", "A2-05")]
    items += [{"kind": "cert_validity", "item_id": "A2-02", "status": "hit", "message": "「营业执照」临期"}]
    for item in items:
        assert {field for field in fields if _field_matches(field, item, policy)} == {
            field for field in fields if _field_matches(field, item)}


def test_report_projection_uses_binding_from_its_frozen_policy():
    from app.plus.blockchain_supervision.report_document import build_report_document
    old = report_policy.load_current_policy()
    changed = copy.deepcopy(old)
    next(row for row in changed["bindings"] if row["kind"] == "completeness" and row["field"] == "主体资料")["item_ids"].remove("RC-A05")
    next(row for row in changed["bindings"] if row["kind"] == "completeness" and row["field"] == "合同及清单")["item_ids"].append("RC-A05")
    report = {"sections": [{"kind": "completeness", "items": [{"item_id": "RC-A05", "subject_id": 7,
        "finding_id": 10, "label": "假设材料", "status": "undecidable", "message": "假设材料内容无法确认"}]}],
        "coverage": {"selected_groups": ["completeness"], "completed_groups": ["completeness"]},
        "checklist_summary": {"subjects": [{"subject_id": 7, "status": "ready", "subject_type": "labor",
            "type_confirmed": True, "type_conflict": False, "checklist_items": [{"item_id": "RC-A05", "finding_id": 10, "status": "undecidable"}]}]},
        "reference_snapshot": {"report_policy": report_policy.freeze_policy(old)}}
    def summaries():
        return {row["label"]: row["summary"] for row in build_report_document(report)["sections"][0]["key_points"]}
    assert "假设材料内容无法确认" in summaries()["主体资料"]
    report["reference_snapshot"]["report_policy"] = report_policy.freeze_policy(changed)
    assert "假设材料内容无法确认" in summaries()["合同及清单"]
    assert "假设材料内容无法确认" not in summaries()["主体资料"]
