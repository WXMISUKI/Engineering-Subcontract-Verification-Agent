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

## 阶段二：解析链路接入（2026-11-06）

范围：`backend/app/plus/blockchain_supervision/service.py`（`_finish_ingest` 收口接入、
`_material_supplement_generation`）及新增定向用例 `backend/tests/test_bs_ingest_material_facts.py`。

实际执行：

- `backend/.venv/Scripts/python.exe -m pytest tests/test_bs_ingest_material_facts.py -q --tb=short`：8 passed。
- 同口径回归 `backend/.venv/Scripts/python.exe -m pytest tests -k "bs_" -q --tb=short`：459 passed / 106 deselected。
- `python -m py_compile`（service、material_facts、新测试文件）：通过。
- `openspec validate --all --strict`：8 passed / 0 failed。
- `git diff --check`：通过。

已验证行为（SQLite 定向用例，解析入口注入、不触发真实 OCR）：

- 解析成功后，事实行、材料 `parse_state="done"` 与 `material.parsed` 同一次提交；事件载荷
  `facts_inserted` 与本次真正新增的事实行数一致。
- 抽取器抛错时材料仍按 `done` 收口，降级记录追加 `facts_failed`，不产生事实行，也不发
  `material.parse_failed`；`facts_inserted` 为 `null`，可区分「没抽到」与「没抽成」。
- 解析失败只写 `material.parse_failed`，不写事实行。
- 同内容重复收口复用同一 `parse_batch_id`，事实行数不变，第二次 `facts_inserted` 为 0。
- 补件材料的事实带上唯一链接代次 `generation_id`；无链接事件或存在多个互斥代次时留空，
  不猜测归属（②A）。
- 质量可疑页（`ocr_quality`）产生 `review_required` 候选事实，解析入口只被调用一次，不自动重读。
- 主体冲突分支仍保留候选事实，同时保留 `subject_conflict` 降级与 `material.subject_conflict`
  事件，事实不构成有效通过证据（③A）。

未覆盖：

- 未在隔离 MySQL/真实 OCR 链路上重跑（事实表结构已由 S1 的隔离 MySQL 验证覆盖，本阶段只新增
  写入调用与代次解析）。
- 未改写任何 finding、A2-05 投影、复核界面或报告门槛；S3 起才消费这些事实。
- 未做真实补件材料正负向验收（S6）。
- 未部署、未推送、未重启服务、未修改生产数据或历史任务结论。

结论：S2 解析链路接入完成，事实只在收口事务内追加，可进入 S3（人员证件三态关系）等待确认。

## 阶段三：人员证件三态关系（2026-11-06）

范围：新增 `backend/app/plus/blockchain_supervision/relation_resolution.py`、`BsEvidenceRelation` 表、
`certificate_number_check.roster_b_entries`，以及 `service._finish_ingest` 的关系解析接入。

实际执行：

- `backend/.venv/Scripts/python.exe -m pytest tests/test_bs_person_certificate_relations.py tests/test_bs_ingest_material_facts.py -q`：27 passed。
- 同口径 BS 回归 `backend/.venv/Scripts/python.exe -m pytest tests -k "bs_" -q`：478 passed / 106 deselected。
- 全量后端回归 `backend/.venv/Scripts/python.exe -m pytest tests -q`：584 passed（153s）。
- 隔离 MySQL 8.0.46 一次性 schema 验证：临时库 `bs_relation_check` 按 ORM DDL 建表，验证后已 `DROP DATABASE`；
  未触碰 `bs_review` 应用库（验证前后均为 15 张表）、未改动业务数据、未重启容器。
- `openspec validate --all --strict`：通过。
- `git diff --check`：通过。

已验证行为（SQLite 定向用例，事实与页面直接构造，不触发 OCR）：

- 姓名、主体、企业、岗位应交类别与名单证号全部一致才判 `matched`，并保存两侧页级依据与事实行号。
- 岗位为项目经理而证书为交安 C 时判 `conflict`，维度里同时给出应交类别与实际类别。
- 名单与证书编号不一致时判 `conflict`。
- 名单无布局线索、取不到可核对证号时判 `unresolved`，不因姓名一致而通过。
- 同名人员在同一任务出现多份互斥证书（类别或编号不同）时判 `unresolved`，不择一采信。
- 同一姓名的名单记录或证书页分属多个非空主体时判 `unresolved`，保留候选关系但不推定同一人。
- 证书页质量 `review_required` 时新增 `evidence.quality` 维度并降为 `unresolved`，字段一致也不得判通过。
- 同一证件多页合并为一条关系；名单页本身不会被当作证书页；无名单记录时不产关系。
- 关系为 append-only：同一关系键重放不重复插入；事实行号变化则追加新版本，旧行保留。
- 收口链路中关系、事实、材料状态与 `material.parsed` 同事务提交；载荷带 `relations_inserted`，
  重放为 0。
- 关系解析抛错只追加 `relations_failed` 降级，材料仍按 `done` 收口且事实不丢。
- 关系记录带唯一补充代次 `generation_id`；取不到代次时留空，不猜归属。

已验证行为（隔离 MySQL 8.0.46）：

- `bs_evidence_relation` 建表成功；列类型与模型一致（`relation_key VARCHAR(64) NOT NULL`、
  `status VARCHAR(20) NOT NULL`、四个 JSON 列、`created_at DATETIME DEFAULT now()`）。
- `uq_bs_evidence_relation_key` 为 `(task_id, relation_key)` 两列唯一索引，`information_schema` 中
  `non_unique=0`；重复写入同键返回 `ERROR 1062 ... for key 'bs_evidence_relation.uq_bs_evidence_relation_key'`。
- 同一任务不同指纹允许追加（2 行并存），符合 append-only 语义；`relation_key` 超 64 字符被拒
  （`ERROR 1406 Data too long`），列宽约束有效。
- 删除主体后关系行保留且 `subject_id` 置空（`ON DELETE SET NULL`）；删除任务后关系行清零
  （`ON DELETE CASCADE`）。
- 索引列宽：`task_id` 4 字节 + `relation_key` 64 × utf8mb4 ≈ 260 字节，远低于 InnoDB 3072 字节上限。

未覆盖：

- 未在隔离 MySQL/真实 OCR 链路上跑完整解析链路；新表由 `create_all` 创建，当前无历史表迁移问题。
- 未做真实多连接并发压测；并发幂等由唯一索引 + 逐行 savepoint 兜底，仅在 SQLite 单进程用例中验证。
- 未改写 finding、未做 A2-05 投影、未改人工复核界面与报告发布门槛（S4/S5）。
- 未使用真实补件材料做正负向隔离验收（S6）。
- 未部署、未推送、未重启服务、未修改生产数据或历史任务结论。

结论：S3 人员证件三态关系完成，可作为候选判定写入并被后续 S4 消费；等待验收后进入 A2-05 投影。
