from types import SimpleNamespace

from app.plus.blockchain_supervision.certificate_number_check import run_b_certificate_number_check
from app.plus.blockchain_supervision.subject_reviews import run_subject_checks
from app.plus.blockchain_supervision.router import _finding_dto
from app.plus.blockchain_supervision.pinning import pin_findings
from app.plus.blockchain_supervision.pinning import reference_location_mode


def material(roster, certificate):
    return SimpleNamespace(
        id=1, role="packet", file_name="报审材料.pdf", parse_state="done",
        pages_json=[{"page": 22, "text": roster}, {"page": 28, "text": certificate}],
        degradations_json=[],
    )


def test_markdown_roster_and_certificate_expose_both_candidate_pages():
    roster = ("主要管理人员名单\n| 序号 | 姓名 | 职务 | 持证及证号 | 电话 |\n"
              "| --- | --- | --- | --- | --- |\n"
              "| 1 | 张三 | 项目经理 | 交安B证 沪交安B（25）G00206 | 13800000001 |")
    cert = "安全生产考核合格证书\n姓名：张三\n证书编号：沪交安B25G00683"
    result = run_b_certificate_number_check(SimpleNamespace(id=1), [material(roster, cert)])[0]
    assert result.status == "undecidable"
    assert "张三" in result.message and "G00206" in result.message and "G00683" in result.message
    assert {ref["page"] for ref in result.evidence_json} == {22, 28}
    assert all(ref["anchor"]["bbox"] is None for ref in result.evidence_json)
    assert all(ref['location_mode'] == 'page' for ref in result.evidence_json)
    pinning_material = material(roster, cert)
    pin_findings([result], [pinning_material])
    dto = _finding_dto(result)
    assert {ref["page"] for ref in dto["evidence"]} == {22, 28}


def test_roster_row_does_not_borrow_another_person_number():
    roster = ("主要管理人员名单\n| 序号 | 姓名 | 职务 | 持证及证号 | 电话 |\n"
              "| --- | --- | --- | --- | --- |\n"
              "| 1 | 张三 | 项目经理 | 无 | 13800000001 |\n"
              "| 2 | 李四 | 安全员 | 交安B证 沪交安B（25）G00206 | 13800000002 |")
    cert = "安全生产考核合格证书\n姓名：张三\n证书编号：沪交安B25G00683"
    result = run_b_certificate_number_check(SimpleNamespace(id=1), [material(roster, cert)])
    assert not any("G00206" in finding.message for finding in result)


def test_uncertain_or_ambiguous_certificate_does_not_create_candidate_difference():
    roster = ("主要管理人员名单\n| 序号 | 姓名 | 职务 | 持证及证号 | 电话 |\n"
              "| --- | --- | --- | --- | --- |\n"
              "| 1 | 张三 | 项目经理 | 沪交安B（25）G00206 | 13800000001 |")
    cert = "安全生产考核合格证书\n姓名：张三\n证书编号：沪交安B25G00683"
    mat = material(roster, cert)
    mat.degradations_json = [{"kind": "parse_incomplete", "ocr_suspect_pages": [28]}]
    result = run_b_certificate_number_check(SimpleNamespace(id=1), [mat])
    assert not any("G00683" in finding.message for finding in result)
    mat.degradations_json = []
    mat.pages_json[1]["text"] += "\n姓名：李四"
    result = run_b_certificate_number_check(SimpleNamespace(id=1), [mat])
    assert not any("G00683" in finding.message for finding in result)


def test_equal_numbers_with_format_variation_are_not_reported_as_difference():
    roster = ("主要管理人员名单\n| 序号 | 姓名 | 职务 | 持证及证号 | 电话 |\n"
              "| --- | --- | --- | --- | --- |\n"
              "| 1 | 张三 | 项目经理 | 沪交安B（25）G00206 | 13800000001 |")
    cert = "安全生产考核合格证书\n姓名：张三\n证书编号：沪交安B25G00206"
    result = run_b_certificate_number_check(SimpleNamespace(id=1), [material(roster, cert)])
    assert not any("同名证书" in finding.message for finding in result)


def test_roster_certificate_number_on_continuation_rows_is_a_reviewable_clue():
    roster = ("主要管理人员名单\n| 序号 | 姓名 | 职务 | 持证以及证号 | 电话 | 备注 |\n"
              "| --- | --- | --- | --- | --- | --- |\n"
              "| 1 | 张三 | 项目经理 | 一级建造师 123 | 13800000001 | |\n"
              "| | | | 交安 B 证 | | |\n"
              "| | | 沪交安 B（25）G00206 | | | |\n"
              "| 2 | 李四 | 项目总工 | 工程师 | 13800000002 | |")
    cert = "安全生产考核合格证书\n姓名：张三\n证书编号：沪交安B25G00683"
    result = run_b_certificate_number_check(SimpleNamespace(id=1), [material(roster, cert)])[0]
    assert result.status == "undecidable"
    assert "G00206" in result.message and "G00683" in result.message
    assert {ref['page'] for ref in result.evidence_json} == {22, 28}


def test_continuation_scan_stops_at_next_person():
    roster = ("主要管理人员名单\n| 序号 | 姓名 | 职务 | 持证及证号 | 电话 |\n"
              "| --- | --- | --- | --- | --- |\n"
              "| 1 | 张三 | 项目经理 | 建造师 | 13800000001 |\n"
              "| 2 | 李四 | 安全员 | 交安 B 证 | 13800000002 |\n"
              "| | | 沪交安 B（25）G00206 | | |")
    cert = "安全生产考核合格证书\n姓名：张三\n证书编号：沪交安B25G00683"
    result = run_b_certificate_number_check(SimpleNamespace(id=1), [material(roster, cert)])
    assert not any("G00206" in finding.message for finding in result)


def test_subject_scope_does_not_pair_different_subjects():
    roster = ("主要管理人员名单\n| 序号 | 姓名 | 职务 | 持证及证号 | 电话 |\n"
              "| --- | --- | --- | --- | --- |\n"
              "| 1 | 张三 | 项目经理 | 沪交安B（25）G00206 | 13800000001 |")
    cert = "安全生产考核合格证书\n姓名：张三\n证书编号：沪交安B25G00683"
    first = material(roster, "")
    first.subject_id = 1
    second = material("", cert)
    second.id, second.subject_id = 2, 2
    subjects = [SimpleNamespace(id=i, stable_code=str(i), retired=False, origin="explicit", name=name,
                                type_confirmed=True, subject_type="professional")
                for i, name in [(1, "甲公司"), (2, "乙公司")]]
    results = run_subject_checks(SimpleNamespace(id=1), subjects, [first, second],
                                 run_b_certificate_number_check, item_id="A2-05",
                                 kind="cert_validity", source="规则", allow_empty=True)
    assert not any("G00683" in finding.message for finding in results)


def test_reference_requires_pdf_rendered_frame_for_precise_mode():
    ref = {'anchor': {'status': 'precise', 'coordinate_space': 'pdf_page', 'bbox': [1, 2, 3, 4]}}
    assert reference_location_mode(ref) == 'page'
    ref['anchor']['coordinate_frame'] = 'rendered_page'
    assert reference_location_mode(ref) == 'precise'
