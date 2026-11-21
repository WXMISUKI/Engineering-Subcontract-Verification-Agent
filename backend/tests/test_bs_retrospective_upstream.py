import asyncio
import json

import pytest

from app.plus.blockchain_supervision.retrospective_sources import analysis_batches
from app.plus.blockchain_supervision.retrospective_upstream_contracts import (
    TOPICS, bind_extraction, bind_refs, bind_relationship, validate_upstream_candidate,
)


def sources():
    return [{'material_id': mid, 'file_name': name, 'role': role, 'relation': 'main',
             'total_pages': 1, 'pages': [{'page': 1, 'text': text, 'readable': True}],
             'reading_coverage': {'reading_complete': True, 'blank_pages': []}}
            for mid, name, role, text in [
                (1, '总包.pdf', 'general', '工程名称：嘉松工程。承包人：建工公司。范围：桥梁吊装。质量检测由业主指定机构完成，承包人仅负责配合。'),
                (2, '分包.pdf', 'sub', '总包工程名称：嘉松工程。发包人：建工公司。范围：桥梁吊装。质量检测由分包人委托，全部检测费用由分包人承担。')]]


def ref(mid, quote):
    return {'material_id': mid, 'page': 1, 'quote': quote, 'clause': ''}


def candidate():
    return {'kind': 'upstream_risk', 'time_scope': 'contract_text',
            'review_dimension': 'schedule_quality_safety',
            'message': '总包约定由业主指定检测机构，分包却约定自行委托，同一检测对象的委托主体及费用边界尚需明确。',
            'conditions': '两侧约定涉及同一检测对象且无另行变更。', 'applicability': '同一桥梁吊装工程的检测责任。',
            'basis': [], 'evidence': [ref(1, '质量检测由业主指定机构完成，承包人仅负责配合。'),
                                     ref(2, '质量检测由分包人委托，全部检测费用由分包人承担。')]}


def test_extraction_restores_exact_source_and_retains_conditions():
    term = {'topic': 'quality', 'statement': '检测由业主指定机构完成', 'obligation_holder': 'employer',
            'transmission': 'reserved', 'conditions': '同一检测对象', 'exceptions': '另行约定除外',
            'priority': '专用条款', 'evidence': [candidate()['evidence'][0]]}
    raw = {'checked_topics': list(TOPICS), 'facts': [{'field': 'project_name', 'value': '嘉松工程', 'evidence': [ref(1, '工程名称：嘉松工程。')]}],
           'terms': [term]}
    bound = bind_extraction(raw, analysis_batches(sources()[:1])[0], sources())
    assert bound['facts'][0]['evidence'][0]['role'] == 'general'
    assert bound['terms'][0]['exceptions'] == '另行约定除外'
    assert bound['terms'][0]['term_id']
    raw['facts'][0]['value'] = '另一个工程'
    partial = bind_extraction(raw, analysis_batches(sources()[:1])[0], sources())
    assert partial['state'] == 'partial' and not partial['facts']
    assert partial['terms'] == bound['terms'] and len(partial['rejected']) == 1


def test_extraction_cannot_borrow_the_other_contract():
    rejected = bind_extraction({'checked_topics': list(TOPICS), 'facts': [], 'terms': [{'topic': 'quality', 'statement': '检测责任不同',
            'obligation_holder': 'sub', 'transmission': 'pass_through', 'conditions': '', 'exceptions': '',
            'priority': '', 'evidence': [candidate()['evidence'][1]]}]}, analysis_batches(sources()[:1])[0], sources())
    assert rejected['state'] == 'failed' and not rejected['terms']


@pytest.mark.parametrize('reference', ['full_page', 'owner_name', 'direct_signatory', 'same_entity'])
def test_labor_employer_fact_keeps_direct_signatory_separate_from_recital_owner(reference):
    from app.plus.blockchain_supervision.retrospective_upstream_contracts import bind_selected_extraction

    src = sources()[1:]
    src[0]['role'] = 'labor'
    owner, signatory = '沿江工程建设中心', '沿江路桥集团有限公司'
    if reference == 'same_entity':
        owner = signatory
    signing_line = f'工程承包人（专业或专项工程承（分）包人）：{signatory}'
    recital = f'鉴于{owner}（发包人全称，以下简称“发包人”）与工程承包人已经签订施工总承包合同。'
    text = f'{signing_line}\n劳务合作人：桥梁劳务有限公司。\n{recital}'
    src[0]['pages'][0]['text'] = text
    quote, value = {
        'full_page': (text, owner),
        'owner_name': (owner, owner),
        'direct_signatory': (signing_line, signatory),
        'same_entity': (text, signatory),
    }[reference]
    batch = analysis_batches(src)[0]
    raw = {'checked_topics': list(TOPICS), 'facts': [
        {'field': 'employer', 'value': value, 'evidence_ids': ['R001']},
    ], 'terms': [], 'skipped_evidence': []}
    bound = bind_selected_extraction(raw, batch, src, {'R001': ref(2, quote)})
    if reference in {'full_page', 'owner_name'}:
        assert bound['state'] == 'partial'
        assert not bound['facts']
        assert bound['rejected'][0]['kind'] == 'facts'
    else:
        assert bound['state'] == 'completed'
        assert bound['facts'][0]['value'] == signatory


def test_invalid_summary_quote_does_not_discard_valid_identity_or_become_evidence():
    raw = {'checked_topics': list(TOPICS), 'facts': [
        {'field': 'project_name', 'value': '嘉松工程', 'evidence': [ref(1, '工程名称：嘉松工程。')]}],
        'terms': [{'topic': 'quality', 'statement': '检测责任与配合范围', 'obligation_holder': 'employer',
                   'transmission': 'reserved', 'conditions': '', 'exceptions': '', 'priority': '',
                   'evidence': [ref(1, '质量检测由业主...承包人仅负责配合。')]}]}
    result = bind_extraction(raw, analysis_batches(sources()[:1])[0], sources())
    assert result['state'] == 'partial' and result['facts'][0]['value'] == '嘉松工程'
    assert not result['terms'] and result['rejected'][0]['kind'] == 'terms'


