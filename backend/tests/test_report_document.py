from app.plus.blockchain_supervision.report_document import build_report_document
from app.plus.blockchain_supervision import export
from docx import Document
from docx.shared import Mm
from io import BytesIO


def _item(kind, status, label, message, decision=None):
    return {"kind": kind, "item_id": f"{kind}-{label}", "label": label,
            "status": status, "message": message,
            "human_disposition": {"code": decision} if decision else None,
            "material_missing": False}


def _report(items, *, completed=None, selected=None):
    grouped = {}
    for item in items:
        grouped.setdefault(item["kind"], []).append(item)
    return {
        "meta": {"project_name": "G15项目", "contract_section": "11标", "baseline_date": "2026-11-05"},
        "basic_information": {"general_contractor": {"status": "matched", "company": "总包公司"}},
        "sections": [{"kind": kind, "title": kind, "checked": kind in grouped,
                      "items": grouped.get(kind, [])}
                     for kind in ("completeness", "signature_chain", "qualification_match",
                                  "negative_list", "transfer_scope", "illegal_subcontract",
                                  "a1_dependency", "person_consistency", "cert_validity", "internal_records")],
        "coverage": {"selected_groups": selected or [], "completed_groups": completed or [],
                     "pending_groups": [], "missing_groups": [], "complete": bool(selected) and set(completed or []) >= set(selected or [])},
        "scope_statements": [], "limitations": [], "ignored_items": [],
    }


def test_document_has_five_business_categories_and_fixed_fields():
    projected = build_report_document(_report([], completed=["completeness", "signature_chain", "a1", "a2", "internal_records"],
                                              selected=["completeness", "signature_chain", "a1", "a2", "internal_records"]))
    assert [row["title"] for row in projected["sections"]] == [
        "要素完整性核查", "资质与内容合规性审查", "关键岗位人员核查",
        "证件证照有效性核查", "内部信用与事故材料核查"]
    assert {row["label"] for row in projected["sections"][0]["fields"]} == {
        "报审资料", "主体资料", "人员资料", "合同及清单", "签字盖章及审批资料"}
    assert projected["conclusion"]["status"] == "incomplete"


def test_report_uses_business_key_points_and_groups_all_limitations():
    report = _report([_item("negative_list", "undecidable", "负面清单", "尚需核实")],
                     completed=["a1"], selected=["a1"])
    report["basic_information"]["subcontract_type"] = "劳务分包"
    report["scope_statements"] = ["总包合同未提供。", "部分材料 OCR 第20页不清晰。", "未开展外部信用渠道查询。"]
    result = build_report_document(report)
    points = {p["label"]: p["summary"] for p in result["sections"][1]["key_points"]}
    assert {"事项定性", "负面清单", "主体资格", "再分包与挂靠"} <= set(points)
    assert "劳务分包" in points["事项定性"] and "核实" in points["事项定性"]
    assert "核实" in points["负面清单"]
    grouped = result["conclusion"]["limitation_groups"]
    assert {text for group in grouped for text in group["items"]} == set(report["scope_statements"])
    assert len(grouped) == 3


def test_key_point_type_does_not_invent_a_classification():
    report = _report([])
    points = {p["label"]: p["summary"] for p in build_report_document(report)["sections"][1]["key_points"]}
    assert "待核实" in points["事项定性"]
    assert "劳务" not in points["事项定性"]


def test_single_key_point_preserves_short_actual_finding():
    item = _item("negative_list", "undecidable", "负面清单", "缺少总包合同，暂无法核实关键性工作范围。")
    report = _report([item], completed=["a1"], selected=["a1"])
    points = {p["label"]: p["summary"] for p in build_report_document(report)["sections"][1]["key_points"]}
    assert "缺少总包合同" in points["负面清单"]


