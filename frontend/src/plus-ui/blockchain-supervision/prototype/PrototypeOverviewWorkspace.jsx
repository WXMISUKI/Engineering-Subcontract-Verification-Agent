import StepOverviewPanel from '../components/StepOverviewPanel'
import SubjectChecklistSummary from '../components/SubjectChecklistSummary'

function isException(finding) {
  return finding?.status === 'hit' || finding?.status === 'undecidable'
}

function isReviewed(finding) {
  return ['accepted', 'ignored', 'confirm', 'reject'].includes(finding?.review?.decision)
}

function basisTextOf(finding) {
  if (typeof finding?.basis === 'string') return finding.basis
  return finding?.basis?.docNo || finding?.basis?.doc_no || finding?.basis?.text || (finding?.status === 'undecidable' ? '证据不足，暂不能支持该判断' : '系统发现，需要人工查看')
}

function Metric({ label, value, tone = 'normal', hint }) {
  const toneClass = tone === 'bad'
    ? 'text-[#C62828]'
    : tone === 'warn'
      ? 'text-[#92400E]'
      : 'text-[#1A1A1A]'
  return (
    <div className="rounded-[6px] border border-[#E9E3D9] bg-white px-3 py-2.5">
      <span className="block text-[11px] text-[#6B7280]">{label}</span>
      <strong className={`mt-1 block text-[22px] leading-none tabular-nums ${toneClass}`}>{value}</strong>
      <small className="mt-1 block text-[10px] leading-relaxed text-[#9CA3AF]">{hint}</small>
    </div>
  )
}

export default function PrototypeOverviewWorkspace({
  findings = [],
  stepOverview = null,
  missingPlan = null,
  checklistSummary = null,
  materialIssues = [],
  loading = false,
  error = null,
  onSelectFinding,
  onSelectStep,
  onSupplement,
  onOpenReport,
  onRerun,
  rerunDisabled = false,
}) {
  const exceptions = findings.filter(isException)
  const unresolved = exceptions.filter((finding) => !isReviewed(finding))
  const checklistItems = Array.isArray(checklistSummary?.subjects)
    ? checklistSummary.subjects.flatMap((subject) => Array.isArray(subject?.checklist_items) ? subject.checklist_items : [])
    : []
  const missingCount = checklistItems.length
    ? checklistItems.filter((item) => item?.missing === true).length
    : missingPlan?.missing?.reduce((total, item) => total + Number(item.count || 0), 0) ?? 0
  const priority = unresolved
    .slice()
    .sort((a, b) => (a.status === 'hit' ? 0 : 1) - (b.status === 'hit' ? 0 : 1))
    .slice(0, 3)

  return (
    <section data-testid="prototype-overview-workspace" className="min-w-0 space-y-3">
      <p className="rounded-[7px] border border-[#C8E4CF] bg-[#EFFAF2] px-3 py-2 text-xs leading-relaxed text-[#2F7D49]">
        先在第 1 步补齐材料，再处理后续步骤发现的问题；人工未处理不会阻断报告生成。
      </p>
      {loading && <p data-testid="prototype-overview-loading" role="status" className="rounded-[6px] border border-dashed border-[#D8E1EB] bg-white px-3 py-5 text-center text-xs text-[#64748B]">正在读取总览数据…</p>}

      <SubjectChecklistSummary summary={checklistSummary} loading={loading} />

      {!loading && <div className="summary grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          <Metric label="缺失材料" value={missingCount} tone="bad" hint="统一在第 1 步处理" />
          <Metric label="发现的问题" value={exceptions.length} hint="符合项不进入问题列表" />
          <Metric label="未人工处理" value={unresolved.length} tone="warn" hint="报告中逐项标注" />
        </div>}

      {error && <p data-testid="prototype-overview-error" role="alert" className="rounded-[6px] border border-[#F3C9C9] bg-[#FDF3F3] px-3 py-2 text-xs text-[#C62828]">总览读取失败：{error}</p>}

      <section data-testid="prototype-overview-priority" className="rounded-[8px] border border-[#E9E3D9] bg-white">
        <header className="flex flex-wrap items-baseline gap-2 border-b border-[#E9E3D9] px-3 py-2.5"><h2 className="text-sm font-semibold text-[#1A1A1A]">待查看的问题</h2><span className="text-[11px] text-[#9CA3AF]">点击进入依据、原文和人工说明</span></header>
        {priority.length ? <div className="grid gap-2 p-3 md:grid-cols-3">{priority.map((finding) => <article key={finding.id} className={`rounded-[7px] border border-[#DCE5EE] border-l-4 ${finding.status === 'hit' ? 'border-l-[#BD3E3E] bg-[#FFF4F3]' : 'border-l-[#96651C] bg-white'} p-2.5`}><b className="block line-clamp-2 text-xs leading-relaxed text-[#1A1A1A]">{finding.item_id || finding.itemId || '问题'} · {finding.message || finding.title || '待核实问题'}</b><p className="mt-1 line-clamp-2 text-[11px] text-[#587089]">依据：{basisTextOf(finding)}</p><button type="button" onClick={() => onSelectFinding?.(finding.id)} className="mt-2 min-h-9 rounded-[4px] border border-[#8B7355] px-2.5 text-[11px] text-[#8B7355]">查看问题</button></article>)}</div> : <p className="px-3 py-4 text-xs text-[#6B7280]">当前没有未人工处理的问题。</p>}
      </section>

      {materialIssues.length > 0 && <section data-testid="prototype-overview-material-issues" className="rounded-[8px] border border-[#F0DCB8] bg-[#FDFAF4] p-3 text-xs leading-relaxed text-[#92400E]">
        <h2 className="text-sm font-semibold text-[#5B4A2F]">解析异常</h2>
        <ul className="mt-2 space-y-1">{materialIssues.map((issue, index) => <li key={`${issue.file_name || 'material'}-${issue.message || index}`}>· {issue.file_name || '材料'}：{issue.message || '读取结果需要人工复核'}</li>)}</ul>
      </section>}

      <StepOverviewPanel
        model={stepOverview}
        loading={loading}
        error={error}
        onSelectStep={onSelectStep}
        onSelectFinding={onSelectFinding}
        onSupplement={onSupplement}
      />
      <div className="mt-3 flex items-center gap-3 rounded-[9px] border border-[#DCE5EE] bg-white px-3.5 py-3">
        <div className="min-w-0 flex-1">
          <strong className="block text-sm text-[#17283D]">报告始终可生成</strong>
          <p className="mt-0.5 text-xs text-[#587089]">未人工处理的问题会逐项写入正式意见书，并明确标注“未经人工处理”。</p>
        </div>
        <button type="button" onClick={onOpenReport} className="min-h-9 shrink-0 rounded-[4px] border border-[#174C7E] bg-[#174C7E] px-3 text-xs text-white">查看报告</button>
      </div>
    </section>
  )
}
