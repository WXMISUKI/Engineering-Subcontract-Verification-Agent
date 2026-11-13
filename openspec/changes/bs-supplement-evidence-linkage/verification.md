# 补件证据关联验证记录

## 2026-11-11 补件与报告隔离回放

- 在合成证据及独立 SQLite 中复现：名单与证书证号明确冲突时 A2-05 已投影为 `hit`，报告关键点仍显示“待核实”；原因是报告规则只识别证照缺件，未消费结构化证号冲突。
- 报告策略 1.1.0 增加该规则：同人、同主体、企业和证件类型均匹配，双方证据可定位且证号明确不同才判不符合；证件字段、主体或证据不足时保持待核实。旧冻结策略不自动套用该规则。
- 合成回放覆盖证号冲突、持证人及企业信息遮挡、主体不一致、关键字段读取不足、证据缺失及同点其他未知事项；核对业务投影与 Word/PDF 判定。
- 隔离 HTTP/数据库回放核对受影响人工意见失效、无关忽略意见保留、未发布和失败候选阻断、历史 Word/PDF 及跨用户访问限制。
- 相关后端回归 182 项、前端报告与历史复核 12 项通过；新增回放补充断言后单独重跑 7 项通过。OpenSpec strict 14 项通过。
- 本记录仅覆盖合成数据、隔离 SQLite 和模拟 HTTP；未重跑真实 OCR/模型、未修改生产任务、未验证真实人工意见或目标环境，不替代业务专家验收。

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

## 阶段四：S3.5 人员证件材料事实与三态关系修复（2026-11-06）

范围：`backend/app/plus/blockchain_supervision/material_facts.py`（`EXTRACTOR_VERSION = material-facts.v2`）、
`certificate_number_check.py`（名单编号来源）、`relation_resolution.py`（`RESOLVER_VERSION = person-cert-relation.v2`），
以及 `backend/tests/test_bs_material_facts.py`、`backend/tests/test_bs_person_certificate_relations.py`。

### 一、缺陷、根因与修复

| 现象 | 根因 | 修复 |
| --- | --- | --- |
| A2-05 只能给出笼统提示，名单侧编号取不到 | 名单编号只从 OCR 布局行取，姓名/岗位/证号不同行时直接返回空 | 布局行优先，布局不可用退回文本表格续行；要求姓名+岗位唯一对应且只命中一个 B 证编号；以 `number_source` 标注 `layout` / `text_table` |
| OCR 连排把字段粘在一起（`鄢明志性别`、`陶瑜系列`） | 字段截断只按行与空格 | 姓名标签允许「姓 名」夹同行空格；按边界词截断，截断后仍须为 2-4 字中文姓名 |
| 企业字段漏识 | 仅识别「单位」等少数标签 | 增加「受聘单位」「单位名称」同义标签，按边界截断且不吞同行证号 |
| 身份证号被误当成证书编号 | 「证号」匹配未排除「身份证」 | 要求带冒号且前缀不是「身份」；清理 LaTeX/OCR 装饰符与尾随日期列 |
| 同一材料同一人出现互斥编号时被静默择一 | 收集后取首条 | 互斥编号整体返回空，关系层保持 `unresolved`，不升级为 `conflict` |
| 同名人员分属不同主体时整条关系被吞 | `latest_relations` 按 `person_key` 收敛 | 改为按 `(subject_id, person_key)` 收敛 |

### 二、自动化验证

- 定向：`pytest tests/test_bs_material_facts.py tests/test_bs_person_certificate_relations.py tests/test_bs_ingest_material_facts.py -q`：53 passed。
- BS 子集：`pytest tests -k "bs_" -q`：492 passed / 106 deselected。
- 后端全量：`pytest tests -q`：598 passed（277 warnings）。
- `openspec validate --all --strict`：8 passed / 0 failed。
- `git diff --check`：通过。
- 反证用例：临时 SQLite 中构造同任务两行同名关系（subject 7 / 8），旧逻辑只剩 1 行、新逻辑保留 2 行；验证后临时库已删除。

