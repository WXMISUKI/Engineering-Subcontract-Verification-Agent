import { useMemo, useState } from 'react'
import MaterialPreviewModal from '../components/MaterialPreviewModal'
import { materialSourcePath } from '../lib/materialState'

const FILTERS = [
  ['all', '全部'],
  ['missing', '缺失'],
  ['pending', '待核实/重跑'],
  ['provided', '已提供'],
]

export function materialIdOf(material) {
  return material?.id ?? material?.material_id ?? null
}

export function checklistRowsOf(detail, checklistSummary, missingPlan) {
  const subjects = Array.isArray(detail?.subjects) ? detail.subjects : []
  const actual = Array.isArray(detail?.materials) ? detail.materials : []
  const actualById = new Map(actual.map((material) => [String(materialIdOf(material)), material]))
  const missingCategories = Array.isArray(missingPlan?.missing) ? missingPlan.missing : []
  const rows = []
  const consumedMaterialIds = new Set()
  for (const summary of Array.isArray(checklistSummary?.subjects) ? checklistSummary.subjects : []) {
    const subject = subjects.find((item) => String(item.subject_id ?? item.id) === String(summary.subject_id))
    for (const item of Array.isArray(summary.checklist_items) ? summary.checklist_items : []) {
      const material = item.material_id != null ? actualById.get(String(item.material_id)) : null
      if (material) consumedMaterialIds.add(String(materialIdOf(material)))
      const matchingCategories = missingCategories.filter((category) =>
        Array.isArray(category.itemIds) && item.item_id != null
          && category.itemIds.some((id) => String(id) === String(item.item_id)),
      )
      const missingCategory = matchingCategories.length === 1 ? matchingCategories[0] : null
      const missing = item.missing === true
      rows.push({
        ...material,
        id: `checklist:${summary.subject_id}:${item.item_id}`,
        material_id: materialIdOf(material),
        file_name: material?.file_name || '',
        role: missingCategory?.roles?.[0] || material?.role || 'checklist',
        category: missingCategory?.category || 'submission_packet',
        role_label: item.label || material?.role_label || '材料要求',
        subject_name: subject?.name || summary.name || '主体待确认',
        subject_id: summary.subject_id,
        source_subject_id: material?.subject_id ?? null,
        parse_state: material?.parse_state || (missing ? null : 'done'),
        checklist_item_id: item.item_id,
        checklist_page: item.page,
        checklist_location_mode: item.location_mode,
        finding_ids: item.finding_id != null ? [item.finding_id] : [],
        checklist_status: item.status || 'pending',
        checklist_missing: missing,
        material_state: item.material_state || null,
        material_confirmation: item.material_confirmation || null,
        synthetic_checklist: true,
      })
    }
  }
  for (const material of actual) {
    if (!consumedMaterialIds.has(String(materialIdOf(material)))) {
      const subject = subjects.find((item) => String(item.subject_id ?? item.id) === String(material.subject_id))
      rows.push({
        ...material,
        subject_name: material.subject_name || subject?.name || '',
      })
    }
  }
  return rows
}

export function statusOf(material) {
  if (material?.material_confirmation?.decision === 'nonconforming') return { key: 'missing', label: '需补件', tone: 'bad' }
  if (material?.material_confirmation?.decision === 'conforming') return { key: 'provided', label: '已提供', tone: 'ok' }
  if (material?.material_state === 'provided') return { key: 'provided', label: '已提供', tone: 'ok' }
  if (material?.material_state === 'missing') return { key: 'missing', label: '需补件', tone: 'bad' }
  if (material?.material_state === 'verification') return { key: 'verification', label: '待核实', tone: 'warn' }
  if (material?.checklist_missing === true) return { key: 'missing', label: '缺失', tone: 'bad' }
  if (material?.checklist_status === 'pending') return { key: 'pending', label: '待重跑', tone: 'warn' }
  if (material?.superseded_by_id != null) return { key: 'superseded', label: '已替换', tone: 'muted' }
  if (['parsing', 'uploaded', 'preparing', 'stored'].includes(material?.parse_state)) return { key: 'pending', label: '待重跑', tone: 'warn' }
  if (material?.parse_state === 'failed') return { key: 'missing', label: '读取失败', tone: 'bad' }
  if (material?.synthetic_checklist && material?.checklist_status === 'undecidable') return { key: 'verification', label: '待核实', tone: 'warn' }
  if (material?.file_name) return { key: 'provided', label: '已提供', tone: 'ok' }
  return { key: 'missing', label: '缺失', tone: 'bad' }
}

