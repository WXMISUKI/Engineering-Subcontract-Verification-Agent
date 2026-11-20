import copy
import json
from pathlib import Path

import pytest

from app.plus.blockchain_supervision.rule_catalog import (
    InvalidRuleCatalog,
    load_rule_catalog,
    validate_rule_catalog,
)


ROOT = Path(__file__).resolve().parents[2]


def _catalog():
    return json.loads((ROOT / "backend/app/plus/blockchain_supervision/data/rule_catalog.json")
                      .read_text(encoding="utf-8"))


def test_catalog_examples_validate_and_keep_modes_separate():
    catalog = load_rule_catalog(project_root=ROOT)
    scopes = {row["id"]: (row["mode_scope"], row["applies_to"]) for row in catalog["entries"]}
    assert scopes["asset:admission.review-flow"] == ("admission_only", ["admission"])
    assert scopes["asset:retrospective.contract-analysis"] == (
        "retrospective_only", ["contract_retrospective"])
    assert scopes["asset:shared.reference-retrieval"][0] == "shared_infrastructure"


def test_catalog_rejects_duplicate_ids():
    catalog = _catalog()
    catalog["entries"].append(copy.deepcopy(catalog["entries"][0]))
    with pytest.raises(InvalidRuleCatalog):
        validate_rule_catalog(catalog, project_root=ROOT)


def test_catalog_rejects_missing_owner():
    catalog = _catalog()
    del catalog["entries"][0]["owner_module"]
    with pytest.raises(InvalidRuleCatalog):
        validate_rule_catalog(catalog, project_root=ROOT)


@pytest.mark.parametrize("scope,modes", [
    ("admission_only", ["admission", "contract_retrospective"]),
    ("shared_infrastructure", ["admission"]),
    ("unresolved", ["admission"]),
])
def test_catalog_rejects_ambiguous_or_contradictory_mode_scope(scope, modes):
    catalog = _catalog()
    catalog["entries"][0]["mode_scope"] = scope
    catalog["entries"][0]["applies_to"] = modes
    with pytest.raises(InvalidRuleCatalog):
        validate_rule_catalog(catalog, project_root=ROOT)


def test_catalog_rejects_non_string_scope_without_leaking_type_error():
    catalog = _catalog()
    catalog["entries"][0]["mode_scope"] = []
    with pytest.raises(InvalidRuleCatalog):
        validate_rule_catalog(catalog, project_root=ROOT)
