"""A3-01 报审资料完整性核查（确定性对表，W1 文本层口径）。

三态语义（全站统一词）：命中=缺陷成立 / 未命中=齐全 / 不可判定=证据不足。
- document 类：报审包文件名关键词 或 包内文本关键词 双通道；
- 含 `required_all` 的条目：按 **AND 子件**逐件判 present/absent/unknown，再做条目级
  三态聚合（缺件必须逐条枚举子件名——OR 语义会把「五证只传一证」静默判齐全，见 AGENTS 纪律 4）；
- 类型专属条目（专业/劳务）：只对同类型报审包评估，类型识别不出时一律「不可判定」，
  绝不默认按专业判；
- content 类：指定材料的文本层里找模式（签字栏标识等）；
- 扫描不可读、解析失败或部分页未读：已有实证仍可核对；未见目标时判「不可判定」
  并说明文件/未读页与人工核对方法，不能把未读内容当作「缺失」。
"""
import re
from html import unescape

from . import pinning
from .attachment_source import is_attachment_source
from .material_versions import subject_assessment_issue
from .personnel_evidence import (contract_roster_evidence, extract_management_roster,
                                 extract_person_relation_evidence, extract_special_trade_roster)
from .models import STATUS_HIT, STATUS_MISS, STATUS_UNDECIDABLE, BsFinding, BsMaterial
from .parsing import describe_reading_gap, material_page_readable, material_reading_gap
from .rules import load_report_checklist


def _packet_materials(materials: list) -> list:
    # 失败件仍是已提交的证据候选，不能过滤成“没有提交”。
    return [m for m in materials if is_attachment_source(m) and m.parse_state in ("done", "failed")]


def _material_text(material) -> str:
    return "\n".join(p.get("text", "") for p in (material.pages_json or []))


def _reading_limit(materials: list) -> str:
    """限制随finding进入页面和导出，不能仅留在解析日志。"""
    limits = []
    for material in materials:
        unread = material_reading_gap(material)
        if unread is None:
            continue
        pages = describe_reading_gap(material, unread)
        limits.append("「%s」%s" % (material.file_name, pages))
    if not limits:
        return ""
    return "；".join(limits) + "，涉及内容暂时无法确认，需人工翻看原件或补充清晰材料"


_WS_RE = re.compile(r"\s+")


def _norm_pages(material) -> list:
    """[(page, 去空白文本)]：中文 PDF 换行会把关键词切开（如「安全生产许可⏎证」），
    匹配与取证统一在去空白文本上做。"""
    return [(p.get("page"), _WS_RE.sub("", p.get("text", "")))
            for p in (material.pages_json or [])]


def _find_evidence(material, pattern: str):
    """返回 (quote, page) 或 None（在去空白文本上匹配）。"""
    for page_no, text in _norm_pages(material):
        if not material_page_readable(material, page_no):
            continue
        m = re.search(pattern, text)
        if m:
            start = max(0, m.start() - 20)
            return text[start:m.end() + 20], page_no
    return None


# ==================== 报审包类型识别（检查期推导，不入库） ====================
# 权威文本 P3「专业分包报审表」/ P4「劳务分包报审表」为类型识别锚点（纯函数可判，
# 零迁移零 UI）；识别不出（两版表头都未命中或同时命中）→ None，绝不猜测。
_PRO_HEADER = "专业分包报审表"
_LABOR_HEADER = "劳务分包报审表"
_PACKET_TYPE_LABELS = {"professional": "专业", "labor": "劳务"}


def _packet_type(material) -> str:
    """在去空白归一化文本层上识别报审包类型。

    仅命中「专业分包报审表」→ "professional"；仅命中「劳务分包报审表」→ "labor"；
    两者都未命中或同时命中（含糊）→ None（识别不出，交由上层判不可判定）。
    """
    text = "".join(text for page, text in _norm_pages(material)
                   if material_page_readable(material, page))
    has_pro = _PRO_HEADER in text
    has_labor = _LABOR_HEADER in text
    if has_pro and not has_labor:
        return "professional"
    if has_labor and not has_pro:
        return "labor"
    return None


# ==================== document 条目：双通道判在场 ====================

def _check_document(item: dict, materials: list) -> tuple:
    """document 条目：返回 (status, material_id, quote, page)。

    双通道：文件名关键词 → 包内文本关键词；未找到且相关候选未读完整时
    判「不可判定」——已读部分没看到不等于未读页也没有。
    """
    if item.get("id") in {"RC-A02", "RC-A03", "RC-A04"}:
        weak_mention = None
        fields = ("统一社会信用代码", "登记机关", "发证机关", "证书编号", "许可证编号", "备案编号")
        for material in materials:
            if any(k in (material.file_name or "") for k in item["keywords"]):
                weak_mention = material
            for page, text in _norm_pages(material):
                keyword = next((k for k in item["keywords"] if k in text), None)
                if not keyword:
                    continue
                weak_mention = material
                if not material_page_readable(material, page):
                    continue
                # 台账、情况说明和模板中也会列出完整证照字段，不能据此
                # 将汇总文字提升为证照原件；混合页难区分时保守留给人工。
                placeholders = re.findall(r"(?:统一社会信用代码|登记机关|发证机关|证书编号|许可证编号|备案编号)[:：]?[＿_]{3,}", text)
                if ("分包报审表" in text or re.search(r"台账|情况说明|模板|样表|填写示例|企业安全生产许可证信息", text[:100])
                        or len(placeholders) >= 2
                        or sum(field in text for field in fields) < 2):
                    continue
                pos = text.index(keyword)
                return STATUS_MISS, material.id, text[max(0, pos-20):pos+len(keyword)+20], page
        if weak_mention is not None:
            return STATUS_UNDECIDABLE, weak_mention.id, None, None
        incomplete = next((m for m in materials if material_reading_gap(m) is not None), None)
        return ((STATUS_UNDECIDABLE, incomplete.id, None, None) if incomplete
                else (STATUS_HIT, None, None, None))
    name_hit = next(
        (m for m in materials if material_reading_gap(m) is None
         and any(k in (m.file_name or "") for k in item["keywords"])), None)
    if name_hit:
        return STATUS_MISS, name_hit.id, None, None
    text_mats = [m for m in materials if _material_text(m).strip()]
    for m in text_mats:
        for kw in item["keywords"]:
            for _page, text in _norm_pages(m):
                if kw in text and material_page_readable(m, _page):
                    pos = text.index(kw)
                    return STATUS_MISS, m.id, text[max(0, pos - 20):pos + len(kw) + 20], _page
    incomplete = next((m for m in materials if material_reading_gap(m) is not None), None)
    if incomplete:
        return STATUS_UNDECIDABLE, incomplete.id, None, None
    return STATUS_HIT, None, None, None


