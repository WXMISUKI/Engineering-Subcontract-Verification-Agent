"""A3-02 签字链双轨核验（W3-2）。

双轨纪律（02 方案 难点3）：法定链与集团链**不合并、不选一**，各自判定、
报告分列——法定链判合规（签字链完整），集团链判流程完整性（集团特有的
指挥部/直属单位环节）。链定义在 data/signature_chains.json（规则=数据）。

三态口径（拍板 2026-09-21，见数据文件 blank_note）：
- 命中 = 链有缺环：节点签署行整体缺席，或签署栏在场但未见签署文字
  （后者 message 注明「若为手写签名请人工复核」）；
- 未命中 = 全节点有签署文字；
- 不可判定 = 无报审包 / 报审包无文本层 / 有文本但全链未见任何签署栏
  （签署页可能为扫描件），必须说明缺什么。

时序核验（先报批后实施）：取总监理工程师签署日期（审批完成锚点）与分包
合同开工日期；总监签署日晚于开工日 → 命中；任一日期抽不到 → 不可判定。
日期解析复用 startup_conditions_check.validity.parse_date_text（只读 import）。
"""
import re
from datetime import date

from ..startup_conditions_check.validity import parse_date_text
from .attachment_source import is_attachment_source
from .models import STATUS_HIT, STATUS_MISS, STATUS_UNDECIDABLE, BsFinding, BsMaterial
from .parsing import describe_reading_gap, material_page_readable, material_reading_gap
from .rules import load_signature_chains

_KIND = "signature_chain"
_TIMING_SOURCE = "先报批后实施（报审批复 ≤ 实施开工）"

# 开工日期抽取（分包合同文本）：匹配后交 parse_date_text 解析
_WORK_START_RE = re.compile(r"(?:计划)?开工日期\s*[:：]\s*([0-9年月日．.\-]+)")
# 签署行：角色名 + 冒号 + 其余文字（名字/日期/空）
_ROLE_LINE_RE_TPL = r"^(%s)\s*[:：]\s*(.*)$"


def _role_line_re(roles: list) -> re.Pattern:
    return re.compile(_ROLE_LINE_RE_TPL % "|".join(re.escape(r) for r in roles))


def extract_signatures(material: BsMaterial, nodes: list) -> dict:
    """报审包 → {角色: {signed, date, page, quote}}；文本层逐行匹配签署栏。

    signed=冒号后存在非日期外文字（姓名等）；栏空 → signed=False（拍板口径：
    判缺签命中并注明人工复核）。角色行整体缺席 → 不入字典（由判定层区分缺环）。
    """
    line_re = _role_line_re(nodes)
    out: dict = {}
    for p in material.pages_json or []:
        if not material_page_readable(material, p.get("page")):
            continue
        for raw in (p.get("text") or "").split("\n"):
            line = raw.strip()
            m = line_re.match(line)
            if not m:
                continue
            role, rest = m.group(1), m.group(2).strip()
            d = parse_date_text(rest)
            # 冒号后除日期外还有文字（姓名）才算有签署文字；仅日期也算已签
            has_text = bool(rest and (re.sub(r"[0-9年月日．.\-\s]", "", rest) or d))
            out[role] = {
                "signed": has_text,
                "date": d,
                "page": p.get("page"),
                "quote": line[:200],
            }
    return out


def _director_date(packet_sigs: list) -> tuple:
    """链结果池里取总监理工程师签署日期（多链取第一处）。"""
    for sigs in packet_sigs:
        d = sigs.get("总监理工程师") or {}
        if d.get("signed") and d.get("date"):
            return d["date"], d.get("page")
    return None, None


