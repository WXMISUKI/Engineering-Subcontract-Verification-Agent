"""独立模型建议执行层；调用者负责授权、来源核验及版本冻结。"""
from .ai_evidence import MODEL_EVIDENCE_INSTRUCTIONS, model_scope_description, validate_ai_proposal
from .semantic_review import InputBudgetExceeded, request_json


_PROMPT = (
    '根据任务材料和已核验的规则原文，对指定核查项提出待人工核实的建议。'
    '不推断缺失事实，不修改固定规则，不作最终业务结论；材料不足时说明限制。'
    '每条建议必须引用同一页的连续材料原文和输入规则的连续原文。'
    '仅提供文本时不能根据文字缺少图形而断言网络图缺失，须提示查看原页。'
    '指定主体名称时，材料引文必须包含该名称；不得将其他主体的事实归给该主体。'
    + MODEL_EVIDENCE_INSTRUCTIONS +
    '输出JSON：{"proposals":[{"item_id":"核查项","subject_id":主体ID,'
    '"status":"hit或undecidable","message":"说明","material_evidence":'
    '[{"material_id":材料ID,"page":页码,"quote":"原文"}],'
    '"rule_evidence":[{"reference_id":"依据ID","quote":"原文"}]}]}。'
)


async def review_item(*, client, task_id, subject_id, item_id, materials, references,
                      input_limitations=None, on_usage=None, required_subject_name=None, item_label=None,
                      incomplete_material_scope=False):
    """只校验实际发送的上下文，不写入任务；空结果不代表无风险。"""
    result = {"proposals": [], "limitations": [], "execution_status": "completed"}
    if (not isinstance(materials, list) or not materials or not isinstance(references, list)
            or not references or any(not isinstance(row, dict) or row.get("task_id") != task_id
            or (subject_id is not None and (type(row.get("subject_id")) is not type(subject_id)
                                            or row.get("subject_id") != subject_id))
            or (row.get("subject_id") is not None and type(row.get("subject_id")) is not int)
            or type(row.get("material_id")) is not int or type(row.get("page")) is not int
            or row["page"] < 1 or not isinstance(row.get("text"), str) or not row["text"].strip()
            for row in materials) or any(not isinstance(row, dict) or not isinstance(row.get("reference_id"), str)
            or not row["reference_id"] or not isinstance(row.get("text"), str) or not row["text"].strip()
            for row in references)):
        result["limitations"].append("材料或依据不完整，未执行模型辅助判断，请人工核实")
        result["execution_status"] = "not_run"
        return result
    if len({r["reference_id"] for r in references}) != len(references):
        result["limitations"].append("依据标识重复，未执行模型辅助判断，请人工核实")
        result["execution_status"] = "not_run"
        return result
    pages = [{k: r[k] for k in ("task_id", "subject_id", "material_id", "page", "text")} for r in materials]
    basis = [{k: r[k] for k in ("reference_id", "text")} for r in references]
    try:
        data = await request_json(client, _PROMPT, {"item_id": item_id, "item_label": item_label or item_id, "subject_id": subject_id,
                                  "materials": pages, "references": basis,
                                  "limitations": list(input_limitations or []),
                                  "材料范围说明": model_scope_description(incomplete_material_scope),
                                  "required_subject_name": required_subject_name}, on_usage=on_usage)
        proposals = data.get("proposals")
        if not isinstance(proposals, list) or len(proposals) > 20:
            raise ValueError("invalid proposals")
    except InputBudgetExceeded:
        result["limitations"].append("材料或依据内容过长，超出单次模型处理范围；本次未调用模型，请人工核实")
        result["execution_status"] = "not_run"
        return result
    except Exception:
        result["limitations"].append("模型辅助判断未完成，请重试或人工核实；不能据此判定无问题")
        result["execution_status"] = "failed"
        return result
    for proposal in proposals:
        validation = validate_ai_proposal(proposal, task_id=task_id, subject_id=subject_id,
                                         item_id=item_id, materials=pages, references=basis,
                                         required_subject_name=required_subject_name,
                                         incomplete_material_scope=incomplete_material_scope)
        if validation.accepted:
            result["proposals"].append(validation.proposal)
        else:
            result["execution_status"] = "partial"
            result["limitations"].append(validation.reason if item_id == "RC-A08" and isinstance(proposal, dict) and proposal.get("status") == "hit"
                                         else "部分模型建议的证据无法核验，已排除，请人工核实")
    if not proposals:
        result["limitations"].append("模型未提出可复核建议，不能据此判定无问题")
    result["limitations"] = list(dict.fromkeys(result["limitations"]))
    return result