# ==================== required_all 条目：AND 子件 + 三态聚合 ====================

def _subitem_state(sub: dict, materials: list) -> str:
    """单个子件状态："present" / "absent" / "unknown"。

    - present：文件名或（去空白归一化）文本命中该子件任一 keyword；
    - unknown：未找到子件且相关候选存在未读内容→ 无法确认在场与否；
    - absent：候选均无解析不足且未命中任何 keyword（含候选材料为空）。
    """
    for m in materials:
        if material_reading_gap(m) is None and any(k in (m.file_name or "") for k in sub["keywords"]):
            return "present"
    text_mats = [m for m in materials if _material_text(m).strip()]
    for m in text_mats:
        for kw in sub["keywords"]:
            for _page, text in _norm_pages(m):
                if kw in text and material_page_readable(m, _page):
                    return "present"
    if any(material_reading_gap(m) is not None for m in materials):
        return "unknown"
    return "absent"


def _applicable_subitems(item: dict, materials: list, packet_types: dict, subject_types=None) -> list:
    """返回本条目在给定评估集下「适用」的 (子件, 候选材料) 列表。

    子件可自带 packet_types（缺省＝随父条目）。带类型的子件**只对同类型报审包评估**：
    无本类型包（无论因另一类型包在场、无任何包、或包类型识别不出）→ 该子件不适用
    （跳过，绝不判缺——不得把「未识别」默认当专业包）。这样 RC-A10 的专业 3 角色
    只对专业包评、劳务 2 角色只对劳务包评，不出对侧角色。
    授权附件核查另可接收已经确认的主体类型；它确定岗位要求，不伪造报审包在场。
    """
    out = []
    for sub in item.get("required_all") or []:
        sub_ptypes = sub.get("packet_types")
        if sub_ptypes:
            cands = [m for m in materials if packet_types.get(m.id) in sub_ptypes]
            if subject_types:
                if not (set(sub_ptypes) & subject_types):
                    continue
            elif not cands:
                continue
        else:
            cands = materials
        out.append((sub, cands))
    return out


def _check_document_required_all(item: dict, materials: list, packet_types: dict) -> tuple:
    """required_all 条目：返回 (status, material_id, quote, page, missing_labels, unknown_labels)。

    条目级三态聚合（逐格实现，不得退化）：
    - absent 非空（任意 unknown）→ HIT（有确证缺失即有缺陷，必须枚举缺项）；
    - absent 空、unknown 非空 → UNDECIDABLE；
    - absent 空、unknown 空 → MISS（n/n 齐全）；
    - 全部子件均不适用（有报审包却都识别不出类型）→ UNDECIDABLE（唯一出口，
      绝不默认按专业判缺）。
    """
    applicables = _applicable_subitems(item, materials, packet_types)
    missing, unknown = [], []
    for sub, cands in applicables:
        state = _subitem_state(sub, cands)
        if state == "absent":
            missing.append(sub["label"])
        elif state == "unknown":
            unknown.append(sub["label"])
    if missing:
        return STATUS_HIT, None, None, None, missing, unknown
    if not applicables:
        # 有报审包但一个都识别不出类型 → 不得默认按专业判
        return STATUS_UNDECIDABLE, None, None, None, missing, unknown
    if unknown:
        return STATUS_UNDECIDABLE, None, None, None, missing, unknown
    return STATUS_MISS, None, None, None, missing, unknown


def _required_all_message(item, status, missing, unknown, materials, packet_types, subject_types=None) -> str:
    """required_all 条目的对外文案（过报告禁词表：无「文本层/三态/降级/内核/OCR」等实现词）。"""
    label = item["label"]
    total = len(_applicable_subitems(item, materials, packet_types, subject_types))
    if status == STATUS_HIT:
        msg = "缺「%s」下的：%s（缺 %d/%d 项）" % (label, "、".join(missing), len(missing), total)
        if unknown:
            msg += "；另有 %d 项因资料未读完整无法判断；%s" % (
                len(unknown), _reading_limit(materials))
        return msg
    if status == STATUS_UNDECIDABLE:
        if unknown:
            return ("「%s」暂时无法判断：%s 的材料内容未读完整（可能为扫描件）；%s"
                    % (label, "、".join(unknown), _reading_limit(materials)))
        return "「%s」不可判定：未识别专业/劳务报审表类型（缺：未识别专业/劳务报审表类型）" % label
    return "「%s」已随报审资料上传（%d/%d 项齐全）" % (label, total, total)


# RC-A10只补读实际岗位授权附件，不将整册合同放进其他报审材料候选。
_AUTH_TITLE_RE = re.compile(r"^.{0,12}授权(?:委托)?书[:：]?$")
_AUTH_NONACTUAL_RE = re.compile(r"样例|模板|填写示范|填写示例|填表说明|(?:应当|应|须|需).{0,8}(?:提交|提供|另附|出具)")
_AUTH_DELEGATE_RE = re.compile(
    r"(?:委托代理人|受托人|被授权人|授权代表)\s*[:：]\s*"
    r"([\u4e00-\u9fa5]{2,4})(?=[\s|;；，,]|$)")
_AUTH_POST_RE = re.compile(r"(?:职务|岗位)\s*[:：]\s*([^|\n;；]+)")
_AUTH_OTHER_PARTY_FIELD_RE = re.compile(r"(?:^|\n|\|)\s*(?:法定代表人|委托人|授权人)\s*[:：]")
_AUTH_ACTION_RE = re.compile(r"(?:现|兹)(?:委托|授权)|(?:授权事项|委托事项|授权范围)\s*[:：]\s*([^|\n;；]+)")
_AUTH_INLINE_RE = re.compile(
    r"(?:授权|任命)([\u4e00-\u9fa5]{2,4}?)(?:[（(][^）)]{1,64}[）)])?"
    r"(?:为|担任)(?:我公司|本公司)?(项目经理(?:/负责人)?|项目总工|总工|项目负责人|专职安全管理人员|安全员)"
)
_AUTH_EMPTY_VALUES = frozenset({"姓名", "受托人", "被授权人", "身份证", "待填", "待填写", "未填", "未填写"})


