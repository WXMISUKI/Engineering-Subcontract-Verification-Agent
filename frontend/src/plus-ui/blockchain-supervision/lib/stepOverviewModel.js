// 刀F（交互重做第一步）：五步审查总览的纯派生层。
//
// 目的（用户 2026-10-25 反馈）：现在五步卡只有计数，看不到「这一步到底缺哪些文件」，
// 上传/补件入口也不在审查页上。本模块把**已有只读数据**派生成「每步缺件清单 + 主要问题」：
//   - 步骤与归属：复用 `buildFiveSteps`（其中 kind→步骤来自后端冻结快照，不另建映射）；
//   - 缺件标签：finding DTO 的 `missing.labels`（后端 reporting.finding_missing 的只读输出）；
//   - 可补类别：`/tasks/{id}/todo` 经 `buildMissingPlan` 的 categories（后端唯一聚合点）；
//   - 依据展示：finding DTO 的 `basis`（刀D；缺失时为 null，不猜条款）。
//
// 纪律：不请求、不判定、不重排结论语义；缺件只在第 1 步按标签去重，不把「审查产出/人工作判」
// 伪装成可上传材料；无法归类的缺件如实进入「需人工核实」，不给补件按钮。
import { buildFiveSteps } from './fiveStepModel'
import { isMaterialOnly } from './reviewDecision'
import { cleanText } from './plainText'

const SEVERITY_RANK = { high: 0, critical: 0, medium: 1, low: 2 }
const UNRANKED = 3

function text(value) {
  // 展示消毒与刀D同源：后端历史行可能带 <p>/<h2> 等标记，总览不原样渲染。
  const cleaned = cleanText(typeof value === 'string' ? value : '')
  return typeof cleaned === 'string' ? cleaned.trim() : ''
}

function isDisplayNoiseLimitation(item) {
  const message = text(typeof item === 'string' ? item : item?.message)
  return (message.includes('冻结的本地固定依据') && message.includes('在线参考来源'))
    || ((message.includes('国家文件仅作参考') || message.includes('参考文件仅作参考')) && message.includes('整份原件尚未人工校对'))
}

function isExcludedDeploymentBasis(item) {
  const clauseId = text(item?.clause_id || item?.clauseId)
  const title = text(item?.doc_title || item?.docTitle || item?.title)
  return clauseId.startsWith('SHCG-DEPLOY-20261014')
    || title.includes('工程分包审查智能体建设工作安排部署')
}

/** finding.missing 的 by_category → 该标签所属材料类；无归类返回 null。 */
function categoryOfLabel(missing, label) {
  const groups = missing && typeof missing.by_category === 'object' ? missing.by_category : null
  if (!groups) return null
  for (const [category, labels] of Object.entries(groups)) {
    if (category === 'null' || category == null) continue
    if (Array.isArray(labels) && labels.includes(label)) return category
  }
  return null
}

function basisView(basis) {
  if (!basis || typeof basis !== 'object') return null
  if (isExcludedDeploymentBasis(basis)) return null
  return {
    clauseId: text(basis.clause_id) || null,
    docNo: text(basis.doc_no) || null,
    marker: text(basis.marker) || null,
    docTitle: text(basis.doc_title) || null,
    text: text(basis.text) || null,
    humanVerified: basis.human_verified === true,
  }
}

/** 计划里的材料类 → 该类的补件入参（与 MissingMaterialPanel 的 onSupplement 同形）。 */
function supplementOf(planItem) {
  if (!planItem || planItem.actionable !== true || !planItem.uploadCategory) return null
  const subjectIds = planItem.subjectMappingReliable === false || !Array.isArray(planItem.subjectIds)
    ? [] : planItem.subjectIds
  return {
    category: planItem.category,
    label: planItem.label,
    hint: planItem.hint,
    count: planItem.count,
    labels: planItem.labels,
    itemIds: planItem.itemIds,
    subjectItems: planItem.subjectItems,
    materialIds: planItem.materialIds,
    subjectIds,
    subjectId: subjectIds.length === 1 ? subjectIds[0] : null,
    uploadCategory: planItem.uploadCategory,
  }
}

/**
 * @param {object} input
 * @param {object|null} input.snapshot   reference_snapshot
 * @param {Array<object>} input.findings 结论 DTO（含 missing / basis）
 * @param {object|null} input.missingPlan buildMissingPlan(todo) 的结果
 * @returns {{steps: Array<object>, totals: object, unassigned: number, limited: boolean}}
 */
