"""会话/任务级模型绑定的解析、校验与锁定判定（设计 §8）。

三个原则：

1. **只认目录**：可选模型必须 ``enabled=true AND is_user_selectable=true``，
   且存在当前生效的价格版本（否则允许选择就必然 ``price_missing``，等于把
   无价模型卖成免费）。
2. **失败说清原因**：错误带稳定错误码与可读 message，前端按错误码展示，不做
   中文串匹配（设计 §18 第 6 条）。
3. **运行中锁定**：任务进入 running/processing/reviewing/verifying 后禁止改绑定；
   AS 会话 running/awaiting_* 同。完成/失败后允许改，用于下一轮/下一次任务。
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

from sqlalchemy.exc import IntegrityError

from ...models.billing_models import LlmModel, ModelBinding
from .registry import get_active_price_version, get_default_model, get_model, get_settings_row

ALLOWED_SCOPE_TYPES = (
    "as_session",
    "oc_task",
    "sr_conversation",
    "sc_task",
    "bs_task",
)

# scope_type → 人类可读名（错误文案用）
SCOPE_LABELS = {
    "as_session": "会话",
    "oc_task": "开工条件核查任务",
    "sr_conversation": "安全方案审查任务",
    "sc_task": "结构受力验证任务",
    "bs_task": "工程分包审查任务",
}


class BindingError(ValueError):
    """绑定校验失败；``code`` 为稳定错误码，``status`` 为建议 HTTP 状态。"""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def get_binding(db, scope_type: str, scope_id) -> Optional[ModelBinding]:
    if not scope_type or scope_id in (None, ""):
        return None
    return (
        db.query(ModelBinding)
        .filter(
            ModelBinding.scope_type == scope_type,
            ModelBinding.scope_id == str(scope_id),
        )
        .first()
    )


def resolve_bound_code(db, model_code: Optional[str]) -> dict:
    """绑定 code 的统一判定（AS 与 Plus 共用，避免接口与实跑口径冲突）。

    - 无 code            → ``{code: None, reason: 'no_binding'}``
    - 目录不存在         → ``{code: model_code, reason: 'catalog_missing'}``
      （保留原 code，显式不可用；不静默冒充默认）
    - 存在但 enabled=false → ``{code: None, reason: 'model_disabled'}``
      （仅用于展示；实际运行入口必须走 ``require_*`` 并失败，不得静默改模型）
    - enabled            → ``{code: model_code, reason: None}``

    不校验 ``is_user_selectable``：它只控制用户选择器可见性；内部模型（OCR、
    向量等）仍可通过运行时解析。管理员关闭模型走 ``enabled``，见 2026-11-02。
    """
    if not model_code:
        return {"code": None, "reason": "no_binding"}
    model = get_model(db, model_code)
    if model is None:
        return {"code": model_code, "reason": "catalog_missing"}
    if not model.enabled:
        return {"code": None, "reason": "model_disabled"}
    return {"code": model.code, "reason": None}


def effective_model_code(db, scope_type: str, scope_id) -> Optional[str]:
    """绑定模型 → 实际可用的目录 code（停用→None；目录缺失→保留原 code）。"""
    row = get_binding(db, scope_type, scope_id)
    if row is None or not row.model_code:
        return None
    return resolve_bound_code(db, row.model_code)["code"]


def default_model_code(db) -> str:
    model = get_default_model(db)
    if model is not None:
        return model.code
    from .registry import default_model_code as _fallback

    return _fallback()


def require_model_enabled(db, model_code: str) -> LlmModel:
    """运行入口的模型可用性闸门：目录存在且 enabled=true。

    2026-11-02 起 ``enabled=false`` 是全局开关，不再只是“选择器隐藏”：管理员
    关闭后，已绑定会话、任务、runtime 默认模型都必须失败并提示重选；管理员
    账号没有豁免，运行入口不做角色判断。
    """
    model = get_model(db, model_code)
    if model is None:
        raise BindingError(
            "MODEL_NOT_FOUND",
            f"模型 {model_code} 不在模型目录，请重新选择模型",
            409,
        )
    if not model.enabled:
        raise BindingError(
            "MODEL_DISABLED",
            f"模型 {model_code} 已被管理员关闭，请选择其他模型",
            409,
        )
    return model


def require_bound_model_code(db, scope_type: str, scope_id) -> Optional[str]:
    """会话/任务已有绑定时返回 code；已关闭直接失败。

    目录缺失保留原 code（与历史口径一致：调用侧按不可用处理，不冒充默认）；
    ``enabled=false`` 则是管理员主动关闭，必须阻断并提示重选。
    """
    row = get_binding(db, scope_type, scope_id)
    if row is None or not row.model_code:
        return None
    model = get_model(db, row.model_code)
    if model is None:
        return row.model_code
    if not model.enabled:
        raise BindingError(
            "MODEL_DISABLED",
            f"模型 {model.code} 已被管理员关闭，请选择其他模型",
            409,
        )
    return model.code


def require_default_model_code(db) -> str:
    """全局默认模型必须处于开启状态；关闭默认模型前必须先切换默认。"""
    model = get_default_model(db)
    if model is not None:
        if not model.enabled:
            raise BindingError(
                "DEFAULT_MODEL_DISABLED",
                f"默认模型 {model.code} 已被管理员关闭，请先在模型目录切换默认模型",
                409,
            )
        return model.code
    code = default_model_code(db)
    fallback = get_model(db, code) if code else None
    if fallback is not None and not fallback.enabled:
        raise BindingError(
            "DEFAULT_MODEL_DISABLED",
            f"默认模型 {fallback.code} 已被管理员关闭，请先在模型目录切换默认模型",
            409,
        )
    return code


def effective_model_code_or_default(db, scope_type: str, scope_id) -> str:
    """运行期实际模型：任务绑定 > 全局默认（billing_settings.default_model_id）。

    Plus 任务在**未显式选择**时也必须用全局默认，而不是环境变量 ``LLM_MODEL``，
    否则实际请求与台账/预估会各用一套模型（账实不符）。

    绑定模型被管理员关闭时**不静默回退**全局默认：直接抛 ``MODEL_DISABLED``，
    让用户重新选择；这是 2026-11-02 的全局开关口径。
    """
    bound = require_bound_model_code(db, scope_type, scope_id)
    return bound or require_default_model_code(db)


def require_effective_model_code(db, scope_type: str, scope_id) -> str:
    """运行入口的显式命名：与 ``effective_model_code_or_default`` 同一判定。"""
    return effective_model_code_or_default(db, scope_type, scope_id)


def _require_selectable(db, model_code: str) -> tuple[LlmModel, object]:
    code = (model_code or "").strip()
    if not code:
        raise BindingError("MODEL_REQUIRED", "请选择模型", 400)
    model = get_model(db, code)
    if model is None:
        raise BindingError("MODEL_NOT_FOUND", f"模型 {code} 不存在", 400)
    if not model.enabled:
        raise BindingError("MODEL_DISABLED", f"模型 {code} 已停用", 409)
    if not model.is_user_selectable:
        raise BindingError("MODEL_NOT_ALLOWED", f"模型 {code} 未开放给用户选择", 400)
    price = get_active_price_version(db, model.id)
    # 只判「有 price 行」不够：全 None 的空版本等于没定价，绑定后仍会 price_missing。
    if not price_has_value(price):
        raise BindingError("PRICE_VERSION_MISSING", f"模型 {code} 尚未配置生效价格", 409)
    return model, price


def validate_model_choice(db, model_code: str) -> LlmModel:
    """创建入口先校验再落库：避免「会话已建但模型非法」的副作用。"""
    model, _price = _require_selectable(db, model_code)
    return model


def set_binding(
    db,
    *,
    user_type: str,
    user_id: int,
    scope_type: str,
    scope_id,
    model_code: str,
) -> ModelBinding:
    """写入/更新绑定；校验 scope_type 合法且模型可选可计价。"""
    if scope_type not in ALLOWED_SCOPE_TYPES:
        raise BindingError("SCOPE_TYPE_INVALID", f"不支持的 scope_type：{scope_type}", 400)
    if scope_id in (None, ""):
        raise BindingError("SCOPE_ID_REQUIRED", "缺少 scope_id", 400)
    _require_selectable(db, model_code)
    sid = str(scope_id)
    row = get_binding(db, scope_type, sid)
    if row is None:
        row = ModelBinding(
            user_type=user_type,
            user_id=int(user_id),
            scope_type=scope_type,
            scope_id=sid,
            model_code=model_code,
        )
        db.add(row)
    else:
        row.user_type = user_type
        row.user_id = int(user_id)
        row.model_code = model_code
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        # 并发首次写入：另一路已建。SAVEPOINT 已回滚，外层事务仍有效，
        # 这里**不能**再调 db.rollback()（会把调用方刚创建的任务一起回滚）。
        db.expire_all()
        row = get_binding(db, scope_type, sid)
        if row is None:
            raise
        row.user_type = user_type
        row.user_id = int(user_id)
        row.model_code = model_code
        db.flush()
    return row




# ── 运行中锁定 ────────────────────────────────────────────────────────────────

def lock_reason(db, scope_type: str, scope_id) -> Optional[str]:
    """运行中/已进入处理态时返回可读原因；可改时返回 None。

    只做**运行态**判断，不判断归属（归属在路由层用 attribution.is_scope_owner）。
    """
    if scope_id in (None, ""):
        return None
    sid = str(scope_id)
    try:
        if scope_type == "as_session":
            from ...models.models import AsSession

            row = db.get(AsSession, sid)
            if row is not None and row.run_status in (
                "running", "awaiting_permission", "awaiting_external_result",
            ):
                return "会话正在运行，运行中不能切换模型"
        elif scope_type == "oc_task":
            from ...plus.startup_conditions_check.models import OcReviewTask

            row = db.get(OcReviewTask, int(sid))
            if row is not None and row.state == "processing":
                return "开工条件核查正在运行，运行中不能切换模型"
        elif scope_type == "sc_task":
            from ...plus.structural_check.models import ScDocSnapshot

            row = db.get(ScDocSnapshot, int(sid))
            # parsing 只是文档解析（不做任务级 LLM 核查），不锁；verifying 才锁。
            if row is not None and row.status == "verifying":
                return "结构受力验证正在运行，运行中不能切换模型"
        elif scope_type == "sr_conversation":
            from ...plus.safety_review.models import SrDocSnapshot

            row = db.get(SrDocSnapshot, int(sid))
            if row is not None and row.review_status == "reviewing":
                return "安全方案审查正在运行，运行中不能切换模型"
        elif scope_type == "bs_task":
            from ...plus.blockchain_supervision.models import BsTask

            row = db.get(BsTask, int(sid))
            if row is not None and row.state in ("preparing", "running", "review"):
                return "工程分包审查正在运行，运行中不能切换模型"
    except Exception:  # noqa: BLE001 —— fail-closed：无法确认运行态时禁止切换
        logger.warning("运行态判定失败，按锁定处理 scope_type=%s scope_id=%s", scope_type, sid, exc_info=True)
        return "无法确认任务运行状态，暂不允许切换模型"
    return None


# ── 可选模型清单 ──────────────────────────────────────────────────────────────

def price_has_value(price) -> bool:
    """价格版本是否至少有一个可用单价。

    全 None 的「空版本」不算生效价（历史播种可能生成过），否则会把它
    当成已定价展示给用户；这类模型应先由管理员调价。
    """
    if price is None:
        return False
    if price.unit_micro_per_unit is not None:
        return True
    return any(
        value is not None
        for value in (
            price.cache_hit_input_micro_per_1k,
            price.cache_miss_input_micro_per_1k,
            price.cache_write_input_micro_per_1k,
            price.output_micro_per_1k,
            price.total_fallback_micro_per_1k,
        )
    )


def available_models(db) -> list[dict]:
    """用户选择器可见的对话模型（enabled + is_user_selectable + token + 有生效价）。"""
    from . import money

    settings_row = get_settings_row(db)
    rate = int(
        (settings_row.credits_per_yuan if settings_row is not None else None)
        or money.DEFAULT_CREDITS_PER_YUAN
    )
    default = get_default_model(db)
    default_id = default.id if default is not None else None
    rows = (
        db.query(LlmModel)
        .filter(
            LlmModel.enabled.is_(True),
            LlmModel.is_user_selectable.is_(True),
            LlmModel.unit_type == "token",
        )
        .order_by(LlmModel.sort_order.asc(), LlmModel.id.asc())
        .all()
    )
    out: list[dict] = []
    for model in rows:
        price = get_active_price_version(db, model.id)
        if not price_has_value(price):
            continue
        buckets = {
            "cache_hit_input_micro_per_1k": price.cache_hit_input_micro_per_1k,
            "cache_miss_input_micro_per_1k": price.cache_miss_input_micro_per_1k,
            "cache_write_input_micro_per_1k": price.cache_write_input_micro_per_1k,
            "output_micro_per_1k": price.output_micro_per_1k,
        }
        credits = {
            key: (money.credits(value) if value is not None else None)
            for key, value in buckets.items()
        }
        out.append(
            {
                "code": model.code,
                "display_name": model.display_name or model.code,
                "supports_vision": bool(model.supports_vision),
                "supports_tools": bool(model.supports_tools),
                "supports_reasoning": bool(model.supports_reasoning),
                "context_size": model.context_size,
                "max_output": model.max_output,
                "is_default": bool(model.id == default_id),
                "price_version_id": price.id,
                # 微积分/千 token：前端换算展示；同时给积分口径，避免前端重复定义系数。
                "price_micro_per_1k": buckets,
                "price_credits_per_1k": credits,
            }
        )
    return out


def _as_runtime_model_name(db, session_id: str) -> Optional[str]:
    """AS 会话的 runtime 实际模型名；解析不到返回 None（不猜）。"""
    try:
        from ...models.models import Agent, AgentScopeRuntime, AsSession
        from ..as_runtime.session_model import runtime_model_name

        session = db.get(AsSession, session_id)
        if session is None:
            return None
        agent = db.get(Agent, session.agent_id)
        runtime_code = (agent.config or {}).get("runtime_code") if agent is not None else None
        if not runtime_code:
            return None
        runtime = (
            db.query(AgentScopeRuntime)
            .filter(AgentScopeRuntime.code == runtime_code)
            .first()
        )
        if runtime is None:
            return None
        return runtime_model_name(runtime)
    except Exception:  # noqa: BLE001 —— 展示口径失败不阻断读取
        return None


def binding_payload(db, *, scope_type: str, scope_id, user_type: str, user_id: int) -> dict:
    """GET /api/model-bindings 的返回体：原选择 + 实际会用的模型 + 回退原因。

    ``model_code`` 保留用户原选择（即使已停用）供 UI 展示；``effective_model_code``
    必须是实跑/计费真正会用的模型，避免「接口说 flash、实际扣 plus」。
    """
    sid = str(scope_id) if scope_id not in (None, "") else None
    row = get_binding(db, scope_type, sid) if sid else None
    raw = row.model_code if row is not None else None
    resolved = resolve_bound_code(db, raw)
    reason_code = resolved["reason"]
    fallback_reason = reason_code if reason_code in ("model_disabled", "catalog_missing") else None

    if reason_code == "catalog_missing":
        # 显式不可用：保留原 code，不冒充默认。
        effective = resolved["code"]
        effective_source = "binding_unavailable"
    elif reason_code == "model_disabled":
        # 全局开关：实际运行会报 MODEL_DISABLED，不再回退 runtime/默认模型。
        effective = None
        effective_source = "unavailable"
    elif resolved["code"]:
        effective = resolved["code"]
        effective_source = "binding"
    elif scope_type == "as_session":
        runtime_name = _as_runtime_model_name(db, sid) if sid else None
        effective = runtime_name or default_model_code(db)
        effective_source = "runtime_default" if runtime_name else "global_default"
    else:
        effective = default_model_code(db)
        effective_source = "global_default"

    lock = lock_reason(db, scope_type, sid) if sid else None
    return {
        "scope_type": scope_type,
        "scope_id": sid,
        "model_code": raw,
        "effective_model_code": effective,
        "effective_source": effective_source,
        "fallback_reason": fallback_reason,
        "is_default": raw is None,
        "locked": lock is not None,
        "locked_reason": lock,
    }