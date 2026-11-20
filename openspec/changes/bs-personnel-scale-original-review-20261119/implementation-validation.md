# DG/TJ人员规模原件纠正：本地切片验收

当前状态：用户已明确选择“按原件纠正现行规则，保留历史”。本切片已实现并完成有限回归；不代表人员专业/职称核定、真实材料或全地域现行效力验收。旧确认及旧报告保留。原发问/用户选择证据见.audit/bs-review-improvement.tsv的staff-scale-confirmation-evidence行。下方实现前记录仅作为历史证据保留。

## 已实现与范围

personnel_adequacy.v2按原件纠正一级公路5km/15万平方米/2亿元大型与二级及以下完整三档；高速/隧道去除旧稿跨行导致的虚假“原文冲突”。单项大型条件可证明最高档，低档必须排除全部适用维度的较高档。低合同额独立保留preliminary_scale、missing_fields及中文待核说明，NaN/Infinity/负数/布尔值不作有效规模值。

A2-07复用完整分包范围段、原合同额含税门禁及可读页，不使用60字预览计算。路基米/公里、路面平方米/万平方米与桥梁单跨/桥长保留全部候选、页码和冲突，不取首值。混合类别、否定、近似、范围值、暂定、冲突和未读范围不能给确定档位。完整合同明确“仅承担路基/路面”且没有另一类道路内容时，核该单项和合同额；普通提及不能排除未知维度。evidence保存适用字段、档位完整性、初判及候选。规模待核不遮蔽独立法定安全员不足。

复用finding→service→report链路，显示中文档位及附录A物理页。basis_clauses/review_basis登记原件表3.3.3（物理12页）、表3.4.1（13页）、附录A（28–32页），review_flow s3冻结。原件有原生文本层，使用既有clause引用模式，未放宽pdf_page的OCR companion门禁。human_verified=false、online_knowledge_verified=false、knowledge_id=null；没有修改线上知识库。external实施日由2017-12-01修为原件2017-06-01，保留旧快照。A2-09共享范围解析标签补“分包工程内容/劳务施工范围”，已回归安许范围。

## 最终执行与身份

最终两组命令在backend目录运行，文件互不重叠：

```powershell
rtk proxy .venv/Scripts/python.exe -X utf8 -m pytest tests/test_bs_personnel_adequacy.py tests/test_bs_special_operation_coverage.py tests/test_bs_safety_permit_scope.py tests/test_bs_basis_validation.py -q
rtk proxy .venv/Scripts/python.exe -X utf8 -m pytest tests/test_bs_ruleset_deployment.py tests/test_bs_a2_kernels.py tests/test_bs_report_version_http.py tests/test_report_template_v14.py tests/test_bs_report_consistency.py tests/test_bs_task_report_readiness.py -q
```

2e957e：122 passed，1 warning，1.94s；e1a272/457fb8：207 passed，103 warnings，35.61s。合计329 passed，覆盖纯内核、A2入口、合成名单真实抽取→service两次幂等→report同源、固定依据、规则版本、历史HTTP和发布门禁。警告为既有配置及Starlette/AnyIO/JWT弃用。正式回归保留，一次性脚本已删除。

保留失败：原件纠正首轮12 failed/14 passed；完整范围接线7 failed/26 passed；范围值及独立安全下限3 failed/35 passed；固定依据误用pdf_page曾5 failed/50 passed，改为原生clause模式后通过。本次单项范围反例c77eaf为4 failed，修复后通过。一次adequcy路径拼错、一次误引不存在的test_bs_ruleset.py均无测试运行，不计通过。历史中间结果不累加到最终329项。gstack已启动并detect出仅tests入口；本仓跨切片未提交改动较多，采用上述有界测试，不宣称其全树READY_FOR_SHIP。

