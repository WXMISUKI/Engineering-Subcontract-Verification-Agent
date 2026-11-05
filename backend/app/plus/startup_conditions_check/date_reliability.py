"""日期用途和识读可靠性公共门禁；不读库、不联网、不用正文质量分冒充字级置信。"""
from __future__ import annotations

import re
from copy import deepcopy
from datetime import date, datetime, timedelta

_YMD_RE = re.compile(r"(?<!\d)(\d{4})\s*[年\-/.]\s*(\d{1,2})\s*[月\-/.]\s*(\d{1,2})\s*日?(?!\d)")
_YM_RE = re.compile(r"(?<!\d)(\d{4})\s*[年\-/.]\s*(\d{1,2})\s*月?(?!\d)")
_DATE_TOKEN_RE = re.compile(
    r"(?<![\dA-Za-z])(?:"
    r"(?:\d{4}|[12]\d[\d?？~〇Oo]{2})\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日?"
    r"|(?:\d{4}|[12]\d[\d?？~〇Oo]{2})\s*[年\-/.]\s*\d{1,2}\s*[月\-/.]\s*\d{1,2}\s*日?"
    r"|(?:\d{4}|[12]\d[\d?？~〇Oo]{2})\s*[年\-/.]\s*\d{1,2}\s*月?"
    r"|(?:\d{1,4})\s*年(?:\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日?)?)?"
    r"|\d{1,2}\s*月\s*\d{1,2}\s*日)(?!\d)"
)
_UNCLEAR_RE = re.compile(r"字迹.{0,5}(?:不清|模糊)|(?:日期|年份|时间|有效期).{0,8}(?:不清|模糊|无法辨认|识别错误)|OCR.{0,5}(?:错误|噪声)|无法辨认|辨认不清|书写不清")
_EXPIRY_RE = re.compile(r"有效期|有效日期|营业期限|校准期|检定期|复审日期|已过期|即将到期|超期未检")
_EXPLICIT_EXPIRY_FIELD_RE = re.compile(r"有效期|有效日期|营业期限|截止日期|期限至|截至|下次(?:校准|复校|检定)日期")
_ADVISORY_INTERVAL_RE = re.compile(r"建议(?:复校|校准|检定)?(?:时间)?间隔\s*\d+(?:\.\d+)?\s*(?:个?月|年)")
_SIGNING_YEAR_NOISE_RE = re.compile(
    r"早于项目当前阶段多年|与其他资料(?:日期)?多为|使用了过期模板|年份(?:明显)?不一致"
    r"|(?:日期|年份|时间).{0,18}(?:早于|晚于|先于|迟于).{0,24}(?:项目|任务|当前|其他资料|建卡)"
    r"|(?:项目|任务|当前|其他资料|建卡).{0,24}(?:日期|年份|时间).{0,18}(?:冲突|不符|不一致|异常)"
)
_BASELINE_COMPARISON_RE = re.compile(
    r"(?:早于|晚于|先于|迟于|远早于|远晚于).{0,28}(?:项目|任务|当前|其他资料|建卡|关联报验单)"
    r"|(?:项目|任务|当前|其他资料|建卡|关联报验单).{0,28}(?:早于|晚于|先于|迟于|冲突|不符|不一致)"
)
_EXPLICIT_ORDER_PAIR_RE = re.compile(
    r"(?:报审|审批|批准|批复|交底|进场|开工|复核|验收|检测|签发|发证|施工|教育|培训|编制|提交|签署)"
    r".{0,24}(?:早于|晚于|先于|迟于)"
    r".{0,24}(?:报审|审批|批准|批复|交底|进场|开工|复核|验收|检测|签发|发证|施工|教育|培训|编制|提交|签署)"
)
_SEQUENCE_RE = re.compile(r"倒挂|晚于|早于|先于|迟于|先后.{0,5}(?:冲突|异常)|时序|审批.{0,12}交底|交底.{0,12}进场")
_DATE_TITLE_RE = re.compile(r"日期|年份|落款|时间|时序|顺序|有效期|过期|到期|超期未检|晚于|早于|先于|迟于")
_YEAR_COMPARISON_RE = re.compile(
    r"当前年份|项目背景.{0,10}年|(?:项目|任务)(?:当前|核查|基准)?(?:日期|年份|时间|基准日)"
    r"|(?:建卡|其他资料|关联报验单).{0,18}(?:日期|年份|时间)"
    r"|与.{0,10}(?:年份|\d{4}年).{0,10}(?:不符|冲突|不一致)"
    r"|年份.{0,6}(?:不符|异常)|超出(?:当前)?合理(?:时间)?范围|(?:属|为|是)未来(?:日期|时间|年份)"
)
_REQUIRED_DATE_RE = re.compile(r"(?:日期|签署时间).{0,8}(?:必须|必填|受管要求)|(?:必须|必填|法定|受管要求).{0,12}(?:日期|签署时间)")
_PAGE_RE = re.compile(r"---\s*Page\s*(\d+)\s*---", re.IGNORECASE)
_DATE_ROLE_LABEL_RE = re.compile(
    r"(?P<expiry>有效期(?:限)?|有效日期|营业期限|下次(?:检定|校准|复审|检验)(?:日期)?|应复审日期|复审日期)"
    r"|(?P<issue>签发日期|发证日期|核发日期|颁发日期|检定日期|校准日期|检测日期|出具日期|核验日期)"
)
_RANGE_SEPARATOR_RE = re.compile(r"\s*(?:至|到|—+|–+|－+|~|～|-)\s*")
_NUMBER_LABEL_RE = re.compile(r"(?:编号|条号|条款)\s*[:：]?\s*$")
_EXPIRY_END_CLAIM_RE = re.compile(r"(?:有效期(?:至|到|截止)|有效日期至|(?:到期|截止)日期(?:为|是)?)\s*[:：]?\s*$")
_NON_EXPIRY_DATE_LABEL_RE = re.compile(r"签署日期|签字日期|签订日期|签名日期|会议日期|报告日期|教育日期|施工期限|审批日期|批准日期|交底日期|进场日期")
_FUTURE_CLAIM_RE = re.compile(r"未来(?:时间|日期|年份)|(?:日期|年份|时间).{0,8}未来")
_EXPIRED_CLAIM_RE = re.compile(r"已过期|已超过有效期|已超出有效期|超期未检")
_CONSISTENT_ORDER_RE = re.compile(r"(?:早于|晚于)[^。；]{0,32}符合|(?:顺序|时序|逻辑)[^。；]{0,8}正确")
_INDEPENDENT_DEFECT_RE = re.compile(
    r"(?:签字|签名|签章|盖章|公章|序列号|型号|主体|设备编号)[^。；，]{0,12}(?:缺失|缺少|空白|不一致|不符|未填写)"
    r"|(?:缺失|缺少|未提供)[^。；，]{0,12}(?:签字|签名|签章|盖章|公章|序列号|型号)"
    r"|(?:签字|签名|签章|签认|交底闭环|人员)[^。；，]{0,16}(?:不全|不完整|人数不足|为空|闭环缺失|闭环不完整|不完全对应|对应关系存疑)"
    r"|(?:不全|不完整|人数不足|为空|闭环缺失|闭环不完整|不完全对应|对应关系存疑)[^。；，]{0,16}(?:签字|签名|签章|签认|交底闭环|人员)"
)
_EXPLICIT_ORDER_CONFLICT_RE = re.compile(
    r"倒挂|时序[^。；]{0,10}(?:冲突|异常)|(?:审批|批准|批复)[^。；]{0,20}晚于[^。；]{0,10}交底"
    r"|(?:早于|晚于|先于|迟于)[^。；]{0,20}(?:不符合|不满足|冲突|异常)"
)
DATE_RELIABILITY_RULE = (
    "日期先按用途核查：仅以可可靠读取的有效期、校准/检定期限或明确工序先后作判断，"
    "不得因签署或会议年份不同于当前年份而报错。日期事实保留用途、原文、文件和页码；"
    "非关键落款的孤立可疑年份只留识读审计；必要日期读不清则指向具体原页核对，"
    "不得断言真实过期/倒挂、不得自动改年，也不得一概豁免手写日期。"
)


