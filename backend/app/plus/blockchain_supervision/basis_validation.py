"""依据引用校验（BASIS-01）：文档角色、强度、主体范围与条款原文一致性。

两处闸门复用同一函数：

1. 规则加载期：``validate_registry()`` / ``validate_review_flow()`` 只报问题，
   由测试和启动自检调用；
2. finding 落库前：``assert_binding()`` 校验 ``item_id ↔ clause_id ↔ 条款原文
   ↔ 主体范围`` 四者一致，主体错位或条款错配直接抛 ``BasisBindingError``。

本模块只读 ``data/`` 下的 JSON，不做缓存，改数据后重新校验即生效。
"""
from __future__ import annotations

import json
import re
from datetime import date
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).with_name("data")

ROLE_FULL_TEXT = "full_text"
ROLE_NOTICE_ONLY = "notice_only"
ROLE_ATTACHMENT_NOTICE = "attachment_notice"
ROLES = {ROLE_FULL_TEXT, ROLE_NOTICE_ONLY, ROLE_ATTACHMENT_NOTICE}

STRENGTHS = {"A", "B", "C", "D"}
CONTENT_FORMATS = {"scanned_pdf", "text_layer", "docx_text", "pdf", "markdown"}
CITATION_MODES = {"pdf_page", "clause"}

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"


class BasisBindingError(ValueError):
    """规则或 finding 引用了不可绑定的依据。"""

    def __init__(self, code: str, message: str, ref: str | None = None):
        super().__init__(message)
        self.code = code
        self.ref = ref
        self.message = message


def _load(name: str) -> dict:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def load_review_basis() -> dict:
    return _load("review_basis.json")


def load_basis_library() -> dict:
    return _load("basis_clauses.json")


def load_basis_index() -> dict:
    return _load("basis_index.json")


def _normalize(text: str | None) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def clause_texts(library: dict) -> dict[str, dict]:
    """展开条款与子项，返回 clause_id → 节点。"""
    result: dict[str, dict] = {}

    def walk(node: dict) -> None:
        result[node["clause_id"]] = node
        for child in node.get("children") or []:
            walk(child)

    for node in library.get("clauses") or []:
        walk(node)
    return result


def _issue(severity: str, code: str, ref: str, message: str) -> dict:
    return {"severity": severity, "code": code, "ref": ref, "message": message}


def _document_binding_issue(doc: dict, entry: dict, basis: dict) -> str | None:
    """返回不可绑定的原因码；可绑定返回 None。"""
    role = doc.get("document_role")
    if role not in ROLES:
        return "basis_doc_role_invalid"
    if role == ROLE_NOTICE_ONLY:
        return "basis_role_not_binding"
    if role == ROLE_ATTACHMENT_NOTICE:
        parent_id = doc.get("parent_doc_id")
        parent = (basis.get("documents") or {}).get(parent_id)
        if not parent:
            return "basis_parent_missing"
        if parent.get("document_role") != ROLE_FULL_TEXT:
            return "basis_parent_not_full_text"
        if entry.get("requirement_kind", "rule") != "material_requirement":
            return "basis_attachment_not_rule_basis"
    return None


