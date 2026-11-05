# -*- coding: utf-8 -*-
"""blockchain_supervision W2 第一片：A1-01 金额占比 + A1-02 负面清单阈值比较 契约测试。

判定纪律锚点（03-需求基线 §6.2）：
- 含税口径不一致 → 不可判定，不静默统一；
- 双口径（含劳务/不含劳务）分歧 → 不可判定；
- 负面清单只做「触发词召回 + 抽数值再比」，抽不到数值 → 不可判定。
"""
from types import SimpleNamespace

import pytest

from app.plus.blockchain_supervision import a1_check, service
from app.plus.blockchain_supervision.models import (
    STATUS_HIT, STATUS_MISS, STATUS_UNDECIDABLE, BsFinding, BsTaskEvent,
)
from app.plus.blockchain_supervision.rules import (
    load_negative_matchers, load_report_checklist, negative_list_rules,
)

pytestmark = pytest.mark.integration

# ==================== 数据文件形状锁 ====================

def test_matchers_cover_all_rules():
    """21 叶负面清单逐条有匹配器；规则里引用的阈值 key 都有抽取器。"""
    matchers = load_negative_matchers()
    rules = negative_list_rules()
    rule_ids = [r["id"] for r in rules]
    assert len(rule_ids) == len(set(rule_ids)) == 21
    for r in rules:
        assert r["id"] in matchers["matchers"], r["id"]
        for th in r.get("thresholds", []):
            assert th["key"] in matchers["extractors"], (r["id"], th["key"])
            assert th["op"] in (">=", ">")
        assert r.get("threshold_logic", "all") in ("all", "any")
        if r.get("kind") == "province_delegated":
            assert matchers["matchers"][r["id"]]["triggers"] == []


def test_extractor_regex_compiled_with_num():
    """NUM 占位符必须被替换成数字捕获组（编译即验证，a1_check 模块加载时完成）。"""
    assert a1_check._EXTRACTORS["water_depth_m"].search("水深12米").group(1) == "12"
    assert a1_check._EXTRACTORS["water_depth_m"].search("水深12米").group(2) == "米"


def test_config_shape():
    """config.json 形状锁（W4 特色规则配置演示载体）：阈值 ∈ (0,1]、出处非空。"""
    from app.plus.blockchain_supervision.rules import load_config
    cfg = load_config()
    ar = cfg["amount_ratio"]
    assert 0 < float(ar["threshold"]) <= 1
    assert ar["comparison"] == "greater_or_equal"   # 已冻结：达到 80% 即命中
    assert ar["source_note"].strip()
    assert "80%" in ar["source_note"]      # AD-5：出处可指回口径原文


def test_amount_threshold_boundary_uses_config():
    """等于阈值命中（comparison=greater_or_equal），阈值读真配置。"""
    th = a1_check.amount_threshold()
    assert a1_check._verdict_of_ratio(th) == STATUS_HIT
    assert a1_check._verdict_of_ratio(th * (1 + 1e-9)) == STATUS_HIT


def test_threshold_change_flips_verdict(monkeypatch):
    """演示锁：改 config 阈值 → 同一占比结论翻转（证明阈值真走数据文件，非硬编码）。"""
    real = a1_check.load_config()
    assert a1_check._verdict_of_ratio(0.5) == STATUS_MISS   # 0.5 < 0.8 不命中
    monkeypatch.setattr(a1_check, "load_config",
                        lambda: {**real, "amount_ratio": {**real["amount_ratio"], "threshold": 0.3}})
    assert a1_check._verdict_of_ratio(0.5) == STATUS_HIT    # 阈值改 0.3 → 命中
    assert "0.3" in str(a1_check.amount_threshold()) or a1_check.amount_threshold() == 0.3


# ==================== A1-01 金额抽取 ====================

def _mat(mid, name, text="", role="sub"):
    pages = [{"page": 1, "text": text}] if text else []
    return SimpleNamespace(id=mid, file_name=name, role=role, parse_state="done",
                           pages_json=pages)


def _task():
    return SimpleNamespace(id=1)


def test_transfer_scope_explains_missing_total_scope_without_claiming_breach():
    sub = _mat(1, "分包合同.pdf", "分包范围：桥梁工程。发包人：甲公司；承包人：乙公司。")
    result = next(f for f in a1_check.run_transfer_scope_check(_task(), [sub]) if f.item_id == "T15-01")
    assert result.status == STATUS_UNDECIDABLE
    assert "总承包范围" in result.message
    assert "总包合同" in result.message
    assert "无法判断" in result.message


def test_amount_extract_incl_tax():
    mat = _mat(1, "总包合同.pdf", "合同金额：人民币12,000万元（含税）。", role="general")
    amt = a1_check.extract_contract_amount(mat)
    assert amt["found"] and amt["value_wan"] == 12000.0
    assert amt["tax"] == "incl" and amt["page"] == 1
    assert "12,000万元" in amt["quote"]


def test_contract_amount_cannot_use_unread_page():
    material = _mat(97, "总包合同.pdf", "合同金额：人民币12,000万元（含税）。", role="general")
    material.degradations_json = [{"kind": "parse_incomplete", "unread_pages": [1]}]
    assert not a1_check.extract_contract_amount(material)["found"]


def test_transfer_scope_cannot_use_unread_page_as_fact():
    general = _mat(98, "总包合同.pdf", "总承包范围：路基、路面、桥梁工程。发包人：甲公司；承包人：乙公司。承包人将全部工程交由丙公司施工。", role="general")
    sub = _mat(99, "分包合同.pdf", "分包范围：路基、路面、桥梁工程。", role="sub")
    assert next(f for f in a1_check.run_transfer_scope_check(_task(), [general, sub])
                if f.item_id == "T15-01").status == STATUS_HIT
    general.degradations_json = [{"kind": "parse_incomplete", "unread_pages": [1]}]
    finding = next(f for f in a1_check.run_transfer_scope_check(_task(), [general, sub]) if f.item_id == "T15-01")
    assert finding.status != STATUS_HIT


