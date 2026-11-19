import json
from types import SimpleNamespace

from app.plus.blockchain_supervision import contract_extraction as ce
from app.plus.blockchain_supervision.report_basic_facts import extract_report_basic_facts


def _material():
    return SimpleNamespace(
        id=12, task_id=3, file_name="嘉松合同.pdf", role="sub", parse_state="done",
        degradations_json=[],
        pages_json=[
            {"page": 1, "text": "嘉松公路越江新建工程预制构件安装工程专业分包合同"},
            {"page": 2, "text": (
                "发包人（全称）：上海建工集团股份有限公司\n"
                "分包人（全称）：上海兴友工程机械有限公司\n"
                "总包工程名称：嘉松公路越江新建工程\n"
                "分包工程名称：嘉松公路越江新建工程预制构件安装工程\n"
                "分包工程承包范围和内容：预制立柱、盖梁、箱梁、板梁、挡墙等构件安装相关的工程内容\n"
                "签约合同价（含税）：4557598.71元\n"
                "不含增值税的签约合同价：4181283.22元\n"
                "税率：9%\n"
                "签订日期：2023年12月22日")},
            {"page": 55, "text": "附属协议\n甲方：上海兴友工程机械有限公司\n乙方：上海建工集团股份有限公司"},
            {"page": 56, "text": "乙方：专业公司"},
        ],
    )


def test_select_pages_keeps_head_and_keyword_pages_and_reports_truncation():
    material = _material()
    pages, truncated = ce.select_contract_pages(material, max_chars=200)
    selected = [page["page"] for page in pages]
    assert selected == [1, 2]
    assert truncated is True


def test_normalize_candidates_drops_unverifiable_evidence_and_placeholders():
    material = _material()
    result = {"fields": {
        "subcontractor": {"value": "上海兴友工程机械有限公司", "confidence": 0.9,
                          "evidence": {"material_id": 12, "page": 2,
                                       "quote": "分包人（全称）：上海兴友工程机械有限公司"}},
        "employer": {"value": "不存在的公司", "confidence": 0.8,
                     "evidence": {"material_id": 12, "page": 2, "quote": "这句话不在原文"}},
        "general_contractor": {"value": "专业公司", "confidence": 0.7,
                               "evidence": {"material_id": 12, "page": 56, "quote": "乙方：专业公司"}},
    }}
    normalized = ce.normalize_candidates(result, [material])
    assert normalized["fields"]["subcontractor"]["value"] == "上海兴友工程机械有限公司"
    assert "employer" not in normalized["fields"]
    assert "general_contractor" not in normalized["fields"]
    assert {(row["field"], row["reason"]) for row in normalized["dropped"]} == {
        ("employer", "evidence_unverifiable"),
        ("general_contractor", "value_rejected"),
    }


def test_normalize_accepts_flat_model_shape():
    material = _material()
    normalized = ce.normalize_candidates({
        "subcontractor": {"value": "上海兴友工程机械有限公司", "material_id": 12,
                          "page": 2, "quote": "分包人（全称）：上海兴友工程机械有限公司"},
        "tax_rate_pct": {"value": 9, "material_id": 12, "page": 2, "quote": "税率：9%"},
    }, [material])
    assert normalized["fields"]["subcontractor"]["value"] == "上海兴友工程机械有限公司"
    assert normalized["fields"]["subcontractor"]["evidence"]["page"] == 2
    assert normalized["fields"]["tax_rate_pct"]["value"] == "9"
    assert normalized["dropped"] == []


def test_merge_candidates_is_symmetric_and_never_silently_picks_a_side():
    rule = {"subcontractor": {"value": "上海兴友工程机械有限公司", "evidence": {"page": 2}}}
    llm = {"subcontractor": {"value": "上海兴友工程机械有限公司", "confidence": 0.9,
                             "evidence": {"page": 2}},
           "scope": {"value": "预制构件安装", "confidence": 0.8, "evidence": {"page": 2}}}
    merged = ce.merge_candidates(rule, llm)
    assert merged["subcontractor"]["status"] == "matched"
    assert merged["subcontractor"]["source"] == "rule+llm"
    assert merged["scope"]["status"] == "provisional"
    assert merged["scope"]["needs_review"] is True

    rule_only = ce.merge_candidates(rule, {})
    assert rule_only["subcontractor"]["status"] == "matched"
    assert rule_only["subcontractor"]["single_source"] is True

    conflict = ce.merge_candidates(
        {"subcontractor": {"value": "甲公司"}},
        {"subcontractor": {"value": "乙公司", "confidence": 0.9}})
    assert conflict["subcontractor"]["status"] == "conflict"
    assert conflict["subcontractor"]["value"] is None
    assert conflict["subcontractor"]["rule_value"] == "甲公司"
    assert conflict["subcontractor"]["llm_value"] == "乙公司"


