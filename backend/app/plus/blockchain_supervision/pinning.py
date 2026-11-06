"""证据三档定位（W2 收口：点结论 → 定位原文页码）。

对已构造的 BsFinding 在落库前做系统定位钉扎：引文在材料文本层里
归一化复核，回填真实页码与三档定位模式（对齐 structural_check P13
「宁缺勿错、不编造页码」纪律）：

  - precise  ：引文归一化后在材料某页文本层命中 → 页码回填为命中页；
  - page     ：引文复核未命中，但结论原有页码存在且材料有文本层
               （页码由引擎匹配时算得，可信）→ 降级为页级定位；
  - unresolved：验不中且无可信页码 / 材料无文本层 → 清空页码，不给幽灵页；
  - location_mode=None：结论无引文（如金额占比计算类），本就无原文可点，
               不参与定位（前端不渲染「定位」按钮）。

归一化口径复用 structural_check.page_pinning.normalize_pin_text（只读
import，不改其现役行为）——跨行/全角标点/空白差异在两例试图一致。
"""
import math

from .models import BsFinding, BsMaterial
from .coordinates import normalize_ocr_bbox_to_pdf
from ..structural_check.page_pinning import normalize_pin_text


def reference_location_mode(ref: dict) -> str:
    """文本匹配不代表有 PDF 坐标；没有可信坐标时明确保留页级入口。"""
    anchor = ref.get("anchor") or {}
    if (anchor.get("status") == "precise" and anchor.get("coordinate_space") == "pdf_page"
            and anchor.get("coordinate_frame") == "rendered_page" and anchor.get("bbox")):
        return "precise"
    return "page"


def _page_pool(material: BsMaterial) -> list:
    """材料 → [(page, normalized_text, raw_page)]；无文本层返回空表。"""
    out = []
    for p in material.pages_json or []:
        text = normalize_pin_text(p.get("text") or "")
        if text:
            out.append((p.get("page"), text, p))
    return out


def _layout_bbox(raw_page: dict, normalized_quote: str) -> tuple[list | None, str | None]:
    """从真实 OCR 词级布局计算引文包围框，并记录可选格式归一方式。"""
    meta = raw_page.get("ocr_meta") if isinstance(raw_page, dict) else None
    layout = meta.get("layout") if isinstance(meta, dict) else None
    if not isinstance(layout, list) or not layout:
        return None, None
    cursor = 0
    entries = []
    for word in layout:
        if not isinstance(word, dict):
            continue
        text = normalize_pin_text(word.get("text") or "")
        if not text:
            continue
        word_start = cursor
        cursor += len(text)
        entries.append((word_start, cursor, word.get("bbox")))
    joined = "".join(
        normalize_pin_text((word or {}).get("text") or "")
        for word in layout if isinstance(word, dict)
    )
    needle = normalized_quote
    match_mode = None
    start = joined.find(needle)
    # 部分旧 OCR 引文含跨行截断的 TeX 文本包装，布局词仍为原始正文。
    # 仅移除已识别的文本包装；其他 TeX 命令、正文和数值一律不变。
    if start < 0 and r"\text{" in needle and "\\" not in needle.replace(r"\text{", ""):
        needle = needle.replace(r"$\text{", "").replace(r"\text{", "").replace("}$", "")
        start = joined.find(needle)
        match_mode = "tex_text_wrapper"
    if not needle or start < 0 or joined.find(needle, start + 1) >= 0:
        return None, None
    end = start + len(needle)
    boxes = []
    for word_start, word_end, bbox in entries:
        if word_start < end and word_end > start:
            if not isinstance(bbox, list) or len(bbox) != 4:
                return None, None
            try:
                box = [float(value) for value in bbox]
            except (TypeError, ValueError):
                return None, None
            if not all(math.isfinite(value) for value in box) or box[0] >= box[2] or box[1] >= box[3]:
                return None, None
            boxes.append(box)
    if not boxes:
        return None, None
    return ([min(box[0] for box in boxes), min(box[1] for box in boxes),
             max(box[2] for box in boxes), max(box[3] for box in boxes)], match_mode)


def _metadata_size(value) -> tuple[float, float] | None:
    if isinstance(value, dict):
        value = (value.get("width"), value.get("height"))
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None


def _layout_anchor(raw_page: dict, normalized_quote: str) -> dict:
    """布局 bbox 的统一 anchor 投影；缺少页面几何时保持旧 OCR 坐标。"""
    source_bbox, match_mode = _layout_bbox(raw_page, normalized_quote)
    if source_bbox is None:
        return {"coordinate_space": None, "bbox": None}
    match_meta = {"layout_match": match_mode} if match_mode else {}
    meta = raw_page.get("ocr_meta") if isinstance(raw_page, dict) else None
    if not isinstance(meta, dict):
        return {"coordinate_space": "ocr_image", "bbox": source_bbox, **match_meta}
    image_size = _metadata_size(meta.get("image_size"))
    page_size = _metadata_size(meta.get("page_size"))
    if image_size is None and page_size is None:
        return {"coordinate_space": "ocr_image", "bbox": source_bbox, **match_meta}
    if image_size is None or page_size is None:
        return {
            "coordinate_space": "ocr_image",
            "bbox": source_bbox,
            "source_coordinate_space": "ocr_image",
            "source_bbox": source_bbox,
            "reason": "missing_page_geometry",
            **match_meta,
        }
    if meta.get("coordinate_frame") != "rendered_page":
        return {
            "coordinate_space": "ocr_image",
            "bbox": source_bbox,
            "source_coordinate_space": "ocr_image",
            "source_bbox": source_bbox,
            "reason": "unknown_coordinate_frame",
            **match_meta,
        }
    rotation = meta.get("rotation", 0)
    try:
        if rotation not in (0, 90, 180, 270):
            raise ValueError("invalid page rotation")
        normalized = normalize_ocr_bbox_to_pdf(
            source_bbox,
            image_size=image_size,
            page_size=page_size,
            # OCR 图像已应用页面旋转；这里仅缩放到同一可见页面坐标。
            rotation=0,
        )
    except (TypeError, ValueError):
        return {
            "coordinate_space": "ocr_image",
            "bbox": source_bbox,
            "source_coordinate_space": "ocr_image",
            "source_bbox": source_bbox,
            "reason": "invalid_page_geometry",
            **match_meta,
        }
    return {
        **normalized,
        "rotation": rotation,
        "coordinate_frame": "rendered_page",
        "source_coordinate_space": "ocr_image",
        "source_bbox": source_bbox,
        **match_meta,
    }


