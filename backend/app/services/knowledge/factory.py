"""检索后端工厂（T02，2026-09-25；T11 实例缓存 + T13 多库路由，2026-09-25）。

按 NEW_KNOWLEDGE_BACKEND 配置选择后端（全平台默认开关）：
    weknora —— WeKnoraAdapter（现网默认，T05 落地）
    lexiang —— LexiangAdapter（业主侧腾讯乐享，第十一节骨架期 2026-10-16）
2026-10-13 起支持**按模块粒度覆盖**（NEW_KNOWLEDGE_BACKEND_BY_MODULE）：get_backend(module=…)
先在映射里查该模块，未列入则退回全局开关。缓存仍按后端名，模块只是选名入口。
业务代码通过 get_backend() 获取端口实现，不直接 new 具体后端。工厂是**唯一配置入口**：
适配器构造不再重复读 Settings——测试只需 patch 本工厂的 get_settings 一个点，
避免「别名不跨模块生效」。

2026-10-23：Chroma 后端已从代码中移除（生产 2026-10-23 全局切 weknora）；
`chroma` 不再是合法值，旧 env 会在取后端时显式报错，不静默回退。
"""
from __future__ import annotations

import json
import logging
import math
from contextlib import contextmanager
from dataclasses import dataclass, field
from hashlib import sha256
from threading import Lock
from typing import Any, Dict, Iterator, Optional

from ...config import get_settings
from .port import RetrievalBackendError
from .provider_client import SecretResolutionError, SecretResolver
from .weknora_adapter import WeKnoraAdapter

logger = logging.getLogger(__name__)

_SUPPORTED = ("weknora", "lexiang")

# 按模块粒度开关的**已知模块名**（NEW_KNOWLEDGE_BACKEND_BY_MODULE 的键域）。
# 单点定义：公共层（context.MODULE_*）从这里再导出，避免两处各写一份模块名。
MODULE_SAFETY_REVIEW = "safety_review"
MODULE_AGENTSCOPE = "agentscope"
MODULE_BLOCKCHAIN_SUPERVISION = "blockchain_supervision"
# structural_check（2026-10-23 决策）：结构验算后端按模块显式选择（KB_MAP + FILE key
# 路线）。全局 NEW_KNOWLEDGE_BACKEND 一动，任何 module-less 调用都会跟着走，影响面是
# 隐式的；故把模块名登记进键域，由 structural 的调用点显式传 module。
MODULE_STRUCTURAL_CHECK = "structural_check"
_KNOWN_MODULES = (
    MODULE_SAFETY_REVIEW,
    MODULE_AGENTSCOPE,
    MODULE_BLOCKCHAIN_SUPERVISION,
    MODULE_STRUCTURAL_CHECK,
)

# T11：进程级实例缓存（按后端名各缓存一个实例，键见 _SUPPORTED）。
# 为什么缓存：WeKnoraAdapter 的 failures/last_error 是**实例属性**，若每次调用都 new，
# 降级计数写在马上被回收的对象上，外部永远读不到（护栏形同虚设）。
# 为什么不做「首次构造冻结 settings + 探测结果类级缓存」：那是全仓无重置点的
# 类级状态，跨用例/跨配置无法清理。本缓存在模块级、按后端名可分、可显式清空。
# 仅缓存**无 transport** 的生产实例；transport 注入（测试）永远绕过缓存（见 get_backend）。
_BACKEND_CACHE: Dict[str, Any] = {}


@dataclass(frozen=True)
class ProviderConnection:
    """数据库冻结的连接身份；凭据只保存运行时引用，tenant_ref 暂仅用于隔离审计。"""

    provider_id: int
    provider_type: str
    base_url: str
    secret_ref: str
    tenant_ref: Optional[str] = None
    revision: str = ""


class ProviderBackendNotReadyError(RetrievalBackendError):
    """Provider 未具备独立检索连接能力，不得回退到环境默认连接。"""

    code = "provider_backend_not_ready"


@dataclass(frozen=True)
class _ProviderCacheKey:
    connection: ProviderConnection
    timeout_ms: float
    vector_threshold: float
    keyword_threshold: float
    secret_digest: bytes = field(repr=False)


@dataclass
class _ProviderBackendEntry:
    key: _ProviderCacheKey = field(repr=False)
    backend: WeKnoraAdapter = field(repr=False)
    leases: int = 0
    retired: bool = False


