"""倒查八列表投影；前端和导出共同消费保存结果，不重新分析或润色。"""
from copy import deepcopy
from datetime import date

from .retrospective_contracts import COLUMNS, REPORT_DIMENSIONS, report_dimension_id, summary
from .retrospective_sources import reading_limitations, repetitive_heading_evidence
from .retrospective_upstream_contracts import reference_supports_fact

TEMPLATE_ID = "contract-retrospective.v6"


def _shanghai_comparison_notice(finding, identity):
    refs = finding["basis"]
    if not refs or any(ref.get('doc_id') != 'SHJT-2026-8' for ref in refs):
        return ''
    effective_dates = {ref['document'].get('effective_from') for ref in refs}
    if len(effective_dates) != 1:
        return ''
    try:
        effective = date.fromisoformat(next(iter(effective_dates)))
        baseline = date.fromisoformat(identity['baseline_date']) if identity.get('baseline_date') else None
    except (TypeError, ValueError):
        return ''
    if baseline is None:
        return f'细则施行日为 {effective}，核查基准日尚未确定，具体适用性请人工判别。'
    if baseline < effective:
        return (f'核查基准日为 {baseline}，细则施行日为 {effective}。'
                '核查基准日早于细则施行日，具体适用性请人工判别。')
    return ''


def _compared_upstream(upstream):
    relation = (upstream.get('relationship') or {}).get('state')
    coverage = upstream.get('coverage') or {}
    batches = coverage.get('comparison_batches')
    if relation != 'matched':
        return False
    if batches is not None:
        return any(batch.get('state') in {'completed', 'partial'} for batch in batches)
    return bool(coverage.get('terms_total'))


def _dimension_label(report, dimension):
    upstream = report.get('upstream')
    if dimension == 'contract_terms_and_upstream' and upstream and not _compared_upstream(upstream):
        return '分包合同条款'
    return REPORT_DIMENSIONS[dimension]


def _narrative_summary(report):
    findings = report.get('findings') or []
    status = report.get('execution_status')
    if status == 'failed':
        return '本次合同文本核查未完成，尚未形成可供使用的核查意见。'
    if not findings:
        if status == 'partial' and not report.get('required_stages_completed'):
            return '本次已完成核查范围内未形成可列示的问题；尚未完成的范围不据此判断不存在风险。'
        return '本次核查范围内未发现可列示的合同问题或风险提示；本意见不判断合同效力或实际履约情况。'
    categories = list(dict.fromkeys(_dimension_label(report, report_dimension_id(
        row.get('review_dimension'))) for row in findings))
    category_text = '、'.join(categories)
    if status == 'partial' and not report.get('required_stages_completed'):
        return (f'本次已完成核查范围内列示{len(findings)}项合同问题或风险提示，涉及{category_text}。'
                '尚未完成的核查范围不据此判断不存在风险；本意见不判断合同效力或实际履约情况。')
    return (f'本次核查范围内列示{len(findings)}项合同问题或风险提示，涉及{category_text}。'
            '具体约定、条款位置及依据见附件一；本意见不判断合同效力或实际履约情况。')


def _project_mismatch_note(check, facts):
    if not check or check['state'] != 'mismatch':
        return ''
    for field, label in (('project_name', '项目'), ('contract_section', '标段')):
        values = {'general': {}, 'sub': {}}
        for ref in check.get('evidence', []):
            if not all(key in ref for key in ('role', 'start', 'end', 'file_name')):
                continue
            side = 'general' if ref['role'] == 'general' else 'sub'
            for fact in facts:
                if fact['field'] == field and reference_supports_fact(ref, fact):
                    value = ' '.join(fact['value'].split())
                    values[side].setdefault(''.join(value.split()).casefold(), (value, ref))
        if any(len(by_value) != 1 for by_value in values.values()):
            continue
        (general_key, general), (sub_key, sub) = (next(iter(by_value.items())) for by_value in values.values())
        if general_key in sub_key or sub_key in general_key:
            continue
        descriptions = [
            f'{side}{label}为「{value}」（《{ref["file_name"]}》PDF第{ref["page"]}页）'
            for side, (value, ref) in (('总包', general), ('分包', sub))
        ]
        return f'两份合同载明的{label}不一致。' + '，'.join(descriptions) + '。请核对上传的总包。'
    return ''


