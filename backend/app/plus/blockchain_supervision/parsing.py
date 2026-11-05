"""PDF 文本层提取（W1 口径：fitz 同步提取；P2-1 起扫描件走 OCR 兜底）。

诚实纪律：扫描件（无文本层）**不假装解析**——先走 OCR 兜底（复用
structural_check.ocr_text：独立版只使用线上 OCR，禁用本地兜底及旧缓存）；OCR 不可用或全部
失败时记录降级 no_text_layer，下游 A3-01 对表据此判「不可判定」而不是
「缺失」，绝不伪造内容。红章图像检测复用 startup_conditions_check，此处不碰。
"""
import logging
import hashlib
import os
import re
import tempfile
from threading import BoundedSemaphore
from collections import Counter
from copy import deepcopy
from html import unescape

from .personnel_evidence import special_trade_layout_rows

logger = logging.getLogger(__name__)

_BS_CLOUD_OCR_CONCURRENCY = 5
_BS_CLOUD_OCR_LIMITER = BoundedSemaphore(_BS_CLOUD_OCR_CONCURRENCY)

UPLOADS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "uploads", "bs")

# 全文总字符低于该值视为无文本层（扫描件）
_MIN_TEXT_CHARS = 10

# 技术质量提示，非签章/授权合法性阈值：短签名栏不能证明同页正文已完整识别。
_SPARSE_SIGNATURE_BODY_CHARS = 80

# OCR 兜底页数封顶（可选成本护栏：超长扫描件只识别前 N 页，其余诚实记录需人工翻看）。
# 默认全页；部署侧可用环境变量 BS_OCR_MAX_PAGES 显式设置上限。未设置时不再静默截断，
# 非法值和 `full`/`unlimited` 回到全页口径。
# ⚠️ 该上限**只服务正式核查**（A1/A3 全量文本解析）。主体类型识别走
# `ocr_text_pages(..., page_nos=...)` 的**有界请求**（见 SUBJECT_TYPE_OCR_MAX_PAGE），
# 两条边界互不顶替：不得把本上限改成 10，也不得先按 30 页识别再在判定层截断
# （T3.2-R1 §2.1）。
_OCR_MAX_PAGES = None

# 主体类型识别专用 OCR 页数上限（T3.2-R1 §2.1）：识别只看**原始第 1..10 页**。
# 由 service 侧按此上限分段请求（1..3 → 4..10），识别侧 OCR 请求永不触及第 11 页。
SUBJECT_TYPE_OCR_MAX_PAGE = 10

# 分包合同的逐字转写要求；共享OCR保留默认行为，此提示只在BS入口显式选用。
BS_CONTRACT_OCR_PROMPT = (
    "请逐字完整识别这张合同扫描页中的所有可见文字，按原文阅读顺序转写为Markdown。"
    "不要总结、解释、改写、补写或遗漏正文。保留条款编号、标题、日期和表格内的实际单元格内容。"
    "仅在图片确有表格时用表格呈现；不要生成不存在的空表、重复行或LaTeX表格。"
    "不要因为版面上的横线或段落对齐把普通正文转换成空表。"
    "印章和手写文字仅转写能辨认的实际文字，不能辨认的位置写[无法辨认]，不要猜测。"
)


def _ocr_max_pages() -> int | None:
    raw = os.environ.get("BS_OCR_MAX_PAGES")
    if not raw or raw.strip().lower() in {"full", "unlimited"}:
        return _OCR_MAX_PAGES
    try:
        n = int(raw)
        return n if n > 0 else _OCR_MAX_PAGES
    except ValueError:
        return _OCR_MAX_PAGES


def _unique_upload_path(dir_path: str, safe_name: str, content: bytes) -> str:
    """同名不覆盖：内容相同则复用同一路径（幂等），内容不同则递增 `__2`/`__3` 后缀。

    为什么必须这么做（P1-01 / 设计 74）：`BsFinding` 的证据是 `(material_id, page, quote)`，
    引文只指向 `material.path`。旧实现同名后传覆盖前传 ⇒ 补件重传后旧结论的引文指向
    「同一个路径、不同的内容」，审计链断裂且无法发现。

    为什么不是无条件加后缀：同一份文件重复上传（刷新、重试、双端同时点）会产生一堆副本，
    而 `uq_bs_material_container_entry` 之类的幂等键反而更难对齐。所以先比内容。

    并发：用 `O_CREAT|O_EXCL` 原子建文件，两个请求同时判「不存在」时只有一个能建成，
    另一个落到 `FileExistsError` 分支继续比较/递增，不会互相覆盖。
    """
    stem, ext = os.path.splitext(safe_name)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    for n in range(1, 1000):
        name = safe_name if n == 1 else f"{stem}__{n}{ext}"
        abs_path = os.path.join(dir_path, name)
        try:
            fd = os.open(abs_path, flags)
        except FileExistsError:
            try:
                with open(abs_path, "rb") as f:
                    if f.read() == content:
                        return abs_path  # 同一份文件：复用，避免副本堆积
            except OSError:
                pass  # 读不了就当不同内容，换下一个后缀（保守：宁可多一份，不可覆盖）
            continue
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        return abs_path
    raise RuntimeError("同名文件过多，拒绝继续落盘")


def save_upload(task_id: int, file_name: str, content: bytes, *, subdir: str = "") -> str:
    """落盘到 app/uploads/bs/<task_id>/[<subdir>/]，返回相对 app/ 的路径（与 structural_check 同约定）。

    `subdir`（T4.1-T2 新增，默认 `""`）：ZIP 容器传 `containers/<随机标识>`，落到专属子目录。

    **同名不覆盖（P1-01）**：同任务、同目录、同名但内容不同时，落到 `name__2.pdf`、
    `name__3.pdf`；内容完全相同时复用原路径。旧行为是后传覆盖前传，会让历史结论的
    引文指向被换掉的内容（设计 74 §2）。返回值仍为「相对 app/ 的路径」，调用方无需改动；
    首次上传的磁盘布局与返回值和旧版逐字节一致。
    """
    base = os.path.abspath(UPLOADS_DIR)
    dir_path = os.path.join(base, str(task_id))
    if subdir:
        # 只接受相对子目录：拒绝对路径与 `..` 段（本函数同时服务上传入口，不信任入参）
        parts = [p for p in str(subdir).replace("\\", "/").split("/")
                 if p not in ("", ".")]
        if not parts or any(p == ".." for p in parts):
            raise ValueError("非法的上传子目录")
        dir_path = os.path.join(dir_path, *parts)
    os.makedirs(dir_path, exist_ok=True)
    # 文件名只保留安全部分，避免路径穿越与非 ASCII 落盘问题
    safe_name = os.path.basename(file_name).replace("\\", "_").replace("/", "_") or "upload.bin"
    abs_path = _unique_upload_path(dir_path, safe_name, content)
    return os.path.relpath(abs_path, os.path.join(os.path.dirname(__file__), "..", ".."))


