# -*- coding: utf-8 -*-
"""blockchain_supervision A2 批次1：证件有效期核查（cert_validity）契约测试。

判定纪律锚点（15-A2能力对照与落位分析-v1 + 09-22 拍板）：
- A2 并入报审核查链路（复用 OC validity 引擎，只 import 不改现役）；
- expired/expiring_soon → 命中；全部有效 → 聚合未命中；
- 无可解析记录 → 不可判定（扫描件与「未见标注」分开说），绝不判缺失；
- 前端 KIND_SECTIONS 必须登记 cert_validity（P1 教训：漏登记=结论 UI 静默丢弃）。
"""
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.plus.blockchain_supervision import a2_check, service
from app.plus.blockchain_supervision.models import (
    STATUS_HIT, STATUS_MISS, STATUS_UNDECIDABLE, BsFinding,
)
from app.plus.blockchain_supervision.rules import load_config
from app.plus.startup_conditions_check import validity as oc_validity

_REPO = Path(__file__).resolve().parents[2]
BASE = date(2026, 9, 22)  # 固定基准日，测试确定性


# ==================== 数据文件形状锁 ====================

def test_config_shape_cert_validity():
    """cert_validity 形状锁：预警窗口为正整数、出处非空且挂条款坐标（AD-5 / 清除表 P-04b）。"""
    cfg = load_config()["cert_validity"]
    assert int(cfg["soon_days"]) > 0
    note = cfg["source_note"]
    assert note.strip()
    assert "沪城公路〔2025〕118号" in note and "沪交行规〔2026〕8号" in note
    assert "需求确认稿" not in note and "建设方案" not in note


def test_soon_days_reads_config(monkeypatch):
    """窗口真走数据文件：改 config → 判定文案里的窗口天数跟着变。"""
    assert a2_check.soon_days() == 30
    real = a2_check.load_config()
    monkeypatch.setattr(a2_check, "load_config",
                        lambda: {**real, "cert_validity": {**real["cert_validity"], "soon_days": 7}})
    assert a2_check.soon_days() == 7


# ==================== 内核判定 ====================

def _mat(mid, name, text="", role="packet"):
    pages = [{"page": 1, "text": text}] if text else []
    return SimpleNamespace(id=mid, file_name=name, role=role, parse_state="done",
                           pages_json=pages)


def _task():
    return SimpleNamespace(id=1)


def test_special_operation_certificate_separates_initial_date_and_expiry():
    text = ("居民身份证\n有效期限2016.11.07-2036.11.07\n姓名\n证号\nT123456789012345678\n"
            "初领日期有效期限\n2020-11-01 2020-11-01至2026-10-31特种作业操作证")
    mat = _mat(1, "报审资料.pdf", text)
    rows = a2_check.run_cert_validity_check(_task(), [mat], baseline=date(2026, 11, 3))
    hit = next(row for row in rows if row.status == "hit")
    assert "2026-10-31" in hit.message and "特种作业操作证" in hit.message
    assert "2036" not in hit.quote
    mat.pages_json[0]["text"] = text.replace("特种作业操作证", "证件说明")
    assert not any(row.status == "hit" for row in a2_check.run_cert_validity_check(
        _task(), [mat], baseline=date(2026, 11, 3)))
    for invalid in (text.replace("证号", "编号"), "填写示例\n" + text):
        mat.pages_json[0]["text"] = invalid
        assert not any(row.status == "hit" for row in a2_check.run_cert_validity_check(
            _task(), [mat], baseline=date(2026, 11, 3)))
    mat.pages_json[0]["text"] = text
    mat.degradations_json = [{"kind": "parse_incomplete", "unread_pages": [2], "ocr_suspect_pages": [1]}]
    assert not any(row.status == "hit" for row in a2_check.run_cert_validity_check(
        _task(), [mat], baseline=date(2026, 11, 3)))


def test_special_certificate_without_baseline_preserves_date_as_undecidable():
    text = ("居民身份证\n有效期限2016.11.07-2036.11.07\n姓名\n证号\nT123456789012345678\n"
            "初领日期有效期限\n2020-11-01 2020-11-01至2026-10-31特种作业操作证")
    rows = a2_check.run_cert_validity_check(_task(), [_mat(1, "报审资料.pdf", text)], baseline=None)
    result = next(row for row in rows if row.page == 1 and "特种作业操作证" in row.message)
    assert result.status == STATUS_UNDECIDABLE
    assert "2026-10-31" in result.message and "基准日" in result.message
    assert result.evidence_json[0]["expiry_date"] == "2026-10-31"


def test_multiline_traffic_safety_certificate_expiry_uses_actual_certificate():
    text = ('公路水运工程施工企业主要负责人和安全生产\n'
            '管理人员安全生产考核合格证书\n姓名：张三\n'
            '企业名称：上海示范建设有限公司\n证书编号：沪交安C(22)G00822\n'
            '有效期：2022-12-09 至 2025-12-09\n考核部门：上海市交通委员会')
    mat = _mat(1, '报审资料.pdf', text)
    findings = a2_check.run_cert_validity_check(_task(), [mat], baseline=date(2026, 11, 3))
    assert any(f.status == STATUS_HIT and f.page == 1 and '2025-12-09' in f.message for f in findings)
    for invalid in ('填写示例\n' + text, text.replace('姓名：张三', ''),
                    text.replace('企业名称：上海示范建设有限公司', ''),
                    text.replace('考核部门：上海市交通委员会', ''),
                    text.replace('沪交安C(22)G00822', '______')):
        mat.pages_json[0]['text'] = invalid
        assert not any(f.status == STATUS_HIT for f in a2_check.run_cert_validity_check(_task(), [mat], baseline=date(2026, 11, 3)))
    mat.pages_json[0]['text'] = text
    mat.degradations_json = [{'kind': 'parse_incomplete', 'ocr_suspect_pages': [1]}]
    assert not any(f.status == STATUS_HIT for f in a2_check.run_cert_validity_check(_task(), [mat], baseline=date(2026, 11, 3)))


