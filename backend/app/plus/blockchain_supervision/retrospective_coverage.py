"""规范覆盖计划和审计聚合；主题召回不能充当已核查证明。"""
import re
from .retrospective_sources import digest, original_citation

VERSION = 'bs.contract-basis-coverage.v2'
ARCHIVE = {'DG-TJ08-2225-ARCHIVE', 'JS-2014-159-ARCHIVE', 'MOHURD-128-2015',
           'SC-397-2014'}


def compact(text):
    return re.sub(r'\s+', '', text)


def plan(basis):
    """逐条登记；只归并同文件真实包含的原文，不猜法律适用。"""
    rows, units = [], {}
    for clause in basis:
        key, text = clause['clause_id'], compact(clause['text'])
        reason = ('档案资质/准入资料核验，不作为已审批合同文本缺件审查。' if clause.get('doc_id') in ARCHIVE
                  else '准入附件/证书资料要求，不作为合同文本缺件审查。'
                  if clause.get('doc_id') == 'ZHB-2025-0620' and key not in {'ZHB-2025-0620#三、', 'ZHB-2025-0620#三、1'}
                  else '准入填报附件模板，不检查报审材料是否齐全。' if '#附件' in key
                  else '仅为章节目录标题，无独立规范义务。' if re.fullmatch(r'第[一二三四五六七八九十百]+章.{0,12}', text)
                  else '')
        if reason:
            rows.append({'clause_id': key, 'doc_id': clause.get('doc_id'), 'state': 'not_applicable',
                         'reason': reason, 'unit_id': None})
            continue
        parents = [p for p in basis if p.get('doc_id') == clause.get('doc_id')
                   and p['clause_id'] != key and p['clause_id'] not in {r['clause_id'] for r in rows if r['state']=='not_applicable'}
                   and compact(p['text']) != text and text in compact(p['text'])]
        # 只取较长的真实父原文；短于自身的标题/同句不参与归并。
        parent = max(parents, key=lambda p: len(compact(p['text']))) if parents else clause
        unit_id = parent['clause_id']
        units.setdefault(unit_id, parent)
        rows.append({'clause_id': key, 'doc_id': clause.get('doc_id'), 'state': 'pending',
                     'reason': '尚待逐项核查', 'unit_id': unit_id})
    groups, current, size = [], [], 0
    for unit in units.values():
        length = len(unit['text']) + len(unit.get('scope_context', ''))
        if current and (size + length > 6000 or len(current) >= 40):
            groups.append(current)
            current, size = [], 0
        current.append(unit)
        size += length
    if current:
        groups.append(current)
    return {'version': VERSION, 'fingerprint': digest(basis), 'clauses': rows,
            'units': list(units.values()), 'groups': groups}


def validate_checks(raw, units, sources, *, batch=None):
    keys = {row['clause_id'] for row in units}
    checks = raw.get('checks')
    if (not isinstance(checks, list) or len(checks) != len(keys)
            or any(not isinstance(row, dict) for row in checks)
            or {row.get('clause_id') for row in checks} != keys):
        raise ValueError('规范覆盖回应缺项或重复')
    result = []
    for row in checks:
        state, reason = row.get('state'), row.get('reason')
        if state not in {'checked', 'not_applicable', 'not_observed', 'pending'} or not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 1800:
            state, reason = 'pending', '模型返回的状态或理由无效，需补核。'
        evidence = row.get('evidence', [])
        if not isinstance(evidence, list) or len(evidence) > 6:
            state, reason, evidence = 'pending', '合同引文格式无效，需补核。', []
        refs = [original_citation(ref, sources) for ref in evidence if isinstance(ref, dict)]
        if len(refs) != len(evidence) or any(ref is None for ref in refs):
            state, reason, refs = 'pending', '合同引文无法逐字定位，需补核。', []
        if batch is not None and any(
                not any(part['material_id'] == ref['material_id'] and part['page'] == ref['page']
                        and part['start'] <= ref['start'] + offset < part['end']
                        for part in batch['parts'])
                for ref in refs for offset, char in enumerate(ref['quote']) if not char.isspace()):
            state, reason, refs = 'pending', '合同引文不在本批实际检查范围，需补核。', []
        if state in {'checked', 'not_applicable'} and not refs:
            state, reason = 'pending', '核对或不适用结论缺少合同原文支撑。'
        if state == 'not_observed' and refs:
            state, reason = 'pending', '本批未见相关约定的记录与所引合同原文冲突，需补核。'
        result.append({'clause_id': row['clause_id'], 'state': state, 'reason': reason, 'evidence': refs})
    return result