def _check_approval_timing_pair(task, packet_sigs: list, materials: list) -> list:
    """时序比对：总监签署日 ≤ 开工日（先报批后实施）。独立三态结论。"""
    approval, approval_page = _director_date(packet_sigs)
    start = None
    start_mat = None
    start_quote = None
    start_page = None
    for m in materials:
        if m.role not in {"sub", "labor"}:
            continue
        for p in m.pages_json or []:
            if not material_page_readable(m, p.get("page")):
                continue
            for raw in (p.get("text") or "").split("\n"):
                mm = _WORK_START_RE.search(raw.strip())
                if mm:
                    d = parse_date_text(mm.group(1))
                    if d is not None and start is None:
                        start, start_mat, start_quote, start_page = d, m, raw.strip()[:200], p.get("page")
    if approval is None or start is None:
        missing = []
        if approval is None:
            missing.append("总监理工程师签署日期")
        if start is None:
            missing.append("分包合同开工日期")
        return [BsFinding(
            task_id=task.id, material_id=None, item_id="A3-02-timing",
            kind=_KIND, status=STATUS_UNDECIDABLE,
            message="不可判定：先报批后实施时序缺 %s" % "、".join(missing),
            quote=None, page=None, source=_TIMING_SOURCE)]
    status = STATUS_HIT if approval > start else STATUS_MISS
    msg = ("命中：先实施后报批——总监理工程师签署日期 %s 晚于分包合同开工日期 %s"
           % (approval.isoformat(), start.isoformat())) if status == STATUS_HIT else (
        "未命中：报审批复先于实施（总监理工程师签署 %s ≤ 开工 %s）"
        % (approval.isoformat(), start.isoformat()))
    return [BsFinding(
        task_id=task.id, material_id=start_mat.id if start_mat else None,
        item_id="A3-02-timing", kind=_KIND, status=status, message=msg,
        quote=start_quote if status == STATUS_HIT else None,
        page=start_page if status == STATUS_HIT else None, source=_TIMING_SOURCE)]


def check_approval_timing(task, materials: list, *, allowed_subject_ids: set | None = None) -> list:
    """仅在同主体合同与报审资料唯一配对时比较日期。"""
    candidates = [m for m in materials if getattr(m, "role", None) in {"sub", "labor", "packet"}]
    subjects = sorted({m.subject_id for m in candidates if getattr(m, "subject_id", None) is not None})
    physical_groups = packet_groups(candidates)
    findings = []

    def unresolved(subject_id, reason):
        return BsFinding(task_id=task.id, subject_id=subject_id, material_id=None,
                         item_id="A3-02-timing", kind=_KIND, status=STATUS_UNDECIDABLE,
                         message="不可判定：%s，请人工核实" % reason,
                         quote=None, page=None, source=_TIMING_SOURCE)

    for subject_id in subjects:
        if allowed_subject_ids is not None and subject_id not in allowed_subject_ids:
            findings.append(unresolved(subject_id, "材料主体尚未确认"))
            continue
        contracts = [m for m in candidates if getattr(m, "subject_id", None) == subject_id
                     and m.role in {"sub", "labor"}]
        groups = [group for group in physical_groups
                  if any(getattr(m, "subject_id", None) == subject_id for m in group)]
        if any(any(getattr(m, "subject_id", None) != subject_id for m in group) for group in groups):
            findings.append(unresolved(subject_id, "报审资料主体不一致或未确认"))
            continue
        # A packet ID explicitly joins separately uploaded files in the same batch.
        merged = {}
        for group in groups:
            packet_ids = {getattr(m, "packet_id", None) for m in group}
            if len(packet_ids) > 1:
                merged[("conflict", id(group))] = group
            else:
                packet_id = next(iter(packet_ids))
                key = ("packet", packet_id) if packet_id is not None else ("group", id(group))
                merged.setdefault(key, []).extend(group)
        if len(contracts) != 1 or len(merged) != 1 or any(key[0] == "conflict" for key in merged):
            findings.append(unresolved(subject_id, "合同或报审资料无法唯一配对"))
            continue
        packet = next(iter(merged.values()))
        contract_packet_id = getattr(contracts[0], "packet_id", None)
        packet_id = getattr(packet[0], "packet_id", None)
        if contract_packet_id is not None and packet_id is not None and contract_packet_id != packet_id:
            findings.append(unresolved(subject_id, "合同与报审资料的资料包标识不一致"))
            continue
        signatures = [extract_signatures(m, ["总监理工程师"]) for m in packet]
        role_pattern = _role_line_re(["总监理工程师"])
        approval_dates = set()
        for material in packet:
            for page in material.pages_json or []:
                for line in (page.get("text") or "").splitlines():
                    match = role_pattern.match(line.strip())
                    date = parse_date_text(match.group(2)) if match else None
                    if date:
                        approval_dates.add(date)
        start_dates = set()
        for page in contracts[0].pages_json or []:
            for line in (page.get("text") or "").splitlines():
                match = _WORK_START_RE.search(line.strip())
                date = parse_date_text(match.group(1)) if match else None
                if date:
                    start_dates.add(date)
        if len(approval_dates) > 1:
            findings.append(unresolved(subject_id, "报审资料存在多个不同的总监签署日期"))
            continue
        if len(start_dates) > 1:
            findings.append(unresolved(subject_id, "合同存在多个不同的开工日期"))
            continue
        result = _check_approval_timing_pair(task, signatures, contracts)[0]
        result.subject_id = subject_id
        findings.append(result)
    if any(getattr(m, "subject_id", None) is None for m in candidates):
        findings.append(unresolved(None, "部分合同或报审资料未关联主体"))
    return findings or [unresolved(None, "缺少可关联主体的合同和报审资料")]


