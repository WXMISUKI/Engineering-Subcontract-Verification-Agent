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
