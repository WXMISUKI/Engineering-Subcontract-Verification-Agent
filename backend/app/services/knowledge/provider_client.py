"""WeKnora 管理面只读客户端、错误分层与运行时 Secret 引用解析（P2，2026-09-28）。

本模块只做**只读**的三件事：解析 Secret 引用、拉 KB 列表/详情、做连通性健康检查。
写入/上传链路另有独立端口（P7），不得混进来。

四条硬约束（对应 17 号任务包 P2 的「打回」条件）：

1. **不接受硬编码默认 Key。** `secret_ref` 只允许 `env://<VAR>` 形式，运行时从进程环境
   或已声明的项目凭据配置解析；独立版检索密钥可通过环境变量指定外置凭据文件。
   解析不到就抛 `SecretResolutionError`，不退回任何内置默认值。
2. **不回显凭据。** 异常文本、日志、返回结构里都不得出现 Key 或 `Authorization`/`X-API-Key`
   的值；远端响应正文只允许以**脱敏后的**摘要形式出现。
3. **远端不可达不得变成空列表。** 401/403/429/5xx/超时/坏 JSON 各有独立的异常类型与
   `code`（见 18 号文档 §3 错误码表）。返回 `[]` 只代表「远端确实说没有」。
4. **缓存只缓存 KB 元数据，绝不缓存 Key。** 缓存是模块级且有 TTL，配 `reset_provider_cache()`
   在密钥轮换后显式失效。

错误分层为什么必须存在而不只是「抛个异常」：上层要据此决定三态语义（`undetermined`
而不是 `miss`）、决定是否可重试、以及给管理员一个能行动的文案。压成一种异常，这三件事
全部丢失。
"""
from __future__ import annotations

import logging
import os
import re
import time
import math
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

import httpx

from ...config import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- 脱敏（18 号文档 D4）

# 大小写不敏感地按**子串**命中；键名里出现任一片段即视为敏感。
_SENSITIVE_KEY_FRAGMENTS = ("token", "key", "authorization", "secret", "password")

MAX_REDACT_DEPTH = 3
MAX_REDACT_FIELDS = 50
MAX_REDACT_STRING = 512
_MASK = "***"

# 形如 `X-API-Key: abc` / `Authorization: Bearer abc` / 裸 `Bearer abc` 的行内凭据。
# 必须把 `Bearer ` 前缀一起吃进匹配：否则 `Authorization: Bearer <token>` 只会掩掉
# "Bearer" 这个词本身，真正的令牌原样留在文本里（这是实测踩到的形态）。
_INLINE_CREDENTIAL = re.compile(
    r"(?i)\b(x-api-key|api[-_]?key|authorization|bearer|secret|token|password)\b"
    r"(\s*[:=]\s*|\s+)(?:bearer\s+)?(\S+)"
)


def is_sensitive_key(key: Any) -> bool:
    name = str(key).lower()
    return any(fragment in name for fragment in _SENSITIVE_KEY_FRAGMENTS)


def redact_text(value: Any, *, limit: int = MAX_REDACT_STRING) -> str:
    """把任意字符串/对象转成**不含凭据**的短文本（用于异常消息与日志）。"""
    text = value if isinstance(value, str) else str(value)
    text = _INLINE_CREDENTIAL.sub(lambda m: f"{m.group(1)}{m.group(2)}{_MASK}", text)
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