def test_word_pdf_present_key_points_and_grouped_limitations():
    import fitz
    report = _report([_item("negative_list", "undecidable", "负面清单", "待核实")], completed=["a1"], selected=["a1"])
    report["meta"].update({"title": "示例", "scope": {}})
    report["scope_statements"] = ["总包合同未提供。", "本次未核查金额占比。", "未开展外部信用渠道查询。"]
    report["business_document"] = build_report_document(report)
    word = Document(BytesIO(export.word_bytes(report)))
    word_text = "\n".join([p.text for p in word.paragraphs] + [c.text for t in word.tables for r in t.rows for c in r.cells])
    pdf = fitz.open(stream=export.pdf_bytes(report), filetype="pdf")
    pdf_text = "\n".join(page.get_text() for page in pdf)
    for text in (word_text, pdf_text):
        assert "事项定性" in text and "负面清单" in text and "主体资格" in text
        normalized = text.replace("\n", "")
        assert "五、核查说明" not in text
        assert "总包合同未提供" in normalized and "金额占比" in normalized and "外部信用" in normalized


def test_key_point_report_omits_generic_conclusions_but_keeps_specific_limitations():
    import fitz
    report = _report([_item("negative_list", "undecidable", "负面清单", "合同范围待核实")],
                     completed=[], selected=["a1"])
    report["meta"].update({"title": "示例", "scope": {}})
    report["scope_statements"] = ["总包合同未提供。"]
    report["business_document"] = build_report_document(report)
    point = next(p for p in report["business_document"]["sections"][1]["key_points"] if p["label"] == "负面清单")
    assert point["status"] == "待核实"
    word = Document(BytesIO(export.word_bytes(report)))
    word_text = "\n".join([p.text for p in word.paragraphs] + [c.text for t in word.tables for r in t.rows for c in r.cells])
    pdf = fitz.open(stream=export.pdf_bytes(report), filetype="pdf")
    for text in (word_text, "\n".join(page.get_text() for page in pdf)):
        assert "负面清单" in text and "总包合同未提供" in text.replace("\n", "")
        assert "本次核查尚未覆盖全部适用范围" not in text
        assert "本类核查尚未完成，当前发现" not in text
        assert "总体核查结论及说明" not in text
    assert report["business_document"]["conclusion"]["status"] == "incomplete"


def test_report_has_two_columns_and_places_relevant_limitations_in_key_points():
    import fitz
    report = _report([_item("negative_list", "undecidable", "负面清单", "合同范围需核实")], selected=["a1"])
    report["meta"].update({"title": "示例", "scope": {}})
    report["scope_statements"] = ["总包合同未提供。", "报审材料第20页识别质量存疑。"]
    report["business_document"] = build_report_document(report)
    word = Document(BytesIO(export.word_bytes(report)))
    text = "\n".join([p.text for p in word.paragraphs] + [c.text for t in word.tables for r in t.rows for c in r.cells])
    review_tables = [t for t in word.tables if t.rows[0].cells[0].text == "审查要点"]
    assert len(review_tables) == 5 and all(len(t.columns) == 2 for t in review_tables)
    assert all(t.rows[0].cells[0].paragraphs[0].paragraph_format.keep_with_next for t in review_tables)
    pdf = fitz.open(stream=export.pdf_bytes(report), filetype="pdf")
    for rendered in (text, "\n".join(page.get_text() for page in pdf)):
        assert "五、核查说明" not in rendered and "状态未标注" not in rendered
        normalized = rendered.replace("\n", "")
        assert "总包合同未提供" in normalized and "第20页识别质量存疑" in normalized


def test_legacy_snapshot_without_sections_still_shows_specific_limitation():
    from app.plus.blockchain_supervision.report_document import report_display_sections
    document = {"sections": [], "conclusion": {"limitations": ["总包合同未提供。"]}}
    rendered = report_display_sections(document)
    assert "总包合同未提供" in str(rendered)
    assert document["sections"] == []


