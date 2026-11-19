# P3公共三页面阶段验收

状态：P3.1/P3.2完成，P3.3进行中；P4真实验收未执行。现有试用服务未切换。

## P3.1

公共PrototypeV3Shell/PrototypeFlowHeader按模式显示上传资料、智能核查、审查报告。复用CheckRunView公共页面骨架、阶段卡片与事件时间线表现组件，准入仍沿用原有五步和完成回执逻辑；倒查按自身快照投影，无强制复核动作。材料回看复用上传清单/预览，接受后角色与关系只读，不进入准入material-edit。

定向前端回归：RetrospectiveWorkspace/checkRunFeedback/aiCompletionWarning/CheckRunTime，29 passed。验证三阶段导航、未到达报告禁用、只读回看不重新启动、准入进度及完成回执保持。未运行npm build，未进行浏览器或真实Provider验收。

## P3.2

原生EventSource hook从准入控制器抽到公共hooks，两模式复用。登记六种倒查事件；服务端按白名单投影，只返回运行身份、批次活动和执行状态，不广播冻结全文、检索原文及模型结果。倒查按run_id过滤、sequence去重；浏览器原生Last-Event-ID重连、stream.reset重放，切换身份/任务关闭旧流且忽略迟到帧。刷新重新读取保存快照及回放，不调用start。

完成检查点补活动计数，状态接口返回最新开始/完成活动的安全投影，分母标为批次而非步骤。轮询读取失败延时恢复，绝不自动重启付费作业。前端相关31项通过（包括正式流回放、去重、切换身份、reset及终态关闭回归）；后端运行11项、事件安全投影1项通过。真实浏览器断线恢复归P4。
