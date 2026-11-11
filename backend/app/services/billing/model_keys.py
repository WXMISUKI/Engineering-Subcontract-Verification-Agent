"""模型 Key 解析（shared / per_model）的**唯一定义点**。

- ``shared``：``llm_providers.api_key_ref`` 一把 Key 下挂多个模型（默认，旧行为）；
- ``per_model``：优先 ``llm_models.api_key_ref``；未配置回退 provider Key；
- 两者都空：回退 ``settings.LLM_API_KEY``。

只解析 **env/secret 引用名**，绝不把 Key 值返回给前端、写日志或写审计。
无 DB / 计费表缺失 / 解析异常时一律回退 ``LLM_API_KEY`` 并告警，不阻断业务。

安全边界（2026-10-26 Codex 复审 P0）：引用名走**白名单**，不再对 settings 做任意
``getattr``——否则把 ``api_key_ref`` 填成 ``SECRET_KEY`` 就会把 JWT 签名密钥当模型
Key 发给上游。例外（模型显式配置了引用却解析不到值）**不得**静默回退 provider，
否则会把请求发到错误账号；此时 ``source=model_unresolved``、``configured=False``、
``resolve_api_key`` 返回空并告警，由调用方走既有失败路径。
"""

from __future__ import annotations

import logging
import os
import re
from typing import Optional

from ...config import get_settings
from ...models.billing_models import BillingSettings, LlmModel, LlmProvider

logger = logging.getLogger(__name__)

KEY_MODES = ("shared", "per_model")
KEY_REF_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")

# 允许直接引用的固定 settings 字段（同时可作为环境变量名）。只认这些名字，
# 其余 settings 属性（SECRET_KEY / DATABASE_URL / JWT_* / WECHAT_* …）一律拒绝。
ALLOWED_KEY_REFS = frozenset({
    "LLM_API_KEY",
    "OCR_CLOUD_API_KEY",
    "COZE_API_KEY",
    "MIMO_API_KEY",
})
# 允许的私有前缀：只从环境变量读取（Settings 不声明动态字段，避免任意 getattr）。
ALLOWED_KEY_REF_PREFIXES = ("MODEL_KEY_", "PROVIDER_KEY_")
# 显式保留名/前缀：即使将来扩充白名单也绝不接受。
DENIED_KEY_REFS = frozenset({"SECRET_KEY", "DATABASE_URL", "ADMIN_PASSWORD"})
DENIED_KEY_REF_PREFIXES = ("JWT_", "WECHAT_")

# 运行时真正接入统一解析链的 provider。其余 provider（coze / mimo / local 等）
# 的调用点仍直读专用 settings，管理端不能把它们显示成「已配置模型级 Key」。
WIRED_PROVIDER_CODES = frozenset({"default", "dashscope"})

# 同一 (mode, model_code, source) 只告警一次，避免每次构造客户端刷屏。
_warned: set[tuple[str, str, str]] = set()


def is_denied_key_ref(ref: Optional[str]) -> bool:
    """保留名/前缀（JWT_*、WECHAT_*、SECRET_KEY 等）一律拒绝。"""
    if ref in (None, ""):
        return False
    name = str(ref).strip()
    return name in DENIED_KEY_REFS or name.startswith(DENIED_KEY_REF_PREFIXES)


def is_allowed_key_ref(ref: Optional[str]) -> bool:
    """白名单：固定名、或 ``MODEL_KEY_`` / ``PROVIDER_KEY_`` 前缀。"""
    if ref in (None, ""):
        return True
    name = str(ref).strip()
    if is_denied_key_ref(name):
        return False
    return name in ALLOWED_KEY_REFS or name.startswith(ALLOWED_KEY_REF_PREFIXES)


def is_valid_key_ref(ref: Optional[str]) -> bool:
    """空 = 未配置（允许）；非空必须是白名单内的环境变量/Secret 引用名。"""
    if ref in (None, ""):
        return True
    return bool(KEY_REF_PATTERN.match(str(ref))) and is_allowed_key_ref(ref)


