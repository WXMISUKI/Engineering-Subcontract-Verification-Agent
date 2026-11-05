# -*- coding: utf-8 -*-
"""blockchain_supervision A1-RULE-03：T15-05 至 T15-09 主体关系与资金流 定向契约测试。

判定纪律锚点（A1-RULE-03 任务包 / A1-03 总计划 / R03-HOTFIX）：
- T15-05 侧在场（劳务主体/范围/价款/管理费）只作缺项判定；命中=关系级联合模式双亮：
  同一语句内「劳务主体↔全部工程」+「劳务主体↔全部工程价款」施受关系成立
  （R03-HOTFIX H1：各侧关键词各自为政不得 hit）；「按全部工程价款计取」类句式不得
  点亮范围侧（「全部工程」与「全部工程价款」是子串关系——任务包点名的回归坑）；
- T15-06 合作/联营/个人承包关键词绝不能单独命中：仅关键词在场必须不可判定；
  全部工程转移=「全部工程」+转移动词邻接，禁「全部工程款」子串误亮；
- T15-07 依赖占位：需先取得 W16/V16（违法分包排除）核查结果，恒不可判定；
- T15-08 资金流统计期间独立识别（不与 T15-04 社保期间共用字段），且只在与转拨/收付
  证据同一材料内检索、「其他项目」分句不计（R03-HOTFIX H2）；资金流材料明示表格
  不完整 → 不可判定列缺失声明；否定前缀下的转拨命中作废不作肯定证据（H3）；
  缺期间/缺表/主体无法对应一律不可判定；payment_relationship=收付维度说明在场而非
  「存在正常收付」；
- T15-09 联合体协议、分工、不施工不管理明示、管理费四类证据缺一不可，且不施工/
  不管理/管理费三证必须同分句齐备（R03-HOTFIX H1：跨分句不得拼接为同一主体）；
- 多证据 finding：主证据锚 material_id/page/quote，关联证据写进 message，
  每条 hit 至少 1 主 + 1 关联；
- 不得出现「已认定」「挂靠风险」「证在人不在」等措辞，命中只陈述线索语气。

本文件只直接调用纯内核 `a1_check.run_transfer_relations_check`（不接入 service 编排，
接线见 R05）；目录数据断言一律走公共 `rules.load_transfer_rules()`（不跨模块调私有符号）。
"""
from types import SimpleNamespace

import pytest

from app.plus.blockchain_supervision import a1_check
from app.plus.blockchain_supervision import rules
from app.plus.blockchain_supervision.models import (
    STATUS_HIT, STATUS_MISS, STATUS_UNDECIDABLE,
)

pytestmark = pytest.mark.integration


# ==================== 夹具（与 test_bs_a1_transfer_materials.py 同形态） ====================

def _mat(mid, name, text="", role="packet"):
    pages = [{"page": 1, "text": text}] if text else []
    return SimpleNamespace(id=mid, file_name=name, role=role, parse_state="done",
                           pages_json=pages)


def _task(**kw):
    kw.setdefault("id", 1)
    return SimpleNamespace(**kw)


def _run(materials, task=None):
    return a1_check.run_transfer_relations_check(task or _task(), materials)


def _find(item_id, materials, task=None):
    return next(f for f in _run(materials, task) if f.item_id == item_id)


def _all_text(findings):
    return " ".join("%s %s" % (f.message or "", f.source or "") for f in findings)


def _anchor_ok(f, mats):
    """证据坐标契约：material_id 指向真实传入材料、page 为该材料真实页、引文非空。"""
    assert f.material_id in {m.id for m in mats}
    mat = next(m for m in mats if m.id == f.material_id)
    pages = {p["page"] for p in mat.pages_json}
    assert isinstance(f.page, int) and f.page in pages
    assert f.quote


# ==================== 返回值形状（恰好 5 条，顺序固定） ====================

def test_relations_returns_five_findings_shape():
    """形状锁：恰好 5 条、item_id 顺序固定 T15-05..T15-09、kind 均为 transfer_scope。"""
    mats = [
        _mat(1, "总包合同.pdf", "总承包范围：路基工程。", role="general"),
        _mat(2, "劳务合同.pdf", "劳务作业范围：钢筋绑扎。", role="labor"),
    ]
    findings = _run(mats)
    assert len(findings) == 5
    assert [f.item_id for f in findings] == \
        ["T15-05", "T15-06", "T15-07", "T15-08", "T15-09"]
    assert {f.kind for f in findings} == {"transfer_scope"}


