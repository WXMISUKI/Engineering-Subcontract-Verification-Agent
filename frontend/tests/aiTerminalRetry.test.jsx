import React from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import CheckRunView from '../src/plus-ui/blockchain-supervision/components/CheckRunView'
import { shouldResumeAiAfterReload } from '../src/plus-ui/blockchain-supervision/lib/autoCheckResume'

afterEach(cleanup)
const snapshot = { review_run_id: 'run-1', ai_auto_enabled: true, ai_auto_groups: ['a1'],
  steps: [{ step_id: 's2', title: '资质与内容合规性审查', state: 'completed', expected_groups: ['a1'] }],
  runs: [{ group_id: 'a1', state: 'completed' }] }

it('页面刷新不会自动重试已失败的后台任务', () => {
  expect(shouldResumeAiAfterReload({ taskState: 'running', progressPhase: 'failed', referenceSnapshot: snapshot })).toBe(false)
  expect(shouldResumeAiAfterReload({ taskState: 'running', progressPhase: 'ai_checking', referenceSnapshot: snapshot,
    activityHealth: { last_event_type: 'check.ai_background_failed' } })).toBe(false)
})

it('服务端确认失败时，在本地没有失败状态也提供显式重试入口', () => {
  const onRetry = vi.fn()
  render(<CheckRunView taskState="running" snapshot={snapshot} autoCheck={{ phase: 'idle' }} onRetry={onRetry}
    progress={{ phase: 'failed', ai_job: { state: 'failed', failure_categories: ['timeout'] } }} />)
  const retry = screen.getByRole('button', { name: '重试核查' })
  expect(retry.disabled).toBe(false)
  fireEvent.click(retry)
  expect(onRetry).toHaveBeenCalledTimes(1)
})

it('任务仍有活动工作时，重试入口保持禁用', () => {
  render(<CheckRunView taskState="running" snapshot={snapshot} autoCheck={{ phase: 'failed' }} mutationBusy
    progress={{ phase: 'ai_checking', ai_job: { state: 'failed' } }} />)
  expect(screen.getByRole('button', { name: '重试核查' }).disabled).toBe(true)
})

it('服务端失败且回执过期时仅提供一个重试按钮', () => {
  const onRetry = vi.fn()
  const onRecheck = vi.fn()
  render(<CheckRunView taskState="running" snapshot={snapshot} autoCheck={{ phase: 'idle' }}
    onRetry={onRetry} onRecheck={onRecheck}
    progress={{ phase: 'failed', ai_job: { state: 'failed', failure_categories: ['timeout'] },
      activity_health: { stale_candidate: true } }} />)
  const buttons = screen.getAllByRole('button', { name: '重试核查' })
  expect(buttons).toHaveLength(1)
  expect(buttons[0].disabled).toBe(false)
  fireEvent.click(buttons[0])
  expect(onRetry).toHaveBeenCalledTimes(1)
  expect(onRecheck).not.toHaveBeenCalled()
})

it('本地失败要求重新核查时不额外提供过期回执重试按钮', () => {
  const onRetry = vi.fn()
  const onRecheck = vi.fn()
  render(<CheckRunView taskState="running" snapshot={snapshot}
    autoCheck={{ phase: 'failed', recheckRequired: true }} onRetry={onRetry} onRecheck={onRecheck}
    progress={{ phase: 'failed', ai_job: { state: 'failed' }, activity_health: { stale_candidate: true } }} />)
  fireEvent.click(screen.getByRole('button', { name: '重新核查' }))
  expect(screen.queryByRole('button', { name: '重试核查' })).toBeNull()
  expect(onRecheck).toHaveBeenCalledTimes(1)
  expect(onRetry).not.toHaveBeenCalled()
})

it('覆盖状态无法核实时不因旧回执提供重试入口', () => {
  render(<CheckRunView taskState="running" snapshot={snapshot} autoCheck={{ phase: 'idle' }} onRetry={vi.fn()}
    progress={{ phase: 'ai_checking', ai_coverage: { state: 'unavailable' },
      activity_health: { stale_candidate: true } }} />)
  expect(screen.queryByRole('button', { name: '重试核查' })).toBeNull()
})

it('任务已完成时不因旧回执提供重试入口', () => {
  render(<CheckRunView taskState="done" snapshot={snapshot} autoCheck={{ phase: 'idle' }} onRetry={vi.fn()}
    progress={{ phase: 'completed', activity_health: { stale_candidate: true } }} />)
  expect(screen.queryByRole('button', { name: '重试核查' })).toBeNull()
})

it('仍在等待回执时保留单个停滞重试入口', () => {
  const onRetry = vi.fn()
  render(<CheckRunView taskState="running" snapshot={snapshot} autoCheck={{ phase: 'idle' }} onRetry={onRetry}
    progress={{ phase: 'ai_checking', ai_job: { state: 'running' }, activity_health: { stale_candidate: true } }} />)
  expect(screen.getByTestId('check-run-stale')).toBeTruthy()
  const buttons = screen.getAllByRole('button', { name: '重试核查' })
  expect(buttons).toHaveLength(1)
  fireEvent.click(buttons[0])
  expect(onRetry).toHaveBeenCalledTimes(1)
})