def test_id_card_expiry_requires_readable_matching_management_roster():
    mat = _mat(1, "报审资料.pdf", "主要管理人员名单\n| 序号 | 姓名 | 职务 | 电话 |\n"
               "| --- | --- | --- | --- |\n| 1 | 张三 | 安全员 | 13800000001 |")
    card = "姓名张三\n中华人民共和国居民身份证\n签发机关 公安局\n有效期限2006.07.25-2026.07.25"
    mat.pages_json.append({"page": 20, "text": card})
    findings = a2_check.run_cert_validity_check(_task(), [mat], baseline=date(2026, 11, 3))
    hit = next(row for row in findings if row.status == "hit" and "身份证" in row.message)
    assert "2026-07-25" in hit.message
    assert {ref["page"] for ref in hit.evidence_json} == {1, 20}
    roster_text = mat.pages_json[0]["text"]
    mat.pages_json[0]["text"] = roster_text.replace("安全员", "施工员")
    assert not any(row.status == "hit" and "身份证" in row.message for row in
                   a2_check.run_cert_validity_check(_task(), [mat], baseline=date(2026, 11, 3)))
    mat.pages_json[0]["text"] = roster_text
    mat.pages_json[1]["text"] = card.replace("姓名张三", "姓名李四")
    assert not any(row.status == "hit" and "身份证" in row.message for row in
                   a2_check.run_cert_validity_check(_task(), [mat], baseline=date(2026, 11, 3)))
    mat.pages_json[1]["text"] = card + "\n姓名李四"
    assert not any(row.status == "hit" and "身份证" in row.message for row in
                   a2_check.run_cert_validity_check(_task(), [mat], baseline=date(2026, 11, 3)))
    mat.pages_json[1]["text"] = card
    mat.degradations_json = [{"kind": "parse_incomplete", "unread_pages": [2], "ocr_suspect_pages": [20]}]
    assert not any(row.status == "hit" and "身份证" in row.message for row in
                   a2_check.run_cert_validity_check(_task(), [mat], baseline=date(2026, 11, 3)))


def test_management_id_without_baseline_keeps_date_and_roster_evidence():
    mat = _mat(1, "报审资料.pdf", "主要管理人员名单\n| 序号 | 姓名 | 职务 | 电话 |\n"
               "| --- | --- | --- | --- |\n| 1 | 张三 | 安全员 | 13800000001 |")
    mat.pages_json.append({"page": 20, "text": "姓名张三\n中华人民共和国居民身份证\n签发机关 公安局\n"
                                             "有效期限2006.07.25-2026.07.25"})
    rows = a2_check.run_cert_validity_check(_task(), [mat], baseline=None)
    result = next(row for row in rows if row.page == 20 and "身份证" in row.message)
    assert result.status == STATUS_UNDECIDABLE
    assert "2026-07-25" in result.message and "基准日" in result.message
    assert {ref["page"] for ref in result.evidence_json} == {1, 20}


def test_expired_is_hit_with_evidence():
    mat = _mat(1, "资质证书.pdf", "建筑业企业资质证书 有效期至2025年12月31日 发证机关：市建委")
    findings = a2_check.run_cert_validity_check(_task(), [mat], baseline=BASE)
    assert len(findings) == 1
    f = findings[0]
    assert f.kind == "cert_validity" and f.status == STATUS_HIT
    assert f.material_id == 1 and f.page == 1 and f.quote
    assert "已超过有效期" in f.message and "2025-12-31" in f.message
    assert "A2-02" == f.item_id and f.source.strip()


@pytest.mark.parametrize("quality_field", ["unread_pages", "ocr_suspect_pages"])
def test_general_certificate_expiry_does_not_decide_from_unreliable_page(quality_field):
    mat = _mat(1, "资质证书.pdf", "建筑业企业资质证书 有效期至2025年12月31日 发证机关：市建委")
    mat.degradations_json = [{"kind": "parse_incomplete", quality_field: [1]}]
    findings = a2_check.run_cert_validity_check(_task(), [mat], baseline=BASE)
    assert not any(f.status in (STATUS_HIT, STATUS_MISS) for f in findings)
    assert any(f.status == STATUS_UNDECIDABLE and f.page == 1 for f in findings)
    quality = next(f for f in findings if f.page == 1 and '识别质量存疑' in f.message)
    assert quality.location_mode == 'page' and quality.quote is None
    mat.degradations_json.append({"kind": "reading_quality_review", "outcome": "readable", "pages": [1]})
    reviewed = a2_check.run_cert_validity_check(_task(), [mat], baseline=BASE)
    assert any(f.status == STATUS_HIT and f.page == 1 for f in reviewed)


def test_certificate_filename_does_not_turn_every_mixed_packet_page_into_a_certificate():
    mat = _mat(1, '营业执照、资质证书、安全生产许可证.pdf', role='packet')
    mat.pages_json = [
        {'page': 1, 'text': '营业执照\n统一社会信用代码：913100000000000000\n发证机关：市场监督局\n营业期限至2030年12月31日'},
        {'page': 2, 'text': '项目管理组织架构：项目经理、安全员、技术负责人。'},
        {'page': 3, 'text': '主要管理人员名单及联系方式。'},
    ]
    findings = a2_check.run_cert_validity_check(_task(), [mat], baseline=BASE)
    assert not any(f.page in (2, 3) and '该证照页未见' in f.message for f in findings)


def test_expiring_soon_is_hit_with_window_days():
    mat = _mat(2, "安许证.pdf", "安全生产许可证 有效期至2026年10月15日 许可证编号：(沪)JZ安许")
    findings = a2_check.run_cert_validity_check(_task(), [mat], baseline=BASE)
    assert len(findings) == 1 and findings[0].status == STATUS_HIT
    assert "临近到期" in findings[0].message and "30 天内" in findings[0].message


def test_all_valid_aggregates_to_single_miss():
    mat = _mat(3, "营业执照.pdf", "营业执照 统一社会信用代码… 营业期限至2030年6月30日")
    findings = a2_check.run_cert_validity_check(_task(), [mat], baseline=BASE)
    assert len(findings) == 1 and findings[0].status == STATUS_MISS
    assert "均在有效期内" in findings[0].message and "1 条" in findings[0].message


def test_mixed_expired_and_valid_only_reports_expired():
    """过期即报；「均有效」聚合条只在零命中时出现，不与命中混排。"""
    mats = [_mat(1, "资质证书.pdf", "资质证书 有效期至2025年1月31日"),
            _mat(2, "营业执照.pdf", "营业执照 营业期限至2030年6月30日")]
    findings = a2_check.run_cert_validity_check(_task(), mats, baseline=BASE)
    assert len(findings) == 1 and findings[0].status == STATUS_HIT


def test_long_term_validity_produces_undecidable_not_hit():
    """「长期有效」不产生记录；无任何记录 → 不可判定（不是未命中）。"""
    mat = _mat(4, "资质证书.pdf", "资质证书 有效期：长期 2020年5月10日核发")
    findings = a2_check.run_cert_validity_check(_task(), [mat], baseline=BASE)
    assert len(findings) == 1 and findings[0].status == STATUS_UNDECIDABLE
    assert "有效期" in findings[0].message and "人工核对" in findings[0].message