def test_relations_empty_materials_all_undecidable_with_missing_sides():
    """空材料列表不抛异常：5 条全部不可判定，缺失项列全（不静默判通过/未命中）。"""
    findings = _run([])
    assert [f.item_id for f in findings] == \
        ["T15-05", "T15-06", "T15-07", "T15-08", "T15-09"]
    assert all(f.status == STATUS_UNDECIDABLE for f in findings)
    assert "缺" in findings[0].message and "劳务企业承包范围" in findings[0].message
    assert "缺" in findings[1].message and "合作/联营/个人承包形式" in findings[1].message
    assert "尚不能自动" in findings[2].message
    assert "缺" in findings[3].message and "资金流表" in findings[3].message
    assert "缺" in findings[4].message and "联合体协议" in findings[4].message


# ==================== T15-05 劳务企业承包全部工程并计取全部工程价款 ====================

def _t15_05_hit_mats():
    return [
        _mat(1, "总包合同.pdf",
             "总承包范围：路基工程。承包人将全部工程发包给某劳务公司。",
             role="general"),
        _mat(2, "劳务合同.pdf",
             "劳务企业计取全部工程价款，另按2%计取管理费。",
             role="labor"),
    ]


def test_t15_05_hit_with_four_aligned_sides():
    """劳务主体 + 全部工程范围明示 + 管理费 + 全部价款计取，四侧齐且同向 → 命中。"""
    mats = _t15_05_hit_mats()
    f = _find("T15-05", mats)
    assert f.status == STATUS_HIT
    assert f.kind == "transfer_scope"
    _anchor_ok(f, mats)
    assert "命中" in f.message and "人工复核" in f.message
    assert "关联证据：" in f.message          # 多证据 finding：主 + 关联


def test_t15_05_whole_payment_substring_never_lights_scope_side():
    """子串回归（任务包点名的坑）：「按全部工程价款计取」不得点亮范围侧——
    范围侧词表尾排除后，该句式只能留在价款侧，范围侧仍列缺。"""
    mats = [
        _mat(1, "总包合同.pdf",
             "总承包范围：路基工程。劳务公司按全部工程价款计取报酬。",
             role="general"),
    ]
    f = _find("T15-05", mats)
    assert f.status != STATUS_HIT
    assert f.status == STATUS_UNDECIDABLE
    assert "缺：全部工程范围明示" in f.message
    assert f.status != STATUS_MISS


def test_t15_05_fee_percentage_clause_never_lights_payment_side():
    """探针回归（对抗探测 FAIL 项）：「管理费按全部工程价款的3%计取」里被计取的
    宾语是管理费而非价款——payment 侧不得点亮（裸词「全部工程价款」已删除，
    只认施受结构邻接），四侧不齐 → 不可判定，不得 hit。"""
    mats = [
        _mat(1, "总包合同.pdf",
             "总承包范围：路基工程。承包人将全部工程发包给某劳务公司。",
             role="general"),
        _mat(2, "劳务合同.pdf", "管理费按全部工程价款的3%计取。", role="labor"),
    ]
    f = _find("T15-05", mats)
    assert f.status != STATUS_HIT
    assert f.status == STATUS_UNDECIDABLE
    assert "缺：全部工程价款计取" in f.message


def test_t15_05_labor_fee_settled_by_whole_price_not_hit():
    """探针回归同型：「劳务报酬按全部工程价款结算」被结算宾语是劳务报酬，
    payment 侧不亮 → 不得 hit。"""
    mats = [
        _mat(1, "总包合同.pdf",
             "总承包范围：路基工程。承包人将全部工程发包给某劳务公司。",
             role="general"),
        _mat(2, "劳务合同.pdf", "劳务报酬按全部工程价款结算。", role="labor"),
    ]
    f = _find("T15-05", mats)
    assert f.status != STATUS_HIT
    assert f.status == STATUS_UNDECIDABLE
    assert "全部工程价款计取" in f.message       # payment 侧按缺失列出（本例亦缺管理费约定）


