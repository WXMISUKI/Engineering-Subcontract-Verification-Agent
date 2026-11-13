# 阶段 0：逐规则来源依赖与闭环现状矩阵

盘点日期：2026-11-12

状态：只读源码盘点；业务范围口径已于 2026-11-12 获用户确认，作为阶段 1 输入冻结。法规原文及逐规则法律解释仍须规则/合规负责人校核。
范围：当前独立版仓库中的材料准入、规则目录/内核、工作区、报告门禁和补件代际；不是法律意见或真实任务验收。

本次只读源码、配置、OpenSpec 与测试索引；没有修改业务代码、访问数据库、运行模型或改写历史任务。工作区原有未提交改动未触碰。

## 结论摘要

1. 当前 `classify.py` 将总包按 0/1 份处理；专业/劳务分包合同合计至少 1 份才满足硬准入。用户已确认：多份独立总包且关系不明是归属错误；同一总包的分册、附件或补充协议可明确关联为一个来源集。当前实现尚无来源集关联能力；未知归属仍是软提示。
2. A1 规则并未统一执行“总包可选”的语义。T15-01/02/03/05 将总包侧证据缺失落为 `undecidable`；W16-03/V16-02 则按来源闸跳过 finding，现有报告范围声明作披露。
3. A1 以外未发现总包合同作为整个核查组硬来源的规则。某些抽取器允许从总包读取证照/关系材料，不等于必须上传总包。
4. `reporting.scope_statements()` 检查当前有效总包材料是否存在；规则执行侧用已解析且有文本的总包列表。已登记但不可读时，规则可能已跳过而报告不显示限制，来源状态存在不一致。
5. scope statement 不是 finding。若可选来源未覆盖通过“删除 finding”处理，但不把范围限制传入 opinion conclusion，零 `hit/undecidable` 可能被派生为“通过”。必须把范围覆盖纳入报告结论投影。
6. 待处理 API/工作区以 finding 为中心；没有独立的中性范围提示类型。不能把提示塞进 finding 或普通缺件计数。
7. 补件已有新核查代和历史快照保护机制；范围提示还需跟代际绑定，不能因上传而提前解除。

## 材料准入与来源状态

| 对象/状态 | 当前事实 | 来源 | 处理含义 |
| --- | --- | --- | --- |
| 总包角色 `general` | 0 或 1 份可选；超过 1 份为硬归属错误 | `backend/app/plus/blockchain_supervision/classify.py:3-10,62-83` | 缺失不得作为任务准入失败；多份仍需更正 |
| 专业/劳务分包角色 `sub/labor` | 合计至少 1 份为启动硬门槛 | 同上 | 不得因总包可选而弱化分包核查基础 |
| 未知角色 | 软缺口，提示更正归属 | 同上 | 与可选总包缺失不同 |
| 总包 absent | 当前准入允许 | `classify.py:74-79` | 仅影响依赖总包的具体规则 |
| 总包 processing | 当前材料读取/报告 readiness 保留运行中保护 | `service.py:5171-5470` | 不能冒充 absent 或成功覆盖 |
| 总包 terminal unreadable / 无必要页 | 规则输入按 `parse_state == done`/可读文本筛选；报告范围说明当前只查 role 是否存在 | `a1_check.py:167,3345-3349`; `reporting.py:79-123` | 当前可能出现“规则跳过但报告没有限制说明” |
| 总包 subject/合同链不明 | 材料/关系抽取可能识别为未解析；不能当作可信总包来源 | `contract_party_evidence.py:52-93`; `subject_reviews.py:30-86` | 仅影响使用该来源的规则，保留归属/冲突事实 |

## A1-03/A1-04/A1-05 规则目录：24 项

下表来源角色是代码当前的证据读取方式，不代表法律解释已获业务负责人复核。`transfer_rules.json` 元数据的法规 PDF 来源状态仍为 `ocr_extracted_pending_manual_proofread`，需在规则语义冻结时核对原文。

