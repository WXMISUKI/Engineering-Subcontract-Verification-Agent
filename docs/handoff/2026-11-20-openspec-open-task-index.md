# OpenSpec 未完成任务索引：2026-11-20

本索引从当前 main 工作区的 `openspec/changes/*/tasks.md` 自动提取。它记录文档勾选，不认证实现、法规适用或运行验收。重复规格、历史阶段与已实现未勾选可能并存，不能将未勾选数量直接解释成缺失功能数量。

当前续接顺序和最新真实证据以 [合同倒查续接交接书](2026-11-20-contract-retrospective-continuation-handoff.md) 为准。用户尚未补充的业务规格保持未完成，不据本索引自动展开全部业务开发。

扫描 46 份任务文件，其中 18 份仍含未勾选项，共 142 条。以下数量仅统计复选框。

## 1. 未完成规格总览

| 变更 | 已勾选 | 未勾选 | 任务文件 |
| --- | ---: | ---: | --- |
| bs-ai-timeout-diagnostics | 35 | 14 | [tasks.md](../../openspec/changes/bs-ai-timeout-diagnostics/tasks.md) |
| bs-contract-retrospective-20261119 | 36 | 9 | [tasks.md](../../openspec/changes/bs-contract-retrospective-20261119/tasks.md) |
| bs-moma-adaptation-baseline | 2 | 3 | [tasks.md](../../openspec/changes/bs-moma-adaptation-baseline/tasks.md) |
| bs-moma-ai-scope-selection | 52 | 5 | [tasks.md](../../openspec/changes/bs-moma-ai-scope-selection/tasks.md) |
| bs-negative-candidate-evidence-gate | 34 | 5 | [tasks.md](../../openspec/changes/bs-negative-candidate-evidence-gate/tasks.md) |
| bs-ocr-quality-advisory-20261116 | 6 | 1 | [tasks.md](../../openspec/changes/bs-ocr-quality-advisory-20261116/tasks.md) |
| bs-report-adaptation | 7 | 9 | [tasks.md](../../openspec/changes/bs-report-adaptation/tasks.md) |
| bs-report-template-v13-20261116 | 16 | 1 | [tasks.md](../../openspec/changes/bs-report-template-v13-20261116/tasks.md) |
| bs-report-v13-content-refinement-20261116 | 23 | 1 | [tasks.md](../../openspec/changes/bs-report-v13-content-refinement-20261116/tasks.md) |
| bs-retrospective-shared-flow-20261119 | 20 | 2 | [tasks.md](../../openspec/changes/bs-retrospective-shared-flow-20261119/tasks.md) |
| bs-retrospective-temporal-and-semantic-guardrails-20261120 | 5 | 11 | [tasks.md](../../openspec/changes/bs-retrospective-temporal-and-semantic-guardrails-20261120/tasks.md) |
| bs-review-dimension-basis-p0-20261118 | 61 | 28 | [tasks.md](../../openspec/changes/bs-review-dimension-basis-p0-20261118/tasks.md) |
| bs-safety-cost-contract-20261119 | 0 | 6 | [tasks.md](../../openspec/changes/bs-safety-cost-contract-20261119/tasks.md) |
| bs-safety-permit-scope-20261119 | 5 | 1 | [tasks.md](../../openspec/changes/bs-safety-permit-scope-20261119/tasks.md) |
| bs-supplement-evidence-linkage | 40 | 18 | [tasks.md](../../openspec/changes/bs-supplement-evidence-linkage/tasks.md) |
| bs-task-safe-recovery | 2 | 6 | [tasks.md](../../openspec/changes/bs-task-safe-recovery/tasks.md) |
| standalone-docker-delivery | 5 | 7 | [tasks.md](../../openspec/changes/standalone-docker-delivery/tasks.md) |
| standalone-platform-learning-governance-20261115 | 20 | 15 | [tasks.md](../../openspec/changes/standalone-platform-learning-governance-20261115/tasks.md) |

## 2. 明确需要先对齐的状态差异

