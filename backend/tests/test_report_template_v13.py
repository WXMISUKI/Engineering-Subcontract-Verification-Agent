"""V1.3 reports preserve facts, coverage and the human approval boundary."""
from copy import deepcopy

import pytest

from app.plus.blockchain_supervision import report_template_v13 as template


def sample_report():
    return {
        "meta": {"title": "测试任务", "task_id": 12, "generated_at": "2026-11-16T08:00:00"},
        "checklist_summary": {"subjects": [{"subject_id": "one", "name": "测试分包企业", "status": "ready",
                                            "subject_type": "professional", "type_confirmed": True}]},
        "coverage": {"completed_groups": ["completeness", "a2"]},
        "business_document": {"basic_information": {"工程名称": "测试工程", "标段": "测试标段",
              "施工总承包单位": "测试总包", "建设单位（业主）": "测试业主", "分包类型": "专业分包",
              "拟分包范围": "测试施工", "报告日期": "2026-11-16T08:00:00"}},
        "sections": [{"kind": "completeness", "items": [
            {"finding_id": "missing", "item_id": "RC-A02", "status": "hit", "material_missing": True,
             "source": "测试依据", "label": "营业执照", "message": "未查见营业执照"},
            {"finding_id": "hint", "status": "undecidable", "finding_semantics": "manual_prompt", "message": "请人工复查"},
            {"finding_id": "ignored", "status": "hit", "review": {"decision": "ignored"}, "message": "已忽略事项"},
            {"finding_id": "open", "status": "undecidable", "open_term": True},
            {"finding_id": "warning", "status": "hit", "message": "一般预警"},
        ]}],
    }


def test_facts_statistics_and_unsigned_approval():
    report = sample_report()
    before = deepcopy(report)
    document = template.build_document(report)
    assert report == before
    assert document["title"] == "测试施工专业分包审查意见（AI初审）"
    assert document["recipient"] == "测试总包／项目部"
    assert document["statistics"] == {"total": 2, "counts": {"符合": 0, "待核实": 1, "不符合": 1}, "missing": 1}
    assert [row["number"] for row in document["issues"]] == [1, 2, 3]
    assert len(document["dimensions"]) == 8
    assert all(not value for value in document["approval"].values())
    text = str(document)
    assert "已忽略事项" not in text
    assert "暂缓通过" not in text and "限期补正" not in text
    assert "91页" not in text and "已对原件逐页核对" not in text
    assert "报送日期" not in document["basic_information"]


def _template_paragraphs(document):
    return [block["text"] for block in document["blocks"]
            if block["type"] in {"paragraph", "heading"}]


