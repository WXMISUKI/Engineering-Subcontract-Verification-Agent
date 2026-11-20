# -*- coding: utf-8 -*-
"""分包申报审查 · ④ 证件证照有效性核查（内核名 cert_validity）。

落位口径（2026-09-22 拍板）：本内核并入分包申报审查链路（一会话一核查不拆两个智能体），
证据取自报审资料包与合同附件内已解析文本；报告/界面分组按业主「五步审查」命名
（FRS v2 §0.3：内部 A2 编号只作程序标识，不再出现在报告与界面文案里）。

复用纪律（红章同款）：只 import 开工条件核查智能体的 validity 引擎
（纯函数、LLM-free、无 DB/网络依赖），不改其现役行为。

判定口径（宁缺勿错，三态全站统一）：
- expired       → 命中：证件已过期，缺陷成立；
- expiring_soon → 命中：临近到期（预警窗口见 data/config.json 的 cert_validity.source_note），
  中风险提示交人工复核；
- valid         → 全部有效时聚合为一条「均有效」未命中；
- 无任何可解析有效期记录 → 不可判定：区分「材料无文字内容（疑似扫描件）」与
  「有文字但未见有效期标注」，绝不把「没看到」判成「过期/缺失」。
"""
from datetime import date
from html import unescape
import hashlib
import re

from ..startup_conditions_check.validity import (
    extract_validity_records, judge_expiry, parse_date_text,
)
from ..startup_conditions_check.date_reliability import build_date_fact
from .finding_text import bounded_finding_message
from .models import (STATUS_HIT, STATUS_MISS, STATUS_NOT_INCLUDED, STATUS_UNDECIDABLE,
                     BsFinding)
from .personnel_evidence import (
    extract_management_roster, extract_person_relation_evidence, extract_special_trade_roster,
    extract_roster_names, extract_social_names, social_evidence_materials,
    normalize_identity_number,
    extract_person_supplement_evidence,
)
from .parsing import material_page_readable, material_reading_gap
from .rules import (load_config, load_extract_matchers, load_qualification_matchers)
from . import qualification_scope

# 结论「依据」字段＝条款坐标（纪律 2：结论回源码坐标，不引用内部需求稿）。
# 坐标与 data/review_flow.json 第④步 basis 同源，由 tests/test_bs_a2_kernels.py 对锚。
SOURCE = ("依据：《沪城公路〔2025〕118号》附件1第2项（营业执照、资质证书、安全生产许可证）、"
          "第4项（三类人员安考证、职业资格/岗位证书、特种作业证书）；"
          "《沪交行规〔2026〕8号》附件3第2项、附件4第3项（证照有效期）；"
          "现场指挥部2025.6.20要求三（注册证书须在有效期内）")
ITEM_ID = "A2-02"

_CERT_HINT_RE = re.compile(r"资质证书|安全生产许可证|安许证|营业执照|证照|执业证|岗位证书|"
                           r"特种作业|建造师|安考证|交安[ABC]?证|三类人员")
_CERT_TITLE_RE = re.compile(
    r"(?m)^[ \t]*(?:" + "|".join(r"\s*".join(re.escape(char) for char in title) for title in (
        "建筑业企业资质证书", "资质证书", "安全生产许可证", "营业执照", "注册建造师证书",
        "特种作业操作证", "安全生产考核合格证", "安全生产考核合格证书")) +
    r")[ \t]*(?:[（(]?[ \t]*(?:正本|副本)[ \t]*[）)]?)?[ \t]*$")
_CERT_DECLARATION_RE = re.compile(
    r"资质情况|资质信息|申报信息|声明|合同价款|填写示例|示例填写|空白模板|空白样表"
    r"|(?:应当|应|须|需).{0,8}(?:提供|提交|出示)")
_CERT_INDIRECT_SOURCE_RE = re.compile(r"台账|情况说明|模板|样表|填写示例|信息汇总|登记表|信息表")
_CERT_PROOF_FIELDS = (
    re.compile(r"(?:证书编号|许可证编号|统一社会信用代码)[ \t]*[:：][ \t]*([^\n]+)"),
    re.compile(r"(?:注册单位|注册企业|聘用单位|受聘单位|单位名称|企业名称)[ \t]*[:：][ \t]*([^\n]+)"),
    re.compile(r"(?:发证机关|颁发机关|核发机关|登记机关|许可机关)[ \t]*[:：][ \t]*([^\n]+)"),
)
_CERT_BOUNDARY_NOTICE = "仅核对所附证照文字，真实性未核验"
_NON_CERT_PAGE_RE = re.compile(r"企业承诺书|工程量清单|实际控制人.*受益所有人", re.S)
_BUILDER_CERT_TITLE_RE = re.compile(r"(?m)^\s*(?:中华人民共和国)?(?:一级|二级)建造师注册证书\s*$")
_TRAFFIC_SAFETY_CERT_TITLE_RE = re.compile(
    r"(?m)^\s*公路水运工程施工(?:企业|单位)主要负责人和安全生产\s*"
    r"管理人员安全生产考核合格证书\s*$")
_SPECIAL_CERT_RANGE_RE = re.compile(
    r"初领日期\s*有效期限\s*(\d{4}-\d{2}-\d{2})\s+(\d{4}-\d{2}-\d{2})\s*至\s*(\d{4}-\d{2}-\d{2})\s*特种作业操作证")
_ID_CARD_PERIOD_RE = re.compile(
    r"居民身份证[\s\S]{0,180}?签发机关[\s\S]{0,120}?"
    r"(?P<period>有效期限\s*(?P<start>\d{4}[./-]\d{2}[./-]\d{2})\s*[-至]\s*(?P<end>\d{4}[./-]\d{2}[./-]\d{2}))")


def _certificate_text(material, text: str) -> str | None:
    """唯一页级证照形态判据；合同词汇/单位字段不能把整册提升为证照。

    独立证照件保留原文；台账、说明、模板或混合材料须有独立标题及至少两类
    实填证照字段。合同申报仍是声明，除非有独立发证字段支撑实际附证片段。
    """
    if not text.strip():
        return None
    plain = re.sub(r"</(?:p|div|tr|h[1-6])\s*>|<br\s*/?>", "\n", text, flags=re.I)
    plain = unescape(re.sub(r"<[^>]*>", " ", plain))
    # 混合资料包不能仅凭文件名把承诺书、工程量清单或股权图当成证照。
    # 有独立证照标题时仍保留核对，避免抑制同页实际附证。
    cost_table = all(word in plain for word in ("项目特征", "数量", "不含税单价"))
    if (_NON_CERT_PAGE_RE.search(plain[:500]) or cost_table) and not _CERT_TITLE_RE.search(plain):
        return None
    contract = (material.role in ("general", "sub", "labor")
                or re.search(r"合同|协议", material.file_name or ""))
    indirect_source = _CERT_INDIRECT_SOURCE_RE.search((material.file_name or "") + "\n" + plain[:80])
    mixed_packet = material.role == "packet" and len(getattr(material, "pages_json", None) or []) > 1
    if (not contract and not mixed_packet and _CERT_HINT_RE.search(material.file_name or "")
            and not indirect_source and not _CERT_DECLARATION_RE.search(plain)):
        return text
    # 电子注册证书常将使用有效期放在证书标题之前；独立标题、姓名、
    # 注册编号及注册专业须同时出现，才能把整页视为证书而不是清单声明。
    if (not indirect_source and not _CERT_DECLARATION_RE.search(plain)
            and _BUILDER_CERT_TITLE_RE.search(plain)
            and re.search(r"姓名\s*[:：]\s*[\u4e00-\u9fff]{2,4}", plain)
            and re.search(r"注册编号\s*[:：]\s*[\u4e00-\u9fffA-Za-z0-9-]{5,}", plain)
            and re.search(r"注册专业\s*[:：]\s*[\u4e00-\u9fff]{2,}", plain)):
        return text
    # 交安证原页标题常由 OCR 按两行输出，须同时有持有人、企业、
    # 交安证号及考核部门字段，不能把清单或材料声明当作证照。
    if (not indirect_source and not _CERT_DECLARATION_RE.search(plain)
            and _TRAFFIC_SAFETY_CERT_TITLE_RE.search(plain)
            and re.search(r"姓名\s*[:：]\s*[\u4e00-\u9fff]{2,4}", plain)
            and re.search(r"企业名称\s*[:：]\s*[^\n]{2,80}(?:公司|集团)", plain)
            and re.search(r"证书编号\s*[:：]\s*[^\n]{0,12}交安\s*[ABC][^\n]{3,}", plain, re.I)
            and re.search(r"考核部门\s*[:：]\s*[^\n]{2,}", plain)):
        return text
    for heading in _CERT_TITLE_RE.finditer(plain):
        fragment = plain[heading.start():]
        fields = sum(any(re.search(r"[\u4e00-\u9fffA-Za-z0-9]", match.group(1))
                         for match in pattern.finditer(fragment))
                     for pattern in _CERT_PROOF_FIELDS)
        if fields < 2:
            continue
        if (_CERT_DECLARATION_RE.search(plain[:heading.start()])
                and not _CERT_PROOF_FIELDS[2].search(fragment)):
            continue
        if _CERT_DECLARATION_RE.search(fragment):
            continue
        return fragment
    return None


def _certificate_reading_limit(material) -> str:
    """复用解析覆盖的唯一定义，仅在此组织证照核对限制文案。"""
    unread = material_reading_gap(material)
    if unread is None:
        return ""
    reason = ("部分内容尚未确认" if any((p.get("text") or "").strip() for p in material.pages_json or [])
              else "没有文字内容")
    return "「%s」%s，涉及证照请人工核对原件，不能据已读部分认定全部有效或一致" % (material.file_name, reason)

# 只识别原记录摘录里明确标注的起止范围；日期合法性和到期判定仍复用 OC 公共函数。
_VALIDITY_DATE_TEXT_RE = re.compile(
    r"\d{4}[年./-]\d{1,2}(?:[月./-](?P<day>\d{1,2})日?|月)?")
_VALIDITY_RANGE_SEPARATOR_RE = re.compile(r"\s*(?:至|到|[-—–~～])\s*")
_VALIDITY_RANGE_AMBIGUITY_RE = re.compile(r"或|待|不详|未知|暂定|约|[?？]")
_VALIDITY_INLINE_HTML_RE = re.compile(
    r"</?(?:br|span|b|strong|i|em|u|font|small|sup|sub)\b[^>]*>", re.IGNORECASE)