def test_same_page_quote_may_cross_contiguous_parts_but_not_unprovided_text():
    src = sources()[:1]
    text = '1.1 工程名称：嘉松工程。\n1.2 承包人：建工公司。'
    src[0]['pages'][0]['text'] = text
    batch = analysis_batches(src)[0]
    assert len(batch['parts']) > 1
    raw = {'checked_topics': list(TOPICS), 'facts': [
        {'field': 'project_name', 'value': '嘉松工程', 'evidence': [ref(1, text)]}], 'terms': []}
    assert bind_extraction(raw, batch, src)['state'] == 'completed'
    omitted = {**batch, 'parts': batch['parts'][:1]}
    assert bind_extraction(raw, omitted, src)['state'] == 'failed'


def test_extraction_selects_local_original_text_and_isolates_foreign_ids():
    from app.plus.blockchain_supervision.retrospective_upstream import extraction_catalog
    from app.plus.blockchain_supervision.retrospective_upstream_contracts import bind_selected_extraction
    src = sources()[:1]
    batch = analysis_batches(src)[0]
    catalog = extraction_catalog(batch)
    key = next(iter(catalog))
    raw = {'checked_topics': list(TOPICS), 'facts': [
        {'field': 'project_name', 'value': '嘉松工程', 'evidence_ids': [key]},
        {'field': 'employer', 'value': '虚构单位', 'evidence_ids': [key]}],
        'skipped_evidence': [], 'terms': [{'topic': 'quality', 'statement': '检测对象及费用约定',
            'obligation_holder': 'general', 'transmission': 'self_perform',
            'conditions': '', 'exceptions': '', 'priority': '', 'evidence_ids': ['foreign']}]}
    result = bind_selected_extraction(raw, batch, src, catalog)
    assert result['state'] == 'partial' and len(result['rejected']) == 2
    assert result['facts'][0]['evidence'][0]['quote'] == catalog[key]['quote']
    assert result['facts'][0]['value'] == '嘉松工程'
    # 即使目录被错误混入其他批次片段，仍由原局部范围校验拒绝。
    catalog['foreign'] = {'material_id': 2, 'page': 1, 'quote': sources()[1]['pages'][0]['text'], 'clause': ''}
    result = bind_selected_extraction(raw, batch, sources(), catalog)
    assert not result['terms']


def test_transport_short_term_is_rejected_individually_and_valid_term_survives():
    from app.plus.blockchain_supervision.retrospective_upstream import extraction_catalog
    from app.plus.blockchain_supervision.retrospective_upstream_contracts import bind_selected_extraction
    src = sources()[:1]
    batch = analysis_batches(src)[0]
    catalog = extraction_catalog(batch)
    key = next(iter(catalog))
    valid = {'topic': 'quality', 'statement': '检测对象及费用约定',
             'obligation_holder': 'general', 'transmission': 'self_perform',
             'conditions': '', 'exceptions': '', 'priority': '', 'evidence_ids': [key]}
    result = bind_selected_extraction(
        {'checked_topics': list(TOPICS), 'facts': [], 'terms': [valid, {**valid, 'statement': 'x'}],
         'skipped_evidence': []}, batch, src, catalog)
    assert result['state'] == 'partial'
    assert len(result['terms']) == 1 and len(result['rejected']) == 1


def test_transport_short_fact_value_is_rejected_individually_and_valid_fact_survives():
    from app.plus.blockchain_supervision.retrospective_upstream import extraction_catalog
    from app.plus.blockchain_supervision.retrospective_upstream_contracts import bind_selected_extraction
    src = sources()[:1]
    batch = analysis_batches(src)[0]
    catalog = extraction_catalog(batch)
    key = next(iter(catalog))
    valid = {'field': 'project_name', 'value': '嘉松工程', 'evidence_ids': [key]}
    result = bind_selected_extraction(
        {'checked_topics': list(TOPICS), 'facts': [valid, {**valid, 'value': 'x'}], 'terms': [],
         'skipped_evidence': []}, batch, src, catalog)
    assert result['state'] == 'partial'
    assert len(result['facts']) == 1 and len(result['rejected']) == 1


@pytest.mark.parametrize('wrong_source', ['another_id', 'prior_context'])
def test_term_copied_from_another_source_cannot_use_a_valid_unrelated_id(wrong_source):
    from app.plus.blockchain_supervision.retrospective_upstream import extraction_catalog
    from app.plus.blockchain_supervision.retrospective_upstream_contracts import bind_selected_extraction

    src = sources()[:1]
    validation = '承包人应按规定设置试块标准养护室并承担现场试块送检费用'
    payment = '承包人应将运输及现场协调费用均计入对应工程量清单综合单价'
    test_text = '1.1 试验检测\n' + validation + '。\n1.2 计价说明\n' + payment + '。'
    src[0]['pages'][0]['text'] = test_text
    batch = analysis_batches(src)[0]
    catalog = extraction_catalog(batch)
    [target_id, payment_id] = catalog
    if wrong_source == 'prior_context':
        batch = {**batch, 'parts': batch['parts'][:1], 'context': batch['parts'][1:]}
        catalog.pop(payment_id)
    item = {'topic': 'quality', 'statement': '施工单位负责设置试块养护室', 'obligation_holder': 'general',
            'transmission': 'self_perform', 'conditions': '', 'exceptions': '', 'priority': '',
            'evidence_ids': [target_id]}
    valid = item if wrong_source == 'prior_context' else {
        **item, 'statement': '运输费与协调费计入单价', 'evidence_ids': [payment_id]}
    result = bind_selected_extraction({
        'checked_topics': list(TOPICS), 'facts': [], 'terms': [{**item, 'statement': payment}, valid],
        'skipped_evidence': [],
    }, batch, src, catalog)
    assert result['state'] == 'partial'
    assert [row['statement'] for row in result['terms']] == [valid['statement']]
    assert [row['code'] for row in result['rejected']] == ['wrong_term_evidence']


