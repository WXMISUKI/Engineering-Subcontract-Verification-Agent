# 阶段 1：领域与 API 契约

状态：用户已于 2026-11-12 确认本契约并批准进入阶段 2。阶段 2–6 已完成代码与隔离验收；阶段 6 本轮定向回归 201 项通过，两个相关 OpenSpec strict validation 与 `git diff --check` 通过。Word/PDF 在业务文档路径展示独立范围限制章节及 `scope_limited` 结论。阶段 7.1 业务验收及 7.1a 来源集关联实现已完成隔离验收；SQLite 旧表幂等补列、路由/API/材料 DTO、同事务审计、替换后重新确认、代快照和历史 v1 快照兼容均有回归。服务端开关仍默认关闭，未修改共享数据库或历史运行数据；flag rollout 仍待独立决策。

## 决策摘要

采用“当前核查代事件 + 审查版本快照”方案，不新增数据库表：

1. 当前代由 `review_run_id` 唯一标识。新语义启用的代追加独立的 `review.scope_snapshot_recorded` 来源范围快照事件；业务范围与 `knowledge.review_started` 的知识参考 seed 分开存放。
2. 当前 API 只按精确 `review_run_id`、当前组 `run_id` 和冻结输入指纹读取，不从任意最近事件推断，也不从“没有 finding”推断规则已通过。
3. 创建 `BsReviewVersion` 时，将当前代的范围快照和规则覆盖投影复制到 `snapshot_json.scope_coverage`。历史版本只读该快照，不随当前材料、规则或 feature flag 改写。
4. `BsTaskEvent` 保留事件审计和当前代状态记录的职责；历史审查版本快照是历史 API/报告的读取依据，不以全量事件重放作为唯一历史真相。
5. `BS_OPTIONAL_SOURCE_SCOPE_LIMITATIONS_ENABLED` 服务端开关默认 `false`，独立于 `BS_MOMA_SCOPE_SELECTOR_ENABLED`，并在核查代 seed 中冻结启用状态。运行中切换配置不改变已开始的代。旧代或开关关闭的代保持既有语义。

此方案复用现有 `BsTaskEvent.payload_json`、`reference_review_id` 代指针以及 `BsReviewVersion.snapshot_json`，阶段 1 不需要数据库 schema 迁移。`BsTaskEvent` 本身没有按事件类型强制的领域 schema；新增 payload 必须由应用层按 schema 版本严格校验。

## 领域标识与边界

| 概念 | 契约身份 | 说明 |
| --- | --- | --- |
| 核查代 | `generation_id`，值为当前 `review_run_id` | 与 `options_snapshot_json.reference_review_id` 精确绑定；不能只用任务 ID 或审查版本号代替 |
| 审查版本 | `version_no` | 用户可见的历史版本号；由 `BsReviewVersion` 冻结 |
| 规则身份 | `rule_id` + `ruleset_version` + `ruleset_fingerprint` | 规则 ID 单独不足以证明历史使用了相同定义 |
| 主体范围 | `subject_id`，可空 | `null` 表示规则范围为任务级；按矩阵逐主体适用的规则必须填主体 ID |
| 来源身份 | `source_role` + `source_material_ids` | 只记录任务内来源身份，不保存合同正文、OCR 原文或模型提示词 |
| 总包来源集 | 用户确认同一总包材料关联 | `BsMaterial` 以 task-scoped `general_source_set_id` 和关系版本保存当前关系；setup 阶段通过用户操作建立、扩组、合并或解除，同事务追加包含操作者的审计事件，并将来源集 ID/成员/关系版本冻结到 generation 快照。材料准入只将完整且版本一致的来源集计为一个总包来源；不得按文件名或时间自动合并；材料替换后须重新确认；不得复用表示报审批次的 `packet_id` |
| finding | 现有 `BsFinding` 三态对象 | 只表达 `hit`、`miss`、`undecidable` 业务结论；范围限制不得伪装为 finding |

## 范围与覆盖 DTO

`/todo`、报告 readiness 和版本详情以可选顶层对象 `scope_coverage` 返回同一代投影。它不改变既有 finding DTO、计数或处置回执。本变更的逐规则覆盖集合仅覆盖阶段 0 矩阵中明确依赖可选总包的规则（以及实际启用时的 A1-01）；不声称为所有 A1/A2/A3 规则引入逐项运行跟踪。其他核查组完整性继续由既有 group-level readiness 负责。