- `bs-contract-retrospective-20261119` 的 R3-R5 记载反例和模型对照已做，`bs-retrospective-temporal-and-semantic-guardrails-20261120` 的相应 P2/P3 仍未勾选。先核源码和验收，不重新盲跑或批量勾选。
- 新建倒查双合同必传已取代早期“总包可选”输入规则，历史单合同任务保留只读兼容。
- `bs-rule-governance-20261120` 复选框已全部勾选，表示有限治理切片完成，不是全部规则逐项映射已完成。
- 线上任务 8 已保存报告，但报告为 partial、0 项问题，不能关闭内容质量和完整覆盖任务。
- Docker 交付已有新换库及恢复证据，但全模式流程、离线交付、许可证、真实客户端及灾备验收需分别核对。
- 资质全量类别和其他后续业务范围等待用户补充，不在这次交接中擅自收窄或补完。

## 3. 各规格原始未勾选条目

以下逐条保留任务文件中的未勾选表述。相邻历史说明、已勾选前置和验收限制请回原文件读取，不能脱离上下文单独执行。

### bs-ai-timeout-diagnostics

来源：[任务文件](../../openspec/changes/bs-ai-timeout-diagnostics/tasks.md)。

- [ ] 1.14 Validate failure injection, checkpoint reuse, real-provider synthetic requests and local acceptance before any deployment.
- [ ] 2.6 Obtain provider-side telemetry for the historical 120-second timeout; local synthetic tests did not reproduce it.
- [ ] 3.1 Compare a failed provider request's status/request ID/Retry-After/input-output tokens/latency with the local baseline.
- [ ] 3.2 Decide whether the cause is payload/output volume, provider queueing or rate limit, network latency, model binding, or shared account usage.
- [ ] 3.3 Implement only the evidence-backed adjustment to chunking, concurrency, timeout, retry/backoff, or connection lifecycle.
- [ ] 3.4 Re-run provider-backed tests and backend regressions; verify no result loss or checkpoint regression.
- [ ] 3.5 Complete local acceptance before proposing any target-environment deployment.
- [ ] 4.5c Repeat the service-layer replay after provider access is restored: a first service replay persisted four checkpoints, while its final child returned HTTP 403; a later fresh replay returned 403 for all five batches and its bounded retry also remained failed. Current logs do not establish the provider-side reason. The service preserved failure and did not checkpoint failed batches.
- [ ] 4.6 Replay representative real material in an isolated environment and obtain local business acceptance; preserve the existing task and shared upload volume.
- [ ] 5.3a Support fragment-qualified “可见……但未见” and “仅见……未见” without exempting any other assertion; verify same-sentence and later unsupported package-wide claims remain rejected, invalidate earlier checkpoint validation, then compare actual real-material suggestions.
- [ ] 5.4 Compare MoMA before/after on the same real batch and representative additional cases; record structural results, rejection reasons, latency and business-review limits. Original prompt: 2/3 fully accepted, 1/3 rejected; initial candidate: 0/3 accepted; revised candidate: 3/3 accepted with one suggestion each (13.6–24.5 seconds). Additional signature and completeness batches completed (29.3 and 81.4 seconds). Expert accuracy, broader samples and formal business acceptance remain outstanding.
- [ ] 5.5 Verify UI/report projection, historical snapshots and focused regressions; retain pending expert acceptance explicitly. Automated report/history tests: 114 passed; frontend review/report tests: 30 passed. A real-task copy in an independent temporary SQLite database ran all five signature batches with MoMA and saved five checkpoints: one finding was partial and two were not run because of evidence gaps. A draft was available, its AI coverage correctly remained incomplete, and Word/PDF exports contained extractable text. An isolated draft was rendered for review; this was not a complete five-stage replay or expert acceptance.
- [ ] 6.3 Reconcile partial-batch AI suggestions against whole-package rule evidence; preserve original audit records and distinct facts, and do not present fragment absence as package absence.
- [ ] 6.4 Verify signature and certificate descriptions, AI scope guard, report output and isolated representative real-material replay; document remaining limits.

### bs-contract-retrospective-20261119

来源：[任务文件](../../openspec/changes/bs-contract-retrospective-20261119/tasks.md)。

