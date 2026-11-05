"""工程分包审查自有 ZIP 安全展开（T4.1-T2 §2.2；权威口径：契约 v3 §7.5）。

职责边界（窄、纯净、可独立测试）：只在「不可信 ZIP（路径或字节）」与
「安全 PDF 条目列表 / 明确错误」之间做转换。**不访问数据库、任务状态、OCR、
主体识别或 HTTP**，也不往目标目录之外写任何字节。

对外只有两件事：

- `validate_zip_upload` / `validate_zip_bytes`：上传阶段格式双验，只确认「结构可
  打开」——**不枚举业务条目、不解包、不读正文**（契约 §7.2.1）；
- `extract_pdf_entries`：把容器安全展开为 PDF 条目，非 PDF 只计数不落盘。

安全硬要求（契约 §7.5.2–§7.5.5）：

- 路径 `\\`→`/` 规范化后**拒绝**：NUL、POSIX 绝对路径、Windows 盘符、UNC 网络
  路径、任意 `..`、空段与 `.` 段（目录条目的尾斜杠不算空段）；
- **拒绝** symlink 条目、加密条目、嵌套 `.zip`（大小写不敏感，不递归展开）；
- 条目数（**全部非目录条目**，防用大量非 PDF 绕过）与解压总量**双封顶**：
  先用中央目录声明值预检，流式复制时再按**实际写入字节**硬封顶；
- 目标路径 `realpath` + `commonpath` 二次校验，确保仍在本次容器专属目录内；
- 任何失败**整体失败**并清理本次容器临时目录，不留可被误认为完整的半份结果；
- 安全但零 PDF → 明确失败，**不返回空成功**；容器自身永不作为证据。

数值口径：条目数/解压量上限是 **BS 自有的上线初始默认值**，允许环境变量配置、
非法或非正值回落默认；**不 import** 其它 Plus 模块（如开工条件核查）的私有常量
或 helper（契约 §7.5.1）。参考值来源：`startup_conditions_check/_shared.py:15-17`。
"""
from __future__ import annotations

import io
import os
import re
import shutil
import zipfile
from dataclasses import dataclass
from typing import BinaryIO, List, Union

# BS 自有上限（参考值：2000 条 / 1 GiB）。环境变量非法或非正值一律回落默认。
DEFAULT_MAX_ENTRIES = 2000
DEFAULT_MAX_UNCOMPRESSED_MB = 1024

# 流式复制块大小：与普通文件拷贝同量级，内存占用与条目体积无关
_COPY_CHUNK = 64 * 1024

# Windows 驱动器路径（`C:` / `c:/x`）；UNC 由开头的 `//` 另行拦截
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")

# Unix 文件类型位：external_attr 高 16 位承载 st_mode
_MODE_MASK = 0o170000
_SYMLINK_MODE = 0o120000

_MSG_NOT_ZIP = "文件不是有效的 ZIP 压缩包，请重新打包后上传"
_MSG_TOO_BIG = "资料包解压后体积超过上限，请拆分后重新上传"


class ZipIngestionError(Exception):
    """ZIP 容器无法安全展开（不安全 / 损坏 / 超限 / 零 PDF）。

    `message` 面向最终用户：平实中文，不暴露技术栈与内部路径。
    """


@dataclass(frozen=True)
class ExtractedPdf:
    """已安全落盘的 PDF 条目。`entry_path` 为**规范化后**的容器内相对路径。"""

    entry_path: str
    absolute_path: str
    size_bytes: int


@dataclass(frozen=True)
class ExtractionResult:
    """一次安全展开的完整结果。

    调用方据此创建派生材料（`pdfs`）与记录可见提示（`skipped_non_pdf`）。
    刻意返回结果对象而非裸列表（任务书建议签名的**有意扩展**）：非 PDF 跳过数
    是契约 §7.5.5 要求的「可见计数」，裸列表无法承载，另开一次遍历会重复解包。
    """

    pdfs: List[ExtractedPdf]
    skipped_non_pdf: int
    total_entries: int


# ==================== 配置（BS 自有 + 环境变量，非法回落默认） ====================

def max_entries() -> int:
    """条目数上限：`BS_ZIP_MAX_ENTRIES`，非法/非正值回落 `DEFAULT_MAX_ENTRIES`。"""
    raw = os.environ.get("BS_ZIP_MAX_ENTRIES")
    if raw:
        try:
            n = int(raw)
            if n > 0:
                return n
        except (TypeError, ValueError):
            pass
    return DEFAULT_MAX_ENTRIES


def max_uncompressed_bytes() -> int:
    """解压总量上限：`BS_ZIP_MAX_UNCOMPRESSED_MB`（默认 1024 = 1 GiB）。"""
    raw = os.environ.get("BS_ZIP_MAX_UNCOMPRESSED_MB")
    if raw:
        try:
            n = int(raw)
            if n > 0:
                return n * 1024 * 1024
        except (TypeError, ValueError):
            pass
    return DEFAULT_MAX_UNCOMPRESSED_MB * 1024 * 1024


