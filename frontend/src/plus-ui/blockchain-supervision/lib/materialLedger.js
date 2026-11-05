import { describeMaterialState, materialSourcePath, subjectDisplayName } from './materialState'

function subjectName(subjectsById, subjectId) {
  if (subjectId == null) return ''
  // 无名主体不得回显「专业分包1」这种带类型前缀的历史编号（编号与类型解耦）。
  return subjectDisplayName(subjectsById[String(subjectId)])
}

function keyFacts(material) {
  const facts = material?.key_facts
  if (!facts || typeof facts !== 'object' || Array.isArray(facts)) return []
  return Object.entries(facts).filter(([, value]) => value !== null && value !== undefined && String(value).trim() !== '')
}

// 刀B（BS-P1-09）：未读页不在这里派生 —— 后端只在报告 `reading_coverage[]` 里披露
// （设计 98 §8），材料 DTO 不透出页数。台账徽标由组件按报告行文件名匹配，
// 避免前端自造第二套页数统计或伪造 DTO 字段。

function buildRow(material, { taskState, containerIds, subjectsById }) {
  const id = material?.id ?? material?.material_id
  const isContainer = containerIds.has(id) || containerIds.has(String(id))
  const state = describeMaterialState(material, { taskState, containerIds })
  const degradations = Array.isArray(material?.degradations) ? material.degradations : []
  const messages = degradations.map((item) => item?.message).filter(Boolean)
  const subjectConflict = degradations.find((item) => item?.kind === 'subject_conflict') || null
  // 版本角色是后端 DTO 的只读事实，不是人工处置状态。旧候选响应尚未带该字段时，
  // 暂保留基于替代指针的兼容回退；一旦 DTO 明确给出 version_role，后续投影不得再
  // 用前端图推导覆盖它（尤其不能把 legacy_unknown 猜成 current）。
  const declaredVersionRole = ['current', 'superseded', 'legacy_unknown'].includes(material?.version_role)
    ? material.version_role
    : null
  const revisionNo = Number.isInteger(material?.revision_no) ? material.revision_no : null
  return {
    id,
    fileName: material?.file_name || '未命名材料',
    role: material?.role || 'unknown',
    roleLabel: material?.role_label || material?.role || '待归类',
    subjectId: material?.subject_id ?? null,
    subjectName: subjectName(subjectsById, material?.subject_id),
    containerMaterialId: material?.container_material_id ?? null,
    parseKind: state.kind,
    parseLabel: state.label,
    tone: state.tone,
    pages: Number.isInteger(material?.pages) ? material.pages : null,
    source: materialSourcePath(material) || null,
    keyFacts: keyFacts(material),
    messages,
    subjectConflict: subjectConflict ? {
      expectedSubjectName: subjectConflict.expected_subject_name || null,
      detectedSubjectName: subjectConflict.detected_subject_name || null,
      confidence: subjectConflict.confidence || null,
    } : null,
    isContainer,
    canPreview: state.kind === 'done' && !isContainer,
    // 刀B（BS-P2-01）替代链：旧行 → 新行的显式指向（无库级外键，只做同任务 id 解析）。
    supersededById: material?.superseded_by_id ?? null,
    supersededAt: material?.superseded_at ?? null,
    // 第二遍填充：替代目标行名与可预览性（目标找不到就不猜名字）。
    supersededByName: null,
    supersededByCanPreview: false,
    revisionNo,
    versionRole: declaredVersionRole || (material?.superseded_by_id != null ? 'superseded' : 'legacy_unknown'),
    // 显式 version_role 优先；没有该字段时才由下方旧 DTO 兼容逻辑补出 current。
    isCurrent: declaredVersionRole === 'current',
    hasDeclaredVersionRole: Boolean(declaredVersionRole),
  }
}

export function buildMaterialLedger(detail, containerIds = new Set()) {
  const materials = Array.isArray(detail?.materials) ? detail.materials : []
  const subjectsById = Object.fromEntries((detail?.subjects || []).map((subject) => [String(subject.subject_id), subject]))
  const rows = materials.map((material) => buildRow(material, {
    taskState: detail?.state,
    containerIds,
    subjectsById,
  }))
  // 替代目标的解析只用同任务材料行（跨任务 id 不串名；目标被删则留空）。
  const byId = new Map(rows.map((row) => [String(row.id), row]))
  const targetIds = new Set(rows
    .filter((row) => row.supersededById != null)
    .map((row) => String(row.supersededById)))
  for (const row of rows) {
    // 仅对没有显式版本角色的旧 DTO 做兼容回退；新 DTO 的 current/superseded/
    // legacy_unknown 是唯一权威，不能被同任务图推导覆盖。
    if (!row.hasDeclaredVersionRole && row.supersededById == null && targetIds.has(String(row.id))) {
      row.versionRole = 'current'
      row.isCurrent = true
    }
    if (row.supersededById != null) {
      const target = byId.get(String(row.supersededById))
      row.supersededByName = target?.fileName || null
      row.supersededByCanPreview = Boolean(target?.canPreview)
    }
  }
  // 内部兼容标记不属于 UI 契约，避免泄露为第二套状态字段。
  for (const row of rows) delete row.hasDeclaredVersionRole
  return {
    rows,
    summary: {
      total: rows.length,
      ready: rows.filter((row) => row.parseKind === 'done').length,
      processing: rows.filter((row) => ['uploaded', 'parsing', 'stored'].includes(row.parseKind)).length,
      attention: rows.filter((row) => ['bad', 'warn'].includes(row.tone)).length,
    },
  }
}