def _authorization_action(line: str) -> bool:
    match = _AUTH_ACTION_RE.search(line)
    if not match:
        return False
    value = (match.group(1) or "").strip()
    return (match.group(1) is None or bool(value and value not in _AUTH_EMPTY_VALUES
                                         and not re.fullmatch(r"[_—\-/.…\s]+", value)))


def _authorization_evidence(item: dict, materials: list) -> tuple[list, bool]:
    """仅确认实际授权文件及明示受权岗位，不推断身份一致、任职或真实性。"""
    refs, uncertain = [], False
    # 有效报审附件优先；同一实填授权的权限表不再生成一份授权证据。
    ordered = sorted(materials, key=lambda material: material.role != "packet")
    for material in ordered:
        if material.role not in {"packet", "sub", "labor"} or material.parse_state != "done":
            continue
        ptype = (_packet_type(material) if material.role == "packet" else
                 {"sub": "professional", "labor": "labor"}.get(material.role))
        for page in material.pages_json or []:
            raw = re.sub(r"</(?:tr|p|div|h[1-6])\s*>|<br\s*/?>", "\n", page.get("text") or "", flags=re.I)
            plain = unescape(re.sub(r"<[^>]*>", " ", raw))
            plain = re.sub(r"\$?\\text\s*\{\s*([^{}]+?)\s*\}\$?", r"\1", plain)
            lines = [re.sub(r"[ \t]+", " ", line).strip(" |#\t") for line in plain.splitlines()]
            heading = next((i for i, line in enumerate(lines) if _AUTH_TITLE_RE.fullmatch(line)), None)
            if heading is None:
                continue
            block = lines[heading:]
            text = "\n".join(block)
            if _AUTH_NONACTUAL_RE.search(text):
                continue
            compact = _WS_RE.sub("", text)
            inline = [match for match in _AUTH_INLINE_RE.finditer(compact)
                      if match.group(1) not in _AUTH_EMPTY_VALUES
                      and match.group(1) not in {"单位", "公司", "人员", "相关人员"}
                      and not re.search(r"不得|禁止|不能|无权|未曾|不再", compact[max(0, match.start()-5):match.start()])]
            if inline:
                keys, role_labels, proof = set(), [], [block[0]]
                for match in inline:
                    role = match.group(2)
                    aliases = {"项目经理/负责人": "项目经理", "专职安全管理人员": "安全员"}
                    role = aliases.get(role, role)
                    for sub in item["required_all"]:
                        if (not ptype or ptype in sub.get("packet_types", [])) and role in sub["keywords"]:
                            keys.add(sub["key"])
                            if sub["label"] not in role_labels:
                                role_labels.append(sub["label"])
                            proof.append("%s：%s" % (sub["label"], match.group(1)))
                if keys:
                    refs.append({"material_id": material.id, "page": page.get("page"),
                                 "quote": "；".join(proof)[:240], "location_mode": "page",
                                 "evidence_kind": "authorization_document", "authorization_roles": role_labels,
                                 "subitem_keys": sorted(keys)})
            delegates = [(i, _AUTH_DELEGATE_RE.search(line)) for i, line in enumerate(block)]
            delegates = [(i, match) for i, match in delegates
                         if match and match.group(1) not in _AUTH_EMPTY_VALUES]
            if not delegates or not any(_authorization_action(line) for line in block):
                uncertain = True  # 标题/空表不是岗位授权已提供的证据。
                continue
            keys, role_labels, proof = set(), [], [block[0]]
            for offset, (index, delegate) in enumerate(delegates):
                end = delegates[offset + 1][0] if offset + 1 < len(delegates) else len(block)
                record = "\n".join(block[index:end])
                other_party = _AUTH_OTHER_PARTY_FIELD_RE.search(record)
                if other_party:
                    record = record[:other_party.start()]
                post = _AUTH_POST_RE.search(record)
                normalized = _WS_RE.sub("", post.group(1)) if post else ""
                matched = [sub for sub in item["required_all"]
                           if (not ptype or ptype in sub.get("packet_types", []))
                           and normalized in sub["keywords"]]
                if not matched:
                    uncertain = True
                proof.append(block[index])
                if post:
                    proof.append(post.group())
                for sub in matched:
                    keys.add(sub["key"])
                    if sub["label"] not in role_labels:
                        role_labels.append(sub["label"])
            action = next((line for line in block if _authorization_action(line)), None)
            if action:
                proof.append(action)
            refs.append({"material_id": material.id, "page": page.get("page"),
                         "quote": re.sub(r"\s+", " ", "\n".join(proof)).strip()[:240],
                         "location_mode": "page", "evidence_kind": "authorization_document",
                         "authorization_roles": role_labels, "subitem_keys": sorted(keys)})
    return refs, uncertain


def _check_authorization(item, eval_packets, packet_types, materials, *, subject_types=None):
    """RC-A10逐岗聚合，实物优先保留；未知岗位或未读不能判全部缺失。"""
    refs, uncertain = _authorization_evidence(item, materials)
    applicables = _applicable_subitems(item, eval_packets, packet_types, subject_types)
    known = {key for ref in refs for key in ref["subitem_keys"]}
    missing, unknown = [], []
    relevant = [material for material in materials if material.role in {"packet", "sub", "labor"}]
    incomplete = any(material_reading_gap(material) is not None for material in relevant)
    for sub, _candidates in applicables:
        if sub["key"] in known:
            continue
        (unknown if incomplete or uncertain else missing).append(sub["label"])
    status = (STATUS_HIT if missing else STATUS_UNDECIDABLE if unknown or not applicables else STATUS_MISS)
    message = _required_all_message(item, status, missing, unknown, eval_packets, packet_types, subject_types)
    if unknown:
        message = ("「%s」暂时无法判断：%s 的岗位授权覆盖待核，需人工核对原件"
                   % (item["label"], "、".join(unknown)))
    if status == STATUS_UNDECIDABLE:
        limit = _reading_limit(relevant)
        if limit:
            message += "；" + limit
    labels = list(dict.fromkeys(label for ref in refs for label in ref["authorization_roles"]))
    if refs:
        message += "；已见%s授权材料（仅确认内容存在，身份、岗位一致性及真实性仍需核对）" % (
            "、".join(labels) if labels else "实际文件，受权岗位未明确的")
    anchor = next((ref for ref in refs if ref["authorization_roles"]), refs[0] if refs else None)
    return status, anchor, message, refs


