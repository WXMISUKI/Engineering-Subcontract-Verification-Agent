# V1.3 报告与负面清单候选修复交接

记录日期：2026-11-17。适用仓库：`C:\project\nanjin\Engineering-Subcontract-Verification-Agent`。

本文件是后续实现 AI 的接手入口。当前本地代码修复和隔离文档导出验收已经取得证据；新版本尚未更新运行服务，也未进行真实 Provider、真实业务重跑或用户最终验收。不要把本地测试通过解释为线上或业务验收完成。

## 1. 接手先读与执行边界

依次阅读本文件、第 5 节规格和 `implementation-validation.md` 的“最终本地隔离验收”节，再检查当前 Git 差异与 CodeGraph。本文中相对路径均相对于上述仓库根目录；PowerShell 命令默认从仓库根目录运行。

当前授权范围：整理规格、修复本地实现、隔离测试、用合成数据实际导出 Word/PDF。不自动执行真实业务重跑、付费模型/OCR、服务重启、部署、客户数据迁移、人工回执写入或历史快照覆盖。本交接不是部署批准。用户早期曾授权过某些重启和任务操作，不代表本轮新实现可直接重复这些操作。

用户协作要求：中文沟通；修改前先读规格和已有证据；问题先复现再修根因；实质影响架构、数据、安全、费用、部署或业务口径时，用可用的 grilling/Grill-Me 方式提出 2–3 个选项并推荐一个。已确认事项不重复询问。普通实现细节可直接处理。

## 2. 当前持久目标与最小闭环

用户在交接期间已将持久目标暂停（paused）。停止继续实现；接手 AI 应在用户明确恢复任务后继续下述目标：

> 修复 V1.3 分包审查报告状态口径与附件二事实摘要，并改进负面清单候选覆盖：遵守已确认的三种初审意见、未定性显示“—”、维度优先级、同事实同处置才归并、总体意见省略空状态行；修复候选召回/读取范围导致的误报未覆盖，不把未评估规则写成规则缺失、符合或违规。以隔离测试和实际文档导出验收；未经确认不重跑真实任务、不调用付费模型/OCR、不重启或部署。

核心用户链路：已有核查结果及人工复核 → 生成/重新生成正式报告快照 → V1.3 预览 → Word/PDF 导出。负面清单新召回逻辑属于核查候选阶段，不能通过导出或普通读请求隐式重新执行。

最小完成门槛：同一报告快照的维度状态、总体意见和附件二事实一致；模板字段/格式恢复；有页级证据的局部结果不因无关 OCR 缺口被全局降级；未能判断仍披露限制；候选只作人工线索，读取/导出不产生新费用。

本地实现完成不等于用户任务完成。剩余真实验收、运行版本确认及用户业务认可仍需单独记录。不要为了结束 goal 去绕过授权或把真实验收复写为已完成。

## 3. 已确认业务规则，不再重新决策

