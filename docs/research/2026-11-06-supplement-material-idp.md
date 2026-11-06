# 企业级文档补件与增量审查：IDP 官方资料调研

日期：2026-11-06
范围：文档分类与拆分、字段级置信度与原文 grounding、人工复核、人机协同、版本/发布语义、RAG 元数据过滤。
目标：为工程分包审查中的“补件后增量审查”提供可审计的设计依据。本文件只做只读调研，不代表已完成业务实现、部署或客户验收。

## 结论先行

1. **先分类/拆分，再按文档单元抽取。** Azure 自定义分类器可以识别一个输入中包含的多个文档及其页码范围；Google Document AI 以 splitter/classifier 和 page anchors 表达文档边界；Textract 的输出则以 `Block`、父子/键值关系和页码组织内容。补件不应把整包重新当成一份“新文档”覆盖旧结果，而应记录新增文档单元及其页范围。
2. **置信度必须是字段级、可定位、可复核的证据，而不是结论。** Azure 明确区分 OCR/read 置信度和字段/page 置信度；Google Entity 同时提供 `confidence`、`textAnchor`、`pageAnchor`/bounding polygon；Textract 的每个 Block 可带 `Confidence`、`Geometry`、`Relationships`。企业系统应保存原始值、规范化值、置信度、页/坐标/文本偏移、模型版本和原始响应。
3. **低置信度、缺失字段、来源无法 grounding、规则冲突都应进入人工复核。** Azure 文档直接建议用置信度决定自动接受或转人工；Google custom extractor 将置信度阈值用于单实体人工复核；AWS A2I 支持按键值/词块置信区间、缺失键和随机抽样触发人工循环。阈值应按错误代价和字段重要性校准，不能把一个全局阈值当作业务真值。
4. **人工复核应产生独立、可审计的修订事件。** 保留机器原始预测和原文 grounding；人工修订另存 reviewer、时间、理由、修改前后值、依据页/框和复核状态。发布的是“经过复核的审查版本”，不是覆盖机器原始响应。
5. **版本与发布必须分开。** Google 区分 Stable、RC、自定义版本，并警告未指定版本时默认版本可能变化；AWS Textract 的 Adapter 通过 `AdapterId + Version` 固定推理配置；Azure 的自定义模型/分类器也有模型复制和增量训练语义。生产审查记录至少要绑定 `extractor_provider/model/version`、schema 版本、规则版本和材料版本，不能只记录“当前模型”。
6. **RAG 召回必须先按权限和业务元数据过滤，再做语义/关键词检索。** OpenAI File Search/Vector Store 支持基于文件 attributes 的比较/复合过滤，结果返回 attributes、file_id、filename 和 score。项目应把项目、分包包、文档类型、材料版本、有效期、复核状态和可见范围作为强制元数据，避免把旧版、未复核或其他项目材料召回到当前审查。

## 官方资料与可核验事实

### Microsoft Azure Document Intelligence / Content Understanding

