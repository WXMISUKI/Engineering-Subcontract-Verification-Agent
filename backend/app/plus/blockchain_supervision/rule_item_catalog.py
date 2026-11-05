"""P2-4 重跑条目目录。

目录只组合会实际产出 ``BsFinding.item_id`` 的规则源；``basis_index`` 仅是条款
坐标，不能充当白名单。每次调用都直读规则数据，使“改规则后重跑”立即生效。
"""
from . import a2_check, certificate_number_check, identity_observation, internal_records, rules


class RuleItemCatalogError(ValueError):
    """规则目录自身不一致，不能安全规划局部重跑。"""


class RerunSelectionError(ValueError):
    """请求条目未知或不属于当前任务范围。"""

    def __init__(self, *, unknown_item_ids=(), unmatched_item_ids=()):
        self.unknown_item_ids = sorted(set(unknown_item_ids))
        self.unmatched_item_ids = sorted(set(unmatched_item_ids))
        super().__init__("重跑条目包含未知条目或不属于本任务核查范围")

    def as_detail(self) -> dict:
        return {
            "code": "invalid_rerun_items",
            "message": str(self),
            "unknown_item_ids": self.unknown_item_ids,
            "unmatched_item_ids": self.unmatched_item_ids,
        }


def _register(catalog: dict[str, str], item_ids, group_id: str) -> None:
    for item_id in item_ids:
        previous = catalog.get(item_id)
        if previous is not None and previous != group_id:
            raise RuleItemCatalogError(
                "核查条目 %s 同时属于 %s / %s" % (item_id, previous, group_id))
        catalog[item_id] = group_id


def item_group_catalog() -> dict[str, str]:
    """返回 ``item_id -> reference group`` 的当前合法目录。"""
    catalog: dict[str, str] = {}
    report_items = rules.load_report_checklist()
    for group_id in ("completeness", "signature_chain"):
        _register(catalog, (item["id"] for item in report_items
                            if item.get("execution_group", "completeness") == group_id),
                  group_id)
    _register(catalog, (item["id"] for item in rules.load_negative_list()["rules"]), "a1")
    _register(catalog, (item["id"] for item in rules.load_transfer_rules()["rules"]), "a1")
    _register(catalog, (
        a2_check.ITEM_UNIT,
        a2_check.ITEM_ID,
        a2_check.ITEM_REG,
        a2_check.ITEM_QUAL,
        certificate_number_check.ITEM_ID,
        identity_observation.ITEM_ID,
    ), "a2")
    _register(catalog, (
        *("A3-02-%s" % chain["id"] for chain in rules.load_signature_chains()),
        "A3-02-timing",
        rules.ATTACHMENT_SEAL_ITEM,
    ), "signature_chain")
    _register(catalog, (internal_records.ITEM_ID,), "internal_records")

    # 单向交叉核验：已有条款坐标必须指向真实规则条目；没有条款坐标的 A2、签字链、
    # 内部核查和 RC-08/RC-09 仍是合法条目，不能被 basis_index 反向裁掉。
    unknown_basis_ids = sorted(set(rules.load_basis_index().get("items") or {}) - set(catalog))
    if unknown_basis_ids:
        raise RuleItemCatalogError(
            "依据坐标包含未知核查条目：%s" % "、".join(unknown_basis_ids))
    return catalog


def item_ids_for_group(group_id: str) -> tuple[str, ...]:
    """返回某核查组的合法条目，顺序与规则源一致。"""
    return tuple(item_id for item_id, group in item_group_catalog().items() if group == group_id)


def _ordered_unique(values) -> list[str]:
    return list(dict.fromkeys(values))


def _full_plan(expected_groups, requested_scope, requested_item_ids, reason) -> dict:
    return {
        "requested_scope": requested_scope,
        "requested_item_ids": requested_item_ids,
        "scope": "all",
        "item_ids": None,
        "rerun_groups": list(expected_groups),
        "carried_forward_groups": [],
        "fallback_reason": reason,
    }


def plan_rerun(*, expected_groups, scope: str, item_ids, changed_item_ids) -> dict:
    """把用户请求与规则影响面归一为一次可审计的实际重跑计划。

    ``changed_item_ids=None`` 表示历史清单或算法不可比较；此时不能证明哪些组未受
    影响，安全回退全量。完整性组是 A1 的上游，命中时将 A1 及其全部条目纳入实际
    范围，避免新完整性结论与旧 A1 结论混代。
    """
    expected_groups = tuple(_ordered_unique(expected_groups))
    requested_item_ids = (_ordered_unique(item_ids or []) if scope == "items" else None)
    if scope == "all":
        return _full_plan(expected_groups, "all", None, None)

    catalog = item_group_catalog()
    unknown = [item_id for item_id in requested_item_ids if item_id not in catalog]
    unmatched = [item_id for item_id in requested_item_ids
                 if item_id in catalog and catalog[item_id] not in expected_groups]
    if unknown or unmatched:
        raise RerunSelectionError(
            unknown_item_ids=unknown, unmatched_item_ids=unmatched)

    if changed_item_ids is None:
        return _full_plan(
            expected_groups, "items", requested_item_ids,
            "ruleset_change_scope_unavailable")
    changed_item_ids = _ordered_unique(changed_item_ids)
    if any(item_id not in catalog for item_id in changed_item_ids):
        return _full_plan(
            expected_groups, "items", requested_item_ids,
            "ruleset_changed_items_unknown")

    relevant_changes = [item_id for item_id in changed_item_ids
                        if catalog[item_id] in expected_groups]
    actual_item_ids = _ordered_unique([*requested_item_ids, *relevant_changes])
    target_groups = {catalog[item_id] for item_id in actual_item_ids}
    if "completeness" in target_groups and "a1" in expected_groups:
        target_groups.add("a1")
        actual_item_ids = _ordered_unique([*actual_item_ids, *item_ids_for_group("a1")])
    rerun_groups = [group for group in expected_groups if group in target_groups]
    carried_groups = [group for group in expected_groups if group not in target_groups]
    return {
        "requested_scope": "items",
        "requested_item_ids": requested_item_ids,
        "scope": "items",
        "item_ids": actual_item_ids,
        "rerun_groups": rerun_groups,
        "carried_forward_groups": carried_groups,
        "fallback_reason": None,
    }


def fallback_to_full(plan: dict, *, expected_groups, reason: str) -> dict:
    """沿用原请求审计字段，把不能安全 carry 的计划显式扩为全量。"""
    return _full_plan(
        tuple(_ordered_unique(expected_groups)),
        plan["requested_scope"],
        plan["requested_item_ids"],
        reason,
    )


__all__ = [
    "RuleItemCatalogError",
    "RerunSelectionError",
    "item_group_catalog",
    "item_ids_for_group",
    "plan_rerun",
    "fallback_to_full",
]