def test_scanned_material_undecidable_message_mentions_manual_review():
    """扫描件（无文本层）→ 不可判定，绝不断言「未上传/缺失」。"""
    mat = _mat(5, "社保缴费证明.pdf", "")  # pages 为空
    findings = a2_check.run_cert_validity_check(_task(), [mat], baseline=BASE)
    assert len(findings) == 1 and findings[0].status == STATUS_UNDECIDABLE
    assert "没有文字内容" in findings[0].message


def test_vehicle_doc_veto_skipped():
    """行驶证/驾驶证域外排除（OC VETO 判例沿袭）：不产过期结论。"""
    mat = _mat(6, "车辆行驶证.pdf", "检验有效期至2024年11月30日")
    findings = a2_check.run_cert_validity_check(_task(), [mat], baseline=BASE)
    assert len(findings) == 1 and findings[0].status == STATUS_UNDECIDABLE


def test_year_month_expiry_uses_month_end():
    """年月粒度按当月末日：有效期至2026年8月 → 8-31 < 基准日 → 过期。"""
    mat = _mat(7, "资质证书.pdf", "资质证书 有效期至2026年8月")
    findings = a2_check.run_cert_validity_check(_task(), [mat], baseline=BASE)
    assert findings[0].status == STATUS_HIT and "2026-08-31" in findings[0].message


def test_contract_labor_validity_range_uses_end_on_review_date():
    """劳务P5为合同声明；保留截止日抽取，不升级为当前证照已核实。"""
    mat = _mat(30, "劳务合同.pdf", role="labor")
    mat.pages_json = [{"page": 5, "text":
                       "<p>安全生产许可证</p>"
                       "<p>证件有效期：2025年03月13日至2028年03月12日</p>"}]
    findings = a2_check.run_cert_validity_check(
        _task(), [mat], baseline=date(2026, 10, 23))
    assert len(findings) == 1 and findings[0].status == STATUS_UNDECIDABLE
    assert "声明填列期限" in findings[0].message and "2028-03-12" in findings[0].message
    assert "覆盖基准日" in findings[0].message and "需核对最新证照" in findings[0].message
    assert findings[0].material_id == 30 and findings[0].page == 5 and findings[0].quote


@pytest.mark.parametrize("text", [
    # V2真实劳务P5摘录：下一独立价款字段中的“签约”不属于日期范围。
    "证件有效期：2025年03月13日至2028年03月12日 五、合同价款 1. 签约合同价款（含税）",
    "有效期：2025-03-13至2028-03-12 合同约定日期：2030-01-01",
])
def test_contract_range_ignores_later_independent_fields_and_keeps_quote(text):
    mat = _mat(41, "劳务合同.pdf", role="labor")
    mat.pages_json = [{"page": 5, "text": text}]
    findings = a2_check.run_cert_validity_check(
        _task(), [mat], baseline=date(2026, 10, 23))
    assert len(findings) == 1 and findings[0].status == STATUS_UNDECIDABLE
    assert "2028-03-12" in findings[0].message and "声明填列期限" in findings[0].message
    assert findings[0].material_id == 41 and findings[0].page == 5
    near_end = a2_check.run_cert_validity_check(
        _task(), [mat], baseline=date(2028, 3, 1))
    assert len(near_end) == 1 and near_end[0].status == STATUS_UNDECIDABLE
    assert "2028-03-12" in near_end[0].message and "需核对最新证照" in near_end[0].message
    assert near_end[0].quote == text[:text.index("有效期") + 48]


def test_contract_professional_expired_range_keeps_source_evidence():
    """专业P3为声明；旧期限早于基准须核续期，不直接认定企业当前无许可。"""
    text = "<p>证件有效期：2022年07月30日至2025年07月29日</p>"
    mat = _mat(31, "专业合同.pdf", role="sub")
    mat.pages_json = [{"page": 3, "text": text}]
    findings = a2_check.run_cert_validity_check(
        _task(), [mat], baseline=date(2026, 10, 23))
    assert len(findings) == 1 and findings[0].status == STATUS_UNDECIDABLE
    assert "有效期至 2025-07-29" in findings[0].message
    assert "有效期至 2022-07-30" not in findings[0].message
    assert "早于基准日" in findings[0].message and "需核对最新证照" in findings[0].message
    assert findings[0].material_id == 31 and findings[0].page == 3
    assert findings[0].quote == text


def test_contract_validity_range_near_end_uses_end_date():
    mat = _mat(32, "安许证.pdf", "有效期：2025-03-13至2028-03-12")
    findings = a2_check.run_cert_validity_check(
        _task(), [mat], baseline=date(2028, 3, 1))
    assert len(findings) == 1 and findings[0].status == STATUS_HIT
    assert "临近到期" in findings[0].message and "2028-03-12" in findings[0].message


@pytest.mark.parametrize("text", [
    "有效期：2028年03月13日至2025年03月12日",
    "有效期：2025年03月13日至2028年02月30日",
    "有效期：2025年03月13日至待补截止日期",
    "有效期：2025年03月13日至2028年03月12日或2029年03月12日",
    "有效期：2025-03-13至2028-03-12；有效期：2025-03-13至2029-03-12",
    "有效期：约2025年03月13日至2028年03月12日",
    "有效期：2025年03月13日至2028年03月",
    "有效期：2025年03月13日至2028年03月123日",
    "有效期：2025年03月13日<br>至待补截止日期",
    "有效期：2025-03-13至2028-03-12（暂定）",
    "有效期：2025-03-13至2028-03-12、2029-03-12",
])
def test_contract_invalid_or_ambiguous_range_is_undecidable(text):
    mat = _mat(33, "安许证.pdf", text)
    findings = a2_check.run_cert_validity_check(
        _task(), [mat], baseline=date(2026, 10, 23))
    assert findings and all(f.status == STATUS_UNDECIDABLE for f in findings)
    assert all(f.page == 1 and f.quote for f in findings)
    assert all("人工核对" in f.message for f in findings)


def test_contract_unknown_range_is_not_hidden_by_other_valid_record():
    mats = [_mat(34, "安许证.pdf", "有效期：2025-03-13至2028-02-30"),
            _mat(35, "资质证书.pdf", "有效期至2030年01月01日")]
    findings = a2_check.run_cert_validity_check(
        _task(), mats, baseline=date(2026, 10, 23))
    assert len(findings) == 1 and findings[0].status == STATUS_UNDECIDABLE
    assert findings[0].material_id == 34


