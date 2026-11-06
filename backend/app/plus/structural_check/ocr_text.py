"""扫描件规范 OCR 回退（2026-09-10，steel-bridge-check 域切片）。

背景：`import_norms` 原仅走 PyMuPDF 文本层，纯扫描规范被如实跳过（no_text_layer）。
实测源池 260 本中有 6 本属此类（含 `钢围堰工程技术标准`，导致 cofferdam-check 集合长期为空），
合计 734 页。本模块为其补 OCR 回退。

设计取舍：
- **云端优先**：复用 `safety_review.ocr_cloud.cloud_ocr_structured`（模型名取 config.OCR_CLOUD_MODEL），
  对规范表格/公式的识别质量显著优于本地 PP-OCR，且并发可控。
- **正文优先结构化**：2026-10-19 实测发现，旧实现只消费词级 `words_info` 并按行压平，
  会把二维公式（分式/根式/上下标）压成一维字符串，产生 `Anb=`、`Ocr三`、`Y==一`
  这类失真；而模型对同一页其实已能输出正确 LaTeX。故现在优先取模型的结构化
  markdown（含 LaTeX），仅在 markdown 缺失时回退词级装配。
- **本地兜底**：历史兼容路径仅显式启用时可用；独立分包版禁用且不打包本地 OCR 模型。
- **逐页容错**：不使用 `cloud_ocr_page_concurrent`——它对任一页失败即整文件抛错，
  对动辄上百页的规范过于脆弱。本模块自行并发，单页失败仅记账，不中断整本。
- **落盘缓存**：按 (绝对路径, 大小, mtime, dpi, 提示词版本) 缓存页文本，重复运行不重复计费。
  缓存目录 `backend/logs/ocr_cache/`（`backend/logs/` 已在 .gitignore 内）。
- **不静默**：返回 stats（引擎/缓存命中/成功页/回退页/失败页），由调用方如实上报。

坐标系说明：云端 `rotate_rect` 与本地 box 均为**渲染像素系**，故本模块只在同一次渲染
（同 dpi）内做行聚合，不做跨 dpi 换算。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Callable

logger = logging.getLogger(__name__)

# backend/logs/ocr_cache/（backend/logs 已被 .gitignore 忽略）
_CACHE_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "logs", "ocr_cache")
)

DEFAULT_DPI = 150
# 行聚合容差（像素@dpi）：同一行内的 bbox 中心 y 偏差阈值
_LINE_TOLERANCE_PX = 12.0
# 文本层页字符数低于此值视为「该页无有效文本层」，需 OCR 补。
# 2026-09-15 由 10 提到 60：实测 GB55006/GB50068/GB55001 扫描件每页仅剩页眉级 20~40 字符，
# 旧阈值下这些页被判「有文本」跳过 OCR，整本以空壳入库（888~2442 字符）。正文页至少数百字符，
# 60 不会误伤真实文本页，只会把页眉级扫描页送进 OCR。
MIN_PAGE_TEXT_CHARS = 60


# ---------------------------------------------------------------------------
# 文本装配
# ---------------------------------------------------------------------------
def _smart_join(parts: list[str]) -> str:
    """拼接同一行内的词块：ASCII 字母数字相邻时补空格，CJK 直接相连。"""
    out = ""
    for part in parts:
        if not part:
            continue
        if out and out[-1].isascii() and out[-1].isalnum() and part[0].isascii() and part[0].isalnum():
            out += " "
        out += part
    return out


def words_to_text(words: list[dict]) -> str:
    """[{text, bbox}] → 按行聚合的纯文本。

    行内次序以 x 为准（不能只按 y 排序：同一行的词 y 会抖动几像素，直接按 y 排会错序）。
    缺 bbox 的词不丢弃（云端 `rotate_rect` 异常时 bbox 为 None）——统一追加到末尾一行，
    宁可位置略偏也不丢内容。
    """
    positioned: list[tuple[float, float, str]] = []
    unpositioned: list[str] = []
    for w in words or []:
        text = str(w.get("text") or "").strip()
        if not text:
            continue
        bbox = w.get("bbox")
        y = x = None
        if bbox is not None:
            try:
                y = (float(bbox[1]) + float(bbox[3])) / 2.0
                x = float(bbox[0])
            except (TypeError, ValueError, IndexError):
                y = x = None
        if y is None:
            unpositioned.append(text)
        else:
            positioned.append((y, x, text))

    if not positioned and not unpositioned:
        return ""

    positioned.sort(key=lambda it: (it[0], it[1]))

    lines: list[str] = []
    bucket: list[tuple[float, str]] = []
    anchor_y: float | None = None

    def flush() -> None:
        if bucket:
            bucket.sort(key=lambda it: it[0])
            lines.append(_smart_join([t for _x, t in bucket]))

    for y, x, text in positioned:
        if anchor_y is None or abs(y - anchor_y) <= _LINE_TOLERANCE_PX:
            bucket.append((x, text))
            if anchor_y is None:
                anchor_y = y
        else:
            flush()
            bucket = [(x, text)]
            anchor_y = y
    flush()

    if unpositioned:
        lines.append(_smart_join(unpositioned))

    return "\n".join(line for line in lines if line)


# ---------------------------------------------------------------------------
# 结构化正文归一
# ---------------------------------------------------------------------------
# 提示词版本参与缓存键：改了提示词或装配策略，旧缓存必须失效，
# 否则会继续命中按旧策略压平的失真文本。
PROMPT_VERSION = "formula-latex-v1"

_IMAGE_PLACEHOLDER_RE = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$")
_GATHERED_RE = re.compile(r"\${1,2}\s*\\begin\{gathered\}(.*?)\\end\{gathered\}\s*\${1,2}", re.S)
# 公式编号：允许 `6.2 .7-3` 这种数字/点之间误插空格
# （模型实测把 `6.2.7-3` 输出成 `6.2 .7-3`，直接按条号匹配会落空）
_EQ_NUMBER_RE = re.compile(r"\((\d+(?:\s*\.\s*\d+)*\s*-\s*\d+)\)")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")


def _tidy_eq_number(raw: str) -> str:
    """`6.2 .7-3` → `6.2.7-3`；只压缩数字与点之间的空白，不动其他字符。"""
    return re.sub(r"\s+", "", raw)


_GATHERED_NUM_RE = re.compile(r"\\{0,3}\(\s*(\d+(?:\s*\.\s*\d+)*\s*-\s*\d+)\s*\)")


def _strip_line_break(seg: str) -> str:
    """去掉段首的 LaTeX 行分隔 `\\\\` 与段尾残留的反斜杠。

    真实模型输出里换行符后常紧跟 `\\sigma`，字节上是 `\\\\\\sigma`（3 个反斜杠）；
    只削掉前两个（行分隔），保留公式自己的那个。
    """
    s = seg.strip()
    if s.startswith("\\\\"):
        s = s[2:].strip()
    return s.rstrip("\\").strip()


def _split_gathered(body: str) -> list[str]:
    """把 `\\begin{gathered}` 内的多条公式拆成独立行间公式。

    以**编号为锚点**切分，而不是按 `\\\\` 切：模型对转义层数不稳定
    （同一页可能出现 `\\\\(6.2.7-3)` 与 `\\\\\\sigma` 混排），按反斜杠切会吃掉公式起始符号。
    编号缺失时退化为按 `\\\\` 切分。
    """
    spans = list(_GATHERED_NUM_RE.finditer(body))
    if not spans:
        out: list[str] = []
        for chunk in re.split(r"\\{2,}", body):
            seg = chunk.strip()
            if seg:
                out.append(f"$${seg}$$")
        return out
    out = []
    cursor = 0
    for match in spans:
        seg = _strip_line_break(body[cursor:match.start()])
        cursor = match.end()
        number = _tidy_eq_number(match.group(1))
        out.append(f"$${seg}$$ ({number})" if seg else f"({number})")
    tail = _strip_line_break(body[cursor:])
    if tail:
        out.append(f"$${tail}$$")
    return out


_FENCED_BLOCK_RE = re.compile(r"```[ \t]*[A-Za-z0-9_+-]*[ \t]*\r?\n(?P<body>.*?)```", re.S)
# 截断的结构化块里逐个 `"text": "..."` 值（含转义），用于 JSON 不完整时的抢救
_TEXT_VALUE_RE = re.compile(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _salvage_text_values(raw: str) -> str | None:
    """JSON 被截断时，按出现顺序抽出全部 `text` 值。

    实测依据（2026-10-22）：四份金样里有 3 页的模型输出在中途被截断，围栏 JSON
    没有闭合，`json.loads` 必然失败。这 3 页的字都还在，只是外层结构坏了——
    丢掉坐标、只取 `text` 值，是保住内容的最小动作；反过来放任原样，坐标数字就会
    留在判定文本里。

    **仅当块内确有 `"text"` 键时才抽**：没有该键的结构（坐标点集、表格 cell 等）
    仍返回 None，由调用方保持原文。
    """
    out: list[str] = []
    for match in _TEXT_VALUE_RE.finditer(raw):
        try:
            value = json.loads('"' + match.group(1) + '"')
        except (ValueError, TypeError):
            value = match.group(1)
        if isinstance(value, str) and value.strip():
            out.append(value.strip())
    return "\n".join(out) if out else None


def _structured_block_to_text(obj) -> str | None:
    """把「已知形态」的结构化 OCR 结果转成正文；未知形态返回 None（不猜）。

    已知形态只有两种，都是云端模型对旋转文本/竖排文本的输出方式：
    - `[{"rotate_rect": [...], "text": "..."}, ...]`：段落列表，顺序即阅读顺序；
    - `{"text"|"markdown": "..."}`：整块正文。
    除此之外（表格结构、坐标点集、其他键）**一律返回 None 保持原文**，因为任何
    「聪明」的转换都可能把坐标数字当正文写进证据里。
    """
    if isinstance(obj, dict):
        for key in ("text", "markdown", "content"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None
    if not isinstance(obj, list):
        return None
    lines: list[str] = []
    for item in obj:
        if not isinstance(item, dict):
            return None
        value = item.get("text")
        if not isinstance(value, str):
            return None
        if value.strip():
            lines.append(value.strip())
    return "\n".join(lines) if lines else None


def collapse_structured_ocr_blocks(text: str) -> str:
    """把 OCR 结果里内嵌的**结构化 JSON 块**折叠为纯文本正文。

    为什么需要（2026-10-22 P0 实测）：四份金样合同 235 页中有 43 页（18.3%）的
    模型输出是围栏 JSON（`rotate_rect` 段落列表），旧 `normalize_ocr_markdown`
    不识别这种形态，于是坐标与键名原样进了判定文本，证据引文形如
    `0],"text":"本合同分包工程暂定劳务报酬…` —— 用户不会相信这种引文，按关键词
    匹配的规则也可能因此漏判。

    **只做格式折叠，不改写文字**：每个段落的 `text` 原样保留、按列表顺序换行拼接。
    未知结构不动（见 `_structured_block_to_text`）。
    无围栏的裸 JSON 页（整页只有 `[...]`）同样处理，这是同一现象的另一种外壳。
    """
    if not text or ("```" not in text and not text.lstrip()[:1] in "[{"):
        return text

    def _repl(match: re.Match) -> str:
        raw = match.group("body").strip()
        if raw[:1] not in "[{":
            return match.group(0)  # 普通代码块（非 JSON 段落列表）：原样保留
        try:
            obj = json.loads(raw)
        except (ValueError, TypeError):
            salvaged = _salvage_text_values(raw)  # 截断块：只抢救 text 值
            return salvaged if salvaged is not None else match.group(0)
        converted = _structured_block_to_text(obj)
        return converted if converted is not None else match.group(0)

    out = _FENCED_BLOCK_RE.sub(_repl, text)

    # 未闭合围栏（模型输出中途被截断，``` 只有开头）：取最后一个 ``` 之后的部分抢救
    if out.count("```") % 2:
        pos = out.rfind("```")
        body_start = out.find("\n", pos)
        tail = out[body_start + 1:].strip() if body_start != -1 else ""
        if tail[:1] in "[{" and '"text"' in tail:
            salvaged = _salvage_text_values(tail)
            if salvaged is not None:
                out = out[:pos] + salvaged

    stripped = out.strip()
    if stripped[:1] in "[{":
        try:
            obj = json.loads(stripped)
        except (ValueError, TypeError):
            obj = None
        converted = _structured_block_to_text(obj) if obj is not None else _salvage_text_values(stripped)
        if converted:
            out = converted
    return out


def normalize_ocr_markdown(text: str) -> str:
    """把模型返回的 markdown 归一为可入库正文。

    只做「不改变语义」的整理：折叠结构化 JSON 段落块、去图占位、拆 gathered 公式块、
    修公式编号空格、压缩多余空行。**不改写条款文字、不补模型没给的符号**。
    """
    if not text:
        return ""
    body = collapse_structured_ocr_blocks(text)
    lines = [ln for ln in body.splitlines() if not _IMAGE_PLACEHOLDER_RE.match(ln)]
    body = "\n".join(lines)
    body = _GATHERED_RE.sub(lambda m: "\n".join(_split_gathered(m.group(1))), body)
    body = _EQ_NUMBER_RE.sub(lambda m: f"({_tidy_eq_number(m.group(1))})", body)
    body = _MULTI_BLANK_RE.sub("\n\n", body)
    return body.strip()


def formula_issues(text: str) -> list[dict]:
    """逐页公式体检：只报结构性问题，不猜语义。

    为什么需要：模型能给出正确的 LaTeX，但会出现「少一个右括号」这类残缺
    （实测 SC01 p39 输出 `\\frac{1}{\\eta_{\\mathrm{cr}}`，花括号不配对）。
    这种残缺肉眼极难发现，却会让下游公式解析直接失败，因此必须在入库前
    自动判定并如实记账，而不是当成正常文本放过。

    检测项（确定性，不含启发式猜测）：
    - $$ 配对、行内 $ 配对；
    - 公式内花括号配对；
    - \\left / \\right 配对；
    - \\begin{x} / \\end{x} 配对；
    - 模型自标的不确定符号。
    """
    issues: list[dict] = []
    if not text:
        return issues

    if text.count("$$") % 2:
        issues.append({"kind": "display_math_unbalanced", "detail": "$$ 数量为奇数"})

    for lineno, line in enumerate(text.replace("$$", "").splitlines(), 1):
        if line.count("$") % 2:
            issues.append({"kind": "inline_math_unbalanced", "line": lineno, "detail": line[:80]})

    displays = re.findall(r"\$\$(.+?)\$\$", text, re.S)
    inlines = re.findall(r"(?<!\$)\$([^$\n]+)\$(?!\$)", text)
    for expr in displays + inlines:
        if expr.count("{") != expr.count("}"):
            issues.append({"kind": "brace_unbalanced", "detail": expr[:100]})
        if expr.count("\\left") != expr.count("\\right"):
            issues.append({"kind": "left_right_unbalanced", "detail": expr[:100]})
        begins = re.findall(r"\\begin\{(\w+\*?)\}", expr)
        ends = re.findall(r"\\end\{(\w+\*?)\}", expr)
        if sorted(begins) != sorted(ends):
            issues.append({"kind": "environment_unbalanced", "detail": expr[:100]})

    if "\\text{?}" in text or "[不确定]" in text:
        issues.append({"kind": "model_marked_uncertain"})

    # 同一类问题只留前两条，避免一页刷出上百条噪声
    counts: dict[str, int] = {}
    kept: list[dict] = []
    for item in issues:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
        if counts[item["kind"]] <= 2:
            kept.append(item)
    return kept


# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------
def _cache_key(pdf_path: str, dpi: int, *, prompt: str | None = None) -> str:
    st = os.stat(pdf_path)
    # 提示词/装配版本入键：旧缓存是按词级压平产出的失真文本，不得复用。
    raw = f"{os.path.abspath(pdf_path)}|{st.st_size}|{int(st.st_mtime)}|{dpi}|{PROMPT_VERSION}"
    if prompt is not None:
        # 默认键逐字兼容旧公式缓存；业务提示仅以摘要隔离，不落盘提示正文。
        raw += "|custom-prompt-sha256:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _cache_load(pdf_path: str, dpi: int, *, prompt: str | None = None) -> dict[int, str]:
    prompt_kwargs = {} if prompt is None else {"prompt": prompt}
    path = os.path.join(_CACHE_DIR, _cache_key(pdf_path, dpi, **prompt_kwargs) + ".json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return {int(k): str(v) for k, v in (data.get("pages") or {}).items()}
    except Exception as exc:  # noqa: BLE001 —— 缓存损坏不影响主流程
        logger.warning("[norm-ocr] 缓存读取失败，忽略: %s", exc)
        return {}


def _cache_load_page_meta(pdf_path: str, dpi: int, *, cached_pages: dict[int, str],
                          prompt: str | None = None) -> dict[int, dict]:
    """读取同次 OCR 产生的可选布局；旧文本缓存不会凭空补造布局。"""
    prompt_kwargs = {} if prompt is None else {"prompt": prompt}
    try:
        path = os.path.join(_CACHE_DIR, _cache_key(pdf_path, dpi, **prompt_kwargs) + ".json")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        meta = data.get("page_meta") or {}
        if not isinstance(meta, dict):
            return {}
        result = {}
        for key, value in meta.items():
            if not isinstance(value, dict):
                continue
            page_no = int(key)
            text = cached_pages.get(page_no)
            if text is None or value.get("_text_sha256") != hashlib.sha256(text.encode("utf-8")).hexdigest():
                continue
            result[page_no] = {k: v for k, v in value.items() if k != "_text_sha256"}
        return result
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def _cache_save(pdf_path: str, dpi: int, pages: dict[int, str], *, prompt: str | None = None,
                page_meta: dict[int, dict] | None = None) -> None:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    prompt_kwargs = {} if prompt is None else {"prompt": prompt}
    path = os.path.join(_CACHE_DIR, _cache_key(pdf_path, dpi, **prompt_kwargs) + ".json")
    try:
        payload = {"source": os.path.basename(pdf_path), "dpi": dpi,
                   "pages": {str(k): v for k, v in sorted(pages.items())}}
        if page_meta:
            payload["page_meta"] = {
                str(k): {**v, "_text_sha256": hashlib.sha256(pages[k].encode("utf-8")).hexdigest()}
                for k, v in sorted(page_meta.items()) if k in pages
            }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[norm-ocr] 缓存写入失败，忽略: %s", exc)


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------
def _render_pages(pdf_path: str, page_nos: list[int], dpi: int) -> dict[int, dict]:
    import pymupdf as fitz

    out: dict[int, dict] = {}
    doc = fitz.open(pdf_path)
    try:
        for pno in page_nos:
            if 0 <= pno < doc.page_count:
                page = doc.load_page(pno)
                pix = page.get_pixmap(dpi=dpi)
                out[pno] = {
                    "png": pix.tobytes("png"),
                    "image_size": [pix.width, pix.height],
                    # get_pixmap 与 PDF 预览都使用裁切并旋转后的可见页面。
                    "page_size": [float(page.rect.width), float(page.rect.height)],
                    "rotation": int(page.rotation or 0),
                    "coordinate_frame": "rendered_page",
                }
    finally:
        doc.close()
    return out


# ---------------------------------------------------------------------------
# 引擎
# ---------------------------------------------------------------------------
def _local_ocr_engine():
    """懒加载本地 OCR 引擎（rapidocr 3.x 优先，回退 rapidocr_onnxruntime）。"""
    try:
        from rapidocr import RapidOCR  # type: ignore

        return RapidOCR(), "rapidocr3"
    except Exception:  # noqa: BLE001
        from rapidocr_onnxruntime import RapidOCR  # type: ignore

        return RapidOCR(), "rapidocr-onnxruntime"


def _append_word(words: list[dict], box, text) -> None:
    """归一化一条识别结果到 {text, bbox}；box 形态异常时不丢文本（bbox=None）。"""
    text = str(text or "").strip()
    if not text:
        return
    bbox = None
    try:
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        bbox = [min(xs), min(ys), max(xs), max(ys)]
    except (TypeError, ValueError, IndexError):
        bbox = None
    words.append({"text": text, "bbox": bbox})


def _local_ocr_page(engine, png_bytes: bytes, *, layout_out: list[dict] | None = None) -> str:
    """本地单页 OCR。兼容两种引擎返回形态：

    - `rapidocr` 3.x：返回 `RapidOCROutput`（对象，含 boxes/txts/scores）；
      检测为空时 boxes 为 None（合法「本页无文字」，返回空串）。
    - `rapidocr_onnxruntime` 1.x：返回 `(result, elapse)`，result 为 `[[box, text, score], ...]`。

    注：曾因只按 1.x 的元组解包而报「cannot unpack non-iterable RapidOCROutput object」，
    导致本地兜底整体失效（云端失败页无法挽回）——故此处按属性探测分支，并配契约测试。
    """
    out = engine(png_bytes)
    words: list[dict] = []

    if hasattr(out, "boxes"):  # rapidocr 3.x
        # 注意：boxes/txts 可能是 numpy 数组，禁用 `arr or []`（truthiness 歧义直接抛
        # ValueError: The truth value of an array ... is ambiguous，实测 2026-09-10 D60 导入）。
        boxes = getattr(out, "boxes", None)
        txts = getattr(out, "txts", None)
        if boxes is None or txts is None:
            return ""  # 检测为空 = 本页无文字（合法）
        for box, text in zip(boxes, txts):
            _append_word(words, box, text)
    elif isinstance(out, (tuple, list)) and len(out) >= 1:  # rapidocr_onnxruntime 1.x
        first = out[0]
        for item in (first if first is not None else []):
            try:
                box, text = item[0], item[1]
            except (TypeError, IndexError):
                continue
            _append_word(words, box, text)
    else:
        raise RuntimeError(f"未识别的本地 OCR 返回形态: {type(out).__name__}")

    if layout_out is not None:
        layout_out.extend(words)
    return words_to_text(words)


def ocr_pdf_pages(
    pdf_path: str,
    page_nos: list[int],
    *,
    dpi: int = DEFAULT_DPI,
    concurrency: int = 4,
    progress: Callable[[str], None] | None = None,
    on_page: Callable[[int, str], None] | None = None,
    use_cache: bool = True,
    prompt: str | None = None,
    use_cloud: bool = True,
    cloud_limiter=None,
    allow_local: bool = False,
) -> tuple[dict[int, str], dict]:
    """对指定页做 OCR；默认只用云端，按需逐页容错并缓存。

    返回 (page_no → 文本, stats)。stats:
        {engine, cached_pages, ocr_pages, local_pages, failed_pages}
    prompt=None 保持云端默认公式提示及旧缓存；显式业务提示使用独立摘要缓存。
    """
    def _say(msg: str) -> None:
        if progress is not None:
            progress(msg)

    def _page_done(pno: int, source: str) -> None:
        if on_page is not None:
            try:
                on_page(pno, source)
            except Exception:  # 进度显示不可改变 OCR 结果
                logger.warning("[norm-ocr] 页进度通知失败 page=%s", pno + 1, exc_info=True)

    stats: dict = {
        "engine": None,
        "cached_pages": 0,
        "ocr_pages": 0,
        "structured_pages": 0,
        "words_fallback_pages": 0,
        "local_pages": 0,
        "failed_pages": [],
        "request_errors": {},
        "formula_issue_pages": [],
        "formula_issue_details": [],
        # 可选页级产物：只有上游实际返回词级布局时才填充；不估算 confidence，
        # 也不为历史缓存补造坐标。BS 解析层会把该字段作为可选 ocr_meta 持久化。
        "page_meta": {},
        # 缓存键是可复现的输入指纹（路径/文件 stat/DPI/提示词摘要），
        # 供 BS 运行回执引用；不把缓存文本当成新的 OCR 产物。
        "cache_key": None,
    }
    if not page_nos:
        return {}, stats

    prompt_kwargs = {} if prompt is None else {"prompt": prompt}
    try:
        stats["cache_key"] = _cache_key(pdf_path, dpi, **prompt_kwargs)
    except OSError:
        stats["cache_key"] = None
    cached = _cache_load(pdf_path, dpi, **prompt_kwargs) if use_cache else {}
    cached_meta = (_cache_load_page_meta(pdf_path, dpi, cached_pages=cached, **prompt_kwargs)
                   if use_cache and cached else {})
    pages: dict[int, str] = {}
    todo: list[int] = []
    for pno in page_nos:
        if pno in cached and cached[pno].strip():
            pages[pno] = cached[pno]
            if pno in cached_meta:
                stats["page_meta"][pno] = cached_meta[pno]
            stats["cached_pages"] += 1
            _page_done(pno, "cache")
        else:
            todo.append(pno)
    if not todo:
        _say(f"    [OCR] 全部命中缓存（{stats['cached_pages']} 页）")
        return pages, stats

    pngs = _render_pages(pdf_path, todo, dpi)

    # ---- 云端并发（逐页容错） ----
    cloud_cfg: dict = {}
    if use_cloud:
        try:
            from app.plus.safety_review.ocr_cloud import cloud_config

            cloud_cfg = cloud_config() or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("[norm-ocr] 云端 OCR 不可用%s: %s", "，转本地" if allow_local else "，需人工核实", exc)

    if cloud_cfg:
        import asyncio

        from app.plus.safety_review.ocr_cloud import cloud_ocr_structured

        sem = asyncio.Semaphore(max(1, int(concurrency)))
        failed_cloud: list[int] = []

        async def one(pno: int) -> None:
            async with sem:
                try:
                    call_kwargs = dict(prompt_kwargs)
                    if cloud_cfg.get("billing_model_code"):
                        call_kwargs["billing_model_code"] = cloud_cfg["billing_model_code"]
                    if cloud_limiter is not None:
                        while not cloud_limiter.acquire(blocking=False):
                            await asyncio.sleep(0.05)
                    try:
                        rich = await cloud_ocr_structured(
                            pngs[pno]["png"], model=cloud_cfg["model"],
                            endpoint=cloud_cfg["endpoint"], api_key=cloud_cfg["api_key"],
                            **call_kwargs,
                        )
                    finally:
                        if cloud_limiter is not None:
                            cloud_limiter.release()
                    # 结构化正文优先：它承载 LaTeX 公式，词级装配只作为回退。
                    structured = normalize_ocr_markdown(str(rich.get("markdown") or ""))
                    text = structured or words_to_text(rich.get("words") or [])
                    if text.strip():
                        pages[pno] = text
                        words = [
                            {"text": str(word.get("text") or ""),
                             "bbox": word.get("bbox")}
                            for word in (rich.get("words") or [])
                            if isinstance(word, dict) and str(word.get("text") or "").strip()
                        ]
                        # 只有真实词级布局存在时才生成页元数据；仅有 markdown
                        # 文本时不添加空 layout，避免把“没有坐标”误当成坐标产物。
                        if words:
                            stats["page_meta"][pno] = {
                                "source": "cloud",
                                "layout": words,
                                "confidence": None,
                                "image_size": pngs[pno]["image_size"],
                                "page_size": pngs[pno]["page_size"],
                                "rotation": pngs[pno]["rotation"],
                                "coordinate_frame": pngs[pno]["coordinate_frame"],
                            }
                        stats["ocr_pages"] += 1
                        if structured:
                            stats["structured_pages"] += 1
                        else:
                            stats["words_fallback_pages"] += 1
                        issues = formula_issues(text)
                        if issues:
                            stats["formula_issue_pages"].append(pno + 1)
                            stats["formula_issue_details"].append({"pdf_page": pno + 1, "issues": issues})
                        _page_done(pno, "cloud")
                    else:
                        failed_cloud.append(pno)
                except Exception as exc:  # noqa: BLE001 —— 单页失败不中断整本
                    from app.plus.request_errors import classify_request_error
                    category = classify_request_error(exc)
                    stats["request_errors"][pno] = category
                    logger.warning("[norm-ocr] 云端第 %s 页失败: %s", pno + 1, category)
                    failed_cloud.append(pno)

        async def run() -> None:
            await asyncio.gather(*(one(p) for p in todo))

        try:
            asyncio.run(run())
            stats["engine"] = f"cloud:{cloud_cfg['model']}"
        except Exception as exc:  # noqa: BLE001
            logger.warning("[norm-ocr] 云端并发层异常%s: %s", "，转本地" if allow_local else "，需人工核实", exc)
            failed_cloud = list(todo)
    else:
        failed_cloud = list(todo)

    # ---- 本地兜底 ----
    if failed_cloud and not allow_local:
        for pno in failed_cloud:
            if pno not in pages and pno not in stats['failed_pages']:
                stats['failed_pages'].append(pno)
                _page_done(pno, 'failed')
    elif failed_cloud:
        try:
            engine, kind = _local_ocr_engine()
            for pno in failed_cloud:
                try:
                    words: list[dict] = []
                    text = _local_ocr_page(engine, pngs[pno]["png"], layout_out=words)
                    if text.strip():
                        pages[pno] = text
                        if words:
                            stats["page_meta"][pno] = {
                                "source": "local",
                                "layout": words,
                                "confidence": None,
                                "image_size": pngs[pno]["image_size"],
                                "page_size": pngs[pno]["page_size"],
                                "rotation": pngs[pno]["rotation"],
                                "coordinate_frame": pngs[pno]["coordinate_frame"],
                            }
                        stats["local_pages"] += 1
                        stats["engine"] = f"{stats['engine'] or ''}+{kind}".lstrip("+")
                        _page_done(pno, "local")
                    else:
                        # 本地亦无文字（空白页/纯图页）——必须记账，不得静默丢弃
                        stats["failed_pages"].append(pno)
                        _page_done(pno, "failed")
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[norm-ocr] 本地第 %s 页失败: %s", pno + 1, str(exc)[:160])
                    stats["failed_pages"].append(pno)
                    _page_done(pno, "failed")
        except Exception as exc:  # noqa: BLE001 —— 本地引擎不可用则如实记失败
            logger.warning("[norm-ocr] 本地 OCR 引擎不可用: %s", exc)
            stats["failed_pages"].extend(p for p in failed_cloud if p not in pages)
            for pno in failed_cloud:
                if pno not in pages:
                    _page_done(pno, "failed")

    if use_cache and pages:
        merged = dict(cached)
        merged.update(pages)
        merged_meta = {p: meta for p, meta in cached_meta.items() if p in cached and p not in todo}
        merged_meta.update(stats["page_meta"])
        meta_kwargs = {"page_meta": merged_meta} if merged_meta else {}
        _cache_save(pdf_path, dpi, merged, **prompt_kwargs, **meta_kwargs)

    _say(
        f"    [OCR] 新增 {stats['ocr_pages']} 页（云端，其中结构化正文 {stats['structured_pages']} 页、"
        f"词级回退 {stats['words_fallback_pages']} 页）+ {stats['local_pages']} 页（本地）"
        f"，缓存命中 {stats['cached_pages']} 页，失败 {len(stats['failed_pages'])} 页"
    )
    return pages, stats