_PROVIDER_BACKEND_LOCK = Lock()
_PROVIDER_BACKEND_CACHE: Dict[int, _ProviderBackendEntry] = {}


def _retire_provider_entry(entry: _ProviderBackendEntry) -> None:
    """调用方持锁；在途租约继续使用旧连接，最后一个归还后关闭。"""
    entry.retired = True
    if entry.leases == 0:
        entry.backend.close()


def reset_provider_backend_cache(provider_id: Optional[int] = None) -> None:
    """退休指定或全部 Provider 连接；不会关闭仍有请求使用的客户端。

    不影响旧 get_backend 缓存。SecretResolver 每次获取读取运行时 Secret；若 Secret 来自
    .env，须另外清理 get_settings 缓存或重启进程才能读到文件修改后的值。
    """
    with _PROVIDER_BACKEND_LOCK:
        provider_ids = list(_PROVIDER_BACKEND_CACHE) if provider_id is None else [provider_id]
        for selected_id in provider_ids:
            entry = _PROVIDER_BACKEND_CACHE.pop(selected_id, None)
            if entry is not None:
                _retire_provider_entry(entry)


@contextmanager
def acquire_provider_backend(
    connection: ProviderConnection,
    *,
    transport: Optional[Any] = None,
    secret_resolver: Optional[SecretResolver] = None,
    timeout_seconds: Optional[float] = None,
) -> Iterator[WeKnoraAdapter]:
    """只使用 Provider 的 URL 与 Secret 引用，按完整连接身份复用检索客户端。

    URL / 引用 / 租户身份 / revision / 超时 / 阈值 / Secret 变化即退休旧缓存。租约仅保证
    生命周期，不保证旧适配器的 failures/last_error 是请求级报告；业务接线须使用独立报告。
    tenant_ref 目前仅隔离缓存和审计，**不发送租户头，也不声明具备切租户能力**。
    transport 注入绕过生产缓存，退出时关闭；真实 Secret 不进入缓存键 repr 或日志。
    """
    if connection.provider_type != "weknora":
        raise ProviderBackendNotReadyError("该 Provider 尚未支持独立检索连接。")
    if isinstance(connection.provider_id, bool) or not isinstance(connection.provider_id, int) or connection.provider_id <= 0:
        raise ProviderBackendNotReadyError("Provider 身份无效。")
    if not isinstance(connection.base_url, str) or not connection.base_url.strip():
        raise ProviderBackendNotReadyError("Provider 服务地址未配置。")
    if timeout_seconds is not None and (
        isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(timeout_seconds) or timeout_seconds <= 0
    ):
        raise ProviderBackendNotReadyError("Provider 请求超时必须是大于零的有限数值。")
    try:
        settings = get_settings()
    except Exception:
        raise ProviderBackendNotReadyError("检索运行配置读取失败，请检查部署配置。") from None
    timeout_ms = (
        timeout_seconds * 1000 if timeout_seconds is not None
        else getattr(settings, "WEKNORA_TIMEOUT_MS", 15000)
    )
    vector_threshold = getattr(settings, "WEKNORA_VECTOR_THRESHOLD", 0.5)
    keyword_threshold = getattr(settings, "WEKNORA_KEYWORD_THRESHOLD", 0.3)
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
        for value in (timeout_ms, vector_threshold, keyword_threshold)
    ) or timeout_ms <= 0 or not 0 <= vector_threshold <= 1 or not 0 <= keyword_threshold <= 1:
        raise ProviderBackendNotReadyError("Provider 检索超时或阈值配置无效。")
    api_key = (secret_resolver if secret_resolver is not None else SecretResolver()).resolve(
        connection.secret_ref
    )
    if not isinstance(api_key, str) or not api_key.strip():
        raise SecretResolutionError("Provider 运行时凭据缺失。")

    def build_backend() -> WeKnoraAdapter:
        return WeKnoraAdapter(
            transport=transport, base_url=connection.base_url, api_key=api_key,
            kb_id="", kb_map={}, timeout_ms=timeout_ms,
            vector_threshold=vector_threshold, keyword_threshold=keyword_threshold,
        )

    if transport is not None:
        backend = build_backend()
        try:
            yield backend
        finally:
            backend.close()
        return

    key = _ProviderCacheKey(
        connection, timeout_ms, vector_threshold, keyword_threshold, sha256(api_key.encode()).digest()
    )
    with _PROVIDER_BACKEND_LOCK:
        entry = _PROVIDER_BACKEND_CACHE.get(connection.provider_id)
        if entry is None or entry.key != key:
            if entry is not None:
                _PROVIDER_BACKEND_CACHE.pop(connection.provider_id)
                _retire_provider_entry(entry)
            entry = _ProviderBackendEntry(key, build_backend())
            _PROVIDER_BACKEND_CACHE[connection.provider_id] = entry
        entry.leases += 1
    try:
        yield entry.backend
    finally:
        with _PROVIDER_BACKEND_LOCK:
            entry.leases -= 1
            if entry.retired and entry.leases == 0:
                entry.backend.close()