### 三、真实任务只读验收

两个真实任务，在本地隔离库上用真实 OCR 结果重放，全程只读：未点击采纳/忽略、未生成报告、未发起补件或重新核查（task 7 的自动续跑例外见「五、副作用声明」）。

**task 7 — 武汉龙辰建设有限公司（`labor`，m4.pdf 67 页，material 6 / subject 6）**

- 事实：18 条，仅一个 `material-facts.v2` 批次。
- 关系：3 条（于文祥、吴昊、吴道记），全部 `unresolved`。
- A2-05：0 条。
- 口径：证书页缺企业与证书类别，未因姓名一致判通过，符合「无法确认不得推测」。

**task 8 — 中铁二十四局集团上海铁建工程有限公司（`professional`，m2.pdf 91 页，material 7 / subject 7）**

- 事实：41 条，仅一个 `material-facts.v2` 批次。
- 关系：10 条 —— 陶瑜 `conflict`，其余 9 人 `unresolved`。
- A2-05：finding 357，`undecidable`，medium。
- 页面只读核对的文案：名单第 22 页陶瑜编号线索 `沪交安 B（25）G00206`，同名证书第 28 页编号 `沪交安B25G00683`，提示核实名单对应关系与正确编号。
- 第 28 页原件核对：陶瑜、受聘单位为中铁二十四局、OCR 含目标编号。
- 第 33 页 OCR 为「郝明志」，未被错误归一为「鄢明志」。
- 代次：review run `eecbba3fff9c4e1e8051db16e515fe9f`，A1 `66388f369afe4d2097f5ba2788035aff`，
  A2 `26935a60c9e043b9a5446db82653e42b`，completeness `96f8498219ed49d5aa8528ce0707278f`，
  signature_chain `815434659c424d20a4ef86e9b4af29f1`，internal_records `1795e0a2b3344097b0ac4f5994e83201`。
- A1 的 44 条查询全部 `hit`，取得 40 条参考原文。
- 后台完成事件 id 3732 `check.ai_background_completed`：`warning=true`、`unlocated_scope_count=0`；本代无新的 `check.ai_background_failed`（历史 `provider_unreachable` 是旧记录，不是本次故障）。

**真实材料只读重放（S3.5 前置证据，仍有效）**：m2 / m4 共 32 项断言全部通过 —— p22 陶瑜 `沪交安B (25) G00206`
规范化为 `沪交安B25G00206`（`text_table`）、p28 `沪交安B25G00683` 判 `conflict`、p33「郝明志」不归一、
p34 鄢明志 `沪交安C(22)G00822` 编号 `unresolved`、p59 张春缺企业与类别 `unresolved`；
页面核对 p22/p24/p32/p33/p34/p28/p59 页码与 PDF 物理页一致。截图按用户规则未入库、未留存。

### 四、AI 覆盖口径（不得夸大）

task 8：completeness 4 completed / 5 partial / 2 not_run；signature_chain 0 completed / 1 partial / 1 not_run；
a1 38 partial；a2 7 partial；internal_records 1 not_run；无 failed、pending、not_started。

`warning=true` 来自模型辅助建议的 `partial`/`not_run` 限制与被跳过范围，**不是**材料事实提取失败，也不能据此生成确定性通过结论；剩余内容必须作为人工复核线索。不得表述为「AI 已完整覆盖」。

### 五、副作用声明

- 切换到 task 7 时，前端 `frontend/src/plus-ui/blockchain-supervision/lib/autoCheckResume.js` 自动续跑了未完成核查组
  （`POST /tasks/7/check`、`/check/a1`、`/ai-review`），新 review run `316a274af7414bf6b715a4b5800f2320`
  于 2026-11-06 09:57:28 完成，`warning=true`、`unlocated_scope_count=0`、无 `check.ai_background_failed`；
  关系结论未变（仍 3 条 `unresolved`，A2-05 仍 0 条）。该次续跑不是人工点击「重新核查」，但确属本阶段副作用，不得记为「完全无副作用」。