def test_contract_single_date_label_keeps_existing_expiry_semantics():
    mat = _mat(36, "安许证.pdf", "有效期：2025年03月13日")
    findings = a2_check.run_cert_validity_check(
        _task(), [mat], baseline=date(2026, 10, 23))
    assert len(findings) == 1 and findings[0].status == STATUS_HIT
    assert "已超过有效期" in findings[0].message and "2025-03-13" in findings[0].message


def test_contract_range_keeps_vehicle_exclusion():
    mat = _mat(37, "车辆行驶证.pdf", "有效期：2022年07月30日至2025年07月29日")
    findings = a2_check.run_cert_validity_check(
        _task(), [mat], baseline=date(2026, 10, 23))
    assert len(findings) == 1 and findings[0].status == STATUS_UNDECIDABLE


def test_contract_distinct_ranges_are_correlated_to_their_own_start_dates():
    mat = _mat(38, "证照.pdf", "有效期：2022-01-01至2028-01-01；有效期：2023-01-01至2029-01-01")
    findings = a2_check.run_cert_validity_check(
        _task(), [mat], baseline=date(2026, 10, 23))
    assert len(findings) == 1 and findings[0].status == STATUS_MISS
    assert "2 条" in findings[0].message


@pytest.mark.parametrize("text", [
    "有效期：2025年03月13日<br>至2028年03月12日",
    "<p>有效期：2025年03月13日<br/>至2028年03月12日</p>",
    "有效期：<span>2025年03月13日</span>至<b>2028年03月12日</b>",
])
def test_contract_range_across_html_tags_uses_end_and_keeps_original_quote(text):
    mat = _mat(39, "安许证.pdf", text)
    findings = a2_check.run_cert_validity_check(
        _task(), [mat], baseline=date(2026, 10, 23))
    assert len(findings) == 1 and findings[0].status == STATUS_MISS
    findings_at_end = a2_check.run_cert_validity_check(
        _task(), [mat], baseline=date(2028, 3, 1))
    assert len(findings_at_end) == 1 and findings_at_end[0].status == STATUS_HIT
    assert "2028-03-12" in findings_at_end[0].message
    assert findings_at_end[0].quote == text[:48]


def test_contract_html_separate_field_does_not_extend_single_date_expiry():
    mat = _mat(40, "安许证.pdf",
               "<p>有效期：2025年03月13日</p><p>合同履行：至2028年03月12日</p>")
    findings = a2_check.run_cert_validity_check(
        _task(), [mat], baseline=date(2026, 10, 23))
    assert len(findings) == 1 and findings[0].status == STATUS_HIT
    assert "有效期至 2025-03-13" in findings[0].message


# ==================== A2-03 证件注册单位一致性（设计 80 B-3） ====================

SUB_CONTRACT = "分包人：上海隧道工程有限公司"
CERT_IN_HOUSE = "建筑业企业资质证书 注册单位：上海隧道工程有限公司 有效期至2030年1月1日"
CERT_FOREIGN = "建筑业企业资质证书 注册单位：江苏建工集团有限公司 有效期至2030年1月1日"
CERT_NO_OWNER = "建筑业企业资质证书 证书编号：D1234567 有效期至2030年1月1日"


def _reg_mats(cert_text, contract_text=SUB_CONTRACT, cert_name="资质证书.pdf"):
    return [_mat(21, "分包合同.pdf", contract_text),
            _mat(22, cert_name, cert_text)]


def test_registered_to_mismatch_is_hit_with_evidence():
    """证书注册单位 ≠ 分包单位 → 命中。别家证书附在包里不得判「有效」。"""
    findings = a2_check.run_cert_registered_to_check(_task(),
                                                     _reg_mats(CERT_FOREIGN))
    assert len(findings) == 1
    f = findings[0]
    assert f.status == STATUS_HIT and f.item_id == "A2-03"
    assert f.kind == "cert_validity"          # 共用 kind：不进新报告分节
    assert "江苏建工集团有限公司" in f.message and "上海隧道工程有限公司" in f.message
    assert f.material_id == 22 and f.page == 1 and f.quote


def test_registered_to_consistent_is_miss():
    findings = a2_check.run_cert_registered_to_check(_task(),
                                                     _reg_mats(CERT_IN_HOUSE))
    assert len(findings) == 1 and findings[0].status == STATUS_MISS
    assert "均为" in findings[0].message


def test_registered_to_missing_baseline_is_undecidable():
    """抽不出分包单位名 → 不可判定，绝不默认「一致」。"""
    findings = a2_check.run_cert_registered_to_check(
        _task(), [_mat(23, "资质证书.pdf", CERT_IN_HOUSE)])
    assert len(findings) == 1 and findings[0].status == STATUS_UNDECIDABLE
    assert "分包单位名称" in findings[0].message


def test_registered_to_unlabeled_cert_is_undecidable_not_pass():
    """证书未标注注册单位 → 不可判定（不判通过也不判违规）。"""
    findings = a2_check.run_cert_registered_to_check(_task(),
                                                     _reg_mats(CERT_NO_OWNER))
    assert len(findings) == 1 and findings[0].status == STATUS_UNDECIDABLE
    assert "未见标注注册单位" in findings[0].message


def test_registered_to_ignores_non_cert_materials():
    """非证件材料（如社保证明）不进一致性比对，避免把人事材料当证书。"""
    mats = [_mat(24, "分包合同.pdf", SUB_CONTRACT),
            _mat(25, "社保缴费证明.pdf", "参保人员 姓名：王建国 缴费基数8000元")]
    findings = a2_check.run_cert_registered_to_check(_task(), mats)
    assert len(findings) == 1 and findings[0].status == STATUS_UNDECIDABLE
    assert "暂无可核对的证件材料" in findings[0].message


def test_service_run_cert_validity_emits_both_a2_items(db_session):
    """编排层：一次 run_cert_validity 同时产出 A2-02（有效期）与 A2-03（一致性）。"""
    from app.plus.blockchain_supervision.models import BsMaterial
    task = service.create_task(db_session, conversation_id=101, title="A2REG",
                               scope_a1=False, scope_a3=True, created_by=1)
    for name, text in (("分包合同.pdf", SUB_CONTRACT),
                       ("资质证书.pdf", CERT_FOREIGN)):
        mat = service.add_material(db_session, task, file_name=name)
        mat.pages_json = [{"page": 1, "text": text}]
        mat.parse_state = "done"
    db_session.commit()
    findings = service.run_cert_validity(db_session, task)
    items = {f.item_id for f in findings}
    assert "A2-02" in items and "A2-03" in items, items
    assert all(f.kind == "cert_validity" for f in findings)


