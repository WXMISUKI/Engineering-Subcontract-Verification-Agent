# 独立版回学开发现状与下一步评估

核验日期：2026-11-16。范围：本聊天持久目标、当前 Git 工作区、OpenSpec、源码和隔离回归。本轮完成 A5 本地技术切片和 A6 首批本地技术验收；后续经用户授权补齐本地试用库结构并启动源码服务，详见第 13 节。未执行客户数据库迁移、客户部署或模型调用。此前各节的 active/未完成描述保留为当时检查点，当前业务验收仍未关闭。

## 1. 当前到底到哪一步

**已完成 A0–A2，A3.1–A5 本地技术切片，A6 首批本地技术验收。阶段 2 尚未完成。**

| 阶段 | 当前状态 | 证据与边界 |
| --- | --- | --- |
| 方向与契约 | 用户已确认 | 独立交付优先；版本化公共契约加各仓适配器；暂不抽共享包；`bs-review-contract.v1` 已冻结 |
| 阶段 2 范围 | 用户已确认 | 20 条确认、13 条待确认、5 条样例不迁移；首批 4/5/13/25 + 21/24 |
| A0–A2 最小报告/收口门禁 | 本地技术验证完成，已有提交 | `b65688b`；不代表真实材料、专家业务或客户部署验收 |
| A3.0 语义存储选择 | 用户已确认并已记录 | 可空 `finding_semantics`；历史 NULL 保留原门禁、不回填；仅本地实现与隔离测试 |
| A3.1 语义基础链路 | 本地技术切片完成，未提交 | 模型列、显式迁移、DTO、冻结/回执、报告、复核队列和旧库诊断均已接通；后端 54、前端 21 聚焦回归通过 |
| A3.2 负面清单 | 本地技术切片完成，未提交 | 有效候选建议显式为 `manual_prompt` 并进入普通待处理；无信号、未定位范围、读取缺口和执行失败保持独立统计/阻断语义 |
| A3.3 监理/书面同意 | 本地技术切片完成，未做真实材料验收 | V16-01 双侧证据分别写入 `evidence_json`，保留混合态/对象冲突/缺侧语义；栏目标题不再点亮风险或合规；真实原页、跨 API/报告页面验收仍待完成 |
| A4 逐人证件与社保 | 本地证据切片完成，未做真实材料验收 | A2-01 证据引用增加人员键、岗位、主体和证据侧；工作台显示逐人元数据，报告 `evidence_list` 保留同一字段；仍保留单条 A2-01 finding，岗位适用规则、真实材料和专家验收待完成 |
| A5 同页质量归组 | 本地技术切片完成，未提交 | 仅按明确主体、材料修订身份和物理页生成只读聚合摘要；逐项 finding/回执保持独立，真实页面与专家验收未完成 |
| A6 首批验收 | 本地技术验收完成 | 后端 `406 passed`、前端 `30 passed`，OpenSpec strict 与 `git diff --check` 通过；真实材料、专家业务验收、后续页级复核、报告完整阶段、恢复兼容及 P0–P6 均未关闭 |

当前 `main` 的 HEAD 为 `b65688b`，前一提交为 `90957d9`。相对本地缓存的 `origin/main` 领先 2 个提交；本轮未 fetch，不将缓存状态当作远端实时审计。

## 2. 暂停时留下的内容

审查开始时有 8 个已跟踪文件修改、2 个未跟踪文件。本记录是本轮新增的第 3 个未跟踪文件。

- `models.py` 增加 `String(30), nullable=True` 的列。
- `schema.py` 和独立版 `schema_migrations.py` 增加对应补列代码。
- 新增 `finding_semantics.py`，定义两个语义值和精确识别 `manual_prompt` 的 helper；已被报告、DTO、冻结、回执同步和复核队列消费。
- 新增后端 `test_bs_finding_semantics.py`，扩展前端 `reportGate.test.js`。
- 治理目录的 design、tasks、规格和存储决定记录已更新。

