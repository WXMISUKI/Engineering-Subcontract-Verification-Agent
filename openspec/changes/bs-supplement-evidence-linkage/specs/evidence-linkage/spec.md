## ADDED Requirements

### Requirement: 补件字段事实可追溯

系统 SHALL 为补件保存材料版本、字段原值、规范化值、质量状态、解析版本和页级原文定位；不得覆盖原始材料或历史审查结果。

#### Scenario: 补件产生新事实版本

- **WHEN** 用户为已存在任务上传补充材料
- **THEN** 系统 SHALL 创建新的材料事实版本并保留原材料、原始解析和历史 finding

### Requirement: 人员证件关系可判定

系统 SHALL 比较人员姓名、岗位、企业、证书类别和证书编号，并将关系标记为 matched、conflict 或 unresolved；仅在主体唯一且证书页质量可读时才可判为 matched。

#### Scenario: 字段一致且可定位

- **WHEN** 名单和证书关键字段一致且均有页级依据
- **THEN** 系统 SHALL 标记关系为 matched 并保存双方依据

#### Scenario: 证书类别或字段明确冲突

- **WHEN** 补件字段可读且与目标人员证件要求明确不一致
- **THEN** 系统 SHALL 输出冲突字段、补件页码和原有依据页码

#### Scenario: 关键字段遮挡或缺失

- **WHEN** 姓名、企业或证书类别无法可靠确认
- **THEN** 系统 SHALL 输出无法确认及具体缺失字段，不得猜测人员身份

#### Scenario: 同名人员跨主体

- **WHEN** 同一姓名出现在多个非空主体的名单记录或证书页中
- **THEN** 系统 SHALL 将关系标记为 unresolved 并保留候选依据，不得按文件顺序或流水号择一认定

#### Scenario: 证书页质量存疑

- **WHEN** 证书页字段一致但页面质量为 review_required 或 unreadable
- **THEN** 系统 SHALL 将关系标记为 unresolved，只作为人工复核线索，不得作为通过证据

### Requirement: 关系记录只追加

系统 SHALL 以 append-only 方式保存人员证件关系；同一任务重复解析只追加新版本，历史关系行保持可读，不更新、不删除。

#### Scenario: 事实批次变化后重算关系

- **WHEN** 同一任务的材料事实产生新批次并重新解析关系
- **THEN** 系统 SHALL 追加新的关系行并保留旧行，供复核对比

#### Scenario: 关系重放

- **WHEN** 事实批次与解析器版本未变化时重复解析关系
- **THEN** 系统 SHALL 命中同一关系键并跳过写入，不产生重复行

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

### Requirement: 材料读取收口携带事实批次

系统 SHALL 在材料读取收口的同一事务内写入该批次材料事实与 `material.parsed` 事件；事实写入失败不得导致已成功的解析结果丢失，也不得自动重新解析。

#### Scenario: 解析成功时原子收口

- **WHEN** 材料解析成功并准备写入 `material.parsed`
- **THEN** 系统 SHALL 在同一事务内提交材料解析状态、页级内容、材料事实批次与 `material.parsed` 事件

#### Scenario: 事实抽取失败降级

- **WHEN** 材料解析成功但事实抽取抛错
- **THEN** 系统 SHALL 仍提交解析结果与 `material.parsed` 事件，在材料降级记录中追加 `facts_failed`，且不自动重跑 OCR

#### Scenario: 解析失败不产生事实

- **WHEN** 材料解析失败
- **THEN** 系统 SHALL 不写入任何事实行，并以 `material.parse_failed` 收口

#### Scenario: 质量可疑页面只产生候选事实

- **WHEN** 页面可读但质量可疑
- **THEN** 系统 SHALL 生成候选事实并标记为 `review_required` 或 `unreadable`，不得将其当作通过证据

### Requirement: 事实批次按代次归属

系统 SHALL 记录材料事实所属补件代次；无法唯一确定代次时 SHALL 保留事实并标记代次未知，不得推测归属。

#### Scenario: 补件材料的事实归属

- **WHEN** 材料由补件链接创建并解析成功
- **THEN** 系统 SHALL 将事实批次关联到该链接记录的补件代次

#### Scenario: 代次无法唯一确定

- **WHEN** 同一材料存在多个互斥的补件链接或链接缺失
- **THEN** 系统 SHALL 写入事实并将代次标记为未知，供后续人工复核