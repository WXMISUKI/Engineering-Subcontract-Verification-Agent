import unittest

from app.plus.safety_review.extract import ExtractDoc, Line, Word
from app.plus.structural_check.finding_location import normalize_findings_for_doc
import pytest

pytestmark = pytest.mark.unit

class StructuralFindingsLocationContractTests(unittest.TestCase):
    def setUp(self):
        self.doc = ExtractDoc(
            pages=2,
            lines=[
                Line(
                    id="L0",
                    page=1,
                    text="模板支架步距 1200mm",
                    words=[
                        Word(10, 10, 40, 20, "模板支架"),
                        Word(41, 10, 65, 20, "步距"),
                        Word(66, 10, 95, 20, "1200mm"),
                    ],
                ),
                Line(
                    id="L1",
                    page=2,
                    text="钢丝绳安全系数不得小于 5.0",
                    words=[
                        Word(10, 30, 35, 40, "钢丝绳"),
                        Word(36, 30, 68, 40, "安全系数"),
                        Word(69, 30, 95, 40, "不得小于"),
                        Word(96, 30, 120, 40, "5.0"),
                    ],
                ),
            ],
        )

    def test_missing_page_is_filtered_and_warning_recorded(self):
        findings, warnings = normalize_findings_for_doc(
            [{"quote": "模板支架步距 1200mm", "content": "缺页码"}],
            doc=self.doc,
        )

        self.assertEqual(findings, [])
        self.assertEqual(warnings[0]["code"], "missing_page")

    def test_missing_quote_is_filtered_and_warning_recorded(self):
        findings, warnings = normalize_findings_for_doc(
            [{"page": 1, "content": "缺引文"}],
            doc=self.doc,
        )

        self.assertEqual(findings, [])
        self.assertEqual(warnings[0]["code"], "missing_quote")

    def test_exact_match_maps_page_line_id_and_rects(self):
        findings, warnings = normalize_findings_for_doc(
            [{"page": 1, "quote": "模板支架步距 1200mm", "content": "步距合规性"}],
            doc=self.doc,
        )

        self.assertEqual(warnings, [])
        self.assertEqual(findings[0]["page"], 1)
        self.assertEqual(findings[0]["line_id"], "P1-L001")
        self.assertEqual(findings[0]["rects"][0], [10.0, 10.0, 40.0, 20.0])

    def test_normalized_match_supports_spacing_and_full_width(self):
        findings, warnings = normalize_findings_for_doc(
            [{"page": 2, "quote": "钢丝绳 安全系数 不得小于５.０", "content": "吊装约束"}],
            doc=self.doc,
        )

        self.assertEqual(warnings, [])
        self.assertEqual(findings[0]["line_id"], "P2-L001")
        self.assertEqual(len(findings[0]["rects"]), 4)

    def test_mapping_failure_keeps_page_and_quote_but_downgrades_location(self):
        findings, warnings = normalize_findings_for_doc(
            [{"page": 2, "quote": "不存在的条款引文", "content": "无法定位"}],
            doc=self.doc,
        )

        self.assertEqual(findings[0]["page"], 2)
        self.assertEqual(findings[0]["quote"], "不存在的条款引文")
        self.assertIsNone(findings[0]["line_id"])
        self.assertEqual(findings[0]["rects"], [])
        self.assertEqual(warnings[0]["code"], "location_mapping_failed_after_calibration")

    def test_fuzzy_match_accepts_minor_ocr_drift_and_keeps_rects(self):
        findings, warnings = normalize_findings_for_doc(
            [{"page": 2, "quote": "钢丝绳安全系数不得小于5.O", "content": "OCR 漂移"}],
            doc=self.doc,
        )

        self.assertEqual(findings[0]["line_id"], "P2-L001")
        self.assertEqual(len(findings[0]["rects"]), 4)
        self.assertEqual(warnings[0]["code"], "location_mapping_fuzzy")

    def test_out_of_range_page_is_corrected_by_global_quote_remap(self):
        findings, warnings = normalize_findings_for_doc(
            [{"page": 382, "quote": "钢丝绳安全系数不得小于 5.0", "content": "页码漂移"}],
            doc=self.doc,
        )

        self.assertEqual(findings[0]["page"], 2)
        self.assertEqual(findings[0]["line_id"], "P2-L001")
        self.assertEqual(warnings[0]["code"], "location_page_corrected")

    def test_out_of_range_unmapped_quote_becomes_non_clickable(self):
        findings, warnings = normalize_findings_for_doc(
            [{"page": 382, "quote": "规范第382页条文", "content": "引用了错误来源"}],
            doc=self.doc,
        )

        self.assertEqual(findings[0]["page"], 0)
        self.assertIsNone(findings[0]["line_id"])
        self.assertEqual(findings[0]["rects"], [])
        self.assertEqual(warnings[0]["code"], "location_page_out_of_range")
        self.assertEqual(warnings[1]["code"], "location_mapping_failed_after_calibration")

    def test_fuzzy_match_rejects_ambiguous_same_page_candidates(self):
        doc = ExtractDoc(
            pages=1,
            lines=[
                Line(id="L0", page=1, text="模板支架步距 1500mm", words=[Word(1, 1, 2, 2, "模板")]),
                Line(id="L1", page=1, text="模板支架步距 1800mm", words=[Word(3, 3, 4, 4, "支架")]),
            ],
        )

        findings, warnings = normalize_findings_for_doc(
            [{"page": 1, "quote": "模板支架步距 1600mm", "content": "歧义测试"}],
            doc=doc,
        )

        self.assertIsNone(findings[0]["line_id"])
        self.assertEqual(findings[0]["rects"], [])
        self.assertEqual(warnings[0]["code"], "location_mapping_failed_after_calibration")


if __name__ == "__main__":
    unittest.main()
