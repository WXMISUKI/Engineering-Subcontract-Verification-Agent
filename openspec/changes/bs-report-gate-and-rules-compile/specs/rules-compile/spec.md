## ADDED Requirements

### Requirement: 规则卡七道校验

系统 SHALL 在规则进入发布候选前执行 source_text_anchor、unit、three_state、numeric_direction、clause_coordinate、duplicate、floor_relation 七道校验。

#### Scenario: 校验通过
- **WHEN** 规则卡七道校验全部通过
- **THEN** 生成可审计的编译结果，状态为 accepted

#### Scenario: 校验失败
- **WHEN** 任一硬校验失败
- **THEN** 规则卡不得进入发布候选，并返回逐项错误

#### Scenario: floor 关系不确定
- **WHEN** 规则卡与 floor 关系无法确定
- **THEN** 状态为 pending_human_compare，不得自动发布

### Requirement: 发布候选必须人工确认

系统 SHALL 只生成 `pending_human` 发布候选快照，不得自动写入正式规则集。

#### Scenario: 编译通过但未人工确认
- **WHEN** 发布候选快照已生成
- **THEN** `published=false`、`writeback_performed=false`，正式规则集不变

#### Scenario: 发布身份不完整
- **WHEN** basis_snapshot_id、ruleset_version、profile_code 或 source_manifest_hash 缺失
- **THEN** 返回 rejected，不生成候选快照

### Requirement: 规则回灌不破坏历史任务

系统 SHALL 保持现有 `ruleset.py` 和历史任务的规则版本读取兼容。

#### Scenario: 历史任务读取
- **WHEN** 回灌规则编译能力后读取历史任务
- **THEN** 历史任务仍使用其冻结的规则版本和依据快照，不被当前规则覆盖
