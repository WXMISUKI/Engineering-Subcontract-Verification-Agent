# 逐台设备事实切片验收

独立清单、检验报告、使用登记、权属证明和租赁合同按设备段或表格行抽取；内部编号、出厂/产品编号、使用登记号分体系保存。同主体且至少一个共享编号体系唯一一致才显示对应候选；共享编号冲突、候选冲突或清单重复编号不对应，不按设备名猜身份。清单“已检验/合格”不能替代独立报告。

A2-12接run_personnel_staffing及personnel_adequacy kind，与配备检查一起替换以保持幂等。固定undecidable + manual_prompt，报告沿用独立履约提示且保留原文/页来源，不计机器问题、缺项、附件分母或签署条件；普通主体门禁仍生效。equipment_facts保留旧pages/rows并追加records，rows补来源，material-facts.v7追加新批次，不回写历史。

只读审查gpt-6.1-sol复现“机械名称”表头漏识别及Markdown分隔行生成伪设备。新增双表头正式回归，原始2 failed/11 passed（exec337869）后统一按name标签识别表头并跳过全分隔行。修后8文件221 passed、10 warnings、16.40s（session43708，exec4e0039），覆盖设备、在岗、证书、material_facts、V14、report_document、规则指纹与finding_semantics。前序219项是修复前中间结果，不追认为最终。未跑全量pytest/npm build、真实Provider、业务重跑或部署。

规则2026-11-19.10 / rs-1d2abe85e2870650；显式expected_version和raw_manifest_hash比对ok（execbe592d），raw_manifest_hash=cc163250e62bfc45749d6105d968387a7984cc175c1e009496624b28ac66e849。V14 mapping=2026-11-19.4，material-facts.v7。OpenSpec strict通过（exec4dc0bb）。gpt-6.1-sol最终只读复核两种表头/对齐分隔行纯内存复现及接线后无新增实质阻断，未独立重跑整套，不是外部或跨模型验收。

主8.4仍部分：本切片未识别特种设备适用性、法定检验周期或设备是否满足施工需要，未证明报告真实性或真实入场验收。实际多页/其他布局和真实设备验收继续待办。
