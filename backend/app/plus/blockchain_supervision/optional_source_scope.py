"""Versioned scope coverage for optional source material in fixed-rule checks."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from typing import Any


SCOPE_POLICY_ENV = "BS_OPTIONAL_SOURCE_SCOPE_LIMITATIONS_ENABLED"
POLICY_VERSION = "optional-source-scope.v1"
LEGACY_SCOPE_SNAPSHOT_SCHEMA = "bs-scope-snapshot.v1"
SCOPE_SNAPSHOT_SCHEMA = "bs-scope-snapshot.v2"
SCOPE_COVERAGE_SCHEMA = "bs-scope-coverage.v1"
CHECK_EVENT_SCHEMA = "bs-check-coverage.v1"
SCOPE_SNAPSHOT_EVENT = "review.scope_snapshot_recorded"
CHECK_STARTED_EVENT = "check.coverage_started"
CHECK_RECORDED_EVENT = "check.rule_coverage_recorded"
CHECK_FAILED_EVENT = "check.failed"
CURRENT_RUNS_KEY = "bs_scope_check_runs"
OPTIONAL_SOURCE_RULE_IDS = ("T15-01", "T15-02", "T15-03", "T15-05", "W16-03", "V16-02")

SOURCE_STATES = frozenset({
    "absent",
    "processing",
    "readable",
    "unreadable",
    "required_pages_unreadable",
    "unlinked",
    "conflicted",
})
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_REASON_CODES = {
    "absent": "optional_source_absent",
    "processing": "optional_source_processing",
    "unreadable": "optional_source_unreadable",
    "required_pages_unreadable": "optional_source_required_pages_unreadable",
    "unlinked": "optional_source_unlinked",
    "conflicted": "optional_source_conflicted",
}


def scope_policy_enabled(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return str(source.get(SCOPE_POLICY_ENV, "")).strip().lower() in _TRUE_VALUES


def freeze_policy(enabled: bool) -> dict[str, Any]:
    return {"enabled": enabled is True, "policy": POLICY_VERSION if enabled is True else "baseline"}


def input_fingerprint(*parts: str | None) -> str:
    payload = json.dumps(["bs-scope-check-input.v1", *parts], ensure_ascii=False,
                         separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def classify_general_source_state(materials: Sequence[Any], *, readable,
                                  reading_gap, linkage_issue=None) -> str:
    general = [material for material in materials if getattr(material, "role", None) == "general"]
    if not general:
        return "absent"

    source_sets = {}
    independent = []
    for material in general:
        source_set_id = getattr(material, "general_source_set_id", None)
        if source_set_id:
            source_sets.setdefault(source_set_id, []).append(material)
        else:
            independent.append(material)
    if len(source_sets) + len(independent) > 1:
        return "conflicted"

    members = next(iter(source_sets.values())) if source_sets else independent
    if source_sets:
        versions = {getattr(material, "general_source_set_version", None) for material in members}
        if len(members) < 2 or len(versions) != 1 or type(next(iter(versions))) is not int:
            return "conflicted"
    issues = [linkage_issue(material) for material in members] if linkage_issue is not None else []
    if "subject_conflict" in issues:
        return "conflicted"
    if any(issues) or any(getattr(material, "subject_id", None) is not None for material in members):
        return "unlinked"

    states = [getattr(material, "parse_state", None) for material in members]
    if any(state in {"uploaded", "stored", "preparing", "queued", "processing", "parsing"}
           for state in states):
        return "processing"
    if any(state != "done" or not readable(material) or reading_gap(material) is not None
           for material, state in zip(members, states)):
        return "unreadable"
    return "readable"


def _normalise_source_sets(source_sets, source_material_ids):
    material_ids = set(source_material_ids)
    normalised = []
    seen_set_ids = set()
    seen_material_ids = set()
    for source_set in source_sets or ():
        if not isinstance(source_set, Mapping):
            raise ValueError("invalid general source set")
        source_set_id = source_set.get("source_set_id")
        relation_version = source_set.get("relation_version")
        member_ids = _positive_ints(source_set.get("member_material_ids", ()))
        if (not isinstance(source_set_id, str) or not source_set_id.strip()
                or source_set_id in seen_set_ids
                or type(relation_version) is not int or relation_version < 1
                or not member_ids or len(member_ids) != len(set(member_ids))
                or not set(member_ids) <= material_ids
                or seen_material_ids.intersection(member_ids)):
            raise ValueError("invalid general source set")
        seen_set_ids.add(source_set_id)
        seen_material_ids.update(member_ids)
        normalised.append({
            "source_set_id": source_set_id,
            "relation_version": relation_version,
            "member_material_ids": member_ids,
        })
    return sorted(normalised, key=lambda item: item["source_set_id"])


def _valid_source_sets(source_sets, source_material_ids) -> bool:
    try:
        return _normalise_source_sets(source_sets, source_material_ids) == source_sets
    except (TypeError, ValueError):
        return False


def build_scope_snapshot(*, generation_id: str, policy: str, ruleset_version: str | None,
                         ruleset_fingerprint: str, material_fingerprint: str,
                         source_state: str, source_material_ids: Sequence[int],
                         rule_ids: Sequence[str], source_sets: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    if source_state not in SOURCE_STATES:
        raise ValueError("unknown optional source state")
    if policy != POLICY_VERSION:
        raise ValueError("unsupported optional source policy")
    material_ids = _positive_ints(source_material_ids)
    source_set_rows = _normalise_source_sets(source_sets, material_ids)
    rules = []
    for rule_id in _unique_strings(rule_ids):
        rules.append({
            "rule_id": rule_id,
            "subject_id": None,
            "source_role": "general",
            "source_material_ids": material_ids,
            "coverage_state": "unknown",
            "execution_state": "not_started",
            "reason_code": _REASON_CODES.get(source_state),
            "finding_ids": [],
        })
    return {
        "schema_version": SCOPE_SNAPSHOT_SCHEMA,
        "generation_id": generation_id,
        "policy": policy,
        "ruleset_version": ruleset_version,
        "ruleset_fingerprint": ruleset_fingerprint,
        "material_fingerprint": material_fingerprint,
        "source": {
            "source_role": "general",
            "source_material_ids": material_ids,
            "source_state": source_state,
            "source_sets": source_set_rows,
        },
        "rules": rules,
    }


def build_rule_coverage(*, generation_id: str, group_id: str, check_run_id: str,
                        input_fingerprint: str, source_state: str,
                        source_material_ids: Sequence[int], rule_ids: Sequence[str],
                        finding_ids_by_rule: Mapping[str, Sequence[int]] | None = None) -> list[dict[str, Any]]:
    if source_state not in SOURCE_STATES:
        raise ValueError("unknown optional source state")
    finding_ids_by_rule = finding_ids_by_rule or {}
    material_ids = _positive_ints(source_material_ids)
    coverage_state = "covered" if source_state == "readable" else (
        "unknown" if source_state == "processing" else "not_covered_optional_source")
    execution_state = "completed" if source_state == "readable" else "not_run"
    return [
        {
            "rule_id": rule_id,
            "subject_id": None,
            "source_role": "general",
            "source_material_ids": material_ids,
            "coverage_state": coverage_state,
            "execution_state": execution_state,
            "reason_code": _REASON_CODES.get(source_state),
            "finding_ids": _positive_ints(finding_ids_by_rule.get(rule_id, ())),
        }
        for rule_id in _unique_strings(rule_ids)
    ]


def scope_event_identity(event_type: str, payload: Mapping[str, Any]) -> tuple[str, ...] | None:
    if event_type == SCOPE_SNAPSHOT_EVENT:
        generation_id = payload.get("generation_id")
        return (event_type, generation_id) if isinstance(generation_id, str) and generation_id else None
    if event_type in {CHECK_STARTED_EVENT, CHECK_RECORDED_EVENT, CHECK_FAILED_EVENT}:
        values = (payload.get("generation_id"), payload.get("group_id"), payload.get("check_run_id"))
        if all(isinstance(value, str) and value for value in values):
            return (event_type, *values)
    return None


def same_event_payload(existing: Mapping[str, Any], candidate: Mapping[str, Any]) -> bool:
    if not isinstance(existing, Mapping) or not isinstance(candidate, Mapping):
        return False
    return _canonical(existing) == _canonical(candidate)


def project_scope_coverage(*, generation_id: str, policy: Mapping[str, Any] | None,
                           scope_snapshots: Sequence[Mapping[str, Any]],
                           current_pointer: Mapping[str, Any] | None,
                           started_events: Sequence[Mapping[str, Any]],
                           terminal_events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not isinstance(policy, Mapping) or policy.get("enabled") is not True:
        return _envelope(generation_id, "baseline", "legacy_unknown")
    if policy.get("policy") != POLICY_VERSION or len(scope_snapshots) != 1:
        return _envelope(generation_id, policy.get("policy"), "unavailable")

    snapshot = scope_snapshots[0]
    if not _valid_snapshot(snapshot, generation_id):
        return _envelope(generation_id, POLICY_VERSION, "unavailable")
    source = dict(snapshot["source"])
    source.setdefault("source_sets", [])
    rules = [dict(rule) for rule in snapshot["rules"]]
    if not rules:
        return _envelope(generation_id, POLICY_VERSION, "available", source=source)
    base = _envelope(generation_id, POLICY_VERSION, "incomplete", rules=rules, source=source)
    if current_pointer is None:
        return base
    if not _valid_pointer(current_pointer, generation_id):
        return _envelope(generation_id, POLICY_VERSION, "unavailable", source=source)

    identity = (generation_id, current_pointer["group_id"], current_pointer["check_run_id"],
                current_pointer["input_fingerprint"])
    matching_starts = [event.get("payload") for event in started_events
                       if _matches_event(event, CHECK_STARTED_EVENT, identity)]
    if len(matching_starts) != 1 or not _valid_check_event(matching_starts[0], identity):
        return _envelope(generation_id, POLICY_VERSION, "unavailable", source=source)

    matching_terminals = [event for event in terminal_events if _matches_terminal(event, identity)]
    if not matching_terminals:
        return _envelope(generation_id, POLICY_VERSION, "running", rules=rules, source=source)
    if len(matching_terminals) != 1:
        return _envelope(generation_id, POLICY_VERSION, "unavailable", source=source)

    terminal = matching_terminals[0]
    event_type = terminal.get("event_type")
    payload = terminal.get("payload")
    if not _valid_check_event(payload, identity):
        return _envelope(generation_id, POLICY_VERSION, "unavailable", source=source)
    source_state = snapshot["source"]["source_state"]
    if event_type == CHECK_FAILED_EVENT:
        return _envelope(generation_id, POLICY_VERSION, "failed", source=source)
    if (event_type != CHECK_RECORDED_EVENT
            or payload.get("policy") != POLICY_VERSION
            or payload.get("source_state") != source_state
            or not _valid_coverage_rules(payload.get("rules"), rules)):
        return _envelope(generation_id, POLICY_VERSION, "unavailable", source=source)

    covered_rules = [dict(rule) for rule in payload["rules"]]
    if source_state == "processing" or any(rule["coverage_state"] == "unknown" for rule in covered_rules):
        return _envelope(generation_id, POLICY_VERSION, "incomplete", rules=covered_rules, source=source)
    limitations = [
        {
            "rule_id": rule["rule_id"],
            "subject_id": rule.get("subject_id"),
            "source_role": rule["source_role"],
            "source_material_ids": list(rule["source_material_ids"]),
            "source_state": source_state,
            "reason_code": rule["reason_code"],
            "counts_as_finding": False,
            "counts_as_pass": False,
            "blocks_report": False,
        }
        for rule in covered_rules if rule["coverage_state"] == "not_covered_optional_source"
    ]
    state = "partial" if limitations else "available"
    return _envelope(generation_id, POLICY_VERSION, state, limitations, covered_rules, source)


def _envelope(generation_id: str, policy: str | None, state: str,
              limitations: Sequence[Mapping[str, Any]] = (),
              rules: Sequence[Mapping[str, Any]] = (),
              source: Mapping[str, Any] | None = None) -> dict[str, Any]:
    envelope = {
        "schema_version": SCOPE_COVERAGE_SCHEMA,
        "generation_id": generation_id,
        "policy": policy,
        "state": state,
        "limitations": [dict(item) for item in limitations],
        "rules": [dict(item) for item in rules],
    }
    if source is not None:
        envelope["source"] = dict(source)
    return envelope


def _valid_snapshot(snapshot: Mapping[str, Any], generation_id: str) -> bool:
    if (not isinstance(snapshot, Mapping)
            or snapshot.get("schema_version") not in {LEGACY_SCOPE_SNAPSHOT_SCHEMA, SCOPE_SNAPSHOT_SCHEMA}
            or snapshot.get("generation_id") != generation_id
            or snapshot.get("policy") != POLICY_VERSION
            or not isinstance(snapshot.get("ruleset_fingerprint"), str)
            or not snapshot.get("ruleset_fingerprint")
            or not isinstance(snapshot.get("material_fingerprint"), str)
            or not snapshot.get("material_fingerprint")):
        return False
    source = snapshot.get("source")
    rules = snapshot.get("rules")
    source_sets = source.get("source_sets", []) if isinstance(source, Mapping) else None
    if (not isinstance(source, Mapping) or source.get("source_role") != "general"
            or source.get("source_state") not in SOURCE_STATES
            or not _is_positive_int_list(source.get("source_material_ids"), allow_empty=True)
            or not isinstance(source_sets, list)
            or not _valid_source_sets(source_sets, source["source_material_ids"])
            or not isinstance(rules, list)):
        return False
    if source["source_state"] == "absent" and source["source_material_ids"]:
        return False
    ids = []
    for rule in rules:
        if (not isinstance(rule, Mapping)
                or rule.get("subject_id") is not None
                or rule.get("source_role") != "general"
                or rule.get("source_material_ids") != source["source_material_ids"]
                or rule.get("coverage_state") != "unknown"
                or rule.get("execution_state") != "not_started"
                or rule.get("reason_code") != _REASON_CODES.get(source["source_state"])
                or rule.get("finding_ids") != []):
            return False
        rule_id = rule.get("rule_id")
        if not isinstance(rule_id, str) or not rule_id:
            return False
        ids.append(rule_id)
    return len(ids) == len(set(ids))


def _valid_pointer(pointer: Mapping[str, Any], generation_id: str) -> bool:
    return (pointer.get("generation_id") == generation_id
            and pointer.get("group_id") == "a1"
            and isinstance(pointer.get("check_run_id"), str) and bool(pointer["check_run_id"])
            and isinstance(pointer.get("input_fingerprint"), str)
            and bool(pointer["input_fingerprint"]))


def _valid_check_event(payload: Any, identity: tuple[str, str, str, str]) -> bool:
    if not isinstance(payload, Mapping):
        return False
    generation_id, group_id, check_run_id, fingerprint = identity
    return (payload.get("schema_version") == CHECK_EVENT_SCHEMA
            and payload.get("generation_id") == generation_id
            and payload.get("group_id") == group_id
            and payload.get("check_run_id") == check_run_id
            and payload.get("input_fingerprint") == fingerprint)


def _matches_event(event: Mapping[str, Any], event_type: str,
                   identity: tuple[str, str, str, str]) -> bool:
    return (isinstance(event, Mapping) and event.get("event_type") == event_type
            and _matches_payload_identity(event.get("payload"), identity))


def _matches_terminal(event: Mapping[str, Any], identity: tuple[str, str, str, str]) -> bool:
    return (isinstance(event, Mapping)
            and event.get("event_type") in {CHECK_RECORDED_EVENT, CHECK_FAILED_EVENT}
            and _matches_payload_identity(event.get("payload"), identity))


def _matches_payload_identity(payload: Any, identity: tuple[str, str, str, str]) -> bool:
    if not isinstance(payload, Mapping):
        return False
    generation_id, group_id, check_run_id, fingerprint = identity
    return (payload.get("generation_id") == generation_id
            and payload.get("group_id") == group_id
            and payload.get("check_run_id") == check_run_id
            and payload.get("input_fingerprint") == fingerprint)


def _valid_coverage_rules(coverage_rules: Any, snapshot_rules: Sequence[Mapping[str, Any]]) -> bool:
    if not isinstance(coverage_rules, list):
        return False
    expected_ids = [rule["rule_id"] for rule in snapshot_rules]
    snapshot_by_id = {rule["rule_id"]: rule for rule in snapshot_rules}
    actual_ids = []
    for rule in coverage_rules:
        if not isinstance(rule, Mapping):
            return False
        rule_id = rule.get("rule_id")
        if not isinstance(rule_id, str) or rule_id in actual_ids:
            return False
        if (rule.get("source_role") != "general"
                or rule.get("coverage_state") not in {"covered", "not_covered_optional_source", "unknown"}
                or rule.get("execution_state") not in {"completed", "not_run", "failed", "partial"}
                or not _is_positive_int_list(rule.get("source_material_ids"), allow_empty=True)
                or not _is_positive_int_list(rule.get("finding_ids"), allow_empty=True)):
            return False
        frozen = snapshot_by_id.get(rule_id)
        if not isinstance(frozen, Mapping):
            return False
        source_state = frozen.get("reason_code")
        if (rule.get("subject_id") != frozen.get("subject_id")
                or rule.get("source_material_ids") != frozen.get("source_material_ids")
                or rule.get("reason_code") != source_state):
            return False
        if source_state is None:
            if rule.get("coverage_state") != "covered" or rule.get("execution_state") != "completed":
                return False
        elif source_state == _REASON_CODES["processing"]:
            if rule.get("coverage_state") != "unknown" or rule.get("execution_state") != "not_run":
                return False
        elif (rule.get("coverage_state") != "not_covered_optional_source"
              or rule.get("execution_state") != "not_run"):
            return False
        actual_ids.append(rule_id)
    return set(actual_ids) == set(expected_ids) and len(actual_ids) == len(expected_ids)


def _is_positive_int_list(values: Any, *, allow_empty: bool = False) -> bool:
    return (isinstance(values, list)
            and (allow_empty or bool(values))
            and all(type(value) is int and value > 0 for value in values)
            and len(values) == len(set(values)))


def _positive_ints(values: Sequence[int]) -> list[int]:
    return sorted({value for value in values if type(value) is int and value > 0})


def _unique_strings(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if isinstance(value, str) and value))


def _canonical(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
