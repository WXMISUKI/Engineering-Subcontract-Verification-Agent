# 跨仓能力映射（草案）

当前映射依据：独立版 `main@8a1b83aed3706fef8ce5488e6c7a74b8ef8d8434`；广场版 `refactor-kbbase-luoyonglai@e4bb2cd759345681de978b6157578f06f43058e9`。进入每个实现阶段前重新核对提交和工作区。分类是处理策略，不代表产品验收。

| 能力 | 独立版基线 | 广场版参考 | 分类/阶段 | 约束与验收门 |
| --- | --- | --- | --- | --- |
| 独立宿主、登录和本人任务隔离 | `app/standalone` 独立身份库与入口 | Plus 容器、统一用户/角色/菜单 | 保留独立实现；平台集成暂缓 | 不合并用户 ID、Cookie 或 schema；未来双形态共享数据须先有身份映射、租户隔离和迁移方案 |
| 固定分包规则与证据反例 | 分包核心规则及证据链 | 已确认专家边界、逐人证据、反例矩阵 | 回学业务规则/证据，阶段 2 | 先映射全部相关意见并标适用性；不把平台新增规则自动变成独立版规则 |
| 范围、可选来源与读取缺口 | 版本化范围快照、`optional-source-scope.v1`、中性披露 | 快照、材料主体、读取质量状态分散实现 | 保留独立语义，契约对照 | 不把可选来源缺失判为 finding 通过；不把未读扩成全量失败 |
| AI 后台任务和恢复 | checkpoint、租约、容量、终态恢复 | `ai_advisory_jobs`/worker、平台任务代次和计费幂等 | 先保留，阶段 6 评估适配 | 不替换任务表/枚举；先对照代次、事件、租约、幂等、错误投影和旧客户端 |
| 报告策略与报告投影 | 业务报告、资质摘要、基本事实/合同类型、策略版本化 | 分包目录 `blockchain_supervision/reporting.py` | 回学治理边界，阶段 5 | 只能有一个事实投影；策略不改写规则指纹或材料证据；资质不能由摘要自动判合规 |
| 页级文本复核和人员证据呈现 | 有读取质量治理，但无广场版同名页复核模态框 | 页面复核、逐人证件/社保线索组件 | 回学交互/证据闭环，阶段 4 | 先定 API、证据来源、回执失效与人工动作，再做 UI；证据不得跨主体串用 |
| 补件与项目基本信息 | 独立版补件发布门禁、项目基本信息 | 平台有材料/补件流程，无同名组件 | 保留并对照适配 | 补件发布视为受控状态变更；人工回执和版本快照需可追溯 |
| 平台计费与模型目录 | 独立账单适配，不是平台完整账单链 | 钱包、预授权、流水、模型价格及任务归因 | 暂缓平台适配 | 独立版继续独立计费；若未来共用，先定义计费所有权、防重复扣费和幂等映射 |
| 印章识别、项目基本信息扩展 | 已有局部能力 | 平台多智能体共享能力 | 暂缓评估 | 以当前产品范围和验收价值决定，不因已有文件自动加入 |
| Compose、冷备和离线交付 | P0-P6 独立交付链 | Plus 网络/卷/非 root/代理等平台部署契约 | 保留独立，阶段 7 验收 | 不将平台 MySQL、Docker 网络、管理员或容器依赖带入独立镜像；许可与源码义务独立验收 |

## 已核实的源码与回归入口

以下坐标由本轮 CodeGraph 符号查询及当前文件清单确认。它们用于后续阶段定位，不代表对应行为已完成业务验收。

实体同名仅作为比对入口。当前 `BsReviewVersion` 与 `BsMaterialFact` 的 SQLAlchemy 列结构相同，但快照生产/消费仍需另验；`BsTaskEvent` 在平台额外含 UTC 偏移字段；平台 `BsFinding` 含独立版没有的 `finding_semantics`；独立版 `BsMaterial` 有总包来源集字段，平台版无该字段。平台有 `BsAiAdvisoryJob` 表，独立版没有对应 job 表。