def test_ignored_items_are_excluded_but_unreviewed_and_undecidable_remain():
    projected = build_report_document(_report([
        _item("completeness", "hit", "安全生产许可证", "许可证缺失", "ignored"),
        _item("cert_validity", "undecidable", "证件有效期", "日期无法确认"),
        _item("internal_records", "hit", "内部记录", "发现警示记录"),
    ], completed=["completeness", "signature_chain", "a1", "a2", "internal_records"],
       selected=["completeness", "signature_chain", "a1", "a2", "internal_records"]))
    assert len(projected["issues"]) == 2
    assert {row["status"] for row in projected["issues"]} == {"hit", "undecidable"}
    assert projected["conclusion"]["status"] == "incomplete"
    assert "需关注" in projected["conclusion"]["text"]


def test_incomplete_scope_cannot_be_reported_as_no_issue():
    projected = build_report_document(_report([], completed=["completeness"],
                                              selected=["completeness", "a1", "a2"]))
    assert projected["conclusion"]["status"] == "incomplete"
    assert "尚未覆盖" in projected["conclusion"]["text"]


def test_unmapped_detail_does_not_imply_individual_fields_are_clear():
    result = build_report_document(_report([_item("cert_validity", "undecidable", "证件有效期", "证件日期无法确认")],
        completed=["a2"], selected=["a2"]))
    cert = result["sections"][3]
    fields = {row["label"]: row["summary"] for row in cert["fields"]}
    assert "无法确认" not in fields["证照真实性及核验情况"]
    assert "未提供" in fields["证照真实性及核验情况"]
    assert "无法确认" not in fields["证件信息一致性"]


def test_specific_certificate_warning_maps_without_classifying_aggregate():
    enterprise = {**_item('cert_validity', 'hit', '证件有效期', '「安全生产许可证」已超过有效期'), 'item_id': 'A2-02'}
    person = {**_item('cert_validity', 'undecidable', '证件有效期', '「特种作业操作证」的有效期不明确'), 'item_id': 'A2-02'}
    aggregate = {**_item('cert_validity', 'miss', '证件有效期', '共核对 2 条证件有效期记录，均在有效期内'), 'item_id': 'A2-02'}
    report = _report([enterprise, person, aggregate], completed=['a2'], selected=['a2'])
    fields = {field['label']: field['summary'] for field in build_report_document(report)['sections'][3]['fields']}
    assert '需关注或核实' in fields['企业证照有效期']
    assert '需关注或核实' in fields['人员证件有效期']
    assert '已核查符合' not in fields['企业证照有效期']
    assert '已核查符合' not in fields['人员证件有效期']


def test_word_and_pdf_render_same_business_content_without_ignored_items():
    accepted = {**_item("internal_records", "hit", "内部记录", "发现警示记录"),
                "human_disposition": {"code": "accepted", "label": "已采纳", "note": "人工复核意见"}}
    report = _report([accepted,
                      _item("cert_validity", "hit", "已忽略证件", "不应出现在报告", "ignored")],
                     completed=["completeness", "signature_chain", "a1", "a2", "internal_records"],
                     selected=["completeness", "signature_chain", "a1", "a2", "internal_records"])
    report["meta"].update({"title": "示例", "scope": {"A1": True, "A3": True}})
    report["business_document"] = build_report_document(report)
    doc = Document(BytesIO(export.word_bytes(report)))
    text = "\n".join([p.text for p in doc.paragraphs] +
                     [cell.text for table in doc.tables for row in table.rows for cell in row.cells])
    assert "五、总体核查结论及说明" not in text
    assert "内部信用与事故材料核查" in text
    assert "不应出现在报告" not in text
    assert "人工复核意见" in text
    import fitz
    pdf = fitz.open(stream=export.pdf_bytes(report), filetype="pdf")
    pdf_text = "\n".join(page.get_text() for page in pdf)
    assert "内部信用与事故材料核查" in pdf_text
    assert "不应出现在报告" not in pdf_text
    assert "人工复核意见" in pdf_text
    opinion = {"meta": report["meta"], "business_document": report["business_document"]}
    opinion_doc = Document(BytesIO(export.opinion_word_bytes(opinion)))
    opinion_text = "\n".join(cell.text for table in opinion_doc.tables for row in table.rows for cell in row.cells)
    assert "内部信用与事故材料核查" in opinion_text
    assert "不应出现在报告" not in opinion_text
    assert "人工复核意见" in opinion_text
    opinion_pdf = fitz.open(stream=export.opinion_pdf_bytes(opinion), filetype="pdf")
    opinion_pdf_text = "\n".join(page.get_text() for page in opinion_pdf)
    assert "内部信用与事故材料核查" in opinion_pdf_text
    assert "不应出现在报告" not in opinion_pdf_text