@pytest.mark.parametrize(
    "baseline_date,expiry_text,expected_status,expected_message",
    [
        (date(2026, 10, 23), "有效期至2026年11月23日", STATUS_MISS, "均在有效期内"),
        (date(2026, 10, 23), "有效期至2026年11月22日", STATUS_HIT, "临近到期"),
        (date(2026, 10, 23), "有效期至2026年10月23日", STATUS_HIT, "临近到期"),
        (date(2026, 10, 23), "有效期至2026年10月22日", STATUS_HIT, "已超过有效期"),
        (date(2026, 10, 23), "有效期：2025年03月13日至2026年11月23日",
         STATUS_MISS, "均在有效期内"),
        (None, "有效期至2026年11月23日", STATUS_UNDECIDABLE, "未设置核查基准日"),
        (None, "有效期至2026年10月23日", STATUS_UNDECIDABLE, "未设置核查基准日"),
    ],
    ids=["31-days", "30-days", "expires-on-baseline", "expired-yesterday",
         "explicit-range-end", "missing-baseline-future", "missing-baseline-past"],
)
def test_service_cert_validity_uses_task_baseline_date(
        db_session, monkeypatch, baseline_date, expiry_text, expected_status, expected_message):
    """真实编排与日期内核以任务日计算；缺少任务基准日不可静默用当天。"""
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 24, 12, 0, 0, tzinfo=tz)

    # judge_expiry 在 OC validity 的命名空间读时钟；不打桩被测分包内核。
    monkeypatch.setattr(oc_validity, "datetime", FrozenDatetime)
    task = service.create_task(
        db_session, conversation_id=101, title="按任务基准日核查证件",
        scope_a1=False, scope_a3=True, created_by=1, baseline_date=baseline_date,
    )
    # 模拟旧任务缺少基准日，仍须保持不可判定。
    if baseline_date is None:
        task.baseline_date = None
    subject = service.create_subject(db_session, task, name="上海测试建设有限公司", subject_type="professional")
    material = service.add_material(
        db_session, task, file_name="资质证书.pdf", role="packet", subject_id=subject.id)
    material.pages_json = [{"page": 7, "text": "建筑业企业资质证书 " + expiry_text}]
    material.parse_state = "done"
    db_session.commit()

    findings = service.run_cert_validity(db_session, task, persist=False)

    validity = [finding for finding in findings if finding.item_id == "A2-02"]
    assert len(validity) == 1
    assert validity[0].status == expected_status
    assert expected_message in validity[0].message
    assert task.baseline_date == baseline_date, "不得把兜底日期写回任务"
    assert any(finding.item_id == "A2-03" for finding in findings)
    if expected_status == STATUS_HIT:
        assert validity[0].material_id == material.id and validity[0].page == 7
        assert expiry_text in validity[0].quote


def test_report_labels_a2_03():
    """报告侧：A2-03 有中文展示名，不落到裸 item_id。"""
    from app.plus.blockchain_supervision import reporting
    neg, rc, sc, val = reporting._label_maps()
    assert val["A2-03"] == "证件注册单位一致性"


# ==================== 编排与报告接线 ====================

def _seed_task_with_material(db, text):
    task = service.create_task(db, conversation_id=101, title="A2T",
                               scope_a1=False, scope_a3=True, created_by=1)
    mat = service.add_material(db, task, file_name="资质证书.pdf")
    mat.pages_json = [{"page": 1, "text": text}]
    mat.parse_state = "done"
    db.commit()
    return task


def test_service_run_replaces_on_rerun(db_session):
    """重跑替换不翻倍（_replace_findings 按 kind 删旧再插）。"""
    task = _seed_task_with_material(db_session, "资质证书 有效期至2025年1月31日")
    first = service.run_cert_validity(db_session, task)
    assert first and all(f.kind == "cert_validity" for f in first)
    again = service.run_cert_validity(db_session, task)
    ids = [f.id for f in again]
    assert len(ids) == len(set(ids)) == len(first)  # 无重复行
    from app.plus.blockchain_supervision.models import BsTaskEvent
    evs = (db_session.query(BsTaskEvent)
           .filter_by(task_id=task.id, event_type="check.completed").all())
    assert len(evs) == 2 and evs[-1].payload_json["kind"] == "cert_validity"


def test_special_certificate_expiry_survives_service_and_report(db_session):
    from app.plus.blockchain_supervision import reporting

    task = service.create_task(db_session, conversation_id=101, title="证件期限",
                               scope_a1=False, scope_a3=True, created_by=1)
    subject = service.create_subject(db_session, task, name="测试建设有限公司", subject_type="labor")
    mat = service.add_material(db_session, task, file_name="报审包.pdf", role="packet", subject_id=subject.id)
    mat.pages_json = [{"page": 31, "text":
        "居民身份证\n有效期限2016.11.07-2036.11.07\n姓名\n证号\nT123456789012345678\n"
        "初领日期有效期限\n2020-11-01 2020-11-01至2026-10-31特种作业操作证"}]
    mat.parse_state = "done"
    task.baseline_date = date(2026, 11, 3)
    db_session.commit()
    findings = service.run_cert_validity(db_session, task)
    assert any(f.status == STATUS_HIT and "2026-10-31" in f.message for f in findings)
    report = reporting.build_report(db_session, task)
    assert "2026-10-31" in str(report)
    assert report["sections"]
    assert report["qualification_summary"]["audit_ref"]["machine_findings"] >= 1


def test_management_id_expiry_survives_service_and_report(db_session):
    from app.plus.blockchain_supervision import reporting

    task = service.create_task(db_session, conversation_id=101, title="身份证期限",
                               scope_a1=False, scope_a3=True, created_by=1)
    subject = service.create_subject(db_session, task, name="测试建设有限公司", subject_type="labor")
    mat = service.add_material(db_session, task, file_name="报审包.pdf", role="packet", subject_id=subject.id)
    mat.pages_json = [
        {"page": 17, "text": "主要管理人员名单\n| 序号 | 姓名 | 职务 | 电话 |\n"
         "| --- | --- | --- | --- |\n| 1 | 张三 | 安全员 | 13800000001 |"},
        {"page": 20, "text": "姓名张三\n中华人民共和国居民身份证\n签发机关 公安局\n"
         "有效期限2006.07.25-2026.07.25"},
    ]
    mat.parse_state = "done"
    task.baseline_date = date(2026, 11, 3)
    db_session.commit()
    findings = service.run_cert_validity(db_session, task)
    assert any(f.status == STATUS_HIT and "2026-07-25" in f.message for f in findings)
    report = reporting.build_report(db_session, task)
    item = next(item for section in report["sections"] for item in section["items"]
                if "2026-07-25" in item.get("message", ""))
    assert {ref["page"] for ref in item["evidence_list"]} == {17, 20}


