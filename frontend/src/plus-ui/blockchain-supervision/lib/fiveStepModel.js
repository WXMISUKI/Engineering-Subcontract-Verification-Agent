// V2-a：五步卡片与待办分组的纯派生层（无 fetch / 无 React / 无副作用 / 不做规则判定）。
//
// 本模块只做**结构映射与展示计数**，不复制后端判定逻辑、不改写三态与严重度、
// 不重排 finding、不猜结论属于哪一步。
//
// 结构镜像（只读来源，不回写、不改动）：
//   - 运行组 ↔ finding kind：backend/app/plus/blockchain_supervision/reference_snapshots.py
//     的 CHECK_GROUPS（组 key / kinds / label 与后端一一对应）；
//   - 五步编号与顺序：backend/app/plus/blockchain_supervision/data/review_flow.json 的
//     steps[].id/title/order；步骤视图由后端 reference_snapshot.steps[] 提供
//     （step_id/title/state/expected_groups/limitations/references），前端不重建；
//   - finding 字段：后端 _finding_dto（status/status_label/severity/severity_label/
//     review.decision/message/material_id/page/location_mode）。
//
// 归属口径：finding DTO **没有 step_id**，步骤归属只能经 kind → 步骤派生。冻结快照
// 自 V2 起直接给出每步 `kinds`（后端 review_flow.json kernels 的只读暴露），本模块优先
// 使用它；只有历史快照缺该字段时才退回「运行组 → kind」近似，并用 `kindsSource`
// 标出计数口径（a2 组横跨第二/三/四步，近似会把同一结论计进多步）。

import { isUnreviewedFinding } from './reviewDecision'
import { statusLabelOf } from './statusDisplay'

/** 运行组 → finding kind（结构镜像自后端 CHECK_GROUPS，顺序即组内展示顺序）。 */
export const GROUP_KINDS = {
  completeness: ['completeness'],
  signature_chain: ['signature_chain'],
  a1: ['negative_list', 'transfer_scope', 'illegal_subcontract'],
  a2: ['cert_validity', 'person_consistency', 'qualification_match'],
  internal_records: ['internal_records'],
}

/** 运行组 → 用户可见文案（镜像自后端 CHECK_GROUPS[...]["label"]，唯一定义点在后端）。 */
export const GROUP_LABELS = {
  completeness: '报审资料齐全性核查',
  signature_chain: '签字盖章核查',
  a1: '分包内容合规性核查',
  a2: '资质、证照与人员核查',
  internal_records: '内部信用与事故材料核查',
}

/** 步骤状态 → 用户可见文案（只在卡片上展示，不参与任何判定）。 */
export const STEP_STATE_LABELS = {
  completed: '已完成',
  running: '进行中',
  not_started: '未开始',
  out_of_scope: '本次不适用',
}

/** 待办展示分组（顺序即界面展示顺序；key 仅作分组锚点，label 才是文案）。
 *
 *  「待补材料」不在这里：缺件必须按材料类归并、由后端 `/tasks/{id}/todo` 给出可补件判断
 *  与计数（刀A / BS-P1-06），本模块不聚合缺件、不从 message 拆文案。右栏结构因此是
 *  「待补材料面板（材料视角）」+ 下面三桶（问题视角）。 */
export const TODO_BUCKETS = [
  { key: 'content_gap', label: '要素不足' },
  { key: 'risk', label: '风险问题' },
  { key: 'manual_check', label: '需人工核实' },
]

/** 每桶的下一步（单一文案定义点）：只说明动作入口，不承诺本版没有的能力。 */
export const TODO_NEXT_STEPS = {
  content_gap: '在左栏选中后可采纳/忽略并填写处理说明。',
  risk: '在左栏选中后采纳/忽略并填写处理说明；未处理项仍会随报告保留。',
  manual_check: '非补件缺口：可在详情里采纳/忽略并填写处理说明；未处理项仍会随报告保留。',
}

// 严重度 → 文案（镜像自后端 severity.SEVERITY_LABELS；仅兜底）。
const SEVERITY_LABELS = { high: '高', medium: '中', low: '低' }
// 已处置的复核回执（后端 review.decision 的规范化取值；未处置 = 不在此集合内）。
const REVIEWED_DECISIONS = new Set(['accepted', 'ignored', 'confirm', 'reject'])
// 严重度排序权重：high→medium→low→未分级（与 EvidencePanel.SEVERITY_RANK 同口径）。
const SEVERITY_RANK = { high: 0, medium: 1, low: 2 }
const UNRANKED = 3

