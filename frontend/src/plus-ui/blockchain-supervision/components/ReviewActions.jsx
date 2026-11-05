import { useEffect, useState } from 'react'
import { reviewBucket } from '../lib/reviewDecision'

// 当前候选人工处置：问题项只提供采纳/忽略和可选说明。
// 未人工处理不是可提交状态，而是由没有当前回执推导出的展示事实。

function ReviewBadge({ review, onEdit }) {
  const accepted = reviewBucket({ review }) === 'accepted'
  return (
    <div className="space-y-1">
      <p className="flex flex-wrap items-center gap-1.5 text-xs">
        <span className={`rounded border px-1.5 py-0.5 ${accepted ? 'border-emerald-200 bg-emerald-50 text-emerald-700' : 'border-red-200 bg-red-50 text-red-700'}`}>
          {accepted ? '已采纳' : '已忽略'}
        </span>
        {review.note && <span className="min-w-0 break-words text-[#6B7280]">「{review.note}」</span>}
      </p>
      {onEdit && <button type="button" onClick={onEdit} className="min-h-9 text-xs text-[#8B7355] hover:underline">修改处理</button>}
    </div>
  )
}

function ReviewForm({ findingId, submitting, onReview, extraAction = null, decisionLeadingActions = null }) {
  const [note, setNote] = useState('')
  const [validationError, setValidationError] = useState('')
  useEffect(() => { setNote('') }, [findingId])
  const submit = (decision) => {
    if (decision === 'ignored' && !note.trim()) {
      setValidationError('忽略必须填写处理理由。')
      return
    }
    setValidationError('')
    onReview?.(findingId, decision, note.trim() || null)
  }
  return (
    <div className="space-y-2">
      <label className="sr-only" htmlFor={`review-note-${findingId}`}>处理意见</label>
      <textarea
        id={`review-note-${findingId}`}
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="处理意见；忽略时必须填写理由"
        rows={3}
        className="w-full min-w-0 resize-y rounded-[4px] border border-[#E5E5E5] bg-white px-2 py-2 text-xs leading-relaxed text-[#1A1A1A] placeholder:text-[#C0C0C0] focus:border-[#8B7355] focus:outline-none"
      />
      {validationError && <p role="alert" data-testid="review-note-error" className="text-[11px] text-[#C62828]">{validationError}</p>}
      <div className="flex flex-wrap gap-2">
        {decisionLeadingActions}
        <button type="button" disabled={submitting} onClick={() => submit('accepted')} className="min-h-10 rounded-[4px] bg-[#2E7D32] px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-60">{submitting ? '提交中…' : '采纳'}</button>
        <button type="button" disabled={submitting} onClick={() => submit('ignored')} className="min-h-10 rounded-[4px] border border-[#E5E5E5] bg-white px-3 py-1.5 text-xs text-[#6B7280] hover:border-red-300 hover:text-red-700 disabled:opacity-60">忽略</button>
        {extraAction}
      </div>
    </div>
  )
}

/** 详情区复用的人工处置区；问题摘要与原文预览保持联动。 */
export default function ReviewActions({
  finding,
  item = null,
  evidence = [],
  loading = false,
  error = null,
  actions = {},
  submitting = false,
  onReview,
  onComplete,
  completing = false,
  completeDisabled = false,
  completeLabel = '生成报告',
  compact = false,
  extraAction = null,
  decisionLeadingActions = null,
  persistentActions = null,
}) {
  const [editing, setEditing] = useState(false)
  const findingId = finding?.id ?? null
  const review = finding?.review || null
  const invalidated = review?.invalidated || null
  const hasDecision = Boolean(review && ['accepted', 'ignored', 'confirm', 'reject'].includes(review.decision) && !invalidated)
  const reviewable = ['hit', 'undecidable'].includes(finding?.status)
  const canReview = !!actions.canReview && reviewable && (!hasDecision || editing || invalidated)
  const source = evidence?.[0] || null
  useEffect(() => { setEditing(false) }, [findingId])
  const summaryTitle = finding?.title && finding.title !== finding.message
    ? finding.title
    : item?.title && item.title !== finding?.message ? item.title : null

  return (
    <section aria-label={compact ? '问题处理' : '人工复核'} data-testid="review-actions" className={compact ? 'space-y-2 border-t border-[#EFE6D8] pt-2' : 'flex min-h-0 w-full shrink-0 flex-col overflow-hidden rounded-[6px] border border-[#EFE6D8] bg-white'}>
      {!compact && <header className="flex shrink-0 items-center justify-between border-b border-[#EFE6D8] px-3 py-2">
        <h2 aria-label="人工处理" className="text-sm font-semibold text-[#8B7355]">人工复核</h2>
        {finding?.statusLabel && <span className="text-[11px] text-[#6B7280]">{finding.statusLabel}</span>}
      </header>}
      <div className={compact ? 'space-y-1' : 'min-h-0 max-h-[min(34vh,300px)] overflow-y-auto p-3'}>
        {loading ? <p className="text-xs text-[#9CA3AF]">加载中…</p>
          : error ? <p className="text-xs text-[#C62828]">{error}</p>
            : !finding ? <div data-testid="detail-empty" className="text-xs text-[#9CA3AF]">请先选择一条问题</div> : (
              <>
                {!compact && <div data-testid="finding-detail" className="mb-2 space-y-1">
                  <div data-testid="finding-summary" className="space-y-1">
                    {summaryTitle && <p className="text-[13px] font-medium leading-relaxed text-[#1A1A1A]">{summaryTitle}</p>}
                    {(finding.message || finding.reason || finding.description || item?.reason) && (
                      <p data-testid={finding.status === 'undecidable' ? 'undecidable-reason' : undefined} className="text-xs leading-relaxed text-[#6B7280]">{finding.message || finding.reason || finding.description || item?.reason}</p>
                    )}
                    {source?.quote && <p className="border-l-2 border-[#C8B89E] pl-2 text-[11px] leading-relaxed text-[#6B7280]">原文：{source.quote}</p>}
                  </div>
                </div>}
                {invalidated && (
                  <p data-testid="review-invalidated-note" className="mb-1.5 text-[11px] leading-relaxed text-[#92400E]">
                    原复核意见已失效，请重新处置；旧意见只作留痕。
                  </p>
                )}
                {hasDecision && !editing ? (
                  <>
                    {persistentActions && <div className="mb-2 flex flex-wrap gap-2">{persistentActions}</div>}
                    <ReviewBadge review={review} onEdit={actions.canReview ? () => setEditing(true) : null} />
                  </>
                ) : canReview ? (
                  <ReviewForm findingId={findingId} submitting={submitting} onReview={onReview} extraAction={extraAction} decisionLeadingActions={<>{persistentActions}{decisionLeadingActions}</>} />
                ) : !compact && reviewable ? (
                  <p data-testid="review-not-required" className="text-xs text-[#6B7280]">当前仅展示机器结论，尚未人工处理。</p>
                ) : null}
              </>
            )}
      </div>
      {onComplete && (
        <div className={compact ? '' : 'shrink-0 border-t border-[#EFE6D8] p-2'}>
          <button type="button" onClick={onComplete} disabled={completing || completeDisabled} aria-disabled={completeDisabled || undefined} className="min-h-10 w-full rounded-[4px] bg-[#8B7355] px-3 py-2 text-xs font-medium text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50">
            {completing ? '正在生成报告…' : completeLabel}
          </button>
        </div>
      )}
    </section>
  )
}
