// T8-U2：核查三栏工作台数据适配层（纯函数，无副作用、无 fetch、无 PDF 读取）。
//
// 当前需求与设计入口：docs/plus/blockchain-supervision/README.md（历史任务契约已移出工作区归档）。
// 本模块负责 view-model、真实字段来源、finding→主体归属派生及三态/证据映射。
//
// 后端结论已提供 subject_id：优先使用明确主体；与材料主体冲突或主体不可查时
// 单列待确认，不按文件名或单一材料推测主体。

import { isMaterialOnly, isUnreviewedFinding } from './reviewDecision'
import { subjectDisplayName } from './materialState'
import { statusLabelOf } from './statusDisplay'
import { cleanText } from './plainText'

// ── 常量（左栏分组锚点；key 仅作 React key / 选中态锚点，不展示） ──────────────
export const SECTION_GROUP_KEY = 'section'
export const SECTION_GROUP_LABEL = '标段整体'
export const UNRESOLVED_SUBJECT_GROUP_KEY = 'subject:unresolved'

// 结论三态在左栏的优先权重（产品口径 §二.4：不符合/无法核实优先，符合折叠在后）。
const STATUS_RANK = { hit: 0, undecidable: 1, miss: 2 }
// 严重度权重：high→medium→low→未分级（与 EvidencePanel.SEVERITY_RANK 同口径）。
const SEVERITY_RANK = { high: 0, medium: 1, low: 2 }
const UNRANKED = 3

/** 主体分组的稳定 key（`subject:` 前缀 + 主体 id）。 */
export function subjectGroupKey(subjectId) {
  return `subject:${subjectId}`
}

/**
 * 材料映射：关联键取 `material.id`（DTO 中 `id` 与 `material_id` 双写同值）。
 * @param {Array<object>} materials - get_task 返回的 materials[]
 * @returns {Record<number, object>}
 */
export function buildMaterialsById(materials) {
  const map = {}
  for (const m of materials || []) {
    if (m == null) continue
    const id = m.id ?? m.material_id
    if (id != null) map[id] = m
  }
  return map
}

/**
 * 主体映射：关联键取 `subject.subject_id`。
 * @param {Array<object>} subjects - get_task 返回的 subjects[]
 * @returns {Record<number, object>}
 */
export function buildSubjectsById(subjects) {
  const map = {}
  for (const s of subjects || []) {
    if (s == null) continue
    if (s.subject_id != null) map[s.subject_id] = s
  }
  return map
}

/**
 * 后端结论与材料主体一致时使用已登记主体；两者冲突或主体无法查实则待确认。
 */
export function resolveFindingSubject(finding, materialsById = {}, subjectsById = {}) {
  const materialId = finding?.material_id
  const material = materialId != null ? materialsById[materialId] : null
  const findingSubjectId = finding?.subject_id ?? null
  const materialSubjectId = material?.subject_id ?? null
  const conflicting = findingSubjectId != null && materialSubjectId != null
    && String(findingSubjectId) !== String(materialSubjectId)
  const subjectId = findingSubjectId ?? materialSubjectId
  const subject = subjectId != null ? subjectsById[subjectId] : null
  if (!conflicting && subjectId != null && subject) {
    const subjectLabel = subjectDisplayName(subject) || null
    return {
      key: subjectGroupKey(subject.subject_id ?? subjectId),
      label: subjectLabel || `主体 ${subjectId}`,
      kind: 'subject',
      subjectId: subject.subject_id ?? subjectId,
      subjectLabel,
    }
  }
  // 完整性核查按主体执行；历史结论缺少主体时不能当作已确认的标段级结果。
  if (conflicting || subjectId != null || finding?.kind === 'completeness') {
    return { key: UNRESOLVED_SUBJECT_GROUP_KEY, label: '主体待确认', kind: 'unresolved', subjectId: null, subjectLabel: null }
  }
  return {
    key: SECTION_GROUP_KEY,
    label: SECTION_GROUP_LABEL,
    kind: 'section',
    subjectId: null,
    subjectLabel: null,
  }
}