| 规则 ID | 当前来源依赖 | 当前缺总包表现 | 阶段 0 判断 |
| --- | --- | --- | --- |
| T15-01 | 必需侧含 general 总承包范围 + sub 分包范围/主体/整体转移 | general_scope 缺失进入 `undecidable` | 规则级依赖，不是任务级必传；应确认纳入范围限制 |
| T15-02 | 必需侧含 general 总承包范围 + 分包范围、标段和覆盖关系 | general_scope 缺失进入 `undecidable` | 规则级依赖，不是任务级必传；应确认纳入范围限制 |
| T15-03 | 采购/租赁责任明文只从 general 搜索；合同、发票、供应商等从证据池搜索 | 缺 general 责任侧时进入 `undecidable` | 规则级依赖；不能把责任证据从分包文本借用 |
| T15-04 | 现场管理、人员、劳动合同、社保/工资、项目负责人等证据池 | 没有 general 专属必需侧 | 不得因缺总包而跳过；自身材料不足仍按规则处理 |
| T15-05 | 劳务主体/整体范围侧从 general 搜索；价款/管理费从 general/sub/labor/packet 证据池 | general 范围侧缺失进入 `undecidable` | 规则级跨来源依赖；与 AI 选页是否可缩页是两个契约 |
| T15-06 | 合作/联营/个人承包及工程范围相关证据池 | 没有 general 专属硬依赖 | 有 general 可读取，但缺 general 不应自动限制 |
| T15-07 | 依赖 W16/V16 排除结果的占位规则 | 当前自身恒为 `undecidable` | 当前不完整是规则实现缺口，不能归因为缺总包 |
| T15-08 | 工程款、资金流、期间等证据池 | 没有 general 专属硬依赖 | 不应把缺总包传播成范围限制 |
| T15-09 | 联合体施工/管理/管理费证据池 | 没有 general 专属硬依赖 | 不应把缺总包传播成范围限制 |
| T15-10 | 开放条款目录登记，当前无确定性内核 | 不形成一般执行 finding | 不进入总包缺源矩阵；保留开放条款披露 |
| W16-01 | 分包主体、资质及条件证据 | 未声明 `requires_general_contract` | 按自身来源执行；不依赖总包在场 |
| W16-02 | 主体/负面清单及分包范围 | 未声明 `requires_general_contract` | 按 sub/labor 主体相关范围执行 |
| W16-03 | `requires_general_contract=true`；证据只来自总包合同 | 没有已解析总包时跳过、不产 finding；报告范围说明披露 | 可作为推荐的 skip + scope disclosure 模式，但需补 terminal unreadable 状态 |
| W16-04 | 分包合同以他人名义承揽相关证据 | 未声明总包硬依赖 | 不应因缺总包跳过 |
| W16-05 | 劳务名义施工分包关系 | 未声明总包硬依赖；与 L17 存在去重关系 | 按劳务/分包关系执行，来源缺口需按自身证据处理 |
| W16-06 | 再分包或劳务作业转交证据 | 未声明总包硬依赖 | 按主体/合同链证据执行；缺链不等于缺总包 |
| W16-07 | 开放条款 | 不形成确定性业务finding | 不进入总包缺源矩阵 |
| V16-01 | 监理签审和发包人书面同意，依赖 A3-01/报审材料 | 不依赖总包来源 | A3 缺失/未执行按自身依赖处理 |
| V16-02 | `requires_general_contract=true`；总包合同与分包合同原则对照 | 无可用 general 时跳过、不产 finding；报告范围说明披露 | 推荐模式；当前未区分 absent/unreadable/unlinked |
| V16-03 | 现场管理机构、派驻人员、分包范围证据 | 未声明总包硬依赖 | 按分包/劳务材料执行 |
| V16-04 | 开放条款 | 不形成确定性业务finding | 不进入总包缺源矩阵 |
| L17-01 | 劳务合同、劳务费与主要/周转材料款关系 | 未声明 general 硬依赖 | 按劳务来源执行 |
| L17-02 | 劳务承担技术/质安职责与承包人职责对照的合同证据 | 未声明 general 硬依赖；文本词表包含承包人/总包称谓，但不等于 general 文件必需 | 矩阵按真实证据读取范围复核，不按词项猜测来源必要性 |
| L17-03 | 开放条款 | 不形成确定性业务finding | 不进入总包缺源矩阵 |

**来源位置：**规则定义在 `backend/app/plus/blockchain_supervision/data/transfer_rules.json`；T15 读取/计算在 `a1_check.py:918-1931`；W/V/L 目录执行及 general 来源闸在 `a1_check.py:2001-2007,3295-3357`；catalog IDs 共 24 项。

## A1 其他规则与其余核查组