- [ ] 4.4b 候选环境登录后的双合同上传/启动/刷新恢复/原页查看/Word-PDF视觉验收，并汇报最终完成范围与后续事项
- [ ] T2 隔离真实分析并核对模型身份、用量记账与失败停止行为
- [ ] T3 真实浏览器上传、启动、刷新、自动报告、原页及Word/PDF下载
- [ ] T4 抽查实际报告内容、导出视觉及兼容回归，记录未覆盖项并汇报
- [ ] C1 对冻结依据逐条建账；父子重复仅在原文包含时归并，明确合同文本外事项
- [ ] C2 独立规范驱动补查，不再用关键词/前12条作为覆盖门槛；保留已核查、不适用、待核查及逐批证据
- [ ] C3 全依据知识检索计划、运行身份及检查点接入，恢复/导出不重新调用模型
- [ ] C4 真实单合同隔离复验、复核新增发现及八列表导出；分开说明依据范围与报告发现数量
- [ ] M4 逐项核对可能适用的官方依据版本、原文、生效时间及项目地域/类型条件；不得以映射表代替来源核验。

### bs-moma-adaptation-baseline

来源：[任务文件](../../openspec/changes/bs-moma-adaptation-baseline/tasks.md)。

- [ ] 将扫描页 OCR 用量按任务、页和实际请求归集，验证缺失 usage、失败和重试的处理及不重复计费。
- [ ] 核对用户已有移动云实测与当前代码版本、文本/OCR 模型及实际环境的对应关系；不要求重复已完成的测试。
- [ ] 按授权范围完成真实材料识别、人工复核、报告、用量对账及环境验收；分别记录结论。

### bs-moma-ai-scope-selection

来源：[任务文件](../../openspec/changes/bs-moma-ai-scope-selection/tasks.md)。

- [ ] 5.2 Compare baseline and candidate page coverage, evidence categories, citation validation, fallback rate, request count, usage, latency, and failures on a stratified sample. A fresh isolated clone compared current baseline and candidate strategy for 23 A1 findings: both contained 1,182 finding-page pairs, 1,686,655 input characters, and the same 22 rule-reference identities; the aggregate page/text context fingerprint matched and no finding narrowed. No model was called. This confirms current fail-closed wiring but yields no candidate recall, citation outcome, token, latency, or failure comparison because the mapping registry remains empty. The earlier `1126 → 1126` comparison predates strategy propagation; the historical `1126 → 765` prototype is not current implementation evidence. Full narrowed-candidate factual equivalence and expert review remain open.
- [ ] 5.3 After the user resumes human review, obtain expert assessment of factual support and omissions; do not infer acceptance from citation validation or cost reduction.
- [ ] 5.5 Obtain a separate user decision before target-environment deployment; deployment is not implied by local acceptance.
- [ ] 6.3 Complete stratified real-material adapter verification and determine whether any rule can satisfy the evidence contract. Synthetic coverage includes no-hit baseline retention, subject separation, cross-page positive/counterevidence, OCR-gap disclosure, scope conflict, source/text mismatch, invalid rule regex, unreadable-versus-absent role reporting, plus vertical fixtures comparing fixed T15-05 hit/miss/undecidable outcomes with shadow relation locators on the same general+labor pages. The shadow audit reports source-role presence separately from readable-page role coverage. Isolated read-only shadow scanning has covered five historical tasks/115 A1 findings/5,225 baseline finding-page pairs. Each task's available material roles were only labor/sub and lacked the required `general` role; four T15-05 contexts yielded five total management-fee occurrences, while one had no rule hits. No real sample yielded labor-whole-scope, labor-payment, or scope-negation relation matches; all retained the original page set. Workspace inventory found no local source-material/SQLite corpus: the preserved pre-cleanup snapshot has 15 DOCX and 21 PDF files, all under `output` report examples, no database file, and no document outside that output tree. Synthetic parity does not validate real cross-role linkage or factual recall. Fixed-rule/kernel regressions, disclosure/checkpoint tests pass; no model request was made. On 2026-11-12, the project's independent MySQL volume was backed up and inventoried under a read-only transaction: all eight existing `bs_` business tables are empty, while `bs_material_fact` and `bs_evidence_relation` are absent. This confirms the independent database is not a usable real-material sample source; 6.3 remains open. See `docs/design/2026-11-12-moma-scope-stage5-isolated-verification.md` for backup integrity and database identity details.
- [ ] 6.4 If a rule meets the evidence contract, conduct the outstanding baseline/candidate comparison in 5.2 and obtain human business acceptance when the user resumes it. Deployment remains a separate decision.

