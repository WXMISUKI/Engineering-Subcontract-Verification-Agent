## Why

独立版与智能体广场版在交付形态、身份、任务执行、规则证据和报告治理上存在长期分叉；按文件同步会带入平台依赖或产生双重事实来源。需要以独立交付为近期主线，先冻结跨仓能力映射和业务契约，再有顺序地推进现有未完成事项。

## What Changes

- 建立跨仓分包能力映射，逐项说明保留、回学、适配、暂缓或禁止直接迁移的理由、来源版本、验收证据和回退方式。
- 明确公共业务语义与交付形态适配层的边界，并将规则证据、页级复核、报告门禁、任务恢复和独立交付按依赖排序。
- 以现有 OpenSpec 变更作为各阶段实现任务的唯一跟踪来源，避免复制任务清单或把技术候选验证写成业务验收。
- 为涉及身份、数据库、报告事实源、后台任务、计费和部署的后续改动设置独立决策门。

## Capabilities

### New Capabilities
- `cross-repo-learning-governance`: 跨仓能力回学的映射、边界、证据和阶段验收治理。

### Modified Capabilities

## Impact

- 本变更只新增 OpenSpec 规格、能力映射和阶段追踪文档；不修改业务代码、数据库、运行配置或平台仓库。
- 相关既有变更包括 `bs-moma-adaptation-baseline`、`bs-moma-ai-scope-selection`、`bs-ai-timeout-diagnostics`、`bs-negative-candidate-evidence-gate`、`bs-report-adaptation`、`bs-optional-source-scope-limitations-20261112`、`bs-supplement-evidence-linkage`、`bs-task-safe-recovery` 和 `standalone-docker-delivery`。
