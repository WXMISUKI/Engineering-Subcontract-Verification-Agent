# 在岗与项目归属事实切片验收

考勤/任职记录按独立标题、姓名段或显式表格列提取项目、日期/期间和原文状态；承诺仍是声明，工资/社保不替代实际出勤。复用身份证优先关联，同名异号、跨主体不借事实，无号仅候选。A2-11固定undecidable + manual_prompt；已确认主体内提示不进入机器计数、问题/缺项/签署条件，未知主体或归属冲突继续按既有机器门禁处理。

接run_person_consistency及person_consistency kind，避免按kind替换时清除人员配备。正式SQLite回归验证重复执行幂等和run_personnel_staffing互不清除。material-facts.v6追加personnel.records；V14独立人工提示明细具原文/页来源，前端分开显示考勤、任职和承诺候选。完整V14文档回归确认提示可见、统计/附件/签署条件不变，忽略机器统计并不丢掉报告明细。

只读审查gpt-6.1-sol发现两类分段缺口：括号后缀执照/证书可向考勤借项目/日期（红测2 failed/15 passed）；既有到岗承诺抽取可吞后续考勤/证照的身份证和单位（红测2 failed/17 passed）。已新增document_boundaries公共独立标题边界供证书、考勤、supplement三入口复用，支持正/副本/括号，不将冒号字段当标题。此函数只用于切段，不能认证实际证照或证明义务履行。

验证：前序8文件237 passed/16.21s与10文件254 passed/16.41s早于最后共享边界修复，保留为中间历史；修后9文件247 passed、10 warnings、16.17s（session34456），含在岗、证书、身份、关系例外、material_facts、语义门禁、V14、report_document、指纹。前端Node单线程纯函数+React静态渲染2文件6 passed、1.33s；不等于浏览器交互验收。未跑全量pytest或npm build，未调用真实Provider/业务重跑/部署。

当前规则2026-11-19.9 / rs-ea8814605661c004，expected_version显式比对ok，raw_manifest_hash=cc163250e62bfc45749d6105d968387a7984cc175c1e009496624b28ac66e849；V14 mapping=2026-11-19.3，material-facts.v6。OpenSpec strict与git diff --check通过。政策解读问题3的B类引文仍pending_verification，没有自动法定判定；单份/多项目记录不证明持续在岗或同时受聘，缺记录不证明缺勤。主8.3仍部分，实际履约/项目核对与真实材料验收待办。gpt-6.1-sol只读复核共享边界、退休/分支兼容及前端展示后无新增阻断；该审查未独立重跑整套，不是外部或跨模型验收。