def test_transport_short_skip_reason_is_rejected_and_marks_partial():
    from app.plus.blockchain_supervision.retrospective_upstream import extraction_catalog
    from app.plus.blockchain_supervision.retrospective_upstream_contracts import bind_selected_extraction
    src = sources()[:1]
    batch = analysis_batches(src)[0]
    catalog = extraction_catalog(batch)
    key = next(iter(catalog))
    result = bind_selected_extraction(
        {'checked_topics': list(TOPICS), 'facts': [], 'terms': [],
         'skipped_evidence': [{'evidence_id': key, 'reason': 'x'}]}, batch, src, catalog)
    assert result['state'] == 'partial'
    assert result['rejected_skipped_count'] == 1


def test_extraction_preserves_explicit_approval_exception_for_the_same_prohibition():
    from app.plus.blockchain_supervision.retrospective_upstream import extraction_catalog
    from app.plus.blockchain_supervision.retrospective_upstream_contracts import bind_selected_extraction

    src = sources()[:1]
    src[0]['pages'][0]['text'] = (
        '承包人应设置标准养护室。除发包人批准以外，不允许租用民房或住宅用房作养护室。'
        '试件养护时间不得少于21天。')
    batch = analysis_batches(src)[0]
    catalog = extraction_catalog(batch)
    [key] = catalog
    term = {'topic': 'quality', 'obligation_holder': 'general', 'transmission': 'self_perform',
            'conditions': '', 'exceptions': '', 'priority': '', 'evidence_ids': [key]}
    result = bind_selected_extraction({
        'checked_topics': list(TOPICS), 'facts': [], 'skipped_evidence': [],
        'terms': [{**term, 'statement': '不得租用民房或住宅用房。'},
                  {**term, 'statement': '试件养护时间不得少于21天。'}],
    }, batch, src, catalog)
    assert result['state'] == 'completed'
    assert result['terms'][0]['exceptions'] == '除发包人批准以外，不允许租用民房或住宅用房作养护室'
    assert result['terms'][1]['exceptions'] == ''


def test_extraction_catalog_preserves_each_substantive_original_fragment():
    from app.plus.blockchain_supervision.retrospective_upstream import extraction_catalog
    src = sources()[:1]
    src[0]['pages'][0]['text'] = '检测工作由业主负责。' * 180 + '\n由承包人负责配合并报价。'
    batch = analysis_batches(src)[0]
    refs = list(extraction_catalog(batch).values())
    assert ''.join(row['quote'] for row in refs) == src[0]['pages'][0]['text']
    assert len(refs) > 1


def test_extraction_catalog_omits_heading_with_footer_but_preserves_price_terms():
    from app.plus.blockchain_supervision.retrospective_upstream import extraction_catalog

    src = sources()[:1]
    title_with_footer = '第四条 合同价款\n\n1'
    price_term = '第四条 合同价款\n\n1. 合同价款为人民币100万元。'
    src[0]['pages'][0]['text'] = title_with_footer
    assert not extraction_catalog(analysis_batches(src)[0])
    src[0]['pages'][0]['text'] = price_term
    entries = extraction_catalog(analysis_batches(src)[0])
    assert ''.join(row['quote'] for row in entries.values()) == price_term


def test_extraction_catalog_separates_numbered_obligations_without_changing_text():
    from app.plus.blockchain_supervision.retrospective_upstream import extraction_catalog
    text = '(19) 检测由业主委托，报价不含检测费，承包人承担配合费。\n\n(20) 供电申请配合费由承包人承担。'
    src = sources()[:1]
    src[0]['pages'][0]['text'] = text
    refs = list(extraction_catalog(analysis_batches(src)[0]).values())
    assert len(refs) == 2
    assert ''.join(row['quote'] for row in refs) == text
    assert all(not ('(19)' in row['quote'] and '(20)' in row['quote']) for row in refs)


def test_unaccounted_or_rejected_evidence_is_not_completed_coverage():
    from app.plus.blockchain_supervision.retrospective_upstream import extraction_catalog
    from app.plus.blockchain_supervision.retrospective_upstream_contracts import bind_selected_extraction
    src = sources()[:1]
    batch = analysis_batches(src)[0]
    catalog = extraction_catalog(batch)
    key = next(iter(catalog))
    raw = {'checked_topics': list(TOPICS), 'facts': [], 'terms': [], 'skipped_evidence': []}
    result = bind_selected_extraction(raw, batch, src, catalog)
    assert result['state'] == 'partial' and key in result['unaccounted_evidence_ids']
    raw['facts'] = [{'field': 'employer', 'value': '虚构单位', 'evidence_ids': [key]}]
    result = bind_selected_extraction(raw, batch, src, catalog)
    assert result['rejected'] and key in result['unaccounted_evidence_ids']
    raw['facts'] = []
    raw['skipped_evidence'] = [{'evidence_id': key, 'reason': '本片段无独立的身份或义务'}]
    result = bind_selected_extraction(raw, batch, src, catalog)
    assert result['state'] == 'completed' and not result['unaccounted_evidence_ids']


