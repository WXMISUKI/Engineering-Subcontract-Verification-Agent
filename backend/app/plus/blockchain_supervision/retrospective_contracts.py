"""合同倒查独立结果契约；不复用准入 hit/undecidable 或人工复核门禁。"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "bs.contract-retrospective.v1"
PROMPT_VERSION = "bs.contract-retrospective-prompt.v12"
RUN_KEY = "retrospective_run_id"
JOB_GROUP = "contract_retrospective"
COLUMNS = ["项目名称", "总包/分包", "合同文件", "合同条款位置", "合同相关摘录",
           "不符合或风险说明", "依据文件", "依据条款及原文摘录"]
KINDS = {
    "text_nonconformity": "文本不符合", "internal_ambiguity": "内部歧义",
    "conditional_risk": "条件性风险", "current_notice": "现行规则提示",
    "upstream_risk": "上下游衔接风险",
}


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Citation(ContractModel):
    material_id: int = Field(gt=0)
    page: int = Field(gt=0)
    quote: str = Field(min_length=4, max_length=6000)
    clause: str = Field(default="", max_length=240)


class BasisCitation(ContractModel):
    clause_id: str = Field(min_length=1, max_length=240)
    quote: str = Field(min_length=4, max_length=6000)


class FindingCandidate(ContractModel):
    kind: Literal["text_nonconformity", "internal_ambiguity", "conditional_risk", "current_notice"]
    time_scope: Literal["historical", "current", "contract_text"]
    message: str = Field(min_length=8, max_length=2400)
    evidence: list[Citation] = Field(min_length=1, max_length=12)
    basis: list[BasisCitation] = Field(default_factory=list, max_length=6)
    conditions: str = Field(default="", max_length=1600)
    applicability: str = Field(default="", max_length=1600)

    @model_validator(mode="after")
    def coherent_kind(self):
        if self.kind == "internal_ambiguity":
            if self.time_scope != "contract_text" or len(self.evidence) < 2:
                raise ValueError("内部歧义需要两段合同原文及合同文本口径")
        elif not self.basis:
            raise ValueError("规范相关发现需要依据引文")
        if self.kind == "text_nonconformity" and self.time_scope != "historical":
            raise ValueError("文本不符合须明确历史适用口径")
        if self.kind == "current_notice" and self.time_scope != "current":
            raise ValueError("现行提示不能充作历史不符合")
        if self.kind == "conditional_risk" and not self.conditions.strip():
            raise ValueError("条件性风险须说明条件")
        return self


class BatchResponse(ContractModel):
    checked_topics: list[str] = Field(max_length=8)
    findings: list[FindingCandidate] = Field(max_length=80)
    limitations: list[str] = Field(max_length=40)


class ConsistencyResponse(ContractModel):
    findings: list[FindingCandidate] = Field(max_length=40)


class ReviewDecision(ContractModel):
    finding_id: str
    keep: bool
    reason: str = Field(min_length=1, max_length=2000)
    message: str = Field(max_length=2400)


class ReviewResponse(ContractModel):
    decisions: list[ReviewDecision]


class BasisCheck(ContractModel):
    clause_id: str
    state: Literal['checked', 'not_applicable', 'pending']
    reason: str = Field(min_length=1, max_length=1800)
    evidence: list[Citation] = Field(max_length=6)


class CoverageResponse(BatchResponse):
    checks: list[BasisCheck]


def strict_response_schema(model):
    """从既有结果契约生成Provider约束；事实/来源/条件仍由业务校验。"""
    schema = model.model_json_schema()
    def normalize(node):
        if isinstance(node, dict):
            node.pop('default', None)
            node.pop('title', None)
            if node.get('type') == 'object':
                node['required'] = list(node.get('properties', {}))
                node['additionalProperties'] = False
            for value in node.values():
                normalize(value)
        elif isinstance(node, list):
            for value in node:
                normalize(value)
    normalize(schema)
    return schema


class RunIdentity(ContractModel):
    schema_version: Literal["bs.contract-retrospective.v1"] = SCHEMA_VERSION
    prompt_version: str = PROMPT_VERSION
    run_id: str = Field(min_length=1, max_length=100)
    task_id: int = Field(gt=0)
    model_code: str = Field(min_length=1, max_length=120)
    model_identity: dict = Field(default_factory=dict)
    material_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    basis_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    knowledge_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    coverage_version: str | None = None
    upstream_version: str | None = None
    coverage_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    baseline_date: str
    contract_date: str | None = None

    @model_validator(mode="after")
    def valid_dates(self):
        date.fromisoformat(self.baseline_date)
        if self.contract_date is not None:
            date.fromisoformat(self.contract_date)
        return self


def execution_status(*, batches_total: int, batches_completed: int, limitations: list) -> str:
    """零发现是业务结果；零执行是执行失败。"""
    if batches_completed == 0:
        return "failed"
    return "partial" if batches_completed < batches_total or limitations else "completed"


def summary(status: str, findings_count: int) -> str:
    if status == "failed":
        return "本次合同倒查未完成，尚不能形成风险检查结论。"
    prefix = "本次已覆盖条款范围内"
    result = (f"列示{findings_count}项合同文本问题或风险提示。" if findings_count else
              "未发现可列示问题。")
    return prefix + result + ("部分范围受限，详见核查限制。" if status == "partial" else "")
