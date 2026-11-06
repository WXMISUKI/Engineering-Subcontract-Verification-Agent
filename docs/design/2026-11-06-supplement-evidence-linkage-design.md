# 补件材料跨文档证据关联设计

日期：2026-11-06
状态：D0 基线已确认；S1 事实层硬化、S2 解析链路接入、S3 人员证件三态关系已实施（均待验收）
范围：工程分包独立版补件、人员证件关联、问题证据投影和人工复核

## 意图

补件文件必须成为当前审查问题的可追溯依据。系统需要回答“这份材料属于谁、是什么材料、证明哪个字段、与原有材料是否一致”，而不是只为新文件生成一条泛化的有效期或读取提示。

## 已确认的根因

当前补件会进入任务的有效材料集合，并按主体传给 A2 核查；但各核查内核独立生成 finding，缺少持久的“人员—岗位—证件—字段—页码”关系。`evidence_json` 只是单条问题的结果投影，没有共享的跨材料事实和关系状态，因此新证书不能影响原有 A2-05 人员证件对应问题。

## 目标

1. 每份补件产生不可变材料版本和解析事实，不覆盖原材料或历史结果。
2. 保存文档类型、字段原值、规范化值、线上 OCR 质量、字段置信度和页级/文本级定位。
3. 将名单、岗位、证书、身份证、任命和社保等材料建立跨文档关系，明确 `matched`、`conflict`、`unresolved` 三态。
4. 将支持、冲突和无法确认的证据投影到同一个 finding，列出具体材料、页码、字段和原因。
5. 低质量或关键字段缺失时进入人工复核，不猜测人员身份，不重复 OCR 旧页面。
6. 补件重跑只更新受影响问题；历史版本、AI 原始结果和人工决定保持可读。

## 非目标

- 本阶段不新增独立的大模型智能体。
- 本阶段不把任务附件写入公共知识库。
- 本阶段不训练专用 OCR 或证件模型。
- 本阶段不修改生产服务、原任务或原知识库。

## 已确认的基线决策（2026-11-06）

| 编号 | 决策 | 结论 |
| --- | --- | --- |
| D-1 | 事实写入事务点 | 在 `_finish_ingest` 内，材料解析成功字段赋值后、`material.parsed` 事件前，用同一 session 写入；抽取失败记录 `facts_failed` 降级并继续提交解析，事实可幂等重放 |
| D-2 | 失败页和质量可疑页 | 解析成功的质量可疑页可产生候选事实并标记 `review_required` / `unreadable`；解析失败的材料不生成事实；任何情况都不自动重跑 OCR |
| D-3 | 删除与保留 | 本阶段保留 `ON DELETE CASCADE`，任务删除视为用户授权的合法清除；永久审计留存另立变更 |
| D-4 | 并发幂等 | `page` 改为 `NOT NULL DEFAULT 0`（0 表示材料级/无页码），增加 `(material_id, parse_batch_id, field_path, page)` 唯一约束，并捕获 `IntegrityError` 视为重复 |

D-4 已在 S1 实施（2026-11-06）：`page` 为非空列且默认 0，四列唯一索引 `uq_bs_material_fact_batch_field_page` 兜底并发；写入层逐行 savepoint，仅在确认冲突键已存在时把 `IntegrityError` 视为重复。验证记录见 `openspec/changes/bs-supplement-evidence-linkage/verification.md`。

## 方案选择

| 方案 | 说明 | 结论 |
| --- | --- | --- |
| A | 在现有智能体内增加事实抽取、证据关系和确定性一致性校验；AI 只负责抽取候选和解释 | 采用。审计性最好，兼容现有 A2 和补件流程 |
| B | 新建独立证件关联智能体服务 | 暂不采用。增加状态、版本和服务间一致性成本 |
| C | 训练自定义证件分类/抽取模型 | 后续评估。当前样本量不足，不能替代跨文档关系校验 |

## 处理链路

```text
上传补件
  → 内容 hash / 材料版本
  → 文档类型候选与页范围
  → 线上 OCR 与字段事实抽取
  → 原值/规范化值/置信度/页码定位
  → 主体绑定与人员证件关系解析
  → 确定性一致性校验
  → finding 证据投影
  → 人工复核或自动进入下一状态
  → 绑定 review snapshot 的报告
```

## 建议的数据边界