export function missingMaterialCountOf(detail, checklistSummary, missingPlan) {
  const rows = checklistRowsOf(detail, checklistSummary, missingPlan)
  if (rows.length > 0) return rows.filter((material) => statusOf(material).key === 'missing').length
  return missingPlan?.missing?.reduce((total, item) => total + Number(item.count || 0), 0) ?? null
}

function statusClass(tone) {
  if (tone === 'ok') return 'border-[#CFE8D4] bg-[#F6FCF7] text-[#2E7D32]'
  if (tone === 'warn') return 'border-[#F0DCB8] bg-[#FFFDF8] text-[#92400E]'
  if (tone === 'bad') return 'border-[#F5C6C6] bg-[#FFF7F7] text-[#C62828]'
  return 'border-[#E5E7EB] bg-[#FAFAFA] text-[#6B7280]'
}

function matchesFilter(material, filter) {
  if (filter === 'all') return true
  const key = statusOf(material).key
  return key === filter || (filter === 'pending' && key === 'verification')
}

function matchingFindings(material, findings) {
  const id = materialIdOf(material)
  const findingIds = new Set((material?.finding_ids || []).map((value) => String(value)))
  return (Array.isArray(findings) ? findings : []).filter((finding) => (
    (finding?.id != null && findingIds.has(String(finding.id)))
    || (id != null && finding?.material_id != null && String(finding.material_id) === String(id))
  ))
}

function materialMetadata(material, findings, stepOverview) {
  const related = matchingFindings(material, findings)
  const stepByFinding = new Map()
  for (const step of stepOverview?.steps || []) {
    for (const findingId of step.findingIds || []) stepByFinding.set(String(findingId), step)
  }
  const steps = [...new Map(related
    .map((finding) => stepByFinding.get(String(finding.id)))
    .filter(Boolean)
    .map((step) => [String(step.stepId), step])).values()]
  const coverage = material?.reading_coverage && typeof material.reading_coverage === 'object'
    ? material.reading_coverage
    : null
  const source = materialSourcePath(material) || (material?.file_name ? '直接上传' : '未记录来源')
  return {
    source,
    steps,
    findings: related,
    coverage,
  }
}