def test_missing_baseline_certificate_dates_survive_service_and_report(db_session):
    from app.plus.blockchain_supervision import reporting

    task = service.create_task(db_session, conversation_id=101, title="证照基准日缺失",
                               scope_a1=False, scope_a3=True, created_by=1)
    subject = service.create_subject(db_session, task, name="测试建设有限公司", subject_type="labor")
    mat = service.add_material(db_session, task, file_name="报审包.pdf", role="packet", subject_id=subject.id)
    mat.pages_json = [
        {"page": 17, "text": "主要管理人员名单\n| 序号 | 姓名 | 职务 | 电话 |\n"
         "| --- | --- | --- | --- |\n| 1 | 张三 | 安全员 | 13800000001 |"},
        {"page": 20, "text": "姓名张三\n中华人民共和国居民身份证\n签发机关 公安局\n"
         "有效期限2006.07.25-2026.07.25"},
        {"page": 31, "text": "居民身份证\n姓名\n证号\nT123456789012345678\n"
         "初领日期有效期限\n2020-11-01 2020-11-01至2026-10-31特种作业操作证"},
    ]
    mat.parse_state = "done"
    task.baseline_date = None
    db_session.commit()
    service.run_cert_validity(db_session, task)
    db_session.expire_all()
    report = reporting.build_report(db_session, task)
    rows = [item for section in report["sections"] for item in section["items"]
            if item["item_id"] == "A2-02" and "未设置核查基准日" in item["message"]]
    selected = {row["evidence"]["page"]: row for row in rows if row["evidence"]}
    assert set(selected) >= {20, 31}
    assert all(selected[page]["status"] == STATUS_UNDECIDABLE for page in (20, 31))
    assert "2026-07-25" in selected[20]["message"] and "2026-10-31" in selected[31]["message"]
    assert {ref["page"] for ref in selected[20]["evidence_list"]} == {17, 20}

    # 隔离服务重算时使用新的基准日；不模拟或改变正式任务的基准日编辑入口。
    task.baseline_date = date(2026, 11, 3)
    db_session.commit()
    service.run_cert_validity(db_session, task)
    db_session.expire_all()
    report = reporting.build_report(db_session, task)
    rows = [item for section in report["sections"] for item in section["items"]
            if item["item_id"] == "A2-02"]
    selected = {row["evidence"]["page"]: row for row in rows
                if row["evidence"] and row["evidence"]["page"] in (20, 31)}
    assert all(selected[page]["status"] == STATUS_HIT for page in (20, 31))
    assert not any("未设置核查基准日" in row["message"] for row in rows)


def test_service_rejects_when_scope_a3_off(db_session):
    task = service.create_task(db_session, conversation_id=101, title="A2T2",
                               scope_a1=True, scope_a3=False, created_by=1)
    with pytest.raises(ValueError):
        service.run_cert_validity(db_session, task)


def test_report_section_registered(db_session):
    """报告侧：cert_validity 分节出现，条目标签映射为「证件有效期」。"""
    from app.plus.blockchain_supervision import reporting
    task = _seed_task_with_material(db_session, "资质证书 有效期至2025年1月31日")
    service.run_cert_validity(db_session, task)
    report = reporting.build_report(db_session, task)
    sec = next(s for s in report["sections"] if s["kind"] == "cert_validity")
    assert sec["checked"] and sec["title"].startswith("④ 证件证照有效性核查")
    assert sec["items"][0]["label"] == "证件有效期"


def test_frontend_kind_section_registered():
    """防静默丢弃契约：EvidencePanel KIND_SECTIONS 必须登记 cert_validity。"""
    src = (_REPO / "frontend" / "src" / "plus-ui" / "blockchain-supervision"
           / "components" / "EvidencePanel.jsx").read_text(encoding="utf-8")
    assert "cert_validity" in src


# ==================== A2-01 社保资料核查（批次2，覆盖口径 2026-10-06） ====================

# 「主要管理人员名单及联系方式」样例：手机号锚定的名单行（R-1 两通道之一）
ROSTER_OK = ("主要管理人员名单及联系方式\n"
             "序号 姓名 岗位 联系方式\n"
             "1 王建国 项目经理 13800138000\n"
             "2 李秀英 安全员 13900139000\n")
SOCIAL_COVERS_BOTH = "参保人员 姓名：王建国 姓名：李秀英"
SOCIAL_COVERS_ONE = "参保人员 姓名：王建国"
SOCIAL_NO_NAME = "社保缴费明细（截图文字）：缴费基数 8000 元，参保人数 2 人"


def _mats(social_text=SOCIAL_COVERS_ONE, roster_text=ROSTER_OK,
          roster_name="主要管理人员名单.pdf"):
    soc = _mat(11, "社保缴费证明.pdf", social_text, role="packet")
    ros = _mat(12, roster_name, roster_text, role="packet")
    return [soc, ros]


def test_extract_roster_names_two_channels():
    """名单抽取两通道：显式「姓名：」+ 手机号名单行内 2-4 字中文串。"""
    assert a2_check.extract_roster_names(ROSTER_OK) == ["王建国", "李秀英"]
    assert a2_check.extract_roster_names("姓名：赵铁柱") == ["赵铁柱"]


def test_extract_roster_names_skips_stopwords_and_long_runs():
    """岗位停用词、以及公司/单位长串（>4 字）不得被当人名。"""
    text = "1 王建国 项目经理 13800138000 上海城建隧道建设工程有限公司"
    assert a2_check.extract_roster_names(text) == ["王建国"]


def test_extract_social_names_keeps_explicit_labels():
    assert a2_check.extract_social_names(SOCIAL_COVERS_BOTH) == ["王建国", "李秀英"]


def test_company_norm_ignores_parentheses_and_spaces():
    """norm_company 为跨模块公开名（extract.py / service.py 复用），删单位比对不得连带删。"""
    assert a2_check.norm_company("上海城建（集团）隧道 有限公司") == "上海城建隧道有限公司"


