"""授权书依据必须与被审查主体类型一致。"""

from datetime import date

import pytest

from app.plus.blockchain_supervision.models import BsFinding, BsSubject, BsTask
from app.plus.blockchain_supervision import reporting
from app.plus.blockchain_supervision.router import _finding_dto
from app.plus.blockchain_supervision.rules import configured_basis_lookup


@pytest.mark.parametrize("number", range(1, 11))
@pytest.mark.parametrize(("subject_type", "side"), [("professional", "一"), ("labor", "二")])
def test_shared_attachment_basis_uses_confirmed_subject_side(number, subject_type, side):
    subject = BsSubject(id=7, task_id=1, stable_code="synthetic", subject_type=subject_type,
                        type_confirmed=True, type_conflict=False)
    finding = BsFinding(id=4, task_id=1, subject_id=7, item_id=f"RC-A{number:02d}",
                        kind="completeness", status="miss", message="示例核查结果")
    result = _finding_dto(finding, configured_basis_lookup(as_of=date(2026, 11, 10)), {7: subject},
                          as_of=date(2026, 11, 10))
    assert (result.get("basis") or {}).get("clause_id") == f"ZHB-2025-0620#{side}、{number}"


@pytest.mark.parametrize(
    ("subject_type", "expected_clause"),
    [("professional", "ZHB-2025-0620#一、10"),
     ("labor", "ZHB-2025-0620#二、10"),
     (None, None)],
)
def test_authorization_basis_matches_subject_type(subject_type, expected_clause):
    subject = BsSubject(id=7, task_id=1, stable_code="synthetic", subject_type=subject_type,
                        type_confirmed=bool(subject_type), type_conflict=False)
    finding = BsFinding(id=4, task_id=1, subject_id=7, item_id="RC-A10", kind="completeness",
                        status="miss", message="示例核查结果")
    basis = configured_basis_lookup(as_of=date(2026, 11, 10))
    result = _finding_dto(finding, basis, {subject.id: subject})
    assert (result.get("basis") or {}).get("clause_id") == expected_clause


def test_conflicting_subject_type_does_not_select_one_authorization_basis():
    subject = BsSubject(id=7, task_id=1, stable_code="synthetic", subject_type="labor",
                        ai_subject_type="professional", type_confirmed=False, type_conflict=True)
    finding = BsFinding(id=4, task_id=1, subject_id=7, item_id="RC-A10", kind="completeness",
                        status="undecidable", message="主体类型存在冲突")
    basis = configured_basis_lookup(as_of=date(2026, 11, 10))
    result = _finding_dto(finding, basis, {subject.id: subject})
    assert result.get("basis") is None


def test_unconfirmed_subject_type_does_not_select_authorization_basis():
    subject = BsSubject(id=7, task_id=1, stable_code="synthetic", subject_type="labor",
                        type_confirmed=False, type_conflict=False)
    finding = BsFinding(id=4, task_id=1, subject_id=7, item_id="RC-A10", kind="completeness",
                        status="undecidable", message="主体类型未确认")
    result = _finding_dto(finding, configured_basis_lookup(as_of=date(2026, 11, 10)), {7: subject},
                          as_of=date(2026, 11, 10))
    assert result.get("basis") is None


def test_missing_authorization_basis_is_not_recreated_from_current_library():
    subject = BsSubject(id=7, task_id=1, stable_code="synthetic", subject_type="labor",
                        type_confirmed=True, type_conflict=False)
    finding = BsFinding(id=4, task_id=1, subject_id=7, item_id="RC-A10", kind="completeness",
                        status="miss", message="示例核查结果")
    result = _finding_dto(finding, {}, {7: subject}, as_of=date(2026, 11, 10))
    assert result.get("basis") is None


@pytest.mark.parametrize(("subject_type", "confirmed", "expected_clause"), [
    ("labor", True, "ZHB-2025-0620#二、10"),
    ("professional", True, "ZHB-2025-0620#一、10"),
    (None, False, None),
])
def test_current_opinion_without_snapshot_resolves_authorization_basis(db_session, subject_type, confirmed, expected_clause):
    task = BsTask(conversation_id=101, title="示例", created_by=1, created_by_type="tester",
                  scope_a3=True, baseline_date=date(2026, 11, 10))
    db_session.add(task)
    db_session.flush()
    subject = BsSubject(task_id=task.id, stable_code="example", subject_type=subject_type,
                        type_confirmed=confirmed, type_conflict=False)
    db_session.add(subject)
    db_session.flush()
    db_session.add(BsFinding(task_id=task.id, subject_id=subject.id, item_id="RC-A10",
                             kind="completeness", status="undecidable", message="授权材料待核实"))
    db_session.add(BsFinding(task_id=task.id, subject_id=subject.id, item_id="RC-A02",
                             kind="completeness", status="undecidable", message="营业执照待核实"))
    db_session.commit()
    result = reporting.build_opinion(db_session, task)
    entries = [entry for entry in result["basis"] if entry.get("item_id") == "RC-A10"]
    assert [entry["clause_id"] for entry in entries] == ([expected_clause] if expected_clause else [])
    entries = [entry for entry in result["basis"] if entry.get("item_id") == "RC-A02"]
    expected = f"ZHB-2025-0620#{'二' if subject_type == 'labor' else '一'}、2" if confirmed else None
    assert [entry["clause_id"] for entry in entries] == ([expected] if expected else [])