# ==================== content 条目 ====================

def _check_content(item: dict, materials: list) -> tuple:
    """content 条目：scoped 材料（角色+名称关键词，文件名或文本双通道）文本层找模式。"""
    def readable_text(material):
        return "".join(text for page, text in _norm_pages(material)
                       if material_page_readable(material, page))

    scoped = [m for m in materials
              if all(k in (m.file_name or "") or k in readable_text(m)
                     for k in item.get("scope_name_keywords", []))]
    if not scoped:
        # 对应材料本身缺失：缺材料由 document 条目记命中，此处无从判定
        limit = _reading_limit(materials)
        if limit:
            incomplete = next(m for m in materials if material_reading_gap(m) is not None)
            return STATUS_UNDECIDABLE, incomplete.id, None, None, limit
        return STATUS_UNDECIDABLE, None, None, None, "报审表中未见对应材料，无法判定"
    no_text = [m for m in scoped if not readable_text(m).strip()]
    if len(no_text) == len(scoped):
        return (STATUS_UNDECIDABLE, scoped[0].id, None, None,
                "该材料里没有文字内容（疑似扫描件）；" + _reading_limit(scoped))
    for m in scoped:
        for pat in item["patterns"]:
            ev = _find_evidence(m, pat)
            if ev:
                return STATUS_MISS, m.id, ev[0], ev[1], None
    incomplete = next((m for m in scoped if material_reading_gap(m) is not None), None)
    if incomplete:
        return STATUS_UNDECIDABLE, incomplete.id, None, None, _reading_limit(scoped)
    return STATUS_HIT, scoped[0].id, None, None, None


# ==================== 编排 ====================

def _item_packet_types(item: dict) -> set:
    """条目适用的报审包类型集（缺省＝专业+劳务，向后兼容）。"""
    return set(item.get("packet_types") or ("professional", "labor"))


def _select_eval_packets(item: dict, packet: list, packet_types: dict,
                         *, subject_types=None) -> tuple:
    """按 packet_types 选评估集；返回 (eval_packets, early_message)。

    - 共享条目（两类）→ 对全部报审包评估（任一包在场即在场，与原行为一致）；
    - 主体类型已确认时，主体边界就是权威类型：使用同类型或尚未识别
      类型的包；未上传包时把空集交给 document 检查判明确缺件；
    - 旧入口未提供主体类型时，才按报审包自身识别结果筛选；识别不出时
      early_message 非空（上层据此直接判不可判定，绝不默认按专业判）。
    """
    ptypes = _item_packet_types(item)
    if ptypes == {"professional", "labor"}:
        return packet, None
    t = next(iter(ptypes))
    if subject_types and t in set(subject_types):
        return [m for m in packet if packet_types.get(m.id) in {None, t}], None
    tlabel = _PACKET_TYPE_LABELS.get(t, t)
    typed = [m for m in packet if packet_types.get(m.id) == t]
    if typed:
        return typed, None
    recognized = [m for m in packet if packet_types.get(m.id) is not None]
    if recognized or not packet:
        # 有其他类型报审包在（或压根没有报审包）→ 确无本类型包
        return [], ("「%s」不可判定：未见%s分包报审包（缺：%s分包报审包）"
                    % (item["label"], tlabel, tlabel))
    return [], ("「%s」不可判定：未识别专业/劳务报审表类型（缺：未识别专业/劳务报审表类型）"
                % item["label"])


def _document_message(item: dict, status: str, materials: list) -> str:
    if status == STATUS_HIT:
        return "报审资料缺「%s」（报审包内未见名称或文本依据）" % item["label"]
    if status == STATUS_UNDECIDABLE:
        if item.get("id") in {"RC-A02", "RC-A03", "RC-A04"} and not _reading_limit(materials):
            return "「%s」暂时无法判断：已见相关名称，但未能定位足够明确的证照原件，请人工核对" % item["label"]
        return ("「%s」暂时无法判断：报审资料未读完整（可能为扫描件）；%s"
                % (item["label"], _reading_limit(materials)))
    return "「%s」已随报审资料上传" % item["label"]


def _personnel_page_clue(item: dict, materials: list):
    """定位岗位资料标题线索；只帮助翻页，不证明标题列出的证件存在。"""
    role = {"RC-A07": "企业负责人", "RC-P11": "项目经理", "RC-P12": "项目总工",
            "RC-P13": "安全员", "RC-L11": "安全员"}.get(item.get("id"))
    if not role:
        return None
    candidates = []
    for material in materials:
        for page in material.pages_json or []:
            text = page.get("text") or ""
            if (not isinstance(text, str) or not material_page_readable(material, page.get("page"))
                    or re.search(r"附件目录|报审表|填写示例|空白模板", text[:200])):
                continue
            for match in re.finditer(re.escape(role), text):
                snippet = text[match.start():match.start() + 120]
                extra = ("交安" if item.get("id") == "RC-A07" else
                         ("学历", "毕业证", "职称") if item.get("id") == "RC-P13" else
                         ("学历", "毕业证"))
                if isinstance(extra, str):
                    extra = (extra,)
                if "身份证" in snippet and any(word in snippet for word in extra):
                    candidates.append((material.id, snippet, page.get("page")))
                    break
    # 多个岗位标题可能代表多个同岗人员，不擅自选择其中一个。
    return candidates[0] if len(candidates) == 1 else None


