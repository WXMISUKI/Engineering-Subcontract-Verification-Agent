import asyncio
import json
from types import SimpleNamespace

from app.plus.blockchain_supervision import semantic_review, service, reporting, report_template_v14


def test_model_contract_risk_is_pending_in_service_and_report(db_session, monkeypatch):
    task = service.create_task(db_session, conversation_id=101, title="合成合同衔接",
                               scope_a1=False, scope_a3=True, created_by=1)
    subject = service.create_subject(db_session, task, name="甲建设有限公司", subject_type="professional")
    mats = []
    for role, quote in (("general", "质量要求达到优良标准"), ("sub", "质量要求达到合格标准")):
        m = service.add_material(db_session, task, file_name=role+".pdf", role=role,
                                 subject_id=subject.id if role == "sub" else None)
        m.pages_json = [{"page": 1, "text": quote}]
        m.parse_state = "done"
        mats.append(m)
    db_session.commit()

    async def synthetic_compare(materials, **kwargs):
        return {"findings": [{"message": "质量标准不同，需人工核对适用要求", "evidence": [
            {"material_id": m.id, "page": 1, "quote": m.pages_json[0]["text"]} for m in mats]}], "limitations": []}

    monkeypatch.setattr(semantic_review, "compare_contracts", synthetic_compare)
    for _ in range(2):
        finding, = service.run_semantic_checks(db_session, task)
        assert finding.status == "undecidable"
        assert finding.subject_id == subject.id
        assert {r["material_id"] for r in finding.evidence_json} == {m.id for m in mats}
    report = reporting.build_report(db_session, task)
    raw = [row for row in report_template_v14._raw_items(report) if row["kind"] == "contract_semantic"]
    assert len(raw) == 1 and report_template_v14._verdict(raw[0]) == "待核实"
    assert report_template_v14._issue_rows(raw) == []
    assert len(report_template_v14._pending_rows(raw)) == 1


class SyntheticClient:
    def __init__(self, *, empty_material=None):
        self.empty_material = empty_material
        self.calls = []

    async def complete(self, messages, **kwargs):
        payload = json.loads(messages[1]["content"])
        self.calls.append((messages[0]["content"], payload))
        if "pages" in payload:
            return json.dumps({"clauses": [] if payload["pages"][0]["material_id"] == self.empty_material else [
                {"material_id": row["material_id"], "page": row["page"], "quote": row["text"]} for row in payload["pages"]]})
        return json.dumps({"findings": [], "limitations": []})


def contracts():
    return [SimpleNamespace(id=mid, role=role, parse_state="done", file_name=role+".pdf",
                            pages_json=[{"page": 1, "text": "合成合同约定质量安全工期环保农民工工资要求"}])
            for mid, role in ((1, "general"), (2, "sub"))]


def test_existing_provider_path_explicitly_requests_five_dimensions_without_demanding_risk_count():
    client = SyntheticClient()
    result = asyncio.run(semantic_review.compare_contracts(contracts(), client=client))
    assert len(client.calls) == 3 and result == {"findings": [], "limitations": []}
    system, payload = client.calls[-1]
    assert all(word in system for word in ("质量", "安全", "工期", "环保", "农民工工资"))
    assert "未提出风险不代表该维已通过" in system
    assert {row["material_id"] for row in payload["general"] + payload["sub"]} == {1, 2}


def test_empty_side_retains_limits_and_skips_comparison_call():
    for empty_mid in (1, 2):
        client = SyntheticClient(empty_material=empty_mid)
        result = asyncio.run(semantic_review.compare_contracts(contracts(), client=client))
        assert len(client.calls) == 2 and not result["findings"]
        assert any("缺少一侧可核验条款" in message for message in result["limitations"])


def test_new_start_and_rerun_freeze_semantic_version_without_rewriting_history(db_session, monkeypatch):
    from copy import deepcopy
    from app.plus.blockchain_supervision.models import BsReviewVersion

    task = service.create_task(db_session, conversation_id=101, title="合成语义版本",
                               scope_a1=False, scope_a3=True, created_by=1)
    # 隔离外部参考库和材料齐套校验；保留真实启动、重跑、SQLite版本归档路径。
    monkeypatch.setattr(service, "validate_task_materials", lambda *_: {
        "hard_ok": True, "parsing_pending": False, "composition": {}, "soft_gaps": []})
    monkeypatch.setattr(service, "initialize_reference_review", lambda *_, **kw: {
        "review_run_id": "synthetic-semantic-version", "expected_groups": [], "steps": []})
    assert service.start_review(db_session, task)["ok"] is True
    assert task.options_snapshot_json["semantic_review_version"] == 2

    # 模拟旧代实际使用的v1记录；重跑应归档旧值，新代才使用v2。
    task.options_snapshot_json = {**task.options_snapshot_json, "semantic_review_version": 1}
    db_session.commit()
    history = service.create_review_version(db_session, task, trigger="completed")
    db_session.commit()
    frozen = deepcopy(history.snapshot_json)
    assert frozen["task"]["options_snapshot"]["semantic_review_version"] == 1
    assert service.rerun_task(db_session, task)["ok"] is True
    db_session.expire_all()
    assert task.options_snapshot_json["semantic_review_version"] == 2
    assert db_session.get(BsReviewVersion, history.id).snapshot_json == frozen
    archived = db_session.query(BsReviewVersion).filter_by(task_id=task.id, trigger="rerun").one()
    assert archived.snapshot_json["task"]["options_snapshot"]["semantic_review_version"] == 1
