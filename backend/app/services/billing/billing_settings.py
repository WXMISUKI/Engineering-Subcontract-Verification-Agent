"""计费设置单行（id=1）读写（设计 §4.13）。

``billing_epoch`` 在**首次创建**时定死为当前时间：早于它的台账标 legacy，
只做报表不补扣。之后永不自动改写——改 epoch 等于改历史归属，必须人工决定。
"""

import logging
from datetime import datetime

from ...models.billing_models import BillingSettings, LlmModel

logger = logging.getLogger(__name__)

EDITABLE_FIELDS = {
    "enforce_mode",
    "default_model_id",
    "default_permission_policy",
    "model_key_mode",
    "welcome_credit_micro",
    "treat_unreported_cache_as_miss",
    "overdraft_limit_micro",
    "min_charge_micro",
    "min_balance_micro",
    "default_output_tokens",
    "scene_default_input_tokens",
    "scene_default_output_tokens",
    "require_ledger",
    "credits_per_yuan",
}

VALID_ENFORCE_MODES = {"off", "shadow", "enforce"}
VALID_MODEL_KEY_MODES = {"shared", "per_model"}


def ensure_settings_row(db) -> BillingSettings:
    row = db.query(BillingSettings).filter(BillingSettings.id == 1).first()
    if row is not None:
        return row
    row = BillingSettings(
        id=1,
        enforce_mode="shadow",
        default_permission_policy="all_enabled",
        model_key_mode="shared",
        welcome_credit_micro=100_000_000,  # 100 积分
        treat_unreported_cache_as_miss=True,
        overdraft_limit_micro=0,
        min_charge_micro=0,
        # 新建库默认 100 积分（1 元）；已有行不强制覆盖，管理员可显式设 0。
        min_balance_micro=100_000_000,
        # 新库行创建即按新默认 100 积分，标记为已完成 014 迁移。
        min_balance_default_migrated=True,
        default_output_tokens=1024,
        scene_default_input_tokens=2000,
        scene_default_output_tokens=1024,
        billing_epoch=datetime.now(),
        require_ledger=True,
        credits_per_yuan=100,  # 1 积分 = 0.01 元
    )
    db.add(row)
    db.flush()
    setattr(row, "_created", True)
    logger.info("计费设置初始化：billing_epoch=%s", row.billing_epoch)
    return row


def get_settings(db) -> BillingSettings:
    return ensure_settings_row(db)


def update_settings(db, patch: dict, *, operator_id: int | None = None) -> BillingSettings:
    row = ensure_settings_row(db)
    rejected = [key for key in patch if key not in EDITABLE_FIELDS]
    if rejected:
        raise ValueError(f"不可修改的计费设置字段：{sorted(rejected)}")
    if "enforce_mode" in patch and patch["enforce_mode"] not in VALID_ENFORCE_MODES:
        raise ValueError("enforce_mode 只允许 off/shadow/enforce")
    if "model_key_mode" in patch and patch["model_key_mode"] not in VALID_MODEL_KEY_MODES:
        raise ValueError("model_key_mode 只允许 shared/per_model")
    if patch.get("default_model_id") is not None:
        # 全局默认模型是用户对话/Plus 任务的实际运行模型，不能把 OCR/向量化/
        # TTS/视觉兜底或已停用模型设成默认；前端下拉也只列对话模型。
        model = db.query(LlmModel).filter(LlmModel.id == int(patch["default_model_id"])).first()
        if model is None:
            raise ValueError("默认模型不存在")
        if not model.enabled:
            raise ValueError("默认模型必须先启用")
        if model.unit_type != "token" or not model.is_user_selectable:
            raise ValueError("默认模型必须是用户可选的对话模型（token 类）")
    if "credits_per_yuan" in patch:
        try:
            rate = int(patch["credits_per_yuan"])
        except (TypeError, ValueError):
            raise ValueError("credits_per_yuan 必须是正整数")
        if rate <= 0:
            raise ValueError("credits_per_yuan 必须大于 0（否则元口径无意义）")
        patch["credits_per_yuan"] = rate
    for key in ("min_balance_micro", "default_output_tokens",
                "scene_default_input_tokens", "scene_default_output_tokens"):
        if key in patch and int(patch[key] or 0) < 0:
            raise ValueError(f"{key} 不能为负")
    for key, value in patch.items():
        setattr(row, key, value)
    # 管理员显式保存 min_balance（包括 0）即视为已做出决定：置迁移标记，
    # 之后启动自愈不再覆盖（否则“先设 0 再改 enforce_mode”会被再次回填）。
    if "min_balance_micro" in patch:
        row.min_balance_default_migrated = True
    row.updated_by = operator_id
    db.flush()
    return row