def reset_backend_cache() -> None:
    """清空进程级后端缓存（测试隔离用；生产改配置后可显式重置再重取）。

    缓存实例持 httpx.Client 连接池且**不 close**（进程退出由 OS 回收）；确需释放时，
    调用方应先对缓存实例 .close() 再 reset。
    """
    _BACKEND_CACHE.clear()


def cached_backend_names() -> tuple:
    """已缓存的后端名（只读快照，按 `_SUPPORTED` 顺序排列）。

    A1（2026-09-28）：预检脚本要报告「缓存状态」（13 号任务书 §2.7 配置矩阵的生效方式
    一列），但 `_BACKEND_CACHE` 是模块私有。跨模块读私有符号是红线，故公共化一个只读
    访问器；**不提供写入口**——缓存仍只由 get_backend 填充、reset_backend_cache 清空。

    注意：返回非空**不代表配置已生效**，只代表本进程已构造过后端实例；配置改动后
    必须重启进程或 reset_backend_cache() 后重取（见 get_backend 文档）。
    """
    return tuple(name for name in _SUPPORTED if name in _BACKEND_CACHE)


def parse_kb_map(
    raw: str,
    *,
    env_name: str = "WEKNORA_KB_MAP",
    prefix: Optional[str] = None,
) -> Dict[str, Any]:
    """解析 KB_MAP（模块级：完整集合名 → 外部 id）。

    A1（2026-09-28）：由 `_parse_kb_map` 公共化——只读预检脚本
    （scripts/precheck_knowledge_config.py）需要校验 KB_MAP 合法性，而
    「任何配置项都不得复制第二套读取逻辑」（13 号任务书 §2.7）；跨模块引用私有符号
    是本仓库红线，故去下划线公共化，不保留旧名别名（AGENTS.md：废弃整项删除）。

    第十一节（2026-10-16）复用给 `LEXIANG_KB_MAP`（值 = space_id，语义一一对应）：
    **解析逻辑与校验口径一份**，只有报错文案里的变量名参数化（`env_name`）——不复制
    第二套解析器，否则「配置项不得有两套读取」当场作废。

    - 空串 / 全空白 → {}（退回单库直连，不报错）；
    - 非合法 JSON / 非 JSON 对象 → 抛 ValueError，**明确说明期望格式**，不静默退化成空 map；
    - 键必须以调用方显式提供的完整集合名前缀开头，手写裸逻辑名不合法；
    - 值必须是非空字符串外部 id（WeKnora=kb_id / 乐享=space_id）。

    独立分包版只接受调用方显式传入的分包前缀，不加载其他智能体的配置模块。
    """
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{env_name} 不是合法 JSON：期望形如 "
            '{"sc_structural-check_general-db": "<external-id>"} 的 JSON 对象。'
            f"（原始值前 120 字符：{text[:120]!r}；解析错误：{exc}）"
        ) from exc
    if not isinstance(data, dict):
        raise ValueError(
            f"{env_name} 必须是 JSON 对象（键=完整集合名，值=外部 id），"
            f"实得 {type(data).__name__}。"
        )
    # 独立版只由分包模块调用，前缀由其 workflow_config 显式传入。
    # 不依赖结构受力智能体的配置模块，也不隐式接受其他智能体的集合。
    if not isinstance(prefix, str) or not prefix:
        raise ValueError(f"{env_name} 必须提供集合名前缀")
    expected_prefix = prefix
    out: Dict[str, Any] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not key.startswith(expected_prefix) or len(key) <= len(expected_prefix):
            raise ValueError(
                f"{env_name} 的键必须是**完整集合名**（由 collection_full_name() 产出，"
                f"形如 {expected_prefix}<逻辑集合名>），不得手写裸逻辑名；非法键：{key!r}。"
            )
        # 值支持两种形态（28 号文档 §多库：一对一 → 一对多）：
        #   字符串 = 单库（历史形态，全量兼容，不破坏既有部署）；
        #   非空字符串数组 = 多库路由（按**部门**切开的物理库合并召回）。
        # 空数组/含空项/非字符串 一律就地报错——「配错要报错，不静默退化」与本文件
        # 其余配置项同一口径。
        if isinstance(value, str):
            if not value.strip():
                raise ValueError(
                    f"{env_name}[{key!r}] 的外部 id 必须是非空字符串，实得 {value!r}。"
                )
            out[key] = value.strip()
        elif isinstance(value, (list, tuple)):
            ids = [v.strip() if isinstance(v, str) else None for v in value]
            if not ids or any(not v for v in ids):
                raise ValueError(
                    f"{env_name}[{key!r}] 的 id 数组必须非空、且每一项都是非空字符串，"
                    f"实得 {value!r}。"
                )
            out[key] = list(dict.fromkeys(ids))
        else:
            raise ValueError(
                f"{env_name}[{key!r}] 的外部 id 必须是**非空字符串或非空字符串数组**，"
                f"实得 {type(value).__name__}: {value!r}。"
            )
    return out