def extract_text_pages(abs_path: str, *, progress=None) -> tuple:
    """返回 (pages, degradations)：pages=[{"page": 1-based, "text": str}]。

    - 无文本层（扫描件）：pages=[] + 降级 no_text_layer（不伪造内容）
    - 非 PDF/损坏：抛出由调用方转 parse failed
    """
    import fitz  # PyMuPDF，backend venv 现成

    degradations = []
    pages = []
    with fitz.open(abs_path) as doc:
        total_pages = doc.page_count
        for i, page in enumerate(doc):
            text = (page.get_text("text") or "").strip()
            if text:
                pages.append({"page": i + 1, "text": text})
            if progress is not None:
                progress(i + 1, total_pages, "text", total_pages)
    total_chars = sum(len(p["text"]) for p in pages)
    if total_chars < _MIN_TEXT_CHARS:
        degradations.append({
            "kind": "no_text_layer",
            "message": "无文本层（疑似扫描件），OCR 解析待接入；涉及文本的核查将判不可判定",
        })
        pages = []
    return pages, degradations


def ocr_text_pages(abs_path: str, *, page_nos=None, use_cache: bool = True,
                   use_cloud: bool = True, progress=None) -> tuple:
    """扫描件 OCR 兜底（P2-1，复用 structural_check.ocr_text）。

    `page_nos`：**原始 PDF 页码**（1-based）列表。None = 正式核查读取全部原始页；
    只有显式设置 `BS_OCR_MAX_PAGES` 时才按前 N 页请求。
    主体类型识别传入原始第 1..10 页的**有界**列表，使边界在 OCR 阶段即生效，
    而不是先识别 30 页再截断（T3.2-R1 §2.1）。

    返回 (pages, info)：
    - 成功：pages=[{"page": 1-based 原始文件页码, "text": str}]（按页升序、空文本页剔除），
      info={"recovered_pages", "total_pages", "unrecovered_pages", "engine"}
    - OCR 链路不可用 / 全部页失败 / 无文字产出：([], None)——调用方保留
      no_text_layer 降级，不伪造内容。

    ⚠️ 页码换算（T3.2-R1 §2.2）：底层 `ocr_pdf_pages` 的 `page_nos` 是 **0-based
    页索引**，本函数在边界上只暴露原始页码：入参按「原始页码 − 1」换算成索引，
    出参按「索引 + 1」换回原始页码。旧版直接把 1..N 当索引用，导致内容整体错位一页
    （第 1 页永远识别不到、单页扫描件识别为空）；此处修正后 page 可回原始文件定位。

    独立版业务 OCR 始终使用云端，禁用旧 OCR 缓存与本地兜底；失败页留待人工核实。
    """
    try:
        import fitz

        from ..structural_check.ocr_text import collapse_structured_ocr_blocks, ocr_pdf_pages
        with fitz.open(abs_path) as doc:
            total = doc.page_count
        if page_nos is None:
            limit = _ocr_max_pages()
            end = total if limit is None else min(total, limit)
            wanted = list(range(1, end + 1))
        else:
            wanted = []
            for raw in page_nos:
                try:
                    wanted.append(int(raw))
                except (TypeError, ValueError):
                    continue  # 非法页码不猜测，直接丢弃（保守：不位移到别的页）
        # 原始页码 → 0-based 引擎索引；越界页在 OCR 阶段剔除（不请求空页）
        idx_to_page = {no - 1: no for no in wanted if 1 <= no <= total}
        if not idx_to_page:
            return [], None
        # 分包所有文件共用进程内云 OCR 限额，避免文件数乘以页并发。
        if not use_cloud:
            raise ValueError('分包材料仅支持线上 OCR')
        ocr_kwargs = dict(concurrency=_BS_CLOUD_OCR_CONCURRENCY, prompt=BS_CONTRACT_OCR_PROMPT,
                          use_cache=False, use_cloud=True, allow_local=False,
                          cloud_limiter=_BS_CLOUD_OCR_LIMITER)
        if progress is not None:
            def on_page(index, source):
                if index in idx_to_page:
                    progress(idx_to_page[index], total, source, len(idx_to_page))
            ocr_kwargs["on_page"] = on_page
        pages_map, stats = ocr_pdf_pages(abs_path, sorted(idx_to_page), **ocr_kwargs)
        pages = []
        page_meta = stats.get("page_meta") if isinstance(stats, dict) else {}
        if not isinstance(page_meta, dict):
            page_meta = {}
        for i, text in sorted(pages_map.items()):
            if i not in idx_to_page:  # 引擎契约：返回键 ⊆ 请求索引；越界键不猜测映射
                logger.warning("[bs] OCR 返回未请求的页索引 %s，已忽略", i)
                continue
            # 结构化 JSON 段落在本模块再折叠一次（BS-P1-10）：`ocr_pdf_pages` 的磁盘
            # 缓存存的是**归一后的成品文本**，只改上游 normalizer 不会重算已有缓存，
            # 线上历史文件仍会带着 `rotate_rect` 坐标进判定。这里做的是幂等折叠，
            # 对已干净的文本无副作用。
            text = collapse_structured_ocr_blocks(text or "")
            if text.strip():
                page = {"page": idx_to_page[i], "text": text}
                # 只持久化真实上游返回的可选元数据。旧缓存/测试桩没有 page_meta
                # 时继续返回原来的两字段结构，保证历史材料和调用契约兼容。
                meta = page_meta.get(i)
                if isinstance(meta, dict) and any(
                        meta.get(key) is not None for key in (
                            "source", "layout", "confidence", "image_size",
                            "page_size", "rotation", "coordinate_frame")):
                    ocr_meta = {
                        "source": meta.get("source"),
                        "layout": meta.get("layout"),
                        "confidence": meta.get("confidence"),
                        "quality": "ocr_ok",
                        "retry_count": 0,
                    }
                    for key in ("image_size", "page_size", "rotation", "coordinate_frame"):
                        if meta.get(key) is not None:
                            ocr_meta[key] = meta[key]
                    page["ocr_meta"] = ocr_meta
                pages.append(page)
        if not pages:
            return [], None
        info = {
            "recovered_pages": len(pages),
            "total_pages": total,
            "unrecovered_pages": max(0, total - len(pages)),
            "engine": stats.get("engine"),
            "cache_key": stats.get("cache_key"),
            "layout_pages": sum(
                1 for meta in page_meta.values()
                if isinstance(meta, dict) and meta.get("layout")
            ),
        }
        return pages, info
    except Exception as e:  # OCR 整体不可用 → 诚实降级，不阻断摄取
        logger.warning("[bs] OCR 兜底不可用 %s: %s", os.path.basename(abs_path), e)
        return [], None


def ocr_rotated_text_pages(abs_path: str, page_nos: list[int]) -> tuple:
    """仅对指定物理页创建隔离旋转副本；不沿用旋转图像坐标作为原件坐标。"""
    if not page_nos:
        return [], None
    try:
        import fitz  # 按需加载 PDF 引擎，避免导入期初始化重量级依赖。
        with tempfile.TemporaryDirectory(prefix="bs-ocr-rotation-") as directory:
            target = os.path.join(directory, "rotated.pdf")
            with fitz.open(abs_path) as original, fitz.open() as rotated:
                valid = sorted({page for page in page_nos if type(page) is int and 1 <= page <= original.page_count})
                for no in valid:
                    rotated.insert_pdf(original, from_page=no - 1, to_page=no - 1)
                    rotated[-1].set_rotation((rotated[-1].rotation + 90) % 360)
                if not valid:
                    return [], None
                rotated.save(target)
            pages, info = ocr_text_pages(target, page_nos=list(range(1, len(valid) + 1)),
                                         use_cache=False, use_cloud=True)
            output = []
            for page in pages:
                position = page.get("page")
                if type(position) is not int or not 1 <= position <= len(valid):
                    continue
                meta = page.get("ocr_meta") or {}
                text = page.get("text") or ""
                rotated_meta = {"source": "cloud_rotated", "quality": meta.get("quality", "ocr_ok"),
                                "retry_count": 2, "layout": [], "rotation_retry": 90}
                if re.search(r"(?:特殊工种|特种作业).{0,16}(?:名单|名册)", text):
                    clues = [{"name": name, "post": post, **({"parts": parts} if len(parts) > 1 else {}),
                              **({"partial": True} if not post.endswith(("工", "司机", "指挥", "操作员", "驾驶员")) else {})}
                             for name, post, parts in special_trade_layout_rows(page, with_parts=True, allow_partial=True)]
                    if clues:
                        rotated_meta["rotation_roster_clues"] = clues
                output.append({"page": valid[position - 1], "text": text, "ocr_meta": rotated_meta})
            return output, info
    except Exception as exc:
        logger.warning("[bs] 横置页补读不可用 %s: %s", os.path.basename(abs_path), exc)
        return [], None


