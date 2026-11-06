"""依赖文件云端 OCR:专属 MaaS 工作空间的 OCR 模型(DashScope 原生多模态协议)。

模型名取 config.OCR_CLOUD_MODEL(唯一定义点);2026-10-14 由已下线的 qwen-vl-ocr-latest
改为 qwen3.5-ocr —— 前者已在工作空间模型清单外,云端每次 404 Model not exist ⇒ 静默降级本地;
同名旧代 qwen-vl-ocr 可调但单页 80s 且 finish_reason=length(输出截断、字序错乱)。

零号门(ocr_cloud_probe.py)实测结论:
- 端点 https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation
- body 带 parameters.ocr_options={"task":"advanced_recognition"} → 返回结构化
  output.choices[0].message.content[].ocr_result.words_info[].{text, rotate_rect}
- rotate_rect=[center_x,center_y,width,height,angle],坐标系=输入图像像素系(已用文本层对照验证,
  与渲染像素精确一致,换算回 PDF 点 ×72/dpi)
- content 可能混入 text 项(模型旁白,可能是 markdown-JSON 围栏),只消费 ocr_result
- usage 在响应顶层 {input_tokens, output_tokens, total_tokens}
"""
import asyncio, base64, json, logging, math
from typing import Awaitable, Callable, Optional


logger = logging.getLogger(__name__)


def cloud_endpoint_from_base(llm_api_base: str) -> str:
    """OpenAI 兼容 base → DashScope 原生多模态端点(同 host)。空/无法推导返回空串。"""
    if not llm_api_base:
        return ""
    host = llm_api_base.split("/compatible-mode")[0]
    return host + "/api/v1/services/aigc/multimodal-generation/generation" if host else ""


def rotate_rect_to_bbox(rotate_rect) -> list:
    """rotate_rect [cx, cy, w, h, angle] → 轴对齐外接 bbox [x0, y0, x1, y1]。
    零号门对照显示 angle 语义不稳(水平文字也报 90°),但四角外接框总能覆盖文字,取外接即可。"""
    if not rotate_rect or len(rotate_rect) < 5:
        return None
    cx, cy, w, h, angle_deg = (float(v) for v in rotate_rect[:5])
    hw, hh = w / 2.0, h / 2.0
    ang = math.radians(angle_deg)
    cos_a, sin_a = math.cos(ang), math.sin(ang)
    corners = [(cx + dx * cos_a - dy * sin_a, cy + dx * sin_a + dy * cos_a)
               for dx, dy in ((hw, hh), (hw, -hh), (-hw, -hh), (-hw, hh))]
    xs = [p[0] for p in corners]
    ys = [p[1] for p in corners]
    return [min(xs), min(ys), max(xs), max(ys)]


def _parse_processed_text(processed_text: str) -> list[dict]:
    """回退通道：解析 processed_text 的 markdown-JSON 围栏。

    实测（2026-09-10 D60/D64 导入）：部分页 words_info 为空，模型改以
    processed_text 返回 ```json [ {"rotate_rect": [...], "text": "..."} ] ```。
    与 words_info 同构，逐条映射 rotate_rect → bbox；解析失败返回 []（调用方
    按页失败处理，不静默丢内容）。
    """
    text = str(processed_text or "").strip()
    if not text:
        return []
    # 去 markdown 围栏（```json ... ``` / ``` ... ```）
    if text.startswith("```"):
        lines = text.splitlines()
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()
    if not text.startswith("["):
        return []
    try:
        entries = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(entries, list):
        return []
    out = []
    for w in entries:
        if not isinstance(w, dict):
            continue
        t = str(w.get("text", "") or "").strip()
        if not t:
            continue
        out.append({"text": t, "bbox": rotate_rect_to_bbox(w.get("rotate_rect"))})
    return out


def _extract_words(content_items) -> list[dict]:
    """从原生响应的 content items 提取 OCR 词：优先 words_info，空则回退 processed_text。
    返回 [{text, bbox}]。"""
    out = []
    for item in content_items or []:
        if not isinstance(item, dict):
            continue
        ocr = item.get("ocr_result")
        if not isinstance(ocr, dict):
            continue
        words_info = ocr.get("words_info")
        for w in words_info if isinstance(words_info, list) else []:
            if not isinstance(w, dict):
                continue
            text = str(w.get("text", "") or "").strip()
            if not text:
                continue
            bbox = rotate_rect_to_bbox(w.get("rotate_rect"))
            out.append({"text": text, "bbox": bbox})
        if not out:
            out.extend(_parse_processed_text(ocr.get("processed_text")))
    if not out:
        for item in content_items or []:
            if isinstance(item, dict):
                out.extend(_parse_processed_text(item.get("text")))
    return out


