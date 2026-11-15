# 工程分包审查：AI 核查因材料范围中断交接书

交接时间：2026-11-13（北京时间）。项目：`C:\project\nanjin\Engineering-Subcontract-Verification-Agent`。

本文交给新窗口的实现 AI。用户要求先弄清当前故障和开发现状，再分析、修复、验收；不要把本文中的待调查假设当成已经确认的业务规则或操作授权。请以本文件、当前工作区代码、相关 OpenSpec 和实时只读证据为准，先核实状态是否变化。

## 1. 用户目标与当前状态

用户在本地上传一份劳务分包报审材料、新建任务后，希望系统完成五步核查、AI 辅助核查、人工复核和报告。此前用户已明确要求：对于任务本身的功能和业务逻辑，应由实现者先自行定位和验收，不能反复让用户测试同类低级错误。用户当前要求本次停止修复，编写清晰交接书，以便新窗口继续。

新任务的后台 AI job `5c54d29c92ff496187bb148c077f7ca7` 于 16:01:27 失败，界面显示“后台 AI 核查未完成 · 材料范围不完整”。页面上的“批次并发执行，回执按发生时间显示，批次编号可能不按顺序出现”只是排序说明，不是失败原因。

**已经定位到触发条件，但尚未确定最佳业务修复，也没有修复本次新故障。** 不能把容器健康、上次测试通过或上述失败条件已定位，说成该真实任务已经完成验收。

## 2. 本次真实任务：只读证据

以下为 2026-11-13 本次会话从运行中独立版本地 MySQL 只读取得的事实，继续前需重新核对：

- 任务 `bs_task.id=4`，状态 `running`，`completed_at` 为空。核查代 `review_run_id=b1b31eec39564230ab9af84c207e1d56`，AI 分组为 `completeness`、`signature_chain`、`a1`、`a2`、`internal_records`。
- job 最后事件 `check.ai_background_failed`，task 4、sequence `850`，`failure_categories=["incomplete_material_scope"]`。其 A1 的普通 AI 请求 ID 为 `98c319b26dde495cb3fd06805023e985`；负面清单请求 ID 为 `e8280bb7e5ec43a0b38e6b67e4bda0c5`。
- 负面清单终态事件 `check.negative_candidates_finished`，sequence `849`，A1 run ID `62b2263871bd41e7a85f0648f46ef8ca`，验证版本 `bs-negative-candidate-evidence-v2`；`model_invoked=false`、`finding_records` 为 0。20 条候选均为 `execution_status=not_run`、`unavailable_reason=rule_signal_not_found`、`source_reading_complete=false`。
- 使用运行版本 `collect_current_coverage()` 得到：`completeness` 6 条 `partial`、6 条可复核；`signature_chain` 1 条 `partial` 和 1 条可复核 `not_run`；`a1` 18 条 `partial`、18 条可复核，以及 20 条 `not_run`、`blocking_not_run=20`、`not_assessed_no_signal=0`、失败分类 `incomplete_material_scope`；`a2` 7 条、`internal_records` 1 条仍为 `not_started`。
- 因此第 28/28 批“部分建议需人工核实”并非直接触发本次失败：A1 的 18 条 partial 都被认作可人工复核。后台在 A1 的负面候选覆盖门禁处中止，A2、内部记录组的 AI 工作尚未开始。普通 AI 批次已发生模型调用；负面候选阶段未调用模型。历史工作不能描述为“整个任务所有 AI 核查已完成”。
- 活跃材料 `bs_material.id=4`，主体 ID 4，角色 `labor`，文件名 `预制板梁劳务分包报审（武汉龙辰建设有限公司）.pdf`，`parse_state=done`，67 页。`reading_coverage` 返回 55 页可读、12 页 `suspect_pages`，同时被计入 `unread_pages`：**20、21、26、28、31、35、37、39、43、61、63、64**；`reading_complete=false`。`degradations_json` 的 `parse_incomplete` 记录把这些页标为 OCR 质量可疑，`ocr_failed_pages=[]`。
- 这些“未可靠读取页”不能简单说成“没有 OCR 文本”：只读检查每页存储文本长度，第 28 页 1926 字，第 37、39、61、64 页分别约 2.3 万字符；第 35、43 页命中图片占位短文本判据。其余页未必命中当前四个即时质量检查函数，但仍由已持久化的 `parse_incomplete.ocr_suspect_pages` 标为可疑。具体质量问题、页面用途、是否误报和是否与合同范围相关，**尚未逐页对照原 PDF**。
- 解析记录中 `ocr_retry_pages=[3,5,36,38,40,42,44]`、`ocr_retry_recovered_pages=[]`；这些与上述 12 页不是同一集合。原因需从解析过程和 `page_state_history` 查明，不能仅凭此认定重试失败或重试未触发。

