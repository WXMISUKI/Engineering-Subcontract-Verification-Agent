# V1.3 单主体第一版实施与验证记录

日期：2026-11-16。状态：第一版实现与技术验证完成；任务 4.4 真实材料业务验收未完成。分支 `report-template-v13-20261116`，基点 `b65688b9ab5bf78baa10ef1a5f6316757ccdea3d`。工作区包含前序未提交改动，本次没有清理、暂存或提交它们。

## 已确认边界

- 双模板可选，旧五阶段模板默认；V1.3 第一版仅支持单主体，多主体明确提示使用旧版。
- 系统给客观摘要；审批意见、期限和三类签字留空，在导出 Word 中人工填写。系统不保存外部填写，不增加审批权限流程或数据库字段。
- 八维度只映射已有能力。设备、资金支付及完整工资保障未覆盖，不能因存在八行表格就认定全部核查完成。
- 项目、业主、总包、分包人、范围及证据复用本代事实；冲突不选首值，未知日期和页数不推算，不导入附件样例事实。

## 实现入口

后端目录：`backend/app/plus/blockchain_supervision/`。

| 文件 | 本次作用 |
| --- | --- |
| `report_template_v13.py` | 纯投影、八维度、同源问题、统计及空白审批；模板 `v1.3`、schema `bs.review-opinion-document.v1`、映射 `2026-11-16.1` |
| `report_template_render.py` | 同一 blocks 渲染 Word／PDF，五列表格及重复表头、长行跨页 |
| `reporting.py`、`service.py` | 显式启用投影；新审查快照冻结 report／opinion 备选文档 |
| `router.py`、`export.py` | 模板参数贯穿读取、预览、四种导出及内容指纹；保留原权限与门禁 |

前端目录：`frontend/src/plus-ui/blockchain-supervision/`。`components/ReportTemplateV13.jsx` 渲染同一 blocks；`components/ReportView.jsx` 提供旧版默认选择；工作台和 `lib/api.js` 贯穿模板选择。切换时清理预览并使迟到报告、意见及下载响应失效。历史列表既有下载仍走旧模板。

历史不重建：缺少 V1.3 投影的旧快照请求新模板返回 409；旧版返回剥离备选投影。指纹包含模板、映射和事实，排除派生生成日期；人工填写后的 Word 需另行留档，不能继续用系统原指纹声称相同内容。

## 定向回归

后端在 `backend` 目录使用既有 `.venv`：

```powershell
rtk proxy .venv/Scripts/python.exe -m pytest tests/test_report_template_v13.py tests/test_report_template_v13_http.py tests/test_report_document.py tests/test_bs_report_version_http.py tests/test_business_report.py tests/test_bs_export_summary.py tests/test_bs_report_gate.py tests/test_bs_task_report_readiness.py tests/test_supplement_report_replay.py -q
```

结果：**147 passed，86.48 秒**。115 条警告来自已有配置及 Starlette／JWT 弃用提示，没有测试失败。测试使用隔离数据，未访问客户数据库或重跑真实任务。

前端在 `frontend` 目录：

```powershell
rtk proxy npm test -- --run tests/reportTemplateV13.test.jsx tests/reportTemplateSwitch.test.jsx tests/reportSummary.test.jsx tests/reportReadiness.test.jsx tests/reportGate.test.js tests/reportWorkbenchImport.test.jsx
```

结果：**6 个文件、39 项通过**。包含实际挂载工作台后的延迟响应／模板切换／意见生成／下载失效验证。未运行 npm build。

新增正式回归保留：后端 `test_report_template_v13.py`、`test_report_template_v13_http.py`；前端 `reportTemplateV13.test.jsx`、`reportTemplateSwitch.test.jsx`。

规格与格式检查：

```powershell
rtk proxy openspec validate bs-report-template-v13-20261116 --strict
rtk git diff --check
```

结果：均通过。

## 实际导出与版面

证据目录：`.research/report-template-v13-20261116/`，该目录被 Git 忽略。所有样稿均为 `sample_report()` 的合成测试事实，不是真实业务报告。

| 产物 | 实际检查 |
| --- | --- |
| `v13-sample.docx`、`v13-sample-word-render.pdf` | Microsoft Word 只读打开并转出 4 页；逐页检查标题、八维度、审批签字、附件及五列 |
| `v13-sample.pdf` | 产品原生 PDF 4 页；逐页检查相同内容和页面边界 |
| `v13-long.docx`、`v13-long-word-render.pdf` | 长问题正文和附件单元格实际渲染 6 页；全页联系图检查跨页、重复表头、五列及末尾事实 |
| `v13-long.pdf` | 原生 PDF 6 页；全页联系图检查同上，自动抽取确认“最终事实终点”保留 |

