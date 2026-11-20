"""实际表单逐页签章候选矩阵；文字与通用章痕迹不等于角色签章认证。"""
import re
import unicodedata

from .parsing import material_page_readable
from .rules import load_signature_form_matrices


def _compact(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def _signature_candidate(text, role):
    # 只消费本表单的签字栏目，正文“分包项目经理：姓名”不是签署证据。
    label = re.compile(r"^\s*(?:监理人\s*)?" + re.escape(role) + r"\s*[（(][^）)]*签字[^）)]*[）)]\s*[:：]")
    lines = [line.strip() for line in text.splitlines() if label.match(line)]
    if not lines:
        return "未见文本", None
    # 保留同页所有候选，不能用最后一个空栏覆盖前面的文字。
    for line in lines:
        rest = line.split(role, 1)[1]
        rest = re.sub(r"^[（(][^）)]*[）)]", "", rest.strip())
        parts = re.split(r"[:：]", rest, maxsplit=1)
        value = parts[1].strip() if len(parts) == 2 else ""
        if re.search(r"字迹不清|无法辨认|模糊", value):
            return "字迹不清", line
        value = re.sub(r"[0-9年月日．.\-_/\s]", "", value)
        if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", value) and value not in {
                "签字", "签名", "盖章", "执业章", "某某", "待签字", "未签字"}:
            return "已见文字候选", line
    return "未见可确认文本", lines[0]


def form_signature_matrices(group):
    """每个实际表单页单独返回矩阵，绝不跨表单/主体拼完整。"""
    forms = load_signature_form_matrices()
    result = []
    for material in group:
        if material.parse_state != "done":
            continue
        for page in material.pages_json or []:
            number, text = page.get("page"), page.get("text") or ""
            if not material_page_readable(material, number) or re.search(r"模板|填写示例|样表", text):
                continue
            headings = {_compact(line) for line in text[:400].splitlines()}
            matching = [form for form in forms if form["title"] in headings]
            if len(matching) != 1:
                continue
            form, = matching
            rows = []
            for role, requirements in form["roles"].items():
                state, quote = _signature_candidate(text, role)
                rows.append({"role": role, "signature_state": state,
                             "requirements": requirements,
                             "seal_states": {kind: "待核实" for kind in requirements if kind != "签字"},
                             "material_id": material.id, "page": number,
                             "quote": quote or form["title"], "evidence_kind": "signature_form_candidate"})
            signals = [row for row in getattr(material, "seal_json", None) or []
                       if isinstance(row, dict) and row.get("page_idx") == number
                       and isinstance(row.get("bbox"), (list, tuple)) and len(row["bbox"]) == 4]
            result.append({"form_id": form["id"], "clause_id": form["clause_id"],
                           "title": form["title"], "material_id": material.id, "page": number,
                           "quote": form["title"], "roles": rows, "seal_signal": bool(signals)})
    return result
