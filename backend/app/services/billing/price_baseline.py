"""默认价基线与历史 token 账单重算（启动自动执行，幂等）。

2026-11-05：模型默认价从占位价改为百炼官方不打折价，并按输入长度分档。
本模块把两件事收成一次**基线迁移**：

1. 把 ``models.seed.json`` 的默认价发布成新的价格版本（旧版本归档）；
2. 把历史 ``usage_charges`` 里 **token 类**账单按新价重算，并按每个账号的
   差额追加一条 ``adjust`` 流水（append-only，不改历史 consume 行）。

触发点：``main.py`` 启动时调用 :func:`ensure_price_baseline`。用
``billing_settings.price_baseline_key`` 做一次性标记：
- 与 ``BASELINE_KEY`` 相同 → 跳过（之后管理员改价不会被启动覆盖）；
- 不同/空 → 执行并写入标记；失败回滚、下次启动重试。

边界（与重算脚本一致，必须如实报告）：
- 只重算 ``unit_type='token'`` 且 usage_status 为 complete/partial 的账单；
- 历史 OCR/vision 是 page/image 计费、没有 token 记录，保持原样；
- ``legacy`` 行本来就不计费，不重算；
- ``unknown`` / 找不到模型或价格的行跳过，不猜。
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from ...models.billing_models import (
    BillingSettings,
    CreditLedger,
    LlmModel,
    ModelPriceVersion,
    UsageCharge,
    UserWallet,
)
from ...models.models import TokenUsage
from . import wallet
from .billing_settings import ensure_settings_row
from .outbox import _normalized_from_row
from .pricing import price_usage
from .registry import get_model, seed_path
from .schema import billing_tables_available

logger = logging.getLogger(__name__)

# 改默认价基线时改这里；标记变化会让每个库再跑一次「应用价 + 重算历史」。
BASELINE_KEY = "bailian-2026-11-05-v3"
IDEMPOTENCY_PREFIX = "price-baseline-20261105"
REASON = "按 2026-11-05 百炼不打折默认价重算历史用量差额"

PRICE_FIELDS = (
    "cache_hit_input_micro_per_1k",
    "cache_miss_input_micro_per_1k",
    "cache_write_input_micro_per_1k",
    "output_micro_per_1k",
    "unit_micro_per_unit",
    "total_fallback_micro_per_1k",
)


# ── 种子价 → 新价格版本 ────────────────────────────────────────────────

def _load_seed_models() -> dict[str, dict]:
    data = json.loads(seed_path().read_text(encoding="utf-8"))
    return {item["code"]: item for item in data.get("models", []) if item.get("code")}


def _price_values(price: dict) -> dict:
    return {field: price.get(field) for field in PRICE_FIELDS}


def _same_price(version: ModelPriceVersion | None, price: dict) -> bool:
    if version is None:
        return False
    if any(getattr(version, field) != price.get(field) for field in PRICE_FIELDS):
        return False
    return (version.tiers_json or None) == (price.get("tiers") or None)


def _diff(old: dict, new: dict, *, old_tiers=None, new_tiers=None) -> str:
    parts = []
    for field in PRICE_FIELDS:
        before, after = old.get(field), new.get(field)
        if before != after:
            parts.append(f"{field}: {before} -> {after}")
    if (old_tiers or None) != (new_tiers or None):
        parts.append(f"tiers: {len(old_tiers or [])} -> {len(new_tiers or [])} 档")
    return "; ".join(parts) or "无变化"


def _latest_version(db, model_id: int) -> Optional[ModelPriceVersion]:
    return (
        db.query(ModelPriceVersion)
        .filter(ModelPriceVersion.model_id == model_id)
        .order_by(ModelPriceVersion.version.desc())
        .first()
    )


def _apply_one(db, model: LlmModel, item: dict, price: dict) -> dict:
    latest = _latest_version(db, model.id)
    target = _price_values(price)
    unit_type = item.get("unit_type", "token")
    unit_scale = int(item.get("unit_scale", 1))
    unit_changed = (model.unit_type, int(model.unit_scale or 1)) != (unit_type, unit_scale)
    if _same_price(latest, price) and not unit_changed:
        return {"code": model.code, "action": "unchanged"}

    if unit_changed:
        model.unit_type = unit_type
        model.unit_scale = unit_scale

    if _same_price(latest, price):
        return {
            "code": model.code,
            "action": "unit_only",
            "unit": f"{unit_type}/{unit_scale}",
        }

    now = datetime.now()
    next_version = (latest.version + 1) if latest else 1
    db.query(ModelPriceVersion).filter(
        ModelPriceVersion.model_id == model.id,
        ModelPriceVersion.status == "published",
    ).update({"status": "archived", "effective_to": now}, synchronize_session=False)
    version = ModelPriceVersion(
        model_id=model.id,
        version=next_version,
        status="published",
        effective_from=now,
        published_at=now,
        tiers_json=price.get("tiers"),
        note=(price.get("note") or "")[:255] or None,
        **target,
    )
    db.add(version)
    db.flush()
    return {
        "code": model.code,
        "action": "published",
        "version": next_version,
        "unit": f"{unit_type}/{unit_scale}",
        "diff": _diff(
            {field: getattr(latest, field) for field in PRICE_FIELDS} if latest else {},
            target,
            old_tiers=getattr(latest, "tiers_json", None) if latest else None,
            new_tiers=price.get("tiers"),
        ),
    }


def plan_seed_prices(db) -> list[dict]:
    """只读预览：种子价与当前最新版本的差异。"""
    report = []
    for code, item in _load_seed_models().items():
        price = item.get("price") or {}
        if not price:
            continue
        model = db.query(LlmModel).filter(LlmModel.code == code).first()
        if model is None:
            report.append({"code": code, "action": "missing_model"})
            continue
        latest = _latest_version(db, model.id)
        unit_changed = (model.unit_type, int(model.unit_scale or 1)) != (
            item.get("unit_type", "token"), int(item.get("unit_scale", 1))
        )
        changed = not _same_price(latest, price) or unit_changed
        report.append({
            "code": code,
            "action": "would_publish" if changed else "unchanged",
            "diff": _diff(
                {field: getattr(latest, field) for field in PRICE_FIELDS} if latest else {},
                _price_values(price),
                old_tiers=getattr(latest, "tiers_json", None) if latest else None,
                new_tiers=price.get("tiers"),
            ),
        })
    return report


def apply_seed_prices(db) -> list[dict]:
    """按种子价发布新版本（不 commit）；返回与 plan 同形的报告。"""
    report = []
    for code, item in _load_seed_models().items():
        price = item.get("price") or {}
        if not price:
            continue
        model = db.query(LlmModel).filter(LlmModel.code == code).first()
        if model is None:
            report.append({"code": code, "action": "missing_model"})
            continue
        report.append(_apply_one(db, model, item, price))
    return report


# ── 历史 token 账单重算 ───────────────────────────────────────────────

def _current_published_price(db, model_id: int) -> Optional[ModelPriceVersion]:
    """取模型当前发布的版本；重算历史必须用它，不能按调用时间回看 effective 窗口。

    MySQL DATETIME 是秒级精度：刚发布的新版本 effective_from 与旧版 effective_to
    可能落在同一秒，按时间点回看会误选旧版。85 首次迁移就因此把 99 条 token 账单
    按旧价重算成原值（差额 0、钱包没补），这里改为直接取 published 的最新版本。
    """
    return (
        db.query(ModelPriceVersion)
        .filter(
            ModelPriceVersion.model_id == model_id,
            ModelPriceVersion.status == "published",
        )
        .order_by(ModelPriceVersion.version.desc(), ModelPriceVersion.id.desc())
        .first()
    )


def plan_rerate(db) -> dict:
    """只读预览：按当前 published 价重算 token 账单，按账号汇总差额。"""
    settings = db.query(BillingSettings).filter(BillingSettings.id == 1).first()
    treat_miss = bool(settings.treat_unreported_cache_as_miss) if settings else True
    min_charge = int(settings.min_charge_micro or 0) if settings else 0

    details = []
    skipped = defaultdict(int)
    per_user = defaultdict(lambda: {"old": 0, "new": 0, "rows": 0})
    charges = (
        db.query(UsageCharge)
        .filter(UsageCharge.status.in_(("rated", "partial")))
        .order_by(UsageCharge.id)
        .all()
    )
    for charge in charges:
        usage = db.get(TokenUsage, charge.usage_id) if charge.usage_id else None
        if usage is None:
            skipped["usage_missing"] += 1
            continue
        if (charge.unit_type or usage.unit_type) != "token":
            skipped[f"non_token:{charge.unit_type or usage.unit_type}"] += 1
            continue
        if (usage.usage_status or "") in ("legacy", "unknown"):
            skipped[f"usage_{usage.usage_status or 'none'}"] += 1
            continue
        model = get_model(db, str(usage.model_code)) if usage.model_code else None
        if model is None:
            skipped["model_missing"] += 1
            continue
        price = _current_published_price(db, model.id)
        if price is None:
            skipped["price_missing"] += 1
            continue
        result = price_usage(
            unit_type="token",
            usage=_normalized_from_row(usage),
            quantity=None,
            unit_scale=int(usage.unit_scale or model.unit_scale or 1),
            price_version=price,
            treat_unreported_as_miss=treat_miss,
            min_charge_micro=min_charge,
        )
        if result.status in ("unknown", "price_missing"):
            skipped[f"price_{result.status}"] += 1
            continue
        old_micro = int(charge.charged_micro or 0)
        new_micro = int(result.charged_micro)
        key = (charge.user_type, int(charge.user_id))
        per_user[key]["old"] += old_micro
        per_user[key]["new"] += new_micro
        per_user[key]["rows"] += 1
        details.append({
            "charge_id": charge.id,
            "usage_id": charge.usage_id,
            "user": f"{charge.user_type}:{charge.user_id}",
            "model": model.code,
            "model_id": model.id,
            "price_version_id": price.id,
            "input": usage.input_count,
            "output": usage.output_count,
            "tier_version": price.version,
            "old_micro": old_micro,
            "new_micro": new_micro,
            "raw_micro": int(result.raw_micro),
            "new_status": result.status,
            "diff_micro": old_micro - new_micro,
        })

    users = []
    for (user_type, user_id), sums in sorted(per_user.items()):
        if user_type == "system" or not user_id:
            continue
        diff = int(sums["old"] - sums["new"])
        if diff == 0:
            continue
        users.append({
            "user_type": user_type,
            "user_id": user_id,
            "user": f"{user_type}:{user_id}",
            "rows": sums["rows"],
            "old_micro": sums["old"],
            "new_micro": sums["new"],
            "diff_micro": diff,
            "old_credits": round(sums["old"] / 1_000_000, 6),
            "new_credits": round(sums["new"] / 1_000_000, 6),
            "diff_credits": round(diff / 1_000_000, 6),
        })
    return {"details": details, "skipped": dict(skipped), "users": users}


def _backup_path() -> Path:
    logs = Path(__file__).resolve().parents[3] / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    return logs / f"price-baseline-backup-{datetime.now():%Y%m%d-%H%M%S}.json"


def _write_backup(db, plan: dict) -> Optional[str]:
    charge_ids = [d["charge_id"] for d in plan["details"]]
    users = [(u["user_type"], u["user_id"]) for u in plan["users"]]
    if not charge_ids and not users:
        return None
    charges = db.query(UsageCharge).filter(UsageCharge.id.in_(charge_ids)).all() if charge_ids else []
    ledgers = []
    wallets = []
    for user_type, user_id in users:
        ledgers.extend(
            db.query(CreditLedger).filter(
                CreditLedger.user_type == user_type, CreditLedger.user_id == user_id
            ).all()
        )
        wallet_row = db.query(UserWallet).filter(
            UserWallet.user_type == user_type, UserWallet.user_id == user_id
        ).first()
        if wallet_row is not None:
            wallets.append(wallet_row)
    payload = {
        "created_at": datetime.now().isoformat(),
        "baseline": BASELINE_KEY,
        "usage_charges": [
            {c.name: getattr(row, c.name) for c in row.__table__.columns}
            for row in charges
        ],
        "credit_ledger": [
            {c.name: getattr(row, c.name) for c in row.__table__.columns}
            for row in ledgers
        ],
        "user_wallets": [
            {c.name: getattr(row, c.name) for c in row.__table__.columns}
            for row in wallets
        ],
    }
    try:
        path = _backup_path()
        path.write_text(json.dumps(payload, ensure_ascii=False, default=str, indent=2), encoding="utf-8")
        return str(path)
    except Exception as exc:  # noqa: BLE001 —— 备份失败不能阻断自动迁移
        logger.warning("价格基线备份写入失败（继续执行重算）：%s", exc, exc_info=True)
        return None


def apply_rerate(db, plan: dict, *, write_backup: bool = True) -> dict:
    """执行重算：更新 usage_charges + 按账号追加 adjust 流水（不 commit）。"""
    if not plan["details"] and not plan["users"]:
        return {"token_charges": 0, "users": [], "backup": None}
    backup = _write_backup(db, plan) if write_backup else None
    for detail in plan["details"]:
        charge = db.get(UsageCharge, detail["charge_id"])
        if charge is None:
            continue
        charge.model_id = detail["model_id"]
        charge.price_version_id = detail["price_version_id"]
        charge.raw_micro = detail["raw_micro"]
        charge.charged_micro = detail["new_micro"]
        charge.status = detail["new_status"]
    applied_users = []
    for user in plan["users"]:
        diff = int(user["diff_micro"])
        if diff == 0:
            continue
        wallet_row = db.query(UserWallet).filter(
            UserWallet.user_type == user["user_type"],
            UserWallet.user_id == user["user_id"],
        ).first()
        if wallet_row is None:
            continue
        wallet.append_ledger(
            db,
            user_type=user["user_type"],
            user_id=user["user_id"],
            entry_type="adjust",
            amount_micro=diff,
            idempotency_key=f"{IDEMPOTENCY_PREFIX}:{user['user_type']}:{user['user_id']}",
            ref_type="price_baseline",
            ref_id=BASELINE_KEY,
            reason=REASON,
            billing_scene="price_baseline",
        )
        applied_users.append({**user, "applied": True})
    return {"token_charges": len(plan["details"]), "users": applied_users, "backup": backup}


# ── 启动入口 ───────────────────────────────────────────────────────────

def ensure_price_baseline(db, *, write_backup: bool = True) -> dict:
    """启动时执行一次：应用种子价 + 重算历史 token 账单。

    幂等：``billing_settings.price_baseline_key`` 等于 ``BASELINE_KEY`` 时直接跳过。
    失败会回滚并抛出，由调用方记录；标记不写入，下次启动重试。
    """
    if not billing_tables_available(db.get_bind()):
        return {"status": "skipped", "reason": "billing_tables_missing"}
    ensure_settings_row(db)
    # MySQL 下锁住单行，避免多 worker 并发跑两次；SQLite 忽略 FOR UPDATE。
    settings = (
        db.query(BillingSettings)
        .filter(BillingSettings.id == 1)
        .with_for_update()
        .first()
    )
    if settings is not None and settings.price_baseline_key == BASELINE_KEY:
        db.commit()
        return {"status": "already", "baseline": BASELINE_KEY}

    try:
        price_report = apply_seed_prices(db)
        plan = plan_rerate(db)
        rerate_report = apply_rerate(db, plan, write_backup=write_backup)
        settings = db.query(BillingSettings).filter(BillingSettings.id == 1).first()
        if settings is not None:
            settings.price_baseline_key = BASELINE_KEY
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {
        "status": "applied",
        "baseline": BASELINE_KEY,
        "prices": price_report,
        "rerate": rerate_report,
        "skipped": plan.get("skipped", {}),
    }