### 触发链

1. `negative_candidate_plan.plan_negative_candidates()` 只取当前主体分包合同的可读页与范围片段。`source_reading_complete` 在所有相关合同均无读取缺口且 `reading_coverage(...).reading_complete is True` 时才为真。
2. 合同可读片段中没有该规则信号，20 条候选的原因记为 `rule_signal_not_found`；但因同一材料有 12 页质量可疑，20 条的 `source_reading_complete=false`。
3. `ai_advisory_snapshots.summarize_coverage()` 只在 `rule_signal_not_found` **且** `source_reading_complete is True` 时计为非阻断的 `not_assessed_no_signal`；本任务 20 条均走阻断分支并追加 `incomplete_material_scope`。
4. `background_ai_review._run_background()` 检查到 `blocking_not_run=20`，抛 `BackgroundReviewIncomplete`；失败事件使界面显示“材料范围不完整”。

该链符合当前工作区及运行代码的既有实现。**待判断的是：质量判定是否正确，以及把整份合同的任何可疑页扩散成所有无信号候选和整个后台作业的硬阻断是否符合业务、是否有可操作的修复闭环。** 目前不能据此断言 OCR 一定误报，也不能为使任务完成而忽略可疑页。

## 3. 以前问题、已经完成的修改和局限

### 3.1 原始问题：19 条负面清单辅助 finding 重复引用

用户最初指出 job `1e95841951534ff6bce3918502f4f264` 的多个“无法核实”问题复用了合同范围/价款的同一片段，却被挂到不同负面清单规则。只读核查 task 2 证实 19 条辅助 finding 共用两段泛化引文，缺少对每条规则专属工程事实的相关性判断。

当前工作区已实现候选规则信号与引文相关性门控；无信号条目不形成 finding，不等于合规通过或确定性未命中；同一引文可分别支持多条规则，前提是分别满足各自信号和上下文。原固定规则、`W16-02` 确定性计数、MySQL 与 WeKnora 职责未因此改造。旧候选校验版本被视为失效，不把历史人工处置静默带到新候选代。

已存在 OpenSpec：`openspec/changes/bs-negative-candidate-evidence-gate/`，其 `proposal.md`、`design.md`、`tasks.md` 和 `specs/negative-list-candidate-review/spec.md` 均为未跟踪文件。注意：其中第 3 节和早期验证记录对应历史时点；第 5 节记录上次覆盖修复。**既有离线验收不证明 task 4 的真实链路已通过**。先前该变更的 4.2 仍未完成，task 2 是 done 态，不能绕过状态守卫强行重算或转移旧人工意见。

### 3.2 上一次中断和已实现的修复

旧 job `0442dba7ed3f45129ee5a6a63ab45c8a`（task 3）因 20 条无信号候选被统一归为 `incomplete_material_scope` 而失败。上次修复：

- 在 `ai_advisory_snapshots.py` 保留 `counts.not_run`，新增 `not_assessed_no_signal`、`blocking_not_run` 和 `blocking_not_run_count()`；仅明确且来源读取完整的无信号候选不阻断，未知原因 fail closed。
- 在 `background_ai_review.py`、`service.py`、`router.py`、`frontend/.../lib/autoAiReview.js` 和 `BlockchainSupervisionWorkbench.jsx` 贯通完成、重试、状态 API、任务进度、报告和中性提示。
- 在 `negative_candidate_plan.py` 新增 `source_reading_complete`，将候选 schema / validation 版本分别提高到 `bs-negative-candidate-v4` / `bs-negative-candidate-evidence-v2`；旧候选事件不自动复用。范围选页本身不等于全合同读取完成，不能只看 `incomplete_material_scope` 布尔值推断可跳过。
- 已运行定向回归：后端 `test_ai_negative_candidate_relevance.py`、`test_bs_scope_unlocated.py`、`test_bs_background_ai_review.py`、`test_bs_report_gate.py`、`test_bs_ai_terminal_retry.py` 共 **69 passed**；前端 `tests/autoAiReview.test.js` **6 passed**；`openspec validate bs-negative-candidate-evidence-gate --type change --strict --no-interactive` 通过。这是对应时点的自动化证据，且离线 worker 测试输入为合成数据，不是 task 4 真实业务链路通过。