def test_t15_05_payment_recipient_form_still_hits():
    """受事形态正向回归：「全部工程价款由劳务企业统一计取」→ payment 侧亮，
    四侧齐 → 仍命中（修复不误杀真阳性）。"""
    mats = [
        _mat(1, "总包合同.pdf",
             "总承包范围：路基工程。承包人将全部工程发包给某劳务公司。",
             role="general"),
        _mat(2, "劳务合同.pdf",
             "全部工程价款由劳务企业统一计取，另按2%计取管理费。",
             role="labor"),
    ]
    f = _find("T15-05", mats)
    assert f.status == STATUS_HIT
    assert "人工复核" in f.message and "关联证据：" in f.message


def test_t15_05_payment_verb_object_form_still_hits():
    """动词前缀形态正向回归：「劳务企业计取全部工程价款」→ payment 侧亮，
    四侧齐 → 仍命中。"""
    mats = [
        _mat(1, "总包合同.pdf",
             "总承包范围：路基工程。承包人将全部工程发包给某劳务公司。",
             role="general"),
        _mat(2, "劳务合同.pdf",
             "劳务企业计取全部工程价款，另按2%计取管理费。",
             role="labor"),
    ]
    f = _find("T15-05", mats)
    assert f.status == STATUS_HIT
    assert "人工复核" in f.message and "关联证据：" in f.message


def test_t15_05_true_hit_with_labor_whole_scope_relation():
    """R03-HOTFIX 正向回归：关系模式受事形态真 hit 必须保持——「全部工程由劳务公司
    承包」+「全部工程价款由劳务企业统一计取」+「管理费按约定计取」→ 命中（防过度收紧）。"""
    mats = [
        _mat(1, "总包合同.pdf", "全部工程由劳务公司承包。", role="general"),
        _mat(2, "劳务合同.pdf", "全部工程价款由劳务企业统一计取。管理费按约定计取。",
             role="labor"),
    ]
    f = _find("T15-05", mats)
    assert f.status == STATUS_HIT
    assert "人工复核" in f.message and "关联证据：" in f.message


def test_t15_05_cross_subject_clauses_never_hit():
    """R03-HOTFIX 反例一：general「总包承担全部工程。甲劳务公司负责钢筋绑扎。」+
    packet「总包收取全部工程价款，并向业主支付管理费。」——裸劳务主体词、裸「收取
    全部工程价款」句式各自为政，未在同一语句建立劳务主体↔全部工程/价款的施受关系，
    不得 hit → 不可判定。"""
    mats = [
        _mat(1, "总包合同.pdf", "总包承担全部工程。甲劳务公司负责钢筋绑扎。",
             role="general"),
        _mat(2, "报审包.pdf", "总包收取全部工程价款，并向业主支付管理费。", role="packet"),
    ]
    f = _find("T15-05", mats)
    assert f.status != STATUS_HIT
    assert f.status == STATUS_UNDECIDABLE
    assert "施受关系" in f.message


def test_t15_05_miss_with_scope_negation():
    """范围否定明示（仅承担劳务作业）+ 无全部工程范围明示 + 劳务语境在场 → 未命中。"""
    mats = [
        _mat(1, "总包合同.pdf",
             "总承包范围：路基工程。劳务公司仅承担路基劳务作业。",
             role="general"),
    ]
    f = _find("T15-05", mats)
    assert f.status == STATUS_MISS
    assert "未命中" in f.message