def _unconfirmed_relationship(upstream, sources):
    dimensions = {
        'project': ('项目或标段', ('总包项目或标段', '分包项目或标段'),
                    (('project_name', 'contract_section'), ('project_name', 'contract_section'))),
        'parties': ('签约主体', ('总包承包人', '分包发包方'), (('contractor',), ('employer',))),
        'scope': ('承包范围', ('总包承包范围', '分包承包范围'), (('scope',), ('scope',))),
    }
    checks = {check['dimension']: check for check in upstream['relationship'].get('checks', [])}
    facts = upstream.get('facts', [])
    project_mismatch = _project_mismatch_note(checks.get('project'), facts)
    by_material = {source['material_id']: source for source in sources}
    locations = {}
    for fact in facts:
        value = ''.join(fact['value'].split())
        if not value:
            continue
        for ref in fact.get('evidence', []):
            source = by_material.get(ref.get('material_id'))
            page = ref.get('page')
            if (source and type(page) is int and 1 <= page <= source['total_pages']
                    and value in ''.join((ref.get('quote') or '').split())):
                side = 'general' if source['role'] == 'general' else 'sub'
                positions = locations.setdefault((side, fact['field']), {})
                positions.setdefault(value, f"《{source['file_name']}》PDF第{page}页")
    missing, located, pending = [], [], []
    for dimension, (label, names, fields) in dimensions.items():
        if checks.get(dimension, {}).get('state') == 'matched' or (dimension == 'project' and project_mismatch):
            continue
        sides = [{value: position for field in side_fields
                  for value, position in locations.get((side, field), {}).items()}
                 for side, side_fields in zip(('general', 'sub'), fields)]
        absent = [name for name, positions in zip(names, sides) if not positions]
        if not absent:
            pending.append(label)
        else:
            missing.extend(absent)
            located.extend(f'{name}原文见{next(iter(positions.values()))}。'
                           for name, positions in zip(names, sides) if len(positions) == 1)
    parts = [project_mismatch] if project_mismatch else []
    if missing:
        parts.append('、'.join(missing) + '尚未核实。')
        parts.extend(located)
    if pending:
        parts.append('两份合同的' + '、'.join(pending) + '对应关系待核实。')
    return ''.join(parts)


def _scope_note(upstream, sources):
    subcontract_only = '本次未开展总分包条款比对。分包合同按其文本和纳入范围的依据核查。'
    if upstream.get('state') == 'not_applicable' or (
            sources and all(source['role'] in {'sub', 'labor'} for source in sources)):
        return '未提供总包合同。' + subcontract_only
    relation = (upstream.get('relationship') or {}).get('state')
    if relation in {'mismatch', 'uncertain'}:
        details = _unconfirmed_relationship(upstream, sources)
        return (details or '总包与分包的对应关系未获确认。') + subcontract_only
    if relation == 'matched' and (upstream.get('coverage') or {}).get('terms_total') == 0:
        return '已确认两份合同的对应关系，未取得可用于比对的总包条款。' + subcontract_only
    if _compared_upstream(upstream):
        return '本次核查合同条款、纳入范围的依据及总分包约定衔接。'
    if any(source['role'] == 'general' for source in sources):
        return subcontract_only
    return '本次核查合同条款及纳入范围的依据。'