def redact(value: Any, *, _depth: int = 0) -> Any:
    """递归脱敏 + 限额。返回值保证可 JSON 序列化（不可序列化的值转 `str` 再截断）。

    - 深度 ≥ `MAX_REDACT_DEPTH` 时不再下钻，直接替换为掩码（防止构造性深嵌套把栈打爆）；
    - 每层容器最多 `MAX_REDACT_FIELDS` 个元素，超出丢弃并置 `__truncated__`；
    - 字符串超长截断。
    """
    if _depth >= MAX_REDACT_DEPTH:
        return _MASK
    if isinstance(value, Mapping):
        result: Dict[str, Any] = {}
        truncated = False
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_REDACT_FIELDS:
                truncated = True
                break
            if is_sensitive_key(key):
                result[str(key)] = _MASK
            else:
                result[str(key)] = redact(item, _depth=_depth + 1)
        if truncated:
            result["__truncated__"] = True
        return result
    if isinstance(value, (list, tuple, set)):
        result_list: List[Any] = []
        truncated = False
        for index, item in enumerate(value):
            if index >= MAX_REDACT_FIELDS:
                truncated = True
                break
            result_list.append(redact(item, _depth=_depth + 1))
        if truncated:
            result_list.append({"__truncated__": True})
        return result_list
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return redact_text(value)


# ---------------------------------------------------------------- Secret 解析


class SecretResolutionError(RuntimeError):
    """Secret 引用无效或运行时凭据不存在。"""


class SecretResolver:
    """解析部署环境引用；显式映射隔离，默认仅标准 WeKnora 凭据可回读 Settings。"""

    def __init__(self, values: Mapping[str, str] | None = None) -> None:
        self._values = values

    def resolve(self, secret_ref: str) -> str:
        if not secret_ref or not secret_ref.startswith("env://"):
            raise SecretResolutionError(
                "secret_ref 必须以 env:// 开头（数据库只存引用，不存密钥本身）。"
            )
        name = secret_ref.removeprefix("env://").strip()
        if not name or any(
            char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_"
            for char in name
        ):
            raise SecretResolutionError(
                "secret_ref 中的环境变量名只允许字母/数字/下划线。"
            )
        value = (self._values if self._values is not None else os.environ).get(name, "")
        if self._values is None and name == 'WEKNORA_API_KEY':
            path = os.environ.get('WEKNORA_API_KEY_FILE', '').strip()
            if path:
                try:
                    secret_file = Path(path)
                    if not secret_file.is_absolute() or secret_file.stat().st_size > 8192:
                        raise OSError('invalid secret file')
                    value = secret_file.read_text(encoding='utf-8').strip()
                    if not value:
                        raise OSError('empty secret file')
                except (OSError, UnicodeError):
                    raise SecretResolutionError('知识检索凭据文件不可用。') from None
        if self._values is None and not value and name in {"WEKNORA_API_KEY", "WEKNORA_FILE_API_KEY"}:
            # Pydantic 的 env_file 不注入 os.environ；管理面与检索工厂读取同一配置源。
            # 只允许这两个已声明的凭据名，且文件凭据不能借用检索凭据。
            try:
                value = getattr(get_settings(), name, "")
            except Exception:
                # Settings 校验异常可能包含敏感输入；固定文案且隐藏原始异常链。
                raise SecretResolutionError("运行时凭据配置读取失败，请检查部署配置。") from None
        if not value:
            # 注意措辞：只报变量**名**，绝不报值，也不回退到任何默认 Key。
            # 也**不**回显 `secret_ref` 本身：该异常的消息会被管理面 API 直接转成
            # 响应体文案，带上 `secret_ref=env://X` 就等于把「凭据引用」这一实现细节
            # 送到前端（P5 浏览器走查实测命中，见 19 号报告 §4）。
            raise SecretResolutionError(
                f"运行时凭据缺失：环境变量 {name} 未设置或为空。"
            )
        return value


# ---------------------------------------------------------------- 错误分层


class ProviderError(RuntimeError):
    """Provider 侧错误的基类。`code` 与 18 号文档 §3 错误码表一一对应。"""

    code = "provider_error"
    http_status = 502
    retryable = False

    def __init__(self, message: str, *, detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        # detail 必须已经过 redact_text；这里再兜一次，防止调用方漏处理。
        self.detail = redact_text(detail) if detail else None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "detail": self.detail,
        }


class ProviderUnreachableError(ProviderError):
    code = "provider_unreachable"
    http_status = 503
    retryable = True


