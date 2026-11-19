# 费用与工资责任事实切片验收

范围：复用合同正文识别，新增逐材料/逐页/独立文档合同段遍历；sub/labor已读合同中保留安全费和工资责任完整原文及主体/材料/页码。比例、金额和税口径只记录未绑定含义的提及，不把同段税率、违约率指定为安全费计提率，不根据待核实的1.5%直接判法定符合或不符合。

RC-A10-cost、RC-A11-wages接run_completeness、既有主体/参考快照门禁及completeness重跑目录。固定undecidable + manual_prompt；报告显示来源，不进入机器问题、附件分母或签署条件。未知主体仍为普通待核门禁。contract.responsibility_records按material-facts.v10追加，重复执行幂等，历史v9事实原值保留。合同应开专户/应代发/应实名不证明实际履约，不要求分包自行开总包专户。

正式验证和原始失败：
- 首轮抽取12 passed（exec5341e5）。接SQLite后1 failed/68 passed（execcfbc7c）：义务句以名单结尾，被当独立标题截断；修后3文件70 passed（exec8eaf2d）。
- 自查正式5 failed/18 passed（exec408969）：银行回单/工资支付凭证/工资发放表/实名制登记表未截段，以及页号1→3断档仍沿用合同范围；增加金融凭证边界和页码连续约束，23 passed（execeaea62）。
- gpt-6.1-sol只读审查发现“本合同约定/施工总承包人应提供工资证明”误切和合同副本标题丢跨页起点。正式7 failed/23 passed（execc50880），改为句内义务动词和有限合同标题后缀归一，修后30 passed（exece3cf7f）。未放宽独立证明边界。
- 助动词最后修复前10文件270 passed、10 warnings、18.36s（session7682、exec2644d0）。覆盖责任、material_facts、方案、合同名单、完整性提示、规则指纹、基础事实、V14及文档和合同语义。前序263项（session12354）早于最后审查修复，不当最终通过。
- 最后助动词反例正式2 failed/30 passed（exec718c3c），允许有限修饰语及需/需要后进入最终联合。整进程在248项后出现MemoryError（exec34c056），不是通过；当时可用物理内存约600MB、虚拟内存约725MB（exec1ca772）。分进程验证：report_document独立83 passed/9.68s（exec6ffe84），其余9文件189 passed/8.27s（exec424123），合计272项全部通过，但不称同进程全套通过。

当前本地未提交版本2026-11-19.13 / rs-5c23d26f9ad6504a，material-facts.v10、V14 mapping=2026-11-19.6。显式expected_version和raw_manifest_hash校验ok（execa7f8f5）；raw_manifest_hash=cc163250e62bfc45749d6105d968387a7984cc175c1e009496624b28ac66e849。规则指纹不绑定整套源码，不表示已部署。

task12已有文本最终隔离回放（exec2ea444；前序exec3a55a7）提取17条主题/来源候选：安全费1条（分包第13页）、工资16条（14/15/16/24/32/35/37/38/39/40/50/55/56/69/70/71页）。这是主题段记录，不能当17条已验证的责任或实际履行证明。未向报告或历史任务写入。统计与引文哈希保存在artifacts/bs-task12-readonly-20261119/responsibility-coverage-rs-5c23d26f9ad6504a-final.json；初始回放文件保留。源快照SHA256仍为3f18cd3f4a306d4fb20c5adec87c770e7dc91c172bab06395bb4f9cfe6cef62f。

未调用新OCR/Provider、重跑真实业务、部署、写历史报告或Git提交；未跑全量pytest/npm build，没有前端改动或浏览器验收。主8.8/8.9仍部分：法定原文/适用性与计提基数、实际单列和提取、实名名册/专户/代发流水及完整真实业务验收待办。

gpt-6.1-sol最终只读纯内存复核已发现的义务语法/副本跨页/真正独立文档反例无新增阻断，未运行套件，不是外部验收。

最后产品代码修改后、文档收尾前的OpenSpec strict（execaff762）及git diff --check（execf6724a）通过；它们不证明随后补写的验收文档/tasks状态。gpt-5.6-sol日志复核已指出这一表述问题；记录更正后会再校验文档及工作树，并以原始执行输出为准。源码清单artifacts/bs-local-source-baseline-20261119/ruleset-13.json记录327源文件，摘要0d6ac5d972fa4eadeab189db521ba8b58cfc6e2575f562a5af29dcc662871a0e；本地分支featurelyl1118/HEAD=4a65a17af412ebdce43ec681a993f5103ec1811c，未提交差异单列哈希，后续仅文档修改不使该源码摘要失效。

可解析原始证据：C:/Users/SZZG/.codex/sessions/2026/11/18/rollout-2026-11-18T23-02-48-01a1ef2e-8ea0-7a31-ac52-e04fc4d6dafa.jsonl:3494（初接线失败）、3518（金融凭证/断页红测）、3565（义务/副本红测）、3613（助动词红测）、3637（MemoryError及最终回放）、3670（83项）、3677（189项）、3687（最终strict/diff/源码清单）；会话首行cwd已核对。