def test_transfer_scope_incomplete_contract_does_not_claim_no_hit():
    general = _mat(98, "总包合同.pdf", "总承包范围：路基、路面、桥梁工程。发包人：甲公司；承包人：乙公司。承包人自行负责施工组织。", role="general")
    sub = _mat(99, "分包合同.pdf", "分包范围：路基工程。", role="sub")
    before = next(f for f in a1_check.run_transfer_scope_check(_task(), [general, sub]) if f.item_id == "T15-01")
    assert before.status == STATUS_MISS
    general.pages_json.append({"page": 2, "text": "未读页面"})
    general.degradations_json = [{"kind": "parse_incomplete", "unread_pages": [2]}]
    finding = next(f for f in a1_check.run_transfer_scope_check(_task(), [general, sub]) if f.item_id == "T15-01")
    assert finding.status == STATUS_UNDECIDABLE
    assert "第2页" in finding.message and finding.message.startswith("不可判定")


def test_amount_extract_excl_tax():
    mat = _mat(2, "总包合同.pdf", "不含税合同金额为8000万元。", role="general")
    amt = a1_check.extract_contract_amount(mat)
    assert amt["found"] and amt["tax"] == "excl"


def test_amount_extract_unit_conversion():
    yuan = a1_check.extract_contract_amount(_mat(3, "a.pdf", "合同金额：50000000元。", role="general"))
    assert yuan["value_wan"] == pytest.approx(5000.0)
    yi = a1_check.extract_contract_amount(_mat(4, "b.pdf", "合同总价1.2亿元。", role="general"))
    assert yi["value_wan"] == pytest.approx(12000.0)


def test_amount_extract_signed_contract_price_with_tax_parenthesis():
    mat = _mat(
        31,
        "预制梁劳务合同.pdf",
        "签约合同价（含税）为人民币 8624411.02 元。",
        role="labor",
    )
    amt = a1_check.extract_contract_amount(mat)
    assert amt["found"] and amt["tax"] == "incl"
    assert amt["value_wan"] == pytest.approx(862.441102)


def test_amount_extract_ocr_tex_wrapped_value_and_unit():
    mat = _mat(
        32,
        "预制梁劳务合同-ocr.pdf",
        r"$\text{1. 签约合同价款（含税）为人民币 }8624411.02\text{元}$",
        role="labor",
    )
    amt = a1_check.extract_contract_amount(mat)
    assert amt["found"] and amt["tax"] == "incl"
    assert amt["value_wan"] == pytest.approx(862.441102)


def test_amount_extract_strong_keyword_wins():
    """泛「金额」会撞上保证金等杂项：强关键词（合同金额）优先。"""
    mat = _mat(5, "c.pdf", "履约保证金金额：50万元。合同金额：10000万元（含税）。", role="general")
    amt = a1_check.extract_contract_amount(mat)
    assert amt["value_wan"] == 10000.0


def test_amount_extract_missing():
    assert a1_check.extract_contract_amount(_mat(6, "d.pdf", "", role="general"))["found"] is False


# ==================== A1-01 三态判定 ====================

def _a1_task_with(general_text, sub_texts, labor_texts=()):
    mats = [_mat(1, "总包合同.pdf", general_text, role="general")]
    for i, t in enumerate(sub_texts):
        mats.append(_mat(10 + i, "分包合同-%d.pdf" % i, t, role="sub"))
    for i, t in enumerate(labor_texts):
        mats.append(_mat(50 + i, "劳务合同-%d.pdf" % i, t, role="labor"))
    return mats


def test_a1_01_hit_over_80():
    mats = _a1_task_with("合同金额：10000万元（含税）。", ["合同金额：9000万元（含税）。"])
    (f,) = a1_check.run_amount_ratio_check(_task(), mats)
    assert f.status == STATUS_HIT and f.item_id == "A1-01"
    th_txt = "%.0f%%" % (a1_check.amount_threshold() * 100)  # 文案随 config.json 阈值走
    assert "90.0%" in f.message and ("达到%s" % th_txt) in f.message
    assert f.page == 1 and f.quote


def test_a1_01_miss_under_80():
    mats = _a1_task_with("合同金额：10000万元（含税）。", ["合同金额：7000万元（含税）。"])
    (f,) = a1_check.run_amount_ratio_check(_task(), mats)
    assert f.status == STATUS_MISS and "70.0%" in f.message


def test_a1_01_exactly_80_is_hit():
    """冻结边界：达到 80% 即命中。"""
    mats = _a1_task_with("合同金额：10000万元（含税）。", ["合同金额：8000万元（含税）。"])
    (f,) = a1_check.run_amount_ratio_check(_task(), mats)
    assert f.status == STATUS_HIT


