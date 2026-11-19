"""担保与保险独立原文事实；不判适用性、真伪或有效覆盖。"""
import re

from .attachment_source import is_attachment_source
from .document_boundaries import is_document_boundary
from .finding_text import bounded_finding_message
from .models import BsFinding, STATUS_UNDECIDABLE
from .parsing import material_page_readable

ITEM_ID = "A3-04-security"
SOURCE = "履约担保与保险原文辅助核对；118号第六条归档待人工校对，首次/重新承揽专业分包及总包同比例等条件需确认"
_TITLES = {"履约保函": "performance_guarantee", "银行履约保函": "performance_guarantee",
           "履约保证金凭证": "performance_deposit", "履约保证金缴纳凭证": "performance_deposit",
           "履约保证金收据": "performance_deposit", "保险单": "insurance_policy",
           "建筑工程一切险保险单": "insurance_policy", "安装工程一切险保险单": "insurance_policy",
           "安全生产责任保险保险单": "insurance_policy", "雇主责任保险保险单": "insurance_policy"}
_LABELS = {"project": {"项目名称", "工程名称"}, "contract_id": {"合同编号"},
           "number": {"保函编号", "凭证编号", "收据编号", "保单号", "保单号码"},
           "applicant": {"申请人", "缴款人", "付款人"}, "beneficiary": {"受益人", "收款人"},
           "guarantor": {"担保人", "出函行", "保证人"}, "policyholder": {"投保人"}, "insured": {"被保险人"},
           "guaranteed_amount": {"担保金额", "保证金额", "保证金金额", "缴纳金额"},
           "insured_amount": {"保险金额", "责任限额"}, "premium": {"保险费", "保费"},
           "ratio": {"担保比例", "保证金比例"}, "period": {"担保期限", "保函有效期", "有效期", "保险期间"},
           "state": {"原文状态", "凭证状态", "保单状态", "保函状态"}}
_TEMPLATE = re.compile(r"^(?:模板|示例|样例|样表|填写说明|填表说明|附件目录)(?:[:：]|$)")


def _title(line):
    return re.sub(r"(?:正本|副本|[（(](?:正本|副本|复印件|电子版)[）)])$", "",
                  re.sub(r"\s+", "", line).strip(" #"))


def _boundary(line):
    plain = _title(line)
    if re.search(r"[，；。]|不替代|不构成|不等于|不能|未提供|(?:应当?|须|需要?|必须).{0,8}(?:提供|提交|支付|出具)", plain):
        return False
    return bool(plain in _TITLES or is_document_boundary(line)
                or re.fullmatch(r"[^:：|]{0,50}(?:凭证|回单|收据|保险单|保函)", plain))


def extract_security_records(materials):
    records = []
    for material in materials:
        if (getattr(material, "parse_state", None) != "done" or not is_attachment_source(material)
                or re.search(r"模板|示例|样例|样表", getattr(material, "file_name", "") or "")):
            continue
        for page in material.pages_json or []:
            if not isinstance(page, dict) or not material_page_readable(material, page.get("page")):
                continue
            lines = str(page.get("text") or "").splitlines()
            if any(_TEMPLATE.match(line.strip(" #")) for line in lines):
                continue
            for start, line in enumerate(lines):
                kind = _TITLES.get(_title(line))
                if not kind:
                    continue
                end = next((i for i in range(start + 1, len(lines)) if _boundary(lines[i])), len(lines))
                quote = "\n".join(lines[start:end])
                row = {"material_id": material.id, "subject_id": getattr(material, "subject_id", None),
                       "page": page["page"], "evidence_kind": kind, "document_title": line.strip(), "quote": quote}
                for key, labels in _LABELS.items():
                    values = list(dict.fromkeys(value.strip() for value in re.findall(
                        r"(?:^|\n)\s*(?:" + "|".join(map(re.escape, labels)) + r")\s*[:：]\s*([^\n]+)", quote)
                        if not re.fullmatch(r"[_＿\s/—-]+|待填", value.strip())))
                    row.update({key: values[0] if len(values) == 1 else None, key + "_candidates": values})
                relevant_amount = row["insured_amount_candidates"] if kind == "insurance_policy" else row["guaranteed_amount_candidates"]
                if row["number_candidates"] or (relevant_amount and (row["project_candidates"] or row["contract_id_candidates"])):
                    records.append(row)
    return records


def run_security_check(task, materials):
    labels = {"performance_guarantee": "履约保函", "performance_deposit": "保证金凭证", "insurance_policy": "保险单"}
    findings = []
    for row in extract_security_records(materials):
        amount = row["insured_amount_candidates"] if row["evidence_kind"] == "insurance_policy" else row["guaranteed_amount_candidates"]
        message = "%s原文候选：项目%s，合同%s，编号%s，金额%s，期间%s，状态%s；请核当事方、原件真伪、实际适用及覆盖，保险不替代履约担保；首次/重新承揽专业分包、总包同比例和合同工期未确认，不自动要求劳务适用同种担保" % (
            labels[row["evidence_kind"]], " / ".join(row["project_candidates"]) or "待核实",
            " / ".join(row["contract_id_candidates"]) or "待核实", " / ".join(row["number_candidates"]) or "待核实",
            " / ".join(amount) or "待核实", " / ".join(row["period_candidates"]) or "待核实",
            " / ".join(row["state_candidates"]) or "待核实")
        findings.append(BsFinding(task_id=task.id, item_id=ITEM_ID, kind="signature_chain", status=STATUS_UNDECIDABLE,
            finding_semantics="manual_prompt", source=SOURCE, material_id=row["material_id"], page=row["page"],
            quote=row["quote"], evidence_json=[row], location_mode="page", message=bounded_finding_message(message)))
    return findings
