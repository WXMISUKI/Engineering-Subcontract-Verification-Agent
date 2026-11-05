"""多 KB 并行查询服务：路由解析 + 真实并发 + 超时隔离 + 聚合（P4，2026-09-28）。

与 `multi_kb.py` 的分工：本模块负责「怎么问、问谁、失败怎么归因」，
`multi_kb.py` 只负责「拿到每库排名后怎么合并」。这样聚合逻辑可以脱离网络单测。

四条实现要求（17 号任务包 P4）：

1. **真实并发**：逐 KB 的 `hybrid-search` 同时发出，不是 for 循环串行。判据是并发屏障
   ——所有请求必须**先全部到达**再一起放行，串行实现永远到不了屏障（有用例锁定）。
2. **单 KB 超时隔离**：一个库超时只影响它自己，其余库照常返回并计入 `partial`。
3. **禁止跨 KB 比较原始 score**：只按 KB 内排名做 `weight/(rrf_k+rank)`，原始 score
   原样留在 `raw_scores` 里备查。
4. **不得静默回退 Chroma**：全失败就是 `undetermined`，不换后端、不换库、不返回空列表
   冒充 `miss`。

线程模型用 `ThreadPoolExecutor`：httpx 客户端是同步的，而本项目 FastAPI 侧大量使用同步
路由；线程池是与既有代码形态冲突最小、也最容易用屏障证明「真的并发」的方案。

G1（2026-10-19）：DB 目标冻结 Provider 身份/连接和绑定文件范围；每个目标租借 Provider
工厂客户端并消费请求级报告。内部 target_key 区分 Provider/绑定，外部 kb_id 保持原值。
显式手造且无 Provider 身份的旧目标继续走 get_backend(module).query_scoped 兼容链。
HTTP 每次超时取单目标预算与整体剩余预算的较小值；主线程只接受期限内结果，不声称能
取消在途线程。工作线程不修改聚合状态，晚到结果不能污染已返回对象。
"""
from __future__ import annotations

import functools
import json
import logging
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from ...models.models import KnowledgeBaseBinding, KnowledgeProvider
from .binding_service import authorized_file_scope, enabled_query_bindings
from .factory import ProviderConnection, acquire_provider_backend, get_backend
from .multi_kb import (
    AggregatedKnowledgeResult,
    aggregate_ranked_results,
)
from .port import ScopedQueryOutcome
from .provider_client import (
    ProviderError,
    ProviderUnreachableError,
    redact,
)

logger = logging.getLogger(__name__)

# 并发上限：既是保护远端（WeKnora 一般不希望你同时打几十个库），也避免线程数随配置膨胀。
MAX_CONCURRENT_QUERIES = 8

# 单库默认超时（秒）；整体上限默认 = 单库超时 + 2s，保证「个别库慢」不会把整个请求拖死。
DEFAULT_PER_KB_TIMEOUT_SECONDS = 8.0
DEFAULT_OVERALL_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class QueryTarget:
    """一个可执行的查询目标：某 provider 下的某个 KB。

    DB 目标的连接字段直接构造 ProviderConnection；provider_id=None 仅为旧手造目标兼容。
    """

    kb_id: str
    kb_name: str
    base_url: str
    secret_ref: str
    weight: float
    priority: int
    tenant_ref: Optional[str] = None
    # None=绑定授权默认的整库；明确空tuple=本次不查。
    knowledge_ids: Optional[Sequence[str]] = None
    provider_id: Optional[int] = None
    binding_id: Optional[int] = None
    provider_type: str = ""
    provider_revision: str = ""
    # 来源级计划拆分同一绑定的多个文件时使用；旧绑定键与任务选择语义保持不变。
    source_key: str = ""

    @property
    def target_key(self) -> str:
        """内部路由身份；外部 KB ID 始终保留在 kb_id。"""
        if self.source_key:
            return self.source_key
        if self.provider_id is None:  # 显式手造的旧目标兼容路径
            return self.kb_id
        return json.dumps([self.provider_id, self.binding_id, self.kb_id], separators=(",", ":"))


