import { describe, expect, it } from 'vitest'
import { buildReviewQueueSections, queueSelectionExpired } from '../src/plus-ui/blockchain-supervision/lib/reviewQueueModel'

describe('queueSelectionExpired', () => {
  it('无法核实筛选最后一项被采纳后队列为空，选中项必须退出', () => {
    const pending = { findingId: 1, status: 'undecidable', review: null }
    const groups = [{ key: 'undecidable', label: '无法核实', items: [pending] }]
    expect(buildReviewQueueSections(groups, 'undecidable')).toHaveLength(1)

    const accepted = { ...pending, review: { decision: 'accepted' } }
    const after = [{ ...groups[0], items: [accepted] }]
    expect(buildReviewQueueSections(after, 'undecidable')).toEqual([])
    expect(queueSelectionExpired({ reviewItemsLength: 0 })).toBe(true)
  })

  it('步骤筛选为空只代表该步骤无待处理项，不退出步骤详情', () => {
    expect(queueSelectionExpired({ reviewItemsLength: 0, stepScoped: true })).toBe(false)
  })

  it('当前筛选仍有队列项时保留选中详情', () => {
    expect(queueSelectionExpired({ reviewItemsLength: 1 })).toBe(false)
  })
})
