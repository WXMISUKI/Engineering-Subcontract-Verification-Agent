"""V1.4 keeps the V1.3 facts while adding a letter-style opinion and 8-sheet appendix."""
from copy import deepcopy
from types import SimpleNamespace

from sqlalchemy.orm import Session

from app.plus.blockchain_supervision import report_template_v13 as v13
from app.plus.blockchain_supervision import report_template_v14 as v14
from app.plus.blockchain_supervision import report_template_render
from app.plus.blockchain_supervision.models import BsReviewVersion
from test_bs_report_version_http import _client, _seed_version


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
             "source": "测试依据", "label": "营业执照", "message": "未查见营业执照",
             "recommendation": "补交营业执照"},
            {"finding_id": "warning", "item_id": "RC-A09", "status": "hit", "source": "测试依据",
             "label": "材料页码", "message": "材料页码不连续"},
        ]}],
    }


def test_v14_builds_conclusion_sections_and_eight_attachments():
    report = sample_report()
    before = deepcopy(report)
    document = v14.build_document(report)

    assert report == before
    assert document["template_id"] == "v1.4"
    assert document["available"] is True
    assert document["title"] == "测试施工专业分包审查意见"
    assert document["conclusion"]["suggested"] == "附条件同意"
    assert document["conclusion"]["risk_counts"]["缺项"] == 1
    assert document["conclusion"]["approval_opinion"] == ""

    assert [row["title"] for row in document["sections"]] == [
        "1. 资质与承接能力", "2. 安全生产许可", "3. 人员配置与持证",
        "4. 分包范围与负面清单", "5. 合同与支付条款",
        "6. 报审程序与资料补正",
    ]
    assert len(document["attachments"]) == 8
    assert document["attachments"][0]["title"] == "一、分包合法性审查"
    assert document["attachments"][-1]["title"] == "八、综合审查结论"

    text = "\n".join(block.get("text", "") for block in document["blocks"])
    assert "一、建设单位审查意见（正式表述）" in text
    assert "本次共核查" not in text
    assert "结论：" in text
    assert "7. 结论" in text
    assert "附件一：本次自动核查结果" in text
    assert "附件二：8 表签约前核查导航" in text
    assert "附件三：需补充材料/人工核查事项" in text
    assert "暂缓签订该分包合同" in text
    assert "补正后重新报审" in text
    assert "指挥部/建设单位（章）" in text
    assert "R10" not in text and "R4" not in text
    assert "未形成机器结论" not in text


def test_v14_appendix_does_not_prefill_compliant():
    document = v14.build_document(sample_report())
    appendix = document["appendix"]
    assert all(row[1] in {"符合", "不符合", "缺项"} for row in appendix["auto_rows"])
    assert all("待核实" not in row[1] and "无法核实" not in row[1]
               for row in appendix["auto_rows"])
    assert appendix["manual_rows"]
    assert any("请核对" in row[3] for row in appendix["manual_rows"])


def test_v14_reads_labor_certificate_and_expired_safety_license():
    from app.plus.blockchain_supervision import certificate_facts

    pages = [
        {"page": 4, "text": "3.资质证书\n建筑业企业施工劳务备案证书\n"
                             "企业名称:武汉龙辰建设有限公司\n"
                             "证书编号:D342238415有效期至:2029年08月04日"},
        {"page": 5, "text": '```json\n{"证照标题": "<x>,安全生产许可证", '
                             '"营业名称": "<x>,武汉龙辰建设有限公司", '
                             '"营业期限": "<x>,2023年03月31日至2026年03月31日"}\n```'},
        {"page": 12, "text": "企业安全生产许可证信息: 安全生产许可证编号：（鄂）JZ安许证字[2023]002798 "
                              "许可证有效日期: 2023/3/31至2026/3/31 许可范围: 建筑施工"},
    ]
    material = SimpleNamespace(file_name="报审.pdf", pages_json=pages)
    facts = certificate_facts.extract_certificate_facts([material])

    assert facts["qualification"]["title"] == "建筑业企业施工劳务备案证书"
    assert facts["qualification"]["cert_no"] == "D342238415"
    assert facts["qualification"]["valid_until"] == "2029-08-04"
    assert facts["safety_license"]["cert_no"] == "（鄂）JZ安许证字[2023]002798"
    assert facts["safety_license"]["valid_until"] == "2026-03-31"
    assert certificate_facts.qualification_evidence(facts) == {
        "business_license": False, "qualification": True, "safety_permit": True}

    lines = v14._section_fact_lines({"certificate_facts": facts}, {"拟分包范围": "预制板梁劳务"}, True)
    text = "\n".join(lines)
    assert "建筑业企业施工劳务备案证书" in text
    assert "有效期至2029-08-04" in text
    assert "安全生产许可证" in text and "有效期至2026-03-31" in text

    expired = {"kind": "cert_validity", "item_id": "A2-02", "status": "undecidable",
               "material_missing": False, "material_only": False, "label": "证件有效期",
               "message": "证照识别结果（「安全生产许可证」，有效期至 2026-03-31）早于基准日；需核对续期情况"}
    condition = v14._condition_for_item(expired)
    assert "已提供" in condition and "续期" in condition
    assert "补交有效期内的安全生产许可证" not in condition