def parse_date_text(text: str) -> date | None:
    """解析完整年月日/年月；年月按月末。仅解析文字，不声称已核原图。"""
    text = str(text or "").strip()
    match = _YMD_RE.search(text)
    if match:
        try:
            return date(*(int(value) for value in match.groups()))
        except ValueError:
            return None
    match = _YM_RE.search(text)
    if match:
        # 月份后已经出现明确的占位/疑似日，不能把不完整的“月？”静默折算为月末。
        tail = text[match.end():]
        if re.match(r"\s*[?？□_*Xx〇O]{1,4}\s*日?", tail):
            return None
        year, month = (int(value) for value in match.groups())
        if not 1 <= month <= 12:
            return None
        try:
            next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
            return next_month - timedelta(days=1)
        except ValueError:
            return None
    return None


def task_date_context(created_at, task_id=None) -> dict:
    """沿用有效期引擎的任务存储日历日，不猜测naive存储时区，不读取机器当前时间。"""
    value = created_at.date() if isinstance(created_at, datetime) else created_at
    return {"baseline_date": value.isoformat() if isinstance(value, date) else None,
            "source": "task.created_at", "task_id": task_id,
            "calendar_basis": "stored_datetime.date_without_timezone_conversion",
            "storage_timezone": (str(created_at.tzinfo) if isinstance(created_at, datetime) and created_at.tzinfo
                                 else "not_inferred_from_naive_storage")}


def _reference_date_context(value):
    if not isinstance(value, dict) or value.get("source") != "task.created_at":
        return None
    raw = str(value.get("baseline_date") or "")
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        return None
    return deepcopy(value) if parsed.isoformat() == raw else None


