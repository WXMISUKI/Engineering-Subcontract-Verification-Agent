"""以已确认的分包主体约束关系类核查的证据范围，不猜测材料归属。"""
from . import a2_check, pinning
from .finding_text import bounded_finding_message
from .identity_labels import subject_identity_label
from .models import BsFinding, STATUS_UNDECIDABLE


def subject_is_confirmed(subject) -> bool:
    """主体身份已由用户确认；自动占位不贡献确定性范围。

    AI 证据与人工选择的类型不一致（type_conflict）只作提示，不取消确认——
    类型以用户所选资料类别为准（方案A）。
    """
    return bool(not subject.retired and subject.origin == "explicit" and subject.name
                and subject.type_confirmed
                and subject.subject_type in {"professional", "labor"})


def subject_material_names_consistent(subject, materials) -> bool:
    """未观察到材料名称冲突时才允许使用已确认的主体归属。"""
    names = a2_check.subcontractor_names(materials)
    return len(names) <= 1 and (not names or a2_check.norm_company(subject.name) in names)


def completeness_subject_types(subjects, materials):
    """仅在全部有效主体及材料归属确定时缩小条目类型；否则保持未知。"""
    if not subjects or any(not subject_is_confirmed(subject) for subject in subjects):
        return None
    subject_ids = {subject.id for subject in subjects}
    if any(material.role != "general" and material.subject_id not in subject_ids
           for material in materials):
        return None
    return {subject.subject_type for subject in subjects}


def run_subject_checks(task, subjects, materials, check, *, item_id, kind, source,
                       use_expected_company=False, allow_empty=False) -> list:
    """逐主体只读计算后合并返回，由调用方统一落库一次。

    主体标识写入可读说明；无证据的聚合结果不伪造材料引文或页码。
    未确认/无效归属的材料只产生待核说明，不贡献其他主体的通过证据。
    """
    findings = []
    grouped_ids = set()

    def unknown(message, owned):
        return BsFinding(task_id=task.id, item_id=item_id, kind=kind,
                         status=STATUS_UNDECIDABLE, message=bounded_finding_message(message), source=source,
                         material_id=owned[0].id if owned else None)

    for subject in subjects:
        owned = [m for m in materials if m.subject_id == subject.id]
        grouped_ids.update(m.id for m in owned)
        # 编号是身份标识（不带创建时的类型前缀），类型另有字段，不并排成「编号（类型）」
        label = "%s（%s）" % (subject_identity_label(subject), subject.name or "单位名称待确认")
        if not subject_is_confirmed(subject):
            findings.append(unknown(label + "：主体名称或类型未确认，材料归属需人工核对", owned))
            continue
        if not subject_material_names_consistent(subject, owned):
            finding = unknown(label + "：材料识别的分包单位名称与已确认主体不一致，可能为识别误差或归属冲突，"
                              "请按所列页码核对原件后确认；不能自动合并为同一企业", owned)
            # 纸面最多展示前三个出处；各不同名称先保留一处，重复同名页不挤掉冲突侧。
            distinct_names = {}
            for ref in a2_check.subcontractor_name_evidence(owned):
                distinct_names.setdefault(ref["company"], ref)
            finding.evidence_json = list(distinct_names.values())
            pinning.pin_findings([finding], owned)
            findings.append(finding)
            continue
        kwargs = {"expected_company": subject.name} if use_expected_company else {}
        current = check(task, owned, **kwargs)
        for finding in current:
            finding.subject_id = subject.id
            finding.message = bounded_finding_message(label + "：" + finding.message)
        pinning.pin_findings(current, owned)
        findings.extend(current)

    # 总包合同是公共上下文，不是某个分包人的证照或人员证明。
    unassigned = [m for m in materials if m.id not in grouped_ids and m.role != "general"]
    if unassigned:
        names = "、".join(m.file_name for m in unassigned[:3])
        findings.append(unknown("资料尚未关联已确认有效主体，不能用于本单位关系核查，需人工确认归属：「%s」"
                                % names, unassigned))
    if not findings and (not allow_empty or not subjects):
        findings.append(unknown("尚无已确认的分包主体，需先确认主体及材料归属后核查", []))
    return findings