@pytest.mark.parametrize(('quote', 'incomplete'), [
    ('1.1.24 基准日期：直接分包工程以分包合同签订日前第 28 天的日期为基准日期。', True),
    ('1.1.25 日：合同中按天计算时间的，开始当天不计入，从次日开始计算，'
     '期限最后一天的截止时间为当天24:00时。', True),
    ('1.1.17 施工场地：是指用于分包工程施工的场所。', False),
])
def test_numbered_contract_timing_rules_cannot_disappear_as_definitions(quote, incomplete):
    from app.plus.blockchain_supervision.retrospective_upstream import extraction_catalog
    from app.plus.blockchain_supervision.retrospective_upstream_contracts import bind_selected_extraction

    src = sources()[:1]
    src[0]['pages'][0]['text'] = quote
    batch = analysis_batches(src)[0]
    catalog = extraction_catalog(batch)
    [evidence_id] = catalog
    result = bind_selected_extraction({
        'checked_topics': list(TOPICS), 'facts': [], 'terms': [],
        'skipped_evidence': [{'evidence_id': evidence_id, 'reason': '仅为定义，无实质义务。'}],
    }, batch, src, catalog)
    if incomplete:
        assert result['state'] == 'partial'
        assert result['unaccounted_evidence_ids'] == result['skipped_contract_timing_ids'] == [evidence_id]
    else:
        assert result['state'] == 'completed' and not result['unaccounted_evidence_ids']


@pytest.mark.parametrize('fault', ['unknown', 'duplicate', 'referenced'])
def test_invalid_skip_accounting_marks_extraction_partial_without_discarding_valid_rows(fault):
    from app.plus.blockchain_supervision.retrospective_upstream import extraction_catalog
    from app.plus.blockchain_supervision.retrospective_upstream_contracts import bind_selected_extraction
    src = sources()[:1]
    batch = analysis_batches(src)[0]
    catalog = extraction_catalog(batch)
    key = next(iter(catalog))
    skip = {'evidence_id': 'foreign' if fault == 'unknown' else key, 'reason': '本片段无独立的身份或义务'}
    raw = {'checked_topics': list(TOPICS), 'facts': [], 'terms': [], 'skipped_evidence': [skip]}
    if fault == 'duplicate':
        raw['skipped_evidence'].append(dict(skip))
    if fault == 'referenced':
        raw['facts'].append({'field': 'project_name', 'value': '嘉松工程', 'evidence_ids': [key]})
    result = bind_selected_extraction(raw, batch, src, catalog)
    assert result['state'] == 'partial'
    assert result['rejected_skipped_count'] == 1
    assert len(result['facts']) == (1 if fault == 'referenced' else 0)


def test_extraction_output_limit_does_not_claim_all_obligations_extracted():
    from app.plus.blockchain_supervision.retrospective_upstream import extraction_catalog
    from app.plus.blockchain_supervision.retrospective_upstream_contracts import bind_selected_extraction
    src = sources()[:1]
    batch = analysis_batches(src)[0]
    catalog = extraction_catalog(batch)
    fact = {'field': 'project_name', 'value': '嘉松工程', 'evidence_ids': [next(iter(catalog))]}
    raw = {'checked_topics': list(TOPICS), 'facts': [fact] * 24, 'terms': [], 'skipped_evidence': []}
    result = bind_selected_extraction(raw, batch, src, catalog)
    assert result['state'] == 'partial' and '上限' in result['limitations'][0]


@pytest.mark.parametrize('same_page', [True, False])
def test_relationship_cannot_transfer_a_party_role_to_another_occurrence(same_page):
    src = sources()
    exact_ref = ref(2, '发包人：建工公司。')
    wrong_quote = '附件中的供应商：建工公司。'
    if same_page:
        src[1]['pages'][0]['text'] += '\n' + wrong_quote
    else:
        src[1]['pages'].append({'page': 2, 'text': wrong_quote, 'readable': True})
    wrong_ref = {**ref(2, wrong_quote), 'page': 1 if same_page else 2}
    facts = [
        {'field': 'contractor', 'value': '建工公司', 'evidence': bind_refs([ref(1, '承包人：建工公司。')], src)},
        {'field': 'employer', 'value': '建工公司', 'evidence': bind_refs([exact_ref], src)},
    ]
    checks = [
        {'dimension': 'project', 'state': 'uncertain', 'reason': '本场景暂不核对项目。', 'evidence': []},
        {'dimension': 'scope', 'state': 'uncertain', 'reason': '本场景暂不核对范围。', 'evidence': []},
        {'dimension': 'parties', 'state': 'matched', 'reason': '总包承包人与分包发包人名称一致。',
         'evidence': [ref(1, '承包人：建工公司。'), exact_ref]},
    ]
    valid = bind_relationship({'checks': checks}, src, facts)
    assert valid['checks'][2]['state'] == 'matched'
    checks[2]['evidence'][1] = wrong_ref
    with pytest.raises(ValueError, match='对应维度的身份事实'):
        bind_relationship({'checks': checks}, src, facts)


def test_relationship_requires_all_dimensions_and_both_sides():
    src = sources()
    dims = {'project': ('project_name', 'project_name', '嘉松工程'),
            'parties': ('contractor', 'employer', '建工公司'), 'scope': ('scope', 'scope', '桥梁吊装')}
    facts = [{'field': field, 'value': value, 'evidence': bind_refs([ref(mid, value)], src)}
             for left, right, value in dims.values()
             for field, mid in [(left, 1), (right, 2)]]
    checks = [{'dimension': dim, 'state': 'matched', 'reason': '同一项目主体及施工范围相衔接。',
               'evidence': [ref(1, value), ref(2, value)]} for dim, (_, _, value) in dims.items()]
    assert bind_relationship({'checks': checks}, sources(), facts)['state'] == 'matched'
    checks[1]['state'] = 'mismatch'
    assert bind_relationship({'checks': checks}, sources(), facts)['state'] == 'mismatch'
    checks[1]['evidence'] = [ref(1, '嘉松工程'), ref(2, '嘉松工程')]
    with pytest.raises(ValueError):
        bind_relationship({'checks': checks}, sources(), facts)
    checks[1]['evidence'] = checks[1]['evidence'][:1]
    with pytest.raises(ValueError):
        bind_relationship({'checks': checks}, sources(), facts)