def date_token_spans(text: str) -> list[tuple[int, int, str]]:
    """从完整原文定位日期token；短年份须显式带年，不把13.4/编号5-4当日期。"""
    raw = str(text or "")
    spans = []
    for match in _DATE_TOKEN_RE.finditer(raw):
        if _NUMBER_LABEL_RE.search(raw[max(0, match.start() - 12):match.start()]):
            continue
        token = match.group(0)
        # `2026年9月?日` 的可见 token 不能退化成 `2026年9月`。
        if re.search(r"年\s*\d{1,2}\s*月\s*$", token) and re.match(
            r"\s*[?？□_*Xx〇O]{1,4}\s*日?", raw[match.end():]
        ):
            continue
        spans.append((match.start(), match.end(), token))
    return spans


def date_tokens(text: str) -> list[str]:
    """保留完整原始日期、非法月份和识读占位符；不修正年份。"""
    return list(dict.fromkeys(raw for _start, _end, raw in date_token_spans(text)))


def date_range_separator(text: str, *, prefix: bool = False) -> re.Match[str] | None:
    """有效期起止连接符共用判据；不把年/月/日内部的分隔符当新日期。"""
    return _RANGE_SEPARATOR_RE.match(text) if prefix else _RANGE_SEPARATOR_RE.fullmatch(text)


def build_date_fact(raw_text: str, *, purpose: str, file_name: str = "", page=None,
                    asset_id=None, context: str = "") -> dict:
    """正文片段转日期事实；readable仅表示格式可读取，不等同字符置信/原图复核。"""
    raw_text = str(raw_text or "").strip()
    parsed = parse_date_text(raw_text)
    reason = ""
    # 范围仅触发识读核验，不能用来宣称年份真实错误，更不能替换为任务年份。
    if parsed is None:
        reason = "原始日期不能完整解析，需核对原页"
    elif not 1900 <= parsed.year <= 2100:
        reason = "原始年份超出现代工程日期的识读范围，需核对原页，未作年份修正"
    elif _UNCLEAR_RE.search(context):
        reason = "日期附近存在字迹或识读不清说明，尚未核清原始日期"
    return {
        "purpose": purpose, "raw_text": raw_text,
        "parsed_date": parsed.isoformat() if parsed is not None and not reason else None,
        "file_name": str(file_name or ""), "asset_id": asset_id, "page": page,
        "reliability": "unresolved" if reason else "readable", "reason": reason,
        "inspection": {"method": "source_text", "image_verified": False},
    }


def date_verification_pending(value: dict) -> bool:
    return isinstance(value, dict) and (value.get("date_verification") or {}).get("status") == "unresolved"


def date_review_note(value: dict) -> str:
    """右侧复核与报告共用的人话提示，保留具体文件、物理页及原始识读。"""
    verification = value.get("date_verification") or {}
    notes = []
    for fact in verification.get("facts") or []:
        if fact.get("reliability") != "unresolved":
            continue
        location = str(fact.get("file_name") or "原始资料")
        if fact.get("page"):
            location += f"第{fact['page']}页"
        raw = str(fact.get("raw_text") or "日期未读清")
        notes.append(f"{location}：原识读『{raw}』，请核对原页日期")
    review = verification.get("original_page_review") or {}
    for attempt in review.get("attempts") or []:
        location = str(attempt.get("file_name") or "原始资料")
        if attempt.get("page"):
            location += f"第{attempt['page']}页"
        candidates = list(dict.fromkeys(str(fact.get("raw_text") or "") for fact in attempt.get("date_candidates") or []))
        if candidates:
            notes.append(f"{location}原页重读候选：{'、'.join(candidates)}；原识读保留，仍请人工确认")
        else:
            status = "超时" if attempt.get("status") == "timeout" else "未取得可核日期"
            notes.append(f"{location}已回看一次，{status}，仍请人工核对")
    for unavailable in review.get("unavailable_sources") or []:
        notes.append(str(unavailable.get("reason") or "原页暂不可回看，请人工核对"))
    return "；".join(dict.fromkeys(notes)) or str(verification.get("reason") or "必要日期尚未读清，请核对原页")


def _asset_value(asset, key, default=None):
    return asset.get(key, default) if isinstance(asset, dict) else getattr(asset, key, default)


def _reference(finding: dict, assets) -> tuple[str, object, object]:
    evidence = finding.get("evidence") or []
    first = evidence[0] if isinstance(evidence, list) and evidence else ""
    if isinstance(first, dict):
        name = str(first.get("file_name") or first.get("file") or "")
    else:
        name = str(first).strip("《》")
    pages = finding.get("_pages") or []
    if isinstance(pages, dict):
        if not name and pages:
            name = next(iter(pages))
        pages = pages.get(name) or []
    page = pages[0] if isinstance(pages, list) and len(pages) == 1 else None
    matches = [asset for asset in assets or [] if name == str(_asset_value(asset, "file_name", ""))]
    asset_id = _asset_value(matches[0], "id") if len(matches) == 1 else None
    return name, page, asset_id


def _purpose_context(text: str, purpose: str) -> str:
    """有效期与签发/检定日期分段，别把另一字段的模糊说明传染给到期日。"""
    if purpose != "expiry":
        return text
    labels = list(_DATE_ROLE_LABEL_RE.finditer(text))
    if not labels:
        return text
    return "\n".join(text[label.start():labels[index + 1].start() if index + 1 < len(labels) else len(text)]
                      for index, label in enumerate(labels) if label.lastgroup == "expiry")


