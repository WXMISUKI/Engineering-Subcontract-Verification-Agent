# 履约担保与保险事实本地验收

独立已读附件标题分别提取履约保函、保证金凭证和保险单；项目/合同号、编号、申请人/受益人/担保人/投保人/被保险人、担保额/保额/保费/比例、期间与原文状态各自保留候选。多值不取首值，失效/否定原样保存，不以保费替代保额或以保险替代履约担保。

A3-04-security接run_signature_chain、主体确认与参考代门禁、signature_chain重跑目录及报告人工提示。固定undecidable/manual_prompt，不进入机器问题/附件分母/签署条件。security.records按material-facts.v12追加，旧v11原值保留，重复执行幂等。118号第六条首次/重新承揽专业分包、总包同比例/工期覆盖仍需人工确认，不机械要求劳务适用同种担保。

首抽取15 passed（exec2ce1d2），接事实/SQLite后3文件57 passed（execf8b9ba）。自查后继投标/预付款保函借字段正式红测2 failed/16 passed（exec9e32c5），补保函标题边界。gpt-6.1-sol只读复现正文“不替代保险单/应另行提供保险单/不构成保证金缴纳凭证”误切导致后续失效状态丢失，正式3 failed/18 passed（exec46d134）；边界排除否定/责任句，真实独立保函/凭证/保险单仍切断。

最后7文件138 passed、1 warning、4.15s（execb7915a），另报告V14/一致性2文件48 passed、10 warnings、5.00s（execc38e3c），分进程合计186项。前序135通过（exec ec946c）早于最后否定句修复，不当最终。gpt-6.1-sol最终纯内存和结构只读复核上述边界、金额/类别隔离无新增阻断，未独立跑套件，不是外部验收。

本地未提交2026-11-19.15 / rs-c87ee980f212df80，facts.v12、V14 mapping=2026-11-19.8。显式version/raw_manifest_hash比对ok（execac6673），raw_manifest_hash=cc163250e62bfc45749d6105d968387a7984cc175c1e009496624b28ac66e849。最初回放命令工作目录已在backend却多写backend/导致找不到Python（exec2c92a4），纠正后完成；该失败不算回放通过。最终task12同快照已有文本回放0条（exec465999），原SHA仍3f18cd3f4a306d4fb20c5adec87c770e7dc91c172bab06395bb4f9cfe6cef62f，统计保存在artifacts/bs-task12-readonly-20261119/security-coverage-rs-c87ee980f212df80-final.json；不证明无担保/保险或不合规。

主8.11仍部分：真实适用性、保函/凭证真实性、总包担保比例、合同工期覆盖、保险险种/受益范围及全流程验收待办。未新OCR/Provider、真实重跑、部署、历史任务/报告写入、Git提交、全量pytest/npm build或浏览器验收。文档/tasks及审计先收尾，再执行最后规格/差异检查并追加实际结果行；规则指纹不证明源码封版或部署。