| 项目 | 已确认规则 |
|---|---|
| 初审意见 | 只允许“符合、不符合、缺项”。“缺陷”不是最终选项；用户后来明确选择“缺项”。 |
| 缺项含义 | 仅表示确认缺少适用报审材料。不可读、未执行、未定位、证据不足不等于缺项。 |
| 无法定性 | 保留维度行，初审意见显示“—”；原因写入主要依据/事实/范围说明。 |
| 维度优先级 | 已确认不符合 > 已确认缺项 > 有证据的符合；否则“—”。存在某个 miss 不证明整个维度符合。 |
| 总体意见 | 与第三部分使用同一维度结论，只列对应维度名称；无维度的状态行省略；去掉“待核实项”。 |
| 附件二事实 | 原 finding 具体事实为主，复核说明/材料页码按需补充，不用“涉及 N 项但未形成结论”代替事实。 |
| 附件二归并 | 同主体、同事实、同人工处置和补正要求才合并；矛盾事实、不同处置不能合并。 |
| 问题类别列 | 只显示模板式主题，不自动追加“（缺项）”“（不符合项）”等内部类别。 |
| 责任单位 | 简单使用“分包人”或“承包人”，不自动拼入长组织名称。 |
| 第四部分 | 只简要展示有确定证据的问题，不把普通提示和未确认事项定性为明确违规。 |
| 正文基本情况 | 仅分包单位、分包事项；工程/标段/金额等放附件一审查概况。 |
| 开篇日期与样式 | 使用当前报送日期并以【】包裹；关键工程/分包名称带模板下划线；日期不以核查基准日代填。 |
| 核查事项数量 | 纳入机器结论统计的逐项记录数；不是五步阶段数、八维度数、人工复核行数或 completeness applicable_total。开放条款/普通提示不计。 |
| 依据目录 | 保留参考模板固定依据，追加本任务实际引用文件并去重。本轮不改向量库。目录中出现名称不证明该文件已入库或检索成功。 |
| 候选策略 | 字面触发词确定性召回；可读范围但无字面触发词的规则进入有双侧证据约束的语义候选。 |
| 候选结论 | 只供人工核实，不自动成为机器符合/违规。空 proposals 不等于规则未命中或符合。 |
| OCR 归属 | 不能因合同某几页不可读，就断言全部规则未覆盖。不能建立页/规则归属时披露中性未归属缺口。 |
| 历史数据 | 保留原件、OCR、事件、人工回执及冻结报告；不靠换模板改写原业务结论。 |

## 4. 已实现内容与本轮复现的根因

### 4.1 报告投影、Word 与附件二

`backend/app/plus/blockchain_supervision/report_template_v13.py`：

- 当前 `MAPPING_VERSION = 2026-11-17.4`。
- 三状态和“—”投影、动态八维度摘要、总体意见同源并省略空分类。
- 维度聚合前单独分类确认缺项，防止旧“三类必需证照=不符合”策略吞掉“缺项”。旧机器统计仍保留，不直接重写机器 status。
- 通过现有 `report_document._summary_theme` 将企业、人员、合同、审批资料映射至资格信用、人员履约、合同条款、分包合规性维度。此前仅映射三种企业证照，其他确认缺项可能漏成“—”。
- 受影响页读取不足、scope_limited 或执行组未完成时，不把 material_missing 标志强行显示为确定缺项。

`report_template_render.py`：

- Word 打开项目内参考 DOCX，填充原 OOXML 段落/run/表格骨架，不再清空后用通用字号重建。
- 开篇【日期】和工程/事项下划线沿用参考 run；维度统计沿用加粗 run。
- 总体意见的空状态行现在删除段落。此前仅清空文字，保留段落行高，实际样稿有空白占位。
- 删除旧“待核实项”槽位；附件二主题不追加内部分类。
- 第四部分标题设置 keepNext，解决实际 LibreOffice 渲染中标题孤悬页底、首条问题落下一页。

`report_content_summary.py`：

- 事实以原消息为主，保留人工复核说明、按文件合并页码。
- 分组键含主体、类别、主题、事实、人工 code/legacy decision、备注和补正要求。此前相同事实但不同补正要求或旧 review.decision 可能错误归并。

### 4.2 负面清单候选与读取缺口

`negative_candidate_plan.py`：

- `SCHEMA_VERSION = bs-negative-candidate-v4`。
- 最终 `VALIDATION_VERSION = bs-negative-candidate-evidence-v4-semantic-batch`。前一轮摘要中的 v3 已被替代。
- 输入携带 `recall_mode=deterministic/semantic`；限定当前任务、主体、合同范围片段和冻结规则参考。
- `scope_attribution`：可读工程范围本身不证明其余不可读页属于哪条规则；存在未读来源时保留 unresolved，不为每条规则制造归属。
- 历史 validation version 保持历史状态，必须显式重试后才能使用新合同，读请求不隐式重跑。

`ai_review_batch.py` 与 `ai_evidence.py`：