def validate_registry(basis: dict | None = None, library: dict | None = None) -> list[dict]:
    """校验登记表自身：角色、格式、引用模式、条款存在与原文一致。"""
    basis = basis if basis is not None else load_review_basis()
    library = library if library is not None else load_basis_library()
    texts = clause_texts(library)
    issues: list[dict] = []
    documents = basis.get("documents") or {}

    for doc_id, doc in documents.items():
        role = doc.get("document_role")
        if role not in ROLES:
            issues.append(_issue(SEVERITY_ERROR, "basis_doc_role_invalid", doc_id,
                                 "文档角色不在 full_text/notice_only/attachment_notice 内"))
        if doc.get("strength") not in STRENGTHS:
            issues.append(_issue(SEVERITY_ERROR, "basis_strength_invalid", doc_id,
                                 "依据强度必须是 A/B/C/D"))
        fmt = doc.get("content_format")
        mode = doc.get("citation_mode")
        if fmt not in CONTENT_FORMATS:
            issues.append(_issue(SEVERITY_ERROR, "basis_content_format_invalid", doc_id,
                                 "内容形态不在允许范围"))
        if mode not in CITATION_MODES:
            issues.append(_issue(SEVERITY_ERROR, "basis_citation_mode_invalid", doc_id,
                                 "引用模式必须是 pdf_page 或 clause"))
        if fmt == "scanned_pdf" and not doc.get("ocr_companion"):
            issues.append(_issue(SEVERITY_ERROR, "basis_ocr_companion_missing", doc_id,
                                 "扫描件必须登记 OCR 伴随文本"))
        if mode == "pdf_page" and not doc.get("ocr_companion"):
            issues.append(_issue(SEVERITY_ERROR, "basis_ocr_companion_missing", doc_id,
                                 "按页引用必须同时具备 OCR 伴随文本"))
        if role == ROLE_ATTACHMENT_NOTICE:
            parent = documents.get(doc.get("parent_doc_id"))
            if not parent:
                issues.append(_issue(SEVERITY_ERROR, "basis_parent_missing", doc_id,
                                     "附件未登记全文父文档"))
            elif parent.get("document_role") != ROLE_FULL_TEXT:
                issues.append(_issue(SEVERITY_ERROR, "basis_parent_not_full_text", doc_id,
                                     "附件父文档必须是全文"))
        if doc.get("human_verified") is False:
            issues.append(_issue(SEVERITY_WARNING, "basis_unverified_text", doc_id,
                                 "OCR/人工尚未核对，引用时需保留核验提示"))

    for clause_id, entry in (basis.get("clauses") or {}).items():
        node = texts.get(clause_id)
        if node is None:
            issues.append(_issue(SEVERITY_ERROR, "basis_clause_missing", clause_id,
                                 "条款坐标在 basis_clauses.json 中不存在"))
            continue
        if entry.get("doc_id") != node.get("doc_id") and entry.get("doc_id") != _doc_of(node, texts):
            issues.append(_issue(SEVERITY_ERROR, "basis_doc_mismatch", clause_id,
                                 "条款归属文档与登记文档不一致"))
        if entry.get("strength") not in STRENGTHS:
            issues.append(_issue(SEVERITY_ERROR, "basis_strength_invalid", clause_id,
                                 "条款强度必须是 A/B/C/D"))
        quote = entry.get("quote")
        if quote and _normalize(quote) not in _normalize(node.get("text") or node.get("title")):
            issues.append(_issue(SEVERITY_ERROR, "basis_quote_mismatch", clause_id,
                                 "登记引文与条款原文不一致"))
        doc = documents.get(entry.get("doc_id")) or {}
        reason = _document_binding_issue(doc, entry, basis)
        if reason:
            issues.append(_issue(SEVERITY_ERROR, reason, clause_id,
                                 "该文档角色不能作为判定依据"))

    for external in basis.get("external_documents") or []:
        if not external.get("knowledge_id"):
            issues.append(_issue(SEVERITY_ERROR, "basis_external_unregistered",
                                 external.get("basis_id"), "外部依据未登记知识库文档"))
        if external.get("quote_status") != "verified" and not external.get("quote"):
            issues.append(_issue(SEVERITY_WARNING, "basis_external_unverified",
                                 external.get("basis_id"), "外部依据尚未取得并核验原文"))
        if external.get("strength") not in STRENGTHS:
            issues.append(_issue(SEVERITY_ERROR, "basis_strength_invalid",
                                 external.get("basis_id"), "依据强度必须是 A/B/C/D"))
    return issues


def _doc_of(node: dict, texts: dict[str, dict]) -> str | None:
    if node.get("doc_id"):
        return node["doc_id"]
    parent = texts.get(node.get("parent") or "")
    return parent.get("doc_id") if parent else None


