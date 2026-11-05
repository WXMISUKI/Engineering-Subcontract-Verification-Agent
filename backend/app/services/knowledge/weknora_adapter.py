"""WeKnora 知识库检索适配器（T05，2026-09-25）。

实现 RetrievalPort 协议（port.py），业务代码经 factory.get_backend() 在
chroma / weknora 间切换，本适配器不改变任何调用方。

契约事实（2026-09-24 实测，证据 tmp/weknora_t04/t04_contract_evidence_20260924.json）：
- 端点：POST {base}/knowledge-bases/{kb_id}/hybrid-search
  body: {"query_text", "vector_threshold", "keyword_threshold", "match_count"}
- 头：X-API-Key
- 响应：{"data": [chunk...], "success": true}
  chunk 字段：id/content/knowledge_id/chunk_index/knowledge_title/start_at/end_at/
             score/match_type/sub_chunk_id/metadata/chunk_type/parent_chunk_id/
             knowledge_filename/knowledge_source/knowledge_channel/knowledge_base_id

打分语义（实测差异，适配器必须处理）：
- WeKnora score 越高越相关（混合检索为 RRF 融合分，量级 8~11）
- 业务侧读 row["distance"] 越小越相关（Chroma 距离语义）
- 归一公式 distance = 1/(1+score)，与 Chroma 降级分支同一语义（倒排的 tf-idf 得分也经此反转）

降级：连接失败 / 超时 / 非 200 → 抛 RetrievalBackendError，由上层降级链记录并回退。
"""
from __future__ import annotations

import logging
import json
import math
import re
from threading import Lock
from time import monotonic
from typing import Any, Dict, List, Mapping, Optional, Sequence

import httpx

from ...config import get_settings
from . import fulltext
from .port import (EngineProvenance, ReportedCountOutcome, RetrievalBackendError,
                   ScopeFilterCapability, ScopedQueryError, ScopedQueryOutcome, SourceScope)
from .provider_client import (ProviderAuthError, ProviderNotFoundError, ProviderProtocolError,
    ProviderRateLimitError, ProviderServerError, ProviderTimeoutError, ProviderUnreachableError)

# 向后兼容再导出：既有 `from ...weknora_adapter import RetrievalBackendError` 保持可用，
# 但异常的真身已上移至端口层 port.py（业务代码应从 port 导入，不直接依赖具体适配器）。
__all__ = ["WeKnoraAdapter", "RetrievalBackendError"]

logger = logging.getLogger(__name__)


