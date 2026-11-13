"""将已授权的当前核查引用与任务材料投影到模型输入。

调用方负责在模型调用前后校验任务、材料版本、运行代次和当前授权，并只传入有效材料。
本模块不写数据库；引用可核验不代表业务推理正确。
"""
from types import SimpleNamespace

from .ai_review_execution import review_item
from .ai_evidence import contains_quote
from .ai_scope_mappings import rule_scope_mapping
from .ai_scope_selection import fallback_scope, select_scope
from .attachment_source import is_attachment_source
from .internal_records import is_internal_record_candidate
from .parsing import material_reading_gap, reading_coverage, reviewed_readable_pages
from .personnel_evidence import extract_management_roster
from .reporting import item_label
from .rules import load_report_checklist, load_transfer_rules
from .signature_chain import check_approval_timing, packet_groups


def _review_item_label(item_id):
    label = item_label(item_id)
    if label != item_id:
        return label
    return next((row.get("title") for row in load_transfer_rules().get("rules", [])
                 if row.get("id") == item_id and row.get("title")), item_id)


_TASK_LEVEL_KINDS = {"transfer_scope", "illegal_subcontract", "negative_list"}


def scope_task_level_materials(finding, materials):
    """按 finding 已登记的材料锚点裁剪任务级模型上下文。

    任务级规则原本会把所有可读页交给每个 AI finding，扫描材料稍大时会
    形成多个 16K 分批请求。只有存在明确材料/页锚点时才裁剪，并把裁剪
    标记交给上层，避免模型把局部材料误当成完整范围；没有锚点则保持
    原有全量 fail-closed 行为。
    """
    rows = list(materials or [])
    if (getattr(finding, "subject_id", None) is not None
            or getattr(finding, "kind", None) not in _TASK_LEVEL_KINDS):
        return rows, False

    material_ids = set()
    page_anchors = {}

    def add_anchor(material_id, page):
        if type(material_id) is not int:
            return
        material_ids.add(material_id)
        if type(page) is int and page > 0:
            page_anchors.setdefault(material_id, set()).update({page - 1, page, page + 1})

    add_anchor(getattr(finding, "material_id", None), getattr(finding, "page", None))
    for evidence in getattr(finding, "evidence_json", None) or []:
        if isinstance(evidence, dict):
            add_anchor(evidence.get("material_id"), evidence.get("page"))
    if not material_ids:
        return rows, False

    selected = []
    for row in rows:
        material_id = row.get("material_id") if isinstance(row, dict) else None
        page = row.get("page") if isinstance(row, dict) else None
        if material_id not in material_ids:
            continue
        allowed_pages = page_anchors.get(material_id)
        if allowed_pages is None or page in allowed_pages:
            selected.append(row)
    if not selected:
        return rows, False
    return selected, len(selected) < len(rows)