def _warn_once(mode: str, model_code: Optional[str], source: str, message: str) -> None:
    key = (mode, model_code or "-", source)
    if key in _warned:
        return
    _warned.add(key)
    logger.warning("[model-keys] %s (mode=%s model=%s source=%s)", message, mode, model_code, source)


def _secret_value(ref: Optional[str]) -> Optional[str]:
    """按引用名取实际 Key：环境变量优先，其次**白名单内**的 settings 字段。

    只接受 ``is_valid_key_ref`` 通过的引用名；绝不 ``getattr(settings, 任意名)``。
    私有前缀（MODEL_KEY_ / PROVIDER_KEY_）只从环境变量读。
    """
    if not ref:
        return None
    name = str(ref).strip()
    if not is_valid_key_ref(name):
        _warn_once("security", None, name, f"拒绝非白名单 Key 引用 {name}")
        return None
    value = os.environ.get(name)
    if value:
        return value
    if name not in ALLOWED_KEY_REFS:
        return None
    try:
        candidate = getattr(get_settings(), name, None)
    except Exception:  # noqa: BLE001
        candidate = None
    return str(candidate) if candidate else None


def _default_api_key() -> Optional[str]:
    """最终兜底：环境变量优先（运行期可注入），其次启动时读取的 settings。"""
    return os.environ.get("LLM_API_KEY") or get_settings().LLM_API_KEY or None


def _mode(db) -> str:
    if db is None:
        return "shared"
    try:
        row = db.query(BillingSettings).filter(BillingSettings.id == 1).first()
        mode = (row.model_key_mode if row is not None else None) or "shared"
        return mode if mode in KEY_MODES else "shared"
    except Exception:  # noqa: BLE001 —— 旧库/无计费表按 shared
        return "shared"


def resolve_model_key_ref(db, model: Optional[LlmModel]) -> dict:
    """返回 Key 归属描述（不含 Key 值）。

    ``source``：provider / model / model_unresolved / provider_fallback / default_fallback。
    ``configured``：本次是否按目录显式配置且引用可解析；回退/未解析一律 false。
    """
    mode = _mode(db)
    model_code = getattr(model, "code", None) if model is not None else None
    if model is None:
        _warn_once(mode, model_code, "default_fallback", "模型不存在，回退默认 LLM_API_KEY")
        return {
            "mode": mode, "model_code": model_code, "ref": None,
            "source": "default_fallback", "configured": False,
            "warning": "模型不在目录，回退默认 LLM_API_KEY",
        }

    provider = None
    try:
        provider = db.get(LlmProvider, model.provider_id) if db is not None else None
    except Exception:  # noqa: BLE001
        provider = None
    provider_ref = getattr(provider, "api_key_ref", None) if provider is not None else None
    model_ref = getattr(model, "api_key_ref", None)

    if mode == "per_model":
        if model_ref:
            configured = _secret_value(model_ref) is not None
            if configured:
                return {
                    "mode": mode, "model_code": model_code, "ref": model_ref,
                    "source": "model", "configured": True, "warning": None,
                }
            # 显式配置了模型 Key 却解析不到值：**不得**静默回退 provider（会把请求
            # 发到错误账号）。返回 model_unresolved，由调用方按既有失败路径报错。
            warning = f"模型 Key 引用 {model_ref} 未解析到值，已停止回退（检查引用名/环境变量）"
            _warn_once(mode, model_code, "model_unresolved", warning)
            return {
                "mode": mode, "model_code": model_code, "ref": model_ref,
                "source": "model_unresolved", "configured": False,
                "warning": warning,
            }
        if provider_ref:
            _warn_once(mode, model_code, "provider_fallback", "模型未配置独立 Key，回退 provider Key")
            return {
                "mode": mode, "model_code": model_code, "ref": provider_ref,
                "source": "provider_fallback", "configured": False,
                "warning": "模型未配置独立 Key，回退 provider",
            }
        _warn_once(mode, model_code, "default_fallback", "模型与 provider 均未配置 Key，回退默认 LLM_API_KEY")
        return {
            "mode": mode, "model_code": model_code, "ref": None,
            "source": "default_fallback", "configured": False,
            "warning": "模型与 provider 均未配置 Key，回退默认 LLM_API_KEY",
        }

    # shared
    if provider_ref:
        configured = _secret_value(provider_ref) is not None
        if not configured:
            _warn_once(mode, model_code, "provider", f"provider Key 引用 {provider_ref} 未解析到值")
        return {
            "mode": mode, "model_code": model_code, "ref": provider_ref,
            "source": "provider", "configured": configured,
            "warning": None if configured else "provider Key 引用未解析到值",
        }
    _warn_once(mode, model_code, "default_fallback", "provider 未配置 Key，回退默认 LLM_API_KEY")
    return {
        "mode": mode, "model_code": model_code, "ref": None,
        "source": "default_fallback", "configured": False,
        "warning": "provider 未配置 Key，回退默认 LLM_API_KEY",
    }


