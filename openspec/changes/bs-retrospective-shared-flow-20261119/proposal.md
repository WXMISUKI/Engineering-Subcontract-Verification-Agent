## Why

当前倒查试用已形成单合同自动报告，但采用独立简化页面，未复用公共上传与核查页面，总包尚未真正参与运行。用户最新要求总包和分包均必传，并先讨论复用方案及实施顺序。

## What Changes

- 本变更已获用户确认并进入分阶段实施，替代旧倒查变更中“总包可选”的新运行产品要求；历史单合同结果保留。
- 复用现有上传、智能核查和报告页面骨架，以任务模式适配业务动作、状态及事件。
- 倒查新运行前后端均验证总包和分包来源，冻结两侧身份与版本，增加真实上游条款比较。
- 保留规范/负面清单检查、双侧证据校验和自动八列表报告；不加入准入五步或强制人工复核。
- 以双合同受理、比较引擎、公共页面、真实试用分阶段验收；按阶段实施，现有服务不部署切换。

## Capabilities

### New Capabilities
- `bs-retrospective-shared-flow`: 公共三页面、双合同受理与可追溯比较闭环。

### Modified Capabilities
无；旧变更仍为历史实现记录，本变更明确后续替代范围。

## Impact

前端BlockchainSupervisionWorkbench、PrototypeUploadPage、流程条、CheckRunView、事件订阅和报告容器；后端倒查readiness、输入快照、上游抽取、双侧证据与报告投影。具体方案见docs/design/2026-11-19-retrospective-shared-flow-plan.md。原分支WIP逐项评估，main8090与5174服务保持既有入口。