/**
 * 组内排序（契约 §U2）：不符合/无法核实优先 → severity(high→medium→low→null) → 保持后端原序。
 * 用 (index) 做稳定次级键，绝不因排序丢失任何 item。
 */
export function sortFindingItems(items) {
  return (items || [])
    .map((it, index) => ({ it, index }))
    .sort((a, b) => {
      const sa = STATUS_RANK[a.it.status] ?? UNRANKED
      const sb = STATUS_RANK[b.it.status] ?? UNRANKED
      if (sa !== sb) return sa - sb
      const va = SEVERITY_RANK[a.it.severity] ?? UNRANKED
      const vb = SEVERITY_RANK[b.it.severity] ?? UNRANKED
      if (va !== vb) return va - vb
      return a.index - b.index
    })
    .map((x) => x.it)
}

/** 刀D（设计 104 §3/§4.2）：条款视图的展示副本；原文/文号只消毒不改写，缺失就是 null。 */
export function toBasisView(basis) {
  if (!basis || typeof basis !== 'object') return null
  return {
    clauseId: basis.clause_id ?? null,
    docId: basis.doc_id ?? null,
    docNo: cleanText(basis.doc_no),
    docTitle: cleanText(basis.doc_title),
    marker: cleanText(basis.marker),
    text: cleanText(basis.text),
    pageStart: basis.page_start ?? null,
    pageEnd: basis.page_end ?? null,
    humanVerified: typeof basis.human_verified === 'boolean' ? basis.human_verified : null,
    sourceKind: basis.source_kind ?? null,
  }
}

export function primaryEvidenceRef(finding) {
  if (!Array.isArray(finding?.evidence)) return null
  const primaryMaterialId = finding.material_id ?? finding.materialId
  return finding.evidence.find((ref) => {
    if (!ref || typeof ref !== 'object') return false
    const materialId = ref.material_id ?? ref.materialId
    if (primaryMaterialId != null && String(materialId) !== String(primaryMaterialId)) return false
    if (finding.page != null && Number(ref.page) !== Number(finding.page)) return false
    return true
  }) ?? null
}

