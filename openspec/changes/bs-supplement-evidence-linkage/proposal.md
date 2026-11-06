## Why

补件材料虽然已进入任务材料集合，但 A2 各核查内核独立产出 finding，缺少持久的人员名单、岗位、证书字段与补件材料之间的跨材料关系，导致补件不能准确影响原有人员证件问题；问题描述只给笼统提示，复核人员无法据其核对名单页与证书页。

## What Changes

- 新增材料字段事实层：append-only 批次，保存原值、规范化值、质量状态、来源类型与页级原文定位。
- 新增人员证件三态关系：按姓名、主体、企业、岗位证书类别、证书编号逐项判定 `matched` / `conflict` / `unresolved`。
- 在材料读取收口事务内写入事实与关系；不重跑 OCR，不自动补读质量可疑页。
- 将补件证据投影到受影响 finding（支持 / 冲突 / 无法确认），并把关键关系接入人工复核与报告发布门槛。
- 收敛原 `bs-personnel-evidence-trace`、`bs-supplement-real-closure` 两个变更的范围与证据。

## Capabilities

### New Capabilities

- `evidence-linkage`: 补件材料事实、人员证件关系与问题证据投影。
- `personnel-evidence-trace`: 人员证件差异的可追溯描述与页级定位等级。
- `supplement-closure`: 补件重核的范围、基线与历史保护。

### Modified Capabilities

无。

## Impact

影响分包审查的材料解析收口、证据存储、问题投影、复核界面与报告发布门槛；沿用现有线上 OCR、模型与账号隔离。不新建独立大模型智能体，不把任务附件写入公共知识库，不修改生产服务、原任务或历史结论。部署、数据库迁移和历史数据改写不在本变更默认执行范围内。