def test_v14_signing_nav_uses_professional_consequence_column():
    document = v14.build_document(sample_report())
    appendix = document["appendix"]
    assert appendix["nav_headers"] == ["检查表", "签约前应提交/核验", "合同应约定", "未落实的后果"]
    assert len(appendix["nav_rows"]) == 8
    assert all(len(row) == 4 for row in appendix["nav_rows"])
    assert appendix["nav_rows"][0][3].startswith("未取得发包人书面同意")
    assert "暂缓" not in appendix["nav_rows"][0][3]


def test_v14_worksheet_docx_is_readable_and_separate_from_opinion():
    from io import BytesIO

    from docx import Document

    document = v14.build_document(sample_report())
    payload = {"template_document": document, "draft_notice": ""}
    data = report_template_render.word_bytes_worksheet_v14(payload, "digest", "2026-11-17")
    assert data[:2] == b"PK"
    assert len(data) > 10000
    rendered = Document(BytesIO(data))
    text = "\n".join(paragraph.text for paragraph in rendered.paragraphs)
    assert "八张检查表工作底稿" in text
    assert "不构成正式审查意见" in text


def test_v14_contract_section_uses_extracted_clause_pages():
    from app.plus.blockchain_supervision.contract_clause_facts import extract_contract_clauses

    pages = [
        {"page": 46, "text": "劳务分包合同 合同编号：CRDQ-01 甲方：中国铁建 乙方：武汉龙辰"},
        {"page": 48, "text": "1.3.2 分包工程暂定价款 计价方式：固定单价"},
        {"page": 49, "text": "计量与验工计价：乙方按月报送工程量"},
        {"page": 50, "text": "支付方式：按月支付进度款"},
        {"page": 57, "text": "违约责任：逾期按日支付违约金"},
        {"page": 58, "text": "合同解除与终止"},
        {"page": 65, "text": "质量、安全、农民工工资支付约定"},
    ]
    material = SimpleNamespace(file_name="报审.pdf", role="labor", parse_state="done", pages_json=pages)
    facts = extract_contract_clauses([material])
    assert facts["pages"] == [46, 48, 49, 50, 57, 58, 65]
    assert facts["clauses"]["pricing"]["page"] == 48
    assert facts["clauses"]["payment"]["page"] == 50

    report = sample_report()
    report["contract_clauses"] = facts
    document = v14.build_document(report)
    contract = next(section for section in document["sections"] if section["code"] == "contract")
    assert contract["status"] == "待核实"
    assert contract["bullets"] == []
    assert "合同主要条款" in contract["conclusion"]
    assert any("合同文本已随报审资料提供" in line for line in contract["summary_lines"])
    text = "\n".join(block.get("text", "") for block in document["blocks"])
    assert "本次未涉及：合同与支付条款" not in text
    assert "合同主要条款已在文本中定位" in text


def test_v14_negative_list_coverage_is_not_reported_as_checked():
    item = {"kind": "illegal_subcontract", "item_id": "W16-02", "status": "undecidable",
            "material_missing": False, "material_only": False,
            "label": "分包负面清单主体或关键性工作",
            "message": "不可判定：负面清单核查覆盖不足，已评估1/21条（当前负面清单目录）"}
    negative = {"kind": "negative_list", "item_id": "NL-R-01", "status": "miss",
                "material_missing": False, "material_only": False,
                "label": "省级负面清单增补", "message": "未触发"}
    lines = v14._section_summary_lines("legality", [negative, item])
    assert any("1/21" in line and "不能认定未命中" in line for line in lines)
    conclusion = v14._section_summary_conclusion("legality", [item], "fallback")
    assert "1/21" in conclusion and "不能认定未命中" in conclusion
    condition = v14._condition_for_item(item)
    assert "1/21" in condition and "逐条对照" in condition

    variant = dict(item, message="无法核实：现有核查结果尚不足以确认是否涉及负面清单；共有 20 项未完成或仍需核实")
    assert "覆盖不足" in v14._section_summary_conclusion("legality", [negative, variant], "fallback")
    assert "覆盖不足" in v14._condition_for_item(variant)