def source_date_facts(text: str, *, purpose: str, file_name: str = "", page=None,
                      asset_id=None, context: str | None = None) -> tuple[list[dict], list[dict]]:
    """主张和原页重读共用字段角色提取；到期事实不能被无关签发/检定日期污染。"""
    raw = str(text or "")
    tokens = date_token_spans(raw)
    facts, other_facts = [], []
    for index, (start, end, token) in enumerate(tokens):
        labels = _date_role_labels(raw[:start])
        role = labels[-1][1] if labels and purpose == "expiry" else purpose
        if purpose == "expiry" and role == "expiry" and index + 1 < len(tokens):
            following_start = tokens[index + 1][0]
            if date_range_separator(raw[end:following_start]):
                role = "validity_start"
        fact = build_date_fact(token, purpose=role or purpose, file_name=file_name, page=page,
                               asset_id=asset_id, context=_purpose_context(raw if context is None else context, role or purpose))
        fact["source_span"] = {"start": start, "end": end}
        (other_facts if purpose == "expiry" and role != "expiry" else facts).append(fact)
    return facts, other_facts


def _facts_from_finding(finding: dict, assets, evidence_texts) -> tuple[str, list[dict], list[dict]]:
    title, detail = str(finding.get("title") or ""), str(finding.get("detail") or "")
    kind = str(finding.get("kind") or "")
    claim = title + "\n" + detail
    explicit_expiry = bool(_EXPLICIT_EXPIRY_FIELD_RE.search(claim))
    # 单个落款日“早于项目阶段多年”仍是签署日期噪声；只有至少两个可解析日期才进入工序先后比较。
    baseline_comparison = bool(_BASELINE_COMPARISON_RE.search(claim)) and not bool(_EXPLICIT_ORDER_PAIR_RE.search(claim))
    date_count = len(date_token_spans(claim))
    # `kind=timeline` 只是模型用途提示，不能绕过“至少两个日期 + 明确工序关系”
    # 的门禁；单个落款日与项目基准年的比较仍属于签署日期噪声。
    sequence_claim = (
        date_count >= 2
        and not baseline_comparison
        and not bool(_SIGNING_YEAR_NOISE_RE.search(claim))
        and bool(_SEQUENCE_RE.search(claim) or (kind == "timeline" and _EXPLICIT_ORDER_PAIR_RE.search(claim)))
    )
    purpose = ("expiry" if kind == "validity" or explicit_expiry
               else "sequence" if sequence_claim
               else "required" if _REQUIRED_DATE_RE.search(claim) else "signature")
    name, page, asset_id = _reference(finding, assets)
    raw = str(finding.get("quote") or "").strip()
    if not raw:
        raw = detail
    facts, other_facts = source_date_facts(raw, purpose=purpose, file_name=name, page=page,
                                         asset_id=asset_id, context=claim)
    # 逐事实定位到已登记且本条明确引用的页。多个文件/页不能用首项兜底。
    pages_map = finding.get("_pages") or {}
    if assets and isinstance(pages_map, dict):
        for fact in facts + other_facts:
            locations = []
            for asset in assets:
                canonical = str(_asset_value(asset, "file_name", ""))
                cited = pages_map.get(canonical) or []
                if not cited:
                    continue
                key = _asset_value(asset, "id")
                source = next((str((evidence_texts or {})[item]) for item in
                               (key, str(key), _asset_value(asset, "relative_path"))
                               if item in (evidence_texts or {})), str(_asset_value(asset, "text_summary", "")))
                headers = list(_PAGE_RE.finditer(source))
                texts = {int(match.group(1)): source[match.end():headers[index + 1].start()
                         if index + 1 < len(headers) else len(source)] for index, match in enumerate(headers)}
                metadata = _asset_value(asset, "metadata_json", {}) or {}
                if not texts and metadata.get("fulltext_page_count") == 1:
                    texts[1] = source
                locations.extend({"file_name": canonical, "asset_id": key, "page": number}
                                 for number in cited if fact["raw_text"] in texts.get(number, ""))
            if len(locations) == 1:
                fact.update(locations[0])
            elif purpose in {"expiry", "sequence", "required"}:
                fact.update(reliability="unresolved", parsed_date=None, page=None,
                            reason="日期片段未能唯一定位到本条引用的具体原页", source_candidates=locations)
    return purpose, facts, other_facts


def _suppressed_claims(result):
    claims = list(((result.get("audit") or {}).get("date_screening") or {}).get("suppressed_claims") or [])
    for trace in result.get("sub_agent_trace") or []:
        if isinstance(trace, dict):
            claims.extend(row for row in _suppressed_claims(trace) if row not in claims)
    return claims


def _same_suppressed_source(finding, facts, previous):
    """复核改写须指向同一原文、文件和物理页；仅共享一个年份不足以继承抑制。"""
    quote = re.sub(r"\s+", "", str(finding.get("quote") or ""))
    if not quote or not facts:
        return False
    keys = {(f.get("file_name"), f.get("page"), f.get("raw_text")) for f in facts}
    for row in previous:
        original = row.get("original_finding") or {}
        if quote != re.sub(r"\s+", "", str(original.get("quote") or "")):
            continue
        old_keys = {(f.get("file_name"), f.get("page"), f.get("raw_text")) for f in row.get("facts") or []}
        # 未定位的两段文字不能仅因年份相同成为同一证据。
        if keys and keys <= old_keys and all(name and page for name, page, _raw in keys):
            return True
    return False