上次验证遗漏了“真实扫描合同中存在大量质量可疑页，20 条无信号候选统一受到整合同读取门槛影响”的端到端场景。上次虽建议用户新建任务测试，但未先完成该真实材料链路验收，这一点需要如实承认并在后续修复中补齐。

## 4. 当前代码、数据及服务边界

- 仓库分支 `main`，HEAD `d9cddde48f1552edfdeaf71a7f1eb7c892c59f2f`，这是交接时快照；不要假设它仍是远端最新。
- 工作区有大量与上述问题直接相关的未提交改动，包括 `backend/app/plus/blockchain_supervision/{ai_advisory_snapshots,ai_evidence,background_ai_review,negative_candidate_plan,router,service}.py`，`backend/tests/test_bs_background_ai_review.py`、`test_bs_report_gate.py`、`test_bs_scope_unlocated.py`，前端工作台和 `lib/autoAiReview.js`、前端测试，以及未跟踪的 `backend/tests/test_ai_negative_candidate_relevance.py` 和上述 OpenSpec 目录。交接书本身也是新增文件。请先 `rtk git status --short` 和 `rtk git diff` 确认，**不要 reset、清理、覆盖或假设这些改动已经提交**。
- 当前本地 Compose project 是 `engineering-subcontract-verification-agent`，仓库 `compose.yaml` 和 `.env`；入口 `http://127.0.0.1:8089/`。backend/frontend 是本地候选镜像；只读验证运行中的 backend 确实加载 `bs-negative-candidate-evidence-v2`。MySQL 和 WeKnora 容器健康，`/api/health` 返回 `{"status":"ok","scope":"identity"}`，此健康检查只证明入口和数据库基础连接，不证明知识检索、模型调用、完整业务链或报告质量。
- 上次按照用户明确要求，仅重建并重启了本地 backend/frontend（`--no-deps`），未执行 schema 迁移或知识初始化。服务启动例程具有中断材料/任务状态恢复行为；不要因为“启动正常”就假定数据库绝无启动恢复写入。其他 `bstrialv2`、`s4-*`、`bs-review-197-verify` 等是不同栈，不要误操作。
- 当前任务的读取检查通过运行中 backend 连接 `DATABASE_URL` 并设置 MySQL `SET SESSION TRANSACTION READ ONLY`；只输出事件计数、状态、页码和文本长度，未导出业务合同全文、未改写业务数据。再次查询时也使用只读事务，不输出连接凭据。
- 这是本地候选运行环境，**不是生产部署或生产验收**。原 job 的失败事件是历史事实，修复后不应改写成成功。旧任务是否可恢复，要验证现有代、材料/规则指纹、候选版本及幂等策略；也不能擅自删除或清理它。

## 5. 需要新窗口优先完成的调查

### 5.1 检查 12 页的真实性质，而不是直接放宽门禁

1. 只读核实 task 4 / material 4 仍为上述状态；读取 `parse_incomplete` 的 `page_states`、`page_state_history`、OCR 重试、质量标记来源。追踪 `parsing.py` 生成 `ocr_suspect_pages` 的具体条件、保存和重试流程。
2. 根据用户已提供的材料来源和项目存储，**对照原 PDF 页面与 OCR 结果**，至少涵盖短文本/图片占位页（35、43）、长 OCR 页（37、39、61、64）、其他可疑页（如 20、21、26、28、31、63）。核对是否存在旋转、图像占位、空白/印章页、表格、乱码或内容丢失；区分实际质量问题和规则误报，记录页码及可复核证据，不把合同全文写进交接或日志。
3. 将 12 页与实际分包合同范围、附件/证照页面及 A1 规则来源做归属分析。整份上传文件包含报审资料，不应预先假设每一页都是判断每个负面清单规则的必需范围；也不能因为页面像附件就自动忽略实际工程事实。
4. 检查现有 API/UI 能否对质量可疑页进行定位、人工核对、重读、留痕和触发新核查代。`reviewed_readable_pages()` 可以消费 `reading_quality_review` 事件式退化记录，但当前只读搜索未找到生产代码写入 `readable` 确认的入口；应继续核实，不能声称闭环已实现。`ReviewWorkbenchLayout.jsx` 的复核界面有文件名、页码列表和“结合原件核对”提示，但核查中断时是否可进入该界面、是否可逐页定位及留痕仍待实测。
5. `router.py` 约第 1535 行有受 `BS_PAGE_TEXT_REFRESH_ENABLED=1` 门控的 `page-text-refresh` API；`parsing.py` 的定向补读调用云 OCR，候选仍需人工核验，应用后会使旧质量确认失效并标为 `review_required`。当前开关状态、前端入口及能否在该 task 状态使用尚未验证；不要启动开关、调用外部 OCR 或写入该任务来证明假设。

