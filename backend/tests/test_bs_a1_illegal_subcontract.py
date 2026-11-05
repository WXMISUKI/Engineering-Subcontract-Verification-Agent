# -*- coding: utf-8 -*-
"""blockchain_supervision A1-RULE-04：W16/V16/L17 违法分包与劳务名义分包 定向契约测试。

判定纪律锚点（A1-RULE-04 任务包 / 记账红线）：
- 入口 `run_illegal_subcontract_check` 在**总包合同在场时**恰好 14 条，顺序 W16-01→W16-07、
  V16-01→V16-04、L17-01→L17-03；kind 统一 illegal_subcontract；**不接入 service**（R05 统一接线）；
- 证据只能来自总包合同的规则（目录 `requires_general_contract`，现为 W16-03/V16-02）：任务未
  上传总包合同时**整体不进本次范围**（不产出「缺件」），范围由 reporting.scope_statements 声明；
- a3_findings/negative_list_findings 为已计算结论显式传入（不调数据库、不重复执行
  A3/负面清单）；qualification_evidence 为可选 dict；
- 开放条款（W16-07/V16-04/L17-03）由目录 open_term 字段驱动：恒不可判定、仅登记、
  锚点 None、不参与命中/未命中统计；
- W16-02 复用负面清单结论（hit→命中并关联条目 id；未跑与全 miss 是两种证据状态）；
- W16-04 禁「挂靠」结论：message 只用「以他人名义承揽线索」语气（禁词扫描把关）；
- 否定句内子串不作肯定证据；双重否定机器不裁 → 不可判定；缺同句施受不得拼 hit；
- L17-01 禁裸词「主要材料费」：百分比计费条款（管理费按主要材料费的3%计取）不得 hit；
- L17-02 责任方向正则迁移进 json illegal_detection（旧硬编码不动），并修复跨行拼接
  缺陷（换行先归一为句界）；
- W16-05×L17 重叠去重走公共纯函数 illegal_subcontract_relations（可测试中间结构，
  不写数据库、不加模型字段）；
- hit 一律线索语气 + 「请人工复核」；缺证据 → 不可判定列缺项，禁静默判通过。

夹具形态与 test_bs_a1_transfer_relations.py 一致（SimpleNamespace materials + status 断言）。
"""
from types import SimpleNamespace

import pytest

from app.plus.blockchain_supervision import a1_check, rules
from app.plus.blockchain_supervision.models import (
    STATUS_HIT, STATUS_MISS, STATUS_UNDECIDABLE,
)

pytestmark = pytest.mark.integration

_EXPECTED_ORDER = ["W16-01", "W16-02", "W16-03", "W16-04", "W16-05", "W16-06",
                   "W16-07", "V16-01", "V16-02", "V16-03", "V16-04",
                   "L17-01", "L17-02", "L17-03"]
_OPEN_TERM_IDS = {"W16-07", "V16-04", "L17-03"}
# 证据只能来自总包合同的规则：从目录数据读出，不在这里另存一份副本
_GENERAL_DEPENDENT_IDS = {rule["id"] for rule in rules.load_transfer_rules()["rules"]
                          if rule.get("requires_general_contract")}


def test_general_dependent_rule_ids_locked():
    """范围闸名单锁：目录里标了 `requires_general_contract` 的就是这两条；
    新增标记者必须同步本条与范围声明（避免有人悄悄把业主没收集的资料改成「缺件」）。"""
    assert _GENERAL_DEPENDENT_IDS == {"W16-03", "V16-02"}


def test_general_dependent_rules_skip_when_no_general_contract():
    """未上传总包合同 → 这两条不进本次范围（不产出「缺件」），其余规则照常判定。"""
    mats = [_mat(2, "分包合同.pdf", "分包合同。", role="sub")]
    ids = [f.item_id for f in _run(mats)]
    assert _GENERAL_DEPENDENT_IDS.isdisjoint(ids)
    assert len(ids) == len(_EXPECTED_ORDER) - len(_GENERAL_DEPENDENT_IDS)


# ==================== 夹具（与 test_bs_a1_transfer_relations.py 同形态） ====================

def _mat(mid, name, text="", role="packet"):
    pages = [{"page": 1, "text": text}] if text else []
    return SimpleNamespace(id=mid, file_name=name, role=role, parse_state="done",
                           pages_json=pages)


def _task(**kw):
    kw.setdefault("id", 1)
    return SimpleNamespace(**kw)


def _run(materials, task=None, **kw):
    return a1_check.run_illegal_subcontract_check(task or _task(), materials, **kw)


def _find(item_id, materials, **kw):
    return next(f for f in _run(materials, **kw) if f.item_id == item_id)


def _anchor_ok(f, mats):
    """证据坐标契约：material_id 指向真实传入材料、page 为该材料真实页、引文非空。"""
    assert f.material_id in {m.id for m in mats}
    mat = next(m for m in mats if m.id == f.material_id)
    pages = {p["page"] for p in mat.pages_json}
    assert isinstance(f.page, int) and f.page in pages
    assert f.quote


def _rc(item_id, status, quote=None, page=None, material_id=None):
    """A3/负面清单已计算结论的夹具形态（显式传入，不执行 A3）。"""
    return SimpleNamespace(item_id=item_id, status=status, message="结论：%s" % item_id,
                           quote=quote, page=page, material_id=material_id)


# ==================== 形状契约（恰好 14 条、顺序、kind、缺省参数） ====================

def test_returns_exactly_14_findings_in_fixed_order():
    """形状锁：恰好 14 条、item_id 顺序固定、kind 统一 illegal_subcontract。"""
    mats = [_mat(1, "总包合同.pdf", "总承包范围：路基工程。", role="general")]
    findings = _run(mats)
    assert len(findings) == 14
    assert [f.item_id for f in findings] == _EXPECTED_ORDER
    assert {f.kind for f in findings} == {"illegal_subcontract"}
    assert all(f.task_id == 1 for f in findings)


def test_empty_materials_no_crash_all_undecidable():
    """空材料列表不抛异常：除「证据只能来自总包合同」的规则外全部不可判定列缺项，
    开放条款仅登记（不参与命中/未命中）。"""
    findings = _run([])
    assert [f.item_id for f in findings] == [item_id for item_id in _EXPECTED_ORDER
                                             if item_id not in _GENERAL_DEPENDENT_IDS]
    by_id = {f.item_id: f for f in findings}
    assert all(f.status == STATUS_UNDECIDABLE for f in findings)
    for item_id in ("W16-01", "W16-02", "W16-04", "W16-06", "V16-01"):
        assert "缺" in by_id[item_id].message     # 非开放规则逐条列缺项
    assert "劳务合作合同" in by_id["W16-05"].message
    assert "labor_contract" in by_id["W16-05"].missing_json
    for item_id in _OPEN_TERM_IDS:
        assert by_id[item_id].status == STATUS_UNDECIDABLE