def _expiry_role_conflict(claim, facts, other_facts):
    starts = {f["parsed_date"] for f in other_facts if f.get("purpose") == "validity_start" and f.get("parsed_date")}
    ends = {f["parsed_date"] for f in facts if f.get("purpose") == "expiry" and f.get("parsed_date")}
    if not starts or not ends:
        return None
    for start, _end, raw in date_token_spans(claim):
        value = parse_date_text(raw)
        if (value and value.isoformat() in starts - ends
                and _EXPIRY_END_CLAIM_RE.search(claim[:start])):
            return {"claimed_expiry_date": value.isoformat(), "validity_start_dates": sorted(starts),
                    "validity_end_dates": sorted(ends)}
    return None


def _date_role_labels(raw):
    labels = [(m.start(), m.lastgroup) for m in _DATE_ROLE_LABEL_RE.finditer(raw)]
    labels += [(m.start(), "signature") for m in _NON_EXPIRY_DATE_LABEL_RE.finditer(raw)]
    return sorted(labels)


def _temporal_claim_check(finding, reference):
    audit = finding.get("audit") or {}
    original = audit.get("date_temporal_original_finding") or audit.get("date_original_finding") or finding
    claim = str(original.get("title") or "") + "\n" + str(original.get("detail") or "")
    has_future = bool(_FUTURE_CLAIM_RE.search(claim))
    has_expiry = bool(_EXPIRED_CLAIM_RE.search(claim) and _EXPLICIT_EXPIRY_FIELD_RE.search(claim))
    kind = "future" if has_future else "expiry" if has_expiry else None
    if not kind or (kind == "expiry" and not reference):
        return None
    raw = str(original.get("quote") or original.get("detail") or "")
    labels = _date_role_labels(raw)
    def role_at(start):
        return next((role for position, role in reversed(labels) if position < start), None)
    if kind == "expiry":
        facts, _other = source_date_facts(raw, purpose="expiry")
        values = [parse_date_text(f.get("parsed_date")) for f in facts if f.get("parsed_date")
                  and (not labels or role_at(f["source_span"]["start"]) == "expiry")]
    else:
        values = [parse_date_text(token) for start, _end, token in date_token_spans(raw) if role_at(start) != "expiry"]
    if not values or any(value is None or not 1900 <= value.year <= 2100 for value in values):
        return None  # 非可靠日历值仍由识读/用途门处理，不把疑似2526年确认为真实未来。
    status = "unverifiable"
    if reference:
        baseline = date.fromisoformat(reference["baseline_date"])
        holds = any(value > baseline for value in values) if kind == "future" else any(value < baseline for value in values)
        status = "confirmed" if holds else "contradicted"
    sequence = bool(original.get("kind") == "timeline" or _SEQUENCE_RE.search(claim))
    # 同条还声称另一种时态缺陷时，不允许由一个子句的反证撤销整条。
    independent = bool((has_future and has_expiry) or _INDEPENDENT_DEFECT_RE.search(claim) or _EXPLICIT_ORDER_CONFLICT_RE.search(claim))
    return {"claim_type": kind, "status": status, "claimed_dates": sorted({value.isoformat() for value in values}),
            "reference_date_context": deepcopy(reference),
            "only_temporal_claim": not independent and (not sequence or bool(_CONSISTENT_ORDER_RE.search(claim))),
            "comparison_scope": "仅校验所引日期文字的时间主张，不确认引用来源或原图真实性"}