def _overview(report, project_name, sources):
    roles = {'general': '总包合同', 'sub': '分包合同', 'labor': '分包合同'}
    materials = []
    for source in sources:
        label = roles.get(source.get('role'))
        if label:
            value = f'{label}《{source.get("file_name") or "未命名文件"}》'
            if value not in materials:
                materials.append(value)
    parts = []
    if project_name:
        parts.append(f'项目：{project_name}。')
    identity = report.get('identity') or {}
    signed = identity.get('contract_date')
    if signed:
        parts.append(f'合同签订日期：{signed}。')
    baseline = identity.get('baseline_date')
    baseline_source = identity.get('baseline_date_source')
    if baseline and baseline_source == 'manual':
        parts.append(f'人工选定的核查基准日为{baseline}。')
    elif baseline and baseline == signed and baseline_source == 'subcontract_signing_date':
        parts.append('核查基准日采用上述分包合同签订日。')
    elif baseline:
        parts.append(f'核查基准日为{baseline}。')
    elif baseline_source == 'unknown':
        parts.append('核查基准日尚未确定。')
    if materials:
        parts.append('核查材料：' + '；'.join(materials) + '。')
    parts.append(report['scope_note'])
    return ''.join(parts)


def build_report(result, *, project_name, sources):
    report = deepcopy(result)
    report['scope_note'] = _scope_note(report.get('upstream') or {}, sources)
    # 检查点回放同样经过证据支撑门槛；排除项只保留审计，不把解析噪声列为合同缺陷。
    excluded = [row for row in report['findings'] if row['kind'] == 'internal_ambiguity'
                and any(repetitive_heading_evidence(ref['quote']) for ref in row['evidence'])]
    if excluded:
        report.setdefault('excluded_findings', []).extend(
            {'finding': row, 'reason_code': 'repetitive_heading_evidence'} for row in excluded)
        report['findings'] = [row for row in report['findings'] if row not in excluded]
        report['analysis_summary'] = report.get('analysis_summary', report['summary'])
        if report['execution_status'] == 'completed':
            report['execution_status'] = 'partial'
        report['limitations'].append('候选的重复标题摘录不足以支持实质歧义，已隔离并保留审计；不据此判断该条款无问题。')
        report['summary'] = summary(report['execution_status'], len(report['findings']))
    # 模型对局部输入的自述不能直接成为全合同缺件/缺页事实。
    # 原始说明留审计，正文只投影可信覆盖记录和明确的分析边界。
    report['analysis_notes'] = deepcopy(report['limitations'])
    limits = reading_limitations(sources)
    limits.extend(report.get('knowledge', {}).get('limitations', []))
    if not report.get('identity', {}).get('baseline_date'):
        limits.append('未选择核查基准日且未能唯一确认合同签订日期；时效性规则不作确定性适用判断。')
    if report.get('final_review') and report['final_review']['state'] != 'completed':
        limits.append('报告收尾核验未完成，候选保留在审计记录中，未列为最终问题。')
    coverage = report['coverage']
    for batch in coverage.get('batches', []):
        if batch['state'] != 'completed':
            pages = '、'.join(str(page) for page in batch.get('pages', []))
            limits.append(f'PDF第{pages}页所在条款批次未完成核查，该范围不能判定无问题。' if pages else
                          '部分条款批次未完成核查，该范围不能判定无问题。')
    if coverage['batches_completed'] < coverage['batches_total'] and not any(
            batch['state'] != 'completed' for batch in coverage.get('batches', [])):
        limits.append('部分条款批次未完成核查，不能据本次结果判断全部合同无问题。')
    if report['analysis_notes']:
        limits.append('本次按合同分段核查，跨条款对照及规范适用范围有限；未确认事项不作为确定性不符合，未发现问题不代表合同全部合规。')
    if any('未自动重复付费调用' in note for note in report['analysis_notes']):
        limits.append('部分请求中断且结果未保存，系统未自动重复付费调用，对应范围尚未完成。')
    report['limitations'] = list(dict.fromkeys(limits))
    # 运行限制留在审计快照；试用正文仅展示已确认可列示的发现。
    report['analysis_summary'] = report.get('analysis_summary', report['summary'])
    report['summary'] = _narrative_summary(report)
    for finding in report['findings']:
        if finding['kind'] not in {'conditional_risk', 'current_notice'}:
            continue
        notice = _shanghai_comparison_notice(finding, report.get('identity') or {})
        if notice:
            finding['time_scope'] = 'current'
            if notice not in finding['message']:
                finding['message'] += '\n' + notice
    rows = []
    roles = {source['material_id']: '总包' if source['role'] == 'general' else '分包' for source in sources}
    for finding in report["findings"]:
        refs = finding["evidence"]
        basis = finding["basis"]
        internal = finding["kind"] == "internal_ambiguity"
        upstream = finding['kind'] == 'upstream_risk'
        label = {"historical": "历史适用判断", "current": "现行规则提示",
                 "contract_text": "合同文本分析"}[finding["time_scope"]]
        basis_files = [row["document"].get("doc_title") or row["doc_id"] for row in basis]
        basis_text = ['\n'.join(filter(None, [row.get('scope_context'), row["marker"] + "：" + row["quote"]]))
                      for row in basis]
        if internal:
            basis_files = ["合同内部约定对照（见本行原文）"]
            basis_text = ["\n".join(f"第{ref['page']}页 {ref['clause']}：{ref['quote']}" for ref in refs)]
        def source_label(ref):
            return f"{roles.get(ref['material_id'], '分包')}《{ref['file_name']}》" if upstream else ref['file_name']
        if upstream:
            upstream_refs = [ref for ref in refs if roles.get(ref['material_id']) == '总包']
            basis_files = ['总包合同签约主体原文' if finding.get('relationship_dimension') == 'parties'
                           else '上游合同约定（合同约束）']
            basis_text = [f"{source_label(ref)} PDF第{ref['page']}页 {ref['clause']}：{ref['quote']}" for ref in upstream_refs]
        cells = [project_name or "合同未唯一载明", "总包／分包" if upstream else "分包",
                 "\n".join(dict.fromkeys(source_label(ref) for ref in refs)),
                 "\n".join(dict.fromkeys(f"{source_label(ref) + ' ' if upstream else ''}PDF第{ref['page']}页 · {ref['clause']}" for ref in refs)),
                 "\n".join(f"{source_label(ref) + ' ' if upstream else ''}第{ref['page']}页：{ref['quote']}" for ref in refs),
                 f"【{finding['kind_label']}／{label}】\n{finding['message']}",
                 "\n".join(dict.fromkeys(basis_files)), "\n".join(basis_text)]
        rows.append({"finding_id": finding["finding_id"], "kind": finding["kind"],
                     "review_dimension": report_dimension_id(finding.get('review_dimension')),
                     "time_scope": finding["time_scope"], "cells": cells, "evidence": refs})
    report.update(review_mode="contract_retrospective", title="工程分包合同倒查意见（系统辅助分析）",
                  project_name=project_name, columns=list(COLUMNS), rows=rows,
                  materials=[{key: source.get(key) for key in
                              ("material_id", "file_name", "role", "relation", "file_sha256", "total_pages")}
                             for source in sources])
    blocks = [
        {"type": "heading", "text": "一、核查概况"},
        {"type": "paragraph", "text": _overview(report, project_name, sources)},
        {"type": "heading", "text": "二、重点问题"},
    ]
    for dimension in REPORT_DIMENSIONS:
        matching = [finding for finding in report['findings']
                    if report_dimension_id(finding.get('review_dimension')) == dimension]
        if matching:
            blocks.append({"type": "heading", "text": _dimension_label(report, dimension)})
            blocks.extend({"type": "paragraph", "text": finding['message']} for finding in matching)
    if not report['findings']:
        blocks.append({"type": "paragraph", "text": "本次未形成可列示的合同问题或风险提示。"})
    blocks.extend([
        {"type": "heading", "text": "三、综合核查意见"},
        {"type": "paragraph", "text": report['summary']},
        {"type": "heading", "text": "附件一：合同问题与风险明细表"},
    ])
    blocks.append({"type": "table", "headers": list(COLUMNS), "rows": [row["cells"] for row in rows],
                   "widths": [.07, .05, .10, .10, .20, .22, .10, .16]})
    report["template_document"] = {
        "template_id": TEMPLATE_ID, "title": report["title"], "page_layout": "landscape",
        "blocks": blocks,
    }
    return report
