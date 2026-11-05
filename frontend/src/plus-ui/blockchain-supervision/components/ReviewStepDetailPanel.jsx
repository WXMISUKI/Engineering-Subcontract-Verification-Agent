import { useEffect, useRef, useState } from 'react'
import { checklistRowsOf, statusOf } from '../prototype/PrototypeMaterialsWorkspace'
import MaterialPreviewModal from './MaterialPreviewModal'
import { isUnreviewedFinding } from '../lib/reviewDecision'

function numberOf(value) {
  const n = Number(value)
  return Number.isFinite(n) && n >= 0 ? n : 0
}

function statusTone(status) {
  if (status === 'hit') return 'border-[#F5C6C6] bg-[#FFF7F7] text-[#C62828]'
  if (status === 'undecidable') return 'border-[#F0DCB8] bg-[#FFFDF8] text-[#92400E]'
  return 'border-[#CFE8D4] bg-[#F6FCF7] text-[#2E7D32]'
}

function basisLabel(issue) {
  const basis = issue?.basis
  if (!basis || typeof basis !== 'object') return ''
  return basis.docTitle || basis.doc_title || basis.docNo || basis.doc_no || basis.docId || basis.doc_id || ''
}

function basisSourceLabel(item) {
  if (!item || typeof item !== 'object') return '依据库'
  return item.doc_no || item.docNo || item.doc_title || item.docTitle || item.doc_id || item.docId || '依据库'
}

function basisMarkerLabel(item) {
  if (!item || typeof item !== 'object') return ''
  return item.marker || item.clause_id || item.clauseId || ''
}

function basisPageLabel(item) {
  const start = Number(item?.page_start ?? item?.pageStart)
  const end = Number(item?.page_end ?? item?.pageEnd)
  if (!Number.isInteger(start) || start <= 0) return ''
  return Number.isInteger(end) && end > start ? `第 ${start}–${end} 页` : `第 ${start} 页`
}

function basisListLabel(item) {
  const no = Number(item?.list_no ?? item?.listNo)
  if (!Number.isInteger(no) || no <= 0) return ''
  const total = Number(item?.list_total ?? item?.listTotal)
  return Number.isInteger(total) && total > 0 ? `第 ${no} 项 / 共 ${total} 项` : `第 ${no} 项`
}

function referenceLabel(item) {
  if (!item || typeof item !== 'object') return '在线参考依据'
  return item.title || item.doc_title || item.docTitle || item.doc_no || item.docNo || item.reference_id || item.referenceId || '在线参考依据'
}

function BasisCard({ item, index, onOpen, testPrefix = 'review-step-fixed-basis' }) {
  const marker = basisMarkerLabel(item)
  const source = basisSourceLabel(item)
  const listLabel = basisListLabel(item)
  const text = item?.text || ''
  const fullText = text || '依据库未返回条款原文。'
  return (
    <button type="button" data-testid={`${testPrefix}-${index}`} onClick={() => onOpen?.(item)} className="group block w-full rounded border border-[#E4E7EB] border-l-[3px] border-l-[#D8C09A] bg-white px-3 py-2.5 text-left transition-colors hover:border-[#CBD5E1] hover:border-l-[#B8945F] hover:bg-[#FFFDFC]">
      <b className="block truncate text-[12px] font-semibold text-[#16283D]">{source}{marker ? ` ${marker}` : ''}{listLabel ? ` · ${listLabel}` : ''}</b>
      <span className="mt-1 block line-clamp-2 text-[11px] leading-relaxed text-[#587089]">{fullText}</span>
    </button>
  )
}

function SectionToggle({ title, count, open, onToggle, testId }) {
  return (
    <button type="button" data-testid={testId} aria-expanded={open} onClick={onToggle} className="flex min-h-10 w-full items-center justify-between gap-3 border-b border-[#D8E1EB] px-4 py-2.5 text-left hover:bg-[#FBFDFF]">
      <span className="text-[12px] font-semibold text-[#16283D]">{title}（{count} 项）</span>
      <span className="text-[11px] text-[#8B7355]">{open ? '收起' : '展开'}</span>
    </button>
  )
}