def _personnel_degree_name_clue(item: dict, materials: list, anchor) -> dict | None:
    """定位岗位资料附近唯一毕业证页的姓名识别缺口，不推断其持有人。"""
    role = {"RC-P11": "项目经理", "RC-P12": "项目总工", "RC-P13": "安全员",
            "RC-L11": "安全员"}.get(item.get("id"))
    if not role or not anchor:
        return None
    material_id, _, anchor_page = anchor
    if type(anchor_page) is not int:
        return None
    material = next((m for m in materials if m.id == material_id), None)
    if material is None:
        return None
    roster = [row for row in extract_management_roster([material])
              if row.get("post") == role and material_page_readable(material, row.get("page"))]
    if len(roster) != 1:
        return None
    name = roster[0].get("name")
    if not name:
        return None
    # 岗位资料按章节排列时，不能跨越后一个岗位的资料标题借用其证书。
    # 即使边界页质量存疑，也宁可缩小候选范围，不把后面的人员材料归到本岗位。
    other_roles = {"企业负责人", "项目经理", "项目总工", "安全员"} - {role}
    boundaries = []
    for page in material.pages_json or []:
        page_no, text = page.get("page"), page.get("text") or ""
        if type(page_no) is not int or not anchor_page < page_no <= anchor_page + 3:
            continue
        head = text[:160]
        if (any(other in head for other in other_roles) and "身份证" in head
                and any(word in head for word in ("学历", "毕业证", "职称", "交安"))):
            boundaries.append(page_no)
    boundary = min(boundaries) if boundaries else anchor_page + 4
    candidates = []
    for page in material.pages_json or []:
        page_no, text = page.get("page"), page.get("text") or ""
        if (type(page_no) is not int or not anchor_page < page_no < boundary
                or not material_page_readable(material, page_no) or "毕业证书" not in text
                or re.search(r"附件目录|填写示例|空白模板", text[:200])):
            continue
        candidates.append((page_no, text))
    if len(candidates) != 1:
        return None
    page_no, text = candidates[0]
    # 姓名已出现或识别出其他明确姓名时，不把该页描述为姓名无法识别。
    if name in text or re.search(r"姓\s*名\s*[:：]\s*[\u4e00-\u9fff]{2,4}", text):
        return None
    return {"material_id": material_id, "page": page_no, "quote": "毕业证书",
            "location_mode": "page", "evidence_kind": "personnel_degree_clue"}


def _personnel_certificate_clues(item: dict, materials: list) -> list[str] | None:
    """仅返回同一唯一岗位人员的证件文字线索，不把线索提升为证件有效性证明。"""
    role, safety = {"RC-P12": ("项目总工", "B"), "RC-L11": ("安全员", "C")}.get(item.get("id"), (None, None))
    if not role:
        return None
    by_id = {material.id: material for material in materials}
    roster = [fact for fact in extract_management_roster(materials)
              if fact.get('material_id') in by_id and material_page_readable(by_id[fact['material_id']], fact.get('page'))]
    role_rows = [fact for fact in roster if fact.get('post') == role]
    names = {fact['name'] for fact in role_rows}
    if len(names) != 1 or len(role_rows) != 1:
        return None
    name = next(iter(names))
    owner = getattr(by_id[role_rows[0]['material_id']], 'subject_id', None)
    if any(fact.get('name') == name and fact is not role_rows[0]
           and getattr(by_id[fact['material_id']], 'subject_id', None) == owner
           and (owner is not None or fact['material_id'] == role_rows[0]['material_id'])
           for fact in roster):
        # 同主体出现同名登记时，不能仅凭姓名把证件归给目标岗位。
        return None
    other_names = {fact['name'] for fact in roster if fact['name'] != name}
    seen = set()
    for material in materials:
        if (not is_attachment_source(material)
                or getattr(material, 'parse_state', None) != 'done'
                or getattr(material, 'subject_id', None) != owner
                or (owner is None and material.id != role_rows[0]['material_id'])):
            continue
        for page in material.pages_json or []:
            text = page.get('text') or ''
            if (not material_page_readable(material, page.get('page')) or name not in text
                    or any(other in text for other in other_names)
                    or re.search(r"附件目录|报审表|填写示例|空白模板", text[:200])):
                continue
            holders = set(re.findall(r"姓\s*名\s*[:：]\s*([\u4e00-\u9fff]{2,4})(?=\s|[，,;；]|$)", text))
            if holders and holders != {name}:
                continue
            id_title = text.find('居民身份证')
            if id_title >= 0:
                nearby = text[max(0, id_title - 100):id_title + 100]
                number_count = len(re.findall(r"(?<!\d)\d{17}[0-9Xx](?!\d)", nearby))
                if number_count == 1:
                    seen.add('身份证')
            if '毕业证书' in text and re.search(r"证书编号|学校|校名", text):
                seen.add('学历证')
            if '任职资格' in text and '工程师' in text and re.search(r"评审委员会|证书编号|签发日期", text):
                seen.add('职称证')
            if ('考核' in text and '证书' in text
                    and re.search(r"交安\s*" + safety + r"\s*\d", text, flags=re.I)):
                seen.add('交安' + safety + '证')
    return [sub['label'] for sub in item.get('required_all') or [] if sub['label'] not in seen]


