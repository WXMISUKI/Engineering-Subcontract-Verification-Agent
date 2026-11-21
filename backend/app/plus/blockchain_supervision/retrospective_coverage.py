"""规范覆盖计划和审计聚合；主题召回不能充当已核查证明。"""
import re
from collections import Counter, defaultdict

from .retrospective_sources import digest, original_citation

VERSION = 'bs.contract-basis-coverage.v10'
ARCHIVE = {'DG-TJ08-2225-ARCHIVE', 'JS-2014-159-ARCHIVE', 'MOHURD-128-2015',
           'SC-397-2014'}
# 已逐条确认只说明制定目的和依据。正文有变化时默认重新纳入核查。
PURPOSE_CLAUSES = {
    'SHCG-2025-118#第一条': 'f0c109d1093a70dc77c5d9efd24199c94ce30d8c4053736f2e60d154e292c4f2',
    'SHJT-2026-8#第一条': '46c695910d7c5b48cbf0f190ff4387b489585dd1a9b7654f0febfdd69bbd0381',
}


def compact(text):
    return re.sub(r'\s+', '', text)


def plan(basis):
    """每个规范条款独立登记；父条款的覆盖不能证明子条款已检查。"""
    rows, units = [], {}
    for clause in basis:
        key, text = clause['clause_id'], compact(clause['text'])
        reason = ('档案资质/准入资料核验，不作为已审批合同文本缺件审查。' if clause.get('doc_id') in ARCHIVE
                  else '准入附件/证书资料要求，不作为合同文本缺件审查。'
                  if clause.get('doc_id') == 'ZHB-2025-0620' and key not in {'ZHB-2025-0620#三、', 'ZHB-2025-0620#三、1'}
                  else '准入填报附件模板，不检查报审材料是否齐全。' if '#附件' in key
                  else '仅为章节目录标题，无独立规范义务。' if re.fullmatch(r'第[一二三四五六七八九十百]+章.{0,12}', text)
                  else '该条仅说明制定目的及依据，无独立合同义务，保留作规范背景。'
                  if key in PURPOSE_CLAUSES and digest(text) == PURPOSE_CLAUSES[key]
                  else '')
        if reason:
            rows.append({'clause_id': key, 'doc_id': clause.get('doc_id'), 'state': 'not_applicable',
                         'reason': reason, 'unit_id': None})
            continue
        units.setdefault(key, clause)
        rows.append({'clause_id': key, 'doc_id': clause.get('doc_id'), 'state': 'pending',
                     'reason': '尚待逐项核查', 'unit_id': key})
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


def _evidence_batch(batch, prompt_batch):
    """只传一次分段正文；证据编号绑定同一物理页的连续原文。"""
    payload = prompt_batch(batch)
    parts, catalog = [], {}
    for part in batch['parts']:
        for start in range(0, len(part['text']), 1200):
            quote = part['text'][start:start + 1200]
            if not quote.strip():
                continue
            key = f"E{len(parts) + 1:03d}"
            reference = {'material_id': part['material_id'], 'page': part['page'],
                         'clause': part['clause'], 'quote': quote,
                         'bounds': (part['start'] + start, part['start'] + start + len(quote))}
            catalog[key] = reference
            parts.append({'evidence_id': key, 'material_id': reference['material_id'],
                          'page': reference['page'], 'text': quote})
    return {**payload, 'parts': parts}, catalog


def _scan_response_schema(rule_keys, evidence_ids):
    from .retrospective_contracts import CoverageResponse, strict_response_schema

    schema = strict_response_schema(CoverageResponse)
    check = schema['$defs']['BasisCheck']
    fields = {key: value for key, value in check['properties'].items() if key != 'clause_id'}
    evidence = fields['evidence_ids']
    evidence['items']['enum'] = list(evidence_ids)
    schema['$defs']['BasisCheck'] = {**check, 'properties': fields, 'required': list(fields)}
    schema['properties']['checks'] = {
        'type': 'object', 'properties': {key: {'$ref': '#/$defs/BasisCheck'} for key in rule_keys},
        'required': list(rule_keys), 'additionalProperties': False,
    }
    return schema


