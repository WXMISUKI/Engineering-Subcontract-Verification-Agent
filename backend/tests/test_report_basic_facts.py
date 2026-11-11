from types import SimpleNamespace

from app.plus.blockchain_supervision.report_basic_facts import extract_report_basic_facts, merge_party_facts, merge_amount_facts, project_task_fact, apply_employer_confirmation


def material(text, *, page=1, material_id=1):
    return SimpleNamespace(id=material_id, file_name="报审材料.pdf", role="packet",
                           parse_state="done", pages_json=[{"page": page, "text": text}],
                           degradations_json=[])


def test_packet_explicit_contractor_and_two_line_quantity_title():
    materials = [material("施工总承包单位：中国铁建大桥工程局集团有限公司\n"
                          "致：上海沪申高速公路建设发展有限公司\n"
                          "分包单位名称：武汉龙辰建设有限公司", page=1),
                 material("G15公路嘉金段改扩建工程主线施工11标\n"
                          "预制板梁劳务分包工程量清单\n"
                          "分包内容 | 单位 | 数量 | 合价（万元） | 分包范围和内容\n"
                          "预制板梁 | 片 | 6838 | 4173.0485 | 预应力空心板梁预制", page=2)]
    facts = extract_report_basic_facts(materials)
    assert facts["general_contractor"]["value"] == "中国铁建大桥工程局集团有限公司"
    assert facts["general_contractor"]["evidence"][0]["page"] == 1
    assert facts["project_name"]["value"] == "G15公路嘉金段改扩建工程主线施工11标"
    assert facts["contract_section"]["value"] == "主线施工11标"
    assert facts["subcontract_scope"]["value"] == "G15公路嘉金段改扩建工程主线施工11标预制板梁劳务分包"
    assert facts["subcontract_scope"]["evidence"][0]["page"] == 2
    assert facts["subcontractor"]["value"] == "武汉龙辰建设有限公司"
    assert facts["subcontractor"]["evidence"][0]["page"] == 1
    assert facts["employer"]["value"] is None


def test_report_recipient_is_a_traceable_candidate_not_an_owner_fact():
    m = material("上海城投公路投资（集团）有限公司\n劳务分包报审表\n"
                 "工程名称：G15公路嘉金段改扩建工程主线施工11标\n"
                 "致:上海沪申高速公路建设发展有限公司\n")
    facts = extract_report_basic_facts([m])
    assert facts["employer"]["value"] is None
    candidate = facts["employer_recipient_candidates"][0]
    assert candidate["value"] == "上海沪申高速公路建设发展有限公司"
    assert candidate["page"] == 1 and candidate["material_id"] == 1 and candidate["quote"].startswith("致")
    result = merge_party_facts({}, facts)["employer"]
    assert result["status"] == "unresolved" and result["company"] is None
    assert result["candidate_company"] == candidate["value"]


def test_recipient_outside_report_form_is_not_a_candidate():
    facts = extract_report_basic_facts([material("监理通知单\n致：某某监理有限公司")])
    assert facts["employer_recipient_candidates"] == []


def test_confirmed_employer_only_applies_to_unchanged_candidate_and_not_conflict():
    facts = extract_report_basic_facts([material("劳务分包报审表\n致：上海沪申高速公路建设发展有限公司")])
    party = merge_party_facts({}, facts)["employer"]
    selected = party["recipient_candidates"][0]
    confirmed = apply_employer_confirmation(party, selected)
    assert confirmed["company"] == selected["value"] and confirmed["status"] == "matched"
    assert confirmed["evidence"][0]["page"] == 1
    assert apply_employer_confirmation(party, {**selected, "material_id": 999})["company"] is None
    assert apply_employer_confirmation({**party, "status": "conflict"}, selected)["company"] is None


def test_labeled_project_name_does_not_include_label():
    facts = extract_report_basic_facts([material("工程名称：G15公路嘉金段改扩建工程主线施工11标")])
    assert facts["project_name"]["value"] == "G15公路嘉金段改扩建工程主线施工11标"