- 批次规划/wire/模型提示传递 recall_mode。
- **本轮正式回归先复现失败后修复**：wire 已传 semantic，但返回 `validate_ai_proposal` 时漏传；有效语义候选仍被字面触发词门槛拒绝，结果 partial。现返回与写入前校验均使用相同模式。
- 保留材料 ID、主体、页码、精确材料引文和冻结规则引文校验；不同 recall_mode 的同一候选批次不应混用。
- 语义校验验证引文归属和内容存在，不能确定证明工程事实与规则在业务语义上相关。该剩余能力由模型与人工复核承担，不能声称语义正确率已验收。

`reading_gap_scope.py`：

- 未明确 attributed 的缺口不默认挂全规则 ID；已归属与未归属缺口混合时均披露。
- `collect` 目前只收集明确 `not_run + rule_signal_not_found + source_reading_complete=false`，与覆盖统计 reading_gap_not_run 对齐。
- 不扩大到 reason=None/unknown/reference_missing；这些状态不能仅因整体 OCR 差就转成读取缺口。
- 新 semantic 候选通常进入批次，已运行/空建议/partial 由候选执行覆盖投影处理，不能靠扩展 collect 掩盖真正未执行。

## 5. 权威规格与代码入口

| 用途 | 路径 |
|---|---|
| 主要报告规格 | `openspec/changes/bs-report-v13-content-refinement-20261116/` |
| 最新验收账本 | 上述目录下 `implementation-validation.md` 的“最终本地隔离验收”节 |
| 模板规格 | `openspec/changes/bs-report-template-v13-20261116/` |
| 候选规格/设计/任务 | `openspec/changes/bs-negative-candidate-evidence-gate/` |
| OCR 后续方向 | `openspec/changes/bs-ocr-quality-advisory-20261116/`；不等于本轮已实现完整 OCR 治理 |
| 报告生成/冻结/门禁 | `reporting.py`、`report_document.py`、`report_policy.py`、`router.py`、`service.py`、`export.py` |
| 候选运行/状态/旧事件 | `negative_candidate_plan.py`、`ai_review_batch.py`、`ai_evidence.py`、`ai_advisory_snapshots.py`、`background_ai_review.py` |
| 页级读取 | `reading_gap_scope.py`、`parsing.py` |
| 前端入口 | `frontend/src/plus-ui/blockchain-supervision/BlockchainSupervisionWorkbench.jsx` |
| 报告组件 | 上述目录的 `components/ReportView.jsx`、`components/ReportTemplateV13.jsx` |
| 邻近阶段计划 | `docs/plus/blockchain-supervision/continuation-plan-20261117.md`、`phase-a-evidence-matrix-20261117.md` |

上表未写目录的 Python 文件均位于 `backend/app/plus/blockchain_supervision/`。

**规格阅读注意：** 部分文档保留早期“四状态”“待核实维度”、历史版本号和更早的验收记录。以最新用户确认、规格三状态场景和最终验收节为当前口径。报告内容规格开篇仍有“事项数不得从机器结论数推算”的早期措辞，后文又明确按机器逐项记录统计；应以用户已确认的逐项机器记录数为准，后续可统一措辞，不能据此改回 14 项。

邻近阶段计划还记录了“OCR 不单独阻断”“旧版默认”等更早的阶段决策；候选规格记录了历史 review_required/范围受限报告路径。当前任务没有重新决策这些门禁。接手时先追踪当前调用链与已批准规格，发现实质矛盾需与用户确认，不得擅自放宽或收紧门禁。

## 6. 已验证证据及边界

