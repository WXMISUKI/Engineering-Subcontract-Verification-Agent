# 实施任务与验收

状态：阶段 0 已获用户确认；阶段 1 契约已于 2026-11-12 获用户确认并批准进入阶段 2；阶段 2 固定规则修复、阶段 3 待处理 API/UI、阶段 4 报告与正式门禁、阶段 5 补件新代与提示生命周期、阶段 6 隔离端到端回归均已完成代码与隔离验收。阶段 6 原定向后端回归 201 项通过；本轮 7.1a 来源集新增后，关联后端回归 53 项、前端完整 Vitest 套件 116 项通过；隔离 MySQL 迁移重复运行、OpenSpec strict validation 与 `git diff --check` 均通过。阶段 7.1 业务口径及 7.1a“显式关联并保留各原件”选型均已确认并实现；用户已决定保持业务 flag 关闭并结束本变更验收，因此不启用候选 rollout、不部署。法规原文/逐规则解释复核及 MoMA 真实材料效果不属于本变更的已完成验收；服务端开关默认关闭，未连接现有 MySQL 容器或共享数据库、未调用模型、未部署。

## 阶段 0：规则与现状冻结（不改业务代码）

- [x] 0.1 形成当前规则来源依赖矩阵草案，包含启用目录、必需/可选来源、缺源行为及证据位置；待业务负责人确认后冻结。见 `rule-source-matrix.md`。
- [x] 0.2 单列 T15-01/02/03/05、W16-03、V16-02，以及默认关闭/条件开启的金额比例规则；逐条核对内核与配置，没有以选择器影子图推断业务依赖。
- [x] 0.3 形成来源状态、规则适用性、执行状态、finding、范围限制和 report readiness 的状态转移草案。见 `design.md` 与 `rule-source-matrix.md`。
- [x] 0.4 只读对照 findings/todo、预览/正式报告、意见书、Word/PDF/证据包、补件代和任务收口入口及当前门禁。见 `rule-source-matrix.md`。

**阶段验收门：**每个当前规则都有有据可查的来源策略；总包缺失只影响被矩阵标明的依赖规则；必需证据缺失、来源不可读、归属冲突、解析运行中和规则执行失败各有不同状态。业务负责人确认矩阵后再进入实现设计。

## 阶段 1：领域与 API 契约

- [x] 1.1 设计独立于 `BsFinding` 的范围限制投影，定义 rule/generation/subject/source/reason/status 字段和版本兼容策略。见 `domain-api-contract.md`。
- [x] 1.2 追踪现有 event、review version、run result、snapshot 和 API DTO；确认可复用 `BsTaskEvent` 承载新增代际事件，并以 `BsReviewVersion.snapshot_json` 冻结历史；现有 `/check` 仍需新增 `check_run_id` 及覆盖事件。不新增数据库表，事件不作为唯一历史真相。
- [x] 1.3 定义 API 对 findings、scope limitations、rule execution coverage、report readiness 的独立返回结构，禁止从 finding 文案或前端推断。
- [x] 1.4 定义旧任务兼容规则：旧代按冻结 finding/snapshot 解释，新语义只用于明确冻结新 policy 的核查代，不回写历史数据。

**阶段验收门（已通过）：**用户已确认采用 B（代际事件 + 版本快照）并批准进入阶段 2。后端隔离测试验证历史快照、coverage 完整性失败关闭、默认关闭及按核查代冻结；待处理 API/UI 已在阶段 3实现，报告门禁留在阶段 4。

## 阶段 2：固定规则垂直修复

- [x] 2.1 为 W16-03/V16-02 补齐可读、缺失、处理中、终态不可读、归属不明和冲突来源状态回归；可读总包继续执行依赖规则，其他规则投影与基线相同。
- [x] 2.1a 为 A1 建立按任务锁保护的当前 `check_run_id` 指针；验证最终持久化入口拒绝旧运行迟到写入、失败事件不污染新运行、同 key 重试幂等且身份冲突 fail closed。逐规则覆盖仅限矩阵中的可选总包依赖规则。
- [x] 2.2 按阶段 0 矩阵修复可选总包缺失语义：只跳过依赖总包的规则，不产生由可选来源缺失导致的三态 finding。
- [x] 2.3 验证同一任务中不依赖总包的规则保持原执行与结果；规则自身必需分包材料缺失仍保留原 `undecidable` 语义。
- [x] 2.4 验证 A1-01 缺可选总包时不产生误导 finding；金额比例配置当前保持默认关闭，启用路径的真实分包金额缺失仍为 `undecidable`。
- [x] 2.5 覆盖总包缺失、处理中、终态不可读、材料读取缺口、归属不明、主体冲突及多个总包。材料级读取缺口只归为一般 `unreadable`；只有规则级页依赖可精确确认后才允许使用 `required_pages_unreadable`。

**阶段验收门（代码与隔离测试通过）：**无总包时不产生由可选来源缺失导致的 finding；不相关规则照常运行；终态 coverage 与 findings 同事务提交并按代冻结；旧运行迟到成功/失败均被拒绝；规则覆盖受矩阵约束。阶段 2+3 定向后端回归 67 项通过；OpenSpec strict validation 通过。报告统一披露与正式门禁留在阶段 4。

