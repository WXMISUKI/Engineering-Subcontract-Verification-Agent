import { describe, expect, it } from 'vitest'
import { buildTaskHeader } from '../src/plus-ui/blockchain-supervision/lib/taskHeaderModel'

const base = {
  detail: {
    title: '任务标题',
    subjects: [{ subject_id: 1, name: '分包公司', subject_type: 'professional' }],
    materials: [],
  },
}

function field(header, key) {
  return header.fields.find((item) => item.key === key)
}

describe('buildTaskHeader contractor evidence', () => {
  it('shows a matched general contractor', () => {
    const header = buildTaskHeader({
      ...base,
      report: { contract_party_evidence: {
        general_contractor: { status: 'matched', company: '总承包公司' },
      } },
    })
    expect(field(header, 'contractor').value).toBe('总承包公司')
    expect(header.company).toBe('分包公司')
  })

  it('shows an unresolved candidate instead of silently leaving the field blank', () => {
    const header = buildTaskHeader({
      ...base,
      report: { contract_party_evidence: {
        general_contractor: { status: 'unresolved', candidate_company: '候选总包公司' },
      } },
    })
    expect(field(header, 'contractor').value).toBe('待核实（候选：候选总包公司）')
  })

  it('shows an explicit conflict state', () => {
    const header = buildTaskHeader({
      ...base,
      report: { contract_party_evidence: {
        general_contractor: { status: 'conflict', company: null },
      } },
    })
    expect(field(header, 'contractor').value).toBe('待核实（主体冲突）')
  })
})

it('displays packet facts without rounding subcontract amount', () => {
  const header = buildTaskHeader({ detail: { ...base.detail,
    report_basic_facts: {
      subcontract_scope: { status: 'matched', value: '预制板梁劳务分包' },
      subcontract_amount: { status: 'matched', value: '4173.0485', unit: '万元' },
    },
  } })
  expect(field(header, 'scope').value).toBe('预制板梁劳务分包')
  expect(field(header, 'amount').value).toBe('4173.0485万元')
})
