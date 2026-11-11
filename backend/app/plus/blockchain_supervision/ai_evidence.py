"""核验模型建议引用；本模块不调用模型、不产生业务结论或写入数据库。

调用者必须仅传入本次请求实际发送的当前任务材料页和已授权核验的依据原文。
引用存在只能证明可定位，不能证明语义推理或业务结论正确。
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any

MODEL_EVIDENCE_INSTRUCTIONS = (
    "材料范围说明表明只看到部分材料时，不得断言未提供、缺失或不存在；"
    "可说‘已提供的文本片段中未见’，并建议人工核实是否缺失，status 使用 undecidable；"
    "不得笼统说材料中未见。"
    "材料及依据的 quote 必须从对应输入 text 逐字复制连续原文，不能改写、合并或补字，"
    "每条引文去空白后至少四个字符；不得把页面编号、摘要或结论当原文。"
    "建议面向业务用户，不输出内部字段名或程序参数；依据未要求的年检、额外证明、章名或签字条件不得自行增加。"
    "材料范围不全时优先提示核对已上传原件，不得直接要求补传尚未确认缺失的材料。"
    "找不到可核验的双侧引文时返回空 proposals，不得编造。"
)


def model_scope_description(incomplete: bool) -> str:
    return ("本次仅提供部分材料或片段，不能据此认定其他内容缺失或齐全"
            if incomplete else "已按本次核查范围提供材料，请仅依据提供的原文判断")


@dataclass(frozen=True)
class ProposalValidation:
    accepted: bool
    reason: str | None
    proposal: dict | None


def contains_quote(text: Any, quote: Any) -> bool:
    if not isinstance(text, str) or not isinstance(quote, str):
        return False
    needle = "".join(quote.split())
    return 4 <= len(needle) <= 1000 and needle in "".join(text.split())


def validate_ai_proposal(proposal: Any, *, task_id: int, subject_id: int | None, item_id: str,
                         materials: list[dict], references: list[dict],
                         required_subject_name: str | None = None,
                         incomplete_material_scope: bool = False,
                         kind: str | None = None) -> ProposalValidation:
    """只验证本次上下文中的双侧连续原文；返回结果仍是待人工核实的建议。"""
    def reject(reason: str) -> ProposalValidation:
        return ProposalValidation(False, reason, None)

    if (not isinstance(proposal, dict) or proposal.get("item_id") != item_id
            or type(proposal.get("subject_id")) is not type(subject_id)
            or proposal.get("subject_id") != subject_id):
        return reject("核查项或主体与本次任务不一致")
    if proposal.get("status") not in {"hit", "miss", "undecidable"}:
        return reject("建议状态无效")
    if proposal["status"] == "miss":
        # AI 可以在完整材料范围内建议符合，但不能对“缺件/签章缺失”类事项
        # 用文本中没看到来证明无问题；这两类仍必须保持无法核实并交人工。
        if incomplete_material_scope:
            return reject("材料范围不完整，不能由模型建议符合")
        if kind in {"completeness", "signature_chain"}:
            return reject("缺件或签章类事项不能仅凭文本片段判为符合")
    if incomplete_material_scope and proposal["status"] == "hit":
        return reject("当前只核对部分材料，无法确认完整范围，确定性建议未采用，请人工核实")
    # 当前模型仅收到 OCR 文本，没有看到页面图像。网络图的图形本体
    # 可能被 OCR 省略，文本证据即便可定位，也不足以证明图不存在。
    if item_id == "RC-A08" and proposal["status"] == "hit":
        return reject("仅凭文本不能判定网络图缺失，请结合页面图像核实")
    message = proposal.get("message")
    if not isinstance(message, str) or not 1 <= len(message.strip()) <= 500:
        return reject("建议说明无效")
    if "incomplete_material_scope" in message.lower():
        return reject("建议包含内部状态字段，不能展示给业务用户")
    # 部分页面的建议即使标为不可判定，正文也不能同时声称已经缺件。
    # “请核实是否缺失”是待核问题，不是断言已经缺件。只豁免明确的
    # 询问措辞，其余肯定缺件表述仍按不完整范围拒绝。
    absence_claim = message
    for qualified_scope in ("已提供的文本片段中未见", "本次输入的文本片段中未见",
                            "本次提供的文本片段中未见"):
        absence_claim = absence_claim.replace(qualified_scope, "")
    for qualified in ("是否缺失", "是否缺少", "是否未提供", "是否未提交", "是否未上传", "是否未附", "是否不存在", "是否没有"):
        absence_claim = absence_claim.replace(qualified, "")
    if incomplete_material_scope and any(term in absence_claim for term in (
            "未提供", "未提交", "未上传", "未附", "未见", "缺少", "缺失", "不存在", "没有")):
        return reject("当前只核对部分材料，建议中的缺件表述无法证实，请人工核实")
    material_refs, rule_refs = proposal.get("material_evidence"), proposal.get("rule_evidence")
    if not isinstance(material_refs, list) or not 1 <= len(material_refs) <= 20:
        return reject("缺少可核验材料证据")
    if not isinstance(rule_refs, list) or not 1 <= len(rule_refs) <= 20:
        return reject("缺少可核验规则依据")
    verified_material_refs = []
    for ref in material_refs:
        if (not isinstance(ref, dict) or type(ref.get("material_id")) is not int
                or type(ref.get("page")) is not int or ref["page"] < 1):
            return reject("材料证据不属于本次任务主体或无法在原页核验")
        material_rows = [row for row in materials if isinstance(row, dict) and row.get("task_id") == task_id
                         and row.get("material_id") == ref["material_id"]]
        if not material_rows:
            return reject("引用材料不在本次提供的材料范围内")
        scoped_rows = [row for row in material_rows if subject_id is None or row.get("subject_id") == subject_id]
        if material_rows and not scoped_rows:
            return reject("材料引用不属于本次核查主体")
        page_rows = [row for row in scoped_rows if row.get("page") == ref["page"]]
        if not page_rows:
            return reject("引用页不在本次提供的材料范围内")
        matches = [row for row in page_rows if contains_quote(row.get("text"), ref.get("quote"))]
        if not matches:
            return reject("引用文字未在本次提供的原页片段中找到")
        if (len(matches) != 1 or (matches[0].get("subject_id") is not None
                                  and type(matches[0].get("subject_id")) is not int)):
            return reject("材料证据不属于本次任务主体或无法在原页核验")
        verified_material_refs.append({**deepcopy(ref),
            **({"subject_id": matches[0].get("subject_id")} if subject_id is None else {})})
    if required_subject_name and not all(required_subject_name in ref.get("quote", "") for ref in material_refs):
        return reject("内部记录引文缺少登记主体名称")
    for ref in rule_refs:
        if (not isinstance(ref, dict) or not isinstance(ref.get("reference_id"), str)
                or not ref["reference_id"] or not any(isinstance(row, dict)
                    and row.get("reference_id") == ref["reference_id"]
                    and contains_quote(row.get("text"), ref.get("quote")) for row in references)):
            return reject("规则依据不在本次授权上下文中或原文无法核验")
    # 已在真实样本中出现但依据未提出的附加要求。仅检查明确列出的几类；
    # 命中词语也不证明完整语义成立，其他语义仍需人工核对。
    basis_ids = {ref["reference_id"] for ref in rule_refs}
    basis_text = "\n".join(row["text"] for row in references
                           if isinstance(row, dict) and row.get("reference_id") in basis_ids
                           and isinstance(row.get("text"), str))
    unsupported = (
        (r"年检|年审", r"年检|年审|年度(?:检查|审查)"),
        (r"(?:法定代表人|法人).{0,8}(?:签字|签章|盖章)",
         r"(?:法定代表人|法人).{0,8}(?:签字|签章|盖章)"),
        (r"总监办.{0,12}(?:签字|签章|盖章)", r"总监办.{0,12}(?:签字|签章|盖章)"),
    )
    def stated_requirement(pattern: str) -> bool:
        for clause in re.split(r"[。；;\n]", basis_text):
            for match in re.finditer(pattern, clause):
                if not re.search(r"无需|无须|不需要|不要求|取消|不再|免于|免除|不得|禁止", clause[:match.end()]):
                    return True
        return False

    if any(re.search(claim, message) and not stated_requirement(required)
           for claim, required in unsupported):
        return reject("建议包含本项依据未体现的附加要求，请人工核实")
    # 报审材料内可能包含新闻或通知，不能因材料存在这类文字就声称
    # 本次引用的核查依据已经发生变更。仅拦截明确归因给依据的常见说法。
    policy_change = r"已修改|已修订|已取消|已废止|不再提供|不再要求|停止执行"
    quoted_basis = "\n".join(ref["quote"] for ref in rule_refs)
    if (re.search(r"依据[^。；;\n]{0,100}(?:" + policy_change + r")", message)
            and not re.search(policy_change, quoted_basis)):
        return reject("建议声称核查依据发生变更，但所引依据未体现该变更，请人工核实")
    return ProposalValidation(True, None, {
        "item_id": item_id, "subject_id": subject_id, "status": proposal["status"],
        "message": message.strip(), "material_evidence": verified_material_refs,
        "rule_evidence": deepcopy(rule_refs), "advisory_only": True,
    })
