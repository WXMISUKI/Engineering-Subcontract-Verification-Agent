# 方案身份与审批事实切片验收

独立施工方案/报审表保存项目、名称、编号、版本，编制/承包审查/监理三侧单位、日期及原文意见。独立审批记录只在同主体、项目/编号/版本唯一对应时形成候选；另一份冲突候选可能覆盖本身份时也不借审批。不按相似名称或合同“应报批”推断已审批。孤立审批记录仍单列来源提示。

A3-03-plan接service.run_signature_chain及既有主体/参考快照门禁；固定undecidable + manual_prompt。追加plan.records、material-facts.v8，旧v7事实不修改。V14 mapping=2026-11-19.5，提示保留双侧页/引文，不进入机器问题、附件分母或签署条件；未知主体继续普通门禁。三侧原文日期可唯一解析才作排列观察，仍不证明审批真实有效或实际实施合法。否定、附条件及整改意见不改为批准。

原始失败与修复：
- SQLite回归直接更改主体后旧参考代拒绝执行（1 failed/13 passed，execcb8187），测试现先断言ReferenceRunSuperseded，再建新参考代验证主体门禁；未放宽产品快照保护。
- 联合回归首次误列不存在的signature_chain/ruleset测试文件，收集未完成（execdfcaa9），不能算通过。改用真实signature_form_matrix/form_clues文件。
- 随后1 failed/230 passed（exec36c7b0）：旧事实版本断言v7需随新抽取器更新为v8；新增正式回归验证历史v7原值/引文保留。
- 只读审查gpt-6.1-sol复现分号截断、后续通知/检验报告借字段、意见整改要求被模板词误排除（正式7 failed/14 passed，exec00ce80）。改为显式字段行读取，意见保留分号/续行，模板判据限非意见区域，独立标题共享切段。
- 修后模板填写说明可藏在意见续行（4 failed/23 passed，exece259bc）；明确填写说明/模板/示例/样表头退出意见状态。新增共享报告标题误切“应提供设备检验报告”（1 failed/27 passed，exec34f1a8）；共享切段排除义务/要求句首，真实独立标题仍截段。

最终：10文件245 passed、10 warnings、18.04s（session87765，execbf7a89）。覆盖方案、在岗、证书、设备、material_facts、签章表单、V14、report_document、指纹；前序240/244通过是最后修复前的历史结果。OpenSpec strict（execac16cd）、git diff --check（exec1678e1）通过。2026-11-19.11 / rs-df5c06a7d1fda95a显式expected_version及raw_manifest_hash核对ok（execb21d40）；raw_manifest_hash=cc163250e62bfc45749d6105d968387a7984cc175c1e009496624b28ac66e849。

gpt-6.1-sol最终只读纯内存反例及接线复核无新增阻断，未独立重跑整套，不是外部/跨模型验收。主8.5/8.6仍部分：真实签章与审批效力、适用性、多页/其他布局、发包人同意与实际实施/进场时序待完成。未调用Provider、重跑真实任务、部署或修改历史报告；未跑全量pytest/npm build。