@dataclass
class MultiKbQueryResult:
    """查询结果 + 可复核的分母信息。

    `per_kb` 是**审计与分母**：每个目标各自的状态、耗时与错误码。没有它，
    「3 个库里 1 个失败」只能靠 `partial` 一个词去猜，运维无法定位是哪一层坏了。
    """

    status: str
    items: List[Dict[str, Any]]
    failed_kbs: List[str]
    succeeded_kbs: List[str]
    undetermined_reason: Optional[str]
    per_kb: List[Dict[str, Any]] = field(default_factory=list)
    total_targets: int = 0
    rrf_k: int = 60
    failed_targets: List[str] = field(default_factory=list)
    succeeded_targets: List[str] = field(default_factory=list)
    # 查询开始时冻结的实际目标范围；仅保留业务身份，不能包含连接或凭据信息。
    # knowledge_ids=None 表示整库，[] 表示明确空范围，两者不得混淆。
    target_scopes: List[Dict[str, Any]] = field(default_factory=list)


def resolve_query_targets(db: Any, agent_id: int) -> List[QueryTarget]:
    """把「启用且允许查询」的绑定解析成可执行目标（P4 的路由解析）。

    为什么单独一步：绑定行只有 `provider_id`，真正要用的 `base_url`/`secret_ref`
    在 provider 表上。解析结果为空**不等于**「查不到东西」，而是「没有配置目标」——
    这个区别正是 D2 要保住的（`undetermined(no_target)` 而不是 `miss`）。
    """
    bindings: Sequence[KnowledgeBaseBinding] = enabled_query_bindings(db, agent_id)
    if not bindings:
        return []

    provider_ids = {row.provider_id for row in bindings}
    providers: Mapping[int, KnowledgeProvider] = {
        provider.id: provider
        for provider in db.query(KnowledgeProvider)
        .filter(KnowledgeProvider.id.in_(provider_ids))
        .all()
    }

    targets: List[QueryTarget] = []
    for row in bindings:
        provider = providers.get(row.provider_id)
        if provider is None:
            logger.warning(
                "绑定 %s 指向的 provider %s 不存在，已跳过（该目标不计入分母）",
                row.id,
                row.provider_id,
            )
            continue
        if not provider.enabled:
            logger.info(
                "provider %s 已停用，绑定 %s 本次不参与检索",
                provider.id,
                row.id,
            )
            continue
        targets.append(
            QueryTarget(
                kb_id=row.external_kb_id,
                kb_name=row.display_name or row.external_kb_id,
                base_url=provider.base_url,
                secret_ref=provider.secret_ref,
                tenant_ref=provider.tenant_ref,
                weight=float(row.retrieval_weight),
                priority=int(row.priority),
                # 只有 NULL 表示整库；空列表或无效格式不授权任何文件。
                knowledge_ids=authorized_file_scope(row.selected_knowledge_ids),
                provider_id=provider.id,
                binding_id=row.id,
                provider_type=provider.provider_type,
                provider_revision=provider.updated_at.isoformat() if provider.updated_at else "",
            )
        )
    return targets


def apply_target_selection(
    targets: Sequence[QueryTarget], selection: Any = None,
) -> List[QueryTarget]:
    """只从服务端目标表收窄；客户端身份字段不参与授权或构造连接。"""
    if not isinstance(selection, dict):
        return list(targets)
    if selection.get("kb_enabled") is False:
        return []

    def clean(values: Any) -> tuple[str, ...]:
        if not isinstance(values, (list, tuple)):
            return ()
        return tuple(dict.fromkeys(str(v).strip() for v in values if isinstance(v, str) and v.strip()))

    def narrow(target: QueryTarget, values: Any) -> QueryTarget:
        requested = clean(values)
        allowed = target.knowledge_ids
        return replace(target, knowledge_ids=requested if allowed is None else tuple(
            value for value in requested if value in allowed))

    choices = selection.get("targets")
    if isinstance(choices, dict):
        out = []
        for target in targets:
            choice = choices.get(target.target_key)
            if choice is None:
                out.append(target)
            elif isinstance(choice, dict):
                intent = choice.get("intent", "default")
                if intent == "default":
                    out.append(target)
                elif intent == "restrict":
                    out.append(narrow(target, choice.get("knowledge_ids")))
                # exclude 或未知意图均不执行，不能把非法意图扩成默认整库。
        return out

    if "kb_ids" not in selection and "knowledge_ids" not in selection:
        return list(targets)
    selected = clean(selection.get("kb_ids"))
    counts = {t.kb_id: sum(other.kb_id == t.kb_id for other in targets) for t in targets}
    out = []
    for target in targets:
        if target.kb_id not in selected or counts[target.kb_id] != 1:
            continue
        by_kb = selection.get("files_by_kb")
        if isinstance(by_kb, dict) and target.kb_id in by_kb:
            out.append(narrow(target, by_kb[target.kb_id]))
        elif "knowledge_ids" in selection:
            out.append(narrow(target, selection["knowledge_ids"]))
        else:
            # 勾选知识库不能扩张服务端绑定的文件授权；整库绑定仍保持整库。
            out.append(target)
    return out


