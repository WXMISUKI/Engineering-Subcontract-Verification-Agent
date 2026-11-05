# Source: startup_conditions_check.ingestion; standalone seal functions only.
import logging
import cv2
import numpy as np
logger = logging.getLogger(__name__)

def _page_is_grayscale(img) -> bool:
    """低饱和判定：三通道平均绝对差 < 25（0-255）视为灰度/黑白页。

    黑章只在灰度页判定：彩色页（含红章页）启用黑通道会大量命中黑色文字，
    淹没真实黑章；灰度扫描/黑白复印件才需要黑通道补齐。
    """
    if img is None or getattr(img, "ndim", 0) != 3:
        return True
    b, g, r = cv2.split(img)
    spread = np.mean(
        np.abs(r.astype(int) - g.astype(int))
        + np.abs(g.astype(int) - b.astype(int))
        + np.abs(b.astype(int) - r.astype(int))
    )
    return spread < 25.0

def _seal_candidate_rect(cnt, w: int, h: int, min_d: float, *, require_circularity: bool = False) -> list:
    """轮廓 → 候选章矩形（与 _local_seal_detect 原有过筛一致：面积/边长/宽高比/占页比/单边长）。

    require_circularity=True（黑通道）：圆形度 4πA/周长² ≥ 0.25 才放行——
    黑字长条/表格线（低圆形度）据此剔除，黑章环（近圆）保留。
    返回矩形列表（0 或 1），保持声明式便于测试。
    """
    if cv2 is None or np is None:
        return []
    area = cv2.contourArea(cnt)
    if area < min_d * min_d * 0.5:
        return []
    x, y, cw, ch = cv2.boundingRect(cnt)
    if cw < min_d or ch < min_d:
        return []
    ratio = max(cw, ch) / max(min(cw, ch), 1)
    if ratio > 3.0:
        return []
    if (cw * ch) / (w * h) > 0.25:
        return []
    if cw > 0.55 * w or ch > 0.55 * h:
        return []
    if require_circularity:
        peri = cv2.arcLength(cnt, True)
        if peri <= 0:
            return []
        circularity = 4.0 * np.pi * area / (peri * peri)
        if circularity < 0.25:
            return []
    return [(x, y, x + cw, y + ch)]

def _point_in_any_rect(x: float, y: float, rects: list) -> bool:
    """点是否落入任一矩形（≤ 阈值即重叠，用于剔除与文本/表格线重叠的黑块）。"""
    for x0, y0, x1, y1 in rects or []:
        if x0 <= x <= x1 and y0 <= y <= y1:
            return True
    return False

def _scan_text_rects(page, dpi: int = 150) -> list:
    """取页面文本层 word 矩形（PDF 点 → dpi 渲染像素坐标），供黑通道排除文字重叠。

    仅电子版（有文本层）可排除；纯扫描件 get_text 为空 → 靠圆形度过滤兜底。
    """
    scale = dpi / 72.0
    rects: list = []
    try:
        for word in page.get_text("words") or []:
            x0, y0, x1, y1 = word[:4]
            rects.append((x0 * scale, y0 * scale, x1 * scale, y1 * scale))
    except Exception:
        pass
    return rects