def parse_backend_by_module(raw: str) -> Dict[str, str]:
    """解析 NEW_KNOWLEDGE_BACKEND_BY_MODULE（模块名 → 后端名）。

    - 空串 / 全空白 → {}（全部走全局 NEW_KNOWLEDGE_BACKEND）；
    - 非合法 JSON / 非 JSON 对象 → 抛 ValueError，明确说明期望格式，**不静默退化成空 map**；
    - 键必须是已知模块名（`_KNOWN_MODULES`）；手写别的模块名 → ValueError；
    - 值必须是受支持的后端名（`_SUPPORTED`）。

    与 `parse_kb_map` 同款严格口径：配置项必须有校验入口，错配要就地报错而非静默回退
    （只读预检脚本 precheck.py 复用本函数，不复制第二份解析逻辑）。
    """
    text = (raw or "").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "NEW_KNOWLEDGE_BACKEND_BY_MODULE 不是合法 JSON：期望形如 "
            '{"safety_review": "weknora", "agentscope": "weknora"} 的 JSON 对象。'
            f"（原始值前 120 字符：{text[:120]!r}；解析错误：{exc}）"
        ) from exc
    if not isinstance(data, dict):
        raise ValueError(
            "NEW_KNOWLEDGE_BACKEND_BY_MODULE 必须是 JSON 对象（键=模块名，值=后端名），"
            f"实得 {type(data).__name__}。"
        )
    out: Dict[str, str] = {}
    for key, value in data.items():
        name = str(key or "").strip()
        if name not in _KNOWN_MODULES:
            raise ValueError(
                f"NEW_KNOWLEDGE_BACKEND_BY_MODULE 的键必须是已知模块名 {_KNOWN_MODULES}，"
                f"不得手写别的模块名；非法键：{key!r}（未列入的模块走全局 NEW_KNOWLEDGE_BACKEND）。"
            )
        backend = str(value or "").strip().lower()
        if backend not in _SUPPORTED:
            raise ValueError(
                f"NEW_KNOWLEDGE_BACKEND_BY_MODULE[{name!r}] 必须是受支持的后端名 {_SUPPORTED}，"
                f"实得 {value!r}。"
            )
        out[name] = backend
    return out


