import { ArrowLeft, Download, FileText } from 'lucide-react'
import ReferenceSnapshot from './ReferenceSnapshot'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { countOf, statusLabelOf } from '../lib/statusDisplay'

// 报告页：draft / confirmed 只是报告稿别，不是人工处置门禁。
// 只有尚未形成可展示核查结果时才显示技术覆盖提示；运行中、部分分组或失败结果由
// coverage / limitations 独立披露，不把“未人工处理”渲染成禁止生成报告。

const DEFAULT_COVERAGE_REASON = '本轮尚未形成可展示的核查结果，请先开始核查。'
const EXPORT_LABELS = { draft: '导出 AI 初稿 Word', confirmed: '导出确认稿 Word' }

function evidenceLabel(item) {
  const count = (item.evidence_list || []).length || (item.evidence ? 1 : 0)
  return count > 0 ? `查看材料证据（${count} 条）` : '暂无可展开材料证据'
}

function issueItems(sections) {
  // 兜底条款（open_term）只登记、不构成结论：放进「不符合项清单」会让人去补一份并不存在
  // 的材料（BS-P1-11）。它们由 openTermItems 单独呈现。
  return sections.flatMap((section) => (section.items || [])
    .filter((item) => !item.open_term && (item.status === 'hit' || item.status === 'undecidable'))
    .map((item) => ({ ...item, category: item.category || section.title })))
}

function openTermItems(sections) {
  return sections.flatMap((section) => (section.items || [])
    .filter((item) => item.open_term)
    .map((item) => ({ ...item, category: item.category || section.title })))
}

function blockingReason(blocking, fallback = DEFAULT_COVERAGE_REASON) {
  return (typeof blocking?.message === 'string' && blocking.message.trim()) || fallback
}

function dispositionCode(item) {
  return item?.human_disposition?.code || item?.review?.decision || 'unprocessed'
}

function summaryItems(summary, key) {
  const rows = summary?.sections?.[key]
  return Array.isArray(rows) ? rows : []
}

function legacyReportSummaries(issues) {
  const groups = new Map()
  const notes = []
  for (const issue of issues || []) {
    const code = issue.human_disposition?.code
    if (code === 'ignored') continue
    const category = issue.category || '其他核查'
    const label = issue.label || '核查事项'
    if (code === 'accepted') {
      notes.push({ category, label, note: issue.human_disposition?.note || '未填写补充说明' })
      continue
    }
    const explicitMissing = issue.status === 'hit' && issue.material_missing === true
    const status = explicitMissing ? '明确缺少材料' : issue.status === 'hit' ? '需关注' : '待核实'
    const missingLabels = explicitMissing && Array.isArray(issue.missing?.labels) ? issue.missing.labels.filter(Boolean).map(String) : []
    const themes = missingLabels.length ? missingLabels : [explicitMissing ? label : (issue.summary_theme || label)]
    const key = JSON.stringify([category, status])
    if (!groups.has(key)) groups.set(key, { category, status, labels: [] })
    const group = groups.get(key)
    for (const theme of themes) if (!group.labels.includes(theme)) group.labels.push(theme)
  }
  return { pending: [...groups.values()].map(({ category, status, labels }) => ({ category, status, text: `${status}：${labels.join('、')}。` })), notes }
}

function reportDisplaySections(document) {
  const sections = (document.sections || []).map((section) => ({ ...section,
    displayPoints: (section.key_points ?? section.fields ?? []).map((point) => ({ ...point })) }))
  if (document.schema_version !== 'bs.business-report-document.v2') {
    const legacy = legacyReportSummaries(document.issues)
    const sectionFor = (category) => {
      let section = sections.find((entry) => entry.title === category)
      if (!section) { section = { title: category, displayPoints: [] }; sections.push(section) }
      return section
    }
    for (const row of document.pending_summaries ?? legacy.pending) {
      sectionFor(row.category || '其他核查').displayPoints.push({ label: '其他核查事项', summary: pendingSummaryText(row) })
    }
    for (const row of document.human_notes ?? legacy.notes) {
      const note = String(row.note || '').trim()
      if (note && note !== '未填写补充说明') sectionFor(row.category || '其他核查').displayPoints.push({ label: row.label || '人工补充', summary: `人工补充：${note}` })
    }
  }
  const limitations = document.conclusion?.limitations || []
  if (limitations.length && !sections.some((section) => section.displayPoints.length)) {
    sections.push({ title: '材料与核查范围', displayPoints: [{ label: '材料与核查范围', summary: '' }] })
  }
  const points = new Map(sections.flatMap((section) => section.displayPoints.map((point) => [point.label, point])))
  for (const value of limitations) {
    const text = String(value)
    let target = '报审资料'
    if (['ocr', '未读', '读取', '识别质量', '文字质量', '可读正文', '存疑页'].some((word) => text.toLowerCase().includes(word))) target = '报审资料'
    else if (text.includes('AI') || text.includes('模型')) target = '报审资料'
    else if (text.includes('信用') || text.includes('事故')) target = '内部信用与事故记录'
    else if (['证照', '证件', '真实性', '官方'].some((word) => text.includes(word))) target = '证照真实性'
    else if (text.includes('金额') || text.includes('工程量')) target = '合同及清单'
    else if (['合同', '依据', '分包范围'].some((word) => text.includes(word))) target = '负面清单'
    const point = points.get(target) || sections.flatMap((section) => section.displayPoints)[0]
    if (point && !String(point.summary || '').includes(text)) point.summary = [point.summary, text].filter(Boolean).join(' ')
  }
  return sections
}

function pendingSummaryText(row) {
  const text = String(row.text || '')
  for (const [prefix, suffix] of [['待核实：', '需要核实。'], ['需关注：', '需要关注。'], ['明确缺少材料：', '材料缺失。']]) {
    if (text.startsWith(prefix)) return text.slice(prefix.length).replace(/。+$/, '') + suffix
  }
  return text
}

