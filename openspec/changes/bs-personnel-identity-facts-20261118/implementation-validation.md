# 人员事实与身份关联第一切片验收

本切片承接主任务4.8/4.1，未宣称人员全部字段、工资、例外或工种覆盖完成。

## 改动

- 名单/证明/社保表保留完整18位身份证候选；掩码不补，多个候选不择一。复用同一候选正则供既有身份差异观察读取，不改变验真边界。
- 社保拆行证明引文同时保留姓名号码行和期间行，避免只留下续行丢失人员锚点。
- 每页`personnel.records`数组保存多个人及每条来源，沿用事实表唯一键、append-only和幂等。提取器由material-facts.v3递增为v4，不回填历史任务。
- A2-01先主体隔离，再按明示身份证匹配；同名异号、多号或姓名冲突不借证据判通过或违规。无号保持姓名单位候选及人工核对说明。分组键使用号码哈希，号码不拼入提示消息。
- 修复社保离散月份被min/max当连续范围，以及社保表`YYYYMM`期间不可解析；反向或非法月份区间保持待核实。

## 验证

初始8个期间边界用例：5 failed / 3 passed，0.23s。修后与既有A2/社保OCR集：114 passed，2.27s。原始失败保留在本聊天执行记录。

最终相关9文件：253 passed，1 warning，3.73s，覆盖同页多人、同名异号、无号候选、主体隔离、拆行证明、事实幂等及新旧批次、原证书关系/证据投影/转包关系。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest tests/test_bs_personnel_identity_facts.py tests/test_bs_a2_kernels.py tests/test_bs_material_facts.py tests/test_personnel_social_ocr.py tests/test_bs_person_certificate_relations.py tests/test_bs_identity_observation_table.py tests/test_bs_evidence_projection.py tests/test_bs_personnel_completeness_messages.py tests/test_bs_a1_transfer_relations.py -q --disable-warnings
```

OpenSpec strict valid。规则code_revision=2026-11-18.5，version=rs-17a48275f2bbfd9b，明确expected_version校验通过；数据清单未变，raw_manifest_hash仍为c72e78318d1f47f43b65ea2fac00f8aa4ee06f378267fdadcc73aa874c62f894。代码变化由code_revision进入规则指纹；change_scope_complete=false。

完整合成HTTP、发布/报告/版本回归（95846）：24 passed，207 warnings，87.84s，退出码0。使用临时SQLite和合成Provider，覆盖候选失败保留、幂等、跨账号隔离、正式报告与规则版本保护。

本切片未执行真实Provider、浏览器、真实业务重跑或部署；未修改历史事实/报告。原始执行记录沿用本聊天rollout-2026-11-18T23-02-48-01a1ef2e-8ea0-7a31-ac52-e04fc4d6dafa.jsonl。CodeGraph传输断开后，按约定回退rg及源码阅读。

## 只读审查后修正

审查者自报gpt-6.1-sol，执行了一次无写入纯函数复现，指出无号候选仍被汇总符合、历史合同补写可能触发完整v4提取。已修正：任一侧缺少可交叉核对的完整身份证号只形成待核实候选，不把候选单位差异归到本人定性；旧任务合同补写使用独立`persist_contract_facts`，仅在既有批次追加contract.*，不重抽人员，不新建解析批次，原行不变。新增单人缺号、候选单位差异及实际backfill入口回归。

第一轮253项与24项HTTP为上述审查修正前结果，不能代替修正后最终验证。与工资/例外切片合并的相关集：300 passed，1 warning，4.26s（13文件）；当时code_revision=2026-11-18.6，version=rs-b69dc81974375b8b，raw_manifest_hash=8f562469daee1cf4da3411bb56c13b14707ae47807e23741f52ceeb403371888，显式版本比对与两个变更strict均通过。修正后HTTP25254：97 passed、274 warnings、113.62s。

后续复核修正分公司简称、否定审批与工资未关联提示；正式事实latest视图排除模型候选批次，避免合同补写污染候选并遮蔽人员事实。最新联合特种工种17文件435 passed、10 warnings、20.04s；HTTP75704：97 passed、277 warnings、125.08s。指纹rs-6f32dabc8a7b6aec / 2026-11-19.2显式比对通过；随后仅补A2-07/A2-08重跑目录注册，专用8项通过（不以旧435覆盖此后增量）。

证据定位（审查时）：本聊天transcript第491行（期间原始5 failed/3 passed）、593行（253 passed）、601行（.5版本指纹）、602行（strict valid）、634行（24 passed）。后续结果保留在同一原始记录。

可见性限制：身份证候选原值和相关引文仍在受当前任务权限控制的事实/证据中；使用哈希分组键不等于API或报告已脱敏。对外报告和人员明细的脱敏策略列入交付前验收，不能据本切片宣称已完成字段级隐私治理。