class WeKnoraAdapter:
    """WeKnora 混合检索适配器（REST，httpx）。

    配置（Settings）：
      WEKNORA_BASE_URL         服务根（如 http://host:8080/api/v1）
      WEKNORA_API_KEY          API Key（X-API-Key 头）
      WEKNORA_KB_ID            默认知识库 id（单库直连；多库未命中 map 时回退此值）
      WEKNORA_TIMEOUT_MS       请求超时（毫秒），默认 15000
      WEKNORA_KB_MAP           多库路由表（完整集合名 → kb_id），空 = 单库直连
      WEKNORA_VECTOR_THRESHOLD 混合检索向量阈值，默认 0.5（=原硬编码值）
      WEKNORA_KEYWORD_THRESHOLD 混合检索关键词阈值，默认 0.3（=原硬编码值）

    能力位（T19，供健康自检 getattr 读取）：
      backend_name  = "weknora"；supports_write = False（本批只读，写/治理/健康显式拒绝）。
    """

    # T19：能力位。健康自检用 getattr(backend, "backend_name"/"supports_write", None) 读取。
    backend_name = "weknora"
    supports_write = False
    # §1.2.7：可按知识 ID 拉分块拼正文（GET /chunks/{id}）。乐享适配器无此能力。
    supports_read_document = True

    def __init__(
        self,
        transport: Optional[httpx.BaseTransport] = None,
        *,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        kb_id: Optional[str] = None,
        timeout_ms: Optional[int] = None,
        kb_map: Optional[Dict[str, Any]] = None,
        vector_threshold: Optional[float] = None,
        keyword_threshold: Optional[float] = None,
    ) -> None:
        """transport/配置均可显式注入（测试用假配置 + MockTransport，进程内不联网）。

        生产调用不传任何参数 → 全从 Settings 读取（WEKNORA_*）；
        kb_map 由 factory 解析 WEKNORA_KB_MAP 后转发（工厂是唯一配置入口）。
        """
        # 完整显式配置不读取 Settings；None 才代表未注入，空值不得借用环境连接。
        settings = (
            get_settings()
            if any(value is None for value in (base_url, api_key, kb_id, timeout_ms))
            else None
        )
        self.base_url = (
            base_url if base_url is not None else settings.WEKNORA_BASE_URL or ""
        ).strip().rstrip("/")
        self.api_key = api_key if api_key is not None else settings.WEKNORA_API_KEY
        # kb_id 用 `is not None` 判「是否显式注入」，**不用 `or`**（2026-09-28 修）：
        # 工厂无条件转发 settings.WEKNORA_KB_ID，若此处再用 `or`，显式传入的空串会被
        # 悄悄换成这里**第二次读取**的 Settings 值 —— 于是「工厂是唯一配置入口」失效。
        # 实测后果：`.env` 配好 WEKNORA_KB_ID 之后，
        # test_knowledge_factory_cache / test_weknora_kb_map 的「KB_ID 未配置 ⇒ 不触网」
        # 夹具被绕过，测试可能意外访问实际知识服务（被测试封闭性护栏抓住）。
        self.kb_id = kb_id if kb_id is not None else settings.WEKNORA_KB_ID
        self.timeout_ms = timeout_ms if timeout_ms is not None else settings.WEKNORA_TIMEOUT_MS
        # T13：多库路由表（键=完整集合名）。空 dict = 单库直连（用 self.kb_id）。
        # 值可能是 str（单库）或 List[str]（多库，按部门切库合并召回）
        self.kb_map: Dict[str, Any] = dict(kb_map or {})
        # T13：阈值从构造参数取（默认等于原硬编码值 0.5/0.3，不改变现状行为）。
        self.vector_threshold = (
            vector_threshold if vector_threshold is not None else 0.5
        )
        self.keyword_threshold = (
            keyword_threshold if keyword_threshold is not None else 0.3
        )
        self._transport = transport
        self._client: httpx.Client | None = None
        self._client_lock = Lock()
        # 降级计数（T07 护栏消费方读取；降级在发生时即记录）
        self.failures: int = 0
        self.last_error: str | None = None

        if not self.base_url or not self.api_key or not self.api_key.strip():
            raise RetrievalBackendError(
                "WeKnora 未配置：WEKNORA_BASE_URL / WEKNORA_API_KEY 必填"
            )
        if isinstance(self.timeout_ms, bool) or self.timeout_ms <= 0:
            raise RetrievalBackendError("WeKnora 请求超时必须大于零。")

    # ---------- 内部 ----------

    def _httpx(self) -> httpx.Client:
        # Provider 缓存会由多个线程共用；首次请求只能创建一个连接池。
        with self._client_lock:
            if self._client is None:
                self._client = httpx.Client(
                    base_url=self.base_url,
                    timeout=httpx.Timeout(self.timeout_ms / 1000.0),
                    headers={"X-API-Key": self.api_key},
                    transport=self._transport,
                )
            return self._client

    def close(self) -> None:
        with self._client_lock:
            if self._client is not None:
                self._client.close()
                self._client = None

    def _record_failure(self, exc: Exception) -> None:
        self.failures += 1
        self.last_error = f"{type(exc).__name__}: {exc}"
        logger.warning("WeKnora 检索降级（failures=%d）: %s", self.failures, self.last_error)

    @staticmethod
    def _normalize_score(score: float) -> float:
        """WeKnora score（越大越相关）→ 业务 distance（越小越相关）。

        公式 1/(1+score) 被契约测试钉死（tests/test_weknora_adapter.py），不改。

        ⚠️ 2026-10-20 实测校正：旧注释称 RRF 分落在 8~11、distance ≈ 0.08~0.11，
        **与当前服务端不符**。当前 hybrid-search 实际返回 0.011~0.017（1/(60+rank)
        量级，向量榜与关键词榜各自一套），归一后 distance 挤在 0.983~0.989。
        因此这个 distance **只保证同批内单调（越小越好），不能跨集合/跨批次比大小**，
        更不能当相似度阈值用。要真实分请读 metadata 里的原始 score 日志。
        """
        return round(1.0 / (1.0 + float(score or 0.0)), 4)

    @staticmethod
    def _chunk_to_row(chunk: Dict[str, Any], collection: str) -> Dict[str, Any]:
        """WeKnora chunk → 业务 row（与 ChromaAdapter 返回结构对齐）。

        Chroma row: {"id","document","metadata","distance","page","page_end"}
        """
        metadata: Dict[str, Any] = dict(chunk.get("metadata") or {})
        content = chunk.get("content") or ""
        locators = chunk.get('source_locators')
        if not isinstance(metadata.get('page'), int) and isinstance(locators, list):
            pdf_locators = [item for item in locators if isinstance(item, dict) and item.get('type') == 'pdf']
            pages = [item.get('page') for item in pdf_locators]
            if (pages and not any(item.get('partial') is True for item in pdf_locators)
                    and all(isinstance(page, int) and not isinstance(page, bool) and page > 0 for page in pages)):
                metadata['page'] = min(pages)
                metadata['page_end'] = max(pages)
                metadata['page_source'] = 'weknora_source_locators'
        if not isinstance(metadata.get("page"), int):
            anchors = [int(value) for value in re.findall(r"##\s*物理\s+PDF\s+第\s+(\d+)\s+页", content)]
            if anchors:
                metadata["page"] = anchors[0]
                metadata["page_source"] = "embedded_pdf_header"
                if len(anchors) > 1:
                    metadata["page_end"] = anchors[-1]
        if not isinstance(metadata.get("page"), int):
            # 部分 Provider 文本块不保留 PDF 标题，而保留页面资源标记。
            # 该标记来自同一原文块，只用于页锚，不把图片描述当作条文正文。
            image_pages = [int(value) for value in re.findall(
                r"_page_(\d+)\.(?:jpg|jpeg|png|webp)", content, flags=re.IGNORECASE,
            )]
            if image_pages:
                metadata["page"] = image_pages[0]
                metadata["page_source"] = "embedded_image_page_marker"
                if len(image_pages) > 1:
                    metadata["page_end"] = image_pages[-1]
        # 溯源锚点：源文件名 + 字符偏移（契约实测存在）
        source = chunk.get("knowledge_title") or chunk.get("knowledge_filename") or ""
        if source:
            metadata["source"] = source
        if chunk.get("knowledge_id"):
            metadata["knowledge_id"] = chunk["knowledge_id"]
        if chunk.get("start_at") is not None:
            metadata["start_at"] = chunk["start_at"]
        if chunk.get("end_at") is not None:
            metadata["end_at"] = chunk["end_at"]
        if chunk.get("chunk_index") is not None:
            metadata["chunk_index"] = chunk["chunk_index"]
        if chunk.get("match_type") is not None:
            metadata["match_type"] = chunk["match_type"]
        # 证据形态（text / image_ocr / image_caption / summary）——报告侧据此标注
        # 「本条为页面描述，非条文原文」，不让读者把 VLM 概述当法条引用。
        if chunk.get("chunk_type"):
            metadata["chunk_type"] = chunk["chunk_type"]
        mapped = WeKnoraAdapter._page_from_map(
            WeKnoraAdapter._page_map_from_metadata(metadata),
            chunk.get("start_at"), chunk.get("end_at")
        )
        if mapped:
            metadata["page"], metadata["page_end"] = mapped
            metadata["page_source"] = "file_page_map"

        return {
            "id": chunk.get("id") or "",
            "document": content,
            "metadata": metadata,
            "distance": WeKnoraAdapter._normalize_score(chunk.get("score", 0.0)),
            "page": metadata.get("page"),
            "page_end": metadata.get("page_end"),
            "collection": collection,
        }

    @staticmethod
    def _page_from_map(raw_map: Any, start_at: Any, end_at: Any) -> tuple[int, int] | None:
        if isinstance(raw_map, str):
            try:
                raw_map = json.loads(raw_map)
            except (TypeError, ValueError):
                return None
        if not isinstance(raw_map, list) or not isinstance(start_at, int) or not isinstance(end_at, int):
            return None
        overlaps = []
        for item in raw_map:
            if not isinstance(item, dict) or not isinstance(item.get("page"), int):
                continue
            begin, finish = item.get("start_at"), item.get("end_at")
            if isinstance(begin, int) and isinstance(finish, int) and start_at < finish and end_at > begin:
                overlaps.append(item["page"])
        return (min(overlaps), max(overlaps)) if overlaps else None

    @staticmethod
    def _page_map_from_metadata(metadata: Mapping[str, Any]) -> Any:
        """读取完整页码映射，兼容旧的单字段和新的分段字段格式。"""
        raw_map = metadata.get("page_map")
        if raw_map:
            return raw_map
        parts = [metadata[key] for key in sorted(metadata)
                 if re.fullmatch(r"page_map_part_\d{3}", key)]
        if not parts:
            return None
        expected_count = metadata.get("page_map_part_count")
        if expected_count is not None:
            try:
                if int(expected_count) != len(parts):
                    return None
            except (TypeError, ValueError):
                return None
        try:
            compact = json.loads("".join(str(part) for part in parts))
        except (TypeError, ValueError):
            return None
        if not isinstance(compact, list):
            return None
        return [{"page": item[0], "start_at": item[1], "end_at": item[2]}
                for item in compact
                if isinstance(item, list) and len(item) == 3
                and all(isinstance(value, int) for value in item)]

    # ---------- RetrievalPort 协议 ----------

    @staticmethod
    def _query_terms(query_text: str) -> tuple[str, ...]:
        """提取最小可解释词锚点；只用于无关查询的保守 no_target 护栏。"""
        terms = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9][A-Za-z0-9._+\-]{2,}", query_text or "")
        return tuple(dict.fromkeys(term.lower() for term in terms))

    @classmethod
    def _has_query_anchor(cls, query_text: str, rows: Sequence[Dict[str, Any]]) -> bool:
        terms = cls._query_terms(query_text)
        if not terms:
            return True
        for row in rows:
            metadata = row.get("metadata") or {}
            haystack = " ".join([str(row.get("document") or ""), str(metadata.get("source") or "")]).lower()
            if any(term in haystack for term in terms):
                return True
        return False

    def _resolve_kb_id(self, collection_name: str) -> Optional[str]:
        """按集合名解析目标 kb_id（T13 多库路由）。

        命中 WEKNORA_KB_MAP（键=完整集合名，由 collection_full_name() 产出）→ 用其
        kb_id；未命中 → 回退默认 self.kb_id（单库直连）。两者都没有 → None。

        ⚠️ 一对多映射时返回**第一个** kb_id。仅用于「必须给单个库」的旧调用面
        （如 health/治理）；检索请用 `_resolve_kb_ids`，否则会静默只查一个库。
        """
        kb_ids = self._resolve_kb_ids(collection_name)
        return kb_ids[0] if kb_ids else None

    def _resolve_kb_ids(self, collection_name: str) -> List[str]:
        """按集合名解析**全部**目标 kb_id（一对多路由，28 号文档 §多库）。

        为什么需要一对多：WeKnora 的物理库按**部门**切（规范1 国标 / 规范5 住建部 /
        规范2 交通部 / 规范7 其他部…），而业务逻辑集合按**专业域**切（general-db /
        manual-db / formwork-check…）。两套分类法正交，一个逻辑集合天然对应多个物理库
        ——例如 `general-db` 要同时查 规范1+规范5+规范2+规范7。旧的「一个键一个
        kb_id」把这种多对多强行压成一对一，接上后规范证据会成片丢失。

        顺序语义：返回顺序即优先级展示顺序，用 dict.fromkeys 去重且保序。
        未命中映射 → 回退 self.kb_id（单库直连）；都为空 → 空列表（调用方判失败）。
        """
        raw = self.kb_map.get(collection_name)
        if isinstance(raw, str):
            values = [raw] if raw.strip() else []
        elif isinstance(raw, (list, tuple)):
            values = [str(v).strip() for v in raw if str(v or "").strip()]
        else:
            values = []
        if not values and self.kb_id:
            values = [self.kb_id]
        return list(dict.fromkeys(values))

    def query_documents(
        self,
        collection_name: str,
        query_text: str,
        n_results: int = 5,
    ) -> List[Dict[str, Any]]:
        """混合检索（向量 + BM25）：返回**去重后**的前 n_results 条。

        collection_name 经 WEKNORA_KB_MAP 路由到对应 kb_id（命中），未命中回退
        WEKNORA_KB_ID（单库直连）。阈值取构造参数（默认 0.5/0.3，=契约实测值）。

        一对多：映射值为数组时走 `hybrid-search` 的 `knowledge_base_ids`（一次请求）。

        ⚠️ 服务端**并不做跨榜融合**（2026-10-20 实测，勿再照抄文档）：
        `hybrid-search` 返回的是「向量榜 + 关键词榜」两段顺序**拼接**，`match_count`
        按每个榜（多库时还要乘库数）分别生效，同一 chunk 两榜都命中就会**出现两次**。
        实测：单库 n=4 返 8 条（4 向量 + 4 关键词，其中 3 条重复）；
        9 库 n=4 返 18 条。若照单全收，下游证据会重复且超量。

        故此处统一收口为 `_finalize_rows`：去重（保留最高原始分）→ 按原始分降序 →
        截断到 n_results。分数语义见 `_normalize_score`（本批 RRF 分在 0.011~0.017，
        归一后 distance 挤在 0.98 附近，**只保证序不保证跨集合可比**）。
        """
        if not query_text.strip():
            return []
        # 过采再收口：转述/图片占位会被 `_finalize_rows` 剔掉（实测约占召回量的 30~40%），
        # 若按 n_results 原样取，n=3 常只兑现 2 条，上层会误判「该集合只检索到 2 条」。
        fetch = min(max(n_results * self._FETCH_MULTIPLIER, n_results), self._FETCH_MAX)
        chunks = self._raw_chunks(collection_name, query_text, fetch)
        if chunks is None:
            return []
        return self._finalize_rows(chunks, collection_name, n_results)

    # 召回预算的过采倍数 / 上限：证据链会剔掉转述与图片占位，必须多取再收口，
    # 否则 `n_results` 兑现不了（见 `query_documents`）。
    _FETCH_MULTIPLIER = 4
    _FETCH_MAX = 40

    def _raw_chunks(
        self, collection_name: str, query_text: str, count: int
    ) -> Optional[List[Dict[str, Any]]]:
        """取**未截断**的原始 chunk 列表；失败记降级事实并返回 None。

        为什么单独抽出来：`query_documents` 会把结果收口到 `n_results`，而按来源范围
        过滤必须发生在**截断之前**（否则先截断再筛，筛完可能一条不剩）。结构化查询
        需要的是原料，截断仍由 `_finalize_rows` 统一负责。
        """
        kb_ids = self._resolve_kb_ids(collection_name)
        if not kb_ids:
            self._record_failure(
                RetrievalBackendError(
                    "WEKNORA_KB_ID 未配置且集合未命中 WEKNORA_KB_MAP"
                )
            )
            return None
        if len(kb_ids) > 1:
            return self._fetch_multi_kb_chunks(kb_ids, query_text, count)

        kb_id = kb_ids[0]
        payload = {
            "query_text": query_text,
            "vector_threshold": self.vector_threshold,
            "keyword_threshold": self.keyword_threshold,
            "match_count": count,
        }
        try:
            resp = self._httpx().post(
                f"/knowledge-bases/{kb_id}/hybrid-search", json=payload
            )
        except Exception as exc:  # noqa: BLE001 —— 连接/超时统一降级
            self._record_failure(exc)
            return None
        if resp.status_code != 200:
            self._record_failure(
                RetrievalBackendError(f"hybrid-search HTTP {resp.status_code}")
            )
            return None
        try:
            body = resp.json()
        except Exception as exc:  # noqa: BLE001
            self._record_failure(exc)
            return None
        return body.get("data") or []

    @staticmethod
    def _chunk_identity(chunk: Dict[str, Any]) -> Any:
        """同一 chunk 的稳定身份。

        顺序即优先级，**必须先是 chunk 自身的 id**：

        2026-10-20 修正：原实现把 `(knowledge_id, start_at, end_at)` 放在最前，但
        WeKnora 对图片页产出的 `image_caption` 与 `image_ocr` **共享同一字符区间**
        （实测同页两者 start_at/end_at 均为 0），于是一页的「VLM 转述」和「逐字 OCR」
        被判成同一条、只留分高的那条（转述语气更贴查询，分总是更高）。逐字条文在
        进入证据链之前就被去重丢掉了 —— 这才是 A/B 里「只剩转述」的真正原因。

        去重的原始目的只是「同一 chunk 在向量榜与关键词榜各出现一次」，而两榜返回的是
        **同一个 id**，所以优先用 id 既精确达成目的，又不会误合并同页的不同形态。
        """
        if chunk.get("id"):
            return ("id", chunk["id"])
        kid = chunk.get("knowledge_id")
        if kid and chunk.get("chunk_index") is not None:
            return ("idx", kid, chunk["chunk_index"])
        start, end = chunk.get("start_at"), chunk.get("end_at")
        if kid and start is not None:
            # 无 id 也无 chunk_index 时才退化到字符区间，并带上形态避免同页互相顶掉
            return ("span", kid, start, end, chunk.get("chunk_type"))
        content = chunk.get("content") or ""
        return ("text", chunk.get("knowledge_title") or "", hash(content))

    # 证据可用性：扫描件入库会产出「纯图片 chunk」（正文只有一个 markdown 图片链接），
    # 这种 chunk 对报告毫无引用价值（渲染成一行残缺图片地址），必须在进证据前剔除。
    _MEDIA_MARKUP_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
    _MIN_CITABLE_CHARS = 20

    # 转述型 chunk：服务端 VLM 对页面图像写的「自然语言概述」，**不是条文原文**。
    #
    # 2026-10-20 实测（这是把「规范库入库形态有问题」纠正为「读取侧排序有问题」的关键）：
    # 同一页会同时产出两条 chunk——`image_ocr`（逐字，含 LaTeX 公式与 markdown 表格）
    # 与 `image_caption`（"该图像为…主要内容包括："）。转述的语言风格天然贴近自然语言
    # 查询，在向量榜上稳定压过逐字法条，于是报告引用了「页面描述」当依据。
    #
    # 结论：语料没坏，是证据链收了摘要。这两类一律不进证据链。
    _PARAPHRASE_CHUNK_TYPES = frozenset({"image_caption", "summary"})
    # 仅当响应缺 chunk_type（旧响应/其他后端）时才按开头语兜底判定，避免误杀正文。
    _PARAPHRASE_HEAD_PREFIXES = ("该图像", "该图片", "此图像", "此图", "本图", "上图", "下图")

    @classmethod
    def _is_citable(cls, content: str) -> bool:
        """去掉图片/媒体标记后，是否仍有足够正文可引用。"""
        stripped = cls._MEDIA_MARKUP_RE.sub("", content or "")
        stripped = re.sub(r"\s+", "", stripped)
        return len(stripped) >= cls._MIN_CITABLE_CHARS

    @classmethod
    def _is_paraphrase(cls, chunk: Dict[str, Any]) -> bool:
        """是否为 VLM 转述/自动摘要（非条文原文，不可作为规范依据引用）。"""
        chunk_type = str(chunk.get("chunk_type") or "").strip().lower()
        if chunk_type:
            return chunk_type in cls._PARAPHRASE_CHUNK_TYPES
        head = (chunk.get("content") or "").lstrip()
        return head.startswith(cls._PARAPHRASE_HEAD_PREFIXES)

    def _finalize_rows(
        self, chunks: Sequence[Dict[str, Any]], collection: str, n_results: int
    ) -> List[Dict[str, Any]]:
        """去重 → 剔除转述与不可引用 chunk → 按原始分降序 → 截断，兑现 `n_results` 契约。

        为什么保留「最高原始分」而不是重算融合分：`distance` 的归一公式
        （1/(1+score)）已被契约测试钉死为**逐 chunk** 语义；若另造融合分会与
        distance 打架，出现「排序说 A 更好、分数说 B 更好」。故只做「取最好的一次
        命中」——顺序与 distance 同向，可解释。

        三级降级（顺序即优先级）：
        1. **逐字且可引用**（`text`/`image_ocr`，正文够长）——正常路径；
        2. **可引用但含转述**——某集合 OCR 全线失败时的兜底，日志如实记录，
           报告侧据 `metadata.chunk_type` 可标注「本条为页面描述，非条文原文」；
        3. **未过滤全集**——连纯图片都剩不下时仍退回，避免上层把「只有图片」
           误判成「查无依据」（那是不实结论）。

        为什么剔除图片 chunk（2026-10-20 A/B 实测）：扫描件入库会产出
        `![](resource://..._page_91.jpg)`，进报告就是一行坏图链接。
        """
        best: Dict[Any, tuple[float, int, Dict[str, Any]]] = {}
        for index, chunk in enumerate(chunks):
            key = self._chunk_identity(chunk)
            score = float(chunk.get("score") or 0.0)
            current = best.get(key)
            # 同分时保留先出现的（服务端顺序 = 向量榜优先），保持可预期
            if current is None or score > current[0]:
                best[key] = (score, index, chunk)
        ordered = sorted(best.values(), key=lambda item: (-item[0], item[1]))

        verbatim = [
            item for item in ordered
            if not self._is_paraphrase(item[2])
            and self._is_citable(item[2].get("content") or "")
        ]
        if verbatim:
            picked = verbatim
        else:
            picked = [
                item for item in ordered
                if self._is_citable(item[2].get("content") or "")
            ]
            if picked:
                logger.info(
                    "集合 %s 未命中逐字条文，退回 %d 条转述/摘要 chunk（已如实标注"
                    "chunk_type，报告侧应注明「页面描述，非条文原文」）",
                    collection, len(picked),
                )
            elif ordered:
                logger.warning(
                    "集合 %s 检索命中 %d 条但全部为图片/媒体 chunk，退回未过滤结果"
                    "（避免上层把「全是图片」误判成「查无依据」）",
                    collection, len(ordered),
                )
                picked = ordered
        return [self._chunk_to_row(item[2], collection) for item in picked[:n_results]]

    def _fetch_multi_kb_chunks(
        self, kb_ids: Sequence[str], query_text: str, n_results: int
    ) -> List[Dict[str, Any]]:
        """一对多集合：**一次请求**取原始 chunk 列表（截断交给 `_finalize_rows`）。

        为什么仍走单请求：服务端按库分别召回后拼成一个列表，虽不做跨榜融合，
        但一次请求就能拿全各库候选，比本地逐库发 N 个请求省往返、也避免各库
        阈值口径漂移。

        契约（已实测）：路径里的 `:id` 必须同时出现在 body 的 `knowledge_base_ids` 中，
        否则 404；多库时路径取 `kb_ids[0]`。
        """
        payload = {
            "query_text": query_text,
            "knowledge_base_ids": list(kb_ids),
            "vector_threshold": self.vector_threshold,
            "keyword_threshold": self.keyword_threshold,
            "match_count": n_results,
        }
        try:
            resp = self._httpx().post(
                f"/knowledge-bases/{kb_ids[0]}/hybrid-search", json=payload
            )
        except Exception as exc:  # noqa: BLE001 —— 连接/超时统一降级
            self._record_failure(exc)
            return []
        if resp.status_code != 200:
            # 403 最常见原因是读凭据的库范围不覆盖某个已登记库（见
            # factory.resolve_weknora_read_key）；错误里带上库数便于定位。
            self._record_failure(
                RetrievalBackendError(
                    f"hybrid-search HTTP {resp.status_code}"
                    f"（多库检索 {len(kb_ids)} 个库；403 时检查读凭据库范围）"
                )
            )
            return []
        try:
            body = resp.json()
        except Exception as exc:  # noqa: BLE001
            self._record_failure(exc)
            return []
        chunks = body.get("data") or []
        # 来源库回填在这里做（_chunk_to_row 之后行结构已定型，故先写进 chunk 的 metadata）
        for chunk in chunks:
            if chunk.get("knowledge_base_id"):
                meta = dict(chunk.get("metadata") or {})
                meta["kb_id"] = chunk["knowledge_base_id"]
                chunk["metadata"] = meta
        return chunks

    def query_scoped(
        self,
        query_text: str,
        *,
        kb_ids: Sequence[str],
        knowledge_ids: Optional[Sequence[str]] = None,
        top_k: int = 8,
    ) -> List[Dict[str, Any]]:
        """跨库 + 按文件限定的混合检索（知识库接入 P0，§6.1）。

        契约要点（开发文档 §5.1，均为实测结论）：
        - 端点路径里的 `:id` **必须同时出现在 body 的 `knowledge_base_ids` 里**，
          否则 404；多库检索时路径取 `kb_ids[0]`。
        - `knowledge_ids` 为 None / 空 → **不带该字段**（= 整库召回，§6.3 / R7）。
        - 阈值取构造参数（P0 用 .env 的 0.1/0.1，模板默认 0.5/0.3 在本语料召不动）。

        返回结构与 `query_documents` 同形；额外补 `metadata.kb_id`（溯源用，来自 chunk 的
        `knowledge_base_id`）——只在本方法补，不改 `_chunk_to_row` 的既有输出。
        """
        if not query_text.strip():
            return []
        scope_kbs = [k for k in (kb_ids or []) if k]
        if not scope_kbs:
            self._record_failure(
                RetrievalBackendError("query_scoped 未提供 kb_ids（无法确定检索范围）")
            )
            return []

        scoped_files = [k for k in (knowledge_ids or []) if k]
        payload = self._scoped_payload(query_text, scope_kbs, scoped_files or None, top_k)

        try:
            resp = self._httpx().post(
                f"/knowledge-bases/{scope_kbs[0]}/hybrid-search", json=payload
            )
        except Exception as exc:  # noqa: BLE001 —— 连接/超时统一降级
            self._record_failure(exc)
            return []
        if resp.status_code != 200:
            self._record_failure(
                RetrievalBackendError(f"hybrid-search HTTP {resp.status_code}")
            )
            return []
        try:
            body = resp.json()
        except Exception as exc:  # noqa: BLE001
            self._record_failure(exc)
            return []

        rows: List[Dict[str, Any]] = []
        for chunk in body.get("data") or []:
            rows.append(self._scoped_chunk_to_row(chunk, scope_kbs[0]))
        return rows

    def _scoped_payload(self, query_text: str, kb_ids: Sequence[str],
                        knowledge_ids: Optional[Sequence[str]], top_k: int) -> Dict[str, Any]:
        payload = {"query_text":query_text, "knowledge_base_ids":list(kb_ids),
                   "match_count":top_k, "vector_threshold":self.vector_threshold,
                   "keyword_threshold":self.keyword_threshold}
        if knowledge_ids is not None:
            payload["knowledge_ids"] = list(knowledge_ids)
        return payload

    def _scoped_chunk_to_row(self, chunk: Dict[str, Any], collection: str) -> Dict[str, Any]:
        row = self._chunk_to_row(chunk, collection)
        if chunk.get("knowledge_base_id"):
            row["metadata"]["kb_id"] = chunk["knowledge_base_id"]
        if chunk.get("score") is not None:
            row["metadata"]["raw_score"] = chunk["score"]
        return row

    def query_scoped_reported(
        self, query_text: str, *, kb_ids: Sequence[str],
        knowledge_ids: Optional[Sequence[str]] = None, top_k: int = 8,
        timeout_seconds: Optional[float] = None,
    ) -> ScopedQueryOutcome:
        """新 Provider 路径的单次报告；不读写共享 failures/last_error。"""
        def failure(code: str, kind: ScopedQueryError = ScopedQueryError.BACKEND_UNAVAILABLE) -> ScopedQueryOutcome:
            return ScopedQueryOutcome((), EngineProvenance("weknora", None, True, code),
                ScopeFilterCapability.NATIVE, False, code, kind, code)

        if not query_text.strip():
            return failure("empty_query", ScopedQueryError.EMPTY_QUERY)
        scope_kbs = list(dict.fromkeys(k for k in kb_ids if isinstance(k, str) and k.strip()))
        files = None if knowledge_ids is None else list(dict.fromkeys(
            k for k in knowledge_ids if isinstance(k, str) and k.strip()))
        if not scope_kbs or files == [] or top_k == 0:
            return failure("empty_scope", ScopedQueryError.EMPTY_SCOPE)
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 0:
            return failure("invalid_query_budget")
        timeout = self.timeout_ms / 1000 if timeout_seconds is None else timeout_seconds
        if isinstance(timeout, bool) or not isinstance(timeout, (int,float)) or not math.isfinite(timeout) or timeout <= 0:
            return failure("invalid_query_timeout")
        try:
            resp = self._httpx().post(f"/knowledge-bases/{scope_kbs[0]}/hybrid-search",
                json=self._scoped_payload(query_text, scope_kbs, files, top_k), timeout=timeout)
        except httpx.TimeoutException:
            return failure(ProviderTimeoutError.code)
        except httpx.HTTPError:
            return failure(ProviderUnreachableError.code)
        if resp.status_code != 200:
            code = (ProviderAuthError.code if resp.status_code in {401,403}
                else ProviderRateLimitError.code if resp.status_code == 429
                else ProviderServerError.code if resp.status_code >= 500
                else ProviderNotFoundError.code if resp.status_code == 404
                else ProviderProtocolError.code)
            return failure(code)
        try:
            body = resp.json()
            if not isinstance(body, dict) or body.get("success") is not True:
                return failure(ProviderProtocolError.code)
            # WeKnora returns `data: null` for a valid empty knowledge base;
            # normalize that shape to an empty result instead of reporting a
            # protocol failure for an intentionally empty BS library.
            raw_data = body.get("data")
            if raw_data is None:
                raw_data = []
            if not isinstance(raw_data, list):
                return failure(ProviderProtocolError.code)
            rows, mismatch = [], False
            for chunk in raw_data:
                if (not isinstance(chunk, dict)
                    or not isinstance(chunk.get("content"), str)
                    or not isinstance(chunk.get("id"), str) or not chunk["id"]
                    or (chunk.get("metadata") is not None and not isinstance(chunk["metadata"], dict))
                    or isinstance(chunk.get("score"), bool) or not isinstance(chunk.get("score"), (int,float))
                    or not math.isfinite(chunk["score"]) or chunk["score"] < 0):
                    return failure(ProviderProtocolError.code)
                kb, file_id = chunk.get("knowledge_base_id"), chunk.get("knowledge_id")
                if kb not in scope_kbs or not isinstance(file_id, str) or not file_id or (files is not None and file_id not in files):
                    mismatch = True
                    continue
                rows.append(self._scoped_chunk_to_row(chunk, scope_kbs[0]))
        except (ValueError, TypeError, KeyError, OverflowError):
            return failure(ProviderProtocolError.code)
        reason = "returned_scope_mismatch" if mismatch else None
        if rows and not mismatch and not self._has_query_anchor(query_text, rows):
            return ScopedQueryOutcome(
                (), EngineProvenance("weknora", "weknora", True, "no_target"),
                ScopeFilterCapability.NATIVE, False, "no_target", None, "no_target"
            )
        return ScopedQueryOutcome(tuple(rows[:top_k]), EngineProvenance("weknora", "weknora", mismatch, reason),
            ScopeFilterCapability.NATIVE, not mismatch, reason)

    # 范围过滤的过采倍数 / 上限。WeKnora **不支持服务端来源过滤**（实测：给
    # `filter`/`metadata_filter` 传不匹配条件仍返回满额命中 ⇒ 字段被静默忽略），
    # 来源/文件级隔离只能由调用方的范围参数在**召回后**筛。多取一些再筛，避免筛空。
    _SCOPE_OVERFETCH = 6
    _SCOPE_MAX_FETCH = 60

    @staticmethod
    def _chunk_in_scope(chunk: Mapping[str, Any], scope: SourceScope) -> bool:
        """chunk 是否落在来源/文档范围内（客户端过滤口径）。

        来源名按 basename 比对：manifest 侧 `source_id` 是文件名，WeKnora 侧
        `knowledge_title`/`knowledge_filename` 可能带目录前缀，全串直比会漏。
        """
        def _base(value: Any) -> str:
            return re.split(r"[\\/]", str(value or ""))[-1]

        titles = {t for t in (_base(chunk.get("knowledge_title")),
                              _base(chunk.get("knowledge_filename"))) if t}
        raw_title = str(chunk.get("knowledge_title") or "")

        if scope.allowed_sources is not None:
            allowed = {_base(s) for s in scope.allowed_sources}
            allowed_raw = {str(s) for s in scope.allowed_sources}
            if not (titles & allowed) and raw_title not in allowed_raw:
                return False
        if scope.excluded_sources:
            excluded = {_base(s) for s in scope.excluded_sources}
            excluded_raw = {str(s) for s in scope.excluded_sources}
            if (titles & excluded) or raw_title in excluded_raw:
                return False
        if scope.allowed_doc_ids is not None:
            ids = {chunk.get("id"), chunk.get("knowledge_id")}
            if not (ids & set(scope.allowed_doc_ids)):
                return False
        return True

    def query_documents_scoped(
        self,
        collection_name: str,
        query_text: str,
        n_results: int = 5,
        *,
        scope: Optional[SourceScope] = None,
    ) -> ScopedQueryOutcome:
        """有范围结构化查询：范围过滤 + 引擎实况 + 执行完整性。

        能力**如实申报** `POST_FILTER_ONLY`：服务端忽略过滤条件，只能召回后筛，
        故 `scope_complete=False`——上层不得把空结果判为可靠 miss，须给出
        不完整/不可判定原因（25 号 §3.5）。

        为什么不再显式拒绝：规范/范例/手册库已迁入 WeKnora，`rag_retriever` 的证据
        锚点查询走的就是这条路径；维持"未实现"会让切后端后证据检索直接抛错
        （2026-10-20 实测：4/4 域 `RetrievalBackendError`，证据链全瘫）。
        """
        active_scope = scope or SourceScope()

        if not (query_text or "").strip():
            return ScopedQueryOutcome(
                rows=(),
                engine=EngineProvenance("weknora", None, False, "空查询文本，未执行检索"),
                filter_capability=ScopeFilterCapability.NATIVE,
                scope_complete=True,
                error_kind=ScopedQueryError.EMPTY_QUERY,
                error_detail="query_text 为空：未执行任何检索（不等于检索为空）",
            )

        if active_scope.is_explicitly_empty:
            return ScopedQueryOutcome(
                rows=(),
                engine=EngineProvenance("weknora", None, False, "明确空选，未执行检索"),
                filter_capability=ScopeFilterCapability.NATIVE,
                scope_complete=True,
                error_kind=ScopedQueryError.EMPTY_SCOPE,
                error_detail="明确空选：按规格该目标本次不查询（空数组不转换成全库）",
            )

        restricted = not active_scope.is_unrestricted
        # 与 `query_documents` 同口径过采（转述/图片占位会被剔掉）；
        # 受限时还要为"召回后过滤"再多留余量，否则筛完常只剩 1 条。
        fetch = min(max(n_results * self._FETCH_MULTIPLIER, n_results), self._FETCH_MAX)
        if restricted:
            fetch = min(max(n_results * self._SCOPE_OVERFETCH, fetch),
                        self._SCOPE_MAX_FETCH)

        chunks = self._raw_chunks(collection_name, query_text, fetch)
        if chunks is None:
            return ScopedQueryOutcome(
                rows=(),
                engine=EngineProvenance(
                    "weknora", None, True, self.last_error or "hybrid-search 未返回结果"
                ),
                filter_capability=ScopeFilterCapability.UNSUPPORTED,
                scope_complete=False,
                incomplete_reason="检索未完成：目标范围未被查询，空结果不得判为可靠 miss",
                error_kind=ScopedQueryError.BACKEND_UNAVAILABLE,
                error_detail=self.last_error or "hybrid-search 执行失败",
            )

        if restricted:
            chunks = [c for c in chunks if self._chunk_in_scope(c, active_scope)]

        rows = self._finalize_rows(chunks, collection_name, n_results)
        return ScopedQueryOutcome(
            rows=tuple(rows),
            engine=EngineProvenance("weknora", "weknora", False),
            filter_capability=(
                ScopeFilterCapability.POST_FILTER_ONLY
                if restricted else ScopeFilterCapability.NATIVE
            ),
            scope_complete=not restricted,
            incomplete_reason=(
                "WeKnora 不支持服务端来源过滤（metadata 条件被静默忽略），"
                "已按来源在召回后过滤：目标范围未被完整查询，空结果不得判为可靠 miss"
            ) if restricted else None,
        )

    def count_documents_reported(self, collection_name: str) -> ReportedCountOutcome:
        """集合内 **knowledge 条目数**（带引擎实况，供健康自检消费）。

        为什么是「文档条目数」而不是「chunk 数」：本适配器此前干脆不支持计数
        （`count_documents` 显式 raise），于是切到 WeKnora 后健康自检 11 个集合全部
        落到 `undetermined / backend_error`——**关停 Chroma 前唯一的观测手段失效了**。
        健康自检需要的本来就是「这个集合有几份资料」，此口径与 Chroma 侧按
        collection 统计文档数一致，故可比。

        一对多：一个逻辑集合可能映射多个物理库（按部门切），此处**相加**——集合规模
        是它所覆盖的全部物理库之和，只算第一个会系统性低估。

        ⚠️ 必须读响应的 `total`，不能用 `data` 长度：列表端点默认 `page_size=20`
        （2026-10-20 实测），按 data 计数会把「规范1」的 102 份看成 20 份。
        这里显式传 `page_size=1` 只取 `total`，避免为了一个数字拉全量。

        失败一律返回结构化 `error_kind`（不静默给 0）：健康自检据此判「不可判定」，
        而不是把「没数到」说成「集合为空」。
        """
        kb_ids = self._resolve_kb_ids(collection_name)
        if not kb_ids:
            return ReportedCountOutcome(
                count=0,
                engine=EngineProvenance(
                    "weknora", None, True,
                    "未配置 WEKNORA_KB_ID 且集合未命中 WEKNORA_KB_MAP",
                ),
                error_kind=ScopedQueryError.BACKEND_UNAVAILABLE,
                error_detail=(
                    f"集合 {collection_name} 未解析到任何 WeKnora 物理库——"
                    "**未执行计数**，此处的 0 不是集合规模"
                ),
            )

        total_count = 0
        for kb_id in kb_ids:
            try:
                resp = self._httpx().get(
                    f"/knowledge-bases/{kb_id}/knowledge",
                    params={"page": 1, "page_size": 1},
                )
            except Exception as exc:  # noqa: BLE001 —— 连接/超时统一判不可判定
                return ReportedCountOutcome(
                    count=0,
                    engine=EngineProvenance(
                        "weknora", None, True, f"计数请求失败：{exc!r}"
                    ),
                    error_kind=ScopedQueryError.BACKEND_UNAVAILABLE,
                    error_detail=(
                        f"集合 {collection_name} 计数未完成（kb_id={kb_id}：{exc!r}）——"
                        "0 不得判为空库"
                    ),
                )
            if resp.status_code != 200:
                return ReportedCountOutcome(
                    count=0,
                    engine=EngineProvenance(
                        "weknora", None, True, f"计数 HTTP {resp.status_code}"
                    ),
                    error_kind=ScopedQueryError.BACKEND_UNAVAILABLE,
                    error_detail=(
                        f"集合 {collection_name} 计数未完成"
                        f"（kb_id={kb_id}：HTTP {resp.status_code}）——0 不得判为空库"
                    ),
                )
            try:
                body = resp.json()
            except Exception as exc:  # noqa: BLE001
                return ReportedCountOutcome(
                    count=0,
                    engine=EngineProvenance(
                        "weknora", None, True, f"计数响应非法 JSON：{exc!r}"
                    ),
                    error_kind=ScopedQueryError.BACKEND_UNAVAILABLE,
                    error_detail=f"集合 {collection_name} 计数响应无法解析：{exc!r}",
                )
            raw_total = body.get("total")
            if raw_total is None:
                # 缺 total 时退回 data 长度，但必须**明说**可能低估（分页默认 20）
                rows = body.get("data")
                size = len(rows) if isinstance(rows, list) else 0
                logger.warning(
                    "WeKnora 计数响应缺少 total 字段（kb=%s），退回 data 长度 %d，可能低估",
                    kb_id, size,
                )
                total_count += size
                continue
            try:
                total_count += int(raw_total)
            except (TypeError, ValueError):
                return ReportedCountOutcome(
                    count=0,
                    engine=EngineProvenance(
                        "weknora", None, True, f"total 字段非法：{raw_total!r}"
                    ),
                    error_kind=ScopedQueryError.BACKEND_UNAVAILABLE,
                    error_detail=f"集合 {collection_name} 的 total 不是整数：{raw_total!r}",
                )

        return ReportedCountOutcome(
            count=total_count,
            engine=EngineProvenance("weknora", "weknora", False),
        )

    # ---------- 文档正文（§1.2.7） ----------

    def _fetch_chunks(self, knowledge_id: str, chunk_type: str) -> List[Dict[str, Any]]:
        """分页拉取某类分块；任何失败都抛 `RetrievalBackendError`，不返回半截正文。"""
        chunks: List[Dict[str, Any]] = []
        page = 1
        while True:
            if page > fulltext.MAX_CHUNK_PAGES:
                raise RetrievalBackendError(
                    f"chunks 分页超过 {fulltext.MAX_CHUNK_PAGES} 页（knowledge_id={knowledge_id}）"
                )
            params = [
                ("page", page),
                ("page_size", fulltext.CHUNK_PAGE_SIZE),
                ("chunk_type", chunk_type),
            ]
            try:
                resp = self._httpx().get(f"/chunks/{knowledge_id}", params=params)
            except Exception as exc:  # noqa: BLE001 —— 连接/超时统一降级
                self._record_failure(exc)
                raise RetrievalBackendError(
                    f"chunks 请求失败（{type(exc).__name__}）"
                ) from exc
            if resp.status_code != 200:
                err = RetrievalBackendError(
                    f"chunks HTTP {resp.status_code}（chunk_type={chunk_type}）"
                )
                self._record_failure(err)
                raise err
            try:
                body = resp.json()
            except Exception as exc:  # noqa: BLE001
                self._record_failure(exc)
                raise RetrievalBackendError("chunks 响应不是合法 JSON") from exc

            data = body.get("data") or []
            chunks.extend(data)
            if not data:
                break
            total = body.get("total")
            try:
                total_int = int(total)
            except (TypeError, ValueError):
                total_int = None
            if total_int is not None and len(chunks) >= total_int:
                break
            if len(data) < fulltext.CHUNK_PAGE_SIZE:
                break
            page += 1
        return chunks

    def fetch_document_text(
        self,
        knowledge_id: str,
        *,
        offset: int = 0,
        max_chars: Optional[int] = fulltext.READ_WINDOW_CHARS,
    ) -> Dict[str, Any]:
        """按知识 ID 拉取 text + image_ocr 分块，客户端拼成正文后按窗口返回。

        返回 `text/total_chars/offset/next_offset/done/chunk_offsets/note`。
        `max_chars=None` 返回全文（工具缓存用），否则按 READ_WINDOW_CHARS 切片。

        失败（非 200 / 超时 / 连接失败 / 坏 JSON）一律 `RetrievalBackendError`，
        不返回空正文假装「文档为空」。
        """
        kid = (knowledge_id or "").strip()
        if not kid:
            raise RetrievalBackendError("缺少 knowledge_id，无法读取文档正文")

        text_chunks = self._fetch_chunks(kid, fulltext.CHUNK_TYPE_TEXT)
        ocr_chunks = self._fetch_chunks(kid, fulltext.CHUNK_TYPE_OCR)
        doc = fulltext.assemble_document(text_chunks, ocr_chunks)
        result = fulltext.slice_document(doc["text"], offset=offset, max_chars=max_chars)
        result["chunk_offsets"] = doc["chunk_offsets"]
        result["note"] = doc["note"]
        return result

    def fetch_document_text_reported(
        self,
        knowledge_id: str,
        *,
        kb_id: str,
        offset: int = 0,
        max_chars: Optional[int] = fulltext.READ_WINDOW_CHARS,
    ) -> Dict[str, Any]:
        """受控条款读取：原文分块身份、分页完整性和正文窗口作为一次请求报告。

        调用方须先核对文件授权。本方法仅校验远端响应范围，不解析或扩大授权。
        任一页失败不返回半截正文；错误只使用固定分类，不能带出连接或凭据。
        """
        def failure(code: str) -> Dict[str, Any]:
            return {"status": "undetermined", "scope_complete": False, "error_code": code,
                    "text": "", "chunks": [], "total_chars": 0, "offset": 0,
                    "next_offset": 0, "done": False, "chunk_offsets": {}, "note": ""}

        if (not isinstance(knowledge_id, str) or not knowledge_id.strip()
                or not isinstance(kb_id, str) or not kb_id.strip()
                or any(char in knowledge_id for char in "/\\?#")):
            return failure("document_identity_invalid")
        deadline = monotonic() + self.timeout_ms / 1000.0
        chunk_groups: dict[str, list[dict[str, Any]]] = {}
        for chunk_type in fulltext.READ_CHUNK_TYPES:
            chunks: list[dict[str, Any]] = []
            seen_ids: set[str] = set()
            expected_total: int | None = None
            total_declared = False
            for page in range(1, fulltext.MAX_CHUNK_PAGES + 1):
                remaining = deadline - monotonic()
                if remaining <= 0:
                    return failure("document_scan_timeout")
                try:
                    response = self._httpx().get(f"/chunks/{knowledge_id}", params={
                        "page": page, "page_size": fulltext.CHUNK_PAGE_SIZE,
                        "chunk_type": chunk_type,
                    }, timeout=remaining)
                    if response.status_code != 200:
                        return failure("document_request_failed")
                    body = response.json()
                except Exception:  # 故障文本可能含 URL/Secret；报告只返回固定分类。
                    return failure("document_request_failed")
                if (not isinstance(body, dict) or body.get("success") is False
                        or not isinstance(body.get("data"), list)):
                    return failure("document_protocol_error")
                data = body["data"]
                raw_total = body.get("total")
                declared = raw_total is not None
                if declared and (isinstance(raw_total, bool) or not isinstance(raw_total, int) or raw_total < 0):
                    return failure("document_protocol_error")
                if page == 1:
                    expected_total, total_declared = raw_total, declared
                elif declared != total_declared or raw_total != expected_total:
                    return failure("document_total_changed")
                for chunk in data:
                    if (not isinstance(chunk, dict) or not isinstance(chunk.get("id"), str)
                            or not chunk["id"].strip() or not isinstance(chunk.get("content"), str)):
                        return failure("document_chunk_identity_missing")
                    if chunk.get("knowledge_id") != knowledge_id or any(
                        chunk.get(key) != kb_id for key in ("kb_id", "knowledge_base_id") if chunk.get(key) is not None
                    ):
                        return failure("document_scope_mismatch")
                    if chunk.get("chunk_type") != chunk_type:
                        return failure("document_chunk_type_mismatch")
                    if chunk["id"] in seen_ids:
                        return failure("document_repeated_page")
                    seen_ids.add(chunk["id"])
                    chunks.append(chunk)
                if expected_total is not None:
                    if len(chunks) > expected_total:
                        return failure("document_scan_incomplete")
                    if len(chunks) == expected_total:
                        break
                    if not data:
                        return failure("document_scan_incomplete")
                elif not data:
                    break
            else:
                return failure("document_scan_limit")
            chunk_groups[chunk_type] = chunks
        doc = fulltext.assemble_document(
            chunk_groups[fulltext.CHUNK_TYPE_TEXT], chunk_groups[fulltext.CHUNK_TYPE_OCR])
        result = fulltext.slice_document(doc["text"], offset=offset, max_chars=max_chars)
        safe_chunks: list[dict[str, Any]] = []
        for chunks in chunk_groups.values():
            for chunk in chunks:
                safe = {key: chunk[key] for key in (
                    "id", "knowledge_id", "chunk_type", "chunk_index", "content", "start_at", "end_at"
                ) if key in chunk and isinstance(chunk[key], (str, int, float, type(None)))
                }
                safe["kb_id"] = kb_id
                metadata = self._chunk_to_row({**chunk, "score": 0,
                    "metadata": chunk.get("metadata") if isinstance(chunk.get("metadata"), Mapping) else {}}, "").get("metadata") or {}
                safe.update({key: metadata[key] for key in ("page", "page_end", "page_source") if key in metadata})
                safe_chunks.append(safe)
        trace = fulltext.trace_document_assembly(
            chunk_groups[fulltext.CHUNK_TYPE_TEXT], chunk_groups[fulltext.CHUNK_TYPE_OCR])
        result.update(status="ok", scope_complete=True, error_code=None, chunks=safe_chunks,
                      chunk_offsets=doc["chunk_offsets"], note=doc["note"], assembly_trace=trace)
        return result

    # 只读检索引擎无需写入/删除/计数；未接入处按检索端口契约显式失败，
    # 只读检索引擎无需写入/删除；未接入处按检索端口契约显式失败，
    # 不静默返回假成功（上层在双跑/回退期已保证写路径只走 Chroma）。
    def add_documents(
        self,
        collection_name: str,
        texts: List[str],
        metadatas: Optional[List[Dict[str, Any]]] = None,
        ids: Optional[List[str]] = None,
    ) -> None:
        raise RetrievalBackendError("WeKnoraAdapter 不支持 add_documents（写入走 T08 入库链路）")

    def delete_collection(self, collection_name: str) -> None:
        raise RetrievalBackendError("WeKnoraAdapter 不支持 delete_collection（删除走 T08 管理链路）")

    def delete_by_source(
        self,
        collection_name: str,
        source_name: str,
        doc_id_prefix: str,
    ) -> None:
        raise RetrievalBackendError("WeKnoraAdapter 不支持 delete_by_source（删除走 T08 管理链路）")

    def count_documents(self, collection_name: str) -> int:
        """兼容包装：只返回数字（引擎实况见 `count_documents_reported`）。

        2026-10-20 起不再 raise：健康自检把「后端不支持计数」当 `undetermined`，
        这与「集合真的为空」是完全不同的状态。切到 WeKnora 后 11 个集合全不可判定，
        等于失去关停 Chroma 前的观测手段，故补齐能力。

        与 Chroma 侧同一兼容语义：取不到数时返回 0 而**不抛**，要区分「真的 0」与
        「没数到」必须读 `count_documents_reported().error_kind`。
        """
        return int(self.count_documents_reported(collection_name).count)
