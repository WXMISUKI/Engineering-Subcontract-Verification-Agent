# 分包核查规则归属与维护基线

日期：2026-11-20  
阶段：P0 初始映射，仍需复核部分字段级关联  
对应 OpenSpec：`bs-rule-governance-20261120`

## 结论

当前工程不是完全无规则归属。准入的流程、依据正文、依据坐标、负面清单、资质/人员参数等已有分工；合同倒查也有独立运行时、输入冻结、分析、知识检索、覆盖和报告模块。但 `rules.py` 聚合加载多个数据族，`service.py`/`router.py` 较大，倒查分析模块兼具提示词、候选验证、时效筛选和批次编排，导致维护入口需要靠约定和测试反向理解。当前适合做目录治理和渐进迁移，不适合整体重写。

## 规则、入口、证据与测试映射

| 规则/数据族 | 权威来源 | 运行入口/主要消费者 | 证据与报告落点 | 关键回归 | 边界/状态 |
|---|---|---|---|---|---|
| 准入步骤与必查内核 | `data/review_flow.json` | `rules.load_review_flow` / `rules.in_scope_kinds` → `reference_snapshots.CHECK_GROUPS` 与 `service` 收口，A1/A2/A3 等内核 | `reporting._SECTIONS`、V14/V13 报告投影 | `test_bs_review_flow.py`、`test_bs_basis_validation.py`、`test_bs_review_mode.py`、各内核测试 | 流程清单为单一真源；新回归锁定声明的入口可调用、收口分组和报告分区；未证明每个内核的业务执行完整性 |
| 规范原文 | `data/basis_clauses.json` | `rules.load_basis_clauses`、`knowledge_runs`、`retrospective_sources` | 冻结依据、条款引用与校验状态 | `test_bs_basis_validation.py`、`test_bs_retrospective_sources.py` | 原文是依据权威源；不同模式的适用时点另行筛选 |
| 依据条目坐标 | `data/basis_index.json` | `rules.basis_lookup/configured_basis_lookup`、依据校验与报告引用 | `review_basis.json` / 条款坐标和报告依据 | `test_bs_basis_validation.py` | 仅坐标索引，不复制条款正文 |
| 准入规则登记/判定表 | `data/review_basis.json`、`data/ruleset.json`、`data/report_checklist.json` | `basis_validation`、`ruleset`、`rule_item_catalog`、具体内核 | 决策、来源记录、报告映射 | `test_bs_basis_validation.py`、`test_bs_rules_compile.py`、规则目录测试 | 文件间职责有交叠风险；P1 需逐字段判定唯一责任和校验方向 |
| 负面清单/转包规则 | `negative_list_2024.json`、`negative_list_applicability.json`、`negative_list_matchers.json`、`transfer_rules.json` | `rules` → `a1_check`、`negative_applicability`、`negative_candidate_plan` | 条款摘录/命中证据/适用性状态/报告附件 | `test_bs_negative_applicability.py`、`test_bs_negative_report_bridge.py` | 规范叶子、适用性、匹配词表应保持分层；具体字段重叠待P1核实 |
| 资质范围与人员配置参数 | `qualification_scope.json`、`qualification_matchers.json`、`personnel_adequacy.json`、`special_operation_catalog.json`、`safety_permit_scope.json` | `qualification_scope`、`personnel_adequacy`、`a2_check` 及专门判定器 | `material_facts`、A2结论、报告字段 | `test_bs_qualification_scope.py`、`test_bs_safety_permit_scope.py`、人员规则测试 | 领域参数由专属文件承载；不宜与法规原文混成通用 JSON |
| 合同倒查分析 | 本地 `retrospective_basis` + 冻结的规范依据/知识库来源 | `retrospective_runtime` → `retrospective_analysis`；`retrospective_knowledge` 调用共享引用检索 | `retrospective_sources` 坐标、批次结果、coverage、`retrospective_report` | `test_bs_retrospective_analysis.py`、`test_bs_retrospective_knowledge.py`、`test_bs_retrospective_runtime.py`、报告测试 | 规则语义专属倒查；底层原文校验/检索能力可共享，时点和判定不得默认继承准入 |
| 报告模板/策略投影 | `report_template_v13/v14.py`、`report_policy_data/policy.json` 及倒查报告模块 | `reporting`、`retrospective_report`、导出模块 | 报告快照、正文/附件、导出文件 | `test_bs_report_consistency.py`、`test_bs_report_gate.py`、倒查报告测试 | 展示结构不是规则真源；必须由已确认结论投影 |