def test_body_matches_template_wording_and_only_shows_supported_statuses():
    report = sample_report()
    report["meta"]["task_id"] = 7
    report["basic_information"] = {
        "submitted_date": {"value": "2026-11-10", "status": "matched"},
        "subcontract_item_name": {"value": "预制构件吊装", "status": "matched"},
    }
    report["checklist_summary"]["subjects"][0]["checklist_summary"] = {"applicable_total": 3}
    report["submission_materials"] = [{"material_id": 6, "subject_id": "one", "pages": 58, "role": "labor"}]
    info = report["business_document"]["basic_information"]
    info.update({"工程名称": "测试工程测试标段", "标段": "测试标段", "分包单位": "测试分包人",
                 "分包类型": "专业分包", "拟分包金额": "100万元", "施工总承包单位": "测试承包人"})

    document = template.build_document(report)
    assert template._item_text({"分包事项": "预制构件吊装劳务", "分包类型": "劳务分包"}) == "预制构件吊装劳务分包"
    assert template._item_text({"分包类型": "劳务分包"}) == "劳务分包"
    blocks = document["blocks"]
    intro = next(block["text"] for block in blocks
                 if block["type"] == "paragraph" and block["text"].startswith("根据"))
    assert intro == (
        "根据相关法律法规和文件，对你部【11月10日】报送的测试工程测试标段预制构件吊装专业分包"
        "报审材料（共58页）实施合规性智能审查。共核查事项2项。审查情况如下："
    )
    paragraphs = _template_paragraphs(document)
    start = paragraphs.index("一、报审基本情况")
    end = paragraphs.index("二、审查依据")
    assert paragraphs[start + 1:end] == ["分包单位：测试分包人", "分包事项：预制构件吊装"]

    dimension_table = next(block for block in blocks
                           if block["type"] == "table" and block["headers"][-1] == "初审意见")
    allowed = {"缺项", "不符合", "符合", "—"}
    assert {row[-1] for row in dimension_table["rows"]} <= allowed
    assert all(row["verdict"] in allowed for row in document["dimensions"])

    issue_start = paragraphs.index("四、审查发现问题")
    opinion_start = paragraphs.index("五、总体审查意见")
    issue_text = paragraphs[issue_start + 1:opinion_start]
    assert any("营业执照" in text for text in issue_text)
    assert all(not any(label in text for label in ("符合项", "证据不足事项", "提示事项"))
               for text in issue_text)
    assert not any("当前核查记录未形成" in text for text in issue_text)

    opinion_text = paragraphs[opinion_start + 1:paragraphs.index("六、补充说明")]
    assert not any(text.startswith("待核实项：") for text in opinion_text)
    assert len([text for text in opinion_text if text.startswith(("符合项：", "不符合项：", "缺项："))]) <= 3
    assert all("机器核查结果" not in text and "不等同于审批通过" not in text for text in opinion_text)

    attachment_start = paragraphs.index("一、审查概况")
    attachment_end = paragraphs.index("二、审查依据", attachment_start)
    assert any("拟分包金额：100万元" == text for text in paragraphs[attachment_start:attachment_end])
    issue_table = next(block for block in blocks
                       if block["type"] == "table" and block["headers"][-1] == "责任单位")
    assert all(row[-1] in {"分包人", "承包人"} for row in issue_table["rows"])
    assert all(value in document["basis"] for value in template._TEMPLATE_BASIS)
    assert any("核查事项按纳入机器结论统计的逐项记录计数" in value for value in document["limitations"])


def test_v13_uses_three_opinions_and_marks_unclassified_dimensions_with_dash():
    report = sample_report()
    document = template.build_document(report)
    table = next(block for block in document["blocks"]
                 if block["type"] == "table" and block["headers"][-1] == "初审意见")
    verdicts = [row[-1] for row in table["rows"]]

    assert set(verdicts) <= {"符合", "不符合", "缺项", "—"}
    assert all(row[-1] != "待核实" for row in table["rows"])
    assert "—" in verdicts
    opinion = _template_paragraphs(document)
    opinion = opinion[opinion.index("五、总体审查意见") + 1:opinion.index("六、补充说明")]
    assert not any(row.startswith("待核实项：") for row in opinion)
    assert all(row.split("：", 1)[0] in {"符合项", "不符合项", "缺项"}
               for row in opinion if "：" in row and row.endswith(("符合", "缺项", "不符合")))
    assert any("未形成初审意见" in row for row in _template_paragraphs(document))


def test_dimension_opinion_precedence_and_completion_require_evidence():
    nonconforming = {"status": "hit", "material_missing": False, "scope_limited": False}
    missing = {"status": "hit", "material_missing": True, "scope_limited": False}
    passing = {"status": "miss", "material_missing": False, "scope_limited": False}
    undecidable = {"status": "undecidable", "material_missing": False, "scope_limited": False}

    assert template._dimension_verdict([(nonconforming, "不符合"), (missing, "缺项"), (passing, "符合")],
                                       ("a1",), {"a1"}) == "不符合"
    assert template._dimension_verdict([(missing, "缺项"), (passing, "符合")],
                                       ("a1",), {"a1"}) == "缺项"
    assert template._dimension_verdict([(passing, "符合")], ("a1",), {"a1"}) == "符合"
    assert template._dimension_verdict([(undecidable, "待核实")], ("a1",), {"a1"}) == "—"
    assert template._dimension_verdict([(passing, "符合")], ("a1",), set()) == "—"


