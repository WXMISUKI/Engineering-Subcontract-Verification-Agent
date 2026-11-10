# 分包合规报告适配：历史版本、补件代次、权限与门禁矩阵

状态：阶段 5 验收基线，尚未完成真实端到端业务验收
日期：2026-11-10
对应 OpenSpec：`openspec/changes/bs-report-adaptation/`

## 验收边界

本矩阵区分代码级验证、隔离数据库验证、真实材料验证和业务专家验收。现有测试通过只证明对应代码路径在合成或测试数据库下满足断言，不能替代真实材料、真实身份和真实导出物验收。

历史报告必须读取保存的 `BsReviewVersion.snapshot_json`；旧版本缺少字段时保持缺失或明确降级，不能从当前材料重新拼装。

## 当前代码入口

| 能力 | 后端入口 | 当前语义 |
|---|---|---|
| 当前任务详情 | `GET /api/plus/blockchain-supervision/tasks/{task_id}` | 返回当前材料代次、当前报告依据快照、补件状态和历史版本列表 |
| 材料替代链 | `GET /api/plus/blockchain-supervision/tasks/{task_id}/materials/{material_id}/history` | 仅在任务范围内读取，跨任务材料表现为 404 |
| 历史版本列表 | `GET /api/plus/blockchain-supervision/tasks/{task_id}/versions` | 只读列出版本号、触发来源和规则版本 |
| 历史版本详情 | `GET /api/plus/blockchain-supervision/tasks/{task_id}/versions/{version_no}` | 读取冻结快照；不回退当前材料正文 |
| 历史页文字 | `GET /api/plus/blockchain-supervision/tasks/{task_id}/versions/{version_no}/materials/{material_id}/pages/{page_no}` | 只返回快照明确保存的页；缺失时 404 |
| 历史版本差异 | `GET /api/plus/blockchain-supervision/tasks/{task_id}/versions/diff?a=...&b=...` | 比较当前或指定版本；补件未完成时当前结果对比可 409 |
| 当前报告门禁 | 报告读取/导出及 `complete_review` | 草稿可查看；确认稿受未处理 `undecidable` 门禁约束 |

## 代码级已验证矩阵

| 场景 | 预期 | 当前证据 | 状态 |
|---|---|---|---|
| `hit` finding 存在 | 不阻断确认稿 | `tests/test_bs_report_gate.py::test_report_gate_only_blocks_undecidable` | 已验证 |
| `miss` finding 存在 | 不阻断确认稿 | 同上 | 已验证 |
| 未处理 `undecidable` 存在 | 确认稿返回 409 | `test_confirmed_report_blocks_pending_undecidable_but_not_hit` | 已验证 |
| 草稿存在未决项 | 允许预览 | `test_draft_preview_is_not_the_formal_report_gate` | 已验证 |
| 完成复核后生成版本 | 返回版本号并保留快照入口 | `test_complete_review_allows_unresolved_hit_but_blocks_unresolved_undecidable` | 已验证 |
| 补件影响项重核 | 清除受影响旧意见 | `test_scoped_recheck_clears_affected_review_but_preserves_other_review` | 已验证 |
| 补件结论变化 | 旧意见保留为失效历史，不继续作为当前处置 | `test_changed_conclusion_invalidates_old_review_and_keeps_history` | 已验证 |
| 跨任务主体/材料绑定 | 返回 404，不泄露其他任务数据 | `test_upload_cross_task_subject_binding_returns_404` | 已验证 |
| 替代材料角色漂移 | 返回 409，旧材料保持未替代 | `test_replacement_role_drift_returns_409_and_keeps_old_material` | 已验证 |
| 未知材料角色 | 返回 400，数据库不落行 | `test_upload_unknown_role_returns_400_without_material_row` | 已验证 |
| 旧/新补件 generation | 事实和证据关系保持 generation_id | `test_bs_ingest_material_facts.py`、`test_bs_evidence_projection.py` | 已验证 |
| 隔离 HTTP 历史版本 | 版本列表/详情、冻结页 200、未冻结页 404、跨用户任务隐藏 | `tests/test_bs_report_version_http.py::test_history_version_and_page_are_read_only_and_cross_user_is_hidden` | 已验证 |

阶段性回归记录：历史/补件/权限相关后端定向测试最近一次为 `57 passed`；新增隔离 HTTP 历史版本测试 `1 passed`。

本轮在项目 `backend/.venv` 中重新执行覆盖历史版本 HTTP、补件保留/发布、报告门禁、安全恢复和证据投影的定向集合，`38 passed`；该结果仍属于隔离测试数据库证据，不替代真实身份和真实材料矩阵。

前端报告/来源/门禁定向集合在 `frontend` 中执行 `6 files / 20 passed`，页面来源导航、摘要、门禁和工作台模型保持通过。