def _resolve_check_keys(raw, rule_keys):
    checks = raw.get('checks')
    if (not isinstance(checks, dict) or checks.keys() != rule_keys.keys()
            or any(not isinstance(row, dict) for row in checks.values())):
        raise ValueError('规范覆盖回应缺项或重复')
    return {**raw, 'checks': [{**checks[key], 'clause_id': rule_id} for key, rule_id in rule_keys.items()]}


def validate_checks(raw, units, sources, *, batch=None, catalog=None, strict=False):
    keys = {row['clause_id'] for row in units}
    checks = raw.get('checks')
    if (not isinstance(checks, list) or len(checks) != len(keys)
            or any(not isinstance(row, dict) for row in checks)
            or {row.get('clause_id') for row in checks} != keys):
        raise ValueError('规范覆盖回应缺项或重复')
    result = []
    for row in checks:
        state, reason = row.get('state'), row.get('reason')
        invalid = None
        if state not in {'checked', 'not_applicable', 'not_observed', 'pending'} or not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 1800:
            invalid = '模型返回的状态或理由无效，需补核。'
        if 'evidence_ids' in row:
            ids = row['evidence_ids']
            allowed = catalog or {}
            if (not isinstance(ids, list) or len(ids) > 6
                    or any(not isinstance(key, str) or key not in allowed for key in ids)
                    or len(set(ids)) != len(ids)):
                invalid, evidence = '引用编号重复或不属于本批原文，需补核。', []
            else:
                evidence = [allowed[key] for key in ids]
        else:
            evidence = row.get('evidence', [])
        if not isinstance(evidence, list) or len(evidence) > 6:
            invalid, evidence = '合同引文格式无效，需补核。', []
        selected_ids = 'evidence_ids' in row
        refs = [original_citation(ref, sources, bounds=ref.get('bounds') if selected_ids else None)
                for ref in evidence if isinstance(ref, dict)]
        if len(refs) != len(evidence) or any(ref is None for ref in refs):
            invalid, refs = '合同引文无法逐字定位，需补核。', []
        if batch is not None and any(
                not any(part['material_id'] == ref['material_id'] and part['page'] == ref['page']
                        and part['start'] <= ref['start'] + offset < part['end']
                        for part in batch['parts'])
                for ref in refs for offset, char in enumerate(ref['quote']) if not char.isspace()):
            invalid, refs = '合同引文不在本批实际检查范围，需补核。', []
        if invalid is None:
            if state in {'checked', 'not_applicable'} and not refs:
                # 逐条记录已返回，只有其结论缺少证据。记录业务待核，不能把原因照抄为已核或不适用。
                state, reason = 'pending', '本批未提供支持该判断的合同原文，规范对应关系尚待核实。'
            if state == 'not_observed' and refs:
                state, reason = 'pending', '模型声称本批未见对应约定，但同时给出合同引文，须复核引文是否相关。'
        if invalid:
            if strict:
                raise ValueError(invalid)
            state, reason = 'pending', invalid
        result.append({'clause_id': row['clause_id'], 'state': state, 'reason': reason, 'evidence': refs})
    return result


def _is_complete_scan(scan, basis_ids):
    if not isinstance(scan, dict) or scan.get('state') != 'completed':
        return False
    checks = scan.get('checks')
    return (isinstance(checks, list)
            and all(isinstance(row, dict) and isinstance(row.get('clause_id'), str)
                    and row.get('state') in ('checked', 'not_applicable', 'not_observed', 'pending')
                    for row in checks)
            and Counter(row['clause_id'] for row in checks) == Counter(basis_ids))


def _findings_by_id(rows):
    if not isinstance(rows, list) or any(
            not isinstance(row, dict) or not isinstance(row.get('finding_id'), str) for row in rows):
        return None
    by_id = {row['finding_id']: row for row in rows}
    return by_id if len(by_id) == len(rows) else None