def test_scope_containment_accepts_distinct_original_ranges_without_admission_documents():
    src = sources()
    src[0]['pages'][0]['text'] = '嘉松工程。建工公司。范围：桥梁及附属工程。'
    src[1]['pages'][0]['text'] = '嘉松工程。建工公司。范围：桥梁预制构件安装。'
    dims = [('project', 'project_name', 'project_name', '嘉松工程', '嘉松工程'),
            ('parties', 'contractor', 'employer', '建工公司', '建工公司'),
            ('scope', 'scope', 'scope', '桥梁及附属工程', '桥梁预制构件安装')]
    facts = [{'field': field, 'value': value, 'evidence': bind_refs([ref(mid, value)], src)}
             for _, left, right, parent, child in dims
             for field, value, mid in [(left, parent, 1), (right, child, 2)]]
    checks = [{'dimension': dim, 'state': 'matched', 'reason': '双侧工程内容支持子项包含。',
               'evidence': [ref(1, parent), ref(2, child)]}
              for dim, _, _, parent, child in dims]
    assert bind_relationship({'checks': checks}, src, facts)['state'] == 'matched'


@pytest.mark.parametrize('fault', ['foreign', 'duplicate'])
def test_selected_evidence_rejects_unknown_or_repeated_ids(fault):
    from app.plus.blockchain_supervision.retrospective_upstream import evidence_catalog, resolve_selected
    from app.plus.blockchain_supervision.retrospective_upstream_contracts import SelectedComparisonResponse
    catalog = evidence_catalog([{'evidence': candidate()['evidence']}])
    first = next(iter(catalog))
    raw = {'checks': [{'term_id': 'quality', 'state': 'risk', 'reason': '同一检测对象责任边界不同。',
        'applicability': '相同对象', 'conditions': '无变更',
        'evidence_ids': ['foreign'] if fault == 'foreign' else [first, first]}]}
    with pytest.raises(ValueError, match='证据编号'):
        resolve_selected(raw, SelectedComparisonResponse, catalog)
    raw['checks'][0]['evidence_ids'] = list(catalog)
    assert resolve_selected(raw, SelectedComparisonResponse, catalog)['checks'][0]['evidence'] == candidate()['evidence']


@pytest.mark.parametrize('fault', ['one_side', 'fabricated', 'law', 'current', 'basis'])
def test_upstream_finding_rejects_unverifiable_or_wrong_semantics(fault):
    row = candidate()
    if fault == 'one_side': row['evidence'] = row['evidence'][:1] * 2
    elif fault == 'fabricated': row['evidence'][1]['quote'] = '原文没有的约定'
    elif fault == 'law': row['message'] += '该约定构成违法。'
    elif fault == 'current': row['time_scope'] = 'current'
    else: row['basis'] = [{'clause_id': '总包约定'}]
    with pytest.raises(ValueError):
        validate_upstream_candidate(row, sources())


def test_upstream_finding_binds_both_roles_without_a_fake_statute():
    row = validate_upstream_candidate(candidate(), sources())
    assert {ref['role'] for ref in row['evidence']} == {'general', 'sub'}
    assert not row['basis'] and row['finding_id']


def test_comparison_budget_recalls_specific_terms_for_each_upstream_obligation():
    from app.plus.blockchain_supervision.retrospective_upstream import comparison_candidates

    def term(key, statement):
        return {'term_id': key, 'topic': 'quality', 'statement': statement}

    upstream = [term('u1', '核查预制梁混凝土取样检测'), term('u2', '焊缝超声无损检测责任')]
    sub = [term(f'noise-{i}', '常规质量检查，整改后提交验收') for i in range(15)] + [
        term('late-a', '预制梁混凝土取样检测由乙方组织'),
        term('late-b', '焊缝超声无损检测由委托机构实施'),
    ]
    selected, complete = comparison_candidates(sub, upstream, budget=175)
    assert {row['term_id'] for row in selected} == {'late-a', 'late-b'}
    assert not complete