| 规则组/目录 | 当前材料来源 | 对总包的判定 | 主要来源 |
| --- | --- | --- | --- |
| A1-01 金额占比 | 总包金额作分母，分包/劳务金额作分子；`amount_ratio.enabled` 当前默认 false | 若启用则具有总包硬证据依赖；缺总包现在产 `undecidable`。它是条件启用规则，不能作为默认任务准入条件 | `a1_check.py:165-257`; `data/config.json:6-18`; `review_flow.json:d1` |
| A1-X-WORKLOAD | 从 sub/labor 合同抽取工程量比例；配置默认 false | 未发现总包必要依赖 | `a1_check.py:632-678`; `data/config.json` |
| A1-02 负面清单 | 按已确认分包主体读取 sub/labor 合同，21 个叶项 | 不依赖总包 | `a1_check.py:413-`; `negative_candidate_plan.py:40-71`; `negative_list_2024.json` |
| T15-07 / 其他固定转包规则 | 见上方 24 项矩阵 | 缺总包只影响表内显式依赖规则 | `a1_check.py` / `transfer_rules.json` |
| A3-01 完整性：RC-08、RC-09、RC-A01..A10、RC-P11..P16、RC-L11..L14 | 报审包及专业/劳务合同中内嵌报审表；按主体类型裁剪目录 | 不依赖总包为硬来源；自身目录缺件可形成 finding | `a3_check.py:26-28,849+`; `attachment_source.py:4-12`; `report_checklist.json` |
| A3-02 签字链/审批时序 | `legal` 与 `group` 双轨签字链；packet 和 sub/labor 开工日期/签署证据 | 不依赖总包 | `signature_chains.json`; `signature_chain.py:120-124,413-422` |
| A2-04 资质匹配及证照抽取 | 资质匹配主范围为 sub/labor；证照识别允许 general/sub/labor 作为候选材料 | 总包可贡献证据但不是必要材料；缺总包不应跳过 | `a2_check.py:86-90,810-818` |
| A2-01 人员一致性/社保 | 名册、岗位、身份、雇主与社保关系，以 sub/labor 作为合同名册来源；general 被明确排除于 roster | 不依赖总包 | `personnel_evidence.py:207-217,446-453`; `a2_check.py:676+` |
| A2-03/A2-05 证件有效性/一致性 | 扫描已解析材料中实际证件及身份字段 | 总包可为证据候选，但无全局总包门槛 | `a2_check.py:250+`; 具体适用字段按规则配置 |
| 负面清单候选 | 按主体读取 sub/labor 合同及任务级授权范围 | 不依赖总包 | `negative_candidate_plan.py:40-71` |
| 内部信用/事故记录 | 任务内显式内部记录材料；general/sub/labor 合同本身不作为内部记录 | 不依赖总包 | `internal_records.py:49-57,99+` |
| MoMA AI 辅助核查 | A1 finding 的候选页上下文；selector default-off；当前映射注册表为空 | T15-05 的 general 来源是安全缩页覆盖条件，不是任务准入条件；无覆盖时 selector 回退 baseline | `ai_scope_evidence_graph.py:224-225`; `ai_scope_mappings.py`; `ai_scope_selection.py`; `bs-moma-ai-scope-selection/spec.md` |

## 当前状态从 finding 到报告/补件

