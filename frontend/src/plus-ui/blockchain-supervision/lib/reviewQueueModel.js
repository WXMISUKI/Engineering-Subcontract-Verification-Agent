import { isMaterialOnly, isOpenTerm, reviewBucket } from './reviewDecision'

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
  if (item?.status === 'undecidable') return 'undecidable'
  if (item?.status === 'hit') return 'hit'
  return null
}

export function blocksReportReview(item) {
  return !isOpenTerm(item) && item?.status === 'undecidable' && reviewBucket(item) === 'pending'
    && (item.reportReviewBlocking ?? item.report_review_blocking) !== false
}

export function reviewQueueCounts(groups) {
  const items = flattenGroups(groups).filter((item) => classifyReviewItem(item))
  return {
    total: items.length,
    undecidable: items.filter((item) => item.status === 'undecidable' && reviewBucket(item) === 'pending').length,
    hit: items.filter((item) => item.status === 'hit' && reviewBucket(item) === 'pending').length,
    accepted: items.filter((item) => reviewBucket(item) === 'accepted').length,
    ignored: items.filter((item) => reviewBucket(item) === 'ignored').length,
  }
}

export function buildReviewQueueSections(groups, filter = 'pending') {
  const issueItems = flattenGroups(groups).filter((item) => classifyReviewItem(item))
  const pendingItems = issueItems.filter((item) => reviewBucket(item) === 'pending')
  const undecidableItems = pendingItems.filter((item) => item.status === 'undecidable')
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

export function reportGateForGroups(groups) {
  const counts = reviewQueueCounts(groups)
  return {
    canGenerate: !flattenGroups(groups).some(blocksReportReview),
    pendingUndecidable: counts.undecidable,
    pendingHit: counts.hit,
  }
}