def summarize(coverage_plan, scans):
    by_unit = {}
    for scan in scans:
        for row in scan.get('checks', []):
            by_unit.setdefault(row['clause_id'], []).append(row)
    rows = []
    for row in coverage_plan['clauses']:
        row = dict(row)
        if row['unit_id'] is not None:
            records = by_unit.get(row['unit_id'], [])
            related = [s for s in scans if row['unit_id'] in s['basis_ids']]
            failed = any(s['state'] != 'completed' for s in related)
            if (not failed and records and len(records) == len(related)
                    and all(r['state'] == 'not_applicable' for r in records)):
                row.update(state='not_applicable', reason='各正文批次均有原文支持不适用，见检查记录。')
            elif (not failed and records and len(records) == len(related)
                  and any(r['state'] == 'checked' for r in records)
                  and all(r['state'] in {'checked', 'not_applicable', 'not_observed'} for r in records)):
                row.update(state='checked', reason='已对全部可读合同批次逐条核查；表示覆盖完成，不等同合规认证。')
            elif (not failed and records and len(records) == len(related)
                  and all(r['state'] == 'not_observed' for r in records)):
                row.update(state='pending', reason='各批未见对应约定；尚不能据此确认规范不适用或合同存在遗漏。')
            else:
                row.update(state='pending', reason='适用条件/合同对应尚未确认或执行未完成，见逐批记录。')
            row['checks'] = records
        rows.append(row)
    return {'version': VERSION, 'basis_fingerprint': coverage_plan['fingerprint'], 'clauses': rows,
            'counts': {state: sum(r['state'] == state for r in rows)
                       for state in ['checked', 'not_applicable', 'pending']},
            'scans': scans, 'scope': '仅本次冻结依据；未收录法律不在本清单分母内。'}