## 仍需在隔离环境补齐的矩阵

| 场景 | 需要的验证动作 | 通过条件 |
|---|---|---|
| 当前草稿报告读取 | 真实任务完成材料解析和核查后读取 draft | 页面、业务投影和来源页一致；未决限制可见 |
| 当前确认稿导出 | 同一任务读取 confirmed Word/PDF | 门禁 200/409 与页面一致 |
| 收口后材料变化 | 收口后上传或补件，再读取旧版本 | 旧版本正文、字段和来源不变；新代次单独展示 |
| 历史版本详情 | 读取 `/versions/{version_no}` | 不从当前材料重建；旧快照缺字段明确降级 |
| 历史页文字 | 读取冻结页与未保存页 | 已冻结页返回 200；未冻结页返回 404 |
| 版本差异 | 比较当前、历史、补件前后 | 差异按 finding/状态/来源展示；补件未完成时返回 409 |
| 无权任务访问 | 不同身份读取任务、版本、材料页和导出 | 403/404 不泄露任务存在性或材料内容 |
| 并发材料变化 | 一边刷新报告，一边产生新材料代次 | 当前报告不混入新代次；历史快照不变化 |
| 忽略记录 | 忽略问题后读取页面、历史和审计导出 | 主报告过滤，审计保留决定、理由和原始 finding |
| 真实 DOCX/PDF | 保存并渲染当前及历史导出物 | 页面、Word、PDF 同源；历史导出不引用当前材料 |

## 进入阶段 5 完成的门槛

- [ ] 使用独立数据库、上传卷、缓存和测试身份完成上述 HTTP 矩阵。
- [ ] 当前、历史、补件前后至少各有一组冻结测试数据。
- [ ] 验证 200、409、403、404 的实际响应和脱敏边界。
- [ ] 保存真实 DOCX/PDF 并完成 Word/PDF 逐页视觉检查。
- [ ] 业务专家确认历史版本、补件代次、问题忽略和结论限制语义。
- [ ] 未经这些证据，不将阶段 5 标记为完成，也不进入生产部署。

## 候选环境真实导出复核记录（2026-11-10）

在现有 `bs-local-ai-acceptance-20261110` 候选容器中，对 task 5 做了只读数据库查询和内存导出：未调用上传、核查、补件、收口或其他写接口，也未提交数据库事务。

- 材料：真实 `预制板梁劳务分包报审（武汉龙辰建设有限公司）.pdf`，67 页，解析状态 `done`。
- 最新只读报告状态：`incomplete`；业务文档 5 个摘要章节、35 条待核实问题（分章 8/19/1/6/1），结论为不完整；未生成历史 review version。
- 当前任务 5 的规则指纹与历史运行指纹不一致，AI 覆盖返回 `rule_version_mismatch`，4 条负面候选被保护性过滤；该状态必须在重新核查或专家明确接受前保持不可作为最终报告。
- 独立快照已创建新 review 代次并完成 A1 规则重跑；读取链修复后新 A1 运行有 44 个参考查询，全部为 `hit`，引用数为 44，代次状态为 `partial`（参考文件未人工校对、上海细则适用范围和文本标点差异限制），模型重核尚未开始。
- Word：44,627 字节；使用 Microsoft Word 复渲染为 A4 8 页。
- PDF：140,787 字节；程序生成 A4 9 页。
- 逐页检查：基本信息、五类摘要、问题表、总体结论和限制文本均可见；未发现裁切、重叠、空白孤页或结论被挤出页面；跨页表格保留表头。
- 纸张修复：Word 导出原先使用 Letter，已统一为 A4，并由 `test_word_exports_use_a4_page_geometry_like_pdf_exports` 锁定。

该记录属于**候选环境只读技术复核**。候选容器使用共享试点数据库/上传卷，不满足独立环境验收条件；业务专家尚未确认报告字段、摘要和结论措辞，因此阶段 5 仍保持未完成。

## 2026-11-10 独立重建复核记录

候选任务 5 的数据库内容已以只读方式导出到一次性临时快照，并在独立 SQLite 中重建后重新执行报告投影和 DOCX/PDF 生成。稳定业务投影与候选端一致；含 `generated_at` 的过程追踪字段按次生成，不作为字节级一致性条件。该记录覆盖当前报告重建，不覆盖历史版本、补件前后版本和业务专家验收。

本轮新增隔离 HTTP 覆盖：历史报告通过 `report?version_no=1` 可读，跨用户读取被隐藏；补件计划处于 `planned` 且编辑代次一致时，`versions/diff?a=1&b=current` 返回 409；计划改为 `completed` 后，同一请求返回 200，并分别标识历史版本 1 与当前侧。这证明当前侧补件门禁已进入矩阵，但尚未覆盖真实补件材料重核和发布后的业务结果。
