import { describe, expect, it } from 'vitest'
import { autoRunIsStale, shouldResumeAiAfterReload } from '../src/plus-ui/blockchain-supervision/lib/autoCheckResume'
import { BS_EVENT_TYPES } from '../src/plus-ui/blockchain-supervision/lib/api'

describe('auto-check stale recovery', () => {
  it('subscribes to background lifecycle events so the timeline and task status can refresh', () => {
    for (const state of ['started', 'progress', 'completed', 'failed']) {
      expect(BS_EVENT_TYPES).toContain(`check.ai_background_${state}`)
    }
  })
  it('opens the recovery path only after the running lease exceeds the threshold', () => {
    expect(autoRunIsStale({ running: true, startedAt: 1000, now: 30_999 })).toBe(false)
    expect(autoRunIsStale({ running: true, startedAt: 1000, now: 31_000 })).toBe(true)
  })

  it('does not treat an idle or malformed timestamp as stale', () => {
    expect(autoRunIsStale({ running: false, startedAt: 1000, now: 100_000 })).toBe(false)
    expect(autoRunIsStale({ running: true, startedAt: null, now: 100_000 })).toBe(false)
  })

  it('does not resume when the server already closed the background AI run', () => {
    const referenceSnapshot = {
      review_run_id: 'run-1',
      ai_auto_enabled: true,
      ai_auto_groups: ['a1', 'a2'],
      runs: [
        { group_id: 'a1', state: 'completed' },
        { group_id: 'a2', state: 'completed' },
      ],
    }
    expect(shouldResumeAiAfterReload({
      taskState: 'running',
      progressPhase: 'completed',
      activityHealth: { last_event_type: 'check.ai_background_completed' },
      referenceSnapshot,
    })).toBe(false)
  })

  it('resumes an open AI run when all deterministic groups are complete', () => {
    expect(shouldResumeAiAfterReload({
      taskState: 'running',
      progressPhase: 'checking',
      activityHealth: { last_event_type: 'check.run_completed' },
      referenceSnapshot: {
        review_run_id: 'run-2',
        ai_auto_enabled: true,
        ai_auto_groups: ['a1', 'a2'],
        runs: [
          { group_id: 'a1', state: 'completed' },
          { group_id: 'a2', state: 'completed' },
        ],
      },
      seenRunId: null,
    })).toBe(true)
  })

  it('does not resume when server cannot verify coverage', () => {
    expect(shouldResumeAiAfterReload({
      taskState: 'running', progressPhase: 'ai_checking',
      aiCoverage: { state: 'unavailable', reason: 'rule_version_mismatch' },
      referenceSnapshot: { review_run_id: 'run-3', ai_auto_enabled: true, ai_auto_groups: ['a1'],
        runs: [{ group_id: 'a1', state: 'completed' }] },
    })).toBe(false)
  })
})