def _with_explicit_range_end(record: dict, baseline: date | None) -> dict:
    """修正 OC 将明确范围的起始日当截止日的记录，不改变其抽取/排除语义。

    仅允许同一标签下、起始日等于原 expiry_date 的唯一范围；原摘录不够完整、
    范围有歧义或日期非法时保留证据并标为 unknown，绝不退回起始日判过期。
    """
    label, snippet = record.get("label"), record.get("snippet") or ""
    if not label:
        return record
    # 仅在匹配视图移除换行/内联标签；段落和表格字段仍为边界，原始 quote 不改。
    snippet = _VALIDITY_INLINE_HTML_RE.sub(" ", snippet)
    labels = list(re.finditer(re.escape(label), snippet))
    candidates = []
    for index, match in enumerate(labels):
        stop = labels[index + 1].start() if index + 1 < len(labels) else len(snippet)
        body = re.split(r"[<>；;。]", snippet[match.end():stop], maxsplit=1)[0].strip()
        start_match = _VALIDITY_DATE_TEXT_RE.search(body)
        if not start_match:
            continue
        start = parse_date_text(start_match.group())
        if not start:
            continue
        tail = body[start_match.end():]
        separator = _VALIDITY_RANGE_SEPARATOR_RE.match(tail)
        clear_start = not body[:start_match.start()].strip() and bool(start_match.group("day"))
        # OCR 可能把起止日期之间的“至”漏掉；不能把第一个日期当截止日。
        # 不猜补连接词，只把紧邻的第二个日期标为无法解析的范围。
        adjacent_date = bool(not separator and _VALIDITY_DATE_TEXT_RE.match(tail.lstrip()))
        end_text = tail[separator.end():] if separator else "" if adjacent_date else None
        candidates.append((start, end_text, clear_start))
    # 同页多个同名标签时，snippet 可能跨过相邻范围。只有上游明确把其中一
    # 个起始日当成当前截止日时才进入本适配；否则交给 validity 的同页范围
    # 关联逻辑，不能把相邻证照拼成一条不可判定记录。
    if len(labels) > 1:
        matching = [candidate for candidate in candidates
                    if candidate[0].isoformat() == record.get("expiry_date")]
        if not matching:
            return record
        candidates = matching
    if not any(end_text is not None for _start, end_text, _clear in candidates):
        return record  # 单日标签维持 OC 原判；长期、车辆等排除项不会进入本适配。
    original_verification = record.get("date_verification") or {}
    # 上游 validity 可能已经把范围截止日读对；起始日仍只在
    # ``validity_start_facts`` 中，必须作为原始抽取事实保留下来，不能因
    # “无需纠正截止日”而丢失。该副本只用于审计，不参与当前三态判定。
    original_facts = record.get("validity_start_facts") or original_verification.get("facts") or []
    original_extraction = {**original_verification, "facts": original_facts}
    location = original_facts[0] if original_facts else {}
    unresolved_fact = {
        **location, "purpose": "expiry", "raw_text": record.get("snippet") or "",
        "parsed_date": None, "reliability": "unresolved",
        "reason": "有效期范围的截止日不明确或无效，不能把起始日作为截止日",
    }
    unknown = {**record, "verdict": "unknown", "expiry_date": None,
               "date_verification": {
                   **original_verification, "status": "unresolved", "purpose": "expiry",
                   "facts": [unresolved_fact], "original_extraction": original_extraction,
                   "range_correction": {"source": "bs_explicit_range_end", "status": "unresolved"},
               }}
    if len(candidates) != 1:
        return unknown
    start, end_text, clear_start = candidates[0]
    end_match = _VALIDITY_DATE_TEXT_RE.match(end_text)
    if not clear_start or not end_match or not end_match.group("day"):
        return unknown
    remainder = end_text[end_match.end():]
    # 只检查紧接截止日的范围连接/修饰语；后续独立字段中的“签约”等不属于日期。
    continuation = remainder.lstrip(" \t（(，,、")
    if (re.match(r"[\d年月日./-]", remainder)
            or _VALIDITY_RANGE_AMBIGUITY_RE.match(continuation)
            or _VALIDITY_RANGE_SEPARATOR_RE.match(continuation)
            or _VALIDITY_DATE_TEXT_RE.match(continuation)):
        return unknown
    try:
        end = parse_date_text(end_match.group())
    except (ValueError, OverflowError):
        end = None
    if not end or end < start:
        return unknown
    # 上游已正确得到截止日：只补回起始日原始事实，不重复改写结论。
    if record.get("expiry_date") == end.isoformat():
        return {**record, "date_verification": {
            **original_verification, "original_extraction": original_extraction,
            "range_correction": {"source": "bs_explicit_range_end", "status": "preserved",
                                 "start_date": start.isoformat(), "end_date": end.isoformat()},
        }}
    end_fact = build_date_fact(
        end_match.group(), purpose="expiry", file_name=location.get("file_name", record.get("doc_hint", "")),
        page=record.get("page"), asset_id=location.get("asset_id"), context=record.get("snippet") or "",
    )
    return {**record, "expiry_date": end.isoformat(),
            "verdict": judge_expiry(end, baseline),
            "date_verification": {
                **original_verification, "status": end_fact["reliability"], "purpose": "expiry",
                "facts": [end_fact], "original_extraction": original_extraction,
                "range_correction": {"source": "bs_explicit_range_end", "status": "corrected",
                                     "start_date": start.isoformat(), "end_date": end.isoformat()},
            }}


def soon_days() -> int:
    """临近到期预警窗口（天），规则=数据，改 config.json 即生效。"""
    cfg = load_config().get("cert_validity", {})
    return int(cfg.get("soon_days", 30))


_CERT_FULL_DATE_RE = re.compile(r"20\d{2}\s*[-./年]\s*\d{1,2}\s*[-./月]\s*\d{1,2}\s*日?")


def _certificate_period_fallback(page_no: int, text: str, baseline: date | None):
    """证照页「有效期」与两行日期分离时的有效期兜底。

    电子证书常把「有效期：」和起止日拆到不同行（甚至终止日在起日之前、标签
    落在日期之后），共享有效期引擎的标签窗口读不到。本兜底只处理**日期分行**的
    版式；单行区间（含倒序、暂定、多终日等）仍交给原引擎判不可判定，不能借兜底洗白。
    """
    if "有效期" not in text:
        return None
    label_at = text.find("有效期")

    def candidates(segment: str):
        found = []
        for match in _CERT_FULL_DATE_RE.finditer(segment):
            parsed = parse_date_text(match.group())
            if parsed is not None and all(parsed != value for _, value in found):
                found.append((match, parsed))
        return found

    tail = text[label_at:]
    valid = candidates(tail)
    if len(valid) == 2:
        (first_match, first_date), (second_match, second_date) = valid
        if "\n" not in tail[:first_match.start()]:
            return None
        if "\n" not in tail[first_match.end():second_match.start()]:
            return None
    else:
        # OCR 也可能把「有效期：」排在日期之后；此时只在标签之前恰好两个日期时兜底。
        head = text[:label_at]
        valid = candidates(head)
        if len(valid) != 2:
            return None
        (first_match, first_date), (second_match, second_date) = valid
        if "\n" not in head[first_match.end():second_match.start()]:
            return None
    dates = sorted((first_date, second_date))
    synthetic = "有效期：%s 至 %s" % (dates[0].isoformat(), dates[1].isoformat())
    records = extract_validity_records({page_no: synthetic}, baseline=baseline)
    if not records:
        return None
    record = records[0]
    # 原页可能把终止日排在起始日之前；证据引用统一用规范起止顺序，避免下游误判倒序。
    record["snippet"] = synthetic
    record["source_format"] = "certificate_text"
    if "安全生产考核合格证" in text or re.search(r"[ABC]\d?证", text):
        record["kind"] = "安全生产考核合格证"
    elif "特种作业" in text or "操作证" in text:
        record["kind"] = "特种作业操作证"
    return record


