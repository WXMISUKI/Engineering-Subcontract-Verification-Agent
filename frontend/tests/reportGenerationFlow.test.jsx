import React from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import BlockchainSupervisionWorkbench from '../src/plus-ui/blockchain-supervision/BlockchainSupervisionWorkbench'
import { completeBsTask, getBsTask, listBsFindings, reviewBsFinding } from '../src/plus-ui/blockchain-supervision/lib/api'

const ready = { schema_version: 'bs.report-readiness.v1', draft_readable: true,
  confirmed_output_ready: true, task_close_ready: true, execution: { state: 'completed' },
  reading_quality: { state: 'pending', pages: [{ material_id: 6, page: 18 }], blocking: false },
  finding_review: { blocking: [] }, blocking_reasons: [], next_actions: [],
  warnings: [{ code: 'reading_quality_limited', message: '部分页面识别不完整，此提示不阻断报告生成。' }] }
const snapshot = { review_run_id: 'report-flow', steps: [], runs: [] }
const detail = (readiness = ready) => ({ task_id: 7, title: '报告生成测试', state: 'running',
  scope: {}, progress: { phase: 'review_required' }, report_readiness: readiness })

vi.mock('../src/plus-ui/PlusUIShell', () => ({ default: ({ children }) => <div>{children}</div> }))
vi.mock('../src/components/ModelSelect', () => ({ default: () => null }))
vi.mock('../src/plus-ui/blockchain-supervision/components/ReviewWorkbenchLayout', () => ({
  default: ({ onComplete, completeDisabled, completeDisabledReason, onReview }) => <div>
    <button disabled={completeDisabled} onClick={onComplete}>生成审查报告</button>
    <button onClick={() => onReview(101, 'accepted', '已核对')}>保存最后一项复核</button>
    <span>{completeDisabledReason}</span>
  </div>,
}))
vi.mock('../src/plus-ui/blockchain-supervision/lib/api', async (original) => ({
  ...await original(),
  listBsTasks: vi.fn(async () => ({ tasks: [{ task_id: 7, state: 'running' }] })),
  getBsTask: vi.fn(), listBsFindings: vi.fn(), reviewBsFinding: vi.fn(), completeBsTask: vi.fn(),
  getBsTodo: vi.fn(async () => ({})), getBsRuleset: vi.fn(async () => ({})),
  getBsTaskUsage: vi.fn(async () => ({})), pingBs: vi.fn(async () => ({ ok: true })),
  getBsReport: vi.fn(async () => ({ meta: {}, sections: [], summary: {}, reference_snapshot: snapshot })),
}))

beforeEach(() => {
  vi.clearAllMocks()
  history.replaceState({}, '', '/?bs_view=review')
  vi.stubGlobal('EventSource', class { addEventListener() {} close() {} })
  getBsTask.mockResolvedValue(detail())
  listBsFindings.mockResolvedValue({ findings: [], reference_snapshot: snapshot })
  completeBsTask.mockResolvedValue({ ok: true })
})
afterEach(() => { cleanup(); sessionStorage.clear(); history.replaceState({}, '', '/'); vi.unstubAllGlobals() })

it('generates the report with pending OCR quality and enters the report view', async () => {
  render(<BlockchainSupervisionWorkbench conversationId="report-flow" initialTaskId={7} />)
  const button = await screen.findByRole('button', { name: '生成审查报告' })
  await waitFor(() => expect(button.disabled).toBe(false))
  fireEvent.click(button)
  await waitFor(() => expect(completeBsTask).toHaveBeenCalledWith(7))
  await screen.findByLabelText('报告模板')
})

it('refreshes readiness after the last human receipt is saved', async () => {
  const blocked = { ...ready, confirmed_output_ready: false, task_close_ready: false,
    finding_review: { blocking: [{ finding_id: 101 }] },
    blocking_reasons: [{ code: 'pending_undecidable_review', message: '还有一项无法核实尚未处理' }] }
  getBsTask.mockResolvedValue(detail(blocked))
  listBsFindings.mockResolvedValue({ findings: [{ finding_id: 101, item_id: 'A2-01',
    kind: 'person_consistency', status: 'undecidable', message: '身份需核对' }], reference_snapshot: snapshot })
  reviewBsFinding.mockImplementation(async () => {
    getBsTask.mockResolvedValue(detail())
    listBsFindings.mockResolvedValue({ findings: [], reference_snapshot: snapshot })
    return { ok: true }
  })
  render(<BlockchainSupervisionWorkbench conversationId="report-flow" initialTaskId={7} />)
  const button = await screen.findByRole('button', { name: '生成审查报告' })
  expect(button.disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: '保存最后一项复核' }))
  await waitFor(() => expect(button.disabled).toBe(false))
  expect(reviewBsFinding).toHaveBeenCalledWith(101, 'accepted', '已核对')
  fireEvent.click(button)
  await waitFor(() => expect(completeBsTask).toHaveBeenCalledWith(7))
})

it('shows a fresh server blocker instead of silently returning or closing the task', async () => {
  render(<BlockchainSupervisionWorkbench conversationId="report-flow" initialTaskId={7} />)
  const button = await screen.findByRole('button', { name: '生成审查报告' })
  await waitFor(() => expect(button.disabled).toBe(false))
  getBsTask.mockResolvedValue(detail({ ...ready, confirmed_output_ready: false, task_close_ready: false,
    execution: { state: 'incomplete' }, blocking_reasons: [{ code: 'ai_execution_incomplete', message: 'AI 服务调用失败，请重试' }] }))
  fireEvent.click(button)
  await waitFor(() => expect(screen.getAllByText('AI 服务调用失败，请重试').length).toBeGreaterThan(0))
  expect(completeBsTask).not.toHaveBeenCalled()
})
