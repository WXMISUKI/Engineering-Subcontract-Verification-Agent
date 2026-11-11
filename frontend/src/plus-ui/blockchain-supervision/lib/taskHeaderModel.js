import { subjectTypeLabel } from './materialState'

// 任务抬头（原型 .task-head）的取数模型。
//
// 原型的抬头是两行（bs-full-flow-prototype-v1.html#L1103-L1114）：
//   第一行：报审包编号 · 分包单位名称
//   第二行：类型 · 合同金额 · 项目 · 签订日期 · 页数整册
// 但原型第二行里的「合同金额 / 签订日期 / 报审包编号」来自演示常量 PACKET，
// 后端目前没有对应字段（bs_material 只有 pages；报审包首页字段抽取尚未立项）。
//
// 因此这里按「结构与原型一致、取不到的字段整段不渲染」处理：
// 绝不渲染「业主：— / 总包：—」这类占位——那会让读者以为数据本该存在。
// 未来抽取能力上线时，只需在 FIELDS 的 source 上换映射，UI 与布局无需改动。
// 口径（2026-10-29 用户确认）：分包单位取**有名字的主体，不区分专业/劳务**。

/** 第二行字段定义：source 说明值从哪来，仅用于本模型内部取数。 */
const FIELDS = [
  { key: 'type', label: '类型', source: 'subject_type' },
  { key: 'contractor', label: '承包单位', source: 'contract_party_evidence' },
  { key: 'employer', label: '建设单位', source: 'contract_party_evidence' },
  { key: 'scope', label: '拟分包范围', source: 'report_basic_facts' },
  { key: 'amount', label: '分包金额', source: 'contract_fact' },
  { key: 'project', label: '项目', source: 'section_project' },
  { key: 'signed', label: '签订', source: 'contract_fact' },
  { key: 'pages', label: '页数整册', source: 'material_pages' },
]

/** 分包类主体（专业/劳务），且名称非空；名称去重后保持出现顺序。 */
function subcontractorNames(subjects) {
  const seen = new Set()
  const names = []
  const types = new Set()
  for (const subject of Array.isArray(subjects) ? subjects : []) {
    const type = subject?.subject_type
    const label = subjectTypeLabel(type) || subjectTypeLabel(subject?.type)
    if (!label) continue // 业主/总包不计入「分包单位」
    const name = typeof subject?.name === 'string' ? subject.name.trim() : ''
    if (name && !seen.has(name)) { seen.add(name); names.push(name) }
    if (label) types.add(label)
  }
  return { names, types: Array.from(types) }
}

// 合同金额 / 签订日期来自报告的分包合同要素抽取（A-15），不是演示常量。
// 只认 `status === 'hit'`：抽取未命中或未跑核查时保持 null → 抬头不渲染该段
// （三态判定：命中才展示，其余不假装有值）。
// 优先取分包合同（专业/劳务）的要素；没有分包要素时退回第一份合同要素。
function contractFactValue(fact, key) {
  const field = fact?.fields?.[key]
  if (!field || field.status !== 'hit') return null
  const value = field.value
  if (value === null || value === undefined || value === '') return null
  const displayValue = String(value).trim()
  return `${displayValue}${field.unit || ''}`
}

function pickContractFact(facts) {
  const list = Array.isArray(facts) ? facts.filter(Boolean) : []
  if (!list.length) return null
  return list.find((fact) => ['sub', 'labor'].includes(fact.role) || /分包/.test(fact.role_label || '')) || null
}

function contractorValue(evidence) {
  const party = evidence?.general_contractor
  if (!party || typeof party !== 'object') return null
  if (party.status === 'matched' && typeof party.company === 'string' && party.company.trim()) {
    return party.company.trim()
  }
  if (party.status === 'conflict') return '待核实（主体冲突）'
  if (party.status === 'unresolved' && party.candidate_company) {
    return `待核实（候选：${String(party.candidate_company).trim()}）`
  }
  if (party.status === 'unresolved') return '未识别/待核实'
  return null
}

