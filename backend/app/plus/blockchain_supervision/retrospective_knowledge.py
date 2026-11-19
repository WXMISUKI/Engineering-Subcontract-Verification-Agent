"""倒查复用准入参考检索；来源、授权、原件指纹与原文校验仍由公共链路执行。"""
from copy import deepcopy

from . import knowledge_runs
from .retrospective_analysis import _words
from .retrospective_coverage import plan


def retrieve(db, task, sources, basis, *, contract_date=None):
    """一次运行冻结一次检索，不在导出或恢复时重新查询。"""
    try:
        package = knowledge_runs.load_reference_package(as_of=task.baseline_date)
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
        source.update(clause_id=row['clause_id'],
                      query_text=f"{row['document'].get('doc_title', row['doc_id'])} 合同条款 {terms}",
                      physical_page=row.get('page_start'), human_verified=False)
        planned.append(source)
    if not planned:
        return {'status': 'local_only', 'references': [], 'queries': [], 'limitations': []}
    # 复用原有按文件归并查询、超时、授权范围、原件哈希、逐字原文及父项/例外验证。
    try:
        result = knowledge_runs.retrieve_reference_run(db, task, {'basis_sources': planned})
    except Exception:
        return {'status': 'unavailable', 'references': [], 'queries': [],
                'limitations': ['在线知识检索未完成；本次保留已冻结的本地规范原文对照结果。']}
    available = {row['clause_id'] for row in basis}
    refs = [ref for ref in result['references'] if ref['clause_id'] in available]
    return {'status': result['status'], 'references': refs, 'queries': result['queries'],
            'authorized_scope': result['authorized_scope'],
            'limitations': ([] if result['status'] == 'ok' else [
                '部分在线依据未取得同版本可核验原文；相关条款使用已冻结的本地规范，在线召回不代表规范全部覆盖。'])}