def _complete_roster_role_from_table(text: str, name: str, prefix: str) -> str | None:
    """仅在原 OCR 的唯一编号表格行中补全同一人的工种，不跨行借词。"""
    if len(prefix) < 3:
        return None
    rows = []
    for table in re.findall(r"<table\b[^>]*>.*?</table\s*>", text, re.I | re.S):
        if re.search(r"\b(?:rowspan|colspan)\s*=", table, re.I):
            continue
        for row in re.findall(r"<tr\b[^>]*>(.*?)</tr\s*>", table, re.I | re.S):
            cells = [unescape(re.sub(r"<[^>]*>", "", cell)).strip() for cell in
                     re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]\s*>", row, re.I | re.S)]
            if cells.count(name) == 1:
                rows.append(cells)
    if len(rows) != 1 or not rows[0] or not re.fullmatch(r"\d+", rows[0][0]):
        return None
    candidates = [cell for cell in rows[0] if cell.startswith(prefix) and
                  re.fullmatch(r"[\u4e00-\u9fa5]{2,10}", cell) and
                  cell.endswith(("工", "司机", "指挥", "操作员", "驾驶员"))]
    return candidates[0] if len(candidates) == 1 else None


def _merge_rotation_roster_clues(original: dict, rotated: dict) -> dict:
    """仅将旋转版面中的可交叉核对线索合入副本；不改变原文或页质量。"""
    result = deepcopy(original)
    text, rotated_text = original.get("text") or "", rotated.get("text") or ""
    if (original.get("page") != rotated.get("page")
            or not re.search(r"(?:特殊工种|特种作业).{0,16}(?:名单|名册)", rotated_text)):
        return result
    meta = rotated.get("ocr_meta") or {}
    clues = meta.get("rotation_roster_clues") if isinstance(meta, dict) else None
    if not isinstance(clues, list):
        return result
    valid = []
    for clue in clues:
        if (not isinstance(clue, dict) or not isinstance(clue.get("name"), str)
                or not isinstance(clue.get("post"), str)
                or not re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", clue["name"])
                or not re.fullmatch(r"[\u4e00-\u9fa5]{2,10}", clue["post"])
                or clue["name"] not in text or clue["name"] not in rotated_text):
            continue
        post = clue["post"]
        if clue.get("partial") is True:
            post = _complete_roster_role_from_table(text, clue["name"], post) if post in rotated_text else None
        elif not (post in rotated_text or (
                isinstance(clue.get("parts"), list) and len(clue["parts"]) == 2
                and all(isinstance(part, str) and part and part in rotated_text for part in clue["parts"])
                and "".join(clue["parts"]) == post)):
            continue
        if post and post in text:
            valid.append({"name": clue["name"], "post": post})
    if valid:
        old_meta = result.get("ocr_meta") if isinstance(result.get("ocr_meta"), dict) else {}
        result["ocr_meta"] = {**old_meta, "rotation_roster_clues": deepcopy(valid)}
    return result


