# 合同倒查公共流程复用：P1验收记录

日期：2026-11-19。分支：`feature/contract-retrospective-shared-flow`。目录：`Engineering-Subcontract-Verification-Agent-retrospective`。

## 目标与结论

P1“双合同上传与受理闭环”已完成代码及定向自动回归验收。双合同的真实比较尚未实现，不把受理成功或报告保存当作P2比较完成。现有8090、5174及后端服务未切换；未写共享业务库。

## 完成范围

- 总包和分包（专业/劳务）两侧均必需；同角色两文件、缺任一侧、读取中/失败、已读无正文、原件哈希缺失均不可创建作业。
- 复用公共上传页的归类、材料台账、原件预览、替换/移除与总包来源集；倒查不提供报审资料包，不要求资质/社保等准入证明。
- 多文件正文/附件/补充协议关系持久化，受理前前后端独立校验；前端以服务端readiness为准，直接API不能绕过。
- 开始时冻结两侧材料ID、角色、原件哈希、修订号、来源集与关系；读取两侧正文并核对冻结身份，变更则在Provider前停止。
- 替换沿用原生后台摄取和原子CAS；成功启用新版，失败保留旧版和原件。失败候选移除后恢复就绪。多文件替换后重新确认当前清单关系/来源集。
- 新建、已有任务、关系保存及刷新恢复均使用倒查启动接口；不调用准入五步或强制人工复核。项目文本连续输入，失焦保存，启动前补存未保存字段。
- 旧单合同报告继续读取/导出；报告不恢复OCR噪声说明、技术指纹及导出时间。

## 验证证据

后端独立SQLite与模拟Provider，共31项相关测试通过：

- `tests/test_bs_retrospective_input.py`：19 passed（门槛、权限、关系保存、来源集、版本变更、空正文/缺原件、两角色替换成功/失败）。最后补入解除/重新关联断言，对应单项再次通过。
- `tests/test_bs_retrospective_runtime.py`：10 passed（前一增量记录；双侧冻结、自动报告、原生文本上传与导出等）。
- `tests/test_bs_review_mode.py`：2 passed（前一增量记录，模式兼容）。

前端Vitest/JSDOM，共37项相关测试通过：

- `RetrospectiveWorkspace`、`generalContractSourceSet`、`reviewModeSelect`、`uploadDropFeedback`、`taskHeaderModel`：25 passed。
- `reportWorkbenchImport`、`reviewWorkbenchModel`、`uploadFiles`、`uploadFailureMessage`：12 passed。

`openspec validate bs-retrospective-shared-flow-20261119 --strict`通过；`git diff --check`通过。

初次新增替换回归因隔离测试缺少测试用SECRET_KEY失败；补齐测试环境后19项通过，未修改真实配置。正式回归保留，未新增一次性诊断文件。未执行npm build。

## 验收边界与后续

本阶段没有真实Provider、真实浏览器及两份业务PDF完整运行验收，这些属于P4。总包与分包均被冻结和读取，但当前仍仅对子合同运行原有规范/内部一致性检查；运行保留总包尚未比较的内部状态，不能宣称双合同核查完整。

下一阶段P2：项目/主体/工程范围关系识别、有效上游要求及例外抽取、同范围条款配对、双侧原文校验、持久化比较结果并加入八列表。付款85%/80%不能仅凭差异认定违法；历史/现行风险继续分开。P3随后接入公共核查页、三阶段流程条、事件时间线与报告容器。P4最后在独立数据和上传目录完成真实试用验收。
