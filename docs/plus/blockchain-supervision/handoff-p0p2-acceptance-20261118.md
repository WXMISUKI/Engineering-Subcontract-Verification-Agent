# 分包核查 P0–P2 交接文档（2026-11-18）

## 0. 接手先读

- 仓库：`c:\project\nanjin\Engineering-Subcontract-Verification-Agent`
- 分支：`featurelyl1118`
- 服务：Docker Compose，前端 `http://127.0.0.1:8089`，后端容器
  `engineering-subcontract-verification-agent-backend-1`
- 本轮未提交 git；仓库里同时存在更早批次留下的未提交改动，接手时先用
  `git status --short` 区分“本轮 P0–P2 改动”和“前序批次改动”，不要整仓 `git add .`。
- 本文档只描述本轮 P0–P2 的事实链路修复、双路抽取和任务 12 验收现状。

## 1. 当前结论（一句话）

P0–P2 代码已实现并部署到 8089，任务 12 已 `done` 并导出确认稿；报告抬头事实
（建设单位/总包/分包/含税金额/范围）已正确。仍有 7 项“无法核实”是真实材料/主体
问题，不是代码崩溃；其中 6 项 A2 的根因是主体表仍为“上海建工”，需要补件计划+
主体更正后重核才能消掉。

## 2. 当前验收状态

### 2.1 任务 12

- `BsTask.id=12`，状态 `done`
- 主体表 `BsSubject.id=12` 仍为 `上海建工集团股份有限公司`（错误，实际总包）
- 材料：
  - `material 12`：嘉松合同.pdf，role=`general`（总包合同）
  - `material 14`：吊装专业工程.pdf，role=`sub`（专业分包合同）
- 重新核查后分组结论：
  - 报审资料齐全性：hit 15 / miss 0 / undecidable 1
  - 签字盖章：undecidable 3
  - A1：miss 3 / undecidable 22
  - A2：undecidable 6
  - 内部信用事故：undecidable 1
- 正式输出：`confirmed_output_ready=true`，已导出确认稿
  `artifacts/bs_task12_report_v14_confirmed.docx`

### 2.2 报告已核实字段

- 建设单位：上海公路投资建设发展有限公司
- 总包单位：上海建工集团股份有限公司
- 分包单位：上海兴友工程机械有限公司
- 含税签约合同价：455.759871 万元
- 不含税：418.128322 万元，税率 9%
- 分包范围：预制立柱、盖梁、箱梁、板梁、挡墙等构件安装相关内容
- 工程名称：嘉松公路越江新建工程

### 2.3 测试

本轮最终回归：

```powershell
cd backend
python -m pytest tests/test_bs_contract_party_extraction.py `
  tests/test_bs_contract_party_evidence.py `
  tests/test_bs_contract_party_router.py `
  tests/test_report_basic_facts.py `
  tests/test_report_document.py `
  tests/test_report_template_v13.py `
  tests/test_report_template_v14.py `
  tests/test_bs_report_version_http.py `
  tests/test_bs_task_report_readiness.py `
  tests/test_bs_a1_kernels.py `
  tests/test_bs_a1_illegal_subcontract.py `
  tests/test_bs_a1_transfer_scope.py `
  tests/test_bs_a2_kernels.py `
  tests/test_bs_certificate_number_evidence.py `
  tests/test_bs_material_facts.py `
  tests/test_bs_ingest_material_facts.py `
  tests/test_bs_execution_summary.py `
  tests/test_bs_contract_dual_extraction.py `
  tests/test_bs_page_text_candidates.py `
  tests/test_bs_ai_review_jobs.py `
  tests/test_bs_background_ai_review.py `
  tests/test_bs_rule_source_coverage.py `
  tests/test_bs_ruleset_deployment.py `
  tests/test_business_report.py `
  tests/test_qualification_summary.py `
  tests/test_bs_export_summary.py -q