## 阶段 3：待处理工作区和 API 展示

- [x] 3.1 在现有 `/tasks/{task_id}/todo` 响应增加独立顶层 `scope_coverage`；不改 findings、待办计数或 checklist 结构。
- [x] 3.2 在 Review 工作区使用独立只读范围提示展示受影响规则、来源状态及 coverage 执行状态；历史/默认关闭代 `legacy_unknown` 不被伪造成“无范围限制”。
- [x] 3.3 范围提示不进入 finding 队列，不出现采纳/忽略操作，不写 finding review receipt，也不改变人工待办分母。
- [x] 3.4 提示仅在 `/todo` 的 `generation_id` 与当前 reference snapshot 一致时显示；finding 刷新会重新读取 todo，避免沿用旧代或历史版本范围结果。

**阶段验收门（API/UI 隔离测试通过）：**后端 `/todo` 回归确认 coverage 独立返回且既有 `total_undecidable` 不变；前端回归确认 partial 显示受影响规则、incomplete 显示执行状态、legacy_unknown 隐藏、不出现 finding 操作。定向前端 3 项通过。

## 阶段 4：报告与正式门禁

- [x] 4.1 在统一 report readiness 中区分范围限制、真实 blocking `undecidable` 和未完成/失败执行。
- [x] 4.2 将规则未覆盖说明接入报告正文、意见书、HTML/JSON API、Word、PDF 和证据包，共用同一 generation 和 scope projection。
- [x] 4.3 验证仅有可选总包范围限制时，其他正式报告门槛满足后允许正式输出；所有格式均明确列出未覆盖规则。
- [x] 4.4 验证存在真实未处置 blocking `undecidable`、必需检查失败或 candidate generation 未发布时，现有门禁仍按政策阻断。
- [x] 4.5 验证有范围限制时，摘要、意见结论和标题不产生“完整覆盖/全部通过”误导性表述。

**阶段验收门（代码验收通过）：**所有正式输出入口的允许/阻断结果、错误结构和限制披露一致。只有范围限制不能产生报告阻断，也不能转成完整通过。复跑 `test_optional_source_scope_lifecycle.py`、`test_report_policy.py`、`test_report_document.py`、`test_bs_report_version_http.py`：154 passed；`openspec validate bs-optional-source-scope-limitations-20261112 --strict` 与 `git diff --check` 通过。Word/PDF 的业务文档路径保留独立范围限制章节，并在 `scope_limited` 时输出受限结论；历史格式与证据包只读同一版本冻结的 `scope_coverage`，旧版本缺字段保持 `legacy_unknown`。

## 阶段 5：补件新代与提示生命周期

- [x] 5.1 通过隔离 SQLite API 工作流验证 `general_contract` 补件映射到 A1/完整性组，并检查计划声明的受影响核查组；影响无法安全缩小时继续由现有计划策略回退。
- [x] 5.2 验证总包处于解析中时 `/supplement-rerun` 返回 `parsing_pending`，当前没有新代且范围投影不变；不可读、未关联及冲突来源的状态分类由 `test_optional_source_scope_lifecycle.py` 覆盖，均不会投影成最终可用来源。
- [x] 5.3 验证补件按延迟解析路径真实完成材料读取后才创建新 generation；新代 A1 核查完成后按当前可读总包生成 coverage。测试不得手动伪造 `parse_state=done` 而省略解析正文。
- [x] 5.4 验证新代 A1 完成前 coverage 为 `incomplete` 且无最终 limitation；完成后仅新代 coverage 更新为 `available`，旧代快照保持原范围限制。
- [x] 5.5 验证父代 findings 与人工意见写入版本快照且不被候选覆盖；第二次补件替换解析失败时，失败候选不替代已发布代 coverage。

**阶段验收门（隔离代码验收通过）：**`backend/tests/test_standalone_workflow.py::test_uploaded_pdf_starts_and_runs_checks_with_unavailable_knowledge` 通过；与 `test_optional_source_scope_lifecycle.py` 的来源状态分类和 coverage 投影回归配合，证明补件闭环从上传/解析门禁到新代 A1 结果、父代快照和失败候选保护。补件编辑期间服务会清空当前 review 指针，因此当前 `/todo.scope_coverage` 按契约返回 `legacy_unknown`；父代当时的 `partial` 只在冻结版本中读取，不冒充当前代。服务端开关在该测试内通过 monkeypatch 开启，产品默认仍关闭。此验收不代表真实材料的业务准确性，也不代表发布或部署。

## 阶段 6：隔离端到端回归