def _confirmed_personnel_certificate_refs(item: dict, materials: list) -> list[dict] | None:
    """仅在四种具名证件均能可靠落页时确认已见；不判断真伪、期限或资质。"""
    role, safety = {"RC-P12": ("项目总工", "B"), "RC-L11": ("安全员", "C")}.get(
        item.get("id"), (None, None))
    if role is None:
        return None
    by_id = {material.id: material for material in materials}
    roster = [row for row in extract_management_roster(materials)
              if row.get("material_id") in by_id
              and material_page_readable(by_id[row["material_id"]], row.get("page"))]
    role_rows = [row for row in roster if row.get("post") == role]
    names = {row["name"] for row in role_rows}
    if len(names) != 1 or len(role_rows) != 1:
        return None
    person = next(iter(names))
    roster_ref = role_rows[0]
    owner = getattr(by_id[roster_ref["material_id"]], "subject_id", None)
    if any(row.get("name") == person and row is not roster_ref
           and getattr(by_id[row["material_id"]], "subject_id", None) == owner
           and (owner is not None or row["material_id"] == roster_ref["material_id"])
           for row in roster):
        return None
    candidates = {key: [] for key in ("id_card", "degree", "title", "safety")}
    holder_re = re.compile(r"姓\s*名\s*[:：]\s*([\u4e00-\u9fff]{2,4})(?=\s|[，,;；]|$)")
    for material in materials:
        if (not is_attachment_source(material) or getattr(material, "parse_state", None) != "done"
                or getattr(material, "subject_id", None) != owner
                or (owner is None and material.id != roster_ref["material_id"])):
            continue
        for page in material.pages_json or []:
            if (not isinstance(page, dict) or type(page.get("page")) is not int or page["page"] < 1
                    or not isinstance(page.get("text"), str)):
                continue
            page_no, text = page["page"], page["text"]
            if not material_page_readable(material, page_no) or re.search(r"附件目录|填写示例|空白模板", text[:200]):
                continue
            holders = list(holder_re.finditer(text))
            if len(holders) != 1 or holders[0].group(1) != person:
                continue
            identities = list(re.finditer(
                r"(?:公民身份号码|身份证号码|身份证号)\s*[:：]?\s*(\d{17}[0-9Xx])(?!\d)", text))
            if len(identities) != 1:
                continue
            checks = {
                "id_card": ("居民身份证", bool(re.search(r"公民身份号码\s*[:：]?\s*\d{17}[0-9Xx](?!\d)", text))),
                "degree": ("毕业证书", bool(re.search(r"证书编号\s*[:：]?\s*[A-Za-z0-9]{5,}", text))),
                "title": ("任职资格", "工程师" in text and bool(re.search(r"证书编号\s*[:：]?\s*[A-Za-z0-9]{5,}", text))),
                "safety": ("安全生产考核合格证书", bool(re.search(r"交安\s*" + safety + r"\s*\d", text, re.I))),
            }
            matched = [(key, title) for key, (title, fields_present) in checks.items()
                       if fields_present and text.count(title) == 1]
            # 一页有不同证件且只有一个姓名字段时，不能借用这个姓名为其他证件背书。
            if len(matched) == 1:
                key, title = matched[0]
                candidates[key].append((material.id, page_no, holders[0].group(), title,
                                        identities[0].group(1).upper()))
    if any(len(rows) != 1 for rows in candidates.values()):
        return None
    if len({rows[0][4] for rows in candidates.values()}) != 1:
        return None
    roster_material = by_id[roster_ref["material_id"]]
    roster_page = next((page for page in roster_material.pages_json or []
                        if page.get("page") == roster_ref["page"]), {})
    roster_lines = [line.strip() for line in (roster_page.get("text") or "").splitlines()
                    if person in line and role in line]
    if len(roster_lines) != 1:
        return None
    refs = [{"material_id": roster_ref["material_id"], "page": roster_ref["page"],
             "quote": roster_lines[0], "evidence_kind": "personnel_roster"}]
    for key, rows in candidates.items():
        material_id, page_no, holder, title, identity = rows[0]
        refs.extend([{"material_id": material_id, "page": page_no, "quote": holder, "evidence_kind": key},
                     {"material_id": material_id, "page": page_no, "quote": title, "evidence_kind": key},
                     {"material_id": material_id, "page": page_no, "quote": identity, "evidence_kind": key}])
    pinned = []
    for ref in refs:
        probe = BsFinding(material_id=ref["material_id"], page=ref["page"], quote=ref["quote"], evidence_json=[ref])
        pinning.pin_findings([probe], materials)
        if probe.location_mode != "precise" or probe.page != ref["page"]:
            return None
        pinned.append({**probe.evidence_json[0], "location_mode": "precise"})
    return pinned




def _special_trade_certificate_clues(materials: list, roster: list, *, allow_unread_roster=False) -> list[dict]:
    """同一材料内按明确证书姓名定位线索；同名不证明身份或工种适用。"""
    names = [row.get("name") for row in roster]
    if not names or len(names) != len(set(names)):
        return []
    by_id = {material.id: material for material in materials}
    for row in roster:
        material = by_id.get(row.get("material_id"))
        if material is None:
            return []
        if not allow_unread_roster and not material_page_readable(material, row.get("page")):
            return []
    title_re = re.compile(r"建筑施工\s*特种作业\s*操作资格证书")
    name_re = re.compile(r"姓\s*名\s*[:：]\s*([\u4e00-\u9fff]{2,4})(?=\s|$)")
    refs = []
    for row in roster:
        candidates = []
        for material in materials:
            if material.id != row.get("material_id"):
                continue
            for page in material.pages_json or []:
                page_no = page.get("page")
                if not material_page_readable(material, page_no):
                    continue
                text = page.get("text") or ""
                titles = list(title_re.finditer(text))
                general_card = bool(re.search(r"(?m)^[ \t]*特种作业操作证[ \t]*$", text))
                if len(titles) == 1:
                    matches = list(name_re.finditer(text[titles[0].start():]))
                elif (not titles and general_card and re.search(r"操作项目|作业类别", text)
                      and not re.search(r"查询记录|查询结果|样例|模板", text[:200])):
                    matches = list(name_re.finditer(text)) + list(re.finditer(
                        r"(?m)^[ \t]*\|[ \t]*姓名[ \t]*\|[ \t]*([\u4e00-\u9fff]{2,4})[ \t]*\|", text))
                else:
                    continue
                if len(matches) != 1 or matches[0].group(1) != row["name"]:
                    continue
                candidates.append({"material_id": material.id, "page": page_no,
                                   "quote": matches[0].group(0),
                                   "location_mode": "page", "evidence_kind": "special_trade_certificate_clue"})
        if len(candidates) == 1:
            refs.append(candidates[0])
    return refs


def _special_trade_personnel_page_clues(materials: list, roster: list) -> list[dict]:
    """仅定位同名身份证和作业记录同页的材料，不推定证件持有人或有效期。"""
    names = [row.get("name") for row in roster]
    if not names or any(not isinstance(name, str) or not name for name in names) or len(names) != len(set(names)):
        return []
    refs = []
    for row in roster:
        candidates = []
        for material in materials:
            if material.id != row.get("material_id"):
                continue
            for page in material.pages_json or []:
                if not isinstance(page, dict) or not material_page_readable(material, page.get("page")):
                    continue
                text = page.get("text") or ""
                if (not isinstance(text, str) or row["name"] not in text or "居民身份证" not in text
                        or not (re.search(r"考试合格作业项目|作业项目复审记录", text)
                                or ("特种作业操作证" in text and "操作项目" in text and "证号" in text))
                        or not (re.search(r"姓名\s*[:：]?\s*" + re.escape(row["name"]) +
                                          r"(?=中华人民共和国|性别|[\s|，,;；]|$)", text)
                                or re.search(r"(?m)^[ \t]*\|[ \t]*姓名[ \t]*\|[ \t]*" +
                                             re.escape(row["name"]) + r"[ \t]*\|", text))
                        or re.search(r"查询记录|查询结果|填写示例|空白模板", text[:200])
                        or sum(name in text for name in names) != 1):
                    continue
                candidates.append({"material_id": material.id, "page": page["page"], "quote": row["name"],
                                   "location_mode": "page", "evidence_kind": "special_trade_personnel_page_clue"})
        if len(candidates) == 1:
            refs.append(candidates[0])
    return refs