@pytest.mark.parametrize(("item_id", "code", "title"), [
    ("RC-A02", "qualification_credit", "分包人资格与信用"),
    ("RC-A09", "personnel", "人员履约能力"),
    ("RC-A01", "contract_clauses", "分包合同条款"),
    ("RC-A10", "compliance", "分包合规性"),
])
def test_confirmed_missing_material_is_classified_before_dimension_aggregation(item_id, code, title):
    report = sample_report()
    report["sections"][0]["items"] = [report["sections"][0]["items"][0]]
    report["sections"][0]["items"][0]["item_id"] = item_id
    document = template.build_document(report)
    dimension = next(row for row in document["dimensions"] if row["code"] == code)
    assert dimension["verdict"] == "缺项"
    assert "缺项：" + title in _template_paragraphs(document)


def test_unconfirmed_missing_marker_does_not_make_a_dimension_missing():
    assert template._dimension_verdict(
        [({"status": "undecidable", "material_missing": True}, "待核实")], ("a1",), {"a1"}) == "—"


def test_unread_material_marker_does_not_make_a_dimension_missing():
    report = sample_report()
    report["sections"][0]["items"] = [report["sections"][0]["items"][0]]
    report["sections"][0]["items"][0]["evidence"] = {"material_id": 3, "page": 8}
    report["reading_coverage"] = [{"material_id": 3, "unread_pages": [8]}]
    document = template.build_document(report)
    assert next(row for row in document["dimensions"] if row["code"] == "qualification_credit")["verdict"] == "—"


def test_task_scope_boundaries_explain_negative_list_and_ocr_without_internal_ids():
    report = sample_report()
    report["ai_reading_gap_scope"] = [{"pages": [18, 23, 27, 57],
        "rule_ids": ["NL-B-%02d" % index for index in range(1, 13)]
                    + ["NL-T-%02d" % index for index in range(1, 9)]}]
    report["reading_coverage"] = [{"file_name": "报审材料.pdf", "suspect_pages": [18, 23, 27, 57]}]
    document = template.build_document(report)
    boundary = "\n".join(document["limitations"])
    assert "桥梁工程规则" not in boundary
    assert "NL-B-01至NL-B-12、NL-T-01至NL-T-08" in boundary
    assert "负面清单用于判断拟分包事项是否属于禁止分包的主体工程或关键性工作" in boundary
    assert "NL-B对应桥梁工程情形，NL-T对应隧道工程情形" in boundary
    assert "这不表示已认定存在违规" in boundary
    assert "OCR质量提示" in boundary and "300dpi以上" in boundary
    assert "AI 辅助候选未覆盖规则 NL-B-01、" not in boundary


def test_related_scope_statements_are_compacted_for_the_attachment():
    report = sample_report()
    report["scope_statements"] = [
        "部分参考依据未取得或查询不完整；本报告保留已执行的规则结果。",
        "第五类仅核对已提供的内部信用及事故材料，未查外部网站。",
        "本次未核查分包金额占比、工程量占比（当前未启用）。",
        "未上传总包合同，未核查需该合同原文的情形。",
        "本次未核查合同条款用词与文字细节（合同语义核查）。",
        "资质仅按工程类别文字初筛；等级与规模、同人同岗尚未自动核实。",
    ]
    document = template.build_document(report)
    boundary = "\n".join(document["limitations"])
    assert boundary.count("依据与外部核验边界：") == 1
    assert boundary.count("合同及比例核查边界：") == 1
    assert boundary.count("资质边界：") == 1


