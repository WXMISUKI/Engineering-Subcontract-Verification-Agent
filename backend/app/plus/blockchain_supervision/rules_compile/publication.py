"""规则卡发布前的不可变候选快照。

这里只准备候选快照，不写数据库、不上传知识库，也不改变当前任务使用的规则。
真正发布必须在管理员确认后由外层持久化流程完成。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence


PUBLICATION_SCHEMA_VERSION = "bs-rule-publication-candidate.v1"


def _digest(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def prepare_publication_candidate(
    catalog: Mapping,
    *,
    basis_snapshot_id: str,
    ruleset_version: str,
    profile_code: str,
    source_manifest_hash: str,
) -> dict:
    """生成待人工确认的规则发布候选快照。

    ``accepted`` 只表示编译闸门通过；返回状态固定为 ``pending_human``，
    防止调用方把纯函数结果当成已经发布的规则。
    """
    identity = {
        "basis_snapshot_id": str(basis_snapshot_id or "").strip(),
        "ruleset_version": str(ruleset_version or "").strip(),
        "profile_code": str(profile_code or "").strip(),
        "source_manifest_hash": str(source_manifest_hash or "").strip(),
    }
    missing_identity = [key for key, value in identity.items() if not value]
    if missing_identity:
        return {
            "schema_version": PUBLICATION_SCHEMA_VERSION,
            "status": "rejected",
            "reason_code": "publication_identity_incomplete",
            "missing_identity": missing_identity,
            "candidate_snapshot_id": None,
            "published": False,
            "writeback_performed": False,
        }
    if not isinstance(catalog, Mapping):
        return {
            "schema_version": PUBLICATION_SCHEMA_VERSION,
            "status": "rejected",
            "reason_code": "catalog_invalid",
            "candidate_snapshot_id": None,
            "published": False,
            "writeback_performed": False,
        }
    reports = catalog.get("reports") or []
    if not isinstance(reports, Sequence) or isinstance(reports, (str, bytes)):
        return {
            "schema_version": PUBLICATION_SCHEMA_VERSION,
            "status": "rejected",
            "reason_code": "catalog_reports_invalid",
            "candidate_snapshot_id": None,
            "published": False,
            "writeback_performed": False,
        }
    if any(not isinstance(report, Mapping) for report in reports):
        return {
            "schema_version": PUBLICATION_SCHEMA_VERSION,
            "status": "rejected",
            "reason_code": "catalog_report_entry_invalid",
            "candidate_snapshot_id": None,
            "published": False,
            "writeback_performed": False,
        }
    if catalog.get("status") != "pass" or any(report.get("status") != "accepted" for report in reports):
        return {
            "schema_version": PUBLICATION_SCHEMA_VERSION,
            "status": "rejected",
            "reason_code": "compile_gate_not_passed",
            "candidate_snapshot_id": None,
            "catalog_status": catalog.get("status"),
            "published": False,
            "writeback_performed": False,
        }

    cards = [report.get("card") for report in reports]
    content = {"identity": identity, "cards": cards}
    content_hash = _digest(content)
    return {
        "schema_version": PUBLICATION_SCHEMA_VERSION,
        "status": "pending_human",
        "reason_code": "compile_gate_passed_waiting_human_publish",
        "candidate_snapshot_id": f"bs-rule-candidate-{content_hash[:16]}",
        "content_hash": content_hash,
        "identity": identity,
        "card_count": len(cards),
        "cards": cards,
        "published": False,
        "writeback_performed": False,
    }


__all__ = ["PUBLICATION_SCHEMA_VERSION", "prepare_publication_candidate"]
