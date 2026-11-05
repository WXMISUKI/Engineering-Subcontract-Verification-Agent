"""OCR 图片坐标到 PDF 页面坐标的纯函数转换。

输入坐标使用图片左上角原点；输出坐标使用 PDF 页面左上角原点，单位与
``page_size`` 一致（通常是 PDF point）。本模块不读取文件、不访问数据库，
便于 pinning、导出和回归测试复用同一套几何口径。
"""

from __future__ import annotations

import math
from collections.abc import Sequence


_ROTATIONS = {0, 90, 180, 270}
_EPSILON = 1e-5


def _finite_positive_pair(value: Sequence[float], *, name: str) -> tuple[float, float]:
    try:
        length = len(value)
    except (TypeError, AttributeError):
        length = -1
    if isinstance(value, (str, bytes)) or length != 2:
        raise ValueError(f"{name} must contain width and height")
    width, height = (float(item) for item in value)
    if not math.isfinite(width) or not math.isfinite(height) or width <= 0 or height <= 0:
        raise ValueError(f"{name} must contain positive finite dimensions")
    return width, height


def _bbox_values(bbox: Sequence[float], *, image_size: tuple[float, float]) -> tuple[float, ...]:
    try:
        length = len(bbox)
    except (TypeError, AttributeError):
        length = -1
    if isinstance(bbox, (str, bytes)) or length != 4:
        raise ValueError("bbox must contain x0, y0, x1 and y1")
    values = tuple(float(item) for item in bbox)
    if not all(math.isfinite(item) for item in values):
        raise ValueError("bbox must contain finite values")
    x0, y0, x1, y1 = values
    image_width, image_height = image_size
    if x0 > x1 or y0 > y1:
        raise ValueError("bbox must be ordered as x0 <= x1 and y0 <= y1")
    if (x0 < -_EPSILON or y0 < -_EPSILON
            or x1 > image_width + _EPSILON or y1 > image_height + _EPSILON):
        raise ValueError("bbox must stay within image bounds")
    return (
        min(max(x0, 0.0), image_width),
        min(max(y0, 0.0), image_height),
        min(max(x1, 0.0), image_width),
        min(max(y1, 0.0), image_height),
    )


def _round(value: float) -> float:
    rounded = round(float(value), 6)
    return 0.0 if abs(rounded) < _EPSILON else rounded


def normalize_ocr_bbox_to_pdf(
    bbox: Sequence[float],
    *,
    image_size: Sequence[float],
    page_size: Sequence[float],
    rotation: int = 0,
) -> dict:
    """将 OCR 图片 bbox 转为 PDF 页面坐标。

    ``rotation`` 表示图片相对于未旋转 PDF 页面顺时针旋转的角度。图片和
    页面尺寸可以不同；换算按各自宽高比例进行。输出包含规范坐标和必要的
    页面元数据，供上层写入 evidence anchor。
    """
    image_width, image_height = _finite_positive_pair(image_size, name="image_size")
    page_width, page_height = _finite_positive_pair(page_size, name="page_size")
    if rotation not in _ROTATIONS:
        raise ValueError("rotation must be one of 0, 90, 180 or 270")

    x0, y0, x1, y1 = _bbox_values(
        bbox,
        image_size=(image_width, image_height),
    )
    points = (
        (x0 / image_width, y0 / image_height),
        (x1 / image_width, y0 / image_height),
        (x0 / image_width, y1 / image_height),
        (x1 / image_width, y1 / image_height),
    )

    def transform(point: tuple[float, float]) -> tuple[float, float]:
        x, y = point
        if rotation == 0:
            return x * page_width, y * page_height
        if rotation == 90:
            return y * page_width, (1.0 - x) * page_height
        if rotation == 180:
            return (1.0 - x) * page_width, (1.0 - y) * page_height
        return (1.0 - y) * page_width, x * page_height

    transformed = [transform(point) for point in points]
    output_bbox = [
        _round(min(point[0] for point in transformed)),
        _round(min(point[1] for point in transformed)),
        _round(max(point[0] for point in transformed)),
        _round(max(point[1] for point in transformed)),
    ]
    return {
        "bbox": output_bbox,
        "coordinate_space": "pdf_page",
        "page_size": {"width": _round(page_width), "height": _round(page_height)},
        "rotation": rotation,
    }


__all__ = ["normalize_ocr_bbox_to_pdf"]
