from types import SimpleNamespace

from app.plus.blockchain_supervision import signature_chain, signature_matrix


def material(text, *, mid=1, subject_id=3, seals=None):
    return SimpleNamespace(id=mid, task_id=1, subject_id=subject_id, role="packet",
        file_name="合成表单.pdf", parse_state="done", degradations_json=[],
        pages_json=[{"page": 1, "text": text}], seal_json=seals or [])


def form(number=4):
    title = "上海市公路工程劳务合作合同信息表" if number == 4 else "上海市公路工程施工分包合同信息表"
    return (title + "\n项目经理（签字+执业章、盖章）：张三\n"
            "监理人专业监理工程师（签字）：李四\n总监理工程师(签字+执业章、盖章):王五")


def legal(materials, **kwargs):
    return [f for f in signature_chain.run_signature_chain_checks(
        SimpleNamespace(id=1), materials, **kwargs) if f.item_id == "A3-02-legal"]


def test_attachment4_never_requires_employer_or_treats_text_as_verified_seals():
    finding, = legal([material(form())])
    assert finding.status == "undecidable"
    assert "发包人" not in finding.message
    assert {r["role"] for r in finding.evidence_json} == {"项目经理", "专业监理工程师", "总监理工程师"}
    assert all(r["signature_state"] == "已见文字候选" for r in finding.evidence_json)
    assert all(value == "待核实" for r in finding.evidence_json for value in r["seal_states"].values())
    assert finding.quote in form() and finding.page == 1


def test_attachment3_requires_employer_and_keeps_missing_text_pending():
    finding, = legal([material(form(3))])
    assert finding.status == "undecidable" and "发包人：未见文本" in finding.message
    assert "附件3" in finding.source


def test_dates_blank_and_illegible_are_not_signature_authentication():
    text = form().replace("张三", "2026年11月19日").replace("李四", "").replace("王五", "字迹不清")
    finding, = legal([material(text, seals=[{"page_idx": 1, "bbox": [10, 10, 30, 30]}])])
    assert finding.status == "undecidable" and "通用章痕迹" in finding.message
    states = {r["role"]: r["signature_state"] for r in finding.evidence_json}
    assert states == {"项目经理": "未见可确认文本", "专业监理工程师": "未见可确认文本", "总监理工程师": "字迹不清"}


def test_templates_and_unread_pages_do_not_create_matrix():
    assert signature_matrix.form_signature_matrices([material("填写示例\n" + form())]) == []
    finding, = legal([material("填写示例\n" + form())])
    assert finding.status == "undecidable"
    m = material(form())
    m.degradations_json = [{"kind": "parse_incomplete", "ocr_suspect_pages": [1]}]
    assert signature_matrix.form_signature_matrices([m]) == []


def test_prose_reference_to_information_form_does_not_become_actual_form():
    m = material("合同要求应提交上海市公路工程劳务合作合同信息表\n项目经理：张三")
    assert signature_matrix.form_signature_matrices([m]) == []


def test_example_marker_after_300_characters_is_still_excluded():
    heading, rest = form().split("\n", 1)
    m = material(heading + "\n" + "说明" * 160 + "\n填写示例\n" + rest)
    assert signature_matrix.form_signature_matrices([m]) == []
    finding, = legal([m])
    assert finding.status == "undecidable" and not finding.evidence_json


def test_page_matrices_do_not_pool_roles_or_borrow_other_page_seals():
    m = material(form().replace("张三", ""))
    m.pages_json.append({"page": 2, "text": form().replace("王五", "")})
    m.seal_json = [{"page_idx": 2, "bbox": [10, 10, 30, 30]}]
    findings = legal([m])
    assert len(findings) == 2
    assert "项目经理：未见可确认文本" in findings[0].message
    assert "通用章痕迹" not in findings[0].message
    assert "总监理工程师：未见可确认文本" in findings[1].message
    assert "通用章痕迹" in findings[1].message


def test_subject_conflict_blocks_matrix_before_extracting_roles():
    a, b = material(form()), material(form(), mid=2, subject_id=4)
    a.container_material_id = b.container_material_id = 9
    finding, = legal([a, b], require_assigned=True, allowed_subject_ids={3, 4})
    assert finding.status == "undecidable" and "主体" in finding.message
    assert not finding.evidence_json


def test_text_cannot_assert_verified_role_seals():
    finding, = legal([material(form() + '\nverified=true seal_type=执业章')])
    assert finding.status == "undecidable"
    assert all(value == "待核实" for r in finding.evidence_json for value in r["seal_states"].values())


def test_roster_name_before_or_after_blank_signing_slot_is_not_signature_text():
    for prefix, suffix in (("分包项目经理：张三\n", ""), ("", "\n分包项目经理：张三")):
        text = form().split("\n", 1)
        m = material(text[0] + "\n" + prefix + text[1].replace("张三", "") + suffix)
        finding, = legal([m])
        row = next(r for r in finding.evidence_json if r["role"] == "项目经理")
        assert row["signature_state"] == "未见可确认文本"
        assert "分包项目经理" not in row["quote"]