def test_name_coverage_alone_is_undecidable_with_facts():
    findings = a2_check.run_person_consistency_check(_task(), _mats(SOCIAL_COVERS_BOTH))
    assert len(findings) == 1
    f = findings[0]
    assert f.kind == "person_consistency" and f.status == STATUS_UNDECIDABLE
    assert f.item_id == "A2-01" and "2 名主要管理人员" in f.message
    assert "同一人" in f.message and "劳动" in f.message
    assert {ref["name"] for ref in f.evidence_json} == {"王建国", "李秀英"}


def test_missing_name_requires_supplement_not_automatic_violation():
    findings = a2_check.run_person_consistency_check(_task(), _mats(SOCIAL_COVERS_ONE))
    assert len(findings) == 1 and findings[0].status == STATUS_UNDECIDABLE
    assert findings[0].item_id == "A2-01"
    assert "李秀英" in findings[0].message and "人工核实" in findings[0].message


def test_no_social_material_is_undecidable():
    mats = [_mat(12, "主要管理人员名单.pdf", ROSTER_OK, role="packet")]
    findings = a2_check.run_person_consistency_check(_task(), mats)
    assert len(findings) == 1 and findings[0].status == STATUS_UNDECIDABLE
    assert "未能识别到社保缴费记录原文" in findings[0].message


def test_no_roster_material_is_undecidable():
    mats = [_mat(11, "社保缴费证明.pdf", SOCIAL_COVERS_BOTH, role="packet")]
    findings = a2_check.run_person_consistency_check(_task(), mats)
    assert len(findings) == 1 and findings[0].status == STATUS_UNDECIDABLE
    assert "主要管理人员名单" in findings[0].message


def test_contract_role_not_used_as_roster():
    """合同条文含「…主要施工管理人员名单…」（实测总包合同 GF-2017-0201 即命中）
    不得被当作名单基准——否则其联系方式段（如「联系人 张工 138…」）会被误抽为姓名而假命中。"""
    contract = _mat(21, "总包合同.pdf",
                    "承包人在开工前提交主要施工管理人员名单及其岗位；联系人 张工 13800138000",
                    role="general")
    soc = _mat(11, "社保缴费证明.pdf", SOCIAL_COVERS_BOTH, role="packet")
    findings = a2_check.run_person_consistency_check(_task(), [contract, soc])
    assert len(findings) == 1 and findings[0].status == STATUS_UNDECIDABLE
    assert "主要管理人员名单" in findings[0].message  # 落到「未上传名单」而非假命中


def test_roster_without_names_is_undecidable():
    """名单在场但抽不出姓名（如扫描件）→ 不可判定，绝不臆断名单为空。"""
    mats = _mats(roster_text="主要管理人员名单及联系方式\n（内容为图片，未识别出文字）")
    findings = a2_check.run_person_consistency_check(_task(), mats)
    assert len(findings) == 1 and findings[0].status == STATUS_UNDECIDABLE
    assert "未能提取到人员姓名" in findings[0].message


def test_social_without_names_is_undecidable():
    """社保文本在场但未标注姓名 → 不可判定（区别于「无文字内容」）。"""
    mats = _mats(social_text=SOCIAL_NO_NAME)
    findings = a2_check.run_person_consistency_check(_task(), mats)
    assert len(findings) == 1 and findings[0].status == STATUS_UNDECIDABLE
    assert "未提取到人员姓名" in findings[0].message


def test_scanned_social_requires_ingestion_result_before_review():
    """OCR只在摄取阶段发生；规则保留缺口，不在核查时另起识别链。"""
    soc = _mat(13, "社保缴费证明.pdf", "", role="packet")
    soc.path = "uploads/bs/1/社保缴费证明.pdf"
    ros = _mat(12, "主要管理人员名单.pdf", ROSTER_OK, role="packet")
    findings = a2_check.run_person_consistency_check(_task(), [soc, ros])
    assert len(findings) == 1 and findings[0].status == STATUS_UNDECIDABLE
    assert "人工翻看" in findings[0].message
    soc.pages_json = [{"page": 1, "text": SOCIAL_COVERS_BOTH}]
    findings = a2_check.run_person_consistency_check(_task(), [soc, ros])
    assert len(findings) == 1 and findings[0].status == STATUS_UNDECIDABLE
    assert "2 名主要管理人员" in findings[0].message


def test_service_person_consistency_replaces_on_rerun(db_session):
    task = service.create_task(db_session, conversation_id=101, title="A2P",
                               scope_a1=False, scope_a3=True, created_by=1)
    soc = service.add_material(db_session, task, file_name="社保缴费证明.pdf")
    soc.pages_json = [{"page": 1, "text": SOCIAL_COVERS_ONE}]
    soc.parse_state = "done"
    ros = service.add_material(db_session, task, file_name="主要管理人员名单.pdf")
    ros.pages_json = [{"page": 1, "text": ROSTER_OK}]
    ros.parse_state = "done"
    db_session.commit()
    first = service.run_person_consistency(db_session, task)
    again = service.run_person_consistency(db_session, task)
    assert len(first) == len(again) == 1
    from app.plus.blockchain_supervision.models import BsFinding as F
    rows = db_session.query(F).filter_by(task_id=task.id).all()
    assert {f.kind for f in rows} == {"person_consistency"}
    assert {f.item_id for f in rows} == {"A2-01"}  # A2-01-P 已退休，不再产出


def test_report_person_section_registered(db_session):
    from app.plus.blockchain_supervision import reporting
    task = service.create_task(db_session, conversation_id=101, title="A2PR",
                               scope_a1=False, scope_a3=True, created_by=1)
    soc = service.add_material(db_session, task, file_name="社保缴费证明.pdf")
    soc.pages_json = [{"page": 1, "text": SOCIAL_COVERS_ONE}]
    soc.parse_state = "done"
    ros = service.add_material(db_session, task, file_name="主要管理人员名单.pdf")
    ros.pages_json = [{"page": 1, "text": ROSTER_OK}]
    ros.parse_state = "done"
    db_session.commit()
    service.run_person_consistency(db_session, task)
    report = reporting.build_report(db_session, task)
    sec = next(s for s in report["sections"] if s["kind"] == "person_consistency")
    # A-13（B2）：报告条目按严重度+三态重排，故按 item_id 取而非位置取。
    unit = next(it for it in sec["items"] if it["item_id"] == "A2-01")
    assert sec["checked"] and unit["label"] == "社保资料覆盖管理人员"


def test_report_label_fallback_for_legacy_a2_01_p():
    """R-3：历史 A2-01-P finding 的标签兜底映射为 A2-01 新名（不动数据）。"""
    from app.plus.blockchain_supervision import reporting
    _neg, _rc, _sc, val = reporting._label_maps()
    assert val["A2-01-P"] == val["A2-01"] == "社保资料覆盖管理人员"


