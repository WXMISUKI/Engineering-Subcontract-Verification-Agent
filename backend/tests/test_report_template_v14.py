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


def test_v14_uses_uniform_subcontractor_labels_for_labor_and_professional():
    for kind in ("专业分包", "劳务分包"):
        report = sample_report()
        info = report["business_document"]["basic_information"]
        info.update({"分包类型": kind, "分包单位": "测试分包企业",
                     "分包事项": "预制梁施工", "拟分包金额": "100万元"})
        document = v14.build_document(report)
        text = "\n".join(block.get("text", "") for block in document["blocks"])
        assert "分包单位：测试分包企业" in text
        assert "分包内容：预制梁施工" in text
        assert "分包金额：100万元" in text
        assert "经核对分包报审资料" in text
        assert "劳务单位：" not in text
        assert "劳务内容：" not in text
        assert "合同金额：" not in text


def test_v14_qualification_matches_content_and_safety_moves_to_chapter_2():
    report = sample_report()
    info = report["business_document"]["basic_information"]
    info.update({"分包类型": "专业分包", "分包单位": "上海华地建设工程有限公司",
                 "分包事项": "桩基工程、基坑围护、地基处理", "拟分包金额": "61718114.95元",
                 "核查基准日": "2026-11-17"})
    report["certificate_facts"] = {
        "business_license": {"name": "上海华地建设工程有限公司", "legal_rep": "张三",
                             "registered_capital": "3.66亿元", "business_scope": "地基基础工程"},
        "qualification": {"category": "地基基础工程专业承包", "level": "一级",
                          "cert_no": "D123", "valid_until": "2030-01-01"},
        "safety_license": {"cert_no": "（沪）JZ安许证字〔2008〕160595",
                           "scope": "建筑施工", "valid_until": "2026-04-16"},
    }
    document = v14.build_document(report)
    text = "\n".join(block.get("text", "") for block in document["blocks"])
    assert "地基基础工程专业承包一级" in text
    assert "与桩基工程、基坑围护、地基处理分包内容匹配" in text
    assert "注册资本3.66亿元" in text
    first, second = text.split("2. 安全生产许可")[:2]
    assert "安全生产许可证" not in first
    assert "（沪）JZ安许证字〔2008〕160595" in second
    assert "早于核查基准日" in second


def test_v14_labor_certificate_is_unclassified_and_acceptable():
    report = sample_report()
    info = report["business_document"]["basic_information"]
    info.update({"分包类型": "劳务分包", "分包单位": "测试劳务公司",
                 "分包事项": "预制板梁劳务分包", "核查基准日": "2026-11-17"})
    report["certificate_facts"] = {
        "business_license": {"name": "测试劳务公司", "registered_capital": "1900万元"},
        "qualification": {"title": "建筑业企业施工劳务备案证书", "category": "施工劳务备案",
                          "cert_no": "D342238415", "valid_until": "2029-08-04"},
    }
    document = v14.build_document(report)
    text = "\n".join(block.get("text", "") for block in document["blocks"])
    assert "施工劳务备案" in text
    assert "施工劳务不分等级，可承接本工程劳务作业" in text
    assert "资质与承接能力已核对" in text


def test_v14_builds_conclusion_sections_and_eight_attachments():
    report = sample_report()
    before = deepcopy(report)
    document = v14.build_document(report)

    assert report == before
    assert document["template_id"] == "v1.4"
    assert document["available"] is True
    assert document["title"] == "测试施工专业分包审查意见"
    assert document["conclusion"]["suggested"] == "原则同意，附条件通过"
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
    assert "附件二：8 表签约前核查明细" in text
    assert "未自动核查事项及原因" in text
    assert "八、综合审查结论" in text
    assert "附件三" not in text
    assert "以上 8 表" not in text
    assert "原则同意，附条件通过" in text
    assert "补正并复核后方可签订" in text
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


def test_v14_contract_condition_skipped_when_contract_pages_located():
    item = {"item_id": "RC-C01", "kind": "contract", "status": "undecidable",
            "material_missing": False, "material_only": False,
            "label": "合同条款", "message": "合同计价条款需核对"}
    assert v14._condition_for_item(item, {}, {"pages": [46, 48]}) == ""
    assert "补交完整分包合同" in v14._condition_for_item(item, {}, {})


def test_v14_safety_summary_marks_unidentified_certificate():
    text = "\n".join(v14._safety_summary_lines({}, None))
    assert "提及安全生产许可证" in text
    assert "未识别证号与有效期" in text


