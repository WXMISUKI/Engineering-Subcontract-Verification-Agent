"""从当前合同可读页面派生上传页字段，不改写任务或历史版本。"""
import re

from . import parsing

_LABEL = re.compile(r"(?<!分包)(工程名称|项目名称|工程全称|工程标段名称|合同段名称|合同段|标段名称|标段)\s*[:：]\s*")
_NEXT_FIELD = re.compile(r"\s*(?:合同编号|合同号|编号|监理单位|承包单位)\s*[:：]")
_PROJECT = {"工程名称", "项目名称", "工程全称", "工程标段名称"}
_SECTION_SUFFIX = re.compile(r"(主线施工\s*[0-9一二三四五六七八九十]+\s*标)\s*$")


def section_suggestions(materials):
    """只返回有明确标签、页面可读且值唯一的候选；冲突时不猜测。"""
    candidates = {"project_name": {}, "contract_section": {}}
    for material in materials:
        if getattr(material, "role", None) not in {"general", "sub", "labor"}:
            continue
        for page in getattr(material, "pages_json", None) or []:
            if not isinstance(page, dict) or not parsing.material_page_readable(material, page.get("page")):
                continue
            for line in (page.get("text") or "").splitlines():
                matches = list(_LABEL.finditer(line))
                for index, match in enumerate(matches):
                    end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
                    value = _NEXT_FIELD.split(line[match.end():end], maxsplit=1)[0].strip(" ：:;；，,。")
                    if not value or len(value) > 160:
                        continue
                    field = "project_name" if match.group(1) in _PROJECT else "contract_section"
                    key = re.sub(r"\s+", "", value)
                    candidates[field].setdefault(key, {"value": value, "material_id": material.id, "page": page.get("page")})
                    if field == "project_name":
                        suffix = _SECTION_SUFFIX.search(value)
                        if suffix:
                            section = re.sub(r"\s+", "", suffix.group(1))
                            candidates["contract_section"].setdefault(section, {"value": section, "material_id": material.id, "page": page.get("page"), "derived_from_project": value})
    return {field: next(iter(values.values())) for field, values in candidates.items() if len(values) == 1}
