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
- 未做并发写入压测；同批次幂等当时只由写入前查重保证，数据库层无唯一约束（S1 已补，见下文）。
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

- S1 代码硬化已完成，见下节；本节保留 D0 当时的待办状态作为历史记录。
- 未接入解析链路、关系解析、A2-05 投影、复核 UI 或报告门槛。
- 未做真实材料正负向验收。

## S1 事实层硬化（2026-11-06）

范围内文件：

- `backend/app/plus/blockchain_supervision/models.py`（`BsMaterialFact.page`、唯一索引、模型说明）
- `backend/app/plus/blockchain_supervision/material_facts.py`（无页码记 0、逐行 savepoint 去重）
- `backend/tests/test_bs_material_facts.py`（新增 6 个定向用例）

实际执行：

- `backend/.venv/Scripts/python.exe -m pytest tests/test_bs_material_facts.py -q --tb=short`：12 passed。
- 基线 9 文件回归（与 D0 同一命令）：49 passed（D0 43 + 新增 6）。
- 隔离 MySQL 8.0.46 一次性验证：临时库 `bs_fact_check` 按 ORM DDL 建表，验证后已 `DROP DATABASE`；
  未触碰 trial 应用库、未改动业务数据、未重启容器。
- `python -m py_compile`（models、material_facts、测试文件）：通过。
- `openspec validate --all --strict`：8 passed / 0 failed。
- `git diff --check`：通过。

已验证行为（SQLite 定向用例）：

- 页面缺页号或 `page=None` 抽取出的 `page` 一律为 0。
- `page` 列 `nullable=False`、`server_default` 为 0；未显式赋值落库后读到 0。
- 直接插入同 `(material_id, parse_batch_id, field_path, page)` 的重复行被数据库拒绝。
- 同字段同页在不同批次允许并存（历史批次不被覆盖）。
- 并发写入方抢先落库时，`persist_material_facts` 把冲突按重复处理，返回的 `inserted` 不含重复行，
  Session 仍可继续读写；只有确认冲突键已存在才吞 `IntegrityError`，其他约束错误照常抛出。
- 开启 `PRAGMA foreign_keys=ON` 删除 `BsTask` 后，材料与事实行级联清除（D-3）。

已验证行为（隔离 MySQL 8.0.46）：

- 建表 DDL：`page INTEGER NOT NULL DEFAULT 0`，未给 page 插入列时落库为 0。
- `uq_bs_material_fact_batch_field_page` 为 4 列唯一索引（`material_id, parse_batch_id, field_path, page`，
  列序与模型一致），`information_schema.statistics` 中 `non_unique=0`。
- 重复写入同批次键返回 `ERROR 1062 ... Duplicate entry ... for key ...uq_bs_material_fact_batch_field_page`。
- 同字段同页写入另一批次成功。
- `DELETE FROM bs_task` 后事实行计数为 0（`ON DELETE CASCADE` 生效）。
- 索引列宽：`parse_batch_id` 64 + `field_path` 120 字符，utf8mb4 下约 752 字节，低于 InnoDB 3072 字节上限。

未覆盖：

- 未做真实多连接并发压测；并发幂等由唯一索引 + savepoint 兜底，未在多进程竞争下实测。
- `bs_material_fact` 尚未在任何环境部署；项目无 Alembic，`create_all` 不会修改已存在的旧表。
  若某个本地/开发库已按 S1 前形态建过该表，需要手工 `ALTER`（先把 `page` 的 NULL 回填为 0，再改
  `NOT NULL DEFAULT 0`，最后补唯一索引），或直接删除空表由 `create_all` 重建。
- 未接入解析完成链路、关系解析、A2-05 投影、复核 UI 或报告门槛。
- 未部署、未推送、未重启服务、未修改生产数据或历史任务结论。

结论：S1 事实层硬化完成，可进入 S2（解析链路接入）等待确认；本阶段不改变任何业务结论。