def run_cert_validity_check(task, materials: list, baseline: date | None = None) -> list:
    """逐份已解析材料抽取有效期记录并判定，返回落库前 BsFinding 列表（不 commit）。"""
    parsed = [m for m in materials if m.parse_state == "done"]
    has_text = [m for m in parsed
                if any((p.get("text") or "").strip() for p in (m.pages_json or []))]
    findings = []
    valid_refs = []
    builder_valid_periods = []
    for m in parsed:
        roster = [row for row in extract_management_roster([m])
                  if row.get("post") in {"企业负责人", "项目经理", "项目负责人", "项目总工", "安全员"}
                  and material_page_readable(m, row.get("page"))]
        # 扫描件上初领日期与有效期限同排时，OCR 可能先输出两个表头、再输出
        # 三个日期。只识别字段顺序明确且唯一的情况，不借用同页身份证期限。
        for page in m.pages_json or []:
            number = page.get("page")
            if not material_page_readable(m, number):
                continue
            text = page.get("text") or ""
            # 身份证只核查能与同一资料包的唯一管理人员名单对应的持有人。
            id_periods = list(_ID_CARD_PERIOD_RE.finditer(text))
            matching = [row for row in roster if re.search(
                r"姓\s*名\s*[:：]?\s*" + re.escape(row["name"])
                + r"(?=中华人民共和国|性别|民族|出生|[^\u4e00-\u9fff]|$)", text)]
            if (len(id_periods) == 1 and len(matching) == 1
                    and len(re.findall(r"姓\s*名", text)) == 1
                    and text.count("居民身份证") == 1
                    and not _CERT_DECLARATION_RE.search(text)
                    and not _CERT_INDIRECT_SOURCE_RE.search(m.file_name or "")):
                period = id_periods[0]
                try:
                    start, end = (date.fromisoformat(period.group(key).replace(".", "-").replace("/", "-"))
                                  for key in ("start", "end"))
                except ValueError:
                    start = end = None
                if start is not None and start <= end and (baseline is None or end < baseline):
                    person = matching[0]
                    quote = period.group("period")
                    refs = [{"material_id": m.id, "page": number, "quote": quote,
                             "source_kind": "certificate_text", "expiry_date": end.isoformat()},
                            {"material_id": m.id, "page": person["page"], "quote": person["quote"],
                             "source_kind": "personnel_roster"}]
                    message = ("未设置核查基准日，无法判断「%s」身份证期限（材料记录至 %s）；请设置基准日后重新核查；%s"
                               if baseline is None else
                               "「%s」身份证已超过有效期（有效期至 %s），请核对最新身份证；%s")
                    findings.append(BsFinding(task_id=task.id, material_id=m.id, item_id=ITEM_ID,
                        kind="cert_validity", status=STATUS_UNDECIDABLE if baseline is None else STATUS_HIT,
                        page=number, quote=quote, source=SOURCE,
                        message=message % (person["post"], end.isoformat(), _CERT_BOUNDARY_NOTICE), evidence_json=refs))
            if (_CERT_DECLARATION_RE.search(text) or _CERT_INDIRECT_SOURCE_RE.search(m.file_name or "")
                    or not re.search(r"证号\s*[:：]?\s*[A-Za-z0-9]{8,}", text)
                    or "姓名" not in text):
                continue
            matches = list(_SPECIAL_CERT_RANGE_RE.finditer(text))
            if len(matches) != 1:
                continue
            match = matches[0]
            try:
                issued, start, end = (date.fromisoformat(value) for value in match.groups())
            except ValueError:
                continue
            if issued > start or start > end or (baseline is not None and end >= baseline):
                continue
            ref = {"material_id": m.id, "page": number, "quote": match.group(),
                   "source_kind": "certificate_text", "expiry_date": end.isoformat()}
            message = ("未设置核查基准日，无法判断「特种作业操作证」期限（材料记录至 %s）；请设置基准日后重新核查；%s"
                       if baseline is None else
                       "「特种作业操作证」已超过有效期（有效期至 %s），需复检或重新取证后再报审；%s")
            findings.append(BsFinding(task_id=task.id, material_id=m.id, item_id=ITEM_ID,
                kind="cert_validity", status=STATUS_UNDECIDABLE if baseline is None else STATUS_HIT,
                page=number, quote=match.group(), source=SOURCE,
                message=message % (end.isoformat(), _CERT_BOUNDARY_NOTICE), evidence_json=[ref]))
        readable_pages = [p for p in (m.pages_json or []) if material_page_readable(m, p.get("page"))]
        for page in (m.pages_json or []):
            if (not material_page_readable(m, page.get("page"))
                    and (_certificate_text(m, page.get("text") or "") is not None
                         or _CERT_HINT_RE.search(page.get("text") or ""))):
                findings.append(BsFinding(
                    task_id=task.id, material_id=m.id, item_id=ITEM_ID, kind="cert_validity",
                    status=STATUS_UNDECIDABLE, page=page.get("page"), source=SOURCE,
                    location_mode="page" if type(page.get("page")) is int and page["page"] > 0 else None,
                    message="该页含证照相关线索，但读取不完整或识别质量存疑，无法可靠判断有效期，请核对原件；" + _CERT_BOUNDARY_NOTICE,
                ))
        certificate_pages = {p.get("page"): _certificate_text(m, p.get("text") or "")
                             for p in readable_pages}
        page_texts = {p.get("page"): certificate_pages.get(p.get("page")) or p.get("text", "")
                      for p in readable_pages}
        records = extract_validity_records(page_texts, file_name=m.file_name,
                                           baseline=baseline)
        # 证照页「有效期」标签与起止日分行时补一条记录；已有明确结论的页不重复。
        for page_no, text in certificate_pages.items():
            if not text:
                continue
            existing = [record for record in records if record.get("page") == page_no]
            if any(record.get("verdict") in {"valid", "expired", "expiring_soon"}
                   and record.get("expiry_date") for record in existing):
                continue
            fallback = _certificate_period_fallback(page_no, text, baseline)
            if fallback is None:
                continue
            if existing:
                records = [record for record in records if record.get("page") != page_no]
            records.append(fallback)
        record_counts = {page: sum(record.get("page") == page for record in records)
                         for page in page_texts}
        recorded_pages = set()
        for record in records:
            r = _with_explicit_range_end(record, baseline)
            recorded_pages.add(r.get("page"))
            certificate = certificate_pages.get(r.get("page")) is not None
            structured_clue = r.get("source_format") == "structured_certificate_text" and not certificate
            verdict = r.get("verdict")
            expiry = r.get("expiry_date", "")
            kind_name = r.get("kind", "证照文件")
            page_text = page_texts.get(r.get("page")) or ""
            filing_title = "建筑业企业施工劳务备案证书"
            filing_clue = (not certificate and not structured_clue
                           and page_text.count(filing_title) == 1 and record_counts.get(r.get("page")) == 1
                           and "证书编号" in page_text and "发证机关" in page_text)
            if filing_clue:
                kind_name = "施工劳务备案证书"
            ref = {"material_id": m.id, "page": r.get("page"), "quote": r.get("snippet"),
                   "source_kind": ("certificate_text" if certificate else "structured_certificate_clue"
                                   if structured_clue else "certificate_page_clue"
                                   if filing_clue else "contract_declaration"),
                   "expiry_date": expiry, "date_verification": r.get("date_verification")}
            refs = [ref]
            if filing_clue:
                refs.append({"material_id": m.id, "page": r.get("page"), "quote": filing_title,
                             "source_kind": "certificate_title_clue"})
            if baseline is None:
                findings.append(BsFinding(
                    task_id=task.id, material_id=m.id, item_id=ITEM_ID,
                    kind="cert_validity", status=STATUS_UNDECIDABLE,
                    message=("未设置核查基准日，无法判断「%s」的期限%s；请设置基准日后重新核查"
                             % (kind_name, "（材料记录至 %s）" % expiry if expiry else "")
                             + ("；" + _CERT_BOUNDARY_NOTICE if certificate else "；材料声明不代表实际证照")),
                    quote=r.get("snippet") or None, page=r.get("page"), source=SOURCE,
                    evidence_json=refs,
                ))
                continue
            if certificate and verdict == "valid":
                valid_refs.append(ref)
                snippet, label = r.get("snippet") or "", r.get("label") or ""
                label_at = snippet.find(label) if label else -1
                before = snippet[:label_at] if label_at >= 0 else ""
                if (_BUILDER_CERT_TITLE_RE.search(certificate_pages.get(r.get("page")) or "")
                        and re.search(r"注册专业\s*[:：][^。；;]{0,30}$", before)):
                    builder_valid_periods.append(ref)
                continue
            status = STATUS_HIT
            if not certificate:
                status = STATUS_UNDECIDABLE
                comparison = ("早于基准日" if verdict == "expired" else "覆盖基准日"
                              if verdict in ("valid", "expiring_soon") else "无法与基准日可靠比较")
                if structured_clue:
                    message = ("证照识别结果（「%s」，有效期至 %s）%s；需核对最新证照、原件及续期情况"
                               % (kind_name, expiry or "不明确", comparison))
                elif filing_clue:
                    message = ("材料页面标注「%s」，有效期至 %s（%s）；需核对原件及最新证照情况"
                               % (kind_name, expiry or "不明确", comparison))
                else:
                    message = ("材料声明填列期限（「%s」，有效期至 %s）%s；不是实际证照核验，"
                               "需核对最新证照、续期或变更资料" % (kind_name, expiry or "不明确", comparison))
            elif verdict == "expired":
                message = "「%s」已超过有效期（有效期至 %s），需复检或重新取证后再报审" % (
                    kind_name, expiry)
            elif verdict == "expiring_soon":
                message = "「%s」临近到期（有效期至 %s，%d 天内），请人工复核是否需安排换证" % (
                    kind_name, expiry, soon_days())
            else:
                status = STATUS_UNDECIDABLE
                message = ("「%s」的有效期起止范围不明确或日期无效，需人工核对原件" % kind_name)
                if certificate and _BUILDER_CERT_TITLE_RE.search(certificate_pages.get(r.get("page")) or ""):
                    adjacent = re.search(
                        r"使用有效期\s*[:：]\s*(\d{4}[年./-]\d{1,2}[月./-]\d{1,2}日?)"
                        r"\s+(\d{4}[年./-]\d{1,2}[月./-]\d{1,2}日?)", r.get("snippet") or "")
                    if adjacent and all(parse_date_text(value) for value in adjacent.groups()):
                        message = ("使用有效期处识别到 %s、%s，起止关系不明确，请核对原件"
                                   % adjacent.groups())
            if certificate:
                message += "；" + _CERT_BOUNDARY_NOTICE
            findings.append(BsFinding(
                task_id=task.id, material_id=m.id, item_id=ITEM_ID,
                kind="cert_validity", status=status, message=message,
                quote=r.get("snippet") or None, page=r.get("page"), source=SOURCE,
                evidence_json=refs,
            ))
        for page_no, text in certificate_pages.items():
            if text is not None and page_no not in recorded_pages:
                findings.append(BsFinding(
                    task_id=task.id, material_id=m.id, item_id=ITEM_ID, kind="cert_validity",
                    status=STATUS_UNDECIDABLE, page=page_no, quote=text[:160], source=SOURCE,
                    message="该证照页未见可识别有效期，需人工核对原件；" + _CERT_BOUNDARY_NOTICE,
                ))
    for m in materials:
        limit = _certificate_reading_limit(m)
        if limit:
            findings.append(BsFinding(
                task_id=task.id, material_id=m.id, item_id=ITEM_ID, kind="cert_validity",
                status=STATUS_UNDECIDABLE, message=limit, source=SOURCE,
            ))
    if findings:
        # 同一电子证书的使用期限待核/过期时，注册专业期限是独立事实；
        # 只对明确标识的同页记录保留，不把它包装成整份证照均有效。
        for ref in builder_valid_periods:
            if not any(f.material_id == ref["material_id"] and f.page == ref["page"] for f in findings):
                continue
            findings.append(BsFinding(
                task_id=task.id, material_id=ref["material_id"], item_id=ITEM_ID,
                kind="cert_validity", status=STATUS_MISS, page=ref["page"], quote=ref["quote"], source=SOURCE,
                message="注册专业有效期至 %s，覆盖核查基准日；%s" % (ref["expiry_date"], _CERT_BOUNDARY_NOTICE),
                evidence_json=[ref],
            ))
        return findings
    if valid_refs:
        findings.append(BsFinding(
            task_id=task.id, item_id=ITEM_ID, kind="cert_validity",
            status=STATUS_MISS,
            message="共核对 %d 条证件有效期记录，均在有效期内；%s" % (
                len(valid_refs), _CERT_BOUNDARY_NOTICE),
            source=SOURCE, evidence_json=valid_refs,
        ))
        return findings
    if parsed and not has_text:
        message = ("上传的材料里没有文字内容（疑似扫描件），证件有效期无法自动判断，"
                   "需人工翻看原件核对")
    elif not parsed:
        message = "暂无可核对的材料"
    else:
        message = "材料里未见标注证件有效期的文字信息，证件有效期需人工核对"
    findings.append(BsFinding(
        task_id=task.id, item_id=ITEM_ID, kind="cert_validity",
        status=STATUS_UNDECIDABLE, message=message, source=SOURCE,
    ))
    return findings


# ===========================================================================
# A2-03 证件一致性核查（批次1 补口，设计 80 B-3）——「registered_to」口径
# ===========================================================================
"""为什么补这一条（设计 80 B-3：证件"三件事"只做了一件）：

现场指挥部 2025.6.20 要求三原文是**三件事**——"所有注册证书必须注册在本单位（分包单位），
注册专业执业范围必须包含分包类别，全部在有效期内"：

| 事 | 内核 | 状态 |
|---|---|---|
| 有效性（是否过期） | A2-02 cert_validity | ✅ 已有 |
| **一致性（注册在本单位）** | **A2-03 cert_registered_to（本条）** | ✅ 本次补上 |
| 执业范围（专业覆盖分包类别） | A2-04 qualification_match（部分覆盖） | 部分 |
| 真实性（证是否为真） | 需外部核验通道 | ⛔ 本期不接，如实输出不可判定 |

不补的后果（实测口径）：`run_cert_validity_check` 只判日期，所以**别家公司的资质证书
附在报审包里同样判「有效」**——这是实质漏判，不只是文案问题。

判定纪律（宁缺勿错，与全站三态一致）：
- 基准（「本单位」＝分包单位）来源优先级：① 合同/报审材料里 `乙方/分包人/分包单位:`
  明确标注的唯一单位名（`extract.extract_subcontractor_name` 同款正则）；② 退到
  `material.role in ('sub','labor')` 材料的同一抽取。两处都抽不出 → **不可判定**
  （绝不拿文件名猜，也绝不默认"一致"）；
- 逐条比对 `norm_company` 归一后的公司名（剥括号备注 + 去空白），只做**词面**判断：
  - 证书上写的注册单位 ≠ 基准 → 命中「非本单位注册」，交人工核实；
  - 证书上**未标注**注册单位（老式证书常见）→ 不可判定，不判通过也不判违规；
  - 全部一致 → 未命中「均登记在本单位」；
- **不做「真实性」结论**：无外部核验通道，如实输出不可判定并写明原因，绝不省略；
- 同一任务内纯文本比对，不调外部 API、零 LLM。
"""

SOURCE_R = ("依据：现场指挥部2025.6.20要求三（所有注册证书必须注册在本单位（分包单位），"
            "注册专业执业范围必须包含分包类别，全部在有效期内）；"
            "《沪交行规〔2026〕8号》附件3第3项、附件4第3项（证书须为分包人自身人员/证件）")
ITEM_REG = "A2-03"

# 「注册单位」标签：证书正文里标注注册/聘用单位的位置（去空白后匹配）
_REGISTERED_TO_LABEL_RE = re.compile(
    r"(?:注册单位|聘用单位|聘用企业|注册企业|工作单位|受聘单位|单位名称|企业名称)[:：]?"
    r"([^\s，。；、,;:：]{2,40}?(?:有限公司|有限责任公司|集团有限公司|工程公司|公司))")
# 公司名归一化后仍要能识别「这就是个公司」：统一后缀锚定，防把「本单位」这类词当公司名
_COMPANY_SUFFIX = ("有限公司", "有限责任公司", "集团有限公司", "工程公司", "公司")
def _company_of(text: str) -> str | None:
    """证照片段须明确唯一注册单位；无字段或多个矛盾单位均不猜。"""
    names = {norm_company(m.group(1)) for m in _REGISTERED_TO_LABEL_RE.finditer(text or "")}
    return next(iter(names)) if len(names) == 1 else None


def certificate_registered_companies(material) -> set[str]:
    """收集已识别证照页中明确的注册或聘用企业；不推测发证机构。"""
    if material.parse_state != "done":
        return set()
    names = set()
    for page in material.pages_json or []:
        if (not isinstance(page, dict) or type(page.get("page")) is not int
                or page["page"] < 1 or not material_page_readable(material, page["page"])):
            continue
        raw = page.get("text") or ""
        text = _certificate_text(material, raw)
        if (text is None and re.search(r"建造师.{0,8}(?:注册)?证书", raw)
                and not _CERT_DECLARATION_RE.search(raw)):
            text = raw
        if text is not None:
            names.update(norm_company(match.group(1)) for match in _REGISTERED_TO_LABEL_RE.finditer(text))
    names.discard("")
    return names


