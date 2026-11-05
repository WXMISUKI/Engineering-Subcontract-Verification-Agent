// 原型页唯一的展示适配层。
// 这里不执行规则、不推导业务结论，只把后端 DTO 变成四阶段页面可消费的稳定形状。

import { subjectCodeLabel, subjectDisplayName, subjectTypeLabel } from '../lib/materialState'

function formatFileSize(sizeKb) {
  const kb = Number(sizeKb)
  if (!Number.isFinite(kb) || kb <= 0) return ''
  if (kb < 1024) return `${Math.round(kb)} KB`
  const mb = kb / 1024
  return `${mb.toFixed(mb >= 10 ? 0 : 1).replace(/\.0$/, '')} MB`
}

export const PROTOTYPE_STAGES = [
  { key: 'upload', label: '上传材料' },
  { key: 'run', label: '智能核查' },
  { key: 'review', label: '人工复核' },
  { key: 'report', label: '审查报告' },
]

const STAGE_INDEX = Object.fromEntries(PROTOTYPE_STAGES.map((stage, index) => [stage.key, index]))

export function stageFromTaskState(state) {
  if (state === 'done') return 'report'
  if (state === 'review') return 'review'
  if (state === 'running') return 'run'
  return 'upload'
}

export function stageIndexOf(key) {
  return STAGE_INDEX[key] ?? 0
}

// 当前阶段默认由任务状态推导（上传阶段只有这一种来源）；工作台里用户所在屏幕
// 与任务状态可能不同（后端不写 review 态，人工复核期间任务仍是 running），
// 此时由调用方显式传入所在阶段，与原型的 state.stage 同义。
//
// 可点性（原型 renderStepper 的 `state.visited[i]`）：只取决于「曾经到达过」，
// 与此刻停在哪一阶段无关，且单调不回退；否则从人工复核退回上传材料会把后续
// 阶段一并锁死，再也回不去（2026-10-29 实机反馈）。
// `reachedIndex` 由页面级外壳记录（跨阶段切换不清零，作废结论时复位）。
export function buildPrototypeFlow(taskState, stage = null, reachedIndex = null) {
  const derived = stageFromTaskState(taskState)
  const current = stage || derived
  const currentIndex = STAGE_INDEX[current] ?? 0
  const reached = Math.max(
    STAGE_INDEX[derived] ?? 0,
    currentIndex,
    Number.isInteger(reachedIndex) ? reachedIndex : 0,
  )
  return {
    current,
    reachedIndex: reached,
    stages: PROTOTYPE_STAGES.map((stage, index) => ({
      ...stage,
      state: index < currentIndex ? 'done' : index === currentIndex ? 'active' : 'upcoming',
      reachable: index <= reached,
    })),
  }
}

export function buildPrototypeMaterial(material = {}, subjectsById = {}) {
  const subject = subjectsById[material.subject_id] || subjectsById[String(material.subject_id)]
    const rawFileName = material.file_name || ''
    const fileSizeKb = material.size_kb ?? material.file_size_kb ?? null
    const fileSizeLabel = formatFileSize(fileSizeKb)
    const fileName = fileSizeLabel ? `${rawFileName}\n${fileSizeLabel}` : rawFileName
  return {
    id: material.material_id ?? material.id ?? null,
    fileName,
      fileSizeKb,
    role: material.role || null,
    roleLabel: material.role_label || material.role || '',
    materialCategory: material.material_category || material.category || null,
    supplementLabel: material.supplement_label || material.todo_label || null,
    subjectConflict: (material.degradations || []).find((item) => item?.kind === 'subject_conflict') || null,
    subjectUnresolved: (material.degradations || []).find((item) => item?.kind === 'subject_unresolved') || null,
    subjectId: material.subject_id ?? null,
    subjectName: material.subject_name || material.subject?.name || subjectDisplayName(subject) || null,
    // 上传页「查看」直接读原件（GET /materials/{id}/file），不必等解析完成；
    // 只有 PDF 能在弹层内联预览，ZIP 原包不给「查看」，改由下载。
      canPreview: /\.pdf$/i.test(rawFileName),
    // 替换在解析成功后才由准备 worker 原子生效（设计 98 §6.2）；生效前只透出
    // 「本次上传要替换谁」，生效后透出「已被谁替换」，页面据此标状态，不猜。
    replacementTargetId: material.replacement_target_id ?? null,
    supersededById: material.superseded_by_id ?? null,
    // 页面只展示后端已经提供的事实；上传页不会把诊断字段提升到主路径。
    parseState: material.parse_state || null,
  }
}

export function buildPrototypeTask(detail = {}) {
  const subjectsById = Object.fromEntries((detail.subjects || []).map((subject) => [subject.subject_id, subject]))
  return {
    taskId: detail.task_id ?? detail.id ?? null,
    title: detail.title || '',
    state: detail.state || 'setup',
    progress: detail.progress || null,
    section: {
      projectName: detail.section?.project_name || '',
      contractSection: detail.section?.contract_section || '',
      baselineDate: detail.section?.baseline_date || '',
    },
    materials: (detail.materials || []).map((material) => buildPrototypeMaterial(material, subjectsById)),
    // 方案A：类型以用户所选资料类别为准；AI 证据反对只在台账里留一条提示，
    // 不阻断启动、也不再要求单独确认。
    subjectTypeNotices: (detail.subjects || [])
      .filter((subject) => subject && subject.type_conflict)
      .map((subject) => ({
        subjectId: subject.subject_id,
        label: subject.name
          ? `${subjectCodeLabel(subject.stable_code)} · ${subject.name}`
          : subjectCodeLabel(subject.stable_code),
        chosenTypeLabel: subjectTypeLabel(subject.subject_type) || '所选类别',
        reason: subject.type_conflict_reason || 'AI 识别证据与所选类别不一致',
      })),
    validation: detail.validation || null,
    flow: buildPrototypeFlow(detail.state),
  }
}

export function buildUploadReadiness({ validation, materials = [] } = {}) {
  const source = validation || {}
  const reasons = Array.isArray(source.hard_gaps) ? source.hard_gaps.filter(Boolean) : []
  const hasSubjectIssue = Array.isArray(source.subject_issue_material_ids) && source.subject_issue_material_ids.length > 0
  if (hasSubjectIssue) reasons.push('请先处理主体不一致或无法确认的材料')
  if (source.parsing_pending && !reasons.includes('材料读取中，读取完成后才能开始核查')) {
    reasons.unshift('材料读取中，读取完成后才能开始核查')
  }
  const ready = materials.length > 0 && source.hard_ok !== false && !source.parsing_pending && reasons.length === 0
  return { ready, reasons }
}

