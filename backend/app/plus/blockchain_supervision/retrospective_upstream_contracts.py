"""上下游比较契约；总包是合同证据，不是法规范。"""
import re
from typing import Literal

from pydantic import Field

from .retrospective_contracts import (ALL_REPORT_DIMENSION_IDS, Citation, ContractModel, KINDS)
from .retrospective_sources import digest, original_citation

VERSION = 'bs.contract-upstream.v16'
LABOR_UPSTREAM_OWNER_SUFFIX = re.compile(
    r'[（(]发包人全称[^（）()]{0,36}[）)]与工程承包人(?:已经|已)?签订施工总承包合同')
CONTRACT_TIMING_RULE = re.compile(
    r'基准日期[：:][^。；;]{0,180}(?:截止日|签订日|签署日)前第?[0-9０-９]+天'
    r'|按天计算时间(?:的)?[，,](?:开始当天|开始日)不计入[，,]从次日开始计算')
PROHIBITION = r'(?:不得|不允许|禁止)(?P<action>[^。；;，,]{6,96})'
PROHIBITION_ACTION = re.compile(PROHIBITION)
APPROVAL_EXCEPTION = re.compile(
    r'除(?:经)?(?:发包人|业主|监理人|总承包人|承包人)(?:书面)?(?:批准|同意)(?:以)?外'
    r'[，,]' + PROHIBITION)
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
    reason: str = Field(min_length=4, max_length=1800)


class SelectedExtractionResponse(ContractModel):
    checked_topics: list[str] = Field(min_length=11, max_length=11)
    facts: list[SelectedProfileFact] = Field(max_length=24)
    terms: list[SelectedContractTerm] = Field(max_length=60)
    skipped_evidence: list[SkippedEvidence] = Field(max_length=300)


class TransportSelectedProfileFact(ContractModel):
    """Provider transport boundary; item-level business validation remains strict."""
    field: Literal['project_name', 'contract_section', 'employer', 'contractor', 'scope']
    value: str = Field(min_length=1, max_length=1200)
    evidence_ids: list[str] = Field(min_length=1, max_length=4)


class TransportSelectedContractTerm(ContractModel):
    """Accept short provider candidates so bind_selected_extraction can reject them individually."""
    topic: Literal['scope', 'price', 'payment', 'schedule', 'quality', 'safety',
                   'environment', 'wages', 'insurance', 'equipment', 'priority']
    statement: str = Field(min_length=1, max_length=1200)
    obligation_holder: Literal['employer', 'general', 'sub', 'both', 'uncertain']
    transmission: Literal['pass_through', 'reserved', 'self_perform', 'uncertain']
    conditions: str = Field(max_length=1200)
    exceptions: str = Field(max_length=1200)
    priority: str = Field(max_length=1200)
    evidence_ids: list[str] = Field(min_length=1, max_length=6)


class TransportSkippedEvidence(ContractModel):
    evidence_id: str
    reason: str = Field(min_length=1, max_length=1800)


class TransportSelectedExtractionResponse(ContractModel):
    """Transport-only schema. Strict item models are applied after transport parsing."""
    checked_topics: list[str] = Field(min_length=11, max_length=11)
    facts: list[TransportSelectedProfileFact] = Field(max_length=24)
    terms: list[TransportSelectedContractTerm] = Field(max_length=60)
    skipped_evidence: list[TransportSkippedEvidence] = Field(max_length=300)


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


def _profile_reference_supports(item, ref, sources):
    value = ''.join(item.value.split())
    quote = ''.join(ref['quote'].split())
    if not value or value not in quote:
        return False
    if item.field != 'employer' or ref['role'] != 'labor':
        return True
    # 劳务合同序言的「发包人全称」可能指与工程承包人签订总包合同的上游业主。
    # 只核对所引名称后紧邻的原文，即使模型仅引用了名称也保留这层区分。
    page = next(page for source in sources if source['material_id'] == ref['material_id']
                for page in source['pages'] if page['page'] == ref['page'])
    following = ''.join(page['text'][ref['end']:ref['end'] + 160].split())
    context = quote + following
    return any(not LABOR_UPSTREAM_OWNER_SUFFIX.match(context, occurrence.end())
               for occurrence in re.finditer(re.escape(value), quote))


def _restore_approval_exception(term):
    """保留与该项禁止行为对应的明确审批例外，不从其他义务移用。"""
    if term['exceptions']:
        return
    statement = ''.join(term['statement'].split())
    actions = [match['action'] for match in PROHIBITION_ACTION.finditer(statement)]
    if not actions:
        return
    exceptions = {
        match.group()
        for ref in term['evidence']
        for match in APPROVAL_EXCEPTION.finditer(''.join(ref['quote'].split()))
        if any(action == match['action'] or match['action'].startswith(action) for action in actions)
    }
    if len(exceptions) == 1:
        term['exceptions'] = exceptions.pop()