# 结构化转写提示词（2026-10-19 实测引入）。
#
# 背景：规范正文里的公式是二维排版，词级 `words_info` 只给「字形 + 矩形」，
# 调用方再按 y/x 排序拼成一维字符串 ⇒ 分式、根式、上下标必然被压平。
# 实测同一页（SC01 物理 p49）两种请求体的差异：
#   - 不加提示词：processed_text 有时给正确 LaTeX（p49），有时给词列表
#     （p60 给了 4.2 万字符的 `h/h4/h/h4...` 图形标注噪声）；
#   - 加提示词：稳定返回整页 markdown，条号（5.1.7、6.3.1）、
#     `P-\Delta`、`80\varepsilon_k`、公式编号均正确保留。
# 提示词只约束「公式必须 LaTeX」和「不确定不要猜」，不要求改写或总结正文。
STRUCTURED_OCR_PROMPT = (
    "请把这一页规范正文完整转写为 Markdown，不要总结、不要改写条款文字。要求：\n"
    "1. 所有数学公式（含行内符号、上下标、根式、分式、希腊字母）一律用 LaTeX 输出，"
    "行间公式用 $$...$$，行内用 $...$。\n"
    "2. 保留公式右端的编号，例如 (6.2.7-3)，编号写在公式同一行末尾的 $$ 之外。\n"
    "3. 无法确定的符号不要猜，写成 \\text{?} 并在该行末尾标注 [不确定]。\n"
    "4. 条款号（如 6.3.1）、图号、表号按原文保留。"
)


def _request_body(model: str, png_b64: str, prompt: Optional[str]) -> dict:
    content: list[dict] = [{"image": f"data:image/png;base64,{png_b64}"}]
    if prompt:
        content.append({"text": prompt})
    return {
        "model": model,
        "input": {"messages": [{"role": "user", "content": content}]},
        "parameters": {"ocr_options": {"task": "advanced_recognition"}},
    }


async def _post_ocr(png_bytes: bytes, *, model: str, endpoint: str, api_key: str,
                    prompt: Optional[str] = None,
                    on_usage: Optional[Callable[[dict], None]] = None,
                    subject=None, billing_model_code: Optional[str] = None) -> list:
    """发一次 OCR 请求，返回 content items。HTTP 层不自持 client，调用方管理。

    独立应用保留原请求协议和用量回调，不依赖平台计费服务。
    """
    import httpx

    try:
        b64 = base64.b64encode(png_bytes).decode("ascii")
        body = _request_body(model, b64, prompt)
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=180.0) as client:
            r = await client.post(endpoint, json=body, headers=headers)
            # Only rejected, transient requests are retried; a successfully read
            # but poor-quality page must go directly to human review.
            if r.status_code == 429 or 500 <= r.status_code < 600:
                import asyncio
                import random
                await asyncio.sleep(1.0 + random.uniform(0, 0.5))
                r = await client.post(endpoint, json=body, headers=headers)
        if r.status_code != 200:
            from app.plus.request_errors import ModelHTTPError
            raise ModelHTTPError(r.status_code)
        data = r.json()
        if on_usage is not None and isinstance(data.get("usage"), dict):
            u = data["usage"]
            on_usage({"prompt_tokens": u.get("input_tokens", 0) or 0,
                      "completion_tokens": u.get("output_tokens", 0) or 0,
                      "total_tokens": u.get("total_tokens", 0) or 0})
        try:
            choice = data["output"]["choices"][0]["message"]
            content = choice.get("content") or []
        except (KeyError, IndexError, TypeError):
            raise RuntimeError("云端OCR返回结构异常")
    except Exception:
        raise
    return content


def _collapse_repeated_page(text: str) -> str:
    """模型偶尔把整页输出两遍（实测 SC01 p49 加提示词后出现）。

    只在「去空行后前后两半逐行完全相同」时截断，避免误伤真正重复的条款。
    """
    lines = text.splitlines()
    if len(lines) < 8:
        return text
    half = len(lines) // 2
    head = [ln.strip() for ln in lines[:half] if ln.strip()]
    tail = [ln.strip() for ln in lines[half:] if ln.strip()]
    if head and head == tail:
        return "\n".join(lines[:half]).strip()
    return text


def _extract_markdown(content_items) -> str:
    """取模型的结构化转写文本（markdown），不取词列表。

    优先级：content 里的 `text` 项 → `ocr_result.processed_text`（且不是 JSON 词列表）。
    只取第一个非空来源，不合并：同一页两个来源是同一内容的两种表示，
    合并会得到重复整页（历史缺陷）。
    """
    for item in content_items or []:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip() and not _parse_processed_text(text):
            return _collapse_repeated_page(text.strip())
    for item in content_items or []:
        if not isinstance(item, dict):
            continue
        ocr = item.get("ocr_result")
        if not isinstance(ocr, dict):
            continue
        text = ocr.get("processed_text")
        if not isinstance(text, str):
            continue
        stripped = text.strip()
        # `[` 开头是 words_info 缺失时的词列表围栏，不是 markdown 正文。
        if stripped and not stripped.lstrip("`").lstrip().startswith(("[", "json")):
            return _collapse_repeated_page(stripped)
    return ""