/** 把后端限制条目（{code,message} 或纯字符串）压成去重后的非空文案，保持出现顺序。 */
function limitationMessages(limitations) {
  const out = []
  const seen = new Set()
  for (const item of limitations || []) {
    const raw = typeof item === 'string' ? item : item && item.message
    if (typeof raw !== 'string') continue
    const message = raw.trim()
    if (!message || seen.has(message)) continue
    seen.add(message)
    out.push(message)
  }
  return out
}

/** 组列表 → kind 并集（保持组顺序，组内保持 GROUP_KINDS 顺序，去重）。 */
function kindsOfGroups(groups) {
  const kinds = []
  const seen = new Set()
  for (const group of groups) {
    for (const kind of GROUP_KINDS[group] || []) {
      if (seen.has(kind)) continue
      seen.add(kind)
      kinds.push(kind)
    }
  }
  return kinds
}

function isReviewed(finding) {
  return REVIEWED_DECISIONS.has(finding?.review?.decision)
}

function todoItemOf(finding) {
  const severity = finding.severity ?? null
  const status = finding.status ?? null
  return {
    findingId: finding.id,
    title: finding.message ?? '',
    status,
    statusLabel: statusLabelOf(finding) || null,
    severity,
    severityLabel: finding.severity_label ?? SEVERITY_LABELS[severity] ?? null,
    kind: finding.kind ?? null,
    materialId: finding.material_id ?? null,
    page: finding.page ?? null,
    locationMode: finding.location_mode ?? null,
    reviewDecision: finding.review?.decision ?? null,
    reviewed: isReviewed(finding),
  }
}

/**
 * 五步卡片派生。
 *
 * @param {{snapshot?: object|null, findings?: Array<object>}} [input]
 *   snapshot：get_task / GET /tasks/{id}/findings 返回的 reference_snapshot；
 *   findings：同一接口返回的 findings[]。
 * @returns {{steps: Array<object>, unassigned: number, limited: boolean}}
 *   steps 顺序与 snapshot.steps 一致；unassigned 为未落进任何步骤的结论条数
 *   （未知 kind，或 kind 所属组不在任何步骤 expected_groups 中）；
 *   limited 表示依据视图受限：无 snapshot/无步骤，或步骤带限制说明。
 */
export function buildFiveSteps({ snapshot, findings } = {}) {
  const rawSteps = Array.isArray(snapshot?.steps) ? snapshot.steps : []
  if (!rawSteps.length) return { steps: [], unassigned: 0, limited: true }

  const list = Array.isArray(findings) ? findings.filter(Boolean) : []
  const steps = rawSteps.map((step, index) => {
    const state = step?.state ?? null
    const expectedGroups = Array.isArray(step?.expected_groups) ? [...step.expected_groups] : []
    const outOfScope = state === 'out_of_scope'
    // 本步内核 kind 优先取后端冻结快照的 `kinds`（真源：review_flow.json 的 kernels）。
    // 历史快照没有该字段时才按「运行组 → kind」近似——a2 组横跨第二/三/四步时，
    // 近似会把同一结论同时计进多步，故 `kindsSource` 如实标出计数口径。
    const kernelKinds = Array.isArray(step?.kinds) ? [...new Set(step.kinds)] : null
    return {
      stepId: step?.step_id ?? null,
      title: step?.title ?? '',
      question: step?.question ?? '',
      description: step?.description ?? '',
      index: index + 1,
      state,
      stateLabel: STEP_STATE_LABELS[state] ?? null,
      expectedGroups,
      // 本次不适用的步骤不给任何 kind：既不计结论，也不展示组文案。
      kinds: outOfScope ? [] : (kernelKinds ?? kindsOfGroups(expectedGroups)),
      kindsSource: outOfScope ? 'none' : (kernelKinds ? 'kernel' : 'group'),
      counts: { total: 0, hit: 0, undecidable: 0, miss: 0, open: 0 },
      issueCount: 0,
      limitations: limitationMessages(step?.limitations),
      // 只认实际检索到的在线参考（step.references）；配置来源 online_sources 不冒充。
      hasOnlineReference: Array.isArray(step?.references) && step.references.length > 0,
      findingIds: [],
    }
  })

  // kind → 步骤集合（一个 kind 可命中多步：共享组不做猜测性唯一归属）。
  const stepsByKind = new Map()
  for (const step of steps) {
    for (const kind of step.kinds) {
      if (!stepsByKind.has(kind)) stepsByKind.set(kind, [])
      stepsByKind.get(kind).push(step)
    }
  }

  let unassigned = 0
  for (const finding of list) {
    const targets = stepsByKind.get(finding.kind)
    if (!targets || !targets.length) {
      unassigned += 1
      continue
    }
    for (const step of targets) {
      // 保持 findings 原顺序：按原数组单遍追加，不排序。
      step.findingIds.push(finding.id)
      step.counts.total += 1
      if (finding.status === 'hit') step.counts.hit += 1
      else if (finding.status === 'undecidable') step.counts.undecidable += 1
      else if (finding.status === 'miss') step.counts.miss += 1
      // open = 未处置的不符合项（三态与回执语义复用 reviewDecision，不在此另立一份）。
      if (isUnreviewedFinding(finding)) step.counts.open += 1
    }
  }

  for (const step of steps) {
    step.issueCount = step.counts.hit + step.counts.undecidable
  }

  const limited = steps.some((step) => step.limitations.length > 0)
  return { steps, unassigned, limited }
}