def build_review_context(*, finding, materials, run, subject_names_by_id=None):
    context = {"materials": [], "references": [], "limitations": []}
    if (not isinstance(run, dict) or run.get("state") != "completed" or run.get("task_id") != finding.task_id
            or type(finding.id) is not int or type(finding.task_id) is not int
            or (finding.subject_id is not None and type(finding.subject_id) is not int)):
        context["limitations"].append("核查结论或主体无法确认，未执行模型辅助判断")
        return context
    context["item_label"] = _review_item_label(finding.item_id)
    for limitation in run.get("limitations") or []:
        if isinstance(limitation, dict) and isinstance(limitation.get("message"), str):
            context["limitations"].append(limitation["message"][:500])
    links = [link for link in run.get("finding_links") or [] if isinstance(link, dict)
             and link.get("finding_id") == finding.id and link.get("subject_id") == finding.subject_id
             and link.get("item_id") == finding.item_id and link.get("kind") == finding.kind]
    ids = {identity for link in links for identity in link.get("reference_ids") or []
           if isinstance(identity, str) and identity}
    references = run.get("references") or []
    for identity in sorted(ids):
        matches = [r for r in references if isinstance(r, dict) and r.get("reference_id") == identity]
        if len(matches) != 1:
            context["limitations"].append("部分依据标识缺失或不唯一，请人工核实")
            continue
        ref = matches[0]
        if ("subject_ids" in ref and (not isinstance(ref["subject_ids"], list)
                                      or finding.subject_id not in ref["subject_ids"])
                or not isinstance(ref.get("quote"), str) or not ref["quote"].strip()):
            context["limitations"].append("部分依据不适用于当前主体或缺少原文，请人工核实")
            continue
        context["references"].append({"reference_id": identity, "text": ref["quote"]})
    timing_ids = None
    if finding.kind == "signature_chain" and finding.item_id == "A3-02-timing":
        task_materials = [m for m in materials if m.task_id == finding.task_id]
        results = check_approval_timing(SimpleNamespace(id=finding.task_id), task_materials)
        matches = [row for row in results if row.subject_id == finding.subject_id
                   and row.status != "undecidable" and row.material_id == getattr(finding, "material_id", None)]
        if finding.subject_id is None or len(matches) != 1 or matches[0].material_id is None:
            context["limitations"].append("签章时序材料或日期无法唯一配对，请人工核实")
            context["material_unavailable"] = True
            return context
        timing_ids = {m.id for m in task_materials if m.subject_id == finding.subject_id
                      and (m.id == matches[0].material_id or is_attachment_source(m))}
    if finding.subject_id is None and finding.kind == "internal_records":
        candidates = [m for m in materials if m.task_id == finding.task_id and is_internal_record_candidate(m)]
        identities = {m.subject_id for m in candidates if m.subject_id is not None}
        names = subject_names_by_id or {}
        if len(identities) != 1 or not isinstance(names.get(next(iter(identities), None)), str):
            context["limitations"].append("内部记录无法确认唯一主体，请人工核实")
            context["material_unavailable"] = True
            return context
        subject_name = names[next(iter(identities))].strip()
        if not subject_name:
            context["limitations"].append("内部记录主体名称缺失，请人工核实")
            context["material_unavailable"] = True
            return context
        context["required_subject_name"] = subject_name
    signature_ids = set()
    if (finding.subject_id is None and finding.kind == "signature_chain"
            and finding.item_id in {"A3-02-legal", "A3-02-group"}):
        packets = [m for m in materials if m.task_id == finding.task_id and is_attachment_source(m)]
        anchor_id = getattr(finding, "material_id", None)
        groups = [group for group in packet_groups(packets) if any(m.id == anchor_id for m in group)]
        if type(anchor_id) is int and len(groups) == 1:
            signature_ids = {m.id for m in groups[0]}
        else:
            context["limitations"].append("无法确认签章核查对应的报审资料包，请人工核实")
    for material in materials:
        if material.task_id != finding.task_id:
            continue
        if timing_ids is not None and material.id not in timing_ids:
            continue
        if finding.subject_id is None:
            # 任务级材料范围按各内核已有证据范围确定，其他组保持不可用。
            a1 = (finding.kind in {"transfer_scope", "illegal_subcontract", "negative_list"}
                  and getattr(material, "role", None) in {"general", "sub", "labor", "packet"})
            internal = (finding.kind == "internal_records" and is_internal_record_candidate(material))
            signature = (finding.kind == "signature_chain" and material.id in signature_ids)
            if not (a1 or internal or signature):
                continue
        elif material.subject_id != finding.subject_id:
            continue
        if material.parse_state != "done":
            context["limitations"].append("部分材料尚未解析完成，请人工核实")
            context["incomplete_material_scope"] = True
            continue
        gap = material_reading_gap(material)
        coverage = reading_coverage(material)
        if gap is not None or not coverage["reading_complete"]:
            context["limitations"].append("部分材料读取不完整或识别质量待核实，不能据此认定无问题")
            context["incomplete_material_scope"] = True
        reviewed = reviewed_readable_pages(material)
        untrusted = set(gap or []) | set(coverage["unread_pages"]) | set(coverage["suspect_pages"])
        for record in getattr(material, "degradations_json", None) or []:
            if isinstance(record, dict) and record.get("kind") == "parse_incomplete":
                untrusted.update(page for page in record.get("ocr_failed_pages") or []
                                 if type(page) is int and page > 0)
        untrusted -= reviewed
        whole_material_failure = any(isinstance(record, dict) and record.get("kind") in
                                     {"no_text_layer", "parse_failed", "parse_interrupted"}
                                     for record in getattr(material, "degradations_json", None) or [])
        unknown_gap = whole_material_failure or (gap == [] and not untrusted)
        for page in material.pages_json or []:
            if (not isinstance(page, dict) or type(page.get("page")) is not int or page["page"] < 1
                    or not isinstance(page.get("text"), str) or not page["text"].strip()):
                context["limitations"].append("部分材料页无法读取，请人工核实")
                context["incomplete_material_scope"] = True
                continue
            if (unknown_gap and page["page"] not in reviewed) or page["page"] in untrusted:
                continue
            if context.get("required_subject_name") and context["required_subject_name"] not in page["text"]:
                continue
            context["materials"].append({"task_id": finding.task_id, "subject_id": material.subject_id,
                                         "material_id": material.id, "page": page["page"], "text": page["text"]})
    scoped_materials, scoped = scope_task_level_materials(finding, context["materials"])
    if scoped:
        context["materials"] = scoped_materials
        context["incomplete_material_scope"] = True
        context["limitations"].append("模型上下文已按当前核查项材料锚点裁剪，其他材料页未纳入；不能据此证明完整范围")
    strategy = run.get("scope_strategy") if isinstance(run, dict) else None
    if (isinstance(strategy, dict) and strategy.get("enabled") is True
            and finding.kind in {"transfer_scope", "illegal_subcontract"}):
        mapping = rule_scope_mapping(finding.item_id)
        if mapping:
            required_categories, category_selectors = mapping
            anchors = []
            if type(getattr(finding, "material_id", None)) is int and type(getattr(finding, "page", None)) is int:
                anchors.append((finding.material_id, finding.page))
            anchors.extend(
                (e.get("material_id"), e.get("page"))
                for e in getattr(finding, "evidence_json", None) or []
                if isinstance(e, dict) and type(e.get("material_id")) is int and type(e.get("page")) is int
            )
            strategy_version = strategy.get("version") or "category-adjacency-v1"
            fallback_reason = (
                "preexisting_incomplete_context" if context.get("incomplete_material_scope")
                else "undecidable_finding" if getattr(finding, "status", None) == "undecidable"
                else None
            )
            if fallback_reason:
                selection = fallback_scope(
                    context["materials"], reason=fallback_reason, strategy_version=strategy_version,
                )
            else:
                selection = select_scope(
                    context["materials"],
                    required_categories=required_categories,
                    category_selectors=category_selectors,
                    anchor_pages=anchors,
                    strategy_version=strategy_version,
                )
            context["scope_selection"] = {
                "strategy_version": selection.strategy_version,
                "narrowed": selection.narrowed,
                "scope_incomplete": selection.scope_incomplete,
                "matched_categories": selection.matched_categories,
                "fallback_reason": selection.fallback_reason,
                "excluded_page_count": selection.excluded_page_count,
                "input_fingerprint": selection.input_fingerprint,
                "selected_page_ids": selection.selected_page_ids,
                "excluded_page_ids": selection.excluded_page_ids,
            }
            if selection.narrowed:
                context["materials"] = list(selection.pages)
                context["incomplete_material_scope"] = True
                context["limitations"].append(
                    "MoMA 输入按已审阅的规则证据类别裁剪，其他授权页面未纳入；不能据此认定完整范围"
                )
            elif selection.fallback_reason:
                context["limitations"].append(
                    "MoMA 证据类别范围未能可靠裁剪，已回退现有模型输入范围，请人工核实"
                )
        elif finding.kind == "transfer_scope" and finding.item_id == "T15-05":
            from .ai_scope_evidence_graph import build_t15_05_shadow_graph

            graph = build_t15_05_shadow_graph(
                eligible_pages=context["materials"], materials=materials)
            occurrences = graph["occurrences"]
            relation_names = sorted({row["relation"] for row in occurrences})
            page_ids = tuple(sorted({
                (page.get("material_id"), page.get("subject_id"), page.get("page"))
                for page in context["materials"]
                if type(page.get("material_id")) is int and type(page.get("page")) is int
            }))
            context["scope_selection"] = {
                "strategy_version": graph["strategy_version"],
                "narrowed": False,
                "scope_incomplete": bool(context.get("incomplete_material_scope")),
                "matched_categories": tuple(relation_names),
                "fallback_reason": graph["fallback_reason"],
                "excluded_page_count": 0,
                "input_fingerprint": graph["input_fingerprint"],
                "selected_page_ids": page_ids,
                "excluded_page_ids": (),
                "shadow_evidence": {
                    "rule_fingerprint": graph.get("rule_fingerprint"),
                    "relation_counts": {
                        name: sum(row["relation"] == name for row in occurrences)
                        for name in relation_names
                    },
                    "relation_page_ids": {
                        name: tuple(sorted({
                            (row["material_id"], row["subject_id"], row["page"])
                            for row in occurrences if row["relation"] == name
                        }))
                        for name in relation_names
                    },
                    "source_role_coverage": graph.get("role_coverage"),
                },
            }
            context["limitations"].append(
                "T15-05 结构化证据图仅作影子分析，当前模型输入保持基线范围"
            )
    role = {"RC-L11": "安全员", "RC-P12": "项目总工"}.get(finding.item_id)
    if role and finding.subject_id is not None and context["materials"]:
        allowed = {(page["material_id"], page["page"]) for page in context["materials"]}
        scoped_materials = [m for m in materials if m.task_id == finding.task_id and m.subject_id == finding.subject_id]
        all_roster = [row for row in extract_management_roster(scoped_materials)
                      if (row.get("material_id"), row.get("page")) in allowed]
        roster = [row for row in all_roster if row.get("post") == role]
        names = {row.get("name") for row in roster if row.get("name")}
        if (len(roster) != 1 or len(names) != 1
                or sum(1 for row in all_roster if row.get("name") in names) != 1):
            context["materials"] = []
            context["limitations"].append("无法唯一确认该岗位人员，未执行模型辅助判断，请人工核实")
        else:
            name = next(iter(names))
            roster_pages = {(row["material_id"], row["page"]) for row in roster}
            anchor = (getattr(finding, "material_id", None), getattr(finding, "page", None))
            degree_refs = [ref for ref in getattr(finding, "evidence_json", None) or []
                           if isinstance(ref, dict) and ref.get("evidence_kind") == "personnel_degree_clue"]
            degree_pages = {(page["material_id"], page["page"]) for page in context["materials"]
                            if any(ref.get("material_id") == page["material_id"]
                                   and ref.get("page") == page["page"]
                                   and contains_quote(page["text"], ref.get("quote")) for ref in degree_refs)}
            context["materials"] = [page for page in context["materials"]
                                    if (page["material_id"], page["page"]) in roster_pages
                                    or (page["material_id"], page["page"]) == anchor
                                    or (page["material_id"], page["page"]) in degree_pages
                                    or name in page["text"]]
            if degree_pages:
                context["limitations"].append("毕业证书线索页的人员归属尚未确认，须核对原件，不得据此认定属于该人员")
            context["incomplete_material_scope"] = True
            context["limitations"].append("仅选取岗位名单及同名材料线索，其他页面未纳入，不能据此认定证件缺失或齐全")
    elif finding.kind == "completeness" and finding.subject_id is not None and context["materials"]:
        item = next((row for row in load_report_checklist() if row.get("id") == finding.item_id), None)
        if item and item.get("kind") == "document":
            words = [word for word in item.get("keywords") or [] if isinstance(word, str) and word.strip()]
            for child in item.get("required_all") or []:
                words.extend(word for word in child.get("keywords") or [] if isinstance(word, str) and word.strip())
            keywords = {"".join(word.split()) for word in words}
            anchors = {(getattr(finding, "material_id", None), getattr(finding, "page", None))}
            anchors.update((ref.get("material_id"), ref.get("page"))
                           for ref in getattr(finding, "evidence_json", None) or [] if isinstance(ref, dict))
            selected = [page for page in context["materials"]
                        if (page["material_id"], page["page"]) in anchors
                        or any(keyword in "".join(page["text"].split()) for keyword in keywords)]
            if len(selected) != len(context["materials"]):
                context["incomplete_material_scope"] = True
                context["limitations"].append("仅选取本项定位或关键词相关的部分材料，其他页面未纳入，不能据此认定缺件或齐全")
            context["materials"] = selected
    if not context["references"] or not context["materials"]:
        context["limitations"].append("缺少当前任务相关材料或已关联的规则原文，请人工核实"
                                      if finding.subject_id is None else "缺少当前主体材料或已关联的规则原文，请人工核实")
    if not context["materials"]:
        context["material_unavailable"] = True
    context["limitations"] = list(dict.fromkeys(context["limitations"]))
    return context


async def review_linked_finding(*, client, finding, materials, run, on_usage=None, subject_names_by_id=None):
    context = build_review_context(finding=finding, materials=materials, run=run, subject_names_by_id=subject_names_by_id)
    if not context["materials"] or not context["references"]:
        return {"proposals": [], "limitations": context["limitations"], "execution_status": "not_run"}
    result = await review_item(client=client, task_id=finding.task_id, subject_id=finding.subject_id,
                               item_id=finding.item_id, item_label=context["item_label"], materials=context["materials"],
                               references=context["references"], input_limitations=context["limitations"],
                               incomplete_material_scope=context.get("incomplete_material_scope", False),
                               required_subject_name=context.get("required_subject_name"),
                               on_usage=on_usage)
    result["limitations"] = list(dict.fromkeys(context["limitations"] + result["limitations"]))
    selection = context.get("scope_selection")
    if isinstance(selection, dict):
        result["scope_selection"] = selection
    return result
