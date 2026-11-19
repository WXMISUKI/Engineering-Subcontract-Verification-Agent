"""上下游比较契约；总包是合同证据，不是法规范。"""
from typing import Literal

from pydantic import Field

from .retrospective_contracts import Citation, ContractModel, KINDS
from .retrospective_sources import digest, original_citation

VERSION = 'bs.contract-upstream.v6'
TOPICS = ('scope', 'price', 'payment', 'schedule', 'quality', 'safety',
          'environment', 'wages', 'insurance', 'equipment', 'priority')


class ProfileFactFields(ContractModel):
    field: Literal['project_name', 'contract_section', 'employer', 'contractor', 'scope']
    value: str = Field(min_length=2, max_length=1200)


class ProfileFact(ProfileFactFields):
    evidence: list[Citation] = Field(min_length=1, max_length=4)


class SelectedProfileFact(ProfileFactFields):
    evidence_ids: list[str] = Field(min_length=1, max_length=4)


class ContractTermFields(ContractModel):
    topic: Literal['scope', 'price', 'payment', 'schedule', 'quality', 'safety',
                   'environment', 'wages', 'insurance', 'equipment', 'priority']
    statement: str = Field(min_length=4, max_length=1200)
    obligation_holder: Literal['employer', 'general', 'sub', 'both', 'uncertain']
    transmission: Literal['pass_through', 'reserved', 'self_perform', 'uncertain']
    conditions: str = Field(max_length=1200)
    exceptions: str = Field(max_length=1200)
    priority: str = Field(max_length=1200)


class ContractTerm(ContractTermFields):
    evidence: list[Citation] = Field(min_length=1, max_length=6)


class SelectedContractTerm(ContractTermFields):
    evidence_ids: list[str] = Field(min_length=1, max_length=6)


class ExtractionResponse(ContractModel):
    checked_topics: list[str] = Field(min_length=11, max_length=11)
    facts: list[ProfileFact] = Field(max_length=24)
    terms: list[ContractTerm] = Field(max_length=60)


class SkippedEvidence(ContractModel):
    evidence_id: str
    reason: str = Field(min_length=4, max_length=600)


class SelectedExtractionResponse(ContractModel):
    checked_topics: list[str] = Field(min_length=11, max_length=11)
    facts: list[SelectedProfileFact] = Field(max_length=24)
    terms: list[SelectedContractTerm] = Field(max_length=60)
    skipped_evidence: list[SkippedEvidence] = Field(max_length=300)


class RelationshipCheck(ContractModel):
    dimension: Literal['project', 'parties', 'scope']
    state: Literal['matched', 'mismatch', 'uncertain']
    reason: str = Field(min_length=4, max_length=1600)
    evidence: list[Citation] = Field(max_length=8)


class RelationshipResponse(ContractModel):
    checks: list[RelationshipCheck] = Field(min_length=3, max_length=3)


class SelectedRelationshipCheck(ContractModel):
    dimension: Literal['project', 'parties', 'scope']
    state: Literal['matched', 'mismatch', 'uncertain']
    reason: str = Field(min_length=4, max_length=1600)
    evidence_ids: list[str] = Field(max_length=8)


class SelectedRelationshipResponse(ContractModel):
    checks: list[SelectedRelationshipCheck] = Field(min_length=3, max_length=3)


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
    if not isinstance(raw, dict) or any(not isinstance(raw.get(key), list) or len(raw[key]) > limit
                                       for key, limit in [('facts', 24), ('terms', 60)]):
        raise ValueError('条款提取外层结构不完整')
    parsed = ExtractionResponse.model_validate({**raw, 'facts': [], 'terms': []})
    if set(parsed.checked_topics) != set(TOPICS):
        raise ValueError('条款提取主题执行字段缺失或重复')
    # 提取只引用当前批次正文，不能把其他文件或跨批相关片段冒充已读取范围。
    compact = lambda value: ''.join(value.split())
    def local_refs(values):
        refs = bind_refs(values, sources)
        for ref in refs:
            parts = [part for part in batch['parts'] if part['material_id'] == ref['material_id'] and part['page'] == ref['page']]
            # 提示正文合并同页连续条款；合法引文可以跨片段，但每个实字仍须在本批范围。
            if any(not char.isspace() and not any(part['start'] <= ref['start'] + offset < part['end'] for part in parts)
                   for offset, char in enumerate(ref['quote'])):
                raise ValueError('引文不在本批正文范围')
        return refs
    facts, terms, rejected = [], [], []
    for kind, schema, output in [('facts', ProfileFact, facts), ('terms', ContractTerm, terms)]:
        for index, value in enumerate(raw[kind]):
            try:
                item = schema.model_validate(value)
                refs = local_refs(item.evidence)
                if kind == 'facts' and not any(compact(item.value) in compact(ref['quote']) for ref in refs):
                    raise ValueError('身份字段不是所引原文中的值')
                row = {**item.model_dump(), 'evidence': refs}
                if kind == 'terms':
                    row['term_id'] = digest(row)[:24]
                output.append(row)
            except (ValueError, TypeError, KeyError) as exc:
                # 坏引文不进入比较；有效候选保留，不把部分提取伪装为全批完成。
                rejected.append({'kind': kind, 'index': index, 'error_type': type(exc).__name__})
    state = ('partial' if facts or terms else 'failed') if rejected else 'completed'
    return {'state': state, 'facts': facts, 'terms': terms, 'rejected': rejected}


