# 技术负责人职称与规模：本地切片验收

状态：featurelyl1118未提交源码已实现并完成有界回归。持久目标active，主4.8仍部分完成。main测试服务独立固定4a65a17、8090继续可用；本切片没有部署到main，也未操作其Docker业务库或上传卷。

## 完成范围与证据边界

A2-14复用personnel_adequacy执行组、逐主体范围、名单/证书身份证优先关联及完整分包范围规模。仅职称证书、专业技术资格证书、专业技术职务任职资格证书中的明确工程技术职称别名参与等级核对：初级覆盖小型，中级覆盖小型/中型，高级覆盖全部。等级不足使用“已提交等级未覆盖”及具体补正提示，不定性违法分包，不证明全部岗位资格、执业专业、证书真实性或全地域现行效力。

名单声明不替代实际证书；姓名候选、异主体、身份冲突、未读、未知职称、证号缺失、字段多候选及规模不明保持待核。独立高等级证书可覆盖规模，同证号等级/字段冲突不能由另一份高等级副本消除。明确劳务合同不机械新增技术负责人要求；明确类型冲突不被材料role兜底覆盖。通用合同抽取修复“劳务分包”内“分包”短词抢占，原有报告独立投影及显示标签保留，报告投影回归更新为修复后的规则语义。

独立附件中明确名单标题紧接姓名/职务及身份或职称表头时，允许未设电话列；仍按同一行提取。无标题、合同表及中间插入下一文档不能因此提升为名单。material-facts.v14采用既有追加批次，旧v13事实不改。

原件DG/TJ 08-2225-2017第4.2.2条已在前序实际渲染并视觉核对物理15页（印刷8页），page-15.png保留并补入归档manifest。PDF SHA256=3ae2ff0dda06215ec32d44849f80fdc6fbd66200c10d8fa23eae84c7b3b6e905。basis_clauses、review_basis、A2-14索引及s3固定快照已接，沿用B强度、human_verified=false、online_knowledge_verified=false、knowledge_id=null、source_url=null。不编造精确下载URL，不修改线上知识库或旧报告。

service两次执行不累积A2-14；真实合成文本经实际名单/证书抽取、逐主体执行、报告原始项及V14实际附件行得到同源结果。修改证书后新结果可变化，数据库内旧报告快照不变。未确认主体、异主体、未分配证书和旧事实追加均有正式回归。这里的“实际抽取”指正式合成文本进入真实函数，不是用户原PDF/OCR、真实Provider或完整浏览器验收。

## 最终验证

在backend目录执行，单次最终命令15个正式文件，不与中间运行累加：

```powershell
rtk proxy .venv/Scripts/python.exe -X utf8 -m pytest tests/test_bs_technical_title_qualification.py tests/test_bs_personnel_adequacy.py tests/test_bs_personnel_certificate_fields.py tests/test_bs_contract_roster_layout.py tests/test_bs_material_facts.py tests/test_bs_report_contract_type_feedback.py tests/test_bs_basis_validation.py tests/test_bs_special_operation_coverage.py tests/test_bs_ruleset_deployment.py tests/test_bs_a2_kernels.py tests/test_bs_report_version_http.py tests/test_report_template_v14.py tests/test_bs_report_consistency.py tests/test_bs_task_report_readiness.py tests/test_bs_a1_kernels.py -q --tb=short
```

执行d6e440/1d8650：460 passed、103 warnings、63.64s。覆盖人员内核、无电话名单边界、主体隔离、service幂等、旧事实/报告保护、V14附件行、固定依据、规则身份、A1/A2入口及发布/历史HTTP门禁。警告为既有配置及Starlette/AnyIO/JWT弃用。测试使用隔离SQLite及外联阻断，没有连接main业务库。

本切片OpenSpec strict执行630b3a通过；最终源码diff检查0f5e7d通过。保留正式回归，两个临时JSON维护/身份生成脚本运行后已删除。未执行npm build、真实Provider/OCR、业务重跑、知识库写入、服务重启或Git提交。本次是有界检查，不宣称全仓READY_FOR_SHIP，也不称自审为独立审查。

保留失败记录：前序首次未实现入口、18 failed/61 passed定位无电话名单与别名层级；本次99249d复现4 failed/112 passed，修复具体核对动作及劳务短词抢占后77c9df为116 passed。080860新增历史保护测试1 failed/44 passed来自测试漏填版本trigger，补齐测试必填字段后通过。旧失败未删除或以弱化断言绕过，最终成绩只计上述460项。

## 源码身份与运行边界

- code_revision：2026-11-19.22；ruleset：rs-4f46b00656d3ebf8。
- raw_manifest_hash：06016d43b7eb58f46a556505becbb1a71762e4eb05dcf7279ad0e8eddcd21b04。
- V14 mapping_version：2026-11-19.11；material-facts.v14。
- 源码清单：artifacts/bs-local-source-baseline-20261119/ruleset-22.json，共335文件；包含新增技术职称正式测试，.21保留历史。
- source_manifest_sha256：207453204d9ace337d9da194db453ae24edc118dc6d0bc53a224571deb8dd1e9，算法沿用按路径排序紧凑JSON到原始文件SHA256映射。

bf5688逐文件实核335项及canonical hash、当前规则/原始清单身份，并验证PDF/文本/三张归档页图共5项hash。10d14d独立main工作树仍干净；ede1b6经LAN地址8090返回health ok。此为本机访问LAN地址，不代替其他测试电脑网络验收。main固定镜像身份、已有12任务数据及备份边界见docs/handoff/2026-11-19-main-local-test-service.md。

## 下一任务

主4.8继续保留：其他岗位执业专业/资格、其他版式证件字段、真实劳动/社保例外材料、真实性及真实业务验收。task12主体冲突、报告1/2原材料及三类签章样例沿既有待确认事项处理，不再次推定或静默更改。其余依据完整性、费用适用比例、履约及隐私契约按docs/handoff/2026-11-19-acceptance-entry-checklist.md依次推进。