async def scan(sources, basis, identity, *, client, load_checkpoint=None, before_batch=None,
               save_checkpoint=None, on_usage=None):
    from .retrospective_analysis import (SYSTEM, TOPICS, ExecutionBoundaryError, prompt_batch,
                                        validate_candidate, review_candidates)
    from .retrospective_sources import analysis_batches
    from .semantic_review import request_json
    from .retrospective_contracts import CoverageResponse, strict_response_schema
    coverage_plan = plan(basis)
    if identity.get('coverage_fingerprint') and identity['coverage_fingerprint'] != digest(coverage_plan):
        raise ValueError('规范覆盖计划与冻结身份不一致')
    if not sources:
        return summarize(coverage_plan, []), []
    scans, findings = [], {}
    batches = analysis_batches(sources)
    work = [(group, batch) for group in coverage_plan['groups'] for batch in batches]
    stopped = False
    for index, (group, batch) in enumerate(work):
        key = 'basis:' + digest({'version': VERSION, 'basis': group, 'batch': batch['batch_id']})
        ids = [row['clause_id'] for row in group]
        saved = load_checkpoint(key) if load_checkpoint else None
        if stopped and saved is None:
            saved = {'state': 'not_executed', 'checks': [], 'findings': [], 'error_type': 'previous_provider_failure'}
        if saved is None:
            if before_batch:
                before_batch(key, index + 1, len(work))
            try:
                raw = await request_json(client, SYSTEM + '\n' + (
                    '这是规范驱动的漏检补查，不受topic_ids或关键词限制。逐一核对本次basis全部条款。'
                    '只比较本批合同正文，其他批次单独扫描；不能据本批没有内容就认定全合同缺失。'
                    '额外返回checks数组，每个basis.clause_id恰好一项：'
                    '{"clause_id":"原ID","state":"checked|not_applicable|not_observed|pending",'
                    '"reason":"具体核对结果、适用条件或尚待核实理由",'
                    '"evidence":[{"material_id":整数,"page":整数,"quote":"合同连续原文","clause":"条款号"}]}。'
                    'checked是已找到原文并对照，不是合规认证，必须引用对应合同原文；not_applicable必须有合同原文证明适用条件不成立。'
                    '本批没有相关合同文字时用not_observed并写明仅指本批，evidence为空；不得改为not_applicable、checked或推断其他批次也无约定。'
                    '无法判断或仅凭一般行业知识时用pending，不能虚构引文。'
                    '法规职责针对实际行为时，只审查合同有没有明确相反约定，不推定行为已发生。'
                     '没有发现不必生成风险。checks理由不得要求准入缺件、编造审批决定或法律。'
                     'checked_topics须逐字返回全部8个topic_ids；规范驱动补查不局限于这些主题。'
                     '最终仍返回一个完整对象，禁止直接返回checks数组或findings数组。完整外层结构：'
                     '{"checked_topics":["scope","price","payment","schedule","quality","safety","responsibility","consistency"],'
                     '"findings":[],"limitations":[],"checks":[{"clause_id":"复制本次basis的实际ID",'
                     '"state":"not_observed","reason":"本批未见对应约定，仅记录本批观察，不判断全合同适用性。","evidence":[]}]}。'
                     'checks须覆盖全部本次basis；findings有发现时使用前述发现项字段，无发现时为空。'),
                    {'analysis_type': 'basis_coverage', 'contract_date': identity.get('contract_date'),
                     'baseline_date': identity.get('baseline_date'),
                     'current_notice_date': identity.get('current_notice_date'),
                     'topic_ids': list(TOPICS),
                     'topic_descriptions': TOPICS,
                     'batch': prompt_batch(batch), 'basis': group}, on_usage=on_usage,
                     response_schema=strict_response_schema(CoverageResponse))
                if raw.get('checked_topics') != list(TOPICS):
                    raise ValueError('规范补查未确认完整主题执行')
                checks = validate_checks(raw, group, sources, batch=batch)
                if not isinstance(raw.get('findings'), list) or len(raw['findings']) > 80:
                    raise ValueError('规范补查发现格式不完整')
                candidates, rejected = {}, []
                for candidate in raw['findings']:
                    try:
                        checked = validate_candidate(candidate, sources, group, identity)
                        candidates[checked['finding_id']] = checked
                    except (ValueError, TypeError, KeyError) as exc:
                        rejected.append(type(exc).__name__)
                decisions, rows = [], []
                if candidates:
                    if before_batch:
                        before_batch(key + ':review', index + 1, len(work))
                    rows, decisions = await review_candidates(list(candidates.values()), sources, identity,
                                                             client=client, on_usage=on_usage)
                saved = {'state': 'completed', 'checks': checks, 'findings': rows,
                         'automatic_review': decisions, 'rejected': rejected}
            except ExecutionBoundaryError:
                raise
            except Exception as exc:
                saved = {'state': 'failed', 'checks': [], 'findings': [], 'error_type': type(exc).__name__}
                # 响应格式/原文问题逐组留待核查；接口故障停止后续请求，不碰运气。
                if not isinstance(exc, (ValueError, TypeError, KeyError)):
                    stopped = True
            if save_checkpoint:
                save_checkpoint(key, saved)
        scan_row = {**saved, 'checkpoint_id': key, 'batch_id': batch['batch_id'], 'basis_ids': ids,
                    'pages': sorted({part['page'] for part in batch['parts']})}
        scans.append(scan_row)
        for row in saved.get('findings', []):
            findings.setdefault(row['finding_id'], row)
    return summarize(coverage_plan, scans), list(findings.values())
