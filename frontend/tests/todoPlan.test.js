import { afterEach, describe, expect, it, vi } from 'vitest'
import { buildMissingPlan, supplementUploadCategoryOf } from '../src/plus-ui/blockchain-supervision/lib/todoPlan'

afterEach(() => { vi.restoreAllMocks() })

describe('补件待办映射', () => {
  it('保留单主体缺件的主体映射并预选报审资料包类别', () => {
    const plan = buildMissingPlan({
      total_undecidable: 1,
      categories: [{
        category: 'submission_packet',
        label: '交安 C 证',
        item_ids: ['RC-P13'],
        subject_ids: [34],
        roles: ['packet'],
        actionable: true,
      }],
    })

    expect(plan.missing).toHaveLength(1)
    expect(plan.missing[0].subjectIds).toEqual([34])
    expect(plan.missing[0].uploadCategory).toBe('packet')
    expect(supplementUploadCategoryOf(plan.missing[0])).toBe('packet')
  })

  it('多主体且映射不可靠时不强行绑定主体', () => {
    const plan = buildMissingPlan({
      categories: [{
        category: 'submission_packet',
        label: '报审附件',
        item_ids: ['RC-A01'],
        subject_ids: [34, 35],
        subject_mapping_reliable: false,
        roles: ['packet'],
        actionable: true,
      }],
    })

    expect(plan.missing[0].subjectIds).toEqual([34, 35])
    expect(plan.missing[0].subjectMappingReliable).toBe(false)
  })
})