code_revision=2026-11-19.21，ruleset=rs-61756954bd422716，raw_manifest_hash=1ec170291d2d0fcce845e38a8b04fc520218284f411ba0f81cc15843a6d3a22c。源码清单artifacts/bs-local-source-baseline-20261119/ruleset-21.json，共334文件，source_manifest_sha256=26b61859698b3f023b7a1b892cb5a26a571394057ac23cf0e62a1f8db107b7b7；.20保留历史。未修改material_facts.v13或V14 mapping版本。

这是featurelyl1118未提交本地源码，不是部署身份。另获用户授权的main测试服务固定运行4a65a17，8090沿用现有Docker bs_review，8089候选已停，见C:/project/nanjin/bs-main-test-runtime/README.md。本切片pytest使用隔离SQLite与外联阻断，不操作该业务库。未部署.21、未跑真实材料、未调用OCR/Provider/Embedding或写线上知识库，未Git提交。

人员岗位专业/职称阈值、全版式规模归属及真实业务验收仍未完成；主4.8和全目标保留未完成。task12主体冲突、报告1/2材料、三类签章样例沿既有待确认项处理。持久目标active，本切片完成不表示项目全量收尾。

## 实现前确认记录（历史，已被上述实现状态替代）

状态：仅完成来源复核、反例与规格；产品实现未开始。不是人员规模功能验收通过。

## 取得的证据

- 原PDF44页，SHA256=3ae2ff0dda06215ec32d44849f80fdc6fbd66200c10d8fa23eae84c7b3b6e905，与既有授权来源README登记一致。公开副本、选页文本、旋转后的第28/29页图和hash位于 docs/plus/blockchain-supervision/evidence/DGTJ规模原文-20261119/manifest.json。
- 实际查看第28物理页表格行列，原件中一级公路大型为路基≥5km、路面≥15万㎡或合同额≥2亿元；二级及以下有完整三档。旧稿把后者错移到前者，并将二级及以下误写空白。
- 第12/13物理页岗位人数表及劳务人数表也实际视觉核对，未发现同样错位；其两张临时渲染图已删除。第28页临时图删除，归档旋转图保留用于复核。
- 物理1–3页明确2017-06-01实施。当前personnel_adequacy.meta日期正确，旧external依据登记2017-12-01有差异，需随获准切片纠正；旧知识库未更新。
- 已执行既有determine_scale纯函数三例诊断，输出保存于同目录current-rule-counterexamples.json：一级公路6km→当前medium（原件large）；二级公路12km→当前small（原件large）；桥梁仅低合同额→当前small且未披露缺单跨/桥长。此为反例诊断，不是新pytest通过或新规则已启用。
- CodeGraph显示run_personnel_staffing_check只传contract_amount_yi，未传合同里其他规模参数；finding未展示scale_info中的解释和初判状态。

来源研究及建议见 docs/design/2026-11-19-dgtj-scale-original-alignment-review.md。精确原始下载URL本轮未恢复；未据此宣称完成2026年全地域现行性认证。

## 待确认选择

先按用户要求调用grill-me / grilling，一次一个决策：推荐按原件纠正当前规则并保留旧确认/旧报告；另一选项为保留业务自定义分档且明确标明与原件差异。问题已经异步提出，尚未收到答复，不将默认推荐项或经过时间视为同意。此前的task12主体及报告/签章材料问题不重复询问。

此前“二级及以下按小型”已登记为已确认口径，因此本次不静默覆盖。主0.1/4.2早期完成记录保留历史，当前入口及旧评审稿开头已追加原件错位警示。当前产品仍为2026-11-19.20 / rs-f0058076f0307ebf，没有新规则身份。

## 已执行与未执行

OpenSpec strict已验证本切片有效（5af672），尚未实现，不作功能通过声明。最终diff与334源码清单只读核对及关键执行指针写入.audit/bs-review-improvement.tsv的staff-scale-*行。

未运行新的pytest、npm build、OCR、付费Provider、真实业务重跑、知识库写入、服务重启、部署或Git提交。没有读取真实任务私有快照。持久目标保持active；其他不依赖本决策的原文核对可继续。
