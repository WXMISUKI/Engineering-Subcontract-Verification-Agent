"""倒查八列表投影；前端和导出共同消费保存结果，不重新分析或润色。"""
from copy import deepcopy

from .retrospective_contracts import COLUMNS

TEMPLATE_ID = "contract-retrospective.v1"


def build_report(result, *, project_name, sources):
    report = deepcopy(result)
    rows = []
    for finding in report["findings"]:
        refs = finding["evidence"]
        basis = finding["basis"]
        internal = finding["kind"] == "internal_ambiguity"
        label = {"historical": "历史适用判断", "current": "现行规则提示",
                 "contract_text": "合同文本分析"}[finding["time_scope"]]
        basis_files = [row["document"].get("doc_title") or row["doc_id"] for row in basis]
        basis_text = [row["marker"] + "：" + row["quote"] for row in basis]
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
        "blocks": [{"type": "paragraph", "text": report["scope_note"]},
                   {"type": "heading", "text": "一、发现摘要"},
                   {"type": "paragraph", "text": report["summary"]},
                   {"type": "paragraph", "text": f"已完成{coverage['batches_completed']}/{coverage['batches_total']}批条款检查；原文位置均为PDF物理页码。"},
                   {"type": "heading", "text": "二、合同问题与风险明细"},
                   {"type": "table", "headers": list(COLUMNS), "rows": [row["cells"] for row in rows],
                    "widths": [.07, .05, .10, .10, .20, .22, .10, .16]},
                   {"type": "heading", "text": "三、核查限制"},
                   *[{"type": "paragraph", "text": text} for text in report["limitations"]],
                   {"type": "paragraph", "text": "本报告无需逐项人工确认；阅读者可对照原件核对或提出纠错，系统原始分析结果保留。"}],
    }
    return report
