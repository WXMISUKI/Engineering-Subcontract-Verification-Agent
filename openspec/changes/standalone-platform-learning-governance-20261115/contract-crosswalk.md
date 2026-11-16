# 公共业务契约 v1 与跨仓责任映射（已确认）

用户于 2026-11-15 确认 D2 及本 v1 语义边界/责任映射：公共语义通过版本化领域/API 契约与各自适配器表达，不抽共享代码包。本文件定义 `bs-review-contract.v1` 的跨形态业务不变量；它不要求两边 API DTO 或数据库 schema 同构。标注“未对齐/后续阶段”的内容不得直接视为已实现。

当前依据：独立版 `main@8a1b83aed3706fef8ce5488e6c7a74b8ef8d8434`；广场版 `refactor-kbbase-luoyonglai@e4bb2cd759345681de978b6157578f06f43058e9`。相关 OpenSpec 为独立版 `bs-optional-source-scope-limitations-20261112`、`bs-supplement-evidence-linkage`、`bs-report-adaptation`，以及广场版 `bs-expert-review-boundaries-20261110`。

| 语义 | 独立版当前依据 | 广场版当前依据 | `bs-review-contract.v1` 约束 | 适配差异/后续项 |
| --- | --- | --- | --- | --- |
| 任务/主体/材料归属 | `BsMaterial` 有 task/subject/packet；额外有 `general_source_set_id/version` | `BsMaterial` 有 task/subject/packet，但无来源集两字段；主体证据模块相近 | 名称相似不建立主体关系；证据保留 task/material/subject 来源，不能跨主体拼接。来源集是独立版扩展 | 两版 `subject_id` 生命周期、角色枚举及空值语义；平台是否未来需要来源集 |
| 核查代与历史版本 | optional-source 规格以 `review_run_id` 作为 `generation_id`；`BsReviewVersion` 保存 version_no/snapshot | 同名 `BsReviewVersion` 有相同列；AI job/run、rule snapshot 与 review version 分层 | 任务 ID、运行代 ID、组运行 ID和报告版本不可互换；历史只能读取自身冻结快照 | 两版快照 JSON 的字段版本、job retry/attempt 与 generation 的关系 |
| 规则身份 | `rule_id`、`ruleset_version`、`ruleset_fingerprint` | 规则快照与独立 candidate/checkpoint 版本 | 只用规则编号不能证明定义相同；规则/输入变化不得复用旧结果或人工回执 | 规则指纹 canonicalization 与旧任务兼容策略 |
| 材料事实与页证据 | `BsMaterialFact` 含 generation、field/page、quality/source、parse/schema/extractor/material 版本；append-only | 同名 `BsMaterialFact` 的列结构与独立版相同 | 原材料和历史事实不覆盖；证据引用必须可定位且主体/材料隔离；不可读或低质量不推断缺失 | 写入时序、字段白名单/质量枚举、事实投影及 API 暴露是否一致 |
| 固定结论、范围限制、人工提示 | `BsFinding.status` 三态、`review_json`；scope coverage 与 finding 分离 | 同名 `BsFinding` 增加 `finding_semantics`，并有 `review_json` | 机器结论、人工核对提示、范围未覆盖、读取质量、执行状态是不同语义，不得互相投影 | finding_semantics NULL 兼容、人工提示计数/门禁和 UI/API 结构映射 |
| 事件与后台任务 | `BsTaskEvent` 通用事件；`background_ai_review.py` 使用 review-run 路由/租约/checkpoint；无对应 job 表 | `BsTaskEvent` 同名基础字段，多 `created_at_utc_offset_minutes`；另有 `BsAiAdvisoryJob` 按 task/run/group 唯一 | 事件、调度 job 和业务结果分别表达；外部执行成功、业务覆盖完整和人工接受分别表达 | job/event schema、租约/代次、重试和收费幂等映射；时区字段语义及旧事件处理 |
| 人工复核回执 | finding review receipt、补件和报告快照生命周期 | 主体/人员/页级回执，并在范围变化时使旧回执失效 | 回执须绑定对象、证据版本和核查代；新材料/新代不继承旧决定，除非已定义安全迁移规则 | 两版回执主键、失效触发和审计 DTO |
| 报告事实与导出 | `business_report.py` 投影；`report_policy.py` 版本化；历史版本冻结 | 分包 `reporting.py`；页面/Word/PDF 消费报告投影和门禁 | 同一代同一事实投影；报告策略不得改写规则结果或材料事实；范围和未决必须显式披露 | 两版投影字段、report readiness 和多格式 snapshot schema |
| 身份、权限、账单和部署 | 独立身份库/本人任务、独立 usage/billing、standalone Compose | 平台统一身份、角色/菜单、wallet/流水、Plus 宿主 | 视为交付适配，不作为公共业务核心字段；没有双形态数据共享决策前不互换 | 若将来共用，需分别新建身份/租户、计费归属和部署决策，不在本阶段推定 |

## v1 契约边界