def test_business_pdf_embeds_chinese_font_for_independent_viewers():
    import fitz
    report = _report([], completed=["internal_records"], selected=["internal_records"])
    report["meta"].update({"title": "示例", "scope": {}})
    report["business_document"] = build_report_document(report)
    for payload in (export.pdf_bytes(report), export.opinion_pdf_bytes(report)):
        pdf = fitz.open(stream=payload, filetype="pdf")
        assert "核查" in "".join(page.get_text() for page in pdf)
        for page in pdf:
            fonts = page.get_fonts()
            assert fonts
            assert all(pdf.extract_font(font[0])[3] for font in fonts)


def test_summary_groups_unprocessed_and_preserves_accepted_note_without_ignored():
    pending = _item("internal_records", "undecidable", "信用材料", "不应逐条展开的原始长文")
    accepted = {**_item("internal_records", "hit", "事故记录", "人工采纳的原始长文"),
                "human_disposition": {"code": "accepted", "label": "已采纳", "note": "已联系补充材料"}}
    ignored = _item("internal_records", "hit", "忽略项", "不应展示", "ignored")
    report = _report([pending, accepted, ignored], completed=["internal_records"], selected=["internal_records"])
    report["meta"].update({"title": "示例", "scope": {}})
    document = build_report_document(report)
    assert "内部记录核查情况" in str(document["pending_summaries"])
    assert "待核实" in str(document["pending_summaries"])
    assert "事故记录" not in str(document["pending_summaries"])
    assert "已联系补充材料" in str(document["human_notes"])
    assert "忽略项" not in str(document["pending_summaries"]) + str(document["human_notes"])
    report["business_document"] = document
    doc = Document(BytesIO(export.word_bytes(report)))
    text = "\n".join([p.text for p in doc.paragraphs] + [c.text for t in doc.tables for r in t.rows for c in r.cells])
    assert "已联系补充材料" in text
    assert "不应逐条展开的原始长文" not in text
    assert "人工采纳的原始长文" not in text


def test_summary_combines_rules_under_business_theme():
    items = [_item("negative_list", "undecidable", f"规则事项{i}", "无法核实") for i in range(12)]
    document = build_report_document(_report(items, completed=["a1"], selected=["a1"]))
    summary = str(document["pending_summaries"])
    assert "拟分包内容与负面清单" in summary
    assert "规则事项1" not in summary
    assert len(document["issues"]) == 12


def test_stage_summary_does_not_copy_long_rule_titles_as_passing_facts():
    long_rule = "涉及高速公路路基工程的复杂条款" * 20
    item = _item("negative_list", "miss", long_rule, "未命中")
    document = build_report_document(_report([item], completed=["a1"], selected=["a1"]))
    field = next(row for row in document["sections"][1]["fields"] if row["label"] == "拟分包内容与负面清单")
    assert long_rule not in field["summary"]
    assert "未命中" in field["summary"]