第一阶段优先复用现有 `BsMaterial`、`BsFinding.evidence_json` 和审查版本，不立即引入大范围迁移；新增结构应保持向后兼容：

```text
BsMaterialFact（已落地）
  material_id, task_id, generation_id, page(0=无页码), field_path, quote
  raw_value, normalized_value, confidence, quality, source_kind
  parse_batch_id, schema_version, extractor_version
  content_sha256, material_version

EvidenceRelation
  subject_id, person_key, role, relation_type
  left_fact_id, right_fact_id
  status, reason, created_in_generation

FindingEvidenceLink
  finding_id, fact_id/relation_id, evidence_role
  support | contradiction | unreadable | expected
```

旧数据没有这些事实时保持为空，不能回填伪造关系。新补件必须使用新的材料版本和当前审查代。

## 关系校验规则

- 人员姓名、企业名称、岗位和证书类型必须分别比较；不能因为文件名或同名自动认定一致。
- 证书编号比较前只做空格、括号、全角字符等可逆规范化，不能猜测数字。
- 交安 B 与交安 C 属于不同证书类型；字段可读且明确不一致时标记 `conflict`，字段被遮挡或缺失时标记 `unresolved`。
- 新材料明确归属其他主体时阻断补件生效；主体未确认时材料可保留，但不能贡献当前主体的通过证据。
- 旧页面质量可疑只生成该页人工核对线索，不自动重跑 OCR，也不把整册质量页附加到每个业务问题。

## 负向和正向示例

模拟交安 B 证的姓名、企业被遮挡且可见交安 C 线索时，系统应输出：

> 补件《交安B证.pdf》第 1 页：姓名和企业名称无法确认，证书类别出现交安 C 线索，不能证明其为项目经理陶瑜的交安 B 证；原问题依据为报审册第 22、28 页，请补充本人清晰证书或正式更正材料。

同一人员的清晰交安 B 证若姓名、企业、类别和编号均能与名单一致，则关系为 `matched`，原 A2-05 应重新投影为已解决或仅保留仍未解决的其他差异，并列出新旧材料两侧证据。

## 验收分层

1. **事实层**：材料类型、字段原值/规范化值、页码和质量状态正确保存。
2. **关系层**：同人、不同人、证书类别冲突、同名歧义、遮挡缺字段均得到正确三态。
3. **问题层**：A2-05 同时展示原名单、原证书和补件证书的支持/冲突/无法确认依据。
4. **版本层**：旧结果、补件代、新 finding、AI 原始结果和人工决定互不覆盖。
5. **工作台层**：人工复核页可查看补件影响的问题、具体页码和字段原因。
6. **报告层**：关键关系未人工确认时，正式报告继续阻断或明确标记范围不完整。

## 验收样本

- 当前中铁二十四局任务的原始 91 页报审材料作为基线。
- `交安B证.pdf` 作为负向/不完整补件：不得猜测姓名，不得误判为正确项目经理证书。
- 同一项目、同一人员、原报审册未包含的清晰交安 B 证作为正向补件；若当前工作区没有该文件，正向验收保持待办。
- 破损 PDF、主体不一致文件、重复补件和补件期间报告读取作为异常反例。

## 发布约束

所有正式报告必须绑定同一 `review_snapshot`、材料版本、规则版本、解析器版本和人工复核状态。补件期间只读展示旧结果；候选结果未达到完整性和复核门槛时不得冒充当前正式结论。

## 外部依据

业界调研见 `docs/research/2026-11-06-supplement-material-idp.md`，其中记录 Azure Document Intelligence、Google Document AI、AWS Textract/A2I 和 OpenAI File Search 的官方资料与版本、grounding、置信度、人工复核及元数据过滤依据。

## S2 解析链路接入（2026-11-06 实施）

收口位置：`service._finish_ingest` 解析成功分支之后、任何提交之前。事实行、材料状态
（`parse_state`/`pages_json`/`degradations_json`）与 `material.parsed` 事件由同一个会话一次性
提交，不存在“材料显示已读完、事实还查不到”的中间态。

- **D-1 同事务**：`persist_material_facts` 在 `material.parsed` 之前调用；该事件的 `commit=True`
  一次性提交全部脏变更，因此不需要额外事务管理。