def test_certificate_facts_ignore_contract_mentions_without_fields():
    from app.plus.blockchain_supervision import certificate_facts

    material = SimpleNamespace(file_name="分包合同.pdf", pages_json=[
        {"page": 10, "text": "分包人应持有营业执照、建筑业企业资质证书和安全生产许可证，"
                              "许可范围应覆盖本工程。"},
    ])
    facts = certificate_facts.extract_certificate_facts([material])
    assert facts["business_license"] == {}
    assert facts["qualification"] == {}
    assert facts["safety_license"] == {}
    assert facts["qualifications"] == []
    assert certificate_facts.qualification_evidence(facts) == {
        "business_license": False, "qualification": False, "safety_permit": False}


def test_certificate_facts_keep_pages_with_substantive_fields():
    from app.plus.blockchain_supervision import certificate_facts

    material = SimpleNamespace(file_name="证照.pdf", pages_json=[
        {"page": 1, "text": "营业执照 统一社会信用代码:91310000MA1K000000 "
                            "法定代表人:张三 经营范围:建设工程施工"},
        {"page": 2, "text": "建筑业企业资质证书 证书编号:D123456 "
                            "有效期至:2028年12月31日 资质类别:地基基础工程专业承包"},
        {"page": 3, "text": "安全生产许可证 安全生产许可证编号:（沪）JZ安许证字[2024]001 "
                            "许可证有效日期:2024年1月1日至2027年1月1日 许可范围:建筑施工"},
    ])
    facts = certificate_facts.extract_certificate_facts([material])
    assert facts["business_license"]["uscc"] == "91310000MA1K000000"
    assert facts["qualification"]["cert_no"] == "D123456"
    assert facts["safety_license"]["cert_no"] == "（沪）JZ安许证字[2024]001"
    assert certificate_facts.qualification_evidence(facts) == {
        "business_license": True, "qualification": True, "safety_permit": True}


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
    assert "安全生产许可证" not in text
    safety_text = "\n".join(v14._safety_summary_lines(facts["safety_license"], None))
    assert "有效期至2026-03-31" in safety_text

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


def test_v14_replaces_internal_signature_terms_and_sources():
    report = sample_report()
    report["sections"].append({"kind": "signature_chain", "items": [{
        "finding_id": "sig", "item_id": "A3-02-legal", "status": "undecidable",
        "label": "法定签字链", "message": "报审表中识别到总监理工程师意见及指挥部意见栏目",
        "source": "《实施细则》附件3/4签字栏",
    }]})
    document = v14.build_document(report)
    text = "\n".join(block.get("text", "") for block in document["blocks"])
    assert "法定签字链" not in text
    assert "报审表法定签署" in text
    item = next(row for row in v14._raw_items(report) if row["item_id"] == "A3-02-legal")
    assert "沪交行规〔2026〕8号" in item["source"]


def test_v14_preserves_single_attachment_signature_source():
    for attachment in ("附件3", "附件4"):
        source = v14._plain_source("A3-02-legal", "《上海市公路工程施工分包管理实施细则》%s签署栏" % attachment)
        assert attachment in source
        assert ("附件4" if attachment == "附件3" else "附件3") not in source


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
    price_sheet = next(sheet for sheet in document["attachments"] if sheet["code"] == "price")
    assert any(row[0] == "计价口径清晰" and "合同第48页" in row[2] for row in price_sheet["auto_rows"])
    assert any("合同条款已定位" in row[0] for row in document["appendix"]["manual_rows"])


def test_v14_adds_equipment_fact_when_list_is_present():
    lines = v14._section_fact_lines(
        {"equipment_facts": {"pages": [12, 13], "rows": [{"page": 12, "quote": "机械设备清单"}]}},
        {"分包类型": "专业分包", "拟分包范围": "测试施工"}, False)
    assert any("报审资料含设备清单或机械配置（第12–13页）" in line for line in lines)