def test_summary_distinguishes_explicit_missing_material_from_uncertain_evidence():
    missing = {**_item("completeness", "hit", "主体资料", "明确缺件"),
               "material_missing": True, "missing": {"labels": ["营业执照", "资质证书"]}}
    uncertain = {**_item("completeness", "undecidable", "人员资料", "无法核实"),
                 "material_missing": True, "missing": {"labels": ["人员证件"]}}
    document = build_report_document(_report([missing, uncertain], completed=["completeness"], selected=["completeness"]))
    summaries = document["pending_summaries"]
    explicit = next(row for row in summaries if row["status"] == "明确缺少材料")
    assert "营业执照" in explicit["text"] and "资质证书" in explicit["text"]
    assert "人员证件" not in explicit["text"]
    assert any(row["status"] == "待核实" for row in summaries)


def test_duplicate_display_merge_preserves_distinct_missing_materials():
    common = {**_item("completeness", "hit", "主体资料", "缺少材料"), "subject_id": 1,
              "material_missing": True, "evidence_list": [{"material_id": 1, "page": 1, "quote": "相同"}]}
    items = [{**common, "finding_id": 1, "missing": {"labels": ["营业执照"]}},
             {**common, "finding_id": 2, "missing": {"labels": ["资质证书"]}}]
    document = build_report_document(_report(items, completed=["completeness"], selected=["completeness"]))
    assert len(document["issues"]) == 2
    text = str(document["pending_summaries"])
    assert "营业执照" in text and "资质证书" in text


def test_partial_frozen_summary_uses_existing_field_and_derives_missing_field():
    document = {
        "pending_summaries": [{"category": "历史类别", "status": "待核实", "text": "冻结摘要"}],
        "issues": [{"category": "历史类别", "label": "人工处理事项", "status": "hit",
                    "human_disposition": {"code": "accepted", "note": "冻结备注"}}],
    }
    pending, notes = export._business_summary_rows(document)
    assert pending[0]["text"] == "冻结摘要"
    assert notes[0]["note"] == "冻结备注"


def test_word_exports_use_a4_page_geometry_like_pdf_exports():
    report = _report([])
    report["meta"].update({"title": "示例", "scope": {}})
    report["business_document"] = build_report_document(report)

    word = Document(BytesIO(export.word_bytes(report)))
    section = word.sections[0]
    assert abs(section.page_width - Mm(210)) < 1000
    assert abs(section.page_height - Mm(297)) < 1000

    opinion = {"meta": report["meta"], "business_document": report["business_document"]}
    opinion_word = Document(BytesIO(export.opinion_word_bytes(opinion)))
    opinion_section = opinion_word.sections[0]
    assert abs(opinion_section.page_width - Mm(210)) < 1000
    assert abs(opinion_section.page_height - Mm(297)) < 1000


def test_conflicting_basic_information_is_marked_in_both_exports():
    report = _report([])
    report["meta"].update({"title": "示例", "scope": {}})
    report["basic_information"]["general_contractor"] = {"status": "conflict", "company": "总包公司"}
    report["business_document"] = build_report_document(report)
    word = Document(BytesIO(export.word_bytes(report)))
    rows = [row for table in word.tables for row in table.rows if row.cells[0].text == "施工总承包单位"]
    assert rows[0].cells[1].text == "总包公司（待核实）"
    import fitz
    pdf = fitz.open(stream=export.pdf_bytes(report), filetype="pdf")
    assert "总包公司（待核实）" in "\n".join(page.get_text() for page in pdf)