### 5.2 明确处理方案和业务决策

先比较以下可能路径，基于真实页证据、既有 OpenSpec 和用户已确认的“范围限制不是合规结论”原则确定：

| 路径 | 适用条件 | 必须核查的风险 |
| --- | --- | --- |
| 修复 OCR 质量误判/有界补读 | 原页与 OCR 对照证明特定判据误判，或现有补读漏掉合适的页 | 不能按文本长度直接判可读；应保留原文、来源、页级状态和失败证据，避免对证照、旋转、表格引入假阳性。 |
| 规则相关范围分级与继续其他分组 | 能可靠证明受影响的材料、规则和范围边界，并能向用户清楚披露未评估 | 不能把未覆盖说成未命中/通过；不能扩大模型输入或凭关键字漏判作为合规结论；真实技术失败与必需材料缺口必须保持原有门禁。 |
| 增加可追溯人工核对/重试闭环 | 存在需要人查看的可疑页、用户有可操作入口 | 不得伪造 finding 或强制将所有规则各生成重复问题；明确审计、页码、使用者确认、补读失败和新代失效策略。 |

前一次讨论中，“缺总包材料”被用户明确确认为影响相关规则的范围提示，不构成 finding、不单独阻断报告，但那是**可选总包来源**的业务口径；不能不加论证地推广为“所有已上传分包材料缺页都不阻断”。用户同时要求相关规则不能被标为完整通过。实际存在多个影响业务结论、范围披露或报告门禁的方案时，应给用户 2–3 个简短选项、推荐理由及具体例子，按其项目指令先确认再改架构或业务口径。普通代码细节无需逐项提问。

### 5.3 先做能复现真实故障的回放

从 `check.negative_candidates_finished` 和 material 4 的结构化状态制作**离线、只读、脱敏**的重现：保留 20 条候选及 12 页质量状态，断言真实 `summarize_coverage -> _run_background` 的门禁行为。构造有依据的对照：确有与规则范围相关不可读页、无关附件质量可疑、信号存在但阈值缺失、质量误报修复、未知原因、模型失败、补读失败。不要新建会写入用户数据库的测试；使用临时 SQLite/内存 mock 或仓库既有测试环境。这里的“隔离测试”只用于自动化测试，不替代用户当前本地服务或改变其数据库。

## 6. 推荐开发与验收阶段

1. **事实和口径阶段**：完成上述 12 页及调用链核查；输出证据表、可疑原因、受影响规则与业务选择。更新现有 OpenSpec 或新增适当 change，先记录决定及反例；确认是否需要用户选择架构/报告门禁口径。
2. **实现最小闭环**：先编写真实故障的失败回归，再按经确认的方案修复页质量、范围分类或人工核实链路；统一事件/覆盖汇总、后台调度、重试与完成、API、前端和报告。不得靠伪造 `source_reading_complete=true`、把 `not_run` 算 `miss`、隐藏 warning 或全面删除门禁来实现“成功”。
3. **恢复及防回归**：验证前端能告诉用户具体材料、页码、原因和可执行动作；重试/补件需维持当前核查代与授权、指纹、幂等性；不能静默复用过期候选与历史人工回执。确保 A2/内部记录等独立分组是否可继续，按最终业务契约验证。
4. **全面验收**：执行定向后端/前端测试、OpenSpec strict validate 和 diff 检查；再在用户授权的数据与外部模型使用范围内，从真实材料上传/解析/核查到人工复核和报告生成走通。区分“技术流程成功”“内容质量/业务规则正确”和“专家验收”，如模型/网络/真实样本限制导致无法完成，逐项明确。不要把 HTTP health 或合成测试当成真实业务报告验收。
5. **本地更新和交接**：仅在修复和对应验证完成后更新现有 `engineering-subcontract-verification-agent` 的 backend/frontend；检查依赖正在使用的本地数据库和业务任务，避免误停其他栈；实际任务重试和发送材料到外部 OCR/模型按会话已有授权的具体范围判断。用户之前只批准一次特定本地验收发送，不能自动扩大到新的材料或无限次调用。

## 7. 可执行的只读排查入口

在 PowerShell 中，工作目录为项目根目录：