class UpstreamClient:
    supports_json_schema = True

    def __init__(self, *, mismatch=False, uncertain=False, no_risk=False, subcontract_employer='建工公司'):
        self.calls = []
        self.mismatch, self.uncertain, self.no_risk = mismatch, uncertain, no_risk
        self.subcontract_employer = subcontract_employer

    async def complete(self, messages, on_usage=None, response_schema=None):
        assert response_schema and response_schema['additionalProperties'] is False
        payload = json.loads(messages[1]['content'])
        self.calls.append(payload)
        if on_usage:
            on_usage({'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30})
        phase = payload.get('analysis_type')
        if phase == 'upstream_extract':
            general = payload['role'] == 'general'
            mid = 1 if general else 2
            party = '建工公司' if general else self.subcontract_employer
            value = {'checked_topics': list(TOPICS), 'facts': [
                {'field': 'project_name', 'value': '嘉松工程', 'evidence': [ref(mid, '嘉松工程')]},
                {'field': 'contractor' if general else 'employer', 'value': party, 'evidence': [ref(mid, party)]},
                {'field': 'scope', 'value': '桥梁吊装', 'evidence': [ref(mid, '桥梁吊装')]},
            ], 'terms': [{'topic': 'quality', 'statement': '工程检测的责任与费用约定',
                'obligation_holder': 'employer' if general else 'sub',
                'transmission': 'reserved' if general else 'pass_through',
                'conditions': '同一检测对象', 'exceptions': '', 'priority': '专用条款',
                'evidence': [candidate()['evidence'][0 if general else 1]]}]}
        elif phase == 'upstream_relationship':
            value = {'checks': [{'dimension': dim,
                'state': 'mismatch' if self.mismatch else 'uncertain' if self.uncertain else 'matched',
                'reason': '双侧身份事实及范围已分别对照。',
                'evidence': [ref(1, text), ref(2, self.subcontract_employer if dim == 'parties' else text)]}
                for dim, text in [('project', '嘉松工程'), ('parties', '建工公司'), ('scope', '桥梁吊装')]]}
        elif phase == 'upstream_compare':
            checks = response_schema['properties']['checks']
            expected_ids = [term['term_id'] for term in payload['upstream_terms']]
            assert checks['minItems'] == checks['maxItems'] == len(expected_ids)
            assert [item['allOf'][1]['properties']['term_id']['const']
                    for item in checks['prefixItems']] == expected_ids
            value = {'checks': [{'term_id': term['term_id'], 'state': 'aligned' if self.no_risk else 'risk',
                'reason': candidate()['message'], 'applicability': candidate()['applicability'],
                'conditions': candidate()['conditions'], 'evidence': candidate()['evidence']}
                for term in payload['upstream_terms']]}
        elif 'candidates' in payload:
            value = {'decisions': [{'finding_id': row['finding_id'], 'keep': True, 'reason': '双侧原文支撑同对象的约定差异。',
                                   'message': row['message']} for row in payload['candidates']]}
        else:
            raise AssertionError(phase)
        if phase in {'upstream_relationship', 'upstream_compare', 'upstream_extract'}:
            for check in (value['facts'] + value['terms'] if phase == 'upstream_extract' else value['checks']):
                refs = check.pop('evidence')
                check['evidence_ids'] = list(dict.fromkeys(key for reference in refs
                    for key, item in payload['evidence_catalog'].items()
                    if all(item[k] == reference[k] for k in ('material_id', 'page'))
                    and reference['quote'] in item['quote']))
            if phase == 'upstream_extract':
                referenced = {key for item in value['facts'] + value['terms'] for key in item['evidence_ids']}
                value['skipped_evidence'] = [{'evidence_id': key, 'reason': '无独立的合同义务。'}
                    for key in payload['evidence_catalog'] if key not in referenced]
        return json.dumps(value, ensure_ascii=False)


def test_compare_executes_every_term_and_restores_without_provider_calls():
    from app.plus.blockchain_supervision.retrospective_upstream import compare
    client, saved, started, usage = UpstreamClient(), {}, [], []
    def run():
        return asyncio.run(compare(sources(), {'baseline_date': '2026-11-19'}, client=client,
            load_checkpoint=saved.get, before_batch=lambda key, i, n: started.append(key),
            save_checkpoint=lambda key, value: saved.update({key: value}), on_usage=usage.append))
    result = run()
    assert result['state'] == 'completed'
    assert result['coverage']['batches_completed'] == 2
    assert result['coverage']['counts']['risk'] == 1
    assert len(result['findings']) == 1
    assert len(client.calls) == len(set(started)) == len(usage) == 5
    assert run() == result and len(client.calls) == 5


@pytest.mark.parametrize('state', ['mismatch', 'uncertain'])
def test_wrong_or_unknown_relationship_never_enters_normal_comparison(state):
    from app.plus.blockchain_supervision.retrospective_upstream import compare
    client = UpstreamClient(**{state: True})
    result = asyncio.run(compare(sources(), {}, client=client))
    assert result['relationship']['state'] == state
    assert not result['findings'] and not result['checks']
    assert all(payload.get('analysis_type') != 'upstream_compare' for payload in client.calls)


def test_confirmed_party_difference_is_a_finding_with_both_role_specific_originals():
    from app.plus.blockchain_supervision.retrospective_upstream import compare
    src = sources()
    src[1]['pages'][0]['text'] = src[1]['pages'][0]['text'].replace('发包人：建工公司', '发包人：另一公司')
    client = UpstreamClient(mismatch=True, subcontract_employer='另一公司')
    result = asyncio.run(compare(src, {}, client=client))
    finding, = result['findings']
    assert '总包合同的承包人' in finding['message'] and '建工公司' in finding['message']
    assert '分包合同的发包方' in finding['message'] and '另一公司' in finding['message']
    assert '授权' in finding['message']
    assert [(ref['file_name'], ref['page']) for ref in finding['evidence']] == [('总包.pdf', 1), ('分包.pdf', 1)]
    assert all(payload.get('analysis_type') != 'upstream_compare' for payload in client.calls)


def test_comparison_must_account_for_every_upstream_term():
    from app.plus.blockchain_supervision.retrospective_upstream import bind_comparison
    with pytest.raises(ValueError):
        bind_comparison({'checks': []}, [{'term_id': 'one'}], [], [], sources())


def test_usage_failure_stops_before_next_upstream_call():
    from app.plus.blockchain_supervision.retrospective_upstream import compare
    from app.plus.blockchain_supervision.retrospective_analysis import ExecutionBoundaryError
    client = UpstreamClient()
    def fail(raw):
        raise RuntimeError('synthetic accounting failure')
    with pytest.raises(ExecutionBoundaryError):
        asyncio.run(compare(sources(), {}, client=client, on_usage=fail))
    assert len(client.calls) == 1


def test_provider_timeout_stops_new_upstream_requests_without_claiming_coverage():
    from app.plus.blockchain_supervision.retrospective_upstream import compare
    class TimeoutClient:
        calls = 0
        async def complete(self, *args, **kwargs):
            self.calls += 1
            raise TimeoutError('synthetic provider timeout')
    client = TimeoutClient()
    result = asyncio.run(compare(sources(), {}, client=client))
    assert client.calls == 1 and result['state'] == 'partial'
    assert result['coverage']['batches_completed'] == 0
    assert result['extraction'][1]['state'] == 'not_executed'


def test_interrupted_extraction_is_not_called_again_or_reported_complete():
    from app.plus.blockchain_supervision.retrospective_upstream import compare
    client = UpstreamClient()
    result = asyncio.run(compare(sources(), {}, client=client,
        load_checkpoint=lambda key: {'state': 'failed', 'findings': [], 'limitations': ['已受理请求中断']}))
    assert client.calls == [] and result['state'] == 'partial'
    assert result['coverage']['batches_completed'] == 0
    assert not result['findings']


def test_no_risk_does_not_require_a_minimum_finding_count():
    from app.plus.blockchain_supervision.retrospective_upstream import compare
    client = UpstreamClient(no_risk=True)
    result = asyncio.run(compare(sources(), {}, client=client))
    assert result['state'] == 'completed' and not result['findings']
    assert result['coverage']['counts']['aligned'] == 1


def test_automatic_rejection_is_retained_as_a_decision_without_report_risk():
    from app.plus.blockchain_supervision.retrospective_upstream import compare
    class Reject(UpstreamClient):
        async def complete(self, messages, on_usage=None, response_schema=None):
            payload = json.loads(messages[1]['content'])
            if 'candidates' in payload:
                assert payload['upstream_context']['relationship']['state'] == 'matched'
                return json.dumps({'decisions': [{'finding_id': row['finding_id'], 'keep': False,
                    'reason': '引用涉及不同的检测范围，不能确认同一对象的约定冲突。', 'message': ''}
                    for row in payload['candidates']]})
            return await super().complete(messages, on_usage, response_schema)
    result = asyncio.run(compare(sources(), {'baseline_date': '2026-11-19'}, client=Reject()))
    assert result['state'] == 'completed' and not result['findings']
    assert result['coverage']['counts']['dismissed'] == 1
    assert result['automatic_review']['automatic_review'][0]['keep'] is False


def test_upstream_final_reviewer_cannot_borrow_another_candidates_facts_or_clause_numbers():
    from app.plus.blockchain_supervision.retrospective_analysis import review_candidates
    row = validate_upstream_candidate(candidate(), sources())
    class BorrowedFacts:
        async def complete(self, messages, **kwargs):
            return json.dumps({'decisions': [{'finding_id': row['finding_id'], 'keep': True,
                'reason': '同一检测对象的费用范围待明确。',
                'message': '总包第99.9条已确认重复收费100万元。'}]})
    kept, decisions = asyncio.run(review_candidates([row], sources(),
        {'baseline_date': '2026-11-19'}, client=BorrowedFacts(), final=True))
    assert kept == [row]
    assert decisions[0]['proposed_message'] == '总包第99.9条已确认重复收费100万元。'
    assert decisions[0]['message_source'] == 'validated_comparison_candidate'


def test_validation_diagnostic_identifies_failure_without_echoing_model_data():
    from app.plus.blockchain_supervision.retrospective_upstream import _validation_failure
    from app.plus.blockchain_supervision.retrospective_upstream_contracts import SelectedExtractionResponse
    from pydantic import ValidationError
    assert _validation_failure(ValueError('提取与略过的原文编号冲突、重复或不属于当前批次')) == {
        'code': 'invalid_skipped_evidence'}
    secret = 'private-contract-or-provider-content'
    assert secret not in json.dumps(_validation_failure(ValueError(secret)))
    try:
        SelectedExtractionResponse.model_validate({'checked_topics': [], 'facts': [], 'terms': [],
            'skipped_evidence': [], secret: secret})
    except ValidationError as exc:
        diagnostic = _validation_failure(exc)
    assert diagnostic['code'] == 'response_schema_invalid'
    assert 'too_short' in diagnostic['error_types'] and 'extra_forbidden' in diagnostic['error_types']
    assert secret not in json.dumps(diagnostic)


def test_pending_comparison_never_becomes_an_omission_finding():
    from app.plus.blockchain_supervision.retrospective_upstream import compare
    class MissingCounterpart(UpstreamClient):
        async def complete(self, messages, on_usage=None, response_schema=None):
            payload = json.loads(messages[1]['content'])
            if payload.get('analysis_type') == 'upstream_compare':
                return json.dumps({'checks': [{'term_id': term['term_id'], 'state': 'pending',
                    'reason': '尚未找到同一检测对象的对应条款，无法确认是否适用。',
                    'applicability': '', 'conditions': '', 'evidence_ids': []}
                    for term in payload['upstream_terms']]})
            return await super().complete(messages, on_usage, response_schema)
    result = asyncio.run(compare(sources(), {}, client=MissingCounterpart()))
    assert result['state'] == 'partial' and not result['findings']
    assert result['coverage']['counts']['pending'] == 1


def test_comparison_cannot_use_unprovided_counterpart_or_call_law_a_contract():
    from app.plus.blockchain_supervision.retrospective_upstream import bind_comparison
    src = sources()
    term = {'term_id': 'quality', 'evidence': [candidate()['evidence'][0]]}
    check = {'term_id': 'quality', 'state': 'risk', 'reason': candidate()['message'],
             'conditions': candidate()['conditions'], 'applicability': candidate()['applicability'],
             'evidence': candidate()['evidence']}
    result = bind_comparison({'checks': [check]}, [term], [], [], src)
    assert result['state'] == 'partial'
    assert result['checks'][0]['state'] == 'pending'
    assert not result['findings']


def test_payment_percentage_and_reserved_insurance_can_close_without_risk():
    """验证合理排除可持久化收口；模拟模型不证明真实Provider语义准确率。"""
    from app.plus.blockchain_supervision.retrospective_upstream import bind_comparison
    src = sources()
    src[0]['pages'][0]['text'] += '进度款支付85%。业主办理建筑工程一切险。'
    src[1]['pages'][0]['text'] += '进度款支付80%。分包人另行办理雇主责任险。'
    for topic, left, right, state in [
            ('payment', '进度款支付85%。', '进度款支付80%。', 'aligned'),
            ('insurance', '业主办理建筑工程一切险。', '分包人另行办理雇主责任险。', 'not_applicable')]:
        term = {'term_id': topic, 'evidence': [ref(1, left)]}
        sub = {'term_id': topic + '-sub', 'evidence': [ref(2, right)]}
        result = bind_comparison({'checks': [{'term_id': topic, 'state': state,
            'reason': '两侧合同的具体付款对象或保险标的不同，单凭比例或险种差异不认定风险。',
            'applicability': '核对合同对象及费用边界。', 'conditions': '未见相反的同对象强制传导约定。',
            'evidence': [ref(1, left), ref(2, right)]}]}, [term], [sub], [], src)
        assert not result['findings'] and result['checks'][0]['state'] == state


def test_real_runtime_persists_dual_evidence_and_same_report_exports(tmp_path, monkeypatch):
    import io
    import fitz
    from docx import Document
    from sqlalchemy.orm import Session
    from app.plus.blockchain_supervision import retrospective_runtime as runtime
    from app.plus.blockchain_supervision.models import BsTask, BsMaterial, BsTaskEvent, BsAiReviewJob
    from app.plus.startup_conditions_check import llm
    from app.services.billing import bindings
    from test_bs_report_version_http import _client
    from test_bs_retrospective_runtime import Client

    class Composite(UpstreamClient, Client):
        def __init__(self):
            UpstreamClient.__init__(self)
            self.internal = Client()

        async def complete(self, messages, on_usage=None, response_schema=None):
            content = messages[1]['content']
            payload = json.loads(content[0]['text'] if isinstance(content, list) else content)
            if payload.get('analysis_type', '').startswith('upstream_') or any(
                    row['kind'] == 'upstream_risk' for row in payload.get('candidates', [])):
                return await UpstreamClient.complete(self, messages, on_usage, response_schema)
            return await self.internal.complete(messages, on_usage)

    http, engine, tid = _client(tmp_path)
    client = Composite()
    monkeypatch.setattr(runtime, 'schedule', lambda *args: False)
    monkeypatch.setattr(runtime, 'freeze_basis', lambda: [])
    monkeypatch.setattr(runtime, '_file_hash', lambda m: str(m.id).zfill(64))
    monkeypatch.setattr(runtime, '_render_citation_page', lambda *args: b'\x89PNG\r\n\x1a\nsynthetic')
    monkeypatch.setattr(bindings, 'effective_model_code_or_default', lambda *args: 'synthetic')
    monkeypatch.setattr(llm, 'client_from_env', lambda **kwargs: client)
    with Session(engine) as db:
        task = db.get(BsTask, tid)
        task.options_snapshot_json = {'review_mode': 'contract_retrospective'}
        for source in sources():
            text = source['pages'][0]['text']
            if source['role'] == 'sub':
                text += '\n工期延长的费用可另行协商。工期延长费用均不调整。'
            db.add(BsMaterial(task_id=tid, file_name=source['file_name'], role=source['role'],
                parse_state='done', pages=1, degradations_json=[], pages_json=[{'page': 1, 'text': text}]))
        db.commit()
    base = f'/api/plus/blockchain-supervision/tasks/{tid}'
    assert http.post(base + '/retrospective/start', json={}).status_code == 202
    with Session(engine) as db:
        job_id = db.query(BsAiReviewJob).one().id
    asyncio.run(runtime.run(engine, job_id))
    result = http.get(base + '/retrospective').json()
    report = result['report']
    assert report, result
    assert report['identity']['upstream_version'] == 'bs.contract-upstream.v16'
    assert report['upstream']['state'] == 'completed'
    assert {row['kind'] for row in report['rows']} == {'internal_ambiguity', 'upstream_risk'}
    row = next(row for row in report['rows'] if row['kind'] == 'upstream_risk')
    assert row['cells'][1] == '总包／分包'
    assert '总包《总包.pdf》' in row['cells'][4] and '分包《分包.pdf》' in row['cells'][4]
    assert row['cells'][6] == '上游合同约定（合同约束）'
    assert {ref['role'] for ref in row['evidence']} == {'general', 'sub'}
    calls = len(client.calls) + client.internal.calls
    asyncio.run(runtime.run(engine, job_id))
    assert calls == len(client.calls) + client.internal.calls
    with Session(engine) as db:
        from app.models.models import TokenUsage
        assert db.query(TokenUsage).count() == calls
        assert db.query(BsTaskEvent).filter_by(event_type=runtime.CHECKPOINT).count() >= 5
    word = http.get(base + '/report/export?fmt=docx')
    pdf = http.get(base + '/report/export?fmt=pdf')
    assert word.status_code == pdf.status_code == 200
    document = Document(io.BytesIO(word.content))
    texts = '\n'.join(cell.text for table in document.tables for cells in table.rows for cell in cells.cells)
    assert candidate()['evidence'][0]['quote'] in texts and candidate()['evidence'][1]['quote'] in texts
    with fitz.open(stream=pdf.content, filetype='pdf') as doc:
        assert all(page.rect.width > page.rect.height for page in doc)
    assert http.get(base + '/retrospective').json()['report'] == report
