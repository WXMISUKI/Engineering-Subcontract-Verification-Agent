# 报告门禁、AI 判定与规则编译回灌设计

## 1. 已冻结决策

| 编号 | 决策 | 结论 |
|---|---|---|
| D1 | 报告门禁对象 | 当前代次所有未处置 `undecidable` 阻断正式报告 |
| D2 | `hit` 是否阻断 | 不阻断报告；报告逐项标注“AI 定性、未经人工处置” |
| D3 | AI 覆盖率 | AI 必须覆盖当前代次全部 `undecidable`，不得存在 pending / failed / partial / not_run |
| D4 | AI 判定边界 | AI 只能在证据充分时改为 `hit` 或 `miss`；证据不足仍保持 `undecidable` 并给出缺口 |
| D5 | 人工处置 | 采纳 / 忽略是独立回执，不把人工处置改写成机器结论 |
| D6 | 候选代次 | candidate 未发布时仍阻断正式报告；发布后恢复当前代次读取 |
| D7 | 规则编译 | 从源项目回灌 `rules_compile` 七道校验和发布候选；不得自动发布 |
| D8 | 计费/计时 | 继续暂停，不在本变更实现 |

## 2. 报告门禁算法

```text
if supplement_publication.state == "candidate":
    block("supplement_candidate_unpublished")
if AI coverage has pending / failed / partial / not_run:
    block("ai_adjudication_incomplete")
if count(current_generation findings with status=undecidable
         and review.decision not in (accepted, ignored)
         and not open_term
         and not material_only) > 0:
    block("unresolved_undecidable")
allow report / opinion / export
```

说明：

- `hit` 未处置不进入阻断集合；
- `miss` 不阻断；
- `open_term` 和 `material_only` 按现有语义处理；
- 候选状态门禁优先于报告内容门禁；
- 报告、意见书、Word/PDF、证据包必须共用同一门禁函数。

## 3. AI 判定数据结构

每条 `undecidable` 至少产生一条可审计 AI 判定：

```json
{
  "finding_id": 123,
  "item_id": "A2-05",
  "generation_id": "supplement-...",
  "review_run_id": "...",
  "status": "hit|miss|undecidable",
  "reason_code": "evidence_match|evidence_conflict|missing_evidence",
  "reason": "可读说明",
  "evidence": [
    {"material_id": 1, "page": 3, "quote": "...", "location_mode": "precise"}
  ],
  "missing": ["社保期间", "证书编号"],
  "confidence": 0.0,
  "model_code": "...",
  "batch_id": "...",
  "created_at": "..."
}
```

约束：

- AI 不得在无证据时把 `undecidable` 改为 `miss`；
- AI 不得覆盖人工 accepted / ignored；
- AI 判定必须绑定当前 generation_id 和 review_run_id；
- AI 失败或未执行时，报告门禁必须阻断；
- 报告保留机器结论与人工处置两列，不能合并。

## 4. 规则编译回灌

从源项目保留并回灌：

- `backend/app/plus/blockchain_supervision/rules_compile/compiler.py`
- `backend/app/plus/blockchain_supervision/rules_compile/publication.py`

保留七道校验：

1. source_text_anchor
2. unit
3. three_state
4. numeric_direction
5. clause_coordinate
6. duplicate
7. floor_relation

发布规则：

- 编译通过只表示候选可用；
- 发布候选状态固定为 `pending_human`；
- 未经人工确认不得写入正式规则集；
- 发布候选必须携带 basis_snapshot_id、ruleset_version、profile_code、source_manifest_hash；
- 当前规则集读取路径保持兼容，不因回灌改变历史任务。

## 5. Open Questions

1. `hit` 的报告文案是否统一为“AI 定性（未经人工处置）”，还是沿用现有文案。
2. AI 判定失败时，报告阻断原因是否显示批次、模型和重试入口。
3. 规则发布候选的人工确认入口放在主产品哪个管理界面。

## 6. 2026-11-16 独立版最小任务级门禁（用户确认 A）

以本节及增量 report-gate 规格为当前实现，前述历史算法不再完整描述现行门禁。只读 `build_report_readiness` 返回 `bs.report-readiness.v1`，分别决定 `draft_readable` / `confirmed_output_ready` / `task_close_ready`。不新增数据库结构。

当前代草稿仅供应用内读取；输入/规则指纹和审查身份仍是硬前提。正式输出先检查候选发布，再共享检查当前所选分组、读取覆盖、必需 AI 执行和人工回执。显式无信号跳过仍需披露且不等于未命中；未知执行计数不能当作零，reviewable_failed/partial 或范围披露不能抵消执行失败。解析失败使读取待核，不能据此把未能核验的执行状态标作完成。

独立版通过收口发布候选，因此候选在技术和人工复核完成后可允许收口，同时正式输出仍受未发布门禁保护。done 的重复收口保留幂等。历史版本沿用冻结内容及回执，不由当前状态重新生成。

当前代 draft 文件输出统一返回 `draft_file_output_blocked`。草稿报告/意见书 JSON 与应用内 Markdown 均披露就绪缺口；摘要、建议、业务报告结论和签署建议暂不形成正式结论，原始明细和计数保留。前端读取任务级 DTO，异常协议阻断；缓存意见书不豁免门禁。服务端正式入口始终重新校验。

验收与限制见 `docs/plus/blockchain-supervision/bs-task-report-readiness-acceptance-20261116.md`。后续普通人工提示的持久化另见治理目录设计，不在此次门禁代码中用三态或消息推断。

## 7. 2026-11-16 用户验收修订：OCR 质量仅提示

用户明确要求 OCR 读取质量不再作为报告阻断项。本节替代第 6 节中读取缺口和可复核部分结果一律阻断的口径，详见 `bs-ocr-quality-advisory-20261116`。

未读/可疑页和解析失败只作质量提示，保持原始记录和报告披露，不要求人工逐页确认。AI 执行覆盖允许已形成可复核结果的 partial；负面候选 not_run 只有经当前代、材料归属和缺口页验证可归因到 OCR 限制时才仅披露。真实 failed、在途、未启动、不明原因和失效覆盖仍阻断。该策略不将任何未覆盖项改成符合或未命中。

前端接受 `reading_quality.state=pending` 与正式就绪同时成立；复核保存后刷新任务详情，点击生成时再读取当前判据，遇到阻断显示原因。服务端正式入口仍重新校验，历史版本不重写。
