# -*- coding: utf-8 -*-
"""oc-validity-engine-v1（2026-09-19，P0-3 证书有效期引擎）：

专家核查四查之三「时效性」补齐：以任务基准日为锚，从匹配证据页文本确定性抽取
「有效期至/营业期限」并判定 expired / expiring_soon / valid；抽取不到 → 不产生记录
（unknown 不判缺，fail-safe）。特殊纪律：
- 「检验有效期至」（车辆年检）与行驶证/驾驶证上下文排除——VETO 判例：行驶证无
  有效期概念，年检状态需人工核验，不构成证书过期缺陷；
- 「长期/永久有效」不产生记录。
集成行为（_apply_validity_screening）：expired → high finding + compliance 升级
nonconforming；expiring_soon → medium finding，原判 compliant 时升级 uncertain；
finding 写入 main trace structured_findings，统计写入 audit.validity_screening。
"""
import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from app.plus.startup_conditions_check.validity import (
    extract_validity_records,
    judge_expiry,
    parse_date_text,
    validity_findings_from_records,
)

BASELINE = date(2026, 9, 19)


class ParseDateTextTest(unittest.TestCase):
    def test_ymd_variants(self):
        for text, expect in [
            ("2026年9月30日", date(2026, 9, 30)),
            ("2026-09-30", date(2026, 9, 30)),
            ("2026.9.30", date(2026, 9, 30)),
            ("有效期至2026/9/30", date(2026, 9, 30)),
        ]:
            self.assertEqual(parse_date_text(text), expect, text)

    def test_ym_uses_month_end(self):
        self.assertEqual(parse_date_text("2026年10月"), date(2026, 10, 31))

    def test_invalid_returns_none(self):
        self.assertIsNone(parse_date_text("长期有效"))
        self.assertIsNone(parse_date_text("有效期3年"))
        self.assertIsNone(parse_date_text(""))


class JudgeExpiryTest(unittest.TestCase):
    def test_expired(self):
        self.assertEqual(judge_expiry(date(2026, 9, 18), BASELINE), "expired")

    def test_expiring_soon_within_30d(self):
        self.assertEqual(judge_expiry(BASELINE, BASELINE), "expiring_soon")
        self.assertEqual(judge_expiry(BASELINE + timedelta(days=30), BASELINE), "expiring_soon")

    def test_valid_beyond_30d(self):
        self.assertEqual(judge_expiry(BASELINE + timedelta(days=31), BASELINE), "valid")