### bs-negative-candidate-evidence-gate

来源：[任务文件](../../openspec/changes/bs-negative-candidate-evidence-gate/tasks.md)。

- [ ] 4.2 Recompute the same business materials under the new candidate contract through an authorized new review generation. The local backend is updated, but the existing AI entrypoint rejects task 2 because it is `done` and only accepts `running/review`. Do not bypass the task-state guard or carry the old `accepted` decisions into a new generation.
- [ ] 6.5 Verify retries, report gate, UI and offline real-scenario replay; run focused regressions and strict validation. Focused tests and real-task continuation passed; full report acceptance remains open.
- [ ] 6.6 Verify the complete real-material workflow within explicit authorization; record remaining limits without modifying historical outcomes.
- [ ] 9.6 Validate terminal group execution, report readiness, scope-limited disclosures, stale cache behavior, partial batches, and no extra model call from reads/exports.
- [ ] 7.4 Verify normal reports, unknown failures, stale generations, multiple materials, and scope-limited reports with focused tests and authorized real-material acceptance. Synthetic gates and Word/PDF passed; real task remains pending 34 human decisions and confirmed-report validation.

### bs-ocr-quality-advisory-20261116

来源：[任务文件](../../openspec/changes/bs-ocr-quality-advisory-20261116/tasks.md)。

- [ ] 2.4 用户验收报告正文、字段和业务表达；Word/PDF 真实文件的内容及版式验收仍待进行。

### bs-report-adaptation

来源：[任务文件](../../openspec/changes/bs-report-adaptation/tasks.md)。

- [ ] 2.1 使用经授权的新任务验证规则冻结、策略切换、结果与证据链；明确 OCR 读取缺口和主体适用性。
- [ ] 2.2 以实际人工备注、忽略及未处理普通事项验证页面、Word、PDF 和证据包一致性。
- [ ] 2.3 以真实历史版本及补件代次验证快照、权限和报告门禁；隔离测试不能代替真实业务验收。
- [ ] 2.4 业务人员确认具体可自动判为“基本符合”的场景及依据；在此之前不新增自动规则。
- [ ] 2.5 如需可视化管理报告策略，另行设计版本发布、权限、审计和验收；现阶段仅配置文件及接口。
- [ ] 3.2 摘要按已查事实、具体不足和针对性核对组织，减少重复标签和无针对性的补件套话；不改变事实、既有判定和证据。
- [ ] 3.3 核查范围、依据和重复限制按要点归属展示；未开展仍显示“—”，不能把未查见推为缺失或符合。
- [ ] 3.4 验证前端、Word、PDF、历史快照、采纳/忽略/备注及未处理结果一致，保留真实材料与专家验收边界。
- [ ] 3.5 使用隔离真实样本生成完整候选报告，请业务人员确认四项及其他五阶段要点。

### bs-report-template-v13-20261116

来源：[任务文件](../../openspec/changes/bs-report-template-v13-20261116/tasks.md)。

- [ ] 4.4 使用经授权真实材料完成两模板字段与报告内容业务验收。

### bs-report-v13-content-refinement-20261116

来源：[任务文件](../../openspec/changes/bs-report-v13-content-refinement-20261116/tasks.md)。

- [ ] 7 用户验收修订报告内容；材料中已存在但旧结论仍列缺项的核查质量另行处理。

### bs-retrospective-shared-flow-20261119

来源：[任务文件](../../openspec/changes/bs-retrospective-shared-flow-20261119/tasks.md)。

