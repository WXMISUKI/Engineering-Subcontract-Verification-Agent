"""规则版本（BS-P2-04 / 设计 98 §3）。

`current_version()` 是**规则版本**的唯一定义点，由两部分串接后取规范化 sha256 前 16 位
（前缀 `rs-`）：

1. 显式登记的决定性规则数据文件（`RULESET_FILES`）：UTF-8 严格解码并按
   `text-lf-v1` 统一换行后，按文件名排序串接规范化哈希；原始字节哈希另存审计；
2. `data/ruleset.json` 里**人维护**的 `code_revision` 字符串——确定性内核
   （`a1_check.py`、`a2_check.py`、`a3_check.py`、`signature_chain.py`、
   `internal_records.py` 等）改动不会被文件哈希发现，必须由改内核的人在
   `ruleset.json` 里递增 `code_revision` 并申明 `changed_item_ids`，否则历史任务
   不会被告知「结论层规则变了、要按新规则重跑」。

诚实边界：`code_revision` 只是人工声明，测试只能保证"数据变了版本必变"（护栏
断言 `version == current_version()`），**不能保证**改内核的人一定更新了它——所以
内核改动必须同时改 `ruleset.json`，这是提交纪律，不是自动化能力。

`missing_material_map.json` 只进入展示哈希，不单独触发结论版本变化。

`data/ruleset.json` 由**人维护**：规则数据或内核口径变更时同步更新 `version`
（必须等于 `current_version()`）、`code_revision` 与 `changed_item_ids`（申明本次
改了哪几条规则）。`tests/test_bs_ruleset.py` 用护栏断言前者的相等；后两者只能由
改规则的人手填，测试无法替代人工判断。
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
from pathlib import Path

_DATA_DIR = Path(__file__).with_name("data")
HASH_ALGORITHM = "text-lf-v1"


class RuleSetFingerprintError(ValueError):
    """规则版本输入不完整或不可安全规范化时的 fail-closed 错误。"""

# 规则数据文件显式清单（唯一定义点，设计 98 §3.1）：只列规则/判定相关数据。
# 刻意不用 `data/*.json` 通配——那样加一份与规则无关的数据文件也会改版本号，
# 版本就表达不了「规则变了」这件事。
RULESET_FILES = (
    "config.json",
    "transfer_rules.json",
    "negative_list_2024.json",
    "negative_list_matchers.json",
    "province_negative_scope.json",
    "report_checklist.json",
    "review_flow.json",
    "signature_chains.json",
    "extract_matchers.json",
    "qualification_matchers.json",
    "severity_matrix.json",
    "missing_material_map.json",
)

# 材料/展示目录保留原始审计哈希，但不单独触发业务结论版本变化。
PRESENTATION_FILES = ("missing_material_map.json",)

# 人维护的版本声明文件（不在 RULESET_FILES 内，避免自我引用）。
RULESET_META_FILE = "ruleset.json"


def _file_sha256(name: str) -> str:
    return hashlib.sha256((_DATA_DIR / name).read_bytes()).hexdigest()


def normalize_text(raw: bytes) -> bytes:
    """按 `text-lf-v1` 严格解码并统一 CRLF/CR 为 LF。"""
    if not isinstance(raw, (bytes, bytearray)):
        raise RuleSetFingerprintError("规则文件必须以 bytes 提供")
    try:
        text = bytes(raw).decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise RuleSetFingerprintError("规则文件不是有效 UTF-8，拒绝生成版本") from exc
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def _validate_file_name(name: str) -> None:
    if not isinstance(name, str) or not name or Path(name).name != name:
        raise RuleSetFingerprintError("规则文件名必须是非空的相对文件名")


def _validated_changed_items(items: Sequence[str] | None) -> list[str]:
    if items is None:
        return []
    if isinstance(items, (str, bytes)) or not isinstance(items, Sequence):
        raise RuleSetFingerprintError("changed_item_ids 必须是字符串数组")
    result = []
    for item in items:
        if not isinstance(item, str) or not item.strip() or item != item.strip():
            raise RuleSetFingerprintError("changed_item_ids 必须是非空字符串")
        if item in result:
            raise RuleSetFingerprintError("changed_item_ids 不得重复")
        result.append(item)
    return result


def _digest_lines(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def build_manifest(files: Mapping[str, bytes], *, code_revision: str,
                   declared_changed_item_ids: Sequence[str] | None = None,
                   presentation_names: Sequence[str] | None = None,
                   change_scope_complete: bool = True) -> dict:
    """构造纯内存规则清单，保留原始审计哈希和规范化文本哈希。

    `text_lf_hash` 只包含决定性规则文件和 `code_revision`；展示/材料目录只进入
    `presentation_hash`。任何文件缺失、类型错误或 UTF-8 损坏都直接失败，不猜版本。
    """
    if not isinstance(files, Mapping) or not files:
        raise RuleSetFingerprintError("规则文件清单不能为空")
    if not isinstance(code_revision, str) or not code_revision.strip():
        raise RuleSetFingerprintError("code_revision 必须是非空字符串")
    presentation = set(presentation_names or ())
    unknown_presentation = presentation - set(files)
    if unknown_presentation:
        raise RuleSetFingerprintError("presentation_names 包含不存在的文件")
    if not isinstance(change_scope_complete, bool):
        raise RuleSetFingerprintError("change_scope_complete 必须是布尔值")
    changed_items = _validated_changed_items(declared_changed_item_ids)

    entries = {}
    for name in sorted(files):
        _validate_file_name(name)
        raw = files[name]
        if not isinstance(raw, (bytes, bytearray)):
            raise RuleSetFingerprintError("规则文件必须以 bytes 提供")
        raw_bytes = bytes(raw)
        normalized = normalize_text(raw_bytes)
        entries[name] = {
            "raw_sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "text_lf_sha256": hashlib.sha256(normalized).hexdigest(),
            "presentation": name in presentation,
        }

    decision_lines = ["algorithm:" + HASH_ALGORITHM]
    decision_lines.extend(
        "%s:%s" % (name, entries[name]["text_lf_sha256"])
        for name in sorted(entries)
        if not entries[name]["presentation"]
    )
    decision_lines.append("code_revision:" + code_revision)
    text_lf_hash = _digest_lines(decision_lines)
    presentation_lines = [
        "%s:%s" % (name, entries[name]["text_lf_sha256"])
        for name in sorted(entries) if entries[name]["presentation"]
    ]
    raw_lines = [
        "%s:%s" % (name, entries[name]["raw_sha256"])
        for name in sorted(entries)
    ]
    return {
        "version": "rs-" + text_lf_hash[:16],
        "hash_algorithm": HASH_ALGORITHM,
        "text_lf_hash": text_lf_hash,
        "raw_manifest_hash": _digest_lines(raw_lines),
        "presentation_hash": _digest_lines(presentation_lines) if presentation_lines else None,
        "code_revision": code_revision,
        "changed_item_ids": changed_items,
        "change_scope_complete": change_scope_complete,
        "decision_files": [name for name in sorted(entries)
                           if not entries[name]["presentation"]],
        "presentation_files": [name for name in sorted(entries)
                               if entries[name]["presentation"]],
        "files": entries,
    }


def diff_manifests(previous: Mapping, current: Mapping) -> dict:
    """比较两个 117-B 清单；展示目录变化不触发业务规则变化。"""
    if not isinstance(previous, Mapping) or not isinstance(current, Mapping):
        raise RuleSetFingerprintError("规则清单必须是对象")
    if previous.get("hash_algorithm") != HASH_ALGORITHM or current.get("hash_algorithm") != HASH_ALGORITHM:
        raise RuleSetFingerprintError("规则清单算法不匹配")
    previous_files = previous.get("files")
    current_files = current.get("files")
    if not isinstance(previous_files, Mapping) or not isinstance(current_files, Mapping):
        raise RuleSetFingerprintError("规则清单缺少 files")
    names = sorted(set(previous_files) | set(current_files))
    changed_files = []
    for name in names:
        old = previous_files.get(name)
        new = current_files.get(name)
        if old is not None and not isinstance(old, Mapping):
            raise RuleSetFingerprintError("上一版规则清单文件项不完整")
        if new is not None and not isinstance(new, Mapping):
            raise RuleSetFingerprintError("当前规则清单文件项不完整")
        # 白名单增删本身也是规则变化；展示文件增删仍只进展示哈希。
        if bool((old or {}).get("presentation")) or bool((new or {}).get("presentation")):
            continue
        if old is None or new is None or old.get("text_lf_sha256") != new.get("text_lf_sha256"):
            changed_files.append(name)
    changed = previous.get("text_lf_hash") != current.get("text_lf_hash")
    changed_items = _validated_changed_items(current.get("changed_item_ids"))
    scope_complete = current.get("change_scope_complete", True)
    if not isinstance(scope_complete, bool):
        raise RuleSetFingerprintError("change_scope_complete 必须是布尔值")
    if changed and not changed_items and scope_complete:
        raise RuleSetFingerprintError("决定性规则发生变化但未声明 changed_item_ids")
    return {
        "changed": changed,
        "changed_files": changed_files,
        "changed_item_ids": (changed_items if scope_complete else None) if changed else [],
        "hash_algorithm": HASH_ALGORITHM,
    }


def manifest() -> dict:
    """读取当前工作树并生成 117-B 规则清单。"""
    meta = _meta()
    if meta.get("hash_algorithm") != HASH_ALGORITHM:
        raise RuleSetFingerprintError("ruleset.json 缺少或不支持 hash_algorithm")
    code_revision = meta.get("code_revision")
    if not isinstance(code_revision, str) or not code_revision.strip():
        raise RuleSetFingerprintError("ruleset.json 缺少有效 code_revision")
    files = {}
    for name in sorted(RULESET_FILES):
        path = _DATA_DIR / name
        if not path.is_file():
            raise RuleSetFingerprintError("规则文件缺失：%s" % name)
        files[name] = path.read_bytes()
    return build_manifest(
        files,
        code_revision=code_revision,
        declared_changed_item_ids=meta.get("changed_item_ids"),
        presentation_names=PRESENTATION_FILES,
        change_scope_complete=meta.get("change_scope_complete", True),
    )


def current_version() -> str:
    """当前规则数据版本（见模块 docstring 的算法）。"""
    return manifest()["version"]


def _meta() -> dict:
    try:
        with open(_DATA_DIR / RULESET_META_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        # 声明文件缺失/损坏时不猜：changed 清单按空处理，版本仍由数据现算。
        return {}
    return data if isinstance(data, dict) else {}


def declared_changed_items() -> list[str]:
    """`data/ruleset.json` 声明的本次变化条目 id（人维护；非法结构按空返回）。"""
    meta = _meta()
    # 范围不完整时不能把已知子集展示成全部变化项或用于局部重跑。
    if meta.get("change_scope_complete", True) is not True:
        return []
    items = meta.get("changed_item_ids")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, str) and item]


def task_ruleset_view(db, task) -> dict:
    """`GET /tasks/{id}/ruleset` 的响应体（设计 98 §3.3）。

    `changed`：任务结论所用版本与当前版本不同即为 True；历史任务
    `ruleset_version=NULL` 同样为 True（「未记录」不等于「就是当前版本」）。
    """
    data = manifest()
    current = data["version"]
    task_version = getattr(task, "ruleset_version", None)
    meta = _meta()
    return {
        "current_version": current,
        "task_version": task_version,
        "changed": task_version != current,
        "hash_algorithm": data["hash_algorithm"],
        "text_lf_hash": data["text_lf_hash"],
        "raw_manifest_hash": data["raw_manifest_hash"],
        "presentation_hash": data["presentation_hash"],
        "code_revision": data["code_revision"],
        "declared_changed_item_ids": declared_changed_items(),
        # `sha256` 保留为旧前端兼容别名；新字段明确原始/规范化两种哈希。
        "files": [{"name": name, "sha256": entry["raw_sha256"],
                   "raw_sha256": entry["raw_sha256"],
                   "text_lf_sha256": entry["text_lf_sha256"],
                   "presentation": bool(entry["presentation"])}
                  for name, entry in data["files"].items()],
        "note": meta.get("note"),
    }
