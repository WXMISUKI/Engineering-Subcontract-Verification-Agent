# 设计：分包核查维度与依据补强（P0）

日期：2026-11-18
关联分析：
- `docs/design/2026-11-18-review-dimension-and-basis-gap-analysis.md`
- `docs/design/2026-11-18-dimension-rule-basis-inventory.md`

## 1 目标

在不改变历史报告的前提下，把“依据引用、判定模型、人员/资质/负面清单、签章、报告话术”做成可执行、可追溯、可验收的一组规则与投影。

完成条件：

1. 每条 finding 能反查 `item_id → clause_id → 条款正文`，主体不错位；
2. 报告不再出现“1/21、未命中、无定位的无法判定”；
3. 人员的岗位、数量、社保、特种作业能逐人/逐岗给结论；
4. 资质等级与承包范围能按证书等级和工程规模判定；
5. 负面清单只对适用条目计覆盖；
6. 人工复核队列只保留“待核实”。

## 2 总体架构

```text
材料上传
  ↓
页级阅读台账（已读/未读/低质）＋定向补读
  ↓
事实层（合同、证照、人员、社保、设备、签章）
  ↓
依据库（review_basis / personnel_adequacy / qualification_scope / negative_list）
  ↓
规则引擎（确定性判定 + 证据门禁）
  ↓
判定模型（verdict / action / severity）
  ↓
报告策略（三档结论 + 四段式 + 阅读覆盖）
```

原则：

- 法条只放数据，不写死在 Python；
- 规则只声明“依据、所需证据、判定策略”；
- LLM 只做受控文本润色，不改判定、不新增事实；
- 历史报告与其策略快照只读。

## 3 数据模型

### 3.1 `review_basis.json`

```json
{
  "basis_id": "SHJT-2026-8#第七条",
  "doc_title": "上海市公路工程施工分包管理实施细则",
  "doc_no": "沪交行规〔2026〕8号",
  "article": "第七条",
  "quote": "……项目负责人和技术、财务、计量、质量、安全等主要管理人员必须是本单位人员。",
  "strength": "A",
  "effective_from": "2026-08-08",
  "effective_to": "2031-08-07",
  "subject_scope": ["contractor", "subcontractor"],
  "document_role": "full_text",
  "parent_doc_id": null,
  "content_format": "scanned_pdf",
  "ocr_companion": "01-上海市公路工程施工分包管理实施细则-OCR.md",
  "citation_mode": "pdf_page",
  "source": "上交通委官网/OCR"
}
```

文档角色与格式字段的约束：

- `document_role`：`full_text` / `notice_only` / `attachment_notice`；**规则只能绑定 `full_text`**，通知页不得作为判定依据。
- `content_format`：`pdf_text` / `scanned_pdf` / `docx` / `html` / `doc_ole`。
- `ocr_companion`：扫描件的 OCR 文本文件；检索可用 OCR，引用必须回到 PDF 页码。
- `citation_mode`：`pdf_page` / `html_anchor` / `doc_section`。
- `parent_doc_id`：附件全文指向通知页，如 `10a → 10`、`12a → 12`；加载时校验“通知页不得单独成规则依据”。

### 3.2 `personnel_adequacy.json`

```json
{
  "basis_id": "DG-TJ08-2225#表3.3.3",
  "scale_type": "professional",
  "scale_levels": {
    "small": {"project_manager": 1, "technical_lead": 1, "safety_officer": 1, "quality_officer": 1},
    "medium": {"project_manager": 1, "technical_lead": 1, "safety_officer": 2, "quality_officer": 2},
    "large": {"project_manager": 1, "technical_lead": 1, "safety_officer": 3, "quality_officer": 3}
  },
  "labor_bands": [
    {"min": 0, "max": 49, "project_manager": 1, "safety_officer": 1, "labor_officer": 1},
    {"min": 50, "max": 199, "project_manager": 1, "safety_officer": 2, "labor_officer": 1},
    {"min": 200, "max": null, "project_manager": 1, "safety_officer": 3, "labor_officer": 1}
  ],
  "notes": ["可依据，不判违法分包", "无特种设备可不设机械员"]
}
```