- `bs-review-contract.v1` 是本 OpenSpec 定义的领域语义版本。只有实际创建跨形态 adapter 消息时，该 adapter envelope SHALL 携带 `contract_version: "bs-review-contract.v1"`；各应用内部 endpoint 继续由各自 DTO 管理 `schema_version`，本阶段不批量增字段或迁移 API。
- 标识只在所属应用命名空间内有效；task/material/subject/review-generation/review-version/rule/job ID 均不得仅凭数值或字符串相同跨仓认定为同一对象。
- 材料事实 SHALL append-only；补件、材料替换、重新解析和规则变更创建新版本或新代，历史事实、finding、人工决定及报告快照保持可读。
- 证据引用 SHALL 保留材料身份、主体归属、物理页、可用原文/定位状态、来源类型与质量状态。主体冲突、未知、未读或低质量不得被归一成缺失或符合。
- 机器核查结论固定为 `hit` / `miss` / `undecidable`。人工核对提示、范围未覆盖、页面质量、后台执行和人工回执是不同维度，不得折叠进机器三态或 finding 计数。
- 规则身份 SHALL 同时带规则 ID 和规则集版本/指纹；仅 ID 相同不得复用旧运行、checkpoint、人工回执或报告结论。
- AI 后台 job 是执行调度对象，不是业务结论；job ID、attempt、review generation 和 rule-group run ID 独立保存。任务执行终态不得替代 coverage/readiness。
- 人工回执 SHALL 关联其对象、证据版本/指纹和核查代；新材料或新代不得静默继承旧回执。旧数据无法证明关联时保持 unknown/legacy 状态。
- 同一报告 generation 的页面、API、Word、PDF 和证据包 SHALL 消费同一冻结业务投影；导出或策略版本不得改写规则事实及材料事实。
- 身份认证、任务所有权、菜单/Plus 宿主、账单归属、数据库初始化和 Compose/部署属于应用适配契约，不进入 `bs-review-contract.v1` 的共享业务语义。

## 唯一责任来源

| 责任 | 独立版权威来源 | 广场版权威来源 | `bs-review-contract.v1` 归属 |
| --- | --- | --- | --- |
| 持久化实体/字段 | `backend/app/plus/blockchain_supervision/models.py` | 同路径的本仓 `models.py` | 本文件只定义跨形态语义；各仓 models.py 只负责本地存储 |
| 材料事实生产 | `material_facts.py` 与事实批次 | `material_facts.py` 与事实批次 | 事实字段及 append-only 语义由本契约定义，产生器由各应用负责 |
| 核查代/历史快照 | `service.py` 创建 `BsReviewVersion`，`snapshot_json` 冻结 | 同路径 `service.py` 创建版本快照 | generation 与 version 语义由本契约定义；快照 payload 属于各自 versioned DTO |
| 范围/覆盖投影 | `optional_source_scope.py`；service `/todo`/readiness；`router.py` DTO | 当前以 `ai_review_context.py`、快照及分组状态组合 | 覆盖和执行分离由本契约定义；平台缺口按阶段 2/5 评估，不由前端推测 |
| finding/人工提示 | `BsFinding.status`、`review_json` 及规则/复核服务 | `BsFinding.status`、`finding_semantics`、`review_json` | 三态与语义分离由本契约定义；平台字段不能假定独立版已存在 |
| 后台 job/结果 | `background_ai_review.py`、`BsTaskEvent` 和 checkpoint | `BsAiAdvisoryJob`、worker、snapshots、`BsTaskEvent` | 执行状态/业务状态分离由本契约定义；run/job 映射由适配器负责 |
| 报告投影与门禁 | `business_report.py`、`report_policy.py`、`report_document.py` | 分包 `reporting.py` 与分包服务/路由 | 投影语义和冻结要求由本契约定义；业务字段和各格式构建由应用实现 |
| 前端 API 和 UI | `frontend/src/plus-ui/blockchain-supervision/lib/api.js` 与独立组件 | `frontend/src/plus-ui/blockchain-supervision` API/组件 | UI 不创造领域事实；组件只显示应用 DTO 中已保存的范围、执行和回执状态 |

## API 与验收责任映射