def resolve_weknora_read_key(settings: Any) -> Any:
    """选出**能覆盖全部已登记库**的 WeKnora 检索凭据。

    背景（2026-10-19 实测，勿再误判为「等运维扩 scope」）：

    - `WEKNORA_API_KEY`（`szzg-sr-corpus10-retrieve`）的 scope **限定 10 个规范库**。
      访问任何不在名单内的库（如新建的范例库 6 个）一律
      `403 {"code":1002,"message":"API key scope does not allow one or more knowledge bases"}`。
    - `WEKNORA_FILE_API_KEY` 是**不限库的 retrieve key**：实测 19 个库（含 6 个范例库、
      3 个 OCR 候选库、10 个规范/法规库）`hybrid-search` **全部 200**。

    注：本仓库 config.py 旧注释称 FILE key「只读、不写、不能管库」**已过时**——实测它能
    `POST /knowledge-bases`（201）与上传文件。能力比注释大，故此处只把它用作**读**凭据，
    写仍统一走 `ingest_port`（唯一写入口），不因「它恰好能写」就放开写路径。

    选择策略（显式优先，不用「试错回退」）：
      - `WEKNORA_KB_MAP` 非空（多库路由，任一库可能不在 API_KEY 名单内）→ 用 FILE key；
      - 否则沿用 `WEKNORA_API_KEY`（单库直连 `WEKNORA_KB_ID`，原行为不变）。
    FILE key 缺失时退回 API_KEY 并留痕：多库场景会 403 降级返空，但**不阻断启动**——
    检索降级已有健康三态与失败计数兜住，硬起不来更糟。

    为什么不在请求失败后再换 key 重试：那会掩盖「配置选错」这个真问题，且让每次
    检索都可能打两次远端。读凭据必须是**构造期确定**的单一事实。
    """
    kb_map = parse_kb_map(getattr(settings, "WEKNORA_KB_MAP", "") or "")
    read_key = getattr(settings, "WEKNORA_API_KEY", "") or ""
    file_key = getattr(settings, "WEKNORA_FILE_API_KEY", "") or ""
    if kb_map and file_key.strip():
        return file_key
    if kb_map and not file_key.strip():
        logger.warning(
            "WEKNORA_KB_MAP 已登记 %d 个库，但 WEKNORA_FILE_API_KEY 未配置："
            "多库检索将用库受限的 WEKNORA_API_KEY，未列入其 scope 的库会 403 降级返空。",
            len(kb_map),
        )
    return read_key


def _build_backend(backend: str, settings: Any, transport: Optional[Any]) -> Any:
    """按后端名构造实例（不涉及缓存；缓存策略在 get_backend）。"""
    if backend == "lexiang":
        # 乐享后端（第十一节骨架期）：httpx REST「AI 搜索」适配器。
        # 配置在此集中转发（工厂是唯一配置入口）；LEXIANG_KB_MAP 复用 parse_kb_map
        # （只参数化报错文案里的变量名，不复制第二套解析器）。
        # 缺 LEXIANG_BASE_URL/APP_KEY/APP_SECRET 时适配器构造即抛 RetrievalBackendError
        # → **显式报错，不静默回退别的后端**（§11.3.2 验收）。
        from .lexiang_adapter import LexiangAdapter  # noqa: PLC0415 —— 懒加载

        logger.debug("检索后端：LexiangAdapter")
        return LexiangAdapter(
            transport=transport,
            base_url=settings.LEXIANG_BASE_URL,
            app_key=settings.LEXIANG_APP_KEY,
            app_secret=settings.LEXIANG_APP_SECRET,
            staff_id=settings.LEXIANG_STAFF_ID,
            space_id=settings.LEXIANG_SPACE_ID,
            timeout_ms=settings.LEXIANG_TIMEOUT_MS,
            kb_map=parse_kb_map(
                getattr(settings, "LEXIANG_KB_MAP", "") or "", env_name="LEXIANG_KB_MAP"
            ),
        )

    if backend == "weknora":
        # WeKnora 后端：httpx REST 混合检索适配器。
        # 配置在此集中转发（工厂是唯一配置入口）。
        logger.debug("检索后端：WeKnoraAdapter")
        return WeKnoraAdapter(
            transport=transport,
            base_url=settings.WEKNORA_BASE_URL,
            # 读凭据必须覆盖全部已登记库：多库路由下用不限库的 FILE key，
            # 否则未列入 API_KEY scope 的库恒 403（见 resolve_weknora_read_key）。
            api_key=resolve_weknora_read_key(settings),
            kb_id=settings.WEKNORA_KB_ID,
            timeout_ms=settings.WEKNORA_TIMEOUT_MS,
            kb_map=parse_kb_map(getattr(settings, "WEKNORA_KB_MAP", "") or ""),
            vector_threshold=getattr(settings, "WEKNORA_VECTOR_THRESHOLD", 0.5),
            keyword_threshold=getattr(settings, "WEKNORA_KEYWORD_THRESHOLD", 0.3),
        )

    # `_SUPPORTED` 已在 get_backend 校验；此处兜底防未来新增值漏实现。
    raise ValueError(f"未实现的检索后端: {backend!r}（可选 {_SUPPORTED}）")


