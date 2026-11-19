const PERSON_SIDES = {
  management_roster: 'roster',
  contract_roster: 'roster',
  social_record: 'social',
  labor_contract: 'labor',
  person_certificate: 'certificate',
  attendance_record: 'attendance',
  project_assignment: 'assignment',
  attendance_commitment: 'commitment',
}

/**
 * 人员证据按人分组；只做展示分组，不把同名推断成同一人。
 * @param {Array<object>} evidence finding.evidence 原始引用
 */
export function buildPersonnelClueGroups(evidence) {
  const groups = new Map()
  for (const ref of Array.isArray(evidence) ? evidence : []) {
    const side = PERSON_SIDES[ref?.evidence_kind]
    const name = typeof ref?.name === 'string' ? ref.name.trim() : ''
    if (!side || !name) continue
    const personKey = ref.person_key || name
    const key = `${ref.subject_id ?? ''}:${personKey}`
    if (!groups.has(key)) {
      groups.set(key, { key, name, post: null, certificateView: false, attendanceView: false,
        sides: { roster: [], social: [], labor: [], certificate: [], attendance: [], assignment: [], commitment: [] } })
    }
    const group = groups.get(key)
    if (ref.certificate_identity_state) group.certificateView = true
    if (ref.attendance_identity_state || ['attendance', 'assignment', 'commitment'].includes(side)) group.attendanceView = true
    if (!group.post && ref.post) group.post = ref.post
    const materialId = ref.material_id
    const page = Number(ref.page)
    if (materialId == null || !Number.isInteger(page) || page < 1) continue
    const exists = group.sides[side].some((row) => String(row.materialId) === String(materialId) && row.page === page
      && (!['certificate', 'attendance', 'assignment', 'commitment'].includes(side) || row.quote === (ref.quote || '')))
    if (!exists) {
      const row = { materialId, page, quote: ref.quote || '' }
      const fields = ref.document_title ? [ref.document_title] : []
      for (const [field, label] of [['professional_title', '职称'], ['qualification_declaration', '合同资格声明'], ['certificate_type', '类别'],
        ['certificate_number', '编号'], ['validity_start', '有效期起'], ['validity_end', '有效期至'],
        ['validity_period', '有效期'], ['project', '项目'], ['period', '日期/期间'], ['attendance_state', '原文状态']]) {
        const candidates = Array.isArray(ref[`${field}_candidates`]) ? ref[`${field}_candidates`] : []
        const values = [...new Set([ref[field], ...candidates].filter((v) => typeof v === 'string' && v.trim()))]
        if (values.length) fields.push(`${label}${values.length > 1 ? '候选：' : '：'}${values.join(' / ')}${values.length > 1 ? '（需核实）' : ''}`)
      }
      if (ref.certificate_identity_state) fields.push(ref.certificate_identity_state === 'identity_fields_correspond'
        ? '身份字段对应（原件仍需核对）' : '身份待核实')
      if (ref.attendance_identity_state) fields.push(ref.attendance_identity_state === 'identity_fields_correspond'
        ? '身份字段对应（履行仍需核对）' : '身份待核实')
      if (fields.length) row.fields = fields
      group.sides[side].push(row)
    }
  }
  return [...groups.values()]
}