def _anchor(*, status: str, normalized_page: str | None = None,
            normalized_quote: str | None = None, raw_page: dict | None = None,
            reason: str | None = None) -> dict:
    start = None
    end = None
    if normalized_page is not None and normalized_quote:
        start = normalized_page.find(normalized_quote)
        if start >= 0:
            end = start + len(normalized_quote)
        else:
            start = None
    geometry = (_layout_anchor(raw_page or {}, normalized_quote or "")
                if status == "precise" and normalized_quote
                else {"coordinate_space": None, "bbox": None})
    result = {"status": status, "text_start": start, "text_end": end,
              **geometry}
    if reason:
        result["reason"] = reason
    return result


def _attach_anchor(f: BsFinding, material_id: int | None, anchor: dict) -> None:
    """为已有证据附坐标；主引文有可信 PDF 坐标时保留其对应引用。"""
    refs = getattr(f, "evidence_json", None)
    if (not refs and material_id is not None and f.material_id == material_id
            and f.page and (f.quote or "").strip() and anchor.get("status") == "precise"
            and anchor.get("coordinate_space") == "pdf_page" and anchor.get("bbox")):
        f.evidence_json = [{"material_id": material_id, "page": f.page, "quote": f.quote,
                            "location_mode": "precise", "anchor": dict(anchor)}]
        return
    if not isinstance(refs, list):
        return
    changed = False
    updated = []
    for ref in refs:
        if not isinstance(ref, dict):
            updated.append(ref)
            continue
        copy = dict(ref)
        ref_material = copy.get("material_id")
        ref_page = copy.get("page")
        ref_quote = copy.get("quote")
        page_matches = ref_page is None or f.page is None or str(ref_page) == str(f.page)
        quote_matches = (not ref_quote or not f.quote
                         or normalize_pin_text(ref_quote) == normalize_pin_text(f.quote))
        if (material_id is not None
                and (ref_material is None or str(ref_material) == str(material_id))
                and page_matches and quote_matches):
            copy["anchor"] = dict(anchor)
            if copy.get("page") is None and anchor.get("status") == "precise" and f.page is not None:
                copy["page"] = f.page
            changed = True
        updated.append(copy)
    if changed:
        f.evidence_json = updated


def pin_findings(findings: list, materials: list) -> None:
    """对 findings 逐条定位（原地修改 ORM 对象，commit 由编排层负责）。

    material_id 精确指材料时只在该材料内匹配；无 material_id（如 V16-01
    转引 A3-01 结论）时在任务全部有文本层的材料池里找第一处命中。
    """
    by_id = {m.id: m for m in materials}
    pool_all: list = []
    for m in materials:
        pool_all.extend((m, page, text, raw) for page, text, raw in _page_pool(m))

    for f in findings:
        if not isinstance(f, BsFinding):
            continue
        quote = (f.quote or "").strip()
        if not quote:
            continue  # 无引文类结论：保持 location_mode=None
        norm_quote = normalize_pin_text(quote)
        pool = pool_all
        if f.material_id is not None:
            mat = by_id.get(f.material_id)
            pool = ([(mat, page, text, raw) for page, text, raw in _page_pool(mat)]
                    if mat is not None else [])
            if f.page is not None:
                # 优先核验引擎原页；同一引文可在合同封面和正文重复出现。
                pool.sort(key=lambda entry: str(entry[1]) != str(f.page))
        if norm_quote:
            if not pool:
                # 材料无文本层 / 材料缺失：引擎页码失去文本层支撑，不可信 → 清空
                f.page = None
                f.location_mode = "unresolved"
                continue
            for mat, page, text, raw in pool:
                if norm_quote in text:
                    f.page = page
                    f.location_mode = "precise"
                    _attach_anchor(
                        f, mat.id,
                        _anchor(status="precise", normalized_page=text,
                                normalized_quote=norm_quote, raw_page=raw),
                    )
                    break
            else:
                _degrade(f, pool[0][0] if f.material_id is not None and pool else None)


def _degrade(f: BsFinding, material=None) -> None:
    """精确未命中的降级：可信页码保留页级定位，否则清空（宁缺勿错）。"""
    if f.page:
        f.location_mode = "page"
        _attach_anchor(f, getattr(material, "id", None), _anchor(
            status="page_only", reason="quote_not_found"))
    else:
        f.page = None
        f.location_mode = "unresolved"
        _attach_anchor(f, getattr(material, "id", None), _anchor(
            status="unresolved", reason="no_verified_page"))