def resolve_api_key(db, model: Optional[LlmModel]) -> Optional[str]:
    """解析实际使用的 Key（仅供构造客户端，禁止外传/记录）。

    - 模型**显式**配置了引用却解析不到：返回空，绝不静默换账号；
    - 只有 provider/默认层的引用不可解析（或本就未配置）时才回退
      ``LLM_API_KEY``，与旧行为一致（云端 OCR 等依赖该兜底）。
    """
    info = resolve_model_key_ref(db, model)
    if info.get("source") == "model_unresolved":
        return None
    if info.get("ref"):
        value = _secret_value(info["ref"])
        if value:
            return value
    return _default_api_key()


def resolve_api_key_for_code(model_code: Optional[str], db=None) -> Optional[str]:
    """运行时入口：按模型编码解析 Key；任何异常回退默认 LLM_API_KEY。"""
    own_session = False
    session = db
    try:
        if session is None:
            from ...database import SessionLocal

            session = SessionLocal()
            own_session = True
        from .registry import get_default_model, get_model

        model = get_model(session, model_code) if model_code else get_default_model(session)
        return resolve_api_key(session, model)
    except Exception:  # noqa: BLE001 —— 无 DB/计费表/任何解析异常都不能阻断业务
        logger.warning("[model-keys] 解析模型 %s 的 Key 失败，回退默认 LLM_API_KEY", model_code, exc_info=True)
        return _default_api_key()
    finally:
        if own_session and session is not None:
            session.close()


def resolve_model_runtime(model_code: Optional[str], db=None) -> dict:
    """按目录 code 解析「请求模型 + Key + base_url」的唯一定义点。

    请求模型统一取目录条目的 ``upstream_model``（缺失时用 ``code``），保证发给
    上游的模型与解析 Key 用的目录条目是同一个；调用方不得再用 ``settings.LLM_MODEL``
    或另一个 code 拼请求，否则 per_model 下会把 A 模型的 Key 发给 B 模型。

    目录找不到该 code / 解析异常时返回 ``available=False``，明确告警，**不回退**
    默认 Key（静默回退会把请求发到错误账号）。
    """
    own_session = False
    session = db
    try:
        if session is None:
            from ...database import SessionLocal

            session = SessionLocal()
            own_session = True
        from .registry import get_model

        model = get_model(session, model_code) if model_code else None
        if model is None:
            logger.warning(
                "[model-keys] 模型目录未找到 %s，运行时不可用（不回退默认 Key）", model_code
            )
            return {
                "available": False, "model_code": model_code, "request_model": None,
                "api_key": None, "base_url": None, "source": "catalog_missing",
                "configured": False, "reason": "catalog_missing",
                "warning": f"模型 {model_code} 不在目录，无法确定请求模型与 Key",
            }
        if not model.enabled:
            # 2026-11-02 全局开关：关闭后不再解析出 Key，任何绕过选择器的调用也会失败。
            request_model = getattr(model, "upstream_model", None) or model.code
            logger.warning("[model-keys] 模型 %s 已被管理员关闭，运行时不可用", model_code)
            return {
                "available": False, "model_code": model.code, "request_model": request_model,
                "api_key": None, "base_url": None, "source": "model_disabled",
                "configured": False, "reason": "model_disabled",
                "warning": f"模型 {model.code} 已被管理员关闭",
            }
        provider = None
        try:
            provider = session.get(LlmProvider, model.provider_id) if model.provider_id else None
        except Exception:  # noqa: BLE001
            provider = None
        info = resolve_model_key_ref(session, model)
        api_key = resolve_api_key(session, model)
        request_model = (getattr(model, "upstream_model", None) or model.code)
        base_url = getattr(provider, "base_url", None) if provider is not None else None
        available = bool(api_key)
        return {
            "available": available,
            "model_code": model.code,
            "request_model": request_model,
            "api_key": api_key,
            "base_url": base_url or None,
            # 2026-11-02：目录里的生效窗口与官方上限，供 Agent 压缩与上下文浮窗共用。
            "context_size": getattr(model, "context_size", None),
            "context_size_limit": getattr(model, "context_size_limit", None),
            "source": info["source"],
            "configured": info["configured"],
            "warning": info.get("warning"),
            "reason": None if available else info["source"],
        }
    except Exception:  # noqa: BLE001 —— 解析失败按不可用处理，不回退他账号
        logger.warning(
            "[model-keys] 解析模型 %s 的运行时端点失败，按不可用处理", model_code, exc_info=True
        )
        return {
            "available": False, "model_code": model_code, "request_model": None,
            "api_key": None, "base_url": None, "source": "resolve_error",
            "configured": False, "reason": "resolve_error",
            "warning": f"模型 {model_code} 运行时端点解析失败",
        }
    finally:
        if own_session and session is not None:
            session.close()


