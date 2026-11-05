"""内部信用与事故材料核查。

本期方案 A 只消费审查任务内的业主材料，不访问信用中国或其他外部网站。
判定坚持宁缺勿错：只有材料中出现明确的正向或负向事实表述才给出命中/未命中；
缺少内部记录、只有规则说明或无法从文本中定位事实时均为不可判定。
"""

from __future__ import annotations

import re

from .models import STATUS_HIT, STATUS_MISS, STATUS_UNDECIDABLE, BsFinding
from .parsing import describe_reading_gap, material_reading_gap

ITEM_ID = "BS-INT-01"
KIND = "internal_records"
SOURCE = "业主内部警示名单、事故记录或信用核查材料；本期不访问外部网站"

_MATERIAL_HINT_RE = re.compile(
    r"警示名单|禁入名单|黑名单|风险名单|事故通报|事故调查|事故记录|信用记录|信用评价|信用查询|信用核查|不良记录"
)

# 先检查负向表述，避免“未列入警示名单”被“列入”子串误判为命中。
_MISS_RE = re.compile(
    r"(?:未列入|未纳入|不在|不存在|未发现|没有|无)(?:[^。；;，,]{0,16}?)"
    r"(?:警示名单|禁入名单|黑名单|风险名单|事故记录|事故|不良记录)"
)
_HIT_RE = re.compile(
    r"(?<!未)(?<!不)(?<!无)(?<!不得)(?<!不应)(?<!禁止)"
    r"(?:列入|纳入|属于|存在|发生|被认定)(?:.{0,16})"
    r"(?:警示名单|禁入名单|黑名单|风险名单|事故|违法分包|转包|不良记录)"
)
_NONFACT_RE = re.compile(
    r"(?:制度|规范|规则|条款)(?:规定|要求|引用|示例)|(?:引用|引文)[:：]"
    r"|(?:^|[:：，,])(?:若|如|如果|假如|倘若|一旦)"
    r"|安全目标|控制目标|预防目标|工作目标|承诺|保证|未来|将来|计划|拟列入|拟纳入"
    r"|(?:尚未|未)(?:被)?(?:认定|确认|查明)|待(?:认定|确认|查明)|将于|拟于|预计于"
)


def _pages(material) -> list[tuple[int | None, str]]:
    return [
        (page.get("page"), page.get("text") or "")
        for page in (material.pages_json or [])
        if (page.get("text") or "").strip()
    ]


def _is_candidate(material) -> bool:
    if material.role in ("general", "sub", "labor"):
        return False
    filename = material.file_name or ""
    if _MATERIAL_HINT_RE.search(filename):
        return True
    # 仅凭文本提示不足以证明是内部记录；要求文件名显式标识记录类型，
    # 避免报审包/制度说明中的规则文字进入事实核查。
    return False


def is_internal_record_candidate(material) -> bool:
    """与固定核查共用内部记录候选材料判据。"""
    return _is_candidate(material)


def _finding(task, *, status: str, message: str, material_id=None,
             page=None, quote=None) -> BsFinding:
    return BsFinding(
        task_id=task.id,
        material_id=material_id,
        item_id=ITEM_ID,
        kind=KIND,
        status=status,
        message=message,
        quote=quote,
        page=page,
        source=SOURCE,
    )


def _evidence(text: str, match: re.Match) -> str:
    start = max(0, match.start() - 40)
    end = min(len(text), match.end() + 60)
    return text[start:end].strip()[:500]


def _matches_subject(text: str, match: re.Match, subject_names: list[str]) -> bool:
    """要求记录事实与当前任务主体同页且同一局部文本相关，避免串用其他单位记录。"""
    start = max(0, match.start() - 120)
    end = min(len(text), match.end() + 120)
    window = text[start:end]
    return any(name and name in window for name in subject_names)


def _nonfactual_match(clause: str, match: re.Match) -> bool:
    """只有命中之前及其自身的非事实语境能支配该事实，后续整改计划不能抹掉事故。"""
    return _NONFACT_RE.search(clause[:match.end()]) is not None