def _seal_note(mat: BsMaterial) -> str:
    """盖章信号提示（W4 红章复用）：材料页面上检出盖章痕迹时附进复核文案。

    OC 硬护栏语义：检不出信号 ≠ 缺章证据——返回空串，维持原有人工复核文案，
    绝不因无信号把结论升级为缺章。
    """
    pages = sorted({int(b["page_idx"]) for b in (getattr(mat, "seal_json", None) or [])
                    if isinstance(b, dict) and b.get("page_idx")})
    if not pages:
        return ""
    if len(pages) > 3:
        return "；材料多页存在盖章线索，请人工核对相关签署页、签名与落款"
    return "；图像检测在%s检出盖章痕迹，请人工核对签名与落款" % "、".join("第%d页" % p for p in pages)


# A-05（B2）：判章口径声明——A3-02 结论的盖章判断仅适用于本地判章兜底口径。
# 部署侧 seal-service（8501）本机不可达，实际全走本地图像检测（17-判章能力基线）；
# 主链路表现不做承诺，故在 source 明示口径。
_SEAL_CALIBER = "；盖章判断仅适用于本地判章口径，不承诺主链路表现"


def _seal_clause(mat: BsMaterial) -> str:
    """结论文案的盖章附注（**判定与文案分离**，不改变 `_seal_note` 行为）。

    - 检出信号（seal_json 非空）→ 复用 `_seal_note` 的检出提示；
    - 未检出信号（seal_json 空）→ 诚实提示「未检出盖章痕迹，盖章情况需人工翻看原件」
      ——**维持原判、绝不据此判缺章**（RD-3）。
    """
    if (getattr(mat, "seal_json", None) or []):
        return _seal_note(mat)
    return "；图像检测未检出盖章痕迹，盖章情况需人工翻看原件"


def _row_source(mat: BsMaterial) -> str:
    """报告用的来源说明：同一 ZIP 容器的子材料显示为「容器名 / 相对路径」。

    - 容器子材料（`container_entry_path` 非空）→「容器文件名 / 相对路径」；
    - 独立 packet（无容器）→ 直接文件名。
    仅用于**显示与跨子材料去重**，不参与数据库写入（不给结论添列 / 改 schema）。
    """
    if getattr(mat, "container_entry_path", None):
        return "%s / %s" % (mat.file_name or "", mat.container_entry_path)
    return mat.file_name or ""


def packet_groups(materials: list) -> list:
    """确定性分组：相同 `container_material_id` 的 `role=='packet'` 子材料一组。

    - 独立 packet（`container_material_id is None`）各自一组（每组一个）；
    - 组内按 `container_entry_path` 排序，无路径者按 `id` —— **不依赖数据库偶然顺序**；
    - 组间按组内最小 `id` 排序，保证重复运行顺序稳定。

    返回 `[[material, ...], ...]`（每个子列表是一组）。
    """
    packets = [m for m in materials if is_attachment_source(m)]
    by_container: dict = {}
    order: list = []
    for m in packets:
        key = getattr(m, "container_material_id", None)  # None（含历史材料）的各成一个独立组
        if key is None:
            key = ("__solo__", m.id)
        if key not in by_container:
            by_container[key] = []
            order.append(key)
        by_container[key].append(m)
    groups = []
    for key in order:
        members = by_container[key]
        members.sort(key=lambda m: (getattr(m, "container_entry_path", None) or "", m.id))
        groups.append(members)
    groups.sort(key=lambda g: min(m.id for m in g))
    return groups


