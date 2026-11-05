"""主体身份编号的展示口径（设计 136 D13）。

`stable_code` 是**任务内身份编号**：创建时按当时类型取前缀，之后永不改写
（`set_subject_type` 只改 `subject_type`）。因此前缀里的「专业分包/劳务分包」
是历史前缀，不是当前类型声明；人读文案不得把两者并排成「专业分包1（劳务分包）」。
展示层统一剥掉前缀，渲染为 `主体 N`；类型一律只来自 `subject_type`。

本模块放在无依赖的叶子位置，是为了让只连 SQLite 的运维脚本
（`scripts/build_bs_review_actions_template.py`）与 ORM 侧共用同一份定义，
避免"界面一套、导出另一套"。
"""
import re

_TYPED_CODE_RE = re.compile(r"^(?:专业分包|劳务分包|分包)(\d+)$")


def stable_code_label(code: str | None) -> str:
    """编号 → 身份标签：`专业分包1` → `主体 1`；非该形态的编号原样保留。"""
    value = (code or "").strip()
    if not value:
        return ""
    match = _TYPED_CODE_RE.match(value)
    return "主体 %s" % match.group(1) if match else value


def subject_identity_label(subject) -> str:
    """主体的身份标签（直接吃 ORM 对象或任何带 `stable_code` 的对象）。"""
    return stable_code_label(getattr(subject, "stable_code", None))
