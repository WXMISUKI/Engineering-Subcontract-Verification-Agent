# -*- coding: utf-8 -*-
"""blockchain_supervision A1-RULE-01：T15-01/T15-02 全部工程与肢解发包 定向契约测试。

判定纪律锚点（A1-03-第十五至十七条任务包 §1 / A1-RULE-01 任务包）：
- T15-01 仅当「同一子句内范围词 × 交付动词」的完整转移明文成立、且四侧证据齐全才可命中；
  范围相似度只能作为提议，不能直接命中（只降级为不可判定 + 人工提示）；
- T15-02 需总包范围 + 至少 2 份分包范围 + 标段锚点 + 明文肢解/分别发包模式；仅多份分包合同
  不能证明肢解；
- 范围缺失、主体不清或无法证明覆盖全部工程时输出不可判定并列明缺项；
- 明确由总包保留施工组织且分包仅承担部分范围时可未命中；
- 不得出现「已认定转包/违法分包」等定性结论，命中只陈述线索语气。

本文件只直接调用纯内核 `a1_check.run_transfer_scope_check`（不接入 service 编排，接线见 R05）。
"""
from types import SimpleNamespace

import pytest

from app.plus.blockchain_supervision import a1_check
from app.plus.blockchain_supervision.ai_scope_selection import SCOPE_SELECTOR_ENV
from app.plus.blockchain_supervision.models import (
    STATUS_HIT, STATUS_MISS, STATUS_UNDECIDABLE,
)

pytestmark = pytest.mark.integration


# ==================== 夹具（与 test_bs_a1_kernels.py 同形态） ====================

def _mat(mid, name, text="", role="sub"):
    pages = [{"page": 1, "text": text}] if text else []
    return SimpleNamespace(id=mid, file_name=name, role=role, parse_state="done",
                           pages_json=pages)


def _task(**kw):
    kw.setdefault("id", 1)
    return SimpleNamespace(**kw)


def _find(item_id, materials, task=None):
    findings = a1_check.run_transfer_scope_check(task or _task(), materials)
    return next(f for f in findings if f.item_id == item_id)


def _all_text(findings):
    return " ".join("%s %s" % (f.message or "", f.source or "") for f in findings)


def test_transfer_scope_does_not_ignore_unread_labor_contract():
    materials = _t15_01_miss_mats()
    labor = _mat(99, "劳务分包合同.pdf", "分包范围：劳务作业。", role="labor")
    labor.degradations_json = [{"kind": "parse_incomplete", "unread_pages": [2]}]
    materials.append(labor)
    finding = _find("T15-01", materials)
    assert finding.status == STATUS_UNDECIDABLE
    assert "第2页" in finding.message
    assert "劳务分包合同.pdf" in finding.message


def test_labor_scope_is_a_locatable_clue_not_a_conclusive_engineering_scope():
    labor = _mat(10, "劳务合同.pdf", "分包范围：预制梁劳务作业。", role="labor")
    labor.pages_json = [{"page": 1, "text": "分包工程合同"},
                        {"page": 2, "text": "分包范围：预制梁劳务作业。"}]
    for finding in a1_check.run_transfer_scope_check(_task(), [labor]):
        assert finding.status == STATUS_UNDECIDABLE
        assert finding.material_id == labor.id and finding.page == 2
        assert finding.quote and "分包范围" in finding.quote
        assert "劳务" in finding.message and "核对" in finding.message


def test_labor_scope_clue_does_not_guess_between_contracts_or_use_unread_page():
    first = _mat(10, "劳务一.pdf", "分包范围：路基作业。", role="labor")
    second = _mat(11, "劳务二.pdf", "分包范围：路面作业。", role="labor")
    assert all(f.material_id is None for f in a1_check.run_transfer_scope_check(_task(), [first, second]))
    first.degradations_json = [{"kind": "parse_incomplete", "unread_pages": [1]}]
    assert all(f.material_id is None for f in a1_check.run_transfer_scope_check(_task(), [first]))


def test_labor_project_name_is_not_treated_as_scope_evidence():
    labor = _mat(10, "劳务合同.pdf", "分包工程名称：预制板梁工程。", role="labor")
    assert all(f.material_id is None and f.quote is None
               for f in a1_check.run_transfer_scope_check(_task(), [labor]))