- task 7 最终覆盖：completeness 2 completed / 4 partial / 8 skipped；signature_chain 3 skipped；
  a1 38 partial / 5 skipped；a2 7 skipped；internal_records 1 skipped。

### 六、结论与未验证边界

- 通过：S3.5 的字段提取与三态关系判定在真实材料上行为正确；A2-05 已能同时引用名单页与证书页的页码与编号；
  证据不足场景保持 `unresolved`，未升级为通过或冲突。
- 未实施：S4 投影（支持/冲突/无法确认的结构化表达）、复核界面展示、报告发布门槛、正向与负向补件隔离闭环。
- 不得据此声明：业务结论正确、AI 全覆盖、线上服务已部署。代码未部署、未重启服务、未修改生产库或历史任务结论。

### 七、验收环境说明

- 本地 task 2 的 `/materials/2/file` 返回 404，原因是本地库 `conversation_id=4` 对应 mapping `is_deleted=1`；未恢复会话、未改库、未用临时服务绕过，页面核对改走远程只读入口。
- 关系与事实验收基于从隔离容器导出的材料快照重放，未写业务库、未重跑任务、未调用模型。

## 阶段五：S3.5 反证复核与关系硬化 v3（2026-11-06）

范围：`backend/app/plus/blockchain_supervision/relation_resolution.py`（`RESOLVER_VERSION` 由
`person-cert-relation.v2` 提升为 `person-cert-relation.v3`，关系 schema 仍为 `evidence-relation.v1`）与
`backend/tests/test_bs_person_certificate_relations.py`（新增 4 个反证回归用例）。

### 一、反证复核发现的四类误判

| 反证场景 | 旧行为 | 风险 | 修复 |
| --- | --- | --- | --- |
| 材料已被替代（`superseded_by_id` 非空） | 仍参与关系判定 | 替换后旧证书绕过替代链，可再次成为通过证据 | `_is_current_material` 排除 |
| 替换候选尚未生效（`replacement_target_id` 非空） | 提前参与判定 | 未生效材料被当作当前证据 | 同上 |
| 同一人同证号多页、企业名称互相矛盾 | 取首页企业比对，可能判 `matched` | 编号相同不能抵消企业矛盾 | 企业集合多于一个即 `unresolved` |
| 证书材料未挂主体（`subject_id` 为空） | 企业名一致即判 `matched` | 缺主体归属却被当作本主体通过证据 | 记 `subject=unresolved`，不判通过 |

`latest_relations` 按 `(subject_id, person_key)` 收敛，避免同名跨主体互相吞并。

### 二、自动化验证（v3 重跑）

- 关系定向：`pytest tests/test_bs_person_certificate_relations.py -q`：23 passed。
- 关系/事实/收口子集：`pytest tests/test_bs_material_facts.py tests/test_bs_ingest_material_facts.py tests/test_bs_person_certificate_relations.py -q`：57 passed。
- BS 子集：`pytest tests -k "bs_" -q`：496 passed / 106 deselected。
- 后端全量：`pytest tests -q`：602 passed（277 warnings）。
- `openspec validate --all --strict`：6 passed / 0 failed（两个并入的变更目录已删除，故由 8 降为 6）。
- `git diff --check`：通过。

### 三、真实材料复核（v3，隔离库只读重放）

