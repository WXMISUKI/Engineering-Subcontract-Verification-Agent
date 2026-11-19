"""上下游比较契约；总包是合同证据，不是法规范。"""
from typing import Literal

from pydantic import Field

from .retrospective_contracts import Citation, ContractModel, KINDS
from .retrospective_sources import digest, original_citation

VERSION = 'bs.contract-upstream.v1'
TOPICS = ('scope', 'price', 'payment', 'schedule', 'quality', 'safety',
          'environment', 'wages', 'insurance', 'equipment', 'priority')


class ProfileFact(ContractModel):
    field: Literal['project_name', 'contract_section', 'employer', 'contractor', 'scope']
    value: str = Field(min_length=2, max_length=1200)
    evidence: list[Citation] = Field(min_length=1, max_length=4)


class ContractTerm(ContractModel):
    topic: Literal['scope', 'price', 'payment', 'schedule', 'quality', 'safety',
                   'environment', 'wages', 'insurance', 'equipment', 'priority']
    statement: str = Field(min_length=4, max_length=1200)
    obligation_holder: Literal['employer', 'general', 'sub', 'both', 'uncertain']
    transmission: Literal['pass_through', 'reserved', 'self_perform', 'uncertain']
    conditions: str = Field(max_length=1200)
    exceptions: str = Field(max_length=1200)
    priority: str = Field(max_length=1200)
    evidence: list[Citation] = Field(min_length=1, max_length=6)


class ExtractionResponse(ContractModel):
    checked_topics: list[str] = Field(min_length=11, max_length=11)
    facts: list[ProfileFact] = Field(max_length=24)
    terms: list[ContractTerm] = Field(max_length=60)


class RelationshipCheck(ContractModel):
    dimension: Literal['project', 'parties', 'scope']
    state: Literal['matched', 'mismatch', 'uncertain']
    reason: str = Field(min_length=4, max_length=1600)
    evidence: list[Citation] = Field(max_length=8)


class RelationshipResponse(ContractModel):
    checks: list[RelationshipCheck] = Field(min_length=3, max_length=3)


def bind_refs(refs, sources, *, allowed_ids=None):
    roles = {s['material_id']: s['role'] for s in sources}
    result = []
    for ref in refs:
        raw = ref.model_dump() if isinstance(ref, Citation) else ref
        if allowed_ids is not None and raw['material_id'] not in allowed_ids:
            raise ValueError('引文不属于本次输入')
        bound = original_citation(raw, sources)
        if bound is None:
            raise ValueError('上下游引文无法回到原文')
        bound['role'] = roles[bound['material_id']]
        if bound not in result:
            result.append(bound)
    return result


def bind_extraction(raw, batch, sources):
    parsed = ExtractionResponse.model_validate(raw)
    if set(parsed.checked_topics) != set(TOPICS):
        raise ValueError('条款提取主题执行字段缺失或重复')
    # 提取只引用当前批次正文，不能把其他文件或跨批相关片段冒充已读取范围。
    compact = lambda value: ''.join(value.split())
    def local_refs(values):
        refs = bind_refs(values, sources)
        if any(not any(part['material_id'] == ref['material_id'] and part['page'] == ref['page']
                       and compact(ref['quote']) in compact(part['text']) for part in batch['parts'])
               for ref in refs):
            raise ValueError('引文不在本批正文范围')
        return refs
    facts, terms = [], []
    for fact in parsed.facts:
        refs = local_refs(fact.evidence)
        # 身份字段是明确记载事实，不能由模型自行归纳出原文没有的名字。
        if not any(compact(fact.value) in compact(ref['quote']) for ref in refs):
            raise ValueError('身份字段不是所引原文中的值')
        facts.append({**fact.model_dump(), 'evidence': refs})
    for term in parsed.terms:
        row = {**term.model_dump(), 'evidence': local_refs(term.evidence)}
        row['term_id'] = digest(row)[:24]
        terms.append(row)
    return {'facts': facts, 'terms': terms}


def bind_relationship(raw, sources, facts):
    response = RelationshipResponse.model_validate(raw)
    if {row.dimension for row in response.checks} != {'project', 'parties', 'scope'}:
        raise ValueError('关系核验维度重复或缺失')
    checks = []
    for check in response.checks:
        refs = bind_refs(check.evidence, sources)
        sides = {'general' if ref['role'] == 'general' else 'sub' for ref in refs}
        if check.state != 'uncertain' and sides != {'general', 'sub'}:
            raise ValueError('关系判断缺少双侧合同事实')
        fields = {'project': {'project_name', 'contract_section'}, 'parties': {'employer', 'contractor'},
                  'scope': {'scope'}}[check.dimension]
        if check.state != 'uncertain':
            for side in ('general', 'sub'):
                eligible = [fact for fact in facts if fact['field'] in fields and any(
                    ('general' if ref['role'] == 'general' else 'sub') == side for ref in fact['evidence'])]
                if check.dimension == 'parties':
                    eligible = [fact for fact in eligible if fact['field'] == ('contractor' if side == 'general' else 'employer')]
                if not any(''.join(fact['value'].split()) in ''.join(ref['quote'].split())
                           for fact in eligible for ref in refs
                           if ('general' if ref['role'] == 'general' else 'sub') == side):
                    raise ValueError('关系判断没有引用对应维度的身份事实')
        checks.append({**check.model_dump(), 'evidence': refs})
    state = ('mismatch' if any(row['state'] == 'mismatch' for row in checks) else
             'matched' if all(row['state'] == 'matched' for row in checks) else 'uncertain')
    return {'state': state, 'checks': checks}


class TermCheck(ContractModel):
    term_id: str
    state: Literal['aligned', 'not_applicable', 'risk', 'pending']
    reason: str = Field(min_length=4, max_length=2400)
    applicability: str = Field(max_length=1600)
    conditions: str = Field(max_length=1600)
    evidence: list[Citation] = Field(max_length=12)


class ComparisonResponse(ContractModel):
    checks: list[TermCheck] = Field(max_length=6)


class UpstreamCandidate(ContractModel):
    kind: Literal['upstream_risk']
    time_scope: Literal['contract_text']
    message: str = Field(min_length=8, max_length=2400)
    evidence: list[Citation] = Field(min_length=2, max_length=12)
    basis: list = Field(max_length=0)
    conditions: str = Field(min_length=1, max_length=1600)
    applicability: str = Field(min_length=1, max_length=1600)


def validate_upstream_candidate(raw, sources):
    import re
    row = UpstreamCandidate.model_validate(raw).model_dump()
    refs = bind_refs(row['evidence'], sources)
    if not any(r['role'] == 'general' for r in refs) or not any(r['role'] in {'sub', 'labor'} for r in refs):
        raise ValueError('上下游风险必须包含总包和分包两侧原文')
    if re.search(r'已违法|构成违法|条款无效|违反.{0,40}(?:规定|法律)|不得.{0,12}(?:进场|签约)', row['message']):
        raise ValueError('合同差异不能自行升级为违法或审批指令')
    row.update(evidence=refs, kind_label=KINDS['upstream_risk'])
    row['finding_id'] = digest({'kind': row['kind'], 'evidence': refs,
                                'applicability': row['applicability'], 'conditions': row['conditions']})[:24]
    return row