def test_default_params_undecidable_no_crash():
    """a3/negative_list/qualification 缺省（None）时相关条目不可判定、不崩、不判通过。"""
    mats = [_mat(1, "分包合同.pdf", "分包合同：分包范围：路基工程。", role="sub")]
    findings = _run(mats)
    by_id = {f.item_id: f for f in findings}
    assert by_id["W16-01"].status == STATUS_UNDECIDABLE
    assert "缺" in by_id["W16-01"].message
    assert by_id["W16-02"].status == STATUS_UNDECIDABLE
    assert "负面清单核查结论" in by_id["W16-02"].message
    assert by_id["V16-01"].status == STATUS_UNDECIDABLE
    assert "监理审查" in by_id["V16-01"].message


def test_open_terms_always_registered_undecidable_anchor_none():
    """3 条开放条款：恒不可判定、锚点 None、message 含「仅登记」且说明不参与统计。"""
    mats = [_mat(1, "分包合同.pdf", "分包合同：分包范围：路基工程。", role="sub")]
    findings = _run(mats)
    for f in findings:
        if f.item_id not in _OPEN_TERM_IDS:
            continue
        assert f.status == STATUS_UNDECIDABLE
        assert f.material_id is None and f.quote is None and f.page is None
        assert "仅登记" in f.message
        assert "不参与命中/未命中统计" in f.message


def test_open_terms_driven_by_catalog_field_not_hardcoded():
    """开放条款由目录 open_term 字段驱动：目录中 14 条里 open_term=true 恰好 3 条，
    且与内核输出中恒不可判定集合一致（改数据即生效，内核不硬编码 ID 清单）。"""
    catalog = [r for r in rules.load_transfer_rules()["rules"]
               if r.get("article") in ("16", "17")]
    assert [r["id"] for r in catalog] == _EXPECTED_ORDER
    open_ids = {r["id"] for r in catalog if r.get("open_term")}
    assert open_ids == _OPEN_TERM_IDS
    # 每条非开放规则都有 illegal_detection 词表；开放条款不需要
    for r in catalog:
        if r["id"] in _OPEN_TERM_IDS:
            continue
        assert isinstance(r.get("illegal_detection"), dict), r["id"]


def test_l17_02_regexes_migrated_from_hardcoded_to_json():
    """L17-02 正则迁移守恒（公共行为对照 + 静态源码语义断言，不引私有符号）：
    旧入口 run_a3_dependent_checks 与新入口 run_illegal_subcontract_check 对同一劳务
    材料给同向结果；json 三条模式清单在场，新内核源码语义引用它们（不 import 私有）。"""
    import pathlib
    root = pathlib.Path(a1_check.__file__).resolve().parent

    # 行为对照：单行真阳性（劳务侧责任）旧/新都命中
    pos = [_mat(21, "劳务合同-甲.pdf", "劳务作业负责施工方案编制及现场配合。", role="labor")]
    old_pos = next(f for f in a1_check.run_a3_dependent_checks(_task(), [], pos)
                   if f.item_id == "L17-02")
    assert old_pos.status == STATUS_HIT
    assert _find("L17-02", pos).status == STATUS_HIT

    # 行为对照：承包人侧责任旧/新都未命中
    neg = [_mat(23, "劳务合同-乙.pdf", "施工方案由承包人负责编制，劳务作业按图施工。",
                role="labor")]
    old_neg = next(f for f in a1_check.run_a3_dependent_checks(_task(), [], neg)
                   if f.item_id == "L17-02")
    assert old_neg.status == STATUS_MISS
    assert _find("L17-02", neg).status == STATUS_MISS

    # 静态源码语义断言：json 携带三条模式清单，内核源码语义引用（语义锚点，不绑私有名）
    det = next(r for r in rules.load_transfer_rules()["rules"]
               if r["id"] == "L17-02")["illegal_detection"]
    for key in ("labor_responsibility_patterns", "contractor_responsibility_patterns",
                "labor_sign_patterns"):
        assert isinstance(det.get(key), list) and det[key], key
    src = (root / "a1_check.py").read_text(encoding="utf-8")
    for key in ("labor_responsibility_patterns", "contractor_responsibility_patterns",
                "labor_sign_patterns"):
        assert 'det["%s"]' % key in src, key


# ==================== W16-01 分包给个人或不具备相应条件企业 ====================

def test_w16_01_hit_individual_award():
    mats = [_mat(2, "分包合同.pdf", "承包人将路基工程分包给包工头张某施工。", role="sub")]
    f = _find("W16-01", mats)
    assert f.status == STATUS_HIT
    _anchor_ok(f, mats)
    assert "个人/包工头" in f.message and "人工复核" in f.message


def test_w16_01_hit_no_safety_permit_statement_same_clause():
    mats = [_mat(2, "分包合同.pdf", "分包单位系未取得安全生产许可证的施工队伍。", role="sub")]
    f = _find("W16-01", mats)
    assert f.status == STATUS_HIT
    _anchor_ok(f, mats)
    assert "未取得" in f.message and "人工复核" in f.message


def test_w16_01_miss_with_full_qualification_evidence():
    mats = [_mat(2, "分包合同.pdf", "分包合同：分包给某工程有限公司。", role="sub")]
    f = _find("W16-01", mats, qualification_evidence={
        "business_license": True, "qualification": True, "safety_permit": True})
    assert f.status == STATUS_MISS
    assert "未命中" in f.message and "未见" in f.message


