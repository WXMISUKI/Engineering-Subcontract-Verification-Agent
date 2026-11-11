"""S4 定向用例：人员证件关系投影到 A2-05。

覆盖三态映射、材料/事实当前性、同名跨主体隔离、坐标三档降级、
问题描述边界，以及旧内核回退与同主体去重。
"""
from types import SimpleNamespace

from app.plus.blockchain_supervision.evidence_projection import (
    latest_current_relations,
    merge_projected_findings,
    project_person_certificate_relations,
)

ROSTER_TEXT = "管理人员名单\n陶瑜 项目经理 沪交安B25G00206"
CERT_TEXT = "姓名：陶瑜 证书编号：沪交安B25G00999"


def _page(number, text, words, *, frame="rendered_page", geometry=True, bbox=True):
    layout = []
    for index, word in enumerate(words):
        item = {"text": word}
        if bbox:
            item["bbox"] = [10 + index * 80, 20, 70 + index * 80, 40]
        layout.append(item)
    meta = {"quality": "ocr_ok", "layout": layout}
    if frame:
        meta["coordinate_frame"] = frame
    if geometry:
        meta["image_size"] = [1000, 1400]
        meta["page_size"] = [595, 842]
    return {"page": number, "text": text, "ocr_meta": meta}


def _material(mid, pages, *, subject_id=7, parse_state="done",
              superseded_by_id=None, replacement_target_id=None):
    return SimpleNamespace(id=mid, task_id=8, subject_id=subject_id, role="packet",
                           file_name="材料%s.pdf" % mid, parse_state=parse_state,
                           pages_json=pages, superseded_by_id=superseded_by_id,
                           replacement_target_id=replacement_target_id)