class ProviderTimeoutError(ProviderError):
    code = "provider_timeout"
    http_status = 504
    retryable = True


class ProviderAuthError(ProviderError):
    code = "provider_auth_failed"
    http_status = 502
    retryable = False


class ProviderRateLimitError(ProviderError):
    code = "provider_rate_limited"
    http_status = 503
    retryable = True


class ProviderServerError(ProviderError):
    code = "provider_server_error"
    http_status = 502
    retryable = True


class ProviderProtocolError(ProviderError):
    """非 JSON、缺字段、结构不符。"""

    code = "provider_bad_response"
    http_status = 502
    retryable = False


class ProviderFileTooLargeError(ProviderError):
    """原件超过调用方明示的内存读取上限。"""

    code = "provider_file_too_large"
    http_status = 413


class ProviderNotFoundError(ProviderError):
    code = "provider_not_found"
    http_status = 404
    retryable = False


class ProviderBackendNotReadyError(ProviderError):
    """该 provider_type 的后端**尚未接入**（骨架期占位客户端）。

    第十一节（2026-10-16）：乐享（腾讯乐享）接口在等业主 AppKey/AppSecret，管理面先立
    接入点。它**不是**「不可达」也不是「认证失败」——远端根本没被请求过，所以归
    `undetermined`（缺信息），文案要指向下一步动作，不得静默假成功（AGENTS.md 三态纪律）。
    """

    code = "provider_backend_not_ready"
    http_status = 501  # 对外的建议映射：该能力尚未实现
    retryable = False


def _remote_message(response: httpx.Response) -> str:
    """从远端响应里取一句可读的错误说明，**并强制脱敏**。

    只取 `message`/`error`/`detail` 字段，且单字段长度受限；不整段回显响应体，
    因为响应体可能把请求头（含 Key）原样带回来。
    """
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 —— 坏 JSON 时退回状态行
        return redact_text(response.reason_phrase or "")
    if isinstance(body, Mapping):
        for key in ("message", "error", "detail", "msg"):
            if body.get(key):
                return redact_text(body[key], limit=200)
    return ""


def raise_for_provider_status(response: httpx.Response, *, context: str = "") -> None:
    """把非 2xx 响应映射成上表的异常。**公开**：P4 的并发查询客户端复用同一映射，
    避免「管理面一套错误码、检索面另一套」的双口径。
    """
    status = response.status_code
    if status < 400:
        return
    detail = _remote_message(response)
    prefix = f"{context} " if context else ""

    # 401/403 分开保留：前者是凭据不对，后者是权限不足，运维处置完全不同。
    if status in (401, 403):
        raise ProviderAuthError(
            f"{prefix}远端拒绝访问（HTTP {status}）。请确认 API Key 有效且具备所需权限。",
            detail=f"HTTP {status} {detail}".strip(),
        )
    if status == 429:
        raise ProviderRateLimitError(
            f"{prefix}远端限流（HTTP 429），可稍后重试。", detail=detail
        )
    if 500 <= status:
        raise ProviderServerError(
            f"{prefix}远端服务错误（HTTP {status}）。", detail=detail
        )
    if status == 404:
        raise ProviderNotFoundError(f"{prefix}远端返回 404（目标不存在）。", detail=detail)
    raise ProviderProtocolError(
        f"{prefix}远端返回未预期状态 HTTP {status}。", detail=detail
    )


# ---------------------------------------------------------------- KB 元数据


@dataclass(frozen=True)
class KnowledgeBaseSummary:
    external_kb_id: str
    name: str
    description: str
    status: str
    knowledge_count: int | None
    chunk_count: int | None
    raw_metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class KnowledgeBaseList:
    items: List[KnowledgeBaseSummary]
    total: int
    page: int | None = None
    page_size: int | None = None


