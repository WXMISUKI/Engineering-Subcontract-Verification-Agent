import { expect, it } from 'vitest'
import { reportGateForGroups, buildReviewQueueSections, reviewQueueCounts } from '../src/plus-ui/blockchain-supervision/lib/reviewQueueModel'

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

it('keeps explicit manual prompts pending without counting them as undecidable', () => {
  const prompt = { status: 'undecidable', findingSemantics: 'manual_prompt', review: null }
  const source = groups([prompt])
  expect(reportGateForGroups(source)).toMatchObject({ canGenerate: true, pendingUndecidable: 0 })
  expect(reviewQueueCounts(source)).toMatchObject({ total: 1, undecidable: 0, manualPrompt: 1 })
  expect(buildReviewQueueSections(source, 'pending')[0].items).toEqual([prompt])
  expect(buildReviewQueueSections(source, 'undecidable')).toEqual([])
  prompt.review = { decision: 'accepted' }
  expect(buildReviewQueueSections(source, 'processed')[0].items).toEqual([prompt])
})

it.each([undefined, null, 'unknown', 'machine_conclusion'])('keeps legacy gate for %s semantics', (semantics) => {
  const source = groups([{ status: 'undecidable', finding_semantics: semantics, message: '人工提示' }])
  expect(reportGateForGroups(source).canGenerate).toBe(false)
  expect(reviewQueueCounts(source).undecidable).toBe(1)
})
