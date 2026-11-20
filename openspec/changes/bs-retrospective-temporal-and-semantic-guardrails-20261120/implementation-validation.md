# P1 验收记录：倒查时间语义

日期：2026-11-20

## 已实现

- 人工明确选择的审查基准日优先；未选择时仅在分包合同存在唯一可靠签订日的情况下，使用该日期作为历史规则基准。
- 缺失签订日时 `baseline_date` 保持为空、来源记为 `unknown`，不使用任务创建日期或运行日期替代；原 `contract_date` 继续记录合同签订事实。
- 将 `current_notice_date` 与历史审查基准分离并在受理时冻结；现行提示按该日期验证，历史不符合按历史基准验证。
- 倒查规范包和在线参考条款核验显式接收历史基准日期；其他既有调用未提供 `as_of` 时继续使用原任务日期行为。
- 不改写任务表、准入运行和历史报告；旧 `RunIdentity` 仍可读取并按 `legacy` 来源兼容。

## 法源核验

上海市交通委员会官网正式通知确认沪交行规〔2026〕8号于2026-07-06发布，自2026-08-08施行，有效期至2031-08-07。详见 `docs/research/2026-11-20-shanghai-subcontract-rule-official-source.md`。官网正文及第二十四条可读核验；附件PDF文本提取未成功，未声称逐页核对或完成具体条款适用审计。项目规则数据中的 `human_verified: false` 保持不变。

## 验收

- `uv run python -m pytest tests/test_bs_retrospective_analysis.py tests/test_bs_retrospective_runtime.py -q`：43 passed。
- `uv run python -m pytest tests/test_bs_retrospective_knowledge.py tests/test_bs_retrospective_coverage.py tests/test_optional_source_scope_lifecycle.py -q`：51 passed。
- `openspec validate bs-retrospective-temporal-and-semantic-guardrails-20261120 --strict`：通过。
- `git diff --check`：通过。

以上是隔离测试证据，不包含真实服务、共享数据库或历史任务写入。

## 结论

P1 技术验收通过。未知签约日仍会阻止历史确定性定性，但合同文本内部一致性核查可继续；现行提示按独立冻结日期验证。进入 P2。