def test_v14_personnel_section_lists_social_period_and_specific_gap():
    report = sample_report()
    report["personnel_roster"] = [{"name": "吴道记", "post": "项目负责人"},
                                  {"name": "于文祥", "post": "安全员"}]
    report["personnel_social"] = [{"name": "吴道记", "period": "202502-202507",
                                   "employer": "武汉龙辰建设有限公司"}]
    report["sections"].append({"kind": "person_consistency", "items": [
        {"finding_id": "l11", "item_id": "RC-L11", "status": "undecidable",
         "label": "安全员（身份证、学历证、职称证、交安C证）",
         "message": "名单第17页中的安全员于文祥，尚不能确认以下证件与其对应：职称证；请核对原件"},
        {"finding_id": "a201", "item_id": "A2-01", "status": "undecidable",
         "label": "社保资料覆盖管理人员",
         "message": "劳动合同关系未证明（缺吴道记、于文祥）"},
    ]})
    document = v14.build_document(report)
    text = "\n".join(block.get("text", "") for block in document["blocks"])
    assert "吴道记（项目负责人）、于文祥（安全员）" in text
    assert "社保缴费记录已覆盖：吴道记（202502-202507），参保单位武汉龙辰建设有限公司" in text
    assert "证件核对：安全员（职称证）" in text
    assert "劳动合同关系未证明：吴道记、于文祥" in text


def test_v14_personnel_section_lists_extracted_roster():
    report = sample_report()
    report["personnel_roster"] = [
        {"name": "吴道记", "post": "项目负责人"},
        {"name": "于文祥", "post": "安全员"},
        {"name": "吴昊", "post": "劳务员"},
    ]
    document = v14.build_document(report)
    personnel = next(section for section in document["sections"] if section["code"] == "personnel")
    text = "\n".join(personnel["summary_lines"])
    assert "吴道记（项目负责人）" in text
    assert "于文祥（安全员）" in text
    assert "吴昊（劳务员）" in text


def test_v14_negative_list_coverage_is_not_reported_as_checked():
    item = {"kind": "illegal_subcontract", "item_id": "W16-02", "status": "undecidable",
            "material_missing": False, "material_only": False,
            "label": "分包负面清单主体或关键性工作",
            "message": "发现 1 项需补充确认的问题"}
    negative = {"kind": "negative_list", "item_id": "NL-R-01", "status": "miss",
                "material_missing": False, "material_only": False,
                "label": "省级负面清单增补", "message": "未触发"}
    lines = v14._section_summary_lines("legality", [negative, item])
    assert any("需补充确认" in line for line in lines)
    assert not any("1/21" in line for line in lines)
    conclusion = v14._section_summary_conclusion("legality", [item], "fallback")
    assert "需补充确认" in conclusion and "1/21" not in conclusion
    condition = v14._condition_for_item(item)
    assert "补充负面清单相关参数" in condition

    variant = dict(item, message="无法核实：现有核查结果尚不足以确认是否涉及负面清单；共有 20 项未完成或仍需核实")
    assert "需补充确认" in v14._section_summary_conclusion("legality", [negative, variant], "fallback")
    assert "覆盖不足" in v14._condition_for_item(variant)


def test_v14_conclusion_is_three_tier():
    assert v14._suggested_conclusion({"dimensions": []}, [
        {"status": "miss", "material_missing": False}]) == "同意"
    assert v14._suggested_conclusion({"dimensions": []}, [
        {"status": "undecidable", "material_missing": False}]) == "原则同意，附条件通过"
    assert v14._suggested_conclusion(
        {"dimensions": [{"code": "compliance", "verdict": "不符合"}]},
        [{"status": "hit", "material_missing": False}]) == "不同意"


def test_v14_issue_row_has_four_segments_and_evidence_location():
    raw = v14._raw_items({"sections": [{"kind": "transfer_scope", "items": [{
        "item_id": "T15-01", "status": "hit", "material_missing": False, "material_only": False,
        "label": "转包情形", "message": "未命中：未见项目管理机构派驻记录",
        "source": "《沪交行规〔2026〕8号》第十五条（一）", "recommendation": "",
        "evidence": {"file_name": "分包合同.pdf", "page": 12, "quote": "分包人自行组织施工并自负盈亏"},
    }]}]})
    rows = v14._issue_rows(raw)
    assert len(rows) == 1
    assert rows[0]["impact"]
    assert "分包合同.pdf" in rows[0]["fact"] and "第12页" in rows[0]["fact"]
    assert "未命中" not in rows[0]["fact"]
    assert rows[0]["reason"].startswith("《沪交行规〔2026〕8号》")


def test_v14_special_operation_issue_uses_work_qualification_impact():
    raw = v14._raw_items({"sections": [{"kind": "personnel_adequacy", "items": [{
        "item_id": "A2-08", "status": "not_included", "material_missing": True,
        "label": "特种作业工种覆盖", "message": "缺少焊工实填工种名单",
        "evidence": {"file_name": "名单.pdf", "page": 2, "quote": "李四 焊工"},
    }]}]})
    row, = v14._issue_rows(raw)
    assert "特种作业工种或证书类型" in row["impact"]
    assert "关键岗位人员不足" not in row["impact"]