- **D-2 降级**：抽取抛错时只追加 `{"kind": "facts_failed"}` 到降级记录，材料仍按 `done` 收口，
  不重跑 OCR、不把抽取异常升级为解析失败。降级同时写入局部 `degradations`，保证冲突分支重建
  降级列表时不会丢掉这条记录。
- **S2-D1 代次归属**（选定 A）：查本材料（ZIP 子材料查其容器）的 `material.supplement_linked`
  事件；恰好一个代次才写入 `generation_id`，零个或多个一律留空，不猜归属、不改表结构。
- **③A 冲突分支**（选定 A）：主体冲突/主体未确认分支因为写入点前置而同样保留候选事实；冲突
  记录仍写在 `degradations_json` 并发出 `material.subject_conflict|unresolved` 事件，事实因此
  只是候选证据，不构成有效通过证据。
- **可观测**：`material.parsed` 载荷新增 `facts_inserted`（本次真正新增行数，重放为 0，降级为
  `null`），用于区分“没抽到”和“没抽成”。
- **不做**：不自动补读可疑页、不在本阶段改写 finding、不新增材料表字段、不改历史任务结论。

验证见 `openspec/changes/bs-supplement-evidence-linkage/verification.md` 阶段二。

## S3 人员证件三态关系（2026-11-06 实施）

实施范围：新增 `bs_evidence_relation` 表与 `relation_resolution.py`，在 `_finish_ingest` 事实写入之后、
主体冲突检查之前解析关系；关系与事实、材料状态、`material.parsed` 同 Session 提交。

- **只消费已落库事实**：证书侧读 `bs_material_fact` 的最新批次，名单侧复用 `extract_management_roster`
  与 OCR 布局线索（`roster_b_entries`）；本阶段不发起任何 OCR、不补齐缺失字符，也不重跑历史批次。
- **三态判定**：姓名、主体、企业名称、岗位应交证书类别（项目经理→B、安全员→C、企业负责人→A）
  与名单证书编号逐项比较；任一冲突为 `conflict`，无冲突但有无法确认项为 `unresolved`，全部一致才
  `matched`，并保存两侧页级依据与事实行号。
- **同名不等于同人**：同一姓名出现在多个非空主体的名单记录或证书页时直接 `unresolved`，保留候选关系
  供人工确认，不按文件顺序或流水号择一。
- **质量存疑不判通过**：证书页质量为 `review_required`/`unreadable` 时新增 `evidence.quality` 维度并
  降为 `unresolved`，即使字段一致也只作为人工复核线索。
- **append-only**：`relation_key` 由任务、主体、人员、两侧页码、事实行号、状态与解析器版本组成；
  重复解析命中同一键则跳过，事实批次变化则追加新行，历史关系不更新、不删除。
- **可观测与降级**：`material.parsed` 载荷新增 `relations_inserted`；关系解析抛错只追加
  `relations_failed` 降级，不影响已读出的文本、事实与 `material.parsed`。
- **不做**：不改写 finding、不做 A2-05 投影、不改复核界面与报告门槛，也不改动历史任务结论。

验证见 `openspec/changes/bs-supplement-evidence-linkage/verification.md` 阶段三。

## 已并入的变更（2026-11-06 收敛）

原 `openspec/changes/bs-personnel-evidence-trace`、`openspec/changes/bs-supplement-real-closure` 的范围已并入本变更；仓库只保留 `openspec/changes/bs-supplement-evidence-linkage/` 作为当前规格、任务与进度的唯一权威来源。

### 来自 personnel-evidence-trace 的既有决策

1. 定位分三级：文件/页、文本片段、经校验的坐标；只有坐标经校验和转换才能高亮，模型输出不是原件证明。
2. 名单字段必须有可信同行关系；同名不保证身份唯一，不能确认时给出候选来源并保持不可判定。
3. 问题描述只包含对象、字段、具体原因和可用来源，不把全文件质量页列表拼进每条业务问题。
4. 首次识别后质量可疑或字段无法确认的页面直接作为人工复核线索，不自动重复识别或旋转补读；不把质量可疑等同于已证实模糊、遮挡或缺件。
5. AI 核查批次上限定为 32 条：真实材料实测 64 条批次触发 120 秒超时，32 条约 20 秒返回；保留后台执行、检查点、有限拆批与人工兜底。拆批必须保留全部条目及证据，不把部分范围冒充完整结论。
6. 证据存储优先复用现有能力；新增字段或迁移须先验证兼容性，不同用户和主体的证据不得混用。

