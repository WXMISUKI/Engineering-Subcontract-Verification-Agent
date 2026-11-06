"""同名身份证资料的识别差异线索，不将 OCR 差异当作身份冲突。"""
import re

from . import pinning
from .attachment_source import is_attachment_source
from .models import BsFinding, STATUS_UNDECIDABLE
from .parsing import material_page_readable

ITEM_ID = "A2-06"
SOURCE = "对照同名人员身份证资料中的姓名和号码原文"
_NAME = re.compile(r"姓\s*名(?:[ \t]*\|[ \t]*|\s*[:：]?\s*)([\u4e00-\u9fa5]{2,4})(?=\s|[|，,;；]|$)")
_NUMBER = re.compile(r"(?:公民身份号码|身份证号码|身份证号)(?:[ \t]*\|[ \t]*|\s*[:：]?\s*)([0-9]{17}[0-9Xx])(?![0-9A-Za-z])")


def run_identity_observations(task, materials):
    grouped = {}
    for material in materials:
        if not is_attachment_source(material) or material.parse_state != "done":
            continue
        for page in material.pages_json or []:
            if not material_page_readable(material, page.get("page")):
                continue
            text = page.get("text") or ""
            if "身份证" not in text or re.search(r"附件目录|模板|填写示例", text[:100]):
                continue
            names, numbers = list(_NAME.finditer(text)), list(_NUMBER.finditer(text))
            if len(names) != 1 or len(numbers) != 1:
                continue
            grouped.setdefault(names[0].group(1), []).append({
                "material_id": material.id, "page": page.get("page"),
                "quote": numbers[0].group(), "number": numbers[0].group(1).upper(),
            })
    findings = []
    for name, refs in grouped.items():
        # 多份材料、重名或多版本无法仅凭姓名确定同一人；保守不自动配对。
        if len(refs) != 2 or refs[0]["material_id"] != refs[1]["material_id"]:
            continue
        if refs[0]["page"] == refs[1]["page"] or refs[0]["number"] == refs[1]["number"]:
            continue
        pinned = []
        for row in refs:
            ref = {key: row[key] for key in ("material_id", "page", "quote")}
            probe = BsFinding(task_id=task.id, material_id=ref["material_id"], page=ref["page"],
                              quote=ref["quote"], evidence_json=[ref])
            pinning.pin_findings([probe], materials)
            if probe.location_mode != "precise" or probe.page != ref["page"]:
                break
            ref = probe.evidence_json[0]
            pinned.append({**ref, "location_mode": pinning.reference_location_mode(ref)})
        if len(pinned) != 2:
            continue
        findings.append(BsFinding(
            task_id=task.id, item_id=ITEM_ID, kind="cert_validity", status=STATUS_UNDECIDABLE, source=SOURCE,
            message="同名人员%s在第%s页与第%s页资料中的身份证号码存在差异线索；请对照原件核实人员及号码" % (name, pinned[0]["page"], pinned[1]["page"]),
            material_id=pinned[0]["material_id"], page=pinned[0]["page"], quote=pinned[0]["quote"], evidence_json=pinned))
    return findings
