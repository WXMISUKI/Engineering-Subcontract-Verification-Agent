"""负面清单内部候选规划；不调用模型，也不创建业务结论。"""
from __future__ import annotations

import hashlib
import json
import re

from .knowledge_runs import references_for_source
from .ai_evidence import validate_ai_proposal
from .models import BsFinding, STATUS_UNDECIDABLE
from .parsing import material_page_readable, material_reading_gap, reading_coverage
from .rules import negative_list_rules

SCHEMA_VERSION = "bs-negative-candidate-v2"
_SCOPE_HEADING = re.compile(r"工程范围|承包范围|分包范围|工作内容|施工内容|施工范围")
_SCOPE_BEFORE = 80
_SCOPE_AFTER = 1600
_CONTINUATION_CHARS = 1200


def _scope_excerpts(pages: list[dict]) -> list[dict]:
    """只提取合同范围线索及紧邻续页；不以清单触发词筛材料。"""
    by_key = {(page["material_id"], page["page"]): page for page in pages}
    ranges = {}
    for page in pages:
        key, text = (page["material_id"], page["page"]), page["text"]
        matches = list(_SCOPE_HEADING.finditer(text))
        if not matches:
            continue
        ranges.setdefault(key, []).extend((max(0, match.start() - _SCOPE_BEFORE),
                                          min(len(text), match.end() + _SCOPE_AFTER)) for match in matches)
        following = (key[0], key[1] + 1)
        if following in by_key:
            ranges.setdefault(following, []).append((0, min(len(by_key[following]["text"]), _CONTINUATION_CHARS)))
    return [{"material_id": material_id, "page": page_no,
             "text_start": min(start for start, _ in spans), "text_end": max(end for _, end in spans)}
            for (material_id, page_no), spans in sorted(ranges.items())]


def plan_negative_candidates(*, task_id: int, run: dict, subjects: list, materials: list,
                             findings: list, current_review_run_id: str,
                             current_material_fingerprint: str, current_rule_fingerprint: str) -> list[dict]:
    """只规划明确主体的条目；调用模型前仍须验证任务和参考授权。"""
    if (type(task_id) is not int or not isinstance(run, dict) or run.get("task_id") != task_id
            or run.get("group_id") != "a1" or run.get("state") != "completed"
            or any(not isinstance(run.get(key), str) or not run[key] for key in
                   ("run_id", "review_run_id", "material_fingerprint", "rule_fingerprint"))):
        raise ValueError("负面清单候选缺少当前核查代或输入指纹")
    if (run["review_run_id"] != current_review_run_id
            or run["material_fingerprint"] != current_material_fingerprint
            or run["rule_fingerprint"] != current_rule_fingerprint):
        raise ValueError("负面清单候选的核查代或输入已变化")
    active = [s for s in subjects if getattr(s, "task_id", None) == task_id
              and getattr(s, "retired", False) is False
              and getattr(s, "type_confirmed", False) is True
              and not getattr(s, "type_conflict", False)
              and getattr(s, "subject_type", None) in {"labor", "professional"}
              and type(getattr(s, "id", None)) is int]
    rules = [r for r in negative_list_rules() if r.get("kind") != "province_delegated"]
    sources = [source for source in (run.get("basis_sources") or [])
               if isinstance(source, dict) and source.get("kind") == "negative_list"]
    existing = {(f.subject_id, f.item_id) for f in findings
                if getattr(f, "task_id", None) == task_id and getattr(f, "kind", None) == "negative_list"
                and type(getattr(f, "subject_id", None)) is int}
    output = []
    for subject in sorted(active, key=lambda row: row.id):
        contracts = sorted((m for m in materials if getattr(m, "task_id", None) == task_id
                            and getattr(m, "subject_id", None) == subject.id
                            and getattr(m, "role", None) in {"sub", "labor"}
                            and type(getattr(m, "id", None)) is int), key=lambda row: row.id)
        pages = [{"material_id": m.id, "page": p["page"], "text": p["text"]} for m in contracts
                 if getattr(m, "parse_state", None) == "done"
                 for p in (getattr(m, "pages_json", None) or [])
                 if isinstance(p, dict) and type(p.get("page")) is int and p["page"] > 0
                 and isinstance(p.get("text"), str) and p["text"].strip()
                 and material_page_readable(m, p["page"])]
        scope_pages = _scope_excerpts(pages)
        original = {(page["material_id"], page["page"]): page for page in pages}
        incomplete = (any(material_reading_gap(m) is not None or not reading_coverage(m)["reading_complete"]
                          for m in contracts)
                      or len(scope_pages) != len(pages)
                      or any(loc["text_start"] != 0 or loc["text_end"] != len(original[(loc["material_id"], loc["page"])]["text"])
                             for loc in scope_pages))
        for rule in rules:
            item_id = rule["id"]
            if (subject.id, item_id) in existing:
                continue
            matching = [source for source in sources if source.get("item_id") == item_id
                        and ("subject_ids" not in source or subject.id in source["subject_ids"])]
            references = references_for_source(run, matching[0]) if len(matching) == 1 else []
            references = [ref for ref in references if isinstance(ref.get("reference_id"), str)
                          and ref["reference_id"] and isinstance(ref.get("quote"), str) and ref["quote"].strip()]
            duplicate_reference_ids = len({ref["reference_id"] for ref in references}) != len(references)
            reason = ("contract_missing" if not contracts else "contract_unreadable" if not pages
                      else "contract_scope_unlocated" if not scope_pages
                      else "source_ambiguous" if len(matching) > 1 or duplicate_reference_ids
                      else "reference_missing" if not references
                      else None)
            identity = {"schema": SCHEMA_VERSION, "task_id": task_id, "run_id": run["run_id"],
                        "review_run_id": run["review_run_id"], "material_fingerprint": run["material_fingerprint"],
                        "rule_fingerprint": run["rule_fingerprint"], "subject_id": subject.id,
                        "item_id": item_id, "contract_ids": [m.id for m in contracts],
                        "reference_ids": sorted({ref["reference_id"] for ref in references})}
            payload = json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            output.append({**identity, "candidate_id": "nc-" + hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                           "state": "not_run", "unavailable_reason": reason,
                           "incomplete_material_scope": incomplete, "material_pages": scope_pages})
    return output