def subcontractor_name_evidence(materials: list) -> list[dict]:
    """显式分包名称及原页出处；识别冲突保留双侧，不模糊合并企业。

    角色判据与主体归属共用 `extract` 的公开常量：只认「分包人/分包单位」，
    泛化「乙方」不参与；附属协议反转的甲乙方与占位词一并排除。
    """
    from .extract import (SUBCONTRACTOR_PARTY_ROLES, is_placeholder_party_name,
                          party_scoped_pages)
    party_re = re.compile(load_extract_matchers()["party_name_regex"])
    output = []
    for material in materials:
        if material.parse_state != "done":
            continue
        for page_no, text in party_scoped_pages(material):
            if not material_page_readable(material, page_no):
                continue
            for match in party_re.finditer(text):
                company = norm_company(match.group(2))
                if (match.group(1) not in SUBCONTRACTOR_PARTY_ROLES or not company
                        or is_placeholder_party_name(company)):
                    continue
                output.append({"company": company, "material_id": material.id,
                               "page": page_no, "quote": match.group()[:240],
                               "location_mode": "page"})
    return output


def subcontractor_names(materials: list) -> set[str]:
    """本组显式分包名称；复用带出处抽取，避免两套名称判据漂移。"""
    return {ref["company"] for ref in subcontractor_name_evidence(materials)}


def baseline_subcontractor(materials: list) -> str | None:
    """明确唯一分包单位才可作为基准；不能用第一份材料掩盖其他正文矛盾。"""
    names = subcontractor_names(materials)
    return next(iter(names)) if len(names) == 1 else None


def run_cert_registered_to_check(task, materials: list, *, expected_company=None) -> list:
    """A2-03 证件注册单位一致性核查，返回落库前 BsFinding 列表（不 commit）。"""
    parsed = [m for m in materials if m.parse_state == "done"]
    names = subcontractor_names(parsed)
    expected = norm_company(expected_company) if expected_company else None
    if len(names) > 1 or (expected and names and names != {expected}):
        return [BsFinding(
            task_id=task.id, item_id=ITEM_REG, kind="cert_validity", status=STATUS_UNDECIDABLE,
            message="分包单位正文信息或已确认主体存在冲突，证件注册单位无法可靠比对，需人工核对",
            source=SOURCE_R,
        )]
    baseline = next(iter(names)) if names else expected
    # ★ 基准缺失必须先收口：否则逐页比对里 `registered == baseline` 永远为假，
    #   会把「不知道本单位是谁」静默升级成「证书不是本单位的」——假命中比不判更糟。
    if not baseline:
        limits = [limit for material in materials if (limit := _certificate_reading_limit(material))]
        message = "未能从合同或报审材料中定位分包单位名称，证件注册单位是否为本单位无法比对，需人工核对"
        if limits:
            message += "；" + "；".join(limits[:3])
            if len(limits) > 3:
                message += "；另有%d份材料读取不完整" % (len(limits) - 3)
        return [BsFinding(
            task_id=task.id, item_id=ITEM_REG, kind="cert_validity",
            status=STATUS_UNDECIDABLE,
            message=message,
            source=SOURCE_R,
        )]
    findings = []
    consistent = 0
    for m in parsed:
        for p in (m.pages_json or []):
            if (not isinstance(p, dict) or type(p.get("page")) is not int
                    or p["page"] < 1 or not material_page_readable(m, p["page"])):
                continue
            text = _certificate_text(m, p.get("text") or "")
            if text is None:
                continue
            registered = _company_of(text)
            if not registered:
                findings.append(BsFinding(
                    task_id=task.id, material_id=m.id, item_id=ITEM_REG, kind="cert_validity",
                    status=STATUS_UNDECIDABLE, page=p.get("page"), quote=text[:160], source=SOURCE_R,
                    message="该证件未见标注注册单位或存在多个单位，需人工核对原件；" + _CERT_BOUNDARY_NOTICE,
                ))
                continue
            if registered == baseline:
                consistent += 1
                continue
            findings.append(BsFinding(
                task_id=task.id, material_id=m.id, item_id=ITEM_REG,
                kind="cert_validity", status=STATUS_HIT,
                message=("证件标注的注册单位「%s」与本次报审的分包单位「%s」不一致，"
                         "需人工核实；%s"
                         % (registered, baseline, _CERT_BOUNDARY_NOTICE)),
                quote=re.sub(r"\s+", " ", text)[:160], page=p.get("page"),
                source=SOURCE_R,
            ))
    for m in materials:
        limit = _certificate_reading_limit(m)
        if limit:
            findings.append(BsFinding(
                task_id=task.id, material_id=m.id, item_id=ITEM_REG, kind="cert_validity",
                status=STATUS_UNDECIDABLE, message=limit, source=SOURCE_R,
            ))
    if findings:
        return findings
    # 无命中分三种"没结论"：证件未标注册单位 / 全一致 / 无证件材料
    if consistent:
        findings.append(BsFinding(
            task_id=task.id, item_id=ITEM_REG, kind="cert_validity",
            status=STATUS_MISS,
            message="共核对 %d 页证件，注册单位均为「%s」；%s" % (
                consistent, baseline, _CERT_BOUNDARY_NOTICE),
            source=SOURCE_R,
        ))
        return findings
    message = "暂无可核对的证件材料，合同申报或普通单位字段不能代替证照注册信息，需人工核对"
    findings.append(BsFinding(
        task_id=task.id, item_id=ITEM_REG, kind="cert_validity",
        status=STATUS_UNDECIDABLE, message=message, source=SOURCE_R,
    ))
    return findings


# ===========================================================================
# A2-01 社保资料核查（批次2，person_consistency）——覆盖口径（2026-10-06 拍板）
# ===========================================================================
"""名单、社保与劳动合同分别抽事实；姓名覆盖仅为线索，不等价关系成立。
名单复用RC-A09的实填合同段/表，保留全部人员及物理页。
缺主体、缺单位、缺期间、身份未核对或材料未读均交人工；月份口径待业务确认。
正式摄取为唯一OCR阶段，规则阶段不另起OCR、不改原材料。
"""

SOURCE_P = ("依据：《沪城公路〔2025〕118号》附件1第3项（现场主要管理人员名单、联系方式及社保关系）；"
            "《沪交行规〔2026〕8号》附件3第3项、附件4第3项（劳动合同、近一个月社保缴费证明）；"
            "细则第十五条（四）（未建立劳动工资和社会养老保险关系属转包情形）")
ITEM_UNIT = "A2-01"


def _person_evidence_refs(facts: list[dict], materials_by_id: dict) -> list[dict]:
    """把人员事实投影为可逐人回查的引用，不把姓名提升为身份结论。"""
    posts_by_person = {}
    for fact in facts:
        name = str(fact.get("name") or "").strip()
        post = str(fact.get("post") or "").strip() or None
        material = materials_by_id.get(fact.get("material_id"))
        subject_id = getattr(material, "subject_id", None) if material is not None else None
        key = (subject_id, _person_key(fact))
        if name and post:
            posts_by_person.setdefault(key, set()).add(post)

    side_by_kind = {
        "management_roster": "management_roster",
        "social_record": "social_record",
        "labor_contract": "labor_contract",
    }
    refs = []
    for fact in facts:
        name = str(fact.get("name") or "").strip()
        material = materials_by_id.get(fact.get("material_id"))
        subject_id = getattr(material, "subject_id", None) if material is not None else None
        person_key = _person_key(fact) or None
        key = (subject_id, person_key)
        posts = posts_by_person.get(key, set())
        post = str(fact.get("post") or "").strip() or (next(iter(posts)) if len(posts) == 1 else None)
        refs.append({
            **dict(fact),
            "person_key": person_key,
            "post": post,
            "subject_id": subject_id,
            "evidence_side": side_by_kind.get(fact.get("evidence_kind"), fact.get("evidence_kind")),
            "location_mode": "page",
        })
    return refs

def norm_company(name: str) -> str:
    """公司名归一：先整段剥括号备注，再去空白与残留括号。

    （A-15 从 `_norm_company` 提为公开名 norm_company——供同模块 extract.py 跨模块复用，
    遵守「不引用他模块 `_` 私有符号」纪律；本文件内既有调用点同批改用公开名。）
    """
    s = re.sub(r"[（(][^）)]*[)）]", "", name or "")
    return re.sub(r"[\s（）()]", "", s)




_BASELINE_MONTH_RE = re.compile(
    r"(?<!\d)(20\d{2})\s*(?:[年./-]\s*(\d{1,2})(?!\d)|(\d{2})(?!\d))"
    r"(?:\s*月)?(?:\s*[./-]\s*\d{1,2}(?!\d)|\s*\d{1,2}\s*日)?")
_PERSON_LEVEL_ORDER = {"pass": 0, "pending": 1, "missing": 2, "blocking": 3}
_PERSON_STATUS = {"pass": STATUS_MISS, "pending": STATUS_UNDECIDABLE,
                  "missing": STATUS_NOT_INCLUDED, "blocking": STATUS_HIT}


def branch_proof_state(expected_company, employer, proofs) -> str:
    expected, actual = norm_company(expected_company), norm_company(employer)
    rows = [proof for proof in proofs if norm_company(proof.get("branch_name")) == actual
            or actual in {norm_company(value) for value in proof.get("branch_candidates") or []}]
    if any(len(proof.get("branch_candidates") or []) > 1
           or len(proof.get("parent_candidates") or []) > 1 for proof in rows):
        return "conflict"
    parents = {norm_company(proof["parent_company"]) for proof in rows if proof.get("parent_company")}
    if len(parents) > 1 or (parents and parents != {expected}):
        return "conflict"
    registration = any(proof.get("evidence_kind") == "branch_registration"
                       and proof.get("non_independent") for proof in rows)
    affiliation = any(proof.get("evidence_kind") == "branch_affiliation"
                      and norm_company(proof.get("parent_company")) == expected for proof in rows)
    return "proved" if registration and affiliation else "missing"


def same_unit(expected_company, employer, *, branch_proofs=()) -> bool | None:
    """单位归属：本部与分公司（非独立法人）视同本单位；子公司/派遣不认。

    返回 None 表示任一侧缺失或分公司证明不足，不能据此判不一致。
    """
    expected, actual = norm_company(expected_company or ""), norm_company(employer or "")
    if not expected or not actual:
        return None
    if expected == actual:
        return True
    if "子公司" in actual or "人力资源" in actual or "劳务派遣" in actual:
        return False
    if "分公司" in actual or "分支机构" in actual:
        return True if branch_proof_state(expected, actual, branch_proofs) == "proved" else None
    return False