def test_t15_05_undecidable_lists_missing_sides():
    """缺劳务主体/范围明示/价款计取 → 不可判定，缺失侧按 required_evidence 映射逐字列全。"""
    mats = [
        _mat(1, "总包合同.pdf", "总承包范围：路基工程。", role="general"),
        _mat(2, "劳务合同.pdf", "管理费按比例计取。", role="labor"),
    ]
    f = _find("T15-05", mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "缺：劳务企业承包范围、全部工程范围明示、全部工程价款计取" in f.message


def test_t15_05_scope_conflict_is_undecidable():
    """范围明示与范围否定同时在场 → 不可判定（指向冲突），不得二选一。"""
    mats = [
        _mat(1, "总包合同.pdf",
             "总承包范围：路基工程。承包人将全部工程发包给某劳务公司。"
             "劳务公司仅承担路基劳务作业。",
             role="general"),
        _mat(2, "劳务合同.pdf",
             "劳务企业计取全部工程价款，另按2%计取管理费。",
             role="labor"),
    ]
    f = _find("T15-05", mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "指向冲突" in f.message


# ==================== T15-06 合作联营个人承包等形式转移全部工程 ====================

def _t15_06_hit_mats():
    return [
        _mat(1, "总包合同.pdf",
             "甲方与乙方采取联合经营方式合作。全部工程整体转由丙公司施工。"
             "实际施工由丙公司完成。施工组织管理由丙公司负责。",
             role="general"),
    ]


def test_t15_06_hit_with_transfer_builder_and_management():
    """合作形式 + 全部工程转移明文 + 实际施工主体 + 施工组织管理责任 → 命中。"""
    mats = _t15_06_hit_mats()
    f = _find("T15-06", mats)
    assert f.status == STATUS_HIT
    assert f.kind == "transfer_scope"
    _anchor_ok(f, mats)
    assert "命中" in f.message and "人工复核" in f.message
    assert "关联证据：" in f.message


def test_t15_06_cooperation_keyword_alone_never_hits():
    """关键词红线：仅「合作/联营」字样在场必须不可判定，绝不能单独命中。"""
    mats = [_mat(1, "总包合同.pdf", "双方开展合作。", role="general")]
    f = _find("T15-06", mats)
    assert f.status != STATUS_HIT
    assert f.status == STATUS_UNDECIDABLE
    assert "不能单独" in f.message


def test_t15_06_whole_payment_wording_is_not_transfer():
    """子串回归：「全部工程款转由…」中「全部工程」后紧邻「款」，转移侧不得点亮
    ⇒ 仅合作字样在场 → 不可判定（而非命中）。"""
    mats = [
        _mat(1, "总包合同.pdf",
             "双方开展合作。全部工程款转由监理代付。",
             role="general"),
    ]
    f = _find("T15-06", mats)
    assert f.status != STATUS_HIT
    assert f.status == STATUS_UNDECIDABLE
    assert "全部工程交付关系" in f.message        # 转移侧按缺失侧标签列出


def test_t15_06_miss_with_transfer_negation():
    """合作形式在场 + 转移否定明示 + 无转移/实际施工明示 → 未命中。"""
    mats = [
        _mat(1, "总包合同.pdf",
             "双方为联营合作关系。不转移全部工程，各自承担各自范围。",
             role="general"),
    ]
    f = _find("T15-06", mats)
    assert f.status == STATUS_MISS
    assert "未命中" in f.message


def test_t15_06_coop_with_transfer_but_no_builder_is_undecidable():
    """合作形式 + 转移明文在场但缺实际施工主体/管理责任 → 不可判定，缺失侧列全。"""
    mats = [
        _mat(1, "总包合同.pdf",
             "双方开展合作。全部工程整体转由丙公司施工。",
             role="general"),
    ]
    f = _find("T15-06", mats)
    assert f.status != STATUS_HIT
    assert f.status == STATUS_UNDECIDABLE
    assert "实际施工主体明示" in f.message
    assert "施工组织管理责任明示" in f.message


# ==================== T15-07 依赖占位（恒不可判定） ====================

def test_t15_07_always_undecidable_dependency_placeholder():
    """T15-07 尚不支持自动判断，界面明确限制和需要人工核对的内容。"""
    for mats in ([], [_mat(1, "分包合同.pdf", "分包范围：路基工程。", role="sub")]):
        f = _find("T15-07", mats)
        assert f.status == STATUS_UNDECIDABLE
        assert f.kind == "transfer_scope"
        assert "尚不能自动" in f.message
        assert "人工" in f.message
        assert "R05" not in f.message
        assert f.material_id is None


def test_t15_07_dependency_read_from_catalog():
    """依赖关系读目录 depends_on 字段（规则=数据），不在内核硬编码；
    断言走公共 rules.load_transfer_rules()（H4：不跨模块调私有符号）。"""
    rule = next(r for r in rules.load_transfer_rules()["rules"]
                if r["id"] == "T15-07")
    assert rule["depends_on"] == ["W16", "V16"]


# ==================== T15-08 工程款无收付关系或扣费后全部转拨 ====================

def _t15_08_hit_mats():
    return [
        _mat(1, "资金往来表.pdf",
             "资金往来表：统计期间2024年1月至6月。付款方：承包人。收款方：分包单位。"
             "扣除管理费后全额转付分包单位。",
             role="packet"),
    ]


def test_t15_08_hit_pass_through_with_period_and_subjects():
    """资金流表 + 期间齐 + 主体可对应 + 扣费转拨明示 → 命中。"""
    mats = _t15_08_hit_mats()
    f = _find("T15-08", mats)
    assert f.status == STATUS_HIT
    assert f.kind == "transfer_scope"
    _anchor_ok(f, mats)
    assert "命中" in f.message and "人工复核" in f.message
    assert "关联证据：" in f.message
    assert "资金流统计期间" in f.message        # 关联证据引用含期间侧


def test_t15_08_miss_when_subcontractor_receives_directly():
    """资金流表+期间+主体对应齐 + 分包方收款记录明示 + 无转拨明示 → 未命中。"""
    mats = [
        _mat(1, "资金往来表.pdf",
             "资金往来表：统计期间2024年1月至12月。付款方：承包人。收款方：分包单位。"
             "分包单位收到工程款。",
             role="packet"),
    ]
    f = _find("T15-08", mats)
    assert f.status == STATUS_MISS
    assert "未命中" in f.message


def test_t15_08_missing_period_is_undecidable():
    """资金流材料在场但未识别到统计期间 → 不可判定，message 含「缺：资金流统计期间」。"""
    mats = [
        _mat(1, "资金往来表.pdf",
             "资金往来表。付款方：承包人。收款方：分包单位。扣除管理费后全额转付。",
             role="packet"),
    ]
    f = _find("T15-08", mats)
    assert f.status != STATUS_HIT and f.status != STATUS_MISS
    assert f.status == STATUS_UNDECIDABLE
    assert "缺：资金流统计期间" in f.message


def test_t15_08_subject_mismatch_is_undecidable():
    """表材料缺承包人侧或分包侧主体字样 → 不可判定「主体无法对应」。"""
    mats = [
        _mat(1, "资金往来表.pdf",
             "资金往来表：统计期间2024年1月至6月。扣除管理费后全额转付。",
             role="packet"),
    ]
    f = _find("T15-08", mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "主体无法对应" in f.message


def test_t15_08_no_flow_statement_is_undecidable():
    """未上传资金流表属证据缺口（任务仍可运行）→ 不可判定并列「缺：资金流表」。"""
    mats = [
        _mat(1, "费用报销单.pdf", "费用报销单：管理费按约定计取。", role="packet"),
    ]
    f = _find("T15-08", mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "证据缺口" in f.message
    assert "缺：资金流表" in f.message


def test_t15_08_payment_relationship_is_dimension_not_normal_flow():
    """payment_relationship 侧语义=收付维度说明在场（收款方/付款方），
    而非「存在正常收付」——无收付关系情形（未发生收付明示）必须能到达命中。"""
    mats = [
        _mat(1, "资金往来表.pdf",
             "资金往来表：统计期间2024年3月至6月。收付关系：未发生收付，"
             "无收付关系记录。",
             role="packet"),
    ]
    f = _find("T15-08", mats)
    assert f.status == STATUS_UNDECIDABLE          # 主体字样缺失 → 主体无法对应，不命中
    assert "主体无法对应" in f.message
    # 补齐主体字样后，「未发生收付/无收付关系」明示应能命中
    mats2 = [
        _mat(1, "资金往来表.pdf",
             "资金往来表：统计期间2024年3月至6月。付款方：承包人。收款方：分包单位。"
             "未发生收付，无收付关系记录。",
             role="packet"),
    ]
    f2 = _find("T15-08", mats2)
    assert f2.status == STATUS_HIT


def test_t15_08_incomplete_table_declaration_is_undecidable():
    """R03-HOTFIX 反例三：资金流材料明示「表格不完整，缺收款金额和交易明细」——
    表内缺失声明在场时不能证明收付与转拨关系 → 不可判定并列识别到的缺失声明。"""
    mats = [
        _mat(1, "资金往来表.pdf",
             "资金往来表：统计期间2024年1月至6月。付款方：承包人。收款方：分包单位。"
             "扣除管理费后全额转付。表格不完整，缺收款金额和交易明细。",
             role="packet"),
    ]
    f = _find("T15-08", mats)
    assert f.status != STATUS_HIT and f.status != STATUS_MISS
    assert f.status == STATUS_UNDECIDABLE
    assert "表格不完整" in f.message
    assert "缺收款金额" in f.message


def test_t15_08_period_not_stitched_from_unrelated_material():
    """R03-HOTFIX 反例四：材料一有转拨无期间，材料二的期间属于「其他项目」——
    期间只在与转拨/收付证据同一材料内检索，无关材料不得跨材料补期间 → 不可判定。"""
    mats = [
        _mat(1, "资金往来表一.pdf",
             "资金往来表。付款方：承包人。收款方：分包单位。扣除管理费后全额转付。",
             role="packet"),
        _mat(2, "资金往来表二.pdf", "资金往来表：其他项目统计期间2024年1月至6月。",
             role="packet"),
    ]
    f = _find("T15-08", mats)
    assert f.status != STATUS_HIT and f.status != STATUS_MISS
    assert f.status == STATUS_UNDECIDABLE
    assert "缺：资金流统计期间" in f.message


def test_t15_08_period_clause_marked_other_project_excluded():
    """R03-HOTFIX H2 纵深防御：即使期间与转拨同材料，含「其他项目」声明的期间分句
    也不计 → 缺期间不可判定。"""
    mats = [
        _mat(1, "资金往来表.pdf",
             "资金往来表：其他项目统计期间2024年1月至6月。付款方：承包人。"
             "收款方：分包单位。扣除管理费后全额转付。",
             role="packet"),
    ]
    f = _find("T15-08", mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "缺：资金流统计期间" in f.message


def test_t15_08_negated_pass_through_substring_not_positive():
    """R03-HOTFIX 反例五：「未扣除管理费后全额转付」——否定前缀下的转拨命中（含其
    子串「全额转付」）作废，不作为肯定证据；分包方收款明示在场 → 未命中（正常收付）。"""
    mats = [
        _mat(1, "资金往来表.pdf",
             "资金往来表：统计期间2024年1月至6月。付款方：承包人。收款方：分包单位。"
             "未扣除管理费后全额转付；分包单位收到工程款。",
             role="packet"),
    ]
    f = _find("T15-08", mats)
    assert f.status == STATUS_MISS
    assert "未命中" in f.message


def test_t15_08_negated_pass_through_without_payee_is_undecidable():
    """R03-HOTFIX H3：否定转拨作废后无肯定证据、也无分包收款明示 → 转拨极性不明，
    不可判定。"""
    mats = [
        _mat(1, "资金往来表.pdf",
             "资金往来表：统计期间2024年1月至6月。付款方：承包人。收款方：分包单位。"
             "未扣除管理费后全额转付。",
             role="packet"),
    ]
    f = _find("T15-08", mats)
    assert f.status != STATUS_HIT and f.status != STATUS_MISS
    assert f.status == STATUS_UNDECIDABLE
    assert "极性不明" in f.message


# ==================== T15-09 联合体一方不施工不管理并收取管理费 ====================

def _t15_09_hit_mats():
    return [
        _mat(1, "联合体协议.pdf",
             "联合体协议：甲方与乙方分工约定如下。乙方不承担施工任务，不参与现场管理，"
             "仅收取管理费。",
             role="packet"),
    ]


def test_t15_09_hit_with_no_construction_no_management_fee_only():
    """协议 + 分工 + 不施工不管理明示 + 管理费在场 → 命中（四类证据缺一不可）。"""
    mats = _t15_09_hit_mats()
    f = _find("T15-09", mats)
    assert f.status == STATUS_HIT
    assert f.kind == "transfer_scope"
    _anchor_ok(f, mats)
    assert "命中" in f.message and "人工复核" in f.message
    assert "关联证据：" in f.message


def test_t15_09_miss_when_all_parties_actually_work():
    """协议 + 分工在场 + 各方实际承担施工/管理明示 + 无仅收取管理费明示 → 未命中。"""
    mats = [
        _mat(1, "联合体协议.pdf",
             "联合体协议约定双方分工。各方共同承担施工任务，均参与现场管理。",
             role="packet"),
    ]
    f = _find("T15-09", mats)
    assert f.status == STATUS_MISS
    assert "未命中" in f.message


def test_t15_09_undecidable_without_core_plaintext():
    """仅有协议与分工、无不施工不管理明示与管理费 → 不可判定，缺失侧列全。"""
    mats = [
        _mat(1, "联合体协议.pdf",
             "联合体协议：双方分工如下，收益按比例分配。",
             role="packet"),
    ]
    f = _find("T15-09", mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "缺：实际施工归属明示、实际管理归属明示、管理费约定" in f.message


def test_t15_09_cross_clause_subjects_never_hit():
    """R03-HOTFIX 反例二：甲公司不承担施工、乙公司不参与管理并收取管理费——不施工/
    不管理/管理费三证未见于同一分句，跨分句不得拼接为同一主体 → 不可判定。"""
    mats = [
        _mat(1, "联合体协议.pdf",
             "联合体协议：联合体分工如下。甲公司不承担施工，负责现场管理；"
             "乙公司负责施工，不参与管理并收取管理费。",
             role="packet"),
    ]
    f = _find("T15-09", mats)
    assert f.status != STATUS_HIT
    assert f.status == STATUS_UNDECIDABLE
    assert "同一分句" in f.message


def test_t15_09_direction_conflict_is_undecidable():
    """不施工明示与各方实际承担施工明示同时在场 → 不可判定（指向冲突）。"""
    mats = [
        _mat(1, "联合体协议.pdf",
             "联合体协议约定双方分工。乙方不承担施工任务。各方共同承担施工任务。",
             role="packet"),
    ]
    f = _find("T15-09", mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "指向冲突" in f.message


# ==================== 多证据 finding & 措辞红线 & 兼容 ====================

def test_relations_hit_messages_carry_related_evidence():
    """每条 hit 至少 1 主 + 1 关联：关联证据以《文件名》第N页（侧标签）写进 message。"""
    hit_bundles = {
        "T15-05": _t15_05_hit_mats(),
        "T15-06": _t15_06_hit_mats(),
        "T15-08": _t15_08_hit_mats(),
        "T15-09": _t15_09_hit_mats(),
    }
    for item_id, mats in hit_bundles.items():
        f = _find(item_id, mats)
        assert f.status == STATUS_HIT, item_id
        assert "关联证据：《" in f.message, (
            "%s 命中 finding 必须携带关联证据引用" % item_id)
        assert "》第1页（" in f.message
        assert f.material_id in {m.id for m in mats}


def test_relations_hit_evidence_anchored_to_real_materials():
    """证据断言到 material_id/page/quote：主证据锚指向真实传入材料页且引文非空。"""
    for item_id, mats in (("T15-05", _t15_05_hit_mats()),
                          ("T15-06", _t15_06_hit_mats()),
                          ("T15-08", _t15_08_hit_mats()),
                          ("T15-09", _t15_09_hit_mats())):
        f = _find(item_id, mats)
        _anchor_ok(f, mats)


def test_relations_messages_never_use_banned_wordings():
    """全局禁词：所有 finding 的 message/source 拼接不得含「已认定」「挂靠」「证在人不在」。"""
    bundles = [
        _t15_05_hit_mats(),
        [_mat(1, "总包合同.pdf", "总承包范围：路基工程。", role="general")],
        _t15_06_hit_mats(),
        [_mat(1, "总包合同.pdf", "双方开展合作。", role="general")],
        _t15_08_hit_mats(),
        _t15_09_hit_mats(),
        [],
    ]
    for mats in bundles:
        findings = _run(mats)
        assert len(findings) == 5, "内核须恰好返回 5 条 finding"
        text = _all_text(findings)
        assert "已认定" not in text
        assert "挂靠" not in text
        assert "证在人不在" not in text


def test_r01_r02_transfer_kernels_behavior_unchanged():
    """R01/R02 兼容门（形状直调断言）：T15-01..T15-04 内核返回形状与三态语义未变。"""
    mats = [
        _mat(1, "总包合同.pdf",
             "总承包范围：路基、路面、桥梁工程。发包人：甲公司；承包人：乙公司。"
             "承包人将全部工程交由丙公司施工。", role="general"),
        _mat(10, "分包合同.pdf", "分包范围：路基、路面、桥梁工程。", role="sub"),
    ]
    scope = a1_check.run_transfer_scope_check(_task(), mats)
    assert [f.item_id for f in scope] == ["T15-01", "T15-02"]
    assert scope[0].status == STATUS_HIT

    mats2 = [
        _mat(1, "总包合同.pdf",
             "总承包范围：路基工程。主要材料由承包人负责采购。", role="general"),
        _mat(2, "采购凭证.pdf",
             "采购合同：主要材料由丙公司采购供应。增值税专用发票已开具。供应商：丙公司。",
             role="sub"),
    ]
    materials_findings = a1_check.run_transfer_materials_check(_task(), mats2)
    assert [f.item_id for f in materials_findings] == ["T15-03", "T15-04"]
    assert materials_findings[0].status == STATUS_HIT
