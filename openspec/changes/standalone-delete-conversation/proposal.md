## Why

独立版历史列表缺少与智能体广场一致的删除入口，用户无法移除自己的新建或历史审查。

## What Changes

- 为本人会话增加确认后的软删除和列表入口；关联任务、材料与审计记录保留。
- 拒绝跨账号操作及正在处理的会话；删除当前会话后切换到剩余任务或空状态。
- 将侧栏开关置于左侧，收起后从左侧恢复。

## Capabilities

### New Capabilities
- `conversation-delete`: 独立版会话删除与权限保护。

### Modified Capabilities

## Impact

- 独立版 API、侧边栏及自动化验证；不修改数据库结构。
