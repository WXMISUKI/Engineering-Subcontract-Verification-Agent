import { describe, expect, it, vi } from 'vitest'
import { runBackgroundAiReview } from '../src/plus-ui/blockchain-supervision/lib/autoAiReview'

describe('background AI review', () => {
  it('submits once and polls until the independent job completes', async () => {
    const startJob = vi.fn().mockResolvedValue({ state: 'running', job_id: 'job-1', review_run_id: 'r1' })
    const getJob = vi.fn()
      .mockResolvedValueOnce({ state: 'running', job_id: 'job-1', review_run_id: 'r1', current_group: 'a1' })
      .mockResolvedValueOnce({ state: 'completed', job_id: 'job-1', review_run_id: 'r1', warning: true, unlocated_scope_count: 2 })
    const result = await runBackgroundAiReview({ taskId: 1, reviewRunId: 'r1', startJob, getJob, wait: async () => {} })
    expect(startJob).toHaveBeenCalledOnce()
    expect(getJob).toHaveBeenCalledTimes(2)
    expect(result).toMatchObject({ state: 'completed', warning: true, unlocatedScopeCount: 2 })
  })

  it('reports interrupted jobs rather than retrying them automatically', async () => {
    const startJob = vi.fn().mockResolvedValue({ state: 'interrupted', job_id: 'job-1', review_run_id: 'r1' })
    const getJob = vi.fn()
    const result = await runBackgroundAiReview({ taskId: 1, reviewRunId: 'r1', startJob, getJob, wait: async () => {} })
    expect(result.state).toBe('interrupted')
    expect(startJob).toHaveBeenCalledOnce()
    expect(getJob).not.toHaveBeenCalled()
  })

  it('does not treat a different job as completion of the submitted one', async () => {
    const startJob = vi.fn().mockResolvedValue({ state: 'running', job_id: 'job-1', review_run_id: 'r1' })
    const getJob = vi.fn().mockResolvedValue({ state: 'completed', job_id: 'job-2', review_run_id: 'r1' })
    await expect(runBackgroundAiReview({ taskId: 1, reviewRunId: 'r1', startJob, getJob, wait: async () => {} })).rejects.toThrow()
  })

  it('retains model timeout categories for the user-facing failure', async () => {
    const startJob = vi.fn().mockResolvedValue({ state: 'failed', job_id: 'job-1', review_run_id: 'r1', failure_categories: ['timeout'] })
    const result = await runBackgroundAiReview({ taskId: 1, reviewRunId: 'r1', startJob, getJob: vi.fn() })
    expect(result).toMatchObject({ state: 'failed', failureCategories: ['timeout'] })
  })
})