def _screen_findings(findings, assets, evidence_texts, previous=(), reference=None):
    kept, suppressed, pending = [], [], []
    for original in findings or []:
        if not isinstance(original, dict):
            kept.append(original)
            continue
        finding = deepcopy(original)
        temporal = _temporal_claim_check(finding, reference)
        if temporal:
            finding.setdefault("audit", {})["temporal_claim_check"] = temporal
            if temporal["status"] == "contradicted" and temporal["only_temporal_claim"]:
                facts = (finding.get("date_verification") or {}).get("facts")
                if not facts:
                    _purpose, facts, _other = _facts_from_finding(finding, assets, evidence_texts)
                suppressed.append({"original_finding": deepcopy(original), "facts": deepcopy(facts),
                    "temporal_claim_check": temporal, "reason": "所引日期与任务基准日比较后不支持未来/过期主张；不据此确认来源，不自动修正原值"})
                continue
            if temporal["status"] in {"contradicted", "unverifiable"} and not temporal["only_temporal_claim"]:
                # 只撤销可证伪的时间子句；同条签认缺失或另一真实倒挂仍保留原来源和等级。
                finding["audit"].setdefault("date_temporal_original_finding", deepcopy(original))
                raw_claim = finding["audit"]["date_temporal_original_finding"]
                for key in ("title", "detail"):
                    text = str(raw_claim.get(key) or "")
                    pattern = r"(?:属于|属|为|是)?未来(?:时间|日期|年份)?" if temporal["claim_type"] == "future" else _EXPIRED_CLAIM_RE
                    replacement = ("未来时间尚缺任务基准核实" if temporal["status"] == "unverifiable" else
                                   "按任务基准不构成未来日期异常" if temporal["claim_type"] == "future" else "按任务基准尚未到期")
                    finding[key] = re.sub(pattern, replacement, text)
            if temporal["status"] == "unverifiable" and temporal["only_temporal_claim"]:
                _purpose, facts, _other = _facts_from_finding(finding, assets, evidence_texts)
                finding.setdefault("audit", {}).setdefault("date_temporal_original_finding", deepcopy(original))
                finding["audit"]["date_reliability_applied"] = True
                finding.update(title="日期时间主张基准待核", severity="low",
                    detail="缺少可靠的任务日期基准，尚不能确认所引日期是否属于未来；请核对任务日期及原件。",
                    date_verification={"status": "unresolved", "purpose": "reference", "facts": facts,
                        "reason": "缺少可靠任务日期基准，不能猜测未来日期或自动通过", "temporal_claim_check": temporal})
                kept.append(finding)
                pending.append(finding["date_verification"])
                continue
        if (finding.get("audit") or {}).get("date_reliability_applied"):
            original_claim = str(((finding.get("audit") or {}).get("date_original_finding") or {}).get("title") or "") + "\n" + str(((finding.get("audit") or {}).get("date_original_finding") or {}).get("detail") or "")
            already_independent = bool(_INDEPENDENT_DEFECT_RE.search(original_claim) or _EXPLICIT_ORDER_CONFLICT_RE.search(original_claim))
            original_finding = (finding.get("audit") or {}).get("date_original_finding") or {}
            original_purpose = _facts_from_finding(original_finding, assets, evidence_texts)[0] if original_finding else ""
            if original_purpose == "signature" and not already_independent:
                suppressed.append({"original_finding": deepcopy(original), "facts": (finding.get("date_verification") or {}).get("facts") or [],
                                   "reason": "普通签署日期或年份差异仅作审计保留，不自动升级为有效期或工序缺陷"})
                continue
            verification = finding.get("date_verification") or {}
            facts = verification.get("facts") or []
            if (verification.get("purpose") == "signature" and _same_suppressed_source(finding, facts, previous)
                    and not (temporal and not temporal["only_temporal_claim"])):
                suppressed.append({"original_finding": deepcopy(original), "facts": deepcopy(facts),
                    "reason": "同一原文及原页的非关键日期疑点已抑制，复核改写沿用原裁定；原值保留"})
                continue
            kept.append(finding)
            if date_verification_pending(finding):
                pending.append(finding["date_verification"])
            continue
        title = str(finding.get("title") or "")
        if not _DATE_TITLE_RE.search(title) and finding.get("kind") not in {"validity", "timeline"}:
            kept.append(finding)
            continue
        purpose, facts, other_facts = _facts_from_finding(finding, assets, evidence_texts)
        if other_facts:
            finding.setdefault("audit", {})["date_other_facts"] = other_facts
        claim = title + "\n" + str(finding.get("detail") or "")
        role_conflict = _expiry_role_conflict(claim, facts, other_facts) if purpose == "expiry" else None
        independent_defect = bool(_INDEPENDENT_DEFECT_RE.search(claim) or _EXPLICIT_ORDER_CONFLICT_RE.search(claim))
        year_only_claim = (purpose == "signature" and (
                               bool(_YEAR_COMPARISON_RE.search(claim))
                               or bool(_SIGNING_YEAR_NOISE_RE.search(claim)))
                           and not independent_defect
                           and not (temporal and (temporal["status"] == "confirmed" or not temporal["only_temporal_claim"])))
        noncritical_noise = purpose == "signature" and bool(facts) and (
            any(fact["reliability"] == "unresolved" for fact in facts) or bool(_UNCLEAR_RE.search(claim))
        ) and not independent_defect and not (temporal and not temporal["only_temporal_claim"])
        inherited = (purpose == "signature" and _same_suppressed_source(finding, facts, previous)
                     and not (temporal and not temporal["only_temporal_claim"]))
        if year_only_claim or noncritical_noise or inherited:
            suppressed.append({"original_finding": deepcopy(original), "facts": facts,
                               "reason": ("同一原文及原页的非关键日期疑点已抑制，复核改写沿用原裁定；原值保留"
                                          if inherited else "孤立落款识读或年份差异不构成业务缺陷；原值保留，未自动改年")})
            continue
        if purpose == "expiry" and not facts and _ADVISORY_INTERVAL_RE.search(claim):
            name, page, asset_id = _reference(finding, assets)
            pages = []
            page_map = finding.get("_pages") or {}
            if isinstance(page_map, dict):
                pages = sorted({int(p) for values in page_map.values() for p in values
                                if str(p).isdigit() and int(p) > 0})
            verification = {
                "status": "unresolved", "purpose": "expiry", "issue_kind": "deadline_not_stated",
                "facts": [],
                "candidate_sources": [{"file_name": name, "asset_id": asset_id, "pages": pages}],
                "reason": "原文仅给出建议复校/校准间隔，未明示有效截止日期，不能据间隔推算有效期。",
            }
            audit = finding.setdefault("audit", {})
            audit["date_original_finding"] = deepcopy(original)
            audit["date_reliability_applied"] = True
            finding["date_verification"] = verification
            finding["severity"] = "low"
            finding["detail"] = str(finding.get("detail") or "") + "；原文仅给出建议间隔，未明示有效截止日期，需人工核对原件。"
            pending.append(verification)
        elif purpose in {"expiry", "sequence", "required"} and (
            role_conflict or any(fact["reliability"] == "unresolved" for fact in facts) or bool(_UNCLEAR_RE.search(_purpose_context(claim, purpose)))
            or (not facts and (finding.get("source_verification") or {}).get("status") == "verified")
        ):
            if not facts:
                name, page, asset_id = _reference(finding, assets)
                facts = [build_date_fact("", purpose=purpose, file_name=name, page=page, asset_id=asset_id)]
            verification = {"status": "unresolved", "purpose": purpose, "facts": facts,
                            "reason": "必要日期尚未读清，不能确认真实过期或工序冲突",
                            "inspection": {"attempts": 1, "limit": 1, "method": "registered_page_text",
                                           "original_image_review": "required"}}
            audit = finding.setdefault("audit", {})
            if role_conflict:
                audit["date_role_conflict"] = role_conflict
                verification["reason"] = (f"原主张把区间起日{role_conflict['claimed_expiry_date']}作为截止日；"
                    f"原文终日为{'、'.join(role_conflict['validity_end_dates'])}，请按起止字段核对，不据此确认已过期")
                verification["inspection"]["method"] = "source_text_role_comparison"
            audit["date_original_finding"] = deepcopy(original)
            audit["date_reliability_applied"] = True
            finding.update(date_verification=verification, severity="low",
                           title="有效期日期待核" if purpose == "expiry" else "必要工序日期待核" if purpose == "sequence" else "必要签署日期待核")
            finding["detail"] = date_review_note(finding) + "；尚未确认真实日期，不据此判定过期或倒挂。"
            pending.append(verification)
        elif facts:
            finding["date_verification"] = {"status": "readable", "purpose": purpose, "facts": facts}
            finding.setdefault("audit", {})["date_reliability_applied"] = True
        kept.append(finding)
    return kept, suppressed, pending