| 验收层次 | 当前证据 | 可证明范围 |
|---|---|---|
| 后端联合隔离测试 | 157 passed | 候选、读取范围、后台、报告门禁、重试、partial、模板、摘要及 HTTP 导出相关行为 |
| 最终候选版本号变更后 | 88 passed | 候选/后台/门禁/重试定向检查 |
| OpenSpec | 三个 change strict 校验通过 | 规格结构有效，不证明运行环境已加载 |
| diff 检查 | `git diff --check` 通过 | 空白/补丁格式，不是业务正确性证明 |
| Word 实物 | 项目导出的合成 DOCX + LibreOffice 4 页 PDF | 当前模板填充和实际分页可读；不是用户真实材料报告 |
| 项目 PDF | 合成数据导出 4 页，逐页图像检查 | 项目 PDF 内容和布局可读；与 Word 不逐像素一致 |
| OOXML schema | 源模板与生成件各 72 项同类错误，无新增 | 不得宣称 schema 校验通过 |
| 前端 | 历史摘要报告过 158 项等测试 | 本轮没有重跑前端，不能当作本轮新鲜证据 |
| 真实 Provider/OCR | 本轮未调用 | 模型业务质量、真实成本/时延、真实 OCR 改善均未验收 |
| 运行服务/部署 | 本轮未重启、未部署、未重新识别端口进程 | 不能声称网页已加载新逻辑，不能给出已生效版本 |
| 真实报告/专家认可 | 本轮未重跑，用户未最终验收 | 原核查准确性及正式审批结论仍未验证 |

联合测试文件（均为有维护价值的正式测试，保留）：

```powershell
Set-Location C:\project\nanjin\Engineering-Subcontract-Verification-Agent\backend
rtk proxy .venv/Scripts/python.exe -m pytest -q tests/test_ai_negative_candidate_relevance.py tests/test_bs_scope_unlocated.py tests/test_bs_background_ai_review.py tests/test_bs_report_gate.py tests/test_bs_ai_terminal_retry.py tests/test_ai_partial_scope_baseline.py tests/test_report_template_v13.py tests/test_report_content_summary.py tests/test_report_template_v13_http.py
```

157 项通过后仅再变更候选 validation version；其后 88 项命令为：

```powershell
rtk proxy .venv/Scripts/python.exe -m pytest -q tests/test_ai_negative_candidate_relevance.py tests/test_bs_background_ai_review.py tests/test_bs_report_gate.py tests/test_bs_ai_terminal_retry.py
```

其他检查：

```powershell
Set-Location C:\project\nanjin\Engineering-Subcontract-Verification-Agent
rtk proxy openspec validate bs-report-v13-content-refinement-20261116 --strict
rtk proxy openspec validate bs-report-template-v13-20261116 --strict
rtk proxy openspec validate bs-negative-candidate-evidence-gate --strict
rtk git diff --check
```

依赖告警：Starlette 空配置文件、anyio/Starlette 和 jose utcnow 弃用告警；测试没有因这些告警失败。未执行 npm build，用户要求按风险选择简洁验收。

## 7. 可供接手复查的实际产物

目录：`artifacts/report-v13-20261117-acceptance/`。

- `README.md`：合成数据声明与文件用途。
- `isolated-v13.docx`：OOXML 模板导出；`isolated-v13.pdf`：同文件经本机 LibreOffice 转换。
- `application-v13.pdf`：项目自身 PDF 导出。
- `*-pages.png`：逐页图像验收底稿；`*.txt`：PDF 抽取文本。
- `projection.json`：合成冻结投影。
- `acceptance-checks.json`：括号、下划线、状态/空类省略、事实/页码/复核说明、问题标题与首条同行页、SHA256。
- `schema-baseline-comparison.json`、`template-schema-validation.json`、`generated-schema-validation.json`：schema 对照及完整诊断。

两份 PDF 均实际检查全部 4 页；Word 标题/样式来自原模板，项目 PDF 仍是独立块渲染器，分页与字体位置不完全一致。若用户要求 PDF 也与 Word 完全一致，需要讨论是否统一为 Word→PDF 渲染链路，不能默认为已实现。

schema 现状：参考模板和生成件各 36 项 `noWrap val=0`、36 项 `uiPriority` 顺序错误。Word/LibreOffice 可读不等于 schema 合法。本轮未修改模板的这些既有问题。一次新增 keepNext 的顺序问题已通过 python-docx 原生属性插入修正，最终错误数回到源模板基线。

