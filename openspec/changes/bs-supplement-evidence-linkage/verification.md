# 补件证据关联验证记录

本文件记录分阶段验证结果；只覆盖已实施部分，不把单元测试等同于业务验收或生产验证。

## 阶段一：材料事实 append-only（2026-11-06）

范围：`backend/app/plus/blockchain_supervision/material_facts.py`、`BsMaterialFact` 表结构及其定向测试。

已执行：

- `backend/.venv/Scripts/python.exe -m pytest tests/test_bs_material_facts.py -q --tb=short`：6 passed。
- `python -m py_compile`（models、material_facts、测试文件）：通过。
- `openspec validate --all --strict`：8 passed, 0 failed。
- `git diff --check`：无空白错误。

已验证行为：

- 字段原值、规范化值和页级原文定位同时保存。
- `source_kind` 按页面实际来源区分线上 OCR、本地 OCR 和文本层。
- 质量可疑页面的事实标记为 `review_required`，不触发二次 OCR。
- 重新解析追加新批次，旧批次事实保留且可读。
- 同一内容与同一抽取器版本重放幂等，不重复插入。
- 事实行写入前校验来源与质量白名单。

未覆盖（后续增量）：

- 未在解析完成链路调用 `persist_material_facts`，当前无业务写入路径。
- 未实现人员/证书关系解析、A2-05 证据投影、人工复核界面和报告发布门槛。
- 未使用真实补件材料做正负向验收。
- 未做并发写入压测；同批次幂等目前由写入前查重保证，数据库层无唯一约束。
- 未部署、未重启服务、未修改生产数据或历史任务结论。

结论：阶段一事实层可提交；业务接入须在单独确认事务点、失败页处理、级联删除策略和并发幂等方案后再实施。

## D0 基线确认（2026-11-06）

实际执行：

- 后端基线回归（9 个测试文件）：43 passed。
- 前端定向回归（`PreviousSupplementReview.test.jsx`、`CheckRunTime.test.jsx`）：2 files / 4 tests passed。
- `openspec validate --all --strict`：8 passed / 0 failed。
- `python -m py_compile`（事实层相关文件）：通过。
- `git diff --check`：通过。

已冻结：

- 代码基线 `main` / `7b04fde` / ahead 6（D0 提交后） / 未推送。
- 四项契约：`_finish_ingest` 同事务写入；质量可疑页可产生候选事实；任务删除级联事实；`page NOT NULL DEFAULT 0` + 四列唯一约束。

未覆盖：

- S1 代码硬化尚未开始，当前 `page` 仍可空且无唯一约束。
- 未做隔离 MySQL 建表/索引验证。
- 未接入解析链路、关系解析、A2-05 投影、复核 UI 或报告门槛。
- 未做真实材料正负向验收。