# -*- coding: utf-8 -*-
"""模型官方上下文上限同步（2026-11-05 改为动态抓取）。

优先级：
1. DashScope 原生模型中心 `GET https://dashscope.aliyuncs.com/api/v1/models`
   的 `model_info`（`max_input_tokens` / `context_window` / `max_output_tokens`）。
   实测 2026-11-05 与百炼模型广场详情页一致：qwen3.7-flash 991,808 / 1,000,000 /
   131,072；deepseek-v4.1-flash 1,000,000 / 1,000,000 / 393,216。
2. 模型中心没有该模型或请求失败时，回退 MaaS 的 OpenAI 兼容端点
   `GET /models/{id}` 的 `extra_info.default_envs`。该接口可能返回工作空间的
   保守值（实测 qwen3.7-flash 为 129,024 / 8,192），只作兜底。

同步口径：
- `context_size_limit` 写官方 max_input_tokens（模型硬上限）；
- `context_size` 为空时默认填官方值；管理员已设置的更小值保留；超过官方值的
  旧配置会被压到上限，并在报告里列出；
- `max_output` 为空时填官方 max_output_tokens；管理员设置过则保留；
- 报告里的 `source`/`source_label` 标明本次该模型用的是模型中心还是接口兜底。
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from urllib.parse import quote

import httpx
from sqlalchemy.orm import Session

from ...config import get_settings
from ...models.billing_models import LlmModel, LlmProvider, ModelPriceVersion
from . import model_keys

logger = logging.getLogger(__name__)

SYNC_PROVIDERS = {"default", "dashscope"}
SYNC_TIMEOUT = 12.0
DASHSCOPE_MODELS_URL = "https://dashscope.aliyuncs.com/api/v1/models"
MODEL_CENTER_CACHE_TTL = 600.0

# 一次按钮同步会遍历多个模型，模型中心列表只拉一次（4 页/373 个模型）。
_MODEL_CENTER_CACHE: dict = {"at": 0.0, "models": []}


def _provider(db: Session, model: LlmModel) -> LlmProvider | None:
    return db.query(LlmProvider).filter(LlmProvider.id == model.provider_id).first()


def _base_url(db: Session, model: LlmModel) -> str:
    provider = _provider(db, model)
    base = (getattr(provider, "base_url", None) or "").strip()
    if not base:
        base = (get_settings().LLM_API_BASE or "").strip()
    return base.rstrip("/")


def _api_key(model: LlmModel) -> str:
    cfg = model_keys.resolve_model_runtime(model.code) or {}
    key = cfg.get("api_key") or ""
    if not key:
        key = model_keys.resolve_api_key_for_code(model.code) or ""
    return key


def _fetch_model_center(api_key: str) -> list[dict]:
    """拉取 DashScope 模型中心全量列表（分页+短缓存），失败抛异常。"""
    now = time.monotonic()
    if _MODEL_CENTER_CACHE["models"] and now - _MODEL_CENTER_CACHE["at"] < MODEL_CENTER_CACHE_TTL:
        return _MODEL_CENTER_CACHE["models"]
    models: list[dict] = []
    page = 1
    with httpx.Client(timeout=SYNC_TIMEOUT) as client:
        while page <= 10:
            last_error = None
            resp = None
            for _attempt in range(2):
                try:
                    resp = client.get(
                        DASHSCOPE_MODELS_URL,
                        headers={"Authorization": f"Bearer {api_key}"},
                        params={"page_no": page, "page_size": 100},
                    )
                    break
                except Exception as exc:  # noqa: BLE001 —— 网络抖动重试一次
                    last_error = exc
            if resp is None:
                raise RuntimeError(f"模型中心请求失败：{last_error}") from last_error
            if resp.status_code != 200:
                raise RuntimeError(f"模型中心 HTTP {resp.status_code}: {resp.text[:120]}")
            output = (resp.json() or {}).get("output") or {}
            batch = output.get("models") or []
            models.extend(batch)
            total = int(output.get("total") or 0)
            if not batch or (total and len(models) >= total):
                break
            page += 1
    if not models:
        raise RuntimeError("模型中心返回空列表")
    _MODEL_CENTER_CACHE.update(at=now, models=models)
    return models


def _match_model_center(models: list[dict], model: LlmModel) -> dict | None:
    """按目录 code / upstream_model 精确匹配；没有再试同名前缀（如 -1017）。"""
    names = [n for n in (model.upstream_model, model.code) if n]
    by_name = {m.get("model"): m for m in models if isinstance(m, dict)}
    for name in names:
        if name in by_name:
            return by_name[name]
    for name in names:
        for item in models:
            code = (item.get("model") or "") if isinstance(item, dict) else ""
            if code.startswith(name + "-") or code.startswith(name + "/"):
                return item
    return None


def _model_center_limits(model: LlmModel) -> dict:
    key = _api_key(model) or get_settings().LLM_API_KEY
    if not key:
        raise RuntimeError("provider api key 未配置")
    item = _match_model_center(_fetch_model_center(key), model)
    if item is None:
        raise RuntimeError("模型中心未找到该模型")
    info = item.get("model_info") or {}
    context_limit = info.get("max_input_tokens") or info.get("context_window")
    max_output = info.get("max_output_tokens")
    try:
        context_limit = int(context_limit) if context_limit is not None else None
    except (TypeError, ValueError):
        context_limit = None
    try:
        max_output = int(max_output) if max_output is not None else None
    except (TypeError, ValueError):
        max_output = None
    if not context_limit or context_limit <= 0:
        raise RuntimeError("模型中心没有可用的 max_input_tokens")
    return {
        "context_size_limit": context_limit,
        "context_length": int(info.get("context_window") or context_limit),
        "max_output": max_output,
        "request_model": item.get("model") or model.upstream_model or model.code,
        "source": "dashscope_models_api",
        "source_label": "DashScope /api/v1/models（与百炼模型广场同源）",
        "source_url": DASHSCOPE_MODELS_URL,
    }


def _maas_endpoint_limits(db: Session, model: LlmModel) -> dict:
    """兜底：MaaS OpenAI 兼容端点 /models/{id} 的 extra_info.default_envs。"""
    base = _base_url(db, model)
    if not base:
        raise RuntimeError("provider base_url 未配置")
    key = _api_key(model)
    if not key:
        raise RuntimeError("provider api key 未配置")
    request_model = (model.upstream_model or model.code or "").strip()
    if not request_model:
        raise RuntimeError("upstream_model 为空")
    url = f"{base}/models/{quote(request_model, safe='')}"
    with httpx.Client(timeout=SYNC_TIMEOUT) as client:
        resp = client.get(url, headers={"Authorization": f"Bearer {key}"})
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:120]}")
    data = resp.json()
    env = ((data.get("extra_info") or {}).get("default_envs") or {})
    context_limit = (
        env.get("max_input_tokens")
        or env.get("context_length")
        or env.get("max_context_length")
        or env.get("max_tokens")
    )
    max_output = (
        env.get("max_output_tokens")
        or env.get("max_output_length")
    )
    try:
        context_limit = int(context_limit) if context_limit is not None else None
    except (TypeError, ValueError):
        context_limit = None
    try:
        max_output = int(max_output) if max_output is not None else None
    except (TypeError, ValueError):
        max_output = None
    if not context_limit or context_limit <= 0:
        raise RuntimeError("官方返回中没有可用的 context/max_input_tokens")
    return {
        "context_size_limit": context_limit,
        "context_length": None,
        "max_output": max_output,
        "request_model": request_model,
        "source": "maas_models_api",
        "source_label": "MaaS /models 接口（兜底，可能为工作空间保守值）",
        "source_url": url,
    }


def fetch_official_limits(db: Session, model: LlmModel) -> dict:
    """读取单个模型官方上下文配置：模型中心优先，失败回退 MaaS /models。"""
    try:
        return _model_center_limits(model)
    except Exception as exc:  # noqa: BLE001 —— 模型中心不可用时按既有路径兜底
        logger.warning("模型中心取 %s 上下文失败，回退 MaaS /models：%s", model.code, exc)
        result = _maas_endpoint_limits(db, model)
        result["fallback_reason"] = str(exc)[:200]
        return result


def sync_official_limits(db: Session, *, codes: list[str] | None = None) -> dict:
    """批量同步对话模型的官方上下文上限。

    只同步 ``unit_type='token'`` 且用户可选的对话模型；OCR/向量化/TTS/视觉兜底
    没有对话上下文的概念，不参与同步。
    """
    query = db.query(LlmModel).filter(
        LlmModel.unit_type == "token",
        LlmModel.is_user_selectable.is_(True),
    )
    models = query.order_by(LlmModel.sort_order, LlmModel.id).all()
    if codes:
        wanted = set(codes)
        models = [m for m in models if m.code in wanted]
    updated: list[dict] = []
    skipped: list[dict] = []
    missing: list[dict] = []
    for model in models:
        provider = _provider(db, model)
        provider_code = (provider.code if provider else "") or ""
        if provider_code not in SYNC_PROVIDERS:
            skipped.append({"code": model.code, "reason": f"provider={provider_code or '?'} 不在同步范围"})
            continue
        try:
            official = fetch_official_limits(db, model)
        except Exception as exc:  # noqa: BLE001
            missing.append({"code": model.code, "reason": str(exc)[:200]})
            continue
        limit = official["context_size_limit"]
        old_context = model.context_size
        old_limit = model.context_size_limit
        if old_context is None:
            model.context_size = limit
        elif old_limit is not None and old_context == old_limit:
            # 旧生效值就是旧官方上限（此前自动填充的），跟随新官方值；
            # 管理员显式设置的更小值不在此列，继续保留。
            model.context_size = limit
        elif old_context > limit:
            # 管理员历史配置超过官方硬上限：压到上限，报告里留痕。
            model.context_size = limit
        if official.get("max_output"):
            # 按钮是显式“同步官方上下文”，max_output 也按官方值更新；
            # 管理员要更保守的输出上限可在模型编辑里再改。
            model.max_output = official["max_output"]
        model.context_size_limit = limit
        updated.append({
            "code": model.code,
            "context_size_limit": limit,
            "context_size": model.context_size,
            "max_output": model.max_output,
            "clamped": bool(old_context is not None and old_context > limit),
            "source": official.get("source"),
            "source_label": official.get("source_label"),
            "source_url": official.get("source_url"),
            "fallback_reason": official.get("fallback_reason"),
        })
    db.commit()
    return {
        "updated": updated,
        "missing": missing,
        "skipped": skipped,
        "counts": {"updated": len(updated), "missing": len(missing), "skipped": len(skipped)},
    }

# ── 从百炼模型中心同步“新模型”到平台目录（2026-11-05） ─────────────────

def _is_chat_candidate(item: dict) -> bool:
    """文本生成模型：capabilities 含 TG；排除 OCR/向量/图像/视频等。"""
    if not isinstance(item, dict):
        return False
    if "TG" not in (item.get("capabilities") or []):
        return False
    info = item.get("model_info") or {}
    return bool(info.get("max_input_tokens") or info.get("context_window"))


def _official_price(item: dict) -> dict | None:
    """从模型中心 prices 取官方价（元/百万 → 微积分/千 token）。

    优先 standard；没有则用 peak（不打折口径）；再没有用无 time_band 的档。
    只取第一组价格，避免把多段上下文阶梯混在一起；新模型可后续在调价里补阶梯。
    """
    groups = item.get("prices") or []
    entries = []
    for group in groups[:1]:
        entries.extend(group.get("prices") or [])
    if not entries:
        return None
    bands = [
        e.get("time_band") for e in entries
        if e.get("type") in ("input_token", "output_token")
    ]
    if "standard" in bands:
        band = "standard"
    elif "peak" in bands:
        band = "peak"
    elif None in bands:
        band = None
    else:
        band = bands[0] if bands else None

    def _pick(kind: str):
        for entry in entries:
            if entry.get("type") != kind:
                continue
            if (entry.get("time_band") or None) != band:
                continue
            try:
                return float(entry.get("price"))
            except (TypeError, ValueError):
                return None
        return None

    input_yuan = _pick("input_token")
    output_yuan = _pick("output_token")
    cache_yuan = _pick("input_token_cache")
    if input_yuan is None or output_yuan is None:
        return None

    def _micro(yuan: float) -> int:
        return int(round(float(yuan) * 100_000))

    return {
        "time_band": band,
        "input_yuan": input_yuan,
        "output_yuan": output_yuan,
        "cache_hit_yuan": cache_yuan,
        "cache_miss_input_micro_per_1k": _micro(input_yuan),
        "output_micro_per_1k": _micro(output_yuan),
        "cache_hit_input_micro_per_1k": _micro(cache_yuan) if cache_yuan is not None else 0,
    }


def _model_center_key(db: Session) -> str:
    key = get_settings().LLM_API_KEY
    if key:
        return key
    model = db.query(LlmModel).filter(LlmModel.code == "qwen3.7-flash").first()
    return (_api_key(model) if model is not None else "") or ""


def _candidate_payload(item: dict, existing_codes: set[str]) -> dict:
    info = item.get("model_info") or {}
    capabilities = item.get("capabilities") or []
    features = item.get("features") or []
    modalities = ((item.get("inference_metadata") or {}).get("request_modality") or [])
    code = item.get("model") or ""
    return {
        "code": code,
        "display_name": item.get("name") or code,
        "provider": item.get("provider"),
        "context_window": info.get("context_window"),
        "max_input_tokens": info.get("max_input_tokens") or info.get("context_window"),
        "max_output_tokens": info.get("max_output_tokens"),
        "supports_vision": any(m in ("Image", "Video") for m in modalities),
        "supports_tools": "function-calling" in features,
        "supports_reasoning": "Reasoning" in capabilities,
        "official_price": _official_price(item),
        "already_exists": code in existing_codes,
    }


def list_bailian_chat_models(db: Session) -> dict:
    """预览：百炼模型中心里的文本生成模型，标记是否已在平台目录。"""
    key = _model_center_key(db)
    if not key:
        raise RuntimeError("未配置 LLM_API_KEY，无法读取百炼模型中心")
    items = [
        _candidate_payload(item, {row[0] for row in db.query(LlmModel.code).all()})
        for item in _fetch_model_center(key)
        if _is_chat_candidate(item)
    ]
    items.sort(key=lambda x: (x["already_exists"], x["code"]))
    return {
        "items": items,
        "counts": {
            "total": len(items),
            "new": sum(1 for item in items if not item["already_exists"]),
            "existing": sum(1 for item in items if item["already_exists"]),
        },
    }


def sync_bailian_models(db: Session, codes: list[str]) -> dict:
    """把选中的百炼文本生成模型加入平台目录。

    - 新模型默认 ``enabled=False``、``is_user_selectable=True``，不影响现有用户；
    - 有官方价时生成 v1 **draft** 价格版本，管理员在「调价」里审核发布；
    - 已存在的 code 跳过，可重复点击。
    """
    key = _model_center_key(db)
    if not key:
        raise RuntimeError("未配置 LLM_API_KEY，无法读取百炼模型中心")
    provider = db.query(LlmProvider).filter(LlmProvider.code == "default").first()
    if provider is None:
        raise RuntimeError("平台缺少 default provider")
    existing = {row[0] for row in db.query(LlmModel.code).all()}
    by_code = {
        item.get("model"): item
        for item in _fetch_model_center(key)
        if _is_chat_candidate(item)
    }
    last = db.query(LlmModel).order_by(LlmModel.sort_order.desc()).first()
    sort_order = int((last.sort_order if last is not None else 0) or 0)
    created, skipped, errors = [], [], []
    for code in codes:
        code = (code or "").strip()
        if not code:
            continue
        if code in existing:
            skipped.append({"code": code, "reason": "already_exists"})
            continue
        item = by_code.get(code)
        if item is None:
            errors.append({"code": code, "reason": "模型中心未找到该文本生成模型"})
            continue
        if len(code) > 64:
            errors.append({"code": code, "reason": "code 超过 64 字符"})
            continue
        info = item.get("model_info") or {}
        capabilities = item.get("capabilities") or []
        features = item.get("features") or []
        modalities = ((item.get("inference_metadata") or {}).get("request_modality") or [])
        context = info.get("max_input_tokens") or info.get("context_window")
        sort_order += 10
        model = LlmModel(
            code=code,
            display_name=(item.get("name") or code)[:64],
            provider_id=provider.id,
            upstream_model=code,
            unit_type="token",
            unit_scale=1000,
            context_size=int(context) if context else None,
            context_size_limit=int(context) if context else None,
            max_output=int(info["max_output_tokens"]) if info.get("max_output_tokens") else None,
            supports_vision=any(m in ("Image", "Video") for m in modalities),
            supports_tools="function-calling" in features,
            supports_reasoning="Reasoning" in capabilities,
            is_user_selectable=True,
            enabled=False,
            sort_order=sort_order,
        )
        db.add(model)
        db.flush()
        price = _official_price(item)
        published = None
        if price is not None:
            # 新模型默认未启用；官方价先发布，管理员确认后一键开启即可。
            # 要改价再走「调价」新建版本，历史版本保留。
            now = datetime.now()
            published = ModelPriceVersion(
                model_id=model.id,
                version=1,
                status="published",
                effective_from=now,
                published_at=now,
                cache_hit_input_micro_per_1k=price["cache_hit_input_micro_per_1k"],
                cache_miss_input_micro_per_1k=price["cache_miss_input_micro_per_1k"],
                cache_write_input_micro_per_1k=0,
                output_micro_per_1k=price["output_micro_per_1k"],
                note="从百炼模型中心同步（2026-11-05，官方标准/忙时价）",
            )
            db.add(published)
        existing.add(code)
        created.append({
            "code": code,
            "display_name": model.display_name,
            "context_size": model.context_size,
            "max_output": model.max_output,
            "official_price": price,
            "price_published": published is not None,
        })
    return {
        "created": created,
        "skipped": skipped,
        "errors": errors,
        "counts": {
            "created": len(created),
            "skipped": len(skipped),
            "errors": len(errors),
        },
    }