def _restore_scan(key, basis_ids, load_checkpoint):
    scan_key = key + ':scan'
    details = load_checkpoint(scan_key)
    if not _is_complete_scan(details, basis_ids) or not isinstance(details.get('rejected'), list):
        return None
    candidates = _findings_by_id(details.get('candidates'))
    if candidates is None:
        return None
    result = {'state': 'completed', 'checks': details['checks'], 'rejected': details['rejected'],
              'findings': [], 'automatic_review': [], 'restored_from': [scan_key]}
    if candidates:
        review_key = key + ':review'
        review = load_checkpoint(review_key)
        if not isinstance(review, dict) or review.get('state') != 'completed':
            return None
        decisions = _findings_by_id(review.get('automatic_review'))
        findings = _findings_by_id(review.get('findings'))
        if decisions is None or findings is None or decisions.keys() != candidates.keys():
            return None
        if (any(type(row.get('keep')) is not bool for row in decisions.values())
                or findings.keys() != {key for key, row in decisions.items() if row['keep']}):
            return None
        result.update(findings=review['findings'], automatic_review=review['automatic_review'])
        result['restored_from'].append(review_key)
    return result


def summarize(coverage_plan, scans, *, planned_scans=None):
    recorded_keys = Counter(scan.get('checkpoint_id') for scan in scans)
    required = planned_scans.values() if planned_scans is not None else (s['basis_ids'] for s in scans)
    expected_by_unit = Counter(unit for units in required for unit in units)
    by_unit = defaultdict(list)
    completed = 0
    for scan in scans:
        key = scan.get('checkpoint_id')
        if planned_scans is not None and (recorded_keys[key] != 1 or planned_scans.get(key) != scan['basis_ids']):
            continue
        if _is_complete_scan(scan, scan['basis_ids']):
            completed += 1
            for row in scan['checks']:
                by_unit[row['clause_id']].append({**row, 'checkpoint_id': key})
    rows = []
    for row in coverage_plan['clauses']:
        row = dict(row)
        if unit_id := row['unit_id']:
            records = by_unit.get(unit_id, [])
            verified = bool(records) and len(records) == expected_by_unit[unit_id]
            if verified and all(r['state'] == 'not_applicable' for r in records):
                row.update(state='not_applicable', reason='各正文批次均有原文支持不适用，见检查记录。')
            elif (verified and any(r['state'] == 'checked' for r in records)
                  and all(r['state'] in {'checked', 'not_applicable', 'not_observed'} for r in records)):
                row.update(state='checked', reason='已对全部可读合同批次逐条核查；表示覆盖完成，不等同合规认证。')
            elif verified and all(r['state'] == 'not_observed' for r in records):
                row.update(state='pending', reason='各批未见对应约定；尚不能据此确认规范不适用或合同存在遗漏。')
            else:
                row.update(state='pending', reason='适用条件/合同对应尚未确认或执行未完成，见逐批记录。')
            row['checks'] = records
        rows.append(row)
    total = len(scans) if planned_scans is None else len(planned_scans)
    execution_done = completed == len(scans) == total and (total > 0 or not coverage_plan['units'])
    return {'version': VERSION, 'basis_fingerprint': coverage_plan['fingerprint'], 'clauses': rows,
            'counts': {state: sum(r['state'] == state for r in rows)
                       for state in ['checked', 'not_applicable', 'pending']},
            'execution': {'state': 'completed' if execution_done else 'incomplete',
                          'batches_total': total, 'batches_completed': completed},
            'scans': scans, 'scope': '仅本次冻结依据；未收录法律不在本清单分母内。'}