function BasisModal({ item, onClose }) {
  const closeRef = useRef(null)
  useEffect(() => {
    closeRef.current?.focus?.()
    const onKeyDown = (event) => {
      if (event.key === 'Escape') onClose?.()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])
  if (!item) return null
  const source = basisSourceLabel(item)
  const marker = basisMarkerLabel(item)
  const page = basisPageLabel(item)
  const listLabel = basisListLabel(item)
  const text = item.text || '依据库未返回条款原文。'
  const contextText = item.context_text || item.contextText || ''
  const quote = (item.text || '').trim()
  const quoteAt = contextText && quote ? contextText.indexOf(quote) : -1
  return (
    <div className="fixed inset-0 z-[90] flex items-center justify-center bg-black/35 p-4" role="dialog" aria-modal="true" aria-label="完整规则依据">
      <section className="flex max-h-[min(82vh,720px)] w-full max-w-3xl flex-col overflow-hidden rounded-[8px] border border-[#D8DEE8] bg-white shadow-2xl">
        <header className="flex shrink-0 items-start gap-3 border-b border-[#E5E7EB] px-4 py-3">
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-[14px] font-semibold text-[#16283D]">{source}{marker ? ` · ${marker}` : ''}{listLabel ? ` · ${listLabel}` : ''}</h2>
            {page && <p className="mt-1 text-[11px] text-[#8B7355]">{page}</p>}
          </div>
          <button ref={closeRef} type="button" onClick={onClose} aria-label="关闭完整规则依据" className="min-h-8 min-w-8 rounded border border-[#E5E7EB] text-lg leading-none text-[#6B7280] hover:border-[#8B7355] hover:text-[#8B7355]">×</button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4 text-[13px] leading-7 text-[#374151]">
          <p className="whitespace-pre-wrap">{text}</p>
          {item.available === false && <p className="mt-3 rounded border border-[#F0DCB8] bg-[#FFFDF8] px-3 py-2 text-[12px] text-[#92400E]">该依据当前不可用，请人工核对原件。</p>}
          {contextText && contextText !== text && (
            <section data-testid="review-step-basis-context" className="mt-4 border-t border-[#E5E7EB] pt-3">
              <p className="text-[11px] font-semibold text-[#8B7355]">所属附件清单（原件摘录，本卡引用处已标注）</p>
              <p className="mt-1 whitespace-pre-wrap text-[12px] leading-6 text-[#64748B]">
                {quoteAt >= 0 ? (
                  <>
                    {contextText.slice(0, quoteAt)}
                    <mark className="rounded bg-[#F7EBD3] px-0.5 text-[#7A4A12]">{quote}</mark>
                    {contextText.slice(quoteAt + quote.length)}
                  </>
                ) : contextText}
              </p>
            </section>
          )}
        </div>
      </section>
    </div>
  )
}

function stepStatus(step, pendingCount) {
  const counts = step?.counts || {}
  const hit = numberOf(counts.hit)
  const undecidable = numberOf(counts.undecidable)
  const miss = numberOf(counts.miss)
  if (pendingCount > 0) return `待处理 ${pendingCount} 项`
  if (hit + undecidable > 0) return '无待处理问题'
  if (miss > 0) return `通过 ${miss} 项`
  return '无本步问题'
}

function evidenceChoicesForRow(row, findings, materials) {
  if (!row.synthetic_checklist || row.subject_id == null) return []
  const finding = findings.find((item) => (row.finding_ids || []).some((id) => String(id) === String(item.id)) && String(item.subject_id) === String(row.subject_id))
  if (!finding) return []
  const choices = []
  for (const ref of Array.isArray(finding.evidence) ? finding.evidence : []) {
    const id = ref.material_id ?? ref.materialId
    const material = materials.find((item) => String(item.id ?? item.material_id) === String(id))
    const mode = ref.location_mode ?? ref.locationMode ?? (ref.anchor?.status === 'precise' ? 'precise' : ref.anchor?.status === 'page_only' ? 'page' : null)
    const page = Number(ref.page)
    if (!material || material.superseded_by_id != null || material.parse_state !== 'done' || !/\.pdf$/i.test(material.file_name || '') || !['page', 'precise'].includes(mode) || !Number.isInteger(page) || page < 1) continue
    if (material.subject_id != null && String(material.subject_id) !== String(row.subject_id)) continue
    if (String(id) !== String(row.material_id) && material.subject_id == null) continue
    if (!choices.some((choice) => String(choice.materialId) === String(id) && choice.page === page)) choices.push({ materialId: id, page, fileName: material.file_name })
  }
  return choices
}

function MaterialRow({ material, onPreview }) {
  const status = statusOf(material)
  const materialId = material.synthetic_checklist ? material.material_id : (material.material_id ?? material.id)
  const [selectedId, setSelectedId] = useState(null)
  const confirmation = material.material_confirmation
  const candidates = confirmation?.candidates || []
  const selectedCandidate = candidates.find((item) => String(item.material_id) === String(selectedId))
    || candidates.find((item) => String(item.material_id) === String(confirmation?.material_id))
    || candidates[0] || null
  const targetId = selectedCandidate?.material_id ?? materialId
  const targetToken = selectedCandidate?.token ?? confirmation?.token
  const subjectMatches = material.source_subject_id == null || material.subject_id == null || String(material.source_subject_id) === String(material.subject_id)
  const canPreview = subjectMatches && (['provided', 'verification'].includes(status.key) || confirmation?.decision === 'nonconforming' || candidates.length > 0) && targetId != null && /\.pdf$/i.test(selectedCandidate?.file_name || material.file_name || '') && material.parse_state === 'done'
  return (
    <li data-testid={`review-material-row-${material.id || material.file_name || material.role_label}`} className="flex min-w-0 items-center gap-3 border-t border-[#EEF2F7] px-3 py-2.5 first:border-t-0">
      <span className="min-w-0 flex-1">
        <b className="block truncate text-[12px] text-[#16283D]" title={material.role_label || material.file_name}>
          {material.role_label || material.file_name || '材料要求'}
        </b>
        <span className="mt-0.5 block truncate text-[10px] text-[#64748B]">
          {[selectedCandidate?.file_name || material.file_name || (status.key === 'missing' ? '册内未见' : '尚未定位文件'), material.subject_name].filter(Boolean).join(' · ')}
        </span>
      </span>
      {candidates.length > 1 && <select aria-label={`选择${material.role_label}候选文件`} value={String(targetId)} onChange={(event) => setSelectedId(event.target.value)} className="max-w-40 rounded border px-1 py-1 text-[10px]">{candidates.map((item) => <option key={item.material_id} value={String(item.material_id)}>{item.file_name}</option>)}</select>}
      {(canPreview || ['pending', 'verification', 'missing'].includes(status.key)) ? <button type="button" onClick={() => onPreview?.(material, canPreview ? targetId : null, canPreview ? targetToken : null)} aria-label={`查看${material.role_label || material.file_name}`} className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] underline ${statusTone(status.key === 'missing' ? 'hit' : ['pending', 'verification'].includes(status.key) ? 'undecidable' : 'miss')}`}>{status.label}</button> : <span className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] ${statusTone(status.key === 'missing' ? 'hit' : ['pending', 'verification'].includes(status.key) ? 'undecidable' : 'miss')}`}>{status.label}</span>}
    </li>
  )
}

export default function ReviewStepDetailPanel({
  step = null,
  detail = null,
  checklistSummary = null,
  missingPlan = null,
  findings = [],
  onSelectFinding,
  onSupplement,
  onConfirmMaterial,
}) {
  if (!step) {
    return <section data-testid="review-step-detail-empty" className="flex min-h-[320px] items-center justify-center rounded-[6px] border border-dashed border-[#D8E1EB] bg-white text-xs text-[#64748B]">从左侧选择一个审查步骤，或从右侧选择一项问题开始</section>
  }

  const findingById = new Map((findings || []).map((finding) => [String(finding.id), finding]))
  const materialRows = step.index === 1 ? checklistRowsOf(detail, checklistSummary, missingPlan) : []
  const checklistRows = materialRows.filter((row) => row.synthetic_checklist)
  const rows = checklistRows.length ? checklistRows : materialRows
  const stepFindingIds = new Set((step.findingIds || []).map((id) => String(id)))
  const issues = (stepFindingIds.size > 0
    ? (findings || []).filter((finding) => stepFindingIds.has(String(finding.id)))
    : (step.issues || []).map((issue) => findingById.get(String(issue.id)) || issue)).filter(isUnreviewedFinding)
  const missingItems = step.missingItems || []
  const stepLabel = step.state === 'completed' ? '已完成' : step.state === 'running' ? '核查中' : step.stateLabel || '待查看'
  const fixedBasis = Array.isArray(step.fixedBasis) ? step.fixedBasis : (Array.isArray(step.fixed_basis) ? step.fixed_basis : [])
  const materialBasis = Array.isArray(step.materialBasis) ? step.materialBasis : (Array.isArray(step.material_basis) ? step.material_basis : [])
  const references = Array.isArray(step.references) ? step.references : []
  const [issuesOpen, setIssuesOpen] = useState(true)
  const [materialsOpen, setMaterialsOpen] = useState(true)
  const [basisOpen, setBasisOpen] = useState(true)
  const [activeBasis, setActiveBasis] = useState(null)
  const [previewMaterial, setPreviewMaterial] = useState(null)
  const openMaterial = (row, targetId = row.material_id ?? row.id, targetToken = null) => {
    const materials = Array.isArray(detail?.materials) ? detail.materials : []
    const allChoices = evidenceChoicesForRow(row, findings || [], materials)
    const resolvedTargetId = targetId ?? (!row.material_confirmation?.candidates?.length ? allChoices[0]?.materialId : null) ?? null
    // 确认前只能预览所选候选；切换候选由材料行选择器统一完成。
    const choices = resolvedTargetId == null ? [] : row.material_confirmation?.candidates?.length
      ? allChoices.filter((choice) => String(choice.materialId) === String(resolvedTargetId)) : allChoices
    const primaryPage = row.synthetic_checklist && ['page', 'precise'].includes(row.checklist_location_mode) ? row.checklist_page : null
    const page = String(resolvedTargetId) === String(row.material_id) ? primaryPage : choices.find((choice) => String(choice.materialId) === String(resolvedTargetId))?.page ?? null
    const finding = (findings || []).find((item) => (row.finding_ids || []).some((id) => String(id) === String(item.id)) && String(item.subject_id) === String(row.subject_id))
    const canConfirm = targetToken && row.finding_ids?.length === 1 && typeof onConfirmMaterial === 'function'
    const reviewDetails = ['pending', 'verification', 'missing'].includes(statusOf(row).key) || row.material_confirmation?.decision === 'nonconforming' || canConfirm ? {
      reason: finding?.message || (targetId != null ? '已关联材料，内容是否符合要求仍需核对原件。' : '尚未定位到可核对的材料，请核对或补充材料。'),
      onSupplement: (statusOf(row).key === 'missing' || row.material_confirmation?.decision === 'nonconforming') && row.role && typeof onSupplement === 'function' ? () => { setPreviewMaterial(null); onSupplement(row) } : null,
      onConfirm: canConfirm ? (decision) => {
        const args = [row.finding_ids[0], decision, targetToken, row.material_confirmation?.confirmation_id ?? null, targetId]
        onConfirmMaterial(...args)
        setPreviewMaterial(null)
      } : null,
    } : null
    setPreviewMaterial({ materialId: resolvedTargetId, page, choices, reviewDetails })
  }

  return (
    <section data-testid="review-step-detail" className="min-h-0 flex-1 overflow-y-auto rounded-[6px] border border-[#D8E1EB] bg-white">
      <header className="border-b border-[#D8E1EB] px-4 py-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <p className="text-[11px] text-[#64748B]">第 {step.index} 步</p>
            <h2 className="mt-0.5 text-[16px] font-semibold text-[#16283D]">{step.title}</h2>
          </div>
          <div className="flex items-center gap-2">
            <span data-testid="review-step-state" className="rounded-full border border-[#D8E1EB] bg-[#F8FAFC] px-2 py-1 text-[11px] text-[#385A77]">{stepLabel}</span>
            <span data-testid="review-step-status" className="rounded-full border border-[#D8E1EB] bg-[#F8FAFC] px-2 py-1 text-[11px] text-[#385A77]">{stepStatus(step, issues.length)}</span>
          </div>
        </div>
        <div className="mt-2 flex flex-wrap gap-3 text-[11px] text-[#64748B]">
          <span>共 {numberOf(step.counts?.total)} 项</span>
          <span className="text-[#2E7D32]">通过 {numberOf(step.counts?.miss)}</span>
          <span className="text-[#C62828]">不符合 {numberOf(step.counts?.hit)}</span>
          <span className="text-[#92400E]">无法核实 {numberOf(step.counts?.undecidable)}</span>
        </div>
      </header>

      {step.index === 1 && (
        <>
          <section className="border-b border-[#D8E1EB] px-4 py-3">
            <h3 className="text-[12px] font-semibold text-[#16283D]">册内定位结果</h3>
            <p className="mt-1 text-[11px] leading-relaxed text-[#64748B]">按分包类型逐项核验附件要求，缺件只在本步骤展示。</p>
            <div data-testid="review-material-summary" className="mt-2 grid gap-2 sm:grid-cols-3">
              <div className="rounded border border-[#E8EEF4] bg-[#FAFBFC] px-2.5 py-2 text-[11px]">已定位 <b className="text-[#2E7D32]">{rows.filter((row) => ['provided', 'verification'].includes(statusOf(row).key) && row.file_name).length}</b></div>
              <div className="rounded border border-[#E8EEF4] bg-[#FAFBFC] px-2.5 py-2 text-[11px]">册内未见 <b className="text-[#C62828]">{rows.filter((row) => statusOf(row).key === 'missing').length}</b></div>
              <div className="rounded border border-[#E8EEF4] bg-[#FAFBFC] px-2.5 py-2 text-[11px]">待核实/重核查 <b className="text-[#92400E]">{rows.filter((row) => statusOf(row).key === 'pending' || (statusOf(row).key === 'verification' && !row.file_name)).length}</b></div>
            </div>
          </section>
          <section className="border-t border-[#D8E1EB]">
            <SectionToggle title="材料要求" count={rows.length} open={materialsOpen} onToggle={() => setMaterialsOpen((value) => !value)} testId="review-step-materials-toggle" />
            {materialsOpen && <div className="px-4 py-3">
              {missingItems.length > 0 && <p className="mb-2 text-[11px] text-[#92400E]">缺件 {missingItems.length} 项</p>}
              {rows.length > 0 ? <ul className="overflow-hidden rounded border border-[#E8EEF4]">{rows.map((row, index) => <MaterialRow key={row.id || row.file_name || index} material={row} onPreview={openMaterial} />)}</ul> : <p className="rounded border border-dashed border-[#D8E1EB] px-3 py-4 text-center text-[11px] text-[#64748B]">后端尚未返回逐项材料要求。</p>}
            </div>}
          </section>
        </>
      )}

      {(
        <section>
          <SectionToggle title="待处理问题" count={issues.length} open={issuesOpen} onToggle={() => setIssuesOpen((value) => !value)} testId="review-step-issues-toggle" />
          {issuesOpen && <div className="px-4 py-3">{issues.length > 0 ? <ul data-testid="review-step-issues" className="space-y-2">{issues.map((issue) => { const basisText = basisLabel(issue); return <li key={issue.id} data-testid={`review-step-issue-${issue.id}`} className="rounded border border-[#E8EEF4] px-3 py-2.5"><button type="button" onClick={() => onSelectFinding?.(issue.id)} className="w-full text-left"><div className="flex items-start justify-between gap-2"><b className="text-[12px] leading-relaxed text-[#16283D]">{issue.message || issue.title || '未命名问题'}</b><span className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] ${statusTone(issue.status)}`}>{issue.status === 'undecidable' ? '无法核实' : issue.status === 'hit' ? '不符合' : '通过'}</span></div>{basisText ? <p className="mt-1 text-[10px] text-[#64748B]">规则依据：{basisText}</p> : <p className="mt-1 text-[10px] text-[#64748B]">规则依据待回读</p>}{issue.quote ? <p className="mt-1 line-clamp-2 text-[10px] text-[#64748B]">材料摘录：{issue.quote}</p> : <p className="mt-1 text-[10px] text-[#92400E]">暂未取得可核实的材料摘录</p>}</button></li> })}</ul> : <p data-testid="review-step-issues-empty" className="rounded border border-dashed border-[#D8E1EB] px-3 py-4 text-center text-[11px] text-[#64748B]">本步骤暂无需要人工查看的问题。</p>}</div>}
        </section>
      )}

      <section data-testid="review-step-basis" className="border-t border-[#D8E1EB] bg-[#FCFDFE]">
        <SectionToggle title="规则依据" count={fixedBasis.length + materialBasis.length + references.length} open={basisOpen} onToggle={() => setBasisOpen((value) => !value)} testId="review-step-basis-toggle" />
        {basisOpen && <div className="px-4 py-3">
          <p className="text-[10px] text-[#64748B]">材料要求依据 {materialBasis.length} 条 · 判定规则依据 {fixedBasis.length} 条 · 在线参考 {references.length} 条</p>
          {materialBasis.length > 0 && <div data-testid="review-step-material-basis-items" className="mt-2 space-y-1.5"><p className="text-[10px] font-medium text-[#587089]">材料要求依据</p>{materialBasis.map((item, index) => <BasisCard key={`${item.clause_id || item.clauseId || index}-${item.list_no ?? item.listNo ?? ''}`} item={item} index={index} testPrefix="review-step-material-basis" onOpen={setActiveBasis} />)}</div>}
          {fixedBasis.length > 0 ? <div data-testid="review-step-fixed-basis" className="mt-3 space-y-1.5"><p className="text-[10px] font-medium text-[#587089]">判定规则依据</p>{fixedBasis.map((item, index) => <BasisCard key={`${item.clause_id || item.clauseId || index}-${item.list_no ?? item.listNo ?? ''}`} item={item} index={index} onOpen={setActiveBasis} />)}</div> : <p data-testid="review-step-fixed-basis-empty" className="mt-2 rounded border border-dashed border-[#D8E1EB] px-2.5 py-2 text-[11px] text-[#64748B]">本步骤未保存判定规则依据。</p>}
          {references.length > 0 && <div data-testid="review-step-references" className="mt-3 space-y-1.5"><p className="text-[10px] font-medium text-[#587089]">在线参考</p>{references.map((item, index) => <BasisCard key={item.reference_id || item.referenceId || index} item={{ ...item, doc_title: referenceLabel(item), text: item.quote || '', page_start: item.physical_page ?? item.page_start, page_end: item.physical_page_end ?? item.page_end }} index={index} testPrefix="review-step-online-basis" onOpen={setActiveBasis} />)}</div>}
        </div>}
      </section>
      <BasisModal item={activeBasis} onClose={() => setActiveBasis(null)} />
      {previewMaterial && <MaterialPreviewModal materials={(detail?.materials || []).filter((material) => String(material.id ?? material.material_id) === String(previewMaterial.materialId) || previewMaterial.choices.some((choice) => String(choice.materialId) === String(material.id ?? material.material_id)))} activeMaterialId={previewMaterial.materialId} page={previewMaterial.page} evidenceChoices={previewMaterial.choices} reviewDetails={previewMaterial.reviewDetails} onSelectEvidence={(choice) => setPreviewMaterial((current) => ({ ...current, materialId: choice.materialId, page: choice.page, reviewDetails: current.materialId === choice.materialId ? current.reviewDetails : null }))} onSelect={(id) => setPreviewMaterial((current) => ({ ...current, materialId: id, page: null, reviewDetails: current.materialId === id ? current.reviewDetails : null }))} onClose={() => setPreviewMaterial(null)} />}
    </section>
  )
}
