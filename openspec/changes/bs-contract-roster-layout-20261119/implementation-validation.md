# 合同人员表真实布局覆盖验收

task12只读调查发现：总包合同90页、分包合同76页均有已有解析文本。当前人员抽取对分包第45/73页独立“分包人主要项目管理人员表”返回0，源页实际为无电话、岗位在姓名前及紧连行；不将零抽取解释为无名单。

新增有限入口仅sub/labor+精确独立标题+前6行明确岗位姓名/名称姓名职务列头，沿用既有人员抽取/RC-A09入口；按有限岗位/资格行语法提取姓名/岗位与qualification_declaration。总部与现场分区只取现场，同行资格保留为合同声明，不填实际certificate_number/id_number，不改变合同角色或扩大证照/社保来源。前端映射contract_roster为名单声明并显示“合同资格声明”；无号仍需身份核对。

验证：初轮正式2 failed/6 passed（exec66d180）复现两种布局漏出；实现后3文件50 passed（exec04f5c3），SQLite及A2-10幂等/事实10 passed（exec6f370d）。gpt-6.1-sol只读复现同页重复独立表跨段借行/重复、模板文件名可出声明；正式红测2 failed/10 passed（exece25a9e），补同精确标题边界及模板文件名排除。最后10文件271 passed、10 warnings、19.16s（session13642，exec785869）；前序269项早于最后边界修复，保留为历史。前端Node单线程纯函数/React静态渲染2文件7 passed、612ms（exec4604b4）；不等于浏览器交互验收。

版本2026-11-19.12 / rs-07502f98d16b88ea，material-facts.v9，V14 mapping仍为2026-11-19.5。显式规则版本/raw_manifest_hash比对ok（execcca979）；原OpenSpec strict（exece2104e）和git diff --check（exec4fd6f7）早于最后边界修复，不能作为最终检查。gpt-5.6-sol日志审查指出后，已在最终271项回归之后补做strict（execf43366）及diff检查（execb4467e），均通过。raw_manifest_hash=cc163250e62bfc45749d6105d968387a7984cc175c1e009496624b28ac66e849。gpt-6.1-sol最后只读复核两缺陷及来源边界无新增阻断，未独立重跑整套，不称跨模型或外部验收。

本切片为未提交的本地改动，不是可部署封版。修复后源码路径/SHA256清单保存在artifacts/bs-local-source-baseline-20261119/ruleset-12.json，325个源文件清单摘要87d29aab12aa3555d16aa583d767e3fb9bab5505eef6ebabb5f1c598ba0a93de；记录分支featurelyl1118、HEAD和已跟踪差异摘要，不声称仅凭规则版本可复现全部源码。

补充可解析原始记录：C:/Users/SZZG/.codex/sessions/2026/11/18/rollout-2026-11-18T23-02-48-01a1ef2e-8ea0-7a31-ac52-e04fc4d6dafa.jsonl:3090（初始2 failed/6 passed）、3180（审查2 failed/10 passed）、3211（最终271 passed）。方案中间失败位于同文件2781、2800、2829、2911、2930行，补齐短exec ID的定位限制。

真实已有文本隔离回放：artifacts/bs-task12-readonly-20261119/manifest.json和extraction-coverage-rs-07502f98d16b88ea.json。原材料14第45/73页各8条声明，共16条来源、8个主体内姓名候选，不代表16人或8个身份已证实人员。9条含原文资格/职务声明；实际人员证书仍提取0，不证明源材料不存在其他证照或其资格不合格。原快照SHA256=3f18cd3f4a306d4fb20c5adec87c770e7dc91c172bab06395bb4f9cfe6cef62f，回放前已确认未变（exec8554de）。快照含业务原文，保留工作区且不得提交/公开。

未重跑真实业务、改写历史报告、部署、付费Provider、新OCR、全量pytest或npm build。当前容器与task12历史规则均rs-86a79735eea01064，不能冒称已部署.12。主4.1/4.8仍部分：其他布局/真实证件/岗位资格及全流程业务验收待办。
