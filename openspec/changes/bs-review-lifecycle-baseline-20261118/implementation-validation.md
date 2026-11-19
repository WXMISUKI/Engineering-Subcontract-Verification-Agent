# 第一阶段验收记录

日期：2026-11-18；分支 featurelyl1118；当前聊天 01a1ef2e-8ea0-7a31-ac52-e04fc4d6dafa。

## 范围与原始失败

先核对主变更 bs-review-dimension-basis-p0-20261118 与两份同日交接，不追认其他批次的 208/666 项或 task12 旧报告。工作区既有67个已跟踪变动文件以及多项未跟踪成果，本阶段不全量暂存、不改写其余成果。

原命令（backend）：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_standalone_workflow.py::test_uploaded_pdf_starts_and_runs_checks_with_unavailable_knowledge -q
```

结果：1 failed，5.87s。原第103行要求 draft DOCX 返回200，实际409 `draft_file_output_blocked`。同时观察到旧测试在上传后触发合同双路抽取，因未配置模型失败，说明合成Provider注入时机过晚。

## 契约与修复

- `publication_status=ready` 与 `publication_state=candidate` 可并存：前者仅表示 findings 可查看/复核。交接声称两者互斥不准确，未据此修改产品。
- 草稿JSON、草稿文件、正式报告和收口为独立权限。保留草稿文件保护；候选经过复核和 `/complete` 才发布。
- 测试在上传前注入合成模型，关闭本用例不负责验证的合同双路抽取后台启动。
- AI测试经真实SQLite持久队列 enqueue/claim/worker 执行。旧用例的虚构job ID无法被worker认领；不能把一次无操作调用当作AI完成。
- 用四阶段组织现有回归：初轮执行与AI门槛；补件冻结/访问隔离；候选执行/复核/发布/导出；失败替换保留旧版本。补充重复收口不新增版本的检查。
- 本阶段未改发布产品逻辑，未放宽任何门禁；另修复下述规则版本登记缺口。

## 已执行验证

| 验证 | 命令或证据 | 结果 |
| --- | --- | --- |
| 漏登修复前的规则显式比对 | `ruleset.verify_deployment_fingerprint(expected_version=ruleset.json.version)` | ok=true，checks包含预期/实际均为 rs-62bdecd7d223b9e4；仅证明当时已登记输入一致 |
| 漏登修复前的规则原始清单哈希 | 同上 | 5d230fa425ea4bc8f5e2f1bcd20b4c55f509a81ae5ed001170e73d4d71382706；最终版本见下文 |
| 完整合成HTTP链路 | `.venv/Scripts/python.exe -m pytest tests/test_standalone_workflow.py -q` | 1 passed，78.76s；包含正式DOCX/证据包、幂等、访问隔离和候选失败历史保护 |
| OpenSpec | `openspec validate bs-review-lifecycle-baseline-20261118 --strict --no-interactive` | valid |
| 相邻定向回归（指纹登记修复前） | 见下方命令 | 93 passed，35.95s |

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_bs_supplement_publication.py tests/test_bs_supplement_review_retention.py tests/test_bs_task_report_readiness.py tests/test_bs_ai_review_jobs.py tests/test_bs_background_ai_review.py tests/test_bs_ruleset_deployment.py tests/test_bs_report_version_http.py -q --disable-warnings
```

## 限制与后续

- 未跑全量pytest、npm build、真实浏览器、真实Provider；未部署、未写真实任务、未重跑task12/报告1/报告2。
- 所有流程验证使用pytest临时SQLite、合成PDF与合成模型；不能据此认定法规或真实材料业务验收通过。
- gstack启动因WSL无 `/bin/bash` 失败，按其降级说明继续原生工具；没有执行gstack运行时审查或遥测。
- 已有Starlette/anyio/JWT依赖警告未在本阶段扩展处理。
- 主变更4.1/4.3/4.5遗留、六态映射和合同不符合口径继续按完善计划推进；第一阶段不据猜测更改业务判定。

## 规则指纹登记缺口与修复

检查第二阶段的前置数据时发现：已参与当前人员配置/负面清单判定的 `personnel_adequacy.json`、`negative_list_applicability.json` 未进入 `RULESET_FILES`，特种作业目录也未登记。显式比对只能证明已登记输入一致，不能发现漏登数据变化。

行为回归在临时目录复制规则数据，分别改变安全员法定下限、桥梁范围关键词、可接受证书目录，再用变更前冻结版本比对。修复前 `tests/test_bs_ruleset_deployment.py`：3 failed / 3 passed（0.46s），三个变化均未使冻结版本失效。

修复：登记三份数据；保留既有A2-04变更声明并补充A2-01/A2-07/NL及W16-02影响；`change_scope_complete=false` 禁止把本次治理增量当作已穷尽的局部重跑范围。没有修改真实阈值、任务或历史快照。

新版本：`rs-343403134db285ac`；`code_revision=2026-11-18.4`；原始清单哈希 `c72e78318d1f47f43b65ea2fac00f8aa4ee06f378267fdadcc73aa874c62f894`。使用明确 expected_version 校验通过。

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_bs_ruleset_deployment.py tests/test_bs_personnel_adequacy.py tests/test_bs_negative_applicability.py tests/test_bs_special_operation_catalog.py tests/test_bs_a2_kernels.py -q --disable-warnings
```

结果：150 passed，2.58s。登记修复后再次运行完整HTTP及相邻7文件（含新增指纹场景），97 passed，275 warnings，110.26s；执行会话89685，退出码0。没有用先前93项替代最终输入验证。

## 证据与只读审查

本聊天原始执行记录：`C:/Users/SZZG/.codex/sessions/2026/11/18/rollout-2026-11-18T23-02-48-01a1ef2e-8ea0-7a31-ac52-e04fc4d6dafa.jsonl`。第123行记录原始1 failed；第225行记录修复后完整流程1 passed；第249行记录相邻93 passed（行号为审查时位置）。

只读子代理审查确认未放宽发布门禁，提出补齐验收台账、任务勾选和原始执行证据指向；已据此更新。审查者自报模型 gpt-6.1-sol，未独立重跑测试，因此不宣称跨模型或外部独立验收。

增量复核未发现代码阻断，要求标明顶部旧指纹属于修复前，并补稳定的原始执行指针；已落实。上述 transcript 第328行（修前3 failed/3 passed）、336行（新指纹）、337行（150 passed）、360行（strict valid）、364行（97 passed）为本次复核时位置。
