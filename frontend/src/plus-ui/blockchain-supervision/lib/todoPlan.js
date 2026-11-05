// 刀A（BS-P1-06）：待补材料（材料视角）的纯派生层。
//
// 只吃后端 `GET /tasks/{id}/todo` 的返回值，不请求、不聚合缺件、不从 message 拆文案、
// 不猜材料类——缺件归并、可补件判断（actionable）与计数全部由后端给出：
//   - 后端 `reporting.missing_summary()` 是唯一聚合点（含 data/missing_material_map.json 的 roles）；
//   - 本模块只做**展示分区**（可补件 / 非补件缺口）与**上传预选角色**的映射。
//
// 计数口径（docs 96 Q3 已确认）：一条结论可同时缺多类材料，各类「可消 N 条」之和
// 可大于无法核实总数，因此必须把无法核实总数作为分母并列展示，不能只显示各类数。

/** 上传阶段「资料类别」下拉当前支持的取值（与 SetupStage 的 uploadCategory 一一对应）。
 *  这不是材料类映射表的副本：roles 仍来自后端 missing_material_map.json。 */
export const UPLOAD_CATEGORIES = ['general', 'sub', 'labor', 'packet']

export function matchingSupplementGeneration(context, role, subjectId) {
  if (!context?.generationId || context.uploadCategory !== role) return null
  if (context.subjectId != null && String(context.subjectId) !== String(subjectId || '')) return null
  if (Array.isArray(context.subjectIds) && context.subjectIds.length > 0
      && !context.subjectIds.some((id) => String(id) === String(subjectId || ''))) return null
  return context.generationId
}

export function supplementItemsForSubject(context, subjectId) {
  const pairs = Array.isArray(context?.subjectItems) ? context.subjectItems : []
  if (subjectId != null && pairs.length) {
    return [...new Set(pairs.filter((pair) => String(pair.subject_id) === String(subjectId)).map((pair) => pair.item_id))]
  }
  // 兼容旧服务端未返回配对的聚合数据：多主体不能把全部条目归给所选主体。
  if (subjectId != null && (context?.subjectIds?.length || 0) > 1) return []
  return Array.isArray(context?.itemIds) ? context.itemIds : []
}

const PACKET_CATEGORIES = new Set([
  'submission_packet', 'certificates', 'personnel_social', 'procurement_lease',
  'fund_flow', 'supervision_consent', 'joint_venture', 'plan_and_signature',
])

const PACKET_LABEL_RE = /营业执照|资质证书|安全生产许可证|证照|报审|清单|诚信手册|业绩|人员|授权|社保|身份证|学历证|职称证|交安|特殊工种|操作证|签章/

/** 材料类 → 上传类别预选值；无可上传角色（如「审查产出或人工作判」）返回 null。
 *  多角色时按 UPLOAD_CATEGORIES 顺序取第一个（合同类优先，其余落到报审资料包）。 */
export function supplementCategoryOf(category) {
  const roles = Array.isArray(category?.roles) ? category.roles : []
  const categoryKey = text(category?.category)
  if (PACKET_CATEGORIES.has(categoryKey) && roles.includes('packet')) return 'packet'
  if (categoryKey === 'general_contract' && roles.includes('general')) return 'general'
  if (categoryKey === 'sub_contract' && roles.includes('sub')) return 'sub'
  if (categoryKey === 'labor_contract' && roles.includes('labor')) return 'labor'
  return UPLOAD_CATEGORIES.find((role) => roles.includes(role)) || null
}

/** 单项缺件/材料行的上传预选：证照和报审附件必须进入 packet，不能落到合同角色。 */
export function supplementUploadCategoryOf(item) {
  if (!item || typeof item !== 'object') return null
  const category = text(item.category || item.material_category)
  if (PACKET_CATEGORIES.has(category) || category === 'submission_packet') return 'packet'
  const label = text(item.label || item.role_label || item.file_name)
  if (PACKET_LABEL_RE.test(label)) return 'packet'
  if (UPLOAD_CATEGORIES.includes(item.uploadCategory)) return item.uploadCategory
  if (UPLOAD_CATEGORIES.includes(item.role)) return item.role
  return supplementCategoryOf(item)
}

function count(value) {
  const n = Number(value)
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0
}

function text(value) {
  return typeof value === 'string' ? value.trim() : ''
}

function normalizeCategory(raw) {
  if (!raw || typeof raw !== 'object') return null
  const category = text(raw.category) || null
  const label = text(raw.label) || '其他未归类材料'
  const itemIds = Array.isArray(raw.item_ids) ? raw.item_ids.filter((id) => id != null) : []
  const labels = Array.isArray(raw.labels) ? raw.labels.filter((lb) => text(lb)) : []
  const subjectIds = Array.isArray(raw.subject_ids) ? raw.subject_ids.filter((id) => id != null) : []
  const materialIds = Array.isArray(raw.material_ids) ? raw.material_ids.filter((id) => id != null) : []
  const rawCount = count(raw.count ?? itemIds.length)
  // 空对象/全空类直接丢弃，避免面板出现「其他未归类材料 0 条」这类假条目。
  if (!category && !itemIds.length && !labels.length && !rawCount) return null
  return {
    category,
    label,
    hint: text(raw.hint),
    count: rawCount,
    itemIds,
    labels,
    subjectIds,
    subjectItems: Array.isArray(raw.subject_items) ? raw.subject_items.filter((pair) => pair?.subject_id != null && text(pair?.item_id)) : [],
    materialIds,
    subjectMappingReliable: raw.subject_mapping_reliable !== false,
    roles: Array.isArray(raw.roles) ? raw.roles.filter((role) => text(role)) : [],
    actionable: raw.actionable === true,
    // 上传预选类别只对可补件类有意义；非补件类保持 null（不诱导上传）。
    uploadCategory: raw.actionable === true ? supplementCategoryOf(raw) : null,
  }
}

/**
 * 后端 todo 负载 → 面板视图模型。
 * 返回 null 表示「还没读到」（调用方据此区分「未就绪」与「确实没有缺件」）。
 */
export function buildMissingPlan(todo) {
  if (!todo || typeof todo !== 'object') return null
  const categories = (Array.isArray(todo.categories) ? todo.categories : [])
    .map(normalizeCategory)
    .filter(Boolean)
  const missing = categories.filter((item) => item.actionable)
  const manual = categories.filter((item) => !item.actionable)
  return {
    missing,
    manual,
    totals: {
      undecidable: count(todo.total_undecidable),
      itemTotal: count(todo.total_items),
      openTerms: count(todo.open_term_count),
      withoutMissing: count(todo.without_missing_count),
    },
    readiness: todo.readiness && typeof todo.readiness === 'object' ? todo.readiness : null,
  }
}

/** 无法核实总数与可补件条数的并列说明文字（单一文案定义点，面板与测试共用）。 */
export const COUNT_NOTE = '按材料类归并；一条结论可能缺多类，各类「可消」数之和可大于无法核实总数。'