def test_v14_scope_status_survives_safety_certificate_expiry_summary():
    for state, verdict in (("not_included", "缺项"), ("undecidable", "待核实")):
        raw = v14._raw_items({"sections": [{"kind": "cert_validity", "items": [{
            "item_id": "A2-09", "status": state, "material_missing": state == "not_included",
            "label": "安全生产许可证许可范围", "message": "安全生产许可证许可范围需核对",
        }]}]})
        sections = v14._sections({}, raw, certificate_facts={"safety_license": {
            "name": "合成公司", "cert_no": "合成001", "scope": "建筑施工", "valid_until": "2028-01-01"}},
            baseline=v14._parse_date("2026-11-19"))
        section = next(row for row in sections if row["code"] == "safety_permit")
        assert section["status"] == verdict
        assert "许可行业核查结果：%s" % verdict in section["summary_conclusion"]
        assert any("许可范围需核对" in line for line in section["summary_lines"])


def test_v14_permit_scope_recommendations_distinguish_missing_sources_and_mapping():
    for message, expected in (
        ("未取得可核对的安全生产许可证原页，请补充证照", "补交本分包单位"),
        ("安全生产许可证已见，许可范围字段未提取，请核对证照原页", "补充可读的许可范围字段"),
        ("安全生产许可证许可范围为建筑施工；许可范围对应规则尚未验证", "对应关系"),
    ):
        item = {"item_id": "A2-09", "message": message, "label": "安全生产许可证许可范围"}
        assert expected in v14._condition_for_item(item)


def test_v14_wording_guard_blocks_internal_state_words():
    assert v14.wording_violations({"text": "本次共核查 12 项，1/21 通过"})
    assert v14.wording_violations({"text": "该事项未命中"})
    assert v14.wording_violations({"text": "材料齐备，未见异常"}) == []


def test_v14_reading_statement_is_rendered_in_blocks():
    report = sample_report()
    report["reading_coverage"] = [{
        "material_id": 7, "file_name": "扫描件.pdf", "total_pages": 310,
        "unread_pages": list(range(1, 41)), "over_limit_pages": [],
        "blank_pages": [41, 42], "suspect_pages": [43, 44, 45, 46, 47, 48],
        "affected_findings": 3,
    }]
    document = v14.build_document(report)
    text = "\n".join(block.get("text", "") for block in document["blocks"])
    assert "扫描件.pdf" in text
    assert "未读 40 页" in text
    assert "本次共 1 份材料存在阅读受限" in document["reading_statement"]


def test_v14_negative_list_pass_is_concise():
    item = {"kind": "illegal_subcontract", "item_id": "W16-02", "status": "miss",
            "material_missing": False, "material_only": False,
            "label": "分包负面清单主体或关键性工作", "message": "通过"}
    lines = v14._section_summary_lines("legality", [item])
    assert lines[0] == "负面清单：通过。"
    assert not any("1/21" in line for line in lines)
    conclusion = v14._section_summary_conclusion("legality", [item], "fallback")
    assert "负面清单：通过" in conclusion


def test_v14_negative_list_hit_is_concise():
    item = {"kind": "illegal_subcontract", "item_id": "W16-02", "status": "hit",
            "material_missing": False, "material_only": False,
            "label": "分包负面清单主体或关键性工作", "message": "发现 1 项问题"}
    lines = v14._section_summary_lines("legality", [item])
    assert lines[0] == "负面清单：发现问题，见下列注意事项。"
    assert not any("1/21" in line for line in lines)
    # 命中时 _section_summary_conclusion 返回调用方 fallback；主文结论由 lines 展示。


def test_v14_negative_coverage_uses_ratio_when_wording_changes():
    item = {"kind": "illegal_subcontract", "item_id": "W16-02", "status": "undecidable",
            "material_missing": False, "material_only": False,
            "label": "分包负面清单主体或关键性工作",
            "message": "不可判定：负面清单核查存在不可判定条目（NL-B-09）；已评估2/21条（当前负面清单目录）"}
    condition = v14._condition_for_item(item)
    assert "2/21" in condition and "逐条对照" in condition
    assert "书面说明" not in condition


