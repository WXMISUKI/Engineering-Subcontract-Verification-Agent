# -*- coding: utf-8 -*-
"""附件下载响应头（RFC 5987），跨业务模块共用。

为什么单独放在这里：结构受力报告与工程分包审查的导出件都要「中文文件名不乱码」，
旧实现各自拼字符串（`blockchain_supervision/router.py` 与 `structural_check/router.py`
各一份），口径一旦分叉就会一个修了另一个漏。

约定：
  - `filename=` 给 ASCII 兜底名（老客户端按 Latin-1 解头也不会乱码）；
  - `filename*=UTF-8''<percent-encoded>` 给真实中文名（RFC 5987 / RFC 6266）；
  - 文件名先去掉换行、引号、路径分隔符 —— 防止头部注入（响应头里出现裸 CR/LF 可被利用）。
"""
from __future__ import annotations

import re
from urllib.parse import quote

# 换行/制表/引号/反斜杠/斜杠都会破坏 Content-Disposition 或让文件名越界。
_UNSAFE_NAME_CHARS = re.compile(r"[\r\n\t\"\\/]+")


def attachment_disposition(file_name: str, *, fallback: str = "report.bin") -> str:
    """构造 `Content-Disposition` 值：ASCII 兜底名 + RFC 5987 UTF-8 名。"""
    name = _UNSAFE_NAME_CHARS.sub("_", (file_name or "").strip()) or fallback
    # 兜底名只留 ASCII：中文名投影后可能只剩扩展名，此时用调用方给的 fallback。
    ascii_name = name.encode("ascii", "ignore").decode("ascii")
    ascii_name = re.sub(r"_+", "_", ascii_name).strip("_ ")
    if not ascii_name or ascii_name.startswith("."):
        ascii_name = fallback
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(name, safe="")}'