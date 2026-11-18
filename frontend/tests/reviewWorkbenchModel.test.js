import { describe, expect, it } from 'vitest'
import { buildQualityGroups, headerSupplementItemOf } from '../src/plus-ui/blockchain-supervision/lib/reviewWorkbenchModel'

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

describe('buildQualityGroups', () => {
  const materials = {
    10: { id: 10, subject_id: 7, revision_no: 2, file_name: '人员证书.pdf' },
    11: { id: 11, subject_id: 8, revision_no: 2, file_name: '另一主体证书.pdf' },
    12: { id: 12, subject_id: 7, revision_no: 3, file_name: '新修订证书.pdf' },
    13: { id: 13, subject_id: 7, file_name: '历史材料.pdf' },
  }

  it('aggregates quality findings on the same physical page and keeps finding ids', () => {
    const groups = buildQualityGroups([
      { findingId: 101, itemId: 'A2-01', subjectId: 7, materialId: 10, page: 4, evidence: [{ material_id: 10, page: 4, quality: 'review_required' }] },
      { findingId: 102, itemId: 'A2-02', subjectId: 7, materialId: 10, page: 4, evidence: [{ material_id: 10, page: 4, quality: 'unreadable' }] },
    ], materials)

    expect(groups).toHaveLength(1)
    expect(groups[0]).toMatchObject({ subjectId: 7, materialId: 10, revisionNo: 2, page: 4, count: 2 })
    expect(groups[0].findingIds).toEqual([101, 102])
  })

  it('does not group across subjects, revisions, unknown pages, or legacy revisions', () => {
    const groups = buildQualityGroups([
      { findingId: 201, subjectId: 7, materialId: 10, evidence: [{ material_id: 10, page: 4, quality: 'review_required' }] },
      { findingId: 202, subjectId: 8, materialId: 11, evidence: [{ material_id: 11, page: 4, quality: 'review_required' }] },
      { findingId: 203, subjectId: 7, materialId: 12, evidence: [{ material_id: 12, page: 4, quality: 'review_required' }] },
      { findingId: 204, subjectId: 7, materialId: 10, evidence: [{ material_id: 10, quality: 'review_required' }] },
      { findingId: 205, subjectId: 7, materialId: 13, evidence: [{ material_id: 13, page: 4, quality: 'review_required' }] },
    ], materials)

    expect(groups).toHaveLength(3)
    expect(groups.map((group) => group.findingIds)).toEqual([[201], [202], [203]])
  })

  it('does not carry review decisions into the aggregate', () => {
    const groups = buildQualityGroups([
      { findingId: 301, itemId: 'A2-01', subjectId: 7, materialId: 10, evidence: [{ material_id: 10, page: 4, quality: 'review_required' }], review: { decision: 'accepted' } },
      { findingId: 302, itemId: 'A2-02', subjectId: 7, materialId: 10, evidence: [{ material_id: 10, page: 4, quality: 'review_required' }], review: null },
    ], materials)

    expect(groups[0].findingIds).toEqual([301, 302])
    expect(groups[0]).not.toHaveProperty('review')
  })
})