- [ ] P4.5 内容质量验收：解决原页与引文失真造成的误报、规范例外条件失真、总包检测条款漏提取；保持173项依据待核状态，不宣称完整覆盖。现有解析专项按用户要求后续处理，已保留原报告/失败证据，未通过修改样本结果冒充修复。
- [ ] P4.5c 新候选公共链路、双侧风险定位与导出内容验收，记录实际覆盖及遗留。

### bs-retrospective-temporal-and-semantic-guardrails-20261120

来源：[任务文件](../../openspec/changes/bs-retrospective-temporal-and-semantic-guardrails-20261120/tasks.md)。

- [ ] 2.1 为水电费明确勾选第一种方式增加反例回归，确保不报未选择冲突
- [ ] 2.2 为施工组织设计上位要求与乙方细化编制增加反例回归，确保允许兼容履行
- [ ] 2.3 为材料检验由分包人承担且费用计入合同价增加反例回归，确保区分责任主体与价格包含
- [ ] 2.4 增加同条件下相反责任主体的真实歧义正向回归
- [ ] 2.5 加强现有自动复核的上下文、选项、主体、条件和费用语义检查；保留现有无人工门禁闭环
- [ ] 2.6 执行倒查定向回归及准入/历史报告兼容验证，记录 P2 验收后再进入 P3
- [ ] 3.1 冻结合同材料哈希、依据版本、日期口径及评估场景，并确认隔离输出目录
- [ ] 3.2 核实 Qwen3.7 Flash 与 Qwen4-Max 实际请求模型身份及结果记录字段
- [ ] 3.3 对同一冻结输入执行对照，保留原始输出、用量记录与失败状态，不对未知请求自动重试
- [ ] 3.4 比较三类误报、正向冲突、证据引用、规则适用时间、报告措辞和Word/PDF一致性
- [ ] 3.5 完成兼容回归及对照验收记录；不更改默认模型、不写共享任务或覆盖既有报告

### bs-review-dimension-basis-p0-20261118

来源：[任务文件](../../openspec/changes/bs-review-dimension-basis-p0-20261118/tasks.md)。