def prepare_candidate_batch_inputs(*, task_id: int, run: dict, subjects: list, materials: list,
                                   findings: list, current_review_run_id: str,
                                   current_material_fingerprint: str, current_rule_fingerprint: str) -> dict:
    """从可信当前快照重新规划并组装本主体合同原页及对应参考，不接收客户端正文。"""
    candidates = plan_negative_candidates(
        task_id=task_id, run=run, subjects=subjects, materials=materials, findings=findings,
        current_review_run_id=current_review_run_id, current_material_fingerprint=current_material_fingerprint,
        current_rule_fingerprint=current_rule_fingerprint)
    by_material = {m.id: m for m in materials if getattr(m, "task_id", None) == task_id}
    rule_labels = {rule["id"]: rule.get("text") or rule["id"] for rule in negative_list_rules()}
    items = []
    for candidate in candidates:
        if candidate["unavailable_reason"]:
            continue
        pages = []
        for loc in candidate["material_pages"]:
            material = by_material.get(loc["material_id"])
            if (material is None or getattr(material, "subject_id", None) != candidate["subject_id"]
                    or not material_page_readable(material, loc["page"])):
                raise ValueError("候选材料主体或读取状态已变化")
            matches = [p for p in (material.pages_json or []) if isinstance(p, dict) and p.get("page") == loc["page"]]
            if len(matches) != 1 or not isinstance(matches[0].get("text"), str) or not matches[0]["text"].strip():
                raise ValueError("候选材料页不唯一或无正文")
            text = matches[0]["text"]
            start, end = loc["text_start"], loc["text_end"]
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
                raise ValueError("候选材料片段范围无效")
            pages.append({"task_id": task_id, "subject_id": candidate["subject_id"],
                          "material_id": material.id, "page": loc["page"], "text": text[start:end],
                          "source_start": start, "source_end": end})
        sources = [source for source in run.get("basis_sources") or [] if isinstance(source, dict)
                   and source.get("kind") == "negative_list" and source.get("item_id") == candidate["item_id"]
                   and ("subject_ids" not in source or candidate["subject_id"] in source["subject_ids"])]
        if len(sources) != 1:
            raise ValueError("候选参考来源已变化")
        matches = [ref for ref in references_for_source(run, sources[0])
                   if ref.get("reference_id") in candidate["reference_ids"]]
        if (len(matches) != len(candidate["reference_ids"])
                or {ref.get("reference_id") for ref in matches} != set(candidate["reference_ids"])):
            raise ValueError("候选参考不唯一或已变化")
        references = [{"reference_id": ref["reference_id"], "text": ref["quote"]} for ref in matches]
        limitations = (["本次只选取合同范围相关片段及续页，其他内容未纳入，不能据此认定无问题"]
                       if candidate["incomplete_material_scope"] else [])
        items.append({"candidate_id": candidate["candidate_id"], "task_id": task_id,
                      "item_id": candidate["item_id"], "item_label": rule_labels[candidate["item_id"]],
                      "subject_id": candidate["subject_id"], "materials": pages, "references": references,
                      "incomplete_material_scope": candidate["incomplete_material_scope"], "limitations": limitations})
    return {"candidates": candidates, "items": items}


