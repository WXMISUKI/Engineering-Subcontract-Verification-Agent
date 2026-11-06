## ADDED Requirements

### Requirement: 补件字段事实可追溯

系统 SHALL 为补件保存材料版本、字段原值、规范化值、质量状态、解析版本和页级原文定位；不得覆盖原始材料或历史审查结果。

#### Scenario: 补件产生新事实版本

- **WHEN** 用户为已存在任务上传补充材料
- **THEN** 系统 SHALL 创建新的材料事实版本并保留原材料、原始解析和历史 finding

### Requirement: 人员证件关系可判定

系统 SHALL 比较人员姓名、岗位、企业、证书类别和证书编号，并将关系标记为 matched、conflict 或 unresolved。

#### Scenario: 字段一致且可定位

- **WHEN** 名单和证书关键字段一致且均有页级依据
- **THEN** 系统 SHALL 标记关系为 matched 并保存双方依据

#### Scenario: 证书类别或字段明确冲突

- **WHEN** 补件字段可读且与目标人员证件要求明确不一致
- **THEN** 系统 SHALL 输出冲突字段、补件页码和原有依据页码

#### Scenario: 关键字段遮挡或缺失

- **WHEN** 姓名、企业或证书类别无法可靠确认
- **THEN** 系统 SHALL 输出无法确认及具体缺失字段，不得猜测人员身份

### Requirement: 问题证据包含补件

系统 SHALL 将补件的支持、冲突或无法确认依据投影到受影响 finding，并保留原有依据。

#### Scenario: 补件影响既有问题

- **WHEN** 补件属于某主体并命中已有问题的目标字段
- **THEN** 系统 SHALL 在该 finding 中同时展示原材料依据和补件依据

### Requirement: 版本和人工复核安全

系统 SHALL 在补件重跑期间保留旧结果只读视图；关键关系未达到复核门槛时，不得生成误称为已确认的正式报告。

#### Scenario: 关键关系未复核

- **WHEN** 证件关系为 unresolved 或 conflict 且尚未完成人工处理
- **THEN** 系统 SHALL 保持人工复核门槛并阻止确认版报告
### Requirement: 材料事实只追加

系统 SHALL 以 append-only 方式保存材料事实：事实行写入后不得更新或删除，重新解析只追加新批次，历史批次保持可读；同一批次重放必须幂等。

#### Scenario: 同一材料重新解析

- **WHEN** 同一材料的内容或抽取器版本发生变化并再次解析
- **THEN** 系统 SHALL 追加新批次事实，历史批次行保持原值与可读，不得覆盖或删除

#### Scenario: 同批次重放

- **WHEN** 同一内容与同一抽取器版本重复解析
- **THEN** 系统 SHALL 复用同一批次标识并不重复插入事实行

#### Scenario: 来源与质量取值

- **WHEN** 写入事实行
- **THEN** 系统 SHALL 只接受白名单内的来源类型与质量状态，并按页面实际来源区分线上 OCR、本地 OCR 与文本层