# ==================== 上传阶段：格式双验（不枚举、不解包） ====================

def validate_zip_upload(source: Union[str, "os.PathLike[str]", BinaryIO]) -> None:
    """上传阶段格式双验：只确认「是结构可打开的 ZIP」。

    `source` 可为磁盘路径或已定位到起点的二进制流——上传入口用 `BytesIO` 在
    **落盘前**先验，避免把伪装/损坏文件写进任务目录。本函数**不枚举内部条目、
    不解包、不读正文**（契约 §7.2.1）：构造 `ZipFile` 只解析中央目录结构。
    """
    try:
        if isinstance(source, (str, os.PathLike)):
            if not os.path.isfile(source):
                raise ZipIngestionError(_MSG_NOT_ZIP)
            if os.path.getsize(source) == 0:
                raise ZipIngestionError("资料包为空，请重新上传")
        if not zipfile.is_zipfile(source):
            raise ZipIngestionError(_MSG_NOT_ZIP)
        with zipfile.ZipFile(source):
            pass  # 构造即解析中央目录；结构不可打开会抛 BadZipFile
    except ZipIngestionError:
        raise
    except (zipfile.BadZipFile, OSError, ValueError, EOFError) as exc:
        raise ZipIngestionError(_MSG_NOT_ZIP) from exc


def validate_zip_bytes(content: bytes) -> None:
    """上传入口用：落盘前只验 ZIP 结构，不写盘、不枚举条目。"""
    if not content:
        raise ZipIngestionError("资料包为空，请重新上传")
    validate_zip_upload(io.BytesIO(content))


# ==================== 安全展开 ====================

def _remove_tree(path: str) -> None:
    """清理本次容器临时目录（不存在即无操作；尽力删，失败不抛）。"""
    shutil.rmtree(path, ignore_errors=True)


def _within(root: str, path: str) -> bool:
    """`path` 是否位于 `root` 之内。

    Windows 跨盘符时 `commonpath` 抛 `ValueError`，按「越界」处理而非放行。
    """
    try:
        return os.path.commonpath([root, path]) == root
    except ValueError:
        return False


def _normalize_entry_path(raw: str, *, is_dir: bool) -> str:
    """把 ZIP 条目名规范化为安全的容器内相对路径；不安全即抛 `ZipIngestionError`。"""
    if not isinstance(raw, str):
        raise ZipIngestionError("资料包内含无法识别的条目名，无法安全展开")
    if "\x00" in raw:
        raise ZipIngestionError("资料包内含非法路径（空字符），无法安全展开")
    path = raw.replace("\\", "/")
    if is_dir:
        path = path.rstrip("/")  # 目录条目尾斜杠不是「空路径段」
    if path.startswith("//"):
        raise ZipIngestionError("资料包内含网络共享路径条目，无法安全展开")
    if path.startswith("/"):
        raise ZipIngestionError("资料包内含绝对路径条目，无法安全展开")
    if _WINDOWS_DRIVE_RE.match(path):
        raise ZipIngestionError("资料包内含盘符路径条目，无法安全展开")
    clean = []
    for seg in path.split("/"):
        if seg in ("", "."):
            raise ZipIngestionError("资料包内含空路径段，无法安全展开")
        if seg == "..":
            raise ZipIngestionError("资料包内含上级目录条目（..），无法安全展开")
        clean.append(seg)
    if not clean:
        raise ZipIngestionError("资料包内含空路径条目，无法安全展开")
    return "/".join(clean)


def _reject_unsafe_entry(info: zipfile.ZipInfo) -> None:
    """拒绝 symlink 与加密条目（契约 §7.5.2）。"""
    if ((info.external_attr >> 16) & _MODE_MASK) == _SYMLINK_MODE:
        raise ZipIngestionError("资料包内含符号链接条目，无法安全展开")
    if info.flag_bits & 0x1:
        raise ZipIngestionError("资料包内含加密条目，无法安全展开")


def _copy_with_cap(src: BinaryIO, out: BinaryIO, budget: int) -> int:
    """流式复制并做**实际字节**硬封顶，返回实际写入字节数。

    中央目录声明值可能不可信（可被伪造成小于真实体积），所以写出时按实际字节
    再封一次；一旦越限立即抛错，由调用方整体清理临时目录。
    """
    written = 0
    while True:
        chunk = src.read(_COPY_CHUNK)
        if not chunk:
            return written
        written += len(chunk)
        if written > budget:
            raise ZipIngestionError(_MSG_TOO_BIG)
        out.write(chunk)


