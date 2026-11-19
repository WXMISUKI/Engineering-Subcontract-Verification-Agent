# 安许官方正文及许可行业本地切片验收

## 范围与最终身份

本切片取得并归档397号2014第二次修正和128号2015修正的官方HTML及文本，A2-09仅判断许可行业与合同明示施工作业对应。资质专业、等级、规模仍由A2-04另核，证照有效性由A2-02另核；不证明证照真实性、实时吊销状态或实际安全条件。完整性研究见docs/design/2026-11-19-safety-permit-primary-source-review.md。

最终本地未提交源码为2026-11-19.18 / rs-a8ab641f0572a542；raw_manifest_hash=2abb1b2f177c2027a020b864df0dcd4b33715ad79eaaac4c0f0ad7713c3ac956；V14 mapping=2026-11-19.10；material_facts仍v13。源码清单artifacts/bs-local-source-baseline-20261119/ruleset-18.json，334文件，source_manifest_sha256=53ca7044bf92b3f2886fb6c4256c8d597425210a4c4c4c2b371eebd7784f6a61。HEAD仍4a65a17af412ebdce43ec681a993f5103ec1811c，不代表已提交、镜像或部署。

## 来源与接线

- 397号HTML SHA256=b2f2c2c6ebc49e8aa2e718624a2026367689e1791075e91acfc2575f7d807a4a，文本24条；128号HTML SHA256=6f1620e73cca4677bd8aa98634572968a9e3372ac2f5eb67471417e4d2ffe42e，文本30条。manifest列条号/文本行号、版本与文本哈希，路径为docs/plus/blockchain-supervision/evidence/安许官方正文-20261119/。
- 旧HTML有旧版完整正文，旧转换PDF5页丢失正文；不能把两者都称缺全文。旧PDF/上传记录保留，review_basis.external_documents的SAFETY-PERMIT-397仍quote=null、pending_verification，未改线上知识库。
- 复用basis_clauses、review_basis、basis_index及review_flow的s2固定快照。A2-09主引MOHURD-128-2015#第二条，SC-397-2014#第二条补充。源URL、文件及哈希随document冻结；HTML无PDF页码，不造页码。human_verified=false，verification=official_local_text_reviewed，online_knowledge_verified=false；本地机器核对不冒称人工校对或全行业现行性核验。
- basis_clauses/basis_index补入RULESET_FILES，原文或索引变更触发规则指纹变化。读取基准日早于2015-01-22时不适用新规则。

## 行为与反例

同主体独立证照、唯一建筑施工范围、完整合同范围段均为明确施工活动、读页与基准日完整且无限制时，仅该行业维度符合。混合供应/制造/租赁、爆破等交界、泛化工程、否定/条件文本、主体/范围冲突、重复字段、证照限制或未读保持待核；未抽到证照原页保持缺项，不推导企业未取许可。

合同范围使用完整段落，空行和任意数字续行不截断；多页未完整取得不借后页补齐。读页检查同时核连续页码、正整数及重复页，不能只信degradation标记。全service主体归属门禁未放宽，旧报告与快照不回写。

## 执行结果与失败保留

当前会话为C:/Users/SZZG/.codex/sessions/2026/11/18/rollout-2026-11-18T23-02-48-01a1ef2e-8ea0-7a31-ac52-e04fc4d6dafa.jsonl。以下是实际输出，先前通过数不代替后续源码验收。

1. fa35f9：1 failed/94 passed，测试把报告分节“通过”误写成逐项“符合”，已修测试并收窄行业摘要。
2. bf7e29：212 passed；后续新反例826165为6 failed/47 passed，定位完整证照后继限制/注销/重复字段及空行/数字续行截断，已修产品。
3. f09298：1 failed/217 passed，限制范围已正确待核，但测试强求另一提示字符串，已修测试；279bb3为1 failed/53 passed，暴露无degradation但页码缺2仍通过，已修连续页码检查。
4. 6f6dc0：219 passed/10 warnings/5.87s；db4d75：125 passed/49 warnings/25.52s。仅对应.17中间态。
5. 固定依据接线后的最终6文件230 passed/10 warnings/6.38s（ba24cf，会话5029行）；另3文件125 passed/49 warnings/25.82s（bc826a，会话5045行）。文件无重叠，共355。没有全量pytest、npm build、真实浏览器或真实Provider验收。

在backend目录执行：

```powershell
rtk proxy .venv/Scripts/python.exe -X utf8 -m pytest tests/test_bs_basis_validation.py tests/test_bs_ruleset_deployment.py tests/test_bs_safety_permit_scope.py tests/test_report_template_v14.py tests/test_bs_a2_kernels.py tests/test_ai_scope_run_strategy.py -q
rtk proxy .venv/Scripts/python.exe -X utf8 -m pytest tests/test_report_document.py tests/test_bs_report_consistency.py tests/test_bs_task_report_readiness.py -q
```

警告为既有配置文件提示、Starlette/AnyIO及JWT弃用。正式回归保留，一次性脚本均内联执行。

只读诊断错误也保留：数据文件创建前读取失败、分段索引越界、证据目录创建前不存在、误读不存在的basis.py/test_bs_ruleset.py、basis_clauses误用documents及review_basis字典切片KeyError；均改用实际schema/路径，未掩盖业务失败。一次PowerShell未显式UTF-8导致显示乱码，改用Python -X utf8；本轮AGENTS.md磁盘不存在，遵守用户提供的指令。网络研究404/验证页/gzip乱码及附件入口Out of memory局限已记研究文档，不重试无关网络。

## 已有真实文本的有限回放

artifacts/bs-task12-readonly-20261119/permit-industry-coverage-rs-b28f09f6d34a4304.json只对应.17纯函数内核回放：2条完整合同范围、reading_complete=true、安许证照0条，源主体与内存合同分包人两个场景均not_included。它绕过service主体门禁，不能当主体核查通过；没有实际证照原页，不能证明企业没有安许或正向材料验收。没有在.18重跑真实任务，不把这份旧回放标为.18证据。私有快照SHA仍3f18cd3f4a306d4fb20c5adec87c770e7dc91c172bab06395bb4f9cfe6cef62f。

## 未完成与下一入口

主8.1及旧安许切片第6项保持部分。真实安许原页与同主体合同正反向验收、线上知识库新正文入库及精确knowledge_id引用、全行业当前效力/特殊许可边界仍未完成。本地归档不等于线上修复；355项不等于真实业务或部署验收。下一步沿集中验收入口核真实主体/报告1和2材料、签章生产来源、资质/人员阈值原文及隐私执行契约。

未执行新OCR、付费Provider、真实业务重跑、知识库写入、服务重启、部署或Git提交。主持久目标保持active。
