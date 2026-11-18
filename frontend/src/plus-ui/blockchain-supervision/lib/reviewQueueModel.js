import { isManualPrompt, isMaterialOnly, isOpenTerm, reviewBucket } from './reviewDecision'

export const REVIEW_QUEUE_SECTIONS = [
  { key: 'pending', label: '待处理' },
  { key: 'undecidable', label: '无法核实' },
  { key: 'processed', label: '已处理' },
]

function flattenGroups(groups) {
  return (groups || []).flatMap((group) => group?.items || [])
}

export function classifyReviewItem(item) {
  if (isOpenTerm(item) || isMaterialOnly(item)) return null
  const bucket = reviewBucket(item)
  if (bucket === 'accepted' || bucket === 'ignored') return 'processed'
  if (item?.status === 'undecidable') return isManualPrompt(item) ? 'manual_prompt' : 'undecidable'
  if (item?.status === 'hit') return 'hit'
  return null
}

export function blocksReportReview(item) {
  return !isOpenTerm(item) && !isManualPrompt(item) && item?.status === 'undecidable' && reviewBucket(item) === 'pending'
    && (item.reportReviewBlocking ?? item.report_review_blocking) !== false
}

export function reviewQueueCounts(groups) {
  const items = flattenGroups(groups).filter((item) => classifyReviewItem(item))
  return {
    total: items.length,
    undecidable: items.filter((item) => item.status === 'undecidable' && !isManualPrompt(item)
      && reviewBucket(item) === 'pending').length,
    manualPrompt: items.filter((item) => isManualPrompt(item) && reviewBucket(item) === 'pending').length,
    hit: items.filter((item) => item.status === 'hit' && reviewBucket(item) === 'pending').length,
    accepted: items.filter((item) => reviewBucket(item) === 'accepted').length,
    ignored: items.filter((item) => reviewBucket(item) === 'ignored').length,
  }
}

export function buildReviewQueueSections(groups, filter = 'pending') {
  const issueItems = flattenGroups(groups).filter((item) => classifyReviewItem(item))
  const pendingItems = issueItems.filter((item) => reviewBucket(item) === 'pending')
  const undecidableItems = pendingItems.filter((item) => item.status === 'undecidable' && !isManualPrompt(item))
  const processedItems = issueItems.filter((item) => ['accepted', 'ignored'].includes(reviewBucket(item)))
  if (filter === 'pending') return pendingItems.length ? [{ key: 'pending', label: '待处理', items: pendingItems }] : []
  if (filter === 'undecidable') return undecidableItems.length ? [{ key: 'undecidable', label: '无法核实', items: undecidableItems }] : []
  if (filter === 'processed') return processedItems.length ? [{ key: 'processed', label: '已处理', items: processedItems }] : []
  if (filter === 'all') {
    return [
      pendingItems.length ? { key: 'pending', label: '待处理', items: pendingItems } : null,
      processedItems.length ? { key: 'processed', label: '已处理', items: processedItems } : null,
    ].filter(Boolean)
  }
  return []
}

/**
 * 当前筛选队列已空时，选中项已不在可导航范围内，必须退出详情/对照。
 * 步骤筛选例外：空队列只代表该步骤没有待处理项，步骤详情仍需保留。
 */
export function queueSelectionExpired({ reviewItemsLength = 0, stepScoped = false } = {}) {
  return !stepScoped && reviewItemsLength === 0
}

/**
 * 全屏对照标题。
 * 当前项被处理后已离开筛选队列，但队列里可能还有别的问题；此时不能显示
 * “已全部处理”，否则会让人误以为整批已清零，随后又跳到下一项。
 */
export function compareHeaderTitle({ scopeLabel, position = -1, total = 0 } = {}) {
  if (position >= 0 && total > 0) return `${scopeLabel} ${position + 1} / ${total}`
  if (total > 0) return `${scopeLabel} 共 ${total} 项`
  return `${scopeLabel} 已全部处理`
}

export function reportGateForGroups(groups) {
  const counts = reviewQueueCounts(groups)
  return {
    canGenerate: !flattenGroups(groups).some(blocksReportReview),
    pendingUndecidable: counts.undecidable,
    pendingHit: counts.hit,
  }
}
