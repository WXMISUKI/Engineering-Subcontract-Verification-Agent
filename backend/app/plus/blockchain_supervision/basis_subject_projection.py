"""依据展示的主体类型投影，不更改核查规则或已冻结快照。"""

from .rules import _basis_view, load_basis_clauses


SHARED_ATTACHMENT_IDS = frozenset(f"RC-A{number:02d}" for number in range(1, 11))


def subject_basis_view(item_id: str, basis_map: dict, subject, *, as_of=None) -> dict | None:
    basis = (basis_map or {}).get(item_id)
    if item_id not in SHARED_ATTACHMENT_IDS or basis is None:
        return basis
    if basis.get("doc_id") != "ZHB-2025-0620":
        return None
    number = int(item_id[-2:])
    if basis.get("clause_id") not in {f"ZHB-2025-0620#一、{number}", f"ZHB-2025-0620#二、{number}"}:
        return None
    if not subject or not subject.type_confirmed or subject.type_conflict:
        return None
    side = {"professional": "一", "labor": "二"}.get(subject.subject_type)
    if side is None:
        return None
    clause_id = f"ZHB-2025-0620#{side}、{number}"
    if basis.get("clause_id") == clause_id:
        return basis
    library = load_basis_clauses(as_of=as_of)
    docs = {d["doc_id"]: d for d in library.get("docs") or []}
    for parent in library.get("clauses") or []:
        for child in parent.get("children") or []:
            if child.get("clause_id") == clause_id:
                doc = docs.get(parent.get("doc_id"))
                return _basis_view(child, doc, parent) if doc else None
    return None
