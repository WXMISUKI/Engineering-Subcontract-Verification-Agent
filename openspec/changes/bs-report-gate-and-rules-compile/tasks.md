# 报告门禁、AI 判定与规则编译回灌任务

## 1. 报告门禁

- [x] 1.1 在 `reporting` 中新增 `report_gate_blocking`，正式报告门禁只判断 `undecidable`；`complete_review`、确认稿报告和历史确认稿共用该判据。
- [x] 1.2 `undecidable` 未处置阻断任务收口和确认稿；`hit` 未处置不阻断，报告继续按机器结论披露。
- [x] 1.3 candidate 未发布继续阻断正式报告；draft preview 保留只读路径，不作为正式输出。
- [x] 1.4 前端“生成审查报告”门禁已只统计未处置 `undecidable`；后端 `complete` 同步放行未处置 `hit`。
- [x] 1.5 增加报告门禁测试：hit 不阻断、undecidable 阻断、draft preview 不冒充正式报告、complete_review 行为。

## 2. AI 判定能力

- [x] 2.1 新增 `BsFinding.ai_adjudication_json` 和 finding/report DTO 投影，包含状态、理由、证据、模型、run_id、request_id 和原状态。
- [x] 2.2 保留现有 AI 覆盖门禁；未执行、失败或部分完成仍阻断报告。
- [x] 2.3 AI 在完整材料、非缺件/签章类事项且双侧证据可核验时允许 `hit`/`miss`；证据不足保持 `undecidable`。
- [x] 2.4 AI 事件 append-only；finding 保存最新判定投影，人工 accepted/ignored 优先且不被覆盖。
- [x] 2.5 finding/report DTO 透传 AI 判定，复核详情展示 AI 认定、理由和模型。
- [x] 2.6 增加 AI miss 准入、AI 写入 finding、审计字段和人工意见优先测试。

## 3. 规则编译/发布回灌

- [x] 3.1 从源项目回灌 `rules_compile/compiler.py` 和 `rules_compile/publication.py`。
- [x] 3.2 保留七道校验和 `pending_human` 发布候选语义。
- [x] 3.3 保持现有 `ruleset.py` / 规则读取路径兼容，不改历史任务规则版本。
- [x] 3.4 回灌源项目 `test_bs_rules_compile.py`、`test_bs_rule_publication.py`。
- [x] 3.5 增加发布候选身份不完整、编译失败、重复卡片、floor 关系异常测试。

## 4. 文档与验收

- [x] 4.1 更新 OpenSpec 规格和任务状态。
- [x] 4.2 更新主产品路线图、报告门禁说明和 AI 判定说明。
- [x] 4.3 已运行 OpenSpec strict、后端定向回归、前端定向回归和 `git diff --check`。
- [x] 4.4 记录未完成的真实材料、目标环境和专家验收边界。