## 模式入口与共享边界

- **准入**：`review_mode.py` 默认历史缺值为 admission；路由门禁和准入 service 处理材料齐套、负面清单、资质、人员和原有流程生命周期。
- **合同倒查**：`retrospective_runtime.py` 管理合同双侧输入和任务运行；按合同条款分批分析、按基准日筛依据并生成倒查报告，不经过准入五步/人工复核生命周期。
- **可共享基础设施**：任务存储与材料解析、可读性判断、来源与引用验证、授权范围内的知识检索基础设施、报告文档渲染。共享函数不意味着共享业务适用条件。
- **明确不继承**：准入的资格门槛、完整性分母、负面清单适用性、人工复核状态及报告结论规则，不能仅因倒查复用某个 loader 或检索函数而视作倒查规则。

## CodeGraph 当前噪声与处置

2026-11-20 查询发现 736 个文件被索引，且旧试用副本 `artifacts/bs-retrospective-trial-20261119/runtime/` 与真实源码同名，CodeGraph 探索返回两份 `rules.py`、`review_mode.py` 等。CodeGraph 安装文档确认索引服从 Git 忽略规则。已在本机 `.git/info/exclude` 精确排除该历史 runtime 路径；完成索引重建后，当前状态为 535 个文件，`load_review_flow` 解析到当前源码唯一入口，排除路径不返回源码。项目 `.gitignore` 和其他 artifacts/backups/tmp 未改。

## 不变量与迁移规则

1. 规则目录只登记来源和关系，不复制可执行规则正文或法条全文。
2. 每个可判定规则需指定稳定 ID、审核模式、权威来源、运行 owner、证据输入/结果输出、报告投影和维护测试；未知项显式标成待查。
3. 准入与倒查可共享解析、证据定位、规范引用和文档渲染基础设施；适用性、判定、生命周期必须单独声明。
4. 规则变化必须有原有行为基线和定向回归；切片迁移期间保留兼容入口，验证后才删除旧路径。
5. 不做历史任务/报告回算，不操作服务或共享数据库，不借维护性目标改规则业务含义。

## P1 规则目录契约

新增 `data/rule_catalog.json` 作为**治理元数据目录**，格式版本为 `bs-rule-catalog.v1`。每个条目记录：稳定 `id`、显示名、模式归属及精确 `applies_to`、映射成熟度、来源类别/路径/符号引用、关联依据 ID、owner 模块与入口符号、证据输入/输出、报告投影、回归测试路径和条目版本。目录不复制法条、阈值、提示词或执行逻辑。

`mapping_status` 使用 `verified`、`partial`、`unverified`、`retired`。未确认关系用 `unresolved` + 空适用范围表示；不得用共享 loader、相似名称或同一报告模板推断规则共享。`shared_infrastructure` 仅允许同时明确声明准入与倒查，并不代表对应业务判定规则共享。

目录中的基础示例是**资产/能力级映射**且标记为 `partial`，用于冻结格式及模式边界；它们不是完整规则逐条名册。P2 已增加严重度矩阵切片 `A13:severity-matrix`，也标记为 `partial`，因为其余规则 ID 尚未逐项登记。规则代码迁移前不能把 `partial` 改为 `verified`。

校验器 `rule_catalog.py` 当前只由治理测试显式调用，不接入运行服务。它拒绝未知字段、重复 ID、缺 owner、无效路径、仓库外路径及 `mode_scope` / `applies_to` 不一致。这样先完成目录契约和验证，再选择兼容迁移切片。

## 跨阶段待办

- 等待全量索引重建后确认历史 runtime 不再作为源码符号返回。
- 逐项核对 `review_basis.json`、`ruleset.json`、规则数据与报告映射的重复/引用关系，不能仅凭文件名认定冗余。
- 对倒查各阶段的内部函数及输出字段补齐精确来源、持久化与报告映射；当前只到模块级。
- P2 当前仅迁移严重度矩阵加载归属；其它候选切片需在实际调用、规则 ID 和测试面确认后再排期。
