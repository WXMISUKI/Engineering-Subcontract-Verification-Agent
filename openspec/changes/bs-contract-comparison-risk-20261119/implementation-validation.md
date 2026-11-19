# 合同语义风险候选切片验收

复用semantic_review.compare_contracts、service.run_semantic_checks、原有可选入口与成本回调；没有新建Provider或执行组。质量、安全、工期、环保、农民工工资五维在请求中明确，每份分包/劳务合同分别与唯一总包合同比较。逐条候选仍须双侧material/page/逐字quote校验；空侧可核验条款缺失时记录限制，不发比较请求。零风险不按期望数量判失败，也不等于合同全部符合。

修复模型风险提议被存成hit并在V14显示不符合：新结果固定undecidable，继承分包侧材料的subject_id，双侧证据保留。真实SQLite服务重复执行→reporting→V14验证只进入待核实，不进入不符合问题清单。原始红测为hit != undecidable；另一次在根目录误运行pytest导致app导入错误，已改backend工作目录，不将其计为通过。

SEMANTIC_REVIEW_VERSION=2；新启动与重跑options快照引用该常量。正式回归直接走start_review/rerun_task/SQLite版本归档，验证新启动=2、新代=2、重跑前归档仍为1、既有历史快照整体不变。测试隔离材料齐套与外部参考库初始化，未替换版本创建或重跑路径。此测试首轮因归档后未提交、被重跑入口rollback撤销而1 failed/3 passed；补测试commit后4 passed、1 warning、1.84s，不改产品事务。

前序9文件联合276 passed、10 warnings、20.61s（session70192），早于语义快照常量增量，不能称其覆盖最终状态。原始记录见`C:/Users/SZZG/.codex/sessions/2026/11/18/rollout-2026-11-18T23-02-48-01a1ef2e-8ea0-7a31-ac52-e04fc4d6dafa.jsonl:1774`。常量及规则.7后4文件26 passed、48 warnings、12.99s（session80502）：合同、补件发布、报告版本HTTP、规则部署。最后新增快照断言后合同文件4 passed、1 warning、1.84s；原始记录见同一transcript第1924行，事件ordinal为1923。测试使用合成Client/monkeypatch且由网络保护fixture禁止外部连接，未调用真实Provider。

只读代码审查gpt-6.1-sol未发现行为阻断，并指出快照断言缺口，现已补回归；该审查不是跨模型或外部验收，也未独立运行整套。规则2026-11-19.7 / rs-57b6941d3b986538显式校验ok，原始记录见同一transcript第1789行。change_scope_complete=false，动态语义条目未冒称完整登记。主8.7仍未完成：可选入口未接为五步必选，真实合同/Provider、浏览器、部署与全维覆盖验收仍待办。
