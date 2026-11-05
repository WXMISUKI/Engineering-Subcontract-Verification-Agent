// 根据同一材料和阶段的实际完成速率估算；只收紧 deadline，不允许回升。

export function updateBsEta(previous, progress, now = Date.now()) {
  const phase = progress?.source === 'text' ? 'text' : 'ocr'
  const identity = `${progress?.material_id ?? ''}:${progress?.run_id ?? ''}:${phase}`
  if (previous?.identity !== identity) previous = null
  if (previous?.deadline && now >= previous.deadline) previous = null
  // A failed page is not evidence that the remaining work will finish on schedule.
  // Start a fresh observation only after later successful progress arrives.
  if (progress?.source === 'failed') return { identity, deadline: null }
  const total = Number(progress?.total_work_pages)
  const current = Number(progress?.done_pages)
  if (!Number.isFinite(total) || total <= 0 || !Number.isFinite(current) || current < 0) {
    return previous || { deadline: null, totalPages: null, donePages: 0 }
  }
  if (previous?.totalPages != null && previous.totalPages !== total) previous = null
  const safeCurrent = Math.min(total, current)
  const startedAt = previous?.startedAt ?? now
  const initialDone = previous?.initialDone ?? safeCurrent
  const elapsed = Math.max(0, now - startedAt)
  const observedPages = safeCurrent - initialDone
  const candidate = observedPages >= 2 && elapsed >= 2000 && safeCurrent < total
    ? now + (elapsed / observedPages) * (total - safeCurrent) * 1.25
    : null
  const deadline = candidate == null ? previous?.deadline ?? null
    : previous?.deadline == null ? candidate : Math.min(previous.deadline, candidate)
  const lastProgressAt = previous?.donePages === safeCurrent ? previous?.lastProgressAt ?? now : now
  return { deadline, startedAt, initialDone, totalPages: total, donePages: safeCurrent, identity, lastProgressAt }
}

export function bsEtaLabel(plan, now = Date.now()) {
  if (!plan?.deadline || !Number.isFinite(plan.deadline)) return '预计剩余 估算中…'
  if (plan.lastProgressAt != null && now - plan.lastProgressAt > 30000) return '预计剩余 估算中…'
  const seconds = Math.ceil(Math.max(0, plan.deadline - now) / 1000)
  if (seconds <= 0) return '预计剩余 估算中…'
  if (seconds >= 60) return `预计剩余 ${Math.floor(seconds / 60)}分${seconds % 60}秒`
  return `预计剩余 ${seconds}秒`
}
