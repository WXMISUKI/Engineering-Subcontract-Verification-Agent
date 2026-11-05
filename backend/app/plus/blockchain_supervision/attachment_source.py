"""判断材料是否包含附件候选，不修改用户选择的材料类别。"""
import re


def has_embedded_submission(material) -> bool:
    if getattr(material, "role", None) not in {"sub", "labor"}:
        return False
    # 只用标题发现候选，标题本身不是可读证据；实际核查仍逐页校验质量。
    return any(re.search(r"(?:劳务|专业)分包报审表", re.sub(r"\s+", "", page["text"]))
               for page in getattr(material, "pages_json", None) or []
               if isinstance(page, dict) and isinstance(page.get("text"), str))


def is_attachment_source(material) -> bool:
    return getattr(material, "role", None) == "packet" or has_embedded_submission(material)
