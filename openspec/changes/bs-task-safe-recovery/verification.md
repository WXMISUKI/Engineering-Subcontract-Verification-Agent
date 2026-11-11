# 验证记录

## 2026-11-09 稳健收口增量

- 在 `service.rerun_task` 的材料校验、版本快照和旧结论处理之前增加当前代 AI 活动/租约保护。
- 活动或租约未到期时返回 `reason=ai_advisory_pending`，不进入破坏性恢复流程。
- 回归测试：`backend/tests/test_bs_safe_recovery.py`，**3 passed**；覆盖活动租约、跨进程 started 未知终态和未启动状态放行。
- 该结果只证明破坏性重核查前的状态保护，不证明持久化执行登记、完整历史快照或线上旧任务恢复已经完成。