function BusinessDocument({ document, basicInformation, onOpenSource, onShowBasicInformation }) {
  if (!document) return null
  const displaySections = reportDisplaySections(document)
  const sourceKeys = { '工程名称': 'project_name', '标段': 'contract_section', '施工总承包单位': 'general_contractor', '建设单位（业主）': 'employer', '分包单位': 'subcontractor', '拟分包范围': 'subcontract_scope', '拟分包金额': 'subcontract_amount' }
  const needsAttention = Object.entries(document.basic_information || {}).some(([label, value]) =>
    sourceKeys[label] && (value == null || value === '' || ['conflict', 'unresolved'].includes(document.basic_information_status?.[label])))
  return <article data-testid="business-document" className="space-y-5 rounded border border-[#E5E5E5] bg-white px-5 py-5 text-sm leading-7 text-[#374151]">
    <section><h2 className="text-base font-semibold text-[#1A1A1A]">一、项目与分包基本信息</h2>
      {needsAttention && onShowBasicInformation && <p className="mt-2 text-xs text-[#92400E]">部分基本信息待核实。 <button type="button" className="underline" onClick={onShowBasicInformation}>查看项目基本信息</button></p>}
      <dl className="mt-3 grid gap-x-6 gap-y-2 sm:grid-cols-2">{Object.entries(document.basic_information || {}).map(([label, value]) => {
        const sources = (basicInformation?.[sourceKeys[label]]?.evidence || []).filter((row) => row?.material_id != null && Number.isInteger(row?.page) && row.page > 0)
        const status = document.basic_information_status?.[label]
        const display = value == null || value === '' ? (status === 'conflict' ? '待核实（材料冲突）' : '待核实') : `${value}${['conflict', 'unresolved'].includes(status) ? '（待核实）' : ''}`
        return <div key={label} className="flex min-w-0 flex-wrap gap-x-2 border-b border-[#F3F4F6] py-1"><dt className="shrink-0 text-[#6B7280]">{label}</dt><dd className="min-w-0 break-words">{display}</dd>{onOpenSource && sources.map((evidence, index) => <button key={`${evidence.material_id}-${evidence.page}-${index}`} type="button" className="text-left text-xs text-[#315d79] underline" onClick={() => onOpenSource(evidence.material_id, evidence.page)}>{sources.length === 1 ? '查看原文' : `查看原文：${evidence.file_name || '材料'} 第${evidence.page}页${evidence.value ? `（${evidence.value}）` : ''}`}</button>)}</div>
      })}</dl>
    </section>
    <section><h2 className="text-base font-semibold text-[#1A1A1A]">二、核查摘要</h2>
      {displaySections.map((section, index) => <section key={section.title} className="mt-4"><h3 className="font-semibold">{index + 1}. {section.title}</h3><div className="overflow-x-auto"><table className="mt-2 w-full border-collapse text-left"><thead><tr><th className="border p-2">审查要点及主要依据</th><th className="border p-2">核查情况</th><th className="border p-2">判定</th></tr></thead><tbody>{section.displayPoints.map((field, pointIndex) => <tr key={`${field.label}-${pointIndex}`}><td className="border p-2 align-top"><div>{field.label}</div><div className="text-xs text-[#6B7280]">主要依据：{field.basis?.length ? field.basis.join('；') : '暂无可展示依据'}</div></td><td className="border p-2 align-top">{field.summary}</td><td className="border p-2 align-top">{field.verdict || '待核实'}</td></tr>)}</tbody></table></div></section>)}
    </section>
  </article>
}

