"""静态守卫：扫描裸调 provider 的调用点（设计 §7 实现要求）。

任何 ``chat.completions.create`` / ``embeddings.create`` 调用都必须落在
**已登记的 provider 客户端模块**里；这些模块自身由上层统一包装器/台账路径
覆盖。新增调用点若不在白名单，本地检查脚本
``backend/scripts/check_billing_guard.py`` 会报错退出。

这是一条**防回归**的静态断言，不是运行时拦截；白名单只允许放
「直接封装 provider SDK」的模块，不允许把业务代码放进来。
"""

import re
from pathlib import Path

PROVIDER_CALL_PATTERNS = (
    re.compile(r"\.chat\.completions\.create\("),
    re.compile(r"\.embeddings\.create\("),
)

# 直接持有 provider SDK client 的模块（计费包装的边界）。
ALLOWED_PROVIDER_CLIENTS = {
    "app/plus/safety_review/llm.py",
    "app/plus/safety_review/ocr_cloud_probe.py",
    "app/plus/startup_conditions_check/checklist_text.py",
    "app/plus/startup_conditions_check/llm.py",
    "app/plus/structural_check/llm.py",
    "app/plus/structural_check/ref_calc_library.py",
}

# 本地 RapidOCR / 规则引擎不调用外部 provider；WeKnora 检索不是生成调用。


def scan(app_root: str | Path) -> list[dict]:
    root = Path(app_root)
    violations: list[dict] = []
    for path in root.rglob("*.py"):
        rel = path.relative_to(root.parent).as_posix()
        # app_root 传 backend/app 时，rel 形如 app/plus/...；兼容直接传 backend。
        if "/tests/" in f"/{rel}" or rel.startswith("tests/"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        hits = []
        for lineno, line in enumerate(text.splitlines(), start=1):
            if any(pattern.search(line) for pattern in PROVIDER_CALL_PATTERNS):
                hits.append(lineno)
        if hits and rel not in ALLOWED_PROVIDER_CLIENTS:
            violations.append({"file": rel, "lines": hits})
    return violations


def assert_clean(app_root: str | Path) -> None:
    violations = scan(app_root)
    if violations:
        detail = "\n".join(f"  {v['file']}:{v['lines']}" for v in violations)
        raise AssertionError(
            "发现未接入计费台账的裸 provider 调用点（请改走统一包装器）：\n" + detail
        )