export const REVIEWABLE_STATUSES = new Set(['hit', 'undecidable'])

export function isOpenTerm(item) {
  return item?.open_term === true || item?.openTerm === true
}

export function isMaterialOnly(item) {
  return item?.material_only === true || item?.materialOnly === true
}

export function reviewBucket(item) {
  if (isOpenTerm(item) || isMaterialOnly(item) || (item?.status && !REVIEWABLE_STATUSES.has(item.status))) return 'all'
  const decision = item?.review?.decision
  if (decision === 'accepted' || decision === 'confirm') return 'accepted'
  if (decision === 'ignored' || decision === 'reject') return 'ignored'
  return 'pending'
}

export function isUnreviewedFinding(item) {
  return !isOpenTerm(item) && !isMaterialOnly(item) && REVIEWABLE_STATUSES.has(item?.status) && reviewBucket(item) === 'pending'
}

// 保留旧导出给外部调用方；业务新逻辑应使用 isUnreviewedFinding。
export function isUnreviewedHit(item) {
  return item?.status === 'hit' && reviewBucket(item) === 'pending'
}
