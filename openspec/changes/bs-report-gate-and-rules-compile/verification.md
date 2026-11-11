# 验证记录

## 2026-11-09 第一轮

### 已实施

- 新增 `reporting.report_gate_blocking`：只有 `undecidable` 进入正式报告门禁，`hit`/`miss` 不阻断。
- 未处理 `hit` 的人工处置标签改为“AI 定性（未经人工处置）”；未处理 `undecidable` 保留“未经人工处理，保留系统核查结果”。
- `service.complete_review` 改为只检查未处置 `undecidable`；未处置 `hit` 不再阻止任务收口。
- 确认稿和历史确认稿门禁使用 `report_gate_blocking`；draft preview 保留只读路径。
- 源项目 `rules_compile` 的 `compiler.py`、`publication.py` 和两份测试已回灌主产品 A。

### 验证

- `backend tests/test_bs_report_gate.py tests/test_bs_f5_review_gate.py tests/test_standalone_workflow.py`：**8 passed**（首轮，含完整业务流）。
- `backend tests/test_ai_adjudication.py tests/test_bs_report_gate.py tests/test_bs_background_ai_review.py tests/test_bs_ai_batch_capacity.py`：**20 passed**（AI 判定 + 门禁 + 后台 AI）。
- `backend tests/test_bs_report_gate.py tests/test_bs_f5_review_gate.py tests/test_bs_rules_compile.py tests/test_bs_rule_publication.py`：**18 passed**（门禁 + 规则编译）。
- `backend tests/test_bs_rules_compile.py tests/test_bs_rule_publication.py`：**10 passed**。
- `frontend tests/reportGate.test.js tests/reviewWorkbenchModel.test.js`：**5 passed**。
- `openspec validate --all --strict`：**7 passed / 0 failed**（A 仓库）。

### 未完成

- AI 结构化判定 DTO、finding 落地和复核页展示已实施；失败/批次 UI 仍可继续完善。
- 源项目材料事实/关系移植尚未验证完成，且源项目工作区与另一个对话共用，需隔离后继续。
- 规则发布候选的管理入口尚未确定。
- 真实材料、目标环境和专家验收未执行。

## 2026-11-09 稳健收口阶段

- 补齐规则发布候选的四类边界回归：身份字段全部缺失、真实编译失败、批量重复卡片、floor 数值无法自动比较。
- `backend/tests/test_bs_rules_compile.py` 与 `backend/tests/test_bs_rule_publication.py`：**14 passed**。
- OpenSpec strict：**11 passed / 0 failed**；`git diff --check`：通过。
- 本轮仅补充测试与验收记录，没有改变规则编译、发布候选或历史规则版本行为。

## 2026-11-09 新任务主线收口

- 已将报告门禁、AI 判定、规则发布候选的当前实现状态同步到主路线图和本变更任务状态。
- 当前主线聚焦新任务闭环：创建、材料准备、五步核查、AI 辅助、人工复核、报告和导出；旧任务恢复不作为当前发布前置条件。
- 真实材料、独立知识环境、目标环境参数、专家结论和正式发布授权仍保留为后续阶段输入，不以自动化测试或候选夹具替代。
- 后端全量回归：**763 passed**；OpenSpec strict：**11 passed / 0 failed**；`git diff --check`：通过。
