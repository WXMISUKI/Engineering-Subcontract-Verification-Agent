"""合同费用/工资责任原文辅助核对；不认证实际履约或法定比例。"""
import re

from .contract_clause_facts import _CLAUSE_PATTERNS, iter_contract_sections
from .finding_text import bounded_finding_message
from .models import BsFinding, STATUS_UNDECIDABLE

ITEMS = {"safety_cost": "RC-A10-cost", "wages": "RC-A11-wages"}
SOURCE = "合同责任原文辅助核对；财资〔2022〕136号、724号及实名制依据引文待核实，不据此自动判法定结论"
_WORDS = {"safety_cost": ("安全生产费", "安全费用", "安全文明施工费"),
          "wages": (*_CLAUSE_PATTERNS["wages"], "实名制", "专用账户", "工资专户", "代发")}


def extract_responsibility_records(materials):
    records = []
    for material, page, text in iter_contract_sections(materials):
        for topic, words in _WORDS.items():
            if not any(word in text for word in words):
                continue
            records.append({"material_id": material.id, "subject_id": getattr(material, "subject_id", None),
                "page": page["page"], "role": material.role, "evidence_kind": "contract_responsibility",
                "topic": topic, "quote": text, "performance_verified": False,
                "percentage_mentions": list(dict.fromkeys(re.findall(r"\d+(?:\.\d+)?\s*[%％]", text))),
                "amount_mentions": list(dict.fromkeys(re.findall(r"(?:人民币\s*)?\d[\d,，]*(?:\.\d+)?\s*(?:万元|元)", text))),
                "tax_basis_mentions": list(dict.fromkeys(re.findall(r"不含税|含税|税前|税后", text))),
                "mentions_are_unassigned": True})
    return records


def run_responsibility_check(task, materials, *, topic):
    rows = [row for row in extract_responsibility_records(materials) if row["topic"] == topic]
    if not rows:
        return []
    label = "安全生产费" if topic == "safety_cost" else "工资支付与实名制"
    notice = ("请核单列约定、金额、比例和计提基数；提及的百分比可能属于税率或其他条款，不自动套用1.5%"
              if topic == "safety_cost" else
              "请核总包专户、总包代发与分包实名制/资料配合责任；不据此要求分包自行开户")
    anchor = rows[0]
    return [BsFinding(task_id=task.id, item_id=ITEMS[topic], kind="completeness", status=STATUS_UNDECIDABLE,
        finding_semantics="manual_prompt", source=SOURCE,
        message=bounded_finding_message(label + "合同责任候选：" + notice +
            "；原文见来源引文（含否定及不同约定）；合同义务不证明实际费用提取、账户开立或工资支付，零抽取不证明未约定"),
        material_id=anchor["material_id"], page=anchor["page"], quote=anchor["quote"],
        location_mode="page", evidence_json=rows)]