def test_project_road_range_parenthetical_and_short_title_match():
    full = "G15公路嘉金段（G1503公路-S32公路）改扩建工程主线施工11标"
    short = "G15公路嘉金段改扩建工程主线施工11标"
    result = extract_report_basic_facts([material("工程名称：" + full), material(short, page=2)])['project_name']
    assert result['status'] == 'matched'
    assert result['value'] == full
    assert [row['page'] for row in result['evidence']] == [1, 2]
    assert project_task_fact(short, result, project_name=True)['status'] == 'matched'
    other = "G15公路嘉金段（G1501公路-S32公路）改扩建工程主线施工11标"
    assert project_task_fact(other, result, project_name=True)['status'] == 'conflict'


def test_project_distinct_sections_and_non_road_parentheses_remain_conflicts():
    for a, b in [("G15公路嘉金段改扩建工程主线施工11标", "G15公路嘉金段改扩建工程主线施工12标"),
                 ("G15公路嘉金段（一期）改扩建工程主线施工11标", "G15公路嘉金段（二期）改扩建工程主线施工11标")]:
        result = extract_report_basic_facts([material(a), material(b, material_id=2)])['project_name']
        assert result['status'] == 'conflict'


def test_task_field_does_not_claim_different_material_as_its_source():
    packet = {"status": "matched", "value": "另一工程", "evidence": [{"value": "另一工程", "page": 1}]}
    result = project_task_fact("任务工程", packet)
    assert result["value"] == "任务工程" and result["source"] == "task"
    assert result["status"] == "conflict"
    assert result["evidence"] == []
    assert result["material_candidates"] == packet["evidence"]
    matched = project_task_fact("另一工程", packet)
    assert matched["evidence"] == packet["evidence"]
    assert matched["status"] == "matched"


def test_material_project_conflict_is_not_replaced_by_fallback_metadata():
    from app.plus.blockchain_supervision.report_document import build_report_document
    fact = project_task_fact(None, {"status": "conflict", "value": None, "evidence": [{"value": "甲"}, {"value": "乙"}]})
    result = build_report_document({"basic_information": {"project_name": fact},
                                    "meta": {"project_name": "其他候选"}, "coverage": {}})
    assert result["basic_information"]["工程名称"] is None
    assert result["basic_information_status"]["工程名称"] == "conflict"


def test_explicit_subcontract_amount_keeps_decimal_and_unit():
    facts = extract_report_basic_facts([material("分包金额：4173.0485万元")])
    assert facts["subcontract_amount"]["value"] == "4173.0485"
    assert facts["subcontract_amount"]["unit"] == "万元"


def test_equivalent_amount_units_and_precision_do_not_create_conflict():
    facts = extract_report_basic_facts([
        material("分包金额：4173.0485万元"),
        material("拟分包金额：41730485.00元", material_id=2),
    ])
    amount = facts["subcontract_amount"]
    assert amount["status"] == "matched"
    assert (amount["value"], amount["unit"]) == ("4173.0485", "万元")
    assert len(amount["evidence"]) == 2


def test_different_amounts_still_conflict_after_unit_conversion():
    facts = extract_report_basic_facts([
        material("分包金额：4173.0485万元"),
        material("拟分包金额：41730486元", material_id=2),
    ])
    assert facts["subcontract_amount"]["status"] == "conflict"
    assert facts["subcontract_amount"]["value"] is None


def test_contract_and_packet_amounts_are_compared_in_the_same_unit():
    packet = {"status": "matched", "value": "4173.0485", "unit": "万元", "evidence": [{"page": 1}]}
    contract = {"status": "hit", "value": "41730485.00", "unit": "元", "source": "合同.pdf"}
    assert merge_amount_facts(packet, contract) == packet
    conflict = merge_amount_facts(packet, {**contract, "value": "41730486"})
    assert conflict["status"] == "conflict"
    assert conflict["contract_candidate"]["source"] == "合同.pdf"
    assert merge_amount_facts(packet, {**contract, "unit": None})["status"] == "conflict"