# ==================== T15-01 全部工程发包给他人 ====================

def _t15_01_hit_mats():
    return [
        _mat(1, "总包合同.pdf",
             "总承包范围：路基、路面、桥梁工程。发包人：甲公司；承包人：乙公司。"
             "承包人将全部工程交由丙公司施工。", role="general"),
        _mat(10, "分包合同.pdf", "分包范围：路基、路面、桥梁工程。", role="sub"),
    ]


def test_t15_01_hit_when_all_sides_and_transfer_plaintext_present():
    """四侧（总包范围/分包范围/合同双方/完整转移明文）齐全 → 命中，证据锚定真实材料页。"""
    f = _find("T15-01", _t15_01_hit_mats())
    assert f.status == STATUS_HIT
    assert f.kind == "transfer_scope"
    assert f.material_id == 1                       # 指向真实材料 id（总包合同）
    assert isinstance(f.page, int) and f.page == 1  # page 为 int
    assert f.quote and "全部工程" in f.quote        # 引文为完整转移明文所在页的原文切片
    assert "命中" in f.message and "人工复核" in f.message


def test_t15_01_missing_party_side_degrades_to_undecidable():
    """缺「合同双方」侧（文本仅出现单侧主体）→ 该例退化为不可判定，并列明缺项。"""
    mats = [
        _mat(1, "总包合同.pdf",
             "总承包范围：路基、路面、桥梁工程。承包人将全部工程交由丙公司施工。",
             role="general"),
        _mat(10, "分包合同.pdf", "分包范围：路基工程。", role="sub"),
    ]
    f = _find("T15-01", mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "缺" in f.message and "合同双方" in f.message


def _t15_01_miss_mats():
    return [
        _mat(1, "总包合同.pdf",
             "总承包范围：路基、路面工程。承包人自行负责施工组织，分包单位仅负责表土清运。",
             role="general"),
        _mat(10, "分包合同.pdf", "分包范围：表土清运。", role="sub"),
    ]


def test_t15_01_miss_when_general_retains_organization():
    """明文由总包保留施工组织且无完整转移明文 → 未命中。"""
    f = _find("T15-01", _t15_01_miss_mats())
    assert f.status == STATUS_MISS
    assert "未命中" in f.message and "保留施工组织" in f.message


def test_t15_01_undecidable_when_no_sub_contract():
    """只有总包合同、无分包合同 → 缺分包范围侧 → 不可判定，并列明缺失项。"""
    mats = [
        _mat(1, "总包合同.pdf",
             "总承包范围：路基、路面工程。发包人：甲公司；承包人：乙公司。"
             "承包人将全部工程交由丙公司施工。", role="general"),
    ]
    f = _find("T15-01", mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "缺" in f.message and "分包范围" in f.message


def test_t15_01_similarity_only_degrades_never_hits():
    """范围高度相似但无完整转移明文 ⇒ 不可判定 + 人工提示，不得命中。"""
    mats = [
        _mat(1, "总包合同.pdf",
             "总承包范围：路基、路面、桥梁工程。发包人：甲公司；承包人：乙公司。",
             role="general"),
        _mat(10, "分包合同.pdf", "分包范围：路基、路面、桥梁工程。", role="sub"),
    ]
    f = _find("T15-01", mats)
    assert f.status == STATUS_UNDECIDABLE
    assert f.status != STATUS_HIT
    assert "范围高度相似" in f.message and "建议人工确认是否整体转移" in f.message


def _t15_01_payment_wording_mats():
    """CASE1：四侧看似齐全，但「全部工程款交由…」是工程款支付安排，非工程交付明文。"""
    return [
        _mat(1, "总包合同.pdf",
             "总承包范围：路基、路面工程。发包人：甲公司；承包人：乙公司。"
             "全部工程款交由发包人直接支付。", role="general"),
        _mat(10, "分包合同.pdf", "分包范围：路基、路面工程。", role="sub"),
    ]


def test_t15_01_payment_wording_is_not_transfer_plaintext():
    """子串误判回归：范围词「全部工程」是「全部工程款」的子串；其后紧邻名词「款」而非交付
    动词（邻接判据）⇒ 属名词复合（工程款），非完整转移明文 ⇒ 不得命中，应退化为不可判定。"""
    f = _find("T15-01", _t15_01_payment_wording_mats())
    assert f.status != STATUS_HIT
    assert f.status == STATUS_UNDECIDABLE
    assert not f.quote or "全部工程款" not in f.quote, "支付安排切片不得被当作转移明文引文"


def test_t15_01_noun_suffix_family_never_hits():
    """邻接判据回归（表格驱动）：范围词后紧邻名词后缀（量/质量/工期/进度/款…）而非交付
    动词 ⇒ 全部判不可判定，绝不命中（「工程X」是开放名词族，不能靠枚举排除词穷举）。"""
    suffixes = ["量交由监理计量确认", "质量交由监理验收", "工期交由发包人确定",
                "进度交由监理确认", "款交由发包人直接支付"]
    for suffix in suffixes:
        mats = [
            _mat(1, "总包合同.pdf",
                 "总承包范围：路基、路面工程。发包人：甲公司；承包人：乙公司。"
                 "全部工程%s。" % suffix, role="general"),
            _mat(10, "分包合同.pdf", "分包范围：路基、路面工程。", role="sub"),
        ]
        f = _find("T15-01", mats)
        assert f.status != STATUS_HIT, "「全部工程%s」不得命中（范围词非动词直接宾语）" % suffix
        assert f.status == STATUS_UNDECIDABLE


def test_t15_01_connector_keeps_true_plaintext_hit():
    """真阳性回归：范围词与交付动词之间只隔连接词「整体」⇒ 仍命中（连接词白名单生效，
    邻接判据没有把真明文一起误杀）。"""
    mats = [
        _mat(1, "总包合同.pdf",
             "总承包范围：路基、路面、桥梁工程。发包人：甲公司；承包人：乙公司。"
             "承包人将全部工程整体交由丙公司施工。", role="general"),
        _mat(10, "分包合同.pdf", "分包范围：路基、路面、桥梁工程。", role="sub"),
    ]
    f = _find("T15-01", mats)
    assert f.status == STATUS_HIT
    assert f.quote and "全部工程" in f.quote


# ==================== T15-02 全部工程肢解后分别发包 ====================

def _t15_02_hit_mats():
    return [
        _mat(1, "总包合同.pdf",
             "总承包范围：路基、路面、桥梁、隧道工程。发包人：甲公司；承包人：乙公司。"
             "承包人将全部工程肢解后分别发包。", role="general"),
        _mat(10, "分包合同一.pdf", "分包范围：合同段一路基工程。", role="sub"),
        _mat(11, "分包合同二.pdf", "分包范围：合同段二路面工程。", role="sub"),
    ]


def test_t15_02_hit_with_two_subs_anchor_and_dismember_plaintext():
    """≥2 份分包合同 + 标段锚点 + 明文肢解/分别发包 → 命中，证据锚定明文所在页。"""
    f = _find("T15-02", _t15_02_hit_mats())
    assert f.status == STATUS_HIT
    assert f.kind == "transfer_scope"
    assert f.material_id == 1
    assert isinstance(f.page, int)
    assert f.quote and ("肢解" in f.quote or "全部工程" in f.quote)
    assert "命中" in f.message and "人工复核" in f.message


def test_t15_02_multiple_subs_without_anchor_is_undecidable():
    """仅多份分包合同、无标段锚点/无覆盖明文 → 不可判定，message 点明该理由。"""
    mats = [
        _mat(1, "总包合同.pdf", "总承包范围：路基、路面工程。", role="general"),
        _mat(10, "分包合同一.pdf", "分包范围：路基工程。", role="sub"),
        _mat(11, "分包合同二.pdf", "分包范围：路面工程。", role="sub"),
    ]
    f = _find("T15-02", mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "仅有多份分包合同不足以证明肢解" in f.message


def _t15_02_miss_mats():
    return [
        _mat(1, "总包合同.pdf",
             "总承包范围：路基、路面、桥梁工程。承包人自行负责施工组织。", role="general"),
        _mat(10, "分包合同一.pdf", "分包范围：部分路基工程。", role="sub"),
    ]


def test_t15_02_miss_when_general_retains_and_sub_partial():
    """明文总包保留施工组织且分包仅承担部分范围，无肢解明文 → 未命中。"""
    f = _find("T15-02", _t15_02_miss_mats())
    assert f.status == STATUS_MISS
    assert "未命中" in f.message and "部分范围" in f.message


def test_t15_02_undecidable_when_evidence_missing():
    """证据不足（分包合同<2、无标段锚点、无覆盖明文）→ 不可判定并列明缺项。"""
    mats = [
        _mat(1, "总包合同.pdf",
             "总承包范围：路基、路面工程。发包人：甲公司；承包人：乙公司。", role="general"),
        _mat(10, "分包合同.pdf", "分包范围：交通安全设施。", role="sub"),
    ]
    f = _find("T15-02", mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "缺" in f.message


def test_t15_02_explains_missing_context_without_assuming_other_contracts_exist():
    finding = _find("T15-02", [_mat(10, "分包合同.pdf", "分包范围：交通安全设施。", role="sub")])
    assert finding.status == STATUS_UNDECIDABLE
    assert "总包合同" in finding.message
    assert "如有其他分包合同" in finding.message
    assert "无法判断" in finding.message


def _t15_02_payment_wording_mats():
    """T15-02 同源负例：四侧看似齐全（含标段锚点 + 2 份分包合同），但覆盖明文位置只有
    「全部工程款…」，属名词复合 ⇒ 不得命中。"""
    return [
        _mat(1, "总包合同.pdf",
             "总承包范围：路基、路面工程。发包人：甲公司；承包人：乙公司。"
             "本标段全部工程款分别交由各分包单位结算。", role="general"),
        _mat(10, "分包合同一.pdf", "分包范围：合同段一路基工程。", role="sub"),
        _mat(11, "分包合同二.pdf", "分包范围：合同段二路面工程。", role="sub"),
    ]


def test_t15_02_payment_wording_is_not_coverage_plaintext():
    """子串误判回归（T15-02 同源）：无排除逻辑时「全部工程款分别交由…」会被当作
    肢解/分别发包覆盖明文而误 hit；加排除后应退化为不可判定。"""
    f = _find("T15-02", _t15_02_payment_wording_mats())
    assert f.status != STATUS_HIT
    assert f.status == STATUS_UNDECIDABLE
    assert not f.quote or "全部工程款" not in f.quote


# ==================== 归因错配与裸词误 miss 回归 ====================

def test_t15_01_retention_in_sub_only_is_not_miss():
    """疑点2：保留模式只能由总包合同声明；分包件写「分包人自行组织施工」不得据此判未命中。"""
    mats = [
        _mat(1, "总包合同.pdf",
             "总承包范围：路基工程。发包人：甲公司；承包人：乙公司。", role="general"),
        _mat(10, "分包合同.pdf", "分包范围：路基工程。分包人自行组织施工。", role="sub"),
    ]
    f = _find("T15-01", mats)
    assert f.status != STATUS_MISS
    assert f.status == STATUS_UNDECIDABLE


def test_t15_01_subjectless_self_construction_is_not_miss():
    """疑点2b：无主体短语「自行施工」在分包件出现属正常履约描述，不得据此判未命中。"""
    mats = [
        _mat(1, "总包合同.pdf",
             "总承包范围：路基工程。发包人：甲公司；承包人：乙公司。", role="general"),
        _mat(10, "分包合同.pdf", "分包范围：路基工程。自行施工。", role="sub"),
    ]
    f = _find("T15-01", mats)
    assert f.status != STATUS_MISS


def test_t15_01_sub_only_subject_bearing_retention_is_not_miss():
    """疑点2 单层盲点补测：带主体保留短语「承包人负责施工组织」出现在**分包件**（分包人引述
    总包要求，属真实常见表述）时，保留证据仍必须落在总包件，不得据此判未命中。
    本用例专为「仅代码层回退」（保留检索改回 `general + subs`、数据层不动）而设。"""
    mats = [
        _mat(1, "总包合同.pdf",
             "总承包范围：路基工程。发包人：甲公司。承包人：乙公司。", role="general"),
        _mat(10, "分包合同.pdf",
             "分包范围：路基工程。分包人应按承包人负责施工组织的要求组织施工。", role="sub"),
    ]
    f = _find("T15-01", mats)
    assert f.status != STATUS_MISS
    assert f.status == STATUS_UNDECIDABLE


def test_t15_01_retention_in_general_still_miss():
    """限定到 general 没把真保留判定一起杀掉：总包合同明写保留施工组织 → 仍未命中。"""
    mats = [
        _mat(1, "总包合同.pdf",
             "总承包范围：路基工程。承包人自行负责施工组织。", role="general"),
        _mat(10, "分包合同.pdf", "分包范围：路基工程。", role="sub"),
    ]
    f = _find("T15-01", mats)
    assert f.status == STATUS_MISS


def test_t15_02_unrelated_partial_word_is_not_miss():
    """疑点3：裸词「部分」在无关语境（本合同部分条款另议）不得触发 partial；即使总包有保留，
    也不得据此判未命中（应不可判定）。"""
    mats = [
        _mat(1, "总包合同.pdf",
             "总承包范围：路基、路面工程。承包人自行负责施工组织。本合同段。", role="general"),
        _mat(10, "分包合同一.pdf",
             "分包范围：路基工程（本合同部分条款另议）。", role="sub"),
        _mat(11, "分包合同二.pdf", "分包范围：路面工程。", role="sub"),
    ]
    f = _find("T15-02", mats)
    assert f.status != STATUS_MISS
    assert f.status == STATUS_UNDECIDABLE


def test_t15_02_sub_only_subject_bearing_retention_is_not_miss():
    """疑点2 同源单层盲点（T15-02）：分包件引述带主体保留短语、分包范围部分，而总包件无保留
    时，保留检索若被单层回退为 `general + subs` 会误判未命中；正确行为应为不可判定。"""
    mats = [
        _mat(1, "总包合同.pdf",
             "总承包范围：路基、路面工程。发包人：甲公司；承包人：乙公司。", role="general"),
        _mat(10, "分包合同.pdf",
             "分包范围：部分路基工程（按承包人负责施工组织的要求施工）。", role="sub"),
    ]
    f = _find("T15-02", mats)
    assert f.status != STATUS_MISS
    assert f.status == STATUS_UNDECIDABLE


# ==================== 措辞红线 & 返回值形状 ====================

def test_transfer_messages_never_claim_determined():
    """全局禁词：所有 finding 的 message/source 拼起来不得含「已认定」。"""
    bundles = [
        _t15_01_hit_mats(),
        _t15_01_miss_mats(),
        _t15_02_hit_mats(),
        _t15_02_miss_mats(),
        [_mat(1, "总包合同.pdf", "总承包范围：路基工程。", role="general")],
    ]
    for mats in bundles:
        findings = a1_check.run_transfer_scope_check(_task(), mats)
        assert findings, "内核须至少返回 finding"
        assert "已认定" not in _all_text(findings)


def test_transfer_scope_returns_two_findings_shape():
    """返回值形状锁：恰好 2 条、item_id 集合固定、kind 稳定为 transfer_scope。"""
    findings = a1_check.run_transfer_scope_check(_task(), _t15_01_hit_mats())
    assert len(findings) == 2
    assert {f.item_id for f in findings} == {"T15-01", "T15-02"}
    assert {f.kind for f in findings} == {"transfer_scope"}
    # 顺序固定 T15-01 → T15-02
    assert [f.item_id for f in findings] == ["T15-01", "T15-02"]


def test_server_scope_flag_does_not_change_deterministic_transfer_findings(monkeypatch):
    def snapshot():
        findings = a1_check.run_transfer_scope_check(_task(), _t15_01_hit_mats())
        return [
            (finding.item_id, finding.status, finding.material_id, finding.page,
             finding.quote, finding.message, finding.source, finding.evidence_json, finding.missing_json)
            for finding in findings
        ]

    monkeypatch.setenv(SCOPE_SELECTOR_ENV, "false")
    baseline = snapshot()
    monkeypatch.setenv(SCOPE_SELECTOR_ENV, "true")
    candidate = snapshot()

    assert candidate == baseline