- [ ] 0.2 评审资质全量类别—等级—承包范围映射，核实甲乙级正式实施及过渡依据后再确定是否存在适用映射。（2026-11-19已核159号原版五类及含本数总则；此前2026-07-01全国实施假设无证实，日期置空、禁止自动换算；建办市〔2023〕47号换证须核实际新证。2026-11-20已据官方目录逐页复核：编号1–12为12类施工总承包、13–48为36类专业承包、49为施工劳务；当前程序词表有11个工程内容标签，范围规则只覆盖已核五类；业务覆盖边界待确认，目录基线见docs/research/2026-11-20-qualification-category-inventory.md。其余类别及全地域当前效力仍待办，见bs-qualification-source-review-20261119。）
- [ ] 0.3 评审负面清单 21 叶的 domain / 结构部位 / 适用性谓词，确认桩基、下部结构、上部承重的归类。
- [ ] 0.4 确认 6 个开放问题：DG/TJ 版本、资质改革过渡、特种作业证书类型、交安25号令计算口径、结构部位分类、报告结论措辞。
- [ ] 0.5 评审通过后冻结本变更的规则数据版本号。
- [ ] 4.1 重构 A2-01：身份证号为主键，姓名+单位兜底，逐人输出岗位/合同/社保/期间/结论。（身份证优先及主体隔离已落地，同名异号不合并，无号仅形成待核实候选；逐人岗位/合同/社保/期间已输出。合同独立人员表补无电话与岗位姓名紧连布局，task12已有文本隔离回放16条声明/8个姓名候选，不等于身份已核；其他布局、完整逐人岗位资格与真实关系验收仍待办。）
- [ ] 4.8 扩展人员事实字段：身份证号、岗位、职称、证书类别/编号/有效期、合同单位/期间、社保单位/期间、工资流水。（bs-personnel-certificate-fields-20261119已补同表行职称/证件字段与逐证照段候选，A2-10身份字段对应及前端/报告接线；合同人员表qualification_declaration只显示合同资格声明，material-facts.v9追加批次，不填实际证书编号或身份证号。bs-technical-title-qualification-20261119已补A2-14技术负责人工程技术类职称等级与分包规模维度、同证号冲突及s3原件4.2.2固定依据、service幂等/报告同源/历史保护；独立无电话名单采用material-facts.v14追加。其他布局/证件形态全字段、其他岗位资格/执业专业适用、真实性及真实材料验收仍未完成。）
- [ ] 6.2 印章检测：公章/执业章“已见/未见/字迹不清+页码”。
- [ ] 6.3 签字检测：角色栏位“已见/未见/字迹不清+页码”，不做笔迹鉴定。
- [ ] 6.5 定向测试：签字留白、只有公章无执业章、字迹不清三类样例。（文本候选/日期/通用章痕迹/不清晰/模板与主体隔离合成回归已完成；真实图像区分公章与执业章尚无生产者，不能用bbox替代此验收。）
- [ ] 7.7 定向测试：task12 报告重跑，逐项检查禁用词与定位。（单测层已覆盖三档结论、四段式、禁用词阻断、阅读覆盖；task12 真实材料重跑待 9.1）
- [ ] 8.1 P0：安许许可范围覆盖。（bs-safety-permit-source-review-20261119已补397号2014/128号2015官方正文、A2-09有限许可行业对应、固定依据/快照和报告；355项定向回归，主任务仍部分。真实证照、旧线上条目修复、全行业效力/特殊许可边界仍待办，资质专业等级规模与有效性分别核查。）
- [ ] 8.3 P1：关键岗位在岗/不得同时受聘多个项目。（bs-personnel-attendance-facts-20261119已补逐人考勤/任职/承诺候选、A2-11普通人工提示、事实追加及工作台/报告展示；B类政策引文仍待核实，不从单份或异期多项目材料判持续在岗/同时受聘，真实履约与全范围验收未完成。）
- [ ] 8.4 P1：特种设备检验/权属。（bs-equipment-identity-facts-20261119已补逐台清单/检验/登记/权属/租赁记录、同主体同编号体系候选、A2-12人工提示、事实追加和报告来源；适用性、法定周期、满足施工需要和真实入场验收仍未完成。）
- [ ] 8.5 P1：施工方案审批链。（bs-plan-approval-facts-20261119已补项目/编号/版本唯一候选、三侧原文意见、A3-03-plan人工提示及追加事实；真实签章、审批效力、适用性及多页/其他布局验收未完成。）
- [ ] 8.6 P1：报审时序。（方案三侧日期仅作同身份原文排列观察；未证明实际实施时间，发包人同意及合同/进场全范围时序仍待办，旧A3-02-timing未由本切片改判。）
- [ ] 8.7 P1：合同不低于总包（需总包合同）。（bs-contract-comparison-risk-20261119已补双侧语义候选待核实、五维请求、空侧不比较与版本快照回归；仍为可选入口，未完成五步必选覆盖及真实合同验收。）
- [ ] 8.8 P1：安全生产费单列与 1.5%。（bs-contract-responsibility-facts-20261119已补逐主体合同原文、未绑定含义的金额/比例/税口径提及、RC-A10-cost人工提示及追加事实；依据原文、法定适用比例/基数、实际单列提取及真实验收仍待办，不把税率或合同金额直接套作法定基数。）
- [ ] 8.9 P1：农民工工资实名制/总包代发（专户由总包开设）。（同切片RC-A11-wages保存原文责任和来源，不把合同应履行当实际专户/代发/实名制完成，不要求分包自开总包专户；实名名册、实际流水/责任对应及真实履约验收待办。）
- [ ] 8.10 P2：劳务人员培训上岗。（bs-training-record-facts-20261119已补独立培训/签到/考核/交底逐人来源与原文结果，A2-13人工提示、training.records追加及报告；不把管理名单当培训分母，不据签到判合格或准予上岗；真实人员覆盖、内容适配/签字与实际进场时序等仍待验收。）
- [ ] 8.11 P2：履约担保/保险。（bs-performance-security-facts-20261119已补独立保函/保证金/保险单字段与类别隔离、A3-04-security人工提示、security.records追加及报告；首次/重新承揽专业分包等适用性、实际凭证/同比例/工期覆盖和保险责任范围及真实验收未完成，不机械适用劳务。）
- [ ] 8.12 P2：合同变更重新报审。（bs-contract-change-facts-20261119已补独立变更/报审身份与前后版本、意见原文、同身份唯一候选/不完整重叠保护、A3-05-change人工提示及change.records追加；前后原件实质差异、真实签章/不同类型流程及实施前重新报审验收未完成。）
- [ ] 9.1 task12 重跑：无 1/21、无条款错配、无“清单未载明→无法核实”。
- [ ] 9.2 报告2 场景：逐人社保结论、断缴/换单位/缺合同分别落到缺项/不符合。
- [ ] 9.3 报告1 场景：桩基不被误判为上部承重；资质等级给出明确结论。
- [ ] 9.4 每条问题有文件+页码+依据；正文/附件/结论计数一致。
- [ ] 9.5 人工复核队列仅含待核实，数量低于改造前。
- [ ] 9.6 定向单测与真实材料回归通过；V1.3/V1.4 历史报告不受影响。
- [ ] 9.7 更新 `implementation-validation.md`，记录命令、结果与未闭环项。

