import React from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import CheckRunView from '../src/plus-ui/blockchain-supervision/components/CheckRunView'
import { getBsBackgroundAiReview } from '../src/plus-ui/blockchain-supervision/lib/api'

vi.mock('../src/plus-ui/blockchain-supervision/lib/api', () => ({
  getBsBackgroundAiReview: vi.fn(),
}))

beforeEach(() => vi.resetAllMocks())
afterEach(cleanup)

const snapshot = {
  review_run_id: 'run-1', ai_auto_enabled: true, ai_auto_groups: ['a1'],
  steps: [{ step_id: 's2', title: '资质与内容合规性审查', state: 'completed', expected_groups: ['a1'] }],
  runs: [{ group_id: 'a1', state: 'completed' }],
}
const progress = { phase: 'completed', percent: 100, current_task: { label: '核查已完成，等待人工复核' } }

it('刷新后恢复当前核查代的完成警示，仍可进入人工复核', async () => {
  getBsBackgroundAiReview.mockResolvedValue({ state: 'completed', review_run_id: 'run-1', job_id: 'job-1', warning: true })
  render(<CheckRunView taskId={5} taskState="running" snapshot={snapshot} progress={progress} onEnterReview={() => {}} />)
  expect(await screen.findByText(/部分 AI 辅助核查未完成或结果需要人工核实/)).toBeTruthy()
  expect(screen.getByTestId('check-run-title').textContent).not.toContain('全部完成')
  expect(screen.getByTestId('check-run-steps').textContent).not.toContain('未发现问题')
  expect(screen.getByRole('button', { name: /进入人工复核/ }).disabled).toBe(false)
  expect(getBsBackgroundAiReview).toHaveBeenCalledWith(5, 'run-1')
})

it('无警示的完成回执不显示警示', async () => {
  getBsBackgroundAiReview.mockResolvedValue({ state: 'completed', review_run_id: 'run-1', job_id: 'job-1', warning: false })
  render(<CheckRunView taskId={5} taskState="running" snapshot={snapshot} progress={progress} />)
  await waitFor(() => expect(screen.queryByText('正在确认 AI 辅助核查状态')).toBeNull())
  expect(screen.queryByText(/部分 AI 辅助核查未完成或结果需要人工核实/)).toBeNull()
})

it('完成回执读取失败时提示状态无法核实', async () => {
  getBsBackgroundAiReview.mockRejectedValue(new Error('offline'))
  render(<CheckRunView taskId={5} taskState="running" snapshot={snapshot} progress={progress} />)
  expect(await screen.findByText(/AI 辅助核查结果状态无法核实/)).toBeTruthy()
  expect(screen.getByTestId('check-run-title').textContent).not.toContain('全部完成')
})

it('切换任务后丢弃旧任务迟到的完成警示', async () => {
  let resolveOld
  getBsBackgroundAiReview.mockImplementation((taskId) => taskId === 5
    ? new Promise((resolve) => { resolveOld = resolve })
    : Promise.resolve({ state: 'completed', review_run_id: 'run-2', job_id: 'job-2', warning: false }))
  const view = render(<CheckRunView taskId={5} taskState="running" snapshot={snapshot} progress={progress} />)
  await waitFor(() => expect(resolveOld).toBeTypeOf('function'))
  view.rerender(<CheckRunView taskId={6} taskState="running" snapshot={{ ...snapshot, review_run_id: 'run-2' }} progress={progress} />)
  await waitFor(() => expect(getBsBackgroundAiReview).toHaveBeenCalledWith(6, 'run-2'))
  await waitFor(() => expect(screen.queryByText('正在确认 AI 辅助核查状态')).toBeNull())
  await act(async () => resolveOld({ state: 'completed', review_run_id: 'run-1', job_id: 'job-1', warning: true }))
  expect(screen.queryByText(/部分 AI 辅助核查未完成或结果需要人工核实/)).toBeNull()
})

it('回执核查代不匹配时不视为成功', async () => {
  getBsBackgroundAiReview.mockResolvedValue({ state: 'completed', review_run_id: 'old-run', job_id: 'old-job', warning: false })
  render(<CheckRunView taskId={5} taskState="running" snapshot={snapshot} progress={progress} />)
  expect(await screen.findByText(/AI 辅助核查结果状态无法核实/)).toBeTruthy()
})
