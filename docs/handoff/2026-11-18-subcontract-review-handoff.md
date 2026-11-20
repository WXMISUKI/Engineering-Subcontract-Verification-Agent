# 分包核查智能体 — 任务交接文档

日期：2026-11-18
用途：把当前实现状态、硬约束与剩余任务交给下一位接手的 AI/工程师，避免重复调研与偏离主设计。
接手后请先读本文件，再读 `openspec/changes/bs-review-dimension-basis-p0-20261118/` 下的 `design.md` 与 `tasks.md`。

---

## 1 项目定位与核心链路

工程分包核查智能体：上传分包报审材料 → 逐页阅读与事实抽取 → 五步规则核查 → 生成可签发报告 → 人工复核。

规则步骤（`data/review_flow.json`）：

| 步骤 | 名称 | 主要内核 |
| --- | --- | --- |
| s1 | 要素完整性核查 | `a1_check.py`（负面清单、转包/违法分包、合同要素） |
| s2 | 资质与内容合规性审查 | `a2_check.py`（A2-04 资质匹配、A2-05 劳务、A2-06 安许） |
| s3 | 关键岗位人员核查 | `a2_check.py`（A2-01 人员一致性、A2-07 岗位配备）+ `personnel_adequacy.py` |
| s4 | 证件证照有效性核查 | `a2_check.py`（A2-02 证件有效期）+ `certificate_facts.py` |
| s5 | 内部信用与事故材料核查 | `a3_check.py`、`internal_records.py` |

产出：`business_document`（报告正文）+ 附件一/附件二 + 结论；报告策略版本 `2.0.0`（`report_policy_data/policy.json`）。

环境：Windows + PowerShell；后端虚拟环境 `backend/.venv`；数据库 SQLite（测试）与生产库按部署配置。

---

## 2 事实来源（不要重新调研，先读这些）

| 内容 | 位置 |
| --- | --- |
| 本变更规格与任务 | `openspec/changes/bs-review-dimension-basis-p0-20261118/{design.md,tasks.md}` |
| 维度与依据缺口分析 | `docs/design/2026-11-18-review-dimension-and-basis-gap-analysis.md` |
| 依据清单与强度 | `docs/design/2026-11-18-dimension-rule-basis-inventory.md` |
| DG/TJ 人员表评审 | `docs/design/2026-11-18-dgtj-2225-staffing-table-review.md` |
| 规则数据目录 | `backend/app/plus/blockchain_supervision/data/` |
| 历史验收记录 | `openspec/changes/*/implementation-validation.md`（本变更尚未生成） |

---

## 3 当前完成度

已完成（`tasks.md` 已勾选）：

- **1.x 依据库与引用校验**：`review_basis.json`、`basis_validation.py`（加载期 `ruleset_gate()` + 落库期 `gate_findings()`）、文档角色 `full_text/notice_only/attachment_notice`、附件父子映射、扫描件引用模式。
- **2.x 判定模型**：新增 `not_applicable` / `not_included` 两状态与 `counted_status()` 折叠；`action`/`severity` 由 `status_disposition()` 推导；待核实三因校验；材料存在性判定；`reading_gate.py` 阅读门禁；`report_template_v14.consistency_violations()` 报告一致性 fail-closed。
- **3.x 负面清单适用性**：`negative_applicability.py`（domain/结构部位/适用性谓词），报告不再出现 `x/21`。
- **4.2–4.6、4.9 人员维度**：岗位齐套（DG/TJ 表3.3.3/3.4.1）、三重关系（合同↔社保同单位，分公司视同本单位）、社保期间（基准日所在月或上一自然月）、安全员双算（法定下限 + DG/TJ 推荐值），`personnel_adequacy.json` 已数据化。
- **5.x 资质等级与承包范围**（本阶段刚完成）：`qualification_scope.json` + `qualification_scope.py`，A2-04 第二段判定；按评审口径只覆盖首批 5 类，未录入依据的类别输出“待核实”；报告分节结论同步支持“符合/不符合/待核实”三值。
- **7.1–7.6 报告话术**：三档结论（不同意 / 原则同意，附条件通过 / 同意）、四段式（事实→依据→影响→补正）、禁用词 fail-closed（`1/21`、`未命中`、`无法判定`、作为结论的 `待补正`）、阅读覆盖说明、确定性文本生成。

