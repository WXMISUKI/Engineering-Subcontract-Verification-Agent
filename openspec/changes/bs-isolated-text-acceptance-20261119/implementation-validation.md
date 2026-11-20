# 已有文本的隔离集成验收

## 范围与身份
当前未提交源码2026-11-19.16 / rs-17428911e1a7e1c6，facts.v13、V14 mapping=2026-11-19.9。逐文件核对ruleset-16.json的333个源文件，变化数0；source_manifest_sha256=fd8bebc795ecbd063ccf3a650b67127964829244ed69bd63f0e99966da8a6af2，不表示部署封版。

输入为既有task12私有快照：2份材料、1个主体，共166页已有文本；SHA256=3f18cd3f4a306d4fb20c5adec87c770e7dc91c172bab06395bb4f9cfe6cef62f，执行后不变。内联Python使用sqlite://内存库和独立合成身份/会话，启用外键，复制基准日、主体确认状态及材料文本。缺失项目/标段元数据和图像签章信号不补造，未复制不存在的packet对象。A1/A3均启用以覆盖集成路径，不是原任务选项的完整复现。

临时目录仅保存合成依据映射，退出自动清除；没有知识Provider绑定，socket connect/connect_ex/create_connection全部阻断。使用原服务initialize_reference_review(ai_auto_groups=[])及五组run入口，不调用start_review/OCR/后台AI。所有在线参考状态均undetermined，不构造已验证在线引用。

## 结果
完整性→A1→签章→A2→内部记录两轮依序完成，分别18/26/6/13/1条，共64条。两轮业务字段摘要均dc762efc3a6bc685c29f686c7085d6e3d3e443081a66e3755412757477c3baf6；主/附证据检查未发现未知材料、主体跨用或页码越界。该检查仅验证引用结构，不证明引文语义与依据正确。

V1.4两轮均available，原consistency_violations及正式文本wording门禁通过；14个问题行、40条待核实、普通manual_prompts=0。这些是测试副本输出计数，不是正式业务结论或人工队列减少验收。材料事实31行重复写入幂等，AI job=0，网络尝试=0。没有持久化完整业务报告或打印业务原文。

统计：artifacts/bs-task12-readonly-20261119/integrated-text-rs-17428911e1a7e1c6-034429020181.json。

## 集成发现：主体名称冲突
复制源主体origin=explicit、professional、type_confirmed=true，但subcontractor_name_evidence在材料14第2页取得的唯一单位名称与主体名称不一致，subject_material_names_consistent=false。因此关系类规则先输出归属待核说明，不会进入下游事实对应/普通人工提示；manual_prompts=0不能解释为无需人工处理。

两名称归一后均12字，相似度约0.583，不能自动视为同一企业或断言只是OCR误差。现有17个责任主题段（安全费1、工资16）仍只是抽取候选，未跨越该主体门禁。需原PDF第2页与主体登记对照后，由有权限的用户确认材料归属或主体；不改源任务、不模糊合并、不把对照当正式业务更正。

无原文统计：integrated-subject-gate-rs-17428911e1a7e1c6.json、integrated-subject-difference-rs-17428911e1a7e1c6.json。单位名只保存单向摘要及差异区间，不展示姓名或企业原文。

## 原件核对与仅内存对照
另用现有容器对材料14作只读SQL事务查原件路径，校验PDF SHA256=4264ff01c7a8b559b069fc5ae60dbb91b8a680ab38948a00de15f8afc1931045、共76页；使用已有fitz渲染第2页。视觉核对证实：源任务登记主体对应合同“发包人（全称）”，合同另列“分包人（全称）”，且现有解析的分包单位与图像一致。该冲突不是单纯名称相似或待猜的OCR错字；未证明录入原因，也不构成修改源业务的授权。临时私有图片已删除，未调用新OCR/模型。

在另一内存数据库只将主体名称改为该页明确分包人，其余输入、五组服务与原门禁不变。两轮分别18/26/3/39/1条，共87条，业务字段摘要均2d2df0d3256668fe15b3efbed03f24bbc07573474d29e23970437a4b0affa2bd。普通manual_prompts=18（费用/工资2、A2类16），问题行16、待核实43；这些均是对照输出，不是已确认问题人数、实际履行或正式业务结论。引用结构/一致性/话术门禁通过、31事实幂等、网络/AI job=0，原快照不变；在线依据仍undetermined。

证据：integrated-original-page-role-check.json及integrated-text-counterfactual-rs-17428911e1a7e1c6-035530123854.json。对照仅检验归属输入对集成的影响，不说明原task12已经修正或完成验收。建议真实验收另建有明确主体和项目元数据的独立任务，保留源task12，不自动改写done任务。

本轮PDF技能目录版本已漂移，首次按26.1116路径读取失败，定位到26.1117.11355后读取成功；应用依赖定位工具返回错误，回退容器既有fitz渲染，没有安装或升级环境。初源码清单样例误将files字典切片导致KeyError，仅只读诊断失败，随后按items读取；不影响源文件核对与两组回放结果。一次性回放为内联脚本，无临时测试脚本遗留。

## 正式合成回归
在backend执行：rtk proxy .venv/Scripts/python.exe -X utf8 -m pytest tests/test_standalone_workflow.py tests/test_bs_report_version_http.py -q。

9 passed、212 warnings、91.81s。警告来自既有Starlette配置/AnyIO及JWT的datetime弃用。回归覆盖合成HTTP工作流、发布/候选与报告版本；不是浏览器、真实Provider、真实材料业务验收或部署。

## 未闭环与下一步
主9.x保持未完成，尤其9.4逐问题依据语义与四处一致仍需业务逐项核验，9.5队列变化没有可信基线。现有文本规则回放不替代真实OCR、角色签章图像、法定适用性或实际履约。

下一批见docs/handoff/2026-11-19-acceptance-entry-checklist.md。新切片规格与文档完成后执行strict和git diff --check；结果以审计追加行记录。不修改产品代码，不重启服务、不重跑历史业务、不提交Git。
