"""计费场景与智能体归因的**唯一定义点**（设计 §18 第 4 条）。

``billing_scene`` 是用户能看懂的「这条消耗属于哪类任务」，与内部 ``source``
（代码调用点）分开：

- ``source`` 由调用点写死（chat / plus:safety-review / ocr …），可以很多；
- ``billing_scene`` 是有限的业务口径，只在这里映射，**不允许在别的文件重复**。

``agent_id`` 只做归属，解析不到就留 NULL（不做猜测）；OCR / 视觉 / embedding
这类子调用继承父任务/会话的智能体，靠调用点传 scope 或显式 agent_id。
"""

from __future__ import annotations

from typing import Optional

# ── 场景常量（对外口径）────────────────────────────────────────────────
PLAN_REVIEW = "plan_review"
STARTUP_REVIEW = "startup_review"
CALC = "calc"
SUBCONTRACT_REVIEW = "subcontract_review"
HAZARD_CHECK = "hazard_check"
CHAT = "chat"
OCR = "ocr"
TTS = "tts"
EMBEDDING = "embedding"
WELCOME = "welcome"
ADMIN_ADJUST = "admin_adjust"
OTHER = "other"

SCENE_NAMES: dict[str, str] = {
    PLAN_REVIEW: "安全方案审查",
    STARTUP_REVIEW: "开工条件核查",
    CALC: "结构受力验证",
    SUBCONTRACT_REVIEW: "工程分包审查",
    HAZARD_CHECK: "事故隐患判别",
    CHAT: "智能问答",
    OCR: "文档识别",
    TTS: "语音合成",
    EMBEDDING: "文本向量化",
    WELCOME: "新用户赠送",
    ADMIN_ADJUST: "管理员调账",
    OTHER: "其他",
}

# source（调用点编码）→ scene。只登记会写台账的来源；未登记 → other。
SOURCE_SCENE_MAP: dict[str, str] = {
    "chat": CHAT,
    "iwen": CHAT,
    "as": CHAT,
    "operate": CHAT,
    "request": OTHER,
    "plus:safety-review": PLAN_REVIEW,
    "plus:startup-conditions-check": STARTUP_REVIEW,
    "plus:structural-check": CALC,
    "plus:blockchain-supervision": SUBCONTRACT_REVIEW,
    "plus:accident-risk": HAZARD_CHECK,
    "plus:accident-risk-chengtou": HAZARD_CHECK,
    "ocr": OCR,
    "ocr_cloud": OCR,
    "vision": OCR,
    "local-ocr": OCR,
    "rapidocr": OCR,
    "tts": TTS,
    "embedding": EMBEDDING,
    "welcome": WELCOME,
    "admin_adjust": ADMIN_ADJUST,
}

# 任务范围 → 场景（scope 自身就说明了业务语义）。唯一定义点也在这里，
# estimates 等模块只引用，不再各自复制一份。
SCOPE_SCENE: dict[str, str] = {
    "as_session": CHAT,
    "chat": CHAT,
    "oc_task": STARTUP_REVIEW,
    "sr_conversation": PLAN_REVIEW,
    "bs_task": SUBCONTRACT_REVIEW,
    "sc_task": CALC,
}

VALID_SCENES = frozenset(SCENE_NAMES)


def scene_for_scope(scope_type: Optional[str]) -> str:
    return SCOPE_SCENE.get(scope_type or "", OTHER)


def scene_for_source(source: Optional[str]) -> str:
    """source → scene；没登记就归 other（不猜业务含义）。"""
    return SOURCE_SCENE_MAP.get((source or "").strip(), OTHER)


def normalize_scene(scene: Optional[str], source: Optional[str] = None) -> str:
    """显式 scene 优先（且必须在已定义集合内），否则按 source 映射。"""
    if scene and scene in VALID_SCENES:
        return scene
    return scene_for_source(source)


def scene_name(scene: Optional[str]) -> str:
    return SCENE_NAMES.get(scene or "", SCENE_NAMES[OTHER])