def validate_basis_index(basis: dict | None = None,
                         index: dict | None = None,
                         library: dict | None = None) -> list[dict]:
    """校验每个条目映射到的条款存在、可绑定，且主体范围与条目主体一致。"""
    basis = basis if basis is not None else load_review_basis()
    index = index if index is not None else load_basis_index()
    library = library if library is not None else load_basis_library()
    texts = clause_texts(library)
    issues: list[dict] = []
    rule_subjects = basis.get("rule_subjects") or {}
    for item_id, row in (index.get("items") or {}).items():
        clause_id = row.get("clause_id")
        node = texts.get(clause_id)
        if node is None:
            issues.append(_issue(SEVERITY_ERROR, "basis_clause_missing", item_id,
                                 "条目映射的条款坐标不存在：%s" % clause_id))
            continue
        entry = (basis.get("clauses") or {}).get(clause_id)
        if entry is None:
            issues.append(_issue(SEVERITY_ERROR, "basis_clause_unregistered", item_id,
                                 "条目映射的条款未在登记表中治理：%s" % clause_id))
            continue
        doc = (basis.get("documents") or {}).get(entry.get("doc_id")) or {}
        reason = _document_binding_issue(doc, entry, basis)
        if reason:
            issues.append(_issue(SEVERITY_ERROR, reason, item_id,
                                 "条目映射到不可绑定文档：%s" % clause_id))
            continue
        subject = row.get("subject") or rule_subjects.get(item_id)
        scope = entry.get("subject_scope") or []
        if subject and subject not in scope:
            issues.append(_issue(SEVERITY_ERROR, "basis_subject_mismatch", item_id,
                                 "条目主体 %s 不在条款主体范围 %s 内" % (subject, scope)))
    return issues


def assert_binding(item_id: str, clause_id: str | None = None,
                   subject: str | None = None,
                   as_of: date | str | None = None,
                   basis: dict | None = None,
                   library: dict | None = None,
                   index: dict | None = None) -> dict:
    """finding 落库前的强校验；通过返回命中的条款登记项。

    主体错位、条款错配、引文不一致、文档角色不可绑定、依据失效均直接拒绝。
    """
    basis = basis if basis is not None else load_review_basis()
    library = library if library is not None else load_basis_library()
    index = index if index is not None else load_basis_index()
    rows = index.get("items") or {}
    if item_id not in rows:
        raise BasisBindingError("basis_item_unmapped", "规则条目未登记依据：%s" % item_id, item_id)
    mapped = rows[item_id].get("clause_id")
    if clause_id and clause_id != mapped:
        raise BasisBindingError(
            "basis_item_clause_mismatch",
            "条目 %s 登记依据为 %s，与 finding 引用 %s 不一致" % (item_id, mapped, clause_id),
            item_id,
        )
    clause_id = mapped
    texts = clause_texts(library)
    node = texts.get(clause_id)
    if node is None:
        raise BasisBindingError("basis_clause_missing", "条款坐标不存在：%s" % clause_id, clause_id)
    entry = (basis.get("clauses") or {}).get(clause_id)
    if entry is None:
        raise BasisBindingError("basis_clause_unregistered",
                                "条款未在登记表中治理：%s" % clause_id, clause_id)
    doc = (basis.get("documents") or {}).get(entry.get("doc_id")) or {}
    reason = _document_binding_issue(doc, entry, basis)
    if reason:
        raise BasisBindingError(reason, "文档不能作为判定依据：%s" % clause_id, clause_id)
    subject = subject or rows[item_id].get("subject") or (basis.get("rule_subjects") or {}).get(item_id)
    scope = entry.get("subject_scope") or []
    if subject and subject not in scope:
        raise BasisBindingError(
            "basis_subject_mismatch",
            "条目 %s 的主体 %s 不在条款 %s 的主体范围 %s 内" % (item_id, subject, clause_id, scope),
            item_id,
        )
    if as_of:
        moment = as_of if isinstance(as_of, date) else date.fromisoformat(str(as_of))
        start = doc.get("effective_from")
        end = doc.get("effective_to")
        if start and moment < date.fromisoformat(start):
            raise BasisBindingError("basis_not_in_effect", "依据尚未生效：%s" % clause_id, clause_id)
        if end and moment > date.fromisoformat(end):
            raise BasisBindingError("basis_not_in_effect", "依据已失效：%s" % clause_id, clause_id)
    return dict(entry, item_id=item_id, subject=subject, clause_id=clause_id,
                quote_source="basis_clauses")