def bind_selected_extraction(raw, batch, sources, catalog):
    """逐项恢复本批已有原文，复用严格来源/局部范围校验；一项错误不丢整批。"""
    if not isinstance(raw, dict) or any(not isinstance(raw.get(key), list) or len(raw[key]) > limit
                                       for key, limit in [('facts', 24), ('terms', 60)]):
        raise ValueError('条款提取外层结构不完整')
    parsed = SelectedExtractionResponse.model_validate({**raw, 'facts': [], 'terms': []})
    if set(parsed.checked_topics) != set(TOPICS):
        raise ValueError('条款提取主题执行字段缺失或重复')
    result = {'facts': [], 'terms': [], 'rejected': []}
    referenced = set()
    for kind, schema in [('facts', SelectedProfileFact), ('terms', SelectedContractTerm)]:
        for index, value in enumerate(raw[kind]):
            try:
                item = schema.model_validate(value).model_dump()
                ids = item.pop('evidence_ids')
                if len(ids) != len(set(ids)) or any(key not in catalog for key in ids):
                    raise ValueError('证据编号重复或不属于本批正文')
                item['evidence'] = [dict(catalog[key]) for key in ids]
                bound = bind_extraction({'checked_topics': parsed.checked_topics,
                    'facts': [item] if kind == 'facts' else [],
                    'terms': [item] if kind == 'terms' else []}, batch, sources)
                result[kind].extend(bound[kind])
                if bound[kind]:
                    referenced.update(ids)
                result['rejected'].extend({**row, 'index': index} for row in bound['rejected'])
            except (ValueError, TypeError, KeyError) as exc:
                result['rejected'].append({'kind': kind, 'index': index, 'error_type': type(exc).__name__})
    result['state'] = ('partial' if result['facts'] or result['terms'] else 'failed') if result['rejected'] else 'completed'
    claimed = {key for kind in ('facts', 'terms') for row in raw[kind]
               if isinstance(row, dict) and isinstance(row.get('evidence_ids'), list)
               for key in row['evidence_ids'] if isinstance(key, str)}
    skipped = [row.evidence_id for row in parsed.skipped_evidence]
    if len(skipped) != len(set(skipped)) or any(key not in catalog or key in claimed for key in skipped):
        raise ValueError('提取与略过的原文编号冲突、重复或不属于当前批次')
    result['skipped_evidence'] = [row.model_dump() for row in parsed.skipped_evidence]
    result['unaccounted_evidence_ids'] = sorted(set(catalog) - referenced - set(skipped))
    if result['unaccounted_evidence_ids']:
        result['state'] = 'partial'
        result.setdefault('limitations', []).append('部分原文片段没有提取或略过记录，不能确认已完整核对。')
    if len(raw['facts']) == 24 or len(raw['terms']) == 60:
        result['state'] = 'partial'
        result.setdefault('limitations', []).append('单批提取达到输出项数上限，尚不能确认所有约定已提取。')
    return result


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


class SelectedTermCheck(ContractModel):
    term_id: str
    state: Literal['aligned', 'not_applicable', 'risk', 'pending']
    reason: str = Field(min_length=4, max_length=2400)
    applicability: str = Field(max_length=1600)
    conditions: str = Field(max_length=1600)
    evidence_ids: list[str] = Field(max_length=12)


class SelectedComparisonResponse(ContractModel):
    checks: list[SelectedTermCheck] = Field(max_length=6)


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
