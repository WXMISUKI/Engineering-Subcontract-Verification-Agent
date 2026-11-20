"""合同主体角色证据的纯读投影。

该模块不修改 BsSubject，也不把业主/总包伪装成专业或劳务分包主体。
它只从已解析合同页文本生成可回指的角色证据，供报告和主体抬头后续接线。
"""

from __future__ import annotations

import re

from .extract import (is_placeholder_party_name, load_extract_matchers,
                      norm_company, party_scoped_pages)

RESOLVER_VERSION = "contract-party.v2"
ROLE_EMPLOYER = "employer"
ROLE_GENERAL_CONTRACTOR = "general_contractor"
ROLE_SUBCONTRACTOR = "subcontractor"
PARTY_ROLES = (ROLE_EMPLOYER, ROLE_GENERAL_CONTRACTOR, ROLE_SUBCONTRACTOR)

_EMPLOYER_LABELS = {"甲方", "发包人", "发包单位", "建设单位"}
_GENERAL_LABELS = {"乙方", "承包人", "承包单位", "总承包人", "总承包单位"}
_SUBCONTRACTOR_LABELS = {"乙方", "承包人", "承包单位", "分包人", "分包单位", "分包单位名称"}


def _pages(material):
    yield from party_scoped_pages(material)


def _evidence(material, *, role, status, reason_code, label, company, page, quote,
              candidate=False, company_display=None):
    return {
        "role": role,
        "status": status,
        "reason_code": reason_code,
        "company": company,
        # 匹配用归一化名称；报告展示必须保留「（集团）」等原文，不能把法定名称截断。
        "company_display": company_display or company,
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
            company_display = match.group(2).strip()
            company = norm_company(company_display)
            if not company or is_placeholder_party_name(company):
                continue
            quote = match.group(0)
            if material_role == "general":
                if label in _EMPLOYER_LABELS:
                    output.append(_evidence(
                        material, role=ROLE_EMPLOYER, status="matched",
                        reason_code="explicit_party_role", label=label,
                        company=company, company_display=company_display,
                        page=page, quote=quote))
                elif label in _GENERAL_LABELS:
                    output.append(_evidence(
                        material, role=ROLE_GENERAL_CONTRACTOR, status="matched",
                        reason_code="explicit_party_role", label=label,
                        company=company, company_display=company_display,
                        page=page, quote=quote))
                elif label in _SUBCONTRACTOR_LABELS:
                    output.append(_evidence(
                        material, role=ROLE_SUBCONTRACTOR, status="matched",
                        reason_code="explicit_party_role", label=label,
                        company=company, company_display=company_display,
                        page=page, quote=quote))
            else:
                if label in _SUBCONTRACTOR_LABELS or label == "承包人":
                    output.append(_evidence(
                        material, role=ROLE_SUBCONTRACTOR, status="matched",
                        reason_code="explicit_party_role", label=label,
                        company=company, company_display=company_display,
                        page=page, quote=quote))
                elif label in _EMPLOYER_LABELS or label in _GENERAL_LABELS:
                    output.append(_evidence(
                        material, role=ROLE_GENERAL_CONTRACTOR, status="unresolved",
                        reason_code="contractor_candidate_requires_confirmation", label=label,
                        company=company, company_display=company_display,
                        page=page, quote=quote, candidate=True))
    return output


def _display_for(evidence: list[dict], company: str) -> str:
    for item in evidence:
        if item.get("company") == company:
            return item.get("company_display") or company
    return company


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
        company = next(iter(names))
        return {
            "role": role,
            "status": "matched",
            "company": company,
            "company_display": _display_for(explicit, company),
            "reason_code": "explicit_party_role",
            "evidence": evidence,
        }
    if len(names) == 1:
        company = next(iter(names))
        return {
            "role": role,
            "status": "unresolved",
            "company": None,
            "candidate_company": company,
            "candidate_company_display": _display_for(source, company),
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


def contract_chain_consistency(materials) -> dict:
    """比对总包合同的承包人与分包合同的发包人，检查合同链条是否闭合。

    只做事实对照，不生成违规结论：两侧都存在且不一致 → ``conflict``；
    任一侧缺证据 → ``unresolved``，由范围说明披露，不能当成“链条正常”。
    """
    general_side, sub_side = {}, {}
    for material in materials or []:
        role = getattr(material, "role", None)
        if role not in {"general", "sub", "labor"}:
            continue
        for item in extract_contract_party_evidence(material):
            if item["role"] != ROLE_GENERAL_CONTRACTOR:
                continue
            company = item.get("company")
            if not company:
                continue
            target = general_side if role == "general" else sub_side
            target.setdefault(norm_company(company), []).append(item)
    general_names = set(general_side)
    sub_names = set(sub_side)
    evidence = [item for rows in list(general_side.values()) + list(sub_side.values())
                for item in rows]
    if not general_names or not sub_names:
        return {"status": "unresolved", "company": None,
                "reason_code": "contract_chain_source_missing", "evidence": evidence}
    if len(general_names) == 1 and len(sub_names) == 1 and general_names == sub_names:
        company = next(iter(general_names))
        return {"status": "matched", "company": company,
                "company_display": _display_for(
                    [item for rows in general_side.values() for item in rows], company),
                "reason_code": "contract_chain_matched", "evidence": evidence}
    return {"status": "conflict", "company": None,
            "reason_code": "contract_chain_mismatch", "evidence": evidence,
            "general_contractors": sorted({_display_for(rows, name)
                                           for name, rows in general_side.items()}),
            "subcontract_employers": sorted({_display_for(rows, name)
                                             for name, rows in sub_side.items()})}


__all__ = [
    "PARTY_ROLES", "ROLE_EMPLOYER", "ROLE_GENERAL_CONTRACTOR",
    "ROLE_SUBCONTRACTOR", "RESOLVER_VERSION", "contract_chain_consistency",
    "extract_contract_party_evidence", "resolve_contract_party_evidence",
]