当前已修改 `service.py`、`router.py`、`reporting.py`、复核队列模型及负面清单候选同步；新增字段和候选语义已在本地隔离链路生效，但不代表真实业务验收或客户部署完成。

## 3. 本轮实际验证

沿用 `backend/.venv/Scripts/python.exe`，数据库用隔离 SQLite，pytest fixture 阻止外部连接。未运行 npm build。

后端在 backend 运行：

```powershell
rtk proxy ./.venv/Scripts/python.exe -m pytest tests/test_bs_finding_semantics.py tests/test_bs_task_report_readiness.py tests/test_bs_report_gate.py tests/test_bs_supplement_review_retention.py -q --tb=line --disable-warnings
```

结果：**40 passed、12 failed、35 warnings**。其中已有定向回归 37 项全部通过；A3 新增用例 3 项通过、12 项失败。新增用例中已验证迁移重复执行后历史值仍为 NULL，以及缺失/未知冻结语义仍阻断。

前端在 frontend 运行：

```powershell
rtk proxy npm test -- --run tests/reportGate.test.js tests/reportReadiness.test.jsx tests/reviewWorkbenchModel.test.js
```

结果：**20 passed、1 failed**。已有定向用例 16 项通过；新增用例 4 项通过、1 项失败。失败项表明普通提示仍进入“无法核实”计数并被阻断。

治理变更 `openspec validate standalone-platform-learning-governance-20261115 --strict` 通过；现有改动的 `git diff --check` 通过。规格通过只表示文档格式有效。

A0–A2 的既有回执记录了 107 个不同后端用例及 37 个前端用例曾通过。本轮仅重跑上述子集，不将之前的结果表述为当前工作区全部验证通过，也不重复累计覆盖数量。

## 4. 主要发现与优先级

### P1：可空列不能兼容“数据库根本没有该列”

锚点：`models.py:342`；独立版 `main.py:58`；迁移入口 `manage.py:27`。

用内存 SQLite 创建当前表结构后，只删除 `finding_semantics` 列，再调用实际 `select(BsFinding)`，可复现 `sqlalchemy.exc.OperationalError: no such column: bs_finding.finding_semantics`。没有连接现有本地或客户数据库。

普通启动不执行新增列 DDL 的边界合理，应保留。但当前没有针对该列的明确结构未就绪诊断；已有 router schema 错误处理只覆盖 `bs_material` 的部分版本列。不能把“列存在但值为 NULL”的兼容结论外推到“列不存在”的旧库。

后续要求：补充窄范围结构诊断及隔离接口测试，明确先迁移结构再使用新 ORM 的顺序。客户迁移仍单独授权。

### P1：语义变化时旧回执仍会被继承

锚点：`service.py:2903` 的 `_CARRY_CONCLUSION_FIELDS` 和 `service.py:2920` 的 `_inherit_review`。

当前指纹没有语义字段。实际测试中只把结论从 `machine_conclusion` 改为 `manual_prompt`，旧 accepted 回执仍直接返回，没有进入 invalidated 留痕。新语义尚未由规则写入，所以这是基础切片未完成的明确缺口。

后续要求：语义进入 carry 的采集和比较两端，保留旧回执原文，语义变化触发失效。

### P2：API、报告、复核队列仍按旧三态工作

锚点：`router.py:1769`、`reporting.py:343`、`reporting.py:446`、`reporting.py:1619`、`reviewQueueModel.js:13`。

实际用例证实 DTO 未提供字段、报告仍把提示计入 undecidable、前端仍阻断。生产 helper 暂无调用者。只新增列不能满足已确认的人工提示需求。

后续要求：通过统一语义 helper 处理 DTO、标签、报告/意见书统计和队列分组；保留原 status、证据及人工处置。NULL/未知值继续旧逻辑，不能用消息推断。

### P2：冻结与回放尚未接通，测试也有后置断言未执行

锚点：`service.py:5119` 的 `_review_version_snapshot`、`router.py:1975` 的冻结门禁。