def run_internal_records_check(task, materials: list,
                               *, subject_names: list[str] | None = None,
                               subject_names_by_id: dict[int, str] | None = None) -> list[BsFinding]:
    """从任务内材料核查内部信用/事故记录，返回一条三态 finding。"""
    candidates = [material for material in materials if _is_candidate(material)]
    if not candidates:
        return [_finding(
            task,
            status=STATUS_UNDECIDABLE,
            message="未提供业主内部警示名单、事故记录或信用核查材料，无法判断；请补充内部记录或人工核实",
        )]

    candidate_subject_ids = {material.subject_id for material in candidates
                             if material.subject_id is not None}
    if len(candidate_subject_ids) > 1:
        return [_finding(
            task,
            status=STATUS_UNDECIDABLE,
            message="内部记录材料归属于多个分包主体，无法安全归属当前核查对象；请人工确认材料归属",
        )]
    if candidate_subject_ids and subject_names_by_id:
        subject_names = [subject_names_by_id.get(next(iter(candidate_subject_ids)), "")]
    else:
        subject_names = subject_names or []
    subject_names = [name.strip() for name in subject_names if name and name.strip()]
    if len(subject_names) != 1:
        return [_finding(
            task,
            status=STATUS_UNDECIDABLE,
            message="内部记录无法与唯一分包主体对应；请先确认主体名称和材料归属",
        )]

    unresolved = False
    reading_gaps = []
    miss_evidence = None
    hit_evidence = None
    for material in candidates:
        unread = material_reading_gap(material)
        if unread is not None:
            unresolved = True
            reading_gaps.append((material, unread))
        pages = _pages(material)
        if material.parse_state != "done" or not pages:
            unresolved = True
            continue
        for page, text in pages:
            # 文件名只是候选，只抑制被制度、未来计划或承诺目标支配的命中；
            # 同句后续整改计划不能抹掉前面已明确记载的实际事实。
            for clause in re.split(r"[。；;！？!?]", text):
                misses = list(_MISS_RE.finditer(clause))
                for miss in misses:
                    if (not _nonfactual_match(clause, miss)
                            and _matches_subject(clause, miss, subject_names)
                            and miss_evidence is None):
                        miss_evidence = (material, page, _evidence(clause, miss))
                for hit in _HIT_RE.finditer(clause):
                    # “没有发生事故”等否定词不一定紧邻发生，须屏蔽整段否定命中。
                    if any(miss.start() <= hit.start() and hit.end() <= miss.end()
                           for miss in misses):
                        continue
                    if (not _nonfactual_match(clause, hit)
                            and _matches_subject(clause, hit, subject_names)
                            and hit_evidence is None):
                        hit_evidence = (material, page, _evidence(clause, hit))

    reading_limit = ""
    if reading_gaps:
        reading_limit = "；".join(
            "「%s」%s" % (
                material.file_name,
                describe_reading_gap(material, unread),
            ) for material, unread in reading_gaps
        ) + "，相关记录的其他内容暂时无法确认，需人工翻看原件或补充清晰材料"
    if hit_evidence:
        material, page, quote = hit_evidence
        return [_finding(
            task,
            status=STATUS_HIT,
            message="内部材料明确记载该主体存在警示、事故或相关不良记录，请人工复核记录效力和适用期间"
                    + ("；" + reading_limit if reading_limit else ""),
            material_id=material.id,
            page=page,
            quote=quote,
        )]
    if miss_evidence and not unresolved:
        material, page, quote = miss_evidence
        return [_finding(
            task,
            status=STATUS_MISS,
            message="内部材料明确记载未发现警示、禁入或事故记录；结论仅限本次提供的内部材料",
            material_id=material.id,
            page=page,
            quote=quote,
        )]
    if reading_limit:
        return [_finding(
            task,
            status=STATUS_UNDECIDABLE,
            message="内部材料未读完整，不能确认全部提供记录的实际状态；" + reading_limit,
            material_id=reading_gaps[0][0].id,
        )]
    return [_finding(
        task,
        status=STATUS_UNDECIDABLE,
        message="已找到内部记录材料，但未能定位明确的主体事实表述；请人工核实原件和适用期间",
    )]


__all__ = ["ITEM_ID", "KIND", "SOURCE", "is_internal_record_candidate", "run_internal_records_check"]