```json
{
  "schema_version": "bs-scope-coverage.v1",
  "generation_id": "review-run-id",
  "policy": "optional-source-scope.v1",
  "state": "partial",
  "limitations": [
    {
      "rule_id": "T15-01",
      "subject_id": 42,
      "source_role": "general",
      "source_material_ids": [],
      "source_state": "absent",
      "reason_code": "optional_source_absent",
      "counts_as_finding": false,
      "counts_as_pass": false,
      "blocks_report": false
    }
  ],
  "rules": [
    {
      "rule_id": "T15-01",
      "subject_id": 42,
      "coverage_state": "not_covered_optional_source",
      "execution_state": "not_run",
      "reason_code": "optional_source_absent",
      "finding_ids": []
    }
  ]
}
```

### 字段取值

- envelope `state`：`running`、`available`、`partial`、`incomplete`、`failed`、`unavailable`、`legacy_unknown`。
- `coverage_state`：`covered`、`not_covered_optional_source`、`not_applicable`、`unknown`。
- `execution_state`：`not_started`、`running`、`completed`、`failed`、`partial`、`not_run`。
- `source_state`：`absent`、`processing`、`readable`、`unreadable`、`required_pages_unreadable`、`unlinked`、`conflicted`。
- `required_pages_unreadable` 仅在规则级页依赖能够被准确识别时使用；通用材料读取缺口本身不能证明缺失页属于具体规则的必需页，当前应归为 `unreadable`。
- `reason_code` 使用稳定机器码；界面文案通过本地化映射生成，不由自由文本决定状态。
- `limitations` 只包含已确定为范围受限的规则。`processing` 不等同缺失或终态范围限制，继续走现有处理/报告就绪门禁。
- `rules` 对本变更覆盖的矩阵规则，每条当前适用规则恰有一个覆盖状态；finding 保持独立，可用 `finding_ids` 关联，不在此重复表达业务结果。非本变更目标规则不要求新增逐项覆盖事件。
- `available` 不等于完整覆盖；存在任何 `not_covered_optional_source` 时 envelope 为 `partial`。没有覆盖数据时不得返回空数组并伪装为 `available`。

范围限制项由来源范围快照和冻结规则矩阵投影产生。每项需能追溯核查代、规则、主体、来源角色、来源状态、原因及规则指纹。限制项没有“采纳/忽略”字段，不产生人工处置回执。

## 持久化与读取

### 当前核查代

1. 新语义开关开启时，追加独立 `review.scope_snapshot_recorded` 事件，至少包含 `schema_version`、`generation_id`、`policy`、规则集版本/指纹、材料指纹、适用规则及其来源角色、来源状态、影响主体和范围原因。无依赖规则时也记录完整空限制集合，避免把缺数据误认成“无范围限制”。
2. 当前固定规则 `/check` 入口没有独立的业务运行身份；阶段 2 必须为每次相关组运行创建 `check_run_id`，不能复用知识参考查询的 `run_id`。开始事件 `check.coverage_started` 和终态 `check.rule_coverage_recorded`/`check.failed` 均绑定同一 `generation_id`、`group_id`、`check_run_id` 和输入指纹。
3. `check.rule_coverage_recorded` 至少包含 `schema_version`、`generation_id`、`group_id`、`check_run_id`、输入指纹和该组逐规则覆盖项。它不能复用在线参考查询完成事件，以免混淆“参考资料查询”与“业务规则执行”。
4. 每个任务/检查组在任务行锁或等价原子 CAS 下登记一个当前 `check_run_id`；该登记对应的 `check.coverage_started` 是该组当前运行的权威指针。终态写入也须取得同一互斥保护，并验证其 `check_run_id` 仍是当前值；被新运行替代的旧运行不得写回 finding、coverage 或失败状态。
5. 当前投影通过精确的范围快照事件、每组当前权威 `check_run_id` 对应的开始/终态事件和输入指纹组装。预期组尚未执行时返回 `not_started`/`incomplete`；当前运行开始但无终态时返回 `running`；执行失败保留 `failed`；不得按 sequence 上“最后一条事件”推断运行权威性，也不得因 finding 为空生成 `covered`。
6. 终态覆盖事件应与对应规则结果在同一事务内提交。若现有事务边界暂不能做到，必须校验终态中的 finding ID、规则身份、结果状态及输入指纹与持久结果一致；不一致则为 `unavailable`，不能报告为已覆盖。受影响规则的 finding 更新与终态覆盖记录应优先收敛到同一事务。
7. 事件表没有领域幂等键列；事件生产者需在任务互斥保护下按 `(event_type, generation_id, group_id, check_run_id)` 查询/去重。相同 key 且内容相同的重试返回已记录结果；相同 key 内容冲突或重复快照身份不唯一时 fail closed，并按新 `check_run_id` 重新核查恢复，不能挑选一条后继续放行。
8. 同一代的范围快照事件，或同一代/组/运行标识的开始/终态事件缺失、重复冲突、schema 不支持、代际指针不匹配或输入指纹变化时，投影为 `unavailable`/`incomplete`，不折叠成空限制列表。用户需重新核查或按既有恢复路径处理。