def _source_sha256(abs_path: str) -> str:
    digest = hashlib.sha256()
    with open(abs_path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_targeted_roster_candidate(abs_path: str, pages: list, page_nos: list[int], *, expected_sha256: str) -> dict:
    """只生成指定页补读候选，不修改调用方数据、数据库或原件。"""
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
        raise ValueError("原件指纹无效")
    digest = expected_sha256.lower()
    if _source_sha256(abs_path) != digest:
        raise ValueError("原件已变化，不能复用既有解析")
    if not isinstance(pages, list) or not isinstance(page_nos, list) or not page_nos:
        raise ValueError("指定页无效")
    if any(type(n) is not int or n < 1 for n in page_nos) or len(set(page_nos)) != len(page_nos):
        raise ValueError("指定页无效")
    indexed = {p.get("page"): p for p in pages if isinstance(p, dict) and type(p.get("page")) is int}
    if len(indexed) != len(pages) or any(n not in indexed or not isinstance(indexed[n].get("text"), str) for n in page_nos):
        raise ValueError("指定页不存在或无法核对原解析")
    rotated, info = ocr_rotated_text_pages(abs_path, page_nos)
    if _source_sha256(abs_path) != digest:
        raise ValueError("原件在补读期间变化，候选已丢弃")
    if any(not isinstance(p, dict) or p.get("page") not in page_nos for p in rotated):
        raise ValueError("补读返回了未请求的页")
    by_page = {p["page"]: p for p in rotated}
    if len(by_page) != len(rotated):
        raise ValueError("补读返回了重复页")
    output, changed = [], []
    for page in pages:
        no = page["page"]
        merged = _merge_rotation_roster_clues(page, by_page[no]) if no in by_page else deepcopy(page)
        output.append(merged)
        if merged != page:
            changed.append(no)
    return {"source_sha256": digest, "pages": output, "changed_pages": changed,
            "status": "completed" if info is not None else "unavailable", "engine": (info or {}).get("engine")}


def prepare_targeted_page_text_candidate(abs_path: str, pages: list, page_nos: list[int], *, expected_sha256: str) -> dict:
    """只读生成页正文候选；候选须人工核验，不能直接视为已读或覆盖原解析。"""
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
        raise ValueError("原件指纹无效")
    digest = expected_sha256.lower()
    if _source_sha256(abs_path) != digest:
        raise ValueError("原件已变化，不能复用既有解析")
    if (not isinstance(pages, list) or not isinstance(page_nos, list) or not page_nos
            or any(type(n) is not int or n < 1 for n in page_nos) or len(set(page_nos)) != len(page_nos)):
        raise ValueError("指定页无效")
    indexed = {p.get("page"): p for p in pages if isinstance(p, dict) and type(p.get("page")) is int}
    if len(indexed) != len(pages) or any(n not in indexed or not isinstance(indexed[n].get("text"), str) for n in page_nos):
        raise ValueError("指定页不存在或无法核对原解析")
    recognized, info = ocr_text_pages(abs_path, page_nos=page_nos, use_cache=False, use_cloud=True)
    if _source_sha256(abs_path) != digest:
        raise ValueError("原件在补读期间变化，候选已丢弃")
    if not isinstance(recognized, list) or any(not isinstance(p, dict) or type(p.get("page")) is not int
                                               or p["page"] not in page_nos for p in recognized):
        raise ValueError("补读返回了未请求的页")
    if len({p["page"] for p in recognized}) != len(recognized):
        raise ValueError("补读返回了重复页")
    if any(not isinstance(p.get("text"), str) or not p["text"].strip() for p in recognized):
        raise ValueError("补读返回了空白或无效正文")
    if not recognized or not isinstance(info, dict):
        return {"source_sha256": digest, "pages": [], "status": "unavailable", "engine": None,
                "requested_pages": sorted(page_nos), "missing_pages": sorted(page_nos)}
    candidates = []
    for page in recognized:
        suspect = (_ocr_quality_suspect(page["text"]) or _ocr_sideways_layout(page)
                   or _ocr_single_short_fragment(page) or _ocr_parallel_certificate_columns(page))
        candidates.append({**deepcopy(page), "quality_suspect": bool(suspect)})
    return {"source_sha256": digest, "pages": candidates, "status": "review_required", "engine": info.get("engine"),
            "requested_pages": sorted(page_nos), "missing_pages": sorted(set(page_nos) - {p["page"] for p in recognized})}


def reviewed_readable_pages(material) -> set[int]:
    """按记录顺序处理质量确认及版本失效，且只允许有正文的页被确认。"""
    text_pages = {page.get("page") for page in (getattr(material, "pages_json", None) or [])
                  if isinstance(page, dict) and type(page.get("page")) is int and page["page"] > 0
                  and isinstance(page.get("text"), str) and page["text"].strip()}
    reviewed = set()
    for record in getattr(material, "degradations_json", None) or []:
        if not isinstance(record, dict):
            continue
        pages = {p for p in (record.get("pages") or []) if type(p) is int and p in text_pages}
        if record.get("kind") == "reading_quality_review_invalidated":
            reviewed.difference_update(pages)
        elif record.get("kind") == "reading_quality_review" and record.get("outcome") == "readable":
            reviewed.update(pages)
    return reviewed


def _unreviewed_ocr_quality_pages(material) -> set[int]:
    """按页复核旧缓存的横置或短残片，不改写原始解析记录。"""
    reviewed = reviewed_readable_pages(material)
    return {page["page"] for page in (getattr(material, "pages_json", None) or [])
            if isinstance(page, dict) and isinstance(page.get("page"), int)
            and not isinstance(page["page"], bool) and page["page"] > 0
            and page["page"] not in reviewed
            and (_ocr_sideways_layout(page) or _ocr_single_short_fragment(page)
                 or _ocr_image_placeholder_fragment(page.get("text") or "")
                 or _ocr_parallel_certificate_columns(page))}


def material_reading_gap(material) -> list[int] | None:
    """材料读取缺口唯一判据；None表示无已知缺口，空数组表示未记录页码。"""
    incomplete = getattr(material, "parse_state", "done") != "done"
    unread = set()
    reviewed_pages = reviewed_readable_pages(material)
    for gap in getattr(material, "degradations_json", None) or []:
        kind = gap.get("kind")
        if kind == "parse_incomplete":
            unread.update(p for p in gap.get("unread_pages", [])
                          if isinstance(p, int) and not isinstance(p, bool) and p > 0)
            suspect = {
                p for p in (gap.get("ocr_suspect_pages") or [])
                if isinstance(p, int) and not isinstance(p, bool) and p > 0
            }
            failed_pages = {
                p for p in (gap.get("ocr_failed_pages") or [])
                if isinstance(p, int) and not isinstance(p, bool) and p > 0
            }
            unread.update(suspect | failed_pages)
            if (unread | suspect | failed_pages) - reviewed_pages:
                incomplete = True
        elif kind in {"no_text_layer", "parse_failed", "parse_interrupted"}:
            incomplete = True
        elif kind == "ocr_fallback" and not gap.get("coverage_recorded"):
            recovered, total = gap.get("recovered_pages"), gap.get("total_pages")
            if isinstance(recovered, int) and isinstance(total, int) and recovered < total:
                incomplete = True
    if not any((p.get("text") or "").strip() for p in (material.pages_json or [])):
        incomplete = True
    unread -= reviewed_pages
    suspect_pages = _unreviewed_ocr_quality_pages(material)
    if suspect_pages:
        incomplete = True
        unread.update(suspect_pages)
    return sorted(unread) if incomplete else None


def describe_reading_gap(material, pages, *, limit=None) -> str:
    """将未读页与已有输出但质量可疑的页分别说明。"""
    if not pages:
        return "部分内容未读（页码未记录）"
    suspect = set(reading_coverage(material).get("suspect_pages") or []) & set(pages)
    unread = [page for page in pages if page not in suspect]
    quality = [page for page in pages if page in suspect]
    def format_pages(values):
        shown = values[:limit] if limit else values
        return "、".join(map(str, shown)) + ("等" if limit and len(values) > limit else "")
    return "；".join(filter(None, [
        "第%s页未读" % format_pages(unread) if unread else "",
        "第%s页识别质量待核实" % format_pages(quality) if quality else "",
    ]))


def material_page_readable(material, page_no) -> bool:
    """页级线索仅在未读、失败和可疑状态排除后可用。"""
    records = getattr(material, "degradations_json", None) or []
    if (getattr(material, "parse_state", "done") != "done"
            or any(isinstance(record, dict) and record.get("kind") in
                   {"no_text_layer", "parse_failed", "parse_interrupted"} for record in records)):
        return False
    gap = material_reading_gap(material)
    if gap is None:
        return True
    if not gap or page_no in gap:
        return False
    reviewed = reviewed_readable_pages(material)
    uncertain = {p for record in records if record.get("kind") == "parse_incomplete"
                 for key in ("ocr_suspect_pages", "ocr_failed_pages") for p in record.get(key) or []}
    return page_no not in uncertain or page_no in reviewed


def reading_coverage(material) -> dict:
    """材料读取覆盖（BS-P1-09 / 设计 98 §8）：只从 `degradations_json` + `pages_json` 派生。

    返回 `{total_pages, read_pages, unread_pages, over_limit_pages, blank_pages,
    suspect_pages, degraded, reading_complete}`：
    - `total_pages`：降级记录里的 `total_pages`（唯一可信来源）；无降级记录时取
      `pages_json` 最大页码；两者都没有 → None（未知，不猜）；
    - `read_pages`：`parse_incomplete.read_pages` 优先；否则 `pages_json` 中有正文的页码；
    - `unread_pages`：`parse_incomplete.unread_pages`（**含**超出识别页数上限的页码）；
    - `over_limit_pages`：`parse_incomplete.ocr_skipped_pages`（超上限的子集，单独披露）；
    - `blank_pages`：`parse_incomplete.ocr_blank_pages`（物理空白页的子集，单独披露）；
    - `suspect_pages`：`parse_incomplete.ocr_suspect_pages`（已取得文字但质量可疑，需人工核对）；
    - `degraded`：存在未读页，或整体读取失败/无文本层（`no_text_layer` /
      `parse_failed` / `parse_interrupted`）。
    - `reading_complete`：已知总页数、每一页都有可读正文，且没有读取降级；这是
      最终报告的读取闸门，不等同于 `parse_state == "done"` 或核查组已完成。

    页集合由保存的解析记录、旧缓存横置检测和有效人工复核共同派生；
    仅人工确认且存在正文的页可以解除对应质量疑点。本函数不写库。
    """
    def _page_no(value) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value > 0

    degradations = [d for d in (getattr(material, "degradations_json", None) or [])
                    if isinstance(d, dict)]
    pages = [p for p in (getattr(material, "pages_json", None) or [])
             if isinstance(p, dict)]
    total_pages = None
    read_pages = []
    unread_pages = []
    over_limit_pages = []
    blank_pages = []
    suspect_pages = []
    page_states = []
    page_state_history = []
    failed = getattr(material, "parse_state", "done") != "done"
    incomplete = None
    refresh_pending = set()
    for gap in degradations:
        kind = gap.get("kind")
        if isinstance(gap.get("total_pages"), int) and not isinstance(gap.get("total_pages"), bool):
            total_pages = gap["total_pages"]
        if kind == "parse_incomplete":
            if gap.get("reason") == "page_refresh_review_required":
                refresh_pending.update(p for p in gap.get("unread_pages") or [] if _page_no(p))
            else:
                incomplete = gap
        elif kind in {"no_text_layer", "parse_failed", "parse_interrupted"}:
            failed = True
    if total_pages is None and pages:
        page_numbers = [p.get("page") for p in pages if _page_no(p.get("page"))]
        total_pages = max(page_numbers) if page_numbers else None
    if incomplete is not None:
        read_pages = sorted({p for p in incomplete.get("read_pages") or [] if _page_no(p)})
        unread_pages = sorted({p for p in incomplete.get("unread_pages") or [] if _page_no(p)})
        over_limit_pages = sorted({p for p in incomplete.get("ocr_skipped_pages") or []
                                   if _page_no(p)})
        blank_pages = sorted({p for p in incomplete.get("ocr_blank_pages") or []
                              if _page_no(p)})
        suspect_pages = sorted({p for p in incomplete.get("ocr_suspect_pages") or []
                                if _page_no(p)})
        unread_pages = sorted(set(unread_pages) | {p for p in incomplete.get("ocr_failed_pages") or []
                                                  if _page_no(p)})
        page_states = [item for item in (incomplete.get("page_states") or [])
                       if isinstance(item, dict) and _page_no(item.get("page"))]
        page_state_history = [item for item in (incomplete.get("page_state_history") or [])
                              if isinstance(item, dict) and _page_no(item.get("page"))]
    else:
        read_pages = sorted({p.get("page") for p in pages
                             if _page_no(p.get("page")) and (p.get("text") or "").strip()})
    if not page_states:
        for gap in degradations:
            if isinstance(gap.get("page_states"), list):
                page_states = [item for item in gap["page_states"]
                               if isinstance(item, dict) and _page_no(item.get("page"))]
                if page_states:
                    break
    if not page_state_history:
        for gap in degradations:
            if isinstance(gap.get("page_state_history"), list):
                page_state_history = [item for item in gap["page_state_history"]
                                      if isinstance(item, dict) and _page_no(item.get("page"))]
                if page_state_history:
                    break
    unread_pages = sorted(set(unread_pages) | refresh_pending)
    reviewed = reviewed_readable_pages(material)
    page_states = [{**item, "state": "suspect"} if item["page"] in refresh_pending - reviewed else item
                   for item in page_states]
    if reviewed:
        unread_pages = sorted(set(unread_pages) - reviewed)
        suspect_pages = sorted(set(suspect_pages) - reviewed)
        over_limit_pages = sorted(set(over_limit_pages) - reviewed)
        blank_pages = sorted(set(blank_pages) - reviewed)
        read_pages = sorted(set(read_pages) | reviewed)
        page_states = [{**item, "state": "reviewed"} if item["page"] in reviewed else item
                       for item in page_states]
    cached_suspect = _unreviewed_ocr_quality_pages(material)
    if cached_suspect:
        suspect_pages = sorted(set(suspect_pages) | cached_suspect)
        unread_pages = sorted(set(unread_pages) | cached_suspect)
        read_pages = sorted(set(read_pages) - cached_suspect)
        page_states = [{**item, "state": "suspect"} if item["page"] in cached_suspect else item
                       for item in page_states]
    read_pages = sorted(set(read_pages) - set(unread_pages) - set(suspect_pages))
    expected_pages = set(range(1, total_pages + 1)) if isinstance(total_pages, int) and total_pages > 0 else set()
    reading_complete = bool(expected_pages) and not unread_pages and not suspect_pages and not failed \
        and set(read_pages) == expected_pages
    result = {
        "total_pages": total_pages,
        "read_pages": read_pages,
        "unread_pages": unread_pages,
        "over_limit_pages": over_limit_pages,
        "blank_pages": blank_pages,
        "suspect_pages": suspect_pages,
        "degraded": bool(unread_pages or suspect_pages) or failed,
        "reading_complete": reading_complete,
    }
    # 历史/原生文本材料没有页级状态记录时保持旧响应形状；只在解析管线
    # 实际生成状态投影后才增加可选字段，避免无意义的空数组扩散到旧消费者。
    if page_states:
        result["page_states"] = page_states
    if page_state_history:
        result["page_state_history"] = page_state_history
    return result


def _page_is_blank(page) -> bool:
    """物理空白页：无正文、无图像、无矢量图形、无批注/链接——确认此处无可呈现内容。

    D10：空白页必须与「OCR 超限 / OCR 失败」**分开记账**。二者是不同的不可读：
    「有字但没读到」要人工翻看原件，「本来就没有字」翻看也是白翻。混成一类会让
    P1-09 承诺的「未读页范围」披露失真，还会为必然零产出的页花掉一次 OCR 预算。

    判据取**保守交集**：任一项非空即不判空白（可能是扫描图、水印、表单域或批注
    里的内容）——宁可多送一次 OCR，也不把「有内容但没读到」误报成「本来就没有」。
    判据本身异常时同样不判空白：判不了就不下结论。
    """
    try:
        if (page.get_text("text") or "").strip():
            return False
        if page.get_images(full=True):
            return False
        if page.get_drawings():
            return False
        if page.get_links():
            return False
        if next(iter(page.annots()), None) is not None:
            return False
    except Exception:
        return False  # 判不了就不判空白（保守）
    return True


def _body_text(text: str) -> str:
    """仅用于读取质量计数，原识别正文不改写；表格格式不能充当实质文字。"""
    text = re.sub(r"(?m)^[ \t]*```(?:html)?[ \t]*$", "", text, flags=re.I)
    body = re.sub(r"<[^>]*>", "", unescape(text))
    body = re.sub(
        r"\\begin\s*\{(?:tabular\*?|tabularx|longtable|array)\}"
        r"(?:\[[^\]]*\])?\s*\{(?:[^{}]|\{[^{}]*\})*\}", "", body)
    body = re.sub(r"\\(?:begin|end)\s*\{[^{}]*\}", "", body)
    body = re.sub(r"\\(?:multicolumn|multirow)\s*\{[^{}]*\}\s*\{[^{}]*\}", "", body)
    body = re.sub(r"\\(?:cline|cmidrule)(?:\([^)]*\))?\s*\{[^{}]*\}", "", body)
    body = re.sub(r"\\[a-zA-Z]+\*?", "", body)
    return "".join(char for char in body if char.isalnum())


def _body_char_count(text: str) -> int:
    return len(_body_text(text))


def _ocr_image_placeholder_fragment(text: str) -> bool:
    """只有图片占位和短标题时，不能将标记符当作证件正文。"""
    return bool(re.search(r"<img\b[^>]*>", text, re.I) and _body_char_count(text) < _MIN_TEXT_CHARS)


def _ocr_quality_suspect(text: str) -> bool:
    """提示 OCR 结构化残片或重复输出疑似吞正文，不作业务判定。"""
    if _ocr_image_placeholder_fragment(text):
        return True
    # 证书标题可识别不代表关键编号已读出；漏号时应补读或交人工核实。
    if ("公路水运工程" in text and "安全生产考核合格证书" in text
            and not re.search(r"交安\s*[ABCＡＢＣ]\s*[（(]?\s*\d", text, re.I)):
        return True
    # 名单表头与多条人员行列数不一致时，不能安全把姓名、岗位和证号按列关联。
    # 带合并单元格的表格需要完整结构重建，此处不以简单列数推断质量。
    for table in re.findall(r"<table\b[^>]*>.*?</table\s*>", text, flags=re.I | re.S):
        if re.search(r"\b(?:rowspan|colspan)\s*=", table, flags=re.I):
            continue
        rows = re.findall(r"<tr\b[^>]*>(.*?)</tr\s*>", table, flags=re.I | re.S)
        cells = [re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]\s*>", row, flags=re.I | re.S)
                 for row in rows]
        if not cells:
            continue
        header = [re.sub(r"<[^>]*>", "", cell).strip() for cell in cells[0]]
        if "序号" not in header or "姓名" not in header or not any("证" in cell for cell in header):
            continue
        numeric_rows = [row for row in cells[1:] if row and re.fullmatch(r"\s*\d+\s*", row[0])]
        if sum(len(row) != len(header) for row in numeric_rows) >= 2:
            return True
    # 实际证件扫描页曾返回字段名加坐标数组，却没有字段值；字段名和数字
    # 会通过普通字符计数，必须将这类结构化残片送入既有的逐页重试。
    coordinate_values = re.findall(r'[:：]\s*["\']?<\s*\d+(?:\s*,\s*\d+){2,}\s*\]', text)
    if len(coordinate_values) >= 2:
        return True
    # 实际扫描页可能只识别出“安全员：身份证、学历证……”这一行标题，
    # 图片中的证件内容全部遗漏。标题枚举不等于整页正文已读。
    body = _body_text(text)
    # 组合证件页可能只识别到页眉（如“身份证+司索工证”），即使文字长度
    # 超过基础门槛也没有读到任一证件字段。补读后仍是页眉则保持未读。
    if (len(body) < 45 and "身份证" in body
            and re.search(r"操作证|作业证|司索工证|电工证|焊工证|资格证", body)
            and not re.search(r"号码|编号|有效期|签发|出生", body)):
        return True
    # 真实注册证书页曾仅识别出国名和发证部门；这些字超过最低长度，
    # 但显然不足以支持证书字段核查，必须再补读一次。
    if (len(body) <= 30 and "中华人民共和国" in body and "住房和城乡建设部" in body
            and not any(field in body for field in ("证书", "编号", "姓名", "有效期"))):
        return True
    evidence_labels = ("身份证", "学历证", "职称证", "建造师证", "交安")
    if (len(body) < 45 and sum(label in body for label in evidence_labels) >= 3
            and any(role in body for role in ("安全员", "项目经理", "项目总工", "企业负责人"))):
        return True
    # 真实网络图曾仅返回短岗位表，页首标题和层级全部遗漏；完整名单表头不命中。
    roles = ("机械员", "材料员", "施工员", "资料员", "安全员", "劳务员", "标准员", "质量员")
    if (len(body) <= 100 and "|" in text and sum(role in body for role in roles) >= 5
            and not any(header in body for header in ("姓名", "岗位", "职务", "名单", "体系", "网络图"))):
        return True
    lines = [line.strip() for line in re.split(r"\r?\n|(?<=</tr>)", text, flags=re.I) if line.strip()]
    if not lines:
        return False
    empty_rows = sum(not _body_text(line) and bool(re.search(
        r"\||\\(?:begin|end|multicolumn|multirow|hline|cline)|</?(?:table|tr|td|th)\b", line, re.I))
        for line in lines)
    if empty_rows >= 20 and empty_rows * 2 > len(lines):
        return True
    line, repeats = Counter(lines).most_common(1)[0]
    return repeats >= 10 and repeats / len(lines) >= 0.8 and 0 < _body_char_count(line) <= 12


