# -*- coding: utf-8 -*-
"""oc-validity-engine-v1（2026-09-19，P0-3 证书有效期引擎）。

专家核查四查之三「时效性」：以拟开工日（任务基准日）为锚，批量判定报审证件
是否过期 / 即将到期。当前智能体只有零散的「日期冲突」缺陷，没有统一的
「签发日/有效期至/基准日」三元组抽取与过期判定——本模块补齐该比对维度。

设计纪律（与匹配链路一致）：
- **确定性优先**：正则抽取 + 逐字上下文摘录（可溯源），不调 LLM、不编造日期；
- **fail-safe**：抽取不到有效期 → `unknown`（交人工，不判过期）；
- **可解释**：每条记录带 原文摘录（snippet）+ 页码 + 文件名，供 finding 定位。

判定口径：
- `expired`       有效期至 < 基准日 → 硬缺陷（调用方升级 nonconforming）；
- `expiring_soon` 基准日 ≤ 有效期至 ≤ 基准日+30 天 → 交人工复核提示；
- `valid`         有效期至 > 基准日+30 天；
- `unknown`       未抽取到有效期（含「长期」等无限期表述→不产生记录）。
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from .date_reliability import build_date_fact, date_range_separator, date_review_note, date_token_spans, date_tokens, parse_date_text, source_date_facts


# ---- 有效期抽取 ----

# 有效期标签（按具体度排序）：有效期至/有效期至：/有效日期至/有效期到/有效期：/营业期限至
# 注意①「有效期3年」类**时长**表述不是日期——由「标签后紧邻日期」约束天然排除；
# 注意②「检验有效期至」是车辆年检日期（行驶证无有效期概念，VETO 判例）——负向后顾排除。
_EXPIRY_LABEL_RE = re.compile(
    r"(?:(?<!检验)(?:有效期至|有效日期至|有效期到|有效期[:：])|营业期限(?:至|[:：])|营业期限)"
)
_LONG_TERM_RE = re.compile(r"(长期|永久|不限)")
_ISSUE_LABEL_RE = re.compile(r"(签发日期|发证日期|核发日期|颁发日期|检定日期|校准日期|检测日期|出具日期|核验日期)")
# 车辆/驾驶证件域外排除（VETO 判例：行驶证年检状态需人工核验，不构成证书过期缺陷）
_VEHICLE_DOC_RE = re.compile(r"(行驶证|驾驶证|机动车|车辆|车牌)")
_SNIPPET_RADIUS = 24
_INLINE_DATE_HTML_RE = re.compile(r"</?(?:br|span|b|strong|i|em|u|font|small|sup|sub)\b[^>]*>", re.I)
_FULL_RANGE_DATE_RE = re.compile(r"\d{4}\s*[年./-]\s*\d{1,2}\s*[月./-]\s*\d{1,2}\s*日?")
_RANGE_QUALIFIER_RE = re.compile(r"或|待|不详|未知|暂定|约|[?？]")


def _range_problem(tail, spans, has_end_token):
    """完整token取值之外保留区间限定语；不跨后续独立字段猜截止日。"""
    if not spans:
        return ""
    clean = lambda value: _INLINE_DATE_HTML_RE.sub(" ", value)
    first_start, first_end, first_raw = spans[0]
    if not has_end_token and not date_range_separator(clean(tail[first_end:]), prefix=True):
        return ""
    if not has_end_token:
        return "有效期区间终日尚未读清，不能用起日代替"
    _start, end, end_raw = spans[1]
    if clean(tail[:first_start]).strip(" \t\r\n:："):
        return "有效期区间带近似或不明确前缀，需核对原件"
    if not _FULL_RANGE_DATE_RE.fullmatch(first_raw) or not _FULL_RANGE_DATE_RE.fullmatch(end_raw):
        return "有效期区间起止日不完整，不能按月末或任一端代替"
    start_date, end_date = parse_date_text(first_raw), parse_date_text(end_raw)
    if start_date is None or end_date is None or end_date < start_date:
        return "有效期区间日期无效或起止倒序，需核对原件"
    remaining = clean(tail[end:])
    continuation = remaining.lstrip(" \t\r\n（(，,、")
    if (re.match(r"[\d年月日./-]", remaining) or _RANGE_QUALIFIER_RE.match(continuation)
            or date_range_separator(continuation, prefix=True) or _FULL_RANGE_DATE_RE.match(continuation)):
        return "有效期区间存在多个终日或暂定等歧义，需核对原件"
    return ""


def _unresolved_range_fact(fact, raw, reason):
    fact = {**fact, "raw_text": raw, "parsed_date": None, "reliability": "unresolved", "reason": reason}
    fact.pop("source_span", None)  # 原字段全文不再是先前单日期token的坐标。
    return fact


def _find_labeled_dates(text: str, label_re: re.Pattern) -> list[tuple[str, date, int]]:
    """在文本中找「标签 + 紧邻日期」组合，返回 [(标签, 日期, 标签位置)]。"""
    out: list[tuple[str, date, int]] = []
    for m in label_re.finditer(text):
        label = m.group(0)
        tail = text[m.end():]
        spans = date_token_spans(tail)
        window = tail[:spans[0][1]] if spans and spans[0][0] <= 24 else ""
        if _LONG_TERM_RE.match(window.strip()):
            continue  # 「长期有效」→ 无限日期，不产生记录
        parsed = parse_date_text(window)
        if parsed is not None:
            out.append((label, parsed, m.start()))
    return out


def _classify_doc_kind(text: str) -> str:
    """证件类别识别（用于 finding 文案），按特征词判定。"""
    if "特种作业" in text or "操作证" in text:
        return "特种作业操作证"
    if "安全生产考核合格证" in text or re.search(r"[ABC]\d?证", text):
        return "安全生产考核合格证"
    if "安全生产许可证" in text:
        return "安全生产许可证"
    if "营业执照" in text or "营业期限" in text:
        return "营业执照"
    if ("检定" in text or "校准" in text) and "证书" in text:
        return "检定/校准证书"
    if "保险" in text:
        return "保险凭证"
    if "资质" in text and "证书" in text:
        return "资质证书"
    return "证照文件"


def extract_validity_records(
    page_texts: dict[int, str],
    *,
    file_name: str = "",
    baseline: date | None = None,
    asset_id=None,
) -> list[dict]:
    """页级文本 → 有效期记录列表。

    返回记录字段：kind/doc_hint/page/expiry_date(issue_date)/verdict/snippet/date_verification。
    同页多个有效期逐条产出；标签明确但读不清保留unknown及原值，无标签不凭空报缺。
    """
    records: list[dict] = []
    for page_no in sorted(page_texts or {}):
        text = str(page_texts.get(page_no) or "")
        if not text.strip():
            continue
        # 一些 OCR 返回证照 JSON：字段引号不是日期限定语，不能让它们干扰
        # 既有区间解析。仅接受唯一、原文未转义的安全生产许可证期限字段；
        # 解析后的证据仍定位到页内的原始连续文本。
        fenced = re.fullmatch(r"\s*```(?:json)?\s*\n([\s\S]*?)\n```\s*", text, re.I)
        json_text = fenced.group(1) if fenced else text
        try:
            pairs = json.loads(json_text, object_pairs_hook=list)
        except (ValueError, TypeError):
            pairs = None
        if isinstance(pairs, list) and all(isinstance(p, tuple) and len(p) == 2 for p in pairs):
            titles = [value for key, value in pairs if key == "证照标题"]
            periods = [value for key, value in pairs if key == "营业期限"]
            if (len(titles) == len(periods) == 1 and isinstance(titles[0], str)
                    and "安全生产许可证" in titles[0] and isinstance(periods[0], str)):
                raw_field = '"营业期限": ' + json.dumps(periods[0], ensure_ascii=False)
                if raw_field not in text:
                    match = re.search(r'"营业期限"\s*:\s*"([^"\\]*)"', text)
                    raw_field = match.group(0) if match and match.group(1) == periods[0] else ""
                if raw_field:
                    period = re.sub(r"^<\d+(?:,\d+){4}>,", "", periods[0])
                    structured = extract_validity_records(
                        {page_no: titles[0] + "\n营业期限：" + period},
                        file_name=file_name, baseline=baseline, asset_id=asset_id)
                    for record in structured:
                        record["snippet"] = raw_field
                        record["source_format"] = "structured_certificate_text"
                        # 日期是在规范化视图内解析的，不能将视图坐标伪装为原页坐标。
                        for fact in (record["date_verification"]["facts"]
                                     + record["validity_start_facts"]):
                            fact.pop("source_span", None)
                    records.extend(structured)
                    continue
        # 车辆/驾驶证件整文件域外排除（VETO 判例：行驶证年检状态需人工核验）
        if _VEHICLE_DOC_RE.search(file_name or ""):
            continue
        for match in _EXPIRY_LABEL_RE.finditer(text):
            label, pos = match.group(0), match.start()
            tail = text[match.end():]
            spans = date_token_spans(tail)
            window = tail[:40].strip()
            if _LONG_TERM_RE.match(window):
                continue
            # Select complete tokens from the original page, never parse an arbitrary character cut.
            if spans and spans[0][0] <= 24:
                last = spans[0][1]
                has_end_token = False
                if len(spans) > 1 and date_range_separator(_INLINE_DATE_HTML_RE.sub(" ", tail[last:spans[1][0]])):
                    last = spans[1][1]
                    has_end_token = True
                window = tail[:last].strip()
            else:
                spans = []
            tokens = date_tokens(window) if spans else []
            # 无日期标签值不凭空建缺件；有不可读识读值则保留必要日期待核。
            if not tokens and not re.search(r"模糊|不清|无法辨认|无法识读", window):
                continue
            facts, other_facts = source_date_facts(_INLINE_DATE_HTML_RE.sub(" ", label + window), purpose="expiry", file_name=file_name,
                page=page_no, asset_id=asset_id, context=label + window)
            fact = facts[0] if facts else build_date_fact(window, purpose="expiry", file_name=file_name,
                page=page_no, asset_id=asset_id, context=label + window)
            problem = _range_problem(tail, spans, has_end_token) if spans else ""
            if problem:
                # 截止日后的“或另一日/暂定”和日期前的“约”必须保留，不能只留首两个token。
                end_of_field = re.search(r"[；;。\n]|</(?:p|td|div)>", tail[last:], re.I)
                raw_field = tail[:last + end_of_field.start()] if end_of_field else tail[:max(last, 240)]
                fact = _unresolved_range_fact(fact, raw_field.strip(), problem)
            expiry = parse_date_text(fact["parsed_date"]) if fact["parsed_date"] else None
            context = text[max(0, pos - 120): pos + 60] or text[:180]
            # 标签上下文属车辆/驾驶证件 → 跳过该条（年检状态域外）
            if _VEHICLE_DOC_RE.search(context):
                continue
            value_end = match.end() + last if spans else match.end() + len(window)
            snippet = text[max(0, pos - _SNIPPET_RADIUS):max(pos + _SNIPPET_RADIUS + 24, value_end)]
            issue_dates = _find_labeled_dates(text, _ISSUE_LABEL_RE)
            issue = issue_dates[0][1] if issue_dates else None
            issue_label = _ISSUE_LABEL_RE.match(text, issue_dates[0][2]) if issue_dates else None
            issue_raw = date_tokens(text[issue_label.end():]) if issue_label else []
            issue_fact = build_date_fact(issue_raw[0] if issue_raw else issue.isoformat(), purpose="issue",
                                         file_name=file_name, page=page_no, asset_id=asset_id) if issue else None
            if issue_fact and issue_fact["reliability"] != "readable":
                issue = None
            records.append({
                "kind": _classify_doc_kind(context),
                "doc_hint": file_name,
                "page": page_no,
                "label": label,
                "issue_date": issue.isoformat() if issue else None,
                "expiry_date": expiry.isoformat() if expiry else None,
                "verdict": judge_expiry(expiry, baseline) if expiry else "unknown",
                "snippet": re.sub(r"\s+", " ", snippet).strip(),
                "date_verification": {"status": "readable" if expiry else "unresolved",
                                      "purpose": "expiry", "facts": [fact]},
                "issue_date_fact": issue_fact,
                "validity_start_facts": [f for f in other_facts if f["purpose"] == "validity_start"],
            })
    by_start = {}
    for record in records:
        starts = [fact.get("parsed_date") for fact in record["validity_start_facts"] if fact.get("parsed_date")]
        if len(starts) == 1:
            by_start.setdefault((record["page"], starts[0]), []).append(record)
    for group in by_start.values():
        if len({record["expiry_date"] for record in group if record["expiry_date"]}) > 1:
            for record in group:
                record.update(expiry_date=None, verdict="unknown")
                verification = record["date_verification"]
                verification["status"] = "unresolved"
                verification["facts"] = [_unresolved_range_fact(fact, record["snippet"], "同一页同一起日对应多个截止日，需核对具体证件范围")
                                         for fact in verification["facts"]]
    return records


def judge_expiry(expiry: date, baseline: date | None = None, soon_days: int = 30) -> str:
    """以基准日判定有效期状态：expired / expiring_soon / valid。"""
    base = baseline or datetime.now().date()
    if expiry < base:
        return "expired"
    if expiry <= base + timedelta(days=soon_days):
        return "expiring_soon"
    return "valid"


# ---- finding 生成 ----

_VERDICT_TITLE = {
    "expired": "证书已过期",
    "expiring_soon": "证书即将到期",
}


def validity_findings_from_records(records: list[dict]) -> list[dict]:
    """有效期记录 → finding 结构（title/detail/evidence/severity/_pages），供合规链路消费。

    - expired → severity=high（对应 severity 矩阵「过期/失效」硬缺陷）；
    - expiring_soon → severity=medium（不判硬缺，交人工复核是否需复检/换证）。
    """
    findings: list[dict] = []
    for record in records or []:
        verdict = str(record.get("verdict") or "")
        if verdict == "unknown" and (record.get("date_verification") or {}).get("status") == "unresolved":
            finding = {
                "kind": "validity", "validity_verdict": "unknown", "title": "有效期日期待核",
                "date_verification": record["date_verification"], "severity": "low",
                "evidence": [record.get("doc_hint") or "证据文件"],
                "quote": record.get("snippet") or "",
                "audit": {"date_reliability_applied": True},
            }
            finding["detail"] = date_review_note(finding) + "；必要日期未核清，不判定真实过期或有效。"
            if isinstance(record.get("page"), int) and record["page"] > 0:
                finding["_pages"] = [record["page"]]
            findings.append(finding)
            continue
        if verdict not in _VERDICT_TITLE:
            continue
        kind = str(record.get("kind") or "证照文件")
        doc = str(record.get("doc_hint") or "").strip() or "证据文件"
        expiry = str(record.get("expiry_date") or "")
        issue = str(record.get("issue_date") or "")
        page = record.get("page")
        snippet = str(record.get("snippet") or "").strip()
        lines = [
            f"{kind}（{doc}）{_VERDICT_TITLE[verdict]}，有效期至 {expiry}。",
        ]
        if issue:
            lines.append(f"签发/检定日期 {issue}。")
        if snippet:
            lines.append(f"原文摘录：{snippet[:60]}")
        if verdict == "expired":
            lines.append("证书已超过有效期，属开工硬条件不满足，需复检或重新取证后方可使用。")
        else:
            lines.append("证书临近有效期，请人工复核是否需安排复检/换证。")
        finding = {
            "kind": "validity",
            "validity_verdict": verdict,
            "title": f"{kind}{_VERDICT_TITLE[verdict]}（有效期至 {expiry}）",
            "detail": " ".join(lines),
            "evidence": [doc],
            "severity": "high" if verdict == "expired" else "medium",
            "date_verification": record.get("date_verification") or {},
            "audit": {"date_reliability_applied": True,
                      "issue_date_fact": record.get("issue_date_fact"),
                      "validity_start_facts": record.get("validity_start_facts") or []},
        }
        if isinstance(page, int) and page > 0:
            finding["_pages"] = [page]
        findings.append(finding)
    return findings
