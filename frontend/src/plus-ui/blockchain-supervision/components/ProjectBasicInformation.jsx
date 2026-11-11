import React from 'react'

const fields = [
  ['工程名称', 'project_name'], ['标段', 'contract_section'], ['施工总承包单位', 'general_contractor'],
  ['建设单位（业主）', 'employer'], ['分包单位', 'subcontractor'], ['拟分包范围', 'subcontract_scope'],
  ['拟分包金额', 'subcontract_amount'],
]

export default function ProjectBasicInformation({ report, detail, onOpenSource, onConfirmEmployer, confirmingEmployer = false }) {
  const basic = report?.basic_information || { ...(detail?.report_basic_facts || {}), ...(detail?.contract_party_evidence || {}) }
  const projected = report?.business_document?.basic_information || {}
  const statuses = report?.business_document?.basic_information_status || {}
  return <section id="project-basic-information" tabIndex={-1} aria-label="项目基本信息" className="mb-4 rounded border border-[#EFE6D8] bg-white px-4 py-3 outline-none focus:ring-2 focus:ring-[#B48C55]">
    <h2 className="text-sm font-semibold text-[#1A1A1A]">项目基本信息</h2>
    <dl className="mt-2 grid gap-x-6 gap-y-2 text-xs sm:grid-cols-2 lg:grid-cols-3">
      {fields.map(([label, key]) => {
        const fact = basic[key] || {}
        const taskValue = ['project_name', 'contract_section'].includes(key) ? detail?.section?.[key] : null
        const subject = key === 'subcontractor' && detail?.subjects?.length === 1 ? detail.subjects[0] : null
        const hasProjectedValue = Object.prototype.hasOwnProperty.call(projected, label)
        const value = hasProjectedValue ? projected[label] : taskValue || fact.company || (fact.value != null ? `${fact.value}${fact.unit || ''}` : null) || (subject?.name_status === 'confirmed' ? subject.name : null)
        const candidates = fact.material_candidates || fact.evidence || []
        const taskConflict = taskValue && (fact.source === 'task' ? fact.status === 'conflict' : candidates.some((row) => row?.value && String(row.value).replace(/\s/g, '') !== String(taskValue).replace(/\s/g, '')))
        const status = statuses[label] || (taskConflict ? 'conflict' : fact.status)
        const sources = candidates.filter((row) => row?.material_id != null && Number.isInteger(row?.page) && row.page > 0)
        return <div key={key} className="min-w-0 border-b border-[#F3EDE3] py-1">
          <dt className="text-[#6B7280]">{label}</dt>
          <dd className="break-words text-[#1A1A1A]">{value ? `${value}${['conflict', 'unresolved'].includes(status) && (hasProjectedValue || !taskValue || taskConflict) ? '（待核实）' : ''}` : status === 'conflict' ? '待核实（材料冲突）' : '待核实'}</dd>
          {key === 'employer' && status === 'unresolved' && (fact.recipient_candidates || []).map((candidate, index) => <div key={`${candidate.material_id}-${candidate.page}-${index}`} className="mt-1 space-y-1">
            <p>收件人候选：{candidate.value}（未确认）</p>
            {onOpenSource && <button type="button" className="mr-2 text-[#315d79] underline" onClick={() => onOpenSource(candidate.material_id, candidate.page)}>查看收件人原文（第{candidate.page}页）</button>}
            {onConfirmEmployer && <button type="button" disabled={confirmingEmployer} className="text-[#315d79] underline disabled:opacity-50" onClick={() => onConfirmEmployer(candidate)}>确认{candidate.value}为建设单位</button>}
          </div>)}
          {onOpenSource && sources.map((source, index) => <button key={`${source.material_id}-${source.page}-${index}`} type="button" className="mr-2 text-left text-[#315d79] underline" onClick={() => onOpenSource(source.material_id, source.page)}>{source.file_name || '材料'} 第{source.page}页{source.value ? `（${source.value}）` : ''}</button>)}
        </div>
      })}
    </dl>
  </section>
}