// 整册页数取 `reading_coverage.total_pages`（parsing.reading_coverage 的唯一定义：
// 降级记录里的 total_pages 优先，其次 pages_json 最大页码）。detail DTO 不直接暴露
// 页数列，故这是唯一可信来源，不另建第二套统计。
// 口径：只要有一份在册材料的总页数不可判定，整册就是不可判定 → 整段不渲染，
// 不拿"已知部分的合计"假冒整册（对外数字必须说清分母）。
function totalPages(materials) {
  const active = (Array.isArray(materials) ? materials : [])
    .filter((material) => material && material.superseded_by_id == null)
  if (!active.length) return null
  let total = 0
  for (const material of active) {
    const pages = Number(material?.reading_coverage?.total_pages)
    if (!Number.isInteger(pages) || pages <= 0) return null
    total += pages
  }
  return total
}

/**
 * @returns {{ company: string|null, fields: Array<{key:string,label:string,value:string|null}> }}
 *   company：上行「分包单位」；fields：下行按原型的字段顺序，value 为 null 表示不渲染。
 */
export function buildTaskHeader({ detail, report } = {}) {
  const subjects = detail?.subjects || []
  const materials = detail?.materials || []
  // 报告与任务详情共享同一标段锚点；报告加载稍晚时仍优先使用详情，
  // 详情缺值则从报告 meta 回填，避免工作台只显示“类型 / 页数”而丢掉施工任务。
  const section = detail?.section || {}
  const reportMeta = report?.meta || {}
  const projectName = typeof section.project_name === 'string' && section.project_name.trim()
    ? section.project_name.trim()
    : typeof reportMeta.project_name === 'string' ? reportMeta.project_name.trim() : ''
  const contractSection = typeof section.contract_section === 'string' && section.contract_section.trim()
    ? section.contract_section.trim()
    : typeof reportMeta.contract_section === 'string' ? reportMeta.contract_section.trim() : ''
  const { names, types } = subcontractorNames(subjects)
  // 任务详情随当前材料版本实时回读合同事实；已有报告可能是旧版本快照，
  // 不能覆盖补件/解析修复后的最新金额与签订日期。报告事实仅作历史 DTO 兼容回退。
  const detailFacts = Array.isArray(detail?.contract_facts) ? detail.contract_facts : []
  const reportFacts = Array.isArray(report?.contract_facts) ? report.contract_facts : []
  const detailPartyEvidence = detail?.contract_party_evidence
  const reportPartyEvidence = report?.contract_party_evidence
  const partyEvidence = detailPartyEvidence && typeof detailPartyEvidence === 'object'
    ? detailPartyEvidence
    : reportPartyEvidence
  const contractFact = pickContractFact(detailFacts.length ? detailFacts : reportFacts)
  const packetFacts = detail?.report_basic_facts || {}
  const packetAmount = packetFacts.subcontract_amount || {}
  const contractAmount = contractFactValue(contractFact, 'amount')
  const packetAmountText = packetAmount.status === 'matched' && packetAmount.value != null
    ? `${packetAmount.value}${packetAmount.unit || ''}` : null
  const amount = packetAmount.status === 'conflict' || (packetAmountText && contractAmount && packetAmountText !== contractAmount)
    ? '待核实（金额冲突）' : (packetAmountText || contractAmount)
  const contractProject = typeof contractFact?.project_name === 'string' ? contractFact.project_name.trim() : ''
  const project = [projectName || contractProject, contractSection].filter(Boolean).join(' · ')

  const values = {
    type: types.length ? types.join('、') : null,
    contractor: contractorValue(partyEvidence),
    employer: contractorValue({ general_contractor: partyEvidence?.employer }),
    scope: packetFacts.subcontract_scope?.status === 'conflict' ? '待核实（范围冲突）' : packetFacts.subcontract_scope?.value,
    amount,
    project: project || null,
    signed: contractFactValue(contractFact, 'sign_date'),
    pages: totalPages(materials),
  }

  const headline = [
      detail?.packet_code,
      detail?.packetCode,
      detail?.report_code,
      detail?.reportCode,
      detail?.section?.packet_code,
      detail?.section?.packetCode,
      detail?.title,
    ].find((value) => typeof value === 'string' && value.trim())?.trim() || null

  return {
    // 后端尚未统一提供报审包编号时回退到任务标题；一旦 DTO 补齐任一编号字段，
    // 页面无需再次改结构即可按原型显示“编号 · 分包单位”。
    ...(headline ? { headline } : {}),
    company: names.length ? names.join('、') : null,
    fields: FIELDS.map((field) => ({
      key: field.key,
      label: field.label,
      value: values[field.key] == null ? null : String(values[field.key]),
    })).filter((field) => field.value != null),
  }
}