@dataclass(frozen=True)
class KnowledgeFileSummary:
    """某知识库内一篇文件的元数据（勾选区的数据来源）。"""

    knowledge_id: str
    file_name: str
    file_type: str
    file_size: int | None
    parse_status: str
    raw_metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class KnowledgeList:
    items: List[KnowledgeFileSummary]
    total: int
    page: int | None = None
    page_size: int | None = None


def _file_parse_status(item: Mapping[str, Any]) -> str:
    """远端 parse_status → 归一状态（仅用于**展示**，不得作为可用性判据）。

    开发文档 §5.1 第 3 条：实测全库 282 篇中 123 篇 parse_status=processing，
    含前一天上传的正式语料，分块与向量索引其实已完成、检索正常。故这里的状态只喂
    给前端做徽标，判断「能否召回」只能实发一次检索。
    """
    explicit = item.get("parse_status") or item.get("status")
    if explicit in {"completed", "success", "done", "available", "ready"}:
        return "ready"
    if explicit in {"processing", "parsing", "pending", "indexing"}:
        return "processing"
    if explicit in {"failed", "error"}:
        return "failed"
    return "unknown"


def _file_summary(item: Mapping[str, Any]) -> KnowledgeFileSummary:
    knowledge_id = str(
        item.get("id") or item.get("knowledge_id") or item.get("file_id") or ""
    )
    if not knowledge_id:
        raise ProviderProtocolError(
            "远端返回的文件缺少 id 字段，无法用于勾选。",
            detail=redact_text(sorted(item.keys())),
        )
    name = str(
        item.get("file_name") or item.get("filename") or item.get("name")
        or item.get("title") or ""
    )
    return KnowledgeFileSummary(
        knowledge_id=knowledge_id,
        file_name=name,
        file_type=str(item.get("file_type") or item.get("type") or ""),
        file_size=item.get("file_size") or item.get("size"),
        parse_status=_file_parse_status(item),
        raw_metadata=redact({k: v for k, v in item.items() if k != "id"}),
    )


def _status(item: Mapping[str, Any]) -> str:
    explicit = item.get("status") or item.get("vector_store_status")
    if explicit in {"available", "ready", "completed"}:
        return "ready"
    if explicit in {"processing", "indexing", "pending"} or item.get("is_processing"):
        return "processing"
    if explicit in {"failed", "error"}:
        return "failed"
    return "unknown"


def _summary(item: Mapping[str, Any]) -> KnowledgeBaseSummary:
    external_id = str(
        item.get("id") or item.get("kb_id") or item.get("knowledge_base_id") or ""
    )
    if not external_id:
        raise ProviderProtocolError(
            "远端返回的知识库缺少 id 字段，无法建立绑定。",
            detail=redact_text(sorted(item.keys())),
        )
    return KnowledgeBaseSummary(
        external_kb_id=external_id,
        name=str(item.get("name") or ""),
        description=str(item.get("description") or ""),
        status=_status(item),
        knowledge_count=item.get("knowledge_count"),
        chunk_count=item.get("chunk_count"),
        # D4：整个原始元数据递归脱敏，不是只过滤顶层 4 个精确小写键。
        raw_metadata=redact({k: v for k, v in item.items() if k != "id"}),
    )


# 短缓存：只缓存 KB 元数据。键里**不含** Key 值（只含 secret_ref 引用名）。
_METADATA_CACHE: Dict[tuple, tuple[float, KnowledgeBaseList]] = {}
DEFAULT_METADATA_TTL_SECONDS = 60.0


def reset_provider_cache() -> None:
    """清空 KB 元数据短缓存（密钥轮换 / 测试隔离 / 管理员手动刷新后调用）。"""
    _METADATA_CACHE.clear()


def cached_metadata_keys() -> tuple:
    """只读快照，供预检脚本与测试断言缓存命中情况（不暴露内部可变对象）。"""
    return tuple(sorted(_METADATA_CACHE.keys()))


