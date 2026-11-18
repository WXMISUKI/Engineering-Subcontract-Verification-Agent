## ADDED Requirements

### Requirement: 无法核实问题全部处理后才允许正式报告

系统 SHALL 在当前审查代次全部未处置 `undecidable` 问题处理完成后，才允许生成、导出正式报告、意见书和证据包；明确标记为 draft preview 的只读中间稿可按现有预览路径保留，但不得作为正式输出。

#### Scenario: 存在未处置无法核实项
- **WHEN** 当前代次仍有 `undecidable` 且没有 accepted / ignored 回执
- **THEN** 报告、意见书、Word/PDF 和证据包均返回结构化阻断，且不生成正式文件

#### Scenario: 全部无法核实项已处理
- **WHEN** 当前代次所有 `undecidable` 均已有 accepted / ignored，或已通过新代次重跑解决
- **THEN** 报告、意见书、Word/PDF 和证据包恢复可用

#### Scenario: 存在未处置不符合项
- **WHEN** 当前代次仍有未处置 `hit`
- **THEN** 正式报告不阻断，但对应条目逐项标注“AI 定性、未经人工处置”

### Requirement: 候选代次与报告门禁组合生效

系统 SHALL 在 candidate 代次未发布时阻断正式报告，即使 findings 暂时没有未处置项。

#### Scenario: 候选未发布
- **WHEN** 当前任务处于 candidate 状态
- **THEN** 正式报告、意见书和导出返回 `supplement_candidate_unpublished`，preview 路径保持只读

#### Scenario: 已发布代次
- **WHEN** 当前代次已发布且没有未处置 `undecidable`
- **THEN** 报告、意见书和导出使用同一发布代次与 generation_id

### Requirement: 报告门禁单一实现

报告、意见书、Word/PDF 和证据包 SHALL 使用同一门禁函数和同一结构化阻断原因。

#### Scenario: 不同输出入口
- **WHEN** 用户分别请求报告、意见书、Word、PDF 或证据包
- **THEN** 阻断条件、错误码、generation_id 和提示文案一致

### Requirement: 任务级报告就绪由服务端唯一计算

系统 SHALL 在任务详情增量返回 `bs.report-readiness.v1`，分别表达 `draft_readable`、`confirmed_output_ready`、`task_close_ready`，及执行、读取质量、人工处置、阻断原因和下一步。使用现有任务与快照只读计算，不新增持久化事实源。正式输出和收口入口 SHALL 重新调用共享判据，不信任客户端布尔值或 finding 计数。

#### Scenario: 人工回执已经全部处置但存在真实执行缺口
- **WHEN** 当前代仍有所选分组未完成、真实 AI 执行失败、在途或未知执行缺口
- **THEN** 正式报告、文件预览、Word/PDF、证据包和收口 SHALL 继续阻断；accepted/ignored 不解除真实执行缺口
- **AND** 可选来源未启用或未提供按既有范围政策披露，不自动扩大为全部任务技术失败

#### Scenario: 仅存在 OCR 读取质量限制
- **WHEN** 当前代业务复核已完成且真实执行条件满足，但仍有未读/可疑页、解析失败或可归因的读取范围未覆盖
- **THEN** 系统 SHALL 保留读取状态和报告披露，不阻断正式输出或收口，不要求页级人工确认；不将未覆盖改为符合

#### Scenario: 前端缓存或未知 DTO
- **WHEN** 已缓存意见书但当前正式就绪撤回，或 DTO 缺失、未知版本、字段无效
- **THEN** 正式输出和收口入口 SHALL 保持不可用，并显示原因或刷新提示

### Requirement: 草稿仅在应用内读取且披露缺口

同代同输入的当前中间结果 MAY 以应用内草稿 JSON 读取，执行/读取/覆盖缺口 SHALL 随草稿披露，摘要不得表示正式合规结论。当前代文件输出 SHALL 只接受 `stage=confirmed`；旧客户端请求 draft 文件 SHALL 返回结构化 `draft_file_output_blocked`，不得静默改成正式输出。

#### Scenario: 只读草稿与正式文件
- **WHEN** 输入和审查代有效，但真实核查执行尚未完成
- **THEN** 草稿 JSON MAY 返回中间结果和披露，且不创建回执/历史版本/导出事件；正式输出继续返回可解释的 409，OCR 质量提示本身不构成该阻断

#### Scenario: 输入已变或候选未发布
- **WHEN** 当前输入指纹不匹配、审查身份失效或补件候选未发布
- **THEN** 不得借草稿例外将旧代结果作为当前可读结果；沿用候选专属只读预览边界

#### Scenario: 历史读取与导出
- **WHEN** 请求指定 `version_no` 的冻结报告
- **THEN** SHALL 沿用该版本的内容、回执及原历史门禁，不以当前 readiness 补齐或重算历史