最近一轮验证：`test_bs_qualification_scope.py`、`test_bs_a2_kernels.py`、`test_bs_ruleset_deployment.py`、`test_bs_ai_terminal_retry.py`、`test_bs_report_version_http.py` 共 **138 项通过**；加上 `test_report_document.py` 等报告用例合计 **208 项通过**。两处过期测试夹具已修正（冻结策略版本改为 `9.9.9`；运行中作业跨进程断言改为幂等返回）。

全量 `pytest` 仅剩 1 个失败：`tests/test_standalone_workflow.py::test_uploaded_pdf_starts_and_runs_checks_with_unavailable_knowledge`。根因是该用例整体落后于未提交的产品改动：草稿禁止文件导出（`draft_file_output_blocked`）、补件候选未发布前不可读正式稿（`supplement_candidate_unpublished`）、AI 未跑完优先拦截（`ai_execution_incomplete`）。其中第 242 行同时要求 `publication_status == "ready"` 与 `publication_state == "candidate"`，在当前实现下互斥。**接手时不要逐条改断言**，先确认“补件候选如何发布后才 ready”的预期流程，再整体重写该用例；产品行为未改动。

---

## 4 关键代码地图

| 模块 | 职责 | 注意点 |
| --- | --- | --- |
| `a2_check.py` | A2-01/02/04/05/06/07 | A2-04 先跑类别段（`qualification_matchers.json`），再追加等级段 |
| `qualification_scope.py` | 资质等级/规模覆盖（纯函数，不依赖 DB） | `evaluate_scope()`；`_category_listed()` 门禁：类别必须在“资质类别/资质等级”段或页面为资质摘录，否则跳过，避免把营业执照经营范围当资质 |
| `personnel_adequacy.py` | 岗位与安全员配备 | 法定下限与推荐值取高者；仅低于推荐值给“建议补正” |
| `negative_applicability.py` | 负面清单适用性 | 只对适用条目计覆盖 |
| `reading_gate.py` | 阅读门禁 | 只降级不抛错；“符合”结论的证据页必须已读 |
| `basis_validation.py` | 依据引用校验 | 只校验 `basis_index.json` 已登记条目，未登记 ID 不阻断 |
| `ruleset.py` | 规则版本唯一定义点 | `RULESET_FILES` 显式清单 + `code_revision` 串接 sha256 |
| `report_document.py` / `report_template_v14.py` | 报告结构、分节、一致性 | A2-04 分节结论由 `_qualification_verdict()` 决定：第二段可量化即可判“符合/不符合”，只有类别词表比对时仍为“待核实” |
| `rule_item_catalog.py` | 规则条目与局部重跑计划 | `changed_item_ids` 驱动 |

---

## 5 硬约束（违反即返工）

1. **不编造法规数字**。阈值必须有原文出处；未归档依据一律 `verified: false`，判定输出“待核实”，不跨版本换算（2026 甲/乙级 vs 旧版一/二/三级）。
2. **规则版本必须手工同步**：改 `RULESET_FILES` 内任一文件或改判定内核后，必须同步 `data/ruleset.json` 的 `version`（用 `ruleset.current_version()` 现算）、`code_revision`、`changed_item_ids`。当前值：`rs-62bdecd7d223b9e4` / `2026-11-18.3` / `["A2-04"]`。
3. **依据强度 A/B/C/D**：只有 A 类法定强制可判“不符合”；B/C 类只能“建议补正/需澄清”。
4. **报告禁用词 fail-closed**：正文不得出现 `1/21`、`未命中`、`无法判定`、作为结论的 `待补正`；一致性校验冲突直接拒绝导出。
5. **待核实与缺项分开**：`undecidable` 只用于冲突/模糊/外部参数缺失；材料没交、字段没抽到=缺项（`not_included`）。
6. **不新增 finding kind 除非确有必要**：沿用既有 kind + item_id，才能自动进入分节、附件、一致性校验与阅读门禁。
7. **保留无关改动**：工作区有大量未提交改动（前端、报告模板等），禁止 `git add -A`/全量暂存；只提交本次相关文件。
8. **测试纪律**：不跑 `npm build`；只跑定向 `pytest`；一次性调试脚本/临时用例验证后删除，正式回归测试保留。
9. **回复语言**：中文。

---

## 6 剩余任务（按推荐顺序）

### 6.1 真实验收（最高优先，对应 9.1–9.4、7.7）