def test_general_contract_amount_is_not_used_as_subcontract_amount():
    item = material("分包金额：4173.0485万元")
    item.role = "general"
    assert extract_report_basic_facts([item])["subcontract_amount"]["value"] is None


def test_packet_amount_uses_table_column_geometry():
    item = material("分包金额\n4173.0485万元")
    item.pages_json[0]["ocr_meta"] = {"layout": [
        {"text": "分包金额", "bbox": [894, 751, 990, 779]},
        {"text": "安全生产许可证有效期", "bbox": [730, 751, 870, 779]},
        {"text": "2026年3月31日", "bbox": [732, 806, 862, 831]},
        {"text": "4173.0485万元", "bbox": [881, 806, 1012, 831]},
    ]}
    result = extract_report_basic_facts([item])["subcontract_amount"]
    assert (result["value"], result["unit"]) == ("4173.0485", "万元")
    assert result["evidence"][0]["page"] == 1


def test_ambiguous_amount_rows_remain_unresolved():
    item = material("分包金额\n100万元\n200万元")
    item.pages_json[0]["ocr_meta"] = {"layout": [
        {"text": "分包金额", "bbox": [100, 100, 200, 120]},
        {"text": "100万元", "bbox": [110, 140, 190, 160]},
        {"text": "200万元", "bbox": [110, 180, 190, 200]},
    ]}
    assert extract_report_basic_facts([item])["subcontract_amount"]["value"] is None


def test_explicit_conflicting_companies_are_not_silently_selected():
    facts = extract_report_basic_facts([material("施工总承包单位：甲建设有限公司"),
                                       material("施工总承包单位：乙建设有限公司", material_id=2)])
    assert facts["general_contractor"]["status"] == "conflict"
    assert facts["general_contractor"]["value"] is None
    assert len(facts["general_contractor"]["evidence"]) == 2


def test_unreadable_page_is_not_a_source():
    item = material("施工总承包单位：某建设有限公司")
    item.degradations_json = [{"kind": "parse_incomplete", "ocr_failed_pages": [1]}]
    facts = extract_report_basic_facts([item])
    assert facts["general_contractor"]["value"] is None


def test_contract_and_packet_party_disagreement_stays_unresolved():
    packet = extract_report_basic_facts([material("施工总承包单位：甲建设有限公司")])
    contract = {"general_contractor": {"status": "matched", "company": "乙建设有限公司", "evidence": []}}
    merged = merge_party_facts(contract, packet)
    assert merged["general_contractor"]["status"] == "conflict"
    assert merged["general_contractor"]["company"] is None


def test_packet_explicit_role_resolves_contract_candidate():
    packet = extract_report_basic_facts([material("施工总承包单位：甲建设有限公司")])
    contract = {"general_contractor": {"status": "unresolved", "candidate_company": "甲建设有限公司", "evidence": []}}
    merged = merge_party_facts(contract, packet)
    assert merged["general_contractor"]["status"] == "matched"
    assert merged["general_contractor"]["company"] == "甲建设有限公司"
    assert merged["general_contractor"]["evidence"][0]["page"] == 1


def test_merge_preserves_subcontractor_role():
    contract = {"subcontractor": {"status": "matched", "company": "分包公司", "evidence": []}}
    assert merge_party_facts(contract, {})["subcontractor"]["company"] == "分包公司"


def test_packet_subcontractor_conflict_is_not_silently_selected():
    packet = extract_report_basic_facts([material("分包单位名称：甲建设有限公司")])
    contract = {"subcontractor": {"status": "matched", "company": "乙建设有限公司", "evidence": []}}
    merged = merge_party_facts(contract, packet)
    assert merged["subcontractor"]["status"] == "conflict"
    assert merged["subcontractor"]["company"] is None


def test_packet_name_conflicting_with_contract_candidate_needs_review():
    packet = extract_report_basic_facts([material("施工总承包单位：甲建设有限公司")])
    contract = {"general_contractor": {"status": "unresolved", "candidate_company": "乙建设有限公司", "evidence": []}}
    assert merge_party_facts(contract, packet)["general_contractor"]["status"] == "conflict"