源码确认快照未保存语义，冻结门禁未读取语义。现有综合测试在报告计数断言就失败，后续冻结断言尚未执行；不能据测试文件里存在断言就声称冻结已经得到验证。

后续要求：分别验证快照原样保存、新版提示放行、旧版缺字段保守处理，以及当前任务语义变化不影响历史。将必要断言拆成可独立失败的用例，避免前置失败掩盖后面的缺口。

## 5. 技能和工具的实际使用边界

- **using-superpowers / grilling**：先核对已有决定和事实。D2、首批范围、A3 存储方案不再重问。本轮事实足以确定下一项工作，无须新增架构选择。
- **gstack**：实际运行 `gstack-skill-start`，返回协议 1、interactive、模型适配 gpt，telemetry 和 artifacts sync 均为 off。采用其完整需求覆盖、最小必要代码及核查差异消费者的思路。
- **gstack 自动扫描的限制**：当前安装的 `review/` 下缺少 `SKILL.md`，源副本可读；本会话未注册该子技能。`gstack-gate-diff` 是 Bun/TypeScript，曾误用 Bash 调用，随后核对了 shebang；PATH 和默认 `.bun/bin` 未找到 Bun，未获得 `GATE_SUMMARY`。不将本轮描述成完整 gstack 自动评审或多模型独立评审，也未安装、升级工具。
- **pstack blast-radius / boundary-discipline / prove-it-works / sequence-verifiable-units**：通过磁盘技能指导实际追踪 ORM、迁移、API、报告、快照、回执和 UI，运行真实导入及隔离回归。这里使用的是技能流程，不依赖一个名为 pstack 的命令。
- **CodeGraph 与原生检索**：先读关联函数，再用精确检索确认新字段的实际消费者和错误处理边界。

工具缺口不会阻止本地现状判断；工具安装修复可另作一次配置任务，不应混入业务语义变更。

## 6. 继续开发时的顺序（A3.1 完成前的计划）

不建议重选共享包路线，也不建议在当前失败基线上并行推进 A3–A5。保留现有改动，先把 A3.1 做成一个完整、可复核的本地切片。

1. **结构兼容**：验证新增列幂等且不回填；补结构未就绪诊断；普通启动继续不执行 DDL。以真实 ORM 的旧库失败及明确接口诊断为验收依据。
2. **语义及审计链**：统一 helper、DTO、carry 采集/指纹、版本冻结及回放。语义来源只允许明确规则分支；未知/NULL 不获得提示豁免。
3. **报告与复核投影**：提示独立标签与计数，留在待处理；不进入机器结论汇总或无法核实门禁。同步工作台、报告和意见书，验证读取/执行/身份缺口仍独立阻断。
4. **A3.1 收口**：所列新增用例和受影响既有用例通过，OpenSpec/diff 检查完成后，才形成可提交的实现切片。保留有维护价值的正式回归。
5. **A3.2 再 A3.3**：先负面清单，再监理/书面同意，逐项补失败反例和双侧证据。A3 完成后再依次 A4、A5、A6。

每一步为本地实施检查点，不是客户部署批准。阶段 2 的真实材料/专家证据与后续阶段门继续独立保留。

当时评估结论为：**A3 实现未完成，下一次恢复开发的明确起点是 A3.1。** 当前结果已在第 7 节更新；A3.1 已完成本地可验证切片，A3.2 起仍未完成。

## 7. A3.1 第一轮链路复核结果（2026-11-16）

本轮在保留全部既有未提交改动的前提下完成了 A3.1 的第一轮链路复核。CodeGraph 当前索引为 391 个文件、6,680 个节点、16,384 条边；影响面覆盖模型、显式迁移、DTO、冻结快照、重跑回执指纹、报告/意见书统计、复核队列和旧库结构诊断。

本地验证结果：