def _group_seal_detail(members: list) -> str:
    """多成员组的盖章明细：「容器名 / 相对路径（第N页）」，说明行来自哪一份子材料。"""
    parts = []
    for m in members:
        pages = sorted({int(b["page_idx"]) for b in (getattr(m, "seal_json", None) or [])
                        if isinstance(b, dict) and b.get("page_idx")})
        if pages:
            if len(pages) > 3:
                parts.append("%s（多页存在盖章线索）" % _row_source(m))
                continue
            parts.append("%s（%s）" % (_row_source(m),
                                      "、".join("第%d页" % p for p in pages)))
    return "；图像检测在%s检出盖章痕迹，请人工核对签名与落款" % "、".join(parts) if parts else ""


def _group_seal_note(members: list) -> str:
    """命中/不可判定用的盖章提示（`_seal_note` 语义：无信号 → 空串）。

    - **单成员组** → 原样 `_seal_note`，保持「第N页检出盖章痕迹」既有文案形态；
    - **多成员组** → 逐子材料明细（说明行来自哪一份）；
    - 全组无信号 → 空串（维持原人工复核文案；绝不判缺章）。
    """
    if len(members) == 1:
        return _seal_note(members[0])
    return _group_seal_detail(members)


def _group_seal_clause(members: list) -> str:
    """未命中用的盖章附注（`_seal_clause` 语义：无信号 → 诚实「未检出需人工翻看」）。

    既有断言要求无信号未命中结论**必须**含「未检出盖章痕迹…人工翻看原件」，
    故此处不得回落为空串；多成员组无信号时同样给诚实附注。
    """
    if len(members) == 1:
        return _seal_clause(members[0])
    return _group_seal_detail(members) or "；图像检测未检出盖章痕迹，盖章情况需人工翻看原件"


def _container_qualifier(members: list) -> str:
    """同容器多子材料时的容器限定语（用于未命中结论说明「本组综合 N 份材料」）。"""
    cids = {getattr(m, "container_material_id", None) for m in members}
    if len(members) > 1 and cids != {None}:
        name = next((m.file_name.split(" / ")[0] for m in members if m.file_name), "")
        return "（来源：报审资料包「%s」共 %d 份材料）" % (name, len(members))
    return ""


_SOURCE_MISSING = "（缺：签署栏文本）"


def _form_opinion_clue(members):
    """只描述可读报审表上的栏目文字，不据此推断签署或审批有效。"""
    candidates = []
    labels = ("总监理工程师意见", "指挥部意见")
    for material in members:
        if material.parse_state != "done":
            continue
        for page in material.pages_json or []:
            number = page.get("page")
            text = page.get("text") or ""
            if (type(number) is int and number > 0 and material_page_readable(material, number)
                    and "分包报审表" in text[:300] and all(label in text for label in labels)
                    and not re.search(r"空白模板|填写示例|样表", text[:300])):
                candidates.append((material, number))
    return candidates[0] if len(candidates) == 1 else None


def _has_confirm_evidence(members: list, nodes: list) -> bool:
    """★分辨力判据：组内是否至少有一条**姓名（非纯日期）**的签署行。

    `extract_signatures` 对「仅日期」的 "角色：2024年3月18日" 也判 signed=True
    （`:54-55` 原始口径，与既有用例一致）。若只凭 `signed` 判「全节点有签署 → 未命中」，
    则一份**全为日期签名**的报审包会被判「合规」——但日期本身说明不了谁签了什么
    （真实风险反而是「本应几条真签名，却只有几条日期」）。
    故聚合后要求承担「未命中」的组**必须存在真实的可确认签署**；否则不判未命中，
    按本仓软信号不升级纪律转「不可判定」并说明缺什么。
    """
    for m in members:
        for s in extract_signatures(m, nodes).values():
            # 取**第一个**冒号（半角或全角）之后的内容，与 extract_signatures 的
            # `m.group(2)` 口径一致；按行内任意冒号分割会误切（如「项目经理：张建国 10:30」）。
            parts = re.split(r"[:：]", s.get("quote") or "", maxsplit=1)
            rest = parts[1] if len(parts) > 1 else ""
            # 纯日期（如「2024年3月18日」）去数字/日期符号后为空 ⇒ 不算可确认签署；
            # 只有残留姓名等非日期字符才算真实签署（这就是与 `signed=True` 的分辨力差异）。
            if re.sub(r"[0-9年月日．.\-\s]", "", rest).strip():
                return True
    return False