def get_backend(transport: Optional[Any] = None, module: Optional[str] = None) -> Any:
    """返回当前配置对应的检索后端实例。

    transport 仅供测试注入（httpx.MockTransport）；生产调用不传。
    - **transport is not None 时绕过缓存、每次新建、且不写入缓存**（否则 MockTransport
      会常驻缓存，或生产拿到被测试污染的实例——本仓库栽过的假绿形态）。
    - transport 为 None 时按后端名命中进程级缓存：**同进程两次调用返回同一实例**，
      使降级计数（failures/last_error）与连接池得以复用并可被外部读到。

    module（按模块粒度开关，2026-10-13）：非空时先在 NEW_KNOWLEDGE_BACKEND_BY_MODULE 里
    查该模块的后端名，未列入则退回全局 NEW_KNOWLEDGE_BACKEND；`module=None/""` 跳过映射
    （直接走全局）。**缓存键仍是后端名** ⇒ 选了同一后端的模块**共用同一实例**
    （`cached_backend_names()` 不变），不因模块不同而复制连接池。

    缓存实例生命周期 = **进程级**：
    - WeKnoraAdapter 持 httpx.Client 连接池（weknora_adapter.py:79-87），**不 close**
      （进程退出由 OS 回收）；确需释放：先 .close() 再 reset_backend_cache()；
    - **get_settings() 仅在首次构造（首次未命中缓存）时读取**——缓存后不再读 Settings，
      故配置改动（如 WEKNORA_KB_ID / BY_MODULE）需**重启进程或 reset_backend_cache() 后重取**才生效；
    - 本缓存按后端名（weknora/lexiang）可分、可显式清空。
    """
    settings = get_settings()
    default = (settings.NEW_KNOWLEDGE_BACKEND or "weknora").strip().lower()

    # 按模块粒度覆盖：malformed 的映射一律就地报错（不静默回退），因此**无条件解析**
    # （即便本次 module=None，也让配置错误在最早一次取后端时暴露）。
    by_module = parse_backend_by_module(
        getattr(settings, "NEW_KNOWLEDGE_BACKEND_BY_MODULE", "") or ""
    )
    backend = by_module.get(module, default) if module else default

    if backend not in _SUPPORTED:
        raise ValueError(
            f"NEW_KNOWLEDGE_BACKEND 不支持: {backend!r}（可选 {_SUPPORTED}）。"
            "请检查 .env 配置：weknora=WeKnora（现网），"
            "lexiang=腾讯乐享（业主侧，骨架期）；Chroma 后端已移除，不得再配 chroma。"
        )

    # 测试注入：绕过缓存，每次新建，不污染进程级缓存。
    if transport is not None:
        return _build_backend(backend, settings, transport)

    cached = _BACKEND_CACHE.get(backend)
    if cached is not None:
        return cached

    instance = _build_backend(backend, settings, None)
    _BACKEND_CACHE[backend] = instance
    return instance


def get_scoped_backend(transport: Optional[Any] = None) -> Any:
    """返回支持**有范围结构化查询**的后端实例（P02a 公共入口）。

    为什么不是让 `get_backend` 直接给出：有范围查询是**可选能力**——本地双引擎能按来源
    在 top-k 之前过滤，外部只读后端当前没有。未实现的后端在这里**显式拒绝**
    （抛 RetrievalBackendError），不静默退回 `query_documents` 的无范围全库查询：
    后者会让调用方把全库结果当成"已按目标范围检索"，并把空结果误判为可靠 miss
    （25 号 §3.5）。业务层只依赖本入口，不 import 任何具体适配器。
    """
    backend = get_backend(transport)
    if not callable(getattr(backend, "query_documents_scoped", None)):
        backend_name = getattr(backend, "backend_name", type(backend).__name__)
        raise RetrievalBackendError(
            f"{backend_name} 未实现 query_documents_scoped（有范围结构化查询）："
            "拒绝退回无范围查询（不得把全库结果冒充已按目标范围检索）"
        )
    return backend