```

结果：`666 passed`。

## 3. 本轮修改清单（按问题域）

### 3.1 合同角色与主体

- `backend/app/plus/blockchain_supervision/extract.py`
  - `party_name_regex` 支持 `（全称）`、`发包单位/承包单位/总承包单位`。
  - `extract_subcontractor_name` 只认“分包人/分包单位/分包单位名称”，不再把泛化“乙方”
    当分包人；多候选返回 `None`。
  - 新增 `party_scoped_pages`：附属协议/补充协议/附件/委托代发工资/廉政责任书/
    文明施工/安全生产/质量责任书出现后停止主体角色抽取，避免附件反转甲乙方。
  - 新增 `is_placeholder_party_name`，过滤“专业公司/劳务公司”等占位词。
  - `SUBCONTRACTOR_PARTY_ROLES` 公开常量，供 a2_check 复用。
- `backend/app/plus/blockchain_supervision/data/extract_matchers.json`
  - 主体正则排除 `：/:`，修复签署页把“法定代表人或委托代理人：…”误当主体。
  - 新增 `party_placeholder_names`。
- `backend/app/plus/blockchain_supervision/contract_party_evidence.py`
  - 证据保留 `company_display`（原文，含“（集团）”），匹配仍用 `company`（归一化）。
  - 新增 `contract_chain_consistency`：总包合同承包人 vs 分包合同发包人三态。
- `backend/app/plus/blockchain_supervision/a2_check.py`
  - `subcontractor_name_evidence` 与 extract 共用角色判据和附件排除。
- `backend/app/plus/blockchain_supervision/service.py`
  - `repair_auto_subject_names` / `_repair_subject_name_if_safe`：仅 setup/补件、
    无 `subject.name_confirmed`、唯一明确分包人、已自动归并时修复主体名，写
    `subject.name_repaired`。
  - `backfill_contract_facts`：历史任务重新核查前幂等补写 `contract.*` 事实。
  - `run_contract_dual_extraction`：双路候选服务入口。
  - `/start` 时先修复主体、补写事实，再校验材料。

### 3.2 报告事实与金额

- `report_basic_facts.py`
  - 按材料 role 解释主体：总包合同发包人=建设单位，承包人=总包；分包合同
    发包人=总包，分包人=分包。
  - 新增分包工程名称、分包工程承包范围和内容、总包工程名称、含税/不含税
    签约合同价、税率；金额统一万元。
  - 新增 `_contract_field_facts` 同源落库。
  - 金额合并按“分”量化比较，避免 float 尾差；相等时优先返回万元口径。
  - 建设单位名称“多数一致 + 短名为长名前缀 + 差异≤4 字”时按多数完整名确认，
    真正不同公司仍保留冲突。
- `a1_check.py`
  - 合同金额优先含税；修复“签约合同价…；不含增值税的签约合同价…”时把后文
    “不含增值税”反向贴到首个金额的问题。
  - 金额正则允许中文大写金额，支持 `人民币(大写)…(¥4557598.71元)`。
  - V16-02 以合同材料在场为对照，不再要求正文出现“总包合同”字样；缺原则对照
    时明确写“缺原则对照”。
- `data/transfer_rules.json`
  - T15-01/T15-02 的 `sub_scope`/`subcontract_scopes` 增加
    “分包工程承包范围和内容/分包工程承包范围”。
- `reporting.py`
  - 合同角色证据优先，单一 subject 不再覆盖合同分包人。
  - 金额用 `packet_facts.subcontract_amount`，报告显示“万元（含税）”。
  - `build_opinion` 同步使用报告事实和显示名。
  - 建设单位多数证据/冲突写入范围说明。
- `report_document.py` / `report_template_v13.py` / `report_template_v14.py`
  - 抬头新增建设单位/总包单位/分包单位三行。
  - v14 新增“8. 本次核查范围与限制”。
  - v13 修复“只要提到总包合同就写未上传总包合同”的误报。

### 3.3 事实落库与双路抽取

- `material_facts.py`
  - `EXTRACTOR_VERSION=material-facts.v3`。
  - 新增 `contract.project_name / total_project_name / item_name / scope /
    party.* / amount.incl_wan / amount.excl_wan / tax_rate_pct`。
  - 新增 `persist_contract_candidates`，候选写
    `contract.<field>.candidate`，quality=`review_required`，批次含
    prompt/schema/模型指纹。
- `contract_extraction.py`（新增）
  - 规则 + schema 约束 LLM 双路；`select_contract_pages` 按关键词选页。
  - LLM 输出兼容 `{"fields":…}` 和顶层平铺两种形态。
  - 证据必须 `material_id/page/quote` 且 quote 逐字回验，否则丢弃。
  - 裁决：两路一致 `matched`；仅 LLM `provisional`；仅规则 `matched` +
    `single_source`；冲突不选边。
- `data/config.json`
  - `contract_dual_extraction.enabled=true`（本次验收按业主确认全量开启）。
  - `max_chars=16000`，`model_code=null`（用当前任务模型）。
  - **正式运行建议改回按任务开关，避免成本随合同量线性增长。**

### 3.4 可观测性与部署

- `execution_summary.py`（新增）：执行/AI 覆盖/读取质量/人工处置统一投影；
  新增 `scope` 块（未覆盖规则 id、未启用功能）。
- `rule_source_coverage.py`、`page_text_candidates.py`、`ai_review_jobs.py`
  （均为前序/本轮共同产物）：规则来源覆盖、页级补读候选、AI 持久任务队列。
- `ruleset.py`：新增 `verify_deployment_fingerprint`，部署前只读比对版本/
  manifest 哈希。
- `schema_migrations.py` / `main.py`：新增表需要 `init-db` 补齐。
- 前端：
  - `FindingDetail.jsx`、`ReviewWorkbenchLayout.jsx`、`ModelSelect.jsx`
    等有前序批次改动，本轮未做新的页面功能开发。

## 4. 已执行的部署与验收动作

1. 备份数据库：
   `backups/bs_review_pre_p0p2_20261118.sql`（约 104MB，未入 git）。
2. `docker compose build backend`。
3. `docker compose run --rm --no-deps backend python -m app.standalone.manage init-db`
   —— 补齐 `bs_ai_review_job`、`bs_page_text_candidate` 等新表。
4. `docker compose up -d --no-deps backend`。
5. 任务 12：`/rerun` → 五个分组 `/check*` → `/ai-review` → 处置无法核实 →
   `/complete` → `/report/export?fmt=docx&stage=confirmed&template_id=v1.4`。
6. 报告正文已核对：建设单位/总包/分包/含税金额/第 8 节均存在。

## 5. 已知遗留问题（按优先级）

### P1：任务 12 的 A2 六项仍是“无法核实”

- 根因：`BsSubject.id=12` 名称仍是“上海建工集团股份有限公司”，而合同分包人是
  “上海兴友工程机械有限公司”。A2-01～A2-06 均因主体冲突无法判定。
- 本轮处置：按“主体冲突”采纳为无法核实，写入复核说明，不伪装通过。
- 彻底解决路径：
  1. 生成补件局部核查计划（`/supplement-plan`）。
  2. 进入补件编辑态，把主体名改为“上海兴友工程机械有限公司”
     （或走正式主体更正流程）。
  3. 重新 `/start` + 五个分组 + `/ai-review`。
  4. 重新 `/complete` + 导出确认稿。
- 注意：主体改名会改变输入版本，旧 AI 结论会失效，必须重跑 AI。

### P1：主体改名的产品缺口

- 核查开始后 `/subjects/{id}/name` 返回 409；`/supplement-edit` 又要求先有补件
  计划。结果是“已知主体错但无法轻量更正”。
- 建议：增加“主体更正”专用流程（仅无人工确认、证据唯一、写审计事件），
  或在补件编辑入口允许无计划的主体更正；不要直接改库。

### P1：AI 后台任务恢复

- 重启后端会丢 `ACTIVE_JOBS` 内存态；旧 `review_run_id` 的 AI job 可能变成
  `unavailable`，再提交报“已有后台任务记录无法核实”。
- 本次恢复方式：删除 `BsAiReviewJob` 中该 `review_run_id` 的旧行 + 整轮
  `/rerun` 重建。**这是应急手段，不应作为常规操作。**
- 建议：补一个正式的“AI 任务恢复/重置”入口或启动巡检，把过期 running 任务
  标记 interrupted 并允许重提；同时补集成测试覆盖“重启后恢复”。

### P2：建设单位多数证据规则的边界

- 当前规则：多数一致、短名是长名前缀、差异≤4 字、长名至少 2 处。
- 已用签署页印章人工核验“上海公路投资建设发展有限公司”，但规则本身仍可能
  在“名称相近但确实是两家公司”时误合并。
- 建议：把“多数证据确认”写进报告限制说明，并在人工复核界面展示参与证据；
  真实不同公司仍保留冲突。

### P2：双路 LLM 默认全量开启

- 本次按业主确认开启；正式运行会产生持续模型调用成本。
- 建议：改为任务级开关或按合同类型灰度；保留 `contract.*.candidate` 与规则
  的 `single_source` / `conflict` 统计，作为质量评估指标。

### P2：报告第 8 节与逐规则适用性

- 目前第 8 节有范围限制，执行摘要有 `scope` 汇总；但“每条规则为什么没查”的
  明细还没有作为报告附件输出。
- 建议：把 `rule_source_coverage` 明细接进报告附件或工作台抽屉。

### P2：前端字段同步

- 报告 docx 已新增建设单位/总包单位；工作台页面仍主要使用旧字段名。
- 如需页面同步展示三行抬头，改前端字段映射即可，后端字段已提供。

### P3：测试环境 SECRET_KEY

- `test_bs_page_text_candidates.py` 已加 autouse fixture 设置测试 `SECRET_KEY`。
- 其他新增测试如涉及 JWT/签名，也应在测试内显式设置，不依赖外部环境。

## 6. 运行与回滚手册

### 6.1 只跑后端定向测试

```powershell
cd backend
python -m pytest tests/test_bs_contract_party_extraction.py tests/test_report_basic_facts.py -q
```

### 6.2 部署

```powershell
docker compose build backend
docker compose run --rm --no-deps backend python -m app.standalone.manage init-db
docker compose up -d --no-deps backend
docker ps --filter name=engineering-subcontract-verification-agent-backend-1
```

### 6.3 规则指纹

```python
from app.plus.blockchain_supervision import ruleset
print(ruleset.verify_deployment_fingerprint())
```

### 6.4 回滚

1. 代码回滚到上一提交/备份分支。
2. `docker compose build backend && docker compose up -d --no-deps backend`。
3. 如数据库结构或数据需回滚，使用
   `backups/bs_review_pre_p0p2_20261118.sql`，先确认目标库再导入；
   不要在未确认目标库时直接 `mysql <`。
4. 任务 12 的报告/事件属于业务数据，回滚数据库会丢失本轮验收痕迹，需业务确认。

## 7. 接手后的建议顺序

1. 先读本文 + `docs/design/2026-11-17-v14-real-test-issue-backlog.md` +
   `docs/design/2026-11-18-review-dimension-and-basis-gap-analysis.md`。
2. 跑一遍第 2.3 的测试，确认 `666 passed`。
3. 只读核对任务 12 报告与 `artifacts/bs_task12_report_v14_confirmed.docx`。
4. 决定是否走“补件计划 + 主体更正 + 重核”消掉 A2 六项。
5. 处理 AI 任务恢复缺口（P1），不要继续用“删 job 行”作为常规恢复。
6. 评估双路 LLM 开启策略与成本。
7. 最后再考虑报告第 8 节明细、前端字段同步等 P2/P3。

## 8. 禁止事项

- 不要整仓 `git add .`；仓库有前序批次未提交改动。
- 不要把 `backups/`、`artifacts/`、`tmp/` 里的临时/大文件提交。
- 不要把密钥、JWT、数据库密码写入文档或日志。
- 不要用“接受无法核实”代替真实材料核对；本次 7 项均已写明依据。
- 不要在没有备份/确认目标库的情况下导入 SQL 或 `init-db` 到生产库。
- 不要为了消掉 A2 直接改数据库主体名；走补件/主体更正流程并留审计事件。

## 9. 关键文件索引

- 合同角色：`backend/app/plus/blockchain_supervision/extract.py`
- 合同主体证据：`contract_party_evidence.py`
- 报告事实：`report_basic_facts.py`
- 报告聚合：`reporting.py`
- 报告文档/模板：`report_document.py`、`report_template_v14.py`
- 金额/规则：`a1_check.py`、`data/transfer_rules.json`
- 事实落库：`material_facts.py`
- 双路抽取：`contract_extraction.py`
- 服务/流程：`service.py`、`router.py`
- 执行摘要：`execution_summary.py`
- 规则指纹：`ruleset.py`
- 本轮新增/修改测试：
  - `backend/tests/test_bs_contract_party_extraction.py`
  - `backend/tests/test_bs_contract_dual_extraction.py`
  - `backend/tests/test_bs_ruleset_deployment.py`
  - `backend/tests/test_report_basic_facts.py`
  - `backend/tests/test_bs_contract_party_evidence.py`
  - `backend/tests/test_bs_material_facts.py`
- 验收产物：`artifacts/bs_task12_report_v14_confirmed.docx`
- 数据库备份：`backups/bs_review_pre_p0p2_20261118.sql`

---

文档生成时间：2026-11-18
当前负责人交接状态：P0–P2 代码与任务 12 验收已完成；A2 主体冲突与 AI 恢复缺口待后续处理。