/** 把原始 finding DTO + 派生归属压成左栏 item（契约 §1 + 允许附加只读展示字段）。 */
function toItem(finding, resolved) {
  // 未确认或冲突的主体不能再传入补件上传。
  const stableSubjectId = resolved.subjectId
  const rawMissing = finding.missing && typeof finding.missing === 'object' ? finding.missing : null
  const missingLabels = Array.isArray(rawMissing?.labels) ? rawMissing.labels.filter(Boolean) : []
  const missingCategories = Array.isArray(rawMissing?.categories) ? rawMissing.categories.filter(Boolean) : []
  const missing = rawMissing ? {
    ...rawMissing,
    label: missingLabels.join('、') || cleanText(finding.message) || '待补材料',
    category: missingCategories[0] || null,
    itemId: finding.item_id ?? null,
    kind: finding.kind ?? null,
    itemIds: finding.item_id != null ? [finding.item_id] : [],
    subjectId: stableSubjectId,
    subjectIds: stableSubjectId != null ? [stableSubjectId] : [],
    materialId: finding.material_id ?? null,
    materialIds: finding.material_id != null ? [finding.material_id] : [],
    actionable: finding.status === 'hit' && missingCategories.length > 0,
  } : null
  return {
    findingId: finding.id,
    title: cleanText(finding.message) ?? '',
    itemId: finding.item_id ?? null,
    kind: finding.kind ?? null,
    status: finding.status,
    openTerm: finding.open_term === true,
    materialOnly: finding.material_only === true,
    // 后端 status_label 是唯一展示词来源；缺失时才按三态码兜底（历史快照）。
    statusLabel: statusLabelOf(finding) || null,
    severity: finding.severity ?? null,
    severityLabel: finding.severity_label ?? null,
    subjectId: resolved.subjectId,
    subjectLabel: resolved.subjectLabel,
    message: cleanText(finding.message) ?? '',
    source: cleanText(finding.source),
    // 判定链是后端可选的解释字段；没有时由详情页明确展示“未提供”，不把规则依据冒充推理。
    analysis: cleanText(finding.analysis),
    reasoning: cleanText(finding.reasoning),
    explanation: cleanText(finding.explanation),
    ruleReason: cleanText(finding.rule_reason ?? finding.ruleReason),
    quote: cleanText(finding.quote),
    // 刀D（设计 104 §3）：规则依据视图（条目→条款坐标），左栏选中卡片与详情面板共用。
    basis: toBasisView(finding.basis),
    basisStatus: finding.basis_status ?? finding.basisStatus ?? null,
    evidence: finding.evidence ?? [],
    reportReviewBlocking: finding.report_review_blocking !== false,
    supplementHint: finding.supplement_hint && typeof finding.supplement_hint === 'object' ? {
      label: finding.supplement_hint.label,
      category: finding.supplement_hint.category,
      itemId: finding.supplement_hint.item_id,
      itemIds: finding.supplement_hint.item_id ? [finding.supplement_hint.item_id] : [],
      subjectId: stableSubjectId,
      subjectIds: stableSubjectId != null ? [stableSubjectId] : [],
      subjectMappingReliable: stableSubjectId != null,
      actionable: true,
    } : null,
    // 只透传与主材料、页码一致的证据引用。
    anchor: finding.anchor ?? primaryEvidenceRef(finding)?.anchor ?? null,
    decisionTrace: finding.decision_trace ?? finding.decisionTrace ?? null,
    page: finding.page ?? null,
    locationMode: finding.location_mode ?? null,
    materialId: finding.material_id ?? null,
    review: finding.review ?? null,
    // 刀B（BS-P1-13，设计 98 §4.4）：结论变化后旧复核回执被作废，旧意见只留痕。
    // invalidated 存在 = 该条回到「可重新处置」，与「从未复核」区分但同属待处理桶。
    invalidated: finding.review?.invalidated ?? null,
    reviewDecision: finding.review?.decision ?? null,
    reviewNote: finding.review?.note ?? null,
    // 第一步缺件的只读结构化入口；补件按钮只由 hit + actionable 驱动，
    // undecidable 即使有缺口标签也必须走人工核实，不伪装成可补件。
    missing,
  }
}

/**
 * 按主体归属分组（契约 §2.1/§3）。标段整体组优先，随后主体组按后端 subjects 顺序；
 * 只包含有 item 的组；组内排序并统计 miss/hit/undecidable/open 计数（openCount = 未人工处置的 hit+undecidable）。
 */
export function groupFindings({ findings, materialsById = {}, subjectsById = {}, subjects = [] } = {}) {
  const byKey = new Map()
  for (const f of findings || []) {
    const resolved = resolveFindingSubject(f, materialsById, subjectsById)
    if (!byKey.has(resolved.key)) {
      byKey.set(resolved.key, {
        key: resolved.key,
        label: resolved.label,
        kind: resolved.kind,
        subjectId: resolved.subjectId,
        missCount: 0,
        hitCount: 0,
        undecidableCount: 0,
        openCount: 0,
        items: [],
      })
    }
    byKey.get(resolved.key).items.push(toItem(f, resolved))
  }

  const groups = []
  if (byKey.has(SECTION_GROUP_KEY)) groups.push(byKey.get(SECTION_GROUP_KEY))
  if (byKey.has(UNRESOLVED_SUBJECT_GROUP_KEY)) groups.push(byKey.get(UNRESOLVED_SUBJECT_GROUP_KEY))

  const subjectOrder = (subjects || []).map((s) => s.subject_id)
  const subjectGroups = [...byKey.values()].filter((g) => g.kind === 'subject')
  subjectGroups.sort((a, b) => {
    const ia = subjectOrder.indexOf(a.subjectId)
    const ib = subjectOrder.indexOf(b.subjectId)
    const ra = ia === -1 ? Number.MAX_SAFE_INTEGER : ia
    const rb = ib === -1 ? Number.MAX_SAFE_INTEGER : ib
    return ra - rb
  })
  for (const g of subjectGroups) groups.push(g)

  for (const g of groups) {
    g.items = sortFindingItems(g.items)
    let miss = 0
    let hit = 0
    let und = 0
    for (const it of g.items) {
      if (it.status === 'miss') miss += 1
      else if (it.status === 'hit') hit += 1
      else if (it.status === 'undecidable') und += 1
    }
    g.missCount = miss
    g.hitCount = hit
    g.undecidableCount = und
    g.openCount = g.items.filter(isUnreviewedFinding).length
  }
  return groups
}