def _ocr_sparse_signature(text: str) -> bool:
    fields = {match.group() for match in re.finditer(r"法定代表人|委托代理人|授权代表|公章|签名", text)}
    return _body_char_count(text) < _SPARSE_SIGNATURE_BODY_CHARS and len(fields) >= 2


def _ocr_single_short_fragment(page: dict) -> bool:
    """扫描页只识别到一小行时，不能据此认定整页正文完整。"""
    meta = page.get("ocr_meta") or {}
    if not isinstance(meta, dict) or meta.get("source") not in {"cloud", "local"}:
        return False
    layout = meta.get("layout")
    return (isinstance(layout, list) and len(layout) == 1
            and isinstance(layout[0], dict) and 0 < _body_char_count(page.get("text") or "") < 20)


def _ocr_sideways_layout(page: dict) -> bool:
    """OCR 长横行若在原页坐标中竖直排列，阅读顺序不能视为可靠。"""
    meta = page.get("ocr_meta") or {}
    if not isinstance(meta, dict) or meta.get("source") not in {"local", "cloud"}:
        return False
    count = 0
    for block in meta.get("layout") or []:
        if not isinstance(block, dict) or _body_char_count(block.get("text") or "") < 10:
            continue
        box = block.get("bbox")
        if (not isinstance(box, (list, tuple)) or len(box) != 4
                or not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in box)):
            continue
        width, height = box[2] - box[0], box[3] - box[1]
        if width > 0 and height > 2 * width:
            count += 1
            if count >= 3:
                return True
    return False