### 历史审查版本

- `_review_version_snapshot` 增加 `scope_coverage`，将本代冻结来源范围、规则覆盖、代标识和规则/材料指纹复制进 `BsReviewVersion.snapshot_json`。
- 版本详情、历史报告和历史导出只读取指定版本的快照；不得回查当前材料或当前规则来填补历史缺字段。
- 旧版本没有 `scope_coverage` 时，响应为 `state: "legacy_unknown"` 或显式 `null`；不能解释为空限制、完整覆盖，也不回写旧 JSON。
- 旧代继续按既有规则及门禁解释。只有新建且 seed 明确冻结 `policy: "optional-source-scope.v1"` 的核查代采用新语义。

## API 兼容

| API 面 | 契约 |
| --- | --- |
| `/findings` | 保持现有三态 finding 列表和计数；不混入范围限制项 |
| `/todo` | 保持现有 `total_undecidable`、材料类别、readiness 等字段；新增可选 `scope_coverage`。范围提示不进入待办分母或 review receipt |
| 报告 readiness | 保持既有 `ok`/阻断原因语义；新增同代 `scope_coverage`。范围限制本身不置 `ok=false`，真实 `undecidable`、执行未完成/失败等原门禁继续生效 |
| 当前报告、意见书及各格式导出 | 使用同一代 `scope_coverage`；范围受限时披露规则和原因，不计入通过数、不表述完整通过 |
| 版本列表/详情 | 返回指定 `version_no` 冻结的 `scope_coverage`；历史无字段保持 `legacy_unknown` |

新语义开启且 coverage 快照/投影缺失或身份不一致时，正式 readiness 必须报告“覆盖记录不可用/不完整”，不能按“零限制”放行。开关关闭或历史代则保持既有行为并标明 `legacy_unknown`，避免对存量任务隐式套用新规则。只有确定存在的范围限制不阻断报告；覆盖记录失效属于执行/审计完整性问题，沿用失败关闭原则。

## 验收断言

1. 对同一代，`/todo`、readiness、报告、意见书、所有导出和历史版本读取相同的范围限制集合及规则覆盖状态；逐规则 coverage 只保证覆盖本变更矩阵中依赖可选总包的规则。
2. 缺少总包只限制矩阵标明依赖它的规则；无关规则的覆盖与执行不受影响。
3. 范围限制数量、findings 数量、finding 状态、采纳/忽略回执互不改变；限制项既不算 finding，也不算符合。
4. 只有范围限制时，其他门槛满足即可正式输出明确受限报告，不能称完整通过；真实 `undecidable` 或执行失败仍按既有门禁处理。
5. 并发启动同组新运行后，旧 `check_run_id` 的迟到完成/失败不得覆盖新代结果；同 key 重试幂等，冲突重复 fail closed。旧任务/旧版本可读且不被回填；补件新代可独立形成新覆盖快照；feature flag 默认关闭且按代冻结。
6. 结构化对象不含合同正文、OCR 全文、模型提示词、凭据或不必要的个人信息。

## 阶段边界

本契约已获用户确认；阶段 2固定规则修复、阶段 3待处理 API/UI、阶段 4统一报告披露与 readiness 门禁、阶段 5补件新代生命周期、阶段 6隔离端到端验收及阶段 7.1a来源集关联均已通过代码与隔离验收。7.1a 增加 nullable 材料列并通过隔离 SQLite 旧表补列验证；未改写共享数据库或历史任务。服务端业务 flag 默认关闭，rollout 是否启用及 7.3候选环境/部署仍需单独决策。