def social_period_state(period, baseline) -> str:
    """社保期间覆盖态：covered / stale / unknown。

    口径（0A.5，2026-11-18.2）：基准日所在月或上一自然月任一被覆盖即算覆盖；
    期间文本无法解析、或基准日缺失时返回 unknown，不猜。
    """
    if baseline is None:
        return "unknown"
    text = period or ""
    matches = list(_BASELINE_MONTH_RE.finditer(text))
    months = [(int(match[1]), int(match[2] or match[3])) for match in matches]
    if not months or any(not 1 <= month <= 12 for _, month in months):
        return "unknown"
    ranges = []
    for index in range(len(matches) - 1):
        separator = text[matches[index].end():matches[index + 1].start()].strip()
        if re.fullmatch(r"(?:至|到|—|–|-|~|～)", separator):
            start, end = months[index:index + 2]
            if start > end:
                return "unknown"
            ranges.append((start, end))
    index = baseline.year * 12 + baseline.month - 1 - 1
    previous = (index // 12, index % 12 + 1)
    for target in ((baseline.year, baseline.month), previous):
        if target in months or any(start <= target <= end for start, end in ranges):
            return "covered"
    return "stale"


def _person_identity_numbers(fact):
    return {number for value in [fact.get("id_number"), *(fact.get("id_number_candidates") or [])]
            for number in [normalize_identity_number(value)] if number}


def _person_key(fact):
    numbers = _person_identity_numbers(fact)
    if len(numbers) == 1:
        # 身份号不直接用作前端分组键；候选原值仍只保留在有来源的事实字段。
        return "id:" + hashlib.sha256(next(iter(numbers)).encode()).hexdigest()[:24]
    return re.sub(r"\s+", "", fact.get("name") or "")


def _person_relation_pool(person, relations, kind, *, ambiguous_name=False):
    """先同主体再身份号；异号/多号只形成身份核对问题，不借单位判违规。"""
    numbers = _person_identity_numbers(person)
    name = re.sub(r"\s+", "", person.get("name") or "")
    scoped = [fact for fact in relations if fact.get("evidence_kind") == kind
              and fact.get("subject_id") == person.get("subject_id")]
    named = [fact for fact in scoped if re.sub(r"\s+", "", fact.get("name") or "") == name]
    if len(numbers) > 1:
        return [], "名单身份证号存在多个候选", False
    if numbers:
        exact = [fact for fact in scoped if _person_identity_numbers(fact) == numbers]
        matched = [fact for fact in exact if re.sub(r"\s+", "", fact.get("name") or "") == name]
        if matched:
            return matched, None, True
        if exact:
            return [], "身份证号相同但姓名不一致，需核对原件", False
        if any(_person_identity_numbers(fact) for fact in named):
            return [], "同名证明身份证号不一致或存在多个候选，不能确认为同一人", False
        if named and ambiguous_name:
            return [], "同名多人缺少可对应的身份证号，不能借用证明", False
        return named, None, False
    candidates = set().union(*(_person_identity_numbers(fact) for fact in named)) if named else set()
    if len(candidates) > 1 or ambiguous_name:
        return [], "同名证明存在多个身份证号，不能合并为同一人", False
    return named, None, False


def _retirement_review(person, relations, expected_company):
    """退休证明触发例外核对；文本候选永远不能代替实际批准及签章复核。"""
    retired, _, identity_matched = _person_relation_pool(person, relations, "retirement_certificate")
    if not retired:
        return None
    if not identity_matched:
        return STATUS_UNDECIDABLE, "同名退休证明身份尚未交叉核对，需确认是否适用退休例外"
    from .personnel_adequacy import normalize_post
    from .rules import load_personnel_adequacy

    data = load_personnel_adequacy()
    post = normalize_post(person.get("post"))
    key_posts = {key for key, value in data["posts"].items() if value.get("key")}
    if post in key_posts:
        return STATUS_NOT_INCLUDED, "关键岗位按已确认口径不自动适用退休返聘例外，需补报符合要求人员"
    other_labels = {value["label"] for value in data.get("other_management", {}).values() if isinstance(value, dict)}
    if post is None and person.get("post") not in other_labels:
        return STATUS_UNDECIDABLE, "退休人员岗位未明确，不能推定为可适用例外的非关键岗位"
    missing, conflicts = [], []
    for kind, label in (("reemployment_agreement", "返聘协议"),
                        ("attendance_commitment", "到岗承诺"), ("owner_approval", "业主书面允许")):
        pool, issue, linked = _person_relation_pool(person, relations, kind)
        if issue:
            conflicts.append(label + "身份存在冲突")
        elif not pool:
            missing.append(label)
        elif not linked:
            conflicts.append(label + "缺少身份证交叉核对")
        elif kind == "owner_approval" and any(row.get("approval_state") != "candidate" for row in pool):
            conflicts.append("业主意见未明确允许返聘或存在拒绝，需人工核对")
        elif kind == "reemployment_agreement":
            units = {norm_company(row.get("employer")) for row in pool if row.get("employer")}
            if not units:
                missing.append("返聘单位字段")
            elif units != {norm_company(expected_company)}:
                conflicts.append("返聘单位与本单位不一致，需核对")
    if missing:
        return STATUS_NOT_INCLUDED, "退休人员默认不按普通社保关系放行，缺" + "、".join(missing)
    if conflicts:
        return STATUS_UNDECIDABLE, "；".join(conflicts)
    return STATUS_UNDECIDABLE, "非关键岗位退休返聘证明候选齐备，需人工核对业主实际书面允许、签章及适用条件"


def evaluate_person_relations(names, relations, *, expected_company, baseline_date,
                               posts=None, roster=None) -> list[dict]:
    """逐人核验劳动合同/社保/单位/期间（纯函数，不重开 PDF、不推断同人）。

    一人一结论：单位不一致=不符合；缺合同/缺社保/期间不覆盖=缺项；
    候选冲突、期间不可解析、单位基准缺失=待核实（证据矛盾或缺外部参数）。
    """
    expected = norm_company(expected_company or "")
    posts = posts or {}
    results = []
    people = roster if roster is not None else [{"name": name} for name in names]
    seen = set()
    for person in people:
        name = person["name"]
        key = re.sub(r"\s+", "", name)
        identity_key = (person.get("subject_id"), _person_key(person))
        if identity_key in seen:
            continue
        seen.add(identity_key)
        retirement = _retirement_review(person, relations, expected_company)
        if retirement:
            status, reason = retirement
            results.append({"name": name, "post": person.get("post"), "status": status,
                            "person_key": identity_key[1], "subject_id": person.get("subject_id"),
                            "identity_basis": "retirement_exception_candidate",
                            "message": "%s（%s）：%s" % (name, person.get("post") or "岗位未明", reason)})
            continue
        same_name = [fact for fact in [*people, *relations]
                     if fact.get("subject_id") == person.get("subject_id")
                     and re.sub(r"\s+", "", fact.get("name") or "") == key]
        numbers = set().union(*(_person_identity_numbers(fact) for fact in same_name))
        ambiguous = len(numbers) > 1
        social, social_issue, social_id = _person_relation_pool(
            person, relations, "social_record", ambiguous_name=ambiguous)
        labor, labor_issue, labor_id = _person_relation_pool(
            person, relations, "labor_contract", ambiguous_name=ambiguous)
        level, issues = "pass", []
        branch_proofs = [fact for fact in relations if fact.get("subject_id") == person.get("subject_id")
                         and fact.get("evidence_kind") in {"branch_registration", "branch_affiliation"}]

        def bump(candidate):
            nonlocal level
            if _PERSON_LEVEL_ORDER[candidate] > _PERSON_LEVEL_ORDER[level]:
                level = candidate

        facts = social + labor
        for issue in (social_issue, labor_issue):
            if issue:
                issues.append(issue)
                bump("pending")
        if (any(len(f.get("employer_candidates") or []) > 1 for f in facts)
                or any(len(f.get("period_candidates") or []) > 1 for f in facts)):
            issues.append("证明材料内单位或期间存在多个候选")
            bump("pending")
        if not labor and not labor_issue:
            issues.append("缺劳动合同")
            bump("missing")
        if not social and not social_issue:
            issues.append("缺社保缴费记录")
            bump("missing")
        if expected:
            for label, pool in (("劳动合同", labor), ("社保", social)):
                for fact in pool:
                    matched = same_unit(expected, fact.get("employer"), branch_proofs=branch_proofs)
                    if matched is False:
                        identity_matched = labor_id if label == "劳动合同" else social_id
                        if identity_matched:
                            issues.append("%s单位「%s」与本单位不一致" % (label, fact.get("employer")))
                            bump("blocking")
                        else:
                            issues.append("%s同名证明单位不同且缺少身份证交叉核对，需核对本人关系" % label)
                            bump("pending")
                    elif matched is None:
                        employer = norm_company(fact.get("employer"))
                        if employer and ("分公司" in employer or "分支机构" in employer):
                            conflict = branch_proof_state(expected, employer, branch_proofs) == "conflict"
                            issues.append("分公司登记或隶属关系证明%s" % ("存在冲突，需核对" if conflict else "缺项"))
                            bump("pending" if conflict else "missing")
                        else:
                            issues.append("%s单位缺失" % label)
                            bump("missing")
        labor_units = {norm_company(f["employer"]) for f in labor if f.get("employer")}
        social_units = {norm_company(f["employer"]) for f in social if f.get("employer")}
        pair_states = [same_unit(left, right, branch_proofs=branch_proofs)
                       for labor_unit in labor_units for social_unit in social_units
                       for left, right in ((labor_unit, social_unit), (social_unit, labor_unit))]
        if pair_states and all(state is False for state in pair_states):
            issues.append("劳动合同与社保单位不一致")
            bump("blocking" if labor_id and social_id else "pending")
        if social:
            states = [social_period_state(fact.get("period"), baseline_date) for fact in social]
            if all(state == "unknown" for state in states):
                issues.append("社保缴费期间不明")
                bump("pending")
            elif "covered" not in states:
                issues.append("社保缴费期间未覆盖基准月或上一自然月")
                bump("missing")
        if not expected:
            issues.append("分包单位基准未明确")
            bump("pending")
        if level == "pass" and not (social_id and labor_id):
            issues.append("缺少可交叉核对的完整身份证号，仅形成姓名单位候选，需核对同一人")
            bump("pending")
        advisories = []
        salary, salary_issue, salary_linked = _person_relation_pool(person, relations, "salary_record", ambiguous_name=ambiguous)
        if salary_issue:
            advisories.append("工资补充证据身份需核对（仅提示）")
        elif salary and not salary_linked:
            advisories.append("已见同名工资补充记录，身份尚未交叉核对（仅提示）")
        elif salary:
            salary_units = {norm_company(row.get("employer")) for row in salary if row.get("employer")}
            advisories.append("已见工资补充记录，单位线索%s（仅提示）" % (
                "一致" if salary_units == {expected} else "不明或不同，需核对"))
        label = "；".join(dict.fromkeys(issues)) if issues else "劳动合同、社保单位一致且缴费期间覆盖基准月"
        if advisories:
            label += "；" + "；".join(advisories)
        post = person.get("post") or posts.get(key)
        results.append({
            "name": name, "post": post, "status": _PERSON_STATUS[level],
            "person_key": identity_key[1], "subject_id": person.get("subject_id"),
            "identity_basis": "id_number" if social_id and labor_id else "name_company_candidate",
            "advisories": advisories,
            "message": ("%s（%s）：%s" % (name, post, label)) if post else ("%s：%s" % (name, label)),
        })
    return results


def aggregate_person_status(results: list[dict]) -> str:
    """整体结论取最严一档：不符合 > 缺项 > 待核实 > 符合。"""
    statuses = {result["status"] for result in results}
    for status in (STATUS_HIT, STATUS_NOT_INCLUDED, STATUS_UNDECIDABLE, STATUS_MISS):
        if status in statuses:
            return status
    return STATUS_UNDECIDABLE


def run_person_certificate_fields_check(task, materials):
    """A2-10：逐人保留证件字段与身份对应候选；不代替A2-02/05的判定。"""
    from . import personnel_certificates as certs
    by_id = {m.id: m for m in materials}
    def readable(fact):
        material = by_id.get(fact.get("material_id"))
        return material is not None and material_page_readable(material, fact.get("page"))
    roster = [row for row in extract_management_roster(materials) if readable(row)]
    proofs = [row for row in certs.extract_person_certificates(materials) if readable(row)]
    if not roster:
        return []
    identities = {}
    for row in roster:
        identities.setdefault((row.get("subject_id"), row["name"]), set()).add(_person_key(row))
    findings = []
    for person in roster:
        ambiguous = len(identities[(person.get("subject_id"), person["name"])]) > 1
        matched, error, identity_matched = _person_relation_pool(
            person, proofs, "person_certificate", ambiguous_name=ambiguous)
        # 未匹配记录也保留为原文候选，但不得放入另一个人的证据组。
        refs = _person_evidence_refs([person, *matched], by_id)
        state = "identity_fields_correspond" if identity_matched else "identity_pending"
        for ref in refs:
            ref["certificate_identity_state"] = state
        label = person["name"] + ("（%s）" % person["post"] if person.get("post") else "")
        details = []
        for proof in matched:
            values = [proof.get("certificate_type") or proof["document_title"],
                      proof.get("certificate_number") or "编号未唯一提取",
                      "有效期至" + proof["validity_end"] if proof.get("validity_end") else
                      proof.get("validity_period") or "有效期未唯一提取"]
            details.append("、".join(values))
        message = "人员证件字段：%s；%s；%s；需核对原件、岗位适用性及证件有效期" % (
            label, "；".join(details) or "尚无可对应的人员证书字段",
            error or ("姓名及完整身份证字段对应，不代表证件真实性已验证" if identity_matched else
                      "缺少可交叉对应的完整身份证字段，仅保留姓名候选"))
        findings.append(BsFinding(task_id=task.id, item_id=certs.ITEM_ID, kind="cert_validity",
            status=STATUS_UNDECIDABLE, source=certs.SOURCE, material_id=person["material_id"],
            page=person["page"], quote=person["quote"], location_mode="page",
            message=bounded_finding_message(message), evidence_json=refs))
    return findings


def run_person_attendance_check(task, materials):
    """A2-11辅助履约提示，不推断出勤率、缺勤或同时受聘。"""
    from . import personnel_attendance as attendance
    by_id = {m.id: m for m in materials}
    def readable(row):
        m = by_id.get(row.get("material_id"))
        return m is not None and material_page_readable(m, row.get("page"))
    roster = [row for row in extract_management_roster(materials) if readable(row)]
    records = [row for row in attendance.extract_attendance_records(materials) if readable(row)]
    commitments = [row for row in extract_person_supplement_evidence(materials)
                   if row.get("evidence_kind") == "attendance_commitment" and readable(row)]
    identities = {}
    for row in roster:
        identities.setdefault((row.get("subject_id"), row["name"]), set()).add(_person_key(row))
    findings = []
    for person in roster:
        sources, reasons = [], []
        for kind in ("attendance_record", "project_assignment", "attendance_commitment"):
            matched, error, identity_matched = _person_relation_pool(person, records + commitments, kind,
                ambiguous_name=len(identities[(person.get("subject_id"), person["name"])]) > 1)
            sources.extend({**row, "attendance_identity_state": "identity_fields_correspond" if identity_matched else
                            "identity_pending"} for row in matched)
            if error and error not in reasons:
                reasons.append(error)
        details = []
        for row in sources:
            if row["evidence_kind"] == "attendance_commitment":
                details.append("已见到岗承诺，仅为声明，不能证明实际出勤")
            else:
                details.append("%s：%s，%s，原文状态%s%s" % (
                    "考勤记录" if row["evidence_kind"] == "attendance_record" else "任职记录",
                    row.get("project") or "项目未唯一提取", row.get("period") or "日期/期间未唯一提取",
                    row.get("attendance_state") or "未唯一提取",
                    "（身份仅为候选）" if row["attendance_identity_state"] != "identity_fields_correspond" else ""))
        refs = _person_evidence_refs([person, *sources], by_id)
        for ref in refs:
            if ref.get("evidence_kind") == "management_roster":
                ref["attendance_identity_state"] = "identity_pending"
        findings.append(BsFinding(task_id=task.id, item_id=attendance.ITEM_ID, kind="person_consistency",
            status=STATUS_UNDECIDABLE, finding_semantics="manual_prompt", source=attendance.SOURCE,
            material_id=person["material_id"], page=person["page"], quote=person["quote"], location_mode="page",
            evidence_json=refs, message=bounded_finding_message("在岗辅助核对：%s；%s；%s请结合原件核对实际任职时段与项目归属，单份材料不证明未在其他项目受聘" % (
                person["name"], "；".join(details) or "尚无可对应的考勤或任职记录，不能由此认定未到岗",
                "；".join(reasons) + "；" if reasons else ""))))
    return findings


def run_person_consistency_check(task, materials: list, *, expected_company=None) -> list:
    """A2-01：逐人核验劳动合同、本单位社保与缴费期间。

    规则只读取摄取结果；不重开PDF、不发OCR。姓名相同不是同人证明，
    缺名也不是未参保证明；基准日缺失或期间不可解析时判待核实，不猜。
    """
    by_id = {material.id: material for material in materials}

    def readable_fact(fact):
        material = by_id.get(fact.get("material_id"))
        page = fact.get("page")
        return material is not None and type(page) is int and page > 0 and material_page_readable(material, page)

    roster = [fact for fact in extract_management_roster(materials) if readable_fact(fact)]
    relations = [fact for fact in extract_person_relation_evidence(materials) if readable_fact(fact)]
    relations.extend(fact for fact in extract_person_supplement_evidence(materials) if readable_fact(fact))
    social = [fact for fact in relations if fact["evidence_kind"] == "social_record"]
    candidates = social_evidence_materials(materials)
    facts = roster + relations
    refs = _person_evidence_refs(facts, by_id)
    gaps = []
    names = list(dict.fromkeys(fact["name"] for fact in roster))
    if not names:
        gaps.append("主要管理人员名单未能提取到人员姓名，需补充实填名单或人工翻看原件")
    if not candidates:
        gaps.append("未能识别到社保缴费记录原文，请核对报审材料；合同缴费义务不能作为参保证明")
    elif not social:
        has_text = any((p.get("text") or "").strip()
                       for m in candidates for p in m.pages_json or [])
        gaps.append("社保信息里未提取到人员姓名，需人工核对"
                    if has_text else "社保材料里没有可读文字，需人工翻看原件并重新解析核查")
    roster_ids = {fact["material_id"] for fact in roster}
    roster_gaps = [(m, material_reading_gap(m)) for m in materials if m.id in roster_ids]
    roster_gaps = [(m, pages) for m, pages in roster_gaps if pages is not None]
    if roster_gaps:
        gaps.append("名单所在材料部分内容尚未确认，已提取人员不代表完整范围，需人工核对原件")
    unread = [(m, material_reading_gap(m)) for m in candidates]
    unread = [(m, pages) for m, pages in unread if pages is not None]
    if unread:
        gaps.append("社保候选材料部分内容尚未确认，未确认姓名不能认定未参保，需人工核对原件")
    companies = subcontractor_names(materials)
    expected = norm_company(expected_company) if expected_company else (
        next(iter(companies)) if len(companies) == 1 else None)
    if expected_company and companies and companies != {expected}:
        gaps.append("已确认主体与合同单位冲突，需核对主体归属")
    if not expected:
        gaps.append("分包单位基准未明确")
    baseline = getattr(task, "baseline_date", None)
    if baseline is None:
        created = getattr(task, "created_at", None)
        baseline = created.date() if hasattr(created, "date") else None
    posts = {}
    for fact in roster:
        if fact.get("post"):
            posts.setdefault(re.sub(r"\s+", "", fact["name"]), fact["post"])
    persons = evaluate_person_relations(names, relations, expected_company=expected,
                                        baseline_date=baseline, posts=posts, roster=roster)
    if names and relations and any(person["identity_basis"] == "name_company_candidate" for person in persons):
        gaps.append("同名尚不能证明同一人，缺号时仅按姓名及单位形成候选，需人工核对身份及关系")
    if persons:
        status = aggregate_person_status(persons)
    elif any(len(fact.get("employer_candidates") or []) > 1
             or len(fact.get("period_candidates") or []) > 1 for fact in relations):
        status = STATUS_UNDECIDABLE
    else:
        status = STATUS_NOT_INCLUDED
    messages = [person["message"] for person in persons[:5]] + gaps
    anchor = (social or roster or relations or [None])[0]
    return [BsFinding(
        task_id=task.id, material_id=anchor["material_id"] if anchor else (
            candidates[0].id if candidates else None),
        item_id=ITEM_UNIT, kind="person_consistency", status=status,
        message=bounded_finding_message("；".join(messages)), source=SOURCE_P,
        quote=anchor["quote"] if anchor else None,
        page=anchor["page"] if anchor else None, evidence_json=refs,
    )]

# ===========================================================================
# ② 资质与内容合规性审查 · 资质匹配（内核名 qualification_match）——词表保守口径（09-22 拍板）
# ===========================================================================
"""判定纪律（词表保守口径，不引 LLM）：
- 分包合同「分包工程内容/范围」标签后窗口内识别工程类别（词表入
  qualification_matchers.json，改文件即生效）；
- 营业执照/资质证书「经营范围/资质类别」标签后窗口做词面包含比对；
- 类别未被经营范围文本覆盖 → 命中「可能超出承揽范围」交人工核实
  （不自动定性——机器结论仅为提示，最终由人工认定）；
- 定位不了分包内容、识别不出类别、找不到经营范围 → 一律不可判定。
"""

SOURCE_Q = ("依据：《沪交行规〔2026〕8号》第十条（分包人应当具备的条件）；"
            "《沪城公路〔2025〕118号》附件1第2项（营业执照、资质证书、安全生产许可证）；"
            "《交办公路〔2024〕6号》公路工程施工分包负面清单")
ITEM_QUAL = "A2-04"
_QUAL_BOUNDARY_NOTICE = "仅为所附证照文字的初步类别比对，未核定正式资质等级、工程规模或真实性"
_SCOPE_WINDOW = 400
_WSQ_RE = re.compile(r"\s+")
_LABOR_CERT_TITLES = ("施工劳务备案证书", "施工劳务资质证书")
_LABOR_CERT_NO_RE = re.compile(r"证书编号\s*[:：]\s*([A-Za-z0-9]+)")
_LABOR_CERT_VALID_RE = re.compile(
    r"有效期至\s*[:：]\s*(\d{4}\s*[-/年]\s*\d{1,2}\s*[-/月]\s*\d{1,2}\s*日?)")


def _material_full_text(material) -> str:
    return _WSQ_RE.sub("", "\n".join(
        p.get("text", "") for p in (material.pages_json or [])))


def _label_window(text: str, patterns: list):
    """标签后窗口文本（去空白全文上找第一个命中的标签）。"""
    for pat in patterns:
        i = text.find(pat)
        if i >= 0:
            return text[i + len(pat): i + len(pat) + _SCOPE_WINDOW]
    return None


def _quote_and_page(material, alias: str):
    """类别词在原文的引文与页码（与 a3_check 同款去空白匹配口径）。"""
    for p in (material.pages_json or []):
        text = _WSQ_RE.sub("", p.get("text", ""))
        i = text.find(alias)
        if i >= 0:
            return text[max(0, i - 20): i + len(alias) + 20], p.get("page")
    return None, None


def _labor_certificate_finding(task, materials: list):
    """劳务分包：施工劳务备案/资质证书不适用专业承包类别词表。"""
    for material in materials:
        for page in material.pages_json or []:
            text = page.get("text") or ""
            title = next((name for name in _LABOR_CERT_TITLES if name in text), None)
            if title is None:
                continue
            # 报审表附件清单也会提到证书名称，必须同时有证书编号或有效期才按实际证照处理。
            cert_no = _LABOR_CERT_NO_RE.search(text)
            valid = _LABOR_CERT_VALID_RE.search(text)
            if cert_no is None and valid is None:
                continue
            details = []
            if cert_no:
                details.append("证书编号" + cert_no.group(1))
            if valid:
                details.append("有效期至" + valid.group(1).replace(" ", ""))
            return BsFinding(
                task_id=task.id, material_id=material.id, item_id=ITEM_QUAL,
                kind="qualification_match", status=STATUS_MISS,
                message="劳务分包，已提供%s%s；施工劳务备案/资质不适用专业承包类别匹配；%s"
                        % (title, "（%s）" % "，".join(details) if details else "",
                           _QUAL_BOUNDARY_NOTICE),
                quote=text[:240], page=page.get("page"), source=SOURCE_Q,
                evidence_json=[{"material_id": material.id, "page": page.get("page"),
                                "quote": text[:240], "source_kind": "labor_certificate"}])
    return None


def _certificate_pages(materials: list) -> list:
    """A2-04 第二段：把每页证照原文连同材料/页码交给等级核对（纯函数模块不依赖 DB）。"""
    pages = []
    for material in materials:
        for page in material.pages_json or []:
            certificate = _certificate_text(material, page.get("text") or "")
            if certificate is None:
                continue
            pages.append({"material_id": material.id, "page": page.get("page"),
                          "text": _WSQ_RE.sub("", certificate)})
    return pages


def run_qualification_match_check(task, materials: list) -> list:
    """A2-04 资质匹配（词表保守口径），返回落库前 BsFinding 列表（不 commit）。"""
    matchers = load_qualification_matchers()
    done = [m for m in materials if m.parse_state == "done"]
    sub = [m for m in done if m.role in ("sub", "labor")]

    # 劳务分包先看施工劳务备案/资质证书；命中即视为满足劳务资格要求，
    # 不再用专业承包工程类别词表去要求“资质类别/等级覆盖分包范围”。
    if any(m.role == "labor" for m in done):
        labor_certificate = _labor_certificate_finding(task, done)
        if labor_certificate is not None:
            return [labor_certificate]

    scope_win = scope_mat = None
    for m in sub:
        w = _label_window(_material_full_text(m), matchers["scope_label_patterns"])
        if w:
            scope_win, scope_mat = w, m
            break
    if scope_win is None:
        return [BsFinding(
            task_id=task.id, item_id=ITEM_QUAL, kind="qualification_match",
            status=STATUS_UNDECIDABLE,
            message="未能从分包合同中定位「分包工程内容/范围」表述，资质匹配需人工核对",
            source=SOURCE_Q)]

    scope_cats = []
    for c in matchers["works_categories"]:
        words = [c["key"]] + c.get("aliases", [])
        if any(w in scope_win for w in words):
            scope_cats.append(c)
    if not scope_cats:
        return [BsFinding(
            task_id=task.id, material_id=scope_mat.id, item_id=ITEM_QUAL,
            kind="qualification_match", status=STATUS_UNDECIDABLE,
            message="未能从分包工程内容中识别出词表内的工程类别，资质匹配需人工核对",
            source=SOURCE_Q)]

    biz_win = None
    biz_ref = declaration_ref = None
    for m in done:
        for page in m.pages_json or []:
            text = page.get("text") or ""
            certificate = _certificate_text(m, text)
            if certificate is None:
                # 合同声明可留作摘录，不得贡献实际证照的经营/资质范围。
                if m.role in ("general", "sub", "labor") and declaration_ref is None:
                    label = next((label for label in matchers["biz_label_patterns"]
                                  if label in _WSQ_RE.sub("", text)), None)
                    if label:
                        quote, page_no = _quote_and_page(m, label)
                        declaration_ref = {"material_id": m.id, "page": page_no, "quote": quote,
                                           "source_kind": "contract_declaration"}
                continue
            w = _label_window(_WSQ_RE.sub("", certificate), matchers["biz_label_patterns"])
            if w:
                biz_win = w
                biz_ref = {"material_id": m.id, "page": page.get("page"), "quote": certificate[:240],
                           "source_kind": "certificate_text"}
                break
        if biz_win is not None:
            break
    if biz_win is None:
        return [BsFinding(
            task_id=task.id, material_id=(declaration_ref or {}).get("material_id", scope_mat.id), item_id=ITEM_QUAL,
            kind="qualification_match", status=STATUS_UNDECIDABLE,
            message="未取得可用于核对经营范围或资质类别的实际证照，分包内容是否超出承揽范围需人工核对"
                    + ("；已保留合同声明摘录，声明不能代替实际证照" if declaration_ref else ""),
            quote=(declaration_ref or {}).get("quote"), page=(declaration_ref or {}).get("page"),
            evidence_json=[declaration_ref] if declaration_ref else None,
            source=SOURCE_Q)]

    def _covered(cat: dict) -> bool:
        return any(w in biz_win for w in [cat["key"]] + cat.get("aliases", []))

    uncovered = [c for c in scope_cats if not _covered(c)]
    findings = []
    for c in uncovered:
        alias = next(w for w in [c["key"]] + c.get("aliases", []) if w in scope_win)
        quote, page = _quote_and_page(scope_mat, alias)
        findings.append(BsFinding(
            task_id=task.id, material_id=scope_mat.id, item_id=ITEM_QUAL,
            kind="qualification_match", status=STATUS_HIT,
            message="分包内容含「%s」，未见于营业执照经营范围/资质类别表述，"
                    "可能超出承揽范围；如经营范围表述笼统，请人工核实；%s" % (c["key"], _QUAL_BOUNDARY_NOTICE),
            quote=quote, page=page, source=SOURCE_Q,
            evidence_json=[{"material_id": scope_mat.id, "page": page, "quote": quote,
                            "source_kind": "contract_scope"}, biz_ref],
        ))
    if not findings:
        covered_names = "、".join(c["key"] for c in scope_cats)
        findings.append(BsFinding(
            task_id=task.id, material_id=scope_mat.id, item_id=ITEM_QUAL,
            kind="qualification_match", status=STATUS_MISS,
            message="分包工程内容（%s）均见于经营范围/资质类别表述；%s" % (covered_names, _QUAL_BOUNDARY_NOTICE),
            source=SOURCE_Q,
            evidence_json=[biz_ref],
        ))
    # 第二段：等级与规模覆盖（设计 5.1–5.4）。类别已由第一段识别；这里只对
    # 证照载明类别核对等级与量化阈值，参数或依据缺失一律待核实，不猜。
    findings.extend(qualification_scope.evaluate_scope(
        task_id=task.id, item_id=ITEM_QUAL, source=SOURCE_Q,
        scope_categories=scope_cats,
        scope_text=scope_win or _material_full_text(scope_mat),
        scope_material=scope_mat,
        certificate_pages=_certificate_pages(done),
    ))
    return findings


# ===========================================================================
# ③ 关键岗位人员核查 · 岗位配置与人员配备（内核名 personnel_adequacy）
# ===========================================================================
"""判定纪律（DG/TJ 08-2225 表3.3.3/3.4.1，见 docs/design/2026-11-18-dgtj-2225-staffing-table-review.md）：
- 专业分包按工程类别与规模套表，劳务分包按作业人数分档套表；
- 关键岗位未配置＝缺项；安全员低于交安25号令法定下限＝不符合；
- 非关键岗位人数不足只作提示，不改变「通过」结论；
- 分包类型、工程类别、规模或作业人数任一无法确定时不估算，判「无法核实」并写明缺什么。
"""

SOURCE_STAFF = ("依据：上海市工程建设规范 DG/TJ 08-2225-2017《建筑工程、公路与市政工程施工现场专业人员配备标准》"
                "表3.3.3（专业分包）、表3.4.1（劳务分包）；"
                "《公路水运工程安全生产监督管理办法》（交通运输部令2017年第25号）第十四条（专职安全员数量）；"
                "《沪交行规〔2026〕8号》第七条（现场管理机构及主要管理人员）")
ITEM_STAFF = "A2-07"

_STAFF_CATEGORY_KEYWORDS = (
    ("隧道", "tunnel"),
    ("桥梁", "bridge"),
    ("交通安全设施", "traffic_safety"),
    ("高速公路", "highway_expressway"),
    ("一级公路", "highway_first_class"),
    ("二级公路", "highway_secondary"),
    ("三级公路", "highway_secondary"),
    ("四级公路", "highway_secondary"),
)
_STAFF_CONTRACT_TYPES = {"劳务合作": "labor", "劳务分包": "labor",
                         "专业分包": "professional", "专业承包": "professional"}


def _staffing_contract(materials: list, extract_fields) -> dict:
    """从分包/劳务合同读取分包类型、合同额（万元）与工程范围文本。

    只认 ``sub`` / ``labor``：总包合同（``general``）的主体与金额口径不同，
    混入会污染分包合同额并导致规模档位误判。
    """
    from .safety_permit_scope import extract_contract_scope_records
    types, amounts, amount_refs = set(), set(), []
    type_conflict = False
    for material in materials:
        if getattr(material, "role", None) not in ("sub", "labor"):
            continue
        fields = (extract_fields(material) or {}).get("fields") or {}
        type_field = fields.get("contract_type") or {}
        label = type_field.get("value")
        type_conflict |= any("冲突" in str(value) for value in type_field.get("missing") or [])
        if label in _STAFF_CONTRACT_TYPES:
            types.add(_STAFF_CONTRACT_TYPES[label])
            if material.role == "labor" and _STAFF_CONTRACT_TYPES[label] != "labor":
                type_conflict = True
        amount = fields.get("amount") or {}
        if (amount.get("status") == STATUS_HIT and type(amount.get("value")) in (int, float)
                and material_page_readable(material, amount.get("page"))):
            amounts.add(float(amount["value"]))
            amount_refs.append({"material_id": material.id, "page": amount.get("page"),
                                "quote": amount.get("quote"), "evidence_kind": "staffing_contract_amount",
                                "value_wan": amount["value"]})
    roles = {getattr(material, "role", None) for material in materials}
    if not types and not type_conflict:
        if "labor" in roles:
            types.add("labor")
        elif "sub" in roles:
            types.add("professional")
    by_id = {material.id: material for material in materials}
    records = extract_contract_scope_records(materials)
    scopes = [record for record in records if record.get("complete")
              and material_page_readable(by_id[record["material_id"]], record["page"])]
    return {
        "type": next(iter(types)) if len(types) == 1 and not type_conflict else None,
        "amount_wan": next(iter(amounts)) if len(amounts) == 1 else None,
        "scope": "\n".join(record["scope"] for record in scopes),
        "scope_records": scopes, "amount_refs": amount_refs,
        "scope_incomplete": len(scopes) != len(records),
        "amount_conflict": len(amounts) > 1,
    }


def _staffing_category(scope_text: str) -> str | None:
    categories = {category for keyword, category in _STAFF_CATEGORY_KEYWORDS if keyword in scope_text}
    specialties = categories & {"tunnel", "bridge", "traffic_safety"}
    if specialties:
        if len(specialties) == 1 and not re.search(r"路基|路面", scope_text):
            return next(iter(specialties))
        return None
    return next(iter(categories)) if len(categories) == 1 else None


def _staffing_scale_fields(category: str | None, scope_text: str) -> list[str] | None:
    """仅完整范围明示单项分包时排除不适用维度，普通提及不作排除证明。"""
    if category not in {"highway_first_class", "highway_secondary"}:
        return None
    text = re.sub(r"\s+", "", scope_text)
    parts = set(re.findall(r"路基|路面", text))
    only = re.search(r"仅(?:承担|实施|包括|负责)?(路基|路面)(?:工程|施工)?(?:[，。；,;]|$)", text)
    if only and parts == {only.group(1)}:
        field = "roadbed_km" if only.group(1) == "路基" else "pavement_area_wan_m2"
        return [field, "contract_amount_yi"]
    return None


def _staffing_scale_context(contract: dict) -> dict:
    from .personnel_adequacy import determine_scale, extract_scale_parameters
    params, candidates, conflicts = extract_scale_parameters(contract["scope_records"])
    if contract["amount_wan"] is not None:
        params["contract_amount_yi"] = contract["amount_wan"] / 10000
    category = _staffing_category(contract["scope"])
    fields = _staffing_scale_fields(category, contract["scope"])
    errors = []
    if conflicts:
        errors.append("规模参数冲突：" + "、".join(conflicts))
    if category is None and contract["scope"]:
        errors.append("工程类别冲突或未能唯一识别本次分包工程类别")
    if contract["scope_incomplete"]:
        errors.append("分包范围段未完整读取")
    if contract["amount_conflict"]:
        errors.append("分包合同额冲突")
    if re.search(r"不含|不包括|不涉及|不承担|拟实施|暂定|不超过|不大于|不少于|不小于|以上|以下|[<>≤≥]|约\s*\d", contract["scope"]):
        errors.append("分包范围含否定、范围值或暂定表述，规模参数归属需核对")
    info = determine_scale(category, params, scale_fields=fields)
    return {"params": params, "candidates": candidates, "conflicts": conflicts,
            "category": category, "scale_fields": fields, "scale_info": info,
            "scale": None if errors else info["scale"], "errors": errors}


ITEM_TECHNICAL_TITLE = "A2-14"
SOURCE_TECHNICAL_TITLE = "DG/TJ 08-2225-2017第4.2.2条，PDF物理第15页（工程技术职称等级与规模维度，非全部岗位资格）"


def run_technical_title_check(task, materials: list) -> list:
    from .extract import extract_contract_fields
    from .personnel_adequacy import normalize_post, evaluate_technical_title, scale_reason_text
    from .personnel_certificates import extract_person_certificates
    from .finding_text import bounded_finding_message
    by_id = {m.id: m for m in materials}

    def readable(row):
        return row.get("material_id") in by_id and material_page_readable(
            by_id[row["material_id"]], row.get("page"))

    roster = [row for row in extract_management_roster(materials) if readable(row)]
    people = [row for row in roster if normalize_post(row.get("post")) == "technical_lead"]
    if not people:
        return []
    contract = _staffing_contract(materials, extract_contract_fields)
    if contract["type"] == "labor":
        return []
    context = _staffing_scale_context(contract)
    proofs = [row for row in extract_person_certificates(materials) if readable(row)]
    identities = {}
    for row in roster:
        identities.setdefault((row.get("subject_id"), row["name"]), set()).add(_person_key(row))
    findings = []
    for person in people:
        matched, error, exact = _person_relation_pool(person, proofs, "person_certificate",
            ambiguous_name=len(identities[(person.get("subject_id"), person["name"])]) > 1)
        result = evaluate_technical_title(matched, context["scale"])
        reasons = list(context["errors"])
        if not exact:
            reasons.append(error or "完整身份证字段尚未交叉对应，需核对身份")
        if contract["type"] != "professional":
            reasons.append("分包类型未唯一识别，需核对适用性")
        if result["status"] != STATUS_MISS and any(material_reading_gap(m) is not None for m in materials):
            reasons.append("材料存在未确认页，需核对是否还有对应职称证明")
        if reasons:
            result = {**result, "status": STATUS_UNDECIDABLE,
                      "reason": "；".join(dict.fromkeys(reasons + [result["reason"]]))}
        if context["scale"] is None:
            result["reason"] += "；" + scale_reason_text(context["scale_info"]["reason"])
        refs = _person_evidence_refs([person, *matched], by_id)
        for ref in refs:
            ref.update(certificate_identity_state="identity_fields_correspond" if exact else "identity_pending",
                       technical_title_assessment=result, technical_title_scale=context["scale"])
        refs.extend({**row, "technical_title_scale": context["scale"]}
                    for row in contract["scope_records"] + contract["amount_refs"])
        scale_label = {"small": "小型", "medium": "中型", "large": "大型"}.get(context["scale"], "待核实")
        findings.append(BsFinding(task_id=task.id, item_id=ITEM_TECHNICAL_TITLE, kind="personnel_adequacy",
            status=result["status"], source=SOURCE_TECHNICAL_TITLE, material_id=person["material_id"],
            page=person["page"], quote=person["quote"], location_mode="page", evidence_json=refs,
            message=bounded_finding_message("技术负责人职称与规模：%s，%s；%s；仅核已提交证书等级维度，原件真实性及其他岗位资格另行核对" % (
                person["name"], scale_label, result["reason"]))))
    return findings


def run_personnel_staffing_check(task, materials: list) -> list:
    """A2-07 岗位配置与人员配备，返回落库前 BsFinding 列表（不 commit）。"""
    from .extract import extract_contract_fields
    from .finding_text import bounded_finding_message
    from .personnel_adequacy import evaluate_staffing, scale_reason_text

    by_id = {material.id: material for material in materials}

    def readable_fact(fact):
        material = by_id.get(fact.get("material_id"))
        page = fact.get("page")
        return material is not None and type(page) is int and page > 0 and material_page_readable(material, page)

    roster = [fact for fact in extract_management_roster(materials) if readable_fact(fact)]
    refs = _person_evidence_refs(roster, by_id)
    contract = _staffing_contract(materials, extract_contract_fields)
    if not contract["type"]:
        return [BsFinding(
            task_id=task.id, item_id=ITEM_STAFF, kind="personnel_adequacy",
            status=STATUS_UNDECIDABLE, source=SOURCE_STAFF,
            message="未能从分包合同识别分包类型（专业分包/劳务分包），暂不能套用人员配置表，需人工核对",
            evidence_json=refs or None)]

    gaps = [(material, pages) for material, pages in
            ((material, material_reading_gap(material)) for material in materials) if pages is not None]
    if not roster and gaps:
        return [BsFinding(
            task_id=task.id, material_id=gaps[0][0].id, item_id=ITEM_STAFF,
            kind="personnel_adequacy", status=STATUS_UNDECIDABLE, source=SOURCE_STAFF,
            message="主要管理人员名单所在材料存在未确认页，暂不能认定岗位是否齐全，需人工核对原件")]

    amount = contract["amount_wan"]
    context = _staffing_scale_context(contract)
    params, candidates, conflicts = context["params"], context["candidates"], context["conflicts"]
    category = context["category"]
    refs.extend(contract["scope_records"] + contract["amount_refs"])
    result = evaluate_staffing(
        subcontract_type=contract["type"],
        roster=[{"name": fact.get("name"), "post": fact.get("post")} for fact in roster],
        category=category,
        params=params,
        contract_amount_wan=amount,
        scale_fields=context["scale_fields"],
    )

    errors = context["errors"] if contract["type"] == "professional" else []
    if errors:
        result = {**result, "verdict": "不符合" if result["verdict"] == "不符合" else "待核实",
                  "reason": "；".join(errors)}

    verdict = result["verdict"]
    if verdict == "符合":
        status = STATUS_MISS
        message = "岗位配置符合标准表要求（已识别 %d 名管理人员）" % len(
            {(fact.get("name") or "") for fact in roster})
        if result.get("advisories"):
            message += "；另有 %d 项非关键岗位配置不足，仅作提示" % len(result["advisories"])
    elif verdict in ("缺项", "不符合"):
        status = STATUS_NOT_INCLUDED if verdict == "缺项" else STATUS_HIT
        lines = [str(item.get("message") or "").strip()
                 for item in result.get("details") or []
                 if item.get("verdict") in ("缺项", "不符合") and item.get("message")]
        message = "；".join(lines[:6]) or (result.get("reason") or "岗位配置不符合标准表要求")
    else:
        status = STATUS_UNDECIDABLE
        message = scale_reason_text(result.get("reason") or "岗位配置所需信息不足，无法判定")
    if verdict in ("缺项", "不符合") and result.get("reason"):
        message += "；" + scale_reason_text(result["reason"])

    scale_info = result.get("scale_info") or {}
    scale = None if errors else result.get("scale")
    if scale:
        message = "工程规模按分包内容核为%s；%s" % ({"small": "小型", "medium": "中型", "large": "大型"}[scale], message)
    for ref in refs:
        if ref.get("evidence_kind") in {"subcontract_scope", "staffing_contract_amount"}:
            ref.update(staffing_scale=scale, staffing_preliminary_scale=scale_info.get("preliminary_scale"),
                       staffing_scale_complete=bool(scale_info.get("complete")) and not errors,
                       staffing_scale_required_fields=scale_info.get("required_fields", []),
                       staffing_scale_parameters=params, staffing_scale_candidates=candidates,
                       staffing_scale_conflicts=conflicts)
    source = SOURCE_STAFF
    if scale_info.get("source_pages"):
        source += "；规模依据：DG/TJ附录A，PDF物理第%s页" % "、".join(map(str, scale_info["source_pages"]))

    anchor = roster[0] if roster else None
    return [BsFinding(
        task_id=task.id, material_id=anchor.get("material_id") if anchor else None,
        item_id=ITEM_STAFF, kind="personnel_adequacy", status=status,
        message=bounded_finding_message(message), source=source,
        quote=anchor.get("quote") if anchor else None,
        page=anchor.get("page") if anchor else None,
        evidence_json=refs or None)]


ITEM_SPECIAL = "A2-08"
SOURCE_SPECIAL = ("依据：《沪交行规〔2026〕8号》第十条（二）、第十二条及报审附件人员要求；"
                  "交安25号令第十六条、JTG F90-2015第5.5.1/5.6.2条；证书类型按已确认特种作业目录核对")


def run_special_operation_coverage_check(task, materials):
    from .extract import extract_contract_fields
    from .special_operation_coverage import evaluate_coverage

    scope_rows = []
    for material in materials:
        if material.role not in {"sub", "labor"} or material.parse_state != "done":
            continue
        field = ((extract_contract_fields(material) or {}).get("fields") or {}).get("scope") or {}
        if field.get("status") == STATUS_HIT and field.get("value"):
            scope_rows.append({"material_id": material.id, "page": field.get("page"),
                               "quote": field.get("quote") or str(field["value"]),
                               "scope": str(field["value"]), "evidence_kind": "subcontract_scope"})
    by_id = {material.id: material for material in materials}
    roster = [fact for fact in extract_special_trade_roster(materials)
              if fact.get("page") and material_page_readable(by_id[fact["material_id"]], fact["page"])]
    # 任一相关合同/报审材料未读完，缺名单不能被当作已查无该人员。
    complete = all(material_reading_gap(material) is None for material in materials
                   if material.role in {"sub", "labor", "packet"})
    result = evaluate_coverage("；".join(row["scope"] for row in scope_rows), roster, reading_complete=complete)
    refs = scope_rows + _person_evidence_refs(roster, by_id)
    messages = [detail["message"] for detail in result["details"]] + result["gaps"]
    messages.append(result["quantity_notice"])
    anchor = (scope_rows or roster or [None])[0]
    return [BsFinding(task_id=task.id, item_id=ITEM_SPECIAL, kind="personnel_adequacy",
                      status=result["status"], source=SOURCE_SPECIAL,
                      material_id=anchor["material_id"] if anchor else None,
                      page=anchor["page"] if anchor else None, quote=anchor["quote"] if anchor else None,
                      message=bounded_finding_message("；".join(messages)), evidence_json=refs or None)]


def run_equipment_identity_check(task, materials):
    from . import equipment_identity as equipment
    records = equipment.extract_equipment_records(materials)
    inventories = [row for row in records if row["evidence_kind"] == "equipment_inventory"]
    proofs = [row for row in records if row["evidence_kind"] != "equipment_inventory"]
    labels = {"equipment_inspection": "检验报告", "equipment_registration": "使用登记",
              "equipment_ownership": "权属证明", "equipment_lease": "租赁合同"}
    findings = []
    for item in inventories:
        matched = equipment.matching_proofs(item, proofs, inventories=inventories)
        details = []
        for row in matched:
            fields = [row.get("model"), row.get("owner"), row.get("user"), row.get("inspection_result"),
                      "下次检验" + row["next_inspection"] if row.get("next_inspection") else None,
                      row.get("period")]
            details.append(labels[row["evidence_kind"]] + "：" + "、".join(value for value in fields if value))
        identity = "、".join(label + str(item[field]) for field, label in (
            ("internal_id", "设备编号"), ("factory_id", "出厂编号"), ("registration_id", "登记号")) if item.get(field))
        message = "设备辅助核对：%s（%s）；%s；请逐台核对编号、设备类型/适用条件、检验期间和权属/租赁，字段对应不证明原件真实或满足施工需要" % (
            item["name"], identity or "无唯一编号", "；".join(details) or "尚无同编号体系可唯一对应的独立证明，清单声明不能替代检验报告")
        findings.append(BsFinding(task_id=task.id, item_id=equipment.ITEM_ID, kind="personnel_adequacy",
            status=STATUS_UNDECIDABLE, finding_semantics="manual_prompt", source=equipment.SOURCE,
            message=bounded_finding_message(message), material_id=item["material_id"], page=item["page"],
            quote=item["quote"], location_mode="page", evidence_json=[item, *matched]))
    return findings


def run_safety_permit_scope_check(task, materials, *, expected_company=None):
    from . import safety_permit_scope as permit

    names = subcontractor_names(materials)
    expected = norm_company(expected_company) if expected_company else None
    company = (next(iter(names)) if len(names) == 1 else expected if not names else None)
    if expected and names and names != {expected}:
        company = None
    records = permit.extract_safety_permit_records(materials, _certificate_text, _CERT_TITLE_RE)
    scopes = permit.extract_contract_scope_records(materials)
    status, message = permit.evaluate_scope(records, company=company, normalize_company=norm_company,
                                            reading_complete=permit.reading_complete(materials),
                                            contract_scopes=scopes, baseline_date=getattr(task, "baseline_date", None))
    refs = records + scopes
    anchor = refs[0] if refs else {}
    return [BsFinding(task_id=task.id, item_id=permit.ITEM_ID, kind="cert_validity", status=status,
                      source=permit.SOURCE, message=bounded_finding_message(message),
                      material_id=anchor.get("material_id"), page=anchor.get("page"),
                      quote=anchor.get("quote"), evidence_json=refs or None)]