### bs-safety-cost-contract-20261119

来源：[任务文件](../../openspec/changes/bs-safety-cost-contract-20261119/tasks.md)。

- [ ] 1. 归档已授权原件及全文/页图/hash，视觉核对17/18条和印发日；补依据库与s1固定快照，保留未人工/线上核验状态。
- [ ] 2. 在现有合同正文范围内提取安全费单独约定、金额/比例/基数与重复提取条款，保留各候选和否定/义务语境，正式回归税率/冲突/未读/附件边界。
- [ ] 2A. RC-A10-rate在general工程层核公路类别、日期过渡及建筑安装造价绑定的唯一比例，按1.5%比较；未知/旧项目/冲突不套标准，不借用分包数据。
- [ ] 3. 演进RC-A10-cost为合同约定结论，保留工资人工提示，接逐主体service幂等、材料事实追加与旧批次/报告保护。
- [ ] 4. 修正V14统一1.5%表述，验证原始项/正文/附件/补正条件同源及参考组固定依据。
- [ ] 5. 运行有界回归、strict、diff，递增规则/映射/提取器版本，完整源码清单与验收台账；主8.8的真实履约仍待验。

### bs-safety-permit-scope-20261119

来源：[任务文件](../../openspec/changes/bs-safety-permit-scope-20261119/tasks.md)。

- [ ] 6. 完整现行依据归档、范围映射确认及真实材料验收（本切片不宣称完成）。

### bs-supplement-evidence-linkage

来源：[任务文件](../../openspec/changes/bs-supplement-evidence-linkage/tasks.md)。

- [ ] 保留真实材料阶段验收记录，后续按需要核对业务结论；当前文件的勾选不代表用户已确认全部样例。
- [ ] S4 补件证据投影到 A2-05（已有结构化投影和回归，完整业务验收待核对）
- [ ] 将补件证据投影到 A2-05，分别表达支持、冲突和无法确认
- [ ] 只在坐标经校验时高亮，无可靠坐标时仅提供页级跳转
- [ ] 问题描述限定对象与字段，不输出无关页面清单
- [ ] S5 人工复核与报告门槛（已有复核、候选发布与门禁实现，完整业务验收待核对）
- [ ] 更新人工复核界面，展示补件影响、字段原因和多页依据
- [ ] 将关键关系和未定位字段接入报告发布门槛
- [ ] S6 真实材料闭环验收（未开始）
- [ ] 使用模拟交安B证完成负向隔离验收
- [ ] 使用同主体清晰交安B证完成正向隔离验收
- [ ] 验证质量可疑和关键字段无法确认时不自动重复补读，保留页级人工复核依据
- [ ] 隔离任务验证上传、核查、复核、报告；记录人工验收边界和未完成项
- [ ] 补件重核（并入原 `bs-supplement-real-closure` 剩余范围，未完成）
- [ ] 对照当前候选/基线状态、发布及失败恢复代码，逐项核验自动生效与报告、人工意见一致性（已有部分实现及合成测试，不能整体标记完成）
- [ ] 验证受影响范围、材料与历史快照、人工意见、异常阻断与报告门槛；发现缺陷先复现再修复
- [ ] 报告阶段验收边界并等待用户确认
- [ ] 每阶段结束运行后端/前端回归与 `openspec validate --all --strict`，并记录结果

