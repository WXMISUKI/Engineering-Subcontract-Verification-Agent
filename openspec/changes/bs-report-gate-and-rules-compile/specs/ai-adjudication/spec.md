## ADDED Requirements

### Requirement: 无法核实问题必须经过 AI 可审计判定

系统 SHALL 对当前代次每个 `undecidable` finding 执行 AI 判定，并保存结构化判定结果、证据、缺口和模型批次信息。

#### Scenario: 证据充分且支持不符合
- **WHEN** AI 找到明确材料证据证明问题成立
- **THEN** AI 输出 `hit`，并写入材料 ID、页码、原文、reason_code 和 generation_id

#### Scenario: 证据充分且支持符合
- **WHEN** AI 找到充分材料证据证明问题不成立
- **THEN** AI 输出 `miss`，并写入材料 ID、页码、原文、reason_code 和 generation_id

#### Scenario: 证据不足
- **WHEN** AI 无法取得足够证据
- **THEN** AI 保持 `undecidable`，并输出具体缺失项、建议补件或人工核实事项

#### Scenario: AI 未执行或失败
- **WHEN** AI 批次 pending、failed、partial 或 not_run
- **THEN** 报告门禁阻断，不得把未执行写成无法核实已处理

### Requirement: AI 判定与人工处置分离

系统 SHALL 保留 AI 机器结论与人工 accepted / ignored 两类事实，不得用人工处置覆盖机器结论。

#### Scenario: 人工采纳 AI 结论
- **WHEN** 人工采纳 AI 判定
- **THEN** 报告同时显示 AI 机器结论和人工采纳回执

#### Scenario: 人工忽略 AI 结论
- **WHEN** 人工忽略 AI 判定
- **THEN** 报告保留 AI 机器结论、人工忽略回执和理由，不把机器结论改写成符合

#### Scenario: AI 重复执行
- **WHEN** 同一 finding 在同一代次重复执行 AI 判定
- **THEN** 新判定追加保存，旧判定保留审计，不覆盖人工意见

### Requirement: AI 证据充分判定直接形成机器结论
系统 SHALL 在 AI 判定证据充分、材料范围完整且不属于缺件/签章缺失类事项时，将 `hit` 或 `miss` 写成当前机器结论，不再要求人工重复处置。

#### Scenario: AI 判定符合
- **WHEN** AI 在完整材料范围内给出可核验的 `miss` 建议
- **THEN** finding 状态更新为 `miss`，保存 AI 判定审计，并允许正式报告继续生成

#### Scenario: AI 判定不符合
- **WHEN** AI 在完整材料范围内给出可核验的 `hit` 建议
- **THEN** finding 状态更新为 `hit`，报告按“AI 定性（未经人工处置）”披露；不因未人工处置阻断报告

#### Scenario: AI 仍无法判断
- **WHEN** AI 证据不足或建议保持 `undecidable`
- **THEN** finding 保持 `undecidable`，保存 AI 缺口说明，报告门禁继续阻断
