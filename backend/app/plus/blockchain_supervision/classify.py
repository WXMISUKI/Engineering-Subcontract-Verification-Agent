"""材料归类与校验（纯函数，无 DB 依赖）。

口径来源：业主 2026-10-14 部署文件 二、（一）「五步审查」＋《沪城公路〔2025〕118 号》第五条申报材料。
- 硬必填（缺则拒绝开始核查）：**分包合同（专业或劳务）合计 ≥1 份**
- 总包合同**可选（0 或 1 份）**：业主五步审查不以总包合同为资料前提；未上传时「证据只能来自
  总包合同原文」的两类情形（细则第十六条（三）明文禁止分包、（二）未遵循承包合同原则）由
  报告/意见书的「范围说明」声明不进本次范围，**不得渲染成缺件**；
  多于 1 份属归属错误（一个任务只有一个总包），仍拒绝启动并提示更正
- 软影响（缺只降判定覆盖面，必须明示）：劳务合作合同
- 报审资料包是可选补充；报审内容可以直接写在合同正文中，不作为启动门槛
- 归属可人工更正（role_confirmed），自动归类只是初始值。
"""
import re

ROLE_GENERAL = "general"
ROLE_SUB = "sub"
ROLE_LABOR = "labor"
ROLE_PACKET = "packet"
ROLE_UNKNOWN = "unknown"

ROLE_LABELS = {
    ROLE_GENERAL: "总包合同",
    ROLE_SUB: "分包合同",
    ROLE_LABOR: "劳务合作合同",
    ROLE_PACKET: "报审资料包",
    ROLE_UNKNOWN: "待归类",
}

# 顺序即优先级：报审类文件名常含「分包」二字（如 报审包-甲·分包合同报审表.pdf），
# 必须先判 packet 再判 sub（v6 原型同口径）。
_CLASSIFY_RULES = (
    (ROLE_PACKET, re.compile(r"报审|名册|请示|批复")),
    (ROLE_LABOR, re.compile(r"劳务")),
    (ROLE_GENERAL, re.compile(r"总包|总承包|施工合同")),
    (ROLE_SUB, re.compile(r"分包|专业分包")),
)


def classify_material(file_name: str) -> str:
    name = (file_name or "").strip()
    for role, pat in _CLASSIFY_RULES:
        if pat.search(name):
            return role
    return ROLE_UNKNOWN


def validate_materials(roles: list, *, scope_a3: bool) -> dict:
    """按 03 §2.2 返回硬/软两级校验结果。

    返回 {hard_ok, hard_gaps, soft_gaps}：
    - hard_gaps：阻止开始核查的缺口（文案已按口径写死）
    - soft_gaps：允许继续但必须明示的软影响
    """
    from collections import Counter

    counts = Counter(roles)
    hard_gaps = []
    soft_gaps = []
    # 总包合同可选（0 或 1 份）：业主五步审查不以它为前提；多于 1 份才是归属错误
    if counts[ROLE_GENERAL] > 1:
        hard_gaps.append("总包合同最多 1 份（当前 %d 份），请合并或更正归属" % counts[ROLE_GENERAL])
    subcontract_count = counts[ROLE_SUB] + counts[ROLE_LABOR]
    if subcontract_count < 1:
        hard_gaps.append("分包合同（专业或劳务）至少 1 份（当前 %d 份）" % subcontract_count)
    # 独立报审包不是必需材料。A3 仍会检查合同正文中的报审条款；若证据不足，
    # 由规则内核按三态结果标记 undecidable，而不是在启动前要求用户重复上传。
    if counts[ROLE_UNKNOWN] > 0:
        soft_gaps.append("有 %d 份材料未归类，请指定归属" % counts[ROLE_UNKNOWN])
    return {"hard_ok": not hard_gaps, "hard_gaps": hard_gaps, "soft_gaps": soft_gaps}