def test_a1_01_dual_basis_diverge_undecidable():
    """不含劳务 75%（miss）vs 含劳务 85%（hit）→ 分歧判不可判定，不二选一。"""
    mats = _a1_task_with("合同金额：10000万元（含税）。",
                         ["合同金额：7500万元（含税）。"],
                         ["合同金额：1000万元（含税）。"])
    (f,) = a1_check.run_amount_ratio_check(_task(), mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "累计口径变化" in f.message
    assert "75.0%" in f.message and "85.0%" in f.message


def test_a1_01_dual_basis_agree_hit():
    mats = _a1_task_with("合同金额：10000万元（含税）。",
                         ["合同金额：8800万元（含税）。"],
                         ["合同金额：500万元（含税）。"])
    (f,) = a1_check.run_amount_ratio_check(_task(), mats)
    assert f.status == STATUS_HIT and "93.0%" in f.message


def test_a1_01_tax_mismatch_undecidable():
    """含税 vs 不含税：9% 增值税足以把 79% 推过线 → 不可判定，不静默统一。"""
    mats = _a1_task_with("合同金额：10000万元（含税）。", ["合同金额：7900万元（不含税）。"])
    (f,) = a1_check.run_amount_ratio_check(_task(), mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "口径不一致" in f.message and "不静默统一" in f.message


def test_a1_01_tax_unknown_undecidable():
    mats = _a1_task_with("合同金额：10000万元。", ["合同金额：7000万元（含税）。"])
    (f,) = a1_check.run_amount_ratio_check(_task(), mats)
    assert f.status == STATUS_UNDECIDABLE and "未标注税额口径" in f.message


def test_a1_01_missing_amount_undecidable():
    mats = _a1_task_with("本合同自签订之日起生效。", ["合同金额：7000万元（含税）。"])
    (f,) = a1_check.run_amount_ratio_check(_task(), mats)
    assert f.status == STATUS_UNDECIDABLE and "缺：合同金额" in f.message


def test_a1_01_no_general_undecidable():
    (f,) = a1_check.run_amount_ratio_check(_task(), [_mat(9, "分包合同.pdf", "合同金额：1万元")])
    assert f.status == STATUS_UNDECIDABLE and "总包合同" in f.message


# ==================== A1-02 负面清单 ====================

def test_a1_02_workmethod_hit():
    """工法类条目：触发词确定性命中（盾构法隧道不得分包）。"""
    mats = [_mat(1, "分包合同-甲.pdf", "分包范围：盾构法施工隧道区间土建工程。", role="sub")]
    findings = a1_check.run_negative_list_check(_task(), mats)
    f = next(x for x in findings if x.item_id == "NL-T-02")
    assert f.status == STATUS_HIT and "盾构" in f.message and f.quote


def test_negative_list_does_not_turn_unread_ocr_into_a_hit():
    material = _mat(91, "分包合同.pdf", "分包范围：盾构法施工隧道区间土建工程。", role="sub")
    material.degradations_json = [{"kind": "parse_incomplete", "unread_pages": [1]}]
    findings = a1_check.run_negative_list_check(_task(), [material])
    assert not any(f.item_id == "NL-T-02" and f.status == STATUS_HIT for f in findings)


def test_negative_list_does_not_use_suspect_only_page_as_threshold_evidence():
    material = _mat(96, "分包合同.pdf", "分包范围：水深12米的围堰工程。", role="sub")
    material.degradations_json = [{"kind": "parse_incomplete", "ocr_suspect_pages": [1]}]
    findings = a1_check.run_negative_list_check(_task(), [material])
    assert not any(f.item_id == "NL-B-02" and f.status == STATUS_HIT for f in findings)


def test_negative_list_cannot_conclude_miss_when_other_contract_page_unread():
    material = _mat(92, "分包合同.pdf", "分包范围：水深8米的围堰工程。", role="sub")
    material.pages_json.append({"page": 2, "text": "其他范围未可靠识别"})
    material.degradations_json = [{"kind": "parse_incomplete", "unread_pages": [2]}]
    finding = next(f for f in a1_check.run_negative_list_check(_task(), [material]) if f.item_id == "NL-B-02")
    assert finding.status == STATUS_UNDECIDABLE and "第2页未读" in finding.message


def test_negative_list_keeps_verified_hit_when_other_page_is_unread():
    material = _mat(93, "分包合同.pdf", "分包范围：盾构法施工隧道区间土建工程。", role="sub")
    material.pages_json.append({"page": 2, "text": "其他页质量待核"})
    material.degradations_json = [{"kind": "parse_incomplete", "unread_pages": [2]}]
    finding = next(f for f in a1_check.run_negative_list_check(_task(), [material]) if f.item_id == "NL-T-02")
    assert finding.status == STATUS_HIT and finding.page == 1


def test_negative_list_cannot_conclude_miss_when_another_contract_failed():
    first = _mat(94, "分包合同.pdf", "分包范围：水深8米的围堰工程。", role="sub")
    failed = _mat(95, "另一份合同.pdf", "", role="sub")
    failed.parse_state = 'failed'
    finding = next(f for f in a1_check.run_negative_list_check(_task(), [first, failed]) if f.item_id == "NL-B-02")
    assert finding.status == STATUS_UNDECIDABLE and "未读" in finding.message


@pytest.mark.parametrize("text", [
    "本工程不采用附着式支撑架施工现浇梁。",
    "规范引用：《公路工程施工分包负面清单》规定：附着式支撑架施工现浇梁不得分包。",
    "若采用附着式支撑架施工现浇梁，则应调整分包范围。",
    "未来可能采用附着式支撑架施工现浇梁，实际分包范围尚待确认。",
])
def test_a1_02_nonfactual_method_mentions_are_undecidable(text):
    material = _mat(81, "分包合同.pdf", text, role="sub")
    finding = next(f for f in a1_check.run_negative_list_check(_task(), [material])
                   if f.item_id == "NL-B-04")
    assert finding.status == STATUS_UNDECIDABLE
    assert "事实" in finding.message and "核对" in finding.message
    assert finding.material_id == material.id and finding.page == 1


def test_a1_02_numeric_rule_quote_is_not_actual_scope_fact():
    material = _mat(82, "分包合同.pdf", "制度规定：水深12米的围堰工程不得进行施工分包。", role="sub")
    finding = next(f for f in a1_check.run_negative_list_check(_task(), [material])
                   if f.item_id == "NL-B-02")
    assert finding.status == STATUS_UNDECIDABLE
    assert "事实" in finding.message


@pytest.mark.parametrize("prefix", [
    "本工程不采用附着式支撑架。",
    "规范引用：附着式支撑架施工现浇梁不得分包。",
])
def test_a1_02_nonfactual_clause_does_not_hide_other_affirmative_scope(prefix):
    material = _mat(83, "分包合同.pdf",
                    prefix + "实际分包范围：附着式支撑架施工现浇梁。", role="sub")
    finding = next(f for f in a1_check.run_negative_list_check(_task(), [material])
                   if f.item_id == "NL-B-04")
    assert finding.status == STATUS_HIT
    assert "实际分包范围" in finding.quote


def test_a1_02_quoted_threshold_does_not_override_actual_threshold_in_other_clause():
    material = _mat(84, "分包合同.pdf",
                    "制度示例：水深12米的围堰工程不得分包。实际分包范围：水深8米的围堰工程。",
                    role="sub")
    finding = next(f for f in a1_check.run_negative_list_check(_task(), [material])
                   if f.item_id == "NL-B-02")
    assert finding.status == STATUS_MISS
    assert "8" in finding.quote and "12" not in finding.quote


def test_a1_02_numeric_quote_does_not_fill_missing_actual_threshold():
    material = _mat(85, "分包合同.pdf",
                    "规范引用：围堰工程，水深12米。实际分包范围：围堰工程，水深尚待核实。",
                    role="sub")
    finding = next(f for f in a1_check.run_negative_list_check(_task(), [material])
                   if f.item_id == "NL-B-02")
    assert finding.status == STATUS_UNDECIDABLE
    assert "缺" in finding.message


def test_a1_02_later_conditional_change_does_not_erase_actual_scope():
    material = _mat(86, "分包合同.pdf",
                    "实际分包范围：附着式支撑架施工现浇梁，若发生变更另行确认。", role="sub")
    finding = next(f for f in a1_check.run_negative_list_check(_task(), [material])
                   if f.item_id == "NL-B-04")
    assert finding.status == STATUS_HIT
    assert "实际分包范围" in finding.quote


@pytest.mark.parametrize("denial", ["没有采用", "并非采用"])
def test_a1_02_extended_denial_cannot_become_positive_scope(denial):
    material = _mat(87, "分包合同.pdf",
                    "本工程%s附着式支撑架施工现浇梁。" % denial, role="sub")
    finding = next(f for f in a1_check.run_negative_list_check(_task(), [material])
                   if f.item_id == "NL-B-04")
    assert finding.status == STATUS_UNDECIDABLE
    assert "否定" in finding.message


def test_a1_02_threshold_hit():
    mats = [_mat(2, "分包合同-乙.pdf", "分包范围：水深12米的围堰工程。", role="sub")]
    findings = a1_check.run_negative_list_check(_task(), mats)
    f = next(x for x in findings if x.item_id == "NL-B-02")
    assert f.status == STATUS_HIT and "12" in f.message and "围堰" in f.message


def test_a1_02_threshold_miss():
    mats = [_mat(3, "分包合同-丙.pdf", "分包范围：水深8米的围堰工程。", role="sub")]
    findings = a1_check.run_negative_list_check(_task(), mats)
    f = next(x for x in findings if x.item_id == "NL-B-02")
    assert f.status == STATUS_MISS


def test_a1_02_threshold_missing_undecidable():
    """难点3：抽不到数值 → 不可判定，绝不凭「围堰」描述判命中。"""
    mats = [_mat(4, "分包合同-丁.pdf", "分包范围：桥梁围堰工程施工。", role="sub")]
    findings = a1_check.run_negative_list_check(_task(), mats)
    f = next(x for x in findings if x.item_id == "NL-B-02")
    assert f.status == STATUS_UNDECIDABLE and "缺" in f.message


def test_a1_02_any_logic_hit():
    """NL-B-05：节段重量≥200吨 或 长度≥4.5米（any）——重量单腿成立即命中。"""
    mats = [_mat(5, "分包合同-戊.pdf", "分包范围：悬臂浇筑现浇梁，节段重量250吨。", role="sub")]
    findings = a1_check.run_negative_list_check(_task(), mats)
    f = next(x for x in findings if x.item_id == "NL-B-05")
    assert f.status == STATUS_HIT


def test_a1_02_all_logic_one_leg_fails_is_miss():
    """NL-T-06：长度>3000米 且 断面≥150㎡（all）——长度达标但断面不足 → 未命中。"""
    mats = [_mat(6, "分包合同-己.pdf", "隧道全长3500米，V级围岩段开挖断面120平方米，分包施工。", role="sub")]
    findings = a1_check.run_negative_list_check(_task(), mats)
    f = next(x for x in findings if x.item_id == "NL-T-06")
    assert f.status == STATUS_MISS


def test_a1_02_context_gate_undecidable():
    """上跨但未见一级公路/铁路等上下文限定 → 不可判定，不硬判命中。"""
    mats = [_mat(7, "分包合同-庚.pdf", "分包范围：上跨乡道桥梁工程。", role="sub")]
    findings = a1_check.run_negative_list_check(_task(), mats)
    f = next(x for x in findings if x.item_id == "NL-B-11")
    assert f.status == STATUS_UNDECIDABLE and "上下文" in f.message


def test_a1_02_province_not_augmented_uses_written_scope():
    """上海细则第九条已明文「路基和路面暂不增补」→ 判未触发，并原文引用。

    这不是「默认未增补」：只有登记了省级书面表述的地区才走这条路径（对照组见下一条）。
    """
    mats = [_mat(8, "分包合同-辛.pdf", "路基填筑工程分包。", role="sub")]
    f = next(x for x in a1_check.run_negative_list_check(_task(), mats)
             if x.item_id == "NL-R-01")
    assert f.status == STATUS_MISS
    assert "暂不增补" in f.message and "第九条" in f.message
    assert "沪交行规〔2026〕8号" in f.source
    assert "缺：省级增补清单" not in f.message
    # 该条依据文本来自 OCR、尚未人工校对 ⇒ 引用时必须如实标注
    assert "待人工校对" in f.message


def test_a1_02_province_without_written_scope_stays_undecidable(monkeypatch):
    """未登记省级口径（或省级未就增补作表述）→ 不可判定，不得反推「未增补」。"""
    monkeypatch.setattr(a1_check, "load_province_negative_scope",
                        lambda: {"default_region": "某省", "regions": {}})
    mats = [_mat(8, "分包合同-辛.pdf", "路基填筑工程分包。", role="sub")]
    f = next(x for x in a1_check.run_negative_list_check(_task(), mats)
             if x.item_id == "NL-R-01")
    assert f.status == STATUS_UNDECIDABLE
    assert "缺：省级增补清单" in f.message


def test_a1_02_province_augmented_without_list_stays_undecidable(monkeypatch):
    """省级已公布增补清单但清单未入库 → 不可判定，缺件仍指向「省级增补清单」。"""
    monkeypatch.setattr(a1_check, "load_province_negative_scope", lambda: {
        "default_region": "某省",
        "regions": {"某省": {"status": "augmented"}},
    })
    mats = [_mat(8, "分包合同-辛.pdf", "路基填筑工程分包。", role="sub")]
    f = next(x for x in a1_check.run_negative_list_check(_task(), mats)
             if x.item_id == "NL-R-01")
    assert f.status == STATUS_UNDECIDABLE
    assert "已公布增补清单" in f.message


def test_a1_02_province_verified_scope_drops_ocr_note(monkeypatch):
    """人工校对后不再标「待人工校对」——标注跟着校对状态走，不写死在文案里。"""
    record = {"status": "not_augmented", "doc_title": "某细则", "doc_no": "某号",
              "article_label": "第九条", "quote": "路基路面暂不增补。",
              "human_verified": True}
    monkeypatch.setattr(a1_check, "load_province_negative_scope", lambda: {
        "default_region": "某省", "regions": {"某省": record}})
    mats = [_mat(8, "分包合同-辛.pdf", "路基填筑工程分包。", role="sub")]
    f = next(x for x in a1_check.run_negative_list_check(_task(), mats)
             if x.item_id == "NL-R-01")
    assert f.status == STATUS_MISS and "待人工校对" not in f.message


def test_a1_02_not_recalled_no_finding():
    """未召回的条目不出逐条结论（报告汇总说明覆盖面），只余省级授权条目。"""
    mats = [_mat(9, "分包合同-壬.pdf", "分包范围：路基土石方填筑与边坡防护。", role="sub")]
    findings = a1_check.run_negative_list_check(_task(), mats)
    assert [f.item_id for f in findings] == ["NL-R-01"]


def test_a1_02_keyword_across_line_breaks():
    """换行切词（盾构⏎法 / 水深⏎12米）在去空白归一化后必须召回并抽取。"""
    mats = [_mat(10, "分包合同-癸.pdf", "分包范围：盾构\n法施工隧道，围堰处水深\n12米。", role="sub")]
    findings = a1_check.run_negative_list_check(_task(), mats)
    by_id = {f.item_id: f for f in findings}
    assert by_id["NL-T-02"].status == STATUS_HIT
    assert by_id["NL-B-02"].status == STATUS_HIT


def test_a1_02_exception_recorded_not_flipped():
    """例外情形只提示不翻案：水深12米围堰 + 钢结构制作在场 → 仍命中，附人工终审提示。"""
    mats = [_mat(11, "分包合同-子.pdf", "分包范围：水深12米围堰工程，含钢结构制作和运输。", role="sub")]
    findings = a1_check.run_negative_list_check(_task(), mats)
    f = next(x for x in findings if x.item_id == "NL-B-02")
    assert f.status == STATUS_HIT and "例外" in f.message


def test_a1_02_cross_material_hit_beats_miss():
    """同一规则跨材料聚合：一份未命中、一份命中 → 取命中。"""
    mats = [
        _mat(12, "分包合同-丑.pdf", "围堰工程，水深6米。", role="sub"),
        _mat(13, "分包合同-寅.pdf", "围堰工程，水深15米。", role="sub"),
    ]
    findings = a1_check.run_negative_list_check(_task(), mats)
    f = next(x for x in findings if x.item_id == "NL-B-02")
    assert f.status == STATUS_HIT and f.material_id == 13


# ==================== A1←A3 依赖判定（V16-01 / L17-02） ====================

def _rc(item_id, status, quote=None, page=None, material_id=None):
    return SimpleNamespace(item_id=item_id, status=status, quote=quote,
                           page=page, material_id=material_id)


def test_v16_01_a3_not_run_undecidable():
    findings = a1_check.run_a3_dependent_checks(_task(), [], [])
    v = next(x for x in findings if x.item_id == "V16-01")
    # A3 未跑 → 不可判定
    assert v.status == STATUS_UNDECIDABLE and "A3-01" in v.message


def test_v16_01_source_item_exists_in_checklist():
    """契约：V16-01 取数源（A3 签章条目）必须在报审清单中真实存在且可被解析。

    a1_check._check_v16_01 硬编码取某条 A3 结论作 V16-01 的证据源；若该条 id
    在清单里被改名而未同步内核，rc09 恒为 None → V16-01 永久判「不可判定」，
    且因两种情形同 status（UNDECIDABLE），测试若无此条则完全不报警（静默失效）。
    本用例从清单**派生**签章条目 id 再喂回内核，改名即 FAIL —— 不硬编码 id。
    """
    items = load_report_checklist()
    # 签章条目 = content 类 + 封面（当前清单中唯一满足）
    seal = [it for it in items if it["kind"] == "content" and it.get("cover")]
    assert len(seal) == 1, "应有且仅有一条 content 类封面（签章）条目"
    seal_id = seal[0]["id"]

    # 用清单派生的 id 造一条「签章命中」结论喂给依赖内核
    hit = _rc(seal_id, STATUS_HIT, quote="报审表未见总监理工程师", page=2, material_id=7)
    v = next(x for x in a1_check.run_a3_dependent_checks(_task(), [hit], [])
             if x.item_id == "V16-01")
    # 若内核取数源 id 与清单不一致 → 取不到 → 落到「无签章结论」分支 → 此处 FAIL
    assert "A3-01 对表无总监签章结论" not in v.message, (
        "V16-01 取数源未命中清单签章条目 id=%s（内核硬编码 id 与清单已漂移，A1←A3 依赖静默失效）" % seal_id)
    assert v.status == STATUS_HIT and v.page == 2


def test_v16_01_rc09_hit_propagates():
    """总监签章缺席（A3-01 命中）→ V16-01 命中，引用 RC-09 证据位。"""
    rc09 = _rc("RC-09", STATUS_HIT, quote="报审表未见总监理工程师", page=2, material_id=7)
    findings = a1_check.run_a3_dependent_checks(_task(), [rc09], [])
    v = next(x for x in findings if x.item_id == "V16-01")
    assert v.status == STATUS_HIT and v.page == 2 and v.material_id == 7
    assert "未经监理审查" in v.message


def test_v16_01_rc09_miss_is_undecidable():
    """或逻辑从严：签章在场只解决监理侧，发包人侧无 A3 条目 → 不可判定。"""
    rc09 = _rc("RC-09", STATUS_MISS)
    findings = a1_check.run_a3_dependent_checks(_task(), [rc09], [])
    v = next(x for x in findings if x.item_id == "V16-01")
    assert v.status == STATUS_UNDECIDABLE and "发包人书面同意" in v.message


def test_v16_01_rc09_undecidable_propagates():
    rc09 = _rc("RC-09", STATUS_UNDECIDABLE)
    findings = a1_check.run_a3_dependent_checks(_task(), [rc09], [])
    v = next(x for x in findings if x.item_id == "V16-01")
    assert v.status == STATUS_UNDECIDABLE and "证据不足" in v.message


def test_l17_02_labor_responsibility_hit():
    """劳务合同把方案编制责任派给劳务侧 → 命中。"""
    mats = [_mat(21, "劳务合同-甲.pdf", "劳务作业负责施工方案编制及现场配合。", role="labor")]
    findings = a1_check.run_a3_dependent_checks(_task(), [], mats)
    f = next(x for x in findings if x.item_id == "L17-02")
    assert f.status == STATUS_HIT and "施工方案" in f.quote


def test_l17_02_packet_sign_hit():
    """报审包方案署名为劳务方 → 命中（第十四条反向证据）。"""
    mats = [_mat(22, "报审包-甲·专项方案.pdf", "专项施工方案，编制单位：某某劳务有限公司", role="packet")]
    findings = a1_check.run_a3_dependent_checks(_task(), [], mats)
    f = next(x for x in findings if x.item_id == "L17-02")
    assert f.status == STATUS_HIT and "劳务" in f.quote


def test_l17_02_gc_responsible_miss():
    """劳务合同明确方案由承包人负责 → 未命中。"""
    mats = [_mat(23, "劳务合同-乙.pdf", "施工方案由承包人负责编制，劳务作业按图施工。", role="labor")]
    findings = a1_check.run_a3_dependent_checks(_task(), [], mats)
    f = next(x for x in findings if x.item_id == "L17-02")
    assert f.status == STATUS_MISS and "承包人" in f.message


def test_l17_02_no_evidence_undecidable():
    mats = [_mat(24, "劳务合同-丙.pdf", "劳务作业范围：钢筋绑扎。", role="labor")]
    findings = a1_check.run_a3_dependent_checks(_task(), [], mats)
    f = next(x for x in findings if x.item_id == "L17-02")
    assert f.status == STATUS_UNDECIDABLE and "缺" in f.message


def test_l17_02_hit_beats_miss_across_channels():
    """命中通道优先：一份劳务合同派责劳务、另一处署名总包 → 取命中。"""
    mats = [
        _mat(25, "劳务合同-丁.pdf", "劳务作业负责试验检测配合。", role="labor"),
        _mat(26, "劳务合同-戊.pdf", "方案编制由承包人负责。", role="labor"),
    ]
    findings = a1_check.run_a3_dependent_checks(_task(), [], mats)
    f = next(x for x in findings if x.item_id == "L17-02")
    assert f.status == STATUS_HIT


# ==================== L17-02 跨同一容器子材料证据追溯（T11-D-A §二.7） ====================
# L17-02 保持任务级单条 finding（不拆分父容器）；本组用例证明命中引文锚点落在**正确
# 的报审包子材料**上（container_material_id / container_entry_path 表明来源），页码为该
# 子 PDF 的原始 1-based 页码。

def _pkt_child(mid, container_id, entry_path, texts):
    """ZIP 报审包派生的一份子 PDF（role 继承 packet，带容器来源两列）。"""
    pages = [{"page": i + 1, "text": t} for i, t in enumerate(texts)]
    return SimpleNamespace(id=mid, file_name="报审包.zip / %s" % entry_path, role="packet",
                           parse_state="done", pages_json=pages,
                           container_material_id=container_id, container_entry_path=entry_path)


def test_l17_02_packet_evidence_anchors_to_child_material():
    """命中引文来自**正确子材料**：署名在 02 号子 PDF → material_id 指向它、页码为原始页。"""
    mats = [
        _pkt_child(201, container_id=9, entry_path="01-封面.pdf",
                   texts=["报审资料封面", "编制单位：某某建筑集团"]),
        # 署名在第二页（原始 1-based 页码=2）
        _pkt_child(202, container_id=9, entry_path="02-专项方案.pdf",
                   texts=["专项施工方案目录", "专项施工方案，编制单位：某某劳务有限公司"]),
    ]
    findings = a1_check.run_a3_dependent_checks(_task(), [], mats)
    f = next(x for x in findings if x.item_id == "L17-02")
    assert f.status == STATUS_HIT
    assert f.material_id == 202, "证据须锚定到署名所在的子材料（02-专项方案.pdf）"
    assert f.page == 2, "页码须为该子 PDF 的原始 1-based 页码"
    assert "劳务" in f.quote


def test_l17_02_no_packet_signature_still_single_task_level_finding():
    """同容器多子材料、均无署名 → 仍只出一条任务级 finding（不因容器内 N 份而拆 N 条）。"""
    mats = [
        _pkt_child(211, container_id=11, entry_path="01.pdf", texts=["封面"]),
        _pkt_child(212, container_id=11, entry_path="02.pdf", texts=["清单"]),
    ]
    findings = a1_check.run_a3_dependent_checks(_task(), [], mats)
    l17 = [x for x in findings if x.item_id == "L17-02"]
    assert len(l17) == 1, "L17-02 是任务级单条结论，不得按报审包子材料拆组"
    assert l17[0].status == STATUS_UNDECIDABLE


def test_run_a1_checks_includes_dependency(db_session):
    """编排接线：/check/a1 输出含依赖两项；A3 已跑时 V16-01 读 RC-09 结论。"""
    task = service.create_task(db_session, conversation_id=101, title="A1+A3",
                               scope_a1=True, scope_a3=True, created_by=1)
    subject = service.create_subject(
        db_session, task, name="合成专业分包单位", subject_type="professional",
    )
    g = service.add_material(db_session, task, file_name="总包合同.pdf", role="general")
    s = service.add_material(db_session, task, file_name="分包合同.pdf", role="sub",
                             subject_id=subject.id)
    g.parse_state = "done"
    g.pages_json = [{"page": 1, "text": "合同金额：10000万元（含税）。"}]
    s.parse_state = "done"
    s.pages_json = [{"page": 1, "text": "合同金额：7000万元（含税）。分包范围：路基土石方。"}]
    db_session.commit()
    # 先跑 A3-01（产生 RC-09 不可判定结论：无报审包）
    service.run_completeness(db_session, task)
    findings = service.run_a1_checks(db_session, task)
    item_ids = {f.item_id for f in findings}
    assert {"V16-01", "L17-02"} <= item_ids
    # 占比类默认关闭：分母是总包合同金额，且业主部署文件二、（六）把「分包合同总合同额匹配」
    # 列为后续功能（能力保留，见 test_ratio_kernels_gated_by_config）。
    assert "A1-01" not in item_ids
    assert service.a1_check._WORKLOAD_ITEM_ID not in item_ids
    assert "A1-03" not in item_ids
    v16 = next(f for f in findings if f.item_id == "V16-01")
    assert v16.status == STATUS_UNDECIDABLE  # 无报审包 → RC-09 不可判定 → 传导


def test_ratio_kernels_gated_by_config(db_session, monkeypatch):
    """占比类默认关闭、开关打回 true 即纳入 —— 是「本期不纳入」不是删功能。

    依据：业主部署文件二、（六）把「分包合同总合同额匹配」列为后续功能，且该项分母是总包
    合同金额；口径与开关说明见 data/config.json 的 enabled / enabled_source_note。
    """
    from app.plus.blockchain_supervision import rules as bs_rules

    task = service.create_task(db_session, conversation_id=101, title="占比开关",
                               scope_a1=True, scope_a3=False, created_by=1)
    g = service.add_material(db_session, task, file_name="总包合同.pdf", role="general")
    s = service.add_material(db_session, task, file_name="分包合同.pdf", role="sub")
    g.parse_state = "done"
    g.pages_json = [{"page": 1, "text": "合同金额：10000万元（含税）。"}]
    s.parse_state = "done"
    s.pages_json = [{"page": 1, "text": "合同金额：7000万元（含税）。分包范围：路基土石方。"}]
    db_session.commit()

    off = service.run_a1_checks(db_session, task)
    assert not [f for f in off if f.kind in ("amount_ratio", "workload_ratio")]

    enabled = {key: (dict(value) if isinstance(value, dict) else value)
               for key, value in bs_rules.load_config().items()}
    enabled["amount_ratio"]["enabled"] = True
    enabled["workload_ratio"]["enabled"] = True
    monkeypatch.setattr(service, "load_config", lambda: enabled)   # 打桩落在实调用命名空间
    kinds = {f.kind for f in service.run_a1_checks(db_session, task)}
    assert {"amount_ratio", "workload_ratio"} <= kinds


# ==================== 编排与落库 ====================

def test_run_a1_checks_persists(db_session):
    task = service.create_task(db_session, conversation_id=101, title="A1",
                               scope_a1=True, scope_a3=False, created_by=1)
    g = service.add_material(db_session, task, file_name="总包合同.pdf", role="general")
    s = service.add_material(db_session, task, file_name="分包合同.pdf", role="sub")
    g.parse_state = "done"
    g.pages_json = [{"page": 1, "text": "合同金额：10000万元（含税）。"}]
    s.parse_state = "done"
    s.pages_json = [{"page": 1, "text": "合同金额：9000万元（含税）。分包范围：盾构法隧道。"}]
    db_session.commit()
    findings = service.run_a1_checks(db_session, task)
    assert findings
    assert db_session.query(BsFinding).filter_by(task_id=task.id).count() == len(findings)
    ev = db_session.query(BsTaskEvent).filter_by(task_id=task.id, event_type="check.completed").all()
    assert len(ev) == 1 and ev[0].payload_json["kind"] == "a1"


def test_run_a1_checks_scope_gate(db_session):
    task = service.create_task(db_session, conversation_id=102, title="A3 only",
                               scope_a1=False, scope_a3=True, created_by=1)
    with pytest.raises(ValueError):
        service.run_a1_checks(db_session, task)


# ==================== 工程量占比（内部 ID A1-X-WORKLOAD，30% 口径，config.workload_ratio） ====================
# T11-D-A：原 A1-03 编号已归还业主语义「转包情形识别」；30% 工程量占比改用内部 ID。

_WL_ID = "A1-X-WORKLOAD"


def _wl_mats(sub_text=None):
    mats = [_mat(1, "总包合同.pdf", "合同金额：10000万元（含税）。", role="general")]
    if sub_text:
        mats.append(_mat(2, "分包合同.pdf", sub_text, role="sub"))
    return mats


def test_workload_hit_over_30():
    mats = _wl_mats("劳务作业量占总工程量的35%，其余由乙方自行组织。")
    (f,) = a1_check.run_workload_ratio_check(_task(), mats)
    assert f.status == STATUS_HIT and f.item_id == _WL_ID
    assert f.kind == "workload_ratio"
    assert "35%" in f.message and "30%" in f.message
    assert f.page == 1 and "35%" in f.quote


def test_workload_miss_under_30():
    mats = _wl_mats("分包人承担的工程量占比为20%，服从总包统一管理。")
    (f,) = a1_check.run_workload_ratio_check(_task(), mats)
    assert f.status == STATUS_MISS and "20%" in f.message and "30%" in f.message


def test_workload_context_without_number_undecidable():
    """有工程量语境但未明示占比数值 → 不可判定，不做金额换算推断。"""
    mats = _wl_mats("分包范围内工程量以现场实际发生为准。")
    (f,) = a1_check.run_workload_ratio_check(_task(), mats)
    assert f.status == STATUS_UNDECIDABLE
    assert "未明示" in f.message and "不做金额换算" in f.message


def test_workload_no_context_undecidable():
    mats = _wl_mats("合同金额：9000万元（含税）。分包范围：桥梁桩基。")
    (f,) = a1_check.run_workload_ratio_check(_task(), mats)
    assert f.status == STATUS_UNDECIDABLE and "未明示" in f.message


def test_workload_no_contracts_undecidable():
    (f,) = a1_check.run_workload_ratio_check(_task(), _wl_mats())
    assert f.status == STATUS_UNDECIDABLE and "缺：合同文本" in f.message


def test_workload_threshold_from_config(monkeypatch):
    """30% 预警线走 config.json（与金额阈值同一配置机制）：改 0.3→0.1 结论翻转。"""
    from app.plus.blockchain_supervision.rules import load_config
    real = load_config()
    mats = _wl_mats("分包人承担的工程量占比为20%。")
    assert a1_check.run_workload_ratio_check(_task(), mats)[0].status == STATUS_MISS
    monkeypatch.setattr(
        a1_check, "load_config",
        lambda: {**real, "workload_ratio": {**real["workload_ratio"], "threshold": 0.1}})
    f = a1_check.run_workload_ratio_check(_task(), mats)[0]
    assert f.status == STATUS_HIT and "10%" in f.message


# ==================== A1-01 文案不得借挂转包语义（T11-D-A §三 Task1） ====================

def test_amount_hit_message_has_no_undertaking_wording():
    """A1-01 命中文案不得暗示「转包」——转包情形识别属 A1-03，本包未实现。

    金额比例判定本身不变（仍为 hit），只清洗误导性措辞。
    """
    mats = _a1_task_with("合同金额：10000万元（含税）。", ["合同金额：9000万元（含税）。"])
    (f,) = a1_check.run_amount_ratio_check(_task(), mats)
    assert f.status == STATUS_HIT
    assert "转包" not in f.message
    assert "人工复核" in f.message          # 仍保留人工复核建议
    # 该结论的 item_id 仍是业主编号 A1-01，未被误标为 A1-03
    assert f.item_id == "A1-01"


def test_amount_miss_message_unchanged_semantics():
    """未命中金额占比文案不受清洗影响（无转包措辞，本就不含）。"""
    mats = _a1_task_with("合同金额：10000万元（含税）。", ["合同金额：7000万元（含税）。"])
    (f,) = a1_check.run_amount_ratio_check(_task(), mats)
    assert f.status == STATUS_MISS and "转包" not in f.message


# ==================== 模拟试点材料回归锁 ====================

def test_amount_extract_labor_remuneration():
    """真实劳务合同措辞「劳务报酬」（试点材料试跑 2026-09-21 发现的缺口）：回归锁。"""
    amt = a1_check.extract_contract_amount(_mat(30, "劳务合作合同.pdf", "劳务报酬：人民币1200万元（含税）。", role="labor"))
    assert amt["found"] and amt["value_wan"] == pytest.approx(1200.0)
    assert amt["tax"] == "incl" and "1200万元" in amt["quote"]