def test_word_export_preserves_reference_paragraph_and_table_formatting():
    import io
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from app.plus.blockchain_supervision import export

    report = sample_report()
    report["business_document"]["basic_information"]["分包单位"] = "测试分包人"
    document = template.build_document(report)
    issues_block = next(block for block in document["blocks"]
                        if block["type"] == "table" and block["headers"] ==
                        ["序号", "问题类别", "问题事实", "补正要求", "责任单位"])
    expected_themes = [row["theme"] for row in document["issue_groups"] if row["category"] != "符合项"]
    assert [row[1] for row in issues_block["rows"]] == expected_themes
    payload = {"meta": report["meta"], "template_document": document}
    generated = Document(io.BytesIO(export.word_bytes(payload, "test-digest", "2026-11-17T10:00:00")))
    source_path = template.__file__.replace("report_template_v13.py", "templates/review_opinion_v13.docx")
    source = Document(source_path)

    title = generated.paragraphs[0]
    assert title.alignment == WD_ALIGN_PARAGRAPH.CENTER
    assert title.runs[0].bold is True and title.runs[0].underline is True
    intro = next(p for p in generated.paragraphs if p.text.lstrip().startswith("根据"))
    assert intro.paragraph_format.first_line_indent.pt == pytest.approx(11)
    unit = next(p for p in generated.paragraphs if p.text.startswith("分包单位："))
    assert unit.paragraph_format.left_indent.pt == pytest.approx(22.5)
    basis = next(p for p in generated.paragraphs if p.text.startswith("《中华人民共和国建筑法》"))
    assert basis.paragraph_format.first_line_indent.pt == pytest.approx(22)
    signature = next(p for p in generated.paragraphs if p.text.startswith("审查人（签字）"))
    assert "keepLines" in signature._p.pPr.xml
    assert generated.tables[0].rows[0].cells[0]._tc.tcPr.xml == source.tables[0].rows[0].cells[0]._tc.tcPr.xml
    all_text = "\n".join(p.text for p in generated.paragraphs) + str(
        [[cell.text for cell in row.cells] for table in generated.tables for row in table.rows])
    assert "鄢明志" not in all_text and "91页" not in all_text and "暂缓通过" not in all_text
    paragraphs = [p.text for p in generated.paragraphs]
    issue_heading = next(p for p in generated.paragraphs if p.text == "四、审查发现问题")
    assert issue_heading.paragraph_format.keep_with_next is True
    opinion = paragraphs[paragraphs.index("五、总体审查意见") + 1:paragraphs.index("审查结论：")]
    assert opinion
    assert all(value.strip() and not value.startswith("待核实项") for value in opinion)