| 环节 | 当前契约 | 对可选总包缺失的风险 | 来源 |
| --- | --- | --- | --- |
| Finding 状态 | `BsFinding` 使用 `hit/miss/undecidable` | T15-01/02/03/05 与启用时 A1-01 会把缺可选来源放进 `undecidable` | `models.py:314+`; `a1_check.py` 各 rule kernel |
| `/findings` 与 `/todo` | findings 暴露三态；todo 从 findings 聚合缺件/无法核实 | 当前没有独立 scope limitation API/工作区对象；伪装 finding 会计入问题和处置 | `router.py:1585-1605,1661-1698,1765+` |
| finding 人工回执 | 只对 hit/undecidable 等实际 finding 做 accepted/ignored | 中性范围提示不应复用 review receipt | `router.py:1765+`; `TodoGroups.jsx` |
| 报告范围声明 | `scope_statements()` 对 `requires_general_contract` 规则做未上传总包说明 | 只检测 active role 存在；未检测解析、可读页或主体关联 | `reporting.py:79-123` |
| Formal report gate | blocking `undecidable` 可阻断确认版；执行 readiness 另行校验 | 若缺总包生成 `undecidable`，会挡报告；如果只删 finding 而不投影 limitation，门禁可能错误放行 | `reporting.py:305-322`; `router.py:1927-1951,2000+`; `service.py:5171-5470` |
| Opinion conclusion | `hit` 优先失败；任一 `undecidable` 返回 pending；零 hit/undecidable 可能返回 pass | 未覆盖规则不进入 counts 时可能被误称通过，需 scope-aware conclusion | `reporting.py:1437-1443` |
| Preview/export | 多个输出入口共用报告/意见书构造与 readiness；但需逐入口回归 | 所有格式需读取同一个 generation scope projection | `router.py:2000-2169`; `reporting.py:1399,1721+` |
| 补件进入编辑 | 先创建 review version 快照，保留旧 finding/报告 | 历史代适合冻结当时范围；不可随当前材料动态改写 | `service.py:6497-6575`; `models.py:298+` |
| 补件激活/发布 | 建计划、校验材料后新代重跑，完成后发布；候选未发布阻止正式输出 | 上传本身不能解除限制；必须由新代 coverage 成功确认 | `service.py:6578-6700`; `report_output_readiness()` |
| 任务收口 | 所选组完成、readiness通过、blocking undecidable 处置完才完成 | 仅 scope limitation 不应进入 finding 处置门禁，但仍必须披露 | `service.py:5474-5571`; `router.py:1411-1421` |

## 规则状态表（阶段 1/2 的输入草案）

| 来源/执行事实 | 规则状态投影 | Finding | 报告/任务门禁 |
| --- | --- | --- | --- |
| 可选总包不存在，规则有总包依赖 | 本代该规则未覆盖，原因 `optional_source_absent` | 不产生 | limitation 单独披露、不单独阻断、不当作符合 |
| 可选总包正在解析 | source processing，规则结果尚未冻结 | 不产生由缺源导致的 finding | 既有材料运行/readiness gate 继续阻止过早正式收口 |
| 可选总包终态不可读/缺必要页 | 依赖规则未覆盖，精确原因 `source_unreadable`/`required_pages_unreadable` | 不产生可选来源缺失 finding | 披露受影响规则；其他无关规则继续 |
| 总包主体/合同归属冲突 | 来源 attribution unresolved/conflicted | 不作为违法 finding；材料归属问题独立保留 | 只限制相关规则，遵循既有冲突处理门禁 |
| 规则自身必需的分包材料/事实不足 | 规则执行后 `undecidable` | 产生 | 按现有真实待核门禁处理 |
| 规则执行未运行/失败/部分完成 | execution incomplete/failed | 不伪造业务结论 | 按既有执行 readiness gate 处理 |
| 新代已正确归属、读取来源并成功执行受影响规则 | 新代覆盖完成 | 根据证据正常输出三态 | 新代提示解除；旧代快照不变 |

## 阶段 0 业务确认记录

用户于 2026-11-12 确认按本矩阵进入阶段 1，确认的业务范围为：

1. T15-01/02/03/05 在缺少总包时仅标记相关规则本轮未覆盖并显示范围提示，不产生 finding；规则原文与具体法律解释仍需规则/合规负责人校核。
2. W16-03/V16-02 保持跳过 finding、披露范围的方向，并在后续补齐来源终态状态。
3. 不依赖总包的规则不因总包缺失而跳过或受影响；规则自身必需材料不足仍走原有真实 `undecidable` 语义。
4. 仅存在范围限制且其他门槛满足时允许出具明确披露限制的报告，不得称完整覆盖或完整通过；真实未完成执行与真实阻断项继续受原门禁约束。
5. 条件开启的 A1-01 金额比例规则当前默认关闭，不属于本次已授权启用范围；如后续开启，须单独验证其来源覆盖和门禁，不得把总包缺失扩散成任务级准入失败。
6. 材料处理中仍沿用既有运行门禁；终态不可读、必要页缺失或来源归属不明时仅限制实际依赖该来源的规则并披露具体原因。多份独立总包且关系不明时仍阻断；同一总包多文件须经用户明确关联后作为一个来源集处理。当前代码尚不支持该关联，必须在业务 flag rollout 前补齐并隔离验收。

该确认冻结的是产品范围与报告语义，不替代规则/合规负责人对法规证据和单条规则实体标准的审定。阶段 0 已完成；阶段 1 契约见 `domain-api-contract.md`，不代表业务代码实现、开关启用或发布已获批准。