def _ocr_parallel_certificate_columns(page: dict) -> bool:
    """证件重复表头位于并排两列时，扁平文本不能可靠对应各列字段。"""
    meta = page.get("ocr_meta") or {}
    if not isinstance(meta, dict) or meta.get("source") not in {"local", "cloud"}:
        return False
    labels = {}
    for entry in meta.get("layout") or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("text"), str):
            continue
        text, box = entry["text"].strip(), entry.get("bbox")
        if (len(text) < 5 or not re.search(r"证|作业|有效|发证", text)
                or not isinstance(box, (list, tuple)) or len(box) != 4
                or any(type(value) not in (int, float) for value in box)
                or box[2] <= box[0] or box[3] <= box[1]):
            continue
        labels.setdefault(text, []).append(box)
    duplicate_headers = 0
    for boxes in labels.values():
        if any((a[2] < b[0] or b[2] < a[0]) and
               min(a[3], b[3]) - max(a[1], b[1]) >= 0.5 * min(a[3] - a[1], b[3] - b[1])
               for i, a in enumerate(boxes) for b in boxes[i + 1:]):
            duplicate_headers += 1
            if duplicate_headers >= 2:
                return True
    return False


def _id_back_retry_candidates(pages: dict, readable: set, requested: list) -> dict:
    """只选择需要补读的扫描页，不据相邻关系认定证件持有人。"""
    ids = re.compile(r"(?<![0-9A-Za-z])\d{17}[0-9Xx](?![0-9A-Za-z])")
    result = {}
    for no in requested:
        if no not in readable or no - 1 not in readable or no not in pages or no - 1 not in pages:
            continue
        text, previous = pages[no].get("text") or "", pages[no - 1].get("text") or ""
        holders = re.findall(r"姓\s*名\s*[:：]?\s*([\u4e00-\u9fff]{2,4})(?=\s|[，,;；]|$)", previous)
        previous_ids = ids.findall(previous)
        if ("身份证" in text and "签发机关" in text and "有效期限" in text
                and not re.search(r"姓\s*名", text) and not ids.findall(text)
                and re.search(r"证书|合格证", previous)
                and not re.search(r"附件目录|报审表|填写示例|空白模板", previous[:200])
                and len(holders) == 1 and len(previous_ids) == 1):
            result[no] = previous_ids[0].upper()
    return result


