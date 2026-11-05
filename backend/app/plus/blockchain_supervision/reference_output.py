"""只渲染已保存的五步依据；Word/PDF共享同一份纸面文字，不读取规则或远端。"""


def reference_lines(snapshot: dict | None) -> list[str]:
    if snapshot is None:
        return ["本次审查未留存五步依据快照，不能补造历史引用；请结合原始审查记录核对。"]
    lines = ["固定依据与在线参考分列；在线参考仅供查阅，不改变核查结论，须人工复核。"]
    runs = snapshot.get("runs") or []
    if runs:
        lines.append("本代分组执行：")
        for run in runs:
            group = run.get("group_id") or "未知组"
            if run.get("execution_mode") == "carried_forward":
                lines.append("%s：沿用上一轮已完成结果（本轮未重新查询）" % group)
            else:
                lines.append("%s：%s" % (group, run.get("state") or "状态待确认"))
    for limitation in snapshot.get("limitations") or []:
        if limitation.get("message"):
            lines.append("限制：" + limitation["message"])
    for step in snapshot.get("steps") or []:
        lines.append(step["title"])
        if step.get("state") == "out_of_scope":
            lines.append("本步骤未纳入本次核查范围。")
            continue
        lines.append("本地固定依据：")
        for basis in step.get("fixed_basis") or []:
            name = basis.get("doc_no") or basis.get("doc_title") or basis.get("clause_id")
            location = basis.get("marker") or basis.get("clause_id") or ""
            if basis.get("list_no") is not None:
                location += " 第%s项" % basis["list_no"]
            lines.append("%s %s" % (name, location))
            lines.append(basis.get("text") or "该依据原文未登记完整，请人工核对原件。")
            start, end = basis.get("page_start"), basis.get("page_end")
            page = ("第%s页" % start if start == end or not end else "第%s—%s页" % (start, end)) if start else "物理页码未确认"
            verification = "已人工校对" if basis.get("human_verified") else "尚未人工校对，请核对原件"
            lines.append("位置与校对状态：%s；%s" % (page, verification))
        material_basis = step.get("material_basis") or []
        if material_basis:
            lines.append("材料清单依据（与规范条款分列）：")
        for basis in material_basis:
            name = basis.get("doc_no") or basis.get("doc_title") or basis.get("clause_id")
            location = basis.get("marker") or basis.get("clause_id") or ""
            if basis.get("list_no") is not None:
                location += " 第%s项" % basis["list_no"]
            lines.append("%s %s" % (name, location))
            lines.append(basis.get("text") or "该材料清单依据原文未登记完整，请人工核对原件。")
            start, end = basis.get("page_start"), basis.get("page_end")
            page = ("第%s页" % start if start == end or not end else "第%s—%s页" % (start, end)) if start else "物理页码未确认"
            verification = "已人工校对" if basis.get("human_verified") else "尚未人工校对，请核对原件"
            lines.append("位置与校对状态：%s；%s" % (page, verification))
        references = step.get("references") or []
        if references:
            lines.append("在线参考依据：")
        for reference in references:
            lines.append("%s %s" % (reference.get("doc_no") or reference.get("doc_title") or "规范文件",
                                     reference.get("clause_id") or ""))
            if reference.get("reference_completeness") == "partial":
                lines.append("仅为部分原文，请核对完整要求")
            lines.append(reference.get("quote") or "")
            page = reference.get("physical_page")
            end = reference.get("physical_page_end")
            position = ("第%s–%s页" % (page, end) if isinstance(page, int) and isinstance(end, int) and end > page
                        else "第%s页" % page if page else "物理页码未确认")
            verification = "已人工校对" if reference.get("human_verified") else "尚未人工校对，请核对原件"
            lines.append("位置与校对状态：%s；%s" % (position, verification))
        for message in dict.fromkeys(limit.get("message") for limit in step.get("limitations") or []
                                     if limit.get("message")):
            lines.append("限制：" + message)
    return lines