def test_word_export_fills_the_reference_ooxml_skeleton():
    import io
    from copy import deepcopy
    from docx import Document
    from app.plus.blockchain_supervision import export

    report = sample_report()
    info = report["business_document"]["basic_information"]
    info.update({"工程名称": "测试工程", "标段": "测试标段", "分包单位": "测试分包人",
                 "施工总承包单位": "测试承包人", "分包事项": "吊装", "分包类型": "专业分包",
                 "拟分包金额": "100万元"})
    report["basic_information"] = {"submitted_date": {"value": "2026-11-10", "status": "matched"},
                                    "subcontract_item_name": {"value": "吊装", "status": "matched"}}
    report["submission_materials"] = [{"material_id": 6, "subject_id": "one", "pages": 58, "role": "labor"}]
    report["ai_reading_gap_scope"] = [{"pages": [18, 23], "rule_ids": ["NL-B-01", "NL-T-01"]}]
    report["reading_coverage"] = [{"file_name": "报审材料.pdf", "suspect_pages": [18, 23]}]
    document = template.build_document(report)
    payload = {"meta": report["meta"], "template_document": document}
    actual = Document(io.BytesIO(export.word_bytes(payload, "test-digest", "2026-11-17T10:00:00")))
    reference = Document(template.__file__.replace("report_template_v13.py", "templates/review_opinion_v13.docx"))

    assert actual.paragraphs[0].style.name == reference.paragraphs[0].style.name == "Normal"
    assert actual.paragraphs[0].alignment == reference.paragraphs[0].alignment
    assert actual.paragraphs[0].runs[0].underline is True
    assert actual.paragraphs[3].style.name == reference.paragraphs[3].style.name == "List Paragraph"
    assert actual.paragraphs[3].text == "报审基本情况"
    assert "numPr" in actual.paragraphs[3]._p.pPr.xml
    assert actual.paragraphs[4].style.name == reference.paragraphs[4].style.name == "List Paragraph"
    assert actual.paragraphs[7].paragraph_format.first_line_indent.pt == pytest.approx(
        reference.paragraphs[7].paragraph_format.first_line_indent.pt
    )
    assert actual.tables[0].rows[0]._tr.xml == reference.tables[0].rows[0]._tr.xml
    assert actual.tables[1].rows[0]._tr.xml == reference.tables[1].rows[0]._tr.xml
    assert len(actual.tables[1].rows) >= len(document["issue_groups"]) + 1
    grouped = [row for row in document["issue_groups"] if row["category"] != "符合项"]
    assert [row.cells[1].text for row in actual.tables[1].rows[1:1 + len(grouped)]] == [
        row["theme"] for row in grouped
    ]
    assert all("91页" not in p.text and "11标" not in p.text for p in actual.paragraphs)
    texts = [p.text for p in actual.paragraphs]
    assert texts.count("四、审查发现问题") == 1
    assert texts.index("四、审查发现问题") < texts.index("五、总体审查意见")
    assert texts.index("附件一、审查概况与审查依据") < texts.index("附件二、问题清单与补正要求")
    intro = next(p for p in actual.paragraphs if p.text.lstrip().startswith("根据相关法律法规"))
    assert "【11月10日】" in intro.text
    assert any(run.underline is True and "吊装专业分包" in run.text for run in intro.runs)
    dimension_summary = next(p for p in actual.paragraphs if p.text.startswith("本次按8个审查维度"))
    assert dimension_summary.text == (
        "本次按8个审查维度对报审材料核查：其中%s。"
        % template._dimension_summary(document["dimensions"])
    )
    assert any(run.bold is True and "个维度" in run.text for run in dimension_summary.runs)
    boundaries = [p for p in actual.paragraphs if "负面清单说明：" in p.text]
    assert boundaries and not boundaries[0].text.startswith("1. ")
    assert "numPr" in boundaries[0]._p.pPr.xml
    assert "劳务劳务分包" not in "\n".join(p.text for p in actual.paragraphs)


def test_uncovered_dimension_is_never_passed_by_internal_records():
    report = sample_report()
    report["sections"] = [{"kind": "internal_records", "items": [{"status": "miss"}]}]
    document = template.build_document(report)
    for row in document["dimensions"]:
        assert "符合" not in row["verdict"]
        if row["code"] in {"equipment", "funds_payment", "wages"}:
            assert row["coverage"] == "本版本未覆盖"


def test_reading_gap_and_conflict_do_not_become_confirmed_fact():
    report = sample_report()
    report["reading_coverage"] = [{"unread_pages": [1]}]
    report["business_document"]["basic_information_status"] = {"施工总承包单位": "conflict"}
    document = template.build_document(report)
    assert document["statistics"]["counts"]["不符合"] == 1
    assert document["recipient"] == "施工总承包单位／项目部"
    assert document["basic_information"]["施工总承包单位"] is None


def test_multiple_subjects_and_history_fail_explicitly():
    report = sample_report()
    report["checklist_summary"]["subjects"].append({"subject_id": "two"})
    document = template.build_document(report)
    with pytest.raises(LookupError, match="仅支持单分包主体"):
        template.select_template({"template_documents": {"v1.3": document}}, "v1.3")
    with pytest.raises(LookupError, match="未冻结"):
        template.select_template({"meta": {}}, "v1.3")
    assert template.select_template({"meta": {}, "template_documents": {"v1.3": document}}, "legacy") == {"meta": {}}
    with pytest.raises(ValueError):
        template.select_template({}, "unknown")


