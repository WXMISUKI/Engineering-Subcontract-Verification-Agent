import { useCallback, useEffect, useMemo, useState } from 'react'

import { buildReviewWorkbenchModel } from '../lib/reviewWorkbenchModel'
import { isOpenTerm, reviewBucket } from '../lib/reviewDecision'

export function matchesReviewFilter(finding, filter) {
  if (filter === 'all') return true
  if (isOpenTerm(finding)) return false
  if (filter === 'processed') {
    return ['accepted', 'ignored'].includes(reviewBucket(finding))
  }
  if (filter === 'undecidable') return finding?.status === 'undecidable'
  if (filter === 'hit') return finding?.status === 'hit'
  return finding?.status !== 'miss' && reviewBucket(finding) === filter
}

/**
 * T8-U2：核查三栏工作台选择态 hook。
 *
 * 职责边界：
 * - 只吃 detail（get_task）+ findings（list_findings）两个数据源，**不直接请求 API**；
 *   复核动作经父组件注入的 onReview 回调透出（组件不 fetch）。
 * - 用 buildReviewWorkbenchModel 产出 view-model；
 * - 维护稳定的 selectedFindingId（刷新后尽量保持；当前项消失 → 取第一条可见待办）；
 * - 维护「符合项」默认折叠态（D6）。
 *
 * @param {object}   args
 * @param {object}   args.detail    get_task 返回体（含 subjects/materials/state）
 * @param {object[]} args.findings  list_findings 返回的 findings[]
 * @param {Function} [args.onReview] 复核回调 (findingId, decision, note) => Promise
 * @returns {{model:object, selectedFindingId:number|null, selectFinding:Function,
 *           missCollapsed:boolean, toggleMiss:Function, submitting:boolean, submitReview:Function}}
 */
export default function useReviewWorkbench({ detail, findings, onReview } = {}) {
  const [selectedFindingId, setSelectedFindingId] = useState(null)
  // D6：左栏符合项默认折叠（绝不 filter 掉，仅折叠）。
  const [missCollapsed, setMissCollapsed] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [reviewFilter, setReviewFilter] = useState('pending')

  const model = useMemo(
    () => {
      const visible = (findings || []).filter((finding) => matchesReviewFilter(finding, reviewFilter))
      const selected = buildReviewWorkbenchModel({ detail, findings: visible, selectedFindingId })
      const all = buildReviewWorkbenchModel({ detail, findings })
      return { ...selected, groups: all.groups }
    },
    [detail, findings, selectedFindingId, reviewFilter],
  )

  // 数据刷新后：若当前选中项已消失，model 会回退到第一条可见待办；据此同步 state（防环靠等值判断）。
  const effectiveId = model.selectedFindingId
  useEffect(() => {
    if (selectedFindingId !== effectiveId) setSelectedFindingId(effectiveId)
  }, [effectiveId, selectedFindingId])

  const selectFinding = useCallback((findingId) => {
    setSelectedFindingId(findingId ?? null)
  }, [])

  const toggleMiss = useCallback(() => setMissCollapsed((v) => !v), [])

  const submitReview = useCallback(
    async (findingId, decision, note) => {
      if (typeof onReview !== 'function' || findingId == null || !decision) return
      setSubmitting(true)
      try {
        return await onReview(findingId, decision, note ?? null)
      } finally {
        setSubmitting(false)
      }
    },
    [onReview],
  )

  return {
    model,
    selectedFindingId: effectiveId,
    selectFinding,
    missCollapsed,
    toggleMiss,
    submitting,
    submitReview,
    reviewFilter,
    setReviewFilter,
  }
}