/**
 * 组装证据数组（契约 §2.3 冻结组装规则）。
 * material_id 为空 → []（不伪造）；材料被删/跨任务 id → fileName=null（显示「材料不可用」）。
 * 数组长度恒为 0 或 1（后端每结论最多一个材料锚点）。
 */
export function buildEvidence(finding, materialsById = {}) {
  if (!finding || finding.material_id == null) return []
  const materialId = finding.material_id
  const mat = materialsById[materialId]
  const evidenceRef = primaryEvidenceRef(finding)
  return [
    {
      materialId,
      fileName: mat ? (mat.file_name ?? null) : null,
      page: finding.page ?? null,
      quote: cleanText(finding.quote),
      locationMode: finding.location_mode ?? null,
      ...((finding.anchor ?? evidenceRef?.anchor)
        ? { anchor: finding.anchor ?? evidenceRef.anchor }
        : {}),
    },
  ]
}

/**
 * 只读动作 flags（契约 §2.4）。适配层只回传 flag，动作仍走现有 API，不重复业务逻辑。
 */
export function deriveActions({ detail, selectedFinding, materialsById = {} } = {}) {
  const materialId = selectedFinding?.material_id
  const hasMaterial = materialId != null && materialsById[materialId] != null
  const canOpenSource = !!(
    selectedFinding &&
    selectedFinding.location_mode === 'precise' &&
    hasMaterial
  )
  const started = !!detail && detail.state !== 'setup'
  return {
    canReview: isUnreviewedFinding(selectedFinding),
    canOpenSource,
    canRerun: started,
    canComplete: !!detail && detail.state !== 'setup' && detail.state !== 'done',
  }
}

/**
 * 稳定选择（契约 §U2）：保留仍存在的 prevId；否则取第一条可见待办（非 miss）；
 * 再否则取第一条 item；全空 → null。
 */
export function pickSelection(groups, prevId = null) {
  const all = []
  for (const g of groups || []) for (const it of g.items || []) if (!isMaterialOnly(it)) all.push(it)
  if (prevId != null && all.some((it) => it.findingId === prevId)) return prevId
  for (const g of groups || []) {
    for (const it of g.items || []) {
      if (!isMaterialOnly(it) && it.status !== 'miss') return it.findingId
    }
  }
  for (const g of groups || []) {
    const first = (g.items || []).find((it) => !isMaterialOnly(it))
    if (first) return first.findingId
  }
  return null
}

/**
 * 主入口：把 get_task（detail）+ list_findings（findings）转成 U1 消费的 view-model（契约 §1）。
 * 附加只读的 materialsById/subjectsById，供 U5 接线复用（不改变契约键语义）。
 */
export function buildReviewWorkbenchModel({ detail, findings, selectedFindingId = null } = {}) {
  const materials = detail?.materials || []
  const subjects = detail?.subjects || []
  const list = findings || []

  const materialsById = buildMaterialsById(materials)
  const subjectsById = buildSubjectsById(subjects)
  const groups = groupFindings({ findings: list, materialsById, subjectsById, subjects })

  const selectedId = pickSelection(groups, selectedFindingId)
  const selectedFinding = selectedId != null ? list.find((f) => f.id === selectedId) || null : null
  const evidence = buildEvidence(selectedFinding, materialsById)
  const actions = deriveActions({ detail, selectedFinding, materialsById })

  return {
    groups,
    selectedFindingId: selectedId,
    selectedFinding,
    evidence,
    actions,
    materialsById,
    subjectsById,
  }
}