### 来自 supplement-real-closure 的既有结论

- 设计约束（冻结基线、候选与公开读分界、代次指纹、范围回退、自动生效与任务收口分离、人工意见继承、报告代次一致、隔离迁移）的落点见本文「补件重核现状与差距」与 `verification.md` 附录。
- 已复现并修复：基线混入补件、报告误读取、跨计划覆盖、影响项回执复用、当前结果误标历史版本、处理中的差异对比。
- 仍未实现：候选与基线隔离、满足条件后自动生效、失败恢复、报告与人工意见一致性。因此该部分不得标记为完成。

### 补件重核现状与差距（源码与既有证据审查，未执行重跑）

1. 补件入口设置 supplementMode：选文件、创建补件计划、进入补件编辑并上传，材料准备后调用 supplement-rerun；普通材料编辑走 material-edit，两条入口不等价，不能由前者推出「新一轮结果准备完成后原子切换」已实现（`BlockchainSupervisionWorkbench.jsx` 约 305、424、932、1903 行）。
2. `plan_supplement_generation` 按材料类别、条目、主体生成并保存计划；`supplement_impact.py` 是静态类别到核查组映射，未知类别全量回退、信息不足按组回退。这是范围规划能力，不证明规则依赖表完整。
3. `enter_supplement_edit` 保存父代引用并保留当前 findings，但把任务切回 setup、清空当前参考运行指针；`rerun_task` 在重核激活时先保存快照，再删除范围内旧结论。因此稳定旧结果与独立候选并未同时存在。
4. `_inherit_review` 按主体、条目、材料、页码配对继承人工结论；材料人工确认另有独立存储，须一并审计，不能仅凭字段一致认定业务依赖未变。
5. 前端已有历史版本列表、只读摘要与对比，后端提供历史与差异接口；补件准备、失败、中断、恢复及全部浏览器入口的无遗漏展示尚无验收证据。历史报告只在快照存在且权限与相应检查通过时可读。
6. 补件计划的 `completed` 在任务收口时更新，不等同机器核查完成或当前结果自动生效；当前报告按当前结果和运行状态守门。
7. 合成材料 HTTP 流已覆盖补件、重核、报告、人工处理和收口（模拟模型与映射）；旧隔离样本的人工回执是技术测试数据，不是业务审核。

主要风险：同一工作区未证明同时维持稳定旧结果与独立候选；补件主体、材料类型、追加/替换、范围映射与未受影响结果继承需端到端一致，须防旧依据或旧人工意见错误沿用；文件读取、模型调用、进程重启与数据库提交跨多步，需明确重复提交、并发与失败恢复语义；历史版本号与当前审查代不必然一一对应。

## 事实批次身份与取值白名单（迁移自原 D0 基线）

- 批次标识 = `content_sha256[:32] + "-" + extractor_version`，截断至 64 字符。同一内容与同一抽取器版本进入同一批次；内容或抽取器变化产生新批次。
- `source_kind` 白名单：`online_ocr_text`、`local_ocr_text`、`embedded_text_layer`。
- `quality` 白名单：`readable`、`review_required`、`unreadable`。
- 版本常量：抽取器 `EXTRACTOR_VERSION = material-facts.v2`；关系解析 `RESOLVER_VERSION = person-cert-relation.v3`；事实 schema `FACT_SCHEMA_VERSION = material-facts.v1`；关系 schema `RELATION_SCHEMA_VERSION = evidence-relation.v1`。

## 实施约束（沿用原交接书，不得放宽）

1. 按阶段实施：每个阶段结束须报告测试范围、失败项与未验证事项，取得用户确认后才进入下一阶段。
2. 保留历史材料、审查快照与人工决定；不得擅自修改用户原任务、原知识库或生产数据。
3. 业务 OCR 全程线上；质量可疑或字段无法确认的页面首次读取后直接交人工复核，不自动反复识别或旋转补读。
4. 真实验收使用隔离副本与测试账号；技术回执、单元测试、合成 HTTP 流不得冒充业务专家结论或生产验证。
5. 只做只读浏览器验收时不得点击采纳、忽略、生成报告、补件或重新核查；任何写操作需单独授权。
6. 中文沟通；遵循仓库既有规范与 OpenSpec；保留正式回归测试，删除一次性脚本、临时验证文件与验收截图。