def _decide_single_chain(group: list, nodes: list) -> dict:
    """单链单组判定（返回 dict：status 与证据/缺失/空栏），不直接构造 BsFinding。

    - `status`：hit / miss / undecidable；
    - `evidence`：`(material, page, quote)` —— 证据锚点，仅 hit 时有值；
    - `missing` / `blank`：跨成员并集后的缺失与空栏节点（hit 文案用）；
    - `reason`：undecidable 的细分（`no_text` 无任何签署栏文字 / `no_confirm` 有栏但无可确认签署人）。
    """
    missing: list = []
    blank: list = []
    read_any_slot = False
    read_any_done = False
    # 跨成员并集：节点只要在**任一**子材料出现即视为「在场」，
    # 只要有**任一处**已签署即视为「已签」——避免逐材料判缺失后并集把
    # 「A 材料签了项目经理、B 材料签了总监」误判成两条都缺。
    present: dict = {}      # node -> 是否在任一子材料出现过签署栏行
    signed_any: dict = {}   # node -> 是否在任一子材料有签署文字
    blank_hit = None        # (member, page, quote) 首个空栏证据
    for m in group:
        if m.parse_state != "done":
            continue
        read_any_done = True
        sigs = extract_signatures(m, nodes)
        if any(s.get("quote") for s in sigs.values()):
            read_any_slot = True
        for n in nodes:
            s = sigs.get(n)
            if s is None:
                continue
            present[n] = True
            if s["signed"]:
                signed_any[n] = True
            elif blank_hit is None:
                blank_hit = (m, s.get("page"), s.get("quote"))

    if not read_any_done or not read_any_slot:
        # 组内无解析完成材料，或整组未见任何签署栏文字（扫描件 / 空包）→ 不可判定
        return {"status": STATUS_UNDECIDABLE, "reason": "no_text"}

    missing = [n for n in nodes if n not in present]
    blank = [n for n in nodes if present.get(n) and not signed_any.get(n)]

    if not missing and not blank:
        # 全节点有签署：须存在真实姓名签署才判「未命中」，否则转不可判定（分辨力）
        if _has_confirm_evidence(group, nodes):
            return {"status": STATUS_MISS, "missing": [], "blank": []}
        return {"status": STATUS_UNDECIDABLE, "reason": "no_confirm"}

    if any(material_reading_gap(member) is not None for member in group):
        return {"status": STATUS_UNDECIDABLE, "reason": "unread_pages"}

    # 命中：空栏优先取证据（栏在场但空，有引文可点），否则取缺失（栏缺席，无引文）
    return {"status": STATUS_HIT, "missing": missing, "blank": blank, "evidence": blank_hit}