def _port_row_to_flat(row: Mapping[str, Any], target: QueryTarget) -> Dict[str, Any]:
    """检索端口行 → `multi_kb` 期望的**扁平行**（溯源字段提到顶层）。

    端口行（`query_scoped` 输出）：`{id, document, metadata{source,knowledge_id,kb_id,
    chunk_index,raw_score,match_type}, distance}`——溯源字段在 `metadata` 里；
    扁平行把 `knowledge_id`/`source`/`chunk_index`/`score` 提到顶层，供 D1 去重键
    （`knowledge_id`+`id`）与库内排序（`score`）使用。

    原始分取 `metadata.raw_score`（WeKnora 侧越大越相关 → 库内降序排序正确）；
    **只用于本库内排序与事后复核，绝不参与跨库比较**（18 号文档）。元数据递归脱敏。
    """
    metadata = dict(row.get("metadata") or {})
    return {
        "id": str(row.get("id") or ""),
        "knowledge_id": str(metadata.get("knowledge_id") or ""),
        "document": row.get("document") or "",
        "kb_id": target.kb_id,
        "kb_name": target.kb_name,
        "source": metadata.get("source") or "",
        "chunk_index": metadata.get("chunk_index"),
        "score": metadata.get("raw_score"),
        "match_type": metadata.get("match_type"),
        "metadata": redact(metadata),
        "provider_id": target.provider_id,
        "binding_id": target.binding_id,
        "target_key": target.target_key,
    }


def _ensure_ranked_and_traceable(
    rows: List[Dict[str, Any]], target: QueryTarget
) -> List[Dict[str, Any]]:
    """在**聚合边界**兜住两条不变量，而不是信任每个 `search_fn` 都做到：

    1. **库内有序**：RRF 用的 `rank` 就是序列下标，输入无序会让排名静默错位——
       错得没有任何报错，只是结果顺序不对，属于最难发现的一类 bug。
       （`default_search_fn` 已排过一次；这里再排是幂等的，且能保护直接注入的 search_fn。）
    2. **溯源字段齐备**：`kb_id`/`kb_name` 是服务层才知道的信息，不能指望检索方填。
       18 号文档 §6 要求每条召回都带得回来源，故在边界补齐而不是「谁忘谁漏」。
    """
    normalized: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["kb_id"] = target.kb_id
        item["provider_id"] = target.provider_id
        item["binding_id"] = target.binding_id
        item["target_key"] = target.target_key
        item.setdefault("kb_name", target.kb_name)
        item.setdefault("source", "")
        item.setdefault("knowledge_id", "")
        item.setdefault("chunk_index", None)
        normalized.append(item)
    return _sort_within_kb(normalized)