def test_rule_candidates_reuse_report_basic_facts():
    facts = extract_report_basic_facts([_material()])
    candidates = ce.rule_candidates(facts)
    assert candidates["subcontractor"]["value"] == "上海兴友工程机械有限公司"
    assert candidates["general_contractor"]["value"] == "上海建工集团股份有限公司"
    assert candidates["amount_incl_wan"]["value"] == "455.759871"
    assert candidates["amount_excl_wan"]["value"] == "418.128322"
    assert candidates["tax_rate_pct"]["value"] == "9"
    assert candidates["project_name"]["value"] == "嘉松公路越江新建工程"


class _FakeClient:
    model_code = "fake-model"

    def __init__(self, payload):
        self.payload = payload

    async def complete(self, messages, **kwargs):
        return json.dumps(self.payload, ensure_ascii=False)


def test_llm_extraction_requires_grounded_evidence():
    import asyncio
    material = _material()
    client = _FakeClient({"fields": {
        "subcontractor": {"value": "上海兴友工程机械有限公司", "confidence": 0.95,
                          "evidence": {"material_id": 12, "page": 2,
                                       "quote": "分包人（全称）：上海兴友工程机械有限公司"}},
        "amount_incl_wan": {"value": "455.759871", "confidence": 0.9,
                            "evidence": {"material_id": 12, "page": 2,
                                         "quote": "签约合同价（含税）：4557598.71元"}},
    }})
    result = asyncio.run(ce.extract_llm_candidates(material, client=client))
    assert result["available"] is True
    assert result["fields"]["subcontractor"]["value"] == "上海兴友工程机械有限公司"
    assert result["fields"]["amount_incl_wan"]["value"] == "455.759871"
    assert result["version"]["model_code"] == "fake-model"


def test_llm_extraction_without_client_is_explicitly_unavailable():
    import asyncio
    result = asyncio.run(ce.extract_llm_candidates(_material(), client=None))
    assert result["available"] is False
    assert result["reason"] == "model_not_configured"
    assert result["fields"] == {}


def _db_task_and_material(db):
    from app.plus.blockchain_supervision.models import BsMaterial, BsTask
    task = BsTask(conversation_id=101, title="双路候选", state="setup",
                  created_by=1, created_by_type="web")
    db.add(task)
    db.flush()
    material = BsMaterial(task_id=task.id, file_name="嘉松合同.pdf", role="sub",
                          parse_state="done", material_category="sub",
                          pages_json=_material().pages_json)
    db.add(material)
    db.commit()
    return task, material


def _fake_payload(material_id):
    return {"fields": {
        "subcontractor": {"value": "上海兴友工程机械有限公司", "confidence": 0.95,
                          "evidence": {"material_id": material_id, "page": 2,
                                       "quote": "分包人（全称）：上海兴友工程机械有限公司"}},
    }}


def test_dual_extraction_is_skipped_when_disabled(db_session, monkeypatch):
    from app.plus.blockchain_supervision import service
    from app.plus.blockchain_supervision.models import BsMaterialFact
    task, material = _db_task_and_material(db_session)
    monkeypatch.setattr(service, "load_config", lambda: {
        "contract_dual_extraction": {"enabled": False}})
    result = service.run_contract_dual_extraction(
        db_session, task, material, client=_FakeClient(_fake_payload(material.id)))
    assert result == {"skipped": "disabled"}
    assert db_session.query(BsMaterialFact).filter_by(material_id=material.id).count() == 0


def test_dual_extraction_persists_review_candidates_with_version_fingerprint(db_session, monkeypatch):
    from app.plus.blockchain_supervision import service
    from app.plus.blockchain_supervision.models import BsMaterialFact, BsTaskEvent
    task, material = _db_task_and_material(db_session)
    monkeypatch.setattr(service, "load_config", lambda: {
        "contract_dual_extraction": {"enabled": True, "max_chars": 16000}})
    result = service.run_contract_dual_extraction(
        db_session, task, material, client=_FakeClient(_fake_payload(material.id)))
    assert result["available"] is True
    row = db_session.query(BsMaterialFact).filter_by(
        material_id=material.id, field_path="contract.subcontractor.candidate").one()
    assert row.raw_value == "上海兴友工程机械有限公司"
    assert row.quality == "review_required"
    assert row.parse_batch_id.startswith(row.content_sha256[:24])
    assert "contract-dual.v1" in row.extractor_version
    assert "fake-model" in row.extractor_version
    event = db_session.query(BsTaskEvent).filter_by(
        task_id=task.id, event_type="contract.candidates_extracted").one()
    assert event.payload_json["statuses"]["subcontractor"] == "matched"
