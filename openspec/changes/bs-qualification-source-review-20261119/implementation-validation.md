# 资质官方原文与五类范围本地切片验收

本切片核对地基基础、桥梁、隧道、公路路面、公路路基五类专业承包的159号原版范围，修复可复现规则缺口及同页/跨页多值误用。它不代表全部资质类别、2026年全地域现行效力、实际证书有效性或真实业务验收完成。研究见 docs/design/2026-11-19-qualification-primary-source-review.md。

## 来源与适用边界

官方通知为 https://www.mohurd.gov.cn/gongkai/zc/wjk/art/2014/art_17339_219511.html ，全文为 https://www.mohurd.gov.cn/file/old/2014/20141106/W020191022031714.pdf 。复用已授权来源目录的公开原件，归档至 docs/plus/blockchain-supervision/evidence/资质官方原文-20261119/，含PDF、selected-scope-pages.txt及manifest.json。PDF共230页，SHA256为2d3dcf4484c604eb9e81a8308f32b81d19dea39318301380590b22d7a32ffc16；PDF和选页文本均实际核对manifest哈希。

| 原文 | PDF物理页（从1起） | 本切片核对内容 |
| --- | --- | --- |
| 总则四（七） | 9–10 | 原文的“以下/不超过”包含本数 |
| 13.4 | 87–88 | 地基基础各等级范围，三级构筑物70米限制 |
| 19.4 | 107 | 桥梁单跨与单座总长分别核对 |
| 20.4 | 111 | 隧道二级断面≤60平方米且单洞≤1000米；三级≤40平方米且≤500米 |
| 28.4 | 142 | 路面二级包含一级公路、三级包含二级公路 |
| 29.4 | 147 | 路基二级包含一级公路、三级包含二级公路 |

不能用目前取得的官方文件支持“2026-07-01全国统一实施甲乙级改革”。建市〔2020〕94号为改革方案，2022年公开标准为征求意见稿，不据此生成正式实施日期或等级映射。已将reform_effective_from及相关effective_from置空，甲乙级保持未核，不跨版本换算。未搜到不能证明后续文件不存在。建办市〔2023〕47号要求实际换领同专业二级证书，不能把读取到的旧三级自动升级；换领、有效期及主管部门状态另核。已取得后续调整正文未直接改写本五类范围，不据此宣称全量当前效力认证。

## 产品接线与修复

qualification_scope.json为五类原版条目登记条款、物理页和原件哈希；隧道双参数及构筑物高度、公路等级含本数按原文修复。qualification_scope.py保留同页及跨页的全部规模/公路等级候选；同一字段出现多个不同值时附全部原页证据待核，不取首个较小值通过，也不凭冲突等级直接判超限。相同值重复不产生冲突，其他无冲突字段的确定超限仍可输出问题。

复用basis_clauses、review_basis、review_flow的s2固定快照，登记JS-2014-159-ARCHIVE及六个条款坐标。finding按实际类别显示精确条款和页码，不把A2-04全部类别硬绑定单条依据。human_verified=false、online_knowledge_verified=false、knowledge_id=null；旧external JS-2014-159仍pending_verification。源码中的原文核对不等于人工校对或线上知识库修复。全service主体门禁及历史报告不改写。

## 实际验证与失败

会话为 C:/Users/SZZG/.codex/sessions/2026/11/18/rollout-2026-11-18T23-02-48-01a1ef2e-8ea0-7a31-ac52-e04fc4d6dafa.jsonl 。以下行号指该文件；命令与结果分别定位，不能用中间通过数代替最终源码。

- 5302行命令及结果、5303行工具输出4766ac：15 failed、17 passed、0.25s，暴露范围缺项、含本数错误与无证实改革日期，随后修复。
- 5387行命令及结果、5388行工具输出cca710：3 failed、32 passed、0.20s，复现同页/跨页断面首值误通过和多个公路等级误判，随后修复多值候选处理。
- 中间0443f8为47 passed；05c523为205 passed，253bc1为179 passed，仅对应.19中间态；08b670为158 passed，是多值修复后的部分验证。这些数字不计入最终合计。
- 最终5404行命令及结果、5405行工具输出8da734：5文件210 passed、10 warnings、5.98s。
- 最终5417行命令及结果、5423行工具输出cfe9d8：另4文件179 passed、49 warnings、25.87s。

在backend目录执行的最终命令：

```powershell
rtk proxy .venv/Scripts/python.exe -X utf8 -m pytest tests/test_bs_qualification_scope.py tests/test_bs_basis_validation.py tests/test_bs_ruleset_deployment.py tests/test_bs_a2_kernels.py tests/test_report_template_v14.py -q
rtk proxy .venv/Scripts/python.exe -X utf8 -m pytest tests/test_report_document.py tests/test_bs_report_consistency.py tests/test_bs_task_report_readiness.py tests/test_bs_safety_permit_scope.py -q
```

9文件无重叠，最终合计389 passed。覆盖纯逻辑、A2实际入口、六条固定依据快照、报告与门禁及安许回归；警告为已有配置提示及Starlette/AnyIO/JWT弃用。不是全量pytest、真实浏览器、真实Provider或真实业务验收。正式回归保留，一次性验证均内联执行。本切片没有新task12文本回放。

## 最终本地身份

code_revision=2026-11-19.20，ruleset=rs-f0058076f0307ebf，raw_manifest_hash=3e5b55d40954183e1a38812439abdf2f76ad8a40fabf0d18311eb837e8a6690a。material_facts仍v13，V14 mapping=2026-11-19.10。源码清单 artifacts/bs-local-source-baseline-20261119/ruleset-20.json 登记334个文件，source_manifest_sha256=be5672324636adbd5aa6a0f2992601d3f745c3e57ebc9c402923fa8cba7c5b0e，算法为排序、紧凑UTF-8 JSON的路径至原始文件SHA256映射。生成证据见会话5415行6f53fe。

HEAD为4a65a17af412ebdce43ec681a993f5103ec1811c；这是未提交本地源码，不是镜像或部署身份。.19清单保留历史；.20仅增加Python内核修复及code_revision，原始数据清单hash与.19相同不代表两次源码相同。文档收口后的strict/diff及334源码复核结果追加至.audit/bs-review-improvement.tsv的qualification-final-check行。

## 剩余与下一入口

主0.2保持未完成：其余资质类别、正式甲乙级适用来源和地域过渡规定仍待核；人员专业/规模/职称阈值继续独立研究。真实证照、工程参数归属及其他版式需真实材料验收，不从合成测试推断全量抽取正确。task12登记主体与合同角色不一致、报告1/2材料及三类签章样例仍沿既有待确认入口处理。

未执行新OCR、付费Provider、真实业务重跑、知识库写入、服务重启、部署或Git提交。持久目标保持active，真实闭环及交付任务不勾满。
