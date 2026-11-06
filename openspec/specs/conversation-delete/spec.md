# conversation-delete Specification

## Purpose
TBD - created by archiving change standalone-delete-conversation. Update Purpose after archive.
## Requirements
### Requirement: 用户可删除本人会话
用户 SHALL 能在历史任务列表删除本人新建或历史会话，并在删除前看到确认提示。

#### Scenario: 删除本人历史会话
- **WHEN** 用户确认删除自己的会话
- **THEN** 会话不再出现在列表和任务访问中，关联资料和审计记录保留

#### Scenario: 删除他人会话
- **WHEN** 用户请求删除不属于自己的会话
- **THEN** 服务拒绝并且不改变会话

#### Scenario: 删除执行中的会话
- **WHEN** 用户请求删除正在读取材料或执行 AI 核查的会话
- **THEN** 服务拒绝并提示等待处理结束

#### Scenario: 删除当前会话或取消操作
- **WHEN** 删除当前会话成功
- **THEN** 页面切换到其他会话或空状态，且取消操作不发送删除请求

### Requirement: 左侧控制任务列表
工作台 SHALL 在左侧提供任务列表收起和展开入口。

#### Scenario: 收起后展开
- **WHEN** 用户收起任务列表
- **THEN** 左侧保留展开入口，用户可从同一侧恢复列表