def test_business_pdf_tables_repeat_headers_and_keep_long_human_note():
    item = {**_item("internal_records", "hit", "内部记录", "风险内容" * 1200 + "末尾标识"),
            "human_disposition": {"code": "accepted", "label": "已采纳", "note": "复核意见" * 1200 + "备注末尾"}}
    report = _report([item], completed=["internal_records"], selected=["internal_records"])
    report["meta"].update({"title": "示例", "scope": {}})
    report["business_document"] = build_report_document(report)
    import fitz
    for payload in (export.pdf_bytes(report), export.opinion_pdf_bytes(report)):
        pdf = fitz.open(stream=payload, filetype="pdf")
        text = "\n".join(page.get_text() for page in pdf)
        assert len(pdf) > 2
        assert text.count("人工意见") >= 2
        assert "备注末尾" in text and "复核意见" in text
        assert "末尾标识" not in text
        assert sum(len(page.get_drawings()) for page in pdf) > 10


def test_word_summary_rows_allow_pagination_and_repeat_headers():
    item = _item('internal_records', 'hit', '内部记录', '风险内容' * 1200 + '末尾标识')
    report = _report([item], completed=['internal_records'], selected=['internal_records'])
    report['meta'].update({'title': '示例', 'scope': {}})
    report['business_document'] = build_report_document(report)
    doc = Document(BytesIO(export.word_bytes(report)))
    issue_table = next(table for table in doc.tables if table.rows[0].cells[1].text == '待处理摘要')
    issue_heading = next(p for p in doc.paragraphs if p.text == '三、待处理事项摘要')
    assert issue_heading.paragraph_format.keep_with_next is True
    assert issue_table.rows[0]._tr.trPr.find('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tblHeader') is not None
    assert issue_table.rows[1]._tr.trPr is None or issue_table.rows[1]._tr.trPr.find('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}cantSplit') is None
    assert '内部记录' in issue_table.rows[1].cells[1].text
    assert '末尾标识' not in issue_table.rows[1].cells[1].text


def test_report_digest_ignores_projected_generation_date():
    report = _report([])
    report["meta"]["generated_at"] = "2026-11-10T12:00:00"
    report["business_document"] = build_report_document(report)
    first = export.report_digest(report)
    report["meta"]["generated_at"] = "2026-11-10T13:00:00"
    report["business_document"]["basic_information"]["报告日期"] = "2026-11-10T13:00:00"
    assert export.report_digest(report) == first


def test_incomplete_ai_coverage_is_disclosed_in_conclusion():
    report = _report([], completed=["completeness", "signature_chain", "a1", "a2", "internal_records"],
                     selected=["completeness", "signature_chain", "a1", "a2", "internal_records"])
    report["ai_coverage"] = {"state": "available", "groups": [{"state": "available", "counts": {"completed": 2, "failed": 1}}]}
    result = build_report_document(report)
    assert result["conclusion"]["status"] == "incomplete"
    assert any("AI" in item for item in result["conclusion"]["limitations"])


def test_completed_group_without_results_does_not_claim_no_problems():
    report = _report([], completed=["a2"], selected=["a2"])
    result = build_report_document(report)
    assert "未发现需要处理的问题" not in result["sections"][2]["summary"]
    assert result["conclusion"]["status"] == "incomplete"


def test_unread_material_prevents_complete_conclusion_and_is_exported():
    groups = ["completeness", "signature_chain", "a1", "a2", "internal_records"]
    items = [_item(kind, "miss", "已核查项", "当前未发现问题") for kind in
             ("completeness", "signature_chain", "qualification_match", "person_consistency", "cert_validity", "internal_records")]
    report = _report(items, completed=groups, selected=groups)
    report["meta"].update({"title": "示例", "scope": {}})
    report["reading_coverage"] = [{"unread_pages": [20, 21], "suspect_pages": [20, 21],
                                   "summary_text": "报审材料第20、21页识别质量待核实。"}]
    report["business_document"] = build_report_document(report)
    conclusion = report["business_document"]["conclusion"]
    assert conclusion["status"] == "incomplete"
    assert "不完整" in conclusion["text"]
    for section in report["business_document"]["sections"]:
        assert section["status"] == "incomplete"
        assert "材料读取" in section["summary"]
        assert "本类已完成核查，未发现" not in section["summary"]
    assert "第20、21页" in conclusion["limitations"][0]
    word = Document(BytesIO(export.word_bytes(report)))
    word_text = "\n".join([p.text for p in word.paragraphs] + [cell.text for table in word.tables for row in table.rows for cell in row.cells])
    import fitz
    pdf = fitz.open(stream=export.pdf_bytes(report), filetype="pdf")
    assert "第20、21页" in word_text
    assert "第20、21页" in "\n".join(page.get_text() for page in pdf)