class ExtractValidityRecordsTest(unittest.TestCase):
    def test_expired_calibration_cert(self):
        pages = {3: "特种设备检验检测机构 核准证 检定证书 编号：JZ2024-001 检定日期：2024年9月1日 有效期至2026年9月1日"}
        records = extract_validity_records(pages, file_name="全站仪检定证书.pdf", baseline=BASELINE)
        self.assertEqual(len(records), 1)
        r = records[0]
        self.assertEqual(r["page"], 3)
        self.assertEqual(r["verdict"], "expired")
        self.assertEqual(r["expiry_date"], "2026-09-01")
        self.assertEqual(r["issue_date"], "2024-09-01")
        self.assertEqual(r["kind"], "检定/校准证书")
        self.assertIn("有效期至", r["snippet"])

    def test_expiring_soon_business_license(self):
        pages = {1: "统一社会信用代码 91330100MA27XXX 营业期限至2026年10月15日"}
        records = extract_validity_records(pages, file_name="营业执照.pdf", baseline=BASELINE)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["verdict"], "expiring_soon")
        self.assertEqual(records[0]["kind"], "营业执照")

    def test_valid_future(self):
        pages = {2: "安全生产许可证 （副） 有效期至2028年12月31日"}
        records = extract_validity_records(pages, file_name="安许证.pdf", baseline=BASELINE)
        self.assertEqual(records[0]["verdict"], "valid")
        self.assertEqual(records[0]["kind"], "安全生产许可证")

    def test_structured_ocr_period_keeps_literal_evidence_and_end_date(self):
        text = '{"证照标题": "安全生产许可证", "营业期限": "2023年03月31日至2026年03月31日", "格式化营业期限终止日期": ""}'
        records = extract_validity_records({5: text}, file_name="报审包.pdf", baseline=date(2026, 11, 11))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["expiry_date"], "2026-03-31")
        self.assertEqual(records[0]["verdict"], "expired")
        self.assertIn(records[0]["snippet"], text)
        self.assertTrue(all("source_span" not in fact for fact in
                            records[0]["date_verification"]["facts"] + records[0]["validity_start_facts"]))

    def test_structured_ocr_uncertain_period_remains_unresolved(self):
        text = '{"证照标题":"安全生产许可证","营业期限":"约2023年03月31日至2026年03月31日（暂定）"}'
        records = extract_validity_records({5: text}, file_name="报审包.pdf", baseline=date(2026, 11, 11))
        self.assertEqual(records[0]["verdict"], "unknown")
        self.assertIsNone(records[0]["expiry_date"])
        self.assertIn(records[0]["snippet"], text)

    def test_fenced_structured_ocr_with_coordinates_extracts_original_period(self):
        text = ('```json\n{\n'
                ' "证照标题": "<576,302,57,299,90>,安全生产许可证",\n'
                ' "营业期限": "<554,576,194,20,0>,2023年03月31日至2026年03月31日",\n'
                ' "格式化营业期限终止日期": ""\n}\n```')
        records = extract_validity_records({5: text}, file_name="报审包.pdf", baseline=date(2026, 11, 11))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["expiry_date"], "2026-03-31")
        self.assertEqual(records[0]["verdict"], "expired")
        self.assertIn(records[0]["snippet"], text)

    def test_no_expiry_no_record(self):
        pages = {1: "特种作业操作证 作业类别：电工 操作项目：低压电工作业"}
        self.assertEqual(extract_validity_records(pages, file_name="电工操作证.pdf", baseline=BASELINE), [])

    def test_long_term_skipped(self):
        pages = {1: "营业执照 营业期限：长期"}
        self.assertEqual(extract_validity_records(pages, file_name="营业执照.pdf", baseline=BASELINE), [])

    def test_duration_not_a_date(self):
        # 「有效期3年」是时长不是日期，不得判定
        pages = {1: "检定证书 检定周期 有效期3年"}
        self.assertEqual(extract_validity_records(pages, file_name="x.pdf", baseline=BASELINE), [])

    def test_inspection_validity_excluded_vehicle_docs(self):
        # VETO 判例：行驶证「有效期」实为年检（检验有效期），不构成证书过期
        pages = {1: "中华人民共和国机动车行驶证 检验有效期至2025年08月 驾驶证副页"}
        self.assertEqual(
            extract_validity_records(pages, file_name="行驶证.pdf", baseline=BASELINE), [])
        # 同页上下文含行驶证 → 跳过
        pages2 = {1: "行驶证 检验有效期至2025年08月"}
        self.assertEqual(
            extract_validity_records(pages2, file_name="设备资料.pdf", baseline=BASELINE), [])
        # 「检验有效期至」标签负向后顾排除（非车辆文件的其他证书页）
        pages3 = {1: "试验检测设备 检验有效期至2025年08月"}
        self.assertEqual(
            extract_validity_records(pages3, file_name="设备资料.pdf", baseline=BASELINE), [])

    def test_special_work_cert_expired(self):
        pages = {5: "特种作业操作证 复审记录 有效期至2026年3月1日"}
        records = extract_validity_records(pages, file_name="特种作业操作证.pdf", baseline=BASELINE)
        self.assertEqual(records[0]["verdict"], "expired")
        self.assertEqual(records[0]["kind"], "特种作业操作证")

    def test_ambiguous_range_retains_unresolved_original_field(self):
        raw = "有效期：约2025年03月13日至2028年03月12日（暂定）"
        records = extract_validity_records({6: raw}, file_name="操作证.pdf", baseline=BASELINE)
        self.assertEqual(records[0]["verdict"], "unknown")
        self.assertIsNone(records[0]["expiry_date"])
        verification = records[0]["date_verification"]
        self.assertEqual(verification["status"], "unresolved")
        self.assertIn("约", verification["facts"][0]["raw_text"])
        self.assertIn("暂定", verification["facts"][0]["raw_text"])
        self.assertIn("2028年03月12日", records[0]["snippet"])

    def test_same_start_with_different_ends_cannot_leave_a_readable_record(self):
        raw = "有效期：2025-03-13至2028-03-12；有效期：2025-03-13至2029-03-12"
        records = extract_validity_records({6: raw}, file_name="操作证.pdf", baseline=BASELINE)
        self.assertEqual(len(records), 2)
        self.assertTrue(all(row["verdict"] == "unknown" and row["expiry_date"] is None for row in records))
        self.assertTrue(all(row["date_verification"]["status"] == "unresolved" for row in records))


class ValidityFindingsTest(unittest.TestCase):
    def _record(self, verdict="expired", kind="检定/校准证书"):
        return {
            "kind": kind, "doc_hint": "全站仪检定证书.pdf", "page": 3,
            "label": "有效期至", "issue_date": "2024-09-01",
            "expiry_date": "2026-09-01", "verdict": verdict,
            "snippet": "检定证书 编号JZ2024-001 有效期至2026年9月1日",
        }

    def test_expired_finding_high(self):
        findings = validity_findings_from_records([self._record()])
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f["severity"], "high")
        self.assertEqual(f["validity_verdict"], "expired")
        self.assertEqual(f["_pages"], [3])
        self.assertIn("全站仪检定证书.pdf", f["evidence"])
        self.assertIn("2026-09-01", f["title"])

    def test_expiring_soon_finding_medium(self):
        findings = validity_findings_from_records([self._record(verdict="expiring_soon")])
        self.assertEqual(findings[0]["severity"], "medium")

    def test_valid_no_finding(self):
        self.assertEqual(validity_findings_from_records([self._record(verdict="valid")]), [])