| 领域 | 独立版源码/测试 | 广场版源码/测试 | 备注 |
| --- | --- | --- | --- |
| 独立身份与任务隔离 | `backend/app/standalone/identity.py`; `backend/tests/test_standalone_identity.py` | 平台统一身份入口与 Plus 权限，不迁移到独立运行时 | 独立身份继续作为独立契约 |
| 主体证据 | `backend/app/plus/blockchain_supervision/contract_party_evidence.py`; `backend/tests/test_bs_contract_party_evidence.py` | `backend/app/plus/blockchain_supervision/personnel_evidence.py`; `identity_observation.py` | 合同角色、企业主体和个人证件必须分开定义 |
| 范围与可选来源 | `ai_scope_selection.py`; `ai_scope_evidence_graph.py`; `optional_source_scope.py`; `reading_gap_scope.py`; `test_ai_scope_selection.py`; `test_ai_scope_evidence_graph.py`; `test_optional_source_scope_lifecycle.py` | `ai_review_context.py`; 快照/批处理链 | 回学主体/材料边界时保留独立版范围快照语义 |
| 后台 AI 恢复 | `background_ai_review.py`; 路由 `submit_background_ai_review`/`get_background_ai_review` 位于 `router.py`; `test_bs_background_ai_review.py` | `ai_advisory_jobs.py`; `ai_advisory_worker.py`; `ai_advisory_snapshots.py`; 对应 jobs/recovery/MySQL 测试 | 先映射状态、代次、租约、checkpoint 与幂等，再讨论适配 |
| 报告 | `business_report.py`; `report_policy.py`; `report_document.py`; `report_basic_facts.py`; `report_contract_type.py`; `test_business_report.py`; `test_report_policy.py`; `test_report_document.py` | 分包实现：`backend/app/plus/blockchain_supervision/reporting.py`; `backend/tests/test_bs_report_gate.py`、`test_bs_report_partial_read.py`、`test_bs_report_export.py` | 当前平台的 `startup_conditions_check/report_governance.py`、`report_service.py` 属于其他智能体，排除在本能力映射外 |
| 页级人工复核 | 有 `ScopeCoverageNotice.jsx`、`MaterialPreviewPane.jsx` 和通用 evidence review；未发现 `PageTextReviewModal.jsx` | `PageTextReviewModal.jsx`; `BlockchainSupervisionWorkbench.jsx`; `PageTextReviewModal.test.jsx` | 独立版需先定义动作/API/回执代次，不能只移植模态框 |
| 人员证据呈现 | `personnel_evidence.py`; `frontend/tests/personnelEvidence.test.jsx` | `PersonnelCertificateClues.jsx`; `PersonnelSocialClues.jsx`; 由 `ReviewWorkbenchLayout.jsx` 与 `ReportView.jsx` 引用 | 逐人、逐证件和材料主体隔离是验收要点 |
| 补件与项目信息 | `ProjectBasicInformation.jsx`; `PreviousSupplementReview.jsx`; `lib/supplementPublication.js`; 对应 `projectBasicInformation`、`PreviousSupplementReview`、`supplementPublication` 测试 | 通过既有材料/补件工作台承载，无同名组件 | 保留独立版补件发布门禁，比较证据定位与人工回执 |
| 计费适配 | `app/standalone` usage/billing 路由与 `test_standalone_billing.py`、`test_bs_task_usage.py` | 平台 billing/credits contracts、wallet 与 `bs_task` 归因 | 不合并记账入口；共享前需明确扣费唯一所有者和幂等键 |

## 跨阶段不变量

- 规则事实、材料证据、报告策略和格式渲染分层；历史快照与人工决定不可被回学流程改写。
- 任务/证据/状态按版本关联；未知、冲突、不可读和未执行保持未决或失败，不升级为通过。
- 合成测试、隔离候选、真实业务验收和客户部署分别记账。
- 每次跨仓变更记录来源提交、迁移测试、未迁移差异和回退办法。
- 报告源码按分包模块归属核实；其他智能体中的同名治理模块不得作为分包复用证据。
- `contract-crosswalk.md` 中的 `bs-review-contract.v1` 语义边界与责任映射已获用户确认；这不代表跨仓运行时 API 或共享数据模型已经实现。