### 3.3 `qualification_scope.json`

```json
{
  "category": "地基基础工程专业承包",
  "level": "二级",
  "standard_version": "建市〔2014〕159号",
  "effective_from": "2015-01-01",
  "limits": {
    "building_height_m": 100,
    "structure_height_m": 120,
    "rigid_pile_depth_m": 24,
    "ground_treatment_depth_m": 10,
    "single_pile_load_kn": 5000,
    "pit_depth_m": 15
  },
  "scope_text": "可承担……",
  "basis_id": "MOHURD-2014-159#13.4.2"
}
```

2026 甲乙级另存一条，`standard_version` 与 `effective_from` 区分；无法确定版本时输出待核实。

### 3.4 `negative_list_2024.json` 扩展

```json
{
  "id": "NL-B-03",
  "domain": "bridge",
  "structure_part": "superstructure",
  "applicability": {
    "requires": ["bridge_superstructure"],
    "excludes": ["pile_foundation", "substructure"]
  }
}
```

### 3.5 `special_operation_catalog.json`

```json
{
  "work_type": "吊装",
  "required_cert_types": ["起重信号司索工", "起重机械司机"],
  "cert_system": ["建筑施工特种作业", "特种设备作业"],
  "basis_id": "MOHURD-2025-60#第三条"
}
```

## 4 判定模型

### 4.1 判定

`符合 / 不符合 / 缺项 / 待核实 / 不适用 / 未纳入`

### 4.2 处置

`无需处理 / 补正 / 说明 / 人工核对`

### 4.3 严重度

`阻断 / 条件 / 提示`

### 4.4 结论

- 有阻断 → 不同意
- 有条件 → 原则同意，附条件通过
- 只有提示 → 同意

### 4.5 决策树

```text
M 未上传 → 缺项
M 已上传但页面未读/OCR失败 → 补读；仍失败 → 缺项（页面不可读）
M 已读但字段未提取 → 缺项（缺字段）
字段唯一且满足 → 符合
字段唯一且违反 A/C → 不符合
字段唯一但低于 B 类推荐 → 符合（未达推荐配置）＋提示
字段冲突/模糊 → 待核实
外部参数缺失 → 待核实
范围不覆盖 → 不适用
任务未启用 → 未纳入
```

### 4.6 待核实三因

1. 同一字段多处冲突；
2. 证据存在但字迹/OCR 无法辨认；
3. 规范要求的外部参数不在材料内且无法推导。

每条待核实必须带“文件 + 页码 + 冲突点/缺失参数”。

### 4.7 与现有状态的适配

| 现有 | 新判定 |
| --- | --- |
| `hit` + `material_missing` | 缺项 |
| `hit` | 不符合 |
| `undecidable` | 待核实（仅三因，否则降级为缺项） |
| `miss` | 符合 |
| 新增 `not_applicable` | 不适用 |
| 新增 `not_included` | 未纳入 |

## 5 P0 规则目录