def _failed_health_report(exc: ProviderError) -> Dict[str, Any]:
    """远端在第一层就失败时的固定报表：后续层一律 `undetermined`（没跑，不能算通过）。"""
    return {
        "verdict": "undetermined",
        "ok": False,
        "layers": [
            {"layer": "tcp_http", "verdict": "failed", **exc.as_dict()},
            {"layer": "auth", "verdict": "undetermined"},
            {"layer": "kb_list", "verdict": "undetermined"},
        ],
        "error_code": exc.code,
    }


class WeKnoraAdminClient:
    """WeKnora 管理面**只读**客户端：列表 / 详情 / 健康检查。

    删除、创建、上传等高风险管理操作不在此处（P7 另立契约并单独授权）。
    """

    def __init__(
        self,
        base_url: str,
        *,
        secret_ref: str,
        tenant_ref: Optional[str] = None,
        secret_resolver: SecretResolver | None = None,
        timeout_seconds: float = 15.0,
        metadata_ttl_seconds: float = DEFAULT_METADATA_TTL_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        if not self.base_url:
            raise ProviderProtocolError("base_url 未配置，无法建立 Provider 客户端。")
        self.secret_ref = secret_ref
        self.tenant_ref = tenant_ref
        self._metadata_ttl = max(0.0, float(metadata_ttl_seconds))
        # 解析在构造时完成：拿不到凭据就根本不该有这个客户端（fail-fast，不退回默认 Key）。
        self._secret = (secret_resolver or SecretResolver()).resolve(secret_ref)
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(float(timeout_seconds)),
            headers={"X-API-Key": self._secret},
            transport=transport,
        )

    # ---------- 基础设施 ----------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "WeKnoraAdminClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """统一出口：把 httpx 的传输层异常映射成上表的异常类型。

        `raise_for_status()` 与 httpx 自带异常一律**不外泄**：它们的文本里可能带 URL 与
        请求头，而且分层信息会被抹平成一种异常。
        """
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                f"远端请求超时（{path}）。", detail=redact_text(type(exc).__name__)
            ) from exc
        except httpx.TransportError as exc:
            raise ProviderUnreachableError(
                f"远端不可达（{path}）。", detail=redact_text(type(exc).__name__)
            ) from exc
        raise_for_provider_status(response, context=f"{method} {path}")
        return response

    def _json(self, response: httpx.Response) -> Mapping[str, Any]:
        try:
            body = response.json()
        except Exception as exc:  # noqa: BLE001
            raise ProviderProtocolError(
                "远端返回的不是合法 JSON。", detail=redact_text(type(exc).__name__)
            ) from exc
        if not isinstance(body, Mapping):
            raise ProviderProtocolError(
                "远端返回的 JSON 顶层不是对象。",
                detail=redact_text(type(body).__name__),
            )
        if body.get("success") is False:
            # HTTP 200 may still carry an explicit business failure. Do not turn
            # it into an empty catalog or expose an untrusted remote message.
            raise ProviderProtocolError("远端明确报告操作失败。")
        return body

    # ---------- 只读能力 ----------

    def list_knowledge_bases(
        self, *, page: int = 1, page_size: int = 100, use_cache: bool = True
    ) -> KnowledgeBaseList:
        """列出当前凭据可见的知识库。

        失败**抛异常**，绝不返回空列表——空列表的含义是「远端确实说没有 KB」，
        把不可达混进这个语义会让管理员看到「一个库都没有」并去建重复的库。
        """
        cache_key = (self.base_url, self.tenant_ref, self.secret_ref, page, page_size)
        now = time.monotonic()
        if use_cache and self._metadata_ttl > 0:
            cached = _METADATA_CACHE.get(cache_key)
            if cached is not None and cached[0] > now:
                return cached[1]

        body = self._json(
            self._request(
                "GET", "/knowledge-bases", params={"page": page, "page_size": page_size}
            )
        )
        raw_items = body.get("data")
        if raw_items is None:
            raw_items = body.get("knowledge_bases") or []
        if not isinstance(raw_items, list):
            raise ProviderProtocolError(
                "远端返回的 KB 列表结构不符（data 不是数组）。",
                detail=redact_text(type(raw_items).__name__),
            )
        items = [_summary(item) for item in raw_items if isinstance(item, Mapping)]
        result = KnowledgeBaseList(
            items=items,
            total=int(body.get("total") or len(items)),
            page=body.get("page", page),
            page_size=body.get("page_size", page_size),
        )
        if self._metadata_ttl > 0:
            _METADATA_CACHE[cache_key] = (now + self._metadata_ttl, result)
        return result

    def get_knowledge_base(self, external_kb_id: str) -> KnowledgeBaseSummary:
        body = self._json(self._request("GET", f"/knowledge-bases/{external_kb_id}"))
        payload = body.get("data") or body
        if not isinstance(payload, Mapping):
            raise ProviderProtocolError("远端返回的 KB 详情结构不符。")
        return _summary(payload)

    def fetch_kb_file(self, external_kb_id: str, reference: str) -> tuple[bytes, str]:
        """按 KB 取原始文件字节（纯图页依据取图用）。

        `GET /knowledge-bases/{id}/files?file_path=<provider://…>`：WeKnora 的 KB 范围文件代理，
        会校验「调用者租户 + 该文件确实被本 KB 引用」，因此比裸存储路径安全。
        `reference` 收 `resource://<handle>` 形态（同一张图在 chunk 正文里就是这个 handle）。

        ⚠️ 该路由**不接受 KB 受限的 API key**（见 `AllowFileServeAPIKey`），调用方必须用
        `WEKNORA_FILE_API_KEY`（非受限 retrieve）构造客户端——不要拿检索 key 调它。
        失败按传输层异常上抛（ProviderError 子类），由调用方映射 HTTP；绝不返回空字节冒充成功。
        """
        response = self._request(
            "GET", f"/knowledge-bases/{external_kb_id}/files", params={"file_path": reference}
        )
        content_type = (response.headers.get("content-type") or "application/octet-stream")
        return response.content, content_type.split(";")[0].strip()

    def fetch_knowledge_original(
        self, external_kb_id: str, knowledge_id: str, *, max_bytes: int,
        overall_timeout_seconds: float | None = None,
    ) -> tuple[bytes, str]:
        """核对文件当前所属库，再有界读取原件；不跟随重定向、不缓存原件。

        WeKnora routes_knowledge.go 的 /knowledge/:id 与 /:id/download 均支持
        retrieve API key；原件不走只允许图片资源的 KB files 代理。权限仍由调用方
        按当前用户和绑定校验，这里额外阻止文件已被移动到另一个 KB 的情况。
        """
        if (not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0
                or not isinstance(knowledge_id, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]+", knowledge_id)
                or not isinstance(external_kb_id, str) or not external_kb_id):
            raise ProviderProtocolError("原件读取范围或大小限制无效。")
        if overall_timeout_seconds is not None and (
                isinstance(overall_timeout_seconds, bool)
                or not isinstance(overall_timeout_seconds, (int, float))
                or not math.isfinite(overall_timeout_seconds) or overall_timeout_seconds <= 0):
            raise ProviderProtocolError("原件读取时间限制无效。")
        deadline = None if overall_timeout_seconds is None else time.monotonic() + overall_timeout_seconds
        def remaining():
            if deadline is None:
                return None
            seconds = deadline - time.monotonic()
            if seconds <= 0:
                raise ProviderTimeoutError("原件读取超时。")
            return seconds
        detail_kwargs = {} if deadline is None else {"timeout": remaining()}
        detail_response = self._request("GET", f"/knowledge/{knowledge_id}", follow_redirects=False, **detail_kwargs)
        if detail_response.status_code != 200:
            raise ProviderProtocolError("原件详情返回非预期状态。")
        body = self._json(detail_response)
        item = body.get("data")
        if (not isinstance(item, Mapping) or item.get("id") != knowledge_id
                or item.get("knowledge_base_id") != external_kb_id):
            raise ProviderProtocolError("原件与授权知识库不一致。")
        try:
            download_kwargs = {} if deadline is None else {"timeout": remaining()}
            with self._client.stream("GET", f"/knowledge/{knowledge_id}/download",
                                     follow_redirects=False, **download_kwargs) as response:
                raise_for_provider_status(response, context="原件读取")
                if response.status_code != 200:
                    raise ProviderProtocolError("原件下载返回非预期状态。")
                declared = response.headers.get("content-length")
                if declared is not None:
                    if not re.fullmatch(r"[0-9]+", declared):
                        raise ProviderProtocolError("原件大小声明无效。")
                    if int(declared) > max_bytes:
                        raise ProviderFileTooLargeError("原件超过本次读取大小上限。")
                data = bytearray()
                for chunk in response.iter_bytes():
                    remaining()
                    if len(data) + len(chunk) > max_bytes:
                        raise ProviderFileTooLargeError("原件超过本次读取大小上限。")
                    data.extend(chunk)
                if (declared is not None and not response.headers.get("content-encoding")
                        and len(data) != int(declared)):
                    raise ProviderProtocolError("原件读取不完整。")
                content_type = response.headers.get("content-type") or "application/octet-stream"
                remaining()
                return bytes(data), content_type.split(";")[0].strip()
        except httpx.TimeoutException:
            raise ProviderTimeoutError("原件读取超时。") from None
        except httpx.HTTPError:
            raise ProviderUnreachableError("原件读取连接异常。") from None

    def list_knowledge(
        self,
        external_kb_id: str,
        *,
        keyword: str = "",
        page: int = 1,
        page_size: int = 50,
    ) -> KnowledgeList:
        """列出某知识库内的文件（勾选区数据源，知识库接入 §6.2）。

        与 `list_knowledge_bases` 同款纪律：失败**抛异常**（由 `_request` 统一映射），
        绝不返回空列表——把「远端不可达」混进「这个库没有文件」会让用户以为语料丢了。

        `keyword` 透传给服务端（`GET .../knowledge?keyword=`）——WeKnora 的列文件接口
        **原生支持关键字搜索**，不要在调用方做本地过滤；而列知识库接口无此参数
        （开发文档 §5.1），那条走前端本地过滤。
        """
        params: Dict[str, Any] = {"page": page, "page_size": page_size}
        if keyword and keyword.strip():
            params["keyword"] = keyword.strip()
        body = self._json(
            self._request(
                "GET", f"/knowledge-bases/{external_kb_id}/knowledge", params=params
            )
        )
        raw_items = body.get("data")
        if raw_items is None:
            raw_items = body.get("knowledge") or body.get("items") or []
        if not isinstance(raw_items, list):
            raise ProviderProtocolError(
                "远端返回的文件列表结构不符（data 不是数组）。",
                detail=redact_text(type(raw_items).__name__),
            )
        items = [_file_summary(item) for item in raw_items if isinstance(item, Mapping)]
        return KnowledgeList(
            items=items,
            total=int(body.get("total") or len(items)),
            page=body.get("page", page),
            page_size=body.get("page_size", page_size),
        )

    def health_check(self, *, probe_kb_id: Optional[str] = None) -> Dict[str, Any]:
        """分层健康检查：**TCP/HTTP → 认证 → 权限（KB 列表）**，逐层给结论。

        为什么分层而不是一个 boolean：三层的处置完全不同——连不上要查网络/服务，
        401 要查 Key，能列但不能检索要查该 Key 的 `retrieve` 权限。压成
        「健康/不健康」会让运维只能靠试。
        """
        layers: List[Dict[str, Any]] = []

        try:
            response = self._client.get(
                "/knowledge-bases", params={"page": 1, "page_size": 1}
            )
        except httpx.TimeoutException as exc:
            return _failed_health_report(
                ProviderTimeoutError(
                    "远端请求超时。", detail=redact_text(type(exc).__name__)
                )
            )
        except httpx.TransportError as exc:
            return _failed_health_report(
                ProviderUnreachableError(
                    "远端不可达。", detail=redact_text(type(exc).__name__)
                )
            )

        layers.append(
            {
                "layer": "tcp_http",
                "verdict": "ok" if response.status_code < 500 else "failed",
                "http_status": response.status_code,
            }
        )

        if response.status_code in (401, 403):
            layers.append(
                {"layer": "auth", "verdict": "failed", "http_status": response.status_code}
            )
            layers.append({"layer": "kb_list", "verdict": "undetermined"})
            return {
                "verdict": "undetermined",
                "ok": False,
                "layers": layers,
                "error_code": ProviderAuthError.code,
            }
        if response.status_code >= 400:
            layers.append(
                {"layer": "auth", "verdict": "undetermined", "http_status": response.status_code}
            )
            layers.append({"layer": "kb_list", "verdict": "undetermined"})
            return {
                "verdict": "undetermined",
                "ok": False,
                "layers": layers,
                "error_code": ProviderServerError.code,
            }

        layers.append({"layer": "auth", "verdict": "ok"})

        try:
            listed = self.list_knowledge_bases(page=1, page_size=1, use_cache=False)
        except ProviderError as exc:
            layers.append({"layer": "kb_list", "verdict": "failed", **exc.as_dict()})
            return {
                "verdict": "undetermined",
                "ok": False,
                "layers": layers,
                "error_code": exc.code,
            }

        layers.append(
            {
                "layer": "kb_list",
                "verdict": "ok" if listed.total > 0 else "empty",
                "total": listed.total,
            }
        )
        probe_layer: Dict[str, Any] = {"verdict": "not_run"}
        if probe_kb_id:
            probe_layer = self._probe_search(probe_kb_id)
            layers.append({"layer": "retrieve", **probe_layer})

        # 三态（与知识健康自检同口径）：ok / empty / undetermined。
        # 「能列但一个库都没有」是 empty，是**可行动**的状态（去建库），不是失败；
        # 但绝不能把 undetermined 算成 ok ——那正是历史上「全部不可判定却报可用 ✅」的漏报绿。
        blocking = [layer for layer in layers if layer.get("verdict") == "failed"]
        if blocking or probe_layer["verdict"] == "failed":
            verdict = "undetermined"
        elif listed.total == 0:
            verdict = "empty"
        else:
            verdict = "ok"

        return {
            "verdict": verdict,
            "ok": verdict == "ok",
            "layers": layers,
            "error_code": blocking[0].get("code") if blocking else None,
        }

    def _probe_search(self, external_kb_id: str) -> Dict[str, Any]:
        """探测 `retrieve` 权限：一次最小 hybrid-search。只读，不改远端状态。"""
        try:
            response = self._client.post(
                f"/knowledge-bases/{external_kb_id}/hybrid-search",
                json={"query_text": "健康检查", "match_count": 1},
            )
            raise_for_provider_status(response, context="retrieve 探测")
        except ProviderError as exc:
            return {"verdict": "failed", **exc.as_dict()}
        return {"verdict": "ok"}


__all__ = [
    "DEFAULT_METADATA_TTL_SECONDS",
    "KnowledgeBaseList",
    "KnowledgeBaseSummary",
    "ProviderAuthError",
    "ProviderBackendNotReadyError",
    "ProviderError",
    "ProviderFileTooLargeError",
    "ProviderNotFoundError",
    "ProviderProtocolError",
    "ProviderRateLimitError",
    "ProviderServerError",
    "ProviderTimeoutError",
    "ProviderUnreachableError",
    "SecretResolutionError",
    "SecretResolver",
    "WeKnoraAdminClient",
    "cached_metadata_keys",
    "is_sensitive_key",
    "raise_for_provider_status",
    "redact",
    "redact_text",
    "reset_provider_cache",
]