export function buildStepOverview({ snapshot, findings, missingPlan } = {}) {
  const list = Array.isArray(findings) ? findings.filter(Boolean) : []
  const snapshotSteps = Array.isArray(snapshot?.steps) ? snapshot.steps : []
  const snapshotStepById = new Map(snapshotSteps.map((item) => [item?.step_id, item]))
  // open_term 只作只读登记：不进入五步结论、无法核实或缺件派生；原始 status 保持三态。
  const openTermFindings = list.filter((finding) => finding.open_term === true)
  const countableFindings = list.filter((finding) => finding.open_term !== true)
  const base = buildFiveSteps({ snapshot, findings: countableFindings })
  const openTermBase = buildFiveSteps({ snapshot, findings: openTermFindings })
  const byId = new Map(countableFindings.map((finding) => [finding.id, finding]))
  const openTermCountByStep = new Map(
    openTermBase.steps.map((step) => [step.stepId, step.counts.total]),
  )

  // 标签 → 可补类别；同一标签在多个类里出现时取第一个（后端按类归并，标签本身不重复补）。
  const categories = Array.isArray(missingPlan?.missing) ? missingPlan.missing : []
  const planByLabel = new Map()
  const planByCategory = new Map()
  for (const item of categories) {
    planByCategory.set(item.category, item)
    for (const label of item.labels || []) {
      if (!planByLabel.has(label)) planByLabel.set(label, item)
    }
  }

  const totals = {
    total: 0,
    hit: 0,
    undecidable: 0,
    miss: 0,
    open: 0,
    openTerm: openTermFindings.length,
    /** 各步骤内按缺件标签去重后的累加；同一标签跨步骤会重复计数，不代表全局文件份数。 */
    missingByStep: 0,
    /** 无法核实、但拿不到缺件标签的条数：需人工核实，不能假装可补件。 */
    manualOnly: 0,
  }
  const steps = base.steps.map((step) => {
    const sourceStep = snapshotStepById.get(step.stepId) || null
    const stepFindings = step.findingIds.map((id) => byId.get(id)).filter(Boolean)
    const missingByLabel = new Map()
    let manualOnly = 0

    for (const finding of stepFindings) {
      if (isMaterialOnly(finding)) continue
      if (finding.status !== 'undecidable') continue
      // 第 1 步已经统一承载材料完整性与缺件；后续步骤的无法核实项
      // 只作为异常/人工核实显示，不能再次派生缺件清单。
      if (step.index !== 1) continue
      const missing = finding.missing && typeof finding.missing === 'object' ? finding.missing : null
      const labels = Array.isArray(missing?.labels) ? missing.labels.map(text).filter(Boolean) : []
      if (!labels.length) {
        manualOnly += 1
        continue
      }
      for (const label of labels) {
        const current = missingByLabel.get(label)
        if (current) {
          current.count += 1
          if (finding.id != null && !current.findingIds.includes(finding.id)) current.findingIds.push(finding.id)
          continue
        }
        const category = categoryOfLabel(missing, label)
        const planItem = (category && planByCategory.get(category)) || planByLabel.get(label) || null
        missingByLabel.set(label, {
          label,
          category: category || planItem?.category || null,
          categoryLabel: planItem?.label || null,
          count: 1,
          findingIds: finding.id != null ? [finding.id] : [],
          supplement: supplementOf(planItem),
          // 可补件只认后端 todo 的可补类别；否则如实标「需人工核实」，不诱导上传。
          actionable: Boolean(supplementOf(planItem)),
        })
      }
    }

    const missingItems = [...missingByLabel.values()].sort((a, b) => {
      if (b.count !== a.count) return b.count - a.count
      return a.label.localeCompare(b.label, 'zh-Hans-CN')
    })

    // 主要问题：不符合优先，其次无法核实；组内按严重度与原顺序。
    const issueFindings = stepFindings
      .filter((finding) => !isMaterialOnly(finding) && (finding.status === 'hit' || finding.status === 'undecidable'))
    const issues = issueFindings
      .slice()
      .sort((a, b) => {
        const sa = a.status === 'hit' ? 0 : 1
        const sb = b.status === 'hit' ? 0 : 1
        if (sa !== sb) return sa - sb
        return (SEVERITY_RANK[a.severity] ?? UNRANKED) - (SEVERITY_RANK[b.severity] ?? UNRANKED)
      })
      .slice(0, 3)
      .map((finding) => ({
        id: finding.id,
        status: finding.status,
        statusLabel: finding.status_label || null,
        severity: finding.severity || null,
        severityLabel: finding.severity_label || null,
        message: text(finding.message) || null,
        itemId: finding.item_id || null,
        basis: basisView(finding.basis),
      }))

    totals.total += step.counts.total
    totals.hit += step.counts.hit
    totals.undecidable += step.counts.undecidable
    totals.miss += step.counts.miss
    totals.open += step.counts.open
    totals.missingByStep += missingItems.length
    totals.manualOnly += manualOnly

    return {
      ...step,
      fixedBasis: Array.isArray(sourceStep?.fixed_basis) ? sourceStep.fixed_basis.filter((item) => !isExcludedDeploymentBasis(item)) : [],
      materialBasis: Array.isArray(sourceStep?.material_basis) ? sourceStep.material_basis.filter((item) => !isExcludedDeploymentBasis(item)) : [],
      references: Array.isArray(sourceStep?.references) ? sourceStep.references.filter((item) => !isExcludedDeploymentBasis(item)) : [],
      limitations: (step.limitations || []).filter((item) => !isDisplayNoiseLimitation(item)),
      openTermCount: openTermCountByStep.get(step.stepId) || 0,
      missingItems,
      missingCount: missingItems.length,
      manualOnly,
      issueCount: issueFindings.length,
      issues,
    }
  })

  return { steps, totals, unassigned: base.unassigned, limited: base.limited }
}