渲染发现并修复 Word Title 样式默认蓝色底线，以及 PDF 审批线／签字折行。Word 与原生 PDF 使用各自排版引擎，内容同源，不要求逐页断点完全相同。

## 工具使用与限制

- using-superpowers：选择适用流程；pstack：按可验证单元推进，不扩大架构和业务边界。
- CodeGraph：前段已实际探索报告事实、投影及调用关系，再对照源码；记录索引 399 files／6781 nodes／16748 edges。
- gstack-spec 用于需求收敛，gstack-verify 用于检查选择和验证流程。自动 checks detect 因缺少 Bun 失败，回退项目声明的定向测试；不宣称 gstack 自动验收通过。
- documents：附件结构解析及导出版面检查。打包渲染脚本因无 LibreOffice 不可用，改用本机 Microsoft Word COM 只读转 PDF；未改写原附件。版面检查的临时 Python／PowerShell 脚本完成后删除，样稿及截图保留供审阅。
- 使用既有 Python 环境和 RTK；PowerShell 内建命令通过 `rtk proxy powershell` 执行。

## 未完成验收及下一步

1. 真实材料业务验收（任务 4.4）：在后续运行最新代码的本地环境，以同一单主体任务对照旧版与 V1.3，核对角色、标题、基准日、问题及证据、统计、八维度范围和 Word 人工填写效果。
2. 第一版明确不包含：逐主体出具、独立短名／项目部等补充录入、完整页数台账、新维度规则和系统内审批流；需要时另确认切片。
3. 本次没有数据库迁移、付费模型调用、真实任务重跑、共享服务器重启、远端部署或 Git 提交。既有运行服务未因本次改动自动被视为最新版本，技术测试不代替业务验收或部署验收。

## 用户验收环境启动（2026-11-16，后续授权）

用户随后明确要求启动服务供亲自验收，并说明上一轮改动已做过代码及一次真实测试。此为用户提供的上一轮验收信息，不等同于本次 V1.3 已通过真实材料业务验收。

- 复用既有 `bstrialv2` 本地试用库和上传卷，源码后端 `bs-local-ai-acceptance-20261110` 只读挂载当前仓库 `backend/app`；8089 另一套打包环境未调整。
- 启动前确认没有解析中材料；任务表残留 running 状态对应旧记录，最新 AI 任务事件为 2026-11-11 的失败终态，当前后端自 2026-11-16 启动后没有新的 AI 开始事件。
- 按本次启动授权重启该源码后端加载 V1.3；启动 Vite `127.0.0.1:5173`，设置 `VITE_API_PROXY=http://127.0.0.1:8002`，沿用既有代理。本次不迁移数据库、不重跑任务或调用模型。
- 实测首页 200、`5173/api/health` 正常、未登录 `5173/api/session` 401。从运行中 OpenAPI 确认 report／report export／opinion／PDF preview／文本 preview／opinion export 六入口均有 `template_id`；Vite 实际返回的 ReportView 包含 V1.3 选择。
- 验收地址 `http://127.0.0.1:5173/`，使用原试用账号，单主体报告页选择 V1.3。旧历史快照未冻结新投影时继续使用旧版，不因测试要求自动重跑真实任务。用户登录及报告内容验收仍待用户操作。

## 2026-11-17 报告细节修正

- 原因：正文日期提取仅识别“报送日期：YYYY年M月D日”字段，未识别报审材料中的“你部【M月D日】报送的”；附件二两种输出路径都在主题后拼接内部类别括号。
- 修正：仅从报审材料中的明确报送语境提取括号日期并保留页码/原文证据；附件二类别列仅输出模板主题名。核查基准日不会作为报送日期的回退值。
- 映射身份更新为 `2026-11-17.2`，已冻结的历史快照不重建。
- 定向测试：报送日期提取 3 项通过；V1.3 正文块和 Word 模板 2 项通过。真实业务任务未重跑，下载件未被修改；完整验收仍由用户在实际任务上确认。

## 2026-11-17 V1.3 重复导出入口修正

- 根因：V1.3 报告和意见书端点返回同一 `template_document`，报告页又在正式意见书区域保留了原型审查报告 Word/PDF 按钮，导致同一内容出现两组导出入口。
- 修正：选择 V1.3 时仅显示正式意见书 Word/PDF 导出；legacy 模板仍显示独立的审查报告导出。后端兼容端点保留。
- 验证：前端 34 个测试文件、158 项通过；OpenSpec strict 与 `git diff --check` 通过。未运行 npm build、未部署或重启服务。