def test_reading_gap_does_not_hide_identified_issues():
    report = _report([_item("internal_records", "hit", "内部记录", "发现警示")],
                     completed=["internal_records"], selected=["internal_records"])
    report["reading_coverage"] = [{"unread_pages": [20], "summary_text": "第20页待核实。"}]
    section = build_report_document(report)["sections"][4]
    assert "需关注 1 项" in section["summary"]
    assert "材料读取" in section["summary"]


def test_incomplete_run_retains_identified_issues_in_summary():
    report = _report([_item("internal_records", "hit", "内部记录", "发现警示记录")],
                     completed=[], selected=["internal_records"])
    result = build_report_document(report)
    assert "需关注" in result["sections"][4]["summary"]
    assert "尚未完成" in result["sections"][4]["summary"]
    assert "需关注" in result["conclusion"]["text"]


def test_conclusion_identifies_attention_and_unresolved_categories_without_ignored_items():
    report = _report([_item('internal_records', 'hit', '警示记录', '发现记录'),
                      _item('cert_validity', 'undecidable', '证照期限', '期限待核实'),
                      _item('completeness', 'hit', '忽略事项', '不展示', 'ignored')],
                     completed=['internal_records', 'a2', 'completeness'],
                     selected=['internal_records', 'a2', 'completeness'])
    text = build_report_document(report)['conclusion']['text']
    assert '需关注：内部信用与事故材料核查' in text
    assert '待核实：证件证照有效性核查' in text
    assert '要素完整性核查' not in text
    assert '7 天' not in text and '暂停支付' not in text


def test_warning_is_not_described_as_noncompliance():
    item = {**_item("cert_validity", "hit", "证件有效期", "证件临近到期，请人工复核"),
            "item_id": "A2-02"}
    report = _report([item], completed=["a2"], selected=["a2"])
    summary = build_report_document(report)["sections"][3]["summary"]
    assert "需关注" in summary
    assert "不符合" not in summary


def test_field_summary_uses_positive_results_without_inventing_coverage():
    item = _item("qualification_match", "miss", "资质与承揽范围匹配", "所提供资质与范围相符")
    report = _report([item], completed=["a1", "a2"], selected=["a1", "a2"])
    section = build_report_document(report)["sections"][1]
    fields = {field["label"]: field["summary"] for field in section["fields"]}
    assert "资质与承揽范围匹配" in fields["分包性质与资质匹配"]
    assert "符合" in fields["分包性质与资质匹配"]
    assert "没有可单独归纳" in fields["拟分包内容与负面清单"]


def test_personnel_summary_includes_existing_roster_special_trade_and_number_checks():
    def item(kind, item_id, status, label):
        return {**_item(kind, status, label, '核查结果'), 'item_id': item_id}
    report = _report([item('completeness', 'RC-A09', 'miss', '主要管理人员名单'),
                      item('completeness', 'RC-L14', 'hit', '特殊工种人员名单及操作证书'),
                      item('cert_validity', 'A2-05', 'undecidable', '项目经理交安 B 证编号一致性')],
                     completed=['completeness', 'a2'], selected=['completeness', 'a2'])
    result = build_report_document(report)
    fields = {field['label']: field['summary'] for field in result['sections'][2]['fields']}
    assert '资料已查见' in fields['主要人员及岗位'] and '主要管理人员名单' in fields['主要人员及岗位']
    assert '项目经理交安 B 证编号一致性' in fields['人员与证件对应']
    assert '特殊工种人员名单及操作证书' in fields['特殊工种人员']
    assert len(result['issues']) == 2
    report['sections'][0]['items'][1]['human_disposition'] = {'code': 'ignored'}
    fields = {field['label']: field['summary'] for field in build_report_document(report)['sections'][2]['fields']}
    assert '特殊工种人员名单及操作证书' not in fields['特殊工种人员']


