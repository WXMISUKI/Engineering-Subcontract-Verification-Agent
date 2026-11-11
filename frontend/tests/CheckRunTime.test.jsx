import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import CheckRunView from '../src/plus-ui/blockchain-supervision/components/CheckRunView'

afterEach(cleanup)

it('核查时间线将历史事件和实时事件显示为北京时间', () => {
  render(<CheckRunView taskState="running" events={[
    { sequence: 1, event_type: 'task.started', label: '核查开始', created_at: '2026-11-06T01:00:15' },
    { sequence: 2, event_type: 'check.progress', label: '核查进行中', created_at: '2026-11-06T01:00:30Z' },
  ]} />)
  const timeline = within(screen.getByTestId('check-run-timeline'))
  expect(timeline.getByText('09:00:15')).toBeTruthy()
  expect(timeline.getByText('09:00:30')).toBeTruthy()
})

it('工作台时间线展示材料识别和 AI 核查的限流信息', () => {
  render(<CheckRunView taskState="running" events={[
    { sequence: 1, event_type: 'material.parsed', label: '材料已读取', message: '材料识别第2页服务请求受限（HTTP 429），请稍后重试', created_at: '2026-11-06T01:00:15' },
    { sequence: 2, event_type: 'check.ai_batch_progress', label: 'AI 批次核查进展', message: '第 3/12 批 · 服务请求受限（HTTP 429），请稍后重试', created_at: '2026-11-06T01:00:30Z' },
  ]} />)
  const timeline = within(screen.getByTestId('check-run-timeline'))
  expect(timeline.getByText(/材料识别第2页服务请求受限（HTTP 429）/u)).toBeTruthy()
  expect(timeline.getByText(/第 3\/12 批 · 服务请求受限（HTTP 429）/u)).toBeTruthy()
})

it('显示最后回执、后台批次关联和无法核实状态', () => {
  render(<CheckRunView taskState="running" progress={{
    activity_health: { stale_candidate: true, last_event_at: '2026-11-09T01:00:00Z', age_seconds: 2400 },
    ai_job: { state: 'running', job_id: 'job-1', review_run_id: 'review-1', request_id: 'request-1',
      current_group: 'a2', current_batch: { index: 4, total: 6 }, last_progress_at: null,
      diagnostic_state: '无法核实', stale_candidate: true },
  }} events={[]} />)
  const timeline = within(screen.getByTestId('check-run-timeline'))
  expect(screen.queryByTestId('check-run-diagnostics')).toBeNull()
  expect(timeline.getByText(/批次 4\/6/u)).toBeTruthy()
  expect(timeline.getByText(/最后回执：09:00:00 · 无法核实/u)).toBeTruthy()
  expect(timeline.getByText(/job-1/u)).toBeTruthy()
  expect(timeline.getByText(/request-1/u)).toBeTruthy()
})

it('核查失败时展示后台任务的失败分类', () => {
  render(<CheckRunView taskState="running" progress={{
    phase: 'failed', current_task: { label: '部分核查未完成' },
    ai_job: { state: 'failed', job_id: 'job-1', diagnostic_state: '执行失败', failure_categories: ['timeout'] },
    activity_health: { stale_candidate: true },
  }} />)
  expect(screen.getByTestId('check-run-error').textContent).toContain('模型调用超时')
  expect(within(screen.getByTestId('check-run-timeline')).getByText(/执行失败/u)).toBeTruthy()
  expect(screen.getByTestId('progress-percent').textContent).toBe('已中断')
  expect(screen.queryByTestId('progress-motion')).toBeNull()
  expect(screen.queryByTestId('check-run-stale')).toBeNull()
})

it('版本不一致时明确显示无法核实，不把后台已完成显示为正在执行', () => {
  const onReviewReady = vi.fn()
  render(<CheckRunView taskState="running" onReviewReady={onReviewReady} progress={{
    phase: 'ai_checking', ai_coverage: { state: 'unavailable', reason: 'rule_version_mismatch' },
    ai_job: { state: 'unavailable', last_recorded_state: 'completed', diagnostic_state: '无法核实', stale_candidate: false },
    activity_health: { stale_candidate: true },
  }} />)
  expect(screen.getByText(/当前规则或核查实现与本次运行不一致/u)).toBeTruthy()
  expect(screen.getByTestId('check-run-title').textContent).not.toMatch(/正在/u)
  expect(screen.queryByTestId('progress-motion')).toBeNull()
  expect(screen.queryByTestId('check-run-stale')).toBeNull()
  expect(screen.getByText('后台事件记录：已结束；结果状态无法核实')).toBeTruthy()
  expect(onReviewReady).not.toHaveBeenCalled()
})

it('未知原因的覆盖不可用不应误报版本不一致', () => {
  render(<CheckRunView taskState="running" progress={{
    phase: 'ai_checking', ai_coverage: { state: 'unavailable', reason: 'unknown' },
  }} />)
  expect(screen.getByText(/AI 核查状态无法核实/u)).toBeTruthy()
  expect(screen.queryByText(/当前规则或核查实现与本次运行不一致/u)).toBeNull()
})

it('补件候选结果未生效时不能提前进入人工复核', () => {
  const onReviewReady = vi.fn()
  render(<CheckRunView taskState="done" publicationPending onReviewReady={onReviewReady} onEnterReview={() => {}} />)
  expect(onReviewReady).not.toHaveBeenCalled()
  expect(screen.queryByRole('button', { name: '进入人工复核' })).toBeNull()
})

it('任务详情尚未加载时，已返回的规则步骤不能提前开放人工复核', () => {
  const onReviewReady = vi.fn()
  const snapshot = { ai_auto_enabled: true, steps: [{ step_id: 'a2', title: '合成步骤', state: 'completed' }] }
  const view = render(<CheckRunView snapshot={snapshot} onReviewReady={onReviewReady} />)
  expect(onReviewReady).not.toHaveBeenCalled()
  view.rerender(<CheckRunView taskState="running" snapshot={snapshot} onReviewReady={onReviewReady} />)
  expect(onReviewReady).not.toHaveBeenCalled()
  view.rerender(<CheckRunView taskState="running" snapshot={snapshot} onReviewReady={onReviewReady}
    progress={{ phase: 'checking' }} />)
  expect(onReviewReady).not.toHaveBeenCalled()
  view.rerender(<CheckRunView taskState="running" snapshot={snapshot} onReviewReady={onReviewReady}
    progress={{ phase: 'ai_checking', ai_coverage: { state: 'unavailable', reason: 'unknown' } }} />)
  expect(onReviewReady).not.toHaveBeenCalled()
  view.rerender(<CheckRunView taskState="running" snapshot={snapshot} onReviewReady={onReviewReady}
    progress={{ phase: 'completed', percent: 100 }} />)
  expect(onReviewReady).toHaveBeenCalledTimes(1)
})