def apply_date_reliability(result: dict, assets=None, *, evidence_texts: dict | None = None,
                           date_context: dict | None = None) -> dict:
    """主、专业和末端聚合共用；剔除非关键噪声，必要日期待核独立于来源可信。"""
    stored = ((result.get("audit") or {}).get("date_screening") or {}).get("reference_date_context")
    reference = _reference_date_context(date_context if date_context is not None else stored)
    previous = _suppressed_claims(result)
    output = _apply_date_reliability(result, assets, evidence_texts, previous, reference)
    updated = _suppressed_claims(output)
    # 一次收集新撤销主张、一次回收较早trace的派生状态；有界两遍，不循环猜测结论。
    return (_apply_date_reliability(output, assets, evidence_texts, updated, reference)
            if updated != previous else output)


def _apply_date_reliability(result, assets, evidence_texts, previous, reference):
    output = deepcopy(result)
    original_reason = str(output.get("reason") or "")
    original_findings = output.get("findings") or []
    if reference:
        output.setdefault("audit", {}).setdefault("date_screening", {})["reference_date_context"] = deepcopy(reference)
    findings, suppressed, pending = _screen_findings(original_findings, assets, evidence_texts, previous, reference)
    if "findings" in output:
        output["findings"] = findings
    removed = {str(row["original_finding"].get("title") or "") for row in suppressed + _suppressed_claims(output)}
    renamed = {str((finding.get("audit") or {}).get("date_original_finding", {}).get("title") or "")
               for finding in findings if isinstance(finding, dict) and date_verification_pending(finding)}
    defects = list(output.get("defects") or [])
    if removed or renamed:
        output["defects"] = [value for value in defects if str(value) not in removed | renamed]
    traces = []
    for trace in output.get("sub_agent_trace") or []:
        if not isinstance(trace, dict) or trace.get("conclusion") in {"skipped", "skipped_no_llm"}:
            traces.append(trace)
            continue
        checked = _apply_date_reliability({**trace, "compliance": trace.get("conclusion"),
                                          "findings": trace.get("structured_findings") or []},
                                         assets, evidence_texts, previous + suppressed, reference)
        checked["structured_findings"] = checked.pop("findings")
        checked["conclusion"] = checked.pop("compliance")
        traces.append(checked)
        removed.update(str(row["original_finding"].get("title") or "") for row in _suppressed_claims(checked))
        if date_verification_pending(checked):
            pending.append(checked["date_verification"])
    if "sub_agent_trace" in output:
        output["sub_agent_trace"] = traces
    if removed:
        output["defects"] = [value for value in output.get("defects") or [] if str(value) not in removed]
        withdrawn_facts = [fact for row in suppressed + _suppressed_claims(output) + _suppressed_claims({"sub_agent_trace": traces})
                           for fact in row.get("facts") or []]
        old_facts = (output.get("date_verification") or {}).get("facts") or []
        keys = {(f.get("purpose"), f.get("file_name"), f.get("raw_text")) for f in withdrawn_facts}
        if (not pending and old_facts and all((f.get("purpose"), f.get("file_name"), f.get("raw_text")) in keys for f in old_facts)):
            output.setdefault("audit", {}).setdefault("date_screening", {}).setdefault(
                "withdrawn_date_verification", deepcopy(output["date_verification"]))
            output.pop("date_verification", None)
    if suppressed:
        audit = output.setdefault("audit", {})
        stored = audit.setdefault("date_screening", {}).setdefault("suppressed_claims", [])
        stored.extend(row for row in suppressed if row not in stored)
        audit["date_screening"].setdefault("original_reason", original_reason)
    retired = removed | {str(row["original_finding"].get("title") or "") for row in previous}
    if retired:
        audit = output.setdefault("audit", {})
        claimed = set(audit.get("source_unverified_claims") or [])
        source_withdrawn = False
        # 来源门禁曾只因这些已撤销主张产生待核时，撤销派生状态而不伪称原件已核实。
        if ((output.get("source_verification") or {}).get("status") == "unresolved"
                and claimed and claimed <= retired
                and not any((f.get("source_verification") or {}).get("status") == "unresolved"
                            for f in findings if isinstance(f, dict))):
            screening = audit.setdefault("date_screening", {})
            screening.setdefault("withdrawn_source_verification", deepcopy(output["source_verification"]))
            output["source_verification"] = {"status": "not_applicable", "issues": [],
                                             "reason": "原来源待核仅针对已撤销的非关键日期主张，未宣称原件已核实"}
            source_withdrawn = True
            if audit.pop("source_unsupported_verdict", False):
                audit.pop("verdict_unsupported", None)
        other_pending = any(
            (record.get("source_verification") or {}).get("status") == "unresolved"
            or (record.get("rental_verification") or {}).get("status") == "undetermined"
            for record in [output] + [trace for trace in traces if isinstance(trace, dict)]
        ) or any(trace.get("conclusion") in {"uncertain", "nonconforming", "error"}
                 for trace in traces if isinstance(trace, dict))
        date_only_verdict = source_withdrawn or (
            output.get("compliance") == "nonconforming"
            and (bool(suppressed) or bool(defects) or any(_suppressed_claims(trace) for trace in traces if isinstance(trace, dict)))
        ) or (
            output.get("compliance") == "uncertain" and bool(_suppressed_claims(output))
            and ((audit.get("date_screening") or {}).get("original_reason") == original_reason
        )
        )
        if date_only_verdict and not findings and not output.get("defects") and not pending and not other_pending and all(str(value) in removed for value in defects):
            if output.get("compliance") in {"nonconforming", "uncertain"}:
                output["compliance"] = "compliant"
            output["reason"] = "孤立落款识读和年份差异已留审计，未形成有效期或工序先后缺陷。"
    if pending:
        facts = []
        for verification in pending:
            for fact in verification.get("facts") or []:
                if fact not in facts:
                    facts.append(fact)
        output["date_verification"] = {"status": "unresolved", "facts": facts,
                                        "reason": "必要日期尚未读清，请核对所列原页"}
        if (output.get("audit") or {}).get("date_page_review"):
            output["date_verification"]["original_page_review"] = deepcopy(output["audit"]["date_page_review"])
        confirmed = [finding for finding in findings + [finding for trace in traces if isinstance(trace, dict)
                     for finding in trace.get("structured_findings") or []] if isinstance(finding, dict)
                     and not date_verification_pending(finding) and not finding.get("unsupported")
                     and finding.get("severity", "medium") in {"high", "medium"}]
        if not confirmed:
            output["compliance"] = "uncertain"
            output["reason"] = date_review_note(output) + "；必要日期尚未核清，不确认过期或倒挂。"
        output["confidence"] = {"level": "low", "label": "低置信", "reason": "必要日期尚未核清"}
    if removed or previous:
        live_findings = [f for f in findings if isinstance(f, dict)]
        live_findings.extend(f for trace in traces if isinstance(trace, dict)
                             for f in trace.get("structured_findings") or [] if isinstance(f, dict))
        if live_findings:
            # 汇总理由是有效发现的投影；旧模型理由不得把已撤销的日期主张重新带回兼容解析器。
            output.setdefault("audit", {}).setdefault("date_screening", {}).setdefault(
                "original_reason", original_reason)
            descriptions = [str(f.get("title") or "") + ("：" + str(f["detail"]) if f.get("detail") else "")
                            for f in live_findings if f.get("title")]
            if descriptions:
                output["reason"] = "核查依据：" + "；".join(dict.fromkeys(descriptions))
        elif output.get("compliance") == "uncertain":
            output.setdefault("audit", {}).setdefault("date_screening", {}).setdefault("original_reason", original_reason)
            output["reason"] = "原日期主张已撤销；当前仍有尚未明确依据的核查状态，请核实独立缺口，未据此自动通过。"
    return output
