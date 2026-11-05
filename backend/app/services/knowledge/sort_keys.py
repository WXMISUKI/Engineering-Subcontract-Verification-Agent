"""知识库列表与文件列表的稳定展示顺序。

文件按文件名开头的序号排（8、9、23、38、44、52），没有序号的排在后面；
知识库按名称中第一个序号排（规范1…规范7），「法律法规」「省级标准」等没有
序号的库排在后面，再按名称排。只影响展示，不改变检索范围与返回集合。
"""
import re

_LEADING_NUMBER = re.compile(r"^\s*([0-9]+)")
_ANY_NUMBER = re.compile(r"([0-9]+)")


def _name_key(name: str) -> str:
    return (name or "").strip().casefold()


def file_display_key(name: str) -> tuple:
    text = name or ""
    match = _LEADING_NUMBER.match(text)
    if match:
        return (0, int(match.group(1)), _name_key(text))
    return (1, 0, _name_key(text))


def knowledge_display_key(name: str) -> tuple:
    text = name or ""
    match = _ANY_NUMBER.search(text)
    if match:
        return (0, int(match.group(1)), _name_key(text))
    return (1, 0, _name_key(text))