- 后端：`backend/.venv/Scripts/python.exe -m pytest tests/test_bs_finding_semantics.py tests/test_bs_finding_semantics_schema.py tests/test_bs_task_report_readiness.py tests/test_bs_report_gate.py tests/test_bs_supplement_review_retention.py -q --tb=line --disable-warnings`，**54 passed**。
- 前端：`npm test -- --run tests/reportGate.test.js tests/reportReadiness.test.jsx tests/reviewWorkbenchModel.test.js`，**21 passed**。
- `openspec validate standalone-platform-learning-governance-20261115 --strict` 通过；`git diff --check` 通过。

已直接验证的兼容事实：历史/NULL/未知语义继续旧门禁；显式 `manual_prompt` 只改变展示、统计和普通提示的复核阻断，不解除未读页、执行失败或身份缺口；语义变化会使旧人工回执失效并保留原文；冻结快照原样保存语义；旧库缺列访问返回明确 503，服务不会自动执行 DDL。

工具边界：CodeGraph 与 pstack 的影响面/边界/可证明性流程已实际使用。gstack skill-start 已运行，但仓库位于 `main` 且未声明可用 gstack checks，故未生成 PR 预落地审查或健康分数；不把这一工具缺口写成代码质量通过。当前 A3.1、A3.2 与 A3.3 均可作为本地可验证切片收口，A4、A5、A6、真实材料/专家验收、客户迁移与部署仍未关闭。

## 8. A3.2 负面清单状态分离结果（2026-11-16）

CodeGraph 复核了 `_run_w16_02`、`plan_negative_candidates`、`summarize_coverage`、`project_job_state`、`make_unlocated_scope_findings` 和 `sync_candidate_review_records` 的调用链。现有规则已区分确定性负面清单结论、候选执行状态、无规则信号、范围未定位、读取缺口和执行失败；本轮补齐了候选进入人工复核时的语义写入与重跑一致性。

本轮改动：

- 通过当前代、主体和双侧原文校验的候选建议，创建 `BsFinding` 时写入 `finding_semantics=manual_prompt`，保留 `status=undecidable`、证据和人工处置权限。
- 候选同步比较加入 `finding_semantics`；同一稳定键的语义漂移会触发 `AiContextChanged`，不会静默复用旧候选或旧回执。
- 无信号、范围未定位、读取缺口和执行失败不写入 `manual_prompt`，继续由覆盖统计和任务 readiness 单独处理；未定位范围记录保持 NULL 并阻断报告。

验证结果：负面候选、候选 checkpoint、范围未定位、报告门禁和 finding 语义聚焦回归 **93 passed**；前端报告/复核队列聚焦回归 **21 passed**；OpenSpec strict validate 和 `git diff --check` 通过。该证据只关闭 A3.2 的本地技术切片，不关闭真实材料、专家验收、客户迁移或部署。

## 9. A3.3 监理/发包人书面同意本地技术切片结果（2026-11-16）

本轮先以反例锁定“栏目标题不等于签署或书面同意”，再补双侧证据投影：

- `_side_polarity_scan` 增加栏目标题守卫。单独的“总监理工程师签章：”“发包人书面同意：”只形成线索，不点亮 `legal` 或 `risk`；完整正文仍按既有极性守卫处理。
- V16-01 的命中、未命中、混合态、对象冲突和单侧缺口结论均保留双侧 `evidence_json`，每侧包含 `material_id`、1-based `page`、`quote`、`side`、`evidence_source` 和 `evidence_kind`；原有 `material_id/page/quote` 主锚点保持兼容。
- 保留既有双侧独立规则：监理侧优先读取 A3 RC-09；发包人书面同意侧独立取材料文本；一侧风险一侧合规仍为混合态；不同项目 token 不拼接为命中；读取/主体/执行缺口不改写为普通提示。

本地验证：

```powershell
rtk proxy ./.venv/Scripts/python.exe -m pytest tests/test_bs_a1_illegal_subcontract.py tests/test_bs_signature_form_clues.py tests/test_bs_a1_kernels.py tests/test_bs_finding_semantics.py tests/test_bs_finding_semantics_schema.py tests/test_ai_negative_candidate_relevance.py -q --tb=short --disable-warnings
```

