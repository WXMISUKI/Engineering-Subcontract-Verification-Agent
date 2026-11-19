"""倒查八列表投影；前端和导出共同消费保存结果，不重新分析或润色。"""
from copy import deepcopy

from .retrospective_contracts import COLUMNS
from .retrospective_sources import reading_limitations

TEMPLATE_ID = "contract-retrospective.v3"


def build_report(result, *, project_name, sources):
    report = deepcopy(result)
    # 模型对局部输入的自述不能直接成为全合同缺件/缺页事实。
    # 原始说明留审计，正文只投影可信覆盖记录和明确的分析边界。
    report['analysis_notes'] = deepcopy(report['limitations'])
    limits = reading_limitations(sources)
    limits.extend(report.get('knowledge', {}).get('limitations', []))
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
    report['summary'] = report['summary'].replace('部分范围受限，详见核查限制。', '')
    rows = []
    for finding in report["findings"]:
        refs = finding["evidence"]
        basis = finding["basis"]
        internal = finding["kind"] == "internal_ambiguity"
        label = {"historical": "历史适用判断", "current": "现行规则提示",
                 "contract_text": "合同文本分析"}[finding["time_scope"]]
        basis_files = [row["document"].get("doc_title") or row["doc_id"] for row in basis]
        basis_text = ['\n'.join(filter(None, [row.get('scope_context'), row["marker"] + "：" + row["quote"]]))
                      for row in basis]
        if internal:
            basis_files = ["合同内部约定对照（见本行原文）"]
            basis_text = ["\n".join(f"第{ref['page']}页 {ref['clause']}：{ref['quote']}" for ref in refs)]
        cells = [project_name or "合同未唯一载明", "分包",
                 "\n".join(dict.fromkeys(ref["file_name"] for ref in refs)),
                 "\n".join(dict.fromkeys(f"PDF第{ref['page']}页 · {ref['clause']}" for ref in refs)),
                 "\n".join(f"第{ref['page']}页：{ref['quote']}" for ref in refs),
                 f"【{finding['kind_label']}／{label}】\n{finding['message']}",
                 "\n".join(dict.fromkeys(basis_files)), "\n".join(basis_text)]
        rows.append({"finding_id": finding["finding_id"], "kind": finding["kind"],
                     "time_scope": finding["time_scope"], "cells": cells, "evidence": refs})
    report.update(review_mode="contract_retrospective", title="分包合同文本倒查报告（系统辅助分析）",
                  project_name=project_name, columns=list(COLUMNS), rows=rows,
                  materials=[{key: source.get(key) for key in
                              ("material_id", "file_name", "role", "relation", "file_sha256", "total_pages")}
                             for source in sources])
    coverage = report["coverage"]
    report["template_document"] = {
        "template_id": TEMPLATE_ID, "title": report["title"], "page_layout": "landscape",
        "blocks": [{"type": "heading", "text": "一、发现摘要"},
                   {"type": "paragraph", "text": report["summary"]},
                   {"type": "heading", "text": "二、合同问题与风险明细"},
                   {"type": "table", "headers": list(COLUMNS), "rows": [row["cells"] for row in rows],
                    "widths": [.07, .05, .10, .10, .20, .22, .10, .16]}],
    }
    return report
