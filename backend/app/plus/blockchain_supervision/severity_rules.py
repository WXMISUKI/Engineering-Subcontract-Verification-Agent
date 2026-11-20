"""严重度矩阵的数据读取；严重度判定留在 severity.py。"""
import json
from pathlib import Path


_MATRIX_PATH = Path(__file__).with_name("data") / "severity_matrix.json"


def load_severity_matrix() -> dict:
    """每次读取权威 JSON，不缓存，保持规则数据更新后立即生效。"""
    with _MATRIX_PATH.open(encoding="utf-8") as source:
        return json.load(source)