结果：**238 passed，5 warnings**；`git diff --check` 通过。证据等级为源码 + 隔离回归（S/T），尚未覆盖真实材料、专家业务验收、完整 API/工作台/Word/PDF 页面回查、客户迁移或部署。A3.3 可作为本地技术切片关闭，但阶段 2、3.4、A4–A6 仍保持未完成。

## 10. A4 逐人证件与社保本地证据切片结果（2026-11-16）

本轮沿用已确认的主体/证据边界，补齐 A2-01 的逐人引用键：

- 后端 `run_person_consistency_check` 对名单、社保和劳动合同事实写入统一的 `person_key`、`post`、`subject_id`、`evidence_side` 和页级定位；同名跨主体保留不同主体键，不借用岗位或关系。
- 既有 A2-01 三态、缺件文案和单条业务 finding 保持兼容；未将姓名覆盖升级为同人、劳动关系成立或岗位必需证件结论。
- 报告的 `evidence_list` 原样保留逐人元数据；复核工作台显示人员、岗位、主体和证据侧，缺失字段继续显示待确认，不猜测。
- 证件关系表的 append-only、材料替代链、事实批次和 A2-05 投影继续沿用现有边界，本轮未改写历史关系或来源分类。

本地验证：

```powershell
rtk proxy ./.venv/Scripts/python.exe -m pytest tests/test_bs_a1_illegal_subcontract.py tests/test_bs_signature_form_clues.py tests/test_bs_a1_kernels.py tests/test_bs_a2_kernels.py tests/test_bs_personnel_completeness_messages.py tests/test_bs_person_certificate_relations.py tests/test_bs_evidence_projection.py tests/test_supplement_report_replay.py tests/test_bs_finding_semantics.py tests/test_bs_finding_semantics_schema.py tests/test_ai_negative_candidate_relevance.py -q --tb=short --disable-warnings
rtk proxy npm test -- --run tests/personnelEvidence.test.jsx tests/reportGate.test.js tests/reportReadiness.test.jsx tests/reviewWorkbenchModel.test.js
```

结果：后端 **404 passed，14 warnings**；前端 **27 passed**。证据等级仍为源码 + 隔离回归（S/T）。A4 仅关闭本地逐人证据投影切片；单条 finding 的兼容形态、岗位适用规则、真实材料/专家验收、客户迁移和部署仍未关闭。

## 11. A5 同页质量归组本地技术切片结果（2026-11-16）

本轮只增加工作台的只读展示聚合，不合并业务 finding：

- `buildQualityGroups` 仅接受明确主体、明确材料修订身份（`revision_no` 或明确 `generation_id`）、正整数物理页和已登记的 `quality=review_required/unreadable` 证据引用。
- 同主体、同修订材料、同物理页的多条质量问题在队列组头显示摘要；每条 finding 仍保留自己的 id、状态和复核入口，聚合项不携带或共享人工回执。
- 跨主体、跨修订、缺页、历史未知版本和没有质量标记的引用不归组；旧快照不会被重算或补写归组字段。

本地验证：

```powershell
rtk proxy npm test -- --run tests/reviewWorkbenchModel.test.js
```

结果：前端聚焦回归 **4 个测试文件、30 passed**。这只证明前端纯函数与工作台投影的隔离行为，不代表真实页面、专家业务验收、客户迁移或部署完成。A5 本地技术切片完成后，下一项为 A6 本地技术验收；阶段 2 与 3.4 继续保持未完成。

## 12. A6 首批本地技术验收结果（2026-11-16）

后端复用了 A3/A4/A5 相关规则、证据、语义、报告投影和隔离回归，执行结果为 **406 passed，14 warnings**；前端工作台、人员证据、报告门禁和 readiness 聚焦回归为 **30 passed**。OpenSpec strict validate 与 `git diff --check` 均通过，CodeGraph 同步后为 391 个文件、6701 个节点、16426 条边。

