# -*- coding: utf-8 -*-
"""BS 专属严重度/置信档位矩阵（A-13）。

需求：严重度 high/medium/low 由**确定性关键词矩阵**判定（矩阵与出处见
`data/severity_matrix.json` 的 source_note：档位属内部呈现档位，重项对齐
《沪交行规〔2026〕8号》第十五～十七条）；置信档由**证据链完备性**判定；本域补充
「命中『转包/违法分包』类结论时，一律不得直接给 high 置信，须人工复核后升档」。

红线与纪律：
- **严禁 import 或修改** `startup_conditions_check.compliance_verdict`（RD-2）：其
  `_HIGH_MARKERS` 是**安全施工语境**（坍塌/脚手架/无证/过期…），BS 是**分包合规语境**
  （转包/违法分包/挂靠/资质/报审/社保）。直接复用会语义错配——把 BS 最重的「转包」判
  medium、把次重的「资质过期」判 high。故本模块自建 BS 专属矩阵（词表外置
  `data/severity_matrix.json`，规则=数据、可 diff）。
- 档位名词与本仓既有 high/medium/low 统一（不新造语义）。
- 零 LLM（RD-6）：纯文本匹配 + 结构化证据判据，无模型调用。
"""
from __future__ import annotations

from .models import STATUS_HIT, STATUS_UNDECIDABLE
from .rules import load_severity_matrix

# 档位 → 中文标签（报告/前端 DTO 共用表源；标签不得含实现侧词汇，见禁词表）
SEVERITY_LABELS = {"high": "高", "medium": "中", "low": "低"}
CONFIDENCE_LABELS = {"high": "高置信", "medium": "中置信", "low": "低置信"}


def _matrix() -> dict:
    """直读矩阵数据文件（不缓存）：改数据 → 重新核查 → 档位变化（规则=数据）。"""
    return load_severity_matrix()


def _finding_text(finding) -> str:
    """判定靶文本：结论正文 + 出处（message 为主，source 补充域提示）。"""
    return "%s %s" % (getattr(finding, "message", "") or "",
                      getattr(finding, "source", "") or "")


def map_bs_severity(finding) -> str:
    """确定性严重度判定（BS 分包合规语境）：high > low > medium（默认 medium）。

    与 OC `map_severity` 同档位、同优先级（high 先判、low 次之、medium 再之、默认
    medium），但**词表为 BS 专属**：转包/违法分包/挂靠类判 high；资质/一致性/报审类
    判 medium；疑似/辨认不清等识别不确定判 low。默认 medium（宁可保守交人工，与 OC
    默认档一致），避免把中性描述误升为重。

    **high 只给「成立性结论」（BS-P1-14）**：high 词表是领域词（转包/违法分包/再分包），
    它们在**未成立**的结论里出现，只说明"本条讨论这个领域"，不说明"违规成立"。实测
    92 条金样里 15 条 `undecidable` 因此被误升 high，三类语境分别是：
      - 在说依赖：T15-07「需先取得 W16、V16（**违法分包**排除）核查结果方可判定」；
      - 在说缺件：W16-06「**再分包**/转交与实际实施的证据链不齐」；
      - 是条款标题：W16-07「其他**违法分包**行为」、T15-10「其他**转包**行为」。
    误升的实际后果不只是标签难看：报告按 `_SEVERITY_RANK` 严重度降序排，这些待确认项
    会排到真正的命中项前面，且 confirmed 闸门只拦 `severity=high` 的 `hit`（`router:
    _require_no_pending_hits`），档位失真会让"高风险待处置"这个名单失去含义。
    故 high 需 `status=hit` 才授予，否则按矩阵 `severity_gate.downgrade_to` 降档。
    """
    data = _matrix().get("severity", {})
    text = _finding_text(finding)
    matched = None
    for level in ("high", "low", "medium"):
        if any(word and word in text for word in data.get(level, [])):
            matched = level
            break
    if matched is None:
        return _matrix().get("default", "medium")
    if matched == "high" and not _high_allowed(finding):
        return _matrix().get("severity_gate", {}).get("downgrade_to", "medium")
    return matched


def _high_allowed(finding) -> bool:
    """high 是否可授予该条结论（数据驱动：`severity_gate.high_requires_hit`）。"""
    gate = _matrix().get("severity_gate", {})
    if not gate.get("high_requires_hit", False):
        return True
    return getattr(finding, "status", None) == STATUS_HIT


def _evidence_anchored(finding) -> bool:
    """证据锚定判据（BS 适配）。

    语义来源：`startup_conditions_check/compliance_verdict.py` `_evidence_anchored`
    （证据必须能解析出锚点，锚定失败则置信降档）。OC 结论的 evidence 是「文件名 + 第N页」
    字符串列表；BS 结论为**结构化证据**（quote 引文 + location_mode 三档定位），故按同
    口径就地适配：引文非空且定位到页（precise 页内验中 / page 可信页码）= 锚定成立；
    计算类无引文结论（location_mode 为 None）不算锚定。
    本模块内实现而非跨模块引用 `_` 私有名（依赖治理红线：禁止跨模块引用私有名）。
    """
    quote = (getattr(finding, "quote", None) or "").strip()
    return bool(quote) and getattr(finding, "location_mode", None) in ("precise", "page")


def bs_confidence(finding) -> str:
    """BS 置信档（high/medium/low）：证据链完备性判定（非分数阈值）。

    - low：结论未成立（不可判定）——缺证据，如实交人工；
    - high：命中/未命中且证据锚定（引文 + 页内定位）——确凿结论；
    - medium：有结论但证据未锚定（计算类无引文/流程类）——可信但非逐字锚定。

    域补充红线：映射为 high 档（转包/违法分包类）的结论**一律不得给 high 置信**，
    降为 medium（须人工复核后升档）——避免把最重的违法结论在置信上「一步到位」。
    """
    status = getattr(finding, "status", None)
    if status == STATUS_UNDECIDABLE:
        return "low"
    cap_levels = _matrix().get("confidence", {}).get("cap_levels", ["high"])
    if map_bs_severity(finding) in cap_levels:
        return "medium"
    if _evidence_anchored(finding):
        return "high"
    return "medium"