export default function PrototypeMaterialsWorkspace({
  detail = null,
  checklistSummary = null,
  missingPlan = null,
  findings = [],
  stepOverview = null,
  onOpen,
  onSupplement,
  onRequestEdit,
  onReplace,
  onOpenReview,
  onBack,
}) {
  const materials = useMemo(() => checklistRowsOf(detail, checklistSummary, missingPlan), [detail, checklistSummary, missingPlan])
  const [filter, setFilter] = useState('all')
  const [query, setQuery] = useState('')
  const [selectedId, setSelectedId] = useState(() => materialIdOf(materials[0]))
  const [previewId, setPreviewId] = useState(null)
  const visible = useMemo(() => materials.filter((material) => {
    if (!matchesFilter(material, filter)) return false
    const text = `${material.file_name || ''} ${material.role_label || material.role || ''}`.toLowerCase()
    return !query.trim() || text.includes(query.trim().toLowerCase())
  }), [filter, materials, query])
  const selected = materials.find((material) => String(materialIdOf(material)) === String(selectedId)) || visible[0] || null
  const selectedStatus = statusOf(selected)
  const selectedMetadata = materialMetadata(selected, findings, stepOverview)
  const counts = {
    all: materials.length,
    missing: materials.filter((material) => statusOf(material).key === 'missing').length,
    pending: materials.filter((material) => matchesFilter(material, 'pending')).length,
    provided: materials.filter((material) => statusOf(material).key === 'provided').length,
  }

  return (
    <section data-testid="prototype-materials-workspace" className="min-w-0 space-y-3">
      <div className="grid min-w-0 gap-3 min-[1051px]:grid-cols-[minmax(0,1fr)_340px]">
        <section className="min-w-0 overflow-hidden rounded-[8px] border border-[#E9E3D9] bg-white">
          <div className="flex flex-wrap items-center gap-3 border-b border-[#E9E3D9] p-3">
            <div className="min-w-[180px] flex-1">
              <h2 className="text-sm font-semibold text-[#1A1A1A]">材料清单</h2>
              <p className="mt-0.5 text-[11px] text-[#6B7280]">应报 {counts.all} 项 · 已提供 {counts.provided} 项 · 缺 {counts.missing} 项</p>
            </div>
            <div className="flex flex-wrap items-center justify-end gap-1.5">
              <div className="flex flex-wrap gap-1" aria-label="材料筛选">
                {FILTERS.map(([key, label]) => <button key={key} type="button" aria-pressed={filter === key} onClick={() => setFilter(key)} className={`min-h-9 rounded-[4px] border px-2.5 text-[11px] ${filter === key ? 'border-[#8B7355] bg-[#F7F1E9] text-[#5B4A2F]' : 'border-[#E5E5E5] text-[#6B7280]'}`}>{label} {counts[key]}</button>)}
              </div>
              <label className="sr-only" htmlFor="prototype-material-search">搜索材料名称</label>
              <input id="prototype-material-search" aria-label="搜索材料名称" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索材料名称、来源或文件" className="min-h-9 w-[220px] max-w-full rounded-[4px] border border-[#E5E5E5] px-2.5 text-xs outline-none focus:border-[#8B7355]" />
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] table-fixed border-collapse text-left text-xs">
              <thead className="bg-[#F8FAFC] text-[11px] text-[#64748B]"><tr><th className="px-3 py-2">#</th><th className="px-3 py-2">材料名称 / 要求</th><th className="px-3 py-2">所属步骤 / 来源</th><th className="px-3 py-2">当前文件</th><th className="px-3 py-2">状态</th><th className="px-3 py-2">操作</th></tr></thead>
              <tbody>
                {visible.map((material, index) => {
                  const id = materialIdOf(material)
                  const status = statusOf(material)
                  const metadata = materialMetadata(material, findings, stepOverview)
                  const stepText = metadata.steps.length ? metadata.steps.map((step) => step.title).join('、') : '未关联步骤'
                  const selectedRow = String(id) === String(selected?.id ?? selected?.material_id)
                  return <tr key={id || material.file_name || index} data-testid={`prototype-material-row-${id}`} className={`border-t border-[#EEF2F7] ${selectedRow ? 'bg-[#F7F1E9]' : 'hover:bg-[#FBFDFF]'}`} onClick={() => setSelectedId(id)}>
                    <td className="px-3 py-2.5 tabular-nums text-[#94A3B8]">{materials.indexOf(material) + 1}</td>
                    <td className="max-w-[220px] px-3 py-2.5"><button type="button" className="max-w-full text-left" onClick={() => setSelectedId(id)}><b className="block truncate text-[#1A1A1A]">{material.synthetic_checklist ? (material.role_label || '材料要求') : (material.file_name || '未提供')}</b><span className="mt-0.5 block truncate text-[10px] text-[#94A3B8]">{metadata.findings.length ? `影响 ${metadata.findings.length} 条结论` : '暂无关联结论'}</span></button></td>
                    <td className="max-w-[160px] px-3 py-2.5 text-[#4B5563]"><span className="block truncate" title={stepText}>{stepText}</span><span className="block truncate text-[10px] text-[#94A3B8]" title={metadata.source}>{metadata.source}</span></td>
                    <td className="max-w-[130px] truncate px-3 py-2.5 text-[#4B5563]">{material.file_name || '未提供'}</td>
                    <td className="px-3 py-2.5"><span className={`inline-flex rounded-full border px-2 py-0.5 text-[10px] ${statusClass(status.tone)}`}>{status.label}</span></td>
                    <td className="whitespace-nowrap px-3 py-2.5"><div className="flex flex-nowrap gap-1.5">{status.key === 'missing' ? <button type="button" className="whitespace-nowrap text-[11px] text-[#8B7355] underline" onClick={(event) => { event.stopPropagation(); onSupplement?.(material) }}>上传补件</button> : status.key === 'pending' ? <button type="button" className="whitespace-nowrap text-[11px] text-[#8B7355] underline" onClick={(event) => { event.stopPropagation(); onRequestEdit?.() }}>重新核查</button> : <>{material.file_name && /\.pdf$/i.test(material.file_name) && <button type="button" className="whitespace-nowrap text-[11px] text-[#8B7355] underline" onClick={(event) => { event.stopPropagation(); setPreviewId(materialIdOf(material)); onOpen?.(materialIdOf(material)) }}>查看原文</button>}<button type="button" className="whitespace-nowrap text-[11px] text-[#8B7355] underline" onClick={(event) => { event.stopPropagation(); onReplace?.(material) }}>替换</button></>}</div></td>
                  </tr>
                })}
                {!visible.length && <tr><td colSpan="6" className="px-3 py-8 text-center text-xs text-[#94A3B8]">当前筛选下没有材料。</td></tr>}
              </tbody>
            </table>
          </div>
        </section>

        <aside data-testid="prototype-material-detail" className="min-w-0 rounded-[8px] border border-[#E9E3D9] bg-white p-4">
          {!selected ? <p className="text-xs text-[#94A3B8]">请选择一份材料查看详情。</p> : <>
            <h2 className="truncate text-sm font-semibold text-[#1A1A1A]" title={selected.file_name}>{selected.file_name || '未提供材料'}</h2>
            <div className="mt-2 flex flex-wrap gap-1.5"><span className={`rounded-full border px-2 py-0.5 text-[10px] ${statusClass(selectedStatus.tone)}`}>{selectedStatus.label}</span><span className="rounded-full border border-[#E5E7EB] px-2 py-0.5 text-[10px] text-[#6B7280]">材料 {materialIdOf(selected)}</span></div>
            <dl className="mt-3 grid grid-cols-[76px_1fr] gap-x-2 gap-y-2 text-xs"><dt className="text-[#94A3B8]">归类</dt><dd>{selected.role_label || selected.role || '待归类'}</dd><dt className="text-[#94A3B8]">主体</dt><dd>{selected.subject_name || '主体待确认'}</dd><dt className="text-[#94A3B8]">步骤</dt><dd>{selectedMetadata.steps.length ? selectedMetadata.steps.map((step) => `第${step.index}步 ${step.title}`).join('、') : '未关联步骤'}</dd><dt className="text-[#94A3B8]">来源</dt><dd className="break-words">{selectedMetadata.source}</dd><dt className="text-[#94A3B8]">影响结论</dt><dd>{selectedMetadata.findings.length ? `${selectedMetadata.findings.length} 条` : '暂无'}</dd><dt className="text-[#94A3B8]">读取</dt><dd>{selected.parse_state || '待确认'}</dd><dt className="text-[#94A3B8]">覆盖</dt><dd>{selectedMetadata.coverage ? `共 ${selectedMetadata.coverage.total_pages ?? '未知'} 页，已读 ${selectedMetadata.coverage.read_pages?.length ?? 0} 页，未读 ${selectedMetadata.coverage.unread_pages?.length ?? 0} 页` : '暂无覆盖数据'}</dd></dl>
            <div className="mt-4 border-t border-[#F1ECE4] pt-3"><h3 className="text-xs font-medium text-[#6B7280]">材料动作</h3><div className="mt-2 flex flex-wrap gap-2">{selectedStatus.key === 'missing' ? <button type="button" onClick={() => onSupplement?.(selected)} className="min-h-9 rounded-[4px] bg-[#8B7355] px-2.5 text-[11px] text-white">上传补件</button> : <>{selected.file_name && /\.pdf$/i.test(selected.file_name) && <button type="button" onClick={() => { setPreviewId(materialIdOf(selected)); onOpen?.(materialIdOf(selected)) }} className="min-h-9 rounded-[4px] border border-[#8B7355] px-2.5 text-[11px] text-[#8B7355]">查看原文</button>}</>}<button type="button" onClick={onOpenReview} className="min-h-9 rounded-[4px] border border-[#8B7355] px-2.5 text-[11px] text-[#8B7355]">去查看问题</button></div></div>
          </>}
        </aside>
      </div>
      {previewId != null && <MaterialPreviewModal materials={Array.isArray(detail?.materials) ? detail.materials : []} activeMaterialId={previewId} requireParsed={false} onSelect={setPreviewId} onClose={() => setPreviewId(null)} />}
    </section>
  )
}