async def scan(sources, basis, identity, *, client, load_checkpoint=None, before_batch=None,
               save_checkpoint=None, on_usage=None, checkpoint_subrequests=False):
    from .retrospective_analysis import (SYSTEM, TOPICS, ExecutionBoundaryError, prompt_batch,
                                        validate_candidate, review_candidates)
    from .retrospective_sources import analysis_batches
    from .semantic_review import request_json
    coverage_plan = plan(basis)
    if identity.get('coverage_fingerprint') and identity['coverage_fingerprint'] != digest(coverage_plan):
        raise ValueError('规范覆盖计划与冻结身份不一致')
    if not sources:
        return summarize(coverage_plan, []), []
    scans, findings = [], {}
    batches = analysis_batches(sources)
    work = {
        'basis:' + digest({'version': VERSION, 'basis': group, 'batch': batch['batch_id']}): (group, batch)
        for group in coverage_plan['groups'] for batch in batches
    }
    planned_scans = {key: [row['clause_id'] for row in group] for key, (group, _) in work.items()}
    stopped = False
    for index, (key, (group, batch)) in enumerate(work.items()):
        ids = planned_scans[key]
        saved = load_checkpoint(key) if load_checkpoint else None
        if (checkpoint_subrequests and saved and saved.get('state') == 'completed'
                and not _is_complete_scan(saved, ids)):
            restored = _restore_scan(key, ids, load_checkpoint)
            if restored is None:
                # 缺少明细时无法确认原请求及计费边界，保持中断并停止新增请求。
                saved = {'state': 'incomplete', 'checks': [], 'findings': [],
                         'error_type': 'IncompleteStoredCheckpoint'}
                stopped = True
            else:
                saved = restored
                if save_checkpoint:
                    save_checkpoint(key, saved)
        if stopped and saved is None:
            saved = {'state': 'not_executed', 'checks': [], 'findings': [], 'error_type': 'previous_provider_failure'}
        if saved is None:
            request_key = key + ':scan' if checkpoint_subrequests else key
            frozen_scan = load_checkpoint(request_key) if checkpoint_subrequests else None
            if frozen_scan is None and before_batch:
                before_batch(request_key, index + 1, len(work))
            try:
                if frozen_scan is not None:
                    checks, candidates, rejected = (frozen_scan['checks'], frozen_scan['candidates'],
                                                    frozen_scan['rejected'])
                else:
                    body, catalog = _evidence_batch(batch, prompt_batch)
                    # 冻结依据仍带完整时效元数据，比较输入只保留条文及实质适用上下文。
                    comparison_basis = [
                        {field: row[field] for field in ('clause_id', 'text', 'scope_context') if field in row}
                        for row in group
                    ]
                    rule_keys = {}
                    native_schema = getattr(client, 'supports_json_schema', False) is True
                    if native_schema:
                        rule_keys = {f'B{number:02d}': row['clause_id']
                                     for number, row in enumerate(comparison_basis, 1)}
                        for check_key, row in zip(rule_keys, comparison_basis):
                            row['check_key'] = check_key
                    checks_format = (
                        'checks是对象，键为basis中所有check_key，各出现一次；值含state、reason、evidence_ids。'
                        '不要在值里重复clause_id。checks的结构如{"B01":{"state":"pending","reason":"具体限制","evidence_ids":[]}}。'
                        if native_schema else
                        'checks是数组，按basis原顺序逐项输出，每个basis.clause_id恰好一项。'
                        '每项含clause_id、state、reason、evidence_ids。'
                    )
                    raw = await request_json(client, SYSTEM + '\n' + (
                    '这是规范驱动的漏检补查，不受topic_ids或关键词限制。逐一核对本次basis全部条款。'
                    '只比较本批合同正文，其他批次单独扫描；不能据本批没有内容就认定全合同缺失。'
                    '发布、签约、施行日期和历史适用性均不能作为跳过规范正文的理由。'
                    '先比对术语、主体、工程范围、义务和例外，逐条记录checks。'
                    '本阶段输出规范相关发现时使用当前对照口径，可提出有明确条件的候选，由系统核验依据时效。'
                    + checks_format +
                    'state只能是checked、not_applicable、not_observed、pending。'
                    'reason说明具体核对结果、适用条件或未能确认的原因。'
                    'checked是已找到原文并对照，不是合规认证，必须选择直接对应的片段编号；'
                    'not_applicable必须有片段证明适用条件不成立。父条款和子条款各自核对。'
                    '本批没有相关合同文字时用not_observed并写明仅指本批，evidence_ids为空；不得推断其他批次也无约定。'
                    '无法判断或仅凭一般行业知识时用pending，不能虚构引文。'
                    '法规职责针对实际行为时，只审查合同有没有明确相反约定，不推定行为已发生。'
                     '没有发现不必生成风险。checks理由不得要求准入缺件、编造审批决定或法律。'
                     'checked_topics须逐字返回全部8个topic_ids；规范驱动补查不局限于这些主题。'
                     '最终返回一个包含checked_topics、findings、limitations和checks的JSON对象。'
                     'checks须覆盖全部本次basis；findings有发现时使用前述发现项字段，无发现时为空。'),
                    {'analysis_type': 'basis_coverage',
                     'topic_ids': list(TOPICS),
                     'topic_descriptions': TOPICS,
                     'batch': body, 'basis': comparison_basis}, on_usage=on_usage,
                     response_schema=_scan_response_schema(rule_keys, catalog) if native_schema else None)
                    if raw.get('checked_topics') != list(TOPICS):
                        raise ValueError('规范补查未确认完整主题执行')
                    if native_schema:
                        raw = _resolve_check_keys(raw, rule_keys)
                    checks = validate_checks(raw, group, sources, batch=batch, catalog=catalog,
                                             strict=checkpoint_subrequests)
                    if not isinstance(raw.get('findings'), list) or len(raw['findings']) > 80:
                        raise ValueError('规范补查发现格式不完整')
                    candidates_by_id, rejected = {}, []
                    for candidate in raw['findings']:
                        try:
                            checked = validate_candidate(candidate, sources, group, identity)
                            candidates_by_id[checked['finding_id']] = checked
                        except (ValueError, TypeError, KeyError) as exc:
                            rejected.append(type(exc).__name__)
                    candidates = list(candidates_by_id.values())
                    if checkpoint_subrequests:
                        save_checkpoint(request_key, {'state': 'completed', 'checks': checks,
                                                      'candidates': candidates, 'rejected': rejected})
                decisions, rows = [], []
                if candidates:
                    request_key = key + ':review'
                    frozen_review = load_checkpoint(request_key) if checkpoint_subrequests else None
                    if frozen_review is None:
                        if before_batch:
                            before_batch(request_key, index + 1, len(work))
                        rows, decisions = await review_candidates(candidates, sources, identity,
                                                                 client=client, on_usage=on_usage)
                        if checkpoint_subrequests:
                            save_checkpoint(request_key, {'state': 'completed', 'findings': rows,
                                                          'automatic_review': decisions})
                    else:
                        rows, decisions = frozen_review['findings'], frozen_review['automatic_review']
                saved = {'state': 'completed', 'checks': checks, 'findings': rows,
                         'automatic_review': decisions, 'rejected': rejected}
            except ExecutionBoundaryError:
                raise
            except Exception as exc:
                saved = {'state': 'failed', 'checks': [], 'findings': [], 'error_type': type(exc).__name__}
                if checkpoint_subrequests:
                    save_checkpoint(request_key, saved)
                # 响应格式/原文问题逐组留待核查；接口故障停止后续请求，不碰运气。
                if not isinstance(exc, (ValueError, TypeError, KeyError)):
                    stopped = True
            if save_checkpoint:
                save_checkpoint(key, saved)
        scan_row = {**saved, 'checkpoint_id': key, 'batch_id': batch['batch_id'], 'basis_ids': ids,
                    'material_ids': sorted({part['material_id'] for part in batch['parts']}),
                    'pages': sorted({part['page'] for part in batch['parts']})}
        scans.append(scan_row)
        for row in saved.get('findings', []):
            findings.setdefault(row['finding_id'], row)
    return summarize(coverage_plan, scans, planned_scans=planned_scans), list(findings.values())