### bs-task-safe-recovery

来源：[任务文件](../../openspec/changes/bs-task-safe-recovery/tasks.md)。

- [ ] 完成跨执行阶段的服务端互斥和活动状态校验，验证并发与异常释放。
- [ ] 确保旧结论及人工回执完整可追溯，同时保持权限和可见性规则。
- [ ] 增加需用户主动确认、适配版本不一致及停滞场景的恢复交互。
- [ ] 通过后端、前端定向回归和合成浏览器验收；记录未覆盖项。
- [ ] 基于线上已部署镜像隔离打包，核验备份、回滚及运行态后部署并进行线上只读验证。
- [ ] 用户自行执行真实业务验收；不自动重跑或删除历史任务。

### standalone-docker-delivery

来源：[任务文件](../../openspec/changes/standalone-docker-delivery/tasks.md)。

- [ ] 1.2 按源码盘点分包前后端、共享服务、数据库、文件、认证和模型依赖，标明保留、替换及排除；复核源基线。
- [ ] 1.3 确认 WeKnora 版本、存储、Embedding、资料清单和目标 ID 生成接口；记录可验证的迁移方案。
- [ ] 2.3 迁移工作台及必要解析和核查依赖，验证业务主流程和三态语义。
- [ ] 2.4 验证复核、补件、报告、重启恢复及持久化；记录未验证项。
- [ ] 3.2 在隔离环境验证五步检索及原文定位，记录模型、维度与结果。
- [ ] 3.4 打包锁定镜像、校验清单及安装说明；在干净环境验证离线加载和恢复。
- [ ] 3.5 审查许可证、敏感信息及制品范围；分别记录自动测试、真实材料、专家及客户环境验收。

### standalone-platform-learning-governance-20261115

来源：[任务文件](../../openspec/changes/standalone-platform-learning-governance-20261115/tasks.md)。

- [ ] 3.4 对获准的差距建立定向回归并实施；分别记录源码测试、合成、隔离真实材料和专家业务验收状态。
- [ ] 3.5 阶段结果经用户确认后再进入页级人工复核工作。
- [ ] 4.1 在独立版定义页级查看/确认/重读/补件动作、证据来源、版本和回执失效条件。
- [ ] 4.2 实现 API/持久化契约与权限、代次检查，并覆盖旧快照和失败路径。
- [ ] 4.3 实现工作台可见的逐页人工操作，确保未覆盖、未读和执行失败状态可区分。
- [ ] 4.4 验证人工动作、快照、后续核查及报告投影一致后关闭阶段。
- [ ] 5.1 对照 `bs-report-adaptation` 和报告门禁变更，确认单一业务事实投影及各导出格式的版本来源。
- [ ] 5.2 验证范围未覆盖、AI 未完成、执行失败、待读复核和真实阻断事项在页面/API/Word/PDF 中语义一致。
- [ ] 5.3 经授权完成代表性真实材料报告与人工处置验收；不得以模型建议数或合成结果替代。
- [ ] 6.1 对照两版任务代次、事件、租约、checkpoint、重试和计费幂等，更新状态/事件映射。
- [ ] 6.2 只有共用需求和维护收益明确时，提出共享代码包或适配器方案及回退计划。
- [ ] 6.3 对涉及 schema、身份、计费、部署或旧客户端的改动完成单独设计审查和用户确认。
- [ ] 7.1 依 `standalone-docker-delivery` 按 P0-P6 更新知识、运行、恢复、离线包、许可和交接证据。
- [ ] 7.2 分别标注源码/自动回归、隔离候选、真实业务/专家、客户部署结论和限制。
- [ ] 7.3 经授权完成目标环境验收；部署与业务验收分别由相应证据关闭。
