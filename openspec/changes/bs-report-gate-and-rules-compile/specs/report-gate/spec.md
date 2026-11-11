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
