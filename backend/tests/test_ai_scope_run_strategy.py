import pytest
from types import SimpleNamespace

from app.plus.blockchain_supervision import knowledge_runs, reference_snapshots
from app.plus.blockchain_supervision import ai_review_context
from app.plus.blockchain_supervision.ai_scope_selection import SCOPE_SELECTOR_ENV
from app.plus.blockchain_supervision.ai_guarded_review import _frozen_review_finding
from app.plus.blockchain_supervision.models import BsFinding, BsTask, BsTaskEvent


@pytest.mark.parametrize("seed_strategy, expected", [
    ({"enabled": True, "version": "category-adjacency-v1"},
     {"enabled": True, "version": "category-adjacency-v1"}),
    (None, {"enabled": False, "version": "baseline"}),
])
def test_reference_run_uses_strategy_frozen_in_its_review_seed(db_session, seed_strategy, expected):
    task = BsTask(
        conversation_id=101, title="MoMA 策略冻结", created_by=1, created_by_type="tester",
        options_snapshot_json={
            reference_snapshots.REVIEW_POINTER: "review-1",
            reference_snapshots.GROUP_POINTERS: {"a1": "run-1"},
        },
        scope_a1=True,
    )
    db_session.add(task)
    db_session.flush()
    seed = {
        "review_run_id": "review-1",
        "expected_groups": ["a1"],
        "steps": [],
    }
    if seed_strategy is not None:
        seed["scope_strategy"] = seed_strategy
    run_payload = {
        "run_id": "run-1", "review_run_id": "review-1", "task_id": task.id,
        "group_id": "a1", "state": "completed", "references": [], "limitations": [],
    }
    db_session.add_all([
        BsTaskEvent(task_id=task.id, sequence=1, event_type=reference_snapshots.REVIEW_EVENT,
                    payload_json=seed),
        BsTaskEvent(task_id=task.id, sequence=2, event_type=reference_snapshots.RUN_EVENT,
                    payload_json=run_payload),
    ])
    db_session.flush()

    current = knowledge_runs.get_reference_run(db_session, task, group_id="a1")

    assert current["scope_strategy"] == expected
    stored_run = db_session.query(BsTaskEvent).filter_by(
        task_id=task.id, event_type=reference_snapshots.RUN_EVENT,
    ).one()
    assert "scope_strategy" not in stored_run.payload_json


def test_review_seed_freezes_server_scope_flag_at_generation_creation(monkeypatch):
    monkeypatch.setattr(reference_snapshots, "load_basis_clauses", lambda **_kwargs: {"docs": []})
    monkeypatch.setattr(reference_snapshots, "_clause_index", lambda _library: {})
    monkeypatch.setattr(reference_snapshots, "expected_groups", lambda _task: [])
    monkeypatch.setattr(reference_snapshots, "resolve_rule_cards", lambda **_kwargs: [])
    monkeypatch.setattr(reference_snapshots, "build_review_flow_overlay", lambda **_kwargs: {"steps": []})
    monkeypatch.setattr(reference_snapshots, "load_review_flow", lambda: {"steps": []})
    monkeypatch.setattr(reference_snapshots, "_source_manifest_hash", lambda: "manifest-hash")
    monkeypatch.setattr(reference_snapshots, "_basis_snapshot_identity", lambda *_args: {
        "basis_snapshot_id": "snapshot", "source_manifest_hash": "manifest-hash",
        "profile_code": None, "jurisdiction": None, "baseline_date": None,
    })

    monkeypatch.delenv(SCOPE_SELECTOR_ENV, raising=False)
    baseline_seed = reference_snapshots.build_review_seed(
        SimpleNamespace(baseline_date=None), review_run_id="review-baseline",
        material_fingerprint="materials", rule_fingerprint="rules", package={"version": "test", "sources": []},
    )
    monkeypatch.setenv(SCOPE_SELECTOR_ENV, "true")
    candidate_seed = reference_snapshots.build_review_seed(
        SimpleNamespace(baseline_date=None), review_run_id="review-candidate",
        material_fingerprint="materials", rule_fingerprint="rules", package={"version": "test", "sources": []},
    )

    assert baseline_seed["scope_strategy"] == {"enabled": False, "version": "baseline"}
    assert candidate_seed["scope_strategy"] == {
        "enabled": True, "version": "category-adjacency-v1",
    }


