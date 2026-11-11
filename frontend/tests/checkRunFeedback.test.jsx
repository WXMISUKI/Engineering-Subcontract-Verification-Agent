import React from 'react'
import { afterEach, expect, it } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import CheckRunView from '../src/plus-ui/blockchain-supervision/components/CheckRunView'

afterEach(cleanup)

const snapshot = {
  ai_auto_enabled: true,
  steps: [
    { step_id: 's1', title: '要素完整性核查', state: 'completed', expected_groups: ['completeness'] },
    { step_id: 's2', title: '资质与内容合规性审查', state: 'completed', expected_groups: ['a1', 'a2'] },
  ],
}

it('规则步骤完成但 AI 仍在核查时不把当前步骤显示为全部完成', () => {
  render(<CheckRunView taskState="running" snapshot={snapshot} progress={{
    phase: 'ai_checking', ai_job: { state: 'running', current_group: 'a1' },
  }} />)
  const steps = within(screen.getByTestId('check-run-steps')).getAllByRole('listitem')
  expect(steps[1].textContent).toContain('AI 辅助核查中')
  expect(steps[1].textContent).not.toContain('✓')
  expect(steps[0].textContent).toContain('规则已完成')
})

it('使用事件时间线解释批次回执乱序并保留真实失败请求', () => {
  const events = [
    { sequence: 1, event_type: 'check.ai_batch_progress', label: 'AI 批次核查进度',
      message: '第 21/33 批 · 正在调用模型', created_at: '2026-11-09T10:15:02Z',
      diagnostics: { batch_index: 21, batch_total: 33, request_id: 'req-current' } },
    { sequence: 2, event_type: 'check.ai_batch_progress', label: 'AI 批次核查进度',
      message: '第 17/33 批 · 部分建议需人工核实', created_at: '2026-11-09T10:15:11Z',
      diagnostics: { batch_index: 17, batch_total: 33, request_id: 'req-current' } },
    { sequence: 3, event_type: 'check.ai_batch_progress', label: 'AI 批次核查进度',
      message: '第 28/33 批 · 模型调用未完成', created_at: '2026-11-09T10:21:55Z',
      diagnostics: { batch_index: 28, batch_total: 33, request_id: 'req-failed', failure_categories: ['timeout'] } },
  ]
  render(<CheckRunView taskState="running" snapshot={snapshot} events={events} progress={{
    phase: 'failed', ai_job: { state: 'failed', request_id: 'req-later', failure_categories: ['timeout'] },
  }} />)
  expect(screen.queryByTestId('check-run-diagnostics')).toBeNull()
  const timeline = within(screen.getByTestId('check-run-timeline'))
  expect(timeline.getByText(/并发.*回执.*顺序/u)).toBeTruthy()
  expect(timeline.getByText(/req-failed/u)).toBeTruthy()
  expect(timeline.getByText(/模型调用超时/u)).toBeTruthy()
})

it('事件尚未载入时保留最近回执标识，且不将其误认为失败请求', () => {
  render(<CheckRunView taskState="running" snapshot={snapshot} events={[]} progress={{
    phase: 'failed', ai_job: { state: 'failed', job_id: 'job-latest', request_id: 'req-latest', failure_categories: ['timeout'] },
  }} />)
  const timeline = within(screen.getByTestId('check-run-timeline'))
  expect(timeline.getByText(/job-latest/)).toBeTruthy()
  expect(timeline.getByText(/req-latest/)).toBeTruthy()
  expect(timeline.getByText(/不一定是失败请求/)).toBeTruthy()
})

it('整体失败时不将最近分组误认成失败分组', () => {
  render(<CheckRunView taskState="running" snapshot={snapshot} progress={{
    phase: 'failed', ai_job: { state: 'failed', current_group: 'a1', failure_categories: ['timeout'] },
  }} />)
  const steps = within(screen.getByTestId('check-run-steps')).getAllByRole('listitem')
  expect(steps[1].textContent).toContain('AI 状态待核实')
  expect(steps[1].textContent).not.toContain('AI 未完成')
})
