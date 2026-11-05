# -*- coding: utf-8 -*-
"""blockchain_supervision A1-RULE-02：T15-03/T15-04 采购租赁与现场/劳动关系 定向契约测试。

判定纪律锚点（A1-RULE-02 任务包 / A1-03 总计划 §4 / 2026-10-08 冻结补充）：
- T15-03 命中=合同明文由承包人负责采购/租赁（责任明文只在总包件检索）+ 四侧配套证据齐全
  + 由其他单位/个人采购租赁的明示；未命中=自购自租（全部采购/租赁证据主体均为承包人自身）；
- T15-04 命中=六侧证据齐全且现场/劳动/社保证据指向其他单位；未命中=六侧齐全且全部指向
  承包人自身人员；缺现场资料/劳动合同等任一侧 → 不可判定并列明缺项；
- 「不能合理解释并提供相应证明」不由机器裁定合理性：材料含解释文本时不得判命中/未命中，
  只能不可判定 + 人工复核提示；
- 社保材料必须识别到统计期间（年月区间确定性正则），缺期间 → 不可判定并列「缺：社保统计期间」；
- 分包件出现「由分包人负责采购」类字样、总包件无责任明文时不得据此命中（归因错配负例）；
- 不得出现「已认定」「挂靠风险」「证在人不在」等措辞，命中只陈述线索语气。

本文件只直接调用纯内核 `a1_check.run_transfer_materials_check`（不接入 service 编排，接线见 R05）。
"""
from types import SimpleNamespace

import pytest

from app.plus.blockchain_supervision import a1_check
from app.plus.blockchain_supervision.models import (
    STATUS_HIT, STATUS_MISS, STATUS_UNDECIDABLE,
)

pytestmark = pytest.mark.integration


# ==================== 夹具（与 test_bs_a1_transfer_scope.py 同形态） ====================

def _mat(mid, name, text="", role="packet"):
    pages = [{"page": 1, "text": text}] if text else []
    return SimpleNamespace(id=mid, file_name=name, role=role, parse_state="done",
                           pages_json=pages)


def _task(**kw):
    kw.setdefault("id", 1)
    return SimpleNamespace(**kw)


def _run(materials, task=None):
    return a1_check.run_transfer_materials_check(task or _task(), materials)


def _find(item_id, materials, task=None):
    return next(f for f in _run(materials, task) if f.item_id == item_id)


def _all_text(findings):
    return " ".join("%s %s" % (f.message or "", f.source or "") for f in findings)


# ==================== T15-03 采购租赁由其他单位或个人完成 ====================

def _t15_03_hit_mats():
    return [
        _mat(1, "总包合同.pdf",
             "总承包范围：路基工程。主要材料、构配件和设备由承包人负责采购。",
             role="general"),
        _mat(2, "材料采购合同及发票.pdf",
             "材料采购合同：主要材料由丙公司采购供应。增值税专用发票已开具。供应商：丙公司。",
             role="sub"),
    ]


def test_t15_03_hit_with_third_party_procurement_and_plaintext():
    """责任明文 + 他人采购证据 + 四侧齐全 → 命中，证据锚定真实材料页且引文非空。"""
    mats = _t15_03_hit_mats()
    f = _find("T15-03", mats)
    assert f.status == STATUS_HIT
    assert f.kind == "transfer_scope"
    assert f.material_id in {m.id for m in mats}    # 指向真实传入材料
    mat = next(m for m in mats if m.id == f.material_id)
    pages = {p["page"] for p in mat.pages_json}
    assert isinstance(f.page, int) and f.page in pages  # page 为 int 且该材料确有该页
    assert f.quote and "采购" in f.quote            # 引文非空
    assert "命中" in f.message and "人工复核" in f.message


def _t15_03_miss_mats():
    return [
        _mat(1, "总包合同.pdf",
             "总承包范围：路基工程。主要材料由承包人负责采购，由承包人自行采购并自行租赁设备。",
             role="general"),
        _mat(2, "采购凭证.pdf",
             "采购合同：主要材料由承包人自行采购。出卖方：承包人。已开具增值税普通发票。",
             role="sub"),
    ]


def test_t15_03_miss_when_all_procurement_is_self():
    """自购自租（全部采购证据主体均为承包人自身）→ 未命中。"""
    f = _find("T15-03", _t15_03_miss_mats())
    assert f.status == STATUS_MISS
    assert "未命中" in f.message and "自购自租" in f.message


