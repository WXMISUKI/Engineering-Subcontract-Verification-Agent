import { describe, expect, it } from 'vitest'
import { headerSupplementItemOf } from '../src/plus-ui/blockchain-supervision/lib/reviewWorkbenchModel'

describe('headerSupplementItemOf', () => {
  it('returns the backend actionable item with its mapping context', () => {
    const item = { itemIds: ['certificate'], subjectIds: [7], materialIds: [12], subjectId: 7 }
    expect(headerSupplementItemOf({ missing: [item] })).toBe(item)
  })

  it('returns null when the task has no actionable missing item', () => {
    expect(headerSupplementItemOf(null)).toBeNull()
    expect(headerSupplementItemOf({ missing: [] })).toBeNull()
    expect(headerSupplementItemOf({ missing: [null] })).toBeNull()
  })
})
