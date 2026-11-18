# 报告生成修复：实现与本地验证

日期：2026-11-16。分支：`report-template-v13-20261116`。本轮遵循用户最新决定：先恢复报告生成、查看报告内容，OCR 优化后续处理。

## 政策与实现

OCR 未读页、可疑页和解析失败作为非阻断提示及范围披露，不要求逐页人工确认。可追溯的读取缺口仍保留 AI 未覆盖记录，不改写为符合或未命中。真实执行失败、未启动、在途、未知或失效覆盖、未处理业务无法核实项继续阻断。

- `backend/app/plus/blockchain_supervision/service.py`：共享正式输出/补件候选的覆盖校验；允许有可追溯读取缺口的有效部分结果；readiness 返回非阻断读取警告。
- `backend/app/plus/blockchain_supervision/parsing.py`：仅修正文档说明，无 OCR 行为变化。
- 前端工作台、`reportReadinessModel.js`、`ReviewWorkbenchLayout.jsx` 和 `ReportView.jsx`：复核保存后刷新任务就绪；生成前重新读取服务器状态；显示真实阻断原因和非阻断 OCR 提示。

双模板继续可选，旧版默认；V1.3 保持单主体适用边界，审批意见、期限和签字由人工在 Word 中填写。

## 自动验证

使用项目 `backend/.venv`，后端相关回归共 **134 个不同用例通过**：

```powershell
# 在 backend 目录执行
rtk proxy ./.venv/Scripts/python.exe -m pytest tests/test_bs_task_report_readiness.py tests/test_bs_report_gate.py tests/test_report_template_v13_http.py tests/test_bs_report_version_http.py tests/test_report_reading_summary.py -q
rtk proxy ./.venv/Scripts/python.exe -m pytest tests/test_bs_supplement_publication.py tests/test_supplement_report_replay.py tests/test_optional_source_scope_lifecycle.py tests/test_bs_finding_semantics.py -q
```

第一组 57 passed。第二组初次 76 passed、1 failed，失败为旧读取阻断断言；按新政策更新后单独重跑 `test_bs_finding_semantics.py`，16 passed。第二组合计 77 个不同用例通过。

覆盖两模板的报告/意见书、Word/PDF、preview、证据包接口成功，真实失败和未处理业务项阻断，原回执和读取降级记录保留。

前端既有全套 **33 个文件、154 passed**；新增 `frontend/tests/reportGenerationFlow.test.jsx` **3 passed**，合计 **157 个不同用例通过**。新增用例覆盖 OCR 提示下生成报告、最后一条复核后刷新就绪，以及点击时服务器阻断原因反馈。未执行 npm build。

以下四项 OpenSpec strict validate 均通过：`bs-ocr-quality-advisory-20261116`、`bs-report-gate-and-rules-compile`、`standalone-platform-learning-governance-20261115`、`bs-report-template-v13-20261116`。

工具采用 using-superpowers、pstack 根因追踪和 gstack-verify 流程。gstack checks 的 Bun 运行时当前不可用，使用项目既有 pytest/Vitest 验证；不将此描述为 gstack 自动检查全部通过。

## 本地真实链路

更新并重启本地源码后端 `bs-local-ai-acceptance-20261110`，前端 Vite 使用 `VITE_API_PROXY=http://127.0.0.1:8002`，地址 `http://127.0.0.1:5173/`。API 健康检查 200。

在已登录的 Edge 浏览器打开任务 7，确认 OCR 提示和可用的生成按钮，点击“生成审查报告”：

- `POST /api/plus/blockchain-supervision/tasks/7/complete` 返回 200。
- 页面进入 `http://127.0.0.1:5173/?bs_view=report`，报告正文、模板选择和 Word/PDF 导出入口显示。
- 数据库只读确认任务状态 `done`，报告版本 1，V1.3 投影已冻结；29 条既有人工复核回执保留。
- 材料 6 共 58 页，读取缺口页 18、23、27、57 保留；A1 的 20 个 `reading_gap_not_run` 和 20 个 `reviewable_partial` 计数保留。

本轮只完成生成流程验证。报告仍展示待核实和范围限制，正文业务质量、真实导出文件版式由用户继续验收。未新增数据库迁移，未操作客户或线上环境，未重新 OCR、重跑核查或调用模型。
