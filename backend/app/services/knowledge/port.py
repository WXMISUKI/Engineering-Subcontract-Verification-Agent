"""知识检索端口（T02，2026-09-25；T12 端口诚实化，2026-09-25）。

业务代码只依赖本端口，不依赖任何具体检索后端（Chroma / WeKnora）。
端口方法与既有 VectorService 逐一对应，按语义分三组（T12：分组仅文档层面的诚实
声明，零结构变更——协议方法签名不变）：

读契约（read）
    - query_documents        混合/向量召回，返回 List[Dict] 业务 row 结构

写/治理契约（write / governance）
    - add_documents          写入切片
    - delete_collection      删除整集合
    - delete_by_source       按来源批量删除

健康契约（health）
    - count_documents        集合文档计数（健康自检用）

后端能力差异（T12 诚实声明，不静默假成功）
    WeKnora 后端在本批（T05–T19）是**只读**：add_documents / delete_collection /
    delete_by_source / count_documents 四个写/治理/健康方法**显式 raise
    RetrievalBackendError**，绝不静默返回假成功（T16 落地 WeKnora 写契约前）。
    写路径调用方（service.upload_norm / delete_norm、ref_calc_library）据此类
    fail-fast 并在健康自检暴露「当前后端是否支持写」。

选择后端用 factory.get_backend()，按 NEW_KNOWLEDGE_BACKEND 配置路由：
    weknora —— WeKnoraAdapter（现网默认）
    lexiang —— LexiangAdapter（业主侧腾讯乐享，骨架期）
    （Chroma 后端已于 2026-10-23 移除，不再有本地可写实现。）

结构化（有范围）查询能力 —— 可选能力，P02a（2026-10-15）新增：
    `RetrievalPort` 的既有方法保持兼容（包括跨库检索 `query_scoped`，旧 list 调用继续兼容）；
    新的 `query_documents_scoped` 由 `ScopedRetrievalPort` 单独声明，**不是** RetrievalPort
    的必选方法。理由：能力确实存在差异——本地双引擎能按来源范围在 top-k 之前过滤，
    外部只读后端当前没有该能力。未实现的后端必须**显式拒绝**（抛 RetrievalBackendError），
    不能静默退化成"全库查询后假装成功"。

    为什么要有这个能力（而不是继续用 query_documents）：旧方法把"Chroma 内部静默回退
    SQLite"和"召回后按来源过滤"都藏起来了，调用方拿到的是一个普通 list——看不到实际引擎、
    看不到范围是否完整执行。`ScopedQueryOutcome` 把这些事实显式化，供上层决定能否
    把空结果判为可靠 miss。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol, Sequence


class RetrievalBackendError(RuntimeError):
    """检索后端不可用 / 不支持该操作（连接失败 / 超时 / 非 200 / 只读后端写请求）。

    定义在**端口层**而非具体适配器：业务代码（service / knowledge_health 等）据此
    区分「后端不支持或不可达」与「其它异常」，且无需直接 import 任何具体后端适配器
    （符合 AGENTS.md「业务代码不得直接依赖具体后端」；也避免命中
    test_knowledge_port_contract 的「业务代码不得 import chroma_adapter/weknora_adapter」断言）。
    上层降级链捕获并记录；写路径据此 fail-fast。
    """


# --------------------------------------------------------------------------------------
# 结构化（有范围）查询：范围、能力与结果
# --------------------------------------------------------------------------------------


class ScopeFilterCapability(str, Enum):
    """引擎按来源范围过滤的能力等级（决定空结果能否判为可靠 miss）。"""

    NATIVE = "native"                  # 在 top-k 之前由引擎原生过滤，目标范围已完整查询
    POST_FILTER_ONLY = "post_filter_only"  # 只能召回后过滤：无法证明目标范围已完整查询
    UNSUPPORTED = "unsupported"        # 不支持过滤：**拒绝执行**，不得扩大为全库查询


class ScopedQueryError(str, Enum):
    """结构化查询的**执行事实**分类（不是检索成败；不得转写为 miss 的情况都在这）。"""

    EMPTY_QUERY = "empty_query"                # 空查询文本（调用方问题，未执行检索）
    EMPTY_SCOPE = "empty_scope"                # 明确空选：按规格不查询该目标（空数组 ≠ 全库）
    COLLECTION_MISSING = "collection_missing"  # 目标集合不存在/无内容
    BACKEND_UNAVAILABLE = "backend_unavailable"  # 可用引擎均不可达
    FILTER_UNSUPPORTED = "filter_unsupported"  # 需要范围限制但引擎不支持 → 拒绝执行


@dataclass(frozen=True)
class SourceScope:
    """已核对的来源范围（在 top-k 之前过滤）。

    ``None`` 与空元组的区别是**契约的核心**（规格 Requirement「File selection preserves
    three intents」）：

    - ``allowed_sources is None``：未指定 → 不加允许限制（使用授权默认范围）；
    - ``allowed_sources == ()``：**明确空选** → 该目标本次不查询；空数组**不得**转换成全库；
    - ``allowed_sources`` 非空：只查这些来源（授权子集）；
    - ``excluded_sources``：排除这些来源（禁用来源 / 用户明确排除）；
    - ``allowed_doc_ids``：分块/文档级子集，语义同上（None / () / 非空）。

    三个字段都是"值"而不是"意图枚举"：三意图的**定义点**在领域层（
    `knowledge_retrieval.SelectionMode`），这里只承载解析后的范围值，不复制第二套枚举。
    """

    allowed_sources: Optional[tuple[str, ...]] = None
    excluded_sources: tuple[str, ...] = ()
    allowed_doc_ids: Optional[tuple[str, ...]] = None

    @property
    def is_unrestricted(self) -> bool:
        """无任何限制：未指定允许集且无排除集。"""
        return self.allowed_sources is None and self.allowed_doc_ids is None and not self.excluded_sources

    @property
    def is_explicitly_empty(self) -> bool:
        """明确空选：允许集被显式给成空 → 该目标不查询（不是"全库"）。"""
        return self.allowed_sources == () or self.allowed_doc_ids == ()

    @property
    def needs_doc_id_filter(self) -> bool:
        """需要分块/文档级过滤（本地引擎无法在 where 里表达 → 只能召回后过滤）。"""
        return self.allowed_doc_ids is not None


@dataclass(frozen=True)
class EngineProvenance:
    """实际执行检索的引擎实况。

    唯一权威定义点：端口层。上层（含 `knowledge_retrieval.RetrievalResult`）单向依赖它，
    不另存一份副本。`primary_engine` = 配置指定的主引擎；`actual_engine` = 真正给出结果的
    引擎（可能因内部回退而不同）。**备用引擎的结果不得被当作主引擎已验证**。
    """

    primary_engine: str
    actual_engine: Optional[str]
    degraded: bool
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """序列化（进运行快照/报告：**不含密钥**，只含引擎名与降级原因）。"""
        return {
            "primary_engine": self.primary_engine,
            "actual_engine": self.actual_engine,
            "degraded": self.degraded,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ScopedQueryOutcome:
    """结构化查询结果：行 + 引擎实况 + 范围完整性 + 执行事实。

    ``scope_complete=False`` 表示"目标范围未能被完整查询"（例如只能召回后过滤）——
    此时**空结果不得判为可靠 miss**，调用方必须给出不完整/不可判定原因。
    """

    rows: tuple[Dict[str, Any], ...]
    engine: EngineProvenance
    filter_capability: ScopeFilterCapability
    scope_complete: bool
    incomplete_reason: Optional[str] = None
    error_kind: Optional[ScopedQueryError] = None
    error_detail: Optional[str] = None

    @property
    def executed(self) -> bool:
        """是否真的执行了检索（未执行 ≠ 检索为空）。"""
        return self.error_kind is None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "row_count": len(self.rows),
            "engine": {
                "primary_engine": self.engine.primary_engine,
                "actual_engine": self.engine.actual_engine,
                "degraded": self.engine.degraded,
                "reason": self.engine.reason,
            },
            "filter_capability": self.filter_capability.value,
            "scope_complete": self.scope_complete,
            "incomplete_reason": self.incomplete_reason,
            "error_kind": self.error_kind.value if self.error_kind else None,
            "error_detail": self.error_detail,
        }


class RetrievalPort(Protocol):
    """检索端口协议：业务代码（rag_retriever / ref_calc_library /
    service / knowledge_health / evidence_provenance）只依赖本协议。

    既有方法（含跨库检索 `query_scoped`）保持兼容；新增的有范围结构化查询能力在
    `ScopedRetrievalPort` 中单独声明，避免把可选能力变成所有后端的必选义务。
    """

    def add_documents(
        self,
        collection_name: str,
        texts: List[str],
        metadatas: Optional[List[Dict[str, Any]]] = None,
        ids: Optional[List[str]] = None,
    ) -> None: ...

    def query_documents(
        self,
        collection_name: str,
        query_text: str,
        n_results: int = 5,
    ) -> List[Dict[str, Any]]: ...

    def query_scoped(
        self,
        query_text: str,
        *,
        kb_ids: Sequence[str],
        knowledge_ids: Optional[Sequence[str]] = None,
        top_k: int = 8,
    ) -> List[Dict[str, Any]]:
        """按「勾选范围」检索：在给定知识库集合内召回，可进一步限定到指定文件。

        与 `query_documents` 的差别是**跨库 + 按文件限定**：`knowledge_ids` 为 None
        表示不限定文件（整库召回），传空序列与 None 等价（详见知识库接入开发文档 §6.3：
        WeKnora 侧「传空数组」与「不传」等价，「勾选空集」表达不了「什么都不召回」）。
        返回结构与 `query_documents` 同形（含 `metadata.source/knowledge_id` 等溯源锚点），
        调用方无需区分后端。

        后端能力差异：`chroma_adapter` 显式 raise `RetrievalBackendError`（本次不实现），
        调用方据此类降级（见开发文档 §6.1）。
        """
        ...

    def delete_collection(self, collection_name: str) -> None: ...

    def delete_by_source(
        self,
        collection_name: str,
        source_name: str,
        doc_id_prefix: str,
    ) -> None: ...

    def count_documents(self, collection_name: str) -> int: ...


class ReportedScopedRetrievalPort(Protocol):
    """可选的跨 KB 请求级报告；与旧 query_scoped 空数组整库语义分开。"""

    def query_scoped_reported(
        self, query_text: str, *, kb_ids: Sequence[str],
        knowledge_ids: Optional[Sequence[str]] = None, top_k: int = 8,
        timeout_seconds: Optional[float] = None,
    ) -> ScopedQueryOutcome:
        """None 使用授权默认；明确 [] 本地停止，不向外部服务发送空范围。"""
        ...


class ScopedRetrievalPort(Protocol):
    """可选的**有范围结构化查询**能力（P02a）。

    实现方须保证：范围限制在 top-k 之前生效（或明确报告只能后过滤/不支持）；
    实际引擎与降级原因如实上报；明确空选不转换成全库查询。
    """

    def query_documents_scoped(
        self,
        collection_name: str,
        query_text: str,
        n_results: int = 5,
        *,
        scope: Optional[SourceScope] = None,
    ) -> ScopedQueryOutcome: ...


# --------------------------------------------------------------------------------------
# 带引擎实况的计数（P02b）：健康自检不得把备用引擎的数字当成主引擎的数字
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ReportedCountOutcome:
    """集合计数 + 这个数**是谁给的**（P02b）。

    为什么不能只返回 int：`ChromaAdapter.count_documents` 在 Chroma 不可用时**静默**返回
    SQLite 镜像的计数，健康自检据此把备用库的数字当成 Chroma 的数字报绿——而两者逐集合
    并不一致（P00 已证）。25 号 §7 P02b 明令"健康不把 SQLite 当 Chroma"。把计数与引擎实况
    绑在一起上报，消费方才能判"这个数能不能算主引擎已验证"。

    能力位约定（与 T19 的 `backend_name`/`supports_write` 同一读法）：后端**可选**实现
    `count_documents_reported()`；消费方用 `getattr(backend, "count_documents_reported", None)`
    探测，未实现则退回旧的 `count_documents()` 并把引擎标为未知（不假定为主引擎）。
    """

    count: int
    engine: EngineProvenance
    error_kind: Optional[ScopedQueryError] = None
    error_detail: Optional[str] = None

    @property
    def is_primary_engine(self) -> bool:
        """计数是否来自**主引擎本体**（未发生内部回退）——只有它支撑"主引擎已验证"。"""
        return (
            self.error_kind is None
            and not self.engine.degraded
            and self.engine.actual_engine == self.engine.primary_engine
        )

    @property
    def executed(self) -> bool:
        return self.error_kind is None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "count": self.count,
            "engine": self.engine.to_dict(),
            "is_primary_engine": self.is_primary_engine,
            "error_kind": self.error_kind.value if self.error_kind else None,
            "error_detail": self.error_detail,
        }


__all__ = [
    "RetrievalBackendError",
    "RetrievalPort",
    "ReportedScopedRetrievalPort",
    "ScopedRetrievalPort",
    "ScopeFilterCapability",
    "ScopedQueryError",
    "ScopedQueryOutcome",
    "SourceScope",
    "EngineProvenance",
    "ReportedCountOutcome",
]
