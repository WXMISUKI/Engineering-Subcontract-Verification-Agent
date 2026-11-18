# -*- coding: utf-8 -*-
"""社保 OCR 表格回归：表头拆行、姓名与缴费期间分两行。"""
from types import SimpleNamespace

from app.plus.blockchain_supervision import a2_check
from app.plus.blockchain_supervision import personnel_evidence


SOCIAL_TEXT = r'''$\text { 湖北省社会保险参保证明 (单位专用) }$

$\text { 单位名称:武汉龙辰建设有限公司 }$

$\text { 序号 }$

$\text { 姓名 }$

$\text { 身份证号 }$

$\text { 个人编号 }$

$\text { 缴费起止时间 }$

$\text { 缴费状态 }$

|  |  |  |  |  |
| --- | --- | --- | --- | --- |
| 1 | 吴道记 | 372833197012111559 | 10062075990 | 年/月 | 年/月 |
| 2 |  |  |  | 202502 | 202507 |
'''


def _material():
    return SimpleNamespace(id=8, file_name="报审.pdf", role="labor", parse_state="done",
                           pages_json=[{"page": 24, "text": SOCIAL_TEXT}], degradations_json=[])


def test_social_table_with_split_header_extracts_person_employer_and_period():
    facts = personnel_evidence.extract_person_relation_evidence([_material()])
    person = next(fact for fact in facts if fact["name"] == "吴道记")
    assert person["employer"] == "武汉龙辰建设有限公司"
    assert person["period"] == "202502-202507"
    assert person["evidence_kind"] == "social_record"


def test_a2_01_does_not_claim_social_record_missing_when_table_is_readable():
    findings = a2_check.run_person_consistency_check(SimpleNamespace(id=8), [_material()])
    message = "\n".join(finding.message for finding in findings)
    assert "未能识别到社保缴费记录原文" not in message
    assert "社保信息里未提取到人员姓名" not in message
