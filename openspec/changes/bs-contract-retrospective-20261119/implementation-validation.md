# 分阶段实施与验收台账

日期：2026-11-19；开发分支 featurelyl1118。用户已授权依冻结方案分阶段实施；不包含部署、main重启或历史任务修改。历史状态为旧目标清除；用户确认“单合同先闭环，再加总包辅助”后已建立阶段2目标。

| 阶段 | 实现 | 验证 | 限制 |
| --- | --- | --- | --- |
| 0 冻结 | 意见书与OpenSpec已写 | openspec validate --strict通过 | 规格不等于产品完成 |
| 1 模式 | 创建、列表、详情保存review_mode；顶栏与模型并列、刷新恢复、首次上传创建后锁定 | 后端test_bs_review_mode 2项、前端reviewModeSelect 3项通过 | 旧任务默认准入；倒查执行未接通，start明确拒绝，不能误走准入流程 |
| 2 单合同 | 技术闭环已完成：独立后台、自动八列表、页面原页入口、同源Word/PDF | 隔离API/解析/模拟模型/恢复/导出及兼容回归通过；76页只读文本17批离线回放；PDF视觉检查通过 | 未执行真实Provider与浏览器全链路；不做实际履约认证，未部署 |
| 3 总包辅助 | 待实施 | 待执行 | 不做总包独立完整审计 |
| 4 自动报告 | 待实施 | 待执行 | 真实Provider/浏览器/导出各自记录 |

已读源码：任务 options JSON、现有语义比对、模型选择器与任务创建入口。现有语义比对是准入人工候选，不直接替代新模式。

## 阶段1说明

- 模式使用现有 options_snapshot_json，未新增DDL，准入启动覆盖快照时保留模式。
- 为避免处理材料中途切换口径，首次上传创建任务后即锁定；需要另一模式新建核查。
- API为临时SQLite隔离验证；前端是Vitest组件/序列化验证，尚未做真实浏览器、Provider或部署验收。
- 用户随后要求优先整理并提交推送已完成内容到featurelyl1118。阶段2—4保持未完成，不以本次推送冒充倒查试用版本。

## 阶段2技术验收（本轮新增）

阶段1记录为当时状态，以下为用户确认顺序后新增结果，不回写旧任务。

- 复用BsAiReviewJob、BsTaskEvent、BsReviewVersion，无新增DDL；独立`retrospective_report`快照，不伪造准入finding或人工处置。
- 新增异步`POST /tasks/{id}/retrospective/start`和只读`GET /tasks/{id}/retrospective`；标准report/export读取同一保存版本，无生成/导出模型调用。
- 受理CAS及行锁、模型code/请求model/provider/端点指纹冻结、材料及依据指纹、后台租约、批次检查点与有界恢复。只有请求开始无结果的未知批次不自动重付；用量提交失败停止后续请求。
- 倒查允许setup上传/删除合同，开始后锁定输入；路由统一拦截准入检查、复核、补件及重跑写操作，保持旧准入逻辑。账号、会话及任务归属在运行中再次核对。
- 全部未执行与零发现区分；失败只显示状态及限制，不提供正常报告导出。部分覆盖可出带明确范围限制的报告。
- 逐页原文、跨页条款索引、空白归一后还原连续摘录、真实物理页码、历史/现行口径、内部歧义双侧原文。只采用可验证依据；未完成的安全费原件不进入本轮依据快照。
- 页面独立挂载，恢复后无需准入确认；多文件关系显式选择。原页入口使用现有鉴权文件接口，不虚构坐标高亮。
- Word/PDF横向八列，固定列宽及长行跨页；PDF重复表头，Word导出结构可重开。未做Word人工视觉验收。

### 可执行验证与结果

后端cwd为`backend`，使用既有`.venv`，全部使用临时SQLite/上传目录和模拟客户端，无共享业务库写入、无付费模型/OCR调用：

```powershell
rtk proxy .venv/Scripts/python.exe -X utf8 -m pytest tests/test_bs_retrospective_contracts.py tests/test_bs_retrospective_sources.py tests/test_bs_retrospective_analysis.py tests/test_bs_retrospective_runtime.py tests/test_bs_retrospective_report.py tests/test_bs_retrospective_report_http.py tests/test_bs_review_mode.py tests/test_bs_background_ai_review.py tests/test_bs_ai_review_jobs.py tests/test_report_template_v14.py -q --tb=short
```

本轮该组合107项通过；随后补充长表格跨页、Word横向结构、真实过期租约及账号隔离断言，runtime/report定向10项通过（与前组合部分重叠）。

前端cwd为`frontend`，`rtk pnpm exec vitest run`：40个文件、183项通过；未执行npm build。覆盖恢复、单合同直接启动、多文件关系、失败报告不出现无风险/导出、原页导航与无强制人工复核控件。

OpenSpec strict及git diff --check通过。CodeGraph状态可用，492文件已索引。main独立测试目录干净，本轮未操作服务。

### 真实文本离线回放及范围

只读复用已核SHA的76页分包合同，签订日期确定性提取为2023-12-22；17批模拟响应全部完成，原文验证后保留第39页12.1两侧摘录的一项内部歧义。候选预先指定，不能证明模型识别质量或完整法规覆盖。

本地私有样本：`artifacts/bs-retrospective-stage2-20261119/single-contract-offline-replay.{json,docx,pdf}`；PDF用Poppler渲染并视觉查看，无表格溢出/叠字。私有材料/产物不纳入代码提交。

回放发现并修复输入预算问题：密集清单产生大量条款位置元数据及全文目录，超过既有100000字符请求上限。改为页内片段合并传输、有界目录；原始索引仍保留，密集清单正式反例回归通过。不放宽预算，不据截取目录认定条款遗漏。

### 未完成的产品验收与后续

真实Provider/OCR效果、真实浏览器上传至导出、Word人工视觉检查、专家对风险表述和适用条件的确认未执行。未部署到试用环境，主服务版本不变。阶段2完成仅表示隔离技术闭环，不表示该真实合同已完成正式核查。

阶段3按已冻结方向增加总包作为上游约定来源、主体/范围匹配与双侧证据比较；阶段4集中完成真实试用验收。九维准入改造与安全费专项WIP继续保留，未混入阶段2。