def _labor_other_personnel_social_clues(materials: list) -> list[dict]:
    """仅为明确的其他管理岗位提供同一材料内的名单和同名社保原文线索。"""
    other_posts = {"施工员", "质量员", "材料员", "资料员", "预算员", "测量员", "试验员", "机械员", "标准员", "劳务员"}
    roster = extract_management_roster(materials)
    relations = extract_person_relation_evidence(materials)
    by_id = {material.id: material for material in materials}
    refs = []
    for row in roster:
        material = by_id.get(row.get("material_id"))
        name = row.get("name")
        if (row.get("post") not in other_posts or not name or material is None
                or not material_page_readable(material, row.get("page"))
                or sum(item.get("name") == name for item in roster) != 1):
            continue
        matches = [fact for fact in relations if fact.get("evidence_kind") == "social_record"
                   and fact.get("name") == name and fact.get("material_id") == material.id
                   and material_page_readable(material, fact.get("page"))]
        if len(matches) != 1:
            continue
        for source, kind in ((row, "personnel_roster_clue"), (matches[0], "social_record_clue")):
            refs.append({"material_id": material.id, "page": source["page"], "quote": source["quote"],
                         "location_mode": "page", "evidence_kind": kind})
    return refs


def _other_personnel_id_name_clues(materials: list) -> list:
    """只提供相邻页号码线索，不据此确定身份证持有人。"""
    identity_re = re.compile(r"(?<![0-9A-Za-z])\d{17}[0-9Xx](?![0-9A-Za-z])")
    clues = []
    for material in materials:
        if not is_attachment_source(material):
            continue
        roster = [row for row in extract_management_roster([material])
                  if row.get("material_id") == material.id and row.get("name")
                  and row.get("post") and material_page_readable(material, row.get("page"))]
        pages = {p.get("page"): p.get("text") for p in (material.pages_json or [])
                 if isinstance(p, dict) and type(p.get("page")) is int and isinstance(p.get("text"), str)}
        for number, text in pages.items():
            previous = pages.get(number - 1, "")
            if (not previous or not material_page_readable(material, number)
                    or not material_page_readable(material, number - 1)
                    or "身份证" not in text or re.search(r"姓\s*名", text)
                    or not re.search(r"(?:证书|合格证)", previous)
                    or re.search(r"附件目录|报审表|填写示例|空白模板", previous[:200])):
                continue
            ids, prior_ids = identity_re.findall(text), identity_re.findall(previous)
            matches = [row for row in roster if row["name"] in previous]
            holders = re.findall(r"姓\s*名\s*[:：]?\s*([\u4e00-\u9fff]{2,4})(?=\s|[，,;；]|$)", previous)
            if (len(ids) != 1 or len(prior_ids) != 1 or ids[0].upper() != prior_ids[0].upper()
                    or len(matches) != 1 or holders != [matches[0]["name"]]
                    or any(row["name"] in text for row in roster)
                    or re.search(r"企业负责人|项目经理|项目负责人|项目总工|安全员", matches[0]["post"])):
                continue
            clues.append({"material_id": material.id, "page": number, "previous_page": number - 1})
    return clues


