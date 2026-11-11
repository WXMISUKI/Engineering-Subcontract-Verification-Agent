"""合同主体角色证据的纯读投影。

该模块不修改 BsSubject，也不把业主/总包伪装成专业或劳务分包主体。
它只从已解析合同页文本生成可回指的角色证据，供报告和主体抬头后续接线。
"""

from __future__ import annotations

import re

from .extract import load_extract_matchers, norm_company

RESOLVER_VERSION = "contract-party.v1"
ROLE_EMPLOYER = "employer"
ROLE_GENERAL_CONTRACTOR = "general_contractor"
ROLE_SUBCONTRACTOR = "subcontractor"
PARTY_ROLES = (ROLE_EMPLOYER, ROLE_GENERAL_CONTRACTOR, ROLE_SUBCONTRACTOR)

_EMPLOYER_LABELS = {"甲方", "发包人", "建设单位"}
_GENERAL_LABELS = {"乙方", "承包人", "总承包人"}
_SUBCONTRACTOR_LABELS = {"乙方", "分包人", "分包单位"}


def _pages(material):
    for page in getattr(material, "pages_json", None) or []:
        if not isinstance(page, dict):
            continue
        text = str(page.get("text") or "")
        if text.strip() and isinstance(page.get("page"), int) and page["page"] > 0:
            yield page["page"], text


def _evidence(material, *, role, status, reason_code, label, company, page, quote,
              candidate=False):
    return {
        "role": role,
        "status": status,
        "reason_code": reason_code,
        "company": company,
        "candidate": candidate,
        "material_id": getattr(material, "id", None),
        "generation_id": getattr(material, "generation_id", None),
        "page": page,
        "quote": quote[:500],
        "source_kind": "contract_text",
        "role_label": label,
        "resolver_version": RESOLVER_VERSION,
    }


def extract_contract_party_evidence(material) -> list[dict]:
    """Extract role evidence from a general/sub/labor contract.

    A sub/labor contract's party A is only a general-contractor candidate and
    remains unresolved until an explicit project role is confirmed.
    """
    material_role = getattr(material, "role", None)
    if material_role not in {"general", "sub", "labor"}:
        return []
    pattern = re.compile(load_extract_matchers()["party_name_regex"])
    output = []
    for page, text in _pages(material):
        for match in pattern.finditer(text):
            label = match.group(1)
            company = norm_company(match.group(2))
            if not company:
                continue
            quote = match.group(0)
            if material_role == "general":
                if label in _EMPLOYER_LABELS:
                    output.append(_evidence(
                        material, role=ROLE_EMPLOYER, status="matched",
                        reason_code="explicit_party_role", label=label,
                        company=company, page=page, quote=quote))
                elif label in _GENERAL_LABELS:
                    output.append(_evidence(
                        material, role=ROLE_GENERAL_CONTRACTOR, status="matched",
                        reason_code="explicit_party_role", label=label,
                        company=company, page=page, quote=quote))
                elif label in _SUBCONTRACTOR_LABELS:
                    output.append(_evidence(
                        material, role=ROLE_SUBCONTRACTOR, status="matched",
                        reason_code="explicit_party_role", label=label,
                        company=company, page=page, quote=quote))
            else:
                if label in _SUBCONTRACTOR_LABELS:
                    output.append(_evidence(
                        material, role=ROLE_SUBCONTRACTOR, status="matched",
                        reason_code="explicit_party_role", label=label,
                        company=company, page=page, quote=quote))
                elif label in _EMPLOYER_LABELS or label in _GENERAL_LABELS:
                    output.append(_evidence(
                        material, role=ROLE_GENERAL_CONTRACTOR, status="unresolved",
                        reason_code="contractor_candidate_requires_confirmation", label=label,
                        company=company, page=page, quote=quote, candidate=True))
    return output


def _resolve_role(role: str, evidence: list[dict]) -> dict:
    explicit = [item for item in evidence if item["status"] == "matched"]
    source = explicit or evidence
    names = {item["company"] for item in source if item.get("company")}
    if len(names) > 1:
        return {
            "role": role,
            "status": "conflict",
            "company": None,
            "reason_code": "party_conflict",
            "evidence": evidence,
        }
    if len(names) == 1 and explicit:
        return {
            "role": role,
            "status": "matched",
            "company": next(iter(names)),
            "reason_code": "explicit_party_role",
            "evidence": evidence,
        }
    if len(names) == 1:
        return {
            "role": role,
            "status": "unresolved",
            "company": None,
            "candidate_company": next(iter(names)),
            "reason_code": "contractor_candidate_requires_confirmation",
            "evidence": evidence,
        }
    return {
        "role": role,
        "status": "unresolved",
        "company": None,
        "reason_code": "party_role_missing",
        "evidence": evidence,
    }


def resolve_contract_party_evidence(materials) -> dict[str, dict]:
    """Resolve all contract role evidence without guessing missing roles."""
    grouped = {role: [] for role in PARTY_ROLES}
    for material in materials or []:
        for item in extract_contract_party_evidence(material):
            grouped[item["role"]].append(item)
    return {role: _resolve_role(role, grouped[role]) for role in PARTY_ROLES}


__all__ = [
    "PARTY_ROLES", "ROLE_EMPLOYER", "ROLE_GENERAL_CONTRACTOR",
    "ROLE_SUBCONTRACTOR", "RESOLVER_VERSION", "extract_contract_party_evidence",
    "resolve_contract_party_evidence",
]