def validate_review_flow(flow: dict | None = None,
                         basis: dict | None = None,
                         library: dict | None = None) -> list[dict]:
    """校验 review_flow 的 basis 引用：坐标存在、文档可绑定、附件有全文父文档。"""
    flow = flow if flow is not None else _load("review_flow.json")
    basis = basis if basis is not None else load_review_basis()
    library = library if library is not None else load_basis_library()
    texts = clause_texts(library)
    issues: list[dict] = []
    for step in flow.get("steps") or []:
        for ref in step.get("basis") or []:
            clause_id = ref.get("clause_id")
            node = texts.get(clause_id)
            if node is None:
                issues.append(_issue(SEVERITY_ERROR, "basis_flow_clause_missing",
                                     step.get("id"), "步骤引用了不存在的条款：%s" % clause_id))
                continue
            registered = (basis.get("clauses") or {}).get(clause_id)
            if registered is None:
                issues.append(_issue(SEVERITY_ERROR, "basis_clause_unregistered",
                                     step.get("id"), "步骤引用的条款未治理：%s" % clause_id))
                continue
            doc = (basis.get("documents") or {}).get(registered.get("doc_id")) or {}
            kind = ref.get("basis_kind") or "rule"
            entry = dict(registered, requirement_kind="material_requirement" if kind == "material_requirement" else "rule")
            reason = _document_binding_issue(doc, entry, basis)
            if reason:
                issues.append(_issue(SEVERITY_ERROR, reason, step.get("id"),
                                     "步骤依据不可绑定：%s" % clause_id))
    return issues


def validate_attachment_pairs(pairs: dict[str, str] | None = None,
                              basis: dict | None = None) -> list[dict]:
    """校验附件短码映射到真实父文档（如 10a→10、12a→12）。"""
    basis = basis if basis is not None else load_review_basis()
    documents = basis.get("documents") or {}
    known = set(documents) | {row.get("basis_id") for row in basis.get("external_documents") or []}
    issues: list[dict] = []
    for attachment, parent in (pairs if pairs is not None else basis.get("attachment_pairs") or {}).items():
        if parent not in known:
            issues.append(_issue(SEVERITY_ERROR, "basis_attachment_parent_missing",
                                 attachment, "附件父文档未登记：%s" % parent))
        elif parent == attachment:
            issues.append(_issue(SEVERITY_ERROR, "basis_attachment_parent_self",
                                 attachment, "附件不能以自身为父文档"))
    return issues


_BASIS_FILE_NAMES = ("review_basis.json", "basis_clauses.json", "basis_index.json", "review_flow.json")


def _basis_stamp() -> tuple:
    """以 mtime+size 作为缓存键：数据未变不重复解析，改数据立即重新校验。"""
    return tuple(
        (name, (DATA_DIR / name).stat().st_mtime_ns, (DATA_DIR / name).stat().st_size)
        for name in _BASIS_FILE_NAMES
    )


@lru_cache(maxsize=8)
def _validate_basis_files(stamp: tuple) -> None:
    problems = (validate_registry() + validate_basis_index()
                + validate_review_flow() + validate_attachment_pairs())
    errors = [issue for issue in problems if issue["severity"] == SEVERITY_ERROR]
    if errors:
        first = errors[0]
        raise BasisBindingError(first["code"], "依据登记表校验失败：%s" % first["message"],
                                first["ref"])


def ruleset_gate() -> None:
    """规则加载期闸门（第一道）：登记表、索引、流程或附件映射任一错配即拒绝加载。"""
    _validate_basis_files(_basis_stamp())


def validate_findings(findings, *, basis: dict | None = None, index: dict | None = None,
                      library: dict | None = None) -> list[dict]:
    """finding 落库期闸门（第二道）：逐条校验 item_id 能否绑定到已治理条款。

    只强校验 ``basis_index.json`` 已登记的条目；未登记条目（派生/历史 ID）不阻断，
    避免把「尚未治理」误判成「引用错配」。
    """
    basis = basis if basis is not None else load_review_basis()
    index = index if index is not None else load_basis_index()
    library = library if library is not None else load_basis_library()
    items = index.get("items") or {}
    issues: list[dict] = []
    for finding in findings:
        item_id = getattr(finding, "item_id", None)
        if item_id not in items:
            continue
        try:
            assert_binding(item_id, basis=basis, index=index, library=library)
        except BasisBindingError as exc:
            issues.append(_issue(SEVERITY_ERROR, exc.code, exc.ref or item_id, exc.message))
    return issues


def gate_findings(findings, *, basis: dict | None = None, index: dict | None = None,
                  library: dict | None = None) -> None:
    """结论落库前调用；任一条依据错配即抛出，由调用方事务回滚。"""
    issues = validate_findings(findings, basis=basis, index=index, library=library)
    if issues:
        first = issues[0]
        raise BasisBindingError(first["code"], "结论依据校验失败：%s" % first["message"],
                                first["ref"])