| 规则 ID | 名称 | 依据 | 所需证据 | 判定策略 |
| --- | --- | --- | --- | --- |
| BASIS-01 | 依据引用校验 | 内部口径 | rule.item_id / clause_id / quote / subject_scope | 错配拦截，不出结论 |
| VERDICT-01 | 判定模型 | 本设计 | finding 状态与证据 | 三因外禁止待核实 |
| EVID-01 | 材料存在性 | 内部口径 | 材料上传记录、页级阅读台账、字段抽取 | 未上传/未读/未提取=缺项 |
| NL-01 | 负面清单适用性 | 上海细则第9条；交办公路〔2024〕6号 | 分包范围、domain、结构部位、阈值参数 | 适用项判定；不适用项标注 |
| SH7-01 | 岗位齐套 | 上海细则第7条；DG/TJ 表3.3.3/3.4.1 | 名单岗位、工程规模 | 缺岗位=缺项；人数不足=提示 |
| SH7-02 | 本单位人员三重关系 | 交公路规〔2024〕2号第23条；上海细则第7条 | 身份证号、劳动合同、社保、工资 | 缺合同=缺项；单位不一致=不符合 |
| SH7-03 | 社保期间 | 附件3/4第3项“近一个月” | 缴费月份/起止 | 基准日所在月或上一自然月；不覆盖=缺项 |
| SH7-04 | 安全员配备 | 交安25号令第14条；DG/TJ 表 | 合同额/产值、安全员名单 | 法定下限+推荐值；不足=提示/条件 |
| SH7-05 | 特种作业工种 | 上海细则第10/12条；附件1附表4 | 分包内容、工种、证书 | 缺工种=缺项；数量=提示 |
| SH10-01 | 资质等级/承包范围 | 建市〔2014〕159号；2026改革 | 资质类别/等级、工程规模参数 | 可量化=判定；模糊=待核实 |
| SH12-01 | 报审材料齐套 | 上海细则第12条；附件3/4 | 材料清单、签章 | 缺项 |
| SIGN-01 | 签章矩阵 | 附件3/4签署栏 | 角色、签字、执业章、公章、页码 | 已见/未见/字迹不清 |
| REP-01 | 报告话术与一致性 | 本设计 | 判定、计数、阅读覆盖 | 三档结论、四段式、禁用词 |

## 6 现有代码改造点

| 文件 | 改动 | 风险 |
| --- | --- | --- |
| `a2_check.py` A2-01 | 身份证号主键、逐人四态、期间判定 | 影响复核队列与报告，需定向回归 |
| `a2_check.py` A2-04 | 增加等级/规模判定 | 依赖 `qualification_scope.json` |
| `a1_check.py` `run_negative_list_check` | 增加适用性判定与覆盖口径 | 需补 21 叶谓词 |
| `rule_item_catalog.py` | 注册新 item_id | 漏注册会导致局部重跑失效 |
| `review_flow.json` | s2/s3/s4 增加第七条、第十条（二）、第二十三条引用 | 影响规则影响面计算 |
| `report_policy_data/policy.json` | 新增策略版本 | 历史快照只读 |
| `report_template_v14.py` / 新模板 | 三档结论、四段式、去 1/21 | 会影响导出对比 |
| `seal_detect.py` / `signature_chain.py` | 签章矩阵 | 仅判有无，不验真 |
| `parsing.reading_coverage` | 规则前门禁 | 未读页必须进入缺项 |

## 7 报告策略与兼容

- 新增报告策略 `version=2.0.0`，保留 V1.3/V1.4 快照只读；
- 旧 finding 通过适配层映射到新判定，不重写历史；
- 新报告必须输出：
  - 结论三档；
  - 每个问题“事实—依据—影响—补正”；
  - 阅读覆盖说明；
  - 计数：不符合/缺项/待核实/建议补正/不适用/未纳入。
- 报告生成器后校验：
  - 不得出现“1/21、未命中、无法判定”；
  - 待核实必须带文件+页码+冲突点/缺失参数；
  - 正文、附件、结论计数同源。

## 8 风险与开放问题

1. DG/TJ 08-2225 版本有效性；
2. 2026 资质改革正式标准与过渡期；
3. 公路工程特种作业证书类型认定；
4. 交安 25 号令“年度施工产值”与“合同额”的口径；
5. 负面清单结构部位分类；
6. 报告结论措辞与附条件上限；
7. A2-01 重构对既有复核队列的兼容；
8. 页级阅读门禁可能增加首轮耗时，需要缓存与增量补读。

## 9 验收标准

1. 交接报告 task12 重跑后：不再出现“1/21”、不再出现 W16-05/T15-04/T15-01 错配、不再把“清单未载明”写成“无法核实”。
2. 报告2 场景：三名人员逐人输出社保单位/期间/结论；断缴人员进入缺项而非全局待核实。
3. 报告1 场景：桩基分包不被直接判为桥梁上部承重结构；资质等级与规模参数能给出符合/不符合/待核实之一。
4. 每条问题都有文件+页码+依据条款；正文、附件二、结论计数一致。
5. 人工复核队列只包含待核实项，数量显著低于改造前。
6. 定向单测与真实材料回归通过；历史 V1.3/V1.4 报告打开与导出不受影响。