| 契约域 | 独立版入口/回归 | 广场版入口/回归 | 现状 |
| --- | --- | --- | --- |
| 范围覆盖 | `service.optional_scope_coverage`; `/todo` 投影；`test_optional_source_scope_lifecycle.py::test_todo_endpoint_returns_scope_coverage_as_a_separate_projection` | `ai_review_context.py`、任务组状态和页面读取质量投影 | 独立版有明确顶层 projection；平台没有同名组件/DTO，后续只映射语义，不要求名称一致 |
| 总包来源集 | `service.associate_general_source_set`/`remove_general_source_set_member`; router 对应路由；`test_general_source_set_api.py` | 未检出同等显式来源集接口 | 独立版专有扩展；不得按平台文件名/时间推导并迁移 |
| 后台 AI 运行 | router `BackgroundAiReviewIn`、`submit_background_ai_review`/查询路由；`test_bs_background_ai_review.py` | `ai_advisory_jobs.enqueue_review_jobs`/worker；`test_bs_ai_advisory_jobs.py`、recovery/MySQL tests | 业务 run 与调度 job 分离；ID 和状态需 adapter 映射 |
| 页级候选/确认 | 未检出同等 `PageTextReviewModal`/候选确认 API；现有 scope/readability 提示不能替代人工操作 | router `create_page_text_candidate`/`list_page_text_candidate`/`get_page_text_candidate`/`confirm_page_text_candidate`; service 对应 candidate functions；确认带 `expected_candidate_version`; `test_bs_page_text_candidate_api.py`; `PageTextReviewModal.test.jsx` | 平台已有版本化确认闭环，独立版仍属阶段 4 差距；确认操作及重核查失效规则须先对齐再实现 |
| 报告策略/版本 | `business_report.py`、`report_policy.py`; `test_business_report.py`、`test_report_policy.py`、`test_report_document.py`、`test_bs_report_version_http.py` | 分包 `reporting.py`; `test_bs_report_gate.py`、`test_bs_report_partial_read.py`、`test_bs_report_export.py` | 同一业务投影原则可共用；实际 report DTO、readiness code 和格式 builder 保持应用所有 |
| finding/人工回执 | `BsFinding.status` + `review_json`; `test_bs_finding_semantics` 不存在于独立版，回执/报告回归分散在 `test_bs_report_gate.py` 等 | `BsFinding.finding_semantics` + `review_json`; `test_bs_finding_semantics.py` | 机器三态相似，人工提示语义不等价；后续规则/证据阶段需确认独立版 gap 后再加字段 |
| 材料事实 | `material_facts.py`; `test_bs_material_facts.py`、`test_bs_ingest_material_facts.py` | 同名 `material_facts.py`; 同名 facts/ingest tests | 字段结构一致；写入事务、API消费及源质量取值仍需逐项对照 |

## 兼容回退责任

- 范围策略：新语义沿独立版代冻结开关运行；回退只对新代禁用，不改历史 coverage/snapshot。
- 页级复核：确认功能启用前必须保留原查看路径；回退停止新候选操作，不删除候选/回执/事件，已冻结版本继续只读。
- 报告：恢复到上一版 report-policy adapter 只影响新报告生成；已保存版本与导出内容不重写。
- AI 执行：停止新 job/run 的调度或回到上一 adapter；保留 job attempt、checkpoint、事件和失败状态，不把未运行转成成功。
- 数据模型：本阶段不迁移数据库；未来添加字段必须用可空/默认兼容策略并通过旧库幂等升级验证，回退代码时保留新增数据，不做破坏性降级。

## 版本与兼容

- 本 OpenSpec 定义的领域契约版本为 `bs-review-contract.v1`；实际跨形态 adapter 消息携带 `contract_version`，各应用内部 DTO 保留独立 `schema_version`，两者分别升级。
- 兼容转换必须发生在应用 adapter/service 边界，不在数据库层做跨仓 ID 对齐，也不在 UI 层把未知状态猜成已完成。
- 不支持的 contract/schema 版本、缺失必需身份或冲突快照 fail closed；历史版本缺字段保持 `legacy_unknown`，不回填猜造值。
- 如需 schema 变更，必须另立阶段规格，采用向前兼容的加列/双读或显式迁移，验证完成后才可切换；不得在本治理阶段直接迁移数据库。

## 本轮源码证据

- 两版 `BsReviewVersion` 的表名和列（id/task_id/version_no/trigger/snapshot_json/created_at）一致；这只证明该表结构当前相同，不证明快照 JSON 或服务生命周期完全兼容。
- 两版 `BsMaterialFact` 的表名、唯一批次键与字段结构一致；仍需在契约确认阶段核对质量取值、事实生产流程及消费方。
- 两版 `BsTaskEvent` 的基础列相同；平台另有 `created_at_utc_offset_minutes`。事件 payload 无数据库级领域 schema，不能因为表结构相似就直接跨仓重放。
- 平台 `BsFinding` 有 `finding_semantics`，独立版没有；两版都有 `review_json`。因此机器三态兼容并不等于人工提示语义兼容。
- 独立版 `BsMaterial` 有 `general_source_set_id` 与关系版本；平台 `BsMaterial` 当前无此字段。平台另有 `BsAiAdvisoryJob` 表，独立版 `background_ai_review.py` 以任务事件等现有存储实现后台生命周期。

## 阶段 1 回退原则

- 本阶段只冻结文档契约，不迁移数据库、任务或材料；若用户评审要求修改，更新 v1 草案后重新严格校验，不涉及运行时回退。
- 后续 adapter 升级采用新增版本并保留旧 adapter 读取路径；关闭新能力只影响新 generation，不改已冻结的历史快照。
- 任何未来 schema 扩展只允许先兼容新增和双读；回退 adapter 可回到上一 DTO/规则策略，但不删除新增列、事实批次、审计事件或用户决定。
- AI job 或人工复核新流程若失败，沿各自应用既有失败/恢复状态处理；不得删除 job/event 或沿用已失效回执来“回滚”业务记录。