def test_w16_01_undecidable_without_qualification_evidence():
    """无资格证据且无否定明示 → 不可判定，逐字列缺哪三侧。"""
    mats = [_mat(2, "分包合同.pdf", "分包合同：分包给某工程有限公司。", role="sub")]
    f = _find("W16-01", mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "缺：营业执照等证照、资质、安全生产许可证" in f.message


def test_w16_01_undecidable_lists_missing_fields_verbatim():
    """qualification_evidence 缺字段 → 逐字列缺哪个。"""
    mats = [_mat(2, "分包合同.pdf", "分包合同：分包给某工程有限公司。", role="sub")]
    f = _find("W16-01", mats, qualification_evidence={"business_license": True})
    assert f.status == STATUS_UNDECIDABLE
    assert "缺：资质、安全生产许可证" in f.message


def test_w16_01_dual_negation_is_undecidable_not_hit():
    """双重否定（「不存在不具备资质情形」）机器不裁：不得点亮否定性明示 → 不可判定。"""
    mats = [_mat(2, "分包合同.pdf", "分包合同：分包给某工程有限公司。"
                 "经核查不存在不具备资质的情形。", role="sub")]
    f = _find("W16-01", mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "双重否定" in f.message


def test_w16_01_cross_subject_qualification_never_hit():
    """反例（R03 跨主体同型）：分包合同在场，但「个人不具备资质」表述与分包主体不同句
    ——无同句施受关系不得拼 hit。"""
    mats = [
        _mat(2, "分包合同.pdf", "分包合同：分包范围：路基工程。", role="sub"),
        _mat(3, "情况说明.pdf", "张某个人不具备相应资质。", role="packet"),
    ]
    f = _find("W16-01", mats)
    assert f.status != STATUS_HIT
    assert f.status == STATUS_UNDECIDABLE
    assert "缺" in f.message


# ==================== W16-02 分包负面清单主体或关键性工作 ====================

def test_w16_02_hit_with_negative_list_hits():
    nl = [_rc("NL-B-02", STATUS_HIT, quote="水深12米的围堰工程", page=1, material_id=5),
          _rc("NL-T-06", STATUS_MISS)]
    mats = [_mat(5, "分包合同.pdf", "分包范围：围堰工程。", role="sub")]
    f = _find("W16-02", mats, negative_list_findings=nl)
    assert f.status == STATUS_HIT
    assert "NL-B-02" in f.message and "NL-T-06" not in f.message.split("关联结论")[0]
    assert "人工复核" in f.message
    assert f.material_id == 5 and f.page == 1 and "围堰" in f.quote


def test_w16_02_miss_when_all_negative_list_miss():
    # “全部未命中”必须覆盖当前真实目录；仅两条召回项不是完整清单核查。
    nl = [_rc(rule["id"], STATUS_MISS) for rule in rules.negative_list_rules()]
    f = _find("W16-02", [], negative_list_findings=nl)
    assert f.status == STATUS_MISS
    assert "全部条目未命中" in f.message


def test_w16_02_undecidable_when_not_run():
    """未跑负面清单（None/空）→ 不可判定，与「跑过且全部未命中」措辞可区分。"""
    f = _find("W16-02", [], negative_list_findings=None)
    assert f.status == STATUS_UNDECIDABLE
    assert "未执行" in f.message and "缺：负面清单核查结论" in f.message


def test_w16_02_hit_precedes_undecided_entries():
    """混合态裁定（主理人对抗探测 5a）：负面清单同时有 hit 与 undecidable 条目
    → hit 优先（复用命中的聚合条目，任一确定性命中即充分依据），
    message 提示「另有 N 条不可判定，无法排除」，不翻案也不静默。"""
    nl = [_rc("NL-B-02", STATUS_HIT, quote="水深12米的围堰工程", page=1, material_id=5),
          _rc("NL-R-01", STATUS_UNDECIDABLE)]
    mats = [_mat(5, "分包合同.pdf", "分包范围：围堰工程。", role="sub")]
    f = _find("W16-02", mats, negative_list_findings=nl)
    assert f.status == STATUS_HIT
    assert "另有 1 条负面清单条目不可判定" in f.message
    assert "NL-R-01" not in f.message.split("关联结论")[0]  # und 条目只在提示语，不作命中依据


def test_w16_02_undecidable_when_negative_list_has_undecided():
    nl = [_rc("NL-B-02", STATUS_MISS), _rc("NL-R-01", STATUS_UNDECIDABLE)]
    f = _find("W16-02", [], negative_list_findings=nl)
    assert f.status == STATUS_UNDECIDABLE
    assert "NL-R-01" in f.message


# ==================== W16-03 分包合同明确不得分包的工程 ====================

def _w16_03_mats(consent_text=None, sub_text="分包合同：分包范围：附属工程。"):
    general_text = "总包合同约定：主体工程不得分包。"
    if consent_text:
        general_text += consent_text
    return [
        _mat(1, "总包合同.pdf", general_text, role="general"),
        _mat(2, "分包合同.pdf", sub_text, role="sub"),
    ]


def test_w16_03_hit_clause_and_subcontract_without_consent():
    f = _find("W16-03", _w16_03_mats())
    assert f.status == STATUS_HIT
    _anchor_ok(f, _w16_03_mats())
    assert "不得分包" in f.message and "人工复核" in f.message


def test_w16_03_miss_with_early_written_consent():
    """同意日期可抽取且早于/等于分包签订日期 → 未命中（两日期都比到才判）。"""
    mats = _w16_03_mats(consent_text="发包人书面同意日期为2024年3月1日。",
                        sub_text="分包合同于2024年4月15日签订，分包范围：附属工程。")
    f = _find("W16-03", mats)
    assert f.status == STATUS_MISS
    assert "2024-03-01" in f.message and "2024-04-15" in f.message


def test_w16_03_undecidable_when_consent_date_missing():
    """同意在场但日期不可比 → 不可判定（缺：发包人书面同意日期或分包签订日期）。"""
    mats = _w16_03_mats(consent_text="已取得发包人书面同意。")
    f = _find("W16-03", mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "日期不可比" in f.message and "缺：发包人书面同意日期或分包签订日期" in f.message


def test_w16_03_undecidable_when_consent_after_subcontract_signing():
    """同意日期晚于分包签订日期：事后同意效力不由机器裁定 → 不可判定 + 人工复核。"""
    mats = _w16_03_mats(consent_text="发包人书面同意日期为2024年6月1日。",
                        sub_text="分包合同于2024年4月15日签订，分包范围：附属工程。")
    f = _find("W16-03", mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "晚于" in f.message and "人工复核" in f.message


def test_w16_03_undecidable_without_clause():
    # 总包合同在场但没有「不得分包」条款 → 是证据缺口（缺条款），判不可判定而非跳过
    mats = [_mat(1, "总包合同.pdf", "总承包范围：路基工程。", role="general"),
            _mat(2, "分包合同.pdf", "分包合同：分包范围：附属工程。", role="sub")]
    f = _find("W16-03", mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "缺" in f.message


# ==================== W16-04 分包人以他人名义承揽分包工程 ====================

def test_w16_04_hit_same_clause_nominee_and_mismatch():
    mats = [_mat(2, "分包合同.pdf",
                 "分包单位以他人名义承揽分包工程，合同签订方与实际施工主体不一致。",
                 role="sub")]
    f = _find("W16-04", mats)
    assert f.status == STATUS_HIT
    _anchor_ok(f, mats)
    assert "以他人名义承揽" in f.message and "人工复核" in f.message
    assert "挂靠" not in f.message               # 禁「挂靠」结论


def test_w16_04_miss_when_subjects_identical():
    mats = [_mat(2, "分包合同.pdf",
                 "分包合同签订主体与实际施工主体一致，均为某工程有限公司。", role="sub")]
    f = _find("W16-04", mats)
    assert f.status == STATUS_MISS
    assert "未命中" in f.message and "一致" in f.message


def test_w16_04_mismatch_alone_is_undecidable_with_review_hint():
    """仅主体不一致线索无名义承揽明示 → 不可判定 + 「主体不一致线索，请人工复核」。"""
    mats = [_mat(2, "分包合同.pdf", "实际施工与合同签订单位不一致，请核实。", role="sub")]
    f = _find("W16-04", mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "主体不一致线索，请人工复核" in f.message


def test_w16_04_no_evidence_undecidable():
    f = _find("W16-04", [])
    assert f.status == STATUS_UNDECIDABLE
    assert "缺" in f.message


# ==================== W16-05 以劳务合作名义进行施工分包 ====================

def test_w16_05_hit_labor_contract_with_construction_scope_and_payment():
    mats = [_mat(3, "劳务合同.pdf",
                 "劳务合作协议：劳务企业承担路基工程施工，按工程量计取工程款。",
                 role="labor")]
    f = _find("W16-05", mats)
    assert f.status == STATUS_HIT
    _anchor_ok(f, mats)
    assert "施工范围" in f.message and "人工复核" in f.message


def test_w16_05_miss_labor_only_scope_and_payment():
    mats = [_mat(3, "劳务合同.pdf",
                 "劳务合同：劳务作业范围：钢筋绑扎。费用仅含劳务报酬，按劳务工作量结算。",
                 role="labor")]
    f = _find("W16-05", mats)
    assert f.status == STATUS_MISS
    assert "仅劳务作业" in f.message and "未见施工范围明示" in f.message


def test_w16_05_scope_conflict_is_undecidable():
    """施工范围与纯劳务范围同在场 → 指向冲突 → 不可判定。"""
    mats = [_mat(3, "劳务合同.pdf",
                 "劳务合同：劳务企业承担路基工程施工，仅承担劳务作业。", role="labor")]
    f = _find("W16-05", mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "指向冲突" in f.message


def test_w16_05_missing_sides_undecidable():
    mats = [_mat(3, "劳务合同.pdf", "劳务合同一份。", role="labor")]
    f = _find("W16-05", mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "缺" in f.message


# ==================== W16-06 再分包或劳务作业转交他人 ====================

def test_w16_06_hit_resubcontract_with_implementation_same_clause():
    mats = [_mat(2, "分包合同.pdf",
                 "分包单位将部分工程再次分包给某施工队伍，实际施工由该队伍进行。",
                 role="sub")]
    f = _find("W16-06", mats)
    assert f.status == STATUS_HIT
    _anchor_ok(f, mats)
    assert "再分包" in f.message and "人工复核" in f.message


def test_w16_06_miss_self_implementation():
    mats = [_mat(2, "分包合同.pdf", "分包单位自行组织施工，不再分包。", role="sub")]
    f = _find("W16-06", mats)
    assert f.status == STATUS_MISS
    assert "不再分包" in f.message


def test_w16_06_missing_chain_or_implementation_undecidable():
    mats = [_mat(2, "分包合同.pdf", "分包范围：附属工程。", role="sub")]
    f = _find("W16-06", mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "缺" in f.message


# ==================== V16-01 未经监理审查或未报发包人书面同意（两侧独立新口径） ====================

def test_v16_01_hit_both_sides_risky_a3_and_text():
    """监理侧取 A3 RC-09 结论 + 同意侧材料明示风险 → 两侧都风险 → 命中。"""
    a3 = [_rc("RC-09", STATUS_HIT, quote="报审表未见总监理工程师", page=2, material_id=7)]
    mats = [_mat(4, "分包报审.pdf", "分包合同未经发包人书面同意。", role="packet")]
    f = _find("V16-01", mats, a3_findings=a3)
    assert f.status == STATUS_HIT
    assert f.material_id == 7 and f.page == 2        # 监理侧锚点取自 A3 结论
    assert "人工复核" in f.message and "发包人书面同意" in f.message


def test_v16_01_hit_both_sides_from_material_text():
    """无 A3 结论时材料文本明示兜底：两侧文本均风险 → 命中。"""
    mats = [_mat(4, "分包报审.pdf",
                 "分包合同未经监理审查，亦未经发包人书面同意。", role="packet")]
    f = _find("V16-01", mats, a3_findings=[])
    assert f.status == STATUS_HIT
    assert "人工复核" in f.message


def test_v16_01_miss_both_sides_legal():
    mats = [_mat(4, "分包报审.pdf",
                 "已经监理审查合格。发包人书面同意分包。", role="packet")]
    f = _find("V16-01", mats, a3_findings=[])
    assert f.status == STATUS_MISS
    assert "未命中" in f.message and "合规" in f.message


def test_v16_01_a3_miss_and_text_consent_legal_is_miss():
    a3 = [_rc("RC-09", STATUS_MISS, quote="总监签章在场", page=1, material_id=7)]
    mats = [_mat(4, "分包报审.pdf", "发包人批准该分包事项。", role="packet")]
    f = _find("V16-01", mats, a3_findings=a3)
    assert f.status == STATUS_MISS


def test_v16_01_missing_one_side_is_undecidable_verbatim():
    """新口径核心：任一侧证据不足 → 不可判定，逐字列缺哪侧（或逻辑从严不再适用）。"""
    a3 = [_rc("RC-09", STATUS_HIT, quote="报审表未见总监理工程师", page=2, material_id=7)]
    f = _find("V16-01", [], a3_findings=a3)          # 监理侧风险、同意侧无证据
    assert f.status == STATUS_UNDECIDABLE
    assert "两个独立证据侧" in f.message
    assert "缺：发包人书面同意" in f.message
    assert "监理审查" not in f.message.split("缺：")[1].split("——")[0]


def test_v16_01_both_sides_silent_is_undecidable():
    f = _find("V16-01", [], a3_findings=None)
    assert f.status == STATUS_UNDECIDABLE
    assert "缺：监理审查、发包人书面同意" in f.message


def test_v16_01_side_conflict_is_undecidable():
    """同侧肯定与否定表述同在场 → 指向冲突 → 不可判定（极性纪律）。"""
    mats = [_mat(4, "分包报审.pdf", "未经监理审查。已经监理审查。", role="packet")]
    f = _find("V16-01", mats, a3_findings=[])
    assert f.status == STATUS_UNDECIDABLE
    assert "指向冲突" in f.message


# ==================== V16-02 未依法签订或未遵循承包合同原则 ====================

def test_v16_02_hit_principle_violation_without_explanation():
    mats = [
        _mat(1, "总包合同.pdf", "总包合同。主要机械设备由分包单位购置。", role="general"),
        _mat(2, "分包合同.pdf", "分包合同。", role="sub"),
    ]
    f = _find("V16-02", mats)
    assert f.status == STATUS_HIT
    _anchor_ok(f, mats)
    assert "总包合同原则" in f.message and "人工复核" in f.message


def test_v16_02_miss_consistency_statement():
    mats = [
        _mat(1, "总包合同.pdf", "总包合同。分包施工符合总包合同原则。", role="general"),
        _mat(2, "分包合同.pdf", "分包合同。", role="sub"),
    ]
    f = _find("V16-02", mats)
    assert f.status == STATUS_MISS
    assert "未命中" in f.message and "符合总包合同原则" in f.message


def test_v16_02_explanation_present_blocks_hit():
    """设备设施说明在场 → 不可判定（说明合理性机器不裁），不得判命中。"""
    mats = [
        _mat(1, "总包合同.pdf", "总包合同。主要机械设备由分包单位购置。", role="general"),
        _mat(2, "分包合同.pdf", "分包合同。机械设备配置说明：见附表。", role="sub"),
    ]
    f = _find("V16-02", mats)
    assert f.status != STATUS_HIT
    assert f.status == STATUS_UNDECIDABLE


def test_v16_02_missing_contract_comparison_undecidable():
    # 总包合同在场但没有对照表述 → 缺合同对照，判不可判定而非跳过
    mats = [_mat(1, "总包合同.pdf", "总承包范围：路基工程。", role="general"),
            _mat(2, "分包合同.pdf", "分包合同。", role="sub")]
    f = _find("V16-02", mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "缺" in f.message


# ==================== V16-03 未设现场管理机构和派驻人员 ====================

def test_v16_03_hit_no_org_no_personnel_statement():
    mats = [_mat(2, "分包合同.pdf",
                 "分包范围：路基工程。未设立现场管理机构，亦未派驻管理人员。", role="sub")]
    f = _find("V16-03", mats)
    assert f.status == STATUS_HIT
    _anchor_ok(f, mats)
    assert "未设现场管理机构" in f.message and "人工复核" in f.message


def test_v16_03_miss_self_managed_with_evidence():
    mats = [_mat(2, "分包合同.pdf",
                 "分包范围：路基工程。分包单位设立现场管理机构并派驻人员，"
                 "管理人员名单齐备。", role="sub")]
    f = _find("V16-03", mats)
    assert f.status == STATUS_MISS
    assert "未命中" in f.message and "分包侧自身" in f.message


def test_v16_03_missing_sides_undecidable():
    mats = [_mat(2, "分包合同.pdf", "分包范围：路基工程。", role="sub")]
    f = _find("V16-03", mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "缺" in f.message


def test_v16_03_negative_with_management_evidence_conflict_undecidable():
    """否定性表述与管理证据同在场 → 指向冲突 → 不可判定。"""
    mats = [_mat(2, "分包合同.pdf",
                 "分包范围：路基工程。未设立现场管理机构。管理人员名单齐备。", role="sub")]
    f = _find("V16-03", mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "指向冲突" in f.message


# ==================== L17-01 劳务企业计取主要材料或周转材料款 ====================

def test_l17_01_employee_contract_is_not_labor_subcontract_evidence():
    mats = [_mat(3, "专业分包.pdf", "乙方在与劳务人员签订劳务合同时，须告知职业病危害。"
                 "本合同分包工程暂定劳务报酬为人民币若干元。", role="sub")]
    finding = _find("L17-01", mats)
    assert finding.status == STATUS_UNDECIDABLE
    assert "劳务合同在场" not in finding.message


def test_l17_01_employee_clause_does_not_hide_actual_labor_subcontract():
    mats = [_mat(3, "资料包.pdf", "乙方与劳务人员签订劳务合同。劳务分包合同：费用仅含劳务报酬。", role="sub")]
    finding = _find("L17-01", mats)
    assert finding.status == STATUS_MISS


def test_w16_05_employee_contract_is_not_labor_subcontract_evidence():
    mats = [_mat(3, "专业分包.pdf", "乙方与劳务人员签订劳务合同。分包范围仅劳务作业，按劳务工日结算。", role="sub")]
    finding = _find("W16-05", mats)
    assert finding.status == STATUS_UNDECIDABLE
    assert "劳务合同在场" not in finding.message

def test_l17_01_hit_forward_order():
    mats = [_mat(3, "劳务合同.pdf", "劳务合同：劳务企业计取主要材料费。", role="labor")]
    f = _find("L17-01", mats)
    assert f.status == STATUS_HIT
    _anchor_ok(f, mats)
    assert "主要材料" in f.message and "人工复核" in f.message


def test_l17_01_hit_reverse_order():
    mats = [_mat(3, "劳务合同.pdf", "劳务合同约定：主要材料款由劳务作业队伍结算。", role="labor")]
    f = _find("L17-01", mats)
    assert f.status == STATUS_HIT
    assert "人工复核" in f.message


def test_l17_01_miss_labor_fee_only():
    mats = [_mat(3, "劳务合同.pdf",
                 "劳务合同：费用仅含劳务报酬，按劳务工作量计取，不涉及材料款。", role="labor")]
    f = _find("L17-01", mats)
    assert f.status == STATUS_MISS
    assert "未命中" in f.message and "劳务报酬" in f.message


def test_l17_01_percentage_clause_never_hits():
    """防误命中回归（任务包点名）：「劳务管理费按主要材料费的3%计取」被计取的宾语是
    管理费而非材料款——禁裸词「主要材料费」，百分比条款不得 hit。"""
    mats = [_mat(3, "劳务合同.pdf", "劳务合同：劳务管理费按主要材料费的3%计取。", role="labor")]
    f = _find("L17-01", mats)
    assert f.status != STATUS_HIT
    assert f.status == STATUS_UNDECIDABLE
    assert "百分比" in f.message


def test_l17_01_missing_labor_contract_undecidable():
    mats = [_mat(2, "分包合同.pdf", "按主要材料费计取相关费用。", role="sub")]
    f = _find("L17-01", mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "无法判断该劳务情形是否适用" in f.message


def test_l17_01_fee_side_unclear_undecidable():
    mats = [_mat(3, "劳务合同.pdf", "劳务合同：双方按约定履行。", role="labor")]
    f = _find("L17-01", mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "费用侧不明" in f.message


# ==================== L17-02 劳务企业自行承担方案检测测量档案及质量安全管理 ====================

def test_l17_02_hit_labor_responsibility():
    mats = [_mat(21, "劳务合同-甲.pdf", "劳务作业负责施工方案编制及现场配合。", role="labor")]
    f = _find("L17-02", mats)
    assert f.status == STATUS_HIT
    assert "施工方案" in f.quote and "人工复核" in f.message


def test_l17_02_hit_packet_signature():
    mats = [_mat(22, "报审包-甲·专项方案.pdf", "专项施工方案，编制单位：某某劳务有限公司",
                 role="packet")]
    f = _find("L17-02", mats)
    assert f.status == STATUS_HIT
    assert "劳务" in f.quote


def test_l17_02_finds_signature_in_subcontract_booklet():
    mats = [_mat(122, "分包合同整册.pdf", "专项施工方案：编制单位：某某劳务有限公司", role="sub")]
    f = _find("L17-02", mats)
    assert f.status == STATUS_HIT
    _anchor_ok(f, mats)


def test_l17_02_does_not_claim_booklet_is_missing():
    mats = [_mat(123, "分包合同整册.pdf", "专业分包合同及附件。", role="sub")]
    f = _find("L17-02", mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "无报审资料包" not in f.message


def test_l17_02_miss_contractor_responsible():
    mats = [_mat(23, "劳务合同-乙.pdf", "施工方案由承包人负责编制，劳务作业按图施工。",
                 role="labor")]
    f = _find("L17-02", mats)
    assert f.status == STATUS_MISS
    assert "承包人" in f.message


def test_l17_02_no_evidence_undecidable():
    mats = [_mat(24, "劳务合同-丙.pdf", "劳务作业范围：钢筋绑扎。", role="labor")]
    f = _find("L17-02", mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "缺" in f.message


def test_l17_02_cross_line_stitching_fixed():
    """缺陷修复回归：换行处不得拼接成一句——「劳务作业负责\\n施工方案编制」在
    旧归一化下会拼成责任派给劳务侧的误命中；新内核把换行归一为句界，不拼 →
    双通道都无 → 不可判定。"""
    mats = [SimpleNamespace(id=31, file_name="劳务合同-丁.pdf", role="labor",
                            parse_state="done",
                            pages_json=[{"page": 1, "text": "劳务作业负责\n施工方案编制。"}])]
    f = _find("L17-02", mats)
    assert f.status != STATUS_HIT
    assert f.status == STATUS_UNDECIDABLE


def test_l17_02_single_line_positive_control_still_hits():
    """正向对照：同一内容单行书写（真阳性）仍命中——修复不误杀。"""
    mats = [_mat(32, "劳务合同-戊.pdf", "劳务作业负责施工方案编制。", role="labor")]
    f = _find("L17-02", mats)
    assert f.status == STATUS_HIT


def test_l17_02_no_labor_no_packet_undecidable():
    f = _find("L17-02", [])
    assert f.status == STATUS_UNDECIDABLE
    assert "无劳务合作合同且无报审资料包" in f.message


# ==================== W16-05 × L17 重叠去重元数据（公共纯函数） ====================

def _w16_05_l17_all_hit_mats():
    """一份劳务材料同时点亮 W16-05（施工范围+施工计费）、L17-01（计取材料费）、
    L17-02（方案责任派给劳务侧）。"""
    return [_mat(3, "劳务合同.pdf",
                 "劳务合作协议：劳务企业承担路基工程施工，按工程量计取工程款。"
                 "劳务作业负责施工方案编制。劳务企业计取主要材料费。",
                 role="labor")]


def test_relations_w16_05_primary_when_overlapping_hit():
    findings = _run(_w16_05_l17_all_hit_mats())
    rel = a1_check.illegal_subcontract_relations(findings)
    assert rel["W16-05"] == {"primary": True,
                             "related_rule_ids": ["L17-01", "L17-02"],
                             "dedup_owner": None}
    assert rel["L17-01"] == {"primary": False, "related_rule_ids": ["W16-05"],
                             "dedup_owner": "W16-05"}
    assert rel["L17-02"] == {"primary": False, "related_rule_ids": ["W16-05"],
                             "dedup_owner": "W16-05"}


def test_relations_l17_independent_when_w16_05_not_hit():
    """W16-05 未命中时 L17 各自独立（primary=True，dedup_owner=None）。"""
    mats = [
        _mat(3, "劳务合同.pdf", "劳务合同：费用仅含劳务报酬。", role="labor"),
        _mat(4, "劳务合同-乙.pdf", "劳务企业计取主要材料费。", role="labor"),
    ]
    findings = _run(mats)
    by_id = {f.item_id: f for f in findings}
    assert by_id["W16-05"].status != STATUS_HIT
    assert by_id["L17-01"].status == STATUS_HIT
    rel = a1_check.illegal_subcontract_relations(findings)
    assert rel["L17-01"]["primary"] is True
    assert rel["L17-01"]["dedup_owner"] is None
    assert rel["W16-05"]["related_rule_ids"] == []


def test_relations_l17_miss_stays_independent_when_w16_05_hit():
    """去重只作用于命中的 L17 条目：W16-05 命中但 L17-02 未命中 → L17-02 独立。"""
    mats = [_mat(3, "劳务合同.pdf",
                 "劳务合作协议：劳务企业承担路基工程施工，按工程量计取工程款。"
                 "施工方案由承包人负责编制。",
                 role="labor")]
    findings = _run(mats)
    by_id = {f.item_id: f for f in findings}
    assert by_id["W16-05"].status == STATUS_HIT
    assert by_id["L17-02"].status == STATUS_MISS
    rel = a1_check.illegal_subcontract_relations(findings)
    assert rel["W16-05"]["related_rule_ids"] == []      # 只列实际命中的 L17
    assert rel["L17-02"] == {"primary": True, "related_rule_ids": [],
                             "dedup_owner": None}


def test_relations_w16_02_carries_negative_list_ids():
    nl = [_rc("NL-B-02", STATUS_HIT, quote="围堰", page=1, material_id=5)]
    mats = [_mat(5, "分包合同.pdf", "分包范围：围堰工程。", role="sub")]
    findings = _run(mats, negative_list_findings=nl)
    rel = a1_check.illegal_subcontract_relations(findings, ["NL-B-02"])
    assert rel["W16-02"] == {"primary": True, "related_rule_ids": ["NL-B-02"],
                             "dedup_owner": None}


def test_relations_shape_only_contains_present_item_ids():
    """只含 findings 中出现的 item_id；其余条目 primary=True、related=[]、owner=None。"""
    findings = _run([])
    rel = a1_check.illegal_subcontract_relations(findings)
    assert set(rel.keys()) == {f.item_id for f in findings}
    for item_id, meta in rel.items():
        if item_id == "W16-02":
            continue
        assert meta == {"primary": True, "related_rule_ids": [], "dedup_owner": None}, item_id


def test_relations_dedup_related_read_from_catalog():
    """关联规则清单读目录 W16-05 illegal_detection.dedup_related（规则=数据）。"""
    rule = next(r for r in rules.load_transfer_rules()["rules"] if r["id"] == "W16-05")
    assert rule["illegal_detection"]["dedup_related"] == ["L17-01", "L17-02"]


def test_relations_accepts_plain_namespaces_pure_function():
    """公共纯函数契约：接受任意带 item_id/status 的对象，不依赖模型/数据库。"""
    fake = [SimpleNamespace(item_id="W16-05", status=STATUS_HIT),
            SimpleNamespace(item_id="L17-01", status=STATUS_HIT)]
    rel = a1_check.illegal_subcontract_relations(fake)
    assert rel["W16-05"]["related_rule_ids"] == ["L17-01"]
    assert rel["L17-01"]["dedup_owner"] == "W16-05"
    assert rel["L17-01"]["primary"] is False


# ==================== 措辞红线（禁「挂靠」「已认定」；hit 线索语气） ====================

def test_messages_wording_discipline_rich_materials():
    """全量措辞扫描：禁「挂靠」「已认定」；命中 message 一律含「请人工复核」。"""
    mats = [
        _mat(1, "总包合同.pdf", "总包合同约定：主体工程不得分包。", role="general"),
        _mat(2, "分包合同.pdf",
             "分包合同：分包给某工程有限公司，分包范围：附属工程。"
             "分包单位以他人名义承揽分包工程，合同签订方与实际施工主体不一致。"
             "分包单位将部分工程再次分包给某施工队伍，实际施工由该队伍进行。",
             role="sub"),
        _mat(3, "劳务合同.pdf",
             "劳务合作协议：劳务企业承担路基工程施工，按工程量计取工程款。"
             "劳务作业负责施工方案编制。",
             role="labor"),
        _mat(4, "分包报审.pdf", "分包合同未经监理审查，亦未经发包人书面同意。",
             role="packet"),
    ]
    a3 = [_rc("RC-09", STATUS_HIT, quote="报审表未见总监理工程师", page=2, material_id=4)]
    findings = _run(mats, a3_findings=a3)
    assert len(findings) == 14
    for f in findings:
        assert "挂靠" not in (f.message or ""), f.item_id
        assert "已认定" not in (f.message or ""), f.item_id
        if f.status == STATUS_HIT:
            assert "请人工复核" in f.message, f.item_id


# ==================== 词表外置纪律（规则=数据） ====================

def test_illegal_detection_vocab_externalized():
    """内核不硬编码词表：14 条目录中非开放条款均携带 illegal_detection 词表/正则，
    且全部正则可编译（改数据 → 重新核查 → 结论变化）。"""
    import re as _re
    catalog = [r for r in rules.load_transfer_rules()["rules"]
               if r.get("article") in ("16", "17")]
    assert len(catalog) == 14
    for r in catalog:
        if r["id"] in _OPEN_TERM_IDS:
            assert "illegal_detection" not in r or not r["illegal_detection"].get(
                "hit_relation_patterns")
            continue
        det = r["illegal_detection"]
        assert det, r["id"]
        for key, val in det.items():
            if isinstance(val, list) and (key.endswith("patterns") or key.endswith("words")):
                for p in val:
                    _re.compile(p)      # 编译失败即 FAIL


# ==================== A1-RULE-04-HOTFIX：独立验收 F01-F13 + 日期反例回归 ====================
# 说明：本组用例逐条固化 R04-独立验收-2026-10-10.md 的 F01-F13 与两个 W16-03 日期反例。
# 判定纪律：否定/条件/制度引用/双重否定/跨对象拼接不得点亮 hit 或 miss；无法可靠
# 解释 → undecidable 并列缺什么；冲突判断先于 hit；不把一切降级掩盖正向样例。


def test_hotfix_f01_forbidden_clause_not_subcontract_fact():
    """F01：sub「不得将工程分包给个人。」禁止条款不是既成分包事实 → 不得 hit。"""
    mats = [_mat(2, "分包合同.pdf", "不得将工程分包给个人。", role="sub")]
    f = _find("W16-01", mats)
    assert f.status != STATUS_HIT
    assert f.status == STATUS_UNDECIDABLE


def test_hotfix_f02_l17_01_negation_prefix_not_hit():
    """F02：labor 否定前缀「劳务企业不得计取主要材料费」→ L17-01 不得 hit。"""
    mats = [_mat(3, "劳务合同.pdf", "劳务合同：劳务企业不得计取主要材料费。", role="labor")]
    f = _find("L17-01", mats)
    assert f.status != STATUS_HIT
    assert f.status == STATUS_UNDECIDABLE


def test_hotfix_f03_l17_01_condition_clause_not_hit():
    """F03：labor 条件句「若劳务企业计取主要材料费，则应纠正。」→ L17-01 不得 hit。"""
    mats = [_mat(3, "劳务合同.pdf", "劳务合同：若劳务企业计取主要材料费，则应纠正。",
                 role="labor")]
    f = _find("L17-01", mats)
    assert f.status != STATUS_HIT
    assert f.status == STATUS_UNDECIDABLE


def test_hotfix_f04_l17_02_negated_responsibility_not_hit():
    """F04：labor「劳务作业不负责施工方案编制。」否定责任 → L17-02 不得 hit。"""
    mats = [_mat(3, "劳务合同.pdf", "劳务作业不负责施工方案编制。", role="labor")]
    f = _find("L17-02", mats)
    assert f.status != STATUS_HIT
    assert f.status == STATUS_UNDECIDABLE


def test_hotfix_f05_v16_03_dual_negation_undecidable():
    """F05：sub「并非未设立现场管理机构」双重否定 → V16-03 不得 hit，不可判定。"""
    mats = [_mat(2, "分包合同.pdf", "分包范围：路基。并非未设立现场管理机构。", role="sub")]
    f = _find("V16-03", mats)
    assert f.status != STATUS_HIT
    assert f.status == STATUS_UNDECIDABLE
    assert "双重否定" in f.message


def test_hotfix_f06_w16_05_cross_project_never_hit():
    """F06：范围侧与计费侧跨项目/跨主体 → W16-05 不得 hit，不可判定。"""
    mats = [_mat(3, "a.pdf", "甲项目劳务合作合同：甲劳务企业承担路基工程施工。", role="labor"),
            _mat(4, "b.pdf", "乙项目劳务合同：乙劳务企业按工程量计取工程款。", role="labor")]
    f = _find("W16-05", mats)
    assert f.status != STATUS_HIT
    assert f.status == STATUS_UNDECIDABLE


def test_hotfix_f07_w16_05_conflict_before_hit():
    """F07：不承担施工/纯劳务 + 施工计费冲突 → 冲突判断先于 hit，不得 hit。"""
    mats = [_mat(3, "劳务合同.pdf",
                 "劳务合同：劳务企业不承担工程施工，仅承担劳务作业，按工程量计取工程款。",
                 role="labor")]
    f = _find("W16-05", mats)
    assert f.status != STATUS_HIT
    assert f.status == STATUS_UNDECIDABLE
    assert "冲突" in f.message


def test_hotfix_f08_v16_01_institutional_quote_not_hit():
    """F08：packet 制度引用「制度规定：未经…不得分包。」非既成事实 → V16-01 不得 hit。"""
    mats = [_mat(4, "制度.pdf", "制度规定：未经监理审查或未经发包人书面同意，不得分包。",
                 role="packet")]
    f = _find("V16-01", mats, a3_findings=[])
    assert f.status != STATUS_HIT


def test_hotfix_f09_v16_01_cross_object_never_hit():
    """F09：两材料分属甲/乙项目，非同一审查对象 → V16-01 不得 hit，不可判定。"""
    mats = [_mat(4, "a.pdf", "甲项目分包合同未经监理审查。", role="packet"),
            _mat(5, "b.pdf", "乙项目分包合同未经发包人书面同意。", role="packet")]
    f = _find("V16-01", mats, a3_findings=[])
    assert f.status != STATUS_HIT
    assert f.status == STATUS_UNDECIDABLE


def test_hotfix_f10_w16_03_scope_no_cross_reference():
    """F10：桥梁主体禁令 vs 绿化分包无对照 → W16-03 不得 hit，不可判定。"""
    mats = [_mat(1, "总包合同.pdf", "总包合同：桥梁主体不得分包。", role="general"),
            _mat(2, "分包合同.pdf", "分包合同：绿化工程施工。", role="sub")]
    f = _find("W16-03", mats)
    assert f.status != STATUS_HIT
    assert f.status == STATUS_UNDECIDABLE
    assert "不对应" in f.message or "同一范围" in f.message


def test_hotfix_f11_w16_03_negated_consent_not_false_miss():
    """F11：否定同意「2026年10月1日未经发包人书面同意」不得作合规依据、其日期不得
    作同意日期 → W16-03 不得假未命中（允许 hit 线索或不可判定，但不得 miss）。"""
    mats = [_mat(1, "总包合同.pdf", "总包合同：主体工程不得分包。", role="general"),
            _mat(2, "分包合同.pdf", "分包合同2026年10月9日签订。", role="sub"),
            _mat(3, "同意.pdf", "2026年10月1日未经发包人书面同意。", role="packet")]
    f = _find("W16-03", mats)
    assert f.status != STATUS_MISS


def test_hotfix_f12_v16_01_mixed_state_explicit_not_empty():
    """F12：混合态（一侧风险一侧合规）→ 不可判定但有明确状态说明，缺项非空、
    不得伪造缺证据。"""
    mats = [_mat(4, "报审.pdf", "已经监理审查合格。未经发包人书面同意。", role="packet")]
    f = _find("V16-01", mats, a3_findings=[])
    assert f.status == STATUS_UNDECIDABLE
    assert "混合态" in f.message
    assert "风险" in f.message and "合规" in f.message
    assert "缺：）" not in f.message and "缺：" not in f.message.split("混合态")[0]
    assert "待人工裁决" in f.message


def test_hotfix_f12b_v16_01_mixed_reverse_supervision_risk():
    """F12 反向：监理侧风险、同意侧合规 → 混合态，同样明确说明、缺项非空。"""
    mats = [_mat(4, "报审.pdf", "未经监理审查。发包人书面同意分包。", role="packet")]
    f = _find("V16-01", mats, a3_findings=[])
    assert f.status == STATUS_UNDECIDABLE
    assert "混合态" in f.message
    assert "风险" in f.message and "合规" in f.message
    assert "待人工裁决" in f.message


def test_hotfix_f13_cross_project_not_deduped():
    """F13：不同项目的 W16-05 与 L17-01 不得去重 → L17-01 primary=True、无 owner。"""
    mats = [_mat(3, "a.pdf", "甲项目劳务合作合同：甲劳务企业承担路基工程施工，按工程量计取工程款。",
                 role="labor"),
            _mat(4, "b.pdf", "乙项目劳务合同：乙劳务企业计取主要材料费。", role="labor")]
    findings = _run(mats)
    by_id = {f.item_id: f for f in findings}
    assert by_id["W16-05"].status == STATUS_HIT
    assert by_id["L17-01"].status == STATUS_HIT
    rel = a1_check.illegal_subcontract_relations(findings)
    assert rel["L17-01"]["primary"] is True
    assert rel["L17-01"]["dedup_owner"] is None
    assert rel["W16-05"]["related_rule_ids"] == []


def test_hotfix_w16_03_plan_date_not_event_date():
    """日期反例1：计划类前缀日期不作事件日期；缺可绑定日期 → 不可判定/不误判。"""
    mats = [_mat(1, "总包合同.pdf", "总包合同：主体工程不得分包。", role="general"),
            _mat(2, "分包合同.pdf", "分包合同：分包范围：附属工程。", role="sub"),
            _mat(3, "同意.pdf", "计划2026年10月1日发包人书面同意日期2026年10月12日。",
                 role="packet")]
    f = _find("W16-03", mats)
    assert f.status != STATUS_MISS


def test_hotfix_w16_03_other_project_consent_not_used():
    """日期反例2：其他项目（乙项目）同意日期不作本项目同意日期 → 不得误判 miss。"""
    mats = [_mat(1, "总包合同.pdf", "总包合同：主体工程不得分包。", role="general"),
            _mat(2, "分包合同.pdf", "分包合同2026年10月9日签订。", role="sub"),
            _mat(3, "同意.pdf", "乙项目2026年10月1日发包人书面同意。", role="packet")]
    f = _find("W16-03", mats)
    assert f.status != STATUS_MISS


# ---- HOTFIX 正向对照：不为修缺陷而误杀真阳性 ----

def test_hotfix_positive_w16_03_early_consent_still_miss():
    """正向：同意日期可绑定且不晚于签订日期 → 仍正确未命中。"""
    mats = [_mat(1, "总包合同.pdf",
                 "总包合同约定：主体工程不得分包。发包人书面同意日期为2024年3月1日。",
                 role="general"),
            _mat(2, "分包合同.pdf", "分包合同于2024年4月15日签订，分包范围：附属工程。",
                 role="sub")]
    f = _find("W16-03", mats)
    assert f.status == STATUS_MISS


def test_hotfix_positive_w16_03_hit_clause_and_subcontract():
    """正向：泛化禁令（主体工程）+ 分包发生 + 无同意 → 仍正确命中。"""
    mats = [_mat(1, "总包合同.pdf", "总包合同约定：主体工程不得分包。", role="general"),
            _mat(2, "分包合同.pdf", "分包合同：分包范围：附属工程。", role="sub")]
    f = _find("W16-03", mats)
    assert f.status == STATUS_HIT


def test_hotfix_positive_w16_01_individual_still_hit():
    """正向：分包给包工头 → W16-01 仍命中。"""
    mats = [_mat(2, "分包合同.pdf", "承包人将路基工程分包给包工头张某施工。", role="sub")]
    assert _find("W16-01", mats).status == STATUS_HIT


def test_hotfix_positive_l17_02_labor_responsibility_still_hit():
    """正向：劳务作业负责施工方案编制 → L17-02 仍命中。"""
    mats = [_mat(21, "劳务合同-甲.pdf", "劳务作业负责施工方案编制及现场配合。", role="labor")]
    assert _find("L17-02", mats).status == STATUS_HIT


def test_hotfix_positive_v16_01_risk_risk_still_hit():
    """正向：两侧均风险 → V16-01 仍命中。"""
    mats = [_mat(4, "报审.pdf", "分包合同未经监理审查，亦未经发包人书面同意。", role="packet")]
    assert _find("V16-01", mats, a3_findings=[]).status == STATUS_HIT


def test_hotfix_positive_v16_01_legal_legal_still_miss():
    """正向：两侧均合规 → V16-01 仍未命中。"""
    mats = [_mat(4, "报审.pdf", "已经监理审查合格。发包人书面同意分包。", role="packet")]
    assert _find("V16-01", mats, a3_findings=[]).status == STATUS_MISS


def test_hotfix_positive_w16_05_same_object_still_hit():
    """正向：同一材料内施工范围 + 施工计费 → W16-05 仍命中。"""
    mats = [_mat(3, "劳务合同.pdf",
                 "劳务合作协议：劳务企业承担路基工程施工，按工程量计取工程款。", role="labor")]
    assert _find("W16-05", mats).status == STATUS_HIT


def test_hotfix_positive_relations_same_object_still_deduped():
    """正向：同一材料同对象真实重叠 → 仍正确去重（不因 F13 误伤正向）。"""
    mats = [_mat(3, "劳务合同.pdf",
                 "劳务合作协议：劳务企业承担路基工程施工，按工程量计取工程款。"
                 "劳务作业负责施工方案编制。劳务企业计取主要材料费。", role="labor")]
    rel = a1_check.illegal_subcontract_relations(_run(mats))
    assert rel["L17-01"]["dedup_owner"] == "W16-05"
    assert rel["L17-02"]["dedup_owner"] == "W16-05"
    assert set(rel["W16-05"]["related_rule_ids"]) == {"L17-01", "L17-02"}