def test_personnel_fields_describe_their_own_source_group_coverage():
    report = _report([_item('person_consistency', 'miss', '社保覆盖', '已核查')], completed=['a2'], selected=['a2', 'completeness'])
    section = build_report_document(report)['sections'][2]
    fields = {field['label']: field['summary'] for field in section['fields']}
    assert '尚未完成' in fields['主要人员及岗位']
    assert '尚未完成' in fields['特殊工种人员']
    assert '社保覆盖' in fields['劳动关系及社保']
    report['coverage']['selected_groups'] = ['a2']
    fields = {field['label']: field['summary'] for field in build_report_document(report)['sections'][2]['fields']}
    assert '未选择' in fields['主要人员及岗位']


def test_completeness_fields_follow_checklist_and_leave_unknown_items_unclassified():
    def checklist_item(item_id, label):
        return {**_item("completeness", "hit", label, "需要核实"), "item_id": item_id}

    report = _report([checklist_item("RC-A09", "主要管理人员名单"),
                      checklist_item("RC-A10", "授权书"),
                      checklist_item("RC-09", "总监签章"),
                      checklist_item("RC-FUTURE", "新增核查事项")],
                     completed=["completeness", "signature_chain"],
                     selected=["completeness", "signature_chain"])
    result = build_report_document(report)
    fields = {field["label"]: field["summary"] for field in result["sections"][0]["fields"]}
    assert "主要管理人员名单" in fields["人员资料"]
    assert "授权书" in fields["签字盖章及审批资料"]
    assert "总监签章" in fields["签字盖章及审批资料"]
    assert all("新增核查事项" not in value for value in fields.values())
    assert any(issue["label"] == "新增核查事项" for issue in result["issues"])


def test_completeness_presence_does_not_claim_substantive_compliance():
    item = {**_item("completeness", "miss", "营业执照", "已查见营业执照"), "item_id": "RC-A02"}
    report = _report([item], completed=["completeness", "signature_chain"],
                     selected=["completeness", "signature_chain"])
    fields = {field["label"]: field["summary"] for field in build_report_document(report)["sections"][0]["fields"]}
    assert "已查见" in fields["主体资料"]
    assert "符合" not in fields["主体资料"]


def test_duplicate_issues_are_merged_only_with_same_subject_and_evidence():
    first = {**_item("cert_validity", "undecidable", "证件有效期", "期限待核实"),
             "finding_id": 11, "subject_id": 1,
             "evidence_list": [{"material_id": 7, "page": 3, "quote": "期限"}]}
    duplicate = {**first, "finding_id": 12, "item_id": "other-rule"}
    other_person = {**first, "finding_id": 13, "subject_id": 2}
    other_page = {**first, "finding_id": 14,
                  "evidence_list": [{"material_id": 7, "page": 4, "quote": "期限"}]}
    projected = build_report_document(_report([first, duplicate, other_person, other_page],
                                               completed=["a2"], selected=["a2"]))
    assert len(projected["issues"]) == 3
    assert projected["issues"][0]["finding_ids"] == [11, 12]
    assert projected["issues"][1]["finding_ids"] == [13]


def test_missing_subject_or_evidence_never_merges_distinct_findings():
    first = {**_item("cert_validity", "undecidable", "证件有效期", "期限待核实"), "finding_id": 11}
    second = {**first, "finding_id": 12}
    assert len(build_report_document(_report([first, second], completed=["a2"], selected=["a2"]))["issues"]) == 2
