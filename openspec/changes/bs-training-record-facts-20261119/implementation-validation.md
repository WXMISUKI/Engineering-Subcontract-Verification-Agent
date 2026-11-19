# 培训记录事实本地验收

独立已读附件培训/签到/考核/交底记录按标题段及同表逐人保存项目、课程、日期、工种、姓名/身份证候选和原文结果。多值冲突保留候选，不将签到等同考核合格，也不将培训代替特种作业证或准予上岗。只对已抽记录出提示，不将管理人员名单当全劳务人员培训义务分母；无记录不推断未培训。

A2-13接run_personnel_staffing及A2参考组，沿用主体归属/确认和参考代门禁；training.records按material-facts.v11追加，旧v10批次不回写。固定undecidable/manual_prompt，通用证据及V14报告人工提示保留原文，不进入机器问题/附件分母/签署条件。V14 mapping=2026-11-19.7。

原始执行：首抽取14 passed（exec4c3860）。接事实和SQLite后17 failed/55 passed（execf6edb7）：模板头在标题段外未排除、既有极简material无parse_state导致AttributeError，以及测试未建立旧参考代就期望Superseded。修复安全默认读取及页级模板头；测试先经run_a2_checks建立参考代，未放宽产品保护。修后3文件72 passed（execa79e67）。重复表头红测1 failed/16 passed（exec8e1489），修为第二个姓名表头处停止此表，避免伪姓名或跨表公共字段借用。

8文件联合初1 failed/242 passed（execeeeb51）：既有无确认主体的person_consistency幂等测试只预期A2-01，旧在岗路径A2-11也会返回普通归属门禁；现断言两条待核归属门禁且重跑不累计，未改产品。最终8文件243 passed、2 warnings、5.89s（execa66271），另报告V14/一致性2文件48 passed、10 warnings、4.66s（exec383963），不同进程、合计291项，未跑全量pytest/npm build。

当前未提交本地版本2026-11-19.14 / rs-e007ce6b7fd336c0，显式version/raw_manifest_hash比对ok（execa58860）；raw_manifest_hash=cc163250e62bfc45749d6105d968387a7984cc175c1e009496624b28ac66e849。规则指纹不证明整套源码封版或部署。gpt-6.1-sol只读结构与纯内存反例复核无新增阻断，未运行套件，不是外部验收。

同task12已有文本只读回放0条培训记录（execa58860），源快照SHA仍3f18cd3f4a306d4fb20c5adec87c770e7dc91c172bab06395bb4f9cfe6cef62f。统计文件artifacts/bs-task12-readonly-20261119/training-coverage-rs-e007ce6b7fd336c0.json；零提取不证明未培训、记录不存在或法定不符合。未做新OCR/Provider、真实业务重跑、部署、历史任务/报告写入或Git提交，无前端改动/浏览器验收。主8.10仍部分：真实人员全覆盖、其他布局、培训内容适配与签字真实性及实际上岗时序待办。

文档/tasks及审计先收尾，再执行最后OpenSpec strict/git diff检查，以紧随其后的原始输出为准，避免把较早检查冒称最终文档验收。
