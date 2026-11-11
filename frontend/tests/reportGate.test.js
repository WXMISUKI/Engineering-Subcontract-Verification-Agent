import { expect, it } from 'vitest'
import { reportGateForGroups } from '../src/plus-ui/blockchain-supervision/lib/reviewQueueModel'

function groups(items) {
  return [{ key: 'pending', label: '待处理', items }]
}

it('allows report generation when only hit items are pending', () => {
  const gate = reportGateForGroups(groups([
    { status: 'hit', review: null },
    { status: 'miss', review: null },
  ]))
  expect(gate.canGenerate).toBe(true)
  expect(gate.pendingHit).toBe(1)
  expect(gate.pendingUndecidable).toBe(0)
})

it('blocks report generation while an undecidable item is pending', () => {
  const gate = reportGateForGroups(groups([
    { status: 'undecidable', review: null },
  ]))
  expect(gate.canGenerate).toBe(false)
  expect(gate.pendingUndecidable).toBe(1)
})

it('allows report generation after the undecidable item is handled', () => {
  const gate = reportGateForGroups(groups([
    { status: 'undecidable', review: { decision: 'accepted' } },
    { status: 'hit', review: null },
  ]))
  expect(gate.canGenerate).toBe(true)
  expect(gate.pendingUndecidable).toBe(0)
})