def _local_seal_detect(abs_path: str) -> list[dict]:
    """本地判章（OpenCV）：红通道（原逻辑）+ 黑白低饱和通道（T3-P1），recall 优先。

    零新增模型依赖（cv2 / PIL / fitz 均已就绪）。输出与远程 seal 块同构：
    {page_idx(1-based), bbox, page_width, page_height, polygon}，可直接被
    classify_seal_blocks / _seal_ocr_section / 前端徽标复用，不改判定与展示链路。

    通道策略：
    - 红通道：保留原过滤（面积/宽高比≤3/占页比≤25%/单边≤55%页），recall 优先；
    - 黑白通道：仅灰度页启用 → 灰阶<80 掩膜 → 先开后闭 → 圆形度≥0.25 → 剔除与
      文本层 word 重叠区域（黑字是主要误报源）；红/黑同物理章按交并比去重。

    护栏：recall 优先——保守阈值、宁可多报（红头/黑块等可能误报），
    多报由人工复核 + G16 VLM 视觉审计双通道印证，不判缺也不误放行；
    内部任何异常返回 []，不阻断主 OCR 流程。
    """
    if cv2 is None or np is None:
        logger.info("[OC_OCR] 本地判章依赖缺失（cv2/numpy），跳过")
        return []
    try:
        import fitz
    except ImportError:
        logger.info("[OC_OCR] 本地判章依赖缺失（fitz），跳过")
        return []
    try:
        doc = fitz.open(abs_path)
    except Exception:
        return []
    seals: list[dict] = []
    try:
        for pno in range(doc.page_count):
            try:
                page = doc.load_page(pno)
                pix = page.get_pixmap(dpi=150)
                nparr = np.frombuffer(pix.tobytes("png"), dtype=np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            except Exception:
                continue
            if img is None:
                continue
            h, w = img.shape[:2]
            min_d = 0.08 * w
            candidates: list = []
            # 红通道（保留原行为：recall 优先，不做圆形度过滤）
            blue, green, red = cv2.split(img)
            red_mask = (((red > 100) & (red - green > 40) & (red - blue > 40)) * 255).astype("uint8")
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
            closed = cv2.morphologyEx(red_mask, cv2.MORPH_CLOSE, kernel)
            contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for cnt in contours:
                candidates.extend(_seal_candidate_rect(cnt, w, h, min_d, require_circularity=False))
            # HSV 饱和通道（S7，2026-08-27）：RGB 红通道在偏暗/浅色/底色干扰下失效时
            # （如测量.pdf 第1页红章漏检），改以色相+饱和判定章色，recall 优先补充召回。
            try:
                hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
                h_ch, s_ch, _v_ch = cv2.split(hsv)
                hsv_mask = (((h_ch < 12) | (h_ch > 165)) & (s_ch > 70) * 255).astype("uint8")
                closed_hsv = cv2.morphologyEx(hsv_mask, cv2.MORPH_CLOSE, kernel)
                contours_hsv, _ = cv2.findContours(closed_hsv, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                for cnt in contours_hsv:
                    candidates.extend(_seal_candidate_rect(cnt, w, h, min_d, require_circularity=False))
            except Exception:  # noqa: BLE001 —— HSV 通道失败不影响红/黑通道
                pass
            # 黑白低饱和通道（T3-P1）：仅灰度页启用
            if _page_is_grayscale(img):
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                black_mask = ((gray < 80) * 255).astype("uint8")
                opened = cv2.morphologyEx(black_mask, cv2.MORPH_OPEN, kernel)
                closed_black = cv2.morphologyEx(opened, cv2.MORPH_CLOSE, kernel)
                text_rects = _scan_text_rects(page, dpi=150)
                contours_black, _ = cv2.findContours(closed_black, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                for cnt in contours_black:
                    for x1, y1, x2, y2 in _seal_candidate_rect(cnt, w, h, min_d, require_circularity=True):
                        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
                        if _point_in_any_rect(cx, cy, text_rects):
                            continue
                        candidates.append((x1, y1, x2, y2))
            # 红/黑同物理章去重（IoU≥0.4 保留先检），随后整页截断 6 个
            dedup: list = []
            for cand in candidates:
                overlap = False
                for dx1, dy1, dx2, dy2 in dedup:
                    ix1, iy1 = max(cand[0], dx1), max(cand[1], dy1)
                    ix2, iy2 = min(cand[2], dx2), min(cand[3], dy2)
                    inter = max(ix2 - ix1, 0) * max(iy2 - iy1, 0)
                    if inter >= 0.4 * max((cand[2] - cand[0]) * (cand[3] - cand[1]), 1):
                        overlap = True
                        break
                if not overlap:
                    dedup.append(cand)
            page_hits: list[dict] = []
            for x, y, x2, y2 in dedup[:6]:
                page_hits.append({
                    "page_idx": pno + 1,
                    "bbox": [int(x), int(y), int(x2), int(y2)],
                    "page_width": w,
                    "page_height": h,
                    "polygon": None,
                })
            seals.extend(page_hits)
        if seals:
            logger.info("[OC_OCR] 本地判章命中 %d 处（%s）", len(seals), abs_path)
    except Exception as exc:
        logger.warning("[OC_OCR] 本地判章失败（不阻断）: %s: %s", abs_path, exc)
    finally:
        try:
            doc.close()
        except Exception:
            pass
    return seals
