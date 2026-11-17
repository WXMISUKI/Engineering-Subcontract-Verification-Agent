from copy import deepcopy

from app.plus.blockchain_supervision.report_content_summary import basis_documents, summarize_items, category_summaries, reading_affects_item
from app.plus.blockchain_supervision import report_template_v13
from test_report_template_v13 import sample_report


def test_basis_is_a_unique_document_directory_without_clause_fragments():
    sources = ["《2025 分包报审附件要求》①1/②1；第一现场指挥部2025.6.20",
               "《2025 分包报审附件要求》①2/②2；第一现场指挥部2025.6.20",
               "《上海市公路工程施工分包管理实施细则》第十五条（一）；PDF第7页",
               "《实施细则》附件3/4签字栏", "依据：《沪交行规〔2026〕8号》第十条"]
    assert basis_documents(sources) == ["《2025 分包报审附件要求》", "《上海市公路工程施工分包管理实施细则》"]


def test_grouping_retains_member_ids_and_separates_subject_and_human_disposition():
    items = [{"finding_id": index, "item_id": "A2-02", "kind": "cert_validity", "subject_id": subject,
              "status": "undecidable", "message": "原始证件细节%s" % index,
              "human_disposition": {"code": decision, "note": "复核说明%s" % index}} for index, subject, decision in
             [(1, 10, "accepted"), (2, 10, "accepted"), (3, 20, "accepted"), (4, 10, "unprocessed")]]
    before = deepcopy(items)
    groups = summarize_items(items, ["待核实"] * 4, "测试分包人")
    assert items == before
    assert len(groups) == 4
    assert groups[0]["finding_ids"] == [1]
    assert "原始证件细节1" in groups[0]["fact"]
    assert "人工复核说明：复核说明1" in groups[0]["fact"]
    assert groups[0]["theme"] == "人员证件有效性及注册关系"
    assert len([row for row in groups if row["theme"] == groups[0]["theme"]
                and row["category"] == groups[0]["category"]]) == 3
    assert "已复核保留事项" in str(category_summaries(groups))


def test_issue_summary_keeps_concrete_facts_and_only_merges_identical_fact_and_disposition():
    items = [
        {"finding_id": 1, "item_id": "A2-02", "kind": "cert_validity", "subject_id": 10,
         "status": "undecidable", "message": "安全员交安C证有效期至2025-12-09。",
         "evidence_list": [{"material_id": 3, "file_name": "人员表.pdf", "page": 8}],
         "human_disposition": {"code": "accepted", "note": "复核确认材料记载的证件日期。"}},
        {"finding_id": 2, "item_id": "A2-02", "kind": "cert_validity", "subject_id": 10,
         "status": "undecidable", "message": "安全员交安C证有效期至2025-12-09。",
         "evidence_list": [{"material_id": 3, "file_name": "人员表.pdf", "page": 9}],
         "human_disposition": {"code": "accepted", "note": "复核确认材料记载的证件日期。"}},
        {"finding_id": 3, "item_id": "A2-02", "kind": "cert_validity", "subject_id": 10,
         "status": "undecidable", "message": "另一名安全员证件页未识别出有效期。",
         "human_disposition": {"code": "accepted", "note": "仍需核对原件。"}},
    ]

    groups = summarize_items(items, ["—"] * len(items), "测试分包人")

    assert len(groups) == 2
    assert groups[0]["finding_ids"] == [1, 2]
    assert "有效期至2025-12-09" in groups[0]["fact"]
    assert "人员表.pdf第8、9页" in groups[0]["fact"]
    assert "复核确认材料记载的证件日期" in groups[0]["fact"]
    assert "另一名安全员证件页未识别出有效期" in groups[1]["fact"]


def test_reading_quality_is_scoped_to_the_actual_evidence_page():
    gaps = [{"material_id": 6, "unread_pages": [18], "suspect_pages": [23]}]
    assert not reading_affects_item({"evidence": {"material_id": 6, "page": 1}}, gaps)
    assert reading_affects_item({"evidence_list": [{"material_id": 6, "page": 23}]}, gaps)
    assert not reading_affects_item({"evidence": {"material_id": 7, "page": 18}}, gaps)


def test_same_fact_with_different_remediation_or_legacy_disposition_stays_separate():
    base = {"finding_id": 1, "subject_id": 10, "kind": "cert_validity", "message": "证照信息不一致。"}
    items = [{**base, "recommendation": "核对原件。", "review": {"decision": "accepted"}},
             {**base, "finding_id": 2, "recommendation": "补充主管部门证明。", "review": {"decision": "accepted"}},
             {**base, "finding_id": 3, "recommendation": "核对原件。", "review": {"decision": "unprocessed"}}]
    groups = summarize_items(items, ["不符合"] * 3, "分包人")
    assert len(groups) == 3
    assert groups[0]["recommendation"] == "核对原件。"
    assert groups[1]["recommendation"] == "补充主管部门证明。"


def test_nonconforming_summary_counts_verdicts_within_a_mixed_missing_group():
    groups = [{"category": "缺项", "theme": "企业资格与业绩资料", "count": 5,
               "verdicts": ["不符合", "不符合", "不符合", "待核实", "待核实"]}]
    summaries = category_summaries(groups)
    assert "共3项" in next(row["text"] for row in summaries if row["category"] == "不符合项")
    assert "共5项" in next(row["text"] for row in summaries if row["category"] == "缺项")


def test_report_sections_follow_reference_and_do_not_dump_raw_findings():
    report = sample_report()
    report["basic_information"] = {"subcontract_item_name": {"status": "matched", "value": "吊装劳务"}}
    report["business_document"]["basic_information"]["分包类型"] = "劳务分包"
    report["checklist_summary"]["subjects"][0]["subject_type"] = "labor"
    report["submission_materials"] = [{"material_id": 6, "subject_id": "one", "pages": 58, "role": "labor"},
                                       {"material_id": 7, "pages": 200, "role": "general"}]
    report["sections"][0]["items"][0]["message"] = "这是一条长而重复的原始核查消息"
    report["reading_coverage"] = [{"material_id": 6, "unread_pages": [18]}]
    report["sections"][0]["items"][0]["source"] = "《本次依据文件》第一条"
    document = report_template_v13.build_document(report)
    assert document["material_page_count"] == 58 and document["basic_information"]["材料页数"] == "58页"
    assert document["title"] == "吊装劳务分包审查意见（AI初审）"
    assert document["statistics"]["counts"]["不符合"] == 1
    tables = [block for block in document["blocks"] if block["type"] == "table"]
    assert tables[0]["headers"] == ["序号", "审查维度", "主要依据", "初审意见"]
    assert len(tables[0]["rows"]) == 8
    assert tables[-1]["headers"] == ["序号", "问题类别", "问题事实", "补正要求", "责任单位"]
    assert "这是一条长而重复的原始核查消息" in str(document["blocks"])
    assert "这是一条长而重复的原始核查消息" in str(document["issues"])
    assert "报送日期" not in str(document["blocks"])
    assert "一、审查概况" in str(document["blocks"])
    # A missing required document can also be nonconforming; the summaries
    # disclose that overlap instead of claiming there are no nonconformities.
    assert "未形成该类确定事项" not in next(row["text"] for row in document["category_summaries"] if row["category"] == "不符合项")
