# 独立版任务级报告与收口门禁：本地技术回执

日期：2026-11-16。范围：治理路线阶段 2 的 A0–A2，仅为首批规则实施的前置门禁。持久目标与阶段 2 继续进行中。基础提交为 `8a1b83aed3706fef8ce5488e6c7a74b8ef8d8434`，改动保留在工作区。

## 结果

- `GET /tasks/{id}` 增量返回 `bs.report-readiness.v1`，独立表达应用内草稿、正式输出与收口权限，以及执行、读取质量、人工回执、原因和下一步。
- 正式报告/意见书 JSON、文件预览、Word/PDF、证据包与收口共享只读技术/人工判据；人工 accepted/ignored 不能解除未读/可疑页、解析失败、必需执行失败或未完成。现有可选来源范围政策保留。
- 当前代草稿 JSON 披露缺口；摘要/建议/意见书签署建议暂不形成正式结论，原始明细与计数保留。草稿文件返回 `409 draft_file_output_blocked`，不静默转换稿别。应用内 Markdown 预览透传同样的门禁披露。
- 候选未发布阻断当前报告及正式输出。保留独立版“收口发布候选”边界：技术/人工复核通过时 `task_close_ready` 可以为 true，而 `confirmed_output_ready` 仍为 false。工作台报告页提供真实收口按钮。
- 指定 `version_no` 的历史内容与原历史门禁不被当前 readiness 重算或补齐。当前输入/规则指纹变化不能借草稿例外通过。
- 前端以任务详情为门禁来源；未知/缺失/结构无效或自相矛盾的 DTO 拒绝放行；刷新期间撤回权限，缓存意见书按钮受控，服务端拒绝后重新读取任务。旧输出错误缓存不能盖过新任务状态。
- 未新增数据库表列，未执行数据库迁移、客户部署、真实材料、付费模型或 OCR 调用。

## 已执行验证

环境：`backend/.venv/Scripts/python.exe`，隔离 SQLite；既有 fixture 阻止外部网络。

后端命令（在 backend）：

```powershell
rtk proxy ./.venv/Scripts/python.exe -m pytest tests/test_bs_task_report_readiness.py tests/test_bs_report_gate.py tests/test_bs_report_version_http.py tests/test_optional_source_scope_lifecycle.py tests/test_bs_supplement_publication.py tests/test_report_reading_summary.py tests/test_supplement_report_replay.py -q --tb=short --disable-warnings
```

结果：106 passed，90 warnings，35.79 秒。其后仅增加“候选可收口、正式输出仍阻断”的正式回归，任务级门禁文件单独验证为 24 passed、35 warnings（13.63 秒）；此前该文件 23 项，新增 1 项，合计 107 个不同后端用例得到验证，不将重复运行计作新的业务覆盖。

前端命令（在 frontend）：

```powershell
rtk proxy npm test -- --run tests/reportReadiness.test.jsx tests/reportSummary.test.jsx tests/reportWorkbenchImport.test.jsx tests/reportGate.test.js tests/App.test.jsx tests/aiFailureNotice.test.jsx
```

结果：6 文件 37 passed。覆盖缓存权限撤回、未知协议、异常布尔、过期执行状态、刷新后的权威门禁恢复和候选收口按钮；入口导入及既有摘要/失败提示回归通过。未运行 npm build。

浏览器：使用临时 Vite 合成组件场景（127.0.0.1:5198），实际观察读取缺口时五个正式按钮禁用、全部就绪时启用且意见书 PDF 回调成功、未知协议时再次禁用。该验证不连接业务接口，不是真实任务完整链验收；最后补充的收口按钮由组件测试覆盖。临时 HTML/JSX 已删除，临时 Vite 已停止。

规格：两个相关 OpenSpec strict 验证通过；改动通过 `git diff --check`。正式回归文件保留。

## 未完成与下一步

意见 4/5/13/25 + 21/24 的规则、逐侧/逐人证据和质量归组尚未实施。下一项为治理目录 `stage-2-semantics-storage-decision.md`：先确认独立版语义字段的持久化方案，再进入 A3。不提前关闭阶段 2、页级复核阶段或 P0–P6 交付。

现有旧快照、候选发布、可选来源和合成文件输出仅在所列回归范围内得到技术验证；真实材料/专家业务验收、客户数据库迁移及目标环境发布均待独立授权和证据。数据库结构选择尚未确认时，不能以 UI 投影或消息推断替代持久化语义。