def _id_back_retry_preserves(original: str, retry: str, expected_id: str) -> bool:
    numbers = re.findall(r"(?<![0-9A-Za-z])\d{17}[0-9Xx](?![0-9A-Za-z])", retry)
    dates = lambda text: {tuple(map(int, match)) for match in re.findall(
        r"(\d{4})[-./年](\d{1,2})[-./月](\d{1,2})日?", text)}
    retry_compact = re.sub(r"\s+", "", retry)
    issuer_lines = [re.sub(r"\s+", "", line) for line in original.splitlines() if "签发机关" in line]
    return (len(numbers) == 1 and numbers[0].upper() == expected_id
            and all(marker not in original or marker in retry for marker in ("身份证", "签发机关", "有效期限"))
            and all(line in retry_compact for line in issuer_lines)
            and dates(original).issubset(dates(retry)))


def parse_material_pages(abs_path: str, *, progress=None) -> tuple:
    """正式摄取：保留原生正文，仅补读可能缺正文的物理页。

    页覆盖只说明是否取得足够文字，不代表内容、签章或证件已经核实。
    默认读取全部原始页；只有显式设置 `BS_OCR_MAX_PAGES` 时才限制原始前 N 页。
    类型识别独立使用有界 ocr_text_pages。

    D10 分开记账：物理空白页（`_page_is_blank`）不送 OCR、不记为「OCR 失败」，
    单独进 `parse_incomplete.ocr_blank_pages` 与单独的说明句；它**仍然留在
    `unread_pages` 里**——「确认空白」是读取结论，不是已读到内容，不因此放宽
    任何判定（A3-01 三态、材料缺口一律不变）。
    """
    import fitz  # 重量级 PDF 读取仅在摄取时加载。

    if progress is None:
        pages, degradations = extract_text_pages(abs_path)
    else:
        pages, degradations = extract_text_pages(abs_path, progress=progress)
    blank = []
    with fitz.open(abs_path) as doc:
        total = doc.page_count
        # 低层提取器在全册不足门槛时清空结果；仍须保留原件已有的短文字。
        if not pages:
            pages = [{"page": i + 1, "text": text}
                     for i, page in enumerate(doc)
                     if (text := (page.get_text("text") or "").strip())]
        native = {p["page"]: p for p in pages}
        readable = {no for no, p in native.items()
                    if _body_char_count(p["text"]) >= _MIN_TEXT_CHARS}
        # D10：物理空白页单独识别（判据与理由见 `_page_is_blank`）。只判「原生正文
        # 不足」的页，且必须在 doc 作用域内判完，避免为判空白再开一次文件。
        blank = sorted(no for no in set(range(1, total + 1)) - readable
                       if _page_is_blank(doc[no - 1]))
    missing = set(range(1, total + 1)) - readable
    # 空白页不进 OCR 请求：必然零产出，不占页数预算（D10）。
    page_limit = _ocr_max_pages()
    requested = sorted(
        no for no in missing - set(blank)
        if page_limit is None or no <= page_limit)
    if requested:
        if progress is None:
            recovered, info = ocr_text_pages(abs_path, page_nos=requested)
        else:
            recovered, info = ocr_text_pages(abs_path, page_nos=requested, progress=progress)
    else:
        recovered, info = [], None
    suspect = set()
    sparse_signatures = set()
    for page in recovered:
        no = page["page"]
        if no in requested:
            if no not in native or _body_char_count(page["text"]) > _body_char_count(native[no]["text"]):
                native[no] = page
            if _ocr_sparse_signature(page["text"]):
                if isinstance(page.get("ocr_meta"), dict):
                    page["ocr_meta"]["quality"] = "ocr_suspect"
                sparse_signatures.add(no)
                suspect.add(no)
            elif (_ocr_quality_suspect(page["text"]) or _ocr_sideways_layout(page)
                  or _ocr_single_short_fragment(page) or _ocr_parallel_certificate_columns(page)):
                if isinstance(page.get("ocr_meta"), dict):
                    page["ocr_meta"]["quality"] = "ocr_suspect"
                suspect.add(no)
            elif _body_char_count(page["text"]) >= _MIN_TEXT_CHARS:
                readable.add(no)
            else:
                # 非空返回不代表读到了正文：短残片同样需要一次有界重试。
                if isinstance(page.get("ocr_meta"), dict):
                    page["ocr_meta"]["quality"] = "ocr_suspect"
                suspect.add(no)

    # P8-01：重复空表、旋转表格和签署页容易命中缓存中的低质量结果。只对本轮
    # 明确标为 suspect 的页绕过缓存重试一次；重试仍可疑则保留人工核验，不把
    # 「模型返回了文字」误当成「正文已可靠读取」。
    field_candidates = _id_back_retry_candidates(native, readable - suspect, requested)
    retry_pages = sorted(suspect | set(field_candidates))
    retry_recovered = []
    field_retry_recovered = []
    retry_info = None
    sideways_retry_pages = []
    rotation_recovered = []
    rotated_info = None
    if retry_pages:
        retry_kwargs = {"page_nos": retry_pages, "use_cache": False, "use_cloud": True}
        if progress is not None:
            progress(0, total, "retry", len(retry_pages))
            def retry_progress(page, page_total, source, work_total):
                progress(page, page_total, "retry_failed" if source == "failed" else "retry", work_total)
            retry_kwargs["progress"] = retry_progress
        retried, retry_info = ocr_text_pages(abs_path, **retry_kwargs)
        for page in retried:
            no = page["page"]
            if no not in retry_pages:
                continue
            body_count = _body_char_count(page["text"])
            sparse = _ocr_sparse_signature(page["text"])
            repeated = (_ocr_quality_suspect(page["text"]) or _ocr_sideways_layout(page)
                        or _ocr_single_short_fragment(page) or _ocr_parallel_certificate_columns(page))
            if no in field_candidates and not _id_back_retry_preserves(
                    native[no].get("text") or "", page["text"], field_candidates[no]):
                continue
            if body_count >= _MIN_TEXT_CHARS and not sparse and not repeated:
                if isinstance(page.get("ocr_meta"), dict):
                    page["ocr_meta"]["quality"] = "ocr_ok"
                    page["ocr_meta"]["retry_count"] = 1
                native[no] = page
                readable.add(no)
                suspect.discard(no)
                sparse_signatures.discard(no)
                retry_recovered.append(no)
                if no in field_candidates:
                    field_retry_recovered.append(no)
            elif sparse:
                sparse_signatures.add(no)
            elif _ocr_sideways_layout(page):
                sideways_retry_pages.append(no)
    if sideways_retry_pages:
        if progress is not None:
            progress(0, total, "rotation", len(set(sideways_retry_pages)))
        rotated, rotated_info = ocr_rotated_text_pages(abs_path, sorted(set(sideways_retry_pages)))
        for page in rotated:
            no, text = page.get("page"), page.get("text") or ""
            original = native.get(no)
            if no in sideways_retry_pages and isinstance(original, dict):
                native[no] = _merge_rotation_roster_clues(original, page)
            # 方向无法单凭框的长宽确定，不能因任意旋转后字符变多就认定成功。
            # 仅接受能独立读到证书标题、姓名和完整交安编号的明确证件页。
            if (no not in sideways_retry_pages or _ocr_quality_suspect(text)
                    or _body_char_count(text) < _MIN_TEXT_CHARS
                    or text.count("安全生产考核合格证书") != 1
                    or len(re.findall(r"姓\s*名\s*[:：]?\s*[\u4e00-\u9fa5]{2,4}", text)) != 1
                    or len(re.findall(r"交安\s*[ABC]\s*\d{2}G\d{5}(?!\d)", text, re.I)) != 1
                    or len(re.findall(r"\d{4}[-./年]\d{1,2}[-./月]\d{1,2}日?", text)) < 2):
                continue
            native[no] = page
            readable.add(no)
            suspect.discard(no)
            retry_recovered.append(no)
            rotation_recovered.append(no)
    unread = sorted(set(range(1, total + 1)) - readable)
    degradations = [d for d in degradations if d.get("kind") != "no_text_layer"]
    recovered_count = len(readable.intersection(requested))
    recovered_pages = {page.get("page") for page in recovered
                       if isinstance(page, dict) and isinstance(page.get("page"), int)}
    page_states = []
    for page_no in range(1, total + 1):
        if page_no in blank:
            state = "blank"
        elif page_limit is not None and page_no > page_limit and page_no in missing:
            state = "over_limit"
        elif page_no in suspect:
            state = "suspect"
        elif page_no in readable:
            state = "ocr_ok" if page_no in recovered_pages else "native_text"
        elif page_no in requested:
            state = "failed"
        else:
            state = "unread"
        page_states.append({"page": page_no, "state": state})
    # Q4：保留页级状态转移历史；旧材料没有该字段时保持旧响应形状。
    page_state_history = []
    for page_no in range(1, total + 1):
        final_state = next(item["state"] for item in page_states if item["page"] == page_no)
        transitions = []
        if final_state == "native_text":
            transitions.extend([
                {"state": "terminal", "attempt": 0},
                {"state": "native_text", "attempt": 0},
            ])
        elif final_state == "blank":
            transitions.extend([
                {"state": "terminal", "attempt": 0},
                {"state": "blank", "attempt": 0},
            ])
        elif final_state == "over_limit":
            transitions.extend([
                {"state": "queued", "attempt": 0},
                {"state": "terminal", "attempt": 0},
                {"state": "over_limit", "attempt": 0},
            ])
        elif page_no in requested:
            transitions.extend([
                {"state": "queued", "attempt": 0},
                {"state": "processing", "attempt": 0},
            ])
            if page_no in retry_pages:
                transitions.append({"state": "suspect", "attempt": 0})
                transitions.append({"state": "retrying", "attempt": 1})
                final_attempt = 1
                if page_no in sideways_retry_pages:
                    transitions.append({"state": "suspect", "attempt": 1})
                    transitions.append({"state": "retrying", "attempt": 2})
                    final_attempt = 2
                transitions.append({"state": "terminal", "attempt": final_attempt})
                transitions.append({"state": final_state, "attempt": final_attempt})
            else:
                transitions.append({"state": "terminal", "attempt": 0})
                transitions.append({"state": final_state, "attempt": 0})
        else:
            transitions.extend([
                {"state": "terminal", "attempt": 0},
                {"state": final_state, "attempt": 0},
            ])
        page_state_history.append({"page": page_no, "transitions": transitions})
    if recovered_count:
        degradations.append({
            "kind": "ocr_fallback", "coverage_recorded": True,
            "message": "扫描页已通过文字识别读取，识别结果可能有误差，关键结论请以原件为准",
            "total_pages": total, "recovered_pages": recovered_count,
            "engine": (info or {}).get("engine"),
            "cache_key": (info or {}).get("cache_key"),
            "layout_pages": (info or {}).get("layout_pages", 0),
            "page_states": page_states,
            "page_state_history": page_state_history,
            "ocr_field_retry_pages": sorted(field_candidates),
            "ocr_field_retry_recovered_pages": sorted(field_retry_recovered),
            "rotation_retry_pages": sorted(set(sideways_retry_pages)),
            "rotation_retry_recovered_pages": sorted(rotation_recovered),
            "rotation_retry_engine": (rotated_info or {}).get("engine"),
        })
    elif not readable and not suspect:
        degradations.append({
            "kind": "no_text_layer",
            "message": "未取得可读正文，涉及内容的核查需人工翻看原件",
        })
    if unread:
        # 超上限必须按**预算边界**算，不能写成 `missing - set(requested)`：空白页已从
        # requested 剔除，那样会把空白页误记成「超出识别页数上限」（D10 分开记账）。
        skipped = sorted(
            no for no in missing - set(blank)
            if page_limit is not None and no > page_limit)
        failed = sorted(set(requested) - readable - suspect)
        notes = []
        repeated = suspect - sparse_signatures
        if repeated:
            notes.append("第%s页文字识别不完整或排列异常，可能漏正文，需翻看原件或补传清晰材料后重核"
                         % "、".join(map(str, sorted(repeated))))
        if sparse_signatures:
            notes.append("第%s页仅取得较少签名栏等文字，正文是否完整需人工核对原件；不据此判签章或授权缺失"
                         % "、".join(map(str, sorted(sparse_signatures))))
        other_unread = sorted(set(unread) - suspect - set(blank))
        if other_unread:
            notes.append("第%s页未取得可读正文，需人工翻看原件或补传清晰材料后重核"
                         % "、".join(map(str, other_unread)))
        if blank:
            # 空白页单独一句：明确「无需人工翻看」，避免把复核人力指向一张空页；
            # 同时留出「原件此处应有内容」的补传出口（判据是保守交集，不排除误判）。
            notes.append("第%s页为空白页（无正文、无图像、无图形），无需人工翻看；"
                         "若原件此处应有内容，请补传清晰材料后重核"
                         % "、".join(map(str, blank)))
        note = "；".join(notes)
        if skipped:
            note += "；第%s页超出本次文字识别页数上限" % "、".join(map(str, skipped))
        degradations.append({
            "kind": "parse_incomplete", "message": note, "total_pages": total,
            "read_pages": sorted(readable), "unread_pages": unread,
            "ocr_requested_pages": requested, "ocr_failed_pages": failed,
            "ocr_skipped_pages": skipped, "ocr_suspect_pages": sorted(suspect),
            "ocr_sparse_signature_pages": sorted(sparse_signatures),
            "ocr_blank_pages": blank,
            "ocr_retry_pages": retry_pages,
            "ocr_retry_recovered_pages": sorted(retry_recovered),
            "ocr_retry_engine": (retry_info or {}).get("engine"),
            "rotation_retry_pages": sorted(set(sideways_retry_pages)),
            "rotation_retry_recovered_pages": sorted(rotation_recovered),
            "rotation_retry_engine": (rotated_info or {}).get("engine"),
            "page_states": page_states,
            "page_state_history": page_state_history,
        })
    return [native[no] for no in sorted(native)], degradations
