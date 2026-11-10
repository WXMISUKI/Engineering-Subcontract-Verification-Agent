from types import SimpleNamespace

from app.plus.blockchain_supervision.ai_review_context import scope_task_level_materials


def _page(material_id, page):
    return {"task_id": 1, "subject_id": None, "material_id": material_id,
            "page": page, "text": f"material-{material_id}-page-{page}"}


def test_task_level_finding_scopes_material_context_to_anchor_and_adjacent_pages():
    finding = SimpleNamespace(
        task_id=1,
        subject_id=None,
        kind="transfer_scope",
        material_id=2,
        page=5,
        evidence_json=[],
    )
    materials = [_page(2, page) for page in range(1, 9)] + [_page(3, 1)]

    scoped, incomplete = scope_task_level_materials(finding, materials)

    assert [(row["material_id"], row["page"]) for row in scoped] == [(2, 4), (2, 5), (2, 6)]
    assert incomplete is True


def test_task_level_finding_without_anchor_keeps_full_context():
    finding = SimpleNamespace(
        task_id=1,
        subject_id=None,
        kind="transfer_scope",
        material_id=None,
        page=None,
        evidence_json=[],
    )
    materials = [_page(2, 1), _page(3, 1)]

    scoped, incomplete = scope_task_level_materials(finding, materials)

    assert scoped == materials
    assert incomplete is False