def test_v14_expired_id_condition_names_role_and_expiry():
    item = {"kind": "cert_validity", "item_id": "A2-02", "status": "hit",
            "material_missing": False, "material_only": False, "label": "证件有效期",
            "message": "主体 1（武汉龙辰建设有限公司）：「安全员」身份证已超过有效期"
                       "（有效期至 2026-07-25），请核对最新身份证；仅核对所附证照文字，真实性未核验"}
    condition = v14._condition_for_item(item)
    assert "安全员" in condition and "2026-07-25" in condition
    assert "补充或核实相关材料" not in condition


def test_v14_merges_negative_conditions_preferring_coverage_ratio():
    raw = [
        {"kind": "illegal_subcontract", "item_id": "NL-B-09", "status": "undecidable",
         "material_missing": False, "material_only": False, "label": "负面清单条目",
         "message": "不可判定：负面清单核查存在不可判定条目（缺：负面清单确定性结论）"},
        {"kind": "illegal_subcontract", "item_id": "W16-02", "status": "undecidable",
         "material_missing": False, "material_only": False, "label": "分包负面清单主体或关键性工作",
         "message": "不可判定：负面清单核查存在不可判定条目；已评估2/21条"},
    ]
    conditions = v14._signing_conditions(raw)
    negatives = [text for text in conditions if "负面清单" in text]
    assert len(negatives) == 1
    assert "2/21" in negatives[0]


def test_v14_procedure_summary_uses_actual_missing_certificate():
    item = {"kind": "completeness", "item_id": "RC-L11", "status": "undecidable",
            "material_missing": False, "material_only": False,
            "label": "安全员（身份证、学历证、职称证、交安C证）",
            "message": "名单第17页中的安全员于文祥，尚不能确认以下证件与其对应：职称证；请核对原件"}
    lines = v14._section_summary_lines("procedure", [item])
    assert any("安全员（职称证）" in line for line in lines)
    assert all("交安C证" not in line for line in lines)


def test_v14_risk_counts_follow_item_verdicts():
    report = sample_report()
    report["sections"][0]["items"].append({
        "finding_id": "expired-id", "item_id": "A2-02", "status": "hit", "source": "测试依据",
        "label": "证件有效期", "message": "「安全员」身份证已超过有效期（有效期至 2026-07-25）"})
    document = v14.build_document(report)
    assert document["conclusion"]["risk_counts"]["不符合"] >= 1


def test_v14_person_condition_does_not_ask_social_record_again_when_covered():
    item = {"kind": "person_consistency", "item_id": "A2-01", "status": "undecidable",
            "material_missing": False, "material_only": False, "label": "社保资料覆盖管理人员",
            "message": "姓名层面已覆盖当前已提取的 3 名主要管理人员；劳动合同关系未证明（缺吴道记、于文祥）"}
    condition = v14._condition_for_item(item)
    assert "社保记录已提供" in condition
    assert "补交主要人员社保缴费证明" not in condition


def test_v14_merges_duplicate_personnel_conditions_into_one():
    raw = [
        {"kind": "person_consistency", "item_id": "A2-01", "status": "undecidable",
         "material_missing": False, "material_only": False, "label": "社保资料覆盖管理人员",
         "message": "劳动合同关系未证明（缺吴道记、于文祥、吴昊）"},
        {"kind": "transfer_scope", "item_id": "T15-04", "status": "undecidable",
         "material_missing": False, "material_only": False, "label": "现场劳动关系与社保期间",
         "message": "社保材料在场但未识别到统计期间"},
    ]
    conditions = v14._signing_conditions(raw)
    personnel = [text for text in conditions if "社保" in text or "劳动合同" in text]
    assert len(personnel) == 1
    assert "补交主要人员社保缴费证明" not in personnel[0]
    assert "社保缴费记录已在册" in personnel[0]


def test_display_message_keeps_negative_list_coverage_ratio():
    from app.plus.blockchain_supervision.finding_text import display_finding_message

    finding = SimpleNamespace(item_id="W16-02", status="undecidable",
                              message="不可判定：负面清单核查覆盖不足，已评估1/21条（当前负面清单目录）")
    text = display_finding_message(finding)
    assert "已评估 1/21 条" in text
    assert "其余 20 条未覆盖" in text
    assert "不能认定未命中" in text


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
    assert "内容指纹" not in "\n".join(paragraph.text for paragraph in rendered.paragraphs)
    import fitz

    with fitz.open(stream=pdf, filetype="pdf") as pdf_doc:
        pdf_text = "".join(page.get_text() for page in pdf_doc)
    assert "内容指纹" not in pdf_text


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
