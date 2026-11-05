// 主体级清单摘要只消费 /todo.checklist_summary 的权威投影。
// 本模块仅做契约校验与展示字段映射：不补零、不重算分母、不从 finding 文案猜计数。

const SCHEMA_VERSION = '1.0'
const COUNT_UNIT = 'checklist_item_per_subject'
const SUBJECT_STATUSES = new Set(['ready', 'type_pending'])
const READY_SUBJECT_TYPES = new Set(['professional', 'labor'])

export const CHECKLIST_COUNT_FIELDS = Object.freeze([
  'base_total',
  'form_checks_total',
  'project_supplements_applicable_total',
  'applicable_total',
  'present_total',
  'nonconforming_total',
  'missing_total',
  'undecidable_total',
  'pending_total',
])

function fallback(kind, message) {
  return {
    kind,
    message,
    schemaVersion: null,
    countUnit: null,
    countNote: '',
    profileCodes: [],
    guidanceNotes: [],
    subjects: [],
  }
}

function requireObject(value, path) {
  if (value == null || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${path} 格式无效`)
  }
  return value
}

function requireText(value, path, { nullable = false } = {}) {
  if (nullable && value == null) return null
  if (typeof value !== 'string' || !value.trim()) throw new Error(`${path} 缺失或格式无效`)
  return value
}

function requireBoolean(value, path) {
  if (typeof value !== 'boolean') throw new Error(`${path} 缺失或格式无效`)
  return value
}

function requireProfileCodes(value, path) {
  if (!Array.isArray(value)) throw new Error(`${path} 缺失或格式无效`)
  value.forEach((code, index) => requireText(code, `${path}[${index}]`))
  return value
}

function requireGuidanceNotes(value, path) {
  if (!Array.isArray(value)) throw new Error(`${path} 缺失或格式无效`)
  value.forEach((raw, index) => {
    const note = requireObject(raw, `${path}[${index}]`)
    requireText(note.code, `${path}[${index}].code`)
    requireText(note.label, `${path}[${index}].label`)
    requireText(note.detail, `${path}[${index}].detail`)
    if (note.counted_as_missing !== false) {
      throw new Error(`${path}[${index}].counted_as_missing 必须为 false`)
    }
  })
  return value
}

function requireProceduralHints(value, path) {
  if (!Array.isArray(value)) throw new Error(`${path} 缺失或格式无效`)
  value.forEach((raw, index) => {
    const hint = requireObject(raw, `${path}[${index}]`)
    requireText(hint.code, `${path}[${index}].code`)
    requireText(hint.text, `${path}[${index}].text`)
    if (hint.counted_as_missing !== false) {
      throw new Error(`${path}[${index}].counted_as_missing 必须为 false`)
    }
  })
  return value
}

function requireCounts(value, subjectLabel) {
  const counts = requireObject(value, `${subjectLabel}.checklist_summary`)
  for (const field of CHECKLIST_COUNT_FIELDS) {
    if (!Object.prototype.hasOwnProperty.call(counts, field)) {
      throw new Error(`${subjectLabel}.checklist_summary 缺少 ${field}`)
    }
    if (!Number.isInteger(counts[field]) || counts[field] < 0) {
      throw new Error(`${subjectLabel}.checklist_summary.${field} 必须为非负整数`)
    }
  }

  const expectedApplicable = counts.base_total
    + counts.form_checks_total
    + counts.project_supplements_applicable_total
  if (counts.applicable_total !== expectedApplicable) {
    throw new Error(`${subjectLabel}.checklist_summary.applicable_total 与基础、表单、项目补充三项合计不闭合`)
  }
  if (counts.missing_total > counts.nonconforming_total) {
    throw new Error(`${subjectLabel}.checklist_summary.missing_total 不得大于 nonconforming_total`)
  }
  const classifiedTotal = counts.present_total
    + counts.nonconforming_total
    + counts.undecidable_total
    + counts.pending_total
  if (classifiedTotal !== counts.applicable_total) {
    throw new Error(`${subjectLabel}.checklist_summary 状态合计与 applicable_total 不闭合`)
  }
  return counts
}

function requireSubject(raw, index, seenIds) {
  const subject = requireObject(raw, `subjects[${index}]`)
  if (!Number.isInteger(subject.subject_id) || subject.subject_id <= 0) {
    throw new Error(`subjects[${index}].subject_id 必须为正整数`)
  }
  if (seenIds.has(subject.subject_id)) throw new Error(`主体 ${subject.subject_id} 重复`)
  seenIds.add(subject.subject_id)

  const stableCode = requireText(subject.stable_code, `subjects[${index}].stable_code`)
  requireText(subject.name, `subjects[${index}].name`, { nullable: true })
  if (subject.subject_type != null && typeof subject.subject_type !== 'string') {
    throw new Error(`${stableCode}.subject_type 格式无效`)
  }
  requireBoolean(subject.type_confirmed, `${stableCode}.type_confirmed`)
  requireBoolean(subject.type_conflict, `${stableCode}.type_conflict`)
  if (!SUBJECT_STATUSES.has(subject.status)) throw new Error(`${stableCode}.status 格式无效`)

  const statusReason = subject.status === 'ready'
    ? requireText(subject.status_reason, `${stableCode}.status_reason`, { nullable: true })
    : requireText(subject.status_reason, `${stableCode}.status_reason`)

  // 方案A：类型以用户所选资料类别为准。type_conflict 只是「AI 证据与所选类别
  // 不一致」的提示标记，与 status 相互独立——ready 与 type_pending 都可能带它。
  if (subject.status === 'ready'
    && (!subject.type_confirmed || !READY_SUBJECT_TYPES.has(subject.subject_type))) {
    throw new Error(`${stableCode}.ready 状态缺少已确认的专业/劳务类型`)
  }

  requireProfileCodes(subject.profile_codes, `${stableCode}.profile_codes`)
  requireProceduralHints(subject.procedural_hints, `${stableCode}.procedural_hints`)
  requireCounts(subject.checklist_summary, stableCode)
  return { ...subject, status_reason: statusReason }
}

export function buildChecklistSummaryModel(rawSummary) {
  if (rawSummary == null) {
    return fallback('unavailable', '当前任务未返回主体级清单摘要，可能是历史任务或旧版接口；仍可继续查看五步总览。')
  }

  try {
    const summary = requireObject(rawSummary, 'checklist_summary')
    if (summary.schema_version !== SCHEMA_VERSION) {
      throw new Error(`schema_version 不受支持（当前仅支持 ${SCHEMA_VERSION}）`)
    }
    if (summary.count_unit !== COUNT_UNIT) {
      throw new Error(`count_unit 不受支持（应为 ${COUNT_UNIT}）`)
    }
    const countNote = requireText(summary.count_note, 'count_note')
    const profileCodes = requireProfileCodes(summary.profile_codes, 'profile_codes')
    const guidanceNotes = requireGuidanceNotes(summary.guidance_notes, 'guidance_notes')
    if (!Array.isArray(summary.subjects)) throw new Error('subjects 缺失或格式无效')
    const seenIds = new Set()
    const subjects = summary.subjects.map((subject, index) => requireSubject(subject, index, seenIds))

    return {
      kind: 'ready',
      message: '',
      schemaVersion: summary.schema_version,
      countUnit: summary.count_unit,
      countNote,
      profileCodes,
      guidanceNotes,
      subjects,
    }
  } catch (error) {
    return fallback('invalid', `主体级清单摘要数据不完整或不一致：${error.message}`)
  }
}