def _relation(**overrides):
    base = {
        "id": 29, "task_id": 8, "subject_id": 7, "person_key": "陶瑜",
        "person_name": "陶瑜", "post": "项目经理", "status": "conflict",
        "reason": "字段冲突：certificate.number",
        "dimensions_json": [{
            "field": "certificate.number", "expected": "沪交安B25G00206",
            "actual": "沪交安B25G00999", "result": "conflict", "reason": "名单与证书编号不一致",
        }],
        "left_json": {"material_id": 1, "page": 22, "quote": "陶瑜"},
        "right_json": {"material_id": 2, "page": 28, "quote": "沪交安B25G00999",
                       "fact_ids": [51, 52], "quality": "readable",
                       "pages": [28, 29, 30]},
        "fact_ids_json": [51, 52],
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _task(task_id=8):
    return SimpleNamespace(id=task_id)


def _materials(*, cert_pages=None, roster_pages=None, roster=None, cert=None):
    roster = roster or _material(1, roster_pages or [_page(
        22, ROSTER_TEXT, ["陶瑜", "项目经理", "沪交安B25G00206"])])
    cert = cert or _material(2, cert_pages or [_page(
        28, CERT_TEXT, ["姓名", "陶瑜", "证书编号", "沪交安B25G00999"])])
    return [roster, cert]


def _projects(relation, materials, **kwargs):
    return project_person_certificate_relations(
        None, _task(), materials, relations=[relation], **kwargs)


def test_matched_projects_to_miss_with_support_on_both_sides():
    relation = _relation(
        status="matched", reason="姓名、主体、证书类别与编号一致",
        dimensions_json=[{"field": "certificate.number", "expected": "沪交安B25G00206",
                          "actual": "沪交安B25G00206", "result": "matched"}],
        right_json={"material_id": 2, "page": 28, "quote": "沪交安B25G00999",
                    "fact_ids": [51], "quality": "readable"},
        fact_ids_json=[51],
    )
    finding = _projects(relation, _materials())[0]
    assert finding.status == "miss"
    assert finding.item_id == "A2-05"
    assert [ref["role"] for ref in finding.evidence_json] == ["support", "support"]
    assert "项目经理陶瑜" in finding.message


def test_conflict_projects_to_hit_and_marks_certificate_as_contradiction():
    finding = _projects(_relation(), _materials())[0]
    assert finding.status == "hit"
    assert finding.evidence_json[0]["role"] == "support"
    assert finding.evidence_json[1]["role"] == "contradiction"
    assert "沪交安B25G00206" in finding.message
    assert "沪交安B25G00999" in finding.message


def test_unresolved_projects_to_undecidable_and_marks_unreadable_right_side():
    relation = _relation(
        status="unresolved", reason="无法确认：certificate.number",
        dimensions_json=[{"field": "certificate.number", "result": "unresolved",
                          "reason": "证书编号未识别，无法核对"}],
        right_json={"material_id": 2, "page": 28, "quote": "沪交安B25G00999",
                    "fact_ids": [51], "quality": "review_required"},
        fact_ids_json=[51],
    )
    finding = _projects(relation, _materials())[0]
    assert finding.status == "undecidable"
    assert finding.evidence_json[1]["role"] == "unreadable"


def test_replaced_material_is_not_projected():
    materials = _materials(cert=_material(
        2, [_page(28, CERT_TEXT, ["沪交安B25G00999"])], superseded_by_id=3))
    selected = latest_current_relations(
        None, 8, materials, relation_rows=[_relation()],
        latest_fact_ids={2: {51, 52}})
    assert selected == []


def test_replacement_candidate_is_not_projected():
    materials = _materials(cert=_material(
        2, [_page(28, CERT_TEXT, ["沪交安B25G00999"])], replacement_target_id=2))
    selected = latest_current_relations(
        None, 8, materials, relation_rows=[_relation()],
        latest_fact_ids={2: {51, 52}})
    assert selected == []


def test_same_name_under_two_subjects_is_not_merged():
    first = _relation(id=29, subject_id=7)
    second = _relation(id=30, subject_id=8)
    projected = project_person_certificate_relations(
        None, _task(), _materials(), relations=[first, second])
    assert [finding.subject_id for finding in projected] == [7, 8]


def test_text_hit_without_bbox_stays_page_level():
    page = _page(22, ROSTER_TEXT, ["陶瑜", "项目经理"], bbox=False)
    materials = _materials(roster=_material(1, [page]))
    finding = _projects(_relation(), materials)[0]
    left = finding.evidence_json[0]
    assert left["location_mode"] == "page"
    assert left["anchor"]["status"] == "page_only"
    assert "bbox" not in left["anchor"]


def test_missing_frame_or_geometry_never_projects_precise():
    for overrides in ({"frame": None}, {"geometry": False}):
        page = _page(22, ROSTER_TEXT, ["陶瑜", "项目经理", "沪交安B25G00206"], **overrides)
        materials = _materials(roster=_material(1, [page]))
        finding = _projects(_relation(), materials)[0]
        assert finding.evidence_json[0]["location_mode"] == "page"


def test_layout_without_quote_does_not_invent_bbox():
    page = _page(22, ROSTER_TEXT, ["项目经理", "沪交安B25G00206"])
    materials = _materials(roster=_material(1, [page]))
    finding = _projects(_relation(), materials)[0]
    left = finding.evidence_json[0]
    assert left["location_mode"] == "page"
    assert "bbox" not in left["anchor"]


def test_message_does_not_include_unrelated_page_list():
    finding = _projects(_relation(), _materials())[0]
    assert "29" not in finding.message and "30" not in finding.message
    assert finding.message.count("第") == 2


def test_valid_projection_replaces_same_subject_old_finding():
    from app.plus.blockchain_supervision.models import BsFinding

    old_same = BsFinding(task_id=8, subject_id=7, item_id="A2-05", kind="cert_validity",
                         status="undecidable", message="旧结论")
    old_other = BsFinding(task_id=8, subject_id=8, item_id="A2-05", kind="cert_validity",
                          status="undecidable", message="其他主体旧结论")
    projected = _projects(_relation(), _materials())
    merged = merge_projected_findings([old_same, old_other], projected)
    assert {finding.subject_id for finding in merged if finding.item_id == "A2-05"} == {7, 8}
    assert merged[-1] is projected[0]


def test_without_valid_relation_legacy_findings_are_kept():
    from app.plus.blockchain_supervision.models import BsFinding

    legacy = BsFinding(task_id=8, subject_id=7, item_id="A2-05", kind="cert_validity",
                       status="undecidable", message="旧内核")
    assert merge_projected_findings([legacy], []) == [legacy]
    assert project_person_certificate_relations(
        None, _task(), _materials(), relations=[]) == []


def test_latest_invalid_row_falls_back_to_current_history():
    stale = _relation(id=30, right_json={
        "material_id": 3, "page": 9, "quote": "旧证书", "fact_ids": [61]})
    current = _relation(id=29)
    materials = _materials()
    selected = latest_current_relations(
        None, 8, materials, relation_rows=[current, stale],
        latest_fact_ids={2: {51, 52}})
    assert [row.id for row in selected] == [29]


def test_relation_with_old_fact_batch_is_not_projected():
    selected = latest_current_relations(
        None, 8, _materials(), relation_rows=[_relation()],
        latest_fact_ids={2: {99}})
    assert selected == []

def test_conflict_prefers_number_evidence_for_roster_quote():
    relation = _relation(left_json={
        "material_id": 1, "page": 22, "quote": "陶瑜", "name": "陶瑜", "post": "项目经理",
        "number_evidence": {
            "material_id": 1, "page": 22, "quote": "沪交安B25G00206",
            "certificate_type": "B", "number": "沪交安B25G00206",
            "number_source": "text_table", "extractor_version": "roster-cert.v1",
        },
    })
    finding = _projects(relation, _materials())[0]
    assert finding.status == "hit"
    support = finding.evidence_json[0]
    assert support["quote"] == "沪交安B25G00206"
    assert support["number_source"] == "text_table"
    assert support["extractor_version"] == "roster-cert.v1"

def test_projection_carries_certificate_authority_evidence():
    relation = _relation(right_json={
        "material_id": 2, "page": 28, "quote": "沪交安B25G00999",
        "fact_ids": [51], "quality": "readable",
        "certificate_type": "B", "certificate_number": "沪交安B25G00999",
        "generation_id": "g-2",
        "certificate_evidence": [
            {"material_id": 1, "page": 22, "role": "superseded",
             "certificate_number": "沪交安B25G00206", "fact_ids": [11]},
            {"material_id": 2, "page": 28, "role": "authoritative",
             "certificate_number": "沪交安B25G00999", "fact_ids": [51]},
        ],
    })
    finding = _projects(relation, _materials())[0]
    contradiction = finding.evidence_json[1]
    assert contradiction["generation_id"] == "g-2"
    assert [item["role"] for item in contradiction["certificate_evidence"]] == [
        "superseded", "authoritative",
    ]

def test_projection_carries_relation_reason_code():
    relation = _relation(
        status="unresolved",
        reason="无法确认：certificate.number",
        dimensions_json=[{
            "field": "certificate.number", "expected": "沪交安B25G00206",
            "actual": None, "result": "unresolved",
            "reason": "补件证书编号未识别，不能回退原证书判通过",
            "reason_code": "supplement_unreadable",
        }],
    )
    finding = _projects(relation, _materials())[0]
    assert finding.status == "undecidable"
    assert all(ref.get("reason_code") == "supplement_unreadable"
               for ref in finding.evidence_json)