```powershell
rtk git status --short
rtk git diff --stat
rtk docker compose -p engineering-subcontract-verification-agent -f compose.yaml ps
rtk proxy curl.exe -fsS --max-time 8 http://127.0.0.1:8089/api/health
rtk docker exec engineering-subcontract-verification-agent-backend-1 python -c "from app.plus.blockchain_supervision import negative_candidate_plan; print(negative_candidate_plan.SCHEMA_VERSION); print(negative_candidate_plan.VALIDATION_VERSION)"
rtk proxy rg -n "source_reading_complete|rule_signal_not_found|blocking_not_run" backend/app/plus/blockchain_supervision
rtk proxy rg -n "ocr_suspect_pages|page_state_history|reading_quality_review|material_reading_gap|reading_coverage" backend/app/plus/blockchain_supervision
rtk openspec validate bs-negative-candidate-evidence-gate --type change --strict --no-interactive
```

代码入口：

- `backend/app/plus/blockchain_supervision/negative_candidate_plan.py`：候选来源读取完整性、范围片段、信号、版本。
- `backend/app/plus/blockchain_supervision/parsing.py`：页级质量判断、`material_reading_gap`、`reading_coverage`、OCR 重试/旋转补读和确认记录读取。
- `backend/app/plus/blockchain_supervision/ai_advisory_snapshots.py`：候选覆盖分类和可人工复核、失败原因。
- `backend/app/plus/blockchain_supervision/background_ai_review.py`：后台按组调度、重试、收口和失败事件。
- `backend/app/plus/blockchain_supervision/service.py`：`task_progress`、补件/报告就绪、`complete_review`。
- `backend/app/plus/blockchain_supervision/router.py` 与 `frontend/src/plus-ui/blockchain-supervision/lib/autoAiReview.js`、`components/CheckRunView.jsx`、`components/ReviewWorkbenchLayout.jsx`：状态投影与可操作信息。
- `backend/app/plus/blockchain_supervision/reporting.py`：报告读取覆盖、来源限制、现有报告语义；Word/PDF 等最终导出需继续追具体渲染链。

后端采用仓库现有 `backend/.venv`，不要用 base 环境。回归命令可参考：

```powershell
cd backend
rtk test .\.venv\Scripts\python.exe -m pytest tests\test_ai_negative_candidate_relevance.py tests\test_bs_scope_unlocated.py tests\test_bs_background_ai_review.py tests\test_bs_report_gate.py tests\test_bs_ai_terminal_retry.py -q
cd ..\frontend
rtk npm run test -- --run tests/autoAiReview.test.js
```

这两条是上次定向回归，不代替为本次真实质量缺口新增的测试和用户真实流程验收。请遵循用户要求：正式回归测试保留，临时调试文件在验证后删除；未经需要不运行额外 `npm build`，也不要为这次交接清除任何已有改动。

## 8. 用户既定业务口径与明确边界

- 总包合同不是强制上传。只依赖可选总包的规则在缺失时标为本轮未覆盖并中性披露，不产生 finding 或强制 `undecidable`；其他规则继续执行。报告只能对已覆盖范围形成意见，不称完整通过。单份总包终态不可用、未关联或来源冲突只影响依赖来源的规则；解析中遵守报告就绪门禁；多个独立总包关系不明仍是准入错误。
- 当前处理的是**分包材料自身 OCR 质量与 AI 候选执行覆盖**，不能混同上述可选总包规则，也不能因通用“避免阻断”的目标误放行真实的读取缺口。
- 用户目前不要求专家盲审。此前允许将特定材料发送一次给指定的外部模型用于本地验收；继续真实材料模型或 OCR 调用时需核对本轮授权是否涵盖新的请求，不能把旧的一次性授权泛化。
- 用户在先前不同上下文曾允许代码提交/推送，但本轮明确要求先交接，新窗口应先核实当前工作区、后续用户指令和变更范围；不可为了交接擅自提交、推送、清理或部署生产。

## 9. 新窗口的第一条指令建议

“请先阅读本交接书、关联 OpenSpec 和当前代码，核对 task 4 / job `5c54d29c92ff496187bb148c077f7ca7` 的只读状态；逐页核查 12 个 OCR 质量可疑页与原 PDF，分析真实质量问题、受影响规则以及人工核查/继续其他分组的闭环。给出涉及业务门禁的选项和推荐后按已确认口径实施，先离线回放并完成回归，最后在授权范围内用真实材料验收完整流程。保留未提交改动、历史事件和本地数据库，不要自动重试旧 job 或调用外部模型。”