def bind_extraction(raw, batch, sources):
    if not isinstance(raw, dict) or any(not isinstance(raw.get(key), list) or len(raw[key]) > limit
                                       for key, limit in [('facts', 24), ('terms', 60)]):
        raise ValueError('条款提取外层结构不完整')
    parsed = ExtractionResponse.model_validate({**raw, 'facts': [], 'terms': []})
    if set(parsed.checked_topics) != set(TOPICS):
        raise ValueError('条款提取主题执行字段缺失或重复')
    # 提取只引用当前批次正文，不能把其他文件或跨批相关片段冒充已读取范围。
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
                if kind == 'facts' and not any(_profile_reference_supports(item, ref, sources) for ref in refs):
                    raise ValueError('所引原文无法支持对应的身份字段或合同角色')
                row = {**item.model_dump(), 'evidence': refs}
                if kind == 'terms':
                    _restore_approval_exception(row)
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
    parsed = TransportSelectedExtractionResponse.model_validate(raw)
    if set(parsed.checked_topics) != set(TOPICS):
        raise ValueError('条款提取主题执行字段缺失或重复')
    result = {'facts': [], 'terms': [], 'rejected': []}
    referenced = set()
    original_text = {key: re.sub(r'[\W_]+', '', ref['quote']) for key, ref in catalog.items()}
    prior_text = [re.sub(r'[\W_]+', '', part['text']) for part in batch.get('context', [])]
    for kind, schema in [('facts', SelectedProfileFact), ('terms', SelectedContractTerm)]:
        for index, value in enumerate(raw[kind]):
            try:
                item = schema.model_validate(value).model_dump()
                ids = item.pop('evidence_ids')
                if len(ids) != len(set(ids)) or any(key not in catalog for key in ids):
                    raise ValueError('证据编号重复或不属于本批正文')
                if kind == 'terms':
                    statement = re.sub(r'[\W_]+', '', item['statement'])
                    # 只有一段较长表述确实来自别处时才拒绝。释义仍由后续语义核查处理。
                    if (len(statement) >= 24 and not any(statement in original_text[key] for key in ids)
                            and (any(statement in text for text in original_text.values())
                                 or any(statement in text for text in prior_text))):
                        result['rejected'].append({'kind': kind, 'index': index, 'error_type': 'ValueError',
                                                   'code': 'wrong_term_evidence'})
                        continue
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
    skipped, skipped_rows, invalid_skipped = set(), [], 0
    skipped_timing = set()
    for row in raw.get('skipped_evidence', []):
        try:
            strict_row = SkippedEvidence.model_validate(row)
        except (ValueError, TypeError, KeyError):
            invalid_skipped += 1
            continue
        key = strict_row.evidence_id
        if key not in catalog or key in claimed or key in skipped or key in skipped_timing:
            invalid_skipped += 1
            continue
        if CONTRACT_TIMING_RULE.search(''.join(catalog[key]['quote'].split())):
            skipped_timing.add(key)
            continue
        skipped.add(key)
        skipped_rows.append(strict_row.model_dump())
    result['skipped_evidence'] = skipped_rows
    result['unaccounted_evidence_ids'] = sorted(set(catalog) - referenced - skipped)
    if result['unaccounted_evidence_ids'] or invalid_skipped:
        result['state'] = 'partial'
        result.setdefault('limitations', []).append('部分原文片段未能可靠归入提取或略过记录，不能确认已完整核对。')
        if invalid_skipped:
            result['rejected_skipped_count'] = invalid_skipped
    if skipped_timing:
        result['skipped_contract_timing_ids'] = sorted(skipped_timing)
        result.setdefault('limitations', []).append('合同日期或期限计算约定被略过，仍需提取。')
    if len(raw['facts']) == 24 or len(raw['terms']) == 60:
        result['state'] = 'partial'
        result.setdefault('limitations', []).append('单批提取达到输出项数上限，尚不能确认所有约定已提取。')
    return result


def reference_supports_fact(ref, fact):
    """同一物理页的重叠原文必须实际包含该字段值。"""
    identity = (ref['material_id'], ref['page'])
    value = ''.join(fact['value'].split())
    for original in fact['evidence']:
        if (original['material_id'], original['page']) != identity:
            continue
        start = max(ref['start'], original['start'])
        end = min(ref['end'], original['end'])
        if start < end and value in ''.join(ref['quote'][start - ref['start']:end - ref['start']].split()):
            return True
    return False


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
                if not any(reference_supports_fact(ref, fact)
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
    review_dimension: Literal['scope_and_qualification', 'procedure_and_subcontracting', 'key_staffing',
        'equipment_and_lifting', 'hazardous_work_and_plans', 'contract_terms_and_upstream',
        'quality_safety_schedule_environment', 'commercial_terms_and_insurance', 'labor_and_wage_protection',
        *ALL_REPORT_DIMENSION_IDS]
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