/**
 * 单条结论的待办展示分组（只分组，不判定）。
 * 符合不是待办；不符合按风险等级分「风险问题/要素不足」，无法核实归「需人工核实」。
 * 缺件不在这里分组——材料视角由后端 todo 聚合（见 lib/todoPlan）。
 */
export function todoKeyOf(finding) {
  const status = finding?.status
  if (status === 'miss' || status == null) return null
  if (status === 'undecidable') return 'manual_check'
  if (status !== 'hit') return null
  if (finding.severity === 'high' || finding.severity === 'critical' || finding.severity === 'medium') {
    return 'risk'
  }
  return 'content_gap'
}

/**
 * 待办分组（长度恒为 TODO_BUCKETS.length，顺序同 TODO_BUCKETS）。
 * 组内排序：未处置在前 → severity(high→medium→low→未分级) → findings 原顺序（稳定）。
 */
export function buildTodoGroups({ findings } = {}) {
  const list = Array.isArray(findings) ? findings.filter(Boolean) : []
  const groups = TODO_BUCKETS.map(({ key, label }) => ({ key, label, count: 0, items: [] }))
  const groupsByKey = new Map(groups.map((group) => [group.key, group]))

  const decorated = []
  list.forEach((finding, index) => {
    const key = todoKeyOf(finding)
    if (key == null) return // miss 与未知状态不是待办，但也不影响其他项。
    decorated.push({ key, index, item: todoItemOf(finding) })
  })

  decorated.sort((a, b) => {
    const ra = a.item.reviewed ? 1 : 0
    const rb = b.item.reviewed ? 1 : 0
    if (ra !== rb) return ra - rb
    const sa = SEVERITY_RANK[a.item.severity] ?? UNRANKED
    const sb = SEVERITY_RANK[b.item.severity] ?? UNRANKED
    if (sa !== sb) return sa - sb
    return a.index - b.index
  })

  for (const { key, item } of decorated) groupsByKey.get(key).items.push(item)
  for (const group of groups) {
    group.count = group.items.length
    // 每桶带一句「下一步」，供面板直接展示（文案单一来源：TODO_NEXT_STEPS）。
    group.nextStep = TODO_NEXT_STEPS[group.key] || null
    for (const item of group.items) item.nextStep = group.nextStep
  }
  return groups
}

/**
 * 结论过滤（展示用）：kinds 非 null 按 kind ∈ kinds；todoKey 非 null 按 todoKeyOf 相等；
 * itemIds 非 null（Set/数组）按 finding.item_id 命中；同时给出时取交集。
 * 都为空时原样返回同一数组。不改变顺序，不额外丢弃任何项。
 */
export function filterFindings(findings, { kinds = null, todoKey = null, itemIds = null } = {}) {
  const list = Array.isArray(findings) ? findings : []
  if (kinds == null && todoKey == null && itemIds == null) return list
  const kindSet = Array.isArray(kinds) ? new Set(kinds) : null
  const itemIdSet = itemIds == null ? null : (itemIds instanceof Set ? itemIds : new Set(itemIds))
  return list.filter((finding) => {
    if (kindSet && !kindSet.has(finding?.kind)) return false
    if (todoKey != null && todoKeyOf(finding) !== todoKey) return false
    if (itemIdSet && !itemIdSet.has(finding?.item_id)) return false
    return true
  })
}