def test_rollback_only_changes_new_review_generations(db_session, monkeypatch):
    task = BsTask(
        conversation_id=101, title="MoMA 回滚策略冻结", created_by=1, created_by_type="tester",
        options_snapshot_json={
            reference_snapshots.REVIEW_POINTER: "review-candidate",
            reference_snapshots.GROUP_POINTERS: {"a1": "run-candidate"},
        },
        scope_a1=True,
    )
    db_session.add(task)
    db_session.flush()
    seed = {
        "review_run_id": "review-candidate", "expected_groups": ["a1"], "steps": [],
        "scope_strategy": {"enabled": True, "version": "category-adjacency-v1"},
    }
    run_payload = {
        "run_id": "run-candidate", "review_run_id": "review-candidate", "task_id": task.id,
        "group_id": "a1", "state": "completed", "references": [], "limitations": [],
    }
    db_session.add_all([
        BsTaskEvent(task_id=task.id, sequence=1, event_type=reference_snapshots.REVIEW_EVENT,
                    payload_json=seed),
        BsTaskEvent(task_id=task.id, sequence=2, event_type=reference_snapshots.RUN_EVENT,
                    payload_json=run_payload),
    ])
    db_session.flush()

    monkeypatch.setenv(SCOPE_SELECTOR_ENV, "false")
    current = knowledge_runs.get_reference_run(db_session, task, group_id="a1")

    assert current["scope_strategy"] == {"enabled": True, "version": "category-adjacency-v1"}
    monkeypatch.setattr(reference_snapshots, "load_basis_clauses", lambda **_kwargs: {"docs": []})
    monkeypatch.setattr(reference_snapshots, "_clause_index", lambda _library: {})
    monkeypatch.setattr(reference_snapshots, "expected_groups", lambda _task: [])
    monkeypatch.setattr(reference_snapshots, "resolve_rule_cards", lambda **_kwargs: [])
    monkeypatch.setattr(reference_snapshots, "build_review_flow_overlay", lambda **_kwargs: {"steps": []})
    monkeypatch.setattr(reference_snapshots, "load_review_flow", lambda: {"steps": []})
    monkeypatch.setattr(reference_snapshots, "_source_manifest_hash", lambda: "manifest-hash")
    monkeypatch.setattr(reference_snapshots, "_basis_snapshot_identity", lambda *_args: {
        "basis_snapshot_id": "snapshot", "source_manifest_hash": "manifest-hash",
        "profile_code": None, "jurisdiction": None, "baseline_date": None,
    })
    new_seed = reference_snapshots.build_review_seed(
        SimpleNamespace(baseline_date=None, scope_a1=True, scope_a3=False),
        review_run_id="review-after-rollback",
        material_fingerprint="materials", rule_fingerprint="rules",
        package={"version": "test", "sources": []},
    )
    assert new_seed["scope_strategy"] == {"enabled": False, "version": "baseline"}


def test_frozen_candidate_strategy_reaches_review_context_selector(db_session, monkeypatch):
    task = BsTask(
        conversation_id=102, title="MoMA 选页接线", created_by=2, created_by_type="tester",
        options_snapshot_json={
            reference_snapshots.REVIEW_POINTER: "review-2",
            reference_snapshots.GROUP_POINTERS: {"a1": "run-2"},
        },
        scope_a1=True,
    )
    db_session.add(task)
    db_session.flush()
    finding = SimpleNamespace(
        id=7, task_id=task.id, subject_id=None, kind="transfer_scope", item_id="T15-01",
        status="hit", material_id=None, page=None, evidence_json=[],
    )
    material = SimpleNamespace(
        id=3, task_id=task.id, subject_id=None, role="general", parse_state="done",
        degradations_json=[], pages_json=[
            {"page": 1, "text": "范围命中"},
            {"page": 2, "text": "邻接说明"},
            {"page": 3, "text": "无关附件"},
        ],
    )
    seed = {
        "review_run_id": "review-2", "expected_groups": ["a1"], "steps": [],
        "scope_strategy": {"enabled": True, "version": "category-adjacency-v1"},
    }
    run_payload = {
        "run_id": "run-2", "review_run_id": "review-2", "task_id": task.id,
        "group_id": "a1", "state": "completed", "finding_ids": [7],
        "finding_links": [{"finding_id": 7, "subject_id": None, "item_id": "T15-01",
                            "kind": "transfer_scope", "reference_ids": ["r1"]}],
        "references": [{"reference_id": "r1", "quote": "适用的规则原文"}],
        "limitations": [],
    }
    db_session.add_all([
        BsTaskEvent(task_id=task.id, sequence=1, event_type=reference_snapshots.REVIEW_EVENT,
                    payload_json=seed),
        BsTaskEvent(task_id=task.id, sequence=2, event_type=reference_snapshots.RUN_EVENT,
                    payload_json=run_payload),
    ])
    db_session.flush()
    monkeypatch.setattr(ai_review_context, "rule_scope_mapping",
                        lambda _item: (("scope",), {"scope": ("范围命中",)}))

    run = knowledge_runs.get_reference_run(db_session, task, group_id="a1")
    context = ai_review_context.build_review_context(
        finding=finding, materials=[material], run=run,
    )

    assert [page["page"] for page in context["materials"]] == [1, 2]
    assert context["scope_selection"]["narrowed"] is True
    assert context["scope_selection"]["strategy_version"] == "category-adjacency-v1"


def test_review_freeze_restores_verified_pre_adjudication_finding_input():
    original = {
        "status": "undecidable", "message": "固定规则原始结论",
        "evidence_json": [{"material_id": 3, "page": 1, "quote": "规则原始引文"}],
        "material_id": 3, "page": 1, "quote": "规则原始引文", "location_mode": "page",
    }
    finding = BsFinding(
        id=19, task_id=6, subject_id=None, item_id="T15-01", kind="transfer_scope",
        status="hit", message="AI 投影结论", evidence_json=[
            {"material_id": 9, "page": 8, "quote": "AI 投影引文"}],
        material_id=9, page=8, quote="AI 投影引文", location_mode="precise",
        ai_adjudication_json={
            "input_fields": original,
            "projected_fields": {
                "status": "hit", "message": "AI 投影结论",
                "evidence_json": [{"material_id": 9, "page": 8, "quote": "AI 投影引文"}],
                "material_id": 9, "page": 8, "quote": "AI 投影引文", "location_mode": "precise",
            },
        },
    )

    frozen = _frozen_review_finding(finding)

    assert frozen.status == original["status"]
    assert frozen.message == original["message"]
    assert frozen.evidence_json == original["evidence_json"]
    assert frozen.material_id == original["material_id"]
    assert frozen.page == original["page"]


def test_review_freeze_accepts_lightweight_finding_without_orm_columns():
    finding = SimpleNamespace(
        id=20, task_id=6, subject_id=4, item_id="T15-01", kind="transfer_scope",
        status="undecidable", material_id=3, page=2, evidence_json=[],
    )

    frozen = _frozen_review_finding(finding)

    assert frozen.id == finding.id
    assert frozen.status == finding.status
    assert frozen.material_id == finding.material_id
    assert frozen.message is None
