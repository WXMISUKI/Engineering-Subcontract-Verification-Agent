"""逐页逐证照段的人员证件字段候选；不判证件真伪或岗位资格。"""
import re

from .attachment_source import is_attachment_source
from .document_boundaries import is_document_boundary
from .personnel_evidence import identity_number_candidates

ITEM_ID = "A2-10"
SOURCE = "人员名单与所附人员证书原文字段对照（字段候选不替代原件及岗位资格核验）"

_TITLE = re.compile(r"^(?:中华人民共和国)?(?:一[级等]|二[级等])?(?:建造师注册证书|注册建造师证书|"
                    r"(?:建筑施工企业|公路水运工程施工企业).{0,32}安全生产考核合格证书|安全生产考核合格证(?:书)?|"
                    r"特种作业操作证|建筑施工特种作业操作资格证(?:书)?|特种设备作业人员证|"
                    r"专业技术职务任职资格证书|专业技术资格证书|职称证书)"
                    r"(?:正本|副本|[（(](?:正本|副本|电子证书|电子版|复印件)[）)])?$" )
_INDIRECT = re.compile(r"模板|示例|样例|样表|填表说明|填写示范|附件目录|应提交|应提供")
_LABELS = {
    "name": ("姓名", "持证人姓名"),
    "professional_title": ("职称", "专业技术职务"),
    "certificate_type": ("证书类别", "证书类型", "作业类别", "资格类别"),
    "certificate_number": ("证书编号", "证件编号", "证号", "管理号"),
    "validity_start": ("有效期起", "有效期开始", "有效起始日期"),
    "validity_end": ("有效期至", "有效期止", "有效截止日期"),
    "validity_period": ("有效期", "有效期限"),
}
_ALL_LABELS = "|".join(re.escape(label) for labels in _LABELS.values() for label in labels)
_ALL_LABELS += "|身份证号(?:码)?|公民身份号码|注册单位|发证机关|签发日期|出生日期|性别"


def _document_boundary(line):
    plain = re.sub(r"\s+", "", line).strip(" #")
    return bool(_TITLE.fullmatch(plain) or is_document_boundary(line))


def _fields(text):
    output = {}
    for field, labels in _LABELS.items():
        pattern = (r"(?:^|[\s;；|])(?:" + "|".join(map(re.escape, labels)) + r")\s*[:：]\s*"
                   r"([^\n;；|]+?)(?=\s+(?:" + _ALL_LABELS + r")\s*[:：]|[\n;；|]|$)")
        values = list(dict.fromkeys(value.strip() for value in re.findall(pattern, text)
                                   if value.strip() not in {"/", "-", "—", "待填"}))
        if field == "name":
            values = [value for value in values if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", value)]
        output[field + "_candidates"] = values
        output[field] = values[0] if len(values) == 1 else None
    numbers = identity_number_candidates(text)
    output.update(id_number=numbers[0] if len(numbers) == 1 else None, id_number_candidates=numbers)
    return output


def extract_person_certificates(materials):
    records = []
    for material in materials:
        if (material.parse_state != "done" or not is_attachment_source(material)
                or _INDIRECT.search(material.file_name or "")):
            continue
        for page in material.pages_json or []:
            text = page.get("text") or ""
            if _INDIRECT.search(text):
                continue
            lines = text.splitlines()
            for start, title in enumerate(lines):
                plain = re.sub(r"\s+", "", title).strip(" #")
                if not _TITLE.fullmatch(plain):
                    continue
                end = next((index for index in range(start + 1, len(lines))
                            if _document_boundary(lines[index])), len(lines))
                quote = "\n".join(lines[start:end])
                fields = _fields(quote)
                if not fields["name_candidates"]:
                    continue
                records.append({**fields, "document_title": plain, "material_id": material.id,
                                "subject_id": getattr(material, "subject_id", None),
                                "page": page.get("page"), "quote": quote,
                                "evidence_kind": "person_certificate"})
    return records