- task 7（武汉龙辰，67 页）：事实 18 条、关系 3 条（于文祥、吴昊、吴道记）全部 `unresolved`，A2-05 为 0 条，与 v2 判定一致。
- task 8（中铁二十四局，91 页）：事实 41 条、关系 10 条 —— 陶瑜 `conflict`（证书第 28 页 `沪交安B25G00683` 对名单第 22 页 `沪交安 B（25）G00206`）、鄢明志 `unresolved`（第 34 页，仅证号待核）、张春 `unresolved`（第 59 页，企业/类别/编号待核）、其余 7 人未找到可唯一归到本人的证书页。
- 名单第 33 页 OCR「郝明志」未被归一为「鄢明志」。
- 结论：v3 只收紧反证路径，不改变真实材料既有业务判定；task 8 仍有大量 `partial`/`not_run` 与 `warning=true`，不得表述为 AI 全覆盖。
- 只读重算复核（不写库）：用库内现有材料与最新批次事实，对 task 7/8 重新调用 `build_person_certificate_relations`，与 `latest_relations` 读回结果**逐人逐主体完全一致**（`identical=true`，无单侧多出）：
  task 7 = 1 份材料 / 18 条事实 / 3 条关系（于文祥、吴昊、吴道记全 `unresolved`）；
  task 8 = 1 份材料 / 41 条事实 / 10 条关系（陶瑜 `conflict`，刘万鲁、刘文全、周宇、张春、徐李君、秦一博、秦明阳、郑威、鄢明志 `unresolved`）。

### 四、落库幂等（本地隔离 MySQL `bs_review`）

- task 7 首次写入 3 条 v3 关系；task 8 首次写入 10 条；同一任务第二次执行插入 0 条。
- `latest_relations` 已指向 v3；历史 v2 行按 append-only 保留。
- 未重跑 OCR、未重新启动任务、未修改 A2-05 投影；这是隔离验收库的追加写入，不是生产库。
- 复核（2026-11-06，库内直查）：task 7 共 6 行（3 条 v2 + 3 条 v3），task 8 共 20 行（10 条 v2 + 10 条 v3），最新行时间 10:21:21 后无新增；
  `uq_bs_evidence_relation_key` 在 `bs_review` 中 `non_unique=0`（`task_id` + `relation_key`）。

### 五、浏览器只读验收

- `http://127.0.0.1:8099/?bs_view=review`（`bs-s35-frontend` → `bs-s35-backend`）打开 task 8：91 页、待复核 58 项、无法核实 57 项；A2-05 的名单第 22 页 / 证书第 28 页编号差异原文可见。
- 未点击采纳、忽略、补件、重新核查、重新开始或生成报告；未留存截图。

### 六、本轮结论

- S3.5 通过反证复核：字段提取与三态判定在真实材料上行为稳定，四类反证缺陷已修复并有回归用例守护。
- 未实施：S4 的 A2-05 结构化投影、复核界面展示、报告发布门槛、补件重核闭环。
- 未部署、未推送、未重启生产服务、未修改生产库或历史任务结论；提交与推送需用户另行授权。

## 附录：补件重核遗留缺陷（并入后节选，未完成阶段验收）

原 `bs-supplement-real-closure` 范围已并入本变更，过程记录随合并收敛；此处只保留仍未闭环的结论，避免把未解决缺陷当作已解决。

- 已冻结旧基线并让激活重核复用同一基线；已复现并修复基线混入补件、报告误读取、跨计划覆盖、影响项回执复用、当前结果误标历史版本、处理中的差异对比。
- 仍未实现：候选与基线隔离、达到条件后自动生效、失败恢复、报告与人工意见一致性；真实业务能否安全缩组未验证。
- 负向补件实测（交安 B 证关键姓名/企业被遮挡）：流程可运行，但未明确指出持证人身份无法确认、项目经理 B 证不匹配或 C 类线索，针对性提示与定位**未通过**。
- 隔离真实重核读回：publication `ready`、当前材料 2 份、历史版本 3 含 1 份材料与 54 项结果、差异接口 200、草稿报告 200 且不冒称历史版本、正式报告 409；未重新执行线上模型调用。
- 《合同扫描件-吊装》封面与武汉龙辰独立交安 C 证均已出现在原报审材料（第 66 页 / 第 17 页），不能当作新增业务事实。
- 仍待：独立有效补件的真实业务验证、AI 运行中断恢复、更全面并发验证。