def reviewable_candidate_proposals(prepared: dict, result: dict) -> list[dict]:
    """在写入复核流之前，以本代输入再次验证候选身份及双侧证据。"""
    items = prepared.get("items") or []
    candidates = result.get("candidates") or []
    if not isinstance(items, list) or not isinstance(candidates, list):
        raise ValueError("候选结果格式无效")
    ids = [item.get("candidate_id") for item in items if isinstance(item, dict)]
    returned = [row.get("candidate_id") for row in candidates if isinstance(row, dict)]
    if (len(ids) != len(items) or any(not isinstance(key, str) or not key for key in ids)
            or len(ids) != len(set(ids)) or len(returned) != len(candidates)
            or any(not isinstance(key, str) or not key for key in returned)
            or len(returned) != len(set(returned))):
        raise ValueError("候选身份不唯一")
    indexed = {item["candidate_id"]: item for item in items}
    output = []
    for row in candidates:
        item = indexed.get(row["candidate_id"])
        if item is None or row.get("execution_status") not in {"completed", "partial"}:
            continue
        if row.get("execution_status") == "partial" and not row.get("reviewable_partial"):
            continue
        proposals = row.get("proposals")
        if not isinstance(proposals, list):
            continue
        for proposal in proposals:
            validation = validate_ai_proposal(proposal, task_id=item["task_id"],
                subject_id=item["subject_id"], item_id=item["item_id"],
                materials=item["materials"], references=item["references"],
                incomplete_material_scope=item.get("incomplete_material_scope", False))
            if validation.accepted:
                output.append({"candidate_id": row["candidate_id"], "proposal": validation.proposal,
                               "limitations": list(dict.fromkeys(
                                   text for text in [*(item.get("limitations") or []), *(row.get("limitations") or [])]
                                   if isinstance(text, str)))})
    return output


def make_candidate_review_findings(*, task_id: int, reviewable: list[dict]) -> list[tuple[str, BsFinding, list[dict]]]:
    """为已通过二次校验的建议构建待复核记录；不分配 ID、不持久化。"""
    if type(task_id) is not int or task_id < 1 or not isinstance(reviewable, list):
        raise ValueError("候选复核输入无效")
    output, seen = [], set()
    for row in reviewable:
        proposal = row.get("proposal") if isinstance(row, dict) else None
        candidate_id = row.get("candidate_id") if isinstance(row, dict) else None
        limitations = row.get("limitations", []) if isinstance(row, dict) else None
        if (not isinstance(candidate_id, str) or not candidate_id.startswith("nc-")
                or not isinstance(proposal, dict) or proposal.get("advisory_only") is not True
                or not isinstance(limitations, list) or any(not isinstance(text, str) for text in limitations)
                or proposal.get("status") not in {"hit", "undecidable"}
                or type(proposal.get("subject_id")) is not int or proposal["subject_id"] < 1
                or not isinstance(proposal.get("item_id"), str) or not proposal["item_id"]
                or not isinstance(proposal.get("message"), str) or not proposal["message"].strip()):
            raise ValueError("候选建议未经校验或身份无效")
        material_refs, rule_refs = proposal.get("material_evidence"), proposal.get("rule_evidence")
        if (not isinstance(material_refs, list) or not material_refs or not isinstance(rule_refs, list) or not rule_refs
                or not all(isinstance(ref, dict) and type(ref.get("material_id")) is int and ref["material_id"] > 0
                    and type(ref.get("page")) is int and ref["page"] > 0
                    and isinstance(ref.get("quote"), str) and ref["quote"].strip() for ref in material_refs)
                or not all(isinstance(ref, dict) and isinstance(ref.get("reference_id"), str)
                    and ref["reference_id"] and isinstance(ref.get("quote"), str) and ref["quote"].strip()
                    for ref in rule_refs)):
            raise ValueError("候选建议缺少有效原文")
        identity = json.dumps({"candidate_id": candidate_id, "proposal": proposal, "limitations": limitations}, ensure_ascii=False,
                              sort_keys=True, separators=(",", ":"))
        key = "ncf-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
        if key in seen:
            raise ValueError("候选建议重复")
        seen.add(key)
        first = material_refs[0]
        limitation_text = "；" + "；".join(dict.fromkeys(text for text in limitations if text.strip())) if limitations else ""
        finding = BsFinding(task_id=task_id, subject_id=proposal["subject_id"], item_id=proposal["item_id"],
            kind="negative_candidate", status=STATUS_UNDECIDABLE,
            message=("模型辅助核查线索，请人工核实：" + proposal["message"].strip() + limitation_text)[:500],
            material_id=first["material_id"], page=first["page"], quote=first["quote"],
            source="负面清单辅助核查", evidence_json=[dict(ref) for ref in material_refs])
        output.append((key, finding, [dict(ref) for ref in rule_refs]))
    return output