| 编号 | 内容 | 完成条件 |
| --- | --- | --- |
| 9.1 | task12 真实材料重跑 | 报告无 `1/21`、无条款错配、无“清单未载明→无法核实” |
| 9.2 | 报告2 场景 | 逐人社保结论；断缴/换单位/缺合同分别落到缺项或不符合 |
| 9.3 | 报告1 场景 | 桩基不被判为上部承重；资质等级给出明确结论 |
| 9.4 | 证据与计数 | 每条问题有文件+页码+依据；正文/附件/结论计数一致 |
| 9.5 | 人工复核队列 | 只含待核实，数量低于改造前 |
| 9.6–9.7 | 回归与记录 | 定向单测通过；新建本变更 `implementation-validation.md` 记录命令、结果、未闭环项 |

### 6.2 签章矩阵（6.1–6.5）

角色矩阵（项目经理/专业监理工程师/总监理工程师/发包人）→ 印章检测（已见/未见/字迹不清+页码）→ 签字检测（不做笔迹鉴定）→ 附件3/4 映射 → 三类样例测试。

### 6.3 人员字段与特种作业（4.1、4.7、4.8）

- 4.7：分包内容→工法→工种→名单覆盖，数量只提示；
- 4.8：扩展身份证号、岗位、职称、证书类别/编号/有效期、合同/社保单位与期间、工资流水（4.1 的身份证主键依赖此步）；
- 4.1：待 4.8 有数据源后再切主键，当前姓名+单位兜底。

### 6.4 补充维度（8.1–8.12）

P0：安许许可范围覆盖（8.1）、专职安全员法定配备（8.2，与 4.6 合并）。
P1：关键岗位在岗、特种设备检验/权属、施工方案审批链、报审时序、合同不低于总包、安全生产费单列 1.5%、农民工工资实名制。
P2：劳务培训、履约担保/保险、合同变更重新报审。

### 6.5 前置评审遗留（0.2–0.5）

0.2 全量类别等级映射（当前仅 5 类）、0.3 负面清单 21 叶归类复核、0.4 六个开放问题、0.5 冻结规则版本号。0.2 已按“证书载明优先、双版本并存”执行，剩余类别属扩展项。

---

## 7 已知坑位

1. **营业执照经营范围**：类别词出现在经营范围不等于有该资质，必须走 `_category_listed()` 门禁。
2. **2026 甲/乙级**：`pending_levels` + `pending_level_reason`，判定为待核实；原文归档后只需把对应 `verified` 改 true 并补阈值。
3. **隧道二/三级**：`data_status: pending_basis`，同理。
4. **测试夹具随策略版本过期**：涉及冻结策略版本的用例不要写死当前版本号，用必然不匹配的值（如 `9.9.9`）。
5. **作业状态机已改租约模型**：`running` 走幂等返回，只有 `interrupted` 才抛“无法确认”；不要再按 `INSTANCE_ID` 断言报错。
6. **报告分节结论的识别方式**：`report_document._scope_segment()` 用证据里的 `source_kind == "qualification_certificate"` 判定“第二段条目”。类别词表条目永远不能单独得出“符合”，否则就是把“只比了类别”说成“资质符合”。
7. **规则数据是业务事实**：`qualification_scope.json` 等文件已进 `RULESET_FILES`，改一个数字就会改规则版本，需同步 `ruleset.json`。

---

## 8 验收命令

```powershell
# 后端定向回归（在 backend 目录）
.\.venv\Scripts\python.exe -m pytest tests/test_bs_qualification_scope.py tests/test_bs_a2_kernels.py tests/test_bs_ruleset_deployment.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_bs_ai_terminal_retry.py tests/test_bs_report_version_http.py -q
```

真实材料验收需要 task12 / 报告1 / 报告2 的原始材料与对应任务 ID；接手前先确认这些材料在工作区或附件目录中的位置，不要用合成数据替代真实验收结论。

---

## 9 交接检查单

- [ ] 已读 `design.md` 与 `tasks.md` 全文
- [ ] 已确认后端 `.venv` 可用、定向 pytest 能跑
- [ ] 已确认 `ruleset.json` 版本与 `ruleset.current_version()` 一致
- [ ] 已确认工作区未提交改动范围，不会误暂存
- [ ] 已拿到 task12 / 报告1 / 报告2 材料位置
