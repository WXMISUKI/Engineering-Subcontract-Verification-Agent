"""倒查复用准入参考检索；来源、授权、原件指纹与原文校验仍由公共链路执行。"""
from copy import deepcopy
from datetime import date

from . import knowledge_runs
from .retrospective_analysis import _words
from .retrospective_coverage import plan
from .rules import SHANGHAI_COMPARISON_DOC_ID


_BASELINE_UNSET = object()


def retrieve(db, task, sources, basis, *, contract_date=None, baseline_date=_BASELINE_UNSET):
    """一次运行冻结一次检索，不在导出或恢复时重新查询。"""
    if baseline_date is _BASELINE_UNSET:
        baseline_date = task.baseline_date
    if baseline_date is None:
        return {'status': 'local_only', 'references': [], 'queries': [],
                'limitations': ['未取得核查基准日；本次不按当前日期筛选在线规则，仅使用已冻结的本地规范原文。']}
    as_of = baseline_date if isinstance(baseline_date, date) else date.fromisoformat(baseline_date)
    try:
        package = knowledge_runs.load_reference_package(as_of=as_of, compare_shanghai=True)
    except (OSError, ValueError, KeyError):
        return {'status': 'unavailable', 'references': [], 'queries': [],
                'limitations': ['在线知识库映射不可用；本次使用已冻结的本地规范原文进行对照。']}
    templates = {row['doc_id']: row for row in package['sources']}
    eligible = {row['clause_id'] for row in plan(basis)['clauses'] if row['unit_id'] is not None}
    selected = {row['clause_id']: row for row in basis if row['clause_id'] in eligible}
    text = '\n'.join(page['text'] for source in sources for page in source['pages'] if page['readable'])
    terms = ' '.join(sorted(_words(text)))
    planned = []
    for row in selected.values():
        template = templates.get(row['doc_id'])
        if template is None:
            continue  # 尚无已登记在线来源的历史规范继续使用冻结原文，不猜知识标识。
        exact = next((source for source in package['sources']
                      if source['clause_id'] == row['clause_id']), None)
        source = deepcopy(exact) if exact else {
            key: template[key] for key in ('doc_id', 'kb_id', 'knowledge_id', 'original_sha256')}
        if exact is None and template.get('temporal_notice'):
            source['temporal_notice'] = template['temporal_notice']
        if row['doc_id'] == SHANGHAI_COMPARISON_DOC_ID:
            source['retrospective_shanghai_lookup'] = True
        source.update(clause_id=row['clause_id'],
                      query_text=f"{row['document'].get('doc_title', row['doc_id'])} 合同条款 {terms}",
                      physical_page=row.get('page_start'), human_verified=False)
        planned.append(source)
    if not planned:
        return {'status': 'local_only', 'references': [], 'queries': [], 'limitations': []}
    # 复用原有按文件归并查询、超时、授权范围、原件哈希、逐字原文及父项/例外验证。
    try:
        result = knowledge_runs.retrieve_reference_run(
            db, task, {'basis_sources': planned}, as_of=as_of)
    except Exception:
        return {'status': 'unavailable', 'references': [], 'queries': [],
                'limitations': ['在线知识检索未完成；本次保留已冻结的本地规范原文对照结果。']}
    planned_by_id = {source['clause_id']: source for source in planned}
    refs = []
    for ref in result['references']:
        source = planned_by_id.get(ref['clause_id'])
        if source is not None:
            notice = source.get('temporal_notice')
            refs.append({**ref, 'temporal_notice': notice} if notice else ref)
    verified_ids = {ref['clause_id'] for ref in refs}
    queries = [{**query, 'reference_verified': query.get('clause_id') in verified_ids}
               for query in result['queries']]
    limitations = []
    if len(verified_ids) < len(planned_by_id):
        limitations.append(f'本次在线查询计划包含 {len(planned_by_id)} 条依据，其中 {len(verified_ids)} 条取得'
                           '同版本可核验原文。其余使用已冻结的本地规范原文。')
    return {'status': result['status'], 'references': refs, 'queries': queries,
            'authorized_scope': result['authorized_scope'], 'limitations': limitations}
