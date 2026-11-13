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
import re

from ..startup_conditions_check.validity import (
    extract_validity_records, judge_expiry, parse_date_text,
)
from ..startup_conditions_check.date_reliability import build_date_fact
from .models import STATUS_HIT, STATUS_MISS, STATUS_UNDECIDABLE, BsFinding
from .personnel_evidence import (
    extract_management_roster, extract_person_relation_evidence,
    extract_roster_names, extract_social_names, social_evidence_materials,
)
from .parsing import material_page_readable, material_reading_gap
from .rules import load_config, load_extract_matchers, load_qualification_matchers

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
    r"(?m)^\s*(?:建筑业企业资质证书|资质证书|安全生产许可证|营业执照|"
    r"注册建造师证书|特种作业操作证|安全生产考核合格证(?:书)?)"
    r"(?:[（(](?:正本|副本)[）)])?\s*$")
_CERT_DECLARATION_RE = re.compile(
    r"资质情况|资质信息|申报信息|声明|合同价款|填写示例|示例填写|空白模板|空白样表"
    r"|(?:应当|应|须|需).{0,8}(?:提供|提交|出示)")
_CERT_INDIRECT_SOURCE_RE = re.compile(r"台账|情况说明|模板|样表|填写示例|信息汇总|登记表|信息表")
_CERT_PROOF_FIELDS = (
    re.compile(r"(?:证书编号|许可证编号|统一社会信用代码)[ \t]*[:：][ \t]*([^\n]+)"),
    re.compile(r"(?:注册单位|注册企业|聘用单位|受聘单位|单位名称)[ \t]*[:：][ \t]*([^\n]+)"),
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
    r"(?:注册单位|聘用单位|聘用企业|注册企业|工作单位|受聘单位|单位名称)[:：]?"
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
    """显式分包名称及原页出处；识别冲突保留双侧，不模糊合并企业。"""
    party_re = re.compile(load_extract_matchers()["party_name_regex"])
    return [{"company": norm_company(match.group(2)), "material_id": material.id,
             "page": page.get("page"), "quote": match.group()[:240], "location_mode": "page"}
            for material in materials if material.parse_state == "done"
            for page in material.pages_json or []
            if isinstance(page, dict) and type(page.get("page")) is int and page["page"] > 0
            and material_page_readable(material, page["page"])
            for match in party_re.finditer(page.get("text") or "")
            if match.group(1) in {"乙方", "分包人", "分包单位"} and norm_company(match.group(2))]


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

def norm_company(name: str) -> str:
    """公司名归一：先整段剥括号备注，再去空白与残留括号。

    （A-15 从 `_norm_company` 提为公开名 norm_company——供同模块 extract.py 跨模块复用，
    遵守「不引用他模块 `_` 私有符号」纪律；本文件内既有调用点同批改用公开名。）
    """
    s = re.sub(r"[（(][^）)]*[)）]", "", name or "")
    return re.sub(r"[\s（）()]", "", s)




def run_person_consistency_check(task, materials: list, *, expected_company=None) -> list:
    """A2-01：分别呈现名单覆盖事实与尚未证明的劳动/本单位社保关系。

    规则只读取摄取结果；不重开PDF、不发OCR。姓名相同不是同人证明，
    缺名也不是未参保证明；月份适用口径未确认时不自动给出关系成立结论。
    """
    by_id = {material.id: material for material in materials}

    def readable_fact(fact):
        material = by_id.get(fact.get("material_id"))
        page = fact.get("page")
        return material is not None and type(page) is int and page > 0 and material_page_readable(material, page)

    roster = [fact for fact in extract_management_roster(materials) if readable_fact(fact)]
    relations = [fact for fact in extract_person_relation_evidence(materials) if readable_fact(fact)]
    social = [fact for fact in relations if fact["evidence_kind"] == "social_record"]
    labor = [fact for fact in relations if fact["evidence_kind"] == "labor_contract"]
    candidates = social_evidence_materials(materials)
    facts = roster + relations
    refs = [dict(fact, location_mode="page") for fact in facts]
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
    missing = [name for name in names if name not in {fact["name"] for fact in social}]
    if social and missing:
        gaps.append("现有可读社保未见" + "、".join(missing[:5]) + "，需补充材料或人工核实")
    elif names and social:
        gaps.append("姓名层面已覆盖当前已提取的 %d 名主要管理人员" % len(names))
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
    if social:
        if any(len(fact.get("employer_candidates", [])) > 1 for fact in social):
            gaps.append("同一证明参保单位冲突，需人工核对")
        if any(len(fact.get("period_candidates", [])) > 1 for fact in social):
            gaps.append("同一证明缴费期间冲突，需人工核对")
        if any(not fact.get("employer") for fact in social):
            gaps.append("参保单位缺失，不能证明本单位社保关系")
        if expected and any(fact.get("employer") and norm_company(fact["employer"]) != expected
                            for fact in social):
            gaps.append("参保单位冲突，不能证明本单位社保关系")
        if any(not fact.get("period") for fact in social):
            gaps.append("社保缴费期间不明")
        else:
            gaps.append("已保留缴费期间，适用月份口径尚待确认")
    labor_names = {fact["name"] for fact in labor}
    missing_labor = [name for name in names if name not in labor_names]
    if missing_labor or not labor:
        gaps.append("劳动合同关系未证明" + ("（缺" + "、".join(missing_labor[:5]) + "）" if missing_labor else ""))
    if any(not fact.get("employer") for fact in labor):
        gaps.append("劳动合同用人单位缺失")
    if expected and any(fact.get("employer") and norm_company(fact["employer"]) != expected
                        for fact in labor):
        gaps.append("劳动合同用人单位冲突")
    if any(not fact.get("period") for fact in labor):
        gaps.append("劳动合同期间不明")
    if names and relations:
        gaps.append("同名尚不能证明同一人，需人工核对身份及关系")
    anchor = (social or roster or relations or [None])[0]
    return [BsFinding(
        task_id=task.id, material_id=anchor["material_id"] if anchor else (
            candidates[0].id if candidates else None),
        item_id=ITEM_UNIT, kind="person_consistency", status=STATUS_UNDECIDABLE,
        message="；".join(gaps), source=SOURCE_P,
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


def run_qualification_match_check(task, materials: list) -> list:
    """A2-04 资质匹配（词表保守口径），返回落库前 BsFinding 列表（不 commit）。"""
    matchers = load_qualification_matchers()
    done = [m for m in materials if m.parse_state == "done"]
    sub = [m for m in done if m.role in ("sub", "labor")]

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
    return findings