def test_v14_person_condition_does_not_ask_social_record_again_when_covered():
    item = {"kind": "person_consistency", "item_id": "A2-01", "status": "undecidable",
            "material_missing": False, "material_only": False, "label": "社保资料覆盖管理人员",
            "message": "姓名层面已覆盖当前已提取的 3 名主要管理人员；劳动合同关系未证明（缺吴道记、于文祥）"}
    condition = v14._condition_for_item(item)
    assert "社保记录已提供" in condition
    assert "补交主要人员社保缴费证明" not in condition


def test_v14_skips_certificate_item_that_already_covers_baseline():
    covered = {"kind": "cert_validity", "item_id": "A2-02", "status": "undecidable",
               "material_missing": False, "material_only": False, "label": "证件有效期",
               "message": "材料页面标注「施工劳务备案证书」，有效期至 2029-08-04（覆盖基准日）；需核对原件"}
    assert v14._condition_for_item(covered) == ""


def test_v14_select_template_and_legacy_compatibility():
    documents = {"v1.3": {"template_id": "v1.3"}, "v1.4": {"template_id": "v1.4", "available": True}}
    payload = {"template_documents": deepcopy(documents)}
    selected = v14.select_template(payload, "v1.4")
    assert selected["template_document"]["template_id"] == "v1.4"
    assert "template_documents" not in selected

    legacy = v14.select_template({"template_documents": deepcopy(documents)}, "legacy")
    assert "template_document" not in legacy
    assert "template_documents" not in legacy


def test_v14_word_and_pdf_exports_are_readable_files():
    from io import BytesIO

    from docx import Document

    document = v14.build_document(sample_report())
    payload = {"template_document": document, "draft_notice": ""}
    docx = report_template_render.word_bytes_v14(payload, "digest", "2026-11-17")
    pdf = report_template_render.pdf_bytes(payload, "digest", "2026-11-17")
    assert docx[:2] == b"PK"
    assert pdf[:4] == b"%PDF"
    assert len(docx) > 10000
    assert len(pdf) > 10000
    rendered = Document(BytesIO(docx))
    section = rendered.sections[0]
    assert round(section.page_width.cm, 1) == 21.0
    assert round(section.page_height.cm, 1) == 29.7
    assert round(section.left_margin.cm, 1) == 3.2
    assert round(section.top_margin.cm, 1) == 2.5
    assert rendered.styles["Normal"].font.size.pt == 11
    assert rendered.styles["Normal"].paragraph_format.line_spacing == 1.5


def test_v14_uses_v13_base_when_multi_subject_is_unavailable():
    report = sample_report()
    report["checklist_summary"]["subjects"].append({"subject_id": "two", "name": "第二主体", "status": "ready"})
    base = v13.build_document(report)
    document = v14.build_document(report, base=base)
    assert document["available"] is False
    assert "单分包主体" in document["reason"]


def test_v14_frozen_http_round_trip_exports_word_and_pdf(tmp_path):
    client, engine, task_id = _client(tmp_path)
    _seed_version(engine, task_id)
    frozen = sample_report()
    document = v14.build_document(frozen)
    with Session(engine) as db:
        version = db.query(BsReviewVersion).filter_by(task_id=task_id, version_no=1).one()
        snapshot = deepcopy(version.snapshot_json)
        for kind in ("report", "opinion"):
            snapshot[kind] = {"meta": frozen["meta"], "business_document": frozen["business_document"],
                              "template_documents": {"v1.4": document}}
        version.snapshot_json = snapshot
        db.commit()
    root = f"/api/plus/blockchain-supervision/tasks/{task_id}"
    query = "version_no=1&stage=confirmed&template_id=v1.4"
    report = client.get(root + "/report?" + query)
    opinion = client.get(root + "/opinion?" + query)
    assert report.status_code == opinion.status_code == 200
    assert report.json()["template_document"]["template_id"] == "v1.4"
    assert opinion.json()["template_document"] == report.json()["template_document"]
    digest = opinion.json()["opinion_digest"]
    for kind in ("report", "opinion"):
        for fmt, signature in (("docx", b"PK"), ("pdf", b"%PDF")):
            response = client.get(root + f"/{kind}/export?{query}&fmt={fmt}&expected_digest={digest}")
            assert response.status_code == 200, response.text if response.status_code != 200 else ""
            assert response.content.startswith(signature)
