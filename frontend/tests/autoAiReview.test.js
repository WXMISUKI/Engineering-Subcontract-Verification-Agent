import { describe, expect, it, vi } from 'vitest'
import { runAutoAiReview } from '../src/plus-ui/blockchain-supervision/lib/autoAiReview'

describe('auto AI review scope handling', () => {
  const run = { group_id: 'a1', state: 'completed', run_id: 'run-1' }
  const counts = { not_started: 0, pending: 0, completed: 0, partial: 0, failed: 0, not_run: 1 }

  it('returns a material-scope warning when an actual review item covers the unlocated candidate', async () => {
    const getStatus = vi.fn().mockResolvedValue({ run_id: 'run-1', counts, skipped: 0,
      reviewable_not_run: 1, unlocated_scope_reviewable: 1 })
    const result = await runAutoAiReview({ taskId: 1, runs: [run], groups: ['a1'], getStatus, runBatch: vi.fn() })
    expect(result).toMatchObject({ state: 'completed', warning: true, unlocatedScopeCount: 1 })
  })

  it('keeps technical failures blocked', async () => {
    const getStatus = vi.fn().mockResolvedValue({ run_id: 'run-1', counts: { ...counts, not_run: 0, failed: 1 },
      skipped: 0, reviewable_not_run: 0, unlocated_scope_reviewable: 0, failure_categories: ['timeout'] })
    const result = await runAutoAiReview({ taskId: 1, runs: [run], groups: ['a1'], getStatus, runBatch: vi.fn() })
    expect(result).toMatchObject({ state: 'failed', failureCategories: ['timeout'] })
  })

  it('uses the status after the batch to report newly created scope review items', async () => {
    const getStatus = vi.fn()
      .mockResolvedValueOnce({ run_id: 'run-1', counts: { ...counts, not_run: 0, not_started: 1 }, skipped: 0 })
      .mockResolvedValueOnce({ run_id: 'run-1', counts, skipped: 0, reviewable_not_run: 1, unlocated_scope_reviewable: 1 })
    const runBatch = vi.fn().mockResolvedValue({})
    const result = await runAutoAiReview({ taskId: 1, runs: [run], groups: ['a1'], getStatus, runBatch })
    expect(runBatch).toHaveBeenCalledOnce()
    expect(result).toMatchObject({ state: 'completed', warning: true, unlocatedScopeCount: 1 })
  })
})
