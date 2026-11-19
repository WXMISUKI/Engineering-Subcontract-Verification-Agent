# -*- coding: utf-8 -*-
"""W16-02 报告桥接测试：NL-SCOPE → 通过/发现问题/需补充确认。"""
from types import SimpleNamespace

from app.plus.blockchain_supervision import a1_check
from app.plus.blockchain_supervision.models import (
    BsFinding, STATUS_HIT, STATUS_MISS, STATUS_UNDECIDABLE,
)
from app.plus.blockchain_supervision.rules import load_transfer_rules

RULE = next(row for row in load_transfer_rules()["rules"] if row["id"] == "W16-02")


def _finding(item_id, status, message):
    return BsFinding(task_id=1, material_id=1, item_id=item_id, kind="negative_list",
                     status=status, message=message)


def test_w16_02_pass_from_nl_scope():
    finding = a1_check._run_w16_02(
        SimpleNamespace(id=1),
        [_finding("NL-SCOPE", STATUS_MISS, "通过")],
        RULE,
    )
    assert finding.status == STATUS_MISS
    assert "通过" in finding.message


def test_w16_02_hit_from_nl_scope_and_rule_finding():
    finding = a1_check._run_w16_02(
        SimpleNamespace(id=1),
        [
            _finding("NL-SCOPE", STATUS_HIT, "发现 1 项问题"),
            _finding("NL-B-08", STATUS_HIT, "命中：整孔预制安装的预制梁"),
        ],
        RULE,
    )
    assert finding.status == STATUS_HIT
    assert "NL-B-08" in finding.message


def test_w16_02_need_confirmation_from_nl_scope():
    finding = a1_check._run_w16_02(
        SimpleNamespace(id=1),
        [
            _finding("NL-SCOPE", STATUS_UNDECIDABLE, "发现 1 项需补充确认的问题"),
            _finding("NL-B-09", STATUS_UNDECIDABLE, "需补充确认：缺单件重量"),
        ],
        RULE,
    )
    assert finding.status == STATUS_UNDECIDABLE
    assert "需补充确认" in finding.message