def run_completeness_check(task, materials: list, *, subject_types=None, items=None) -> list:
    """执行适用条目对表。

    ``items`` 是主体级清单模块给出的已裁剪目录。旧调用不传时仅执行既有基础/表单
    完整性项，项目补充与签章组不会被悄悄混进历史任务口径。
    """
    if items is None:
        items = [item for item in load_report_checklist()
                 if (item.get("execution_group") or "completeness") == "completeness"
                 and not item.get("profile_codes")]
    packet = _packet_materials(materials)
    packet_types = {m.id: _packet_type(m) for m in packet}
    findings = []
    for item in items:
        if subject_types and not (_item_packet_types(item) & subject_types):
            continue
        eval_packets, early = _select_eval_packets(
            item, packet, packet_types, subject_types=subject_types,
        )
        if early:
            limit = _reading_limit(packet)
            if limit:
                early += "；" + limit
            findings.append(BsFinding(
                task_id=task.id, material_id=None, item_id=item["id"],
                kind="completeness", status=STATUS_UNDECIDABLE, message=early,
                quote=None, page=None, source=item["source"],
            ))
            continue
        evidence_refs = None
        location_mode = None
        missing = []
        if item["id"] == "RC-A10":
            status, anchor, message, evidence_refs = _check_authorization(
                item, eval_packets, packet_types, materials, subject_types=subject_types)
            mat_id, quote, page = ((anchor["material_id"], anchor["quote"], anchor["page"])
                                   if anchor else (None, None, None))
        elif item["kind"] == "document":
            if item.get("required_all"):
                status, mat_id, quote, page, missing, unknown = _check_document_required_all(
                    item, eval_packets, packet_types)
                message = _required_all_message(
                    item, status, missing, unknown, eval_packets, packet_types)
                if (eval_packets and status in {STATUS_MISS, STATUS_HIT}
                        and item["id"] in {"RC-A07", "RC-P11", "RC-P12", "RC-P13", "RC-L11"}):
                    # 关键词只能证明文字在材料内，不能证明各证件属于该岗位人员。
                    # 在人员与证件的逐项关联具备可靠证据前，不得宣布齐全。
                    status = STATUS_UNDECIDABLE
                    message = "「%s」尚不能确认人员、岗位与各证件对应，请人工核对" % item["label"]
                    if missing:
                        message += "；未找到明确线索：%s" % "、".join(missing)
                    limit = _reading_limit(eval_packets)
                    if limit:
                        message += "；" + limit
                if status == STATUS_UNDECIDABLE:
                    clue = _personnel_page_clue(item, eval_packets)
                    if clue:
                        mat_id, quote, page = clue
                        message += "；已定位岗位资料线索，证件与人员对应仍需核实"
                    degree_clue = _personnel_degree_name_clue(item, eval_packets, clue)
                    if degree_clue:
                        message += "；第%d页有可能相关的毕业证书，姓名未能确认，请核对原件" % degree_clue["page"]
                        evidence_refs = [{"material_id": mat_id, "page": page, "quote": quote,
                                          "location_mode": "page", "evidence_kind": "personnel_role_clue"},
                                         degree_clue]
                    unconfirmed = _personnel_certificate_clues(item, eval_packets)
                    if unconfirmed:
                        # 读取缺口可能包含大量页码，落库限长前先保留具体待核证件。
                        message = "该岗位人员尚未确认的证件：" + "、".join(unconfirmed) + "；" + message
                    confirmed = _confirmed_personnel_certificate_refs(item, eval_packets)
                    if confirmed:
                        evidence_refs = confirmed
                        mat_id, quote, page = confirmed[0]["material_id"], confirmed[0]["quote"], confirmed[0]["page"]
                        status, missing = STATUS_MISS, []
                        message = "「%s」已定位同一人员的各类证件；证件真实性和有效期另行核实" % item["label"]
            else:
                status, mat_id, quote, page = _check_document(item, eval_packets)
                message = _document_message(item, status, eval_packets)
                if status == STATUS_UNDECIDABLE and item["id"] in {"RC-A02", "RC-A03", "RC-A04"}:
                    uncertain_clues = [(m.id, p) for m in eval_packets for p, text in _norm_pages(m)
                                       if not material_page_readable(m, p)
                                       and any(keyword in text for keyword in item["keywords"])]
                    if len(uncertain_clues) == 1:
                        mat_id, page = uncertain_clues[0]
                        quote = None
                        location_mode = "page"
                        message += "；第%d页存在相关文字线索，但识别质量待核，请核对原件" % page
                if item["id"] == "RC-A09" and status != STATUS_MISS:
                    contract_evidence = contract_roster_evidence(materials)
                    if contract_evidence:
                        mat_id, quote, page = contract_evidence
                        status = STATUS_MISS
                        message = "「%s」已见于合同已填写人员信息" % item["label"]
            if (eval_packets and status in {STATUS_HIT, STATUS_MISS}
                    and item["id"] in {"RC-P15", "RC-L13", "RC-P16", "RC-L14"}):
                # 目录和合同义务只证明提及，不能证明名单中每个人的证件已提供。
                status, mat_id, quote, page = STATUS_UNDECIDABLE, None, None, None
                message = "「%s」尚不能确认人员名单与证件逐项对应，请人工核对" % item["label"]
                if item["id"] in {"RC-P16", "RC-L14"}:
                    roster = extract_special_trade_roster(eval_packets)
                    if roster:
                        first = roster[0]
                        mat_id, quote, page = first["material_id"], first["quote"], first["page"]
                        message = "「%s」已识别部分人员名单，尚不能确认人员与操作证逐项对应，请人工核对" % item["label"]
                        cert_refs = _special_trade_certificate_clues(eval_packets, roster)
                        if cert_refs:
                            evidence_refs = [{"material_id": first["material_id"], "page": first["page"],
                                              "quote": first["quote"], "location_mode": "page",
                                              "evidence_kind": "special_trade_roster"}, *cert_refs]
                            message += "；已定位 %d 条同名操作证线索，请核对原件" % len(cert_refs)
                limit = _reading_limit(eval_packets)
                if limit:
                    message += "；" + limit
        else:
            status, mat_id, quote, page, reason = _check_content(item, eval_packets)
            if status == STATUS_UNDECIDABLE:
                message = "「%s」不可判定：%s" % (item["label"], reason)
            elif status == STATUS_HIT:
                message = "报审表未见「%s」签字栏标识" % item["label"]
            else:
                message = "报审表已见「%s」标识" % item["label"]
        if item["id"] in {"RC-P16", "RC-L14"} and status == STATUS_UNDECIDABLE and mat_id is None:
            roster = extract_special_trade_roster(eval_packets)
            if roster:
                first = roster[0]
                mat_id, quote, page = first["material_id"], first["quote"], first["page"]
                message = "「%s」已识别部分人员名单线索，名单与操作证尚不能逐项确认，请人工核对" % item["label"]
                cert_refs = _special_trade_certificate_clues(eval_packets, roster, allow_unread_roster=True)
                personnel_refs = _special_trade_personnel_page_clues(eval_packets, roster)
                personnel_refs = [ref for ref in personnel_refs if not any(
                    cert["material_id"] == ref["material_id"] and cert["page"] == ref["page"]
                    and ref["quote"] in cert["quote"] for cert in cert_refs)]
                if cert_refs or personnel_refs:
                    evidence_refs = [{"material_id": mat_id, "page": page, "quote": quote,
                                      "location_mode": "page", "evidence_kind": "special_trade_roster"}, *cert_refs, *personnel_refs]
                    if cert_refs:
                        message += "；已定位 %d 条同名操作证线索" % len(cert_refs)
                    if personnel_refs:
                        message += "；已定位 %d 条同名人员资料线索" % len(personnel_refs)
                    message += "，名单识别质量及人员对应需核对原件"
                limit = _reading_limit(eval_packets)
                if limit:
                    message += "；" + limit
        if item["id"] == "RC-L13" and status == STATUS_UNDECIDABLE and eval_packets:
            evidence_refs = _labor_other_personnel_social_clues(eval_packets)
            message = "「%s」其他管理人员的社保依据和人员对应尚需核实" % item["label"]
            if evidence_refs:
                first = evidence_refs[0]
                mat_id, page, quote, location_mode = first["material_id"], first["page"], first["quote"], "page"
                message += "；已定位同名名单及社保线索，不能据此确认身份、单位、期间或全部人员覆盖"
            limit = _reading_limit(eval_packets)
            if limit:
                message += "；" + limit
        if item["id"] == "RC-P15" and status == STATUS_UNDECIDABLE:
            clues = _other_personnel_id_name_clues(eval_packets)
            if len(clues) == 1:
                clue = clues[0]
                mat_id, page, quote, location_mode = clue["material_id"], clue["page"], None, "page"
                message += "；第%d页身份证姓名未能识别，与第%d页证件存在相同号码线索，请核对原件" % (page, clue["previous_page"])
        missing_keys = None
        if status == STATUS_HIT and item.get("kind") == "document":
            child_by_label = {child["label"]: child["key"]
                              for child in item.get("required_all") or []}
            missing_keys = (["checklist:%s:%s" % (item["id"], child_by_label[label])
                             for label in missing if label in child_by_label]
                            or ["checklist:%s" % item["id"]])
        findings.append(BsFinding(
            task_id=task.id, material_id=mat_id, item_id=item["id"],
            kind="completeness", status=status, message=message,
            quote=quote, page=page, location_mode=location_mode,
            source=item["source"], evidence_json=evidence_refs,
            missing_json=missing_keys,
        ))
    return findings