def test_template_digest_changes_with_content_but_not_generation_date():
    from app.plus.blockchain_supervision import export
    report = sample_report()
    def output():
        return template.select_template({"meta": report["meta"], "business_document": report["business_document"],
                 "template_documents": {"v1.3": template.build_document(report)}}, "v1.3")
    first = export.report_digest(output())
    report["meta"]["generated_at"] = "2026-11-17T09:00:00"
    report["business_document"]["basic_information"]["报告日期"] = "2026-11-17T09:00:00"
    assert export.report_digest(output()) == first
    report["sections"][0]["items"][0]["message"] = "内容发生变化"
    assert export.report_digest(output()) != first
    assert export.report_digest({"meta": report["meta"], "business_document": report["business_document"]}) != first


def test_word_pdf_and_preview_keep_five_columns_and_long_rows():
    import io
    from docx import Document
    import fitz
    from app.plus.blockchain_supervision import export
    report = sample_report()
    report["sections"][0]["items"][0]["message"] = "长表跨页核查事实。" * 200 + "最终事实终点"
    document = template.build_document(report)
    # The renderer must handle legitimately long grouped prose, while grouping
    # itself is verified separately and does not dump raw finding messages.
    document["issue_groups"][0]["fact"] = report["sections"][0]["items"][0]["message"]
    document["blocks"] = template._blocks(document)
    payload = {"meta": report["meta"], "template_document": document}
    word = export.word_bytes(payload)
    doc = Document(io.BytesIO(word))
    assert "pBdr" not in doc.styles["Title"].element.xml
    assert [cell.text for cell in doc.tables[-1].rows[0].cells] == ["序号", "问题类别", "问题事实", "补正要求", "责任单位"]
    assert "最终事实终点" in str([cell.text for row in doc.tables[-1].rows for cell in row.cells])
    assert "tblHeader" in doc.tables[-1].rows[0]._tr.xml
    assert export.opinion_word_bytes(payload)[:2] == b"PK"
    for data in (export.pdf_bytes(payload), export.opinion_pdf_bytes(payload)):
        with fitz.open(stream=data, filetype="pdf") as pdf:
            text = "".join(page.get_text() for page in pdf)
            assert len(pdf) > 3
            for expected in ("最终事实终点", "责任单位", "补正期限", "人工审批意见", "测试业主"):
                assert expected in text.replace("\n", "")
            for page in pdf:
                assert all(block[3] < page.rect.height - 25 for block in page.get_text("blocks"))
    markdown = export.opinion_markdown(payload, "test")
    assert "最终事实终点" in markdown and "责任单位" in markdown


def test_grouped_attachment_footer_stays_with_the_last_issue_rows():
    import fitz
    from app.plus.blockchain_supervision import export, report_content_summary

    report = sample_report()
    document = template.build_document(report)
    document["limitations"] = ["边界说明%s：" % index + "仅说明本次覆盖范围，不替代人工核验。" * 3
                               for index in range(1, 8)]
    document["basis"] = ["《审查依据文件%s》" % index for index in range(1, 7)]
    kinds = ["signature_chain", "signature_chain", "qualification_match", "negative_list",
             "transfer_scope", "illegal_subcontract", "a1_dependency", "person_consistency",
             "cert_validity", "internal_records", "contract_semantic", "amount_ratio", "workload_ratio"]
    items = [{"finding_id": index, "kind": kind, "subject_id": "one", "status": "undecidable",
              "human_disposition": {"code": "accepted", "note": "已核对相关材料并保留原核查意见。"}}
             for index, kind in enumerate(kinds, 1)]
    document["issue_groups"] = report_content_summary.summarize_items(items, ["待核实"] * len(items), "测试分包人")
    document["category_summaries"] = report_content_summary.category_summaries(document["issue_groups"])
    document["blocks"] = template._blocks(document)
    payload = {"meta": report["meta"], "template_document": document}

    with fitz.open(stream=export.pdf_bytes(payload), filetype="pdf") as pdf:
        final_page = pdf[-1].get_text()
        assert "内容指纹：" in final_page
        assert "分包工程量" in final_page