def run_signature_chain_checks(task, materials: list, *, require_assigned: bool = False,
                               allowed_subject_ids: set | None = None) -> list:
    """A3-02 双轨 + 时序编排（纯函数层，落库由 service 负责）。

    **容器级聚合（T11-D-A §二.3）**：同 `container_material_id` 的 packet 子材料归一组、
    合出一组链结论；`container_material_id is None`（含历史材料）各自独立成组。分组与
    组内顺序由 `packet_groups` 确定性给出，不依赖数据库偶然顺序。时序 `A3-02-timing`
    按主体核查，合同与报审资料不能唯一配对时交人工核实。证据锚点来自真实子材料与原始 1-based 页码。
    """
    groups = packet_groups(materials)
    findings: list = []
    for chain in load_signature_chains():
        nodes = chain["nodes"]
        # A-05（B2）：结论出处附判章口径声明（盖章判断仅适用本地兜底口径）
        source = "%s%s" % (chain["source"], _SEAL_CALIBER)
        item_id = "A3-02-%s" % chain["id"]
        if not groups:
            findings.append(BsFinding(
                task_id=task.id, material_id=None, item_id=item_id,
                kind=_KIND, status=STATUS_UNDECIDABLE,
                message="不可判定：任务无报审资料包，签字链无从核验（缺：报审包）",
                quote=None, page=None, source=source))
            continue
        for group in groups:
            group_subjects = {getattr(m, "subject_id", None) for m in group}
            if require_assigned and (len(group_subjects) != 1 or None in group_subjects
                    or (allowed_subject_ids is not None and not group_subjects.issubset(allowed_subject_ids))):
                findings.append(BsFinding(
                    task_id=task.id, material_id=group[0].id, item_id=item_id,
                    kind=_KIND, status=STATUS_UNDECIDABLE,
                    message="报审资料主体未确认或存在冲突，无法核验该主体的签字链，请先核实材料归属",
                    quote=None, page=None, source=source))
                continue
            verdict = _decide_single_chain(group, nodes)
            rep = group[0]
            subject_id = next(iter(group_subjects)) if len(group_subjects) == 1 else None
            status = verdict["status"]
            if status == STATUS_UNDECIDABLE:
                reason = verdict.get("reason")
                clue = _form_opinion_clue(group) if reason == "no_text" else None
                if reason == "unread_pages":
                    scope = "；".join(
                        describe_reading_gap(member, gap)
                        for member in group
                        if (gap := material_reading_gap(member)) is not None
                    ) or "部分材料未读"
                    msg = "暂时无法判断：%s，签署环节是否齐全请核对原件%s" % (scope, _group_seal_note(group))
                elif reason == "no_confirm":
                    msg = ("暂时无法判断：%s签署栏在场，但未见可确认的签署人姓名"
                           "（签署栏仅含日期或空缺，无法确认签署人），需人工翻看原件"
                           "（缺：签署人姓名）%s" % (chain["name"], _group_seal_note(group)))
                elif clue:
                    msg = ("报审表中识别到总监理工程师意见及指挥部意见栏目；"
                           "本项签署环节、签章及意见内容需结合原件核对%s" % _group_seal_note(group))
                else:
                    msg = ("暂时无法判断：报审资料里没有读到签署栏文字，签署页可能为扫描件"
                           "（若为手写签名请人工翻看原件）%s%s"
                           % (_SOURCE_MISSING, _group_seal_note(group)))
                findings.append(BsFinding(
                    task_id=task.id, material_id=clue[0].id if clue else rep.id, subject_id=subject_id, item_id=item_id,
                    kind=_KIND, status=STATUS_UNDECIDABLE, message=msg,
                    quote="总监理工程师意见" if clue else None, page=clue[1] if clue else None, source=source,
                    evidence_json=([{"material_id": clue[0].id, "page": clue[1], "quote": label}
                                    for label in ("总监理工程师意见", "指挥部意见")] if clue else None)))
                continue
            if status == STATUS_MISS:
                findings.append(BsFinding(
                    task_id=task.id, material_id=rep.id, subject_id=subject_id, item_id=item_id,
                    kind=_KIND, status=STATUS_MISS,
                    message="未命中：%s全节点有签署文字（%s）%s%s"
                            % (chain["name"], "、".join(nodes),
                               _container_qualifier(group),
                               _group_seal_clause(group)),
                    quote=None, page=None, source=source))
                continue
            # 命中
            parts = ["缺「%s」签署环节" % n for n in verdict.get("missing", [])]
            parts += ["「%s」签署栏在场但未见签署文字" % n for n in verdict.get("blank", [])]
            ev = verdict.get("evidence")
            ev_mat, ev_page, ev_quote = (ev if ev else (None, None, None))
            anchor = ev_mat.id if ev_mat is not None else rep.id
            findings.append(BsFinding(
                task_id=task.id, material_id=anchor, subject_id=subject_id, item_id=item_id,
                kind=_KIND, status=STATUS_HIT,
                message="命中：%s%s（若为手写签名请人工复核）%s%s"
                        % (chain["name"], "；".join(parts),
                           _container_qualifier(group),
                           _group_seal_note(group)),
                quote=ev_quote, page=ev_page, source=source))
    findings.extend(check_approval_timing(
        task, materials, allowed_subject_ids=allowed_subject_ids if require_assigned else None))
    return findings