本验收只关闭本地源码和隔离回归门，不关闭真实材料/专家业务验收、客户数据库迁移、部署、页级人工复核闭环、报告全格式验收、恢复兼容或 P0–P6 交付。持久目标和阶段 2 仍保持 active/未完成；下一步应由用户确认是否进入页级人工复核阶段，而不是自行扩大业务规则或执行部署。

## 13. 用户本地业务验收环境启动（2026-11-16）

用户要求启动最新源码供本地测试，并分别明确批准 `finding_semantics` 单列与材料表两列的窄范围补齐。仅操作 `bstrialv2` 本地 `bs_review` 库，未修改 8089 对应另一套库、客户或线上环境。

- `bs_finding` 新增 `finding_semantics VARCHAR(30) NULL`。
- `bs_material` 新增 `general_source_set_id VARCHAR(36) NULL`、`general_source_set_version INTEGER NULL`。
- 历史数据未回填。补齐后实际 ORM 读取两表成功；finding 为 287 行，material 为 5 行，三个新增字段的非 NULL 记录均为 0。原字段内容一致性已在补列前后核对。

补列前表备份保存在 `backups/local-start-20261116/`，属于本地数据库备份，不应随源码交付或提交：

| 文件 | 字节 | SHA256 |
| --- | ---: | --- |
| `bstrialv2-bs_finding-133111.sql` | 219221 | `789242d384ea874dc9d22760334bbe9f2ad7b395e1e87b270592156fb591d963` |
| `bstrialv2-bs_material-133347.sql` | 1473236 | `ef813bdfda45c983a70aa1858f27f7992c039539cd1e6aa43b1ac88aafb0cefd` |

最新前端使用 Vite，地址为 `http://127.0.0.1:5173/`，API 经本地 `8002` 代理转发至源码后端 `bs-local-ai-acceptance-20261110`。后端启动成功后，代理仍连接旧上游地址导致 502；仅重启本地代理 `bs-local-http-proxy-20261111` 后恢复，未修改产品代码。

最终实测：前端首页 200，`8002/api/health` 和 `5173/api/health` 均 200，未登录访问 `5173/api/session` 返回预期 401（请先登录）。8089 是另一套既有打包环境，本轮验收应使用 5173。

上述证据只确认服务启动、前后端连通及两表 ORM 兼容；未代替用户登录、真实材料处理、报告页面和专家业务验收，也未触发付费模型或重跑真实任务。当前可以开始用户本地业务验收。

## 14. 报告流程与 V1.3 内容修订（2026-11-16）

用户后续明确 OCR 读取质量仅提示、不单独阻断报告，真实执行失败继续保留门禁。实现与技术验证见 `openspec/changes/bs-ocr-quality-advisory-20261116/implementation-validation.md`。任务 7 已真实完成报告链路和版本 1 留存；流程成功不代表报告内容已通过业务验收。

针对用户实际导出与领导模板，再完成当前工程角色筛选、工程名格式归一、认可表事项／范围／金额、58 页及业主确认投影。用户明确业主为上海沪申高速公路建设发展有限公司，以任务事件保存；写前备份、写后比对，历史版本 1 和 29 条人工回执哈希不变。

V1.3 映射 `2026-11-16.2` 按文件去重依据，正文第四部分改分类概要，第三部分恢复八维度四列表，附件一为概况／边界／依据，附件二为五列主题归并表，原明细留在系统。旧模板仍默认，审批、期限、签字留待 Word 人工填写。

相关后端回归及 17 项前端专项回归通过；两个报告规格 strict validate、diff check 通过；参考与修订 Word 实际渲染、产品 PDF 逐页检查完成，真实登录页面核实正确字段与概要。本地源码后端已更新，5173 继续作为验收入口。详细证据及产物位置见 `openspec/changes/bs-report-v13-content-refinement-20261116/implementation-validation.md`。

本轮没有重新 OCR、调用模型或重跑规则，不将旧缺项和证据不足直接改为符合；原核查质量与未覆盖能力仍需后续处理。用户报告内容验收未关闭，客户／线上部署未执行，原阶段 2 与后续治理任务不因此自动完成。