def test_t15_03_undecidable_when_contract_and_invoice_missing():
    """缺采购/租赁合同、发票（亦无解释）、供应商主体 → 不可判定，缺失侧标签逐字列全。"""
    mats = [
        _mat(1, "总包合同.pdf", "总承包范围：路基工程。主要材料由承包人负责采购。", role="general"),
    ]
    f = _find("T15-03", mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "缺" in f.message
    assert "采购/租赁合同" in f.message
    assert "发票或合理解释" in f.message
    assert "供应商主体" in f.message


def test_t15_03_explanation_never_judged_by_machine():
    """「合理解释不裁」：材料含解释性说明 ⇒ 仍不可判定 + 人工复核提示，不得判命中/未命中。"""
    mats = [
        _mat(1, "总包合同.pdf", "总承包范围：路基工程。主要材料由承包人负责采购。", role="general"),
        _mat(2, "情况说明.pdf",
             "采购合同复印件见附件。出卖方为承包人。情况说明：相关采购事项另附书面说明备查。",
             role="packet"),
    ]
    f = _find("T15-03", mats)
    assert f.status != STATUS_HIT and f.status != STATUS_MISS
    assert f.status == STATUS_UNDECIDABLE
    assert "人工复核" in f.message
    assert "合理解释" in f.message


def test_t15_03_responsibility_in_sub_only_never_hits():
    """归因错配负例：分包件含「由分包人负责采购」类字样、总包件无责任明文 ⇒ 不得据此命中。"""
    mats = [
        _mat(1, "总包合同.pdf", "总承包范围：路基工程。", role="general"),
        _mat(2, "分包合同.pdf",
             "分包范围：材料采购。主要材料由分包人负责采购，由丙公司采购供应。"
             "采购合同。增值税普通发票。供应商：丙公司。", role="sub"),
    ]
    f = _find("T15-03", mats)
    assert f.status != STATUS_HIT
    assert f.status == STATUS_UNDECIDABLE
    assert "采购租赁责任明文" in f.message       # 缺失侧标签来自目录 required_evidence 映射


# ==================== T15-04 现场管理机构、劳动关系或项目负责人管理 ====================

def _t15_04_hit_mats():
    return [
        _mat(20, "现场与劳动资料.pdf",
             "现场管理机构：项目部。派驻人员由劳务公司派驻。主要管理人员名单附后。"
             "劳动合同由劳务公司与进场人员签订。社会保险缴费记录：统计期间2024年1月至6月，由劳务公司缴纳。"
             "工资由劳务公司发放。项目负责人组织管理由劳务公司负责。",
             role="packet"),
    ]


def test_t15_04_hit_when_six_sides_point_to_third_party():
    """六侧齐全且现场/劳动/社保关系指向其他单位 → 命中，证据锚定真实材料页。"""
    mats = _t15_04_hit_mats()
    f = _find("T15-04", mats)
    assert f.status == STATUS_HIT
    assert f.kind == "transfer_scope"
    assert f.material_id in {m.id for m in mats}
    mat = next(m for m in mats if m.id == f.material_id)
    pages = {p["page"] for p in mat.pages_json}
    assert isinstance(f.page, int) and f.page in pages
    assert f.quote
    assert "命中" in f.message and "人工复核" in f.message


def _t15_04_miss_mats():
    return [
        _mat(21, "现场与劳动资料.pdf",
             "现场管理机构为承包人项目部。派驻人员由承包人派驻。主要管理人员名单附后。"
             "劳动合同与承包人签订。社会保险缴费记录：统计期间2024年1月至2024年6月，由承包人缴纳。"
             "工资由承包人发放。项目负责人由承包人管理。",
             role="packet"),
    ]


def test_t15_04_miss_when_six_sides_point_to_contractor():
    """六侧齐全且现场/劳动/社保关系全部指向承包人自身人员 → 未命中。"""
    f = _find("T15-04", _t15_04_miss_mats())
    assert f.status == STATUS_MISS
    assert "未命中" in f.message and "承包人自身" in f.message


def test_t15_04_undecidable_when_site_and_labor_evidence_missing():
    """缺现场管理机构、缺劳动合同 → 不可判定，缺失侧标签逐字列全，不得降级为未命中。"""
    mats = [
        _mat(22, "人员资料.pdf",
             "派驻人员由劳务公司派驻。主要管理人员名单附后。"
             "社会保险由劳务公司缴纳，统计期间2024年3月至6月。工资由劳务公司发放。"
             "项目负责人由劳务公司管理。",
             role="packet"),
    ]
    f = _find("T15-04", mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "缺" in f.message
    assert "现场管理机构" in f.message
    assert "劳动合同" in f.message


def test_t15_04_social_period_missing_is_undecidable():
    """社保材料在场但无年月区间 ⇒ 不可判定，message 含「缺：社保统计期间」。"""
    mats = [
        _mat(23, "现场与社保资料.pdf",
             "现场管理机构：项目部。派驻人员由承包人派驻。主要管理人员名单附后。"
             "劳动合同与承包人签订。社会保险参保人员名单及工资发放记录。"
             "项目负责人由承包人管理。",
             role="packet"),
    ]
    f = _find("T15-04", mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "缺：社保统计期间" in f.message


def test_t15_04_social_period_present_not_undecidable_by_period():
    """社保材料带合规年月区间 ⇒ 不因统计期间判不可判定（该例按主体指向正常出未命中）。"""
    mats = [
        _mat(24, "现场与社保资料.pdf",
             "现场管理机构：项目部。派驻人员由承包人派驻。主要管理人员名单附后。"
             "劳动合同与承包人签订。社会保险缴费记录：2023年1月至12月。工资由承包人发放。"
             "项目负责人由承包人管理。",
             role="packet"),
    ]
    f = _find("T15-04", mats)
    assert f.status == STATUS_MISS
    assert "社保统计期间" not in f.message


def _t15_04_base_mats(social_text):
    return [
        _mat(30, "现场与劳动资料.pdf",
             "现场管理机构：项目部。派驻人员由承包人派驻。主要管理人员名单附后。"
             "劳动合同与承包人签订。项目负责人由承包人管理。" + social_text,
             role="packet"),
    ]


def test_t15_04_obligation_clause_is_not_social_proof():
    """R3 回归（台账 BS-P1-02）：合同里的社保义务表述不能顶替社保证明材料在场。

    旧实现把「社会保险」「工资支付」等义务条款词当成"社保材料在场"，缺件提示因此变成
    「缺：社保统计期间」——用户会去补"期间"而不是补社保证明。
    """
    mats = _t15_04_base_mats(
        "社会保险由承包人缴纳，统计期间2024年1月至2024年6月。工资支付按合同约定。")
    f = _find("T15-04", mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "授权人员社保证明" in f.message        # 缺件指向材料本身
    assert "社保统计期间" not in f.message        # 不得指向"补期间"
    assert "仅见社保/工资相关表述" in f.message   # 说清"只看到义务表述"


def test_t15_04_wage_table_alone_is_not_social_proof():
    """工资表/工资发放不算社保证明：两者是不同材料，不得互相顶替。"""
    mats = _t15_04_base_mats("工资表：2024年1月至2024年6月工资发放记录齐全。")
    f = _find("T15-04", mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "授权人员社保证明" in f.message
    assert "社保统计期间" not in f.message


def test_t15_04_social_proof_table_header_counts_as_present():
    """社保明细表头（参保人员明细 + 姓名/缴费基数列）也算证明材料在场。

    真实社保截图 OCR 后常常没有「证明」二字，只有表头列名；只认"证明"会漏掉真材料。
    """
    mats = _t15_04_base_mats(
        "参保人员明细：姓名 身份证号 缴费基数 缴费金额 2024年1月至2024年6月。")
    f = _find("T15-04", mats)
    # 六侧齐 + 主体指向承包人自身 ⇒ 未命中；不得因社保侧"缺材料"落到不可判定
    assert f.status == STATUS_MISS
    assert "授权人员社保证明" not in f.message


def test_t15_03_owner_supplied_material_clause_is_not_hit():
    """甲供材回归：总包件含责任明文 + 「发包人供应的材料由发包人负责采购」+ 四侧齐
    ⇒ 不得命中（甲供材是发包人的正常合同权利，不是「承包人把采购租赁交由他人完成」的
    转包线索）；证据不指向他人/自身任一侧 ⇒ 不可判定。"""
    mats = [
        _mat(1, "总包合同.pdf",
             "承包范围：路基工程。主要材料、构配件由承包人负责采购。"
             "发包人供应的材料由发包人负责采购。",
             role="general"),
        _mat(2, "采购凭证.pdf", "采购合同。供应商：丙公司。开具增值税专用发票。", role="packet"),
    ]
    f = _find("T15-03", mats)
    assert f.status != STATUS_HIT
    assert f.status == STATUS_UNDECIDABLE


def test_t15_03_third_party_still_hits_after_owner_removal():
    """真第三方回归（甲供材修复不误杀）：把上游主体换成「材料商」等真实第三方
    ⇒ 四侧齐 + 他人采购明示仍命中（证明移除建设单位/发包人未伤及真第三方判定）。"""
    mats = [
        _mat(1, "总包合同.pdf",
             "承包范围：路基工程。主要材料、构配件由承包人负责采购。"
             "发包人供应的材料由材料商负责采购。",
             role="general"),
        _mat(2, "采购凭证.pdf", "采购合同。供应商：丙公司。开具增值税专用发票。", role="packet"),
    ]
    f = _find("T15-03", mats)
    assert f.status == STATUS_HIT
    assert f.kind == "transfer_scope"
    assert f.material_id in {m.id for m in mats} and isinstance(f.page, int) and f.quote


@pytest.mark.parametrize("upstream", [
    "建设单位", "发包单位", "业主单位", "甲方公司", "发包人", "业主",
])
def test_t15_03_upstream_subject_procurement_never_hits(upstream):
    """上游主体泛匹配修复回归（R02-HOTFIX）：泛正则曾把「由建设单位/发包单位/
    业主单位/甲方公司负责采购」识别为他人采购 ⇒ 甲供材被推成 hit。修复后上游
    主体一律不点亮他人采购侧：责任明文 + 四侧齐 + 仅上游供材表述 ⇒ 不可判定
    （而非命中），并注明「未见采购/租赁主体指向的明示」。"""
    mats = [
        _mat(1, "总包合同.pdf",
             "承包范围：路基工程。主要材料、构配件由承包人负责采购。"
             "主要材料由%s负责采购。" % upstream,
             role="general"),
        _mat(2, "采购凭证.pdf", "采购合同。供应商：丙公司。开具增值税专用发票。", role="packet"),
    ]
    f = _find("T15-03", mats)
    assert f.status != STATUS_HIT
    assert f.status == STATUS_UNDECIDABLE
    assert "未见采购/租赁主体指向的明示" in f.message


def test_t15_03_upstream_mention_does_not_block_genuine_self_miss():
    """上游提及不压制合法未命中：自购自租全链在场时，即使材料另含「由建设单位
    负责采购」的上游供材表述，他人采购侧也不得被点亮 ⇒ 维持未命中结论。"""
    mats = [
        _mat(1, "总包合同.pdf",
             "承包范围：路基工程。主要材料由承包人负责采购，由承包人自行采购并自行租赁设备。"
             "另有甲供材部分由建设单位负责采购。",
             role="general"),
        _mat(2, "采购凭证.pdf",
             "采购合同：主要材料由承包人自行采购。出卖方：承包人。已开具增值税普通发票。",
             role="sub"),
    ]
    f = _find("T15-03", mats)
    assert f.status == STATUS_MISS
    assert "未命中" in f.message and "自购自租" in f.message


def test_t15_03_third_party_still_hits_after_upstream_guard():
    """真第三方保留回归（上游主体排除不误杀）：同句先提上游、再出现真实第三方
    主体（租赁公司/第三方单位）⇒ 他人采购侧仍被点亮，四侧齐即命中。"""
    mats = [
        _mat(1, "总包合同.pdf",
             "承包范围：路基工程。主要材料、构配件由承包人负责采购。",
             role="general"),
        _mat(2, "机械租赁凭证.pdf",
             "经发包人同意，施工机械由租赁公司统一租赁。租赁合同。出租方：丁租赁公司。"
             "开具增值税专用发票。", role="packet"),
    ]
    f = _find("T15-03", mats)
    assert f.status == STATUS_HIT
    assert f.kind == "transfer_scope"
    assert f.material_id in {m.id for m in mats} and isinstance(f.page, int) and f.quote


def test_t15_04_conflicting_directions_is_undecidable():
    """指向冲突分支：六侧齐 + 社保期间在场，但同一材料集同时出现「社保由劳务公司缴纳」
    （指向他方）与「社保由承包人缴纳」（指向自身）⇒ 不可判定，message 点明主体指向冲突。"""
    mats = [
        _mat(25, "现场与社保资料.pdf",
             "现场管理机构：项目部。派驻人员由承包人派驻。主要管理人员名单附后。"
             "劳动合同与承包人签订。社会保险缴费记录：统计期间2024年1月至6月，由劳务公司缴纳。"
             "工资由承包人发放。项目负责人由承包人管理。社保由承包人缴纳。",
             role="packet"),
    ]
    f = _find("T15-04", mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "主体指向冲突" in f.message


def test_t15_03_explanation_does_not_substitute_invoice_for_hit():
    """「解释顶替发票」回归：责任明文 + 他人采购明示 + 供应商主体 + 情况说明在场，
    但无任何发票字样 ⇒ 不得命中（解释不能顶替发票），应不可判定且 message 含
    「人工复核」与「合理解释」提示。"""
    mats = [
        _mat(1, "总包合同.pdf", "总承包范围：路基工程。主要材料由承包人负责采购。", role="general"),
        _mat(2, "供货合同及说明.pdf",
             "供货合同：由材料商负责采购。出卖方：恒泰公司。情况说明：相关事项另行书面说明。",
             role="packet"),
    ]
    f = _find("T15-03", mats)
    assert f.status != STATUS_HIT
    assert f.status == STATUS_UNDECIDABLE
    assert "人工复核" in f.message
    assert "合理解释" in f.message


# ==================== 措辞红线 & 返回值形状 & R01 兼容 ====================

def test_materials_messages_never_use_banned_wordings():
    """全局禁词：所有 finding 的 message/source 拼接不得含「已认定」「挂靠」「证在人不在」。"""
    bundles = [
        _t15_03_hit_mats(),
        _t15_03_miss_mats(),
        [_mat(1, "总包合同.pdf", "总承包范围：路基工程。主要材料由承包人负责采购。", role="general")],
        _t15_04_hit_mats(),
        _t15_04_miss_mats(),
        [],
    ]
    for mats in bundles:
        findings = _run(mats)
        assert len(findings) == 2, "内核须恰好返回 2 条 finding"
        text = _all_text(findings)
        assert "已认定" not in text
        assert "挂靠" not in text
        assert "证在人不在" not in text


def test_transfer_materials_returns_two_findings_shape():
    """返回值形状锁：恰好 2 条、item_id 顺序固定 T15-03 → T15-04、kind=transfer_scope；
    空材料列表不抛异常，返回 2 条不可判定且缺失项列全。"""
    findings = _run(_t15_03_hit_mats())
    assert len(findings) == 2
    assert [f.item_id for f in findings] == ["T15-03", "T15-04"]
    assert {f.kind for f in findings} == {"transfer_scope"}

    empty = _run([])
    assert [f.item_id for f in empty] == ["T15-03", "T15-04"]
    assert all(f.status == STATUS_UNDECIDABLE for f in empty)
    assert "缺" in empty[0].message and "供应商主体" in empty[0].message
    assert "缺" in empty[1].message
    assert "现场管理机构" in empty[1].message
    assert "项目负责人组织管理" in empty[1].message


def test_r01_transfer_scope_check_behavior_unchanged():
    """R01 兼容门：T15-01/T15-02 的规则 ID、kind、三态语义与证据字段未变（形状直调断言）。"""
    mats = [
        _mat(1, "总包合同.pdf",
             "总承包范围：路基、路面、桥梁工程。发包人：甲公司；承包人：乙公司。"
             "承包人将全部工程交由丙公司施工。", role="general"),
        _mat(10, "分包合同.pdf", "分包范围：路基、路面、桥梁工程。", role="sub"),
    ]
    findings = a1_check.run_transfer_scope_check(_task(), mats)
    assert [f.item_id for f in findings] == ["T15-01", "T15-02"]
    assert {f.kind for f in findings} == {"transfer_scope"}
    assert findings[0].status == STATUS_HIT
    assert findings[0].material_id == 1 and isinstance(findings[0].page, int)
    assert findings[0].quote