def resolve_client_params(
    model_code: Optional[str],
    *,
    default_model: Optional[str],
    default_base_url: Optional[str],
) -> dict:
    """构造客户端的三元组（请求模型 / base_url / Key）的唯一定义点。

    - 无 ``model_code``：保持旧行为（``LLM_MODEL`` + ``LLM_API_BASE`` + Key 解析）；
    - 有 ``model_code``：走目录 ``resolve_model_runtime``；目录 provider 未配 base_url
      时回退平台端点（default provider 即如此）。请求模型取目录 ``upstream_model``，
      与计费 ``model_code`` 同源，per_model 下不会把 A 的 Key 发给 B。
    """
    if not model_code:
        return {
            "model": default_model,
            "base_url": default_base_url,
            "api_key": resolve_api_key_for_code(default_model),
            "source": "env",
            "available": bool(default_model and resolve_api_key_for_code(default_model)),
        }
    runtime = resolve_model_runtime(model_code)
    return {
        "model": runtime.get("request_model") or model_code,
        "base_url": runtime.get("base_url") or default_base_url,
        "api_key": runtime.get("api_key"),
        "source": runtime.get("source"),
        "available": bool(runtime.get("available")),
    }


def runtime_key_info(db, model: Optional[LlmModel], provider_code: Optional[str] = None) -> dict:
    """管理端展示用：附带「该模型是否真的走统一解析链」。

    未接入统一解析的 provider（coze / mimo / local …）即使配了 ``api_key_ref``
    也不会在运行时生效，状态必须显示 ``not_wired`` 且 ``configured=False``，
    不能对管理员谎报「已配置」。
    """
    info = dict(resolve_model_key_ref(db, model))
    wired = provider_code is None or provider_code in WIRED_PROVIDER_CODES
    info["wired"] = wired
    if not wired:
        info.update(
            source="not_wired",
            configured=False,
            warning="该 provider 的运行时调用尚未接入统一 Key 解析，模型级 Key 不生效",
        )
    return info


def provider_key_status(provider: LlmProvider) -> dict:
    """给管理端展示的 provider Key 状态（不含值）。"""
    ref = getattr(provider, "api_key_ref", None)
    configured = bool(ref and _secret_value(ref))
    return {
        "api_key_ref": ref,
        "key_source": "provider" if ref else "default_fallback",
        "configured": configured,
    }


def clear_model_key_cache() -> None:
    """当前实现每次读 DB（量小），无需缓存；保留幂等入口供管理端调用。"""
    return None