function QualificationSummaryCard({ summary, prototypeMode = false }) {
  if (!summary || typeof summary !== 'object') return null
  const contractor = summary.general_contractor || {}
  const counts = summary.counts || {}
  const groups = [
    ['non_compliant', '不符合'],
    ['missing', '缺件'],
    ['undecidable', '无法核实'],
  ]
  const subjects = Array.isArray(summary.subjects) ? summary.subjects : []
  const limitations = Array.isArray(summary.limitations) ? summary.limitations : []
  return (
    <section data-testid="qualification-summary" className={`${prototypeMode ? '' : 'mb-4'} rounded border border-[#DCE5EE] bg-[#F8FAFC] px-4 py-3`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-[#1A1A1A]">资质合规摘要</h2>
        {summary.publication?.state && <span data-testid="qualification-summary-publication" className="rounded border border-[#E5E5E5] px-1.5 py-0.5 text-[10px] text-[#6B7280]">{summary.publication.state}</span>}
      </div>
      <dl className="mt-3 grid gap-2 text-xs text-[#4B5563] sm:grid-cols-3">
        <div>
          <dt className="text-[#9CA3AF]">承包单位</dt>
          <dd data-testid="qualification-summary-contractor" className="mt-0.5 font-medium">{contractor.company || contractor.candidate_company || contractor.label || '承包单位未识别，待核实'}</dd>
        </div>
        <div>
          <dt className="text-[#9CA3AF]">主体</dt>
          <dd data-testid="qualification-summary-subjects" className="mt-0.5 font-medium">{subjects.map((item) => item.name).filter(Boolean).join('、') || '未识别主体'}</dd>
        </div>
        <div>
          <dt className="text-[#9CA3AF]">摘要计数</dt>
          <dd data-testid="qualification-summary-counts" className="mt-0.5 font-medium">不符合 {counts.hit ?? 0} · 缺件 {summaryItems(summary, 'missing').length} · 待核实 {counts.undecidable ?? 0}</dd>
        </div>
      </dl>
      <div className="mt-3 grid gap-3 md:grid-cols-3">
        {groups.map(([key, label]) => {
          const items = summaryItems(summary, key)
          return (
            <section key={key} data-testid={`qualification-summary-${key}`} className="rounded border border-[#E5E5E5] bg-white px-3 py-2">
              <h3 className="text-xs font-medium text-[#6B7280]">{label}（{items.length}）</h3>
              {items.length === 0 ? <p className="mt-1 text-[11px] text-[#9CA3AF]">暂无</p> : <ul className="mt-1 space-y-1 text-[11px] leading-relaxed text-[#4B5563]">
                {items.slice(0, 5).map((item, index) => <li key={`${item.item_id || key}-${index}`}>
                  <span>{item.label || item.item_id || '核查项'}</span>
                  {item.evidence?.page ? <span className="ml-1 text-[#8B7355]">· 第 {item.evidence.page} 页</span> : null}
                </li>)}
                {items.length > 5 && <li className="text-[#9CA3AF]">其余 {items.length - 5} 项见机器审计明细</li>}
              </ul>}
            </section>
          )
        })}
      </div>
      {limitations.length > 0 && <div data-testid="qualification-summary-limitations" className="mt-3 rounded border border-[#F0DCB8] bg-[#FDFAF4] px-3 py-2 text-[11px] leading-relaxed text-[#92400E]">
        <p className="font-medium">限制与人工核对提示</p>
        <ul className="mt-1 list-disc space-y-1 pl-4">{limitations.slice(0, 5).map((item, index) => <li key={index}>{item}</li>)}</ul>
      </div>}
    </section>
  )
}

function BusinessReportStructureCard({ businessReport }) {
  if (!businessReport || typeof businessReport !== 'object') return null
  const sections = Array.isArray(businessReport.sections) ? businessReport.sections : []
  const attachments = Array.isArray(businessReport.attachment_register) ? businessReport.attachment_register : []
  const statusClass = {
    available: 'border-[#DDEBDD] bg-[#F8FCF8] text-[#2E7D32]',
    partial: 'border-[#F0DCB8] bg-[#FDFAF4] text-[#92400E]',
    not_covered: 'border-[#E5E5E5] bg-[#FAFAFA] text-[#6B7280]',
  }
  return (
    <section data-testid="business-report-structure" className="mb-4 rounded border border-[#DCE5EE] bg-white px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-[#1A1A1A]">业务报告结构</h2>
        <span data-testid="business-report-status" className={`rounded border px-1.5 py-0.5 text-[10px] ${statusClass[businessReport.overall_status] || statusClass.partial}`}>
          {businessReport.overall_status_label || '部分覆盖'}
        </span>
      </div>
      <p className="mt-1 text-[11px] leading-relaxed text-[#6B7280]">{businessReport.notice}</p>
      <div className="mt-3 grid gap-2 md:grid-cols-2">
        {sections.map((section) => (
          <section key={section.code} data-testid={`business-report-module-${section.code}`} className="rounded border border-[#EFE6D8] px-3 py-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-xs font-medium text-[#374151]">{section.title}</h3>
              <span className={`rounded border px-1.5 py-0.5 text-[10px] ${statusClass[section.status] || statusClass.partial}`}>{section.status_label}</span>
            </div>
            <p className="mt-1 text-[11px] leading-relaxed text-[#4B5563]">{section.conclusion}</p>
            {section.limitations?.length > 0 && <p className="mt-1 text-[11px] leading-relaxed text-[#92400E]">{section.limitations[0]}</p>}
          </section>
        ))}
      </div>
      {attachments.length > 0 && <details className="mt-3 rounded border border-[#EFE6D8] px-3 py-2">
        <summary className="cursor-pointer text-xs font-medium text-[#6B7280]">附件核对清单（{attachments.length} 项）</summary>
        <ul className="mt-2 space-y-1 text-[11px] leading-relaxed text-[#4B5563]">
          {attachments.map((item) => <li key={item.code}><span className="font-medium">{item.label}</span>：{item.state === 'provided' ? '已归入材料台账' : item.state === 'generated' ? '由系统过程记录生成' : '尚未明确归类'}；{item.note}</li>)}
        </ul>
      </details>}
      {businessReport.process_trace && <p data-testid="business-report-process-trace" className="mt-3 text-[11px] leading-relaxed text-[#6B7280]">AI 核验过程：规则版本 {businessReport.process_trace.ruleset_version || '未记录'}；运行链路 {businessReport.process_trace.run_lineage_available ? '已记录' : '未记录'}；人工复核记录 {businessReport.process_trace.human_review_records || 0} 条。</p>}
    </section>
  )
}

/** 技术覆盖提示：列缺项（未完成组 / 进行中组），不把 409 说成网络错误。 */
function ReportCoverageBlockingPanel({ blocking, onRefresh, refreshing }) {
  const missing = Array.isArray(blocking?.missingGroups) ? blocking.missingGroups : []
  const pending = Array.isArray(blocking?.pendingGroups) ? blocking.pendingGroups : []
  return (
    <section data-testid="draft-blocking-panel" data-coverage-blocking="true" role="alert" className="rounded border border-[#F0DCB8] bg-[#FFFDF8] px-4 py-3">
      <h2 className="text-sm font-semibold text-[#92400E]">报告暂不可用：尚未形成可展示的核查结果</h2>
      <p data-testid="draft-blocking-message" className="mt-1 text-xs leading-relaxed text-[#6B7280]">
        {blockingReason(blocking)}
      </p>
      {missing.length > 0 && (
        <p data-testid="draft-blocking-missing" className="mt-2 text-xs leading-relaxed text-[#4B5563]">
          未完成组：{missing.join('、')}
        </p>
      )}
      {pending.length > 0 && (
        <p data-testid="draft-blocking-pending" className="mt-1 text-xs leading-relaxed text-[#4B5563]">
          进行中组：{pending.join('、')}
        </p>
      )}
      <p className="mt-2 text-[11px] leading-relaxed text-[#6B7280]">开始核查后，部分结果也会先以中间报告展示；未人工处理的问题仍会逐项写入报告。</p>
      <button type="button" onClick={onRefresh} disabled={refreshing} className="mt-2 min-h-8 rounded border border-[#8B7355] bg-white px-2.5 text-[11px] text-[#8B7355] hover:bg-[#F7F1E9] disabled:opacity-60">
        {refreshing ? '正在重试…' : '重新读取报告'}
      </button>
    </section>
  )
}

export default function ReportView({
  report,
  onBack,
  onExport,
  exporting,
  stage = 'draft',
  onStageChange,
  stageBusy = false,
  blockingByStage = {},
  onSelectBlocking,
  onRefresh,
  refreshing = false,
  refreshDisabled = false,
  refreshMessage = '',
  onDownloadReference,
  onOpenSource,
  onShowBasicInformation,
  onComplete,
  completing = false,
  completeDisabled = false,
  completed = false,
  started = true,
  error = '',
  pendingProblems = 0,
  prototypeMode = false,
  opinion = null,
  opinionMarkdown = null,
  opinionLoading = false,
  opinionError = '',
  onGenerateOpinion,
  onExportOpinion,
  opinionExporting = null,
}) {
  const blocking = blockingByStage?.[stage] || null
  const draftBlocked = Boolean(blockingByStage?.draft)
  const confirmedBlocked = Boolean(blockingByStage?.confirmed)
  // 材料缺口由材料清单承载，不计入人工问题处置摘要。
  const allIssues = issueItems(report?.sections || [])
  const issues = allIssues.filter((item) => !prototypeMode || !item.material_only)
  const openTerms = openTermItems(report?.sections || [])
  const ignoredItems = Array.isArray(report?.ignored_items) ? report.ignored_items : []
  const materialRows = Array.isArray(report?.materials) ? report.materials : []
  const checklistSummary = report?.checklist_summary || {}
  const counts = report?.summary?.counts
  const passed = countOf(counts, 'miss')
  const coverage = report?.coverage || null
  const coverageLimitations = Array.isArray(report?.limitations)
    ? report.limitations
    : (Array.isArray(coverage?.limitations) ? coverage.limitations : [])
  const runLineage = report?.run_lineage && typeof report.run_lineage === 'object'
    ? report.run_lineage
    : null
  const qualificationSummary = report?.qualification_summary && typeof report.qualification_summary === 'object'
    ? report.qualification_summary
    : null
  const completeTitle = completed
    ? '本轮已收口，报告与结论为只读。'
    : '完成复核后本轮结果收口并生成历史版本；此后报告与结论为只读。'

  return (
    <div className={`flex h-full min-h-0 flex-col ${prototypeMode ? 'p-0' : 'px-8 py-6'}`}>
      {!prototypeMode && <header className="flex shrink-0 flex-wrap items-center gap-3 border-b border-[#E5E5E5] pb-4">
        {!prototypeMode && <button type="button" onClick={onBack} className="inline-flex min-h-10 items-center gap-1 rounded border border-[#E5E5E5] px-3 text-xs text-[#6B7280] hover:bg-[#F7F1E9]">
          <ArrowLeft className="h-3.5 w-3.5" /> 返回核查
        </button>}
        <div className="min-w-[14rem] flex-1">
          <h1 className="text-base font-semibold text-[#1A1A1A]">核查报告</h1>
          {report?.stage_label && <p data-testid="report-stage-label" className="mt-1 text-xs font-medium text-[#8B7355]">{report.stage_label}</p>}
          <p className="mt-1 text-xs text-[#6B7280]">{report?.meta?.title || '分包合规核查'} · {report?.meta?.contract_section || '合同段未填写'} · {report?.meta?.baseline_date || '核查日期未填写'}</p>
          {refreshMessage && <p data-testid="report-refresh-status" className="mt-1 text-[11px] text-[#2E7D32]">{refreshMessage}</p>}
        </div>
        {/* 稿别切换：AI 初稿 / 确认稿。只切读取口径，不在前端判定人工处置。 */}
        {!prototypeMode && <div role="group" aria-label="报告稿别" className="flex shrink-0 items-center gap-1 rounded border border-[#E5E5E5] bg-white p-0.5">
          <button
            type="button"
            data-testid="report-stage-draft"
            aria-pressed={stage === 'draft'}
            onClick={() => onStageChange?.('draft')}
            className={`min-h-9 rounded px-2.5 text-xs ${stage === 'draft' ? 'bg-[#F7F1E9] font-medium text-[#5B4A2F]' : 'text-[#6B7280] hover:bg-[#FAFAF7]'}`}
          >
            AI 初稿
          </button>
          <button
            type="button"
            data-testid="report-stage-confirmed"
            aria-pressed={stage === 'confirmed'}
            onClick={() => onStageChange?.('confirmed')}
            className={`min-h-9 rounded px-2.5 text-xs ${stage === 'confirmed' ? 'bg-[#F7F1E9] font-medium text-[#5B4A2F]' : 'text-[#6B7280] hover:bg-[#FAFAF7]'}`}
          >
            确认稿
          </button>
        </div>}
        {onRefresh && (
          <button type="button" onClick={onRefresh} disabled={refreshing} className="inline-flex min-h-10 items-center gap-1 rounded border border-[#E5E5E5] bg-white px-3 text-xs text-[#6B7280] hover:border-[#8B7355] hover:bg-[#F7F1E9] disabled:opacity-60">
            <FileText className="h-3.5 w-3.5" /> {refreshing ? '刷新中…' : '刷新报告'}
          </button>
        )}
        {onComplete && (
          <button type="button" onClick={onComplete} disabled={completing || completeDisabled} title={pendingProblems > 0 ? `还有 ${pendingProblems} 项问题待处理；不影响初稿查看和下载。` : completeTitle} className="min-h-10 rounded border border-[#E5E5E5] bg-white px-3 text-xs text-[#6B7280] disabled:cursor-not-allowed disabled:opacity-50">
            {completing ? '正在完成复核…' : '完成复核（收口，生成历史版本）'}
          </button>
        )}
        <div role="group" aria-label="报告导出" className="flex flex-wrap gap-2">
          {!prototypeMode && <>
          <button
            type="button"
            data-testid="report-export-draft"
            disabled={exporting != null || draftBlocked}
            title={draftBlocked ? blockingReason(blockingByStage?.draft) : EXPORT_LABELS.draft}
            onClick={() => onExport?.('draft')}
            className="inline-flex min-h-10 items-center gap-1 rounded border border-[#E5E5E5] bg-white px-3 text-xs text-[#6B7280] hover:border-[#8B7355] hover:bg-[#F7F1E9] disabled:cursor-not-allowed disabled:opacity-60"
          >
            <Download className="h-3.5 w-3.5" /> {exporting === 'draft' ? '导出中…' : EXPORT_LABELS.draft}
          </button>
          <button
            type="button"
            data-testid="report-export-confirmed"
            disabled={exporting != null || confirmedBlocked}
            title={confirmedBlocked ? blockingReason(blockingByStage?.confirmed) : EXPORT_LABELS.confirmed}
            onClick={() => onExport?.('confirmed')}
            className="inline-flex min-h-10 items-center gap-1 rounded border border-[#E5E5E5] bg-white px-3 text-xs text-[#6B7280] hover:border-[#8B7355] hover:bg-[#F7F1E9] disabled:cursor-not-allowed disabled:opacity-60"
          >
            <Download className="h-3.5 w-3.5" /> {exporting === 'confirmed' ? '导出中…' : EXPORT_LABELS.confirmed}
          </button>
          </>}
        </div>
      </header>}
      <main className={`min-h-0 flex-1 ${prototypeMode ? 'overflow-visible py-0' : 'overflow-y-auto py-4'}`}>
        {completed && !prototypeMode && <p data-testid="report-readonly-note" className="mb-3 text-xs leading-relaxed text-[#6B7280]">本轮已收口，报告与结论为只读；历史版本仅供只读回看，未提供冻结接口。</p>}
        {blocking ? (
          <ReportCoverageBlockingPanel blocking={blocking} onRefresh={onRefresh} refreshing={refreshing} />
        ) : !report ? (
          <div className="flex min-h-[300px] flex-col items-center justify-center gap-3 rounded border border-[#EFE6D8] bg-white text-center">
            <p data-testid="report-prerequisite" className="max-w-md text-sm leading-relaxed text-[#6B7280]">
              {!started
                ? '请先完成材料上传并开始核查。'
                : stageBusy || refreshing
                  ? '正在读取报告中…'
                  : (error || '本轮尚未形成可展示的核查结果；开始核查后可先查看中间报告。')}
            </p>
            <button type="button" onClick={onRefresh} disabled={!started || refreshing || refreshDisabled || !onRefresh} className="min-h-10 rounded-[4px] bg-[#8B7355] px-4 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-50">
              {refreshing ? '正在读取报告…' : (stage === 'confirmed' ? '读取确认稿' : '查看 AI 初稿')}
            </button>
          </div>
        ) : (
          <>
            {!prototypeMode && report.draft_notice && <p data-testid="report-draft-notice" className="mb-4 rounded border border-[#F0DCB8] bg-[#FDFAF4] px-4 py-3 text-xs leading-relaxed text-[#92400E]">{report.draft_notice}</p>}
            {!prototypeMode && (report.scope_statements || []).length > 0 && <div data-testid="report-scope-statements" className="mb-4 space-y-1 text-xs leading-relaxed text-[#6B7280]">{report.scope_statements.map((statement, index) => <p key={index}>{statement}</p>)}</div>}
            {prototypeMode && (
              <section data-testid="report-formal-opinion" className="mb-4 rounded border border-[#E5E5E5] bg-white px-4 py-3">
                <div data-testid="report-generate-gate" className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <h2 className="text-sm font-semibold text-[#1A1A1A]">正式意见书</h2>
                  </div>
                  <div className="flex flex-wrap items-center justify-end gap-2">
                    {!opinion && <button type="button" data-testid="generate-formal-opinion" onClick={onGenerateOpinion} disabled={opinionLoading || !onGenerateOpinion} className="min-h-8 rounded border border-[#8B7355] bg-[#8B7355] px-3 text-xs text-white disabled:cursor-not-allowed disabled:opacity-60">
                      {opinionLoading ? '正在生成…' : '生成正式意见书'}
                    </button>}
                    {opinion && <div className="flex flex-wrap gap-2" aria-label="正式意见书导出"><button type="button" data-testid="opinion-export-word" disabled={opinionExporting != null} onClick={() => onExportOpinion?.('docx')} className="min-h-8 rounded border border-[#E5E5E5] bg-white px-2.5 text-[11px] text-[#6B7280] disabled:opacity-60">{opinionExporting === 'docx' ? '导出中…' : '意见书 Word'}</button><button type="button" data-testid="opinion-export-pdf" disabled={opinionExporting != null} onClick={() => onExportOpinion?.('pdf')} className="min-h-8 rounded border border-[#E5E5E5] bg-white px-2.5 text-[11px] text-[#6B7280] disabled:opacity-60">{opinionExporting === 'pdf' ? '导出中…' : '意见书 PDF'}</button></div>}
                  </div>
                </div>
                <div data-testid="prototype-report-exports" className="mt-3 flex flex-wrap items-center gap-2 border-t border-[#F3EDE3] pt-3" aria-label="审查报告导出">
                  <span className="mr-1 text-[11px] text-[#6B7280]">审查报告</span>
                  <button type="button" data-testid="prototype-report-export-draft-docx" disabled={exporting != null || draftBlocked} onClick={() => onExport?.('draft', 'docx')} className="min-h-8 rounded border border-[#E5E5E5] bg-white px-2.5 text-[11px] text-[#6B7280] disabled:cursor-not-allowed disabled:opacity-60">{exporting === 'docx' ? '导出中…' : 'AI 初稿 Word'}</button>
                  <button type="button" data-testid="prototype-report-export-draft-pdf" disabled={exporting != null || draftBlocked} onClick={() => onExport?.('draft', 'pdf')} className="min-h-8 rounded border border-[#E5E5E5] bg-white px-2.5 text-[11px] text-[#6B7280] disabled:cursor-not-allowed disabled:opacity-60">AI 初稿 PDF</button>
                  <button type="button" data-testid="prototype-report-export-confirmed-docx" disabled={exporting != null || confirmedBlocked} onClick={() => onExport?.('confirmed', 'docx')} className="min-h-8 rounded border border-[#E5E5E5] bg-white px-2.5 text-[11px] text-[#6B7280] disabled:cursor-not-allowed disabled:opacity-60">确认稿 Word</button>
                  <button type="button" data-testid="prototype-report-export-confirmed-pdf" disabled={exporting != null || confirmedBlocked} onClick={() => onExport?.('confirmed', 'pdf')} className="min-h-8 rounded border border-[#E5E5E5] bg-white px-2.5 text-[11px] text-[#6B7280] disabled:cursor-not-allowed disabled:opacity-60">确认稿 PDF</button>
                </div>
                {opinionError && <p data-testid="formal-opinion-error" role="alert" className="mt-2 rounded border border-[#F3C9C9] bg-[#FDF3F3] px-3 py-2 text-xs text-[#C62828]">正式意见书读取失败：{opinionError}</p>}
              </section>
            )}
            {report.business_document && <BusinessDocument document={report.business_document} basicInformation={report.basic_information} onOpenSource={onOpenSource} onShowBasicInformation={onShowBasicInformation} />}
            {!report.business_document && prototypeMode && <section data-testid="prototype-report-body" className="space-y-3">
              <BusinessReportStructureCard businessReport={report?.business_report} />
              <QualificationSummaryCard summary={qualificationSummary} prototypeMode />
              <p data-testid="report-overall-summary" className="sr-only">{allIssues.some((item) => item.material_only) ? '当前报告已形成，可查看逐项处置情况。' : (report.summary?.headline || '当前报告已形成，可查看逐项处置情况。')}</p>
              <section data-testid="report-disposition-summary" className="rounded border border-[#E5E5E5] bg-white px-4 py-3"><h2 className="text-sm font-semibold text-[#1A1A1A]">问题处置摘要</h2><div className="mt-3 grid gap-2 sm:grid-cols-4"><div className="rounded border border-[#EFE6D8] p-3 text-xs"><span className="text-[#9CA3AF]">发现问题</span><b className="mt-1 block text-lg text-[#1A1A1A]">{issues.length}</b><small className="text-[#9CA3AF]">合规项不列入</small></div><div className="rounded border border-[#DDEBDD] bg-[#F8FCF8] p-3 text-xs"><span className="text-[#6B7280]">已采纳</span><b className="mt-1 block text-lg text-[#2E7D32]">{issues.filter((item) => dispositionCode(item) === 'accepted').length}</b><small className="text-[#9CA3AF]">保留人工说明</small></div><div className="rounded border border-[#EFE6D8] p-3 text-xs"><span className="text-[#6B7280]">已忽略</span><b className="mt-1 block text-lg text-[#8B7355]">{issues.filter((item) => dispositionCode(item) === 'ignored').length}</b><small className="text-[#9CA3AF]">保留忽略理由</small></div><div className="rounded border border-[#F0DCB8] bg-[#FDFAF4] p-3 text-xs"><span className="text-[#92400E]">未经人工处理</span><b className="mt-1 block text-lg text-[#92400E]">{issues.filter((item) => dispositionCode(item) === 'unprocessed').length}</b><small className="text-[#9CA3AF]">报告逐项标注</small></div></div></section>
              {opinionMarkdown && <section data-testid="opinion-preview" className="rounded border border-[#E5E5E5] bg-white px-4 py-3"><h2 className="text-sm font-semibold text-[#1A1A1A]">意见书预览</h2><div className="markdown-body mt-4 text-sm leading-7 text-[#374151]"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{ h1: ({ children }) => <h1 className="mb-4 text-center text-xl font-semibold">{children}</h1>, h2: ({ children }) => <h2 className="mb-2 mt-5 text-base font-semibold text-[#1A1A1A]">{children}</h2>, h3: ({ children }) => <h3 className="mb-2 mt-4 text-sm font-semibold text-[#1A1A1A]">{children}</h3>, p: ({ children }) => <p className="mb-2 whitespace-pre-wrap">{children}</p> }}>{opinionMarkdown}</ReactMarkdown></div></section>}
              <ReferenceSnapshot key={report.reference_snapshot?.review_run_id || 'no-reference'} snapshot={report.reference_snapshot ?? null} onDownload={onDownloadReference} />
              <div data-testid="report-prototype-banner" className="rounded border border-[#E5E5E5] bg-[#FAFAF7] px-4 py-3 text-xs leading-relaxed text-[#6B7280]">报告保留机器原始结论、依据、证据和自动核查覆盖限制；人工处置只作为独立记录，不覆盖机器事实。</div>
            </section>}
            {!report.business_document && !prototypeMode && <>
             <BusinessReportStructureCard businessReport={report?.business_report} />
             <QualificationSummaryCard summary={qualificationSummary} />
             {coverage && (
               <section data-testid="report-coverage" className={`mb-4 rounded border px-4 py-3 ${coverage.complete ? 'border-[#DDEBDD] bg-[#F8FCF8]' : 'border-[#F0DCB8] bg-[#FDFAF4]'}`}>
                 <div className="flex flex-wrap items-center justify-between gap-2">
                   <h2 className="text-sm font-semibold text-[#1A1A1A]">核查覆盖说明</h2>
                   <span data-testid="report-coverage-status" className="rounded border border-[#EFE6D8] px-1.5 py-0.5 text-[10px] text-[#6B7280]">{coverage.status_label || (coverage.complete ? '完整结果' : '中间结果')}</span>
                 </div>
                 {!coverage.complete && <p data-testid="report-coverage-warning" className="mt-1 text-xs leading-relaxed text-[#92400E]">当前报告可先查看，但不能将未完成范围理解为已核查无问题。</p>}
                 {coverageLimitations.length > 0 && <ul data-testid="report-limitations" className="mt-2 space-y-1 text-xs leading-relaxed text-[#4B5563]">{coverageLimitations.map((row, index) => <li key={`${row.code || 'limitation'}-${index}`}>{row.message || row}</li>)}</ul>}
               </section>
             )}
            {onComplete && pendingProblems > 0 && <p className="mb-4 text-xs text-[#6B7280]">完成复核前还有 {pendingProblems} 项问题待处理；可先查看和下载 AI 初稿。</p>}
            <section data-testid="report-section-overview" className="mb-4 rounded border border-[#EFE6D8] bg-white px-4 py-3">
              <h2 className="text-sm font-semibold text-[#1A1A1A]">一、审查概况</h2>
              <p data-testid="report-overall-summary" className="mt-1 text-[13px] leading-relaxed text-[#4B5563]">{report.summary.headline}</p>
              {report.advisory_conclusion?.text && <p data-testid="report-advisory-conclusion" className="mt-2 rounded border border-[#F0DCB8] bg-[#FDFAF4] px-3 py-2 text-xs text-[#92400E]">建议结论（供审批参考）：{report.advisory_conclusion.text}</p>}
              <dl className="mt-3 grid gap-2 text-xs text-[#4B5563] sm:grid-cols-3">
                <div><dt className="text-[#9CA3AF]">核查材料</dt><dd className="mt-0.5 font-medium">{materialRows.length} 份</dd></div>
                <div><dt className="text-[#9CA3AF]">适用核查项</dt><dd className="mt-0.5 font-medium">{checklistSummary.applicable_total ?? checklistSummary.total_items ?? '—'} 项</dd></div>
                <div><dt className="text-[#9CA3AF]">基准日</dt><dd className="mt-0.5 font-medium">{report.meta?.baseline_date || '未填写'}</dd></div>
              </dl>
            </section>
            {ignoredItems.length > 0 && <section data-testid="report-ignored-items" className="mt-3 rounded border border-[#E5E5E5] bg-white px-4 py-3">
              <h2 className="text-sm font-semibold text-[#1A1A1A]">已忽略问题（保留审计明细）</h2>
              <p className="mt-1 text-xs leading-relaxed text-[#6B7280]">已忽略项不计入风险统计，但保留系统结论、人工处置和理由。</p>
              <ul className="mt-2 space-y-2 text-xs leading-relaxed text-[#4B5563]">
                {ignoredItems.map((item) => <li key={item.item_id} className="border-t border-[#F3EDE3] pt-2 first:border-t-0 first:pt-0">
                  <span className="font-medium">[{item.category}] {item.label}</span>
                  <span className="ml-2 text-[#6B7280]">{item.human_disposition?.note ? `忽略理由：${item.human_disposition.note}` : '忽略理由未提供'}</span>
                </li>)}
              </ul>
            </section>}
            <div className="mb-4 grid gap-2 sm:grid-cols-4">
              <div className="rounded border border-[#EFE6D8] bg-white p-3 text-xs"><span className="text-[#9CA3AF]">核查项</span><b className="mt-1 block text-lg text-[#1A1A1A]">{report.summary.total}</b></div>
              <div className="rounded border border-[#EFE6D8] bg-white p-3 text-xs"><span className="text-[#9CA3AF]">需要处理</span><b className="mt-1 block text-lg text-[#B45309]">{countOf(counts, 'hit')}</b></div>
              <div className="rounded border border-[#EFE6D8] bg-white p-3 text-xs"><span className="text-[#9CA3AF]">待确认</span><b className="mt-1 block text-lg text-[#8B7355]">{countOf(counts, 'undecidable')}</b></div>
              <div className="rounded border border-[#EFE6D8] bg-white p-3 text-xs"><span className="text-[#9CA3AF]">已通过</span><b className="mt-1 block text-lg text-[#2E7D32]">{passed}</b></div>
            </div>
            {(report.summary.open_term?.count ?? 0) > 0 && <p data-testid="report-open-term-count" className="mb-4 -mt-2 text-[11px] text-[#9CA3AF]">另有 {report.summary.open_term.count} 项兜底条款仅登记，未计入上述统计。</p>}
            {/* 刀B（BS-P1-09，设计 98 §8）：材料未读页范围 + 受影响结论数 + 次级建议。
                summary_text / suggestion_text 逐字取后端，前端不另写措辞、不自行汇总页数。 */}
            {(report.reading_coverage || []).length > 0 && (
              <section data-testid="report-reading-coverage" className="mb-4 rounded border border-[#F0DCB8] bg-[#FDFAF4] px-4 py-3">
                <h2 className="text-sm font-semibold text-[#1A1A1A]">材料未读页说明</h2>
                <ul className="mt-2 space-y-2">
                  {(report.reading_coverage || []).map((row, index) => (
                    <li key={`${row.file_name || 'material'}-${index}`} data-testid={`reading-coverage-${index}`} className="text-xs leading-relaxed text-[#4B5563]">
                      {row.file_name && <p data-testid={`reading-coverage-file-${index}`} className="font-medium text-[#1A1A1A]">{row.file_name}</p>}
                      {row.summary_text && <p data-testid={`reading-coverage-summary-${index}`} className="mt-0.5">{row.summary_text}</p>}
                      {Number.isInteger(row.affected_findings) && (
                        <p data-testid={`reading-coverage-affected-${index}`} className="mt-0.5 text-[#92400E]">受影响结论 {row.affected_findings} 条</p>
                      )}
                      {row.page_state_counts && typeof row.page_state_counts === 'object' && (
                        <p data-testid={`reading-coverage-states-${index}`} className="mt-0.5 text-[#6B7280]">
                          页级状态：{Object.entries(row.page_state_counts)
                            .filter(([, count]) => Number(count) > 0)
                            .map(([state, count]) => `${state} ${count}`)
                            .join('、') || '未提供'}
                        </p>
                      )}
                      {Array.isArray(row.page_state_history) && row.page_state_history.length > 0 && (
                        <p data-testid={`reading-coverage-history-${index}`} className="mt-0.5 text-[#6B7280]">
                          状态流转：{row.page_state_history.slice(0, 3).map((item) => (
                            `第${item.page}页 ${Array.isArray(item.transitions) ? item.transitions.map((step) => step.state).join('→') : '未提供'}`
                          )).join('；')}
                          {row.page_state_history.length > 3 ? '；其余页见证据包' : ''}
                        </p>
                      )}
                      {row.suggestion_text && <p data-testid={`reading-coverage-suggestion-${index}`} className="mt-0.5 text-[#6B7280]">{row.suggestion_text}</p>}
                    </li>
                  ))}
                </ul>
              </section>
            )}
            {runLineage && (
              <section data-testid="report-run-lineage" className="mb-4 rounded border border-[#DCE5EE] bg-[#F8FAFC] px-4 py-3">
                <h2 className="text-sm font-semibold text-[#1A1A1A]">运行链路</h2>
                <p data-testid="report-run-lineage-summary" className="mt-1 text-xs leading-relaxed text-[#4B5563]">
                  解析运行 {Array.isArray(runLineage.material_runs) ? runLineage.material_runs.length : 0} 次；
                  参考核查 {Array.isArray(runLineage.reference_runs) ? runLineage.reference_runs.length : 0} 次；
                  重核查 {Array.isArray(runLineage.reruns) ? runLineage.reruns.length : 0} 次。
                  {runLineage.latest_review_version_no != null ? ` 最新历史快照 ${runLineage.latest_review_version_no}` : ''}
                </p>
              </section>
            )}
            <div className="mb-4"><ReferenceSnapshot key={report.reference_snapshot?.review_run_id || 'no-reference'} snapshot={report.reference_snapshot ?? null} onDownload={onDownloadReference} /></div>
            <section data-testid="report-section-issues" className="rounded border border-[#E5E5E5] bg-white px-4 py-3">
              <p className="mb-1 text-[11px] text-[#9CA3AF]">二、问题与处理情况</p>
              <h2 className="text-sm font-semibold text-[#1A1A1A]">不符合项清单</h2>
              {issues.length === 0 ? <p className="mt-2 text-xs text-[#6B7280]">{coverage ? (coverage.complete ? '本次未发现需要整改的问题。' : '当前尚无逐项核查结论，不能视为全部符合。') : '本次未发现需要整改的问题。'}</p> : (
                <ol className="mt-3 space-y-3">
                  {issues.map((item, index) => {
                    const evidence = item.evidence_list?.length ? item.evidence_list : (item.evidence ? [item.evidence] : [])
                    return <li key={item.item_id} className="border-t border-[#F3EDE3] pt-3 text-xs leading-relaxed first:border-t-0 first:pt-0">
                      <div className="grid gap-1 sm:grid-cols-[28px_minmax(0,1fr)]">
                        <span className="font-semibold text-[#9CA3AF]">{index + 1}</span>
                        <div>
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="rounded border border-[#EFE6D8] px-1.5 py-0.5">{statusLabelOf(item)}</span>
                            <b className="text-[#1A1A1A]">[{item.category}] {item.label}</b>
                            {item.reading_limited && (
                              <span data-testid={`report-item-reading-limited-${item.item_id}`} className="rounded border border-[#F0DCB8] bg-[#FDFAF4] px-1.5 py-0.5 text-[10px] text-[#92400E]">材料有未读页</span>
                            )}
                            {item.human_disposition?.label && (
                              <span data-testid={`report-item-disposition-${item.item_id}`} className="rounded border border-[#EFE6D8] bg-[#FDFAF4] px-1.5 py-0.5 text-[10px] text-[#6B7280]">{item.human_disposition.label}</span>
                            )}
                          </div>
                          {item.human_disposition?.note && (
                            <p data-testid={`report-item-disposition-note-${item.item_id}`} className="mt-1 text-[11px] leading-relaxed text-[#6B7280]">人工说明：{item.human_disposition.note}</p>
                          )}
                          <p className="mt-1 text-[#4B5563]"><span className="font-medium text-[#6B7280]">问题：</span>{item.message}</p>
                          <p className="mt-1 text-[#4B5563]"><span className="font-medium text-[#6B7280]">整改建议：</span>{item.recommendation || '请核对相关资料，完成整改后重新核查。'}</p>
                          {evidence.length > 0 && <details className="mt-1.5">
                            <summary className="cursor-pointer text-[11px] text-[#8B7355]">{evidenceLabel(item)}</summary>
                            <div className="mt-1 space-y-1.5 pl-3">{evidence.map((ref, refIndex) => <blockquote key={refIndex} className="border-l-2 border-[#C8B89E] pl-2 text-[11px] text-[#6B7280]">{ref.file_name || '材料'}{ref.page ? ` · 第 ${ref.page} 页` : ''}{ref.quote ? `：${ref.quote}` : ''}</blockquote>)}</div>
                          </details>}
                        </div>
                      </div>
                    </li>
                  })}
                </ol>
              )}
            </section>
            <section data-testid="report-section-materials" className="mt-3 rounded border border-[#E5E5E5] bg-white px-4 py-3">
              <h2 className="text-sm font-semibold text-[#1A1A1A]">三、册内材料定位与缺失</h2>
              {materialRows.length === 0 ? <p className="mt-2 text-xs text-[#6B7280]">暂无材料定位记录。</p> : (
                <ul className="mt-2 space-y-2 text-xs leading-relaxed text-[#4B5563]">
                  {materialRows.map((row, index) => <li key={`${row.material_id || row.file_name || 'material'}-${index}`} className="border-t border-[#F3EDE3] pt-2 first:border-t-0 first:pt-0">
                    <span className="font-medium">{row.file_name || '材料'}</span>
                    <span className="ml-2 text-[#6B7280]">{row.version_role === 'superseded' ? '历史版本' : row.version_role === 'current' ? '当前版本' : '已接收'}</span>
                    {row.parse_state && <span className="ml-2 text-[#6B7280]">读取：{row.parse_state}</span>}
                  </li>)}
                </ul>
              )}
              {(report.material_issues || []).length > 0 && <p className="mt-3 text-xs leading-relaxed text-[#92400E]">{report.material_issues.map((item) => item.message).filter(Boolean).join('；')}</p>}
            </section>
            <section data-testid="report-section-review" className="mt-3 rounded border border-[#E5E5E5] bg-white px-4 py-3">
              <h2 className="text-sm font-semibold text-[#1A1A1A]">四、复核说明</h2>
              <p className="mt-2 text-xs leading-relaxed text-[#4B5563]">系统结论依据核查基准日有效的规则版本生成；未人工处置的问题保留系统核查结果，已忽略问题保留人工理由。</p>
              {coverage && <p className="mt-1 text-xs leading-relaxed text-[#6B7280]">当前状态：{coverage.status_label || (coverage.complete ? '完整结果' : '中间结果')}。</p>}
              {coverageLimitations.length > 0 && <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-[#6B7280]">{coverageLimitations.map((row, index) => <li key={index}>{row.message || row}</li>)}</ul>}
            </section>
            {openTerms.length > 0 && <section data-testid="report-open-terms" className="mt-3 rounded border border-[#E5E5E5] bg-white px-4 py-3">
              <h2 className="text-sm font-semibold text-[#1A1A1A]">兜底条款（仅登记）</h2>
              <p className="mt-1 text-xs leading-relaxed text-[#6B7280]">{report.summary.open_term?.notice || `本次另有 ${openTerms.length} 项为法律法规兜底条款，仅登记备查、不参与结论。`}</p>
              <ul className="mt-2 space-y-1.5 text-xs leading-relaxed text-[#4B5563]">
                {openTerms.map((item) => <li key={item.item_id} className="flex flex-wrap items-center gap-2">
                  <span className="rounded border border-[#EFE6D8] px-1.5 py-0.5 text-[10px] text-[#6B7280]">{statusLabelOf(item)}</span>
                  <span>[{item.category}] {item.label}</span>
                </li>)}
              </ul>
            </section>}
            {issues.length > 0 && <section className="mt-3 rounded border border-[#E5E5E5] bg-white px-4 py-3">
              <h2 className="text-sm font-semibold text-[#1A1A1A]">整改建议</h2>
              <p className="mt-1 text-xs leading-relaxed text-[#6B7280]">请针对以上问题补充或核实资料，完成整改后重新提交核查。</p>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-xs leading-relaxed text-[#4B5563]">
                {[...new Map(issues.map((item) => [item.category, item])).values()].map((item) => <li key={item.category}>{item.category}：{item.recommendation || '请核对相关资料并完成整改。'}</li>)}
              </ul>
            </section>}
            </>}
          </>
        )}
      </main>
      {prototypeMode && report?.stage_label && (
        <div className="sr-only" aria-hidden="true">
          <span data-testid="report-stage-label">{report.stage_label}</span>
        </div>
      )}
    </div>
  )
}