- [Interpret and improve model accuracy and confidence](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/concept-accuracy-confidence?view=doc-intel-2.1.0)：字段置信度是 0 到 1 的估计概率；2024-11-30 GA 的自定义模型支持字段内 word confidence，以及 table/row/cell confidence。文档要求同时检查 `readResults` 和 `pageResults`，并建议用置信度决定自动接受或转人工。
- [Build and train a custom classifier](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/how-to-guides/build-a-custom-classifier?view=doc-intel-4.0.0)：分类器可在单个输入中识别多个文档或多个实例，返回识别出的文档类型、confidence 和 page range；可在调用抽取模型前做 confidence-based check。
- [Custom document models](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/concept-custom?view=doc-intel-4.0.0)：自定义抽取模型基于标注数据训练；自定义神经模型覆盖结构化、半结构化和非结构化文档，v4.0 支持重叠字段、签名检测以及表/行/单元格置信度。
- [Azure Content Understanding document overview](https://learn.microsoft.com/en-us/azure/ai-services/content-understanding/document/overview)：在文档分析 API 中，confidence/grounding 是显式 opt-in；字段可带页码、空间坐标和 span（offset/length）。官方建议用置信度和 source grounding 将低置信度或 grounding 不足的结果路由到人工复核。该页当前标注为 `2026-06-01-preview`/相关 API 能力，采用时需固定目标 API 版本。

**可借鉴的企业模式：**分类器的页范围是补件拆分的边界证据；抽取时同时保存 OCR 词级证据和字段级证据；字段没有可靠 source span/坐标时不得直接进入自动发布。

### Google Cloud Document AI

- [Document resource reference](https://docs.cloud.google.com/document-ai/docs/reference/rest/v1/Document)：Entity 提供 `confidence`（0 到 1）、`textAnchor`、`pageAnchor`、`normalizedValue`、嵌套 `properties` 和 `provenance`；PageRef 可带页索引、bounding polygon 和页面元素 confidence。
- [Document splitters behavior](https://docs.cloud.google.com/document-ai/docs/splitters)：Splitter 输出以 Entity 表示子文档，带文档类型、confidence 和 `pageAnchor.pageRefs[]` 页范围；Splitter 只识别边界，不会替业务物理拆分 PDF，实际拆分需由应用或 Document AI Toolbox 执行。
- [Handle processing response](https://docs.cloud.google.com/document-ai/docs/handle-response)：`Document.text` 是文本事实源，实体通过 start/end index 引用其中片段；实体可以嵌套，专用解析器会返回带 confidence、text anchor 和 page anchor 的字段。
- [Custom extractor overview](https://docs.cloud.google.com/document-ai/docs/custom-extractor-overview)：entity confidence 表示模型将实体与预测值关联的强度，可用于设置单实体人工复核阈值。
- [Evaluate performance](https://docs.cloud.google.com/document-ai/docs/evaluate)：评估时可设置 confidence threshold；低于阈值的预测会被视为未参与匹配，并提供 below-threshold false negatives，用于选择阈值和权衡 precision/recall。
- [Managing processor versions](https://docs.cloud.google.com/document-ai/docs/manage-processor-versions)：处理器版本分为 Stable、RC 和用户自定义版本；稳定版本可能在新版本发布后弃用，未指定版本的请求使用默认版本，而默认版本变化会改变行为。页面说明可部署/取消部署版本，并建议持续创建、评估和切换新用户版本。
- [Document AI release notes（CDE/HITL）](https://docs.cloud.google.com/document-ai/docs/release-notes)：CDE 的官方能力清单包含数据导入、schema/标注、训练、评估、版本管理和 HITL “last-mile” 质量保证；历史 HITL 说明包括按字段配置复核、突出显示低于 confidence 的字段及复核分析。REST 总览中的 `humanReviewConfig.reviewDocument` 当前标注为 deprecated，接入前应核验目标处理器和区域的现行 HITL 路径。

**可借鉴的企业模式：**把 `Document.text` 作为稳定文本坐标系，把 page anchor 作为视觉证据；将规范化值与原始 mentionText 并存；升级 processor 时做离线评估、部署、回放和分阶段发布，不让默认版本漂移悄悄改写历史审查结果。

### AWS Textract 与 Amazon A2I

- [AnalyzeDocument API](https://docs.aws.amazon.com/textract/latest/APIReference/API_AnalyzeDocument.html)：`AnalyzeDocument` 返回页面、LINE/WORD、表/单元格、键值对、签名、Query/QueryResult 等 Block；Block 可含 `Confidence`、`Geometry`、`Page`、`Relationships`。
- [Text detection and document analysis response objects](https://docs.aws.amazon.com/textract/latest/dg/how-it-works-document-layout.html)：Block 通过 CHILD/VALUE 关系组成页面、行词、表和键值结构；低 confidence 的检测可能需要人工视觉确认。Geometry 以归一化 BoundingBox 和 Polygon 表示位置（[Locating items](https://docs.aws.amazon.com/textract/latest/dg/text-location.html)）。
- [A2I with Textract](https://docs.aws.amazon.com/sagemaker/latest/dg/a2i-textract-task-type.html)：可按 Textract 表单键的 confidence 区间触发 human loop。
- [Human loop activation conditions](https://docs.aws.amazon.com/sagemaker/latest/dg/a2i-json-humantaskactivationconditions-textract-example.html)：`ImportantFormKeyConfidenceCheck` 支持键值块/词块的等于、小于、大于等比较；`MissingImportantFormKey` 处理缺失键；`Sampling` 支持按比例抽样审计。多条件中任一条件满足即可触发，并可把原始 inference 送入 worker UI。
- [A2I output data](https://docs.aws.amazon.com/sagemaker/latest/dg/a2i-output-data.html)：人工结果写入指定 S3 输出位置，包含 human answers 以及 human loop、worker 和输入对象元数据。当前页面注明 A2I 不再向新客户开放，新客户应先确认替代方案或现有账户资格。
- [Textract adapters](https://docs.aws.amazon.com/textract/latest/dg/how-it-works-custom-queries.html) 与 [Using adapters during inference](https://docs.aws.amazon.com/textract/latest/dg/textract-adapter-inference.html)：适配器通过标注数据定制查询输出，推理时显式传入 `AdapterId` 和 `AdapterVersion`；同一文档不同页可指定不同 adapter/version，但每页只能应用一个 adapter。

**可借鉴的企业模式：**用 Block ID/Relationships 连接字段与词块、表格和页面；人审触发条件同时覆盖低置信度、关键字段缺失和随机抽样；适配器版本必须写入每次推理记录。A2I 的新客户可用性是采购/架构约束，不能把它当作无条件可选的托管服务。

### OpenAI File Search / Vector Store 元数据过滤

- [File search guide](https://developers.openai.com/api/docs/guides/tools-file-search)：File Search 在 Vector Store 上做语义和关键词搜索；可用文件 metadata 过滤结果；Responses API 可通过 `include: ["file_search_call.results"]` 获取检索结果和文件引用。
- [Retrieval guide](https://developers.openai.com/api/docs/guides/retrieval)：Vector Store 文件会自动 chunk、embedding 和索引；`vector_store.file` 带可用于过滤的 `attributes`。
- [Search vector store API](https://developers.openai.com/api/reference/resources/vector_stores/methods/search)：搜索请求支持比较过滤和复合过滤，操作符包括 `eq`、`ne`、`gt`、`gte`、`lt`、`lte`、`in`、`nin`；返回结果包含 `attributes`、`content`、`file_id`、`filename`、`score`。API 参考同时规定 attributes 的键和值类型/长度约束（当前实现为最多 16 个键值属性，键最长 64 字符，字符串值最长 512 字符）。

**可借鉴的企业模式：**把权限、项目和材料生命周期元数据写入 vector-store file；检索时用服务端过滤器表达硬约束，再做语义排序。检索结果的 score 只能表示相关性，不能替代文档字段 confidence、人工复核状态或业务规则结论。

## 面向“补件/增量审查”的建议数据与状态

以下是结合上述官方能力和本项目现有“保留原文、人工复核、补件、报告”边界的设计建议，属于项目推断，不是云厂商 API 的强制模型。

### 1. 文档单元和不可变证据

每次上传先计算内容 hash 并创建 `material_revision`，再执行分类/拆分。建议最小记录：

```text
material_revision_id
case_id / subcontract_package_id
source_file_id, source_sha256, original_filename
document_unit_id, document_type, page_start, page_end
extractor_provider, extractor_model, extractor_version, schema_version
raw_response_uri, source_page_image_uri (如可用)
```

原始文件、OCR/解析响应、分类页范围和人工操作记录只追加，不覆盖。补件产生新 revision；同一 hash 可去重，但不能因为文件名相同就合并。

### 2. 字段级结果与 grounding

字段表至少同时保存：

```text
field_path, raw_value, normalized_value
field_confidence, ocr_confidence (若供应商提供)
source_page, source_text_start, source_text_end
bounding_polygon / bounding_box, source_block_ids
extraction_method, model_version, produced_at
```

`normalized_value` 不能替换 `raw_value`；数值、日期、单位和名称的规范化必须可回指原文。若没有可定位的 span/page/box，状态应是 `grounding_incomplete`，进入人工队列或保持不可判定。

### 3. 人工复核与人机协同

建议按字段重要性设置三档策略：

| 阶段 | 自动处理 | 人工触发条件 | 发布前要求 |
| --- | --- | --- | --- |
| A | 高置信度且 grounding 完整的非关键字段 | 抽样审计 | 保留抽样结果 |
| B | 关键字段或中置信度字段 | 低于字段阈值、缺失、OCR/字段置信度不一致、规则冲突 | reviewer 确认并留痕 |
| C | 低置信度、无法定位、疑似冲突/篡改 | 必须人工处理；必要时要求补件 | 未完成不得进入报告结论 |

人工事件建议包含 `review_id`、队列原因、初始 AI 值、人工值、依据页/框、审核人、审核时间、意见、动作（确认/修改/退回补件/拒绝）和后续影响的 check IDs。保留原始 AI 预测，人工修订作为新版本或事件写入。

### 4. 增量审查和报告语义

补件后只重新分类/抽取新增或受影响的文档单元，随后计算“受影响检查项集合”。旧检查结果保留为历史版本，并标注 `superseded_by_revision`；未受影响的检查项不应被静默重算。建议状态：

```text
uploaded -> classified -> extracted -> needs_review
         -> human_verified -> ready_for_decision
         -> accepted / rejected / incomplete_material_scope
```

报告发布需绑定一个完整的 `review_snapshot_id`，其中包含材料 revision、模型/schema/rule 版本、复核事件和证据索引。存在未读、未定位或未复核的关键材料时，报告只能表达“范围不完整/待人工确认”，不能伪造完成结论。

### 5. 版本与发布门

建议将以下版本分开管理：

- **材料版本**：原文 hash、补件序号、页范围和上传时间。
- **解析版本**：供应商、模型/processor/adapter ID、版本及请求参数。
- **Schema 版本**：字段路径、类型、规范化规则。
- **规则版本**：五步核查/报告 eligibility 规则。
- **复核快照**：在某一时点人工确认后的不可变视图。
- **发布状态**：`draft`、`reviewing`、`verified`、`published`、`superseded`、`withdrawn`。

上线新解析器时先离线回放已审核材料，比较字段级 precision/recall、grounding 完整率、人工改动率和关键字段漏检率；通过后再改变默认路由。不要因为供应商默认版本变化而重写已发布历史。

### 6. RAG 元数据过滤建议

对每个 chunk/file 至少设置：

```text
case_id, subcontract_package_id, project_id
document_type, document_unit_id, page_start, page_end
material_revision, source_sha256
review_status, evidence_status, report_snapshot_id
effective_from, effective_to, supersedes_revision
visibility_scope, tenant_id
```

检索顺序建议为：权限/租户硬过滤 → 项目和分包包过滤 → 文档类型/有效期/复核状态过滤 → 语义与关键词检索 → 返回 `file_id`、页码、chunk、attributes 和 score → 由业务层把 chunk 回链到字段及原文。过滤条件缺失或 metadata 不可信时，应返回“不可判定/无可用证据”，而不是扩大召回范围。

## 供应商能力对照

| 能力 | Azure Document Intelligence | Google Document AI | AWS Textract/A2I | OpenAI File Search |
| --- | --- | --- | --- | --- |
| 分类/拆分 | 自定义分类器返回文档类型与页范围 | splitter/classifier、page anchor | Block/page/relationship；拆分需业务编排 | 不负责文档分类，靠上传前元数据/业务层 |
| 字段置信度 | 字段、词、表/行/单元格（依 API/模型） | Entity/layout/page confidence | Block confidence | 检索 score，不是抽取字段置信度 |
| 原文 grounding | span + 页/空间坐标（Content Understanding 新 API） | textAnchor + pageAnchor/bounding polygon | Block Geometry + Relationships | 返回 chunk/file 引用，业务需维护页/字段映射 |
| 人工复核 | 官方建议按 confidence/grounding 路由 | CDE/HITL 能力；旧 `reviewDocument` 已标 deprecated，需核实现行路径 | A2I 条件触发、抽样、人审输出；A2I 新客户受限 | 不提供文档字段审查工作流，需业务自建 |
| 版本语义 | 自定义模型/分类器、API 版本、模型复制/增量训练 | Stable/RC/自定义版本、部署和默认版本 | Adapter ID + Version | vector store/file/chunk 与 attributes；模型和规则版本需业务自管 |

## 落地前检查清单

- [ ] 每个补件都有新 `material_revision` 和 hash；旧原文、解析响应、人工决定可追溯。
- [ ] 分类/拆分结果带文档类型、页范围和分类 confidence；跨页文档边界可人工修订。
- [ ] 关键字段同时保存 raw/normalized、字段 confidence、OCR/词级 confidence（若有）和 grounding。
- [ ] 阈值按字段风险校准，并有随机抽样审计；低置信度、缺失、冲突和 grounding 缺失有明确队列。
- [ ] 人工修订不覆盖 AI 原始结果；具备 reviewer、时间、理由、前后值和依据页/框。
- [ ] 新材料只重算受影响检查项；报告绑定 review snapshot，未决关键材料阻止正式结论。
- [ ] 推理/规则/schema/材料版本在报告中可读；新模型先回放评估再发布。
- [ ] RAG 检索先做 tenant/project/package/document_type/review_status/effective-time 过滤，再做语义召回。
- [ ] 当前供应商能力、区域、API 版本和服务可用性在部署前重新核验；本文不替代客户环境验收。