async def cloud_ocr_structured(png_bytes: bytes, *, model: str, endpoint: str, api_key: str,
                               subject=None,
                               prompt: Optional[str] = STRUCTURED_OCR_PROMPT,
                               on_usage: Optional[Callable[[dict], None]] = None,
                               billing_model_code: Optional[str] = None) -> dict:
    """单图结构化 OCR：同时返回 markdown 正文与词级 bbox。

    返回 `{"markdown": str, "words": [{text, bbox}]}`。markdown 可能为空
    （模型只给词列表时），调用方需回退 `words_to_text`，不得把空串当成功。
    """
    content = await _post_ocr(png_bytes, model=model, endpoint=endpoint, api_key=api_key,
                              prompt=prompt, on_usage=on_usage, subject=subject,
                              billing_model_code=billing_model_code)
    words = _extract_words(content)
    markdown = _extract_markdown(content)
    if not words and not markdown:
        raise RuntimeError("云端OCR未识别到文字")
    return {"markdown": markdown, "words": words}


async def cloud_ocr_image(png_bytes: bytes, *, model: str, endpoint: str, api_key: str,
                          subject=None,
                          prompt: Optional[str] = None,
                          on_usage: Optional[Callable[[dict], None]] = None,
                          billing_model_code: Optional[str] = None) -> list[dict]:
    """单图云端 OCR,返回 [{text, bbox}]。HTTP 层调用 httpx(不在本模块持有 client,调用方管理)。

    `prompt` 仅用于需要结构化正文的调用方；本函数仍只返回词级结果，
    公式页请改用 `cloud_ocr_structured`（词级结果无法承载二维公式）。
    """
    content = await _post_ocr(png_bytes, model=model, endpoint=endpoint, api_key=api_key,
                              prompt=prompt, on_usage=on_usage, subject=subject,
                              billing_model_code=billing_model_code)
    words = _extract_words(content)
    if not words:
        raise RuntimeError("云端OCR未识别到文字")
    return words


async def cloud_ocr_page_concurrent(pdf_path: str, page_nos: list[int], *,
                                    model: str, endpoint: str, api_key: str,
                                    dpi: int = 150, concurrency: int = 4,
                                    on_page: Optional[Callable[[int, list[dict]], None]] = None,
                                    on_usage: Optional[Callable[[dict], None]] = None,
                                    subject=None,
                                    billing_model_code: Optional[str] = None) -> dict:
    """多页并发云端 OCR:page_no(0-based) → 渲染 PNG → words。

    返回 {page_no: [{text, bbox}]};单页失败抛 RuntimeError(原子性由调用方决策:整文件失败→转本地)。"""
    import pymupdf as fitz

    doc = fitz.open(pdf_path)
    try:
        _pngs = {}
        for pno in page_nos:
            pix = doc.load_page(pno).get_pixmap(dpi=dpi)
            _pngs[pno] = pix.tobytes("png")
    finally:
        doc.close()

    sem = asyncio.Semaphore(concurrency)
    results: dict[int, list[dict]] = {}
    first_err: Optional[Exception] = None

    async def one(pno: int):
        nonlocal first_err
        async with sem:
            words = await cloud_ocr_image(_pngs[pno], model=model, endpoint=endpoint,
                                          api_key=api_key, on_usage=on_usage,
                                          subject=subject,
                                          billing_model_code=billing_model_code)
        results[pno] = words
        if on_page is not None:
            on_page(pno, words)

    tasks = [asyncio.create_task(one(p)) for p in page_nos]
    for t in asyncio.as_completed(tasks):
        try:
            await t
        except Exception as e:
            if first_err is None:
                first_err = e
    if first_err is not None:
        raise first_err
    return results


# ── 配置读取 ────────────────────────────────────────────────────────
def cloud_config() -> dict:
    """从独立应用环境变量读模型、端点与密钥；缺一项则不可用。"""
    import os
    model = os.environ.get("OCR_CLOUD_MODEL", "").strip()
    endpoint = os.environ.get("OCR_CLOUD_ENDPOINT", "").strip()
    api_key = os.environ.get("OCR_CLOUD_API_KEY", "").strip()
    if not all((model, endpoint, api_key)):
        return {}
    return {"model": model, "endpoint": endpoint, "api_key": api_key, "enabled": True}
