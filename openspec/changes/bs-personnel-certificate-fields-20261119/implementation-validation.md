# 人员证书字段切片本地验收

同表行保留职称、证书类别/编号/有效期及冲突候选。独立证照按页和标题段抽取，实际证件与名单声明分别保存。新增A2-10复用身份证优先匹配、逐主体cert_validity执行、重跑目录和V14白名单；字段齐备仍为待核实，不改A2-01关系、A2-02期限或A2-05项目经理B证的既有判定。

material-facts.v5通过新批次追加personnel.records，多人/多证保留数组，不回写旧批次。正式SQLite回归验证重复解析/重复服务执行幂等、旧事实不变、同名异号/跨主体不借证、无号候选与未读页不放行，报告仅进入待核实且影响/建议不误写证照失效或已证明缺证。

原始失败与修正：版本递增后既有测试硬编码v4导致1 failed/71 passed，更新预期为v5。只读审查gpt-6.1-sol发现后继“特种作业操作证”和“营业执照副本”未截断；补第一证书姓名/编号/日期/引文隔离断言后2 failed/17 passed（transcript2168）。统一标题识别与边界并支持正/副本，19 passed（2178）。含冒号的“证书类别：建造师注册证书”不误当下一文档标题。

前序10文件328 passed、10 warnings、24.23s（session95634；transcript2118），早于上述最后边界修复。修后4文件90 passed、10 warnings、8.94s（session90771；transcript2203）：人员证书、material_facts、V14、规则指纹。不将328项冒称最终全量。相同聊天原始记录：C:/Users/SZZG/.codex/sessions/2026/11/18/rollout-2026-11-18T23-02-48-01a1ef2e-8ea0-7a31-ac52-e04fc4d6dafa.jsonl。

前端显示名单声明/证照标题、字段及冲突候选、身份待核实或字段对应状态；同一人员同页多证按引文保留，使用主体及person_key分组。只读审查发现候选字段被省略，现已修复。最终纯函数+React服务端静态渲染2文件5项通过（709ms），覆盖冲突可见性和不误提示本视图缺社保。命令：`rtk proxy node --max-old-space-size=128 node_modules/vitest/vitest.mjs run tests/personnelClues.test.js tests/personnelCertificateView.test.jsx --environment=node --pool=threads --maxWorkers=1 --minWorkers=1`。默认Vitest及单fork两次因主机VirtualAlloc/heap分配失败，无测试完成，不算通过；只读系统查询提交可用约556MB，未终止其他进程。纯函数和服务端渲染适用Node环境，此结果不等于浏览器交互验收。

规则2026-11-19.8 / rs-d133a890af61c316，V14 mapping=2026-11-19.2；保留change_scope_complete=false和既有变更声明。OpenSpec strict、git diff --check通过。只读审查者实际身份按任务记录为gpt-6.1-sol，复核修复后无新增阻断，未独立重跑整套；不是跨模型或外部验收。上轮审查回复中的gpt-6-astra标签与任务身份不一致，已更正，不作为额外审查证明。

主4.1/4.8仍部分：布局/其他证件形态的全字段覆盖、岗位专业规模/职称判定尚需补齐；完整身份证只是字段对应，不证明人证真伪。原值及引文仍在任务权限内，隐私治理未完成。真实人员材料、实际到岗、浏览器、Provider与部署未验收。
