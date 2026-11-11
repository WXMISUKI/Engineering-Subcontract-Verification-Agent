"""模型目录 / 默认模型 / 生效价格版本 / 种子（设计 §4.1–4.3、§9）。

env 只做**首次种子与兜底**：运行时全局默认读 ``billing_settings.default_model_id``。
``DEFAULT_MODEL_CODE`` 为空时回退 ``LLM_MODEL`` 并告警（只告警一次）。
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import select

from ...config import get_settings
from ...models.billing_models import (
    BillingOutbox,
    BillingSettings,
    LlmModel,
    LlmProvider,
    ModelPriceVersion,
)
from ...models.models import TokenUsage

logger = logging.getLogger(__name__)

# 来源 → 目录模型编码。仅用于**没有显式 model_code** 的旧调用点兜底，
# 显式传入的 model_code 永远优先（设计 §1：计费只解析实际使用的模型）。
SOURCE_MODEL_MAP = {
    "chat": "coze-default",
    "iwen": "coze-default",
    "tts": "mimo-v2.5-tts",
    "ocr": "qwen3.5-ocr",
    "ocr_cloud": "qwen3.5-ocr",
    "vision": "qwen3-vl-plus",
    "embedding": "text-embedding-v3",
    "local-ocr": "rapidocr-local",
    "rapidocr": "rapidocr-local",
}

_warned_llm_model_fallback = False


def default_model_code() -> str:
    """平台默认模型编码：DEFAULT_MODEL_CODE > LLM_MODEL（告警） > qwen3.7-flash。"""
    global _warned_llm_model_fallback
    settings = get_settings()
    code = (settings.DEFAULT_MODEL_CODE or "").strip()
    if code:
        return code
    legacy = (settings.LLM_MODEL or "").strip()
    if legacy:
        if not _warned_llm_model_fallback:
            logger.warning(
                "DEFAULT_MODEL_CODE 未配置，回退 LLM_MODEL=%s 作为种子/兜底；"
                "运行时请以 billing_settings.default_model_id 为准",
                legacy,
            )
            _warned_llm_model_fallback = True
        return legacy
    return "qwen3.7-flash"


def seed_path() -> Path:
    settings = get_settings()
    if settings.MODEL_SEED_PATH:
        return Path(settings.MODEL_SEED_PATH)
    packaged = Path(__file__).resolve().parents[3] / "config" / "models.seed.json"
    if packaged.exists():
        return packaged
    # 独立版镜像把 app 目录作为不可变运行根时，允许种子随 app 包一起发布。
    return Path(__file__).resolve().parents[2] / "models.seed.json"


def get_model(db, code: str | None) -> Optional[LlmModel]:
    if not code:
        return None
    return db.query(LlmModel).filter(LlmModel.code == code).first()


def get_default_model(db) -> Optional[LlmModel]:
    settings = get_settings_row(db)
    if settings is not None and settings.default_model_id:
        model = db.query(LlmModel).filter(LlmModel.id == settings.default_model_id).first()
        if model is not None:
            return model
    model = db.query(LlmModel).filter(LlmModel.is_default.is_(True)).first()
    if model is not None:
        return model
    return get_model(db, default_model_code())


def resolve_model_for_source(db, source: str | None, explicit: str | None = None):
    """显式模型 > 来源映射 > 全局默认。返回 LlmModel 或 None。"""
    if explicit:
        return get_model(db, explicit)
    key = (source or "").strip()
    mapped = SOURCE_MODEL_MAP.get(key)
    if mapped:
        model = get_model(db, mapped)
        if model is not None:
            return model
    # plus:<code> / as / operate 等统一走全局默认（实际使用的就是 LLM_MODEL）。
    return get_default_model(db)


def get_active_price_version(db, model_id: int, at: datetime | None = None):
    """按台账时间找生效的 published 版本；找不到返回 None（→ price_missing）。"""
    at = at or datetime.now()
    query = db.query(ModelPriceVersion).filter(
        ModelPriceVersion.model_id == model_id,
        # archived 版本仍要能被「历史时间点」找到——历史账单引用旧版本，
        # 归档只表示不再对新调用生效，不是删除；靠 effective_to 封口。
        ModelPriceVersion.status.in_(("published", "archived")),
    )
    candidates = query.order_by(ModelPriceVersion.effective_from.desc(), ModelPriceVersion.id.desc()).all()
    for version in candidates:
        start = version.effective_from
        end = version.effective_to
        if start is not None and start > at:
            continue
        if end is not None and end <= at:
            continue
        return version
    return None


def get_settings_row(db) -> Optional[BillingSettings]:
    return db.query(BillingSettings).filter(BillingSettings.id == 1).first()


def _load_seed() -> dict:
    path = seed_path()
    if not path.exists():
        logger.warning("模型目录种子不存在：%s（跳过种子）", path)
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        logger.exception("模型目录种子解析失败：%s", path)
        return {}


def _ensure_provider(db, item: dict) -> LlmProvider:
    provider = db.query(LlmProvider).filter(LlmProvider.code == item["code"]).first()
    if provider is not None:
        return provider
    provider = LlmProvider(
        code=item["code"],
        name=item.get("name", ""),
        protocol=item.get("protocol", "openai_compatible"),
        base_url=(item.get("base_url") or "")[:255] or None,
        api_key_ref=(item.get("api_key_ref") or "")[:128] or None,
        extra_json=item.get("extra_json"),
        enabled=bool(item.get("enabled", True)),
    )
    db.add(provider)
    db.flush()
    return provider


def _ensure_price_version(db, model: LlmModel, price: dict, effective_from: datetime) -> bool:
    """模型没有任何价格版本时补一条 v1 published；已有版本一律不动。"""
    exists = db.query(ModelPriceVersion.id).filter(
        ModelPriceVersion.model_id == model.id
    ).first()
    if exists:
        return False
    version = ModelPriceVersion(
        model_id=model.id,
        version=1,
        status="published",
        effective_from=effective_from,
        cache_hit_input_micro_per_1k=price.get("cache_hit_input_micro_per_1k"),
        cache_miss_input_micro_per_1k=price.get("cache_miss_input_micro_per_1k"),
        cache_write_input_micro_per_1k=price.get("cache_write_input_micro_per_1k"),
        output_micro_per_1k=price.get("output_micro_per_1k"),
        unit_micro_per_unit=price.get("unit_micro_per_unit"),
        total_fallback_micro_per_1k=price.get("total_fallback_micro_per_1k"),
        tiers_json=price.get("tiers"),
        note=(price.get("note") or "")[:255] or None,
    )
    db.add(version)
    db.flush()
    return True


def seed_catalog(db, *, effective_from: datetime | None = None) -> dict:
    """幂等种子：provider/模型/首个价格版本。已存在的行**不覆盖**。

    返回本次实际创建数量，便于启动日志复核（空报告 = 已是最新）。
    """
    from .billing_settings import ensure_settings_row

    data = _load_seed()
    report = {"providers": 0, "models": 0, "price_versions": 0, "settings_created": False}
    if not data:
        return report
    settings_row = ensure_settings_row(db)
    report["settings_created"] = bool(getattr(settings_row, "_created", False))
    epoch = effective_from or settings_row.billing_epoch or datetime.now()

    providers: dict[str, LlmProvider] = {}
    for item in data.get("providers", []):
        before = db.query(LlmProvider.id).filter(LlmProvider.code == item["code"]).first()
        providers[item["code"]] = _ensure_provider(db, item)
        if before is None:
            report["providers"] += 1

    for item in data.get("models", []):
        provider = providers.get(item.get("provider")) or _ensure_provider(
            db, {"code": item.get("provider", "default"), "name": item.get("provider", "default")}
        )
        model = db.query(LlmModel).filter(LlmModel.code == item["code"]).first()
        if model is None:
            model = LlmModel(
                code=item["code"],
                display_name=item.get("display_name", item["code"]),
                provider_id=provider.id,
                upstream_model=item.get("upstream_model", item["code"]),
                unit_type=item.get("unit_type", "token"),
                unit_scale=int(item.get("unit_scale", 1)),
                context_size=item.get("context_size"),
                max_output=item.get("max_output"),
                supports_vision=bool(item.get("supports_vision", False)),
                supports_tools=bool(item.get("supports_tools", False)),
                supports_reasoning=bool(item.get("supports_reasoning", False)),
                is_user_selectable=bool(item.get("is_user_selectable", True)),
                enabled=bool(item.get("enabled", True)),
                sort_order=int(item.get("sort_order", 0)),
            )
            db.add(model)
            db.flush()
            report["models"] += 1
        # 只有种子显式给了价格才建 published 版本。没有 price 的模型代表
        # 「只入目录、未定价」：不能补一条全 None 的版本冒充生效价，否则会被
        # available_models 当成已定价（→ price_missing 也照样对用户可见）。
        price = item.get("price") or {}
        if price and _ensure_price_version(db, model, price, epoch):
            report["price_versions"] += 1

    # 全局默认：仅在完全没设过时按 DEFAULT_MODEL_CODE 指向种子模型，之后由管理员维护。
    code = default_model_code()
    target = db.query(LlmModel).filter(LlmModel.code == code).first()
    if target is not None:
        if not settings_row.default_model_id:
            settings_row.default_model_id = target.id
        if db.query(LlmModel.id).filter(LlmModel.is_default.is_(True)).first() is None:
            target.is_default = True
    db.flush()
    return report


def mark_legacy_usage(db, *, epoch: datetime | None = None) -> int:
    """把 billing_epoch 之前的台账标 legacy：只报表、不补扣、不删行。

    只动 ``usage_status IS NULL`` 的行，重复执行幂等；新链路写入的行不会命中。
    """
    if epoch is None:
        row = get_settings_row(db)
        epoch = row.billing_epoch if row is not None else None
    if epoch is None:
        return 0
    legacy_ids = [
        row[0] for row in db.query(TokenUsage.id).filter(
            TokenUsage.usage_status.is_(None),
            TokenUsage.created_at < epoch,
        ).all()
    ]
    if legacy_ids:
        # 历史行不生成账单：清掉可能由插入钩子登记的 pending 出账任务。
        db.query(BillingOutbox).filter(
            BillingOutbox.usage_id.in_(legacy_ids),
            BillingOutbox.status == "pending",
        ).delete(synchronize_session=False)
    changed = (
        db.query(TokenUsage)
        .filter(
            TokenUsage.usage_status.is_(None),
            TokenUsage.created_at < epoch,
        )
        .update(
            {
                TokenUsage.unit_type: "token",
                TokenUsage.usage_status: "legacy",
                TokenUsage.billing_status: "legacy",
                TokenUsage.status: "completed",
            },
            synchronize_session=False,
        )
    )
    db.flush()
    if changed:
        logger.info("历史用量标记 legacy：%s 行（epoch=%s）", changed, epoch)
    return int(changed or 0)


def bootstrap(db) -> dict:
    """启动时一次性引导：计费设置 → 种子 → 历史台账 legacy。幂等。"""
    from . import ledger  # noqa: F401 —— 注册 TokenUsage.after_insert 出账钩子

    report = seed_catalog(db)
    from .billing_settings import ensure_settings_row

    settings_row = ensure_settings_row(db)
    report["legacy_rows"] = mark_legacy_usage(db, epoch=settings_row.billing_epoch)
    db.commit()
    return report