def extract_pdf_entries(
    zip_path: str,
    destination_root: str,
    *,
    max_entries: int,
    max_uncompressed_bytes: int,
) -> ExtractionResult:
    """把 `zip_path` 安全展开为 PDF 条目。

    `destination_root`：**本次容器专属**目录，由本函数独占——进入时重建、失败时
    整体清理，因此重试不会把上一次的半份结果误认为完整。

    失败一律抛 `ZipIngestionError`，且此时临时目录**已清理**：不安全容器、损坏
    容器、加密/嵌套条目、条目数或解压量超限，以及**安全但未发现任何 PDF**
    （不返回空成功）。容器自身永不作为证据。
    """
    dest = os.path.abspath(destination_root)
    _remove_tree(dest)
    try:
        os.makedirs(dest, exist_ok=False)
    except OSError as exc:
        raise ZipIngestionError("资料包展开失败，请稍后重试") from exc

    try:
        return _extract_checked(zip_path, dest, max_entries, max_uncompressed_bytes)
    except ZipIngestionError:
        _remove_tree(dest)
        raise
    except zipfile.BadZipFile as exc:
        _remove_tree(dest)
        raise ZipIngestionError("资料包已损坏或不是有效的压缩包，无法读取") from exc
    except Exception as exc:  # 兜底：任何未预期异常都不留半份目录（契约 §7.5.4）
        _remove_tree(dest)
        raise ZipIngestionError("资料包展开失败，请检查文件后重新上传") from exc


def _extract_checked(zip_path: str, dest: str, max_entries: int,
                     max_uncompressed_bytes: int) -> ExtractionResult:
    """`extract_pdf_entries` 的主体（调用方已建好独占目录并负责清理）。"""
    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        file_infos = [i for i in infos if not i.is_dir()]

        # ① 条目数：**全部非目录条目**都计数（目录忽略但不免除其余上限）
        if len(file_infos) > max_entries:
            raise ZipIngestionError(
                "资料包内文件数量超过上限（最多 %d 个），请拆分后重新上传" % max_entries)

        # ② 中央目录声明体积预检：不解压即拒，挡住 ZIP 炸弹的第一道
        declared = 0
        for info in file_infos:
            try:
                declared += max(0, int(info.file_size or 0))
            except (TypeError, ValueError):
                raise ZipIngestionError("资料包内条目体积信息不可信，无法安全展开")
            if declared > max_uncompressed_bytes:
                raise ZipIngestionError(_MSG_TOO_BIG)

        extracted: dict = {}
        skipped = 0
        written_total = 0
        real_dest = os.path.realpath(dest)

        for info in infos:
            is_dir = info.is_dir()
            entry_path = _normalize_entry_path(info.filename, is_dir=is_dir)
            if is_dir:
                continue
            _reject_unsafe_entry(info)
            lowered = entry_path.lower()
            if lowered.endswith(".zip"):
                raise ZipIngestionError("资料包内含嵌套压缩包，无法安全展开")
            if not lowered.endswith(".pdf"):
                # 非 PDF 普通文件只计数、不写盘、不建材料（契约 §7.5.5）
                skipped += 1
                continue

            target = os.path.abspath(os.path.join(dest, *entry_path.split("/")))
            if not _within(dest, target):
                raise ZipIngestionError("资料包内含越界路径条目，无法安全展开")
            parent = os.path.dirname(target)
            os.makedirs(parent, exist_ok=True)
            # realpath 二次校验：挡符号链接目录把写入引出容器目录（目录外零写入）
            real_parent = os.path.realpath(parent)
            if real_parent != real_dest and not _within(real_dest, real_parent):
                raise ZipIngestionError("资料包内含越界路径条目，无法安全展开")

            try:
                with zf.open(info) as src, open(target, "wb") as out:
                    size = _copy_with_cap(src, out, max_uncompressed_bytes - written_total)
            except zipfile.BadZipFile as exc:
                raise ZipIngestionError("资料包已损坏，无法读取其中内容") from exc
            except RuntimeError as exc:
                # 加密或不支持的压缩方式在读取期抛 RuntimeError
                raise ZipIngestionError("资料包内含无法读取的条目，无法安全展开") from exc
            written_total += size
            # 重名条目：磁盘上最终存在的是**最后写入**的那份，故按同键覆盖，
            # 让结果与实际落盘一致（下游按 entry_path 幂等，不会建出重复材料）
            extracted[entry_path] = ExtractedPdf(
                entry_path=entry_path, absolute_path=target, size_bytes=size)

        pdfs = list(extracted.values())
        if not pdfs:
            # 安全但零 PDF：明确失败，不返回空成功（契约 §7.5.5 / OpenSpec 场景）
            raise ZipIngestionError("资料包内未发现可核查 PDF")
        return ExtractionResult(pdfs=pdfs, skipped_non_pdf=skipped,
                                total_entries=len(file_infos))