本机 LibreOffice：`C:\Program Files\LibreOffice\program\soffice.com`，可用但未加入 PATH。渲染时使用独立 UserInstallation，避免干扰用户现有会话；本轮临时 profile 已删除。不要再声称本机没有 LibreOffice。

用户参考模板：

`C:\Users\SZZG\Documents\xwechat_files\wxid_pc6sc451nt9022_dea0\msg\file\2026-11\20261113工程分包审查智能体输出模板V1.3.docx`

项目使用：`backend/app/plus/blockchain_supervision/templates/review_opinion_v13.docx`。

交接时已核对两者 SHA256 完全相同：`adca722ec884793b1d21042bbb436378c9fadf3ae1a113e2495f8852e1ffa374`。

最近用户反馈的 (7) 文件仍在：`C:\Users\SZZG\Downloads\分包合规核查 2026-11-16-V1.3-核查 (7).docx`。该文件是旧导出问题证据，不是本轮新代码生成件。其他 (1)–(6) 文件不在本轮重新逐一验收范围；需要时再确认是否仍存在。不要改写用户下载件。

## 8. Git、环境与并发改动

- 分支：`report-template-v13-20261116`。
- 交接时 HEAD：`b65688b feat(bs): unify formal output and task closure readiness gates`。
- 本轮修复仍在工作区，未提交、未推送；不能通过只 checkout HEAD 获得当前实现。交接应保留整个现有工作区，包括未跟踪的报告模块、模板二进制、测试和 OpenSpec。
- 存在大量更早/其他工作区改动：A1/A2、finding_semantics、schema、前端工作台/门禁、独立学习治理等。交接期间还出现了其他新增设计文档；不要认为全是本任务所写，更不要全量 add。
- 禁止 `git reset --hard`、`git clean`、盲目 stash 或全量暂存；不为追求干净树删除 artifacts/backups 或 `[options]` 等归属不明文件。
- 后端使用既有 `backend/.venv`，不需要重新创建 conda/venv。PowerShell 将中文脚本通过管道传给 Python 时，先设置 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`；跨 shell 注意 `$` 与反引号转义，避免中文变成乱码后生成错误验收件。
- RTK 可用；当前提示未安装 hook 不影响 `rtk proxy`。不需要为本任务全局初始化 hook。
- 前端声明 `pnpm@11.5.3`，测试为 `vitest run`。仅在改动前端或需要运行边界证据时针对性执行。
- CodeGraph 已初始化；交接时 MCP status 为 402 files / 6875 nodes / 16912 edges。结构问题优先 CodeGraph；修改后允许 watcher 同步。状态统计不证明测试通过或部署完成。
- 未将密钥写入本交接，不复制 .env 内容。运行服务路径/身份/端口和数据库必须现场只读确认；本轮没有作实时部署盘点。

## 9. 接手后的执行顺序

### 第一步：只读重建状态

1. 核对分支、HEAD、staged/unstaged/untracked，确认新修改和并发编辑；不清理旧改动。
2. 对照规格与本交接，检查三个版本常量和现有关键回归。通过 CodeGraph 追踪候选计划→批次→验证→复核记录→coverage→门禁→报告冻结→导出。
3. 查阅 artifact/checks 与实际 Word/PDF，明确当前产物是合成数据。通常不必无变化重跑全部测试；代码变更或证据不匹配时再按受影响范围执行。

### 第二步：有限完成度审查

逐条检查：三状态/“—”、缺项适用性、优先级、完整范围才符合、附件二同事实同处置、空状态段落删除、日期/下划线/加粗、统计单位、固定依据、责任单位、候选模式全链路、双侧引用、unknown 不变 OCR、旧缓存失效、读导出不调用模型。

以下是需要审查的边界，不是已确认缺陷：

- 附件二 `summarize_items` 的内部类别仍可因 `material_missing=True` 进入缺项，维度投影则有完成/读取限制。核对上游该标志是否始终可信，避免同一记录在维度“—”而事实/补正要求断言“确认缺件”。若可复现，按“确认缺适用材料才缺项”修根因，不靠删除事实遮盖。
- `document.statistics.counts` 仍有旧内部“待核实”统计键；初审意见与总体意见已移除该结论。检查是否被其他 UI 当成新初审意见展示，不要为了内部字段名就改机器审计状态。
- OCR scope_attribution 当前采取保守归属；不声称已实现对所有材料的可靠页→工程→规则分类。特别检查新的 semantic 空建议/partial 如何进入报告范围说明，而不是给所有规则附上同一缺口。
- 语义候选严格引文检查不证明语义相关性；真实样本需比较误召回、漏召回及模型预算，不以 mock 返回正确代替真实质量。
- 固定法规目录与真实检索依据是不同来源。本轮没有向量入库/法规有效性调查，不得把目录中的文件名都描述为从向量库取到。
- 前端生成/重新生成/导出按钮问题有早前实现和测试记录，本轮没有真实浏览器重新验收，不能对当前运行网页做已修复承诺。

若无可复现问题，不继续无限优化。对影响用户输出的缺口补正式回归，再小范围修正并更新验收账本。

### 第三步：准备真实验收，先明确授权

只读确认当前服务/端口/代码加载方式/数据库、目标任务及其最新冻结版本、候选 validation version 和阅读覆盖。不自动启动新调用。

向用户说明需授权的具体动作及理由：

- 服务更新/重启：让新代码实际生效；先核对现有进程和资源，并提供回退方案。
- 已有任务重生成报告：可能写入新报告快照/版本；明确任务 ID，只重生成报告是否足够，不能隐式重算核查。
- 负面清单新策略真实验收：需要显式新候选运行/新核查代；定义材料、规则范围、Provider、费用/请求上限和停止条件。报告重新生成不能证明新候选策略已运行。
- 人工复核/收口：由用户决定真实业务事实。历史“功能测试采纳”不作为专家认可，不自动批量 accepted/ignored。

授权后才执行依赖动作；备份与回读核对业务状态，单次有界运行，出现明确失败保留原始证据，不通过反复付费重试碰运气。用户没有批准时，可完成本地准备和交付说明，但不得标注真实验收完成。

### 第四步：正式交付

1. 逐项记录源码、隔离测试、真实客户端、Provider、部署、业务专家验收各自证据，未做的明确未做。
2. 更新 OpenSpec：候选 tasks 9.6 仍保留真实链路待验收；报告 tasks 用户验收项仍未勾选。不要把历史已勾选项当作最新版本所有门禁已验收。
3. 相关范围验收后再讨论/执行有审查范围的集中提交；不带入其他阶段改动，不删除正式回归。
4. 只有当前完整目标获得逐项证据且无必需项待办时，才结束持久目标；交接本身不等于完成实现目标。

## 10. 不在本轮继续扩展的事项

向量库法规入库、知识库内容治理、完整 OCR 重试/页级确认产品、新的用户审批流程、独立版全阶段迁移、生产发布、客户数据库迁移、PDF/Word 像素级统一均需按各自规格和授权推进。它们不能被包装成本次模板修复顺带完成。

## 11. 可直接发给接手 AI 的提示

> 用户恢复任务后，请在当前原工作区继续，先阅读 `docs/plus/blockchain-supervision/handoff-report-v13-negative-candidates-20261117.md` 及其引用的最新 OpenSpec/验收记录，再只读核对 Git 与 CodeGraph。当前主目标是 V1.3 报告状态、附件二事实和负面清单混合候选修复收尾，已有 157 项本地联合回归、最终版本 88 项定向回归和真实合成 DOCX/PDF 导出证据。请先进行有限完成度审查，修复可复现缺口，再准备真实运行验收；保留全部已有改动。未经用户明确授权，不重跑真实任务、不调用付费模型/OCR、不重启部署、不改人工回执或历史快照。先告诉用户你核实的现状及下一步，不从头重复设计，不把历史运行记录当作当前授权。