- [x] 6.1 建立不含敏感正文日志的隔离样本矩阵：无总包、可读总包、处理中、终态不可读、缺页、主体错配、重复总包、来源冲突，以及与总包无关规则并存。来源分类、重复/冲突终态事件和 fail-closed 回归见 `test_optional_source_scope_lifecycle.py`。
- [x] 6.2 验证材料准入、规则结果、待处理工作区、API coverage、报告 readiness、预览/正式输出和任务收口。对应 API、报告/意见书、历史版本和文档回归见本阶段定向测试集；无关规则与范围限制保持分离。
- [x] 6.3 验证总包补件前后的父代/新代、checkpoint/快照、固定规则完整输入及历史报告兼容。隔离 SQLite 补件工作流在阶段 5 完成；本轮复跑 `test_standalone_workflow.py` 和版本/报告回归。
- [x] 6.4 验证 `BS_MOMA_SCOPE_SELECTOR_ENABLED` 独立开关的 on/off；selector 仍不能改变固定规则结果、finding 或报告结论。四种 selector/business-scope flag 组合均验证独立冻结；相同固定 finding 在 selector on/off 新代的规则投影、摘要计数和报告结论一致。因 selector registry 仍无已审核映射，该项不代表真实缩页效果已通过。
- [x] 6.5 运行项目约定的定向后端测试、两个相关 OpenSpec strict validation 和 `git diff --check`；正式回归测试保留。共 201 项通过。无临时验证脚本或材料新增。

**阶段验收门（隔离代码验收通过）：**隔离链路回归通过，未写入共享数据库/上传卷；证据区分代码通过、报告呈现通过与业务准确性验收。MoMA 真实材料效果仍需同一主线的合法配对总包及分包原件，当前不构成产品总包必传要求；具体样本缺口记录在 `bs-moma-ai-scope-selection/tasks.md` 的 6.3。阶段 7.1 的范围提示与报告边界已获业务确认；7.1a 来源集关联是 rollout 前置项。

## 阶段 7：业务验收与发布决策

- [x] 7.1 由用户/业务专家确认规则矩阵、范围限制文案、报告结论边界及来源状态处理；确认记录见 `business-acceptance-review.md`。盲审是否恢复由用户单独决定。
- [x] 7.1a 实现显式的总包来源集关联：用户确认采用 task-scoped 来源集 ID；只允许 setup 阶段通过 API/UI 关联、扩组、合并和解除；各原件独立保留，不按文件名/时间自动合并；完整且版本一致的来源集在启动准入中计为一份总包，独立总包或失效关系仍阻断；改类和删除会解除并审计，替换材料不继承关联，须重新确认；关联 ID、成员和关系版本冻结进新核查代及版本快照。回归覆盖来源聚合、API/DTO、来源集可启动/拆分后阻断/重关联后启动、启动后拒绝、操作者审计、替换重关联和历史 v1 快照兼容。关联后端回归 `test_general_source_set_api.py`、`test_optional_source_scope_lifecycle.py`、`test_standalone_workflow.py`、`test_bs_role_whitelist.py` 共 53 项通过；前端完整 Vitest 套件 116 项通过。`ensure_standalone_schema` 的 SQLite/MySQL 幂等补列均已验证；MySQL 验证仅在无持久卷的临时 `mysql:8.0.46` 容器中创建空测试表并运行两次，来源集两列成功补齐且重复运行无副作用，容器已停止并移除。未连接本机已有 MySQL 容器或共享数据库。
- [x] 7.2 用户已决定保持 `BS_OPTIONAL_SOURCE_SCOPE_LIMITATIONS_ENABLED=false`，不启用业务语义 rollout。Compose 显式透传且默认 `false`，`.env.example` 为 `false`，实际运行候选容器只读核验均未设置该变量；新代 flag 冻结和运行中切换不改旧代的回归已通过。该决定不修改实际 `.env`、不启动或重启容器。
- [x] 7.3 用户已决定结束本变更验收，不进行候选环境启用、观察或部署。未来若重新启动 rollout，必须新建隔离候选环境并另行批准；本决定不授权任何生产数据写入、数据库切换、服务重启或部署。

**阶段 7 rollout 预审（2026-11-12）：**业务 flag 缺省关闭，并在新核查代创建时冻结；切换环境变量不会改写已有代。已在 Compose 增加默认 `false` 的 backend 环境映射，并在 `.env.example` 声明关闭值；未修改实际 `.env`、未启动/重启容器、未启用功能。当前没有专用自动观察指标，需在未来另行批准候选 rollout 时人工对照新代状态、finding、范围提示和报告。此核查未修改数据库、上传卷或运行服务。

**阶段 7.2/7.3 最终决定（2026-11-12）：**`scope_policy_enabled()`、`freeze_policy()` 与新代初始化路径的回归通过（定向 8 项）；运行中的候选服务通过容器内只读环境核验均未设置该变量，按默认值保持关闭；`docker compose config --quiet` 通过。用户已决定保持 flag 关闭并结束本变更验收，不进行候选 rollout、观察或部署。未来若重新启动 rollout，必须另行批准隔离环境、观察指标和回滚步骤。

## 不在本方案内

- 不提交或清理既有未提交工作。
- 不重跑或改写共享数据库历史任务，不修改上传原件。
- 不开启 MoMA 选页、不启用任何未获业务准入的 rule mapping、不调用模型作为业务验收替代。
- 不进行盲审或部署，除非用户另行决定。