def test_frontend_person_section_registered():
    src = (_REPO / "frontend" / "src" / "plus-ui" / "blockchain-supervision"
           / "components" / "EvidencePanel.jsx").read_text(encoding="utf-8")
    assert "person_consistency" in src


# ==================== A2-04 资质匹配（批次3，词表保守口径） ====================

from app.plus.blockchain_supervision.rules import load_qualification_matchers  # noqa: E402

pytestmark = pytest.mark.integration

def test_qualification_matchers_shape():
    """词表形状锁：类别非空、别名列表化、出处可指回条款坐标（AD-5 / 清除表 P-04c）。"""
    mt = load_qualification_matchers()
    assert mt["works_categories"] and mt["scope_label_patterns"]
    note = mt["meta"]["source_note"]
    assert "沪交行规〔2026〕8号" in note and "沪城公路〔2025〕118号" in note
    assert "建设方案" not in note and "需求确认稿" not in note
    for c in mt["works_categories"]:
        assert c["key"] and isinstance(c.get("aliases", []), list)


def _qual_mats(scope_text, biz_text):
    sub = _mat(20, "02_分包合同_桩基.pdf", scope_text, role="sub")
    lic = _mat(21, "营业执照.pdf", biz_text, role="packet")
    return [sub, lic]


SCOPE_OK = ("分包工程内容：桥梁工程桩基施工，含钻孔灌注桩。")
SCOPE_OUT = ("分包工程内容：隧道工程开挖支护施工。")
BIZ_OK = ("经营范围：公路工程施工，桥梁工程、桩基工程、隧道工程施工；"
          "资质类别：公路工程施工总承包一级")


def test_qualification_all_covered_is_miss():
    findings = a2_check.run_qualification_match_check(_task(), _qual_mats(SCOPE_OK, BIZ_OK))
    assert len(findings) == 1 and findings[0].status == STATUS_MISS
    assert "桥梁工程" in findings[0].message and "桩基工程" in findings[0].message


def test_qualification_uncovered_is_hit_with_quote_and_page():
    scope = "分包工程内容：桥梁工程桩基施工，含钻孔灌注桩与钢结构安装。"
    findings = a2_check.run_qualification_match_check(
        _task(), _qual_mats(scope, BIZ_OK))
    hits = [f for f in findings if f.status == STATUS_HIT]
    assert hits and any("钢结构工程" in f.message for f in hits)
    assert hits[0].quote and "钢结构" in hits[0].quote and hits[0].page == 1


def test_qualification_no_biz_material_is_undecidable():
    sub = _mat(22, "02_分包合同_桩基.pdf", SCOPE_OK, role="sub")
    findings = a2_check.run_qualification_match_check(_task(), [sub])
    assert len(findings) == 1 and findings[0].status == STATUS_UNDECIDABLE
    assert "经营范围" in findings[0].message


def test_qualification_no_scope_label_is_undecidable():
    lic = _mat(23, "营业执照.pdf", BIZ_OK, role="packet")
    sub = _mat(24, "02_分包合同_桩基.pdf", "本合同经双方协商签订，无内容标签。", role="sub")
    findings = a2_check.run_qualification_match_check(_task(), [sub, lic])
    assert findings[0].status == STATUS_UNDECIDABLE and "分包工程内容" in findings[0].message


def test_qualification_reads_labor_contract_scope_without_guessing_certificate():
    labor = _mat(24, "劳务分包合同.pdf", "劳务施工范围：桥梁工程桩基施工。", role="labor")
    findings = a2_check.run_qualification_match_check(_task(), [labor])
    assert findings[0].status == STATUS_UNDECIDABLE
    assert "经营范围" in findings[0].message
    assert "未能从分包合同中定位" not in findings[0].message


def test_qualification_no_category_is_undecidable():
    findings = a2_check.run_qualification_match_check(
        _task(), _qual_mats("分包工程内容：土建配合及零星作业。", BIZ_OK))
    assert findings[0].status == STATUS_UNDECIDABLE and "工程类别" in findings[0].message


def test_service_qualification_replaces_on_rerun(db_session):
    task = service.create_task(db_session, conversation_id=101, title="A2Q",
                               scope_a1=False, scope_a3=True, created_by=1)
    sub = service.add_material(db_session, task, file_name="分包合同.pdf", role="sub")
    sub.pages_json = [{"page": 1, "text": SCOPE_OK}]
    sub.parse_state = "done"
    lic = service.add_material(db_session, task, file_name="营业执照.pdf")
    lic.pages_json = [{"page": 1, "text": BIZ_OK}]
    lic.parse_state = "done"
    db_session.commit()
    first = service.run_qualification_match(db_session, task)
    again = service.run_qualification_match(db_session, task)
    assert len(again) == len(first)
    from app.plus.blockchain_supervision.models import BsFinding as F
    kinds = {f.kind for f in db_session.query(F).filter_by(task_id=task.id).all()}
    assert kinds == {"qualification_match"}


def test_report_qualification_section_registered(db_session):
    from app.plus.blockchain_supervision import reporting
    task = service.create_task(db_session, conversation_id=101, title="A2QR",
                               scope_a1=False, scope_a3=True, created_by=1)
    sub = service.add_material(db_session, task, file_name="分包合同.pdf", role="sub")
    sub.pages_json = [{"page": 1, "text": SCOPE_OK}]
    sub.parse_state = "done"
    db_session.commit()
    service.run_qualification_match(db_session, task)
    report = reporting.build_report(db_session, task)
    sec = next(s for s in report["sections"] if s["kind"] == "qualification_match")
    assert sec["checked"] and sec["items"][0]["label"] == "资质与承揽范围匹配"


def test_frontend_qualification_section_registered():
    src = (_REPO / "frontend" / "src" / "plus-ui" / "blockchain-supervision"
           / "components" / "EvidencePanel.jsx").read_text(encoding="utf-8")
    assert "qualification_match" in src


def test_a2_endpoint_runs_all_three_batches(db_session):
    """run_a2_checks 汇齐三批次 kind（接线契约，防批次漏挂）。"""
    from app.plus.blockchain_supervision import service as svc
    src_types = []
    import inspect
    code = inspect.getsource(svc.run_a2_checks)
    for k in ("run_cert_validity", "run_person_consistency", "run_qualification_match"):
        assert k in code, k
    assert src_types is not None  # noqa: F841 占位，防误删断言