def _sort_within_kb(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按 score 降序做**库内**排序。

    为什么敢用 score 排序：同一库、同一模型的 score 同尺度，可比。跨库比 score 才是
    18 号文档禁止的事。远端顺序不保证是相关性序，故这里显式排一次。
    """
    def _key(row: Dict[str, Any]) -> float:
        score = row.get("score")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            return float("-inf")
        return float(score)

    return sorted(rows, key=_key, reverse=True)


def default_search_fn(
    target: QueryTarget,
    query_text: str,
    match_count: int,
    *,
    timeout_seconds: float,
    module: Optional[str] = None,
) -> List[Dict[str, Any]] | ScopedQueryOutcome:
    """DB 目标租借自己的 Provider 连接，返回单请求 ScopedQueryOutcome。

    每次只查该目标KB/文件范围，HTTP使用传入预算；不读取共享失败计数做新路径归因。
    无 Provider 身份的旧手造目标仍返回 list，由旧共享计数边界还原原有失败语义。
    """
    if target.provider_id is not None:
        if target.knowledge_ids is not None and not target.knowledge_ids:
            return []
        connection = ProviderConnection(
            provider_id=target.provider_id, provider_type=target.provider_type,
            base_url=target.base_url, secret_ref=target.secret_ref,
            tenant_ref=target.tenant_ref, revision=target.provider_revision,
        )
        with acquire_provider_backend(connection, timeout_seconds=timeout_seconds) as backend:
            outcome = backend.query_scoped_reported(query_text, kb_ids=[target.kb_id],
                knowledge_ids=None if target.knowledge_ids is None else list(target.knowledge_ids),
                top_k=match_count, timeout_seconds=timeout_seconds)
            return replace(outcome, rows=tuple(_port_row_to_flat(row, target) for row in outcome.rows))
    return _search_backend(get_backend(module=module), target, query_text, match_count)


def _search_backend(backend: Any, target: QueryTarget, query_text: str, match_count: int) -> List[Dict[str, Any]]:
    failures_before = getattr(backend, "failures", 0)
    rows = backend.query_scoped(
        query_text,
        kb_ids=[target.kb_id],
        knowledge_ids=list(target.knowledge_ids) if target.knowledge_ids else None,
        top_k=match_count,
    )
    if getattr(backend, "failures", 0) > failures_before:
        raise ProviderUnreachableError(
            f"知识库 {target.kb_name!r} 检索失败（后端已降级）。",
            detail=getattr(backend, "last_error", None) or "backend_degraded",
        )
    return _sort_within_kb([_port_row_to_flat(row, target) for row in rows])


SearchFn = Callable[..., List[Dict[str, Any]] | ScopedQueryOutcome]


def run_multi_kb_query(
    targets: Sequence[QueryTarget],
    query_text: str,
    *,
    top_k_per_kb: int = 5,
    limit: int = 10,
    rrf_k: int = 60,
    per_kb_timeout_seconds: float = DEFAULT_PER_KB_TIMEOUT_SECONDS,
    overall_timeout_seconds: Optional[float] = DEFAULT_OVERALL_TIMEOUT_SECONDS,
    max_workers: int = MAX_CONCURRENT_QUERIES,
    search_fn: Optional[SearchFn] = None,
    module: Optional[str] = None,
    cancel_event: Optional[threading.Event] = None,
) -> MultiKbQueryResult:
    """并发查询所有目标并聚合。**不触碰数据库**（目标由调用方解析好）。"""
    target_scopes = [
        {
            "provider_id": target.provider_id,
            "binding_id": target.binding_id,
            "kb_id": target.kb_id,
            "knowledge_ids": None if target.knowledge_ids is None else list(target.knowledge_ids),
            "target_key": target.target_key,
        }
        for target in targets
    ]
    if not targets:
        # D2：没有可执行目标 ⇒ undetermined(no_target)。这里连一次网络都不发。
        return MultiKbQueryResult(
            status="undetermined",
            items=[],
            failed_kbs=[],
            succeeded_kbs=[],
            undetermined_reason="no_target",
            per_kb=[],
            total_targets=0,
            rrf_k=rrf_k,
            target_scopes=target_scopes,
        )

    if not query_text.strip():
        # 空查询不是「没命中」，而是「这次没问」——对全部目标都是不可判定。
        return MultiKbQueryResult(
            status="undetermined",
            items=[],
            failed_kbs=[target.kb_id for target in targets],
            succeeded_kbs=[],
            undetermined_reason="all_failed",
            per_kb=[
                {"kb_id": target.kb_id, "target_key": target.target_key,
                 "provider_id": target.provider_id, "binding_id": target.binding_id,
                 "kb_name": target.kb_name, "status": "skipped",
                 "error_code": "empty_query"}
                for target in targets
            ],
            total_targets=len(targets),
            rrf_k=rrf_k,
            target_scopes=target_scopes,
            failed_targets=[t.target_key for t in targets],
        )

    # 端口化后 `default_search_fn` 需要 `module` 才能选对后端；`functools.partial` 让
    # `_run` 的调用形态（以及注入 search_fn 的契约）保持不变。
    do_search = search_fn or functools.partial(default_search_fn, module=module)
    stop = cancel_event or threading.Event()
    deadline = None if overall_timeout_seconds is None else _now() + max(0, overall_timeout_seconds)

    ranked: Dict[str, List[Dict[str, Any]]] = {}
    failed: List[str] = []
    per_kb: List[Dict[str, Any]] = []
    def _entry(target: QueryTarget) -> Dict[str, Any]:
        return {
            "kb_id": target.kb_id,
            "target_key": target.target_key,
            "provider_id": target.provider_id,
            "binding_id": target.binding_id,
            "kb_name": target.kb_name,
            "weight": target.weight,
        }

    def _run(target: QueryTarget) -> tuple[Dict[str, Any], Optional[List[Dict[str, Any]]], bool, float]:
        entry = _entry(target)
        if target.provider_id is not None and target.knowledge_ids is not None and not target.knowledge_ids:
            entry.update({"status": "skipped", "error_code": "empty_scope"})
            return entry, None, True, _now()
        if stop.is_set():
            # 取消传播：整体超时/调用方取消后，尚未发出的请求不再发出。
            entry.update({"status": "cancelled", "error_code": "cancelled"})
            return entry, None, True, _now()
        started = _now()
        remaining = None if deadline is None else deadline - started
        if remaining is not None and remaining <= 0:
            entry.update({"status":"failed", "error_code":"deadline_exceeded"})
            return entry, None, True, _now()
        try:
            report = do_search(
                target,
                query_text,
                top_k_per_kb,
                timeout_seconds=per_kb_timeout_seconds if remaining is None else min(per_kb_timeout_seconds, remaining),
            )
            incomplete = False
            if isinstance(report, ScopedQueryOutcome):
                rows = [] if report.error_kind is not None else list(report.rows)
                incomplete = not report.scope_complete or report.error_kind is not None or report.engine.degraded
                entry.update({"scope_complete":report.scope_complete,
                    "filter_capability":report.filter_capability.value, "engine":report.engine.to_dict()})
                if incomplete:
                    entry["error_code"] = report.error_detail or report.incomplete_reason or (
                        report.error_kind.value if report.error_kind else "scope_incomplete")
            else:
                rows = list(report)
            if target.provider_id is not None:
                valid = []
                for row in rows:
                    md = row.get("metadata") or {}
                    kb = md.get("kb_id", row.get("kb_id"))
                    kid = md.get("knowledge_id", row.get("knowledge_id"))
                    if kb != target.kb_id or not kid or (target.knowledge_ids is not None and kid not in target.knowledge_ids):
                        incomplete = True
                        entry.update({"scope_complete":False,"error_code":"returned_scope_mismatch"})
                        continue
                    valid.append(row)
                rows = valid
            rows = _ensure_ranked_and_traceable(rows, target)[:max(0, top_k_per_kb)]
        except ProviderError as exc:
            entry.update(
                {
                    "status": "failed",
                    "error_code": exc.code,
                    "retryable": exc.retryable,
                    "elapsed_ms": round((_now() - started) * 1000, 1),
                }
            )
            logger.warning("知识库目标 %s 检索失败（%s）", target.target_key, exc.code)
            return entry, None, True, _now()
        except Exception as exc:  # noqa: BLE001 —— 未预期异常也不得静默变空结果
            entry.update(
                {
                    "status": "failed",
                    "error_code": getattr(exc, "code", "unexpected_error"),
                    "elapsed_ms": round((_now() - started) * 1000, 1),
                }
            )
            # 连接/凭据配置异常也可能含敏感原文，只记录固定错误分类。
            logger.warning("知识库目标 %s 检索失败（%s）", target.target_key, entry["error_code"])
            return entry, None, True, _now()

        entry.update(
            {
                "status": ("partial" if rows else "failed") if incomplete else "ok",
                "hit_count": len(rows),
                "elapsed_ms": round((_now() - started) * 1000, 1),
            }
        )
        if _now() - started > per_kb_timeout_seconds:
            entry.update({"status":"failed", "error_code":"provider_timeout", "hit_count":0})
            return entry, None, True, _now()
        return entry, rows if rows or not incomplete else None, incomplete, _now()

    # 工作线程只返回局部值。主线程是聚合状态唯一写者，迟到结果无法改写已返回对象。
    pool = ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(targets))))
    pending = {pool.submit(_run, target):target for target in targets}
    try:
        while pending:
            remaining = None if deadline is None else max(0, deadline - _now())
            done, _ = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
            if not done:
                break
            for future in done:
                target = pending.pop(future)
                entry, rows, is_failed, finished = future.result()
                if deadline is not None and finished > deadline:
                    entry = dict(_entry(target), status="failed", error_code="deadline_exceeded")
                    rows, is_failed = None, True
                per_kb.append(entry)
                if is_failed:
                    failed.append(target.target_key)
                if rows is not None:
                    ranked[target.target_key] = rows
        for future, target in pending.items():
            future.cancel()  # 只能取消尚未运行的任务，不声称取消在途 HTTP/线程。
            per_kb.append(dict(_entry(target), status="failed", error_code="deadline_exceeded"))
            failed.append(target.target_key)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    # 完成顺序不应改变相同 RRF 分数下的来源和显示顺序。
    ranked = {target.target_key:ranked[target.target_key] for target in targets if target.target_key in ranked}

    aggregated: AggregatedKnowledgeResult = aggregate_ranked_results(
        ranked,
        binding_weights={target.target_key: target.weight for target in targets},
        failed_kbs=failed,
        limit=limit,
        rrf_k=rrf_k,
    )

    # 目标顺序稳定化，便于测试与前端展示（并发完成顺序不可预测）。
    order = {target.target_key: index for index, target in enumerate(targets)}
    per_kb.sort(key=lambda entry: order.get(entry["target_key"], 1 << 30))
    failed_keys = sorted(aggregated.failed_kbs, key=lambda k: order[k])
    succeeded_keys = sorted(aggregated.succeeded_kbs, key=lambda k: order[k])
    raw_kbs = {target.target_key: target.kb_id for target in targets}

    return MultiKbQueryResult(
        status=aggregated.status,
        items=aggregated.items,
        failed_kbs=[raw_kbs[key] for key in failed_keys],
        succeeded_kbs=[raw_kbs[key] for key in succeeded_keys],
        failed_targets=failed_keys,
        succeeded_targets=succeeded_keys,
        undetermined_reason=aggregated.undetermined_reason,
        per_kb=per_kb,
        total_targets=len(targets),
        rrf_k=rrf_k,
        target_scopes=target_scopes,
    )


def query_agent_knowledge(
    db: Any,
    *,
    agent_id: int,
    query_text: str,
    module: Optional[str] = None,
    selection: Any = None,
    **kwargs: Any,
) -> MultiKbQueryResult:
    """业务入口：解析目标 + 并发查询。绑定为空时**不触网**，直接 undetermined(no_target)。

    selection 只收窄服务端解析目标，不改写绑定；DB目标按Provider连接，module保留给旧兼容链。
    """
    targets = apply_target_selection(resolve_query_targets(db, agent_id), selection)
    return run_multi_kb_query(targets, query_text, module=module, **kwargs)


def _now() -> float:
    import time

    return time.monotonic()


__all__ = [
    "DEFAULT_OVERALL_TIMEOUT_SECONDS",
    "DEFAULT_PER_KB_TIMEOUT_SECONDS",
    "MAX_CONCURRENT_QUERIES",
    "MultiKbQueryResult",
    "QueryTarget",
    "apply_target_selection",
    "default_search_fn",
    "query_agent_knowledge",
    "resolve_query_targets",
    "run_multi_kb_query",
]
